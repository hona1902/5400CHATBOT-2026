"""GraphRAG-08 full-run index-retry hardening tests (offline; no DB/provider/sidecar).

Covers the bounded (max 2 attempts/Source) transient-retry policy and the hard
100%-corpus gate (task §11 A-L): first-attempt success continues; a transient
track failure retries once and continues; a transient failure twice aborts; a
non-retryable/unknown cause never retries; no Source exceeds 2 attempts; the retry
reindexes the SAME canonical Source; a partial corpus can never enter evaluation;
and graph rank/score semantics are unchanged.
"""

from __future__ import annotations

import asyncio

import pytest

from open_notebook.integrations.graphrag.config import GraphRAGConfig
from open_notebook.integrations.graphrag.eval import dataset08 as d
from open_notebook.integrations.graphrag.eval import index_retry08 as ir
from open_notebook.integrations.graphrag.eval.gd_seam import GDQueryClient
from open_notebook.integrations.graphrag.eval.runner08 import (
    EvalRunConfig08,
    GraphRAG08EvalRunner,
    IndexNotReadyError,
)
from open_notebook.integrations.graphrag.models import (
    GraphRAGConfigurationError,
    GraphRAGRequestError,
    GraphRAGServerError,
    GraphRAGUnavailableError,
    IndexState,
)

# ---- classifier unit tests -------------------------------------------------

def test_classify_submit_exception():
    assert ir.classify_submit_exception(GraphRAGUnavailableError("t")) is True
    assert ir.classify_submit_exception(GraphRAGServerError("5xx")) is True
    assert ir.classify_submit_exception(GraphRAGRequestError("4xx")) is False
    assert ir.classify_submit_exception(GraphRAGConfigurationError("auth")) is False
    assert ir.classify_submit_exception(ValueError("bug")) is False


def test_is_transient_reason():
    for good in ["Error code: 429", "rate limit exceeded", "Read timeout",
                 "connection reset by peer", "HTTP 503 unavailable", "please try again"]:
        assert ir.is_transient_reason(good) is True, good
    for bad in [None, "", "invalid json schema", "malformed entity output",
                "authentication failed"]:
        assert ir.is_transient_reason(bad) is False, bad


def test_classify_failed_track_unknown_without_config():
    assert asyncio.run(ir.classify_failed_track(None, "trk")) == ir.CATEGORY_UNKNOWN


# ---- runner retry harness --------------------------------------------------

class _Ack:
    def __init__(self, track_id):
        self.accepted = True
        self.track_id = track_id
        self.detail = ""


class _Status:
    def __init__(self, state):
        self.state = state


class _FakeService:
    """Scriptable GraphRAG service: on_index(src, attempt)->('ok',track)|('raise',exc);
    on_track(track)->IndexState."""

    def __init__(self, on_index, on_track):
        self.on_index = on_index
        self.on_track = on_track
        self.index_calls: dict[str, int] = {}
        self.delete_calls: dict[str, int] = {}

    async def index_source(self, *, source_id, canonical_text):
        self.index_calls[source_id] = self.index_calls.get(source_id, 0) + 1
        kind, payload = self.on_index(source_id, self.index_calls[source_id])
        if kind == "raise":
            raise payload
        return _Ack(payload)

    async def track_status(self, track):
        return _Status(self.on_track(track))

    async def delete_document_for_source(self, *, source_id):
        self.delete_calls[source_id] = self.delete_calls.get(source_id, 0) + 1


def _runner(service, *, config=None):
    bench = d.load_benchmark08()
    cfg_gr = GraphRAGConfig(enabled=True, base_url="http://x", timeout=5.0, api_key=None)
    r = GraphRAG08EvalRunner(
        bench,
        service=service,
        gd_client=GDQueryClient(cfg_gr),
        selected_source_keys=("S001", "S002"),
        selected_query_ids=("GR08Q01",),
        config=config or EvalRunConfig08(index_ready_timeout_s=10.0, poll_interval_s=0.0),
        graphrag_config=object(),  # non-None so classify path is reached
    )
    # Simulate the pre-index state create_and_index would have set.
    r.created_ids = ["source:ca", "source:cb"]
    r.key_to_source_id = {"S001": "source:ca", "S002": "source:cb"}
    return r


def _ok_index(src, attempt):
    return ("ok", f"{src}#a{attempt}")


def test_A_all_first_attempt_success(monkeypatch):
    svc = _FakeService(_ok_index, lambda trk: IndexState.PROCESSED)
    r = _runner(svc)
    asyncio.run(r._graph_index_with_retry())
    r._assert_complete_corpus()
    assert svc.index_calls == {"source:ca": 1, "source:cb": 1}
    assert svc.delete_calls == {}


def test_B_transient_track_fail_then_success(monkeypatch):
    # ca fails on its first track, succeeds after one reindex; cb always ok.
    def on_track(trk):
        if trk == "source:ca#a1":
            return IndexState.FAILED
        return IndexState.PROCESSED

    monkeypatch.setattr(ir, "diagnose_failed_track", _fake_diag(ir.CATEGORY_TRANSIENT, True))
    svc = _FakeService(_ok_index, on_track)
    r = _runner(svc)
    asyncio.run(r._graph_index_with_retry())
    r._assert_complete_corpus()
    assert svc.index_calls["source:ca"] == 2  # one retry (reindex)
    assert svc.delete_calls["source:ca"] == 1  # reindex = delete-then-insert
    assert svc.index_calls["source:cb"] == 1


def test_C_transient_twice_aborts(monkeypatch):
    def on_track(trk):
        return IndexState.FAILED if trk.startswith("source:ca") else IndexState.PROCESSED

    monkeypatch.setattr(ir, "diagnose_failed_track", _fake_diag(ir.CATEGORY_TRANSIENT, True))
    svc = _FakeService(_ok_index, on_track)
    r = _runner(svc)
    with pytest.raises(IndexNotReadyError):
        asyncio.run(r._graph_index_with_retry())
    assert svc.index_calls["source:ca"] == 2  # E: never a 3rd attempt


def test_D_non_retryable_no_retry(monkeypatch):
    def on_track(trk):
        return IndexState.FAILED if trk.startswith("source:ca") else IndexState.PROCESSED

    monkeypatch.setattr(ir, "diagnose_failed_track", _fake_diag(ir.CATEGORY_NON_RETRYABLE, False))
    svc = _FakeService(_ok_index, on_track)
    r = _runner(svc)
    with pytest.raises(IndexNotReadyError):
        asyncio.run(r._graph_index_with_retry())
    assert svc.index_calls["source:ca"] == 1  # no reindex
    assert "source:ca" not in svc.delete_calls


def test_D2_unknown_cause_fails_closed(monkeypatch):
    monkeypatch.setattr(ir, "diagnose_failed_track", _fake_diag(ir.CATEGORY_UNKNOWN, False))
    svc = _FakeService(_ok_index,
                       lambda trk: IndexState.FAILED if "ca" in trk else IndexState.PROCESSED)
    r = _runner(svc)
    with pytest.raises(IndexNotReadyError):
        asyncio.run(r._graph_index_with_retry())
    assert svc.index_calls["source:ca"] == 1  # UNKNOWN treated as non-retryable


def test_submit_transient_exception_retries_within_cap(monkeypatch):
    # ca raises a transient submit exception on attempt 1, succeeds on attempt 2.
    def on_index(src, attempt):
        if src == "source:ca" and attempt == 1:
            return ("raise", GraphRAGUnavailableError("timeout"))
        return ("ok", f"{src}#a{attempt}")

    svc = _FakeService(on_index, lambda trk: IndexState.PROCESSED)
    r = _runner(svc)
    asyncio.run(r._graph_index_with_retry())
    assert svc.index_calls["source:ca"] == 2  # one transient submit retry


def test_submit_non_retryable_exception_aborts(monkeypatch):
    def on_index(src, attempt):
        if src == "source:ca":
            return ("raise", GraphRAGRequestError("422"))
        return ("ok", f"{src}#a{attempt}")

    svc = _FakeService(on_index, lambda trk: IndexState.PROCESSED)
    r = _runner(svc)
    with pytest.raises(IndexNotReadyError):
        asyncio.run(r._graph_index_with_retry())
    assert svc.index_calls["source:ca"] == 1  # no retry on non-transient submit error


def test_cross_surface_counter_submit_retry_then_track_fail(monkeypatch):
    # THE core invariant (review MEDIUM-1): a submit-time transient retry AND a
    # later track-time FAILED must share ONE per-Source counter. ca burns both
    # attempts on submit (raise -> success), then its doc reaches FAILED: the run
    # MUST abort at 2 total index_source calls, never a 3rd. A two-separate-counter
    # bug would allow a 3rd attempt and pass every other test.
    def on_index(src, attempt):
        if src == "source:ca" and attempt == 1:
            return ("raise", GraphRAGUnavailableError("timeout"))
        return ("ok", f"{src}#a{attempt}")

    def on_track(trk):
        return IndexState.FAILED if trk.startswith("source:ca") else IndexState.PROCESSED

    monkeypatch.setattr(ir, "diagnose_failed_track", _fake_diag(ir.CATEGORY_TRANSIENT, True))
    svc = _FakeService(on_index, on_track)
    r = _runner(svc)
    with pytest.raises(IndexNotReadyError):
        asyncio.run(r._graph_index_with_retry())
    assert svc.index_calls["source:ca"] == 2  # NEVER a 3rd attempt across surfaces


def test_submit_transient_twice_aborts_at_cap(monkeypatch):
    # Submit transient-fails on BOTH attempts -> abort at cap (exercises the
    # top-of-loop attempts>=cap guard). Reindex predelete fires on the 2nd attempt.
    def on_index(src, attempt):
        if src == "source:ca":
            return ("raise", GraphRAGUnavailableError("connection reset"))
        return ("ok", f"{src}#a{attempt}")

    svc = _FakeService(on_index, lambda trk: IndexState.PROCESSED)
    r = _runner(svc)
    with pytest.raises(IndexNotReadyError):
        asyncio.run(r._graph_index_with_retry())
    assert svc.index_calls["source:ca"] == 2  # exactly 2, never 3
    assert svc.delete_calls.get("source:ca") == 1  # 2nd submit used the reindex path


def test_I_partial_corpus_gate_blocks(monkeypatch):
    svc = _FakeService(_ok_index, lambda trk: IndexState.PROCESSED)
    r = _runner(svc)
    # Simulate only one of two sources graph-indexed.
    r.track_ids = {"source:ca": "source:ca#a1"}
    with pytest.raises(IndexNotReadyError):
        r._assert_complete_corpus()


def test_G_fixture_unchanged_by_retry_code():
    ok, h = d.verify_integrity()
    assert ok and h == "a58a68535c345e18f0263904f818e4e2068a164056408665d8bb9233eceb143d"


def test_L_graph_rank_semantics_unchanged():
    # Graph evidence remains an unordered set (no rank/score) after the change.
    from open_notebook.integrations.graphrag.eval.normalize import (
        normalize_graph_references,
    )

    norm = normalize_graph_references([], benchmark_ids=frozenset())
    assert norm.ordered is False


def _acoro(value):
    async def _c():
        return value

    return _c()


def _fake_diag(classification, retry_allowed):
    """Return an async stand-in for diagnose_failed_track yielding a FailureDiagnostic."""

    async def _f(config, track_id, *, attempt_number,
                 canonical_source_id=None, logical_source_id=None):
        return ir.FailureDiagnostic(
            failure_surface="TRACK",
            attempt_number=attempt_number,
            classification=classification,
            classification_reason_code="TEST",
            retry_allowed=retry_allowed,
            retry_consumed=attempt_number >= 2,
            error_text_present=True,
            error_text_length_bucket="1_64",
            matched_transient_classes=(),
            matched_non_transient_classes=(),
            http_status_class=None,
            exception_type=None,
            canonical_source_id=canonical_source_id,
            logical_source_id=logical_source_id,
        )

    return _f
