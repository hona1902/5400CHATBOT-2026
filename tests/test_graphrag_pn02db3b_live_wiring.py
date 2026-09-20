"""PN02D-B3B — provider-free tests for the live fact-recall observability WIRING.

Proves: (a) the optional ``B2QAStage.execution_observer`` hook is a strict no-op by
default (byte-identical B2), (b) when installed it captures the ACTUAL member-filtered
evidence + result exactly once per (query, arm) across all three arms, and (c) the B3
adapter transforms captured records through the CHECKPOINTED diagnostic into R0/G0/OK
(+ mismatch candidate), fails closed, reports completeness honestly, and is content-safe.

ZERO provider traffic: the answer seam and QA stage are driven with injected fakes.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from open_notebook.integrations.graphrag.eval.datasetpn02 import (
    NOTEBOOK_IDS,
    load_fixture,
)
from open_notebook.integrations.graphrag.eval.normalizepn02 import (
    normalize_graph,
    normalize_vector,
)
from open_notebook.integrations.graphrag.eval.p1diagpn02db3 import P1FactLayer
from open_notebook.integrations.graphrag.eval.p1diagrunnerpn02db3 import (
    REFERENCE_B2_RUN_ID,
    B3ObservabilityCollector,
    B3ObservabilityError,
    build_b3_observability_artifact,
    observed_arm_coverage,
)
from open_notebook.integrations.graphrag.eval.qastagepn02db2 import (
    ARM_ORDER,
    B2QAExecutionRecord,
    B2QAStage,
)
from open_notebook.integrations.graphrag.eval.schemaspn02 import (
    ArmId,
    GDEvidenceResult,
    QAAnswerResult,
    VectorEvidenceResult,
)

OBS_RUN_ID = "pn02db3-obs-0000000000000000000000000000"


def _fx():
    return load_fixture()


def _first_positive(fx):
    return sorted(
        (q for q in fx.queries if q.answerable and q.expected_answer_facts),
        key=lambda q: q.query_id,
    )[0]


class _FakeSeam:
    """Provider-free final-answer seam returning a fixed answer_text (no provider)."""

    def __init__(self, answer_text: str = "placeholder answer"):
        self.answer_text = answer_text
        self.calls = 0

    async def answer(self, notebook_id, question, evidence_source_ids):
        self.calls += 1
        return QAAnswerResult(
            query_id="", notebook_id=notebook_id, arm=ArmId.QA_V,
            abstained=False, answer_text=self.answer_text, citation_source_ids=(),
        )


def _one_query_evidence(fx):
    nb = NOTEBOOK_IDS[0]
    members = sorted(fx.members_of(nb))
    assert len(members) >= 2
    m0, m1 = members[0], members[1]
    q = fx.queries_for_notebook(nb)[0]
    vector_results = {
        q.query_id: VectorEvidenceResult(
            query_id=q.query_id, notebook_id=nb,
            evidence=normalize_vector([m0, m1], allowlist=fx.source_keys),
        )
    }
    gd_results = {
        q.query_id: GDEvidenceResult(
            query_id=q.query_id, notebook_id=nb,
            evidence=normalize_graph([m1], allowlist=fx.source_keys),
        )
    }
    return q, vector_results, gd_results


def _rec(fx, query, arm, *, evidence, answer_text, abstained=False):
    return B2QAExecutionRecord(
        query_id=query.query_id, notebook_id=query.notebook_id, arm=arm,
        evidence_source_ids=tuple(evidence),
        result=QAAnswerResult(
            query_id=query.query_id, notebook_id=query.notebook_id, arm=arm,
            abstained=abstained, answer_text=answer_text, citation_source_ids=(),
        ),
    )


# --------------------------------------------------------------------------- #
# Default no-op — normal B2 path byte-identical
# --------------------------------------------------------------------------- #


def test_stage_hook_default_none_is_byte_identical():
    fx = _fx()
    q, vr, gd = _one_query_evidence(fx)
    baseline = asyncio.run(
        B2QAStage(answer_seam=_FakeSeam())(
            fx=fx, ordered_queries=[q], vector_results=vr, gd_results=gd
        )
    )
    # observer defaults to None -> no capture; results identical in shape/content
    stage = B2QAStage(answer_seam=_FakeSeam())
    assert stage.execution_observer is None
    observed = asyncio.run(
        stage(fx=fx, ordered_queries=[q], vector_results=vr, gd_results=gd)
    )
    assert observed == baseline
    assert stage.final_answer_completed_answers == len(ARM_ORDER)


# --------------------------------------------------------------------------- #
# Capture: actual evidence, exactly-once, three-arm coverage
# --------------------------------------------------------------------------- #


def test_collector_captures_actual_evidence_exactly_once_three_arms():
    fx = _fx()
    q, vr, gd = _one_query_evidence(fx)
    collector = B3ObservabilityCollector()
    stage = B2QAStage(answer_seam=_FakeSeam(), execution_observer=collector)
    asyncio.run(stage(fx=fx, ordered_queries=[q], vector_results=vr, gd_results=gd))

    # exactly one record per (query, arm); 3 arms
    assert len(collector.records) == len(ARM_ORDER)
    assert observed_arm_coverage(collector.records) == (ArmId.QA_V, ArmId.QA_GD, ArmId.QA_VGD)
    # evidence captured equals the stage's own plan (ACTUAL arm evidence)
    plan = {i.arm: i.evidence_source_ids for i in stage.plan(
        fx=fx, ordered_queries=[q], vector_results=vr, gd_results=gd)}
    for rec in collector.records:
        assert rec.evidence_source_ids == plan[rec.arm]
        assert rec.query_id == q.query_id and rec.notebook_id == q.notebook_id


def test_collector_rejects_duplicate_query_arm():
    fx = _fx()
    q = _first_positive(fx)
    collector = B3ObservabilityCollector()
    r = _rec(fx, q, ArmId.QA_V, evidence=(), answer_text="")
    collector(r)
    with pytest.raises(B3ObservabilityError):
        collector(r)  # duplicate (query_id, arm)


# --------------------------------------------------------------------------- #
# Adapter diagnostics via the checkpointed module — R0 / G0 / OK
# --------------------------------------------------------------------------- #


def _artifact(fx, records):
    return build_b3_observability_artifact(
        fx, records, observation_run_id=OBS_RUN_ID
    )


def test_artifact_r0_when_evidence_lacks_fact():
    fx = _fx()
    q = _first_positive(fx)
    rec = _rec(fx, q, ArmId.QA_V, evidence=(), answer_text="", abstained=True)
    art = _artifact(fx, [rec])
    entry = art["diagnostics"]["per_query_arm"][0]
    assert all(layer == P1FactLayer.RETRIEVAL_MISSING.value for _f, layer in entry["per_fact_layer"])


def test_artifact_g0_when_evidence_has_fact_but_answer_omits():
    fx = _fx()
    q = _first_positive(fx)
    rec = _rec(fx, q, ArmId.QA_GD, evidence=q.required_source_ids, answer_text="", abstained=True)
    art = _artifact(fx, [rec])
    entry = art["diagnostics"]["per_query_arm"][0]
    assert set(entry["evidence_contained_fact_ids"]) == set(q.expected_answer_facts)
    assert all(layer == P1FactLayer.GENERATION_MISSING.value for _f, layer in entry["per_fact_layer"])


def test_artifact_ok_when_evidence_has_fact_and_answer_recognized():
    fx = _fx()
    q = _first_positive(fx)
    rec = _rec(fx, q, ArmId.QA_VGD, evidence=q.required_source_ids,
               answer_text=" ".join(q.expected_answer_facts))
    art = _artifact(fx, [rec])
    entry = art["diagnostics"]["per_query_arm"][0]
    assert all(layer == P1FactLayer.OK.value for _f, layer in entry["per_fact_layer"])


def test_artifact_mismatch_candidate_via_wiring():
    fx = _fx()
    q = _first_positive(fx)
    token = q.expected_answer_facts[0]
    # case-varied answer: strict grader misses; normalized observability candidate fires
    rec = _rec(fx, q, ArmId.QA_V, evidence=q.required_source_ids, answer_text=token.lower() + " x")
    art = _artifact(fx, [rec])
    entry = art["diagnostics"]["per_query_arm"][0]
    if token != token.lower():  # only meaningful if the token has case to vary
        assert token in entry["grading_mismatch_candidate_fact_ids"]
        assert token in entry["missing_fact_ids"]  # strict P1 unchanged


def test_multi_source_any_provenance_source_prevents_false_r0():
    fx = _fx()
    q = _first_positive(fx)
    token = q.expected_answer_facts[0]
    src = q.required_source_ids[0]  # a source that contains the fact
    rec = _rec(fx, q, ArmId.QA_V, evidence=(src,), answer_text="")
    art = _artifact(fx, [rec])
    entry = art["diagnostics"]["per_query_arm"][0]
    assert token in entry["evidence_contained_fact_ids"]  # NOT a false R0
    assert dict(  # type: ignore[arg-type]
        (f, layer) for f, layer in entry["per_fact_layer"]
    )[token] != P1FactLayer.RETRIEVAL_MISSING.value


# --------------------------------------------------------------------------- #
# Completeness, run-id distinctness, content-safety, fail-closed
# --------------------------------------------------------------------------- #


def test_partial_completeness_reported_honestly():
    fx = _fx()
    q = _first_positive(fx)
    art = _artifact(fx, [_rec(fx, q, ArmId.QA_V, evidence=(), answer_text="")])
    assert art["expected_pair_count"] == 72
    assert art["completed_diagnostic_pair_count"] == 1
    assert art["completeness"] == "PARTIAL"
    assert art["report_kind"] == "PN02DB3_FACT_RECALL_OBSERVABILITY"
    assert art["mode"] == "OBSERVABILITY_ONLY"
    assert art["reference_b2_run_id"] == REFERENCE_B2_RUN_ID
    assert art["observation_run_id"] != REFERENCE_B2_RUN_ID


def test_observation_run_id_must_not_reuse_b2_run_id():
    fx = _fx()
    q = _first_positive(fx)
    with pytest.raises(B3ObservabilityError):
        build_b3_observability_artifact(
            fx, [_rec(fx, q, ArmId.QA_V, evidence=(), answer_text="")],
            observation_run_id=REFERENCE_B2_RUN_ID,  # forbidden
        )


def test_artifact_is_content_safe():
    fx = _fx()
    q = _first_positive(fx)
    rec = _rec(fx, q, ArmId.QA_V, evidence=q.required_source_ids,
               answer_text="SECRET-RAW-ANSWER-TEXT must never be persisted")
    art = _artifact(fx, [rec])
    blob = json.dumps(art)
    assert "SECRET-RAW-ANSWER-TEXT" not in blob
    assert "answer_text" not in blob


def test_fail_closed_on_unknown_query():
    fx = _fx()
    q = _first_positive(fx)
    bad = B2QAExecutionRecord(
        query_id="NOT_A_REAL_QUERY", notebook_id=q.notebook_id, arm=ArmId.QA_V,
        evidence_source_ids=(), result=QAAnswerResult(
            query_id="NOT_A_REAL_QUERY", notebook_id=q.notebook_id, arm=ArmId.QA_V,
            abstained=True, answer_text="", citation_source_ids=()),
    )
    with pytest.raises(B3ObservabilityError):
        _artifact(fx, [bad])


def test_classification_accounting_consistent():
    fx = _fx()
    q = _first_positive(fx)
    recs = [
        _rec(fx, q, ArmId.QA_V, evidence=q.required_source_ids,
             answer_text=" ".join(q.expected_answer_facts)),  # OK
        _rec(fx, q, ArmId.QA_GD, evidence=q.required_source_ids, answer_text=""),  # G0
        _rec(fx, q, ArmId.QA_VGD, evidence=(), answer_text=""),  # R0
    ]
    agg = _artifact(fx, recs)["diagnostics"]["aggregate"]
    assert agg["ok_count"] + agg["generation_missing_after_evidence_count"] + agg["retrieval_missing_count"] == agg["required_fact_total"]
