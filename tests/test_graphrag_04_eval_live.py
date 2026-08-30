"""GraphRAG-04 live baseline + isolation/cleanup safety (synthetic data ONLY).

Two tiers:
  * LIVE-DB isolation/cleanup (skipped without SurrealDB): proves the runner
    touches ONLY the ids it created — foreign sources are never indexed or
    deleted, cleanup never global-purges (task §46 tests 37-40). Providers are
    stubbed so this tier needs no embedding/LLM credentials.
  * FULL LIVE baseline (skipped without a configured LightRAG sidecar): the real
    end-to-end §65 acceptance run — real vector embeddings, real LightRAG v1.5.6
    hybrid query, real provenance, cleanup verified. Synthetic corpus only.
"""

from __future__ import annotations

import os
import types
import uuid
from typing import Any, Callable, Optional

import pytest
import pytest_asyncio

from open_notebook.integrations.graphrag.eval import dataset as ds
from open_notebook.integrations.graphrag.eval import report as rp
from open_notebook.integrations.graphrag.eval.runner import (
    EvalRunConfig,
    GraphRAGEvalRunner,
)
from open_notebook.integrations.graphrag.models import (
    DeleteOutcome,
    DeleteState,
    GraphQueryResult,
    IndexAck,
    IndexState,
    IndexStatus,
    QueryMode,
    record_id_for,
)

_SOURCE_TABLES = frozenset({"source"})


async def _db_reachable() -> bool:
    try:
        from open_notebook.database.repository import repo_query

        await repo_query("RETURN 1")
        return True
    except Exception:
        return False


@pytest_asyncio.fixture
async def live_db():
    if not await _db_reachable():
        pytest.skip("SurrealDB not reachable")
    yield


class _FakeGraphService:
    """A GraphRAGService stand-in that needs no sidecar/provider.

    Records exactly which source ids were indexed and deleted so a test can prove
    the runner never reaches outside its created set.
    """

    def __init__(
        self, references_for: Optional[Callable[[str], list]] = None
    ) -> None:
        self.indexed: list[str] = []
        self.deleted: list[str] = []
        self._refs: Callable[[str], list] = references_for or (lambda q: [])

    async def index_source(self, *, source_id: str, canonical_text: str) -> IndexAck:
        self.indexed.append(source_id)
        return IndexAck(track_id=f"track-{len(self.indexed)}", accepted=True, detail="ok")

    async def track_status(self, track_id: str) -> IndexStatus:
        return IndexStatus(track_id=track_id, state=IndexState.PROCESSED, total_count=1)

    async def query_strict(
        self, question: str, *, mode: QueryMode = QueryMode.HYBRID, top_k=None
    ) -> GraphQueryResult:
        return GraphQueryResult(answer="(ignored)", references=self._refs(question))

    async def delete_document_for_source(self, *, source_id: str) -> DeleteOutcome:
        self.deleted.append(source_id)
        return DeleteOutcome(doc_id="doc-x", state=DeleteState.GONE, detail="ok")


def _stub_embed(monkeypatch):
    async def _fake_embed(input_data):
        return types.SimpleNamespace(
            success=True, chunks_created=1, error_message=None
        )

    monkeypatch.setattr(
        "commands.embedding_commands.embed_source_command", _fake_embed
    )


# ===========================================================================
# LIVE-DB isolation / cleanup safety (providers stubbed)  — task §46, §47
# ===========================================================================
@pytest.mark.asyncio
async def test_runner_isolation_and_cleanup_touch_only_created_ids(live_db, monkeypatch):
    from open_notebook.database.repository import repo_query

    _stub_embed(monkeypatch)
    bench = ds.load_benchmark()
    fake = _FakeGraphService()
    cfg = EvalRunConfig(id_prefix=f"gr04t{uuid.uuid4().hex[:8]}")
    runner = GraphRAGEvalRunner(bench, fake, cfg)

    # A FOREIGN source that must never be indexed, queried against, or deleted.
    foreign = record_id_for(f"source:foreign{uuid.uuid4().hex[:10]}", tables=_SOURCE_TABLES)
    await repo_query(
        "CREATE $id SET full_text = 'unrelated foreign content', title = 'foreign'",
        {"id": foreign},
    )
    try:
        await runner.create_and_index()

        # Every created id is unique, tag-stamped, and under our namespace.
        assert len(runner.created_ids) == len(bench.sources)
        assert str(foreign) not in runner.created_ids
        # The graph service only ever saw OUR ids (never the foreign one).
        assert set(fake.indexed) == set(runner.created_ids)
        assert str(foreign) not in fake.indexed

        cleanup = await runner.cleanup()
        # Cleanup deleted only ours; never touched the foreign source.
        assert set(fake.deleted) <= set(runner.created_ids)
        assert str(foreign) not in fake.deleted
        assert cleanup.clean, cleanup.remaining_synthetic_ids

        # Foreign source is still present — no global purge.
        rows = await repo_query("SELECT VALUE id FROM source WHERE id = $id", {"id": foreign})
        assert [str(r) for r in (rows or [])] == [str(foreign)]
        # None of our synthetic sources remain.
        mine = await repo_query(
            "SELECT VALUE id FROM source WHERE $tag IN topics",
            {"tag": bench.namespace_tag},
        )
        assert set(runner.created_ids).isdisjoint({str(r) for r in (mine or [])})
    finally:
        await repo_query("DELETE source_embedding WHERE source = $id", {"id": foreign})
        await repo_query("DELETE $id", {"id": foreign})
        # Defensive: remove any of our synthetic rows if the test aborted early.
        for canonical in runner.created_ids:
            rid = record_id_for(canonical, tables=_SOURCE_TABLES)
            await repo_query("DELETE source_embedding WHERE source = $id", {"id": rid})
            await repo_query("DELETE $id", {"id": rid})


@pytest.mark.asyncio
async def test_runner_end_to_end_metrics_shape_with_fake_retrievers(live_db, monkeypatch):
    """The full pipeline (create->index->retrieve->normalize->report) produces a
    well-formed, content-free report. Vector rows and graph refs are stubbed so
    this needs no providers; it checks plumbing/aggregation, not real quality."""
    _stub_embed(monkeypatch)
    bench = ds.load_benchmark()

    # Graph returns the first relevant source for each answerable query.
    key_to_id: dict[str, str] = {}

    def _refs(question: str):
        for q in bench.queries:
            if q.text == question and q.relevant_source_keys:
                sid = key_to_id.get(q.relevant_source_keys[0])
                if sid:
                    return [
                        types.SimpleNamespace(
                            source_id=sid, reference_id="r1", resolved=True, excerpts=[]
                        )
                    ]
        return []

    fake = _FakeGraphService(references_for=_refs)
    cfg = EvalRunConfig(id_prefix=f"gr04m{uuid.uuid4().hex[:8]}")
    runner = GraphRAGEvalRunner(bench, fake, cfg)

    # Vector returns the same first relevant source id at rank 1 (stubbed).
    async def _fake_vector(question, *, results, source, note, minimum_score):
        for q in bench.queries:
            if q.text == question and q.relevant_source_keys:
                sid = runner.key_to_source_id.get(q.relevant_source_keys[0])
                if sid:
                    return [{"parent_id": sid, "similarity": 0.9}]
        return []

    monkeypatch.setattr("open_notebook.domain.notebook.vector_search", _fake_vector)

    try:
        await runner.create_and_index()
        key_to_id.update(runner.key_to_source_id)
        evaluations = await runner.run()
        assert len(evaluations) == len(bench.queries)
        summary: Any = rp.summarize(evaluations)
        # Every answerable query got a rank-1 hit from the stub.
        assert summary["overall"]["VECTOR_BASELINE"]["hit@1"] == 1.0
        assert summary["overall"]["GRAPHRAG_BASELINE_CURRENT_HYBRID"]["source_hit_rate"] == 1.0
        # Rank metrics stay N/A for graph.
        assert "N/A" in str(
            summary["overall"]["GRAPHRAG_BASELINE_CURRENT_HYBRID"]["rank_metrics"]
        )
        artifact = rp.build_artifact(await runner.build_metadata(), evaluations)
        import json as _json

        blob = _json.dumps(artifact)
        for banned in ("full_text", "api_key", "authorization", '"answer"', '"content"'):
            assert banned.lower() not in blob.lower()
    finally:
        await runner.cleanup()


# ===========================================================================
# FULL LIVE baseline — real LightRAG v1.5.6 + real embeddings (task §65)
# ===========================================================================
def _live_service_or_skip():
    base = os.environ.get("OPEN_NOTEBOOK_GRAPHRAG_BASE_URL", "").strip()
    enabled = os.environ.get("OPEN_NOTEBOOK_GRAPHRAG_ENABLED", "").lower() in {
        "1", "true", "yes",
    }
    if not base or not enabled:
        pytest.skip(
            "live LightRAG not configured "
            "(set OPEN_NOTEBOOK_GRAPHRAG_ENABLED=true + OPEN_NOTEBOOK_GRAPHRAG_BASE_URL)"
        )
    from open_notebook.integrations.graphrag.service import GraphRAGService

    return GraphRAGService()


@pytest.mark.asyncio
async def test_full_live_baseline_synthetic(live_db):
    """Real end-to-end synthetic baseline. Writes a content-free artifact and
    verifies clean teardown. Skipped unless DB + sidecar + providers are live."""
    service = _live_service_or_skip()
    health = await service.health()
    if not health.healthy:
        pytest.skip(f"LightRAG sidecar unhealthy: {health.detail}")

    # The vector side needs a configured embedding model; skip (don't fail) when
    # none is set up, exactly like the sidecar gate. This live baseline is an
    # operator-run acceptance check, not a CI test.
    from open_notebook.ai.models import model_manager

    if await model_manager.get_embedding_model() is None:
        pytest.skip("no default embedding model configured (live baseline)")

    from pathlib import Path

    bench = ds.load_benchmark()
    cfg = EvalRunConfig(id_prefix=f"gr04L{uuid.uuid4().hex[:8]}")
    runner = GraphRAGEvalRunner(bench, service, cfg)
    try:
        await runner.create_and_index()
        evaluations = await runner.run()
        assert len(evaluations) == len(bench.queries)
        metadata = await runner.build_metadata()
        artifact = rp.build_artifact(metadata, evaluations)
        run_id = uuid.uuid4().hex[:12]
        out = Path(".artifacts") / "graphrag-04" / run_id / "evaluation.json"
        rp.write_artifact(out, artifact)
        assert out.exists()
        # At least one query evaluated on each retriever (real run smoke check).
        assert any(e.vector_state == rp.EvalState.EVALUATED for e in evaluations)
        assert any(e.graph_state == rp.EvalState.EVALUATED for e in evaluations)
    finally:
        cleanup = await runner.cleanup()
        assert cleanup.clean, f"synthetic sources left behind: {cleanup.remaining_synthetic_ids}"
