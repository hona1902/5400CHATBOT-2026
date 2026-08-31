"""GraphRAG-08B content-safe failure-diagnostic tests (offline; no DB/provider/sidecar).

Covers task §20-§29: coarse transient/non-transient classification, present-no-match,
absent, unreadable, RAW-CONTENT CONTAINMENT, failed-before-ANALYZE telemetry, per-Source
attempt accounting, frozen classifier semantics, and atomic artifact write. NONE of these
tests changes the retry DECISION (that stays in test_graphrag_08_index_retry.py).
"""

from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from open_notebook.integrations.graphrag.config import GraphRAGConfig
from open_notebook.integrations.graphrag.eval import dataset08 as d
from open_notebook.integrations.graphrag.eval import index_retry08 as ir
from open_notebook.integrations.graphrag.models import (
    GraphRAGRequestError,
    GraphRAGServerError,
    GraphRAGUnavailableError,
)


def _cfg():
    return GraphRAGConfig(enabled=True, base_url="http://sidecar.test", timeout=5.0, api_key=None)


def _track_transport(*, status=200, documents=None):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.startswith("/documents/track_status/")
        if status >= 400:
            return httpx.Response(status, json={"detail": "err"})
        return httpx.Response(200, json={"documents": documents or []})

    return httpx.MockTransport(handler)


def _failed_doc(**extra):
    doc = {"id": "doc-x", "status": "failed"}
    doc.update(extra)
    return doc


# ---- §20 transient class mapping -------------------------------------------

def test_transient_match_classes_cover_frozen_categories():
    cases = {
        "Error code: 429 rate limit": "RATE_LIMIT",
        "Read timeout after 30s": "TIMEOUT",
        "temporarily unavailable, retry later": "TEMPORARILY_UNAVAILABLE",
        "connection reset by peer": "CONNECTION_RESET",
        "HTTP 503 from upstream": "HTTP_5XX",
        "502 Bad Gateway": "BAD_GATEWAY",
        "service unavailable": "SERVICE_UNAVAILABLE",
        "overloaded, server is busy": "OVERLOADED",
        "please try again": "TRY_AGAIN",
    }
    for text, expected_class in cases.items():
        classes = ir.transient_match_classes(text)
        assert expected_class in classes, (text, classes)
        assert ir.is_transient_reason(text) is True, text  # consistency w/ decision


def test_class_mapping_consistent_with_frozen_decision():
    # For a battery, ANY transient class match IFF is_transient_reason is True.
    battery = [
        "Error code: 429", "rate limit exceeded", "Read timeout", "gateway timeout",
        "connection reset by peer", "HTTP 503 unavailable", "please try again",
        "internal server error", "bad gateway", "service unavailable", "overloaded",
        "invalid json schema", "malformed entity output", "authentication failed",
        "unexpected token in response", "", "entity count 500 exceeds limit",
    ]
    for text in battery:
        has_class = bool(ir.transient_match_classes(text))
        assert has_class == ir.is_transient_reason(text), text


# ---- §21/§22/§23 diagnose_failed_track present/absent/unreadable ------------

def _diagnose(transport):
    return asyncio.run(
        ir.diagnose_failed_track(_cfg(), "trk", attempt_number=1, transport=transport)
    )


def test_diagnose_present_no_transient_match_non_retryable():
    tp = _track_transport(documents=[_failed_doc(error_msg="entity extraction returned malformed output")])
    diag = _diagnose(tp)
    assert diag.classification == ir.CATEGORY_NON_RETRYABLE
    assert diag.classification_reason_code == ir.ReasonCode.TRACK_TEXT_PRESENT_NO_ALLOWLIST_MATCH
    assert diag.retry_allowed is False
    assert diag.error_text_present is True
    assert diag.matched_transient_classes == ()


def test_diagnose_present_transient_retry_allowed():
    tp = _track_transport(documents=[_failed_doc(error_msg="Error code: 429 - rate limit")])
    diag = _diagnose(tp)
    assert diag.classification == ir.CATEGORY_TRANSIENT
    assert diag.classification_reason_code == ir.ReasonCode.TRACK_TRANSIENT_ALLOWLIST_MATCH
    assert diag.retry_allowed is True
    assert "RATE_LIMIT" in diag.matched_transient_classes


def test_diagnose_absent_error_field():
    tp = _track_transport(documents=[_failed_doc()])  # failed doc, no error field
    diag = _diagnose(tp)
    assert diag.classification == ir.CATEGORY_UNKNOWN
    assert diag.classification_reason_code == ir.ReasonCode.TRACK_TEXT_ABSENT
    assert diag.retry_allowed is False
    assert diag.error_text_present is False


def test_diagnose_unreadable():
    diag = _diagnose(_track_transport(status=500))
    assert diag.classification == ir.CATEGORY_UNKNOWN
    assert diag.classification_reason_code == ir.ReasonCode.TRACK_TEXT_UNREADABLE
    assert diag.retry_allowed is False
    # config None also unreadable
    diag2 = asyncio.run(ir.diagnose_failed_track(None, "trk", attempt_number=1))
    assert diag2.classification_reason_code == ir.ReasonCode.TRACK_TEXT_UNREADABLE


# ---- §24 RAW CONTENT CONTAINMENT -------------------------------------------

def test_raw_error_text_never_leaks_into_diagnostic():
    secrets = [
        "sk-livekey-ABC123SECRET",
        "Authorization: Bearer TOKENXYZ",
        "user@example.com",
        "Which company manufactures the Talos-9 controller",  # query-like
        "Helix Robotics is a privately held firm in Calderon",  # source-like
    ]
    raw = "extraction failed: " + " | ".join(secrets) + (" padding" * 200)
    tp = _track_transport(documents=[_failed_doc(error_msg=raw)])
    diag = _diagnose(tp)
    blob = json.dumps(diag.as_dict())
    for s in secrets:
        assert s not in blob, s
    assert "padding" not in blob
    # length is coarse-bucketed, not exact
    assert diag.error_text_length_bucket == "GT_1024"
    assert str(len(raw)) not in blob


def test_diagnostic_fields_are_content_free():
    diag = _diagnose(_track_transport(documents=[_failed_doc(error_msg="429")]))
    allowed = {
        "failure_surface", "attempt_number", "classification",
        "classification_reason_code", "retry_allowed", "retry_consumed",
        "error_text_present", "error_text_length_bucket",
        "matched_transient_classes", "matched_non_transient_classes",
        "http_status_class", "exception_type", "logical_source_id", "canonical_source_id",
    }
    assert set(diag.as_dict().keys()) == allowed
    for k in diag.as_dict():
        assert "text" not in k or k in ("error_text_present", "error_text_length_bucket")
        assert "raw" not in k and "excerpt" not in k and "message" not in k


# ---- §20 submit-exception diagnostics --------------------------------------

def test_diagnose_submit_exception_classes():
    t = ir.diagnose_submit_exception(GraphRAGUnavailableError("timeout"), attempt_number=1)
    assert t.classification == ir.CATEGORY_TRANSIENT
    assert t.classification_reason_code == ir.ReasonCode.TYPED_TRANSIENT_EXCEPTION
    s = ir.diagnose_submit_exception(GraphRAGServerError("5xx"), attempt_number=1)
    assert s.retry_allowed is True and s.http_status_class == "5XX"
    r = ir.diagnose_submit_exception(GraphRAGRequestError("secret-token-XYZ"), attempt_number=1)
    assert r.classification == ir.CATEGORY_NON_RETRYABLE and r.http_status_class == "4XX"
    # exception MESSAGE text never leaks (only the type name is kept)
    assert "secret-token-XYZ" not in json.dumps(r.as_dict())
    u = ir.diagnose_submit_exception(ValueError("bug"), attempt_number=2)
    assert u.classification == ir.CATEGORY_UNKNOWN
    assert u.classification_reason_code == ir.ReasonCode.UNKNOWN_EXCEPTION_FAIL_CLOSED


# ---- §29 frozen classifier semantics ---------------------------------------

def test_classifier_semantics_frozen_regression():
    # Locks the frozen transient DECISION for a battery (08B must not alter it).
    expected = {
        "Error code: 429": True, "rate limit exceeded": True, "Read timeout": True,
        "connection reset by peer": True, "HTTP 503 unavailable": True,
        "please try again": True, "bad gateway": True, "service unavailable": True,
        "invalid json schema": False, "malformed entity output": False,
        "authentication failed": False, "entity count 500 exceeds limit": False,
        "": False,
    }
    for text, exp in expected.items():
        assert ir.is_transient_reason(text) is exp, text


# ---- §13 atomic write ------------------------------------------------------

def test_atomic_write_json(tmp_path):
    from open_notebook.integrations.graphrag.eval.precheck08 import _atomic_write_json

    p = tmp_path / "sub" / "out.json"
    _atomic_write_json(p, {"a": 1, "b": [2, 3]})
    assert json.loads(p.read_text()) == {"a": 1, "b": [2, 3]}
    assert not (tmp_path / "sub" / "out.json.tmp").exists()  # no leftover temp


# ---- §28 fixture integrity -------------------------------------------------

def test_fixture_integrity_unchanged():
    ok, h = d.verify_integrity()
    assert ok and h == "a58a68535c345e18f0263904f818e4e2068a164056408665d8bb9233eceb143d"


# ---- §25/§26 failed-before-ANALYZE telemetry + attempt accounting ----------

class _Ack:
    def __init__(self, track_id):
        self.accepted, self.track_id, self.detail = True, track_id, ""


class _Status:
    def __init__(self, state):
        self.state = state


class _FakeService:
    def __init__(self, on_track):
        self.on_track = on_track
        self.calls = {}

    async def index_source(self, *, source_id, canonical_text):
        self.calls[source_id] = self.calls.get(source_id, 0) + 1
        return _Ack(f"{source_id}#a{self.calls[source_id]}")

    async def track_status(self, track):
        return _Status(self.on_track(track))

    async def delete_document_for_source(self, *, source_id):
        pass


def _runner(service):
    from open_notebook.integrations.graphrag.eval.gd_seam import GDQueryClient
    from open_notebook.integrations.graphrag.eval.runner08 import (
        EvalRunConfig08,
        GraphRAG08EvalRunner,
    )

    bench = d.load_benchmark08()
    r = GraphRAG08EvalRunner(
        bench,
        service=service,
        gd_client=GDQueryClient(_cfg()),
        selected_source_keys=("S001", "S002"),
        selected_query_ids=("GR08Q01",),
        config=EvalRunConfig08(index_ready_timeout_s=10.0, poll_interval_s=0.0),
        graphrag_config=object(),
    )
    r.created_ids = ["source:ca", "source:cb"]
    r.key_to_source_id = {"S001": "source:ca", "S002": "source:cb"}
    return r


def test_failed_before_analyze_telemetry(monkeypatch):
    from open_notebook.integrations.graphrag.eval.runner08 import IndexNotReadyError

    def on_track(trk):
        from open_notebook.integrations.graphrag.models import IndexState
        return IndexState.FAILED if "ca" in trk else IndexState.PROCESSED

    # Non-retryable classification -> abort at attempt 1.
    async def fake_diag(config, track_id, *, attempt_number, canonical_source_id=None,
                        logical_source_id=None, transport=None):
        return ir.FailureDiagnostic(
            failure_surface="TRACK", attempt_number=attempt_number,
            classification=ir.CATEGORY_NON_RETRYABLE,
            classification_reason_code=ir.ReasonCode.TRACK_TEXT_PRESENT_NO_ALLOWLIST_MATCH,
            retry_allowed=False, retry_consumed=False, error_text_present=True,
            error_text_length_bucket="1_64", matched_transient_classes=(),
            matched_non_transient_classes=("CONTENT_PARSE",), http_status_class=None,
            exception_type=None, canonical_source_id=canonical_source_id,
            logical_source_id=logical_source_id,
        )

    monkeypatch.setattr(ir, "diagnose_failed_track", fake_diag)
    r = _runner(_FakeService(on_track))
    with pytest.raises(IndexNotReadyError):
        asyncio.run(r._graph_index_with_retry())

    tel = r.index_telemetry()  # telemetry survives the pre-ANALYZE abort
    # ca aborts the poll before cb is confirmed PROCESSED (insertion order).
    assert tel["graphrag_indexed_count"] <= 1
    assert "source:ca" in tel["failed_canonical_ids"]
    assert "S001" in tel["failed_logical_ids"]
    acc = tel["retry_accounting"]
    assert acc["non_retryable_failures"] == 1
    assert acc["max_attempts_observed"] <= 2
    assert len(tel["failure_diagnostics"]) >= 1
    # telemetry is content-free
    blob = json.dumps(tel)
    assert "Helix" not in blob and "Talos" not in blob


def test_attempt_accounting_never_exceeds_two(monkeypatch):
    async def transient_diag(config, track_id, *, attempt_number, canonical_source_id=None,
                             logical_source_id=None, transport=None):
        return ir.FailureDiagnostic(
            failure_surface="TRACK", attempt_number=attempt_number,
            classification=ir.CATEGORY_TRANSIENT, classification_reason_code="T",
            retry_allowed=True, retry_consumed=attempt_number >= 2,
            error_text_present=True, error_text_length_bucket="1_64",
            matched_transient_classes=("RATE_LIMIT",), matched_non_transient_classes=(),
            http_status_class=None, exception_type=None,
            canonical_source_id=canonical_source_id, logical_source_id=logical_source_id,
        )

    monkeypatch.setattr(ir, "diagnose_failed_track", transient_diag)

    # ca fails once then succeeds; cb ok.
    state = {"ca": 0}

    def on_track(trk):
        from open_notebook.integrations.graphrag.models import IndexState
        if "ca" in trk:
            state["ca"] += 1
            return IndexState.FAILED if state["ca"] == 1 else IndexState.PROCESSED
        return IndexState.PROCESSED

    r = _runner(_FakeService(on_track))
    asyncio.run(r._graph_index_with_retry())
    acc = r.retry_accounting()
    assert acc["sources_retried"] == 1
    assert acc["retry_succeeded"] == 1
    assert acc["max_attempts_observed"] == 2
    assert all(n <= 2 for n in r.index_attempts.values())


def test_raw_text_containment_end_to_end_through_runner(monkeypatch):
    # Drive REAL secret error text through the real diagnose_failed_track into the
    # runner's telemetry, and assert none of it escapes (review Gap 1).
    from open_notebook.integrations.graphrag.eval.runner08 import IndexNotReadyError
    from open_notebook.integrations.graphrag.models import IndexState

    secret = "sk-LEAK-SECRET-999 Authorization: Bearer T user@x.com Helix Talos-9 query text"

    async def fake_fetch(config, track_id, *, transport=None):
        return ("PRESENT", secret)

    monkeypatch.setattr(ir, "_fetch_failed_reason_ex", fake_fetch)

    def on_track(trk):
        return IndexState.FAILED if "ca" in trk else IndexState.PROCESSED

    r = _runner(_FakeService(on_track))
    with pytest.raises(IndexNotReadyError):
        asyncio.run(r._graph_index_with_retry())
    blob = json.dumps(r.index_telemetry())
    for tok in ["sk-LEAK-SECRET-999", "Bearer", "user@x.com", "Helix", "Talos-9", "query text"]:
        assert tok not in blob, tok
    # A content-free diagnostic WAS recorded (non-transient text -> abort).
    assert any(
        dd["classification"] == ir.CATEGORY_NON_RETRYABLE for dd in r.index_diagnostics
    )


def test_classify_and_diagnose_decision_twins_agree(monkeypatch):
    # classify_failed_track (decision-reference) and diagnose_failed_track must return
    # the same classification for identical fetch results (review Gap 2).
    scenarios = [
        (("PRESENT", "Error code: 429"), ir.CATEGORY_TRANSIENT),
        (("PRESENT", "malformed entity output"), ir.CATEGORY_NON_RETRYABLE),
        (("ABSENT", None), ir.CATEGORY_UNKNOWN),
        (("UNREADABLE", None), ir.CATEGORY_UNKNOWN),
    ]
    for ret, expected in scenarios:
        async def fake_fetch(config, track_id, *, transport=None, _r=ret):
            return _r

        monkeypatch.setattr(ir, "_fetch_failed_reason_ex", fake_fetch)
        cat = asyncio.run(ir.classify_failed_track(_cfg(), "trk"))
        diag = asyncio.run(ir.diagnose_failed_track(_cfg(), "trk", attempt_number=1))
        assert cat == expected, ret
        assert diag.classification == cat  # twins agree
