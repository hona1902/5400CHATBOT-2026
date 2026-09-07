"""Offline PN02 evaluation orchestrator (task §19-§34, §57).

EVALUATION-ONLY. Nothing in production imports this. Consumes deterministic,
already-produced result records (no retriever, no provider) and applies the frozen
Stage-1 gate, then — ONLY if Stage 1 passes — the Stage-2 value/QA/multi-hop
decisions. The fail-closed property is structural: Stage-2 metrics are never
computed when Stage 1 fails (task §21), and the Stage-2 code path is guarded by the
``Stage1Authorization`` capability minted only on PASS.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Mapping, Optional, Sequence, Tuple

from open_notebook.integrations.graphrag.eval.datasetpn02 import (
    FixturePN02,
    MembershipRemovalScenario,
    QueryClassPN02,
)
from open_notebook.integrations.graphrag.eval.decisionspn02 import (
    MultihopDecision,
    QADecision,
    RetrievalDecision,
    multihop_decision,
    qa_decision,
    retrieval_decision,
)
from open_notebook.integrations.graphrag.eval.membershippn02 import (
    RemovalReport,
    evaluate_removal,
    removal_reprobe_isolation_probes,
)
from open_notebook.integrations.graphrag.eval.metricspn02 import (
    QAArmMetrics,
    RetrievalProbe,
    grade_answer,
    incremental_retrieval_totals,
    qa_arm_metrics,
)
from open_notebook.integrations.graphrag.eval.schemaspn02 import (
    ArmId,
    GDEvidenceResult,
    QAAnswerResult,
    RemovalProbeResult,
    ScienceVerdict,
    ScientificOutputs,
    VectorEvidenceResult,
)
from open_notebook.integrations.graphrag.eval.stage1pn02 import (
    IsolationProbe,
    Stage1Authorization,
    Stage1Decision,
    evaluate_stage1,
    require_stage2_authorization,
)

VECTOR_K_STAGE2 = 5  # PN02A §8c: Stage-2 QA arms use K=5.


@dataclass(frozen=True)
class OfflineEvaluationResult:
    stage1: Stage1Decision
    isolation_evidenced: bool
    removal: RemovalReport
    retrieval: RetrievalDecision
    qa: QADecision
    multihop: MultihopDecision
    qa_baseline: Optional[QAArmMetrics]
    qa_graph_arms: Tuple[QAArmMetrics, ...]
    scientific: ScientificOutputs


def build_baseline_isolation_probes(
    fx: FixturePN02, gd_results: Mapping[str, GDEvidenceResult]
) -> List[IsolationProbe]:
    """One IsolationProbe per baseline query from its GD evidence (24 probes)."""
    probes: List[IsolationProbe] = []
    for q in fx.queries:
        gd = gd_results.get(q.query_id)
        if gd is None:
            raise ValueError(f"missing GD result for query {q.query_id}")
        members = fx.members_of(q.notebook_id)
        probes.append(
            IsolationProbe(
                query_id=q.query_id,
                notebook_id=q.notebook_id,
                members=frozenset(members),
                gd_candidate_sources=frozenset(gd.evidence.as_set()),
                provenance_foreign=gd.evidence.stats.foreign,
                provenance_malformed=gd.evidence.stats.malformed,
            )
        )
    return probes


def build_retrieval_probes(
    fx: FixturePN02,
    vector_results: Mapping[str, VectorEvidenceResult],
    gd_results: Mapping[str, GDEvidenceResult],
) -> List[RetrievalProbe]:
    """One RetrievalProbe per query for the incremental-value computation (§9c)."""
    probes: List[RetrievalProbe] = []
    for q in fx.queries:
        members = fx.members_of(q.notebook_id)
        v = vector_results.get(q.query_id)
        gd = gd_results.get(q.query_id)
        if v is None or gd is None:
            raise ValueError(f"missing V/GD result for query {q.query_id}")
        v5 = frozenset(v.evidence.top_k(VECTOR_K_STAGE2)) & frozenset(members)
        gdm = frozenset(gd.evidence.as_set()) & frozenset(members)
        probes.append(
            RetrievalProbe(
                query_id=q.query_id,
                notebook_id=q.notebook_id,
                required=frozenset(q.required_source_ids),
                optional=frozenset(q.optional_support_source_ids),
                gd_member_set=gdm,
                v5_set=v5,
                is_negative=q.is_negative,
                is_multihop=q.multi_hop_required,
            )
        )
    return probes


def _grade_arm(
    fx: FixturePN02,
    arm: ArmId,
    qa_results: Sequence[QAAnswerResult],
) -> QAArmMetrics:
    grades = []
    latencies: List[Optional[int]] = []
    for r in qa_results:
        if r.arm is not arm:
            continue
        q = fx.query(r.query_id)
        members = fx.members_of(q.notebook_id)
        grades.append(grade_answer(r, q, members))
        latencies.append(r.latency_ms)
    if not grades:
        raise ValueError(f"no QA answers for arm {arm.value}")
    return qa_arm_metrics(grades, latencies_ms=latencies)


def run_offline_evaluation(
    fx: FixturePN02,
    *,
    fixture_hash: str,
    scenario: MembershipRemovalScenario,
    vector_results: Mapping[str, VectorEvidenceResult],
    gd_results: Mapping[str, GDEvidenceResult],
    removal_after_removed_nb: RemovalProbeResult,
    removal_after_retained_nb: RemovalProbeResult,
    qa_results: Optional[Sequence[QAAnswerResult]] = None,
) -> OfflineEvaluationResult:
    """Run the full offline evaluation with the fail-closed Stage-2 gate.

    Stage 1 is computed over 24 baseline + 2 removal re-probes. If it fails, no
    Stage-2 metric is computed and every value verdict is NOT_EVALUATED.
    """
    # ---- Stage 1 (isolation + evidence validity) --------------------------- #
    baseline_probes = build_baseline_isolation_probes(fx, gd_results)
    removal_probes = removal_reprobe_isolation_probes(
        scenario, removal_after_removed_nb, removal_after_retained_nb
    )
    stage1_decision, auth = evaluate_stage1(
        baseline_probes + removal_probes, fixture_hash=fixture_hash
    )

    # Removal report is Stage-1 evidence validity (no final-answer calls, §14).
    removal = evaluate_removal(
        scenario, removal_after_removed_nb, removal_after_retained_nb
    )

    if auth is None:
        # FAIL-CLOSED: Stage 2 is unreachable. Value verdicts are NOT_EVALUATED and
        # no Stage-2 metric is computed (task §21).
        retrieval = retrieval_decision(False, _zero_totals())
        multihop = multihop_decision(False, 0)
        qa = QADecision(ScienceVerdict.NOT_EVALUATED, "Q0")
        scientific = ScientificOutputs(
            per_notebook_isolation_evidenced=ScienceVerdict.NO,
            per_notebook_graph_retrieval_value_evidenced=ScienceVerdict.NOT_EVALUATED,
            per_notebook_graph_qa_value_evidenced=ScienceVerdict.NOT_EVALUATED,
            per_notebook_multihop_incremental_value_evidenced=ScienceVerdict.NOT_EVALUATED,
            notes=("stage1_failed",) + stage1_decision.violations,
        )
        return OfflineEvaluationResult(
            stage1=stage1_decision,
            isolation_evidenced=False,
            removal=removal,
            retrieval=retrieval,
            qa=qa,
            multihop=multihop,
            qa_baseline=None,
            qa_graph_arms=(),
            scientific=scientific,
        )

    # ---- Stage 2 (guarded by the authorization capability) ----------------- #
    return _run_stage2(
        fx,
        auth,
        stage1_decision,
        removal,
        vector_results,
        gd_results,
        qa_results,
    )


def _run_stage2(
    fx: FixturePN02,
    auth: Stage1Authorization,
    stage1_decision: Stage1Decision,
    removal: RemovalReport,
    vector_results: Mapping[str, VectorEvidenceResult],
    gd_results: Mapping[str, GDEvidenceResult],
    qa_results: Optional[Sequence[QAAnswerResult]],
) -> OfflineEvaluationResult:
    require_stage2_authorization(auth)  # structural gate (task §21)

    totals = incremental_retrieval_totals(
        build_retrieval_probes(fx, vector_results, gd_results)
    )
    retrieval = retrieval_decision(True, totals)
    multihop = multihop_decision(
        True, totals.multihop_incremental_required_recovery
    )

    qa_baseline: Optional[QAArmMetrics] = None
    qa_graph_arms: Tuple[QAArmMetrics, ...] = ()
    qa: QADecision
    notes: List[str] = []
    if qa_results:
        qa_baseline = _grade_arm(fx, ArmId.QA_V, qa_results)
        graph_arms = []
        for arm in (ArmId.QA_GD, ArmId.QA_VGD):
            if any(r.arm is arm for r in qa_results):
                graph_arms.append(_grade_arm(fx, arm, qa_results))
        qa_graph_arms = tuple(graph_arms)
        qa = qa_decision(True, qa_baseline, qa_graph_arms)
        if qa.positive_arms:
            notes.append("qa_positive_arms=" + ",".join(qa.positive_arms))
    else:
        # QA answers were not supplied — a HARNESS/data-absent state, NOT one of
        # the predeclared §17e INCONCLUSIVE (Q3) metric conditions. Report it as a
        # distinct NOT_EVALUATED technical state, never as a scientific Q3 verdict
        # (review LOW-2; keeps §56/§57 technical-vs-scientific separation).
        qa = QADecision(ScienceVerdict.NOT_EVALUATED, "QA_NOT_SUPPLIED")
        notes.append("qa_not_supplied")

    scientific = ScientificOutputs(
        per_notebook_isolation_evidenced=ScienceVerdict.YES,
        per_notebook_graph_retrieval_value_evidenced=retrieval.verdict,
        per_notebook_graph_qa_value_evidenced=qa.verdict,
        per_notebook_multihop_incremental_value_evidenced=multihop.verdict,
        notes=tuple(notes),
    )
    return OfflineEvaluationResult(
        stage1=stage1_decision,
        isolation_evidenced=True,
        removal=removal,
        retrieval=retrieval,
        qa=qa,
        multihop=multihop,
        qa_baseline=qa_baseline,
        qa_graph_arms=qa_graph_arms,
        scientific=scientific,
    )


def _zero_totals():
    from open_notebook.integrations.graphrag.eval.metricspn02 import (
        IncrementalRetrievalTotals,
    )

    return IncrementalRetrievalTotals(
        new_req=0,
        new_fp=0,
        sum_increment=0,
        incremental_graph_precision=None,
        n_gain_notebooks_d=0,
        neg_return_gd=0,
        neg_return_v5=0,
        multihop_incremental_required_recovery=0,
    )


# Re-exported for callers that group by class (diagnostics only).
QUERY_CLASS = QueryClassPN02

__all__ = [
    "VECTOR_K_STAGE2",
    "OfflineEvaluationResult",
    "build_baseline_isolation_probes",
    "build_retrieval_probes",
    "run_offline_evaluation",
]
