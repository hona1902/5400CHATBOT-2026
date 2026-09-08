"""Membership-removal executor for the PN02 live driver (PN02D-B0B).

EVALUATION-ONLY. Nothing in production imports this (``PRODUCTION_IMPORTS_EVAL =
NO``). Offline orchestration of the frozen lifecycle transition (design §28-§30,
task §37-§39): SH_AB begins in NB_A + NB_B; the canonical edge is removed from NB_A;
the derived graph copy is deleted at **NB_A's endpoint only**; then 2 GD + 2 vector
re-probes validate isolation.

Two frozen safety properties:

  * **No cross-workspace deletion (design §29):** the derived doc id is identical in
    NB_B, so correctness depends entirely on the endpoint. A delete for
    ``(workspace_A, SH_AB)`` is refused if dispatched to any workspace/route other
    than NB_A's (``CrossWorkspaceDeleteRefused``).
  * **Delete-failure defense in depth (design §30):** even if the graph delete
    fails and A's stale store still returns SH_AB, the ON canonical-membership
    ``post_validate`` (applied by ``membershippn02.evaluate_removal``) drops it, so
    ``STALE_GRAPH_EVIDENCE_ACCEPTED_AS_VALID = 0`` regardless of delete success.

The vector re-probe candidate universe is the POST-removal member snapshot resolved
BEFORE ranking (design §39), so a removed Source is excluded from A's candidate set,
not filtered post-hoc.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Dict, Mapping, Protocol

from open_notebook.integrations.graphrag.eval.authlivepn02d import (
    PN02ProviderRunAuthorization,
    ProviderOperationClass,
    QueryAuthorization,
    require_operation_allowed,
    require_query_authorization,
)
from open_notebook.integrations.graphrag.eval.budgetlivepn02d import (
    BudgetClass,
    StatefulBudgetGuard,
)
from open_notebook.integrations.graphrag.eval.datasetpn02 import (
    FixturePN02,
    MembershipRemovalScenario,
)
from open_notebook.integrations.graphrag.eval.docidpn02d import (
    DeleteTarget,
    DerivedDocMappingStore,
)
from open_notebook.integrations.graphrag.eval.gdlivepn02d import GDQueryExecutor
from open_notebook.integrations.graphrag.eval.routelivepn02d import (
    NotebookRuntimeRoute,
    PN02Router,
)
from open_notebook.integrations.graphrag.eval.routingpn02c import RoutingViolation
from open_notebook.integrations.graphrag.eval.schemaspn02 import (
    RemovalPhase,
    RemovalProbeResult,
)
from open_notebook.integrations.graphrag.eval.vectorlivepn02d import (
    NotebookLocalVectorExecutor,
)


class CrossWorkspaceDeleteRefused(RoutingViolation):
    """A derived-doc delete was dispatched to a workspace it does not belong to (§29)."""


@dataclass(frozen=True)
class DeleteResult:
    succeeded: bool


class DeleteBackend(Protocol):
    """Notebook-bound derived-document delete backend. A fake in B0B; in B1 wraps
    ``client.delete_document(compute_doc_id(source_id))`` at the notebook's sidecar."""

    async def delete_document(self, *, derived_document_id: str) -> DeleteResult: ...


DeleteBackendFactory = Callable[[NotebookRuntimeRoute], DeleteBackend]


@dataclass(frozen=True)
class MembershipRemovalOutcome:
    """Content-safe result of the removal transition (design §31)."""

    shared_source: str
    delete_target: DeleteTarget
    graph_delete_succeeded: bool
    removed_after: RemovalProbeResult
    retained_after: RemovalProbeResult

    def as_dict(self) -> Dict[str, object]:
        return {
            "shared_source": self.shared_source,
            "delete_target": {
                "workspace_id": self.delete_target.workspace_id,
                "notebook_id": self.delete_target.notebook_id,
                "canonical_source_id": self.delete_target.canonical_source_id,
                "derived_document_id": self.delete_target.derived_document_id,
            },
            "graph_delete_succeeded": self.graph_delete_succeeded,
            "removed_after_notebook": self.removed_after.notebook_id,
            "retained_after_notebook": self.retained_after.notebook_id,
        }


class MembershipRemovalExecutor:
    """Runs the frozen removal transition entirely against injected fake backends."""

    def __init__(
        self,
        *,
        fx: FixturePN02,
        router: PN02Router,
        budget: StatefulBudgetGuard,
        mapping_store: DerivedDocMappingStore,
        provider_run_auth: PN02ProviderRunAuthorization,
        query_auth: QueryAuthorization,
        gd_executor: GDQueryExecutor,
        vector_executor: NotebookLocalVectorExecutor,
        delete_backend_factory: DeleteBackendFactory,
    ) -> None:
        self._fx = fx
        self._router = router
        self._budget = budget
        self._store = mapping_store
        self._provider_run_auth = provider_run_auth
        self._query_auth = require_query_authorization(query_auth)
        self._gd = gd_executor
        self._vector = vector_executor
        self._delete_backend_factory = delete_backend_factory

    async def delete_membership(
        self,
        *,
        workspace_id: str,
        canonical_source_id: str,
        at_notebook_id: str,
    ) -> DeleteResult:
        """Delete a derived doc at ONE notebook's endpoint — refuse cross-workspace (§29).

        The delete target is resolved for ``(workspace_id, canonical_source_id)``; the
        dispatch route is ``at_notebook_id``. If the target's workspace is not the one
        owned by ``at_notebook_id``, the delete is structurally refused even though the
        vendor derived doc id is identical across workspaces.
        """
        target = self._store.resolve_delete_target(workspace_id, canonical_source_id)
        route = self._router.route_for(at_notebook_id)
        if target.workspace_id != route.workspace_id:
            raise CrossWorkspaceDeleteRefused(
                f"delete for ({workspace_id}, {canonical_source_id}) cannot be "
                f"dispatched to {at_notebook_id} (workspace {route.workspace_id}) — "
                "cross-workspace deletion refused (design §29)"
            )
        require_operation_allowed(
            self._provider_run_auth, ProviderOperationClass.GRAPH_DELETE
        )
        self._budget.reserve(BudgetClass.GRAPH_DELETE)
        backend = self._delete_backend_factory(route)
        return await backend.delete_document(
            derived_document_id=target.derived_document_id
        )

    async def run(
        self,
        scenario: MembershipRemovalScenario,
        *,
        members_after: Mapping[str, frozenset],
    ) -> MembershipRemovalOutcome:
        """Execute delete (NB_A only) + 2 GD + 2 vector re-probes (design §28)."""
        removed_nb = scenario.removed_from_notebook
        retained_nb = scenario.retained_notebook
        route_removed = self._router.route_for(removed_nb)
        shared = scenario.shared_source

        # 1) canonical edge removal is represented by ``members_after`` (edge-only).
        # 2) derived graph delete at NB_A's endpoint ONLY (design §29).
        delete_target = self._store.resolve_delete_target(
            route_removed.workspace_id, shared
        )
        delete_result = await self.delete_membership(
            workspace_id=route_removed.workspace_id,
            canonical_source_id=shared,
            at_notebook_id=removed_nb,
        )

        # 3) map the two SHARED_SOURCE re-probe queries to their notebooks.
        removed_qid = self._reprobe_qid_for(scenario, removed_nb)
        retained_qid = self._reprobe_qid_for(scenario, retained_nb)

        # 4) GD + vector re-probes with the POST-removal member snapshots (§39).
        removed_after = await self._reprobe(
            query_id=removed_qid,
            notebook_id=removed_nb,
            member_source_ids=frozenset(members_after[removed_nb]),
            graph_delete_succeeded=delete_result.succeeded,
        )
        retained_after = await self._reprobe(
            query_id=retained_qid,
            notebook_id=retained_nb,
            member_source_ids=frozenset(members_after[retained_nb]),
            graph_delete_succeeded=delete_result.succeeded,
        )
        return MembershipRemovalOutcome(
            shared_source=shared,
            delete_target=delete_target,
            graph_delete_succeeded=delete_result.succeeded,
            removed_after=removed_after,
            retained_after=retained_after,
        )

    def _reprobe_qid_for(
        self, scenario: MembershipRemovalScenario, notebook_id: str
    ) -> str:
        for qid in scenario.reprobe_query_ids:
            if self._fx.query(qid).notebook_id == notebook_id:
                return qid
        raise RoutingViolation(
            f"no removal re-probe query for notebook {notebook_id!r}"
        )

    async def _reprobe(
        self,
        *,
        query_id: str,
        notebook_id: str,
        member_source_ids: frozenset,
        graph_delete_succeeded: bool,
    ) -> RemovalProbeResult:
        question = self._fx.query(query_id).question
        gd = await self._gd.query(
            query_id=query_id, notebook_id=notebook_id, question=question
        )
        vector = await self._vector.query(
            query_id=query_id,
            notebook_id=notebook_id,
            question=question,
            member_source_ids=member_source_ids,
        )
        return RemovalProbeResult(
            query_id=query_id,
            notebook_id=notebook_id,
            phase=RemovalPhase.AFTER,
            gd_evidence=gd.evidence,
            vector_evidence=vector.evidence,
            graph_delete_succeeded=graph_delete_succeeded,
        )


__all__ = [
    "CrossWorkspaceDeleteRefused",
    "DeleteResult",
    "DeleteBackend",
    "DeleteBackendFactory",
    "MembershipRemovalOutcome",
    "MembershipRemovalExecutor",
]
