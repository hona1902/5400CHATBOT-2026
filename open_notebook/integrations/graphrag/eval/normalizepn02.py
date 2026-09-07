"""Normalize PN02 retriever output to canonical fixture Source identities.

EVALUATION-ONLY. Nothing in production imports this. Distinct from the GraphRAG-08
``normalize.py`` (which validates runtime ``source:`` RecordIDs): PN02 truth uses
logical fixture Source keys (``A1``, ``SH_AB``), so identity is validated against
the fixture's canonical allowlist (task §16).

Provenance vocabulary (reused from GraphRAG-07 principles, task §16):

  * VALID     — a canonical fixture Source id (kept as a candidate).
  * MALFORMED — missing / empty id.
  * FOREIGN   — a well-formed id that is NOT a fixture Source (provenance from
                outside the fixture, e.g. a stale/foreign store id).

Non-member-but-valid Sources are **kept** here (they are the raw material of the
Stage-1 leakage gate — leakage is a MEMBERSHIP judgement made in the metrics
layer, not a provenance judgement). Vector output is normalized to a RANKED,
deduped list (``ordered=True``); graph (GD) output to an UNORDERED set
(``ordered=False``) — no rank/score is read or invented (PN02A §18,
``QUERY_DATA_EXPOSES_VALID_RANK/SCORE=NO``).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import AbstractSet, List, Optional, Sequence, Tuple


@dataclass(frozen=True)
class ProvenancePN02:
    """Accounting for how raw candidate ids mapped to canonical fixture Sources."""

    total: int          # raw candidate values seen
    valid_unique: int   # distinct canonical fixture source ids kept
    duplicates: int     # repeat occurrences of an already-seen id
    malformed: int      # missing / empty
    foreign: int        # well-formed but not a fixture Source

    @property
    def is_clean(self) -> bool:
        """No malformed and no foreign provenance."""
        return self.malformed == 0 and self.foreign == 0


@dataclass(frozen=True)
class NormalizedEvidencePN02:
    """Retriever output reduced to canonical fixture Source ids.

    ``ordered`` is True only when position is a genuine relevance rank (vector).
    For graph (GD) it is False: ``source_ids`` is a SET rendered in encounter
    order for reproducibility, and no rank metric may be computed from it.
    """

    source_ids: Tuple[str, ...]
    ordered: bool
    stats: ProvenancePN02

    def top_k(self, k: int) -> Tuple[str, ...]:
        if not self.ordered:
            raise ValueError(
                "top_k is undefined for an unordered (graph) evidence set — GD "
                "exposes no valid rank (PN02A §18)"
            )
        return self.source_ids[:k]

    def as_set(self) -> frozenset[str]:
        return frozenset(self.source_ids)


def _normalize(
    raw_values: Sequence[Optional[str]],
    *,
    ordered: bool,
    allowlist: AbstractSet[str],
) -> NormalizedEvidencePN02:
    kept: List[str] = []
    seen: set[str] = set()
    duplicates = 0
    malformed = 0
    foreign = 0

    for value in raw_values:
        candidate = value.strip() if isinstance(value, str) else None
        if not candidate:
            malformed += 1
            continue
        if candidate not in allowlist:
            foreign += 1
            continue
        if candidate in seen:
            duplicates += 1
            continue
        seen.add(candidate)
        kept.append(candidate)

    stats = ProvenancePN02(
        total=len(raw_values),
        valid_unique=len(kept),
        duplicates=duplicates,
        malformed=malformed,
        foreign=foreign,
    )
    return NormalizedEvidencePN02(source_ids=tuple(kept), ordered=ordered, stats=stats)


def normalize_vector(
    raw_ranked: Sequence[Optional[str]], *, allowlist: AbstractSet[str]
) -> NormalizedEvidencePN02:
    """Normalize a RANKED vector candidate list (best-first) to a deduped list.

    The first occurrence of a Source is kept so its best rank is preserved; K=3
    and K=5 are then SLICES of this single ranked list (PN02A §17, task §17), not
    separate retrievals.
    """
    return _normalize(raw_ranked, ordered=True, allowlist=allowlist)


def normalize_graph(
    raw_set: Sequence[Optional[str]], *, allowlist: AbstractSet[str]
) -> NormalizedEvidencePN02:
    """Normalize a GD candidate collection to an UNORDERED, deduped Source set.

    The upstream ``/query/data`` list has no score/rank, so this is a set
    (``ordered=False``) — no order is invented (PN02A §18, task §18).
    """
    return _normalize(raw_set, ordered=False, allowlist=allowlist)


__all__ = [
    "ProvenancePN02",
    "NormalizedEvidencePN02",
    "normalize_vector",
    "normalize_graph",
]
