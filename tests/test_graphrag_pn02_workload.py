"""GraphRAG-PN02 workload ledger tests (task §35-§38).

OFFLINE. Verifies the frozen caps, the exact arithmetic, the fixture cross-check,
and the cap-enforcement primitive.
"""

from __future__ import annotations

import dataclasses

import pytest

from open_notebook.integrations.graphrag.eval import datasetpn02 as ds
from open_notebook.integrations.graphrag.eval import workloadpn02 as w


def test_frozen_ledger_validates() -> None:
    led = w.frozen_ledger()
    led.validate()  # raises on inconsistency


def test_exact_caps() -> None:
    led = w.frozen_ledger()
    assert led.planned_graph_index_operations == 24
    assert led.max_index_attempts_per_operation == 2
    assert led.max_graph_index_attempts == 48
    assert led.graph_delete_operations == 1
    assert led.max_canonical_source_embeddings == 21
    assert led.max_gd_queries == 26
    assert led.max_vector_query_operations == 26
    assert led.max_query_embedding_operations == 26
    assert led.max_final_answer_calls == 72
    assert led.judge_model_calls == 0
    assert led.final_answer_retries == 0
    assert led.max_query_technical_retries == 1


def test_arithmetic_breakdown() -> None:
    led = w.frozen_ledger()
    assert led.max_graph_index_attempts == led.planned_graph_index_operations * led.max_index_attempts_per_operation
    assert led.max_gd_queries == led.baseline_stage1_gd_queries + led.membership_removal_gd_queries
    assert led.max_vector_query_operations == led.baseline_stage1_vector_queries + led.membership_removal_vector_queries
    assert led.stage2_final_answer_calls == 24 * 3
    assert led.membership_removal_final_answer == 0
    assert led.membership_removal_reindex_ops == 0


def test_validate_against_fixture() -> None:
    fx = ds.load_fixture()
    led = w.frozen_ledger()
    w.validate_against_fixture(led, fx)  # raises on mismatch
    # PLANNED_GRAPH_INDEX_OPERATIONS == membership edges (24), NOT canonical (21).
    assert led.planned_graph_index_operations == len(fx.memberships) == 24
    assert led.max_canonical_source_embeddings == len(fx.sources) == 21


def test_cap_enforcement() -> None:
    led = w.frozen_ledger()
    led.check_cap("gd", 26, led.max_gd_queries)  # at cap: OK
    with pytest.raises(w.WorkloadCapExceeded):
        led.check_cap("gd", 27, led.max_gd_queries)


def test_tampered_ledger_fails_validation() -> None:
    led = w.frozen_ledger()
    bad = dataclasses.replace(led, max_graph_index_attempts=47)
    with pytest.raises(w.WorkloadLedgerError):
        bad.validate()
    bad2 = dataclasses.replace(led, judge_model_calls=1)
    with pytest.raises(w.WorkloadLedgerError):
        bad2.validate()


def test_as_dict_is_content_free() -> None:
    d = w.as_dict()
    assert d["MAX_FINAL_ANSWER_CALLS"] == 72
    assert d["JUDGE_MODEL_CALLS"] == 0
    assert all(isinstance(v, int) for v in d.values())
