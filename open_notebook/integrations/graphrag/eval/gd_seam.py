"""SYSTEM GD — evaluation-only LightRAG ``/query/data`` seam (task §31-§34).

EVALUATION-ONLY. This is NOT the GraphRAG-07 production Structured Evidence
Adapter and NOT a production ``query_data()`` / ``query_evidence()`` method. It
lives inside the eval boundary, nothing in production imports it, and it never
becomes a runtime service. It exists so the benchmark can measure SYSTEM GD
(structured ``/query/data`` evidence) without building the production adapter
merely to decide whether the adapter is worth building.

Boundary rules enforced here (task §32-§34):
  * The raw LightRAG ``/query/data`` schema (entities / relationships / chunks /
    references dicts, ``weight``, ``reference_id``) exists ONLY as a transient
    local inside this module. It is projected to canonical Source ids + counts and
    then dropped. It is never returned, logged, or persisted.
  * Only STRONG anchors establish a Source candidate: ``chunks[].file_path`` and
    ``references[].file_path`` (both carry the canonical source_id losslessly).
    Entity/relation provenance is PARTIAL (many-to-many / ``unknown_source``) and
    is recorded only as diagnostic counts — never as a Source candidate.
  * The result is an UNORDERED set. No score/rank is read or invented; the graph
    order (round-robin interleave) is discarded.
  * ``final_answer_generation`` is False by construction: ``/query/data`` sets
    ``only_need_context=True`` upstream and never runs the final-answer LLM. Other
    retrieval-side provider work (keyword-extraction LLM, embeddings) may still
    run — this is NOT "no LLM egress" (task §33 wording rule).

The vendor error surface is normalized to a single ``GDEvalError`` (content-free:
never echoes the response body or the API key).
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import AbstractSet, Any, Dict, List, Optional

import httpx

from open_notebook.integrations.graphrag.config import GraphRAGConfig
from open_notebook.integrations.graphrag.eval.normalize import (
    NormalizedRetrieval,
    _normalize,
)
from open_notebook.integrations.graphrag.models import QueryMode

# Result dispositions (mirrors the GraphRAG-07 contract vocabulary but is an
# eval-local enum, not the production type).
GD_SUCCESS = "SUCCESS"
GD_EMPTY = "EMPTY"
GD_DEGRADED = "DEGRADED"


class GDEvalError(RuntimeError):
    """The GD ``/query/data`` seam could not produce a usable evidence set.

    Content-free: never carries the response body or credentials.
    """


@dataclass(frozen=True)
class GDEvidence:
    """Content-free structured-evidence result for one query.

    ``retrieval`` is the canonical Source-id SET (``ordered=False``) plus provenance
    stats, reusing the same normalizer as SYSTEM GQ so the two are directly
    comparable. The extra fields are diagnostic counts only — no chunk text, no
    entity/relation descriptions, no raw payload.
    """

    retrieval: NormalizedRetrieval
    status: str
    raw_evidence_present: bool
    entity_count: int
    relationship_count: int
    chunk_count: int
    reference_count: int
    final_answer_generation: bool
    latency_ms: Optional[int]

    def as_set(self) -> frozenset[str]:
        return self.retrieval.as_set()

    @property
    def source_ids(self):
        return self.retrieval.source_ids


class GDQueryClient:
    """Evaluation-only HTTP caller for LightRAG ``POST /query/data``.

    Constructed from the existing ``GraphRAGConfig`` (base_url / api_key / timeout)
    — it does NOT extend the production ``GraphRAGClient`` and adds no production
    method. Holds no connection state beyond the httpx client it opens/closes per
    call. An injected transport lets tests exercise it with no live sidecar.
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

    async def _post_query_data(self, body: Dict[str, Any]) -> Dict[str, Any]:
        try:
            async with httpx.AsyncClient(
                base_url=self._config.base_url,
                timeout=self._config.timeout,
                headers=self._headers(),
                transport=self._transport,
            ) as client:
                response = await client.request("POST", "/query/data", json=body)
        except httpx.TimeoutException as e:
            raise GDEvalError("GD /query/data timed out") from e
        except httpx.TransportError as e:
            raise GDEvalError(
                f"GD /query/data sidecar unreachable: {type(e).__name__}"
            ) from e

        if response.status_code >= 400:
            # Content-free: never echo the response body (may contain content).
            raise GDEvalError(
                f"GD /query/data returned HTTP {response.status_code}"
            )
        try:
            payload = response.json()
        except ValueError as e:
            raise GDEvalError("GD /query/data returned non-JSON") from e
        if not isinstance(payload, dict):
            raise GDEvalError("GD /query/data returned a non-object payload")
        return payload

    async def query_evidence(
        self,
        question: str,
        *,
        mode: QueryMode = QueryMode.HYBRID,
        top_k: Optional[int] = None,
        benchmark_ids: Optional[AbstractSet[str]] = None,
    ) -> GDEvidence:
        """Run ``/query/data`` and project it to a canonical Source evidence set.

        ``benchmark_ids`` restricts candidates to the run's benchmark Sources; a
        structurally-valid source id from outside the run is counted off_benchmark
        and dropped (never a candidate). The raw payload is consumed here and never
        leaves this method.
        """
        # only_need_context=True is what suppresses the final-answer LLM. The
        # /query/data endpoint forces it upstream regardless, but we send it
        # explicitly so the no-generation intent is asserted by THIS client, not
        # merely assumed of the endpoint (defensive; harmless if ignored).
        body: Dict[str, Any] = {
            "query": question,
            "mode": mode.value,
            "only_need_context": True,
        }
        if top_k is not None:
            body["top_k"] = top_k

        started = time.monotonic()
        payload = await self._post_query_data(body)
        latency_ms = int((time.monotonic() - started) * 1000)

        # --- transient raw handling (does not escape this scope) ---------------
        data = payload.get("data")
        data = data if isinstance(data, dict) else {}
        entities = _as_list(data.get("entities"))
        relationships = _as_list(data.get("relationships"))
        chunks = _as_list(data.get("chunks"))
        references = _as_list(data.get("references"))

        # STRONG anchors only: chunk + reference file_paths carry the canonical
        # source id losslessly. Entity/relation file_paths are PARTIAL and are
        # NOT used to establish a candidate (counts only).
        strong_file_paths: List[Optional[str]] = []
        for item in chunks:
            strong_file_paths.append(_file_path(item))
        for item in references:
            strong_file_paths.append(_file_path(item))

        retrieval = _normalize(
            strong_file_paths, ordered=False, allowlist=benchmark_ids
        )
        # --- raw payload is now fully projected; drop every reference to it ----

        raw_present = bool(entities or relationships or chunks or references)
        dropped = (
            retrieval.stats.malformed
            + retrieval.stats.foreign
            + retrieval.stats.off_benchmark
        )
        if not retrieval.source_ids:
            status = GD_EMPTY
        elif dropped > 0 or retrieval.stats.duplicates > 0:
            status = GD_DEGRADED
        else:
            status = GD_SUCCESS

        return GDEvidence(
            retrieval=retrieval,
            status=status,
            raw_evidence_present=raw_present,
            entity_count=len(entities),
            relationship_count=len(relationships),
            chunk_count=len(chunks),
            reference_count=len(references),
            # Endpoint invariant, not a per-call measurement: /query/data runs no
            # final-answer LLM because only_need_context=True (forced upstream and
            # sent by us). Retrieval-side LLM/embedding egress may still occur —
            # this is NOT "no LLM egress" (task §33). The benchmark reports this as
            # an invariant of the seam, not as an observed provider-call count.
            final_answer_generation=False,
            latency_ms=latency_ms,
        )


def _as_list(value: Any) -> List[Any]:
    return value if isinstance(value, list) else []


def _file_path(item: Any) -> Optional[str]:
    if isinstance(item, dict):
        fp = item.get("file_path")
        if isinstance(fp, str):
            fp = fp.strip()
            return fp or None
    return None


__all__ = [
    "GDQueryClient",
    "GDEvidence",
    "GDEvalError",
    "GD_SUCCESS",
    "GD_EMPTY",
    "GD_DEGRADED",
]
