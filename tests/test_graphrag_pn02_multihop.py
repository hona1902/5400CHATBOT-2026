"""GraphRAG-PN02 multi-hop decision matrix M0-M3 (task §53).

OFFLINE. Proves >=2 notebooks are required for YES; a single successful multi-hop
notebook alone is INCONCLUSIVE, never a general positive.
"""

from __future__ import annotations

import pytest

from open_notebook.integrations.graphrag.eval.decisionspn02 import multihop_decision
from open_notebook.integrations.graphrag.eval.metricspn02 import (
    RetrievalProbe,
    incremental_retrieval_totals,
)
from open_notebook.integrations.graphrag.eval.schemaspn02 import ScienceVerdict


@pytest.mark.parametrize(
    "isolation,mh,expected,rule",
    [
        (False, 3, ScienceVerdict.NOT_EVALUATED, "M0"),
        (True, 2, ScienceVerdict.YES, "M1"),
        (True, 3, ScienceVerdict.YES, "M1"),
        (True, 0, ScienceVerdict.NO, "M2"),
        (True, 1, ScienceVerdict.INCONCLUSIVE, "M3"),
    ],
)
def test_multihop_matrix(isolation, mh, expected, rule) -> None:
    dec = multihop_decision(isolation, mh)
    assert dec.verdict is expected and dec.rule == rule and dec.mh == mh


def _mh_probe(qid, nb, completed: bool, full_notebook: bool = False) -> RetrievalProbe:
    # Multi-hop query with 2 required Sources; V5 has only one; GD supplies the other.
    required = frozenset({f"{nb}1", f"{nb}4"})
    if full_notebook:
        gd = frozenset({f"{nb}{i}" for i in range(1, 7)} | {"SH_AB", "SH_AC"})
    elif completed:
        gd = frozenset({f"{nb}1", f"{nb}4"})
    else:
        gd = frozenset({f"{nb}4"})
    return RetrievalProbe(
        query_id=qid, notebook_id=nb,
        required=required, optional=frozenset(),
        gd_member_set=gd,
        v5_set=frozenset({f"{nb}4"}),  # missing the {nb}1 hop
        is_negative=False, is_multihop=True,
    )


def test_single_notebook_completion_is_inconclusive() -> None:
    # Only NB_A completes its multi-hop; NB_B/NB_C do not -> MH == 1 -> M3.
    probes = [
        _mh_probe("QA", "A", completed=True),
        _mh_probe("QB", "B", completed=False),
        _mh_probe("QC", "C", completed=False),
    ]
    t = incremental_retrieval_totals(probes)
    assert t.multihop_incremental_required_recovery == 1
    dec = multihop_decision(True, t.multihop_incremental_required_recovery)
    assert dec.verdict is ScienceVerdict.INCONCLUSIVE and dec.rule == "M3"


def test_two_notebook_completion_is_yes() -> None:
    probes = [
        _mh_probe("QA", "A", completed=True),
        _mh_probe("QB", "B", completed=True),
        _mh_probe("QC", "C", completed=False),
    ]
    t = incremental_retrieval_totals(probes)
    assert t.multihop_incremental_required_recovery == 2
    assert multihop_decision(True, t.multihop_incremental_required_recovery).verdict is ScienceVerdict.YES


def test_full_notebook_multihop_does_not_count() -> None:
    # A multi-hop 'completion' that returns the whole notebook (fraction 1.0) is
    # NOT a valid incremental recovery.
    probes = [
        _mh_probe("QA", "A", completed=True, full_notebook=True),
        _mh_probe("QB", "B", completed=True, full_notebook=True),
        _mh_probe("QC", "C", completed=False),
    ]
    t = incremental_retrieval_totals(probes)
    assert t.multihop_incremental_required_recovery == 0
    assert multihop_decision(True, 0).verdict is ScienceVerdict.NO
