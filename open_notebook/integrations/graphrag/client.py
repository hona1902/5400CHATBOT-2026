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

from typing import Any, Dict, List, Optional

import httpx
from loguru import logger

from open_notebook.integrations.graphrag.config import (
    VERIFIED_LIGHTRAG_VERSION,
    GraphRAGConfig,
)
from open_notebook.integrations.graphrag.models import (
    GraphQueryResult,
    GraphRAGConfigurationError,
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
