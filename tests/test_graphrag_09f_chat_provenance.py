"""GraphRAG-09F chat-surface provenance integration tests (provider-free, no sidecar, no DB).

Two layers, both synthetic and deterministic:

* helper unit tests drive the REAL ``build_chat_graph_provenance`` with an injected graph query
  (synthetic ``GraphReference`` objects) and the REAL 09E ``validate_graph_references`` backed by
  an in-memory canonical authority. They prove: feature-off zero work, intersection-only output,
  graph-only sources dropped, typed-failure/validation-unavailable degrade to ``None``,
  foreign/removed/deleted non-leakage, graph answer/excerpt suppression, bounded output, at most
  one graph query + one validation, empty-context short-circuit, and canonical context ordering.
* router tests drive ``POST /api/chat/execute`` through ``TestClient`` with the canonical chat
  path fully mocked, proving the helper receives the server-resolved notebook id and the current
  user message only, that ``chat_graph.invoke`` input/count are unchanged by provenance, and that
  the optional response field is backward-compatible.

No provider traffic, no real sidecar, no internal data.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from open_notebook.database.repository import ensure_record_id
from open_notebook.integrations.graphrag.models import (
    _INDEXABLE_TABLES,
    GraphRAGUnavailableError,
    GraphReference,
    record_id_for,
)
from open_notebook.integrations.graphrag.product_provenance import (
    ProductProvenance,
    build_chat_graph_provenance,
)
from open_notebook.integrations.graphrag.provenance import (
    ProvenanceValidationResult,
    validate_graph_references,
)

# A sentinel that models raw graph answer / excerpt content; must never surface on the product side.
GRAPH_ANSWER_SENTINEL = "GRAPH_ANSWER_SENTINEL_09F"
GRAPH_EXCERPT_SENTINEL = "GRAPH_EXCERPT_SENTINEL_09F"


def _ref(source_id, *, excerpts=None):
    return GraphReference(
        source_id=source_id,
        reference_id="ref-id-should-not-surface",
        resolved=True,
        excerpts=list(excerpts or []),
    )


def _skey(source_id: str) -> str:
    return str(record_id_for(source_id, tables=_INDEXABLE_TABLES))


def _nkey(notebook_id: str) -> str:
    return str(ensure_record_id(notebook_id))


class FakeCanonical:
    """In-memory canonical authority: live sources (+title) and reference membership edges.

    Mirrors the shape the real validator queries (in=source, out=notebook), so the helper runs the
    genuine 09E validation against it.
    """

    def __init__(self) -> None:
        self.live: dict[str, str | None] = {}
        self.memberships: set[tuple[str, str]] = set()
        self.calls = {"source": 0, "reference": 0}

    def add_source(self, source_id: str, *, title: str | None = None) -> None:
        self.live[_skey(source_id)] = title

    def add_membership(self, source_id: str, notebook_id: str) -> None:
        self.memberships.add((_nkey(notebook_id), _skey(source_id)))

    async def repo_query(self, sql: str, params: dict | None = None):
        params = params or {}
        if "FROM reference" in sql:
            self.calls["reference"] += 1
            nb = str(params["notebook"])
            ids = [str(x) for x in params["ids"]]
            return [{"in": sid} for sid in ids if (nb, sid) in self.memberships]
        if "FROM source" in sql:
            self.calls["source"] += 1
            ids = [str(x) for x in params["ids"]]
            return [
                {"id": sid, "title": self.live[sid]} for sid in ids if sid in self.live
            ]
        raise AssertionError(f"unexpected query: {sql}")


class CountingQuery:
    """A graph-query spy returning fixed references and recording its single invocation."""

    def __init__(self, references):
        self._references = references
        self.calls = 0
        self.last_question = None

    async def __call__(self, question):
        self.calls += 1
        self.last_question = question
        return self._references


class CountingValidate:
    """A validate spy that delegates to the real validator and counts invocations."""

    def __init__(self):
        self.calls = 0

    async def __call__(self, references, *, notebook_id, repo_query):
        self.calls += 1
        return await validate_graph_references(
            references, notebook_id=notebook_id, repo_query=repo_query
        )


@pytest.fixture
def enabled_env(monkeypatch):
    """Feature ON + reachable base_url so ``load_config().configured`` is True."""
    monkeypatch.setenv("OPEN_NOTEBOOK_GRAPHRAG_ENABLED", "true")
    monkeypatch.setenv("OPEN_NOTEBOOK_GRAPHRAG_BASE_URL", "http://sidecar.invalid:9999")
    monkeypatch.delenv("OPEN_NOTEBOOK_GRAPHRAG_API_KEY", raising=False)


@pytest.fixture
def disabled_env(monkeypatch):
    monkeypatch.delenv("OPEN_NOTEBOOK_GRAPHRAG_ENABLED", raising=False)
    monkeypatch.delenv("OPEN_NOTEBOOK_GRAPHRAG_BASE_URL", raising=False)


# =========================================================================== helper: feature gate


@pytest.mark.asyncio
async def test_flag_off_zero_work_no_calls(disabled_env):
    """§23/§48: feature off => no graph query, no validation, no provenance, zero work."""
    fake = FakeCanonical()
    query = CountingQuery([_ref("source:s1")])
    validate = CountingValidate()

    result = await build_chat_graph_provenance(
        question="hello",
        notebook_id="notebook:a",
        canonical_source_ids=["source:s1"],
        graph_query=query,
        validate=validate,
        repo_query=fake.repo_query,
    )

    assert result is None
    assert query.calls == 0
    assert validate.calls == 0
    assert fake.calls == {"source": 0, "reference": 0}


@pytest.mark.asyncio
async def test_flag_off_does_not_construct_graph_service(disabled_env):
    """§22: with the flag off the helper must not even construct the GraphRAG service/client."""
    with patch(
        "open_notebook.integrations.graphrag.service.GraphRAGService"
    ) as mock_service:
        result = await build_chat_graph_provenance(
            question="hello",
            notebook_id="notebook:a",
            canonical_source_ids=["source:s1"],
        )
    assert result is None
    mock_service.assert_not_called()


# =========================================================================== helper: intersection


@pytest.mark.asyncio
async def test_positive_intersection_only_s2(enabled_env):
    """§49: context {S1,S2}, graph {S2,S3}, both validated => product == [S2]."""
    fake = FakeCanonical()
    for sid in ("source:s2", "source:s3"):
        fake.add_source(sid)
        fake.add_membership(sid, "notebook:a")
    query = CountingQuery([_ref("source:s2"), _ref("source:s3")])

    result = await build_chat_graph_provenance(
        question="q",
        notebook_id="notebook:a",
        canonical_source_ids=["source:s1", "source:s2"],
        graph_query=query,
        repo_query=fake.repo_query,
    )

    assert result is not None
    assert result.has_validated_graph_provenance is True
    assert result.source_ids == (_skey("source:s2"),)
    assert result.count == 1


@pytest.mark.asyncio
async def test_graph_only_source_dropped(enabled_env):
    """§50: a validated graph source absent from the chat context is dropped entirely."""
    fake = FakeCanonical()
    fake.add_source("source:s3")
    fake.add_membership("source:s3", "notebook:a")
    query = CountingQuery([_ref("source:s3")])

    result = await build_chat_graph_provenance(
        question="q",
        notebook_id="notebook:a",
        canonical_source_ids=["source:s1", "source:s2"],  # no S3
        graph_query=query,
        repo_query=fake.repo_query,
    )

    assert result is None  # nothing in context intersects the validated set


@pytest.mark.asyncio
async def test_order_follows_canonical_context(enabled_env):
    """§61: context order S3,S1,S2; validated S1,S3 => product order S3,S1 (context order)."""
    fake = FakeCanonical()
    for sid in ("source:s1", "source:s3"):
        fake.add_source(sid)
        fake.add_membership(sid, "notebook:a")
    # graph returns them in a DIFFERENT order; product order must follow context, not graph.
    query = CountingQuery([_ref("source:s1"), _ref("source:s3")])

    result = await build_chat_graph_provenance(
        question="q",
        notebook_id="notebook:a",
        canonical_source_ids=["source:s3", "source:s1", "source:s2"],
        graph_query=query,
        repo_query=fake.repo_query,
    )

    assert result is not None
    assert result.source_ids == (_skey("source:s3"), _skey("source:s1"))


# =========================================================================== helper: degrade to None


@pytest.mark.asyncio
async def test_graph_failure_returns_none(enabled_env):
    """§51: a typed GraphRAG failure from the graph query degrades to no provenance."""

    async def boom(_question):
        raise GraphRAGUnavailableError("sidecar down")

    result = await build_chat_graph_provenance(
        question="q",
        notebook_id="notebook:a",
        canonical_source_ids=["source:s1"],
        graph_query=boom,
    )
    assert result is None


@pytest.mark.asyncio
async def test_validation_unavailable_returns_none(enabled_env):
    """§52: graph query succeeds but 09E validation is unavailable/empty => no provenance."""

    async def failing_validate(_references, *, notebook_id, repo_query):
        return ProvenanceValidationResult(validated=(), validation_error=1)

    query = CountingQuery([_ref("source:s1")])
    result = await build_chat_graph_provenance(
        question="q",
        notebook_id="notebook:a",
        canonical_source_ids=["source:s1"],
        graph_query=query,
        validate=failing_validate,
    )
    assert result is None


@pytest.mark.asyncio
async def test_programming_error_not_swallowed(enabled_env):
    """§32: a non-GraphRAG programming error is NOT silently swallowed by the helper."""

    async def exploding(_question):
        raise KeyError("latent bug")

    with pytest.raises(KeyError):
        await build_chat_graph_provenance(
            question="q",
            notebook_id="notebook:a",
            canonical_source_ids=["source:s1"],
            graph_query=exploding,
        )


# =========================================================================== helper: no leakage


@pytest.mark.asyncio
async def test_foreign_source_no_leakage(enabled_env):
    """§53: a graph source that is a member only of a FOREIGN notebook never surfaces."""
    fake = FakeCanonical()
    fake.add_source("source:s1")
    fake.add_membership("source:s1", "notebook:b")  # foreign to A
    query = CountingQuery([_ref("source:s1")])

    result = await build_chat_graph_provenance(
        question="q",
        notebook_id="notebook:a",
        canonical_source_ids=["source:s1"],
        graph_query=query,
        repo_query=fake.repo_query,
    )
    assert result is None


@pytest.mark.asyncio
async def test_removed_membership_no_leakage(enabled_env):
    """§54: source still live but no longer a member of the notebook => not surfaced."""
    fake = FakeCanonical()
    fake.add_source("source:s1")  # live, but no membership edge added
    query = CountingQuery([_ref("source:s1")])

    result = await build_chat_graph_provenance(
        question="q",
        notebook_id="notebook:a",
        canonical_source_ids=["source:s1"],
        graph_query=query,
        repo_query=fake.repo_query,
    )
    assert result is None


@pytest.mark.asyncio
async def test_deleted_source_no_leakage(enabled_env):
    """§55: a deleted (absent) source with a stale graph reference never surfaces."""
    fake = FakeCanonical()
    fake.add_membership("source:s1", "notebook:a")  # stale edge, source not live
    query = CountingQuery([_ref("source:s1")])

    result = await build_chat_graph_provenance(
        question="q",
        notebook_id="notebook:a",
        canonical_source_ids=["source:s1"],
        graph_query=query,
        repo_query=fake.repo_query,
    )
    assert result is None


# =========================================================================== helper: content safety


@pytest.mark.asyncio
async def test_graph_answer_and_excerpt_suppressed(enabled_env):
    """§20/§56/§57/§67: graph answer, excerpts, reference ids never reach the product marker."""
    fake = FakeCanonical()
    fake.add_source("source:s1", title=f"title {GRAPH_ANSWER_SENTINEL}")
    fake.add_membership("source:s1", "notebook:a")
    query = CountingQuery(
        [_ref("source:s1", excerpts=[f"snippet {GRAPH_EXCERPT_SENTINEL}"])]
    )

    result = await build_chat_graph_provenance(
        question=f"q {GRAPH_ANSWER_SENTINEL}",
        notebook_id="notebook:a",
        canonical_source_ids=["source:s1"],
        graph_query=query,
        repo_query=fake.repo_query,
    )

    assert result is not None
    blob = repr(result)
    assert GRAPH_ANSWER_SENTINEL not in blob
    assert GRAPH_EXCERPT_SENTINEL not in blob
    assert "ref-id-should-not-surface" not in blob
    # Only the three content-free fields exist on the product marker.
    assert set(vars(result).keys()) == {
        "has_validated_graph_provenance",
        "source_ids",
        "count",
    }


# =========================================================================== helper: bounds / counts


@pytest.mark.asyncio
async def test_output_bounded_by_context(enabled_env):
    """§63: product payload is bounded; it never exceeds the canonical answer-context set."""
    fake = FakeCanonical()
    context_ids = [f"source:c{i}" for i in range(5)]
    # Graph returns MANY more validated refs than the context contains.
    many = [f"source:c{i}" for i in range(5)] + [f"source:x{i}" for i in range(100)]
    for sid in many:
        fake.add_source(sid)
        fake.add_membership(sid, "notebook:a")
    query = CountingQuery([_ref(sid) for sid in many])

    result = await build_chat_graph_provenance(
        question="q",
        notebook_id="notebook:a",
        canonical_source_ids=context_ids,
        graph_query=query,
        repo_query=fake.repo_query,
    )

    assert result is not None
    assert result.count <= len(context_ids)
    assert set(result.source_ids) <= {_skey(c) for c in context_ids}


@pytest.mark.asyncio
async def test_graph_query_and_validation_called_at_most_once(enabled_env):
    """§64/§65: at most one graph query and one validation pass per chat request."""
    fake = FakeCanonical()
    fake.add_source("source:s1")
    fake.add_membership("source:s1", "notebook:a")
    query = CountingQuery([_ref("source:s1")])
    validate = CountingValidate()

    await build_chat_graph_provenance(
        question="q",
        notebook_id="notebook:a",
        canonical_source_ids=["source:s1"],
        graph_query=query,
        validate=validate,
        repo_query=fake.repo_query,
    )

    assert query.calls == 1
    assert validate.calls == 1
    assert fake.calls["source"] == 1 and fake.calls["reference"] == 1


@pytest.mark.asyncio
async def test_empty_context_short_circuit_no_graph_substitution(enabled_env):
    """§66: empty canonical context => return before any graph query; no graph substitution."""
    query = CountingQuery([_ref("source:s1")])
    validate = CountingValidate()

    result = await build_chat_graph_provenance(
        question="q",
        notebook_id="notebook:a",
        canonical_source_ids=[],
        graph_query=query,
        validate=validate,
    )

    assert result is None
    assert query.calls == 0  # short-circuited BEFORE the graph query
    assert validate.calls == 0


@pytest.mark.asyncio
async def test_graph_query_receives_only_current_message(enabled_env):
    """§26/§69: the graph query is keyed on the current user message only."""
    fake = FakeCanonical()
    fake.add_source("source:s1")
    fake.add_membership("source:s1", "notebook:a")
    query = CountingQuery([_ref("source:s1")])

    await build_chat_graph_provenance(
        question="only-this-message",
        notebook_id="notebook:a",
        canonical_source_ids=["source:s1"],
        graph_query=query,
        repo_query=fake.repo_query,
    )

    assert query.last_question == "only-this-message"


@pytest.mark.asyncio
async def test_no_notebook_scope_returns_none(enabled_env):
    """Notebook scope must be server-resolved; a missing notebook id yields no provenance."""
    query = CountingQuery([_ref("source:s1")])
    result = await build_chat_graph_provenance(
        question="q",
        notebook_id=None,
        canonical_source_ids=["source:s1"],
        graph_query=query,
    )
    assert result is None
    assert query.calls == 0


# =========================================================================== router: /chat/execute


@pytest.fixture
def client():
    from api.main import app

    return TestClient(app)


class _Msg:
    def __init__(self, id, type, content):
        self.id = id
        self.type = type
        self.content = content


def _graph_state(values):
    state = MagicMock()
    state.values = values
    return state


def _execute_mocks(mock_graph, mock_session_get, mock_repo, mock_notebook_get):
    """Wire the canonical chat path so execute_chat runs end-to-end with no DB/LLM."""
    mock_session_get.return_value = SimpleNamespace(
        id="chat_session:abc", model_override=None, save=AsyncMock()
    )
    mock_repo.return_value = [{"out": "notebook:a"}]
    mock_notebook_get.return_value = SimpleNamespace(id="notebook:a")
    mock_graph.get_state.return_value = _graph_state({"messages": []})
    mock_graph.invoke.return_value = {"messages": [_Msg("m1", "ai", "the answer")]}


_CONTEXT = {"sources": [{"id": "source:s1"}, {"id": "source:s2"}], "notes": []}


@pytest.mark.asyncio
@patch("api.routers.chat.build_chat_graph_provenance", new_callable=AsyncMock)
@patch("api.routers.chat.Notebook.get", new_callable=AsyncMock)
@patch("api.routers.chat.repo_query", new_callable=AsyncMock)
@patch("api.routers.chat.chat_graph")
@patch("api.routers.chat.ChatSession.get", new_callable=AsyncMock)
async def test_execute_passes_server_notebook_and_message_only(
    mock_session_get, mock_graph, mock_repo, mock_notebook_get, mock_helper, client
):
    """§68/§69: helper receives the server-resolved notebook id and the current message only."""
    _execute_mocks(mock_graph, mock_session_get, mock_repo, mock_notebook_get)
    mock_helper.return_value = None

    resp = client.post(
        "/api/chat/execute",
        json={"session_id": "abc", "message": "hi there", "context": _CONTEXT},
    )

    assert resp.status_code == 200
    mock_helper.assert_awaited_once()
    kwargs = mock_helper.await_args.kwargs
    assert kwargs["question"] == "hi there"
    assert kwargs["notebook_id"] == "notebook:a"  # from refers_to, not the graph
    assert kwargs["canonical_source_ids"] == ["source:s1", "source:s2"]


@pytest.mark.asyncio
@patch("api.routers.chat.build_chat_graph_provenance", new_callable=AsyncMock)
@patch("api.routers.chat.Notebook.get", new_callable=AsyncMock)
@patch("api.routers.chat.repo_query", new_callable=AsyncMock)
@patch("api.routers.chat.chat_graph")
@patch("api.routers.chat.ChatSession.get", new_callable=AsyncMock)
async def test_execute_invoke_parity_and_count_unaffected(
    mock_session_get, mock_graph, mock_repo, mock_notebook_get, mock_helper, client
):
    """§59/§60: chat_graph.invoke input and call count are identical with/without provenance."""
    # Run 1: provenance absent.
    _execute_mocks(mock_graph, mock_session_get, mock_repo, mock_notebook_get)
    mock_helper.return_value = None
    r1 = client.post(
        "/api/chat/execute",
        json={"session_id": "abc", "message": "hi", "context": _CONTEXT},
    )
    assert r1.status_code == 200
    assert mock_graph.invoke.call_count == 1
    state1 = mock_graph.invoke.call_args.kwargs["input"]
    ctx1 = state1["context"]

    # Run 2: provenance present.
    mock_graph.reset_mock()
    _execute_mocks(mock_graph, mock_session_get, mock_repo, mock_notebook_get)
    mock_helper.return_value = ProductProvenance(
        has_validated_graph_provenance=True,
        source_ids=(_skey("source:s2"),),
        count=1,
    )
    r2 = client.post(
        "/api/chat/execute",
        json={"session_id": "abc", "message": "hi", "context": _CONTEXT},
    )
    assert r2.status_code == 200
    assert mock_graph.invoke.call_count == 1  # provenance did NOT add a second invocation
    state2 = mock_graph.invoke.call_args.kwargs["input"]

    # The context handed to the chat graph is exactly the posted context in both runs.
    assert ctx1 == _CONTEXT
    assert state2["context"] == _CONTEXT


@pytest.mark.asyncio
@patch("api.routers.chat.build_chat_graph_provenance", new_callable=AsyncMock)
@patch("api.routers.chat.Notebook.get", new_callable=AsyncMock)
@patch("api.routers.chat.repo_query", new_callable=AsyncMock)
@patch("api.routers.chat.chat_graph")
@patch("api.routers.chat.ChatSession.get", new_callable=AsyncMock)
async def test_execute_response_backward_compatible_when_absent(
    mock_session_get, mock_graph, mock_repo, mock_notebook_get, mock_helper, client
):
    """§62: with provenance absent the response keeps session_id + messages; field is null."""
    _execute_mocks(mock_graph, mock_session_get, mock_repo, mock_notebook_get)
    mock_helper.return_value = None

    resp = client.post(
        "/api/chat/execute",
        json={"session_id": "abc", "message": "hi", "context": _CONTEXT},
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["session_id"] == "abc"
    assert body["messages"][0]["content"] == "the answer"
    assert body["graph_provenance"] is None


@pytest.mark.asyncio
@patch("api.routers.chat.build_chat_graph_provenance", new_callable=AsyncMock)
@patch("api.routers.chat.Notebook.get", new_callable=AsyncMock)
@patch("api.routers.chat.repo_query", new_callable=AsyncMock)
@patch("api.routers.chat.chat_graph")
@patch("api.routers.chat.ChatSession.get", new_callable=AsyncMock)
async def test_execute_response_includes_provenance_when_present(
    mock_session_get, mock_graph, mock_repo, mock_notebook_get, mock_helper, client
):
    """A positive marker serializes as a separate, content-free object (not a citation)."""
    _execute_mocks(mock_graph, mock_session_get, mock_repo, mock_notebook_get)
    mock_helper.return_value = ProductProvenance(
        has_validated_graph_provenance=True,
        source_ids=(_skey("source:s2"),),
        count=1,
    )

    resp = client.post(
        "/api/chat/execute",
        json={"session_id": "abc", "message": "hi", "context": _CONTEXT},
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["graph_provenance"] == {
        "has_validated_graph_provenance": True,
        "source_ids": [_skey("source:s2")],
        "count": 1,
    }
    # Provenance is a sibling field, not embedded in messages/citations.
    assert "graph_provenance" not in str(body["messages"])


# =========================================================================== extractor: no mutation


def test_context_source_id_extraction_no_mutation():
    """§8/§45/§46/§58: extraction reads ids, skips malformed, and never mutates the context."""
    from api.routers.chat import _canonical_answer_context_source_ids

    context = {
        "sources": [
            {"id": "source:s1"},
            {"id": "   "},  # blank -> skipped
            {"title": "no id"},  # id-less -> skipped
            {"id": "source:s2"},
            "not-a-dict",  # -> skipped
        ],
        "notes": [{"id": "note:n1"}],
    }
    before = repr(context)

    ids = _canonical_answer_context_source_ids(context)

    assert ids == ["source:s1", "source:s2"]
    assert repr(context) == before  # unchanged


def test_context_extraction_handles_non_dict_context():
    from api.routers.chat import _canonical_answer_context_source_ids

    assert _canonical_answer_context_source_ids(None) == []
    assert _canonical_answer_context_source_ids({"sources": "bad"}) == []
    assert _canonical_answer_context_source_ids({}) == []
