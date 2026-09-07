"""Frozen deterministic QA-V+GD evidence combination (PN02A §10b / task §31).

EVALUATION-ONLY. Nothing in production imports this. This is the ONLY combination
rule the QA-V+GD arm uses; it is frozen BEFORE any live result and contains NO
RRF, NO graph rank, NO graph score, and NO post-result tuning (PN02A §10b, §17e).

Rule (verbatim, PN02A §10b):

    evidence = [ ordered V(K=5) member Sources ]
            ++ [ GD member Sources NOT already in V(K=5), sorted by canonical
                 source_id (NOT any score) ]
    cap: |evidence| <= K5_CAP = 8   (= notebook size)
    hard filter: every admitted Source is a current member of the queried
                 notebook (drop any non-member)

If this rule is judged indefensible in review, QA-V+GD is OMITTED rather than
re-tuned (PN02A §10b).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import AbstractSet, List, Sequence, Tuple

from open_notebook.integrations.graphrag.eval.datasetpn02 import (
    FROZEN_SOURCES_PER_NOTEBOOK,
)

K5_CAP = FROZEN_SOURCES_PER_NOTEBOOK  # 8 = notebook size (PN02A §10b)


@dataclass(frozen=True)
class CombinedEvidence:
    """Result of the frozen V+GD combination.

    ``ordered_source_ids`` preserves the V(K=5) rank for the V portion; the GD
    additions are appended in canonical-id order (a stable NON-relevance order —
    no pseudo-rank is invented). ``gd_added`` are the GD member Sources not
    already in V5. ``dropped_non_members`` records any non-member the hard filter
    excluded (leakage defense).
    """

    ordered_source_ids: Tuple[str, ...]
    v5_member_portion: Tuple[str, ...]
    gd_added: Tuple[str, ...]
    dropped_non_members: Tuple[str, ...]
    capped: bool

    def as_set(self) -> frozenset[str]:
        return frozenset(self.ordered_source_ids)


def combine_v_gd(
    v5_ranked: Sequence[str],
    gd_set: AbstractSet[str],
    members: AbstractSet[str],
    *,
    cap: int = K5_CAP,
) -> CombinedEvidence:
    """Apply the frozen QA-V+GD combination rule (PN02A §10b).

    ``v5_ranked`` is the ranked (best-first) K=5 vector candidate list; ``gd_set``
    is the UNORDERED GD candidate set; ``members`` are the current members of the
    queried notebook. Non-members are dropped (hard filter); duplicates removed;
    GD additions ordered by canonical id; total capped at ``cap``.
    """
    member_set = frozenset(members)
    dropped: List[str] = []

    # 1) V(K=5) member portion, order-preserving + deduped.
    v_portion: List[str] = []
    v_seen: set[str] = set()
    for sid in v5_ranked:
        if sid in v_seen:
            continue
        v_seen.add(sid)
        if sid not in member_set:
            dropped.append(sid)
            continue
        v_portion.append(sid)

    # 2) GD member additions not already in V5, sorted by canonical id.
    gd_added: List[str] = []
    for sid in sorted(gd_set):
        if sid in v_seen or sid in gd_added:
            continue
        if sid not in member_set:
            if sid not in dropped:
                dropped.append(sid)
            continue
        gd_added.append(sid)

    combined = v_portion + gd_added
    capped = len(combined) > cap
    if capped:
        combined = combined[:cap]

    return CombinedEvidence(
        ordered_source_ids=tuple(combined),
        v5_member_portion=tuple(v_portion),
        gd_added=tuple(gd_added),
        dropped_non_members=tuple(dropped),
        capped=capped,
    )


__all__ = ["K5_CAP", "CombinedEvidence", "combine_v_gd"]
