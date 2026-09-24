"""PN02D-B3U — provider-free tests for content-safe end-to-end QA-VALUE observability.

OBSERVABILITY ONLY. No provider, retrieval, prompt, grader, or decision change. Covers
GRADE-ONCE / PROJECT-TWICE, the six frozen QA-value dimensions (P1/P2/P3/S1/S2/S3),
content-safety (no raw answer/source/prompt), fail-closed on incomplete projection,
qa_arm_metrics + qa_decision parity, and that the existing B3 fact-recall diagnostics are
unchanged (the QA-value block is additive + backward compatible).
"""

from __future__ import annotations

import dataclasses
import json
from typing import Any, Dict, List, Tuple, cast

import pytest

from open_notebook.integrations.graphrag.eval import p1diagrunnerpn02db3 as runner
from open_notebook.integrations.graphrag.eval.datasetpn02 import (
    FixturePN02,
    QueryPN02,
    load_fixture,
)
from open_notebook.integrations.graphrag.eval.decisionspn02 import qa_decision
from open_notebook.integrations.graphrag.eval.metricspn02 import (
    AnswerGrade,
    grade_answer,
    qa_arm_metrics,
)
from open_notebook.integrations.graphrag.eval.qastagepn02db2 import B2QAExecutionRecord
from open_notebook.integrations.graphrag.eval.qavaluepn02db3 import (
    QA_VALUE_OBSERVABILITY_VERSION,
    QAValueObservabilityError,
    assert_observation_complete,
    build_qa_value_projection,
    project_qa_value_observation,
)
from open_notebook.integrations.graphrag.eval.schemaspn02 import ArmId, QAAnswerResult

_ARMS: Tuple[ArmId, ...] = (ArmId.QA_V, ArmId.QA_GD, ArmId.QA_VGD)


def _fx() -> FixturePN02:
    return load_fixture()


def _answer(
    query: QueryPN02,
    arm: ArmId,
    *,
    answer_text: str | None,
    abstained: bool = False,
    citations: Tuple[str, ...] = (),
) -> QAAnswerResult:
    return QAAnswerResult(
        query_id=query.query_id,
        notebook_id=query.notebook_id,
        arm=arm,
        abstained=abstained,
        citation_source_ids=tuple(citations),
        answer_text=answer_text,
    )


def _grade(fx: FixturePN02, query: QueryPN02, result: QAAnswerResult) -> AnswerGrade:
    return grade_answer(result, query, fx.members_of(query.notebook_id))


def _good_result(fx: FixturePN02, query: QueryPN02, arm: ArmId) -> QAAnswerResult:
    """A perfectly-answered positive / correctly-abstaining negative (all safety = 0)."""
    if query.is_negative:
        return _answer(query, arm, answer_text=None, abstained=True)
    return _answer(
        query,
        arm,
        answer_text=" ".join(query.expected_answer_facts),
        citations=query.required_citation_source_ids,
    )


def _all_pairs(fx: FixturePN02) -> List[Tuple[QueryPN02, AnswerGrade]]:
    pairs: List[Tuple[QueryPN02, AnswerGrade]] = []
    for q in sorted(fx.queries, key=lambda x: x.query_id):
        for arm in _ARMS:
            pairs.append((q, _grade(fx, q, _good_result(fx, q, arm))))
    return pairs


def _all_records(fx: FixturePN02) -> List[B2QAExecutionRecord]:
    recs: List[B2QAExecutionRecord] = []
    for q in sorted(fx.queries, key=lambda x: x.query_id):
        members = fx.members_of(q.notebook_id)
        ev = tuple(s for s in q.required_source_ids if s in members)
        for arm in _ARMS:
            recs.append(
                B2QAExecutionRecord(
                    query_id=q.query_id,
                    notebook_id=q.notebook_id,
                    arm=arm,
                    evidence_source_ids=ev,
                    result=_good_result(fx, q, arm),
                )
            )
    return recs


def _first_positive(fx: FixturePN02) -> QueryPN02:
    return sorted(
        (q for q in fx.queries if q.answerable and q.expected_answer_facts),
        key=lambda q: q.query_id,
    )[0]


def _first_negative(fx: FixturePN02) -> QueryPN02:
    return sorted((q for q in fx.queries if q.is_negative), key=lambda q: q.query_id)[0]


# --------------------------------------------------------------------------- #
# §9-§15 per-record projection from the canonical grade
# --------------------------------------------------------------------------- #


def test_record_identity_and_p1_projection():
    fx = _fx()
    q = _first_positive(fx)
    grade = _grade(fx, q, _good_result(fx, q, ArmId.QA_GD))
    obs = project_qa_value_observation(q, grade)
    assert (obs.query_id, obs.notebook_id, obs.arm, obs.is_negative) == (
        q.query_id,
        q.notebook_id,
        "QA-GD",
        False,
    )
    assert obs.required_fact_count == len(q.expected_answer_facts)
    assert obs.recognized_fact_count == len(q.expected_answer_facts)  # perfectly answered
    assert obs.missing_fact_count == 0


def test_p1_partial_and_missing():
    fx = _fx()
    q = _first_positive(fx)
    if len(q.expected_answer_facts) >= 2:
        partial = _answer(q, ArmId.QA_V, answer_text=q.expected_answer_facts[0])
        obs = project_qa_value_observation(q, _grade(fx, q, partial))
        assert obs.recognized_fact_count == 1
        assert obs.missing_fact_count == len(q.expected_answer_facts) - 1
    none = _answer(q, ArmId.QA_V, answer_text="nothing here")
    obs2 = project_qa_value_observation(q, _grade(fx, q, none))
    assert obs2.recognized_fact_count == 0
    assert obs2.missing_fact_count == len(q.expected_answer_facts)


def test_p2_citation_coverage_projection():
    fx = _fx()
    q = next(
        (x for x in fx.queries if x.answerable and x.required_citation_source_ids), None
    )
    assert q is not None
    covered = _answer(
        q, ArmId.QA_V, answer_text=" ".join(q.expected_answer_facts),
        citations=q.required_citation_source_ids,
    )
    obs = project_qa_value_observation(q, _grade(fx, q, covered))
    assert obs.required_citation_count == len(q.required_citation_source_ids)
    assert obs.required_citations_covered_count == len(q.required_citation_source_ids)
    assert obs.required_citations_missing_count == 0
    # missing coverage
    obs2 = project_qa_value_observation(
        q, _grade(fx, q, _answer(q, ArmId.QA_V, answer_text="x", citations=()))
    )
    assert obs2.required_citations_covered_count == 0
    assert obs2.required_citations_missing_count == len(q.required_citation_source_ids)


def test_p3_negative_abstention_projection():
    fx = _fx()
    q = _first_negative(fx)
    abst = project_qa_value_observation(
        q, _grade(fx, q, _answer(q, ArmId.QA_V, answer_text=None, abstained=True))
    )
    assert abst.is_negative and abst.abstained
    noabst = project_qa_value_observation(
        q, _grade(fx, q, _answer(q, ArmId.QA_V, answer_text="claim", abstained=False))
    )
    assert noabst.is_negative and not noabst.abstained


def test_s2_valid_and_invalid_citation_projection():
    fx = _fx()
    q = _first_positive(fx)
    members = fx.members_of(q.notebook_id)
    valid_id = sorted(members)[0]
    nonmember = next(s for s in sorted(fx.source_keys) if s not in members)
    res = _answer(q, ArmId.QA_V, answer_text="x", citations=(valid_id, nonmember))
    obs = project_qa_value_observation(q, _grade(fx, q, res))
    assert valid_id in obs.valid_citation_source_ids and obs.valid_citation_count >= 1
    assert nonmember in obs.invalid_citation_source_ids and obs.invalid_citation_count == 1


def test_s1_s3_forbidden_fact_and_leak_projection():
    fx = _fx()
    q = next((x for x in fx.queries if x.forbidden_answer_facts), None)
    assert q is not None
    clean = project_qa_value_observation(
        q, _grade(fx, q, _answer(q, ArmId.QA_GD, answer_text="safe text"))
    )
    assert not clean.cross_notebook_answer_leak
    assert clean.forbidden_facts_present_count == 0
    leaked = project_qa_value_observation(
        q,
        _grade(
            fx, q, _answer(q, ArmId.QA_GD, answer_text=q.forbidden_answer_facts[0])
        ),
    )
    assert leaked.cross_notebook_answer_leak  # S1
    assert leaked.forbidden_facts_present_count >= 1  # S3


# --------------------------------------------------------------------------- #
# §40/§41 content safety — no raw answer / source / prompt / secret
# --------------------------------------------------------------------------- #


def test_projection_never_serializes_raw_answer_sentinel():
    fx = _fx()
    sentinel = "SENTINEL_RAW_LEAK_ZZZ_DO_NOT_PERSIST"
    q = _first_positive(fx)
    text = sentinel + " " + " ".join(q.expected_answer_facts)
    grade = _grade(fx, q, _answer(q, ArmId.QA_V, answer_text=text, citations=()))
    # single record projection
    obs = project_qa_value_observation(q, grade)
    blob = json.dumps(dataclasses.asdict(obs))
    assert sentinel not in blob
    assert "answer_text" not in blob
    # full projection block
    pairs = _all_pairs(fx)
    pairs[0] = (q, grade)
    proj = build_qa_value_projection(pairs, isolation_evidenced=True)
    proj_blob = json.dumps(proj)
    assert sentinel not in proj_blob
    for banned in ("answer_text", "prompt", "materialized", "source_text", "api_key"):
        assert banned not in proj_blob


# --------------------------------------------------------------------------- #
# §48 completeness / §49-§50 fail-closed
# --------------------------------------------------------------------------- #


def test_complete_72_record_projection_marker_true():
    fx = _fx()
    proj = build_qa_value_projection(_all_pairs(fx), isolation_evidenced=True)
    assert proj["observed_pair_count"] == 72
    assert proj["observed_positive_count"] == 63
    assert proj["observed_negative_count"] == 9
    assert proj["qa_value_observability_complete"] is True
    assert proj["version"] == QA_VALUE_OBSERVABILITY_VERSION
    assert len(cast(list, proj["per_query_arm"])) == 72
    assert len(cast(list, proj["arm_metrics"])) == 3


def test_missing_record_fails_closed():
    fx = _fx()
    pairs = _all_pairs(fx)[:-1]  # drop one
    with pytest.raises(QAValueObservabilityError):
        build_qa_value_projection(pairs, isolation_evidenced=True)


def test_missing_required_dimension_fails_closed():
    fx = _fx()
    q = _first_positive(fx)
    obs = project_qa_value_observation(q, _grade(fx, q, _good_result(fx, q, ArmId.QA_V)))
    tampered = dataclasses.replace(obs, required_fact_count=cast(Any, None))
    with pytest.raises(QAValueObservabilityError):
        assert_observation_complete(tampered)


def test_missing_arm_fails_closed():
    fx = _fx()
    # keep only QA-V arm records
    pairs = [(q, g) for (q, g) in _all_pairs(fx) if g.arm is ArmId.QA_V]
    with pytest.raises(QAValueObservabilityError):
        build_qa_value_projection(
            pairs, isolation_evidenced=True, expected_pair_count=len(pairs),
            expected_positive=sum(1 for q, _ in pairs if not q.is_negative),
            expected_negative=sum(1 for q, _ in pairs if q.is_negative),
        )


# --------------------------------------------------------------------------- #
# §51 aggregate parity / §52 decision parity — reuse frozen logic
# --------------------------------------------------------------------------- #


def test_qa_arm_metrics_parity():
    fx = _fx()
    pairs = _all_pairs(fx)
    proj = build_qa_value_projection(pairs, isolation_evidenced=True)
    by_arm: Dict[ArmId, List[AnswerGrade]] = {}
    for _q, g in pairs:
        by_arm.setdefault(g.arm, []).append(g)
    for block in cast(List[Dict[str, Any]], proj["arm_metrics"]):
        arm = ArmId(block["arm"])
        canonical = qa_arm_metrics(by_arm[arm])
        assert block["answer_required_fact_recall"] == canonical.answer_required_fact_recall
        assert (
            block["citation_required_source_coverage"]
            == canonical.citation_required_source_coverage
        )
        assert (
            block["negative_answer_abstention_rate"]
            == canonical.negative_answer_abstention_rate
        )
        assert block["citation_invalid_count"] == canonical.citation_invalid_count


def test_qa_decision_parity():
    fx = _fx()
    pairs = _all_pairs(fx)
    proj = build_qa_value_projection(pairs, isolation_evidenced=True)
    by_arm: Dict[ArmId, List[AnswerGrade]] = {}
    for _q, g in pairs:
        by_arm.setdefault(g.arm, []).append(g)
    baseline = qa_arm_metrics(by_arm[ArmId.QA_V])
    graph = [qa_arm_metrics(by_arm[ArmId.QA_GD]), qa_arm_metrics(by_arm[ArmId.QA_VGD])]
    canonical = qa_decision(True, baseline, graph)
    got = cast(Dict[str, Any], proj["qa_decision"])
    assert got["verdict"] == canonical.verdict.value
    assert got["rule"] == canonical.rule
    assert tuple(got["positive_arms"]) == tuple(canonical.positive_arms)


def test_isolation_false_yields_safety_gate_fail():
    fx = _fx()
    proj = build_qa_value_projection(_all_pairs(fx), isolation_evidenced=False)
    assert proj["qa_value_status"] == "QA_VALUE_SAFETY_GATE_FAIL"
    assert cast(Dict[str, Any], proj["qa_decision"])["rule"] == "Q0"


# --------------------------------------------------------------------------- #
# §53 single-grading invariant / §54 existing diagnostics unchanged
# --------------------------------------------------------------------------- #


def test_single_grading_invariant_72_calls(monkeypatch):
    fx = _fx()
    records = _all_records(fx)
    calls = {"n": 0}
    real = grade_answer

    def _counting(result, query, members):
        calls["n"] += 1
        return real(result, query, members)

    monkeypatch.setattr(runner, "grade_answer", _counting)
    artifact = runner.build_b3_observability_artifact(
        fx, records, observation_run_id="pn02db3u-test-run", isolation_evidenced=True
    )
    assert calls["n"] == 72  # graded ONCE per (query, arm), not twice for two projections
    assert "qa_value_observability" in artifact
    qa_block = cast(Dict[str, Any], artifact["qa_value_observability"])
    assert qa_block["qa_value_observability_complete"] is True


def test_backward_compatible_when_isolation_absent():
    fx = _fx()
    records = _all_records(fx)
    # No isolation_evidenced -> pre-B3U shape, no qa_value_observability key.
    art_plain = runner.build_b3_observability_artifact(
        fx, records, observation_run_id="pn02db3u-plain"
    )
    assert "qa_value_observability" not in art_plain
    # diagnostics identical to the standalone build_b3_diagnostics projection.
    from open_notebook.integrations.graphrag.eval.p1diagpn02db3 import (
        aggregate_p1_diagnostics,
        project_p1_diagnostics,
    )

    diags = runner.build_b3_diagnostics(fx, records)
    expected = project_p1_diagnostics(diags, aggregate_p1_diagnostics(diags))
    assert art_plain["diagnostics"] == expected


def test_s3_aggregate_serialized_under_fact_key_not_citation_key():
    # PN02DB3U-IR1-L2: the S3 forbidden/hallucinated-FACT aggregate must serialize under a
    # fact-named key, NEVER "forbidden_citation_count" (which conflates facts with citations).
    fx = _fx()
    pairs = _all_pairs(fx)
    # inject a forbidden-fact-present answer into one graph-arm positive pair so the S3 count > 0
    q = next((x for x in fx.queries if x.answerable and x.forbidden_answer_facts), None)
    assert q is not None
    leaked = _grade(fx, q, _answer(q, ArmId.QA_GD, answer_text=q.forbidden_answer_facts[0]))
    for i, (qq, _g) in enumerate(pairs):
        if qq.query_id == q.query_id and _g.arm is ArmId.QA_GD:
            pairs[i] = (q, leaked)
            break
    proj = build_qa_value_projection(pairs, isolation_evidenced=True)
    # correct fact-named key present; misleading citation-named key absent
    assert "forbidden_fact_count" in proj
    assert "forbidden_citation_count" not in proj
    # value parity: aggregate == sum of per-record forbidden_facts_present_count (from canonical grades)
    expected = sum(o["forbidden_facts_present_count"] for o in cast(list, proj["per_query_arm"]))
    assert proj["forbidden_fact_count"] == expected >= 1
    # citation dimensions remain citation-specific and are not renamed
    assert "invalid_citation_count" in proj and "cross_notebook_citation_count" in proj


def test_qa_value_additive_does_not_change_diagnostics():
    fx = _fx()
    records = _all_records(fx)
    plain = runner.build_b3_observability_artifact(
        fx, records, observation_run_id="pn02db3u-a"
    )
    withqa = runner.build_b3_observability_artifact(
        fx, records, observation_run_id="pn02db3u-a", isolation_evidenced=True
    )
    assert withqa["diagnostics"] == plain["diagnostics"]
    assert withqa["completed_diagnostic_pair_count"] == plain["completed_diagnostic_pair_count"]
