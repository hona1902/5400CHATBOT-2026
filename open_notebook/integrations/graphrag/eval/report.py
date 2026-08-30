"""Aggregate per-query outcomes into an honest, content-free evaluation report.

Joins each benchmark query with the two retrievers' normalized output and rolls
them up (overall, by split, by class) using only metrics whose assumptions hold:
vector gets ranked metrics, graph gets set metrics, and negatives are reported
with candidate counts only (task §18-§24). Every query carries an explicit
evaluation state so errors/skips stay in the accounting and are never silently
dropped from denominators (task §30).

The emitted artifact contains ONLY ids, classes, source ids, metric values,
counts, and non-secret metadata — never retrieved chunks, answers, or credentials
(task §31, §32). That is a structural property of what this module writes: it
never receives document text or the generated answer.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, FrozenSet, List, Optional, Sequence, Tuple

from open_notebook.integrations.graphrag.eval import (
    GRAPHRAG_BASELINE,
    VECTOR_BASELINE,
)
from open_notebook.integrations.graphrag.eval.metrics import (
    complementarity,
    hit_at_k,
    mrr,
    recall_at_k,
    set_hit,
    set_recall,
)
from open_notebook.integrations.graphrag.eval.normalize import NormalizedRetrieval

DEFAULT_K_BUDGETS = (1, 3, 5)


class EvalState:
    """Explicit per-query evaluation states (task §30)."""

    EVALUATED = "evaluated"
    RETRIEVER_ERROR = "retriever_error"
    TIMEOUT = "timeout"
    INVALID_PROVENANCE = "invalid_provenance"
    UNSUPPORTED_METRIC = "unsupported_metric"
    SKIPPED = "skipped_with_reason"


@dataclass(frozen=True)
class QueryEvaluation:
    """One query joined with both retrievers' normalized (content-free) output."""

    query_id: str
    query_class: str
    split: str
    is_negative: bool
    relevant_ids: FrozenSet[str]
    vector_state: str
    graph_state: str
    vector: Optional[NormalizedRetrieval] = None
    graph: Optional[NormalizedRetrieval] = None
    vector_detail: str = ""
    graph_detail: str = ""


def _mean(values: Sequence[float]) -> Optional[float]:
    return round(sum(values) / len(values), 6) if values else None


def _rate(flags: Sequence[bool]) -> Optional[float]:
    return round(sum(1 for f in flags if f) / len(flags), 6) if flags else None


def _counts_by_state(states: Sequence[str]) -> Dict[str, int]:
    out: Dict[str, int] = {}
    for state in states:
        out[state] = out.get(state, 0) + 1
    return out


def _vector_usable(
    evals: Sequence[QueryEvaluation],
) -> List[Tuple[QueryEvaluation, NormalizedRetrieval]]:
    return [
        (e, e.vector)
        for e in evals
        if not e.is_negative
        and e.vector_state == EvalState.EVALUATED
        and e.vector is not None
    ]


def _graph_usable(
    evals: Sequence[QueryEvaluation],
) -> List[Tuple[QueryEvaluation, NormalizedRetrieval]]:
    return [
        (e, e.graph)
        for e in evals
        if not e.is_negative
        and e.graph_state == EvalState.EVALUATED
        and e.graph is not None
    ]


def _vector_block(evals: Sequence[QueryEvaluation], ks: Sequence[int]) -> Dict[str, object]:
    answerable = [e for e in evals if not e.is_negative]
    usable = _vector_usable(evals)
    block: Dict[str, object] = {
        "evaluated_count": len(usable),
        "state_counts": _counts_by_state([e.vector_state for e in answerable]),
    }
    for k in ks:
        block[f"hit@{k}"] = _rate([hit_at_k(v.source_ids, e.relevant_ids, k) for e, v in usable])
        block[f"recall@{k}"] = _mean(
            [recall_at_k(v.source_ids, e.relevant_ids, k) for e, v in usable]
        )
    block["mrr"] = _mean([mrr(v.source_ids, e.relevant_ids) for e, v in usable])
    return block


def _graph_block(evals: Sequence[QueryEvaluation]) -> Dict[str, object]:
    answerable = [e for e in evals if not e.is_negative]
    usable = _graph_usable(evals)
    return {
        "evaluated_count": len(usable),
        "state_counts": _counts_by_state([e.graph_state for e in answerable]),
        # Rank metrics (MRR/nDCG) are intentionally absent: the reference set is
        # unordered and carries no score (task §8/§18/§50).
        "rank_metrics": "N/A (unordered provenance set, no score/rank)",
        # source_hit_rate/recall are computed over the FULL unordered candidate
        # set. Read them next to the provenance volume below: a high hit rate on
        # a set that returns most of the corpus is candidate coverage, not
        # precision. There is deliberately no Precision@K (no honest rank/K).
        "metric_note": (
            "source_hit_rate/recall are over the full unordered candidate set; "
            "interpret beside valid_unique_total (broad coverage != precision)"
        ),
        "source_hit_rate": _rate([set_hit(g.as_set(), e.relevant_ids) for e, g in usable]),
        "mean_source_recall": _mean(
            [set_recall(g.as_set(), e.relevant_ids) for e, g in usable]
        ),
        "provenance": {
            "raw_candidates_total": sum(g.stats.total for _, g in usable),
            "valid_unique_total": sum(g.stats.valid_unique for _, g in usable),
            "duplicates_total": sum(g.stats.duplicates for _, g in usable),
            "malformed_total": sum(g.stats.malformed for _, g in usable),
            "foreign_total": sum(g.stats.foreign for _, g in usable),
            "off_benchmark_total": sum(g.stats.off_benchmark for _, g in usable),
        },
    }


def _complementarity_block(
    evals: Sequence[QueryEvaluation], ks: Sequence[int]
) -> Dict[str, object]:
    # Both retrievers must have evaluated an answerable query for a fair cell.
    usable = [
        (e, e.vector, e.graph)
        for e in evals
        if not e.is_negative
        and e.vector_state == EvalState.EVALUATED and e.vector is not None
        and e.graph_state == EvalState.EVALUATED and e.graph is not None
    ]
    out: Dict[str, object] = {"paired_evaluated_count": len(usable)}
    for k in ks:
        cells = [
            complementarity(v.source_ids, g.as_set(), e.relevant_ids, k)
            for e, v, g in usable
        ]
        out[f"k={k}"] = {
            "both_hit": sum(1 for c in cells if c.both_hit),
            "vector_only_hit": sum(1 for c in cells if c.vector_only_hit),
            "graph_only_hit": sum(1 for c in cells if c.graph_only_hit),
            "both_miss": sum(1 for c in cells if c.both_miss),
            "oracle_union_hit_rate": _rate([c.oracle_union_hit for c in cells]),
            "mean_oracle_union_recall": _mean([c.oracle_union_recall for c in cells]),
        }
    return out


def _negative_block(evals: Sequence[QueryEvaluation]) -> Dict[str, object]:
    negatives = [e for e in evals if e.is_negative]
    v_usable = [e.vector for e in negatives if e.vector is not None]
    g_usable = [e.graph for e in negatives if e.graph is not None]
    return {
        "count": len(negatives),
        "note": (
            "No benchmark evidence exists by definition; nearest-neighbour "
            "retrieval may still return candidates. No precision metric is "
            "reported (no abstention contract). Candidate counts indicate "
            "false-confidence risk (task §23)."
        ),
        "vector_mean_candidates": _mean([float(len(v.source_ids)) for v in v_usable]),
        "vector_any_returned_rate": _rate([bool(v.source_ids) for v in v_usable]),
        "graph_mean_candidates": _mean([float(len(g.source_ids)) for g in g_usable]),
        "graph_any_returned_rate": _rate([bool(g.source_ids) for g in g_usable]),
    }


def metrics_block(
    evals: Sequence[QueryEvaluation], ks: Sequence[int] = DEFAULT_K_BUDGETS
) -> Dict[str, object]:
    """Full metric block for a set of queries (all / a split / a class)."""
    return {
        "n_queries": len(evals),
        "n_answerable": sum(1 for e in evals if not e.is_negative),
        "n_negative": sum(1 for e in evals if e.is_negative),
        VECTOR_BASELINE: _vector_block(evals, ks),
        GRAPHRAG_BASELINE: _graph_block(evals),
        "complementarity": _complementarity_block(evals, ks),
        "negatives": _negative_block(evals),
    }


def summarize(
    evaluations: Sequence[QueryEvaluation],
    ks: Sequence[int] = DEFAULT_K_BUDGETS,
) -> Dict[str, object]:
    """Aggregate overall, by split, and by query class."""
    classes = sorted({e.query_class for e in evaluations})
    splits = sorted({e.split for e in evaluations})
    return {
        "k_budgets": list(ks),
        "overall": metrics_block(evaluations, ks),
        "by_split": {
            s: metrics_block([e for e in evaluations if e.split == s], ks)
            for s in splits
        },
        "by_class": {
            c: metrics_block([e for e in evaluations if e.query_class == c], ks)
            for c in classes
        },
    }


def build_artifact(
    run_metadata: Dict[str, object],
    evaluations: Sequence[QueryEvaluation],
    ks: Sequence[int] = DEFAULT_K_BUDGETS,
) -> Dict[str, object]:
    """Assemble the machine-readable result (ids/metrics/counts only)."""
    per_query = [
        {
            "query_id": e.query_id,
            "query_class": e.query_class,
            "split": e.split,
            "is_negative": e.is_negative,
            "n_relevant": len(e.relevant_ids),
            "vector_state": e.vector_state,
            "graph_state": e.graph_state,
            "vector_candidate_ids": list(e.vector.source_ids) if e.vector else [],
            "graph_candidate_ids": list(e.graph.source_ids) if e.graph else [],
            "graph_provenance": (
                {
                    "total": e.graph.stats.total,
                    "valid_unique": e.graph.stats.valid_unique,
                    "duplicates": e.graph.stats.duplicates,
                    "malformed": e.graph.stats.malformed,
                    "foreign": e.graph.stats.foreign,
                    "off_benchmark": e.graph.stats.off_benchmark,
                }
                if e.graph
                else None
            ),
        }
        for e in evaluations
    ]
    return {
        "metadata": run_metadata,
        "summary": summarize(evaluations, ks),
        "per_query": per_query,
    }


def write_artifact(path: Path, artifact: Dict[str, object]) -> Path:
    """Write the artifact as JSON (creates parent dirs). Returns the path."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        json.dump(artifact, fh, indent=2, ensure_ascii=False, sort_keys=True)
    return path
