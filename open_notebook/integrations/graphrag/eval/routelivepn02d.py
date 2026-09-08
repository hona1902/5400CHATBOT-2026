"""Per-notebook runtime/workspace/endpoint/storage routing (PN02D-B0B).

EVALUATION-ONLY. Nothing in production imports this (``PRODUCTION_IMPORTS_EVAL =
NO``). Implements the frozen routing invariant (design §10-§13, task §12/§13):

    notebook_id -> attested runtime -> workspace_id -> endpoint -> storage identity

Identity is derived from the canonical notebook ``record_id`` (via
``manifestpn02.workspace_id_for``), NEVER from a mutable display theme (design §10).
A wrong endpoint / wrong workspace is rejected BEFORE any backend invocation (reusing
the PN02C ``routingpn02c`` error vocabulary), and a ``(source_key, notebook_id)`` that
is not a fixture membership edge cannot be indexed into that workspace (design §40.6).
This module validates routes offline; it opens no endpoint and dials nothing.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import AbstractSet, Dict, Mapping, Optional, Tuple

from open_notebook.integrations.graphrag.eval.datasetpn02 import FixturePN02
from open_notebook.integrations.graphrag.eval.manifestpn02 import (
    LIGHTRAG_EVAL_VERSION,
    ManifestError,
    WorkspaceAttestation,
    WorkspaceRoute,
    attest_workspace,
    routing_manifest,
)
from open_notebook.integrations.graphrag.eval.routingpn02c import (
    RoutingViolation,
    WrongEndpointRoutingError,
    WrongWorkspaceRoutingError,
    safe_provider_config_identity,
)

#: Default per-notebook storage-identity scheme (content-safe; never dialed).
STORAGE_IDENTITY_SCHEME = "pn02-store"


class UnauthorizedMembershipError(RoutingViolation):
    """A Source was routed into a workspace it is not a fixture member of (§40.6)."""


def default_endpoint_for(workspace_id: str) -> str:
    """Deterministic non-openable endpoint identity for a workspace (design §10)."""
    return f"eval-null://workspace/{workspace_id}"


def default_storage_identity_for(workspace_id: str) -> str:
    return f"{STORAGE_IDENTITY_SCHEME}://{workspace_id}"


@dataclass(frozen=True)
class NotebookRuntimeRoute:
    """One notebook's frozen runtime route (design §13)."""

    notebook_id: str
    notebook_record_id: str
    workspace_id: str
    endpoint: str
    storage_identity: str

    def as_workspace_route(self) -> WorkspaceRoute:
        return WorkspaceRoute(
            notebook_id=self.notebook_id,
            notebook_record_id=self.notebook_record_id,
            workspace_id=self.workspace_id,
            endpoint_placeholder=self.endpoint,
        )

    def as_public_dict(self) -> Dict[str, object]:
        return {
            "notebook_id": self.notebook_id,
            "notebook_record_id": self.notebook_record_id,
            "workspace_id": self.workspace_id,
            "endpoint": self.endpoint,
            "storage_identity": self.storage_identity,
        }


def build_route_table(
    fx: FixturePN02,
    *,
    endpoints: Optional[Mapping[str, str]] = None,
    storage_identities: Optional[Mapping[str, str]] = None,
) -> Dict[str, NotebookRuntimeRoute]:
    """Deterministic notebook -> route map (design §10/§13).

    ``endpoints`` / ``storage_identities`` may override the defaults (e.g. a fake
    topology's owned endpoints); every workspace/endpoint/storage must be distinct.
    """
    endpoints = endpoints or {}
    storage_identities = storage_identities or {}
    ws_routes = routing_manifest(fx)  # raises on workspace-id collision
    routes: Dict[str, NotebookRuntimeRoute] = {}
    seen_endpoints: set[str] = set()
    seen_storage: set[str] = set()
    for nb, wr in ws_routes.items():
        endpoint = endpoints.get(nb) or default_endpoint_for(wr.workspace_id)
        storage = storage_identities.get(nb) or default_storage_identity_for(
            wr.workspace_id
        )
        if endpoint in seen_endpoints:
            raise ManifestError(f"endpoint collision for {nb} ({endpoint})")
        if storage in seen_storage:
            raise ManifestError(f"storage identity collision for {nb} ({storage})")
        seen_endpoints.add(endpoint)
        seen_storage.add(storage)
        routes[nb] = NotebookRuntimeRoute(
            notebook_id=nb,
            notebook_record_id=wr.notebook_record_id,
            workspace_id=wr.workspace_id,
            endpoint=endpoint,
            storage_identity=storage,
        )
    return routes


class PN02Router:
    """Fail-closed per-notebook router. Validates every route before dispatch."""

    def __init__(self, routes: Mapping[str, NotebookRuntimeRoute]) -> None:
        if not routes:
            raise ManifestError("router requires >=1 route")
        self._routes = dict(routes)
        self._endpoint_owner = {r.endpoint: nb for nb, r in self._routes.items()}
        self._workspace_owner = {r.workspace_id: nb for nb, r in self._routes.items()}

    def notebook_ids(self) -> Tuple[str, ...]:
        return tuple(self._routes.keys())

    def route_for(self, notebook_id: str) -> NotebookRuntimeRoute:
        route = self._routes.get(notebook_id)
        if route is None:
            raise RoutingViolation(f"unknown notebook {notebook_id!r}")
        return route

    def owner_of_endpoint(self, endpoint: str) -> Optional[str]:
        return self._endpoint_owner.get(endpoint)

    def validate_route(
        self,
        notebook_id: str,
        *,
        target_endpoint: str,
        target_workspace_id: str,
    ) -> NotebookRuntimeRoute:
        """Return the owned route, or REJECT a mismatch BEFORE any backend op (§11)."""
        route = self.route_for(notebook_id)
        if target_endpoint != route.endpoint:
            owner = self.owner_of_endpoint(target_endpoint)
            raise WrongEndpointRoutingError(
                f"{notebook_id} routed to endpoint owned by {owner or 'unknown'} "
                f"(expected its own {route.endpoint})"
            )
        if target_workspace_id != route.workspace_id:
            raise WrongWorkspaceRoutingError(
                f"{notebook_id} routed to workspace {target_workspace_id!r} "
                f"(expected {route.workspace_id!r})"
            )
        return route

    def resolve_membership_route(
        self,
        source_key: str,
        notebook_id: str,
        *,
        memberships: AbstractSet[Tuple[str, str]],
    ) -> NotebookRuntimeRoute:
        """Route a membership edge, rejecting an unauthorized (non-member) edge (§40.6)."""
        if (source_key, notebook_id) not in memberships:
            raise UnauthorizedMembershipError(
                f"source {source_key!r} is not a fixture member of {notebook_id!r} — "
                "refusing to index into an unauthorized workspace (design §40.6)"
            )
        return self.route_for(notebook_id)


def attest_route(
    route: NotebookRuntimeRoute,
    *,
    observed_workspace_id: Optional[str] = None,
    observed_endpoint: Optional[str] = None,
    observed_storage_identity: Optional[str] = None,
    observed_lightrag_version: str = LIGHTRAG_EVAL_VERSION,
) -> WorkspaceAttestation:
    """Attest a route by comparing observations to its frozen identity (design §11).

    Defaults observe the route's own identity (a PASS); a test can pass a mismatched
    observation to prove the fail-closed attestation.
    """
    return attest_workspace(
        route.as_workspace_route(),
        observed_workspace_id=observed_workspace_id or route.workspace_id,
        observed_endpoint_identity=observed_endpoint or route.endpoint,
        observed_storage_identity=observed_storage_identity or route.storage_identity,
        observed_lightrag_version=observed_lightrag_version,
        expected_storage_identity=route.storage_identity,
        safe_provider_config_identity=safe_provider_config_identity(),
    )


__all__ = [
    "STORAGE_IDENTITY_SCHEME",
    "UnauthorizedMembershipError",
    "default_endpoint_for",
    "default_storage_identity_for",
    "NotebookRuntimeRoute",
    "build_route_table",
    "PN02Router",
    "attest_route",
]
