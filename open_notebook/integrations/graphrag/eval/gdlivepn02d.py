"""Per-notebook GD ``/query/data`` executor for the PN02 live driver (PN02D-B0B).

EVALUATION-ONLY. Nothing in production imports this (``PRODUCTION_IMPORTS_EVAL =
NO``). A notebook-isolated, attestation-gated structured-evidence query (design
§5-D/§18/§19/§20). The GD backend is INJECTED (a fake in B0B; a thin adapter over
``gd_seam.GDQueryClient`` — ``only_need_context=True``, ``final_answer_generation=
False``, never ``client.query()`` — satisfies it in B1). Every op requires the
``QueryAuthorization`` capability (24/24), the operation allowlist
(``GD_QUERY_DATA``), a fail-closed ``require_attested_before_query`` + notebook-match
route check, and a budget reservation BEFORE dispatch. The raw vendor payload never
crosses this seam: the backend yields candidate canonical Source ids, which are
projected to a PN02 ``NormalizedEvidencePN02`` UNORDERED set (foreign/malformed are
dropped and counted, feeding the Stage-1 exact-``=0`` gate, design §20/§30).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import AbstractSet, Callable, Dict, Mapping, Optional, Protocol, Sequence

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
from open_notebook.integrations.graphrag.eval.manifestpn02 import (
    WorkspaceAttestation,
    require_attested_before_query,
)
from open_notebook.integrations.graphrag.eval.normalizepn02 import normalize_graph
from open_notebook.integrations.graphrag.eval.routelivepn02d import (
    NotebookRuntimeRoute,
    PN02Router,
)
from open_notebook.integrations.graphrag.eval.routingpn02c import RoutingViolation
from open_notebook.integrations.graphrag.eval.schemaspn02 import (
    GDEvidenceResult,
    TechnicalOutcome,
)


class GDBackendError(RuntimeError):
    """The injected GD backend could not produce a usable evidence set (content-free)."""


@dataclass(frozen=True)
class GDBackendResult:
    """Raw candidate projection from a GD backend (pre-PN02-normalization).

    ``candidate_source_ids`` are the STRONG-anchor projected candidates (may include
    foreign / malformed / ``None`` for provenance testing); the PN02 normalizer
    validates them against the fixture allowlist.
    """

    candidate_source_ids: Sequence[Optional[str]]
    latency_ms: Optional[int] = None


class GDQueryBackend(Protocol):
    """Notebook-bound GD backend. A fake in B0B; ``gd_seam.GDQueryClient`` in B1."""

    async def query_evidence(
        self, question: str, *, benchmark_ids: Optional[AbstractSet[str]] = None
    ) -> GDBackendResult: ...


GDBackendFactory = Callable[[NotebookRuntimeRoute], GDQueryBackend]


class GDQueryExecutor:
    """Executes attestation-gated, notebook-isolated GD queries -> ``GDEvidenceResult``."""

    def __init__(
        self,
        *,
        router: PN02Router,
        budget: StatefulBudgetGuard,
        provider_run_auth: PN02ProviderRunAuthorization,
        query_auth: QueryAuthorization,
        attestations: Mapping[str, WorkspaceAttestation],
        backend_factory: GDBackendFactory,
        source_allowlist: AbstractSet[str],
    ) -> None:
        self._router = router
        self._budget = budget
        self._provider_run_auth = provider_run_auth
        self._query_auth = require_query_authorization(query_auth)
        self._attestations = dict(attestations)
        self._backend_factory = backend_factory
        self._allowlist = frozenset(source_allowlist)

    def _authorize(self, notebook_id: str) -> NotebookRuntimeRoute:
        require_query_authorization(self._query_auth)
        require_operation_allowed(
            self._provider_run_auth, ProviderOperationClass.GD_QUERY_DATA
        )
        attestation = self._attestations.get(notebook_id)
        if attestation is None:
            raise RoutingViolation(f"no attestation for {notebook_id!r}")
        require_attested_before_query(attestation)
        if attestation.notebook_id != notebook_id:
            raise RoutingViolation(
                f"attestation is for {attestation.notebook_id!r}, not {notebook_id!r}"
            )
        route = self._router.route_for(notebook_id)
        return self._router.validate_route(
            notebook_id,
            target_endpoint=route.endpoint,
            target_workspace_id=route.workspace_id,
        )

    async def query(
        self,
        *,
        query_id: str,
        notebook_id: str,
        question: str,
    ) -> GDEvidenceResult:
        """Run one GD query (fail-closed gates first), returning a PN02 GD result."""
        route = self._authorize(notebook_id)
        self._budget.reserve(BudgetClass.GD_QUERY)
        backend = self._backend_factory(route)
        try:
            result = await backend.query_evidence(
                question, benchmark_ids=self._allowlist
            )
        except GDBackendError:
            return GDEvidenceResult(
                query_id=query_id,
                notebook_id=notebook_id,
                evidence=normalize_graph([], allowlist=self._allowlist),
                latency_ms=None,
                outcome=TechnicalOutcome.FAILED_GD_QUERY,
            )
        evidence = normalize_graph(
            list(result.candidate_source_ids), allowlist=self._allowlist
        )
        return GDEvidenceResult(
            query_id=query_id,
            notebook_id=notebook_id,
            evidence=evidence,
            latency_ms=result.latency_ms,
            outcome=TechnicalOutcome.COMPLETED,
        )


@dataclass(frozen=True)
class GDDiagnostics:
    """Content-safe per-query GD provenance diagnostics (design §20)."""

    query_id: str
    notebook_id: str
    valid_unique: int = 0
    foreign: int = 0
    malformed: int = 0
    duplicates: int = 0
    extras: Dict[str, object] = field(default_factory=dict)


def gd_diagnostics(result: GDEvidenceResult) -> GDDiagnostics:
    s = result.evidence.stats
    return GDDiagnostics(
        query_id=result.query_id,
        notebook_id=result.notebook_id,
        valid_unique=s.valid_unique,
        foreign=s.foreign,
        malformed=s.malformed,
        duplicates=s.duplicates,
    )


__all__ = [
    "GDBackendError",
    "GDBackendResult",
    "GDQueryBackend",
    "GDBackendFactory",
    "GDQueryExecutor",
    "GDDiagnostics",
    "gd_diagnostics",
]
