"""PN02D-B0B — GD + notebook-local vector executors (design §18-§23/§31-§36)."""

from __future__ import annotations

import graphrag_pn02db0b_common as C
import pytest

from open_notebook.integrations.graphrag.eval.budgetlivepn02d import (
    BudgetClass,
    StatefulBudgetGuard,
    WorkloadCapExceeded,
)
from open_notebook.integrations.graphrag.eval.fakeslivepn02d import (
    FakeGDBackend,
    FakeVectorBackend,
    make_gd_backend_factory,
    make_vector_backend_factory,
)
from open_notebook.integrations.graphrag.eval.gdlivepn02d import GDQueryExecutor
from open_notebook.integrations.graphrag.eval.manifestpn02 import (
    UnattestedWorkspaceError,
)
from open_notebook.integrations.graphrag.eval.routelivepn02d import attest_route
from open_notebook.integrations.graphrag.eval.schemaspn02 import TechnicalOutcome
from open_notebook.integrations.graphrag.eval.vectorlivepn02d import (
    NotebookLocalVectorExecutor,
)


def _gd_executor(fx, backend, *, budget=None, attestations=None):
    router = C.router_for(fx)
    auth, _ia, qa = C.query_auth()
    return GDQueryExecutor(
        router=router,
        budget=budget or StatefulBudgetGuard(),
        provider_run_auth=auth,
        query_auth=qa,
        attestations=attestations or C.attestations_for(fx, router),
        backend_factory=make_gd_backend_factory(backend),
        source_allowlist=fx.source_keys,
    )


def _vec_executor(fx, backend, *, budget=None):
    router = C.router_for(fx)
    auth, _ia, qa = C.query_auth()
    return NotebookLocalVectorExecutor(
        router=router,
        budget=budget or StatefulBudgetGuard(),
        provider_run_auth=auth,
        query_auth=qa,
        backend_factory=make_vector_backend_factory(backend),
        source_allowlist=fx.source_keys,
    )


# ------------------------------- GD ---------------------------------------- #

@pytest.mark.asyncio
async def test_gd_returns_member_evidence():
    fx = C.fixture()
    backend = FakeGDBackend({"q1": ["A1", "A2"]})
    ex = _gd_executor(fx, backend)
    res = await ex.query(query_id="PN02Q01", notebook_id="NB_A", question="q1")
    assert res.outcome is TechnicalOutcome.COMPLETED
    assert res.evidence.as_set() == frozenset({"A1", "A2"})
    assert res.evidence.ordered is False


@pytest.mark.asyncio
async def test_gd_provenance_foreign_and_malformed_counted():
    fx = C.fixture()
    backend = FakeGDBackend({"q1": ["A1", "ZZZ_not_a_source", None, "  "]})
    ex = _gd_executor(fx, backend)
    res = await ex.query(query_id="PN02Q01", notebook_id="NB_A", question="q1")
    assert res.evidence.as_set() == frozenset({"A1"})
    assert res.evidence.stats.foreign == 1
    assert res.evidence.stats.malformed == 2


@pytest.mark.asyncio
async def test_gd_unattested_workspace_refused():
    fx = C.fixture()
    router = C.router_for(fx)
    # Build a deliberately failing attestation for NB_A.
    bad = attest_route(router.route_for("NB_A"), observed_workspace_id="nb_wrong")
    attestations = C.attestations_for(fx, router)
    attestations["NB_A"] = bad
    ex = _gd_executor(fx, FakeGDBackend({"q1": ["A1"]}), attestations=attestations)
    with pytest.raises(UnattestedWorkspaceError):
        await ex.query(query_id="PN02Q01", notebook_id="NB_A", question="q1")


@pytest.mark.asyncio
async def test_gd_cap_refuses_27th_before_backend():
    fx = C.fixture()
    budget = StatefulBudgetGuard()
    for _ in range(26):
        budget.reserve(BudgetClass.GD_QUERY)
    backend = FakeGDBackend({"q1": ["A1"]})
    ex = _gd_executor(fx, backend, budget=budget)
    with pytest.raises(WorkloadCapExceeded):
        await ex.query(query_id="PN02Q01", notebook_id="NB_A", question="q1")
    assert backend.calls == 0


@pytest.mark.asyncio
async def test_gd_backend_error_maps_to_failed_gd_query():
    fx = C.fixture()
    backend = FakeGDBackend({}, error_questions=["q1"])
    ex = _gd_executor(fx, backend)
    res = await ex.query(query_id="PN02Q01", notebook_id="NB_A", question="q1")
    assert res.outcome is TechnicalOutcome.FAILED_GD_QUERY
    assert res.evidence.as_set() == frozenset()


# ------------------------------ Vector ------------------------------------- #

@pytest.mark.asyncio
async def test_vector_one_embedding_k3_prefix_of_k5():
    fx = C.fixture()
    members = fx.members_of("NB_A")
    backend = FakeVectorBackend({"q1": sorted(members)})
    ex = _vec_executor(fx, backend)
    res = await ex.query(
        query_id="PN02Q01", notebook_id="NB_A", question="q1", member_source_ids=members
    )
    assert res.outcome is TechnicalOutcome.COMPLETED
    assert backend.embed_calls == 1  # ONE embedding for both K
    k3 = res.evidence.top_k(3)
    k5 = res.evidence.top_k(5)
    assert k5[:3] == k3  # K=3 is a slice of the single K=5 ranking


@pytest.mark.asyncio
async def test_vector_candidate_universe_is_members_only():
    fx = C.fixture()
    members = fx.members_of("NB_A")
    backend = FakeVectorBackend({"q1": sorted(members)})
    ex = _vec_executor(fx, backend)
    await ex.query(
        query_id="PN02Q01", notebook_id="NB_A", question="q1", member_source_ids=members
    )
    assert set(backend.last_candidates) == set(members)  # never a global set


@pytest.mark.asyncio
async def test_vector_global_topk_cannot_leak_foreign_sources():
    """A global ranking would surface foreign Sources first; the member-scoped
    executor never lets a foreign Source into the candidate universe (design §36)."""
    fx = C.fixture()
    members = fx.members_of("NB_A")
    # Foreign notebook Sources rank ABOVE this notebook's members globally.
    foreign = ["B1", "B2", "C1", "C2"]
    global_order = foreign + sorted(members)
    backend = FakeVectorBackend(global_order=global_order)
    ex = _vec_executor(fx, backend)
    res = await ex.query(
        query_id="PN02Q01", notebook_id="NB_A", question="q1", member_source_ids=members
    )
    result_set = res.evidence.as_set()
    assert result_set <= set(members)  # only notebook-local Sources
    assert not (result_set & set(foreign))  # foreign never present
    assert set(backend.last_candidates) == set(members)


@pytest.mark.asyncio
async def test_vector_cap_refuses_27th():
    fx = C.fixture()
    budget = StatefulBudgetGuard()
    for _ in range(26):
        budget.reserve(BudgetClass.VECTOR_QUERY)
    backend = FakeVectorBackend({"q1": []})
    ex = _vec_executor(fx, backend, budget=budget)
    with pytest.raises(WorkloadCapExceeded):
        await ex.query(
            query_id="PN02Q01", notebook_id="NB_A", question="q1",
            member_source_ids=fx.members_of("NB_A"),
        )
    assert backend.embed_calls == 0


@pytest.mark.asyncio
async def test_vector_query_embedding_cap_refuses_before_embed():
    fx = C.fixture()
    budget = StatefulBudgetGuard()
    for _ in range(26):
        budget.reserve(BudgetClass.QUERY_EMBEDDING)
    backend = FakeVectorBackend({"q1": []})
    ex = _vec_executor(fx, backend, budget=budget)
    with pytest.raises(WorkloadCapExceeded):
        await ex.query(
            query_id="PN02Q01", notebook_id="NB_A", question="q1",
            member_source_ids=fx.members_of("NB_A"),
        )
    assert backend.embed_calls == 0
