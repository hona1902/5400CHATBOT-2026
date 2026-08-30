"""HTTP client for the LightRAG sidecar.

This module is the ONLY place in Open Notebook that knows LightRAG's HTTP
contract: its URLs, field names, status strings, and error shapes. Everything it
returns is a normalized type from models.py, and every failure it raises is a
GraphRAGError subclass - httpx exceptions never escape (AGR-005 §21.4).

Verified against LightRAG v1.5.6 by reading the router source:
  GET  /health                             -> liveness (200 even unauthenticated)
  POST /documents/text                     -> InsertTextRequest -> InsertResponse
  GET  /documents/track_status/{track_id}  -> TrackStatusResponse
  POST /query                              -> QueryRequest -> QueryResponse
Auth is the X-API-Key header (lightrag/api/utils_api.py:400).

Do NOT wire index_document() into source ingestion in this phase - it exists for
manual/synthetic PoC calls only (AGR-005 §21.4, §21.9).
"""

import hashlib
from typing import Any, Dict, List, Optional

import httpx
from loguru import logger

from open_notebook.integrations.graphrag.config import (
    VERIFIED_LIGHTRAG_VERSION,
    GraphRAGConfig,
)
from open_notebook.integrations.graphrag.models import (
    AbsenceState,
    DeleteOutcome,
    DeleteState,
    DocumentsPage,
    GraphQueryResult,
    GraphRAGConfigurationError,
    GraphRAGConflictError,
    GraphRAGError,
    GraphRAGProtocolError,
    GraphRAGRequestError,
    GraphRAGServerError,
    GraphRAGUnavailableError,
    GraphReference,
    HealthResult,
    IndexAck,
    IndexState,
    IndexStatus,
    QueryMode,
    is_valid_record_id,
    normalize_index_state,
)

# LightRAG's paginated listing caps page_size at 200 (verified live: 201 -> 422).
# The absence probe reads a SINGLE page of this size and confirms absence only if
# that one page enumerated the whole set, so 200 is also the single-response
# ceiling above which a corpus yields UNKNOWN (never a false ABSENT_CONFIRMED).
ABSENCE_PROBE_PAGE_SIZE = 200


def compute_doc_id(source_id: str) -> str:
    """Compute LightRAG's document id for a canonical Open Notebook source_id.

    Verified against LightRAG v1.5.6: for a document inserted with a valid
    ``file_source`` (which Open Notebook always supplies), the id is
    ``"doc-" + md5(canonical_file_source)`` — deterministic and independent of
    content (lightrag/pipeline.py:936-946 → utils.compute_mdhash_id →
    compute_args_hash, single-arg md5). ``normalize_document_file_path`` only
    strips ``[hint]`` segments and collapses placeholder sources; a canonical
    Open Notebook record id ("source:...") contains neither, so it is its own
    canonical file-source form.

    Computing this locally means DELETE and RECONCILE never need a list/search
    round trip, and the id does not drift when a source's text changes — which
    is exactly what lets an idempotent delete-then-insert target the right
    document. The caller is responsible for passing an already-validated
    canonical source_id (service.validate_source_id); this function does no
    validation so it cannot mask a bad id as a plausible-looking doc id.
    """
    return "doc-" + hashlib.md5(source_id.encode("utf-8")).hexdigest()

# Tables that may appear as retrieval PROVENANCE. Broader than the indexable set
# (service._INDEXABLE_TABLES == {"source"}): fn::vector_search can return source,
# note, and source_insight rows, so a future hybrid layer may legitimately surface
# all three, even though GraphRAG-02 only ever indexes source documents.
_PROVENANCE_TABLES = frozenset({"source", "note", "source_insight"})


def _looks_like_record_id(value: Optional[str]) -> bool:
    """Whether a returned provenance value is structurally an Open Notebook id.

    Shape only: NOT authorization, and NOT proof the record still exists. Live
    validation against SurrealDB is GraphRAG-05/06 work (AGR-005 §8) and is
    deliberately absent from this diagnostic-only phase.

    Uses the SAME structural validation as outbound ids, widened to the
    provenance table set. A prefix-only check would mark
    ``source:https://internal/doc?token=x``, ``source:../../secret``, and
    ``source:a\\nb`` as resolved=True - a misleading trust signal on exactly the
    path/URL/token-shaped values the boundary exists to distrust. The sidecar may
    hold documents indexed outside this guarded path, or echo corrupted
    provenance, so inbound values get no more benefit of the doubt than outbound
    ones.

    Accepts both canonical presentations - bare (``source:abc123``) and
    SurrealDB-escaped (``source:<0123456789>``) - since str(RecordID) emits the
    escaped form for identifiers that are all-digit or contain characters outside
    [A-Za-z0-9_].
    """
    if not value:
        return False
    return is_valid_record_id(value, tables=_PROVENANCE_TABLES)


class GraphRAGClient:
    """Thin, fail-explicit HTTP client for a LightRAG sidecar.

    Construct per operation (or per request) - it holds no connection state
    beyond the httpx client it opens and closes around each call. An injected
    ``transport`` lets tests exercise every transport-level failure mode without
    a live sidecar and without a new test dependency.
    """

    def __init__(
        self,
        config: GraphRAGConfig,
        transport: Optional[httpx.AsyncBaseTransport] = None,
    ) -> None:
        self._config = config
        self._transport = transport

    def _headers(self) -> Dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self._config.api_key:
            headers["X-API-Key"] = self._config.api_key
        return headers

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=self._config.base_url,
            timeout=self._config.timeout,
            headers=self._headers(),
            transport=self._transport,
        )

    async def _request(self, method: str, path: str, **kwargs: Any) -> Dict[str, Any]:
        """Issue a request and normalize every failure mode.

        Ordering matters: transport failures first (no response exists), then
        HTTP status, then payload shape. The API key is never included in log
        output or in the raised message.
        """
        try:
            async with self._client() as client:
                response = await client.request(method, path, **kwargs)
        except httpx.TimeoutException as e:
            raise GraphRAGUnavailableError(
                f"GraphRAG sidecar timed out after {self._config.timeout}s"
            ) from e
        except httpx.TransportError as e:
            # Covers ConnectError (refused/DNS), ReadError, ProtocolError.
            raise GraphRAGUnavailableError(
                f"GraphRAG sidecar is unreachable: {type(e).__name__}"
            ) from e

        if response.status_code >= 500:
            raise GraphRAGServerError(
                f"GraphRAG sidecar returned HTTP {response.status_code}"
            )
        # Not every 4xx is the caller's fault, and a diagnostic endpoint exists
        # precisely to tell those cases apart. 401/403 mean our X-API-Key is
        # missing or wrong; 404/405 mean the path/method this client was written
        # against is not there - both are sidecar configuration or version
        # mismatches on our side, not bad input from whoever called us.
        if response.status_code in (401, 403):
            raise GraphRAGConfigurationError(
                f"GraphRAG sidecar rejected our credentials with HTTP "
                f"{response.status_code}: check OPEN_NOTEBOOK_GRAPHRAG_API_KEY"
            )
        if response.status_code in (404, 405):
            raise GraphRAGConfigurationError(
                f"GraphRAG sidecar has no {method} {path} (HTTP "
                f"{response.status_code}): likely a version mismatch with "
                f"{VERIFIED_LIGHTRAG_VERSION}"
            )
        if response.status_code == 409:
            # A document for this file_source still exists. During REINDEX this
            # is transient: LightRAG deletion is asynchronous, so an insert
            # issued right after a delete can still see the old document
            # (pipeline.py:1121-1170). Typed separately from 422 so the caller
            # retries rather than failing permanently.
            raise GraphRAGConflictError(
                "GraphRAG sidecar rejected the request with HTTP 409 "
                "(document for this file_source already exists)"
            )
        if response.status_code >= 400:
            # Genuine request-level rejection, e.g. 422 schema validation.
            # Deliberately does not echo the response body, which could contain
            # submitted content.
            raise GraphRAGRequestError(
                f"GraphRAG sidecar rejected the request with HTTP {response.status_code}"
            )

        try:
            payload = response.json()
        except ValueError as e:
            raise GraphRAGProtocolError(
                "GraphRAG sidecar returned a non-JSON response"
            ) from e

        if not isinstance(payload, dict):
            raise GraphRAGProtocolError(
                f"GraphRAG sidecar returned {type(payload).__name__}, expected an object"
            )
        return payload

    async def health(self) -> HealthResult:
        """Probe sidecar liveness.

        Returns a HealthResult instead of raising: a health check that throws is
        useless for rendering status. Upstream keeps GET /health at 200 for
        unauthenticated callers, exposing config only when authenticated, so an
        unauthenticated probe still confirms liveness.
        """
        try:
            payload = await self._request("GET", "/health")
        except GraphRAGUnavailableError as e:
            return HealthResult(healthy=False, detail=str(e))
        except (GraphRAGConfigurationError, GraphRAGRequestError) as e:
            # Reachable but rejecting us - live, yet unusable.
            return HealthResult(healthy=False, detail=str(e))
        except GraphRAGServerError as e:
            return HealthResult(healthy=False, detail=str(e))
        except GraphRAGProtocolError as e:
            return HealthResult(healthy=False, detail=str(e))

        status = str(payload.get("status", "")).strip().lower()
        # Upstream reports "healthy"; treat anything else as degraded rather
        # than guessing at synonyms.
        healthy = status in {"healthy", "ok"}
        version = payload.get("core_version") or payload.get("api_version")
        return HealthResult(
            healthy=healthy,
            detail=status or "sidecar returned no status field",
            version=str(version) if version else None,
        )

    async def index_document(
        self, *, canonical_text: str, source_id: str
    ) -> IndexAck:
        """Insert one synthetic document.

        SYNTHETIC / MANUAL POC USE ONLY - must not be called from source
        ingestion in GraphRAG-02 (AGR-005 §21.9).

        Upstream POST /documents/text accepts only {text, file_source, chunking}
        - there is no arbitrary metadata field. So source_id travels in
        ``file_source`` as the sole join key, and title / content_hash /
        notebook_ids / contract_version are NOT sent at all; they stay in Open
        Notebook and are joined locally by source_id. This is strictly less
        egress than the AGR-005 §7 allowlist permits.

        Acceptance is not completion: poll track_status() before querying.
        """
        payload = await self._request(
            "POST",
            "/documents/text",
            json={"text": canonical_text, "file_source": source_id},
        )

        status = str(payload.get("status", "")).strip().lower()
        track_id = payload.get("track_id")
        if not isinstance(track_id, str) or not track_id:
            raise GraphRAGProtocolError(
                "GraphRAG sidecar accepted the document but returned no track_id"
            )
        return IndexAck(
            track_id=track_id,
            accepted=status in {"success", "partial_success"},
            detail=str(payload.get("message", "")) or status,
        )

    async def delete_document(self, doc_id: str) -> DeleteOutcome:
        """Request deletion of one document by its LightRAG doc_id.

        Hits DELETE /documents/delete_document (verified v1.5.6). Upstream runs
        the delete in the BACKGROUND and returns a status immediately.

        The pinned v1.5.6 endpoint (DeleteDocByIdResponse.status,
        document_routes.py:6114) exposes exactly three values:

          - ``deletion_started`` -> DeleteState.GONE (scheduled; nothing left
            for us to retain from the caller's perspective). An absent document
            also returns this — the background delete absorbs the not-found.
          - ``busy``             -> DeleteState.BUSY (pipeline busy; retry)
          - ``not_allowed``      -> DeleteState.REFUSED

        ``not_found`` is NOT returned by this endpoint in v1.5.6; it is a
        DEFENSIVE/internal normalized value (it is the status of LightRAG's own
        core ``DeletionResult``, lightrag.py:5387). We still map it to GONE so
        that a future endpoint version or an internal caller surfacing it stays
        idempotent — an already-absent document is success for a
        delete-then-insert. The branch is deliberately retained even though it
        is currently unreachable via this route.

        Note the asymmetry with retention guarantees: ``deletion_started`` is an
        acceptance, not a completion. This method is used inside REINDEX
        (remove-old-before-insert-new); the DURABLE lifecycle delete with its
        own retention proof is GraphRAG-03B/03C and is deliberately not built
        here.

        ``delete_file`` / ``delete_llm_cache`` are left at their upstream
        defaults (False): Open Notebook never uploaded a file to the sidecar
        (text-only ingestion), and cache cleanup is not required for reindex
        correctness.
        """
        payload = await self._request(
            "DELETE",
            "/documents/delete_document",
            json={"doc_ids": [doc_id]},
        )

        # Why `deletion_started` -> GONE is safe for the immediately-following
        # reindex insert (verified against v1.5.6, not assumed): the delete
        # endpoint acquires `destructive_busy` SYNCHRONOUSLY before returning
        # `deletion_started` (document_routes.py ~6200), and the background
        # delete holds it until its own `finally` AFTER all data removal
        # (background_delete_documents). Meanwhile `insert_text` raises HTTP 409
        # whenever `destructive_busy` is set. So an insert issued while the old
        # doc is still being deleted gets 409 -> classified TRANSIENT ->
        # retried; the insert can only SUCCEED once the delete has fully
        # completed and released the slot. There is therefore no
        # "insert-then-late-background-delete-erases-it" race for the same
        # deterministic doc_id. (The remaining crash-after-delete gap is a
        # durability concern for 03-B/03-D, not a correctness bug here.)
        status = str(payload.get("status", "")).strip().lower()
        if status in {"deletion_started", "not_found"}:
            state = DeleteState.GONE
        elif status == "busy":
            state = DeleteState.BUSY
        elif status == "not_allowed":
            state = DeleteState.REFUSED
        else:
            # An unrecognized status is not proof of deletion. Treating it as
            # GONE could drop a still-present copy from our bookkeeping; surface
            # it as a protocol error so the caller retries instead.
            raise GraphRAGProtocolError(
                f"GraphRAG sidecar returned unexpected delete status {status!r}"
            )

        detail = str(payload.get("message", "")) or status
        logger.debug(f"GraphRAG delete doc_id={doc_id} -> {state.value}")
        return DeleteOutcome(doc_id=doc_id, state=state, detail=detail)

    async def list_documents_page(
        self,
        *,
        page: int = 1,
        page_size: int = ABSENCE_PROBE_PAGE_SIZE,
        sort_field: str = "id",
        sort_direction: str = "asc",
    ) -> DocumentsPage:
        """List one page of the sidecar's documents (POST /documents/paginated).

        Verified against v1.5.6: request is
        ``{status_filters?, page>=1, page_size in [10,200], sort_field in
        {created_at,updated_at,id,file_path}, sort_direction in {asc,desc}}`` and
        the response carries ``documents[].id`` plus a ``pagination`` block with
        ``total_count`` and ``total_pages``. Both are required; a response missing
        either is a protocol error, not a silent empty page — a caller proving
        ABSENCE must never mistake a malformed response for "no documents".
        """
        payload = await self._request(
            "POST",
            "/documents/paginated",
            json={
                "page": page,
                "page_size": page_size,
                "sort_field": sort_field,
                "sort_direction": sort_direction,
            },
        )
        documents = payload.get("documents")
        pagination = payload.get("pagination")
        if not isinstance(documents, list) or not isinstance(pagination, dict):
            raise GraphRAGProtocolError(
                "GraphRAG paginated response is missing documents/pagination"
            )
        total_count = pagination.get("total_count")
        total_pages = pagination.get("total_pages")
        if not isinstance(total_count, int) or not isinstance(total_pages, int):
            raise GraphRAGProtocolError(
                "GraphRAG pagination is missing total_count/total_pages"
            )
        # Keep only ids we can actually read. A document dict with no usable id
        # shortens doc_ids, which makes the completeness check below fail closed
        # (UNKNOWN) rather than mistaking an unreadable page for a complete one.
        doc_ids = tuple(
            str(d["id"])
            for d in documents
            if isinstance(d, dict) and d.get("id")
        )
        return DocumentsPage(
            doc_ids=doc_ids,
            page=page,
            page_size=page_size,
            total_count=total_count,
            total_pages=total_pages,
        )

    async def confirm_document_absent(self, doc_id: str) -> AbsenceState:
        """Prove (or refute) that ``doc_id`` is absent, from ONE complete snapshot.

        This is the GraphRAG-03C resolution gate: a durable deletion tombstone may
        be CAS-resolved only on ``ABSENT_CONFIRMED``. Never raises — every failure
        or uncertainty collapses to ``UNKNOWN`` so the caller keeps the tombstone
        pending and re-drives later, never treating an error as success.

        Why a single request and never multi-page traversal (verified live): the
        listing is offset-paginated, so between two page requests a concurrent
        delete of a lower-sorted document shifts offsets and could move the target
        across an already-read page boundary — a false absence. A single response
        is a consistent server-side snapshot, so ``ABSENT_CONFIRMED`` is returned
        ONLY when that one response provably enumerated the entire current set
        (``total_pages <= 1`` and ``total_count == len(doc_ids)``). Any set that
        would need a second request (``total_pages > 1``) is ``UNKNOWN`` by
        construction, which is the documented single-response ceiling
        (page_size = ABSENCE_PROBE_PAGE_SIZE = 200): a corpus larger than one page
        never yields a false confirmation, it simply stays pending.
        """
        try:
            page = await self.list_documents_page(
                page=1,
                page_size=ABSENCE_PROBE_PAGE_SIZE,
                sort_field="id",
                sort_direction="asc",
            )
        except GraphRAGError:
            # Timeout, HTTP error, config/version mismatch, or malformed response.
            return AbsenceState.UNKNOWN

        if doc_id in page.doc_ids:
            return AbsenceState.FOUND
        if page.total_pages <= 1 and page.total_count == len(page.doc_ids):
            return AbsenceState.ABSENT_CONFIRMED
        return AbsenceState.UNKNOWN

    async def track_status(self, track_id: str) -> IndexStatus:
        """Poll indexing progress for a track_id.

        Collapses LightRAG's 7-state pipeline into IndexState. A track_id with
        no documents yet is IN_PROGRESS, not FAILED: upstream registers the
        track before the document reaches the pipeline.
        """
        payload = await self._request(
            "GET", f"/documents/track_status/{track_id}"
        )

        documents = payload.get("documents")
        if not isinstance(documents, list):
            raise GraphRAGProtocolError(
                "GraphRAG track status response has no documents list"
            )

        summary = payload.get("status_summary")
        state_counts = summary if isinstance(summary, dict) else {}

        states = [
            normalize_index_state(str(doc.get("status", "")))
            for doc in documents
            if isinstance(doc, dict)
        ]
        if not states:
            state = IndexState.IN_PROGRESS
        elif any(s is IndexState.IN_PROGRESS for s in states):
            state = IndexState.IN_PROGRESS
        elif any(s is IndexState.FAILED for s in states):
            # Nothing pending and at least one failure - surface the failure
            # rather than a partial success.
            state = IndexState.FAILED
        else:
            state = IndexState.PROCESSED

        total = payload.get("total_count")
        return IndexStatus(
            track_id=track_id,
            state=state,
            total_count=total if isinstance(total, int) else len(states),
            state_counts={str(k): v for k, v in state_counts.items()},
        )

    async def query(
        self,
        question: str,
        *,
        mode: QueryMode = QueryMode.HYBRID,
        top_k: Optional[int] = None,
    ) -> GraphQueryResult:
        """Run an experimental graph query.

        DIAGNOSTIC ONLY. ``include_references=True`` asks upstream for the
        supporting reference list; each ReferenceItem.file_path is mapped back
        to a source_id. References are NOT validated against live Open Notebook
        records here - that validation is GraphRAG-05/06 work (AGR-005 §8), so
        nothing from this call may become a citation.
        """
        body: Dict[str, Any] = {
            "query": question,
            "mode": mode.value,
            "include_references": True,
        }
        if top_k is not None:
            body["top_k"] = top_k

        payload = await self._request("POST", "/query", json=body)

        answer = payload.get("response")
        if not isinstance(answer, str):
            raise GraphRAGProtocolError(
                "GraphRAG query response has no 'response' string"
            )

        references: List[GraphReference] = []
        raw_refs = payload.get("references")
        if isinstance(raw_refs, list):
            for item in raw_refs:
                if not isinstance(item, dict):
                    continue
                # Upstream calls this field file_path; for documents WE indexed
                # it carries the source_id we passed as file_source. Never treat
                # it as a filesystem path.
                joined = item.get("file_path")
                joined = joined.strip() if isinstance(joined, str) else None
                excerpts = item.get("content")
                references.append(
                    GraphReference(
                        source_id=joined or None,
                        reference_id=(
                            str(item["reference_id"])
                            if item.get("reference_id") is not None
                            else None
                        ),
                        resolved=_looks_like_record_id(joined),
                        excerpts=[str(c) for c in excerpts]
                        if isinstance(excerpts, list)
                        else [],
                    )
                )

        elapsed = payload.get("response_time")
        result = GraphQueryResult(
            answer=answer,
            references=references,
            mode=mode.value,
            elapsed_seconds=float(elapsed)
            if isinstance(elapsed, (int, float))
            else None,
        )
        logger.debug(
            f"GraphRAG query returned {len(references)} reference(s) in mode={mode.value}"
        )
        return result
