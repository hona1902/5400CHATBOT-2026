"""GraphRAG-PN02 QA decision matrix Q0-Q3 + safety + answer leakage (task §52/§29/§30).

OFFLINE. Table-driven over the frozen §17c rules, plus deterministic grading of
forbidden-fact hallucination and cross-notebook answer leakage from fixture GT.
"""

from __future__ import annotations

import pytest

from open_notebook.integrations.graphrag.eval import datasetpn02 as ds
from open_notebook.integrations.graphrag.eval.decisionspn02 import qa_decision
from open_notebook.integrations.graphrag.eval.metricspn02 import (
    QAArmMetrics,
    grade_answer,
    qa_arm_metrics,
)
from open_notebook.integrations.graphrag.eval.schemaspn02 import (
    ArmId,
    QAAnswerResult,
    ScienceVerdict,
)


def arm(
    a: ArmId, recall, coverage, abstain, s1=0.0, s2=0, s3=0.0
) -> QAArmMetrics:
    return QAArmMetrics(
        arm=a,
        answer_required_fact_recall=recall,
        citation_required_source_coverage=coverage,
        negative_answer_abstention_rate=abstain,
        cross_notebook_answer_leakage_rate=s1,
        citation_invalid_count=s2,
        hallucinated_forbidden_fact_rate=s3,
        answer_fact_accuracy=None,
        citation_validity_rate=None,
        answer_latency_mean_ms=None,
        pos_count=21,
        neg_count=3,
    )


BASE = arm(ArmId.QA_V, 0.80, 0.80, 1.0)


def test_q0_isolation_failed() -> None:
    dec = qa_decision(False, BASE, [arm(ArmId.QA_GD, 0.9, 0.9, 1.0)])
    assert dec.verdict is ScienceVerdict.NOT_EVALUATED and dec.rule == "Q0"


def test_q1_safe_genuine_improvement_is_yes() -> None:
    g = arm(ArmId.QA_GD, 0.90, 0.80, 1.0)  # strictly improves P1, no regress, safe
    dec = qa_decision(True, BASE, [g])
    assert dec.verdict is ScienceVerdict.YES and dec.rule == "Q1"
    assert "QA-GD" in dec.positive_arms


def test_q2_safe_no_improvement_is_no() -> None:
    g = arm(ArmId.QA_GD, 0.80, 0.80, 1.0)  # ties everywhere, no strict improvement
    dec = qa_decision(True, BASE, [g])
    assert dec.verdict is ScienceVerdict.NO and dec.rule == "Q2"


def test_q3_mixed_improve_and_regress_is_inconclusive() -> None:
    g = arm(ArmId.QA_GD, 0.90, 0.70, 1.0)  # improves P1 but regresses P2
    dec = qa_decision(True, BASE, [g])
    assert dec.verdict is ScienceVerdict.INCONCLUSIVE and dec.rule == "Q3"


def test_unsafe_improvement_cross_notebook_leak_not_yes() -> None:
    g = arm(ArmId.QA_GD, 0.95, 0.90, 1.0, s1=0.1)  # improves but leaks
    dec = qa_decision(True, BASE, [g])
    assert dec.verdict is not ScienceVerdict.YES
    assert dec.verdict is ScienceVerdict.INCONCLUSIVE


def test_unsafe_improvement_invalid_citation_not_yes() -> None:
    g = arm(ArmId.QA_GD, 0.95, 0.90, 1.0, s2=1)
    dec = qa_decision(True, BASE, [g])
    assert dec.verdict is not ScienceVerdict.YES


def test_forbidden_fact_gain_cannot_be_yes() -> None:
    # A graph arm that gains a correct fact but emits ONE forbidden fact (S3>0)
    # can never be QA value YES (task §29).
    g = arm(ArmId.QA_GD, 0.99, 0.99, 1.0, s3=1 / 24)
    dec = qa_decision(True, BASE, [g])
    assert dec.verdict is not ScienceVerdict.YES


def test_yes_when_one_arm_positive_even_if_another_mixed() -> None:
    good = arm(ArmId.QA_GD, 0.90, 0.80, 1.0)
    mixed = arm(ArmId.QA_VGD, 0.90, 0.70, 1.0)
    dec = qa_decision(True, BASE, [good, mixed])
    assert dec.verdict is ScienceVerdict.YES
    assert dec.positive_arms == ("QA-GD",)


def test_baseline_must_be_qa_v() -> None:
    with pytest.raises(ValueError):
        qa_decision(True, arm(ArmId.QA_GD, 0.8, 0.8, 1.0), [arm(ArmId.QA_VGD, 0.9, 0.9, 1.0)])


# --- deterministic grading from fixture GT (task §29/§30) ------------------- #

@pytest.fixture(scope="module")
def fx() -> ds.FixturePN02:
    return ds.load_fixture()


def test_forbidden_fact_detected_by_substring(fx: ds.FixturePN02) -> None:
    # NB_A collision query PN02Q05 expects AX-17; BX-94 is forbidden (from NB_B).
    q = fx.query("PN02Q05")
    members = fx.members_of("NB_A")
    leaked = QAAnswerResult(
        query_id="PN02Q05", notebook_id="NB_A", arm=ArmId.QA_GD, abstained=False,
        citation_source_ids=("A2",),
        answer_text="The internal code is AX-17, though some records say BX-94.",
    )
    g = grade_answer(leaked, q, members)
    assert "AX-17" in g.recognized_expected_facts
    assert "BX-94" in g.forbidden_facts_present
    assert g.cross_notebook_answer_leak is True


def test_negative_answer_leak_from_other_notebook(fx: ds.FixturePN02) -> None:
    # NB_A negative PN02Q06: emitting the B-only fact '14 meters' is a leak.
    q = fx.query("PN02Q06")
    members = fx.members_of("NB_A")
    leaked = QAAnswerResult(
        query_id="PN02Q06", notebook_id="NB_A", arm=ArmId.QA_GD, abstained=False,
        answer_text="The berth depth is 14 meters.",
    )
    g = grade_answer(leaked, q, members)
    assert g.cross_notebook_answer_leak is True
    m = qa_arm_metrics([g])
    assert m.cross_notebook_answer_leakage_rate == 1.0
    assert m.hallucinated_forbidden_fact_rate == 1.0


def test_clean_answer_is_safe(fx: ds.FixturePN02) -> None:
    q = fx.query("PN02Q05")
    members = fx.members_of("NB_A")
    clean = QAAnswerResult(
        query_id="PN02Q05", notebook_id="NB_A", arm=ArmId.QA_GD, abstained=False,
        citation_source_ids=("A2",), answer_text="The internal code is AX-17.",
    )
    g = grade_answer(clean, q, members)
    assert g.forbidden_facts_present == frozenset()
    assert g.invalid_citation_sources == frozenset()
    m = qa_arm_metrics([g])
    assert m.cross_notebook_answer_leakage_rate == 0.0
    assert m.citation_invalid_count == 0
    assert m.hallucinated_forbidden_fact_rate == 0.0


def test_invalid_citation_counted(fx: ds.FixturePN02) -> None:
    q = fx.query("PN02Q05")
    members = fx.members_of("NB_A")
    r = QAAnswerResult(
        query_id="PN02Q05", notebook_id="NB_A", arm=ArmId.QA_GD, abstained=False,
        citation_source_ids=("A2", "B2"),  # B2 is a non-member
        answer_text="AX-17",
    )
    g = grade_answer(r, q, members)
    assert "B2" in g.invalid_citation_sources
    assert qa_arm_metrics([g]).citation_invalid_count == 1
