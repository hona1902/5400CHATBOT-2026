"""Real per-notebook derived-document delete backend for the PN02 LIVE driver (B0C-B).

EVALUATION-ONLY. Nothing in production imports this (``PRODUCTION_IMPORTS_EVAL =
NO``). Supplies the REAL implementation behind the B0B ``DeleteBackend`` Protocol +
``DeleteBackendFactory`` seam (design §14) by wrapping the verified
``client.delete_document(doc_id)`` path — a thin adapter, NOT a new lifecycle.

The workspace-safety property is enforced by ``removallivepn02d.MembershipRemovalExecutor``
BEFORE this backend is invoked: a ``(workspace_A, SH_AB)`` delete is refused
(``CrossWorkspaceDeleteRefused``) unless dispatched to NB_A's own route, even though
the vendor ``derived_document_id`` is identical in NB_B. This backend is bound to ONE
route's own ``base_url`` and simply issues the delete against that sidecar.

Verified wire behaviour (design §14): ``DELETE {base_url}/documents/delete_document``
with body ``{"doc_ids": [derived_document_id]}`` where ``derived_document_id =
compute_doc_id(source_id)``; upstream ``deletion_started``/``not_found`` →
``DeleteState.GONE`` → ``succeeded=True`` (idempotent); ``busy`` → ``BUSY``,
``not_allowed`` → ``REFUSED``, unexpected → ``GraphRAGProtocolError`` — all
``succeeded=False``. A failed delete never restores membership: the ON canonical
``post_validate`` backstop keeps ``STALE_GRAPH_EVIDENCE_ACCEPTED_AS_VALID = 0``
(design §14). B0C-B tests inject a mock DELETE transport
(``EXTERNAL_PROVIDER_NETWORK_CALLS = 0``).
"""

from __future__ import annotations

from typing import Optional
from urllib.parse import urlparse

import httpx

from open_notebook.integrations.graphrag.config import (
    DEFAULT_TIMEOUT_SECONDS,
    GraphRAGConfig,
)
from open_notebook.integrations.graphrag.eval.authmintlivepn02d import (
    require_live_provider_run_authorization,
)
from open_notebook.integrations.graphrag.eval.removallivepn02d import (
    DeleteBackend,
    DeleteBackendFactory,
    DeleteResult,
)
from open_notebook.integrations.graphrag.eval.routelivepn02d import NotebookRuntimeRoute

_PLACEHOLDER_SCHEMES = frozenset({"eval-null"})


class RealDeleteAdapterError(RuntimeError):
    """A real delete backend could not be built for a route (fail-closed)."""


class RealPN02DeleteBackend(DeleteBackend):
    """Adapts ``client.delete_document`` to the PN02 ``DeleteBackend`` Protocol.

    Bound to ONE notebook's ``base_url``. ``derived_document_id`` is the vendor id the
    removal executor already resolved (``compute_doc_id(canonical_source_id)``);
    ``succeeded`` is True only on ``DeleteState.GONE``. Any HTTP/protocol failure is
    contained to ``succeeded=False`` (content-free) — a delete failure is never turned
    into a scientific verdict, and the stale-evidence backstop preserves correctness.
    """

    def __init__(
        self,
        config: GraphRAGConfig,
        *,
        transport: Optional[httpx.AsyncBaseTransport] = None,
    ) -> None:
        from open_notebook.integrations.graphrag.client import GraphRAGClient

        self._client = GraphRAGClient(config, transport=transport)

    async def delete_document(self, *, derived_document_id: str) -> DeleteResult:
        from open_notebook.integrations.graphrag.models import (
            DeleteState,
            GraphRAGError,
        )

        try:
            outcome = await self._client.delete_document(derived_document_id)
        except GraphRAGError:
            # Content-free: an HTTP/protocol/timeout failure is a non-converged delete
            # (never assumed success). Correctness is preserved by the ON post_validate
            # backstop (design §14).
            return DeleteResult(succeeded=False)
        return DeleteResult(succeeded=outcome.state is DeleteState.GONE)


def _config_for_route(
    route: NotebookRuntimeRoute, *, api_key: Optional[str], timeout: float
) -> GraphRAGConfig:
    parsed = urlparse(route.endpoint)
    if parsed.scheme in _PLACEHOLDER_SCHEMES or parsed.scheme not in {"http", "https"}:
        raise RealDeleteAdapterError(
            f"route {route.notebook_id} endpoint is not a dialable base_url "
            f"(scheme {parsed.scheme!r}); refusing to build a real delete backend"
        )
    return GraphRAGConfig(
        enabled=True, base_url=route.endpoint, timeout=timeout, api_key=api_key
    )


def build_real_delete_backend_factory(
    *,
    live_auth: object,
    api_key: Optional[str] = None,
    transport: Optional[httpx.AsyncBaseTransport] = None,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
) -> DeleteBackendFactory:
    """Build the per-route real delete-backend factory — REJECTS before auth (§12/§16).

    Each built backend is bound to its route's own ``base_url``. ``transport`` injects a
    mock DELETE transport for contract tests.
    """
    require_live_provider_run_authorization(live_auth)

    def _factory(route: NotebookRuntimeRoute) -> DeleteBackend:
        config = _config_for_route(route, api_key=api_key, timeout=timeout)
        return RealPN02DeleteBackend(config, transport=transport)

    return _factory


__all__ = [
    "RealDeleteAdapterError",
    "RealPN02DeleteBackend",
    "build_real_delete_backend_factory",
]
