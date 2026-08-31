"""Load, validate, and freeze the GraphRAG-08 v1 synthetic benchmark.

EVALUATION-ONLY. Pure I/O over JSON fixtures — no retriever, no DB, no network,
no provider. Nothing in production imports this module (the dependency direction
is eval -> production only; task §86).

This is a NEW, separate loader from the frozen GraphRAG-04 ``dataset.py`` (which
stays untouched). GraphRAG-08 has a richer schema than 04:

  * 10 query classes (04 had 6);
  * explicit ``answerable`` flag and ``design_label``;
  * ``required_source_keys`` vs ``optional_support_source_keys`` (multi-source GT
    with a conservative required/optional split — task §17);
  * ``negative_construction`` taxonomy for the 12 negatives (task §15);
  * a ``rationale`` per query (review-only; NEVER sent to a retriever — task §16).

``load_benchmark08`` enforces the structural invariants; ``validate_frozen_shape``
additionally enforces the FROZEN counts (75 / 60 / 30 / 30 / 12; task §9/§24); and
``compute_integrity`` / ``verify_integrity`` provide the content-hash freeze so a
committed benchmark cannot silently drift once a provider-backed run has begun
(task §25/§85).
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Dict, FrozenSet, List, Optional, Tuple

BENCHMARK_VERSION = "graphrag_08_eval_v1"
NAMESPACE_TAG = "__graphrag08_eval_v1__"

# The frozen shape of graphrag_08_eval_v1 (task §9/§14/§15/§24). Any deviation is
# a design contradiction and must become graphrag_08_eval_v2, never a silent edit.
FROZEN_SOURCE_COUNT = 75
FROZEN_QUERY_COUNT = 60
FROZEN_DEV_COUNT = 30
FROZEN_HOLDOUT_COUNT = 30
FROZEN_NEGATIVE_COUNT = 12
FROZEN_QUERY_CLASS_COUNT = 10


class QueryClass08(str, Enum):
    DIRECT_LEXICAL = "direct_lexical"
    SEMANTIC_PARAPHRASE = "semantic_paraphrase"
    TWO_HOP = "two_hop"
    THREE_HOP_CROSS_SOURCE = "three_hop_cross_source"
    ENTITY_COLLISION = "entity_collision"
    RELATIONSHIP_COLLISION = "relationship_collision"
    DISTRACTOR_TERM_COLLISION = "distractor_term_collision"
    NEGATIVE_UNANSWERABLE = "negative_unanswerable"
    PARTIAL_EVIDENCE = "partial_evidence"
    BROAD_ENTITY_NAME_COLLISION = "broad_entity_name_collision"


#: The two classes whose queries are unanswerable (GT is the empty set).
NEGATIVE_CLASSES: FrozenSet[QueryClass08] = frozenset(
    {QueryClass08.NEGATIVE_UNANSWERABLE, QueryClass08.PARTIAL_EVIDENCE}
)


class Split(str, Enum):
    DEV = "dev"
    HOLDOUT = "holdout"


class DesignLabel(str, Enum):
    GRAPH_NATIVE = "graph_native"
    GRAPH_AUGMENTED = "graph_augmented"
    VECTOR_SOLVABLE = "vector_solvable"
    NEGATIVE = "negative"


class NegativeConstruction(str, Enum):
    NO_MATCH = "no_match"
    ENTITY_EXISTS_BUT_RELATION_DOES_NOT = "entity_exists_but_relation_does_not"
    RELATION_EXISTS_BUT_TARGET_DOES_NOT = "relation_exists_but_target_does_not"
    PLAUSIBLE_COMBINATION_NOT_IN_CORPUS = "plausible_combination_not_in_corpus"
    PARTIAL_FACT_ONLY = "partial_fact_only"
    CONTRADICTORY_RELATION = "contradictory_relation"


class Benchmark08Error(ValueError):
    """The GraphRAG-08 benchmark fixture violates a structural invariant."""


@dataclass(frozen=True)
class BenchmarkSource08:
    key: str
    title: str
    text: str


@dataclass(frozen=True)
class BenchmarkQuery08:
    query_id: str
    query_class: QueryClass08
    split: Split
    answerable: bool
    text: str
    #: Sources genuinely REQUIRED to answer. Empty iff the query is unanswerable.
    required_source_keys: Tuple[str, ...]
    #: Corroborating Sources that MAY appear but are never required and never a
    #: false positive (task §17).
    optional_support_source_keys: Tuple[str, ...]
    design_label: DesignLabel
    negative_construction: Optional[NegativeConstruction]
    #: Review-only justification. Never used by any retriever or metric.
    rationale: str

    @property
    def is_negative(self) -> bool:
        return not self.answerable


@dataclass(frozen=True)
class Benchmark08:
    version: str
    namespace_tag: str
    sources: Tuple[BenchmarkSource08, ...]
    queries: Tuple[BenchmarkQuery08, ...]

    @property
    def source_keys(self) -> FrozenSet[str]:
        return frozenset(s.key for s in self.sources)

    def queries_for_split(self, split: Split) -> Tuple[BenchmarkQuery08, ...]:
        return tuple(q for q in self.queries if q.split is split)

    def required_keys(self, query: BenchmarkQuery08) -> FrozenSet[str]:
        return frozenset(query.required_source_keys)


def default_fixture_dir() -> Path:
    """Repo-relative path to the committed benchmark fixture directory."""
    # .../eval/dataset08.py -> repo root is 5 parents up.
    repo_root = Path(__file__).resolve().parents[4]
    return repo_root / "tests" / "fixtures" / BENCHMARK_VERSION


def load_benchmark08(fixture_dir: Optional[Path] = None) -> Benchmark08:
    """Load, structurally validate, and return the GraphRAG-08 benchmark.

    Raises ``Benchmark08Error`` on any structural violation. Does NOT enforce the
    frozen counts — call ``validate_frozen_shape`` for that (kept separate so unit
    tests can build tiny fixtures without the 75/60 requirement).
    """
    fixture_dir = fixture_dir or default_fixture_dir()
    corpus_raw = _read_json(fixture_dir / "corpus.json")
    queries_raw = _read_json(fixture_dir / "queries.json")

    version = str(corpus_raw.get("benchmark_version") or "")
    if version != BENCHMARK_VERSION:
        raise Benchmark08Error(
            f"corpus benchmark_version {version!r} != {BENCHMARK_VERSION!r}"
        )
    if str(queries_raw.get("benchmark_version") or "") != BENCHMARK_VERSION:
        raise Benchmark08Error("queries benchmark_version mismatch")
    namespace_tag = str(corpus_raw.get("namespace_tag") or "")
    if namespace_tag != NAMESPACE_TAG:
        raise Benchmark08Error(
            f"corpus namespace_tag {namespace_tag!r} != {NAMESPACE_TAG!r}"
        )
    if str(queries_raw.get("namespace_tag") or "") != NAMESPACE_TAG:
        raise Benchmark08Error("queries namespace_tag mismatch")

    sources = _parse_sources(corpus_raw)
    source_keys = {s.key for s in sources}
    queries = _parse_queries(queries_raw, source_keys)

    return Benchmark08(
        version=version,
        namespace_tag=namespace_tag,
        sources=tuple(sources),
        queries=tuple(queries),
    )


def _read_json(path: Path) -> Dict[str, object]:
    if not path.exists():
        raise Benchmark08Error(f"benchmark file not found: {path}")
    with path.open("r", encoding="utf-8") as fh:
        data = json.load(fh)
    if not isinstance(data, dict):
        raise Benchmark08Error(f"{path.name} must be a JSON object")
    return data


def _parse_sources(corpus_raw: Dict[str, object]) -> List[BenchmarkSource08]:
    raw_sources = corpus_raw.get("sources")
    if not isinstance(raw_sources, list) or not raw_sources:
        raise Benchmark08Error("corpus.sources must be a non-empty list")

    sources: List[BenchmarkSource08] = []
    seen_keys: set[str] = set()
    for entry in raw_sources:
        if not isinstance(entry, dict):
            raise Benchmark08Error("each corpus source must be an object")
        key = str(entry.get("key") or "").strip()
        title = str(entry.get("title") or "").strip()
        text = str(entry.get("text") or "").strip()
        if not key:
            raise Benchmark08Error("a corpus source has an empty key")
        if key in seen_keys:
            raise Benchmark08Error(f"duplicate corpus source key: {key}")
        if not text:
            raise Benchmark08Error(f"corpus source {key} has empty text")
        # A runtime SurrealDB RecordID must never be baked into the fixture as
        # truth (task §8): logical keys only, no "source:" ids.
        if key.startswith("source:") or ":" in key:
            raise Benchmark08Error(
                f"corpus source key {key!r} looks like a runtime record id; "
                "use a logical benchmark key (e.g. S001)"
            )
        seen_keys.add(key)
        sources.append(BenchmarkSource08(key=key, title=title, text=text))
    return sources


def _parse_queries(
    queries_raw: Dict[str, object], source_keys: set[str]
) -> List[BenchmarkQuery08]:
    raw_queries = queries_raw.get("queries")
    if not isinstance(raw_queries, list) or not raw_queries:
        raise Benchmark08Error("queries.queries must be a non-empty list")

    queries: List[BenchmarkQuery08] = []
    seen_ids: set[str] = set()
    for entry in raw_queries:
        if not isinstance(entry, dict):
            raise Benchmark08Error("each query must be an object")
        query_id = str(entry.get("query_id") or "").strip()
        if not query_id:
            raise Benchmark08Error("a query has an empty query_id")
        if query_id in seen_ids:
            raise Benchmark08Error(f"duplicate query_id: {query_id}")
        seen_ids.add(query_id)

        text = str(entry.get("text") or "").strip()
        if not text:
            raise Benchmark08Error(f"query {query_id} has empty text")

        query_class = _parse_enum(
            QueryClass08, entry.get("query_class"), query_id, "query_class"
        )
        split = _parse_enum(Split, entry.get("split"), query_id, "split")
        design_label = _parse_enum(
            DesignLabel, entry.get("design_label"), query_id, "design_label"
        )

        answerable = entry.get("answerable")
        if not isinstance(answerable, bool):
            raise Benchmark08Error(f"query {query_id} answerable must be a bool")

        required = _parse_keys(
            entry.get("required_source_keys"), source_keys, query_id, "required"
        )
        optional = _parse_keys(
            entry.get("optional_support_source_keys"),
            source_keys,
            query_id,
            "optional",
        )
        overlap = set(required) & set(optional)
        if overlap:
            raise Benchmark08Error(
                f"query {query_id} lists {sorted(overlap)} as BOTH required and optional"
            )

        is_negative_class = query_class in NEGATIVE_CLASSES
        # answerable, class, and GT must agree (task §16/§24).
        if answerable == is_negative_class:
            raise Benchmark08Error(
                f"query {query_id}: answerable={answerable} disagrees with "
                f"negative class membership ({query_class.value})"
            )
        if answerable and not required:
            raise Benchmark08Error(
                f"answerable query {query_id} must have >=1 required source"
            )
        if not answerable and required:
            raise Benchmark08Error(
                f"unanswerable query {query_id} must have no required source"
            )

        raw_nc = entry.get("negative_construction")
        negative_construction: Optional[NegativeConstruction] = None
        if answerable:
            if raw_nc is not None:
                raise Benchmark08Error(
                    f"answerable query {query_id} must not set negative_construction"
                )
            if design_label is DesignLabel.NEGATIVE:
                raise Benchmark08Error(
                    f"answerable query {query_id} must not use the NEGATIVE design label"
                )
        else:
            negative_construction = _parse_enum(
                NegativeConstruction, raw_nc, query_id, "negative_construction"
            )
            if design_label is not DesignLabel.NEGATIVE:
                raise Benchmark08Error(
                    f"unanswerable query {query_id} must use the NEGATIVE design label"
                )

        rationale = str(entry.get("rationale") or "").strip()
        if not rationale:
            raise Benchmark08Error(f"query {query_id} has empty rationale")

        queries.append(
            BenchmarkQuery08(
                query_id=query_id,
                query_class=query_class,
                split=split,
                answerable=answerable,
                text=text,
                required_source_keys=required,
                optional_support_source_keys=optional,
                design_label=design_label,
                negative_construction=negative_construction,
                rationale=rationale,
            )
        )
    return queries


def _parse_enum(enum_cls, value: object, query_id: str, field: str):
    try:
        return enum_cls(str(value))
    except ValueError as exc:
        raise Benchmark08Error(
            f"query {query_id} has invalid {field} {value!r}"
        ) from exc


def _parse_keys(
    raw: object, source_keys: set[str], query_id: str, field: str
) -> Tuple[str, ...]:
    if raw is None:
        return ()
    if not isinstance(raw, list):
        raise Benchmark08Error(f"query {query_id} {field}_source_keys must be a list")
    out: List[str] = []
    for rk in raw:
        rk_s = str(rk).strip()
        if rk_s not in source_keys:
            raise Benchmark08Error(
                f"query {query_id} references unknown source key {rk_s!r}"
            )
        if rk_s in out:
            raise Benchmark08Error(
                f"query {query_id} lists duplicate {field} key {rk_s!r}"
            )
        out.append(rk_s)
    return tuple(out)


def validate_frozen_shape(bench: Benchmark08) -> None:
    """Enforce the FROZEN graphrag_08_eval_v1 counts (task §24).

    Raises ``Benchmark08Error`` on any deviation. This is deliberately separate
    from load-time structural validation so that unit tests may construct small
    synthetic benchmarks, while the committed fixture is held to the exact frozen
    shape (75 / 60 / 30 / 30 / 12 / 10 classes, every class in both splits).
    """
    if len(bench.sources) != FROZEN_SOURCE_COUNT:
        raise Benchmark08Error(
            f"source_count {len(bench.sources)} != {FROZEN_SOURCE_COUNT}"
        )
    if len(bench.queries) != FROZEN_QUERY_COUNT:
        raise Benchmark08Error(
            f"query_count {len(bench.queries)} != {FROZEN_QUERY_COUNT}"
        )
    dev = sum(1 for q in bench.queries if q.split is Split.DEV)
    holdout = sum(1 for q in bench.queries if q.split is Split.HOLDOUT)
    if dev != FROZEN_DEV_COUNT:
        raise Benchmark08Error(f"dev_count {dev} != {FROZEN_DEV_COUNT}")
    if holdout != FROZEN_HOLDOUT_COUNT:
        raise Benchmark08Error(f"holdout_count {holdout} != {FROZEN_HOLDOUT_COUNT}")
    negatives = sum(1 for q in bench.queries if q.is_negative)
    if negatives != FROZEN_NEGATIVE_COUNT:
        raise Benchmark08Error(
            f"negative_count {negatives} != {FROZEN_NEGATIVE_COUNT}"
        )
    classes = {q.query_class for q in bench.queries}
    if len(classes) != FROZEN_QUERY_CLASS_COUNT:
        raise Benchmark08Error(
            f"query_class_count {len(classes)} != {FROZEN_QUERY_CLASS_COUNT}"
        )
    # Every class must appear in BOTH splits (task §14/§18).
    for qc in QueryClass08:
        for sp in Split:
            n = sum(
                1
                for q in bench.queries
                if q.query_class is qc and q.split is sp
            )
            if n < 1:
                raise Benchmark08Error(
                    f"class {qc.value} has no {sp.value} queries (needed in both splits)"
                )
    # Every negative construction must be exercised at least once (task §15).
    used_nc = {
        q.negative_construction for q in bench.queries if q.negative_construction
    }
    missing = set(NegativeConstruction) - used_nc
    if missing:
        raise Benchmark08Error(
            f"negative constructions never exercised: {sorted(c.value for c in missing)}"
        )


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def compute_integrity(fixture_dir: Optional[Path] = None) -> Dict[str, object]:
    """Content-hash the fixture files for the freeze marker (task §25).

    Hashes the raw bytes of corpus.json and queries.json plus a combined digest.
    Contains NO fixture content — only hashes and counts — so the freeze marker
    is safe to commit and to echo in a content-free artifact.
    """
    fixture_dir = fixture_dir or default_fixture_dir()
    corpus_sha = _sha256_file(fixture_dir / "corpus.json")
    queries_sha = _sha256_file(fixture_dir / "queries.json")
    combined = hashlib.sha256(
        (corpus_sha + ":" + queries_sha).encode("ascii")
    ).hexdigest()
    bench = load_benchmark08(fixture_dir)
    return {
        "benchmark_version": BENCHMARK_VERSION,
        "namespace_tag": NAMESPACE_TAG,
        "corpus_sha256": corpus_sha,
        "queries_sha256": queries_sha,
        "combined_sha256": combined,
        "source_count": len(bench.sources),
        "query_count": len(bench.queries),
        "dev_count": sum(1 for q in bench.queries if q.split is Split.DEV),
        "holdout_count": sum(1 for q in bench.queries if q.split is Split.HOLDOUT),
        "negative_count": sum(1 for q in bench.queries if q.is_negative),
    }


def load_freeze(fixture_dir: Optional[Path] = None) -> Dict[str, object]:
    fixture_dir = fixture_dir or default_fixture_dir()
    return _read_json(fixture_dir / "freeze.json")


def verify_integrity(fixture_dir: Optional[Path] = None) -> Tuple[bool, str]:
    """Compare the live fixture hash against the committed freeze marker.

    Returns (ok, detail). ok=False means the fixture has diverged from its frozen
    hash — a hard stop for any provider-backed run (task §85).
    """
    fixture_dir = fixture_dir or default_fixture_dir()
    freeze = load_freeze(fixture_dir)
    live = compute_integrity(fixture_dir)
    if freeze.get("combined_sha256") != live["combined_sha256"]:
        return False, (
            f"combined_sha256 mismatch: frozen={freeze.get('combined_sha256')!r} "
            f"live={live['combined_sha256']!r}"
        )
    return True, live["combined_sha256"]  # type: ignore[return-value]


__all__ = [
    "BENCHMARK_VERSION",
    "NAMESPACE_TAG",
    "FROZEN_SOURCE_COUNT",
    "FROZEN_QUERY_COUNT",
    "FROZEN_DEV_COUNT",
    "FROZEN_HOLDOUT_COUNT",
    "FROZEN_NEGATIVE_COUNT",
    "QueryClass08",
    "Split",
    "DesignLabel",
    "NegativeConstruction",
    "NEGATIVE_CLASSES",
    "Benchmark08",
    "BenchmarkSource08",
    "BenchmarkQuery08",
    "Benchmark08Error",
    "load_benchmark08",
    "validate_frozen_shape",
    "compute_integrity",
    "verify_integrity",
    "load_freeze",
    "default_fixture_dir",
]
