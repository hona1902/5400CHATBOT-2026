"""Fake injected backends for the offline PN02 live driver (PN02D-B0B).

EVALUATION-ONLY. Nothing in production imports this (``PRODUCTION_IMPORTS_EVAL =
NO``). Deterministic in-memory fakes implementing the exact injected Protocols the
B1 driver consumes (index client / GD backend / vector backend / delete backend), so
the ENTIRE orchestrator runs offline with ZERO provider/network/DB traffic (design
§25/§41). Each fake yields exactly the record shapes a real backend would, so the
same records flow into the frozen PN02B evaluator and produce the same decisions. No
fake opens a socket, starts a container, or reads a secret.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Mapping, Optional, Sequence

from open_notebook.integrations.graphrag.eval.gdlivepn02d import (
    GDBackendError,
    GDBackendFactory,
    GDBackendResult,
    GDQueryBackend,
)
from open_notebook.integrations.graphrag.eval.indexlivepn02d import (
    STATE_PROCESSED,
    IndexClientFactory,
)
from open_notebook.integrations.graphrag.eval.live_indexer08 import (
    CellIndexClient,
    IndexStatusResult,
    IndexSubmitResult,
)
from open_notebook.integrations.graphrag.eval.removallivepn02d import (
    DeleteBackend,
    DeleteBackendFactory,
    DeleteResult,
)
from open_notebook.integrations.graphrag.eval.routelivepn02d import NotebookRuntimeRoute
from open_notebook.integrations.graphrag.eval.vectorlivepn02d import (
    VectorBackend,
    VectorBackendError,
    VectorBackendFactory,
)

# --------------------------------------------------------------------------- #
# Fake index client (submit/poll)
# --------------------------------------------------------------------------- #

@dataclass
class FakeAttempt:
    """One scripted index attempt outcome (deterministic)."""

    submit_accepted: bool = True
    submit_detail: Optional[str] = None
    terminal_state: str = STATE_PROCESSED  # PROCESSED | FAILED | TIMEOUT
    status_detail: Optional[str] = None


class FakeCellIndexClient(CellIndexClient):
    """Deterministic per-source scripted index client (submit + status)."""

    def __init__(
        self,
        attempts_by_source: Optional[Mapping[str, Sequence[FakeAttempt]]] = None,
        *,
        default_attempt: Optional[FakeAttempt] = None,
    ) -> None:
        self._attempts: Dict[str, List[FakeAttempt]] = {
            k: list(v) for k, v in (attempts_by_source or {}).items()
        }
        self._default = default_attempt or FakeAttempt()
        self._attempt_idx: Dict[str, int] = {}
        self._pending: Dict[str, FakeAttempt] = {}
        self.submit_calls = 0
        self.status_calls = 0

    async def submit(self, *, source_id: str, canonical_text: str) -> IndexSubmitResult:
        self.submit_calls += 1
        idx = self._attempt_idx.get(source_id, 0)
        seq = self._attempts.get(source_id) or [self._default]
        att = seq[idx] if idx < len(seq) else seq[-1]
        self._attempt_idx[source_id] = idx + 1
        if not att.submit_accepted:
            return IndexSubmitResult(
                accepted=False, track_id=None, detail=att.submit_detail
            )
        track_id = f"{source_id}#att{idx}"
        self._pending[track_id] = att
        return IndexSubmitResult(accepted=True, track_id=track_id, detail=None)

    async def status(self, *, track_id: str) -> IndexStatusResult:
        self.status_calls += 1
        att = self._pending.get(track_id, self._default)
        return IndexStatusResult(state=att.terminal_state, detail=att.status_detail)


def make_index_client_factory(client: CellIndexClient) -> IndexClientFactory:
    """A per-route factory returning one shared fake index client (route ignored)."""

    def _factory(_route: NotebookRuntimeRoute) -> CellIndexClient:
        return client

    return _factory


# --------------------------------------------------------------------------- #
# Fake GD backend
# --------------------------------------------------------------------------- #

class FakeGDBackend(GDQueryBackend):
    """Returns scripted candidate sets keyed by the exact question string."""

    def __init__(
        self,
        candidates_by_question: Mapping[str, Sequence[Optional[str]]],
        *,
        error_questions: Optional[Sequence[str]] = None,
    ) -> None:
        self._by_question = {k: list(v) for k, v in candidates_by_question.items()}
        self._errors = set(error_questions or ())
        self.calls = 0

    async def query_evidence(
        self, question: str, *, benchmark_ids=None
    ) -> GDBackendResult:
        self.calls += 1
        if question in self._errors:
            raise GDBackendError("fake GD backend error (content-free)")
        return GDBackendResult(
            candidate_source_ids=list(self._by_question.get(question, [])),
            latency_ms=1,
        )


def make_gd_backend_factory(backend: GDQueryBackend) -> GDBackendFactory:
    def _factory(_route: NotebookRuntimeRoute) -> GDQueryBackend:
        return backend

    return _factory


# --------------------------------------------------------------------------- #
# Fake vector backend
# --------------------------------------------------------------------------- #

class FakeVectorBackend(VectorBackend):
    """Deterministic ranking. ``embed_query`` counts calls + remembers the question;
    ``rank_members`` ranks ONLY the supplied candidates, proving pre-ranking restriction.

    ``global_order`` is a global preference over ALL sources (incl. foreign ones a
    global-top-K design would surface); because ``rank_members`` only ever sees the
    member candidate set, the global order can never leak a foreign Source in
    (design §36 negative test).
    """

    def __init__(
        self,
        ranking_by_question: Optional[Mapping[str, Sequence[str]]] = None,
        *,
        global_order: Optional[Sequence[str]] = None,
        error_questions: Optional[Sequence[str]] = None,
    ) -> None:
        self._ranking = {k: list(v) for k, v in (ranking_by_question or {}).items()}
        self._global = list(global_order) if global_order is not None else None
        self._errors = set(error_questions or ())
        self.embed_calls = 0
        self.rank_calls = 0
        self.last_candidates: Optional[List[str]] = None
        self._last_question: Optional[str] = None

    async def embed_query(self, question: str):
        if question in self._errors:
            raise VectorBackendError("fake vector backend error (content-free)")
        self.embed_calls += 1
        self._last_question = question
        return (float(len(question)),)

    async def rank_members(self, *, query_embedding, candidate_source_ids):
        self.rank_calls += 1
        self.last_candidates = list(candidate_source_ids)
        cset = set(candidate_source_ids)
        pref = self._ranking.get(self._last_question or "")
        if pref is not None:
            ranked = [s for s in pref if s in cset]
            ranked += sorted(cset - set(ranked))
            return ranked
        if self._global is not None:
            return [s for s in self._global if s in cset]
        return sorted(candidate_source_ids)


def make_vector_backend_factory(backend: VectorBackend) -> VectorBackendFactory:
    def _factory(_route: NotebookRuntimeRoute) -> VectorBackend:
        return backend

    return _factory


# --------------------------------------------------------------------------- #
# Fake delete backend
# --------------------------------------------------------------------------- #

@dataclass
class FakeDeleteBackend(DeleteBackend):
    succeed: bool = True
    calls: int = field(default=0)

    async def delete_document(self, *, derived_document_id: str) -> DeleteResult:
        self.calls += 1
        return DeleteResult(succeeded=self.succeed)


def make_delete_backend_factory(backend: DeleteBackend) -> DeleteBackendFactory:
    def _factory(_route: NotebookRuntimeRoute) -> DeleteBackend:
        return backend

    return _factory


# --------------------------------------------------------------------------- #
# Workspace-graph fakes (phase-aware GD + delete) for the full driver E2E
# --------------------------------------------------------------------------- #

class FakeWorkspaceGraph:
    """A per-workspace derived-graph store: canonical docs + per-question relevance.

    A successful delete removes a canonical doc, so a later GD re-probe no longer
    returns it (modelling the real derived store). A FAILED delete leaves it present
    (stale), exercising the ON post-validation backstop (design §30).
    """

    def __init__(
        self,
        canonical_docs: Sequence[str],
        relevance_by_question: Mapping[str, Sequence[Optional[str]]],
    ) -> None:
        self._docs = set(canonical_docs)
        self._relevance = {k: list(v) for k, v in relevance_by_question.items()}
        self._deleted: set[str] = set()

    def delete_by_derived(self, derived_document_id: str) -> bool:
        from open_notebook.integrations.graphrag.eval.docidpn02d import (
            compute_derived_document_id,
        )

        for canonical in self._docs:
            if compute_derived_document_id(canonical) == derived_document_id:
                self._deleted.add(canonical)
                return True
        return False

    def gd_candidates(self, question: str) -> List[Optional[str]]:
        return [
            c
            for c in self._relevance.get(question, [])
            if not (isinstance(c, str) and c in self._deleted)
        ]


class GraphGDBackend(GDQueryBackend):
    """A GD backend that reads one ``FakeWorkspaceGraph`` (phase-aware)."""

    def __init__(self, graph: FakeWorkspaceGraph) -> None:
        self._graph = graph
        self.calls = 0

    async def query_evidence(
        self, question: str, *, benchmark_ids=None
    ) -> GDBackendResult:
        self.calls += 1
        return GDBackendResult(
            candidate_source_ids=self._graph.gd_candidates(question), latency_ms=1
        )


@dataclass
class GraphDeleteBackend(DeleteBackend):
    graph: FakeWorkspaceGraph
    succeed: bool = True
    calls: int = field(default=0)

    async def delete_document(self, *, derived_document_id: str) -> DeleteResult:
        self.calls += 1
        if not self.succeed:
            # Delete FAILS: the stale doc is intentionally left in the graph (§30).
            return DeleteResult(succeeded=False)
        self.graph.delete_by_derived(derived_document_id)
        return DeleteResult(succeeded=True)


@dataclass
class FakeLightRAGTopology:
    """Per-route fake backends over per-workspace graphs (used by the driver E2E).

    GD graphs AND vector backends are keyed by ``workspace_id`` — the fixture reuses
    the SAME question string across parallel notebooks (18 unique of 24), so a single
    global question-keyed backend would bleed one notebook's ranking into another. A
    per-workspace backend only ever sees its own notebook's (unique) questions.
    """

    graphs_by_workspace: Dict[str, FakeWorkspaceGraph]
    vector_backends_by_workspace: Dict[str, FakeVectorBackend]
    index_client: FakeCellIndexClient
    delete_succeed: bool = True

    def index_client_factory(self) -> IndexClientFactory:
        return make_index_client_factory(self.index_client)

    def gd_backend_factory(self) -> GDBackendFactory:
        def _factory(route: NotebookRuntimeRoute) -> GDQueryBackend:
            return GraphGDBackend(self.graphs_by_workspace[route.workspace_id])

        return _factory

    def vector_backend_factory(self) -> VectorBackendFactory:
        def _factory(route: NotebookRuntimeRoute) -> VectorBackend:
            return self.vector_backends_by_workspace[route.workspace_id]

        return _factory

    def delete_backend_factory(self) -> DeleteBackendFactory:
        def _factory(route: NotebookRuntimeRoute) -> DeleteBackend:
            return GraphDeleteBackend(
                self.graphs_by_workspace[route.workspace_id],
                succeed=self.delete_succeed,
            )

        return _factory


def build_clean_fake_topology(fx, router, *, delete_succeed: bool = True):
    """Build a deterministic, isolation-clean fake topology from the fixture.

    Per notebook: GD returns exactly the query's REQUIRED∪OPTIONAL member Sources
    (leak-free, provenance-clean); the vector ranking is required-first over members.
    This is FAKE data (no scientific logic) — the frozen PN02B evaluator computes
    every verdict.
    """
    from open_notebook.integrations.graphrag.eval.datasetpn02 import NOTEBOOK_IDS

    graphs: Dict[str, FakeWorkspaceGraph] = {}
    vector_backends: Dict[str, FakeVectorBackend] = {}
    for nb in NOTEBOOK_IDS:
        route = router.route_for(nb)
        members = sorted(fx.members_of(nb))
        relevance: Dict[str, List[Optional[str]]] = {}
        ranking: Dict[str, List[str]] = {}
        for q in fx.queries_for_notebook(nb):
            member_relevant = [
                s
                for s in list(q.required_source_ids) + list(q.optional_support_source_ids)
                if s in set(members)
            ]
            relevance[q.question] = list(member_relevant)
            # vector ranking: required-first, then remaining members (deterministic).
            ranking[q.question] = member_relevant + [
                m for m in members if m not in member_relevant
            ]
        graphs[route.workspace_id] = FakeWorkspaceGraph(members, relevance)
        vector_backends[route.workspace_id] = FakeVectorBackend(ranking)
    return FakeLightRAGTopology(
        graphs_by_workspace=graphs,
        vector_backends_by_workspace=vector_backends,
        index_client=FakeCellIndexClient(),
        delete_succeed=delete_succeed,
    )


__all__ = [
    "FakeAttempt",
    "FakeCellIndexClient",
    "make_index_client_factory",
    "FakeGDBackend",
    "make_gd_backend_factory",
    "FakeVectorBackend",
    "make_vector_backend_factory",
    "FakeDeleteBackend",
    "make_delete_backend_factory",
    "FakeWorkspaceGraph",
    "GraphGDBackend",
    "GraphDeleteBackend",
    "FakeLightRAGTopology",
    "build_clean_fake_topology",
]
