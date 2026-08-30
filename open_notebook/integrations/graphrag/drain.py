"""GraphRAG-03C deletion-tombstone drain: the remote convergence lifecycle.

This is the first phase permitted to perform a remote LightRAG deletion. It turns
the durable *intent* recorded by GraphRAG-03B (a ``graphrag_deletion`` tombstone,
migration 24/25) into proven derived *convergence*, then resolves the tombstone.

Source of truth: SurrealDB ``source`` is canonical; LightRAG is derived and
removable; the tombstone is durable local deletion intent. A tombstone NEVER
means "delete this doc_id unconditionally" — it means "the derived state of the
DELETED generation of this source_id must not survive." So every attempt reloads
the CURRENT canonical Source and branches (the contract in
GRAPHRAG_03B_DURABLE_DELETE.md §17.1):

    load CURRENT source
      ├─ absent                          -> converge to ABSENT (delete + confirm)
      ├─ present, empty/whitespace text  -> converge to ABSENT (delete + confirm)
      └─ present, non-empty text
             ├─ indexing flag ON  -> converge to CURRENT (03A delete-then-insert)
             └─ indexing flag OFF -> converge to ABSENT (no Boundary-B egress)

Resolution is ALWAYS an ``arm_id`` compare-and-set DELETE of the tombstone, and
only after the required derived state is *proven*: CONFIRMED remote absence for
the absent/empty branches (never an async ``deletion_started`` acknowledgement),
or a confirmed current insert for the live-non-empty branch. Anything unproven
leaves the tombstone pending and DEFERS it (``next_attempt_at`` in the future) so
the batch stays fair and bounded.

Boundary: the absent/empty branches need only the source-derived doc identity and
the sidecar (Boundary A) — no embedding/LLM/provider call. The live-non-empty
branch reuses the already-approved 03A index path (Boundary B on synthetic data),
and ONLY when the indexing flag is on; with the flag off it converges to absent
instead, so deletion draining never silently broadens data egress.

No secrets or document content are logged: only source ids, arm ids, normalized
outcomes, and sanitized exception classes.
"""

import asyncio
from dataclasses import dataclass
from enum import Enum

from loguru import logger

from open_notebook.database.repository import repo_query
from open_notebook.integrations.graphrag import deletion
from open_notebook.integrations.graphrag.config import (
    GraphRAGDrainConfig,
    load_drain_config,
)
from open_notebook.integrations.graphrag.lifecycle import IndexResult, index_source
from open_notebook.integrations.graphrag.models import (
    _INDEXABLE_TABLES,
    AbsenceState,
    GraphRAGError,
    GraphRAGValidationError,
    record_id_for,
)
from open_notebook.integrations.graphrag.service import GraphRAGService

#: Name of the surreal-commands drain worker (registered in commands/).
DRAIN_COMMAND_NAME = "graphrag_drain_deletions"


class DrainOutcome(str, Enum):
    """Normalized result of converging ONE tombstone (explicit, not a bool).

    - RESOLVED_ABSENT: remote absence was CONFIRMED and the tombstone was
      arm-fenced deleted (absent/empty/flag-off branches).
    - CONVERGED_CURRENT: the live source's current text was (re)indexed and the
      tombstone was arm-fenced deleted (live non-empty, flag on).
    - SUPERSEDED: the arm_id CAS matched zero rows — the tombstone was re-armed
      (deleted again) or already resolved mid-convergence. NOT an error and NOT a
      failure: the newer arm is immediately due and will be re-driven. We do not
      touch it.
    - DEFERRED: convergence could not be proven this attempt (remote absence
      UNKNOWN, sidecar unreachable/busy, transient index failure, canonical state
      changed). The tombstone stays pending and is pushed out of the due set.
    - PERMANENT_LOCAL_ERROR: a local, non-retryable defect (a tombstone identity
      that is not a canonical source record id, or a permanent index rejection).
      Deletion is never abandoned, so it is still deferred (never hot-looped) and
      surfaced for operators rather than silently dropped.
    """

    RESOLVED_ABSENT = "resolved_absent"
    CONVERGED_CURRENT = "converged_current"
    SUPERSEDED = "superseded"
    DEFERRED = "deferred"
    PERMANENT_LOCAL_ERROR = "permanent_local_error"


@dataclass
class DrainSummary:
    """Aggregate counts for one drain command invocation (no content, no ids)."""

    scanned: int = 0
    resolved_absent: int = 0
    converged_current: int = 0
    superseded: int = 0
    deferred: int = 0
    permanent_local_error: int = 0

    def __str__(self) -> str:  # compact, log-safe
        return (
            f"scanned={self.scanned} resolved_absent={self.resolved_absent} "
            f"converged_current={self.converged_current} superseded={self.superseded} "
            f"deferred={self.deferred} permanent_local_error={self.permanent_local_error}"
        )


async def _source_became_live_current(
    service: GraphRAGService, record_id: object
) -> bool:
    """True if the source is now live with non-empty text AND indexing is on.

    Used as a pre-destructive re-check: the branch decision was made from an
    earlier canonical read, and a recreate/edit could have landed since. If the
    source is now a live, non-empty, indexable document, its sidecar doc is (or
    will be) the CURRENT generation and MUST NOT be deleted by the absent branch —
    the drain re-drives through the current-convergence branch instead. Returns
    False on any DB error (do not claim it went live under uncertainty; but the
    caller only DELETES when this is False, so uncertainty is handled by the
    caller treating a DB error as its own defer)."""
    try:
        rows = await repo_query("SELECT * FROM $id", {"id": record_id})
    except Exception:
        raise
    if not rows:
        return False
    full_text = rows[0].get("full_text") or ""
    return bool(full_text.strip()) and bool(service.config.enabled)


async def _converge_to_absent(
    service: GraphRAGService, source_id: str, arm_id: str, record_id: object
) -> DrainOutcome:
    """Ensure NO sidecar document survives for ``source_id``, then resolve.

    Confirms absence FIRST: an already-absent document resolves in a single probe
    with no delete call. Otherwise it issues an idempotent delete and DEFERS,
    re-confirming on a later attempt — because ``deletion_started`` is acceptance,
    not proof (verified live: a background delete can fail and leave the document
    present). The tombstone is resolved ONLY on CONFIRMED absence.

    Before the DESTRUCTIVE delete it RE-CHECKS canonical state (the branch was
    chosen from an earlier read): if the source has since become live + non-empty
    with indexing on, the present doc is the CURRENT generation, so it must not be
    deleted — the drain defers and re-drives through the current branch. This
    closes the recreate-between-read-and-delete race (03B §17.1.2). A confirmed
    absence still resolves safely even if the source was recreated: the deleted
    generation is gone, and the live source's own index job owns the new doc."""
    try:
        absence = await service.confirm_source_document_absent(source_id=source_id)
    except GraphRAGValidationError:
        # Not a canonical source id -> never a valid remote target. Permanent.
        return DrainOutcome.PERMANENT_LOCAL_ERROR
    except GraphRAGError:
        # Sidecar unreachable / not configured -> cannot prove absence -> retry.
        return DrainOutcome.DEFERRED

    if absence is AbsenceState.ABSENT_CONFIRMED:
        resolved = await deletion.resolve_tombstone_cas(source_id, arm_id)
        return (
            DrainOutcome.RESOLVED_ABSENT if resolved else DrainOutcome.SUPERSEDED
        )

    # FOUND or UNKNOWN -> a destructive delete is about to happen. Re-confirm the
    # source is still absent/empty/flag-off first; if it went live+current, do NOT
    # delete the current doc — re-drive through the current branch.
    try:
        if await _source_became_live_current(service, record_id):
            return DrainOutcome.DEFERRED
    except Exception:
        # Could not re-check canonical state -> do not risk a blind delete.
        return DrainOutcome.DEFERRED

    try:
        await service.delete_document_for_source(source_id=source_id)
    except GraphRAGValidationError:
        return DrainOutcome.PERMANENT_LOCAL_ERROR
    except GraphRAGError:
        # Busy / unreachable / misconfigured. Safe to retry: nothing was resolved.
        pass
    return DrainOutcome.DEFERRED


async def _converge_to_current(
    service: GraphRAGService,
    source_id: str,
    arm_id: str,
    record_id: object,
    canonical_text: str,
) -> DrainOutcome:
    """Reindex the live source's CURRENT text (03A delete-then-insert), then resolve.

    Reuses the approved 03A ``index_source`` with a ``confirm_current`` guard so a
    canonical change mid-convergence (edit/delete/redact) does not egress stale
    text. Resolves only on a confirmed current insert (INDEXED); a SUPERSEDED or
    transient result defers so the next attempt re-reads canonical state (which
    may by then be absent/empty and take the delete branch).
    """

    async def _still_current() -> bool:
        latest = await repo_query("SELECT * FROM $id", {"id": record_id})
        if not latest:
            return False
        return (latest[0].get("full_text") or "") == canonical_text

    outcome = await index_source(
        service,
        source_id=source_id,
        canonical_text=canonical_text,
        confirm_current=_still_current,
    )
    if outcome.result is IndexResult.INDEXED:
        # INDEXED means the old generation was deleted (delete-then-insert) and the
        # CURRENT text was accepted — the confidentiality goal (no deleted-
        # generation content survives) is met. Resolve ATOMICALLY with a canonical
        # re-check: the tombstone DELETE-CAS also requires the source to STILL hold
        # exactly the shipped text (source_id.full_text = $expected, one statement),
        # so a redaction/edit/delete landing after the insert cannot bless now-stale
        # sidecar text — it matches zero rows and the tombstone stays pending, and
        # the next attempt takes the empty/absent branch to remove the stale doc.
        # (The async-insert-not-yet-processed case is an AVAILABILITY residual — the
        # canonical source is rebuildable — not a confidentiality one, so INDEXED is
        # a safe resolve point once canonical is confirmed unchanged; this also
        # avoids reindex churn on large sidecars where presence cannot be single-
        # page confirmed.)
        resolved = await deletion.resolve_current_tombstone_cas(
            source_id, arm_id, canonical_text
        )
        # Zero rows = re-armed, already resolved, or canonical changed since the
        # insert -> do NOT treat as done; re-drive (the next attempt re-branches).
        return (
            DrainOutcome.CONVERGED_CURRENT if resolved else DrainOutcome.DEFERRED
        )
    if outcome.result is IndexResult.PERMANENT:
        return DrainOutcome.PERMANENT_LOCAL_ERROR
    # SUPERSEDED (canonical changed) or TRANSIENT -> re-drive next attempt.
    return DrainOutcome.DEFERRED


async def converge_tombstone(
    service: GraphRAGService, tombstone: deletion.DeletionTombstone
) -> DrainOutcome:
    """Drive ONE tombstone toward its correct derived state (never blindly delete).

    Loads the CURRENT canonical Source by its losslessly-built RecordID and
    branches per the §17.1 contract. Never raises for a remote/DB hiccup — those
    become DEFERRED so the caller reschedules; only a genuinely permanent local
    defect returns PERMANENT_LOCAL_ERROR.
    """
    source_id = tombstone.source_id
    arm_id = tombstone.arm_id

    try:
        record_id = record_id_for(source_id, tables=_INDEXABLE_TABLES)
    except GraphRAGValidationError:
        # A tombstone identity that is not a canonical source record id must never
        # be turned into an arbitrary remote delete. No HTTP; surface as permanent.
        # The rejected value is NOT logged: it could be path/URL/token/content-
        # shaped, and this is exactly the boundary that must not leak it.
        logger.error(
            "GraphRAG drain: a tombstone identity is not a canonical source id; "
            "skipping (no remote action taken)"
        )
        return DrainOutcome.PERMANENT_LOCAL_ERROR

    try:
        rows = await repo_query("SELECT * FROM $id", {"id": record_id})
    except Exception as e:
        # Could not determine canonical state (transient DB error) -> retry.
        logger.debug(f"GraphRAG drain: canonical load failed: {type(e).__name__}")
        return DrainOutcome.DEFERRED

    if not rows:
        # Canonical source absent -> the deleted generation's doc must not survive.
        return await _converge_to_absent(service, source_id, arm_id, record_id)

    full_text = rows[0].get("full_text") or ""
    if not full_text.strip():
        # Live but empty/non-indexable -> desired derived state is ABSENT (03A's
        # skipped_no_content does NOT delete, so the drain must, §17.1.2).
        return await _converge_to_absent(service, source_id, arm_id, record_id)

    if not service.config.enabled:
        # Live + non-empty but indexing DISABLED: reindexing would be a Boundary-B
        # egress while the feature is off. Converge the slot to ABSENT instead so
        # stale deleted-generation content is removed with no provider call;
        # normal indexing recreates the current doc when re-enabled.
        return await _converge_to_absent(service, source_id, arm_id, record_id)

    return await _converge_to_current(
        service, source_id, arm_id, record_id, full_text
    )


async def drain_pending_deletions(
    service: GraphRAGService | None = None,
    *,
    drain_config: GraphRAGDrainConfig | None = None,
) -> DrainSummary:
    """Process a BOUNDED, FAIR batch of due tombstones.

    Fairness/boundedness (no OFFSET over a mutating set): each iteration selects
    the currently-due rows (``next_attempt_at <= now``, oldest first) up to the
    batch size; a resolved row is DELETEd and a non-converged row is DEFERRED into
    the future, so both leave the due set and the next selection returns the NEXT
    due rows — never re-reading the same failing rows by position. A persistently
    failing row is pushed out of the due set for ``retry_delay`` seconds, so it
    cannot starve later rows. Total work is hard-capped at ``max_rows``.

    Finite per-row handling: a failure in one tombstone never aborts the batch and
    never raises out of this function; the row is deferred and the loop continues.
    """
    service = service or GraphRAGService()
    cfg = drain_config or load_drain_config()
    summary = DrainSummary()

    while summary.scanned < cfg.max_rows:
        remaining = min(cfg.batch_size, cfg.max_rows - summary.scanned)
        try:
            due = await deletion.list_due_deletions(remaining)
        except Exception as e:
            logger.warning(
                f"GraphRAG drain: due-set query failed: {type(e).__name__}"
            )
            break
        if not due:
            break

        for tombstone in due:
            summary.scanned += 1
            try:
                outcome = await converge_tombstone(service, tombstone)
            except Exception as e:
                # Belt-and-braces: nothing should raise out of converge_tombstone,
                # but a single bad row must never abort the batch.
                logger.warning(
                    f"GraphRAG drain: unexpected error converging a tombstone: "
                    f"{type(e).__name__}"
                )
                outcome = DrainOutcome.DEFERRED

            if outcome is DrainOutcome.RESOLVED_ABSENT:
                summary.resolved_absent += 1
            elif outcome is DrainOutcome.CONVERGED_CURRENT:
                summary.converged_current += 1
            elif outcome is DrainOutcome.SUPERSEDED:
                # Re-armed mid-flight: the new arm is due; leave it for re-drive.
                summary.superseded += 1
            else:
                # DEFERRED / PERMANENT_LOCAL_ERROR: push out of the due set so the
                # batch stays fair (arm-fenced: a concurrent re-arm is left due).
                if outcome is DrainOutcome.PERMANENT_LOCAL_ERROR:
                    summary.permanent_local_error += 1
                else:
                    summary.deferred += 1
                try:
                    # Fenced on arm_id ALONE, so even a malformed source_id row is
                    # moved out of the due set and cannot monopolize the batch.
                    await deletion.defer_tombstone_cas(
                        tombstone.arm_id, cfg.retry_delay_seconds
                    )
                except Exception as e:
                    # Belt-and-braces (e.g. a transient DB error): a defer failure
                    # MUST NOT abort the batch — later tombstones must still progress.
                    logger.warning(
                        f"GraphRAG drain: could not reschedule a tombstone: "
                        f"{type(e).__name__}"
                    )

    logger.info(f"GraphRAG deletion drain complete: {summary}")
    return summary


async def _drain_command_already_queued() -> bool:
    """Best-effort check for an already-queued-but-UNSTARTED drain (status 'new').

    OPTIMISATION ONLY — explicitly NOT a distributed lock, and it deliberately
    considers ONLY ``new`` rows, never ``running``. The command queue marks a job
    ``running`` before execution and never clears it if the worker crashes, so
    treating ``running`` as "active" would let a single crashed drain PERMANENTLY
    suppress every future drain — reintroducing the exact crash-stuck-running
    failure the durable-tombstone re-drive exists to avoid. A healthy running
    drain plus a fresh enqueue is safe (idempotent remote ops + arm_id CAS); this
    guard only avoids piling up unstarted duplicates. Any query error returns
    False (enqueue anyway) rather than suppressing work.
    """
    try:
        rows = await repo_query(
            "SELECT id FROM command "
            "WHERE app = $app AND name = $name AND status = 'new' LIMIT 1",
            {"app": "open_notebook", "name": DRAIN_COMMAND_NAME},
        )
        return len(rows) > 0
    except Exception:
        return False


async def enqueue_drain_if_pending() -> None:
    """If any tombstone is due, enqueue one bounded drain command (best-effort).

    Enqueue-only: the HTTP-capable draining runs on the worker, not in this
    process. Correctness never depends on the dedup guard (see
    ``_drain_command_already_queued``)."""
    if not await deletion.has_due_deletions():
        return
    if await _drain_command_already_queued():
        return
    from surreal_commands import submit_command

    # submit_command uses a blocking DB client; run it off the event loop.
    command_id = await asyncio.to_thread(
        submit_command, "open_notebook", DRAIN_COMMAND_NAME, {}
    )
    logger.debug(f"GraphRAG: enqueued deletion drain command {command_id}")


async def graphrag_drain_wakeup_loop(interval_seconds: float) -> None:
    """Periodic durable-state discovery, hosted by the FastAPI lifespan.

    Runs an immediate first tick (the startup kick), then every
    ``interval_seconds`` re-checks for due tombstones and enqueues a bounded
    drain. This is what makes a raw SurrealQL ``DELETE source`` (which fires no
    Python hook) eventually discovered without a restart or operator action. It
    only ENQUEUES bounded work — it never holds a worker slot and never tight
    loops (the sleep is the sole cadence and the cancellation point). Cancelled
    deterministically on app shutdown.
    """
    logger.info(
        f"GraphRAG deletion-drain wake-up loop started "
        f"(interval={interval_seconds}s)"
    )
    try:
        while True:
            try:
                await enqueue_drain_if_pending()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.warning(
                    f"GraphRAG drain wake-up tick failed: {type(e).__name__}"
                )
            await asyncio.sleep(interval_seconds)
    except asyncio.CancelledError:
        logger.info("GraphRAG deletion-drain wake-up loop cancelled")
        raise
