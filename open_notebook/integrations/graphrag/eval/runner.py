"""Live orchestration for the GraphRAG-04 baseline (synthetic data ONLY).

Drives one end-to-end baseline against the systems as they are wired today:

  create isolated synthetic Sources (unique per-run id namespace + benchmark tag)
    -> vector-embed them through the real embed_source command
    -> GraphRAG-index them through the real 03A service seam
    -> bounded wait for BOTH indexes to be ready
    -> for each frozen query: real vector_search + real GRAPHRAG hybrid query
    -> normalize both to canonical Source ids, record per-query eval state
    -> clean up ONLY the ids this run created

Guardrails:
  * Every DB/sidecar operation targets ONLY ``created_ids`` — the exact set this
    run created. There is never a "select all sources" sweep (task §5, §35).
  * Synthetic/public content only — Boundary B stays synthetic-only (AGR-005 §6).
  * The GraphRAG answer is discarded; only references are scored (task decision).
  * No production retrieval code is modified; this calls existing seams unchanged.
"""

from __future__ import annotations

import asyncio
import subprocess
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

from open_notebook.integrations.graphrag.config import VERIFIED_LIGHTRAG_VERSION
from open_notebook.integrations.graphrag.eval.dataset import Benchmark
from open_notebook.integrations.graphrag.eval.normalize import (
    NormalizedRetrieval,
    normalize_graph_references,
    normalize_vector_results,
)
from open_notebook.integrations.graphrag.eval.report import EvalState, QueryEvaluation
from open_notebook.integrations.graphrag.models import (
    IndexState,
    QueryMode,
    record_id_for,
)

_SOURCE_TABLES = frozenset({"source"})


class EvalIsolationError(RuntimeError):
    """A safety invariant about synthetic-source isolation was violated."""


class IndexNotReadyError(RuntimeError):
    """Indexing readiness could not be proven within the bounded wait."""


@dataclass(frozen=True)
class EvalRunConfig:
    id_prefix: str = field(default_factory=lambda: f"gr04e{uuid.uuid4().hex[:8]}")
    k_budgets: Tuple[int, ...] = (1, 3, 5)
    #: How many vector rows to fetch (>= max K so top-K is well defined).
    vector_fetch: int = 10
    #: Production default similarity floor; recorded in metadata.
    minimum_score: float = 0.2
    graph_mode: QueryMode = QueryMode.HYBRID
    graph_top_k: Optional[int] = None
    index_ready_timeout_s: float = 240.0
    poll_interval_s: float = 3.0


@dataclass(frozen=True)
class CleanupResult:
    deleted: int
    remaining_synthetic_ids: Tuple[str, ...]
    errors: Tuple[str, ...]

    @property
    def clean(self) -> bool:
        return not self.remaining_synthetic_ids


class GraphRAGEvalRunner:
    def __init__(
        self,
        benchmark: Benchmark,
        service: object,
        config: Optional[EvalRunConfig] = None,
    ) -> None:
        self.benchmark = benchmark
        self.service = service  # GraphRAGService (duck-typed to keep imports light)
        self.config = config or EvalRunConfig()
        self.key_to_source_id: Dict[str, str] = {}
        self.created_ids: List[str] = []
        self.track_ids: Dict[str, str] = {}

    # -- setup ---------------------------------------------------------------
    async def create_and_index(self) -> None:
        from open_notebook.database.repository import repo_query

        tag = self.benchmark.namespace_tag
        for i, src in enumerate(self.benchmark.sources):
            rid = record_id_for(
                f"source:{self.config.id_prefix}{i:02d}", tables=_SOURCE_TABLES
            )
            canonical = str(rid)
            # Record the intended id BEFORE the CREATE so cleanup always covers it,
            # even if the write commits but this call then raises (ambiguous
            # commit). DELETE of a never-created id is a harmless no-op.
            self.key_to_source_id[src.key] = canonical
            self.created_ids.append(canonical)
            await repo_query(
                "CREATE $id SET full_text = $t, title = $title, topics = $topics",
                {"id": rid, "t": src.text, "title": src.title, "topics": [tag]},
            )

        await self._assert_isolation()
        await self._vector_embed_all()
        await self._graph_index_all()
        await self._await_graph_ready()

    async def _assert_isolation(self) -> None:
        """Every id we will touch must be one we just created AND carry our tag."""
        from open_notebook.database.repository import repo_query

        if len(set(self.created_ids)) != len(self.benchmark.sources):
            raise EvalIsolationError("created source ids are not unique / complete")
        rows = await repo_query(
            "SELECT VALUE id FROM source WHERE $tag IN topics",
            {"tag": self.benchmark.namespace_tag},
        )
        tagged = {str(r) for r in (rows or [])}
        missing = set(self.created_ids) - tagged
        if missing:
            raise EvalIsolationError("created synthetic sources missing benchmark tag")
        # Fail closed on residue: a benchmark-tagged source that is NOT one of ours
        # means a prior run leaked (or something else claimed the tag). We never
        # touch it — but surface it so the operator cleans up before proceeding,
        # rather than evaluating against contaminated state.
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
        for src in self.benchmark.sources:
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
    async def run(self) -> List[QueryEvaluation]:
        evaluations: List[QueryEvaluation] = []
        for q in self.benchmark.queries:
            relevant_ids = frozenset(
                self.key_to_source_id[k] for k in q.relevant_source_keys
            )
            vstate, vnorm, vdetail = await self._vector(q.text)
            gstate, gnorm, gdetail = await self._graph(q.text)
            evaluations.append(
                QueryEvaluation(
                    query_id=q.query_id,
                    query_class=q.query_class.value,
                    split=q.split.value,
                    is_negative=q.is_negative,
                    relevant_ids=relevant_ids,
                    vector_state=vstate,
                    graph_state=gstate,
                    vector=vnorm,
                    graph=gnorm,
                    vector_detail=vdetail,
                    graph_detail=gdetail,
                )
            )
        return evaluations

    async def _vector(
        self, question: str
    ) -> Tuple[str, Optional[NormalizedRetrieval], str]:
        from open_notebook.domain.notebook import vector_search

        try:
            rows = await vector_search(
                question,
                results=self.config.vector_fetch,
                source=True,
                note=True,
                minimum_score=self.config.minimum_score,
            )
        except Exception as exc:  # noqa: BLE001 - record, never abort the sweep
            return EvalState.RETRIEVER_ERROR, None, type(exc).__name__
        return EvalState.EVALUATED, normalize_vector_results(rows or []), ""

    async def _graph(
        self, question: str
    ) -> Tuple[str, Optional[NormalizedRetrieval], str]:
        try:
            result = await self.service.query_strict(  # type: ignore[attr-defined]
                question, mode=self.config.graph_mode, top_k=self.config.graph_top_k
            )
        except Exception as exc:  # noqa: BLE001 - record, never abort the sweep
            detail = str(exc).lower()
            state = (
                EvalState.TIMEOUT
                if "timed out" in detail or "timeout" in detail
                else EvalState.RETRIEVER_ERROR
            )
            return state, None, type(exc).__name__
        # Restrict graph candidates to benchmark sources: a structurally-valid
        # source id the sidecar might hold from outside this run is counted as
        # off_benchmark, never as a candidate.
        norm = normalize_graph_references(
            result.references, benchmark_ids=frozenset(self.created_ids)
        )
        return EvalState.EVALUATED, norm, ""

    # -- cleanup -------------------------------------------------------------
    async def cleanup(self) -> CleanupResult:
        from open_notebook.database.repository import repo_query

        errors: List[str] = []
        deleted = 0
        for canonical in self.created_ids:
            rid = record_id_for(canonical, tables=_SOURCE_TABLES)
            # Best-effort eager sidecar delete of ONLY this source's doc.
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

        # Verify no synthetic source WE created remains.
        rows = await repo_query(
            "SELECT VALUE id FROM source WHERE $tag IN topics",
            {"tag": self.benchmark.namespace_tag},
        )
        tagged = {str(r) for r in (rows or [])}
        remaining = tuple(sorted(tagged & set(self.created_ids)))
        return CleanupResult(
            deleted=deleted, remaining_synthetic_ids=remaining, errors=tuple(errors)
        )

    # -- metadata ------------------------------------------------------------
    async def build_metadata(self) -> Dict[str, object]:
        from open_notebook.database.repository import repo_query

        try:
            all_ids = await repo_query("SELECT VALUE id FROM source")
            db_total_sources: Optional[int] = len(all_ids or [])
        except Exception:  # noqa: BLE001
            db_total_sources = None

        return {
            "benchmark_version": self.benchmark.version,
            "baseline_names": ["VECTOR_BASELINE", "GRAPHRAG_BASELINE_CURRENT_HYBRID"],
            "git_commit": _git_commit(),
            "lightrag_version": VERIFIED_LIGHTRAG_VERSION,
            "graph_query_mode": self.config.graph_mode.value,
            "graph_top_k": self.config.graph_top_k,
            "vector_fetch": self.config.vector_fetch,
            "vector_minimum_score": self.config.minimum_score,
            "k_budgets": list(self.config.k_budgets),
            "corpus_size": len(self.benchmark.sources),
            "query_count": len(self.benchmark.queries),
            "dev_count": sum(1 for q in self.benchmark.queries if q.split.value == "dev"),
            "holdout_count": sum(
                1 for q in self.benchmark.queries if q.split.value == "holdout"
            ),
            "db_total_sources": db_total_sources,
            "benchmark_sources": len(self.created_ids),
            "id_namespace": f"source:{self.config.id_prefix}*",
            "namespace_tag": self.benchmark.namespace_tag,
            "synthetic_only": True,
            "answer_scored": False,
            "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        }


def _git_commit() -> Optional[str]:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        return out.stdout.strip() or None
    except Exception:  # noqa: BLE001
        return None


__all__ = [
    "EvalRunConfig",
    "GraphRAGEvalRunner",
    "CleanupResult",
    "EvalIsolationError",
    "IndexNotReadyError",
]
