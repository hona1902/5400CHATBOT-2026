"""Real per-notebook graph-index backend for the PN02 LIVE driver (PN02D-B0C-B).

EVALUATION-ONLY. Nothing in production imports this (``PRODUCTION_IMPORTS_EVAL =
NO``). Supplies the REAL concrete implementation behind the already-approved B0B
``CellIndexClient`` Protocol + ``IndexClientFactory`` seam (design §9/§10) so the
existing ``indexlivepn02d.MembershipIndexExecutor`` — which owns submit→poll→terminal,
bounded retry, budget, routing, authorization, and the run_id cross-check — drives a
real LightRAG sidecar with NO new orchestration.

The single net-new piece is a ``NotebookRuntimeRoute -> CellIndexClient`` factory that
binds each notebook's own real ``base_url`` (from the runtime manager) so a notebook's
index client talks ONLY to that notebook's sidecar. Completion is proven by the track
surface (``GET /documents/track_status/{id}`` aggregate ``PROCESSED``), never by submit
acceptance — that logic is already inside the executor.

**Design/source discrepancy (flagged for Codex, task §2).** The frozen design (§9)
names ``live_indexer08.RealCellIndexClient`` as the reuse target. But
``RealCellIndexClient`` submits via ``GraphRAGService.index_source`` →
``validate_source_id``, which validates ``source_id`` as an Open Notebook **record id**
(``source:…``) and REJECTS a PN02 fixture key (``A1`` / ``SH_AB``). PN02 MUST submit the
fixture key as ``file_source`` so GD provenance normalizes against
``fixture.source_keys`` (design §12). So this adapter wraps the SAME wire seam
``RealCellIndexClient`` reaches internally — ``GraphRAGClient.index_document``
(``POST /documents/text`` with ``{"text": canonical_text, "file_source": source_id}``)
and ``GraphRAGClient.track_status`` — WITHOUT the service-layer ON record-id validation
(inapplicable to a synthetic fixture-key file_source). Equivalent safety is preserved:
a ``source_id`` must be a known fixture Source key (guarded by the executor's membership
check and, defensively, by an optional allowlist here). The wire behaviour, completion
model, and secret handling are identical to ``RealCellIndexClient``.

Secret safety (design §17): this module never reads an env secret and never logs a
key — it forwards an api-key VALUE it is handed (a dummy in B0C-B tests; resolved late
by the live dependency builder in a future authorized run). B0C-B tests inject a mock
httpx transport, so ``EXTERNAL_PROVIDER_NETWORK_CALLS = 0``.
"""

from __future__ import annotations

from typing import AbstractSet, Optional
from urllib.parse import urlparse

import httpx

from open_notebook.integrations.graphrag.config import (
    DEFAULT_TIMEOUT_SECONDS,
    GraphRAGConfig,
)
from open_notebook.integrations.graphrag.eval.authmintlivepn02d import (
    require_live_provider_run_authorization,
)
from open_notebook.integrations.graphrag.eval.indexlivepn02d import IndexClientFactory
from open_notebook.integrations.graphrag.eval.live_indexer08 import (
    CellEndpoint,
    CellIndexClient,
    IndexStatusResult,
    IndexSubmitResult,
)
from open_notebook.integrations.graphrag.eval.routelivepn02d import NotebookRuntimeRoute

#: The non-dialable placeholder scheme a real client must never be built against.
_PLACEHOLDER_SCHEMES = frozenset({"eval-null"})

_STATE_PROCESSED = "PROCESSED"
_STATE_FAILED = "FAILED"
_STATE_IN_PROGRESS = "IN_PROGRESS"


class RealIndexAdapterError(RuntimeError):
    """A real index client could not be built for a route (fail-closed, content-free)."""


class RealPN02IndexClient(CellIndexClient):
    """Real per-notebook index client over the LightRAG ``/documents`` wire seam.

    Wraps the SAME ``GraphRAGClient.index_document`` / ``track_status`` methods
    ``RealCellIndexClient`` reaches internally (identical wire contract + completion
    model), but submits the fixture-key ``file_source`` directly — see the
    module-level design/source discrepancy note. An injected httpx ``transport`` (and
    the ephemeral FAILED-reason read) let B0C-B contract tests exercise the real wire
    logic with ZERO provider traffic (design §19/§49). ``allowed_source_keys``, when
    supplied, is the equivalent fixture-key safety guard replacing the ON record-id
    validation.
    """

    def __init__(
        self,
        endpoint: CellEndpoint,
        *,
        allowed_source_keys: AbstractSet[str],
        api_key: Optional[str] = None,
        transport: Optional[httpx.AsyncBaseTransport] = None,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        from open_notebook.integrations.graphrag.client import GraphRAGClient

        # B0CB-M4: the fixture-key allowlist is MANDATORY for the real PN02 index client
        # — it is the equivalent safety guard that replaces the inapplicable ON
        # record-id validation, so it can never be constructed disabled. A guard-less
        # variant does not exist on the real path (any generic no-guard behaviour must
        # live in a separate test-only fake, not this client).
        if not allowed_source_keys:
            raise RealIndexAdapterError(
                "RealPN02IndexClient requires a non-empty fixture-key allowlist "
                "(fail-closed, B0CB-M4)"
            )
        # Bound to THIS notebook's own base_url — never a global/default GraphRAG URL.
        self._cfg = GraphRAGConfig(
            enabled=True, base_url=endpoint.base_url, timeout=timeout, api_key=api_key
        )
        self._transport = transport
        self._client = GraphRAGClient(self._cfg, transport=transport)
        self._allowed = frozenset(allowed_source_keys)

    async def submit(self, *, source_id: str, canonical_text: str) -> IndexSubmitResult:
        # Mandatory fixture-key guard (B0CB-M4): the file_source MUST be a known fixture
        # Source key. Rejects arbitrary file_source, ON record ids, paths, URLs, and
        # foreign/malformed provenance BEFORE any HTTP request.
        if source_id not in self._allowed:
            return IndexSubmitResult(
                accepted=False, track_id=None, detail="source_id not an allowed fixture key"
            )
        ack = await self._client.index_document(
            canonical_text=canonical_text, source_id=source_id
        )
        return IndexSubmitResult(
            accepted=bool(ack.accepted), track_id=ack.track_id or None, detail=ack.detail
        )

    async def status(self, *, track_id: str) -> IndexStatusResult:
        # Same terminal-state mapping as RealCellIndexClient, threaded through the SAME
        # (mock) transport so the FAILED path never touches a real network in tests.
        from open_notebook.integrations.graphrag.eval.index_retry08 import (
            _fetch_failed_reason_ex,
        )
        from open_notebook.integrations.graphrag.models import IndexState

        st = await self._client.track_status(track_id)
        if st.state == IndexState.PROCESSED:
            return IndexStatusResult(state=_STATE_PROCESSED)
        if st.state == IndexState.FAILED:
            presence, text = await _fetch_failed_reason_ex(
                self._cfg, track_id, transport=self._transport
            )
            return IndexStatusResult(
                state=_STATE_FAILED, detail=text if presence == "PRESENT" else None
            )
        return IndexStatusResult(state=_STATE_IN_PROGRESS)


def cell_endpoint_for_route(route: NotebookRuntimeRoute) -> CellEndpoint:
    """Build a content-free ``CellEndpoint`` from a route's REAL base_url (design §9).

    Fails closed if the route still carries a non-dialable placeholder endpoint (a
    real client must never be built against ``eval-null://`` — only the runtime
    manager's booted loopback base_url).
    """
    parsed = urlparse(route.endpoint)
    if parsed.scheme in _PLACEHOLDER_SCHEMES or parsed.scheme not in {"http", "https"}:
        raise RealIndexAdapterError(
            f"route {route.notebook_id} endpoint is not a dialable base_url "
            f"(scheme {parsed.scheme!r}); refusing to build a real index client"
        )
    return CellEndpoint(
        run_id="",
        cell_id=route.notebook_id,
        base_url=route.endpoint,
        port=parsed.port or 0,
        workspace=route.workspace_id,
        storage_dir=route.storage_identity,
        container_identity=route.workspace_id,
    )


def build_real_index_client_factory(
    *,
    live_auth: object,
    allowed_source_keys: AbstractSet[str],
    api_key: Optional[str] = None,
    transport: Optional[httpx.AsyncBaseTransport] = None,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
) -> IndexClientFactory:
    """Build the per-route real index-client factory — REJECTS before auth (§12/§16).

    ``live_auth`` MUST be a genuine ``LiveProviderRunAuthorization`` (a simulation
    authorization or ``None`` is rejected here, before any client is built).
    ``allowed_source_keys`` is the MANDATORY fixture-key safety guard (B0CB-M4; usually
    ``fixture.source_keys``) — a non-empty set is required or construction fails closed.
    ``api_key`` is the LightRAG ``X-API-Key`` value forwarded to the client (a dummy in
    tests, resolved late by the live dependency builder in a real run); ``transport``
    injects a mock httpx transport for contract tests.
    """
    require_live_provider_run_authorization(live_auth)
    if not allowed_source_keys:
        raise RealIndexAdapterError(
            "build_real_index_client_factory requires a non-empty fixture-key allowlist "
            "(fail-closed, B0CB-M4)"
        )

    def _factory(route: NotebookRuntimeRoute) -> CellIndexClient:
        endpoint = cell_endpoint_for_route(route)
        return RealPN02IndexClient(
            endpoint,
            allowed_source_keys=allowed_source_keys,
            api_key=api_key,
            transport=transport,
            timeout=timeout,
        )

    return _factory


__all__ = [
    "RealIndexAdapterError",
    "RealPN02IndexClient",
    "cell_endpoint_for_route",
    "build_real_index_client_factory",
]
