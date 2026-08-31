"""GraphRAG-08 fixture validation tests (offline; no DB, no provider, no network).

Enforces the frozen shape and the authoring gates that must hold BEFORE any
provider-backed run (task §24, §25, §56): counts, split balance, GT integrity,
multi-hop cardinality (R1 structural), negative emptiness (R2 structural), no
runtime record ids baked in as truth, and the integrity freeze.
"""

from __future__ import annotations

import pytest

from open_notebook.integrations.graphrag.eval import dataset08 as d


@pytest.fixture(scope="module")
def bench() -> d.Benchmark08:
    return d.load_benchmark08()


def test_loads_and_frozen_shape(bench):
    d.validate_frozen_shape(bench)  # raises on any deviation
    assert len(bench.sources) == d.FROZEN_SOURCE_COUNT == 75
    assert len(bench.queries) == d.FROZEN_QUERY_COUNT == 60


def test_split_counts_and_balance(bench):
    dev = bench.queries_for_split(d.Split.DEV)
    holdout = bench.queries_for_split(d.Split.HOLDOUT)
    assert len(dev) == 30 and len(holdout) == 30
    dev_ids = {q.query_id for q in dev}
    holdout_ids = {q.query_id for q in holdout}
    assert dev_ids.isdisjoint(holdout_ids)
    assert dev_ids | holdout_ids == {q.query_id for q in bench.queries}
    # Every class present in both splits.
    for qc in d.QueryClass08:
        assert any(q.query_class is qc and q.split is d.Split.DEV for q in bench.queries)
        assert any(
            q.query_class is qc and q.split is d.Split.HOLDOUT for q in bench.queries
        )


def test_source_keys_unique_and_not_runtime_ids(bench):
    keys = [s.key for s in bench.sources]
    assert len(keys) == len(set(keys))
    for k in keys:
        assert ":" not in k  # no "source:..." runtime id baked in as truth (§8)


def test_query_ids_unique(bench):
    ids = [q.query_id for q in bench.queries]
    assert len(ids) == len(set(ids))


def test_multihop_cardinality_r1_structural(bench):
    # A two_hop needs >=2 required sources; a three_hop needs >=3 (task §18/§82).
    for q in bench.queries:
        if q.query_class is d.QueryClass08.TWO_HOP:
            assert len(q.required_source_keys) >= 2, q.query_id
        if q.query_class is d.QueryClass08.THREE_HOP_CROSS_SOURCE:
            assert len(q.required_source_keys) >= 3, q.query_id


def test_negatives_empty_and_labeled_r2_structural(bench):
    negatives = [q for q in bench.queries if not q.answerable]
    assert len(negatives) == d.FROZEN_NEGATIVE_COUNT == 12
    for q in negatives:
        assert q.required_source_keys == ()  # empty GT (task §41)
        assert q.optional_support_source_keys == ()
        assert q.design_label is d.DesignLabel.NEGATIVE
        assert q.negative_construction is not None
        assert q.query_class in d.NEGATIVE_CLASSES


def test_all_negative_constructions_present(bench):
    used = {q.negative_construction for q in bench.queries if q.negative_construction}
    assert set(d.NegativeConstruction) <= used


def test_answerable_have_required_and_no_negctor(bench):
    for q in bench.queries:
        if q.answerable:
            assert len(q.required_source_keys) >= 1, q.query_id
            assert q.negative_construction is None
            assert q.design_label is not d.DesignLabel.NEGATIVE
            # required and optional are disjoint
            assert set(q.required_source_keys).isdisjoint(q.optional_support_source_keys)


def test_all_gt_keys_exist(bench):
    keyset = bench.source_keys
    for q in bench.queries:
        for k in q.required_source_keys + q.optional_support_source_keys:
            assert k in keyset, (q.query_id, k)


def test_every_query_has_rationale(bench):
    for q in bench.queries:
        assert q.rationale.strip()


def test_integrity_freeze_matches(bench):
    ok, detail = d.verify_integrity()
    assert ok, detail
    freeze = d.load_freeze()
    assert freeze.get("frozen") is True
    assert freeze.get("source_count") == 75
    assert freeze.get("query_count") == 60
    assert freeze.get("negative_count") == 12


def test_frozen_shape_rejects_mutated_counts():
    # A benchmark missing sources must fail the frozen-shape gate.
    bench = d.load_benchmark08()
    trimmed = d.Benchmark08(
        version=bench.version,
        namespace_tag=bench.namespace_tag,
        sources=bench.sources[:-1],
        queries=bench.queries,
    )
    with pytest.raises(d.Benchmark08Error):
        d.validate_frozen_shape(trimmed)
