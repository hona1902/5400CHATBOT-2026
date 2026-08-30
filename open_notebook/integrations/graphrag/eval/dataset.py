"""Load and validate the frozen GraphRAG-04 synthetic benchmark.

Pure I/O over JSON fixtures — no retriever, no DB, no network. The benchmark
(corpus + queries + labels + split) is the committed source of truth; this module
only reads it and enforces the structural invariants that the dataset-validation
tests (task §43) assert, so a malformed or leaking benchmark fails loudly at load
time rather than silently distorting metrics.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Dict, FrozenSet, List, Optional, Tuple

BENCHMARK_VERSION = "graphrag_04_eval_v1"
NAMESPACE_TAG = "__graphrag04_eval_v1__"


class QueryClass(str, Enum):
    DIRECT = "direct"
    PARAPHRASE = "paraphrase"
    TWO_HOP = "two_hop"
    THREE_HOP = "three_hop"
    DISTRACTOR = "distractor"
    NEGATIVE = "negative"


class Split(str, Enum):
    DEV = "dev"
    HOLDOUT = "holdout"


class BenchmarkError(ValueError):
    """The benchmark fixture violates a structural invariant."""


@dataclass(frozen=True)
class BenchmarkSource:
    key: str
    title: str
    text: str


@dataclass(frozen=True)
class BenchmarkQuery:
    query_id: str
    query_class: QueryClass
    split: Split
    text: str
    #: Corpus keys judged relevant. Empty for negative (unanswerable) controls.
    relevant_source_keys: Tuple[str, ...]

    @property
    def is_negative(self) -> bool:
        return self.query_class is QueryClass.NEGATIVE


@dataclass(frozen=True)
class Benchmark:
    version: str
    namespace_tag: str
    sources: Tuple[BenchmarkSource, ...]
    queries: Tuple[BenchmarkQuery, ...]

    @property
    def source_keys(self) -> FrozenSet[str]:
        return frozenset(s.key for s in self.sources)

    def queries_for_split(self, split: Split) -> Tuple[BenchmarkQuery, ...]:
        return tuple(q for q in self.queries if q.split is split)

    def relevant_keys(self, query: BenchmarkQuery) -> FrozenSet[str]:
        return frozenset(query.relevant_source_keys)


def default_fixture_dir() -> Path:
    """Repo-relative path to the committed benchmark fixture directory."""
    # open_notebook/integrations/graphrag/eval/dataset.py -> repo root is 5 up.
    repo_root = Path(__file__).resolve().parents[4]
    return repo_root / "tests" / "fixtures" / BENCHMARK_VERSION


def load_benchmark(fixture_dir: Optional[Path] = None) -> Benchmark:
    """Load, validate, and return the frozen benchmark.

    Raises BenchmarkError on any structural violation (duplicate keys/ids, a
    label pointing at a missing source, empty query text, invalid class/split,
    DEV/HOLDOUT overlap, or a negative query that carries relevant sources).
    """
    fixture_dir = fixture_dir or default_fixture_dir()
    corpus_raw = _read_json(fixture_dir / "corpus.json")
    queries_raw = _read_json(fixture_dir / "queries.json")

    sources = _parse_sources(corpus_raw)
    source_keys = {s.key for s in sources}
    queries = _parse_queries(queries_raw, source_keys)

    version = str(corpus_raw.get("benchmark_version") or "")
    if version != BENCHMARK_VERSION:
        raise BenchmarkError(
            f"corpus benchmark_version {version!r} != {BENCHMARK_VERSION!r}"
        )
    if str(queries_raw.get("benchmark_version") or "") != BENCHMARK_VERSION:
        raise BenchmarkError("queries benchmark_version mismatch")

    namespace_tag = str(corpus_raw.get("namespace_tag") or "")
    if namespace_tag != NAMESPACE_TAG:
        raise BenchmarkError(
            f"corpus namespace_tag {namespace_tag!r} != {NAMESPACE_TAG!r}"
        )

    return Benchmark(
        version=version,
        namespace_tag=namespace_tag,
        sources=tuple(sources),
        queries=tuple(queries),
    )


def _read_json(path: Path) -> Dict[str, object]:
    if not path.exists():
        raise BenchmarkError(f"benchmark file not found: {path}")
    with path.open("r", encoding="utf-8") as fh:
        data = json.load(fh)
    if not isinstance(data, dict):
        raise BenchmarkError(f"{path.name} must be a JSON object")
    return data


def _parse_sources(corpus_raw: Dict[str, object]) -> List[BenchmarkSource]:
    raw_sources = corpus_raw.get("sources")
    if not isinstance(raw_sources, list) or not raw_sources:
        raise BenchmarkError("corpus.sources must be a non-empty list")

    sources: List[BenchmarkSource] = []
    seen_keys: set[str] = set()
    for entry in raw_sources:
        if not isinstance(entry, dict):
            raise BenchmarkError("each corpus source must be an object")
        key = str(entry.get("key") or "").strip()
        title = str(entry.get("title") or "").strip()
        text = str(entry.get("text") or "").strip()
        if not key:
            raise BenchmarkError("a corpus source has an empty key")
        if key in seen_keys:
            raise BenchmarkError(f"duplicate corpus source key: {key}")
        if not text:
            raise BenchmarkError(f"corpus source {key} has empty text")
        seen_keys.add(key)
        sources.append(BenchmarkSource(key=key, title=title, text=text))
    return sources


def _parse_queries(
    queries_raw: Dict[str, object], source_keys: set[str]
) -> List[BenchmarkQuery]:
    raw_queries = queries_raw.get("queries")
    if not isinstance(raw_queries, list) or not raw_queries:
        raise BenchmarkError("queries.queries must be a non-empty list")

    queries: List[BenchmarkQuery] = []
    seen_ids: set[str] = set()
    for entry in raw_queries:
        if not isinstance(entry, dict):
            raise BenchmarkError("each query must be an object")
        query_id = str(entry.get("query_id") or "").strip()
        if not query_id:
            raise BenchmarkError("a query has an empty query_id")
        if query_id in seen_ids:
            raise BenchmarkError(f"duplicate query_id: {query_id}")
        seen_ids.add(query_id)

        text = str(entry.get("text") or "").strip()
        if not text:
            raise BenchmarkError(f"query {query_id} has empty text")

        try:
            query_class = QueryClass(str(entry.get("query_class")))
        except ValueError as exc:
            raise BenchmarkError(
                f"query {query_id} has invalid query_class {entry.get('query_class')!r}"
            ) from exc
        try:
            split = Split(str(entry.get("split")))
        except ValueError as exc:
            raise BenchmarkError(
                f"query {query_id} has invalid split {entry.get('split')!r}"
            ) from exc

        raw_rel = entry.get("relevant_source_keys")
        if not isinstance(raw_rel, list):
            raise BenchmarkError(
                f"query {query_id} relevant_source_keys must be a list"
            )
        rel_keys: List[str] = []
        for rk in raw_rel:
            rk_s = str(rk).strip()
            if rk_s not in source_keys:
                raise BenchmarkError(
                    f"query {query_id} references unknown source key {rk_s!r}"
                )
            if rk_s in rel_keys:
                raise BenchmarkError(
                    f"query {query_id} lists duplicate relevant key {rk_s!r}"
                )
            rel_keys.append(rk_s)

        is_negative = query_class is QueryClass.NEGATIVE
        if is_negative and rel_keys:
            raise BenchmarkError(
                f"negative query {query_id} must have no relevant sources"
            )
        if not is_negative and not rel_keys:
            raise BenchmarkError(
                f"non-negative query {query_id} must have >=1 relevant source"
            )

        queries.append(
            BenchmarkQuery(
                query_id=query_id,
                query_class=query_class,
                split=split,
                text=text,
                relevant_source_keys=tuple(rel_keys),
            )
        )
    return queries
