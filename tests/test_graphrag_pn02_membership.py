"""GraphRAG-PN02 membership-removal + defense-in-depth tests (task §32-§34).

OFFLINE simulation — no real deletion call. Proves the post-removal postconditions
and that stale graph evidence is rejected by ON membership post-validation even
when the derived-store delete FAILS.
"""

from __future__ import annotations

from open_notebook.integrations.graphrag.eval import datasetpn02 as ds
from open_notebook.integrations.graphrag.eval.membershippn02 import (
    evaluate_removal,
    post_validate,
)
from open_notebook.integrations.graphrag.eval.normalizepn02 import (
    normalize_graph,
    normalize_vector,
)
from open_notebook.integrations.graphrag.eval.schemaspn02 import (
    RemovalPhase,
    RemovalProbeResult,
)

FX = ds.load_fixture()
SCEN = ds.membership_removal_scenario(FX)
ALLOW = FX.source_keys


def _probe(qid, nb, gd_sources, delete_ok=True) -> RemovalProbeResult:
    return RemovalProbeResult(
        query_id=qid,
        notebook_id=nb,
        phase=RemovalPhase.AFTER,
        gd_evidence=normalize_graph(list(gd_sources), allowlist=ALLOW),
        vector_evidence=normalize_vector([], allowlist=ALLOW),
        graph_delete_succeeded=delete_ok,
    )


def test_post_validate_drops_non_members() -> None:
    accepted = post_validate({"A1", "SH_AB", "B3"}, {"A1", "A2"})
    assert accepted == frozenset({"A1"})


def test_normal_removal_postconditions_hold() -> None:
    # Delete succeeded: A's GD no longer returns SH_AB; B still returns it.
    a_after = _probe(SCEN.reprobe_query_ids[0], "NB_A", ["A1", "A2"], delete_ok=True)
    b_after = _probe(SCEN.reprobe_query_ids[1], "NB_B", ["SH_AB", "B1"], delete_ok=True)
    report = evaluate_removal(SCEN, a_after, b_after)
    assert report.postcondition_removed_holds is True
    assert report.postcondition_retained_holds is True
    assert report.both_postconditions_hold is True
    assert report.stale_graph_evidence_accepted_as_valid == 0
    assert "SH_AB" not in report.accepted_evidence_removed_nb
    assert "SH_AB" in report.accepted_evidence_retained_nb


def test_defense_in_depth_stale_evidence_rejected() -> None:
    # Delete FAILED: GD for NB_A STILL returns the stale SH_AB. The ON backstop
    # must reject it -> postcondition A holds, stale accepted == 0 (task §34).
    a_after = _probe(SCEN.reprobe_query_ids[0], "NB_A", ["A1", "SH_AB"], delete_ok=False)
    b_after = _probe(SCEN.reprobe_query_ids[1], "NB_B", ["SH_AB"], delete_ok=True)
    report = evaluate_removal(SCEN, a_after, b_after)
    assert report.graph_delete_succeeded_removed_nb is False
    # The stale Source was returned but must NOT be accepted.
    assert "SH_AB" not in report.accepted_evidence_removed_nb
    assert report.postcondition_removed_holds is True
    assert report.stale_graph_evidence_accepted_as_valid == 0
    # B is unaffected.
    assert report.postcondition_retained_holds is True


def test_defense_in_depth_test_passes_only_if_rejected() -> None:
    # If we bypass the backstop (accept the raw stale evidence), the stale metric
    # would flag it -> proving the metric actually detects the failure it guards.
    a_after = _probe(SCEN.reprobe_query_ids[0], "NB_A", ["A1", "SH_AB"], delete_ok=False)
    # Simulate a BROKEN backstop by post-validating against the pre-removal members
    # (which still include SH_AB) -> SH_AB would be wrongly accepted.
    broken_accept = post_validate(
        a_after.gd_evidence.as_set(), SCEN.members_before_removed_nb
    )
    assert "SH_AB" in broken_accept  # a broken backstop accepts the stale source
    # The correct backstop (current members) rejects it:
    correct_accept = post_validate(
        a_after.gd_evidence.as_set(), SCEN.members_after_removed_nb
    )
    assert "SH_AB" not in correct_accept
