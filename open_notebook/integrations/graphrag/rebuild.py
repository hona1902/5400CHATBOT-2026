"""GraphRAG-03E REBUILD: operator-triggered canonical convergence dispatcher.

REBUILD re-drives the EXISTING GraphRAG-03A ``graphrag_index_source`` command
(source_id ONLY) over the CURRENT non-empty Open Notebook Sources. It exists
because LightRAG v1.5.6 exposes no ``content_hash``: 03D can only classify an
owned, present, live+non-empty document as PRESENT_UNVERIFIED, never prove its
content is current. 03E is the operator's tool to force that convergence.

What REBUILD is NOT (task §2/§4/§31/§32):

  * NOT a global LightRAG purge — it never deletes the corpus or drops storage.
  * NOT a second index engine — it only ENQUEUES 03A jobs; 03A reloads the CURRENT
    Source, honours current content/deletion/flag, and prevents stale-text
    resurrection (there is no queued text in the payload to resurrect).
  * NOT a deletion orchestrator — empty/should-be-absent sources are REPORTED
    (Option A), never armed here; their cleanup stays with 03D REPAIR -> 03B/03C.
  * NOT automatic — never runs at startup, on a schedule, or after migrations. It
    is a Boundary-B-scale egress event (indexing forwards text to an LLM), so it
    must be explicitly operator-triggered and is synthetic-only until Boundary B
    is approved for real internal data.

Two modes:

  PLAN (default): strictly read-only. Enumerate canonical sources by keyset,
    classify empty/non-empty, count. NO health probe, NO enqueue, NO remote call,
    NO mutation, NO content in the result/logs. Works from the canonical DB alone.

  EXECUTE (explicit): validate cursor -> require flag ON + base_url -> content-free
    ``GET /health`` preflight -> bounded keyset sweep enqueuing a source_id-only
    03A index per non-empty source. A failed preflight yields ZERO partial
    dispatch. Once dispatch begins, a later sidecar/provider failure is an 03A
    execution outcome; 03E never claims those jobs indexed.

Completion semantics (task §11/§12 + the operator's terminology guard):
    ``REBUILD_DISPATCH_COMPLETE`` means ONLY that the canonical sources discovered
    within THIS sweep / continuation were fully processed for dispatch, with no
    continuation remaining and no enqueue failure. It does NOT mean a globally
    atomic rebuild, that every source that ever existed was covered, that every
    enqueued 03A job completed (INDEX_COMMAND_COMPLETION), or that remote content
    equality was verified (REMOTE_CONTENT_CONVERGENCE_VERIFIED — impossible on
    v1.5.6). Sources may be created/updated/deleted/emptied during the sweep; a
    source created behind an already-passed cursor is outside this sweep's
    coverage (normal 03A ingestion indexes it anyway).

Fairness / continuation (task §14/§23/§24): bounded by ``max_sources_per_run``.
    Hitting the cap with more canonical rows still available yields
    ``continuation_required=True`` + a ``next_cursor`` (a canonical source RecordID
    string, no content). The operator re-invokes with that cursor. Traversal is
    keyset (``id > $last``), never OFFSET; the cursor is a real ``RecordID`` rebuilt
    via ``record_id_for`` (a bound string compares non-strict on SurrealDB v2.6.5
    and misreads live sources as absent). An invalid cursor fails closed before any
    dispatch. numeric ``source:123`` and string-numeric ``source:⟨123⟩`` stay
    distinct.

Result is a typed summary of counts + capped sample identities only. No document
text, titles, urls, file paths, credentials, or raw provider payloads ever appear
in the result or logs. Enqueue acceptance is never counted as successful indexing.
"""

import asyncio
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from loguru import logger

from open_notebook.database.repository import repo_query
from open_notebook.integrations.graphrag.config import (
    GraphRAGRebuildConfig,
    load_rebuild_config,
)
from open_notebook.integrations.graphrag.models import (
    _INDEXABLE_TABLES,
    GraphRAGValidationError,
    record_id_for,
)
from open_notebook.integrations.graphrag.service import GraphRAGService

#: Name of the surreal-commands rebuild command (registered in commands/).
REBUILD_COMMAND_NAME = "graphrag_rebuild"

#: The 03A index command rebuild enqueues (source_id ONLY). REBUILD never invents a
#: rebuild-specific command and never passes full_text.
INDEX_COMMAND_NAME = "graphrag_index_source"

#: Modes.
PLAN = "plan"
EXECUTE = "execute"

# Completion vocabulary (task terminology guard). Deliberately NONE of these words
# claim remote convergence or per-job completion.
DISPATCH_COMPLETE = "REBUILD_DISPATCH_COMPLETE"  # sweep fully dispatched; no more rows
DISPATCH_INCOMPLETE = "DISPATCH_INCOMPLETE"  # cap hit; continue with next_cursor
DISPATCH_PARTIAL = "DISPATCH_PARTIAL"  # an enqueue/enumeration error occurred
PLAN_ONLY = "PLAN_ONLY"  # read-only plan; nothing dispatched
SKIPPED_DISABLED = "SKIPPED_DISABLED"  # execute requested but flag OFF
SKIPPED_NOT_CONFIGURED = "SKIPPED_NOT_CONFIGURED"  # execute requested but no base_url
PREFLIGHT_FAILED = "PREFLIGHT_FAILED"  # content-free health preflight failed
INVALID_CURSOR = "INVALID_CURSOR"  # continuation cursor did not validate
SKIPPED_EXECUTE_NOT_ALLOWED = "SKIPPED_EXECUTE_NOT_ALLOWED"  # dedicated execute lock OFF


@dataclass
class RebuildSummary:
    """Typed aggregate result of one rebuild run (counts + capped samples only).

    Carries NO document content and NO raw payloads. Sample identities are record
    ids (opaque), capped per class. ``enqueued`` counts ACCEPTED enqueue calls, NOT
    successful indexing — convergence is verified elsewhere (and cannot be proven
    remotely on v1.5.6).
    """

    mode: str  # "plan" | "execute"
    # Gating / preflight (execute).
    execute_allowed: bool = False  # execute lock ON AND flag ON AND base_url set
    execute_not_allowed: bool = False  # execute requested but the dedicated lock is OFF
    skipped_disabled: bool = False  # execute requested, flag OFF
    skipped_not_configured: bool = False  # execute requested, base_url unset
    preflight_unhealthy: bool = False  # content-free health preflight failed
    invalid_cursor: bool = False  # continuation cursor did not validate
    # Enumeration.
    canonical_scanned: int = 0
    eligible_nonempty: int = 0  # live + non-empty text -> a dispatch candidate
    empty: int = 0  # live + empty/whitespace text -> desired ABSENT (Option A: reported)
    vanished: int = 0  # deleted between enumeration and its state read
    # Dispatch.
    planned: int = 0  # PLAN: would-enqueue count (== eligible_nonempty)
    enqueued: int = 0  # EXECUTE: source_id-only 03A jobs accepted by the queue
    enqueue_failures: int = 0  # submit_command raised (never counted as indexed)
    # Continuation / completion.
    continuation_required: bool = False
    next_cursor: Optional[str] = None  # canonical source RecordID string, no content
    errors: int = 0
    notes: str = ""
    samples: Dict[str, List[str]] = field(default_factory=dict)
    max_sample_ids: int = 20

    def add_sample(self, key: str, value: str) -> None:
        bucket = self.samples.setdefault(key, [])
        if value and len(bucket) < self.max_sample_ids:
            bucket.append(value)

    @property
    def completion(self) -> str:
        """Honest headline status for this run (see module docstring)."""
        if self.invalid_cursor:
            return INVALID_CURSOR
        if self.mode != EXECUTE:
            return PLAN_ONLY
        if self.execute_not_allowed:
            return SKIPPED_EXECUTE_NOT_ALLOWED
        if self.skipped_disabled:
            return SKIPPED_DISABLED
        if self.skipped_not_configured:
            return SKIPPED_NOT_CONFIGURED
        if self.preflight_unhealthy:
            return PREFLIGHT_FAILED
        # A failure anywhere in the sweep blocks a clean "complete" claim.
        if self.enqueue_failures > 0 or self.errors > 0:
            return DISPATCH_PARTIAL
        if self.continuation_required:
            return DISPATCH_INCOMPLETE
        return DISPATCH_COMPLETE

    @property
    def dispatch_complete(self) -> bool:
        """True ONLY for a clean, fully-dispatched, non-continued EXECUTE sweep."""
        return self.completion == DISPATCH_COMPLETE

    def __str__(self) -> str:  # compact, log-safe (no content)
        return (
            f"mode={self.mode} completion={self.completion} "
            f"scanned={self.canonical_scanned} eligible={self.eligible_nonempty} "
            f"empty={self.empty} vanished={self.vanished} planned={self.planned} "
            f"enqueued={self.enqueued} enqueue_failures={self.enqueue_failures} "
            f"continuation={self.continuation_required} errors={self.errors}"
        )


async def _canonical_state(record_id: object) -> str:
    """Return CURRENT canonical state: "absent" | "empty" | "nonempty".

    Mirrors 03A/03C/03D ``.strip()`` semantics so every lifecycle agrees on what
    "empty" means. Only ``full_text`` is read, and ONLY to decide empty/non-empty —
    it is never transmitted, logged, or returned. One record at a time keeps memory
    bounded regardless of corpus size (never ``SELECT * FROM source``).
    """
    rows = await repo_query("SELECT full_text FROM $id", {"id": record_id})
    if not rows:
        return "absent"
    full_text = rows[0].get("full_text") or ""
    return "nonempty" if full_text.strip() else "empty"


async def _fetch_ids(last_id: object, limit: int) -> list[Any]:
    """Keyset page of canonical source ids (``id > $last`` when continuing).

    Never OFFSET: offset paging over a mutating canonical table can skip rows. The
    cursor is bound as a RecordID by the caller.
    """
    if last_id is None:
        return await repo_query(
            "SELECT VALUE id FROM source ORDER BY id ASC LIMIT $n", {"n": limit}
        )
    return await repo_query(
        "SELECT VALUE id FROM source WHERE id > $last ORDER BY id ASC LIMIT $n",
        {"last": last_id, "n": limit},
    )


async def _enqueue_index(source_id: str, summary: RebuildSummary) -> bool:
    """Enqueue a source_id-ONLY 03A index job (the worker reloads CURRENT source).

    This is an ENQUEUE, not an index engine: it submits the existing
    ``graphrag_index_source`` command with a source_id-only payload. Returns True
    iff the submit was ACCEPTED (queued) — never "indexed". A submit failure is
    counted as an enqueue_failure and returns False so the sweep can fail-stop
    without advancing the resumable cursor past an un-dispatched source.
    """
    from surreal_commands import submit_command

    try:
        # submit_command uses a blocking DB client; keep it off the event loop.
        await asyncio.to_thread(
            submit_command, "open_notebook", INDEX_COMMAND_NAME, {"source_id": source_id}
        )
        summary.enqueued += 1
        return True
    except Exception as e:
        summary.enqueue_failures += 1
        logger.warning(f"GraphRAG rebuild: index enqueue failed: {type(e).__name__}")
        return False


def _halt(summary: RebuildSummary, last_good_id: object, note: str) -> None:
    """Stop the sweep on a per-row failure WITHOUT advancing the resumable cursor
    past the un-handled source (Codex A/C).

    The ONLY value ever offered as a continuation cursor is ``last_good_id`` — the
    id of the last source that was FULLY handled (dispatched, or classified
    empty/vanished/planned). A resume therefore re-fetches strictly AFTER the last
    good source, so the source that failed is RE-ATTEMPTED, never skipped, and the
    run is DISPATCH_PARTIAL (errors/enqueue_failures > 0 ⇒ never COMPLETE). If no
    source was fully handled yet, ``last_good_id`` is None → no cursor → resume from
    the beginning (03A is idempotent, so re-dispatch is safe).
    """
    summary.notes = note
    summary.continuation_required = True
    summary.next_cursor = str(last_good_id) if last_good_id is not None else None


async def _sweep(
    service: GraphRAGService,
    cfg: GraphRAGRebuildConfig,
    summary: RebuildSummary,
    *,
    start_cursor: object,
    execute: bool,
) -> None:
    """Bounded keyset sweep of canonical sources.

    For each source: classify CURRENT state; a non-empty source is a dispatch
    candidate (PLAN counts it as ``planned``; EXECUTE enqueues a source_id-only 03A
    job). Empty/absent sources are reported (Option A), never dispatched. Bounded by
    ``max_sources_per_run``; hitting the cap CLEANLY (no per-row failure) with more
    rows available sets ``continuation_required`` + ``next_cursor`` — a single keyset
    look-ahead distinguishes "exactly cap" (complete) from "more remain" so N==cap
    never false-signals continuation and N==cap+1 never false-completes.

    Per-row failure semantics (Codex A/C): the resumable cursor tracks
    ``last_good_id`` — the last FULLY-handled source — and is the ONLY value ever
    issued as ``next_cursor``. On the first per-row failure (invalid id, canonical
    state read error, or enqueue failure) the sweep FAILS STOP: it never advances the
    cursor past an un-handled source, so a resume re-attempts that source rather than
    skipping it, and the run is DISPATCH_PARTIAL — never COMPLETE.
    """
    last_id = start_cursor  # traversal position (RecordID | None)
    # The last FULLY-handled source id — the only value ever issued as a resumable
    # cursor. Starts at the incoming cursor: nothing before it belongs to this run.
    last_good_id = start_cursor
    while summary.canonical_scanned < cfg.max_sources_per_run:
        remaining = cfg.max_sources_per_run - summary.canonical_scanned
        limit = min(cfg.canonical_batch_size, remaining)
        try:
            raw_rows = await _fetch_ids(last_id, limit)
        except Exception as e:
            # Enumeration failed: PARTIAL, resumable only from the last good source.
            summary.errors += 1
            _halt(
                summary, last_good_id,
                f"halted at a canonical enumeration error: {type(e).__name__}",
            )
            logger.warning(
                f"GraphRAG rebuild: canonical enumeration failed: {type(e).__name__}"
            )
            return
        if not raw_rows:
            return  # canonical corpus exhausted within the cap -> complete (no cursor)
        for raw in raw_rows:
            summary.canonical_scanned += 1
            try:
                rid = record_id_for(str(raw), tables=_INDEXABLE_TABLES)
            except GraphRAGValidationError:
                # A structurally invalid id in the `source` table is a "can't happen"
                # corruption we cannot build a lossless keyset cursor for. Fail closed:
                # halt, surface the offending id as a remediation target, and resume
                # only from the last good source (never past this row). (Codex A.)
                summary.errors += 1
                summary.add_sample("invalid_source_id", str(raw))
                _halt(
                    summary, last_good_id,
                    "halted at a structurally invalid source id; see samples",
                )
                logger.error(
                    "GraphRAG rebuild: halting sweep at a structurally invalid "
                    "source id (cannot build a safe keyset cursor)"
                )
                return
            last_id = rid
            try:
                state = await _canonical_state(rid)
            except Exception as e:
                # Could not determine canonical state: do NOT advance the cursor past
                # this source (it was not handled). Halt PARTIAL; resume re-attempts it.
                summary.errors += 1
                _halt(
                    summary, last_good_id,
                    f"halted at a canonical state read error: {type(e).__name__}",
                )
                logger.warning(
                    f"GraphRAG rebuild: canonical state read failed: {type(e).__name__}"
                )
                return
            if state == "nonempty":
                summary.eligible_nonempty += 1
                source_id = str(rid)
                if execute:
                    if not await _enqueue_index(source_id, summary):
                        # Enqueue failed: this source was NOT dispatched. Halt without
                        # advancing the cursor past it — a resume re-attempts it, never
                        # skips it (Codex C). DISPATCH_PARTIAL, never COMPLETE.
                        _halt(
                            summary, last_good_id,
                            "halted at an enqueue failure; the failed source is "
                            "re-attempted on resume (not skipped)",
                        )
                        return
                else:
                    summary.planned += 1
                    summary.add_sample("planned", source_id)
            elif state == "empty":
                summary.empty += 1
                summary.add_sample("empty", str(rid))
            else:  # absent -> deleted between enumeration and state read
                summary.vanished += 1
            # This source is FULLY handled -> the cursor may safely advance past it.
            last_good_id = rid
        if len(raw_rows) < limit:
            return  # short page -> canonical corpus exhausted -> complete (no cursor)
    # Cap reached with NO per-row failure (any failure returns above): last_good_id
    # == last_id. Distinguish "exactly the cap" (complete) from "more remain"
    # (continuation) with a single keyset look-ahead past the last handled source.
    if last_good_id is not None:
        try:
            more = await _fetch_ids(last_good_id, 1)
        except Exception as e:
            # Cannot confirm whether more remain: fail toward continuation (never
            # claim complete while more rows might exist) and record the error.
            summary.errors += 1
            _halt(
                summary, last_good_id,
                f"continuation look-ahead failed: {type(e).__name__}",
            )
            logger.warning(
                f"GraphRAG rebuild: continuation look-ahead failed: {type(e).__name__}"
            )
            return
        if more:
            summary.continuation_required = True
            summary.next_cursor = str(last_good_id)


async def rebuild(
    service: GraphRAGService | None = None,
    *,
    mode: str = PLAN,
    cursor: Optional[str] = None,
    rebuild_config: GraphRAGRebuildConfig | None = None,
) -> RebuildSummary:
    """Run one bounded rebuild pass. PLAN (default) is read-only; EXECUTE dispatches.

    Order for EXECUTE (task preflight requirement): validate cursor -> validate
    flag/config -> content-free health preflight -> bounded keyset sweep/dispatch.
    A failed gate/preflight returns BEFORE any enumeration or dispatch, so a
    disabled/unconfigured/unhealthy rebuild causes zero partial egress.
    """
    service = service or GraphRAGService()
    cfg = rebuild_config or load_rebuild_config()
    mode_norm = EXECUTE if str(mode).strip().lower() == EXECUTE else PLAN
    summary = RebuildSummary(mode=mode_norm)
    summary.max_sample_ids = cfg.max_sample_ids
    # A real EXECUTE dispatch requires ALL of: the dedicated execute lock, the
    # GraphRAG flag, and a base_url. The execute lock is deliberately separate from
    # the flag so enabling GraphRAG for ordinary ingestion does not also unlock a
    # corpus-wide, Boundary-B-scale rebuild.
    summary.execute_allowed = bool(
        cfg.execute_enabled and service.config.enabled and service.config.base_url
    )
    execute = mode_norm == EXECUTE

    # 1. Validate the continuation cursor FIRST (both modes). Fail closed: an invalid
    #    cursor never enumerates and never dispatches. A canonical source RecordID is
    #    the only accepted form (no content, numeric vs string-numeric preserved).
    start_cursor = None
    if cursor is not None and str(cursor).strip():
        try:
            start_cursor = record_id_for(str(cursor), tables=_INDEXABLE_TABLES)
        except GraphRAGValidationError:
            summary.invalid_cursor = True
            summary.errors += 1
            summary.notes = "invalid continuation cursor; refused before any dispatch"
            logger.warning("GraphRAG rebuild: invalid continuation cursor; failing closed")
            return summary

    # 2. EXECUTE gating + preflight, BEFORE any enumeration or dispatch.
    if execute:
        # 2a. Dedicated execute lock FIRST — a Boundary-B-scale corpus rebuild must
        #     never be an accidental side effect of GraphRAG being enabled for
        #     ordinary ingestion. Default OFF; the sidecar is never even probed while
        #     locked. This lock does NOT approve Boundary B for real internal data —
        #     it only keeps EXECUTE an explicit, deliberate operator action.
        if not cfg.execute_enabled:
            summary.execute_not_allowed = True
            summary.notes = (
                "GraphRAG rebuild EXECUTE is locked "
                "(OPEN_NOTEBOOK_GRAPHRAG_REBUILD_EXECUTE_ENABLED is not set); "
                "no enumeration, no dispatch, no sidecar probe"
            )
            logger.warning(f"GraphRAG rebuild: {summary}")
            return summary
        if not service.config.enabled:
            summary.skipped_disabled = True
            summary.notes = "GraphRAG indexing disabled; execute is a no-op (no egress)"
            logger.info(f"GraphRAG rebuild: {summary}")
            return summary
        if not service.config.base_url:
            summary.skipped_not_configured = True
            summary.notes = "GraphRAG base URL unset; refusing partial dispatch"
            logger.warning(f"GraphRAG rebuild: {summary}")
            return summary
        # Content-free liveness preflight (GET /health — no body, no source content).
        try:
            health = await service.health()
            healthy = bool(getattr(health, "healthy", False))
        except Exception as e:
            healthy = False
            logger.warning(f"GraphRAG rebuild: health preflight raised: {type(e).__name__}")
        if not healthy:
            summary.preflight_unhealthy = True
            summary.notes = "sidecar preflight not healthy; refusing dispatch"
            logger.warning(f"GraphRAG rebuild: {summary}")
            return summary

    # 3. Bounded keyset sweep (PLAN counts; EXECUTE dispatches source_id-only).
    await _sweep(service, cfg, summary, start_cursor=start_cursor, execute=execute)

    logger.info(f"GraphRAG rebuild complete: {summary}")
    return summary
