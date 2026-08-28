"""Index/reindex orchestration for the GraphRAG lifecycle (GraphRAG-03A).

This is the seam between the command layer (commands/graphrag_commands.py) and
the LightRAG-specific service/client. It owns the *idempotency semantics* of
indexing a source, and nothing else:

    delete existing sidecar document (if any) -> insert CURRENT canonical text

It does NOT decide *when* to index (the command + save_source seam do), does not
reload the Source (the command does, so this function is handed already-current
text), and does not know LightRAG's HTTP contract (the service/client do).

Why delete-then-insert. Verified against LightRAG v1.5.6: re-POSTing the same
file_source is rejected as a filename duplicate rather than updating in place
(pipeline.py:1121-1170). So the only way to make REINDEX reflect changed
content is to remove the old document first. The document id is deterministic
and content-independent (client.compute_doc_id), so the delete reliably targets
the right document even across content edits.

Why the internal delete is fail-CLOSED here (not fail-open). This module is only
ever reached from inside an index job that is about to insert. If the delete
cannot be confirmed, inserting anyway would either duplicate-conflict (409) or
leave stale content, so an unconfirmed delete must block the insert and let the
command's retry layer re-drive the whole operation. This is NOT the durable
lifecycle DELETE (GraphRAG-03B/03C) and sets no "best-effort delete is fine"
precedent: the fail-open contract that matters for ingestion lives at the
save_source enqueue seam, not here.
"""

from dataclasses import dataclass
from enum import Enum
from typing import Awaitable, Callable, Optional

from loguru import logger

from open_notebook.integrations.graphrag.models import (
    DeleteState,
    GraphRAGConflictError,
    GraphRAGError,
    GraphRAGRequestError,
    GraphRAGServerError,
    GraphRAGUnavailableError,
    GraphRAGValidationError,
)
from open_notebook.integrations.graphrag.service import GraphRAGService


class IndexResult(str, Enum):
    """Terminal classification of an index/reindex attempt.

    - INDEXED: the current canonical text was accepted by the sidecar.
    - SUPERSEDED: canonical state changed or was removed between the caller's
      read and the moment of insert (checked via ``confirm_current``); we
      deliberately did NOT insert the now-stale text. Terminal, not an error.
    - TRANSIENT: a retryable failure (sidecar down/slow/busy, delete not yet
      materialized, 5xx, malformed response). The caller should raise so the
      command retry layer re-drives.
    - PERMANENT: a non-retryable failure (the sidecar rejected the request as
      malformed / the value was refused before egress). The caller should NOT
      retry.
    """

    INDEXED = "indexed"
    SUPERSEDED = "superseded"
    TRANSIENT = "transient"
    PERMANENT = "permanent"


@dataclass(frozen=True)
class IndexOutcome:
    result: IndexResult
    detail: str
    track_id: str | None = None


async def index_source(
    service: GraphRAGService,
    *,
    source_id: str,
    canonical_text: str,
    confirm_current: Optional[Callable[[], Awaitable[bool]]] = None,
) -> IndexOutcome:
    """Idempotently (re)index one source's CURRENT canonical text.

    Steps:
      1. Re-confirm canonical state (``confirm_current``) BEFORE any destructive
         action. If it fails, we return SUPERSEDED having touched nothing.
      2. Delete the existing sidecar document for this source_id, if any.
         ``GONE`` (deletion_started or not_found) is the only outcome that lets
         us proceed — both mean "old copy is being/already removed". ``BUSY`` /
         ``REFUSED`` / any delete failure is transient: we do NOT insert into a
         racing or non-empty destructive slot.
      3. Re-confirm AGAIN, immediately before egress (the delete was a network
         round-trip during which the source could change).
      4. Insert the current canonical text.

    ``confirm_current`` guards TWO distinct TOCTOU races, which is why it is
    checked twice:
      - BEFORE the delete: if the source changed/vanished before we started, a
        superseded job must perform NO delete and NO insert — otherwise it would
        DESTROY the document a newer, already-completed index job just wrote and
        leave the graph empty.
      - AFTER the delete, before the POST: the delete round-trip is a window in
        which the source can be redacted/deleted; re-confirming here prevents
        shipping now-stale (possibly redacted/deleted) text. A SUPERSEDED at this
        point is safe — we removed only our own stale document and do not
        reinsert.
    The only irreducible residual is a change in the sub-step gap between the
    second confirm and the HTTP insert (no transactional sidecar exists); that,
    plus the ack-lost-then-deleted case, is covered by durable DELETE + RECONCILE
    (03-B/03-D) and query-time validation (AGR-005 §8). When ``confirm_current``
    is omitted (e.g. a manual call), behavior is unchanged from a plain
    (re)index.

    The insert uses the same flag + allowlist guards as every other outbound
    call (service.index_source -> build_sidecar_document -> validate_source_id).
    """
    # 1. Confirm canonical state is still current BEFORE any destructive action.
    #    A stale/superseded job must NOT delete: a newer job may have already
    #    written the current document, and deleting-then-returning-superseded
    #    would erase it (found in adversarial review). So we short-circuit here,
    #    having mutated nothing.
    if confirm_current is not None:
        try:
            still_current = await confirm_current()
        except Exception as e:
            # Could not confirm (e.g. transient DB error) -> take no destructive
            # action; retry.
            return IndexOutcome(IndexResult.TRANSIENT, f"confirm failed: {e}")
        if not still_current:
            return IndexOutcome(
                IndexResult.SUPERSEDED,
                "canonical state changed; not indexing stale text and not deleting",
            )

    # 2. Remove any existing document so the re-insert is not rejected as a
    #    filename duplicate.
    try:
        delete_outcome = await service.delete_document_for_source(source_id=source_id)
    except GraphRAGValidationError as e:
        # A deterministic, non-retryable failure: the source_id is not a valid
        # canonical record id and never will be. It also guarantees ZERO egress
        # (validate_source_id rejects before any HTTP). Classify PERMANENT here,
        # exactly as the insert step does — the same invalid id must not be
        # TRANSIENT in one step and PERMANENT in the other. (The command
        # pre-validates, so reaching here is defensive; the lifecycle function's
        # contract must still stand on its own.)
        return IndexOutcome(IndexResult.PERMANENT, f"invalid source_id: {e}")
    except GraphRAGConflictError as e:
        # Shouldn't occur on a DELETE, but if upstream ever surfaces one, it is
        # transient by definition.
        return IndexOutcome(IndexResult.TRANSIENT, f"delete conflict: {e}")
    except (GraphRAGUnavailableError, GraphRAGServerError) as e:
        return IndexOutcome(IndexResult.TRANSIENT, f"delete unavailable: {e}")
    except GraphRAGError as e:
        # Remaining errors (config/protocol). Classified conservatively as
        # transient so a misconfigured or hiccuping sidecar heals on retry
        # rather than silently dropping the index. Validation is handled above,
        # so it can never be mis-classified transient here.
        return IndexOutcome(IndexResult.TRANSIENT, f"delete failed: {e}")

    if delete_outcome.state is DeleteState.BUSY:
        return IndexOutcome(
            IndexResult.TRANSIENT,
            "sidecar busy during pre-insert delete; will retry",
        )
    if delete_outcome.state is DeleteState.REFUSED:
        return IndexOutcome(
            IndexResult.TRANSIENT,
            f"sidecar refused pre-insert delete: {delete_outcome.detail}",
        )
    # delete_outcome.state is GONE -> safe to insert.

    # 3. Re-confirm IMMEDIATELY before egress. The delete above is a network
    #    round-trip; the source can be updated/redacted/deleted during it. This
    #    second check shrinks the stale-egress window to the irreducible gap
    #    between here and the POST below. (The FIRST confirm cannot cover this —
    #    it ran before the delete round-trip. The two confirms guard two
    #    distinct races: pre-delete prevents erasing a newer job's document;
    #    pre-insert prevents shipping text that went stale during the delete.)
    #    A SUPERSEDED here is safe: we deleted our own stale doc and simply do
    #    not reinsert; the newer state has its own index job, and
    #    RECONCILE/REBUILD (03-D/03-E) reconcile any residual.
    if confirm_current is not None:
        try:
            still_current = await confirm_current()
        except Exception as e:
            return IndexOutcome(IndexResult.TRANSIENT, f"confirm failed: {e}")
        if not still_current:
            return IndexOutcome(
                IndexResult.SUPERSEDED,
                "canonical state changed after delete; not egressing stale text",
            )

    # 4. Insert current canonical text.
    try:
        ack = await service.index_source(
            source_id=source_id, canonical_text=canonical_text
        )
    except GraphRAGConflictError as e:
        # The old document has not finished deleting yet (async delete). Retry
        # rather than fail permanently.
        return IndexOutcome(IndexResult.TRANSIENT, f"insert conflict (async delete not settled): {e}")
    except (GraphRAGUnavailableError, GraphRAGServerError) as e:
        return IndexOutcome(IndexResult.TRANSIENT, f"insert unavailable: {e}")
    except (GraphRAGRequestError, GraphRAGValidationError) as e:
        # A request-level rejection (422 schema) or a refusal-before-egress is
        # deterministic and permanent — retrying cannot change the outcome.
        return IndexOutcome(IndexResult.PERMANENT, f"insert rejected: {e}")
    except GraphRAGError as e:
        # Any other integration error (protocol/config) we treat as transient so
        # a transient hiccup heals on retry.
        return IndexOutcome(IndexResult.TRANSIENT, f"insert failed: {e}")

    if not ack.accepted:
        # Accepted-but-not-really: treat as transient so it re-drives.
        logger.warning(f"GraphRAG index not accepted for {source_id}: {ack.detail}")
        return IndexOutcome(
            IndexResult.TRANSIENT, f"insert not accepted: {ack.detail}", ack.track_id
        )

    return IndexOutcome(IndexResult.INDEXED, ack.detail, ack.track_id)
