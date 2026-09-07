"""Content-safe PN02 artifact serializers (task §55).

EVALUATION-ONLY. Nothing in production imports this. Serializes metrics, decisions,
manifests, and the membership-removal report into content-free JSON-ready dicts —
ONLY ids, labels, counts, metric values, verdicts, and non-secret metadata. It
never receives or emits source text, query text, generated answer text, raw vendor
payloads, or credentials (that is structural: every input is already content-free,
and answer text on ``QAAnswerResult`` is deliberately dropped here).
"""

from __future__ import annotations

from typing import Dict, Optional, Sequence

from open_notebook.integrations.graphrag.eval.decisionspn02 import (
    MultihopDecision,
    QADecision,
    RetrievalDecision,
)
from open_notebook.integrations.graphrag.eval.manifestpn02 import RunManifest
from open_notebook.integrations.graphrag.eval.membershippn02 import RemovalReport
from open_notebook.integrations.graphrag.eval.metricspn02 import QAArmMetrics
from open_notebook.integrations.graphrag.eval.schemaspn02 import ScientificOutputs
from open_notebook.integrations.graphrag.eval.stage1pn02 import Stage1Decision


def stage1_report(decision: Stage1Decision) -> Dict[str, object]:
    m = decision.metrics
    return {
        "stage1_status": decision.stage1_status,
        "stage2_authorized_by_result": decision.stage2_authorized_by_result,
        "violations": list(decision.violations),
        "metrics": {
            "probe_count": m.probe_count,
            "cross_notebook_leakage_rate": m.cross_notebook_leakage_rate,
            "cross_notebook_leak_query_count": m.cross_notebook_leak_query_count,
            "cross_notebook_leak_source_occurrences": m.cross_notebook_leak_source_occurrences,
            "provenance_foreign": m.provenance_foreign,
            "provenance_malformed": m.provenance_malformed,
            "citation_membership_invalid": m.citation_membership_invalid,
            "stale_graph_evidence_accepted_as_valid": m.stale_graph_evidence_accepted_as_valid,
        },
    }


def retrieval_report(decision: RetrievalDecision) -> Dict[str, object]:
    return {
        "verdict": decision.verdict.value,
        "rule": decision.rule,
        "new_req": decision.new_req,
        "new_fp": decision.new_fp,
        "n_gain_notebooks_d": decision.n_gain_notebooks_d,
        "neg_return_gd": decision.neg_return_gd,
        "neg_return_v5": decision.neg_return_v5,
    }


def _qa_arm_dict(m: QAArmMetrics) -> Dict[str, object]:
    return {
        "arm": m.arm.value,
        "answer_required_fact_recall": m.answer_required_fact_recall,
        "citation_required_source_coverage": m.citation_required_source_coverage,
        "negative_answer_abstention_rate": m.negative_answer_abstention_rate,
        "cross_notebook_answer_leakage_rate": m.cross_notebook_answer_leakage_rate,
        "citation_invalid_count": m.citation_invalid_count,
        "hallucinated_forbidden_fact_rate": m.hallucinated_forbidden_fact_rate,
        "answer_fact_accuracy": m.answer_fact_accuracy,
        "citation_validity_rate": m.citation_validity_rate,
        "answer_latency_mean_ms": m.answer_latency_mean_ms,
        "pos_count": m.pos_count,
        "neg_count": m.neg_count,
    }


def qa_report(
    baseline: QAArmMetrics,
    graph_arms: Sequence[QAArmMetrics],
    decision: QADecision,
) -> Dict[str, object]:
    return {
        "verdict": decision.verdict.value,
        "rule": decision.rule,
        "positive_arms": list(decision.positive_arms),
        "arms": [_qa_arm_dict(baseline)] + [_qa_arm_dict(a) for a in graph_arms],
    }


def multihop_report(decision: MultihopDecision) -> Dict[str, object]:
    return {
        "verdict": decision.verdict.value,
        "rule": decision.rule,
        "multihop_incremental_required_recovery": decision.mh,
    }


def removal_report(report: RemovalReport) -> Dict[str, object]:
    return {
        "shared_source": report.shared_source,
        "removed_from_notebook": report.removed_from_notebook,
        "retained_notebook": report.retained_notebook,
        "accepted_evidence_removed_nb": sorted(report.accepted_evidence_removed_nb),
        "accepted_evidence_retained_nb": sorted(report.accepted_evidence_retained_nb),
        "postcondition_removed_holds": report.postcondition_removed_holds,
        "postcondition_retained_holds": report.postcondition_retained_holds,
        "graph_delete_succeeded_removed_nb": report.graph_delete_succeeded_removed_nb,
        "stale_graph_evidence_accepted_as_valid": report.stale_graph_evidence_accepted_as_valid,
    }


def scientific_outputs_report(outputs: ScientificOutputs) -> Dict[str, object]:
    return {
        "PER_NOTEBOOK_ISOLATION_EVIDENCED": outputs.per_notebook_isolation_evidenced.value,
        "PER_NOTEBOOK_GRAPH_RETRIEVAL_VALUE_EVIDENCED": outputs.per_notebook_graph_retrieval_value_evidenced.value,
        "PER_NOTEBOOK_GRAPH_QA_VALUE_EVIDENCED": outputs.per_notebook_graph_qa_value_evidenced.value,
        "PER_NOTEBOOK_MULTIHOP_INCREMENTAL_VALUE_EVIDENCED": outputs.per_notebook_multihop_incremental_value_evidenced.value,
        "notes": list(outputs.notes),
    }


def manifest_report(manifest: RunManifest) -> Dict[str, object]:
    return {
        "run_id": manifest.run_id,
        "fixture_name": manifest.fixture_name,
        "fixture_hash": manifest.fixture_hash,
        "git_commit": manifest.git_commit,
        "lightrag_version_expected": manifest.lightrag_version_expected,
        "systems_enabled": list(manifest.systems_enabled),
        "vector_k_values": list(manifest.vector_k_values),
        "query_count": manifest.query_count,
        "stage_status": manifest.stage_status,
        "created_at": manifest.created_at,
        "boundary_b": {
            "dataset_class": manifest.boundary_b.dataset_class.value,
            "real_internal_data_allowed": manifest.boundary_b.real_internal_data_allowed,
            "synthetic_only": manifest.boundary_b.synthetic_only,
        },
        "budget_caps": dict(manifest.budget_caps),
        "routing": {
            nb: {
                "notebook_record_id": route.notebook_record_id,
                "workspace_id": route.workspace_id,
                "endpoint_placeholder": route.endpoint_placeholder,
            }
            for nb, route in manifest.routing.items()
        },
    }


def build_report(
    *,
    manifest: Optional[RunManifest] = None,
    stage1: Optional[Stage1Decision] = None,
    retrieval: Optional[RetrievalDecision] = None,
    qa_baseline: Optional[QAArmMetrics] = None,
    qa_graph_arms: Optional[Sequence[QAArmMetrics]] = None,
    qa_decision_result: Optional[QADecision] = None,
    multihop: Optional[MultihopDecision] = None,
    removal: Optional[RemovalReport] = None,
    scientific: Optional[ScientificOutputs] = None,
) -> Dict[str, object]:
    """Assemble the full content-safe PN02 report from whatever stages ran."""
    report: Dict[str, object] = {"report_kind": "graphrag_pn02_offline_report"}
    if manifest is not None:
        report["manifest"] = manifest_report(manifest)
    if stage1 is not None:
        report["stage1"] = stage1_report(stage1)
    if retrieval is not None:
        report["retrieval"] = retrieval_report(retrieval)
    if (
        qa_baseline is not None
        and qa_graph_arms is not None
        and qa_decision_result is not None
    ):
        report["qa"] = qa_report(qa_baseline, qa_graph_arms, qa_decision_result)
    if multihop is not None:
        report["multihop"] = multihop_report(multihop)
    if removal is not None:
        report["membership_removal"] = removal_report(removal)
    if scientific is not None:
        report["scientific_outputs"] = scientific_outputs_report(scientific)
    return report


__all__ = [
    "stage1_report",
    "retrieval_report",
    "qa_report",
    "multihop_report",
    "removal_report",
    "scientific_outputs_report",
    "manifest_report",
    "build_report",
]
