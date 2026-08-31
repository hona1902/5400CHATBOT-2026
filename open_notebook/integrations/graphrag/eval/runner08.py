"""GraphRAG-08 three-system eval runner (V / GQ / GD) — synthetic data ONLY.

EVALUATION-ONLY. Nothing in production imports this. It drives, for a SELECTED
subset of the frozen benchmark, the three systems as they are wired today plus the
eval-only GD seam:

  V  = VECTOR_BASELINE               (real ``vector_search``; ranked)
  GQ = CURRENT_LIGHTRAG_QUERY_EVIDENCE (real ``service.query_strict`` references)
  GD = STRUCTURED_QUERY_DATA_EVIDENCE  (eval-only ``GDQueryClient.query_evidence``)

Isolation (task §35/§47/§48): this runner uses the PROVEN GraphRAG-04 safety model
— a unique per-run id namespace + benchmark tag + candidate allowlist + fail-closed
isolation assertion + per-id cleanup. Every DB/sidecar op targets ONLY
``created_ids``; there is never a "select all" sweep and foreign Sources are never
touched. (The design's preferred dedicated temporary Surreal namespace/database —
Option A — remains the target for the FULL run; using it requires bootstrapping the
schema into a temp namespace and is a documented follow-up. Option B here is the
design's documented fallback and is what GraphRAG-04 executed.)

For the micro-precheck the allowlist / corpus denominator is the SELECTED subset,
not 75 (task §35). Synthetic content only; the generated LightRAG answer is
discarded; no production retrieval code is modified.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

from open_notebook.integrations.graphrag.config import VERIFIED_LIGHTRAG_VERSION
from open_notebook.integrations.graphrag.eval.dataset08 import (
    Benchmark08,
    BenchmarkQuery08,
    QueryClass08,
    Split,
)
from open_notebook.integrations.graphrag.eval.gd_seam import GDEvidence, GDQueryClient
from open_notebook.integrations.graphrag.eval.normalize import (
    NormalizedRetrieval,
    normalize_graph_references,
    normalize_vector_results,
)
from open_notebook.integrations.graphrag.models import (
    IndexState,
    QueryMode,
    record_id_for,
)

_SOURCE_TABLES = frozenset({"source"})

# Per-query system state (reuses the GraphRAG-04 vocabulary).
STATE_EVALUATED = "evaluated"
STATE_RETRIEVER_ERROR = "retriever_error"
STATE_TIMEOUT = "timeout"


class EvalIsolationError(RuntimeError):
    """A synthetic-source isolation invariant was violated."""


class IndexNotReadyError(RuntimeError):
    """Indexing readiness could not be proven within the bounded wait."""


@dataclass(frozen=True)
class EvalRunConfig08:
    id_prefix: str = field(default_factory=lambda: f"gr08e{uuid.uuid4().hex[:8]}")
    k_budgets: Tuple[int, ...] = (1, 3, 5, 10)
    vector_fetch: int = 12
    minimum_score: float = 0.2
    graph_mode: QueryMode = QueryMode.HYBRID
    graph_top_k: Optional[int] = None
    index_ready_timeout_s: float = 240.0
    poll_interval_s: float = 3.0
    run_type: str = "MICRO_PRECHECK"


@dataclass(frozen=True)
class QueryEvaluation08:
    query_id: str
    query_class: str
    split: str
    answerable: bool
    required_ids: frozenset[str]
    allowed_ids: frozenset[str]  # required ∪ optional (for false-positive accounting)
    vector_state: str
    gq_state: str
    gd_state: str
    vector: Optional[NormalizedRetrieval]
    gq: Optional[NormalizedRetrieval]
    gd: Optional[GDEvidence]
    vector_latency_ms: Optional[int]
    gq_latency_ms: Optional[int]
    gd_latency_ms: Optional[int]
    detail: str = ""


@dataclass(frozen=True)
class CleanupResult:
    deleted: int
    remaining_synthetic_ids: Tuple[str, ...]
    errors: Tuple[str, ...]

    @property
    def clean(self) -> bool:
        return not self.remaining_synthetic_ids


def select_precheck_subset(
    benchmark: Benchmark08,
    *,
    max_sources: int = 8,
    max_queries: int = 6,
) -> Tuple[Tuple[str, ...], Tuple[str, ...]]:
    """Deterministically pick a DEV-only subset within the hard caps (task §61).

    Greedy, class-prioritised: prefers coverage of one direct/semantic, one genuine
    two_hop, one three_hop (only if it fits), one collision, one negative, and one
    more mechanically-useful DEV query, while keeping the union of every selected
    query's required∪optional Sources within ``max_sources`` and the query count
    within ``max_queries``. HOLDOUT is never selected. If a three_hop would exceed
    the source cap it is skipped (the cap is never raised — task §61).
    """
    priority = [
        QueryClass08.DIRECT_LEXICAL,
        QueryClass08.TWO_HOP,
        QueryClass08.THREE_HOP_CROSS_SOURCE,
        QueryClass08.ENTITY_COLLISION,
        QueryClass08.NEGATIVE_UNANSWERABLE,
        QueryClass08.SEMANTIC_PARAPHRASE,
        QueryClass08.RELATIONSHIP_COLLISION,
        QueryClass08.DISTRACTOR_TERM_COLLISION,
        QueryClass08.PARTIAL_EVIDENCE,
        QueryClass08.BROAD_ENTITY_NAME_COLLISION,
    ]
    dev = [q for q in benchmark.queries if q.split is Split.DEV]
    # Deterministic order: by query_id within each class.
    by_class: Dict[QueryClass08, List[BenchmarkQuery08]] = {}
    for q in sorted(dev, key=lambda x: x.query_id):
        by_class.setdefault(q.query_class, []).append(q)

    chosen: List[BenchmarkQuery08] = []
    source_union: set[str] = set()

    def needed(q: BenchmarkQuery08) -> set[str]:
        return set(q.required_source_keys) | set(q.optional_support_source_keys)

    for qc in priority:
        if len(chosen) >= max_queries:
            break
        for q in by_class.get(qc, []):
            if len(chosen) >= max_queries:
                break
            add = needed(q)
            if len(source_union | add) <= max_sources:
                chosen.append(q)
                source_union |= add
                break  # one per class in the first pass

    # Second pass: fill remaining query slots from any class if room remains.
    if len(chosen) < max_queries:
        chosen_ids = {q.query_id for q in chosen}
        for q in sorted(dev, key=lambda x: x.query_id):
            if len(chosen) >= max_queries:
                break
            if q.query_id in chosen_ids:
                continue
            add = needed(q)
            if len(source_union | add) <= max_sources:
                chosen.append(q)
                source_union |= add
                chosen_ids.add(q.query_id)

    query_ids = tuple(sorted(q.query_id for q in chosen))
    source_keys = tuple(sorted(source_union))
    return source_keys, query_ids


@dataclass(frozen=True)
class RunManifest:
    """Content-free run manifest for cleanup + recovery (task §46/§77)."""

    run_id: str
    benchmark_version: str
    combined_sha256: Optional[str]
    run_type: str
    namespace_tag: str
    id_namespace: str
    selected_query_ids: Tuple[str, ...]
    selected_source_keys: Tuple[str, ...]
    key_to_source_id: Dict[str, str]
    created_ids: Tuple[str, ...]
    temp_model_id: Optional[str]
    state: str
    created_utc: str

    def as_dict(self) -> Dict[str, object]:
        return {
            "run_id": self.run_id,
            "benchmark_version": self.benchmark_version,
            "fixture_combined_sha256": self.combined_sha256,
            "run_type": self.run_type,
            "namespace_tag": self.namespace_tag,
            "id_namespace": self.id_namespace,
            "selected_query_ids": list(self.selected_query_ids),
            "selected_source_keys": list(self.selected_source_keys),
            "key_to_source_id": dict(self.key_to_source_id),
            "created_ids": list(self.created_ids),
            "temp_model_id": self.temp_model_id,
            "state": self.state,
            "created_utc": self.created_utc,
        }


class GraphRAG08EvalRunner:
    def __init__(
        self,
        benchmark: Benchmark08,
        service: object,
        gd_client: GDQueryClient,
        *,
        selected_source_keys: Tuple[str, ...],
        selected_query_ids: Tuple[str, ...],
        config: Optional[EvalRunConfig08] = None,
        combined_sha256: Optional[str] = None,
    ) -> None:
        self.benchmark = benchmark
        self.service = service
        self.gd_client = gd_client
        self.config = config or EvalRunConfig08()
        self.combined_sha256 = combined_sha256
        self.run_id = f"gr08-{uuid.uuid4().hex[:12]}"
        self.state = "PLANNED"
        self.temp_model_id: Optional[str] = None

        key_set = set(selected_source_keys)
        self.selected_sources = tuple(
            s for s in benchmark.sources if s.key in key_set
        )
        qid_set = set(selected_query_ids)
        self.selected_queries = tuple(
            q for q in benchmark.queries if q.query_id in qid_set
        )
        # DEV-only guard: the micro-precheck must never touch HOLDOUT (task §14/§84).
        holdout = [q for q in self.selected_queries if q.split is Split.HOLDOUT]
        if holdout:
            raise EvalIsolationError(
                f"micro-precheck selected {len(holdout)} HOLDOUT query(ies); "
                "the precheck is DEV-only"
            )
        self.key_to_source_id: Dict[str, str] = {}
        self.created_ids: List[str] = []
        self.track_ids: Dict[str, str] = {}

    @property
    def corpus_size(self) -> int:
        """Denominator for candidate_fraction = the run's created Sources (task §35)."""
        return len(self.created_ids)

    def manifest(self) -> RunManifest:
        return RunManifest(
            run_id=self.run_id,
            benchmark_version=self.benchmark.version,
            combined_sha256=self.combined_sha256,
            run_type=self.config.run_type,
            namespace_tag=self.benchmark.namespace_tag,
            id_namespace=f"source:{self.config.id_prefix}*",
            selected_query_ids=tuple(q.query_id for q in self.selected_queries),
            selected_source_keys=tuple(s.key for s in self.selected_sources),
            key_to_source_id=dict(self.key_to_source_id),
            created_ids=tuple(self.created_ids),
            temp_model_id=self.temp_model_id,
            state=self.state,
            created_utc=datetime.now(timezone.utc).isoformat(),
        )

    # -- setup ---------------------------------------------------------------
    async def create_and_index(self) -> None:
        from open_notebook.database.repository import repo_query

        tag = self.benchmark.namespace_tag
        for i, src in enumerate(self.selected_sources):
            rid = record_id_for(
                f"source:{self.config.id_prefix}{i:02d}", tables=_SOURCE_TABLES
            )
            canonical = str(rid)
            # Record intended id BEFORE the CREATE so cleanup always covers it.
            self.key_to_source_id[src.key] = canonical
            self.created_ids.append(canonical)
            await repo_query(
                "CREATE $id SET full_text = $t, title = $title, topics = $topics",
                {"id": rid, "t": src.text, "title": src.title, "topics": [tag]},
            )
        self.state = "FULL_INDEX"
        await self._assert_isolation()
        await self._vector_embed_all()
        await self._graph_index_all()
        await self._await_graph_ready()

    async def _assert_isolation(self) -> None:
        from open_notebook.database.repository import repo_query

        if len(set(self.created_ids)) != len(self.selected_sources):
            raise EvalIsolationError("created source ids are not unique / complete")
        rows = await repo_query(
            "SELECT VALUE id FROM source WHERE $tag IN topics",
            {"tag": self.benchmark.namespace_tag},
        )
        tagged = {str(r) for r in (rows or [])}
        missing = set(self.created_ids) - tagged
        if missing:
            raise EvalIsolationError("created synthetic sources missing benchmark tag")
        extra = tagged - set(self.created_ids)
        if extra:
            raise EvalIsolationError(
                f"{len(extra)} benchmark-tagged source(s) are not from this run "
                "(possible leftover from a prior run); clean up before evaluating"
            )

    async def _vector_embed_all(self) -> None:
        from commands.embedding_commands import (
            EmbedSourceInput,
            embed_source_command,
        )

        for canonical in self.created_ids:
            out = await embed_source_command(EmbedSourceInput(source_id=canonical))
            if not out.success or out.chunks_created <= 0:
                raise IndexNotReadyError(
                    f"vector embedding failed for {canonical}: {out.error_message}"
                )

    async def _graph_index_all(self) -> None:
        for src in self.selected_sources:
            canonical = self.key_to_source_id[src.key]
            ack = await self.service.index_source(  # type: ignore[attr-defined]
                source_id=canonical, canonical_text=src.text
            )
            if not ack.accepted or not ack.track_id:
                raise IndexNotReadyError(
                    f"GraphRAG index not accepted for {canonical}: {ack.detail}"
                )
            self.track_ids[canonical] = ack.track_id

    async def _await_graph_ready(self) -> None:
        deadline = time.monotonic() + self.config.index_ready_timeout_s
        pending = dict(self.track_ids)
        while pending and time.monotonic() < deadline:
            for canonical, track in list(pending.items()):
                status = await self.service.track_status(track)  # type: ignore[attr-defined]
                if status.state == IndexState.PROCESSED:
                    del pending[canonical]
                elif status.state == IndexState.FAILED:
                    raise IndexNotReadyError(
                        f"GraphRAG indexing FAILED for {canonical}"
                    )
            if pending:
                await asyncio.sleep(self.config.poll_interval_s)
        if pending:
            raise IndexNotReadyError(
                f"{len(pending)} source(s) not PROCESSED within "
                f"{self.config.index_ready_timeout_s}s — readiness not proven"
            )

    # -- retrieval -----------------------------------------------------------
    async def run(self) -> List[QueryEvaluation08]:
        self.state = "FULL_QUERY"
        allow_ids = frozenset(self.created_ids)
        evaluations: List[QueryEvaluation08] = []
        for q in self.selected_queries:
            required_ids = frozenset(
                self.key_to_source_id[k]
                for k in q.required_source_keys
                if k in self.key_to_source_id
            )
            allowed_ids = required_ids | frozenset(
                self.key_to_source_id[k]
                for k in q.optional_support_source_keys
                if k in self.key_to_source_id
            )
            vstate, vnorm, vlat = await self._vector(q.text)
            gqstate, gqnorm, gqlat = await self._gq(q.text, allow_ids)
            gdstate, gdev, gdlat = await self._gd(q.text, allow_ids)
            evaluations.append(
                QueryEvaluation08(
                    query_id=q.query_id,
                    query_class=q.query_class.value,
                    split=q.split.value,
                    answerable=q.answerable,
                    required_ids=required_ids,
                    allowed_ids=allowed_ids,
                    vector_state=vstate,
                    gq_state=gqstate,
                    gd_state=gdstate,
                    vector=vnorm,
                    gq=gqnorm,
                    gd=gdev,
                    vector_latency_ms=vlat,
                    gq_latency_ms=gqlat,
                    gd_latency_ms=gdlat,
                )
            )
        return evaluations

    async def _vector(
        self, question: str
    ) -> Tuple[str, Optional[NormalizedRetrieval], Optional[int]]:
        from open_notebook.domain.notebook import vector_search

        started = time.monotonic()
        try:
            rows = await vector_search(
                question,
                results=self.config.vector_fetch,
                source=True,
                note=True,
                minimum_score=self.config.minimum_score,
            )
        except Exception:  # noqa: BLE001 - record, never abort the sweep
            return STATE_RETRIEVER_ERROR, None, None
        lat = int((time.monotonic() - started) * 1000)
        return STATE_EVALUATED, normalize_vector_results(rows or []), lat

    async def _gq(
        self, question: str, allow_ids: frozenset[str]
    ) -> Tuple[str, Optional[NormalizedRetrieval], Optional[int]]:
        started = time.monotonic()
        try:
            result = await self.service.query_strict(  # type: ignore[attr-defined]
                question, mode=self.config.graph_mode, top_k=self.config.graph_top_k
            )
        except Exception as exc:  # noqa: BLE001
            detail = str(exc).lower()
            state = (
                STATE_TIMEOUT
                if "timed out" in detail or "timeout" in detail
                else STATE_RETRIEVER_ERROR
            )
            return state, None, None
        lat = int((time.monotonic() - started) * 1000)
        norm = normalize_graph_references(result.references, benchmark_ids=allow_ids)
        return STATE_EVALUATED, norm, lat

    async def _gd(
        self, question: str, allow_ids: frozenset[str]
    ) -> Tuple[str, Optional[GDEvidence], Optional[int]]:
        try:
            ev = await self.gd_client.query_evidence(
                question,
                mode=self.config.graph_mode,
                top_k=self.config.graph_top_k,
                benchmark_ids=allow_ids,
            )
        except Exception as exc:  # noqa: BLE001
            detail = str(exc).lower()
            state = (
                STATE_TIMEOUT
                if "timed out" in detail or "timeout" in detail
                else STATE_RETRIEVER_ERROR
            )
            return state, None, None
        return STATE_EVALUATED, ev, ev.latency_ms

    # -- cleanup -------------------------------------------------------------
    async def cleanup(self) -> CleanupResult:
        from open_notebook.database.repository import repo_query

        self.state = "CLEANUP"
        errors: List[str] = []
        deleted = 0
        for canonical in self.created_ids:
            rid = record_id_for(canonical, tables=_SOURCE_TABLES)
            try:
                await self.service.delete_document_for_source(  # type: ignore[attr-defined]
                    source_id=canonical
                )
            except Exception as exc:  # noqa: BLE001
                errors.append(f"sidecar delete {canonical}: {type(exc).__name__}")
            try:
                await repo_query(
                    "DELETE source_embedding WHERE source = $id", {"id": rid}
                )
                await repo_query("DELETE $id", {"id": rid})
                deleted += 1
            except Exception as exc:  # noqa: BLE001
                errors.append(f"db delete {canonical}: {type(exc).__name__}")

        rows = await repo_query(
            "SELECT VALUE id FROM source WHERE $tag IN topics",
            {"tag": self.benchmark.namespace_tag},
        )
        tagged = {str(r) for r in (rows or [])}
        remaining = tuple(sorted(tagged & set(self.created_ids)))
        return CleanupResult(
            deleted=deleted, remaining_synthetic_ids=remaining, errors=tuple(errors)
        )

    def build_metadata(self) -> Dict[str, object]:
        return {
            "run_id": self.run_id,
            "run_type": self.config.run_type,
            "value_run": False,
            "holdout_used": False,
            "full_benchmark_executed": False,
            "benchmark_version": self.benchmark.version,
            "fixture_combined_sha256": self.combined_sha256,
            "system_names": [
                "VECTOR_BASELINE",
                "CURRENT_LIGHTRAG_QUERY_EVIDENCE",
                "STRUCTURED_QUERY_DATA_EVIDENCE",
            ],
            "lightrag_version": VERIFIED_LIGHTRAG_VERSION,
            "graph_query_mode": self.config.graph_mode.value,
            "k_budgets": list(self.config.k_budgets),
            "selected_source_count": len(self.selected_sources),
            "selected_query_count": len(self.selected_queries),
            "candidate_fraction_denominator": self.corpus_size,
            "namespace_tag": self.benchmark.namespace_tag,
            "id_namespace": f"source:{self.config.id_prefix}*",
            "synthetic_only": True,
            "answer_scored": False,
            "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        }


__all__ = [
    "EvalRunConfig08",
    "QueryEvaluation08",
    "CleanupResult",
    "RunManifest",
    "GraphRAG08EvalRunner",
    "EvalIsolationError",
    "IndexNotReadyError",
    "select_precheck_subset",
    "STATE_EVALUATED",
    "STATE_RETRIEVER_ERROR",
    "STATE_TIMEOUT",
]
