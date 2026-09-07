"""Fully mechanical PN02 decision tables (PN02A §17 / task §23-§25, §28).

EVALUATION-ONLY. Nothing in production imports this. Each verdict is a first-match
table of EXACT integer/rational comparisons on frozen metrics — no adjectives, no
weighting, no threshold tuning, no post-run judgment (PN02A §17). INCONCLUSIVE is
predeclared (only R4 / Q3 / M3), never an escape hatch (PN02A §17e).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Sequence, Tuple

from open_notebook.integrations.graphrag.eval.metricspn02 import (
    IncrementalRetrievalTotals,
    QAArmMetrics,
)
from open_notebook.integrations.graphrag.eval.schemaspn02 import ArmId, ScienceVerdict

FULL_NOTEBOOK_FRACTION = 1.0


def is_full_notebook_return(gd_candidate_fraction: float) -> bool:
    """Exact full-notebook breadth trap (task §24 / PN02A §17b criterion D).

    True ONLY at exactly 1.0 (the complete 8-Source member corpus was returned).
    There is deliberately NO 7/8, 'near-full', or 'approximately full' category —
    fractions < 1.0 (including 0.875 = 7/8) are governed entirely by the NEW_REQ >
    NEW_FP comparison.
    """
    return gd_candidate_fraction == FULL_NOTEBOOK_FRACTION


# --------------------------------------------------------------------------- #
# Retrieval value R0-R4 (PN02A §17b)
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class RetrievalDecision:
    verdict: ScienceVerdict
    rule: str
    new_req: int
    new_fp: int
    n_gain_notebooks_d: int
    neg_return_gd: int
    neg_return_v5: int


def retrieval_decision(
    isolation_evidenced: bool, totals: IncrementalRetrievalTotals
) -> RetrievalDecision:
    """PER_NOTEBOOK_GRAPH_RETRIEVAL_VALUE_EVIDENCED — first match wins (PN02A §17b)."""

    def out(verdict: ScienceVerdict, rule: str) -> RetrievalDecision:
        return RetrievalDecision(
            verdict=verdict,
            rule=rule,
            new_req=totals.new_req,
            new_fp=totals.new_fp,
            n_gain_notebooks_d=totals.n_gain_notebooks_d,
            neg_return_gd=totals.neg_return_gd,
            neg_return_v5=totals.neg_return_v5,
        )

    # R0 — isolation failed: retrieval value is not reported.
    if not isolation_evidenced:
        return out(ScienceVerdict.NOT_EVALUATED, "R0")
    # R1 — GD adds no required Source V5 missed, anywhere.
    if totals.new_req == 0:
        return out(ScienceVerdict.NO, "R1")
    # R2 — every gain came from a full-8 return (coverage, not discrimination).
    if totals.n_gain_notebooks_d == 0:
        return out(ScienceVerdict.NO, "R2")
    # R3 — discriminative gain in >=2 notebooks, C (NEW_REQ>NEW_FP), E (negatives).
    if (
        totals.n_gain_notebooks_d >= 2
        and totals.new_req > totals.new_fp
        and totals.neg_return_gd <= totals.neg_return_v5
    ):
        return out(ScienceVerdict.YES, "R3")
    # R4 — real gain but confined to 1 notebook, OR NEW_FP>=NEW_REQ, OR GD regresses negatives.
    return out(ScienceVerdict.INCONCLUSIVE, "R4")


# --------------------------------------------------------------------------- #
# QA value Q0-Q3 (PN02A §17c)
# --------------------------------------------------------------------------- #

def _safe(arm: QAArmMetrics, isolation_evidenced: bool) -> bool:
    return (
        isolation_evidenced
        and arm.cross_notebook_answer_leakage_rate == 0
        and arm.citation_invalid_count == 0
        and arm.hallucinated_forbidden_fact_rate == 0
    )


def _improves(arm: QAArmMetrics, baseline: QAArmMetrics) -> bool:
    return (
        arm.answer_required_fact_recall > baseline.answer_required_fact_recall
        or arm.citation_required_source_coverage
        > baseline.citation_required_source_coverage
        or arm.negative_answer_abstention_rate > baseline.negative_answer_abstention_rate
    )


def _no_regress(arm: QAArmMetrics, baseline: QAArmMetrics) -> bool:
    return (
        arm.answer_required_fact_recall >= baseline.answer_required_fact_recall
        and arm.citation_required_source_coverage
        >= baseline.citation_required_source_coverage
        and arm.negative_answer_abstention_rate
        >= baseline.negative_answer_abstention_rate
    )


def is_positive_arm(
    arm: QAArmMetrics, baseline: QAArmMetrics, isolation_evidenced: bool
) -> bool:
    """POSITIVE(G) := SAFE(G) AND IMPROVES(G) AND NO_REGRESS(G) (PN02A §17c)."""
    return (
        _safe(arm, isolation_evidenced)
        and _improves(arm, baseline)
        and _no_regress(arm, baseline)
    )


@dataclass(frozen=True)
class QADecision:
    verdict: ScienceVerdict
    rule: str
    positive_arms: Tuple[str, ...] = field(default_factory=tuple)


def qa_decision(
    isolation_evidenced: bool,
    baseline: QAArmMetrics,
    graph_arms: Sequence[QAArmMetrics],
) -> QADecision:
    """PER_NOTEBOOK_GRAPH_QA_VALUE_EVIDENCED — first match wins (PN02A §17c).

    ``baseline`` is QA-V; ``graph_arms`` are QA-GD and/or QA-V+GD.
    """
    if baseline.arm is not ArmId.QA_V:
        raise ValueError("qa_decision baseline must be the QA-V arm")
    if any(g.arm is ArmId.QA_V for g in graph_arms):
        raise ValueError("graph_arms must not include the QA-V baseline")
    if not graph_arms:
        raise ValueError("qa_decision requires >=1 graph arm")

    # Q0 — isolation failed.
    if not isolation_evidenced:
        return QADecision(ScienceVerdict.NOT_EVALUATED, "Q0")

    positive = [
        g for g in graph_arms if is_positive_arm(g, baseline, isolation_evidenced)
    ]
    # Q1 — some arm is POSITIVE.
    if positive:
        return QADecision(
            ScienceVerdict.YES, "Q1", tuple(g.arm.value for g in positive)
        )
    # Q2 — every arm is SAFE and none improves any primary.
    if all(
        _safe(g, isolation_evidenced) and not _improves(g, baseline)
        for g in graph_arms
    ):
        return QADecision(ScienceVerdict.NO, "Q2")
    # Q3 — mixed (improves one primary but regresses another) or unsafe improvement.
    return QADecision(ScienceVerdict.INCONCLUSIVE, "Q3")


# --------------------------------------------------------------------------- #
# Multi-hop M0-M3 (PN02A §17d)
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class MultihopDecision:
    verdict: ScienceVerdict
    rule: str
    mh: int


def multihop_decision(
    isolation_evidenced: bool, multihop_incremental_required_recovery: int
) -> MultihopDecision:
    """PER_NOTEBOOK_MULTIHOP_INCREMENTAL_VALUE_EVIDENCED — first match (PN02A §17d).

    MH counts distinct notebooks (one multi-hop query each) where GD completes the
    >=2-Source required set V5 alone missed, with GD_CANDIDATE_FRACTION < 1.0.
    """
    mh = multihop_incremental_required_recovery
    if not isolation_evidenced:
        return MultihopDecision(ScienceVerdict.NOT_EVALUATED, "M0", mh)
    if mh >= 2:
        return MultihopDecision(ScienceVerdict.YES, "M1", mh)
    if mh == 0:
        return MultihopDecision(ScienceVerdict.NO, "M2", mh)
    # mh == 1: a single query cannot establish general multi-hop value.
    return MultihopDecision(ScienceVerdict.INCONCLUSIVE, "M3", mh)


__all__ = [
    "FULL_NOTEBOOK_FRACTION",
    "is_full_notebook_return",
    "RetrievalDecision",
    "retrieval_decision",
    "is_positive_arm",
    "QADecision",
    "qa_decision",
    "MultihopDecision",
    "multihop_decision",
]
