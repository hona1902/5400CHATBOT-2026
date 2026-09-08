"""PN02D-B0B — membership removal + delete isolation + stale-evidence defense (§28-§30)."""

from __future__ import annotations

import graphrag_pn02db0b_common as C
import pytest

from open_notebook.integrations.graphrag.eval.budgetlivepn02d import StatefulBudgetGuard
from open_notebook.integrations.graphrag.eval.datasetpn02 import (
    NOTEBOOK_IDS,
    membership_removal_scenario,
)
from open_notebook.integrations.graphrag.eval.docidpn02d import DerivedDocMappingStore
from open_notebook.integrations.graphrag.eval.fakeslivepn02d import (
    build_clean_fake_topology,
)
from open_notebook.integrations.graphrag.eval.gdlivepn02d import GDQueryExecutor
from open_notebook.integrations.graphrag.eval.membershippn02 import evaluate_removal
from open_notebook.integrations.graphrag.eval.removallivepn02d import (
    CrossWorkspaceDeleteRefused,
    MembershipRemovalExecutor,
)
from open_notebook.integrations.graphrag.eval.vectorlivepn02d import (
    NotebookLocalVectorExecutor,
)


def _build(delete_succeed=True):
    fx = C.fixture()
    router = C.router_for(fx)
    topo = build_clean_fake_topology(fx, router, delete_succeed=delete_succeed)
    auth, _ia, qa = C.query_auth()
    budget = StatefulBudgetGuard()
    store = DerivedDocMappingStore()
    # Register the derived-doc mappings the delete needs (as indexing would).
    for src, nb in fx.memberships:
        store.register(
            canonical_source_id=src,
            workspace_id=router.route_for(nb).workspace_id,
            notebook_id=nb,
        )
    gd = GDQueryExecutor(
        router=router, budget=budget, provider_run_auth=auth, query_auth=qa,
        attestations=C.attestations_for(fx, router),
        backend_factory=topo.gd_backend_factory(), source_allowlist=fx.source_keys,
    )
    vec = NotebookLocalVectorExecutor(
        router=router, budget=budget, provider_run_auth=auth, query_auth=qa,
        backend_factory=topo.vector_backend_factory(), source_allowlist=fx.source_keys,
    )
    ex = MembershipRemovalExecutor(
        fx=fx, router=router, budget=budget, mapping_store=store,
        provider_run_auth=auth, query_auth=qa, gd_executor=gd, vector_executor=vec,
        delete_backend_factory=topo.delete_backend_factory(),
    )
    return fx, router, ex


def _members_after(fx, scenario):
    members_after = {
        scenario.removed_from_notebook: scenario.members_after_removed_nb,
        scenario.retained_notebook: scenario.members_after_retained_nb,
    }
    for nb in NOTEBOOK_IDS:
        members_after.setdefault(nb, fx.members_of(nb))
    return members_after


@pytest.mark.asyncio
async def test_cross_workspace_delete_refused():
    fx, router, ex = _build()
    wa = router.route_for("NB_A").workspace_id
    with pytest.raises(CrossWorkspaceDeleteRefused):
        # A's (workspace, SH_AB) target dispatched to NB_B must be refused.
        await ex.delete_membership(
            workspace_id=wa, canonical_source_id="SH_AB", at_notebook_id="NB_B"
        )


@pytest.mark.asyncio
async def test_successful_removal_isolates_shared_source():
    fx, _router, ex = _build(delete_succeed=True)
    scenario = membership_removal_scenario(fx)
    outcome = await ex.run(scenario, members_after=_members_after(fx, scenario))
    assert outcome.graph_delete_succeeded is True
    # SH_AB gone from NB_A GD evidence, still present for NB_B.
    assert "SH_AB" not in outcome.removed_after.gd_evidence.as_set()
    assert "SH_AB" in outcome.retained_after.gd_evidence.as_set()
    report = evaluate_removal(scenario, outcome.removed_after, outcome.retained_after)
    assert report.both_postconditions_hold
    assert report.stale_graph_evidence_accepted_as_valid == 0


@pytest.mark.asyncio
async def test_delete_failure_stale_evidence_never_accepted():
    fx, _router, ex = _build(delete_succeed=False)
    scenario = membership_removal_scenario(fx)
    outcome = await ex.run(scenario, members_after=_members_after(fx, scenario))
    assert outcome.graph_delete_succeeded is False
    # The stale store STILL returns SH_AB for NB_A (delete failed) ...
    assert "SH_AB" in outcome.removed_after.gd_evidence.as_set()
    # ... but the ON post-validation backstop rejects it (design §30).
    report = evaluate_removal(scenario, outcome.removed_after, outcome.retained_after)
    assert report.postcondition_removed_holds is True
    assert report.stale_graph_evidence_accepted_as_valid == 0


@pytest.mark.asyncio
async def test_vector_removal_excludes_source_before_ranking():
    fx, _router, ex = _build(delete_succeed=True)
    scenario = membership_removal_scenario(fx)
    outcome = await ex.run(scenario, members_after=_members_after(fx, scenario))
    # NB_A vector candidate universe excludes SH_AB (post-removal snapshot).
    assert "SH_AB" not in outcome.removed_after.vector_evidence.as_set()
    # NB_B retains SH_AB in its candidate universe.
    assert "SH_AB" in outcome.retained_after.vector_evidence.as_set()
