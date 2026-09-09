"""Exact notebook-local vector executor for the PN02 live driver (PN02D-B0B).

EVALUATION-ONLY. Nothing in production imports this (``PRODUCTION_IMPORTS_EVAL =
NO``). This is the net-new eval-only layer E (design §5-E/§21/§22, task §31-§36).

**Hard semantics (design §R1.8):**
``PN02_VECTOR_BASELINE_CANDIDATE_UNIVERSE = CURRENT_SOURCES_OF_QUERIED_NOTEBOOK_ONLY``
and ``GLOBAL_TOPK_THEN_POSTFILTER_ALLOWED = NO``. Candidate restriction happens
BEFORE ranking: the executor resolves the notebook's CURRENT member Source ids and
hands ONLY those to the backend for scoring — the backend never sees a non-member,
so a global-top-K-then-post-filter implementation is structurally impossible here
(approach A / equivalent approach B, design §32/§33).

**One embedding, one ranking (design §34):** the query is embedded ONCE
(``VECTOR_QUERY_EMBEDDING`` op, one ``QUERY_EMBEDDING`` budget unit) and one ranked
member list is produced; K=3 and K=5 are SLICES of that single list
(``VECTOR_K3_K5_ONE_RANKING = YES``). The DB/vector execution boundary is injected
and fakeable — B0B performs NO live provider embedding and NO DB query. Genuine
cosine scores (if the backend supplies them) are never modified or fabricated
(design §23).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import (
    AbstractSet,
    Callable,
    List,
    Optional,
    Protocol,
    Sequence,
    Tuple,
)

from open_notebook.integrations.graphrag.eval.authlivepn02d import (
    PN02ProviderRunAuthorization,
    ProviderOperationClass,
    QueryAuthorization,
    assert_run_ids_consistent,
    require_operation_allowed,
    require_provider_run_authorization,
    require_query_authorization,
)
from open_notebook.integrations.graphrag.eval.budgetlivepn02d import (
    BudgetClass,
    StatefulBudgetGuard,
)
from open_notebook.integrations.graphrag.eval.normalizepn02 import normalize_vector
from open_notebook.integrations.graphrag.eval.routelivepn02d import (
    NotebookRuntimeRoute,
    PN02Router,
)
from open_notebook.integrations.graphrag.eval.schemaspn02 import (
    TechnicalOutcome,
    VectorEvidenceResult,
)


class VectorBackendError(RuntimeError):
    """The injected vector backend could not produce a ranking (content-free)."""


class VectorBackend(Protocol):
    """Notebook-bound vector backend (DB/vector boundary). A fake in B0B.

    ``embed_query`` is the single per-query embedding (one provider call in B1);
    ``rank_members`` scores ONLY the supplied candidate member ids — it must never
    consult a global corpus (that is what makes candidate restriction pre-ranking).
    """

    async def embed_query(self, question: str) -> Tuple[float, ...]: ...

    async def rank_members(
        self,
        *,
        query_embedding: Sequence[float],
        candidate_source_ids: Sequence[str],
    ) -> Sequence[str]: ...


VectorBackendFactory = Callable[[NotebookRuntimeRoute], VectorBackend]


@dataclass(frozen=True)
class VectorRankingTrace:
    """Content-safe trace proving pre-ranking restriction (design §36)."""

    query_id: str
    notebook_id: str
    candidate_universe_size: int
    embedding_calls: int
    ranked_size: int


class NotebookLocalVectorExecutor:
    """Member-scoped ranked retrieval — restriction BEFORE ranking (design §21/§32)."""

    def __init__(
        self,
        *,
        router: PN02Router,
        budget: StatefulBudgetGuard,
        provider_run_auth: PN02ProviderRunAuthorization,
        query_auth: QueryAuthorization,
        backend_factory: VectorBackendFactory,
        source_allowlist: AbstractSet[str],
    ) -> None:
        self._router = router
        self._budget = budget
        # L-2 (design §8): unforgeable capability-type guard at init.
        self._provider_run_auth = require_provider_run_authorization(provider_run_auth)
        self._query_auth = require_query_authorization(query_auth)
        # L-1 (design §8): pin the run identity + cross-check every capability.
        self._run_id = self._provider_run_auth.run_id
        assert_run_ids_consistent(
            self._run_id, self._provider_run_auth, self._query_auth
        )
        self._backend_factory = backend_factory
        self._allowlist = frozenset(source_allowlist)
        self._last_trace: Optional[VectorRankingTrace] = None

    @property
    def last_trace(self) -> Optional[VectorRankingTrace]:
        return self._last_trace

    async def query(
        self,
        *,
        query_id: str,
        notebook_id: str,
        question: str,
        member_source_ids: AbstractSet[str],
    ) -> VectorEvidenceResult:
        """One notebook-local vector query. ``member_source_ids`` is the CURRENT snapshot.

        For a removal re-probe the caller passes the POST-removal member set, so a
        removed Source is excluded from the candidate universe BEFORE ranking
        (design §28/§39).
        """
        require_query_authorization(self._query_auth)
        # L-1: fail closed BEFORE dispatch on any run_id drift.
        assert_run_ids_consistent(
            self._run_id, self._provider_run_auth, self._query_auth
        )
        require_operation_allowed(
            self._provider_run_auth, ProviderOperationClass.VECTOR_NOTEBOOK_QUERY
        )
        route = self._router.route_for(notebook_id)
        self._router.validate_route(
            notebook_id,
            target_endpoint=route.endpoint,
            target_workspace_id=route.workspace_id,
        )

        # Candidate universe = CURRENT members only, resolved BEFORE ranking (§R1.8).
        candidates: List[str] = sorted(member_source_ids)

        self._budget.reserve(BudgetClass.VECTOR_QUERY)
        # ONE query embedding shared by K=3/K=5 (design §22/§34).
        require_operation_allowed(
            self._provider_run_auth, ProviderOperationClass.VECTOR_QUERY_EMBEDDING
        )
        self._budget.reserve(BudgetClass.QUERY_EMBEDDING)

        backend = self._backend_factory(route)
        try:
            query_embedding = await backend.embed_query(question)
            ranked = await backend.rank_members(
                query_embedding=query_embedding, candidate_source_ids=candidates
            )
        except VectorBackendError:
            self._last_trace = VectorRankingTrace(
                query_id=query_id,
                notebook_id=notebook_id,
                candidate_universe_size=len(candidates),
                embedding_calls=1,
                ranked_size=0,
            )
            return VectorEvidenceResult(
                query_id=query_id,
                notebook_id=notebook_id,
                evidence=normalize_vector([], allowlist=self._allowlist),
                latency_ms=None,
                outcome=TechnicalOutcome.FAILED_VECTOR_QUERY,
            )

        # Defense in depth: the backend must not have returned a non-member. Any id
        # outside the candidate universe is a contract breach -> drop it (never a
        # post-filter that could rescue a global-top-K design).
        candidate_set = set(candidates)
        restricted = [s for s in ranked if s in candidate_set]
        evidence = normalize_vector(restricted, allowlist=self._allowlist)
        self._last_trace = VectorRankingTrace(
            query_id=query_id,
            notebook_id=notebook_id,
            candidate_universe_size=len(candidates),
            embedding_calls=1,
            ranked_size=len(evidence.source_ids),
        )
        return VectorEvidenceResult(
            query_id=query_id,
            notebook_id=notebook_id,
            evidence=evidence,
            latency_ms=None,
            outcome=TechnicalOutcome.COMPLETED,
        )


__all__ = [
    "VectorBackendError",
    "VectorBackend",
    "VectorBackendFactory",
    "VectorRankingTrace",
    "NotebookLocalVectorExecutor",
]
