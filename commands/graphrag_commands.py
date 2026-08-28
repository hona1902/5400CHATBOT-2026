"""GraphRAG lifecycle commands (GraphRAG-03A: INDEX / REINDEX).

Only the indexing verb is implemented in this slice. DELETE (durable /
tombstone), RECONCILE, and REBUILD are later slices (GraphRAG-03B → 03E) and are
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
from open_notebook.integrations.graphrag.lifecycle import (
    IndexResult,
    index_source,
)
from open_notebook.integrations.graphrag.models import (
    _INDEXABLE_TABLES,
    GraphRAGValidationError,
    record_id_for,
)
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
