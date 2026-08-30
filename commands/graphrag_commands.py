"""GraphRAG lifecycle commands (03A INDEX/REINDEX, 03C DRAIN, 03D RECONCILE).

Implements the indexing verb (03A), the durable deletion drain (03C), and the
defense-in-depth reconcile (03D). REBUILD is a later slice (03E) and is
deliberately absent.

Design invariants (see docs/agribank/development/GRAPHRAG_03A_INDEXING.md):

- **Identity is source_id, never queued text.** The command payload carries only
  ``source_id``. At execution the worker reloads the CURRENT Source from
  SurrealDB and indexes its CURRENT ``full_text``. An older job that runs after a
  newer save therefore indexes current state; it cannot resurrect stale text,
  because the payload has no text to resurrect.
- **Canonical is the source of truth.** If the source no longer exists (deleted
  before this job ran), the job is a safe no-op — nothing stale is indexed.
- **Fail-open for ingestion.** This command runs off the ingestion path; whether
  it succeeds or fails, canonical source processing and vector RAG are
  unaffected (the enqueue seam in graphs/source.py never raises).
- **Flag-gated.** With OPEN_NOTEBOOK_GRAPHRAG_ENABLED off, the command makes no
  external call and completes as skipped.

LightRAG HTTP specifics live entirely in open_notebook/integrations/graphrag/;
this module orchestrates and never imports the LightRAG package.
"""

import time
from typing import Optional

from loguru import logger
from surreal_commands import CommandInput, CommandOutput, command

from open_notebook.database.repository import repo_query
from open_notebook.domain.notebook import Source
from open_notebook.exceptions import ConfigurationError
from open_notebook.integrations.graphrag.drain import drain_pending_deletions
from open_notebook.integrations.graphrag.lifecycle import (
    IndexResult,
    index_source,
)
from open_notebook.integrations.graphrag.models import (
    _INDEXABLE_TABLES,
    GraphRAGValidationError,
    record_id_for,
)
from open_notebook.integrations.graphrag.reconcile import reconcile
from open_notebook.integrations.graphrag.service import GraphRAGService

# Mirror the embedding commands' retry posture: retry transient sidecar/queue
# failures with jittered backoff, but never retry validation/config errors.
# TRANSIENT lifecycle outcomes are surfaced by raising (below), so the retry
# layer re-drives them; PERMANENT outcomes and skips return success/handled
# without raising.
GRAPHRAG_INDEX_RETRY_CONFIG = {
    "max_attempts": 5,
    "wait_strategy": "exponential_jitter",
    "wait_min": 1,
    "wait_max": 60,
    "stop_on": [ValueError, ConfigurationError],
    "retry_log_level": "debug",
}


class GraphRAGIndexInput(CommandInput):
    # NOTE: source_id ONLY. No full_text. This is the structural guarantee
    # against stale-text resurrection — there is no queued text field to carry
    # an outdated document body.
    source_id: str


class GraphRAGIndexOutput(CommandOutput):
    success: bool
    source_id: str
    # One of: "indexed", "superseded", "skipped_disabled", "skipped_absent",
    # "skipped_no_content", "permanent_failure".
    outcome: str
    processing_time: float
    track_id: Optional[str] = None
    error_message: Optional[str] = None


@command("graphrag_index_source", app="open_notebook", retry=GRAPHRAG_INDEX_RETRY_CONFIG)
async def graphrag_index_source_command(
    input_data: GraphRAGIndexInput,
) -> GraphRAGIndexOutput:
    """(Re)index the CURRENT state of one source into the LightRAG sidecar."""
    start_time = time.time()
    source_id = input_data.source_id

    def done(
        *, success: bool, outcome: str, track_id: Optional[str] = None,
        error_message: Optional[str] = None,
    ) -> GraphRAGIndexOutput:
        return GraphRAGIndexOutput(
            success=success,
            source_id=source_id,
            outcome=outcome,
            processing_time=time.time() - start_time,
            track_id=track_id,
            error_message=error_message,
        )

    # Flag gate FIRST, before any DB or network work. A job queued while the
    # feature was on but executed after it was turned off completes as a clean
    # skip: it makes no external call and is terminal (not left retryable), so a
    # disabled feature never holds queue entries. Any gap is reconciled by
    # REBUILD/RECONCILE in later slices.
    service = GraphRAGService()
    # Distinguish the FLAG from full configuration. `enabled` (the flag) being
    # OFF is an intentional, terminal skip. But flag-ON-yet-misconfigured
    # (e.g. BASE_URL unset) is NOT a clean skip: it is a transient
    # misconfiguration that a later fix should let the job re-drive, so we must
    # not swallow it as `skipped_disabled`. `service.enabled` returns
    # `configured` (flag AND base_url), so gate on the raw flag here.
    if not service.config.enabled:
        logger.debug(
            f"graphrag_index_source skipped for {source_id}: GraphRAG disabled"
        )
        return done(success=True, outcome="skipped_disabled")
    if not service.config.base_url:
        # Flag on but not configured — retryable, not a terminal skip.
        logger.warning(
            f"graphrag_index_source: enabled but OPEN_NOTEBOOK_GRAPHRAG_BASE_URL "
            f"is unset; deferring {source_id} for retry"
        )
        raise RuntimeError("GraphRAG enabled but base URL not configured")

    # Validate the id BEFORE loading — a structurally invalid source_id can
    # never index and must not reach the sidecar. Permanent, no retry.
    #
    # Build the RecordID OBJECT losslessly here (record_id_for), NOT via
    # ensure_record_id(source_id): ensure_record_id -> RecordID.parse()
    # double-escapes an already-escaped identifier (source:⟨123⟩ ->
    # source:⟨⟨123\⟩⟩), which would bind the WRONG record and terminally skip a
    # live escaped-id source as "absent" — a numeric vs string-numeric identity
    # violation. record_id_for shares validate_source_id's structural logic and
    # returns the correctly-built object. Source.get() has the same
    # parse-hazard, so we also load by this object rather than by the string.
    try:
        record_id = record_id_for(source_id, tables=_INDEXABLE_TABLES)
    except GraphRAGValidationError as e:
        logger.error(f"graphrag_index_source refusing invalid source_id: {e}")
        return done(
            success=False, outcome="permanent_failure", error_message=str(e)
        )

    # Load CURRENT canonical state by the losslessly-built RecordID. We must
    # distinguish "genuinely absent" (deleted before this job ran → safe
    # terminal no-op) from "could not determine" (transient DB outage → must
    # retry). A direct query does exactly that: a DB error propagates (→
    # transient retry via the raise below), an empty result is a true absence.
    # (Source.get() collapses both into NotFoundError at base.py:127 AND
    # re-parses the id, so it is unusable for this decision.)
    try:
        rows = await repo_query(
            "SELECT * FROM $id", {"id": record_id}
        )
    except Exception as e:
        # Transient: could not reach/parse the DB. Re-drive rather than skip.
        logger.debug(
            f"graphrag_index_source: existence check failed for {source_id}: {e}"
        )
        raise
    if not rows:
        logger.info(
            f"graphrag_index_source: source {source_id} no longer exists; skipping"
        )
        return done(success=True, outcome="skipped_absent")

    source = Source(**rows[0])

    if not source or not source.full_text or not source.full_text.strip():
        # KNOWN LIMITATION (GraphRAG-03A): if a source that was previously
        # indexed with non-empty text is later reprocessed to empty text, the
        # old sidecar document is NOT removed here. Removing a document because
        # canonical content became empty is a DELETION semantic with a retention
        # guarantee, which is deliberately out of scope for the index/reindex
        # slice — it belongs to the durable DELETE lifecycle (GraphRAG-03B/03C)
        # and to RECONCILE, which will purge such stale/orphaned documents.
        # Emitting a best-effort delete here would set the wrong precedent
        # (AGR-005 §9: deletion must not be best-effort). Documented in
        # docs/agribank/development/GRAPHRAG_03A_INDEXING.md.
        logger.info(
            f"graphrag_index_source: source {source_id} has no text; skipping"
        )
        return done(success=True, outcome="skipped_no_content")

    text_to_index = source.full_text

    async def _still_current() -> bool:
        """Re-read canonical state right before egress; True only if the text we
        are about to send still equals the CURRENT stored full_text.

        Narrows the window in which a delete/redaction/edit that landed after
        the initial read could ship stale (possibly removed) content to the
        sidecar. A missing row (deleted) or changed text returns False → the
        lifecycle reports SUPERSEDED and sends nothing. A DB error propagates so
        we do NOT egress under uncertainty.
        """
        latest = await repo_query("SELECT * FROM $id", {"id": record_id})
        if not latest:
            return False
        current = Source(**latest[0])
        return (current.full_text or "") == (text_to_index or "")

    # Index the CURRENT text (never a queued copy), with a last-moment recheck.
    outcome = await index_source(
        service,
        source_id=source_id,
        canonical_text=text_to_index,
        confirm_current=_still_current,
    )

    if outcome.result is IndexResult.INDEXED:
        logger.info(
            f"graphrag_index_source: indexed {source_id} "
            f"(track_id={outcome.track_id})"
        )
        return done(success=True, outcome="indexed", track_id=outcome.track_id)

    if outcome.result is IndexResult.SUPERSEDED:
        # Canonical state changed before we could insert; the stale text was NOT
        # sent. Terminal success — a newer save has its own index job, and
        # RECONCILE/REBUILD (03-D/03-E) reconcile any residual.
        logger.info(
            f"graphrag_index_source: {source_id} superseded before insert; "
            f"skipped stale content"
        )
        return done(success=True, outcome="superseded")

    if outcome.result is IndexResult.PERMANENT:
        # Do not retry — the sidecar rejected the request itself, or we refused
        # to send it. Return handled (success=False) rather than raising so the
        # retry layer does not re-drive a doomed request.
        logger.error(
            f"graphrag_index_source: permanent failure for {source_id}: "
            f"{outcome.detail}"
        )
        return done(
            success=False, outcome="permanent_failure", error_message=outcome.detail
        )

    # TRANSIENT — raise so surreal-commands' retry layer re-drives. On the next
    # attempt the source is reloaded again, so the retry always targets current
    # state.
    logger.debug(
        f"graphrag_index_source: transient failure for {source_id}: {outcome.detail}"
    )
    raise RuntimeError(f"GraphRAG index transient failure: {outcome.detail}")


# ---------------------------------------------------------------------------
# GraphRAG-03C: durable deletion drain
# ---------------------------------------------------------------------------


class GraphRAGDrainInput(CommandInput):
    # No arguments: the durable graphrag_deletion tombstone table is the
    # source-of-truth work list, NOT the command payload. A drain simply processes
    # whatever is currently due, so it is safe to run any number of times.
    pass


class GraphRAGDrainOutput(CommandOutput):
    success: bool
    scanned: int
    resolved_absent: int
    converged_current: int
    superseded: int
    deferred: int
    permanent_local_error: int
    processing_time: float


# No retry layer: the drain never raises for per-row failures (they are DEFERRED
# in-band via next_attempt_at so one failing tombstone cannot abort the batch),
# and the durable tombstone + the periodic lifespan wake-up ARE the re-drive
# mechanism — not the command queue (which cannot re-drive a crashed 'running'
# job). max_attempts=1 keeps a crashed drain from being retried in place; the next
# wake-up tick re-drives from the durable table.
@command("graphrag_drain_deletions", app="open_notebook", retry={"max_attempts": 1})
async def graphrag_drain_deletions_command(
    input_data: GraphRAGDrainInput,
) -> GraphRAGDrainOutput:
    """Process one bounded, fair batch of due GraphRAG deletion tombstones.

    Runs on the worker (where GraphRAG HTTP egress already lives). Enumerates due
    tombstones, converges each toward its correct derived state (absent / current)
    and resolves confirmed ones by arm_id CAS; unconfirmed rows are deferred. See
    open_notebook/integrations/graphrag/drain.py for the lifecycle and fairness
    guarantees.
    """
    start_time = time.time()
    summary = await drain_pending_deletions()
    return GraphRAGDrainOutput(
        success=True,
        scanned=summary.scanned,
        resolved_absent=summary.resolved_absent,
        converged_current=summary.converged_current,
        superseded=summary.superseded,
        deferred=summary.deferred,
        permanent_local_error=summary.permanent_local_error,
        processing_time=time.time() - start_time,
    )


# ---------------------------------------------------------------------------
# GraphRAG-03D: defense-in-depth reconcile (drift detection + safe repair)
# ---------------------------------------------------------------------------


class GraphRAGReconcileInput(CommandInput):
    # AUDIT is the default (detect/classify only, no mutation). REPAIR must be
    # explicitly requested: it arms durable deletion intents for owned orphans /
    # should-be-absent docs (drained by 03C) and enqueues source_id-only 03A
    # index repairs for authoritatively-missing live sources when indexing is ON.
    # It NEVER deletes remotely itself and NEVER resolves a tombstone.
    repair: bool = False


class GraphRAGReconcileOutput(CommandOutput):
    success: bool
    mode: str
    remote_scanned: int
    owned_present_unverified: int
    owned_orphan: int
    owned_should_be_absent: int
    foreign: int
    unknown_ownership: int
    deletion_intents_armed: int
    deletion_intents_already_pending: int
    canonical_scanned: int
    present_confirmed: int
    missing_confirmed: int
    index_repairs_enqueued: int
    incomplete_inventory: bool
    errors: int
    processing_time: float


# No retry layer: reconcile is a bounded, re-runnable, idempotent sweep whose
# repairs route through the EXISTING durable lifecycle (03C tombstones re-driven
# by the wake-up loop; 03A index jobs with their own retry). A crashed reconcile
# is simply re-run by an operator; there is nothing to re-drive in place, so
# max_attempts=1 keeps the queue from retrying a partial sweep.
@command("graphrag_reconcile", app="open_notebook", retry={"max_attempts": 1})
async def graphrag_reconcile_command(
    input_data: GraphRAGReconcileInput,
) -> GraphRAGReconcileOutput:
    """Run one bounded GraphRAG reconcile pass (AUDIT default; REPAIR opt-in).

    Compares canonical Sources against sidecar documents, classifies drift, and in
    REPAIR mode applies only safe repairs by reusing the 03B/03C deletion lifecycle
    and the 03A index lifecycle. See open_notebook/integrations/graphrag/reconcile.py
    for the classification/ownership contract and the forensic absence limitation.
    """
    start_time = time.time()
    summary = await reconcile(repair=input_data.repair)
    return GraphRAGReconcileOutput(
        success=True,
        mode=summary.mode,
        remote_scanned=summary.remote_scanned,
        owned_present_unverified=summary.owned_present_unverified,
        owned_orphan=summary.owned_orphan,
        owned_should_be_absent=summary.owned_should_be_absent,
        foreign=summary.foreign,
        unknown_ownership=summary.unknown_ownership,
        deletion_intents_armed=summary.deletion_intents_armed,
        deletion_intents_already_pending=summary.deletion_intents_already_pending,
        canonical_scanned=summary.canonical_scanned,
        present_confirmed=summary.present_confirmed,
        missing_confirmed=summary.missing_confirmed,
        index_repairs_enqueued=summary.index_repairs_enqueued,
        incomplete_inventory=summary.incomplete_inventory,
        errors=summary.errors,
        processing_time=time.time() - start_time,
    )
