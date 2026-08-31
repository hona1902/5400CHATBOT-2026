"""GraphRAG-08 unordered-set + breadth metrics (task §40-§44, §60, §63-§65).

EVALUATION-ONLY. Additive to the frozen GraphRAG-04 ``metrics.py`` (which stays
untouched and still provides the RANKED vector metrics hit_at_k / recall_at_k /
mrr, reused here). This module adds the GraphRAG-08 graph SET metrics, candidate
breadth, multi-source full/partial recovery, negative metrics, and FULL-recovery
complementarity.

Two invariants baked in structurally:

  * NO RANK / NO SCORE for graph systems. Every graph metric takes an
    ``AbstractSet`` of candidate source ids, so candidate ORDER cannot affect any
    result — permuting the graph candidates is a no-op by construction (task §53).
  * Broad coverage is penalised, not rewarded. ``set_precision``,
    ``candidate_fraction`` and ``false_positive_count`` fall as the candidate set
    grows, so "return most of the corpus" yields high recall but low precision and
    high fraction (task §54/§65) — never a quality win.

Negatives (empty required set) never get recall/precision/f1: those raise, exactly
as in GraphRAG-04. Negatives use ``abstained`` / ``candidate_count`` /
``candidate_fraction`` / ``false_positive_count`` instead (task §41).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import AbstractSet, Dict, Optional, Sequence

from open_notebook.integrations.graphrag.eval.metrics import hit_at_k


def _require_required(required: AbstractSet[str]) -> None:
    if not required:
        raise ValueError(
            "graph set metric is undefined for a query with no required sources "
            "(negative control) — use the negative metrics, do not score 0"
        )


# -- unordered graph SET metrics (task §60) ---------------------------------

def set_recall(candidates: AbstractSet[str], required: AbstractSet[str]) -> float:
    """|required ∩ candidates| / |required|. Requires a non-empty required set."""
    _require_required(required)
    return len(candidates & required) / len(required)


def set_precision(
    candidates: AbstractSet[str], required: AbstractSet[str]
) -> Optional[float]:
    """|required ∩ candidates| / |candidates|.

    Defined only when |candidates| > 0 (returns None otherwise — an abstaining
    system has undefined precision, NOT 0). Requires a non-empty required set.
    """
    _require_required(required)
    if not candidates:
        return None
    return len(candidates & required) / len(candidates)


def set_f1(
    candidates: AbstractSet[str], required: AbstractSet[str]
) -> Optional[float]:
    """Harmonic mean of set_precision and set_recall (None if precision is None)."""
    _require_required(required)
    precision = set_precision(candidates, required)
    if precision is None:
        return None
    recall = set_recall(candidates, required)
    if precision + recall == 0:
        return 0.0
    return 2 * precision * recall / (precision + recall)


def candidate_count(candidates: AbstractSet[str]) -> int:
    """Number of distinct benchmark source candidates (order-free)."""
    return len(candidates)


def candidate_fraction(candidates: AbstractSet[str], corpus_size: int) -> float:
    """|candidates| / corpus_size — the direct 'fraction of the corpus returned'.

    corpus_size is the BENCHMARK corpus size (the denominator that exposes the
    GraphRAG-04 '11 of 14' broad-set behaviour; task §65), never the whole DB.
    """
    if corpus_size <= 0:
        raise ValueError("corpus_size must be > 0")
    return len(candidates) / corpus_size


def false_positive_count(
    candidates: AbstractSet[str],
    required: AbstractSet[str],
    *,
    allowed: Optional[AbstractSet[str]] = None,
) -> int:
    """Candidates that are neither required nor allowed-optional.

    ``allowed`` should be required ∪ optional_support so an optional-support
    Source is never counted as a false positive (task §17). For a negative query
    (required and allowed both empty) this equals candidate_count.
    """
    allow = set(allowed) if allowed is not None else set(required)
    allow |= set(required)
    return len(set(candidates) - allow)


def full_source_set_recovered(
    candidates: AbstractSet[str], required: AbstractSet[str]
) -> bool:
    """True iff the ENTIRE required set is present (task §62). Requires |required|>0."""
    _require_required(required)
    return required <= candidates


def partial_source_set_recovered(
    candidates: AbstractSet[str], required: AbstractSet[str]
) -> bool:
    """True iff SOME but not ALL of the required set is present (task §62)."""
    _require_required(required)
    hit = bool(required & candidates)
    return hit and not (required <= candidates)


# -- negative-query metrics (task §41) --------------------------------------

def abstained(candidates: AbstractSet[str]) -> bool:
    """True iff the system returned zero candidates."""
    return not candidates


# -- candidate breadth distribution (task §42, §26) -------------------------

def _percentile(sorted_vals: Sequence[float], pct: float) -> float:
    """Nearest-rank percentile (deterministic, no numpy). pct in [0,100]."""
    if not sorted_vals:
        raise ValueError("percentile of empty sequence")
    if pct <= 0:
        return sorted_vals[0]
    if pct >= 100:
        return sorted_vals[-1]
    # nearest-rank: ceil(pct/100 * n) -> 1-indexed
    import math

    rank = math.ceil(pct / 100.0 * len(sorted_vals))
    rank = max(1, min(rank, len(sorted_vals)))
    return sorted_vals[rank - 1]


@dataclass(frozen=True)
class BreadthDistribution:
    n: int
    mean: Optional[float]
    median: Optional[float]
    p75: Optional[float]
    p90: Optional[float]
    p95: Optional[float]
    max: Optional[float]

    def as_dict(self) -> Dict[str, Optional[float]]:
        return {
            "n": self.n,
            "mean": self.mean,
            "median": self.median,
            "p75": self.p75,
            "p90": self.p90,
            "p95": self.p95,
            "max": self.max,
        }


def breadth_distribution(values: Sequence[float]) -> BreadthDistribution:
    """Distribution of a breadth measure (candidate_count or candidate_fraction).

    A single average hides the broad-set pathology, so report the full spread
    (task §26/§42).
    """
    vals = sorted(float(v) for v in values)
    n = len(vals)
    if n == 0:
        return BreadthDistribution(0, None, None, None, None, None, None)

    def r(x: float) -> float:
        return round(x, 6)

    return BreadthDistribution(
        n=n,
        mean=r(sum(vals) / n),
        median=r(_percentile(vals, 50)),
        p75=r(_percentile(vals, 75)),
        p90=r(_percentile(vals, 90)),
        p95=r(_percentile(vals, 95)),
        max=r(vals[-1]),
    )


# -- FULL-recovery complementarity + oracle union (task §43, §63, §64) ------

@dataclass(frozen=True)
class ComplementarityFullCell:
    """Per-answerable-query FULL-set complementarity at vector budget K.

    Vector has a genuine rank so it is truncated to top-K; the graph set is
    unordered so its full set is used (deliberate, documented budget asymmetry —
    task §43). Success is FULL required-set recovery (task §62), kept strictly
    separate from any 'partial' or 'any-hit' notion.
    """

    k: int
    vector_full: bool
    graph_full: bool
    both_full: bool
    vector_only_full: bool
    graph_only_full: bool
    both_fail_full: bool
    oracle_union_full: bool


def complementarity_full(
    vector_ranked: Sequence[str],
    graph_set: AbstractSet[str],
    required: AbstractSet[str],
    k: int,
) -> ComplementarityFullCell:
    """FULL-recovery complementarity for one answerable query at budget K.

    ORACLE_UNION_FULL asks only whether the union of vector top-K and the graph
    set would contain the ENTIRE required set — an offline upper bound, never a
    produced fused ranker and never an RRF signal (task §44/§69).
    """
    _require_required(required)
    v_top = set(vector_ranked[:k]) if k > 0 else set()
    v_full = required <= v_top
    g_full = required <= set(graph_set)
    union_full = required <= (v_top | set(graph_set))
    return ComplementarityFullCell(
        k=k,
        vector_full=v_full,
        graph_full=g_full,
        both_full=v_full and g_full,
        vector_only_full=v_full and not g_full,
        graph_only_full=g_full and not v_full,
        both_fail_full=not v_full and not g_full,
        oracle_union_full=union_full,
    )


def vector_full_recovered(
    vector_ranked: Sequence[str], required: AbstractSet[str], k: int
) -> bool:
    """True iff the entire required set is in the vector top-K (ranked system)."""
    _require_required(required)
    # hit_at_k proves >=1; FULL requires the whole set inside the budget.
    _ = hit_at_k  # keep the ranked-metric import meaningful/co-located
    return required <= set(vector_ranked[:k]) if k > 0 else False


__all__ = [
    "set_recall",
    "set_precision",
    "set_f1",
    "candidate_count",
    "candidate_fraction",
    "false_positive_count",
    "full_source_set_recovered",
    "partial_source_set_recovered",
    "abstained",
    "breadth_distribution",
    "BreadthDistribution",
    "complementarity_full",
    "ComplementarityFullCell",
    "vector_full_recovered",
]
