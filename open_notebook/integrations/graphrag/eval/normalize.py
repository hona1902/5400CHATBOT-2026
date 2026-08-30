"""Normalize retriever output to canonical Source identities.

Both retrievers are compared at the canonical SOURCE level (task §10). Vector
rows and graph references are different shapes, so each is normalized here:

  VECTOR  chunk/source row --> parent_id --> canonical source id
          (dedup, PRESERVING the first/best-ranked occurrence -> ordered list)

  GRAPH   reference --> file_path(=source_id) --> canonical source id
          (dedup -> UNORDERED set; the upstream reference list carries no
          score/rank, so no order is manufactured — task §8/§50)

Identity is validated with the SAME structural RecordID helpers the production
GraphRAG boundary uses (open_notebook/integrations/graphrag/models), so numeric
vs string-numeric ids stay distinct (source:123 != source:⟨123⟩) and escaped ids
round-trip losslessly. Invalid, malformed, or foreign-table provenance is counted
separately and NEVER silently becomes a benchmark hit (task §10, §24).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import AbstractSet, Any, List, Mapping, Optional, Sequence, Tuple

from open_notebook.integrations.graphrag.models import (
    GraphRAGValidationError,
    record_id_for,
)

# The only table an indexed benchmark document can be. A candidate on any other
# table is valid-but-foreign for this benchmark, never a source hit.
_SOURCE_TABLES = frozenset({"source"})
# Tables that are legitimate GraphRAG provenance but are NOT indexable sources
# (fn::vector_search can surface these; we index only `source`). Kept distinct so
# a note/insight id is reported as foreign rather than mistaken for a source hit.
_OTHER_PROVENANCE_TABLES = frozenset({"note", "source_insight"})


@dataclass(frozen=True)
class ProvenanceStats:
    """Accounting for how raw candidates mapped to canonical sources."""

    total: int          # raw candidate values seen
    valid_unique: int   # distinct canonical source ids kept
    duplicates: int     # repeat occurrences of an already-seen source id
    malformed: int      # missing / not a structurally valid record id
    foreign: int        # valid record id but not an indexable `source`
    #: valid `source:` ids that are NOT in the benchmark allowlist (only counted
    #: when an allowlist is supplied; 0 otherwise). These are dropped, never kept
    #: as candidates, so a structurally-valid but wrong-namespace source can never
    #: be counted as a benchmark candidate.
    off_benchmark: int = 0


@dataclass(frozen=True)
class NormalizedRetrieval:
    """Retriever output reduced to canonical source ids.

    ``ordered`` is True only when the position of ``source_ids`` is a genuine
    relevance rank (vector). For graph it is False: ``source_ids`` is a SET
    rendered in encounter order for reproducibility, and rank metrics must not be
    computed from it.
    """

    source_ids: Tuple[str, ...]
    ordered: bool
    stats: ProvenanceStats

    def top_k(self, k: int) -> Tuple[str, ...]:
        return self.source_ids[:k]

    def as_set(self) -> frozenset[str]:
        return frozenset(self.source_ids)


def canonical_source_id(value: Optional[str]) -> Optional[str]:
    """Return the canonical serialized ``source:`` id, or None if not one.

    Lossless: numeric and string-numeric identities stay distinct and escaped
    ids are preserved, because validation goes through ``record_id_for`` (which
    builds the RecordID object) rather than re-parsing the presentation string.
    """
    if not value:
        return None
    try:
        return str(record_id_for(value, tables=_SOURCE_TABLES))
    except GraphRAGValidationError:
        return None


def _is_other_provenance(value: str) -> bool:
    try:
        record_id_for(value, tables=_OTHER_PROVENANCE_TABLES)
        return True
    except GraphRAGValidationError:
        return False


def _normalize(
    raw_values: Sequence[Optional[str]],
    *,
    ordered: bool,
    allowlist: Optional[AbstractSet[str]] = None,
) -> NormalizedRetrieval:
    """Normalize raw provenance values to canonical source ids.

    When ``allowlist`` is given, a structurally-valid ``source:`` id that is NOT
    in it is counted as ``off_benchmark`` and dropped — it never becomes a
    candidate. With no allowlist (the vector path), every valid source id is a
    legitimate candidate (non-benchmark sources are real ranked competitors).
    """
    kept: List[str] = []
    seen: set[str] = set()
    duplicates = 0
    malformed = 0
    foreign = 0
    off_benchmark = 0

    for value in raw_values:
        canonical = canonical_source_id(value)
        if canonical is None:
            if value and _is_other_provenance(value):
                foreign += 1
            else:
                malformed += 1
            continue
        if allowlist is not None and canonical not in allowlist:
            off_benchmark += 1
            continue
        if canonical in seen:
            duplicates += 1
            continue
        seen.add(canonical)
        kept.append(canonical)

    stats = ProvenanceStats(
        total=len(raw_values),
        valid_unique=len(kept),
        duplicates=duplicates,
        malformed=malformed,
        foreign=foreign,
        off_benchmark=off_benchmark,
    )
    return NormalizedRetrieval(source_ids=tuple(kept), ordered=ordered, stats=stats)


def normalize_vector_results(
    rows: Sequence[Mapping[str, Any]],
    *,
    parent_key: str = "parent_id",
) -> NormalizedRetrieval:
    """Normalize `vector_search` rows to a RANKED, deduped source list.

    Rows are assumed already ordered best-first (the query does
    ``ORDER BY similarity DESC``). Each row's ``parent_id`` is the canonical
    source id (source_embedding and source_insight rows both project
    ``source.id`` as parent_id). The first occurrence of a source is kept, so
    five chunks from one source cannot occupy five source-level slots and the
    best rank is preserved (task §25).
    """
    raw = [
        (str(row.get(parent_key)) if row.get(parent_key) is not None else None)
        for row in rows
    ]
    return _normalize(raw, ordered=True)


def normalize_graph_references(
    references: Sequence[Any],
    *,
    source_id_attr: str = "source_id",
    benchmark_ids: Optional[AbstractSet[str]] = None,
) -> NormalizedRetrieval:
    """Normalize GraphRAG references to an UNORDERED, deduped source set.

    Each reference exposes a ``source_id`` (recovered upstream from
    ReferenceItem.file_path). The upstream list has no score/rank, so this is a
    set (``ordered=False``) — no order is invented. Note/insight ids are counted
    as ``foreign`` and never as a source hit; missing/invalid ids as ``malformed``.

    ``benchmark_ids`` (canonical source ids created by this run) restricts the
    candidate set to benchmark sources: a structurally-valid ``source:`` id that
    a sidecar might hold from outside the benchmark is counted as ``off_benchmark``
    and excluded, so a wrong-namespace source can never inflate the candidate set.
    """
    raw: List[Optional[str]] = []
    for ref in references:
        value = getattr(ref, source_id_attr, None)
        if value is None and isinstance(ref, Mapping):
            value = ref.get(source_id_attr)
        raw.append(str(value) if value is not None else None)
    return _normalize(raw, ordered=False, allowlist=benchmark_ids)
