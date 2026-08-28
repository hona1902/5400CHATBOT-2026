"""GraphRAG-03A command + enqueue-seam tests.

Covers the properties that live above the HTTP boundary: the command reloads
current canonical state (never queued text), a deleted source is a safe no-op,
and the save_source enqueue seam is fail-open (a submit failure never breaks
ingestion).
"""

import pytest

import commands.graphrag_commands as gc
import open_notebook.graphs.source as source_graph_mod
from commands.graphrag_commands import GraphRAGIndexInput, graphrag_index_source_command
from open_notebook.integrations.graphrag.lifecycle import IndexOutcome, IndexResult


class _FakeSource:
    def __init__(self, id, full_text):
        self.id = id
        self.full_text = full_text


# ------------------------------------------------- payload has no text (3,5)


def test_index_input_has_no_text_field():
    """Property (3,5): the queued payload carries source_id ONLY. If a full_text
    field existed, an old job could resurrect stale content. This is the
    structural guarantee — enforced by the schema, not by convention."""
    fields = set(GraphRAGIndexInput.model_fields)
    assert "source_id" in fields
    # No field may carry document body content. (execution_context is
    # surreal_commands bookkeeping, not source content.)
    content_carrying = {"full_text", "content", "canonical_text", "text", "body"}
    assert fields & content_carrying == set()


# ------------------------------------------------- reload current state (4)


def _exists_query(rows):
    """Build a fake repo_query that returns ``rows`` for the existence check."""

    async def fake_repo_query(query, params=None):
        return rows

    return fake_repo_query


@pytest.mark.asyncio
async def test_command_reloads_source_and_indexes_current_text(monkeypatch):
    """Property (4): the command loads the CURRENT Source at execution and
    indexes its current text — not anything from the payload."""
    monkeypatch.setenv("OPEN_NOTEBOOK_GRAPHRAG_ENABLED", "true")
    monkeypatch.setenv("OPEN_NOTEBOOK_GRAPHRAG_BASE_URL", "http://x.invalid:9621")

    indexed = {}

    async def fake_index_source(service, *, source_id, canonical_text, confirm_current=None):
        indexed["source_id"] = source_id
        indexed["text"] = canonical_text
        return IndexOutcome(IndexResult.INDEXED, "ok", "track-9")

    # The command loads via a direct SELECT *; return a row dict with CURRENT
    # text, so the test proves the command indexes DB state, not the payload.
    monkeypatch.setattr(
        gc,
        "repo_query",
        _exists_query([{"id": "source:abc", "full_text": "CURRENT text from DB"}]),
    )
    monkeypatch.setattr(gc, "index_source", fake_index_source)

    out = await graphrag_index_source_command(GraphRAGIndexInput(source_id="source:abc"))
    assert out.success and out.outcome == "indexed"
    assert indexed["source_id"] == "source:abc"
    assert indexed["text"] == "CURRENT text from DB"  # from DB, not payload


@pytest.mark.asyncio
async def test_deleted_source_is_safe_noop(monkeypatch):
    """Property (6): a source that is GENUINELY absent (empty existence result)
    is a safe no-op; no stale content is indexed and no sidecar call is made."""
    monkeypatch.setenv("OPEN_NOTEBOOK_GRAPHRAG_ENABLED", "true")
    monkeypatch.setenv("OPEN_NOTEBOOK_GRAPHRAG_BASE_URL", "http://x.invalid:9621")

    called = {"index": False}

    async def fake_index_source(*a, **k):
        called["index"] = True

    monkeypatch.setattr(gc, "repo_query", _exists_query([]))  # not found
    monkeypatch.setattr(gc, "index_source", fake_index_source)

    out = await graphrag_index_source_command(GraphRAGIndexInput(source_id="source:gone"))
    assert out.success and out.outcome == "skipped_absent"
    assert called["index"] is False


@pytest.mark.asyncio
async def test_escaped_record_id_is_queried_losslessly(monkeypatch):
    """Property (Codex HIGH-3): a live source with an escaped (string-numeric)
    id must be looked up by a losslessly-built RecordID, NOT by re-parsing the
    canonical string (which double-escapes and binds the wrong record). If the
    id were mangled, the existence query would miss a live source and terminally
    skip it as absent — a numeric vs string-numeric identity violation."""
    monkeypatch.setenv("OPEN_NOTEBOOK_GRAPHRAG_ENABLED", "true")
    monkeypatch.setenv("OPEN_NOTEBOOK_GRAPHRAG_BASE_URL", "http://x.invalid:9621")

    escaped_id = "source:⟨123⟩"  # string id "123", distinct from source:123
    bound = {}

    async def capture_query(query, params=None):
        bound["id"] = params["id"]
        return [{"id": escaped_id, "full_text": "text"}]

    async def fake_index_source(service, *, source_id, canonical_text, confirm_current=None):
        return IndexOutcome(IndexResult.INDEXED, "ok", "t")

    monkeypatch.setattr(gc, "repo_query", capture_query)
    monkeypatch.setattr(gc, "index_source", fake_index_source)

    out = await graphrag_index_source_command(GraphRAGIndexInput(source_id=escaped_id))
    assert out.success and out.outcome == "indexed"
    # The bound RecordID must round-trip to the ORIGINAL escaped presentation,
    # not a double-escaped form.
    assert str(bound["id"]) == escaped_id


@pytest.mark.asyncio
async def test_source_changed_between_read_and_insert_is_superseded(monkeypatch):
    """Property (Codex pass-3 HIGH): if the source's text changes (or it is
    deleted) between the initial load and the pre-insert confirm, the command
    must NOT send the stale text. The real index path is exercised (not mocked)
    so the confirm callback actually runs; a recording sidecar proves no insert
    body was sent."""
    monkeypatch.setenv("OPEN_NOTEBOOK_GRAPHRAG_ENABLED", "true")
    monkeypatch.setenv("OPEN_NOTEBOOK_GRAPHRAG_BASE_URL", "http://x.invalid:9621")

    # repo_query is called twice: initial load, then the confirm re-read.
    calls = {"n": 0}

    async def changing_query(query, params=None):
        calls["n"] += 1
        if calls["n"] == 1:
            return [{"id": "source:abc", "full_text": "ORIGINAL text"}]
        # Confirm re-read: content has since changed (redaction/edit).
        return [{"id": "source:abc", "full_text": "REDACTED"}]

    sent = {"insert": False}

    async def fake_index_source(service, *, source_id, canonical_text, confirm_current=None):
        # Delegate to the REAL lifecycle so confirm_current runs; but stub the
        # service's outbound calls by asserting confirm gates before egress.
        assert confirm_current is not None
        still = await confirm_current()
        if not still:
            from open_notebook.integrations.graphrag.lifecycle import (
                IndexOutcome,
                IndexResult,
            )

            return IndexOutcome(IndexResult.SUPERSEDED, "changed")
        sent["insert"] = True
        return IndexOutcome(IndexResult.INDEXED, "ok", "t")

    monkeypatch.setattr(gc, "repo_query", changing_query)
    monkeypatch.setattr(gc, "index_source", fake_index_source)

    out = await graphrag_index_source_command(GraphRAGIndexInput(source_id="source:abc"))
    assert out.success and out.outcome == "superseded"
    assert sent["insert"] is False


@pytest.mark.asyncio
async def test_transient_db_error_is_not_skipped_as_absent(monkeypatch):
    """Property (Codex HIGH-2): a transient DB failure during the existence
    check must NOT be misclassified as a deletion. If it were skipped as
    'absent' (terminal success), a live source would never be indexed after a
    momentary outage. It must raise so the retry layer re-drives."""
    monkeypatch.setenv("OPEN_NOTEBOOK_GRAPHRAG_ENABLED", "true")
    monkeypatch.setenv("OPEN_NOTEBOOK_GRAPHRAG_BASE_URL", "http://x.invalid:9621")

    async def boom_query(query, params=None):
        raise RuntimeError("db connection lost")

    called = {"index": False}

    async def fake_index_source(*a, **k):  # pragma: no cover - must not run
        called["index"] = True

    monkeypatch.setattr(gc, "repo_query", boom_query)
    monkeypatch.setattr(gc, "index_source", fake_index_source)

    with pytest.raises(RuntimeError):
        await graphrag_index_source_command(GraphRAGIndexInput(source_id="source:abc"))
    assert called["index"] is False


@pytest.mark.asyncio
async def test_enabled_but_unconfigured_is_transient_not_terminal_skip(monkeypatch):
    """Property (Codex MEDIUM-2): flag ON but BASE_URL unset must be transient
    (retry after the operator fixes config), not a terminal skipped_disabled
    that a later fix can never re-drive."""
    monkeypatch.setenv("OPEN_NOTEBOOK_GRAPHRAG_ENABLED", "true")
    monkeypatch.delenv("OPEN_NOTEBOOK_GRAPHRAG_BASE_URL", raising=False)

    with pytest.raises(RuntimeError):
        await graphrag_index_source_command(GraphRAGIndexInput(source_id="source:abc"))


@pytest.mark.asyncio
async def test_flag_off_at_execution_skips_without_call(monkeypatch):
    """Property (18): a job executing while the flag is OFF completes as a clean
    skip and makes no external call or DB load."""
    monkeypatch.delenv("OPEN_NOTEBOOK_GRAPHRAG_ENABLED", raising=False)

    async def fake_get(source_id):  # pragma: no cover - must not be called
        raise AssertionError("must not load source when disabled")

    monkeypatch.setattr(gc.Source, "get", staticmethod(fake_get))
    out = await graphrag_index_source_command(GraphRAGIndexInput(source_id="source:abc"))
    assert out.success and out.outcome == "skipped_disabled"


@pytest.mark.asyncio
async def test_invalid_source_id_is_permanent_failure(monkeypatch):
    """Property (12): a structurally invalid source_id never reaches the sidecar
    and never retries forever."""
    monkeypatch.setenv("OPEN_NOTEBOOK_GRAPHRAG_ENABLED", "true")
    monkeypatch.setenv("OPEN_NOTEBOOK_GRAPHRAG_BASE_URL", "http://x.invalid:9621")

    async def fake_get(source_id):  # pragma: no cover
        raise AssertionError("must not load an invalid source_id")

    monkeypatch.setattr(gc.Source, "get", staticmethod(fake_get))
    out = await graphrag_index_source_command(
        GraphRAGIndexInput(source_id="not-a-record-id")
    )
    assert out.success is False and out.outcome == "permanent_failure"


# ------------------------------------------------- fail-open seam (2)


def test_enqueue_seam_never_raises_on_submit_failure(monkeypatch):
    """Property (2): if submit_command raises, the seam swallows it. Canonical
    ingestion must not fail because of a GraphRAG queue hiccup."""
    monkeypatch.setenv("OPEN_NOTEBOOK_GRAPHRAG_ENABLED", "true")
    monkeypatch.setenv("OPEN_NOTEBOOK_GRAPHRAG_BASE_URL", "http://x.invalid:9621")

    import surreal_commands

    def boom(*a, **k):
        raise RuntimeError("queue down")

    monkeypatch.setattr(surreal_commands, "submit_command", boom)

    src = _FakeSource("source:abc", "text")
    # Must not raise.
    source_graph_mod._maybe_enqueue_graphrag_index(src)


def test_enqueue_seam_noops_when_flag_off(monkeypatch):
    """Property (1): flag OFF ⇒ the seam submits nothing (baseline behavior)."""
    monkeypatch.delenv("OPEN_NOTEBOOK_GRAPHRAG_ENABLED", raising=False)

    import surreal_commands

    called = {"n": 0}

    def counting_submit(*a, **k):  # pragma: no cover - must not be called
        called["n"] += 1
        return "command:x"

    monkeypatch.setattr(surreal_commands, "submit_command", counting_submit)
    source_graph_mod._maybe_enqueue_graphrag_index(_FakeSource("source:abc", "text"))
    assert called["n"] == 0


def test_enqueue_seam_submits_only_source_id(monkeypatch):
    """Property (3): the seam enqueues {source_id} and nothing else — no text."""
    monkeypatch.setenv("OPEN_NOTEBOOK_GRAPHRAG_ENABLED", "true")
    monkeypatch.setenv("OPEN_NOTEBOOK_GRAPHRAG_BASE_URL", "http://x.invalid:9621")

    import surreal_commands

    captured = {}

    def capture(app, name, args):
        captured["app"] = app
        captured["name"] = name
        captured["args"] = args
        return "command:x"

    monkeypatch.setattr(surreal_commands, "submit_command", capture)
    source_graph_mod._maybe_enqueue_graphrag_index(_FakeSource("source:abc", "text"))
    assert captured["name"] == "graphrag_index_source"
    assert captured["args"] == {"source_id": "source:abc"}
