"""GraphRAG-08 content-free evaluation report / artifact (task §44-§45, §77, §79).

EVALUATION-ONLY. Rolls per-query V/GQ/GD outcomes into a machine-readable artifact
containing ONLY ids, class/split labels, source ids, metric values, counts,
latencies, statuses, and non-secret metadata — never query text, source/chunk
text, entity/relation descriptions, generated answers, raw payloads, or
credentials. That is structural: this module never receives content, only the
normalized (content-free) results.

Metric discipline:
  * Vector (V) gets RANKED metrics (hit@k, recall@k, mrr, full_set_recovered@k).
  * GQ and GD get UNORDERED SET metrics only (task §40). No MRR/nDCG for graph.
  * Negatives use abstention/breadth metrics, never recall/precision (task §41).
  * candidate_fraction denominator = the run's benchmark corpus size (task §35/§65).
  * ORACLE_UNION_FULL is reported as an upper bound only — never as RRF/fusion
    validation (task §44/§69).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

from open_notebook.integrations.graphrag.eval import metrics as m04
from open_notebook.integrations.graphrag.eval import metrics08 as m08
from open_notebook.integrations.graphrag.eval.gd_seam import GDEvidence
from open_notebook.integrations.graphrag.eval.normalize import (
    ProvenanceStats,
)
from open_notebook.integrations.graphrag.eval.runner08 import (
    STATE_EVALUATED,
    QueryEvaluation08,
)

VECTOR_SYSTEM = "VECTOR_BASELINE"
GQ_SYSTEM = "CURRENT_LIGHTRAG_QUERY_EVIDENCE"
GD_SYSTEM = "STRUCTURED_QUERY_DATA_EVIDENCE"
DEFAULT_K_BUDGETS = (1, 3, 5, 10)


def _mean(values: Sequence[float]) -> Optional[float]:
    return round(sum(values) / len(values), 6) if values else None


def _rate(flags: Sequence[bool]) -> Optional[float]:
    return round(sum(1 for f in flags if f) / len(flags), 6) if flags else None


def _graph_pair(
    ev: QueryEvaluation08, system: str
) -> Optional[Tuple[frozenset[str], ProvenanceStats]]:
    """Return (candidate_set, provenance_stats) for GQ or GD, or None if unusable."""
    if system == GQ_SYSTEM:
        if ev.gq_state != STATE_EVALUATED or ev.gq is None:
            return None
        return ev.gq.as_set(), ev.gq.stats
    if system == GD_SYSTEM:
        if ev.gd_state != STATE_EVALUATED or ev.gd is None:
            return None
        return ev.gd.as_set(), ev.gd.retrieval.stats
    raise ValueError(f"unknown graph system {system}")


def _vector_block(
    evals: Sequence[QueryEvaluation08], ks: Sequence[int]
) -> Dict[str, object]:
    usable = [
        e
        for e in evals
        if e.answerable and e.vector_state == STATE_EVALUATED and e.vector is not None
    ]
    block: Dict[str, object] = {"evaluated_count": len(usable)}
    for k in ks:
        block[f"hit@{k}"] = _rate(
            [m04.hit_at_k(e.vector.source_ids, e.required_ids, k) for e in usable]  # type: ignore[union-attr]
        )
        block[f"recall@{k}"] = _mean(
            [m04.recall_at_k(e.vector.source_ids, e.required_ids, k) for e in usable]  # type: ignore[union-attr]
        )
        block[f"full_set_recovered@{k}"] = _rate(
            [
                m08.vector_full_recovered(e.vector.source_ids, e.required_ids, k)  # type: ignore[union-attr]
                for e in usable
            ]
        )
    block["mrr"] = _mean(
        [m04.mrr(e.vector.source_ids, e.required_ids) for e in usable]  # type: ignore[union-attr]
    )
    return block


def _graph_block(
    evals: Sequence[QueryEvaluation08], system: str, corpus_size: int
) -> Dict[str, object]:
    answerable = [e for e in evals if e.answerable]
    precisions: List[float] = []
    recalls: List[float] = []
    f1s: List[float] = []
    full_flags: List[bool] = []
    partial_flags: List[bool] = []
    fp_counts: List[float] = []
    counts: List[float] = []
    fractions: List[float] = []
    prov = {"total": 0, "valid_unique": 0, "duplicates": 0, "malformed": 0, "foreign": 0, "off_benchmark": 0}
    evaluated = 0
    for e in answerable:
        pair = _graph_pair(e, system)
        if pair is None:
            continue
        evaluated += 1
        cands, stats = pair
        recalls.append(m08.set_recall(cands, e.required_ids))
        p = m08.set_precision(cands, e.required_ids)
        if p is not None:
            precisions.append(p)
        f1 = m08.set_f1(cands, e.required_ids)
        if f1 is not None:
            f1s.append(f1)
        full_flags.append(m08.full_source_set_recovered(cands, e.required_ids))
        partial_flags.append(m08.partial_source_set_recovered(cands, e.required_ids))
        fp_counts.append(
            float(m08.false_positive_count(cands, e.required_ids, allowed=e.allowed_ids))
        )
        counts.append(float(m08.candidate_count(cands)))
        fractions.append(m08.candidate_fraction(cands, corpus_size))
        for kf in prov:
            prov[kf] += getattr(stats, kf)

    return {
        "evaluated_count": evaluated,
        "rank_metrics": "N/A (unordered evidence set, no score/rank)",
        "set_recall_mean": _mean(recalls),
        "set_precision_mean": _mean(precisions),
        "set_f1_mean": _mean(f1s),
        "full_source_set_recovered_rate": _rate(full_flags),
        "partial_source_set_recovered_rate": _rate(partial_flags),
        "false_positive_count_mean": _mean(fp_counts),
        "candidate_count": m08.breadth_distribution(counts).as_dict(),
        "candidate_fraction": m08.breadth_distribution(fractions).as_dict(),
        "provenance": prov,
        "interpretation_note": (
            "Read set_recall AND full_source_set_recovered_rate beside set_precision "
            "+ candidate_fraction: all recovery rates are breadth-inflatable, so high "
            "recovery with low precision and high candidate_fraction is broad coverage, "
            "not quality (task §66)."
        ),
    }


def _gd_extra_block(evals: Sequence[QueryEvaluation08]) -> Dict[str, object]:
    gds: List[GDEvidence] = [e.gd for e in evals if e.gd is not None]
    status_counts: Dict[str, int] = {}
    for gd in gds:
        status_counts[gd.status] = status_counts.get(gd.status, 0) + 1
    return {
        "status_counts": status_counts,
        # Endpoint invariant (/query/data forces only_need_context), NOT a measured
        # provider-call count. False by construction; surfaced so a reader can see the
        # invariant held for every GD call and never silently flipped.
        "final_answer_generation_invariant_holds": all(
            gd.final_answer_generation is False for gd in gds
        ),
        "entity_count_total": sum(gd.entity_count for gd in gds),
        "relationship_count_total": sum(gd.relationship_count for gd in gds),
        "chunk_count_total": sum(gd.chunk_count for gd in gds),
        "reference_count_total": sum(gd.reference_count for gd in gds),
    }


def _negatives_block(
    evals: Sequence[QueryEvaluation08], system: str, corpus_size: int
) -> Dict[str, object]:
    negatives = [e for e in evals if not e.answerable]
    abstained: List[bool] = []
    counts: List[float] = []
    fractions: List[float] = []
    for e in negatives:
        pair = _graph_pair(e, system)
        if pair is None:
            continue
        cands, _ = pair
        abstained.append(m08.abstained(cands))
        counts.append(float(m08.candidate_count(cands)))
        fractions.append(m08.candidate_fraction(cands, corpus_size))
    return {
        "count": len(negatives),
        "abstained_rate": _rate(abstained),
        "candidate_count": m08.breadth_distribution(counts).as_dict(),
        "candidate_fraction": m08.breadth_distribution(fractions).as_dict(),
        "note": (
            "Empty required set by construction. Non-empty output is NOT success "
            "(task §41); read abstained_rate + candidate breadth as false-confidence "
            "risk."
        ),
    }


def _complementarity_block(
    evals: Sequence[QueryEvaluation08], graph_system: str, ks: Sequence[int]
) -> Dict[str, object]:
    usable = [
        e
        for e in evals
        if e.answerable
        and e.vector_state == STATE_EVALUATED
        and e.vector is not None
        and _graph_pair(e, graph_system) is not None
    ]
    out: Dict[str, object] = {"paired_evaluated_count": len(usable)}
    for k in ks:
        cells = []
        for e in usable:
            gset, _ = _graph_pair(e, graph_system)  # type: ignore[misc]
            cells.append(
                m08.complementarity_full(e.vector.source_ids, gset, e.required_ids, k)  # type: ignore[union-attr]
            )
        out[f"k={k}"] = {
            "both_full": sum(1 for c in cells if c.both_full),
            "vector_only_full": sum(1 for c in cells if c.vector_only_full),
            "graph_only_full": sum(1 for c in cells if c.graph_only_full),
            "both_fail_full": sum(1 for c in cells if c.both_fail_full),
            "oracle_union_full_rate": _rate([c.oracle_union_full for c in cells]),
            "oracle_note": "upper bound only; NOT RRF/fusion validation (task §69)",
        }
    return out


def _parity_block(evals: Sequence[QueryEvaluation08]) -> Dict[str, object]:
    equal = 0
    gq_only = 0
    gd_only = 0
    paired = 0
    for e in evals:
        gq = _graph_pair(e, GQ_SYSTEM)
        gd = _graph_pair(e, GD_SYSTEM)
        if gq is None or gd is None:
            continue
        paired += 1
        gqs, _ = gq
        gds, _ = gd
        if gqs == gds:
            equal += 1
        gq_only += len(gqs - gds)
        gd_only += len(gds - gqs)
    return {
        "paired_count": paired,
        "gq_eq_gd_count": equal,
        "gq_only_total": gq_only,
        "gd_only_total": gd_only,
        "note": (
            "GraphRAG-06 predicts retrieval-semantic parity; measured here, not "
            "assumed (task §38/§71)."
        ),
    }


def _latency_block(evals: Sequence[QueryEvaluation08]) -> Dict[str, object]:
    def p(vals: List[int], pct: float) -> Optional[float]:
        if not vals:
            return None
        return m08.breadth_distribution([float(v) for v in vals]).as_dict()[
            "median" if pct == 50 else "p95"
        ]

    v = [e.vector_latency_ms for e in evals if e.vector_latency_ms is not None]
    gq = [e.gq_latency_ms for e in evals if e.gq_latency_ms is not None]
    gd = [e.gd_latency_ms for e in evals if e.gd_latency_ms is not None]
    return {
        VECTOR_SYSTEM: {"p50_ms": p(v, 50), "p95_ms": p(v, 95), "n": len(v)},
        GQ_SYSTEM: {"p50_ms": p(gq, 50), "p95_ms": p(gq, 95), "n": len(gq)},
        GD_SYSTEM: {"p50_ms": p(gd, 50), "p95_ms": p(gd, 95), "n": len(gd)},
        "note": "measurement-plumbing validation only; not a runtime-value verdict (task §76)",
    }


@dataclass(frozen=True)
class ReportInputs:
    evaluations: Sequence[QueryEvaluation08]
    corpus_size: int
    ks: Sequence[int] = DEFAULT_K_BUDGETS


def _metrics_block(
    evals: Sequence[QueryEvaluation08], cs: int, ks: Sequence[int]
) -> Dict[str, object]:
    """Full metric block for a set of queries (all / a split / a class)."""
    return {
        "n_queries": len(evals),
        "n_answerable": sum(1 for e in evals if e.answerable),
        "n_negative": sum(1 for e in evals if not e.answerable),
        VECTOR_SYSTEM: _vector_block(evals, ks),
        GQ_SYSTEM: _graph_block(evals, GQ_SYSTEM, cs),
        GD_SYSTEM: {
            **_graph_block(evals, GD_SYSTEM, cs),
            "gd_diagnostics": _gd_extra_block(evals),
        },
        "negatives": {
            GQ_SYSTEM: _negatives_block(evals, GQ_SYSTEM, cs),
            GD_SYSTEM: _negatives_block(evals, GD_SYSTEM, cs),
        },
        "complementarity": {
            "vector_vs_gq": _complementarity_block(evals, GQ_SYSTEM, ks),
            "vector_vs_gd": _complementarity_block(evals, GD_SYSTEM, ks),
        },
        "gq_gd_parity": _parity_block(evals),
        "latency": _latency_block(evals),
    }


def summarize(inputs: ReportInputs) -> Dict[str, object]:
    evals = inputs.evaluations
    cs = inputs.corpus_size
    ks = inputs.ks
    splits = sorted({e.split for e in evals})
    classes = sorted({e.query_class for e in evals})
    return {
        "k_budgets": list(ks),
        "candidate_fraction_denominator": cs,
        # HOLDOUT is authoritative for value conclusions (task §41); DEV is
        # execution-correctness only. Both are reported; the value decision reads
        # by_split["holdout"].
        "overall": _metrics_block(evals, cs, ks),
        "by_split": {
            s: _metrics_block([e for e in evals if e.split == s], cs, ks)
            for s in splits
        },
        "by_class": {
            c: _metrics_block([e for e in evals if e.query_class == c], cs, ks)
            for c in classes
        },
    }


def _per_query(evals: Sequence[QueryEvaluation08]) -> List[Dict[str, object]]:
    out: List[Dict[str, object]] = []
    for e in evals:
        gd_prov = e.gd.retrieval.stats if e.gd is not None else None
        out.append(
            {
                "query_id": e.query_id,
                "query_class": e.query_class,
                "split": e.split,
                "answerable": e.answerable,
                "n_required": len(e.required_ids),
                "vector_state": e.vector_state,
                "gq_state": e.gq_state,
                "gd_state": e.gd_state,
                "vector_candidate_ids": list(e.vector.source_ids) if e.vector else [],
                "gq_candidate_ids": list(e.gq.source_ids) if e.gq else [],
                "gd_candidate_ids": list(e.gd.source_ids) if e.gd else [],
                "gq_provenance": _prov_dict(e.gq.stats) if e.gq else None,
                "gd_provenance": _prov_dict(gd_prov) if gd_prov else None,
            }
        )
    return out


def _prov_dict(stats: ProvenanceStats) -> Dict[str, int]:
    return {
        "total": stats.total,
        "valid_unique": stats.valid_unique,
        "duplicates": stats.duplicates,
        "malformed": stats.malformed,
        "foreign": stats.foreign,
        "off_benchmark": stats.off_benchmark,
    }


def build_artifact(
    run_metadata: Dict[str, object],
    evaluations: Sequence[QueryEvaluation08],
    corpus_size: int,
    ks: Sequence[int] = DEFAULT_K_BUDGETS,
) -> Dict[str, object]:
    """Assemble the content-free artifact (ids/metrics/counts/latencies only)."""
    return {
        "metadata": {
            "run_type": "MICRO_PRECHECK",
            "value_run": False,
            "holdout_used": False,
            "full_benchmark_executed": False,
            **run_metadata,
        },
        "summary": summarize(ReportInputs(evaluations, corpus_size, ks)),
        "per_query": _per_query(evaluations),
    }


def write_artifact(path: Path, artifact: Dict[str, object]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        json.dump(artifact, fh, indent=2, ensure_ascii=False, sort_keys=True)
    return path


__all__ = [
    "VECTOR_SYSTEM",
    "GQ_SYSTEM",
    "GD_SYSTEM",
    "ReportInputs",
    "summarize",
    "build_artifact",
    "write_artifact",
]
