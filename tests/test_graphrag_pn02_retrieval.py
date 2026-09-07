"""GraphRAG-PN02 retrieval decision matrix R0-R4 + full-notebook trap (task §51).

OFFLINE. Table-driven over the frozen §17b first-match rules, plus the exact 8/8
breadth trap and the adversarial one-required-many-false-positive case.
"""

from __future__ import annotations

import pytest

from open_notebook.integrations.graphrag.eval.decisionspn02 import (
    is_full_notebook_return,
    retrieval_decision,
)
from open_notebook.integrations.graphrag.eval.metricspn02 import (
    IncrementalRetrievalTotals,
    RetrievalProbe,
    incremental_retrieval_totals,
)
from open_notebook.integrations.graphrag.eval.schemaspn02 import ScienceVerdict


def totals(new_req, new_fp, d, neg_gd=0, neg_v5=0, mh=0) -> IncrementalRetrievalTotals:
    return IncrementalRetrievalTotals(
        new_req=new_req,
        new_fp=new_fp,
        sum_increment=new_req + new_fp,
        incremental_graph_precision=None if (new_req + new_fp) == 0 else new_req / (new_req + new_fp),
        n_gain_notebooks_d=d,
        neg_return_gd=neg_gd,
        neg_return_v5=neg_v5,
        multihop_incremental_required_recovery=mh,
    )


@pytest.mark.parametrize(
    "isolation,t,expected,rule",
    [
        (False, totals(3, 0, 2), ScienceVerdict.NOT_EVALUATED, "R0"),
        (True, totals(0, 0, 0), ScienceVerdict.NO, "R1"),
        (True, totals(3, 0, 0), ScienceVerdict.NO, "R2"),        # gains only from full-8
        (True, totals(3, 1, 2), ScienceVerdict.YES, "R3"),
        (True, totals(2, 0, 1), ScienceVerdict.INCONCLUSIVE, "R4"),  # gain in 1 notebook
        (True, totals(1, 5, 2), ScienceVerdict.INCONCLUSIVE, "R4"),  # NEW_FP >= NEW_REQ
        (True, totals(3, 1, 2, neg_gd=2, neg_v5=1), ScienceVerdict.INCONCLUSIVE, "R4"),  # GD regresses negatives
    ],
)
def test_retrieval_decision_matrix(isolation, t, expected, rule) -> None:
    dec = retrieval_decision(isolation, t)
    assert dec.verdict is expected
    assert dec.rule == rule


def test_r3_boundary_equal_new_req_new_fp_is_inconclusive() -> None:
    # Criterion C is STRICT: NEW_REQ == NEW_FP does not qualify.
    dec = retrieval_decision(True, totals(2, 2, 2))
    assert dec.verdict is ScienceVerdict.INCONCLUSIVE and dec.rule == "R4"


def test_r3_negatives_tie_allowed() -> None:
    # Criterion E is <=, so equal negatives still permits YES.
    dec = retrieval_decision(True, totals(3, 1, 2, neg_gd=1, neg_v5=1))
    assert dec.verdict is ScienceVerdict.YES and dec.rule == "R3"


def test_full_notebook_trap_exact() -> None:
    assert is_full_notebook_return(1.0) is True
    assert is_full_notebook_return(0.875) is False  # 7/8 is NOT full
    assert is_full_notebook_return(0.99) is False


def test_full_8_return_is_not_a_discriminative_gain() -> None:
    # GD adds a required Source V5 missed, but returns ALL 8 members -> fraction 1.0
    # -> the query is NOT a discriminative gain -> n_gain_notebooks_d does not count it.
    members = {f"A{i}" for i in range(1, 7)} | {"SH_AB", "SH_AC"}
    assert len(members) == 8
    probe = RetrievalProbe(
        query_id="Q",
        notebook_id="NB_A",
        required=frozenset({"A1", "A2"}),
        optional=frozenset(),
        gd_member_set=frozenset(members),          # returns everything
        v5_set=frozenset({"A2", "A3", "A4", "A5", "A6"}),  # missed A1
        is_negative=False,
        is_multihop=False,
    )
    t = incremental_retrieval_totals([probe])
    assert t.new_req == 1              # A1 is a new required Source
    assert t.n_gain_notebooks_d == 0   # but the full-8 return disqualifies it
    dec = retrieval_decision(True, t)
    assert dec.verdict is ScienceVerdict.NO and dec.rule == "R2"


def test_seven_of_eight_can_still_gain() -> None:
    # 7/8 (< 1.0) is governed by NEW_REQ > NEW_FP, not disqualified by breadth.
    probeA = RetrievalProbe(
        query_id="QA", notebook_id="NB_A",
        required=frozenset({"A1"}), optional=frozenset(),
        gd_member_set=frozenset({"A1", "A2", "A3", "A4", "A5", "A6", "SH_AB"}),  # 7/8
        v5_set=frozenset({"A2", "A3", "A4", "A5", "A6"}),
        is_negative=False, is_multihop=False,
    )
    probeB = RetrievalProbe(
        query_id="QB", notebook_id="NB_B",
        required=frozenset({"B1"}), optional=frozenset(),
        gd_member_set=frozenset({"B1", "B2"}),
        v5_set=frozenset({"B2"}),
        is_negative=False, is_multihop=False,
    )
    t = incremental_retrieval_totals([probeA, probeB])
    assert t.n_gain_notebooks_d == 2
    assert t.new_req == 2


def test_adversarial_one_required_many_false_positives_not_yes() -> None:
    # NEW_REQ=1, NEW_FP large across 2 notebooks: must NOT be YES.
    pA = RetrievalProbe(
        query_id="QA", notebook_id="NB_A",
        required=frozenset({"A1"}), optional=frozenset(),
        gd_member_set=frozenset({"A1", "A2", "A3", "A4"}),  # A2..A4 = false positives
        v5_set=frozenset(), is_negative=False, is_multihop=False,
    )
    pB = RetrievalProbe(
        query_id="QB", notebook_id="NB_B",
        required=frozenset({"B1"}), optional=frozenset({"B1"}),
        gd_member_set=frozenset({"B2", "B3"}),  # no new required, extra FPs
        v5_set=frozenset({"B1"}), is_negative=False, is_multihop=False,
    )
    t = incremental_retrieval_totals([pA, pB])
    assert t.new_req == 1
    assert t.new_fp >= 3
    dec = retrieval_decision(True, t)
    assert dec.verdict is not ScienceVerdict.YES
