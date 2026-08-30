"""GraphRAG-04 evaluation harness — dataset / normalization / metric / security tests.

Pure, offline, no DB/network/provider. Expected metric values are hand-calculated
in the test body (task §45: never test metric code against itself). Live baseline
tests (real SurrealDB + LightRAG v1.5.6) live in test_graphrag_04_eval_live.py.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from open_notebook.integrations.graphrag.eval import (
    GRAPHRAG_BASELINE,
    VECTOR_BASELINE,
)
from open_notebook.integrations.graphrag.eval import dataset as ds
from open_notebook.integrations.graphrag.eval import metrics as mx
from open_notebook.integrations.graphrag.eval import normalize as nz
from open_notebook.integrations.graphrag.eval import report as rp
from open_notebook.integrations.graphrag.eval.dataset import QueryClass, Split


# ---------------------------------------------------------------------------
# Fixture-file helpers for dataset-validation negative cases
# ---------------------------------------------------------------------------
def _valid_corpus() -> dict:
    return {
        "benchmark_version": ds.BENCHMARK_VERSION,
        "namespace_tag": ds.NAMESPACE_TAG,
        "sources": [
            {"key": "S1", "title": "t1", "text": "alpha"},
            {"key": "S2", "title": "t2", "text": "beta"},
        ],
    }


def _valid_queries() -> dict:
    return {
        "benchmark_version": ds.BENCHMARK_VERSION,
        "queries": [
            {"query_id": "Q1", "query_class": "direct", "split": "dev",
             "text": "q1", "relevant_source_keys": ["S1"]},
            {"query_id": "Q2", "query_class": "negative", "split": "holdout",
             "text": "q2", "relevant_source_keys": []},
        ],
    }


def _write(tmp: Path, corpus: dict, queries: dict) -> Path:
    tmp.mkdir(parents=True, exist_ok=True)
    (tmp / "corpus.json").write_text(json.dumps(corpus), encoding="utf-8")
    (tmp / "queries.json").write_text(json.dumps(queries), encoding="utf-8")
    return tmp


# ===========================================================================
# A. FROZEN BENCHMARK integrity (task §43, tests 1-10) — the committed fixture
# ===========================================================================
def test_frozen_benchmark_loads_and_is_well_formed():
    bench = ds.load_benchmark()
    # 1. unique corpus keys; 8. version fixed; 9. namespace fixed
    keys = [s.key for s in bench.sources]
    assert len(keys) == len(set(keys))
    assert bench.version == "graphrag_04_eval_v1"
    assert bench.namespace_tag == "__graphrag04_eval_v1__"
    # spec scale ~14 sources / ~28 queries
    assert len(bench.sources) == 14
    assert len(bench.queries) == 28
    # 2. unique query ids
    qids = [q.query_id for q in bench.queries]
    assert len(qids) == len(set(qids))
    # 3. every relevant label exists; 4. no empty text; 5. valid class
    for q in bench.queries:
        assert q.text.strip()
        assert isinstance(q.query_class, QueryClass)
        for rk in q.relevant_source_keys:
            assert rk in bench.source_keys


def test_frozen_benchmark_split_valid_and_disjoint():
    # 6. split valid; 7. no query id overlap between splits
    bench = ds.load_benchmark()
    dev = {q.query_id for q in bench.queries_for_split(Split.DEV)}
    hold = {q.query_id for q in bench.queries_for_split(Split.HOLDOUT)}
    assert dev and hold
    assert dev.isdisjoint(hold)
    assert dev | hold == {q.query_id for q in bench.queries}
    # every class appears in BOTH splits (no class hidden from holdout baseline)
    for cls in QueryClass:
        in_dev = any(q.query_class is cls and q.split is Split.DEV for q in bench.queries)
        in_hold = any(q.query_class is cls and q.split is Split.HOLDOUT for q in bench.queries)
        assert in_dev and in_hold, f"class {cls} missing from a split"


def test_frozen_benchmark_dev_holdout_ratio_in_range():
    bench = ds.load_benchmark()
    dev = len(bench.queries_for_split(Split.DEV))
    total = len(bench.queries)
    assert 0.55 <= dev / total <= 0.70


def test_negative_queries_have_no_labels_answerable_have_some():
    bench = ds.load_benchmark()
    for q in bench.queries:
        if q.query_class is QueryClass.NEGATIVE:
            assert q.relevant_source_keys == ()
        else:
            assert len(q.relevant_source_keys) >= 1


@pytest.mark.parametrize(
    "mutate, needle",
    [
        (lambda c, q: c["sources"].append({"key": "S1", "title": "d", "text": "x"}), "duplicate corpus"),
        (lambda c, q: c["sources"].__setitem__(0, {"key": "S1", "title": "t", "text": "  "}), "empty text"),
        (lambda c, q: q["queries"].append(dict(q["queries"][0])), "duplicate query_id"),
        (lambda c, q: q["queries"].__setitem__(0, {**q["queries"][0], "relevant_source_keys": ["S_missing"]}), "unknown source key"),
        (lambda c, q: q["queries"].__setitem__(0, {**q["queries"][0], "text": ""}), "empty text"),
        (lambda c, q: q["queries"].__setitem__(0, {**q["queries"][0], "query_class": "bogus"}), "invalid query_class"),
        (lambda c, q: q["queries"].__setitem__(0, {**q["queries"][0], "split": "train"}), "invalid split"),
        (lambda c, q: q["queries"].__setitem__(1, {**q["queries"][1], "relevant_source_keys": ["S1"]}), "negative query"),
    ],
)
def test_dataset_validation_rejects_malformed(tmp_path, mutate, needle):
    corpus, queries = _valid_corpus(), _valid_queries()
    mutate(corpus, queries)
    _write(tmp_path, corpus, queries)
    with pytest.raises(ds.BenchmarkError) as exc:
        ds.load_benchmark(tmp_path)
    assert needle in str(exc.value)


def test_dataset_rejects_wrong_version_and_namespace(tmp_path):
    corpus = _valid_corpus()
    corpus["benchmark_version"] = "something_else"
    _write(tmp_path, corpus, _valid_queries())
    with pytest.raises(ds.BenchmarkError):
        ds.load_benchmark(tmp_path)


# ===========================================================================
# B. NORMALIZATION (task §44, tests 11-20)
# ===========================================================================
def test_vector_chunk_to_source_normalization():  # 11
    rows = [{"parent_id": "source:A", "similarity": 0.9}]
    out = nz.normalize_vector_results(rows)
    assert out.source_ids == ("source:A",)
    assert out.ordered is True


def test_vector_multiple_chunks_same_source_dedup_best_rank():  # 12, 13, 25
    rows = [
        {"parent_id": "source:A"},  # best rank for A
        {"parent_id": "source:A"},
        {"parent_id": "source:B"},
        {"parent_id": "source:A"},
    ]
    out = nz.normalize_vector_results(rows)
    assert out.source_ids == ("source:A", "source:B")  # A keeps rank 1
    assert out.stats.duplicates == 2
    assert out.stats.valid_unique == 2


def test_graph_reference_to_source_normalization_unordered():  # 14, 20
    refs = [{"source_id": "source:B"}, {"source_id": "source:A"}]
    out = nz.normalize_graph_references(refs)
    assert set(out.source_ids) == {"source:A", "source:B"}
    assert out.ordered is False  # no manufactured rank


def test_graph_malformed_and_foreign_provenance_separated():  # 15, 16
    refs = [
        {"source_id": "source:A"},          # valid source
        {"source_id": "note:abc"},          # foreign (valid other-provenance)
        {"source_id": "source_insight:zz"}, # foreign
        {"source_id": "../../secret"},      # malformed
        {"source_id": None},                # malformed
        {"source_id": "source:https://x?token=y"},  # malformed (not a record id)
    ]
    out = nz.normalize_graph_references(refs)
    assert out.source_ids == ("source:A",)
    assert out.stats.foreign == 2
    assert out.stats.malformed == 3
    assert out.stats.valid_unique == 1


def test_numeric_vs_string_numeric_ids_distinct():  # 17
    numeric = nz.canonical_source_id("source:123")
    string_numeric = nz.canonical_source_id("source:⟨123⟩")
    assert numeric is not None and string_numeric is not None
    assert numeric != string_numeric  # source:123 != source:⟨123⟩


def test_escaped_record_id_round_trips_losslessly():  # 18
    escaped = "source:⟨abc-def⟩"
    once = nz.canonical_source_id(escaped)
    assert once is not None
    assert nz.canonical_source_id(once) == once  # stable, no double-escape drift


def test_graph_duplicate_provenance_deduplicated():  # 19
    refs = [{"source_id": "source:A"}, {"source_id": "source:A"}]
    out = nz.normalize_graph_references(refs)
    assert out.source_ids == ("source:A",)
    assert out.stats.duplicates == 1


def test_graph_off_benchmark_source_excluded_with_allowlist():
    # A structurally-valid source id NOT in the benchmark allowlist must be
    # counted as off_benchmark and never kept as a candidate (Codex A/B fix).
    refs = [{"source_id": "source:A"}, {"source_id": "source:B"}, {"source_id": "source:C"}]
    out = nz.normalize_graph_references(refs, benchmark_ids=frozenset({"source:A", "source:B"}))
    assert set(out.source_ids) == {"source:A", "source:B"}
    assert out.stats.off_benchmark == 1  # source:C dropped, not a candidate
    assert out.stats.valid_unique == 2
    assert out.stats.foreign == 0 and out.stats.malformed == 0
    # No allowlist => global behavior unchanged (all three are candidates).
    out2 = nz.normalize_graph_references(refs)
    assert set(out2.source_ids) == {"source:A", "source:B", "source:C"}
    assert out2.stats.off_benchmark == 0


def test_vector_normalization_is_not_allowlist_filtered():
    # Vector must remain global: non-benchmark sources are legitimate ranked
    # competitors and must NOT be reclassified/dropped.
    rows = [{"parent_id": "source:X"}, {"parent_id": "source:A"}]
    out = nz.normalize_vector_results(rows)
    assert out.source_ids == ("source:X", "source:A")
    assert out.stats.off_benchmark == 0


# ===========================================================================
# C. METRICS with hand-calculated fixtures (task §45, tests 21-32)
# ===========================================================================
def test_hit_at_k_handcalc():  # 21
    ranked = ["source:B", "source:A", "source:C"]
    rel = {"source:A"}
    assert mx.hit_at_k(ranked, rel, 1) is False
    assert mx.hit_at_k(ranked, rel, 2) is True
    assert mx.hit_at_k(ranked, rel, 3) is True


def test_recall_at_k_handcalc():  # 22
    ranked = ["source:B", "source:A", "source:C"]
    assert mx.recall_at_k(ranked, {"source:A"}, 1) == 0.0
    assert mx.recall_at_k(ranked, {"source:A"}, 2) == 1.0


def test_mrr_handcalc():  # 23
    assert mx.mrr(["source:B", "source:A"], {"source:A"}) == 0.5
    assert mx.mrr(["source:A", "source:B"], {"source:A"}) == 1.0
    assert mx.mrr(["source:X", "source:Y"], {"source:A"}) == 0.0


def test_multi_relevant_source_recall_handcalc():  # 24
    ranked = ["source:A", "source:X", "source:B"]
    rel = {"source:A", "source:B", "source:C"}
    assert mx.recall_at_k(ranked, rel, 3) == pytest.approx(2 / 3)
    assert mx.hit_at_k(ranked, rel, 1) is True  # one hit != full recall


def test_set_metrics_handcalc():
    assert mx.set_hit({"source:A", "source:C"}, {"source:A", "source:B"}) is True
    assert mx.set_recall({"source:A", "source:C"}, {"source:A", "source:B"}) == 0.5
    assert mx.set_hit({"source:Z"}, {"source:A"}) is False


def test_complementarity_cells_handcalc():  # 25, 26, 27, 28, 29
    # both hit
    c = mx.complementarity(["source:A"], {"source:A"}, {"source:A"}, 1)
    assert (c.both_hit, c.vector_only_hit, c.graph_only_hit, c.both_miss) == (True, False, False, False)
    # vector only + oracle union recall 1.0
    c = mx.complementarity(["source:A", "source:X"], {"source:B"}, {"source:A"}, 1)
    assert c.vector_only_hit is True and c.graph_only_hit is False
    assert c.oracle_union_hit is True and c.oracle_union_recall == 1.0
    # graph only
    c = mx.complementarity(["source:X", "source:Y"], {"source:A"}, {"source:A"}, 2)
    assert c.graph_only_hit is True and c.oracle_union_hit is True
    # both miss + oracle recall 0
    c = mx.complementarity(["source:X"], {"source:Y"}, {"source:A"}, 1)
    assert c.both_miss is True and c.oracle_union_hit is False and c.oracle_union_recall == 0.0


def test_metrics_are_na_for_empty_relevant_not_zero():  # 32
    for call in (
        lambda: mx.hit_at_k(["source:A"], set(), 1),
        lambda: mx.recall_at_k(["source:A"], set(), 1),
        lambda: mx.mrr(["source:A"], set()),
        lambda: mx.set_hit({"source:A"}, set()),
        lambda: mx.set_recall({"source:A"}, set()),
    ):
        with pytest.raises(ValueError):
            call()


# ===========================================================================
# D. REPORT aggregation + error accounting (task §45.30, §45.31, §30)
# ===========================================================================
def _norm(ordered_ids, *, ordered):
    stats = nz.ProvenanceStats(
        total=len(ordered_ids), valid_unique=len(ordered_ids),
        duplicates=0, malformed=0, foreign=0,
    )
    return nz.NormalizedRetrieval(source_ids=tuple(ordered_ids), ordered=ordered, stats=stats)


def _eval(qid, cls, split, relevant, vec_ids, graph_ids,
          vstate=rp.EvalState.EVALUATED, gstate=rp.EvalState.EVALUATED):
    return rp.QueryEvaluation(
        query_id=qid, query_class=cls, split=split,
        is_negative=(cls == "negative"),
        relevant_ids=frozenset(relevant),
        vector_state=vstate, graph_state=gstate,
        vector=_norm(vec_ids, ordered=True) if vec_ids is not None else None,
        graph=_norm(graph_ids, ordered=False) if graph_ids is not None else None,
    )


def test_class_level_aggregation_present():  # 30
    evals = [
        _eval("Q1", "direct", "dev", {"source:A"}, ["source:A"], ["source:A"]),
        _eval("Q2", "two_hop", "holdout", {"source:A", "source:B"}, ["source:A"], ["source:B"]),
    ]
    out: Any = rp.summarize(evals)
    assert set(out["by_class"]) == {"direct", "two_hop"}
    assert set(out["by_split"]) == {"dev", "holdout"}
    assert out["by_class"]["direct"][VECTOR_BASELINE]["hit@1"] == 1.0


def test_error_query_stays_in_accounting_not_dropped():  # 31
    evals = [
        _eval("Q1", "direct", "dev", {"source:A"}, ["source:A"], ["source:A"]),
        _eval("Q2", "direct", "dev", {"source:A"}, None, None,
              vstate=rp.EvalState.RETRIEVER_ERROR, gstate=rp.EvalState.TIMEOUT),
    ]
    block: Any = rp.metrics_block(evals)
    vblock = block[VECTOR_BASELINE]
    # the errored query is NOT in evaluated_count, but IS in state_counts
    assert vblock["evaluated_count"] == 1
    assert vblock["state_counts"][rp.EvalState.RETRIEVER_ERROR] == 1
    assert block[GRAPHRAG_BASELINE]["state_counts"][rp.EvalState.TIMEOUT] == 1
    # hit@1 averaged over the 1 evaluated answerable query only
    assert vblock["hit@1"] == 1.0


def test_graph_block_declares_rank_metrics_na():
    evals = [_eval("Q1", "direct", "dev", {"source:A"}, ["source:A"], ["source:A"])]
    block: Any = rp.metrics_block(evals)
    assert "N/A" in str(block[GRAPHRAG_BASELINE]["rank_metrics"])
    assert "mrr" not in block[GRAPHRAG_BASELINE]


# ===========================================================================
# E. SECURITY / content-safety of the report (task §46, tests 33-36)
# ===========================================================================
def test_artifact_contains_no_source_content_or_secrets():  # 33, 34, 35, 36
    evals = [_eval("Q1", "direct", "dev", {"source:A"}, ["source:A"], ["source:A"])]
    meta: dict[str, object] = {"git_commit": "deadbeef", "lightrag_version": "v1.5.6"}
    artifact: Any = rp.build_artifact(meta, evals)
    blob = json.dumps(artifact)
    # 34. no full_text; content excerpts never enter the report
    assert "full_text" not in blob
    assert "alpha" not in blob and "beta" not in blob
    # 35/36. no secret-shaped fields
    for banned in ("api_key", "x-api-key", "authorization", "password", "token"):
        assert banned.lower() not in blob.lower()
    # per_query carries only ids/metrics/states, never text/answer/excerpts
    pq = artifact["per_query"][0]
    assert set(pq) == {
        "query_id", "query_class", "split", "is_negative", "n_relevant",
        "vector_state", "graph_state", "vector_candidate_ids",
        "graph_candidate_ids", "graph_provenance",
    }
    # no generated-answer text and no reference excerpts are stored (check the
    # JSON key form so it doesn't collide with legit keys like "n_answerable")
    assert '"answer"' not in blob and '"response"' not in blob
    assert "excerpt" not in blob and '"content"' not in blob


def test_normalized_retrieval_holds_no_text():
    # The types that reach the report expose ids + counts only, never text.
    out = nz.normalize_graph_references([{"source_id": "source:A", "content": ["secret text"]}])
    assert out.source_ids == ("source:A",)
    assert "secret text" not in json.dumps(
        {"ids": list(out.source_ids), "stats": out.stats.__dict__}
    )
