"""PN02D-B3 — provider-free tests for content-safe P1 fact-recall observability.

OBSERVABILITY ONLY: these tests exercise the diagnostic capability without any
provider, retrieval, prompt, grader, or decision change. They cover the failure
layers R0 (retrieval-missing) / G0 (generation-missing) / OK, multi-fact fact-set
semantics, the latent strict-matcher false-negative signal (§22), all three arms,
and content-safety of the projection (§21 cases A-E).
"""

from __future__ import annotations

import json
from typing import cast

import pytest

from open_notebook.integrations.graphrag.eval.datasetpn02 import (
    QueryClassPN02,
    QueryPN02,
    load_fixture,
)
from open_notebook.integrations.graphrag.eval.metricspn02 import (
    fact_present,
    grade_answer,
)
from open_notebook.integrations.graphrag.eval.p1diagpn02db3 import (
    P1FactLayer,
    aggregate_p1_diagnostics,
    diagnose_query_arm,
    evidence_contains_fact,
    fact_source_provenance,
    project_p1_diagnostics,
)
from open_notebook.integrations.graphrag.eval.schemaspn02 import ArmId, QAAnswerResult


def _fx():
    return load_fixture()


def _first_positive(fx):
    return sorted(
        (q for q in fx.queries if q.answerable and q.expected_answer_facts),
        key=lambda q: q.query_id,
    )[0]


def _answer(query, arm, *, answer_text, abstained=False, citations=()):
    return QAAnswerResult(
        query_id=query.query_id,
        notebook_id=query.notebook_id,
        arm=arm,
        abstained=abstained,
        citation_source_ids=tuple(citations),
        answer_text=answer_text,
    )


def _grade(fx, query, result):
    return grade_answer(result, query, fx.members_of(query.notebook_id))


# --------------------------------------------------------------------------- #
# Provenance is deterministic and fixture-grounded (no mutation, no LLM)
# --------------------------------------------------------------------------- #


def test_fact_source_provenance_is_fixture_substring_truth():
    fx = _fx()
    q = _first_positive(fx)
    token = q.expected_answer_facts[0]
    prov = fact_source_provenance(fx, token)
    assert prov, "every expected fact must be sourced by fixture invariant"
    # provenance sources genuinely contain the token; and the query's required
    # members are within provenance (fixture load invariant, datasetpn02 §648).
    for sid in prov:
        assert token in fx.source_text(sid)
    assert set(q.required_source_ids) & set(prov)


# --------------------------------------------------------------------------- #
# CASE C — required fact in evidence AND recognized -> OK
# --------------------------------------------------------------------------- #


def test_case_c_ok_layer():
    fx = _fx()
    q = _first_positive(fx)
    result = _answer(q, ArmId.QA_V, answer_text=" ".join(q.expected_answer_facts))
    grade = _grade(fx, q, result)
    diag = diagnose_query_arm(
        fx=fx, query=q, arm=ArmId.QA_V,
        evidence_source_ids=q.required_source_ids, grade=grade, result=result,
    )
    assert set(diag.grader_recognized_fact_ids) == set(q.expected_answer_facts)
    assert set(diag.evidence_contained_fact_ids) == set(q.expected_answer_facts)
    assert diag.missing_fact_ids == ()
    assert all(layer == P1FactLayer.OK.value for _f, layer in diag.per_fact_layer)
    # answer_contained shares the grader matcher.
    assert diag.answer_contained_fact_ids == diag.grader_recognized_fact_ids


# --------------------------------------------------------------------------- #
# CASE B — required fact in evidence but absent from answer -> G0
# --------------------------------------------------------------------------- #


def test_case_b_generation_missing_layer():
    fx = _fx()
    q = _first_positive(fx)
    result = _answer(q, ArmId.QA_V, answer_text="", abstained=True)
    grade = _grade(fx, q, result)
    diag = diagnose_query_arm(
        fx=fx, query=q, arm=ArmId.QA_V,
        evidence_source_ids=q.required_source_ids, grade=grade, result=result,
    )
    assert diag.grader_recognized_fact_ids == ()
    assert set(diag.evidence_contained_fact_ids) == set(q.expected_answer_facts)
    assert set(diag.missing_fact_ids) == set(q.expected_answer_facts)
    assert all(
        layer == P1FactLayer.GENERATION_MISSING.value for _f, layer in diag.per_fact_layer
    )


# --------------------------------------------------------------------------- #
# CASE A — required fact absent from retrieved evidence -> R0
# --------------------------------------------------------------------------- #


def test_case_a_retrieval_missing_layer():
    fx = _fx()
    q = _first_positive(fx)
    result = _answer(q, ArmId.QA_V, answer_text="", abstained=True)
    grade = _grade(fx, q, result)
    diag = diagnose_query_arm(
        fx=fx, query=q, arm=ArmId.QA_V,
        evidence_source_ids=(), grade=grade, result=result,
    )
    assert diag.evidence_contained_fact_ids == ()
    assert diag.grader_recognized_fact_ids == ()
    assert all(
        layer == P1FactLayer.RETRIEVAL_MISSING.value for _f, layer in diag.per_fact_layer
    )


# --------------------------------------------------------------------------- #
# CASE D — multi-fact query: fact-set diagnostics split OK vs R0
# --------------------------------------------------------------------------- #


def _two_disjoint_provenance_tokens(fx):
    """Pick two real fact tokens (t_a, t_b) and a Source S_a that contains t_a but
    NOT t_b, using only frozen-fixture provenance. Deterministic over the fixture."""
    facts = []
    for q in sorted((q for q in fx.queries if q.answerable), key=lambda q: q.query_id):
        for t in q.expected_answer_facts:
            facts.append(t)
    for t_a in facts:
        prov_a = fact_source_provenance(fx, t_a)
        for s_a in prov_a:
            for t_b in facts:
                if t_b == t_a:
                    continue
                if t_b not in fx.source_text(s_a):
                    return t_a, t_b, s_a
    raise AssertionError("fixture has no separable fact pair")


def test_case_d_multifact_fact_set_split():
    fx = _fx()
    t_a, t_b, s_a = _two_disjoint_provenance_tokens(fx)
    q = QueryPN02(
        query_id="SYN_MULTI", notebook_id="NB_A",
        query_class=QueryClassPN02.DIRECT_LOCAL, question="q", answerable=True,
        required_source_ids=(s_a,), optional_support_source_ids=(),
        forbidden_source_ids=(), expected_answer_facts=(t_a, t_b),
        forbidden_answer_facts=(), expected_abstention=False,
        required_citation_source_ids=(), multi_hop_required=False, rationale="",
    )
    result = _answer(q, ArmId.QA_VGD, answer_text=t_a)  # answer contains t_a only
    grade = _grade(fx, q, result)
    diag = diagnose_query_arm(
        fx=fx, query=q, arm=ArmId.QA_VGD,
        evidence_source_ids=(s_a,), grade=grade, result=result,
    )
    layer_by_fact = dict(diag.per_fact_layer)
    assert layer_by_fact[t_a] == P1FactLayer.OK.value
    assert layer_by_fact[t_b] == P1FactLayer.RETRIEVAL_MISSING.value
    assert t_a in diag.evidence_contained_fact_ids
    assert t_b not in diag.evidence_contained_fact_ids
    assert diag.grader_recognized_fact_ids == (t_a,)
    assert diag.missing_fact_ids == (t_b,)
    # fact-set based: both required facts are retained, not collapsed to a boolean.
    assert set(diag.required_fact_ids) == {t_a, t_b}


# --------------------------------------------------------------------------- #
# §22 — latent strict-matcher false-negative surfaced WITHOUT changing the grader
# --------------------------------------------------------------------------- #


def test_grading_mismatch_candidate_without_changing_p1():
    fx = _fx()
    q = QueryPN02(
        query_id="SYN_CASE", notebook_id="NB_A",
        query_class=QueryClassPN02.DIRECT_LOCAL, question="q", answerable=True,
        required_source_ids=(), optional_support_source_ids=(),
        forbidden_source_ids=(), expected_answer_facts=("Alpha",),
        forbidden_answer_facts=(), expected_abstention=False,
        required_citation_source_ids=(), multi_hop_required=False, rationale="",
    )
    result = _answer(q, ArmId.QA_V, answer_text="the alpha value")  # case differs
    # The P1 matcher is UNCHANGED and still misses the case-varied token.
    assert fact_present(result, "Alpha") is False
    grade = _grade(fx, q, result)
    assert grade.recognized_expected_facts == frozenset()
    diag = diagnose_query_arm(
        fx=fx, query=q, arm=ArmId.QA_V,
        evidence_source_ids=(), grade=grade, result=result,
    )
    # Observability-only signal surfaces the latent false negative.
    assert diag.grading_mismatch_candidate_fact_ids == ("Alpha",)
    assert "Alpha" in diag.missing_fact_ids  # grader still (correctly) reports it missing


# --------------------------------------------------------------------------- #
# All three arms are covered
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("arm", [ArmId.QA_V, ArmId.QA_GD, ArmId.QA_VGD])
def test_all_arms_diagnosable(arm):
    fx = _fx()
    q = _first_positive(fx)
    result = _answer(q, arm, answer_text=" ".join(q.expected_answer_facts))
    grade = _grade(fx, q, result)
    diag = diagnose_query_arm(
        fx=fx, query=q, arm=arm,
        evidence_source_ids=q.required_source_ids, grade=grade, result=result,
    )
    assert diag.arm == arm
    assert diag.query_id == q.query_id


# --------------------------------------------------------------------------- #
# Aggregates (§14)
# --------------------------------------------------------------------------- #


def test_aggregate_counts():
    fx = _fx()
    q = _first_positive(fx)
    ok = diagnose_query_arm(
        fx=fx, query=q, arm=ArmId.QA_V,
        evidence_source_ids=q.required_source_ids,
        grade=_grade(fx, q, _answer(q, ArmId.QA_V, answer_text=" ".join(q.expected_answer_facts))),
        result=_answer(q, ArmId.QA_V, answer_text=" ".join(q.expected_answer_facts)),
    )
    r0 = diagnose_query_arm(
        fx=fx, query=q, arm=ArmId.QA_GD, evidence_source_ids=(),
        grade=_grade(fx, q, _answer(q, ArmId.QA_GD, answer_text="", abstained=True)),
        result=_answer(q, ArmId.QA_GD, answer_text="", abstained=True),
    )
    agg = aggregate_p1_diagnostics([ok, r0])
    n = len(q.expected_answer_facts)
    assert agg.positive_query_arm_count == 2
    assert agg.required_fact_total == 2 * n
    assert agg.ok_count == n
    assert agg.retrieval_missing_count == n
    assert agg.required_fact_in_evidence_count == n  # only the OK arm had evidence
    assert agg.grader_recognized_count == n


# --------------------------------------------------------------------------- #
# CASE E — content-safe projection: only ids/tokens/booleans/counts
# --------------------------------------------------------------------------- #


def test_projection_is_content_safe():
    fx = _fx()
    q = _first_positive(fx)
    result = _answer(q, ArmId.QA_V, answer_text="SECRET-RAW-ANSWER-TEXT should never appear")
    grade = _grade(fx, q, result)
    diag = diagnose_query_arm(
        fx=fx, query=q, arm=ArmId.QA_V,
        evidence_source_ids=q.required_source_ids, grade=grade, result=result,
    )
    projected = project_p1_diagnostics([diag])
    blob = json.dumps(projected)
    assert "SECRET-RAW-ANSWER-TEXT" not in blob
    assert "answer_text" not in blob
    assert projected["b3_mode"] == "OBSERVABILITY_ONLY"
    assert projected["answer_contained_shares_grader_matcher"] is True
    entry = cast("list[dict[str, object]]", projected["per_query_arm"])[0]
    allowed = {
        "query_id", "notebook_id", "arm", "required_fact_ids", "evidence_source_ids",
        "evidence_contained_fact_ids", "grader_recognized_fact_ids",
        "answer_contained_fact_ids", "missing_fact_ids",
        "grading_mismatch_candidate_fact_ids", "per_fact_layer",
    }
    assert set(entry.keys()) == allowed
    assert "aggregate" in projected


def test_evidence_contains_fact_ignores_unknown_sources():
    fx = _fx()
    q = _first_positive(fx)
    token = q.expected_answer_facts[0]
    assert evidence_contains_fact(fx, q.required_source_ids, token) is True
    assert evidence_contains_fact(fx, ("NOT_A_REAL_SOURCE_KEY",), token) is False
