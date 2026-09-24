"""PN02D-B3U — content-safe end-to-end QA-VALUE observability projection (OBSERVABILITY ONLY).

The B3 fact-recall observability (``p1diagpn02db3``) retains only the P1 layer (R0/G0/OK)
of the canonical grade. This module completes the observability so a FUTURE governed
treatment run can decide END-TO-END QA value (P1 fact recall, P2 citation coverage, P3
negative abstention, S1 cross-notebook leakage, S2 invalid citations, S3 forbidden/
hallucinated facts) entirely provider-free.

Core invariant — GRADE ONCE / PROJECT TWICE (task B3U §6): this module NEVER grades. It
consumes the SAME canonical ``metricspn02.AnswerGrade`` objects already produced once by the
frozen grader during B3 capture and projects them content-safe, then reuses the frozen
aggregator (``metricspn02.qa_arm_metrics`` → ``QAArmMetrics``) and the frozen decision
(``decisionspn02.qa_decision``, Q0-Q3). There is NO second grader, NO second citation
parser, NO LLM judge, and NO raw-answer / raw-source / raw-prompt persistence (task B3U
§16/§17/§18): only ids, tokens, booleans, counts and ratios are retained — the exact
content-safety class the checkpointed B3 projection already permits.

Fail-closed (task B3U §24/§52): an incomplete projection (wrong record count, a missing arm,
or a missing/ill-typed applicable dimension) raises ``QAValueObservabilityError`` rather than
emitting an apparently-complete artifact.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Mapping, Sequence, Tuple

from open_notebook.integrations.graphrag.eval.datasetpn02 import QueryPN02
from open_notebook.integrations.graphrag.eval.decisionspn02 import qa_decision
from open_notebook.integrations.graphrag.eval.metricspn02 import (
    AnswerGrade,
    QAArmMetrics,
    qa_arm_metrics,
)
from open_notebook.integrations.graphrag.eval.schemaspn02 import ArmId, ScienceVerdict

#: Internal version of the additive QA-value observability subsection (task B3U §21).
QA_VALUE_OBSERVABILITY_VERSION = 1

#: 24 queries × 3 arms; 21 answerable × 3 = 63 positive, 3 unanswerable × 3 = 9 negative.
EXPECTED_QA_VALUE_RECORD_COUNT = 72
EXPECTED_POSITIVE_RECORD_COUNT = 63
EXPECTED_NEGATIVE_RECORD_COUNT = 9

_ALL_ARMS: Tuple[ArmId, ...] = (ArmId.QA_V, ArmId.QA_GD, ArmId.QA_VGD)


class QAValueObservabilityError(RuntimeError):
    """Raised when the QA-value projection is incomplete — the run FAILS CLOSED rather
    than emitting an apparently-complete QA-value artifact (task B3U §24/§52)."""


@dataclass(frozen=True)
class QAValueObservation:
    """Content-safe per-(query, arm) end-to-end QA-value observation (task B3U §9-§15).

    EVERY field is a derived id/token/boolean/count — NEVER answer text, source body,
    materialized evidence, prompt, or secret. Projected purely from the canonical
    ``AnswerGrade``; no value is recomputed from a raw answer.
    """

    query_id: str
    notebook_id: str
    arm: str
    is_negative: bool
    # P1 — fact recall (applicable to positive/answerable; 0 by construction for negatives).
    required_fact_count: int
    recognized_fact_count: int
    missing_fact_count: int
    # P2 — required-citation coverage (applicable to positive/answerable).
    required_citation_count: int
    required_citations_covered_ids: Tuple[str, ...]
    required_citations_covered_count: int
    required_citations_missing_ids: Tuple[str, ...]
    required_citations_missing_count: int
    # P3 — negative abstention (applicable to negative/unanswerable).
    abstained: bool
    # S1 — cross-notebook answer leakage (applicable to all).
    cross_notebook_answer_leak: bool
    # S2 — invalid citations (applicable to all).
    valid_citation_source_ids: Tuple[str, ...]
    valid_citation_count: int
    invalid_citation_source_ids: Tuple[str, ...]
    invalid_citation_count: int
    # S3 — forbidden/hallucinated facts (applicable to all).
    forbidden_facts_present_ids: Tuple[str, ...]
    forbidden_facts_present_count: int


def project_qa_value_observation(
    query: QueryPN02, grade: AnswerGrade
) -> QAValueObservation:
    """Project ONE canonical ``AnswerGrade`` into a content-safe QA-value observation.

    Pure — reuses the grade produced once by ``metricspn02.grade_answer``; grades nothing,
    parses no citations, reads no answer text. ``query`` supplies only ids/counts already
    fixed by the frozen fixture (notebook id, required-fact/citation counts)."""
    if grade.query_id != query.query_id:
        raise QAValueObservabilityError(
            f"grade/query id mismatch: {grade.query_id!r} != {query.query_id!r}"
        )
    return QAValueObservation(
        query_id=query.query_id,
        notebook_id=query.notebook_id,
        arm=grade.arm.value,
        is_negative=grade.is_negative,
        required_fact_count=len(query.expected_answer_facts),
        recognized_fact_count=len(grade.recognized_expected_facts),
        missing_fact_count=len(grade.missing_expected_facts),
        required_citation_count=len(query.required_citation_source_ids),
        required_citations_covered_ids=tuple(sorted(grade.required_citations_covered)),
        required_citations_covered_count=len(grade.required_citations_covered),
        required_citations_missing_ids=tuple(sorted(grade.required_citations_missing)),
        required_citations_missing_count=len(grade.required_citations_missing),
        abstained=grade.abstained,
        cross_notebook_answer_leak=grade.cross_notebook_answer_leak,
        valid_citation_source_ids=tuple(sorted(grade.valid_citation_sources)),
        valid_citation_count=len(grade.valid_citation_sources),
        invalid_citation_source_ids=tuple(sorted(grade.invalid_citation_sources)),
        invalid_citation_count=len(grade.invalid_citation_sources),
        forbidden_facts_present_ids=tuple(sorted(grade.forbidden_facts_present)),
        forbidden_facts_present_count=len(grade.forbidden_facts_present),
    )


_INT_FIELDS = (
    "required_fact_count",
    "recognized_fact_count",
    "missing_fact_count",
    "required_citation_count",
    "required_citations_covered_count",
    "required_citations_missing_count",
    "valid_citation_count",
    "invalid_citation_count",
    "forbidden_facts_present_count",
)
_BOOL_FIELDS = ("is_negative", "abstained", "cross_notebook_answer_leak")
_TUPLE_FIELDS = (
    "required_citations_covered_ids",
    "required_citations_missing_ids",
    "valid_citation_source_ids",
    "invalid_citation_source_ids",
    "forbidden_facts_present_ids",
)


def assert_observation_complete(obs: QAValueObservation) -> None:
    """Fail-closed check that every applicable QA-value dimension is present and well-typed
    on a single observation (task B3U §26/§50). A None/ill-typed required field raises."""
    for name in ("query_id", "notebook_id", "arm"):
        value = getattr(obs, name)
        if not isinstance(value, str) or not value:
            raise QAValueObservabilityError(
                f"QA-value observation missing identity field {name!r}"
            )
    for name in _BOOL_FIELDS:
        if not isinstance(getattr(obs, name), bool):
            raise QAValueObservabilityError(
                f"QA-value observation field {name!r} must be bool"
            )
    for name in _INT_FIELDS:
        value = getattr(obs, name)
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise QAValueObservabilityError(
                f"QA-value observation field {name!r} must be a non-negative int"
            )
    for name in _TUPLE_FIELDS:
        if not isinstance(getattr(obs, name), tuple):
            raise QAValueObservabilityError(
                f"QA-value observation field {name!r} must be a tuple"
            )


def _project_arm_metrics(m: QAArmMetrics) -> Dict[str, object]:
    """Content-safe projection of the frozen ``QAArmMetrics`` (numbers only)."""
    return {
        "arm": m.arm.value,
        "answer_required_fact_recall": m.answer_required_fact_recall,  # P1
        "citation_required_source_coverage": m.citation_required_source_coverage,  # P2
        "negative_answer_abstention_rate": m.negative_answer_abstention_rate,  # P3
        "cross_notebook_answer_leakage_rate": m.cross_notebook_answer_leakage_rate,  # S1
        "citation_invalid_count": m.citation_invalid_count,  # S2
        "hallucinated_forbidden_fact_rate": m.hallucinated_forbidden_fact_rate,  # S3
        "pos_count": m.pos_count,
        "neg_count": m.neg_count,
    }


def _project_one_observation(o: QAValueObservation) -> Dict[str, object]:
    return {
        "query_id": o.query_id,
        "notebook_id": o.notebook_id,
        "arm": o.arm,
        "is_negative": o.is_negative,
        "required_fact_count": o.required_fact_count,
        "recognized_fact_count": o.recognized_fact_count,
        "missing_fact_count": o.missing_fact_count,
        "required_citation_count": o.required_citation_count,
        "required_citations_covered_ids": list(o.required_citations_covered_ids),
        "required_citations_covered_count": o.required_citations_covered_count,
        "required_citations_missing_ids": list(o.required_citations_missing_ids),
        "required_citations_missing_count": o.required_citations_missing_count,
        "abstained": o.abstained,
        "cross_notebook_answer_leak": o.cross_notebook_answer_leak,
        "valid_citation_source_ids": list(o.valid_citation_source_ids),
        "valid_citation_count": o.valid_citation_count,
        "invalid_citation_source_ids": list(o.invalid_citation_source_ids),
        "invalid_citation_count": o.invalid_citation_count,
        "forbidden_facts_present_ids": list(o.forbidden_facts_present_ids),
        "forbidden_facts_present_count": o.forbidden_facts_present_count,
    }


def _qa_value_status(
    isolation_evidenced: bool,
    arm_metrics: Mapping[ArmId, QAArmMetrics],
    verdict: ScienceVerdict,
) -> str:
    """Map to a QA-value interpretation state using ONLY frozen semantics (task B3U §36/§35).

    Hard-safety uses the SAME ``== 0`` conditions as ``decisionspn02._safe`` (no new
    threshold). No production-approval state is defined here.
    """
    hard_safety_ok = all(
        m.cross_notebook_answer_leakage_rate == 0
        and m.citation_invalid_count == 0
        and m.hallucinated_forbidden_fact_rate == 0
        for m in arm_metrics.values()
    )
    if not isolation_evidenced or not hard_safety_ok:
        return "QA_VALUE_SAFETY_GATE_FAIL"
    if verdict is ScienceVerdict.YES:
        return "QA_VALUE_DEMONSTRATED_ON_PN02"
    if verdict is ScienceVerdict.NO:
        return "QA_VALUE_NOT_DEMONSTRATED"
    if verdict is ScienceVerdict.INCONCLUSIVE:
        return "QA_VALUE_INCONCLUSIVE"
    return "QA_VALUE_SAFETY_GATE_FAIL"  # NOT_EVALUATED (Q0 isolation) — caught above


def build_qa_value_projection(
    pairs: Sequence[Tuple[QueryPN02, AnswerGrade]],
    *,
    isolation_evidenced: bool,
    expected_pair_count: int = EXPECTED_QA_VALUE_RECORD_COUNT,
    expected_positive: int = EXPECTED_POSITIVE_RECORD_COUNT,
    expected_negative: int = EXPECTED_NEGATIVE_RECORD_COUNT,
) -> Dict[str, object]:
    """Build the content-safe, additive QA-value observability subsection (task B3U §8/§20).

    ``pairs`` are the ``(query, grade)`` produced by the SINGLE B3 capture grading pass — this
    function grades nothing. Reuses the frozen ``qa_arm_metrics`` (per-arm aggregate) and
    ``qa_decision`` (Q0-Q3). FAILS CLOSED on wrong counts, a missing arm, or a missing/ill-typed
    applicable dimension; ``qa_value_observability_complete`` is True only when every check passes.
    """
    observations = [project_qa_value_observation(q, g) for q, g in pairs]
    if len(observations) != expected_pair_count:
        raise QAValueObservabilityError(
            f"QA-value projection incomplete: {len(observations)} records != "
            f"{expected_pair_count} expected"
        )
    positive = sum(1 for o in observations if not o.is_negative)
    negative = sum(1 for o in observations if o.is_negative)
    if positive != expected_positive or negative != expected_negative:
        raise QAValueObservabilityError(
            f"QA-value projection class split wrong: {positive} pos / {negative} neg != "
            f"{expected_positive}/{expected_negative}"
        )
    for obs in observations:
        assert_observation_complete(obs)

    grades_by_arm: Dict[ArmId, List[AnswerGrade]] = {}
    for _q, g in pairs:
        grades_by_arm.setdefault(g.arm, []).append(g)
    if set(grades_by_arm) != set(_ALL_ARMS):
        raise QAValueObservabilityError(
            "QA-value projection requires all three arms (QA-V, QA-GD, QA-V+GD); "
            f"observed {sorted(a.value for a in grades_by_arm)}"
        )
    # Frozen aggregator over the SAME canonical grades — no second aggregator.
    arm_metrics = {arm: qa_arm_metrics(grades_by_arm[arm]) for arm in _ALL_ARMS}
    baseline = arm_metrics[ArmId.QA_V]
    graph_arms = [arm_metrics[ArmId.QA_GD], arm_metrics[ArmId.QA_VGD]]
    # Frozen decision — no B3-specific thresholds.
    decision = qa_decision(isolation_evidenced, baseline, graph_arms)

    # P3 negative-abstention gate (task B3U §12/§32): over the negative observations.
    negatives = [o for o in observations if o.is_negative]
    negative_abstention_pass = sum(1 for o in negatives if o.abstained)
    negative_abstention_fail = sum(1 for o in negatives if not o.abstained)

    # Citation / safety gate scalars (task B3U §33) — frozen aggregate definitions.
    invalid_citation_count = sum(o.invalid_citation_count for o in observations)
    forbidden_fact_count = sum(o.forbidden_facts_present_count for o in observations)
    cross_notebook_citation_count = invalid_citation_count  # invalid == non-member citation

    status = _qa_value_status(isolation_evidenced, arm_metrics, decision.verdict)

    return {
        "version": QA_VALUE_OBSERVABILITY_VERSION,
        "mode": "OBSERVABILITY_ONLY",
        "isolation_evidenced": isolation_evidenced,
        "expected_pair_count": expected_pair_count,
        "expected_positive_count": expected_positive,
        "expected_negative_count": expected_negative,
        "observed_pair_count": len(observations),
        "observed_positive_count": positive,
        "observed_negative_count": negative,
        "applicability": {
            "P1_fact_recall": "positive",
            "P2_citation_coverage": "positive",
            "P3_negative_abstention": "negative",
            "S1_cross_notebook_leakage": "all",
            "S2_invalid_citations": "all",
            "S3_forbidden_facts": "all",
        },
        "per_query_arm": [_project_one_observation(o) for o in observations],
        "arm_metrics": [_project_arm_metrics(arm_metrics[a]) for a in _ALL_ARMS],
        "negative_abstention_pass_count": negative_abstention_pass,
        "negative_abstention_fail_count": negative_abstention_fail,
        "citation_coverage_baseline": baseline.citation_required_source_coverage,
        "invalid_citation_count": invalid_citation_count,
        # S3 forbidden/hallucinated-FACT aggregate — named as fact data, not citation data
        # (PN02DB3U-IR1-L2). The frozen S3 dimension is forbidden facts, never "forbidden citations".
        "forbidden_fact_count": forbidden_fact_count,
        "cross_notebook_citation_count": cross_notebook_citation_count,
        "qa_decision": {
            "verdict": decision.verdict.value,
            "rule": decision.rule,
            "positive_arms": list(decision.positive_arms),
        },
        "qa_value_status": status,
        "qa_value_observability_complete": True,
    }


__all__ = [
    "QA_VALUE_OBSERVABILITY_VERSION",
    "EXPECTED_QA_VALUE_RECORD_COUNT",
    "EXPECTED_POSITIVE_RECORD_COUNT",
    "EXPECTED_NEGATIVE_RECORD_COUNT",
    "QAValueObservabilityError",
    "QAValueObservation",
    "project_qa_value_observation",
    "assert_observation_complete",
    "build_qa_value_projection",
]
