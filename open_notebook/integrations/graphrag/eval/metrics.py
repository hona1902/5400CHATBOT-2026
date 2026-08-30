"""Source-level retrieval metrics (task §18-§21).

Atomic, pure, and hand-checkable. Two families, used only where their assumptions
hold:

  RANKED (vector): hit_at_k, recall_at_k, mrr — require a genuine relevance rank.
  SET  (graph):    set_hit, set_recall — for an unordered candidate set.

Every function that needs graded/rank truth RAISES on an empty relevant set rather
than returning 0, so a negative (unanswerable) query can never be silently scored
as a miss (task §23, §45.32). The caller decides "N/A", not the metric.

Complementarity (task §20) compares the two retrievers at a candidate budget K and
computes the offline ORACLE_UNION upper bound — a set union, NOT a fused ranker.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import AbstractSet, Sequence


def _require_relevant(relevant: AbstractSet[str]) -> None:
    if not relevant:
        raise ValueError(
            "metric is undefined for a query with no relevant sources "
            "(negative control) — treat as N/A, do not score 0"
        )


def hit_at_k(ranked: Sequence[str], relevant: AbstractSet[str], k: int) -> bool:
    """True if any relevant source appears in the top-k of a RANKED list."""
    _require_relevant(relevant)
    if k <= 0:
        return False
    return any(sid in relevant for sid in ranked[:k])


def recall_at_k(ranked: Sequence[str], relevant: AbstractSet[str], k: int) -> float:
    """Fraction of relevant sources present in the top-k of a RANKED list."""
    _require_relevant(relevant)
    if k <= 0:
        return 0.0
    found = {sid for sid in ranked[:k] if sid in relevant}
    return len(found) / len(relevant)


def mrr(ranked: Sequence[str], relevant: AbstractSet[str]) -> float:
    """Reciprocal rank of the FIRST relevant source (0.0 if none). Rank starts 1."""
    _require_relevant(relevant)
    for index, sid in enumerate(ranked, start=1):
        if sid in relevant:
            return 1.0 / index
    return 0.0


def set_hit(candidates: AbstractSet[str], relevant: AbstractSet[str]) -> bool:
    """True if the unordered candidate set contains any relevant source."""
    _require_relevant(relevant)
    return bool(candidates & relevant)


def set_recall(candidates: AbstractSet[str], relevant: AbstractSet[str]) -> float:
    """Fraction of relevant sources present in the unordered candidate set."""
    _require_relevant(relevant)
    return len(candidates & relevant) / len(relevant)


@dataclass(frozen=True)
class ComplementarityCell:
    """Per-query complementarity at a candidate budget K (task §20)."""

    k: int
    vector_hit: bool
    graph_hit: bool
    both_hit: bool
    vector_only_hit: bool
    graph_only_hit: bool
    both_miss: bool
    oracle_union_hit: bool
    oracle_union_recall: float


def complementarity(
    vector_ranked: Sequence[str],
    graph_set: AbstractSet[str],
    relevant: AbstractSet[str],
    k: int,
) -> ComplementarityCell:
    """Compare vector top-K vs the graph candidate set for one answerable query.

    Budget asymmetry is deliberate and documented (task §28): vector has an
    explicit rank so it is truncated to top-K; the graph provenance set has no
    honest rank/K, so its full set is used. ORACLE_UNION is the set union of the
    two candidate sets — an offline upper bound, never a produced hybrid ranker.
    """
    _require_relevant(relevant)
    v_hit = hit_at_k(vector_ranked, relevant, k)
    g_hit = set_hit(graph_set, relevant)
    union = set(vector_ranked[:k]) | set(graph_set)
    oracle_recall = len(union & set(relevant)) / len(relevant)
    return ComplementarityCell(
        k=k,
        vector_hit=v_hit,
        graph_hit=g_hit,
        both_hit=v_hit and g_hit,
        vector_only_hit=v_hit and not g_hit,
        graph_only_hit=g_hit and not v_hit,
        both_miss=not v_hit and not g_hit,
        oracle_union_hit=v_hit or g_hit,
        oracle_union_recall=oracle_recall,
    )
