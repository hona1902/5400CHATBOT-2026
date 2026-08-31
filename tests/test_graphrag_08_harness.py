"""GraphRAG-08 eval-harness tests (offline; mock transport, no DB, no provider).

Covers the GD /query/data seam parsing + vendor-schema containment (task §31-§34),
the content-free artifact (task §44-§45, §79), GQ/GD parity accounting (task §38),
and the DEV-only micro-precheck subset selection under the hard caps (task §61).
"""

from __future__ import annotations

import asyncio
import dataclasses
import json

import httpx

from open_notebook.integrations.graphrag.config import GraphRAGConfig
from open_notebook.integrations.graphrag.eval import dataset08 as d
from open_notebook.integrations.graphrag.eval import report08 as r
from open_notebook.integrations.graphrag.eval.gd_seam import (
    GD_DEGRADED,
    GD_EMPTY,
    GDEvalError,
    GDEvidence,
    GDQueryClient,
)
from open_notebook.integrations.graphrag.eval.normalize import (
    NormalizedRetrieval,
    ProvenanceStats,
    canonical_source_id,
)
from open_notebook.integrations.graphrag.eval.runner08 import (
    STATE_EVALUATED,
    QueryEvaluation08,
    select_precheck_subset,
)


def _cfg() -> GraphRAGConfig:
    return GraphRAGConfig(
        enabled=True, base_url="http://sidecar.test", timeout=5.0, api_key=None
    )


def _mock(payload: dict, *, status: int = 200) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/query/data"
        return httpx.Response(status, json=payload)

    return httpx.MockTransport(handler)


# -- GD seam: STRONG-anchor projection + containment -------------------------

def test_gd_seam_projects_strong_anchors_and_drops_the_rest():
    good_a = canonical_source_id("source:gr08e00")
    good_b = canonical_source_id("source:gr08e01")
    off = canonical_source_id("source:outsider99")  # valid source id, not allowlisted
    allow = frozenset({good_a, good_b})
    payload = {
        "status": "success",
        "data": {
            "chunks": [
                {"file_path": "source:gr08e00", "content": "SECRET CHUNK TEXT"},
                {"file_path": "source:gr08e00", "content": "dup of A"},
                {"file_path": "source:outsider99", "content": "off-benchmark"},
            ],
            "references": [
                {"file_path": "source:gr08e01", "reference_id": "1"},
                {"file_path": "note:xyz", "reference_id": "2"},  # foreign table
                {"file_path": "../../secret", "reference_id": "3"},  # malformed
            ],
            "entities": [{"entity_name": "X", "file_path": "unknown_source"}],
            "relationships": [{"src_id": "X", "tgt_id": "Y", "weight": 1.0}],
        },
        "metadata": {"query_mode": "hybrid"},
    }
    client = GDQueryClient(_cfg(), transport=_mock(payload))
    ev: GDEvidence = asyncio.run(
        client.query_evidence("q", benchmark_ids=allow)
    )
    assert ev.as_set() == {good_a, good_b}
    st = ev.retrieval.stats
    assert st.duplicates == 1  # second chunk for A
    assert st.foreign == 1  # note:xyz
    assert st.malformed == 1  # ../../secret
    assert st.off_benchmark == 1  # source:outsider99 not in allowlist
    assert off not in ev.as_set()
    assert ev.entity_count == 1 and ev.relationship_count == 1
    assert ev.chunk_count == 3 and ev.reference_count == 3
    assert ev.final_answer_generation is False
    assert ev.status == GD_DEGRADED  # drops present


def test_gd_result_is_content_free():
    # The GDEvidence dataclass exposes only content-free fields — no chunk text,
    # entity/relation descriptions, or raw payload can escape the seam.
    field_names = {f.name for f in dataclasses.fields(GDEvidence)}
    assert field_names == {
        "retrieval",
        "status",
        "raw_evidence_present",
        "entity_count",
        "relationship_count",
        "chunk_count",
        "reference_count",
        "final_answer_generation",
        "latency_ms",
    }


def test_gd_empty_result():
    payload = {"status": "failure", "message": "no context", "data": {}}
    client = GDQueryClient(_cfg(), transport=_mock(payload))
    ev = asyncio.run(client.query_evidence("q", benchmark_ids=frozenset()))
    assert ev.status == GD_EMPTY
    assert ev.as_set() == frozenset()
    assert ev.raw_evidence_present is False


def test_gd_http_error_raises_content_free():
    client = GDQueryClient(_cfg(), transport=_mock({"detail": "boom"}, status=500))
    try:
        asyncio.run(client.query_evidence("q", benchmark_ids=frozenset()))
        assert False, "expected GDEvalError"
    except GDEvalError as exc:
        assert "boom" not in str(exc)  # body never echoed


def test_gd_transport_error_raises():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    client = GDQueryClient(_cfg(), transport=httpx.MockTransport(handler))
    try:
        asyncio.run(client.query_evidence("q", benchmark_ids=frozenset()))
        assert False, "expected GDEvalError"
    except GDEvalError:
        pass


# -- report artifact: content-free + structure -------------------------------

def _norm(ids, ordered):
    return NormalizedRetrieval(
        source_ids=tuple(ids),
        ordered=ordered,
        stats=ProvenanceStats(
            total=len(ids),
            valid_unique=len(ids),
            duplicates=0,
            malformed=0,
            foreign=0,
            off_benchmark=0,
        ),
    )


def _gd(ids):
    return GDEvidence(
        retrieval=_norm(ids, ordered=False),
        status="SUCCESS" if ids else GD_EMPTY,
        raw_evidence_present=bool(ids),
        entity_count=0,
        relationship_count=0,
        chunk_count=len(ids),
        reference_count=len(ids),
        final_answer_generation=False,
        latency_ms=12,
    )


def _mk_eval(qid, cls, answerable, required, vec, gq, gd):
    return QueryEvaluation08(
        query_id=qid,
        query_class=cls,
        split="dev",
        answerable=answerable,
        required_ids=frozenset(required),
        allowed_ids=frozenset(required),
        vector_state=STATE_EVALUATED,
        gq_state=STATE_EVALUATED,
        gd_state=STATE_EVALUATED,
        vector=_norm(vec, ordered=True),
        gq=_norm(gq, ordered=False),
        gd=_gd(gd),
        vector_latency_ms=5,
        gq_latency_ms=40,
        gd_latency_ms=20,
    )


def test_report_artifact_is_content_free_and_structured():
    evals = [
        _mk_eval("Q1", "two_hop", True, ["A", "B"], ["A", "B", "C"], ["A", "B"], ["A", "B"]),
        _mk_eval("Q2", "negative_unanswerable", False, [], ["Z"], ["Z"], []),
    ]
    artifact = r.build_artifact(
        run_metadata={"run_id": "test-run", "benchmark_version": "graphrag_08_eval_v1"},
        evaluations=evals,
        corpus_size=8,
    )
    meta = artifact["metadata"]
    assert meta["run_type"] == "MICRO_PRECHECK"
    assert meta["value_run"] is False
    assert meta["holdout_used"] is False
    assert meta["full_benchmark_executed"] is False

    summary = artifact["summary"]
    assert summary["candidate_fraction_denominator"] == 8
    assert r.VECTOR_SYSTEM in summary and r.GQ_SYSTEM in summary and r.GD_SYSTEM in summary
    assert "gq_gd_parity" in summary and "complementarity" in summary
    # Vector recovered the full two-source set at k>=2.
    assert summary[r.VECTOR_SYSTEM]["full_set_recovered@3"] == 1.0
    # GD final-answer invariant surfaced (holds = every GD call had it False).
    assert (
        summary[r.GD_SYSTEM]["gd_diagnostics"][
            "final_answer_generation_invariant_holds"
        ]
        is True
    )

    # Content-free: the serialized artifact carries no text keys.
    blob = json.dumps(artifact)
    assert "SECRET" not in blob
    per_query_keys = set(artifact["per_query"][0].keys())
    assert "text" not in per_query_keys and "rationale" not in per_query_keys
    assert {"query_id", "query_class", "split", "answerable"} <= per_query_keys


def test_parity_counts_gq_vs_gd():
    evals = [
        _mk_eval("Q1", "two_hop", True, ["A", "B"], ["A", "B"], ["A", "B"], ["A", "B"]),
        _mk_eval("Q2", "two_hop", True, ["A", "B"], ["A", "B"], ["A"], ["A", "B"]),
    ]
    summary = r.summarize(r.ReportInputs(evals, corpus_size=8))
    parity = summary["gq_gd_parity"]
    assert parity["paired_count"] == 2
    assert parity["gq_eq_gd_count"] == 1  # Q1 sets equal; Q2 differs (gq={A}, gd={A,B})
    assert parity["gd_only_total"] == 1  # Q2: B present in gd, absent in gq
    assert parity["gq_only_total"] == 0


# -- micro-precheck subset selection (task §61) ------------------------------

def test_precheck_subset_respects_caps_and_is_dev_only():
    bench = d.load_benchmark08()
    source_keys, query_ids = select_precheck_subset(
        bench, max_sources=8, max_queries=6
    )
    assert 1 <= len(query_ids) <= 6
    assert len(source_keys) <= 8
    selected = {q.query_id: q for q in bench.queries}
    for qid in query_ids:
        assert selected[qid].split is d.Split.DEV  # never HOLDOUT
    # At least one negative (empty-GT) query is reachable and included.
    classes = {selected[qid].query_class for qid in query_ids}
    assert d.QueryClass08.NEGATIVE_UNANSWERABLE in classes
    # Every selected answerable query's required sources fit within the subset.
    subset = set(source_keys)
    for qid in query_ids:
        q = selected[qid]
        assert set(q.required_source_keys) <= subset
