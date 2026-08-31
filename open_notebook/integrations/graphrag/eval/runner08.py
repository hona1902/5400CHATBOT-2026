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
    #: 1 initial attempt + at most 1 bounded transient retry per Source (task §2).
    max_index_attempts_per_source: int = 2


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
        allow_holdout: bool = False,
        graphrag_config: object = None,
    ) -> None:
        self.benchmark = benchmark
        self.service = service
        self.gd_client = gd_client
        self.config = config or EvalRunConfig08()
        # Eval-only GraphRAGConfig used ONLY to read a FAILED doc's error text for
        # transient-vs-non-retryable classification (task §4). None -> fail closed.
        self.graphrag_config = graphrag_config
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
        # The authorized FULL value run sets allow_holdout=True to execute HOLDOUT;
        # it stays False everywhere else, so the precheck can never touch HOLDOUT.
        if not allow_holdout:
            holdout = [q for q in self.selected_queries if q.split is Split.HOLDOUT]
            if holdout:
                raise EvalIsolationError(
                    f"micro-precheck selected {len(holdout)} HOLDOUT query(ies); "
                    "the precheck is DEV-only"
                )
        self.key_to_source_id: Dict[str, str] = {}
        self.created_ids: List[str] = []
        self.track_ids: Dict[str, str] = {}
        #: Sources proven to have reached IndexState.PROCESSED (the 100%-gate signal).
        self.processed_ids: set[str] = set()
        #: Per-Source index attempt count (telemetry only; ≤ max_index_attempts).
        self.index_attempts: Dict[str, int] = {}
        #: Content-free per-failure diagnostics (GraphRAG-08B; never raw text).
        self.index_diagnostics: List[Dict[str, object]] = []
        #: Canonical ids whose indexing ultimately failed (aborting the run).
        self.failed_source_ids: List[str] = []

    @property
    def corpus_size(self) -> int:
        """Denominator for candidate_fraction = the run's created Sources (task §35)."""
        return len(self.created_ids)

    def _logical_id(self, canonical: str) -> Optional[str]:
        for key, cid in self.key_to_source_id.items():
            if cid == canonical:
                return key
        return None

    def retry_accounting(self) -> Dict[str, int]:
        """Content-free per-Source index-attempt summary (execution reliability only).

        Valid on BOTH a completed run and an aborted-before-query run (GraphRAG-08B):
        it reads only counters populated during indexing, so it survives a pre-ANALYZE
        abort."""
        att = self.index_attempts
        cap = self.config.max_index_attempts_per_source
        failed = set(self.failed_source_ids)
        first = sum(1 for c, n in att.items() if n == 1 and c in self.processed_ids)
        retried = sum(1 for n in att.values() if n >= 2)
        retry_succeeded = sum(
            1 for c, n in att.items() if n >= 2 and c in self.processed_ids
        )
        retry_exhausted = sum(1 for c in failed if att.get(c, 0) >= cap)
        non_retryable = sum(1 for c in failed if att.get(c, 0) < cap)
        return {
            "sources_indexed_first_attempt": first,
            "sources_retried": retried,
            "retry_succeeded": retry_succeeded,
            "retry_exhausted": retry_exhausted,
            "non_retryable_failures": non_retryable,
            "max_attempts_observed": max(att.values()) if att else 0,
        }

    def index_telemetry(self) -> Dict[str, object]:
        """Content-free indexing telemetry (survives a pre-ANALYZE abort; task §10/§11)."""
        return {
            "run_id": self.run_id,
            "selected_source_count": len(self.selected_sources),
            "created_source_count": len(self.created_ids),
            "graphrag_indexed_count": len(self.processed_ids),
            "max_index_attempts_per_source": self.config.max_index_attempts_per_source,
            "retry_accounting": self.retry_accounting(),
            "failed_canonical_ids": list(self.failed_source_ids),
            "failed_logical_ids": [self._logical_id(c) for c in self.failed_source_ids],
            "per_source_attempts": {
                (self._logical_id(c) or c): n for c, n in self.index_attempts.items()
            },
            "failure_diagnostics": list(self.index_diagnostics),
        }

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
        from open_notebook.integrations.graphrag.eval.isolation08 import (
            require_active_isolation,
        )

        # Option-A HARD BLOCK (GraphRAG-08A §28): the live path may only create
        # canonical Sources inside a dedicated temporary Surreal namespace. This
        # refuses to run against the normal application DB even if a caller wired
        # it that way — the earlier Option-B normal-DB mode is not an authorized
        # GraphRAG-08 live path.
        require_active_isolation()

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
        await self._graph_index_with_retry()
        self._assert_complete_corpus()

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

    def _canonical_text(self) -> Dict[str, str]:
        return {
            self.key_to_source_id[s.key]: s.text for s in self.selected_sources
        }

    async def _submit_index_bounded(
        self, canonical: str, text: str, attempts: Dict[str, int], *, reindex: bool
    ) -> str:
        """Submit one Source for indexing, with a submit-time transient retry within
        the per-Source attempt cap (task §2). Returns the track_id or aborts."""
        from open_notebook.integrations.graphrag.eval.index_retry08 import (
            classify_submit_exception,
            diagnose_submit_exception,
        )

        cap = self.config.max_index_attempts_per_source
        do_reindex = reindex
        while True:
            if attempts[canonical] >= cap:
                raise IndexNotReadyError(
                    f"GraphRAG index attempts exhausted for {canonical}"
                )
            attempts[canonical] += 1
            try:
                if do_reindex:
                    # Approved reindex path = delete-then-insert (03A/03B).
                    try:
                        await self.service.delete_document_for_source(  # type: ignore[attr-defined]
                            source_id=canonical
                        )
                    except Exception:  # noqa: BLE001 - best-effort predelete
                        pass
                ack = await self.service.index_source(  # type: ignore[attr-defined]
                    source_id=canonical, canonical_text=text
                )
                if not ack.accepted or not ack.track_id:
                    # Not-accepted is a deterministic rejection: never retried.
                    raise IndexNotReadyError(
                        f"GraphRAG index not accepted for {canonical}: {ack.detail}"
                    )
                return ack.track_id
            except IndexNotReadyError:
                raise
            except Exception as exc:  # noqa: BLE001
                diag = diagnose_submit_exception(
                    exc,
                    attempt_number=attempts[canonical],
                    canonical_source_id=canonical,
                    logical_source_id=self._logical_id(canonical),
                )
                self.index_diagnostics.append(diag.as_dict())
                # Retry ONLY a clearly transient submit failure, within the cap.
                if attempts[canonical] < cap and classify_submit_exception(exc):
                    do_reindex = True
                    continue
                self.failed_source_ids.append(canonical)
                raise IndexNotReadyError(
                    f"GraphRAG index submit failed for {canonical}: {type(exc).__name__}"
                ) from exc

    async def _graph_index_with_retry(self) -> None:
        """Index every selected Source, with a bounded (max 2 attempts/Source)
        transient retry, and NEVER proceed on a partial corpus (task §2/§7/§8).

        A Source that reaches DocStatus.FAILED is retried ONCE via the reindex path
        iff (a) it is under the attempt cap AND (b) its failure is classified
        TRANSIENT from the sidecar's error text. A NON_RETRYABLE / UNKNOWN cause, or
        cap exhaustion, aborts the whole run (FULL_INDEX_FAIL) — no partial corpus.
        """
        from open_notebook.integrations.graphrag.eval.index_retry08 import (
            diagnose_failed_track,
        )

        texts = self._canonical_text()
        attempts: Dict[str, int] = {c: 0 for c in self.created_ids}
        self.index_attempts = attempts  # telemetry: reflects final per-Source counts
        pending: Dict[str, str] = {}
        for canonical in self.created_ids:
            pending[canonical] = await self._submit_index_bounded(
                canonical, texts[canonical], attempts, reindex=False
            )
        self.track_ids = dict(pending)

        deadline = time.monotonic() + self.config.index_ready_timeout_s
        while pending and time.monotonic() < deadline:
            for canonical, track in list(pending.items()):
                status = await self.service.track_status(track)  # type: ignore[attr-defined]
                if status.state == IndexState.PROCESSED:
                    del pending[canonical]
                    self.processed_ids.add(canonical)
                elif status.state == IndexState.FAILED:
                    # Content-safe diagnostic (never raw text); decision is unchanged
                    # (retry iff diag.retry_allowed == classify_failed_track TRANSIENT).
                    diag = await diagnose_failed_track(
                        self.graphrag_config,
                        track,
                        attempt_number=attempts[canonical],
                        canonical_source_id=canonical,
                        logical_source_id=self._logical_id(canonical),
                    )
                    self.index_diagnostics.append(diag.as_dict())
                    if attempts[canonical] >= self.config.max_index_attempts_per_source:
                        self.failed_source_ids.append(canonical)
                        raise IndexNotReadyError(
                            f"GraphRAG indexing FAILED for {canonical} after "
                            f"{attempts[canonical]} attempt(s) — full corpus not indexed"
                        )
                    if not diag.retry_allowed:
                        self.failed_source_ids.append(canonical)
                        raise IndexNotReadyError(
                            f"GraphRAG indexing FAILED for {canonical} "
                            f"(cause={diag.classification}, not retryable) — full corpus not indexed"
                        )
                    # transient + under cap: one reindex retry, then re-poll.
                    pending[canonical] = await self._submit_index_bounded(
                        canonical, texts[canonical], attempts, reindex=True
                    )
            if pending:
                await asyncio.sleep(self.config.poll_interval_s)
        if pending:
            raise IndexNotReadyError(
                f"{len(pending)} source(s) not PROCESSED within "
                f"{self.config.index_ready_timeout_s}s — readiness not proven"
            )

    def _assert_complete_corpus(self) -> None:
        """Hard 100%-corpus gate (task §7/§8): every selected Source must be created
        and (having reached here) vector- + graph-indexed. No partial-corpus eval."""
        n = len(self.selected_sources)
        if len(self.created_ids) != n or len(set(self.created_ids)) != n:
            raise IndexNotReadyError(
                f"canonical source count {len(self.created_ids)} != selected {n}"
            )
        if len(self.track_ids) != n:
            raise IndexNotReadyError(
                f"graph-indexed source count {len(self.track_ids)} != selected {n}"
            )
        # Independent processed-readiness check (not merely 'submitted'): every
        # created Source must have reached PROCESSED (review LOW-2 / task §7).
        if self.processed_ids != set(self.created_ids):
            missing = len(set(self.created_ids) - self.processed_ids)
            raise IndexNotReadyError(
                f"{missing} source(s) not PROCESSED — full corpus not indexed"
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
            "retry_accounting": self.retry_accounting(),
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
