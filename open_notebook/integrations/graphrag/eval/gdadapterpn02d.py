"""Real per-notebook GD ``/query/data`` backend for the PN02 LIVE driver (PN02D-B0C-B).

EVALUATION-ONLY. Nothing in production imports this (``PRODUCTION_IMPORTS_EVAL =
NO``). Supplies the REAL implementation behind the B0B ``GDQueryBackend`` Protocol +
``GDBackendFactory`` seam (design §11/§12). The executor
(``gdlivepn02d.GDQueryExecutor``) owns attestation, routing, the operation allowlist,
budget, and the run_id cross-check; this backend only issues one
``POST {base_url}/query/data`` with ``only_need_context=True`` and returns the
STRONG-anchor candidate Source ids.

**Design/source discrepancy (flagged for Codex, task §2).** The frozen design (§11)
names ``gd_seam.GDQueryClient`` as the wrap target. But ``GDQueryClient`` projects the
STRONG anchors through the GraphRAG-08 ``normalize._normalize`` →
``canonical_source_id`` → ``record_id_for(..., tables={"source"})``, which validates
each ``file_path`` as an Open Notebook **record id** and DROPS a PN02 fixture key
(``A1`` / ``SH_AB``) as ``malformed``. PN02 provenance is fixture keys, and the
fixture-key allowlist filtering is the PN02 executor's job
(``normalize_graph(allowlist=fixture.source_keys)``, design §12). So this backend
issues the SAME ``/query/data`` wire call ``GDQueryClient`` makes (identical body,
``only_need_context=True``, headers, timeout, and error surface) and extracts the SAME
STRONG anchors (``chunks[].file_path`` + ``references[].file_path``) as RAW candidate
strings — WITHOUT the record-id normalization — and hands them to the executor, which
applies the PN02 fixture-key normalizer. No rank/score is read or invented
(``QUERY_DATA_EXPOSES_VALID_RANK/SCORE = NO``). The raw payload never leaves this method.

Routing (design §23): the backend is bound to ONE route's own ``base_url``; the
executor validates route ownership before invoking the factory, and a placeholder
(non-dialable) endpoint fails closed here. B0C-B tests inject a mock ``/query/data``
httpx transport (``EXTERNAL_PROVIDER_NETWORK_CALLS = 0``).
"""

from __future__ import annotations

import time
from typing import AbstractSet, Any, Dict, List, Optional
from urllib.parse import urlparse

import httpx

from open_notebook.integrations.graphrag.config import (
    DEFAULT_TIMEOUT_SECONDS,
    GraphRAGConfig,
)
from open_notebook.integrations.graphrag.eval.authmintlivepn02d import (
    require_live_provider_run_authorization,
)
from open_notebook.integrations.graphrag.eval.gdlivepn02d import (
    GDBackendError,
    GDBackendFactory,
    GDBackendResult,
    GDQueryBackend,
)
from open_notebook.integrations.graphrag.eval.routelivepn02d import NotebookRuntimeRoute

_PLACEHOLDER_SCHEMES = frozenset({"eval-null"})


class RealGDAdapterError(RuntimeError):
    """A real GD backend could not be built for a route (fail-closed, content-free)."""


def _as_list(value: Any) -> List[Any]:
    return value if isinstance(value, list) else []


def _file_path(item: Any) -> Optional[str]:
    if isinstance(item, dict):
        fp = item.get("file_path")
        if isinstance(fp, str):
            fp = fp.strip()
            return fp or None
    return None


class RealPN02GDBackend(GDQueryBackend):
    """Issues ``POST /query/data`` (``only_need_context=True``) and returns STRONG anchors.

    Bound to ONE notebook's ``base_url``. Extracts ``chunks[].file_path`` +
    ``references[].file_path`` as RAW candidate ids (fixture keys); the PN02 executor
    applies the fixture-key allowlist normalization. A vendor error surfaces as a
    content-free ``GDBackendError`` (never the response body or a key), which the
    executor maps to ``TechnicalOutcome.FAILED_GD_QUERY`` — a TECHNICAL failure, never
    a scientific NO. ``client.query()`` / ``/query`` and the final-answer LLM are never
    used.
    """

    def __init__(
        self,
        config: GraphRAGConfig,
        *,
        transport: Optional[httpx.AsyncBaseTransport] = None,
    ) -> None:
        self._config = config
        self._transport = transport

    def _headers(self) -> Dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self._config.api_key:
            headers["X-API-Key"] = self._config.api_key
        return headers

    async def query_evidence(
        self, question: str, *, benchmark_ids: Optional[AbstractSet[str]] = None
    ) -> GDBackendResult:
        # only_need_context=True suppresses the final-answer LLM (sent explicitly, also
        # forced upstream). Never client.query() / /query.
        body: Dict[str, Any] = {
            "query": question,
            "mode": "hybrid",
            "only_need_context": True,
        }
        started = time.monotonic()
        try:
            async with httpx.AsyncClient(
                base_url=self._config.base_url,
                timeout=self._config.timeout,
                headers=self._headers(),
                transport=self._transport,
            ) as client:
                response = await client.request("POST", "/query/data", json=body)
        except httpx.TimeoutException as exc:
            raise GDBackendError("GD /query/data timed out") from exc
        except httpx.TransportError as exc:
            raise GDBackendError(
                f"GD /query/data unreachable: {type(exc).__name__}"
            ) from exc
        latency_ms = int((time.monotonic() - started) * 1000)

        if response.status_code >= 400:
            # Content-free: never echo the response body (may carry content).
            raise GDBackendError(f"GD /query/data returned HTTP {response.status_code}")
        try:
            payload = response.json()
        except ValueError as exc:
            raise GDBackendError("GD /query/data returned non-JSON") from exc
        if not isinstance(payload, dict):
            raise GDBackendError("GD /query/data returned a non-object payload")

        # --- transient raw handling (does not escape this scope) ------------------
        data = payload.get("data")
        data = data if isinstance(data, dict) else {}
        raw_candidates: List[Optional[str]] = []
        for item in _as_list(data.get("chunks")):
            raw_candidates.append(_file_path(item))
        for item in _as_list(data.get("references")):
            raw_candidates.append(_file_path(item))
        # --- raw payload is now fully projected; drop every reference to it -------

        # NO record-id normalization here (fixture-key provenance); the PN02 executor's
        # normalize_graph(allowlist=fixture.source_keys) does the fixture-key filtering.
        return GDBackendResult(
            candidate_source_ids=raw_candidates, latency_ms=latency_ms
        )


def _config_for_route(
    route: NotebookRuntimeRoute, *, api_key: Optional[str], timeout: float
) -> GraphRAGConfig:
    parsed = urlparse(route.endpoint)
    if parsed.scheme in _PLACEHOLDER_SCHEMES or parsed.scheme not in {"http", "https"}:
        raise RealGDAdapterError(
            f"route {route.notebook_id} endpoint is not a dialable base_url "
            f"(scheme {parsed.scheme!r}); refusing to build a real GD backend"
        )
    return GraphRAGConfig(
        enabled=True, base_url=route.endpoint, timeout=timeout, api_key=api_key
    )


def build_real_gd_backend_factory(
    *,
    live_auth: object,
    api_key: Optional[str] = None,
    transport: Optional[httpx.AsyncBaseTransport] = None,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
) -> GDBackendFactory:
    """Build the per-route real GD-backend factory — REJECTS before auth (§12/§16).

    ``live_auth`` MUST be a genuine ``LiveProviderRunAuthorization``. Each built backend
    is bound to its route's own ``base_url``. ``transport`` injects a mock
    ``/query/data`` transport for contract tests.
    """
    require_live_provider_run_authorization(live_auth)

    def _factory(route: NotebookRuntimeRoute) -> GDQueryBackend:
        config = _config_for_route(route, api_key=api_key, timeout=timeout)
        return RealPN02GDBackend(config, transport=transport)

    return _factory


__all__ = [
    "RealGDAdapterError",
    "RealPN02GDBackend",
    "build_real_gd_backend_factory",
]
