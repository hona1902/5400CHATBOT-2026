"""GraphRAG-09G offline cross-phase integration tests (provider-free, no sidecar, no SurrealDB).

Proves that the CAPTURED production seams from 09B..09F COMPOSE under provider-free conditions. These
are not phase-local unit duplicates: they drive the real FastAPI ``POST /chat/execute`` → real
``execute_chat`` → real ``build_chat_graph_provenance`` (09F) → real ``_default_graph_query`` seam →
real ``validate_graph_references`` (09E) → real ``repo_query`` (09D-shaped canonical reads), faking
ONLY the two external boundaries the architecture isolates:

  * the GraphRAG network/service query boundary (``GraphRAGService.query_strict``) — a synthetic
    ``GraphQueryResult``; and
  * the database driver boundary (``repository.db_connection``) — a faithful in-memory canonical model
    of ``source`` existence and the ``reference`` relation (in = source, out = notebook).

The final provenance AUTHORIZATION result is never mocked: 09E runs for real against the faithful
canonical fake, so client-supplied chat context can never by itself authorize provenance. An
independent, autouse network-interdiction guard makes any accidental real SurrealDB connection or real
GraphRAG HTTP client construction fail loudly. All data is synthetic.
"""

import contextlib
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from open_notebook.database.repository import ensure_record_id
from open_notebook.integrations.graphrag.models import (
    _INDEXABLE_TABLES,
    GraphQueryResult,
    GraphRAGUnavailableError,
    GraphReference,
    record_id_for,
)

# --------------------------------------------------------------------------- synthetic constants
NB_A = "notebook:a"
NB_B = "notebook:b"
S1, S2, S3 = "source:s1", "source:s2", "source:s3"
S_FOREIGN = "source:foreign"
GRAPH_ANSWER_SENTINEL = "GRAPH_ANSWER_SENTINEL_09G"
GRAPH_EXCERPT_SENTINEL = "GRAPH_EXCERPT_SENTINEL_09G"
GRAPH_RELATIONSHIP_SENTINEL = "GRAPH_RELATIONSHIP_SENTINEL_09G"


def _skey(source_id: str) -> str:
    return str(record_id_for(source_id, tables=_INDEXABLE_TABLES))


def _nkey(notebook_id: str) -> str:
    return str(ensure_record_id(notebook_id))


# --------------------------------------------------------------------------- faithful canonical DB
class FaithfulCanonicalDB:
    """In-memory canonical authority faked at the DB-driver boundary (``db_connection``).

    Models exactly the two SELECTs the real 09E validator issues through the real ``repo_query``:
    source existence (``SELECT id, title FROM source WHERE id IN $ids``) and notebook membership
    (``SELECT in FROM reference WHERE out=$notebook AND in IN $ids``), with reference.in = source and
    reference.out = notebook. Rows are returned as plain dicts/strings — exactly what a real driver
    hands to ``parse_record_ids`` — so the real repository code path executes unchanged.
    """

    def __init__(self) -> None:
        self.live: dict[str, str | None] = {}
        self.members: set[tuple[str, str]] = set()
        self.open_count = 0
        self.fail_source = False
        self.fail_reference = False

    # canonical-state mutation (synthetic)
    def add_source(self, sid: str, *, title: str | None = None) -> None:
        self.live[_skey(sid)] = title

    def delete_source(self, sid: str) -> None:
        self.live.pop(_skey(sid), None)

    def add_member(self, sid: str, nb: str) -> None:
        self.members.add((_nkey(nb), _skey(sid)))

    def remove_member(self, sid: str, nb: str) -> None:
        self.members.discard((_nkey(nb), _skey(sid)))

    # db_connection() replacement: an async context manager yielding a connection with .query()
    def connection(self):
        db = self

        @contextlib.asynccontextmanager
        async def _cm():
            db.open_count += 1
            yield _FakeConn(db)

        return _cm()


class _FakeConn:
    def __init__(self, db: FaithfulCanonicalDB) -> None:
        self._db = db

    async def query(self, query_str: str, vars: dict | None = None):
        vars = vars or {}
        if "FROM reference" in query_str:
            if self._db.fail_reference:
                raise RuntimeError("canonical membership query boom")
            nb = str(vars["notebook"])
            ids = [str(x) for x in vars["ids"]]
            return [{"in": sid} for sid in ids if (nb, sid) in self._db.members]
        if "FROM source" in query_str:
            if self._db.fail_source:
                raise RuntimeError("canonical source query boom")
            ids = [str(x) for x in vars["ids"]]
            return [
                {"id": sid, "title": self._db.live[sid]}
                for sid in ids
                if sid in self._db.live
            ]
        raise AssertionError(f"unexpected canonical query: {query_str}")


# --------------------------------------------------------------------------- synthetic graph result
def _graph_result(ref_ids, *, answer="synthetic graph answer", excerpts=None, raw=None):
    refs = [
        GraphReference(
            source_id=sid,
            reference_id=f"rid-should-not-surface-{sid}",
            resolved=True,
            excerpts=list((excerpts or {}).get(sid, [])),
        )
        for sid in ref_ids
    ]
    return GraphQueryResult(
        answer=answer, references=refs, mode="hybrid", elapsed_seconds=0.01, raw=raw
    )


class _Msg:
    def __init__(self, id, type, content):
        self.id = id
        self.type = type
        self.content = content


def _state(values):
    s = MagicMock()
    s.values = values
    return s


# --------------------------------------------------------------------------- network interdiction
@pytest.fixture(autouse=True)
def _network_interdiction(monkeypatch):
    """Independent of the happy-path fakes: a forgotten patch must fail loudly, never reach network.

    If any test's ``db_connection`` patch is missing, the real repository code builds a real
    ``AsyncSurreal`` → this raises. If any test's ``query_strict`` patch is missing, the real service
    builds a real ``GraphRAGClient`` → this raises.
    """

    def _surreal_boom(*a, **k):
        raise AssertionError("09G attempted a real SurrealDB connection")

    def _client_boom(*a, **k):
        raise AssertionError("09G attempted to construct a real GraphRAGClient")

    monkeypatch.setattr(
        "open_notebook.database.repository.AsyncSurreal", _surreal_boom, raising=True
    )
    monkeypatch.setattr(
        "open_notebook.integrations.graphrag.service.GraphRAGClient",
        _client_boom,
        raising=True,
    )
    yield


@pytest.fixture
def client():
    from api.main import app

    return TestClient(app)


# --------------------------------------------------------------------------- router harness
def _install_router(monkeypatch, *, notebook_out=NB_A):
    """Wire the canonical chat path (session/notebook/chat_graph) and return the chat_graph mock."""
    session = SimpleNamespace(id="chat_session:abc", model_override=None, save=AsyncMock())
    graph = MagicMock()
    graph.get_state.return_value = _state({"messages": []})
    graph.invoke.return_value = {"messages": [_Msg("m1", "ai", "the canonical answer")]}
    monkeypatch.setattr("api.routers.chat.ChatSession.get", AsyncMock(return_value=session))
    monkeypatch.setattr(
        "api.routers.chat.repo_query",
        AsyncMock(return_value=([{"out": notebook_out}] if notebook_out else [])),
    )
    monkeypatch.setattr(
        "api.routers.chat.Notebook.get",
        AsyncMock(return_value=SimpleNamespace(id=notebook_out)),
    )
    monkeypatch.setattr("api.routers.chat.chat_graph", graph)
    return graph


def _set_graph_env(monkeypatch, *, enabled: bool, base_url):
    if enabled:
        monkeypatch.setenv("OPEN_NOTEBOOK_GRAPHRAG_ENABLED", "true")
    else:
        monkeypatch.delenv("OPEN_NOTEBOOK_GRAPHRAG_ENABLED", raising=False)
    if base_url:
        monkeypatch.setenv("OPEN_NOTEBOOK_GRAPHRAG_BASE_URL", base_url)
    else:
        monkeypatch.delenv("OPEN_NOTEBOOK_GRAPHRAG_BASE_URL", raising=False)
    monkeypatch.delenv("OPEN_NOTEBOOK_GRAPHRAG_API_KEY", raising=False)


def _patch_graph_query(monkeypatch, *, result=None, exc=None):
    qs = AsyncMock(side_effect=exc) if exc is not None else AsyncMock(return_value=result)
    monkeypatch.setattr(
        "open_notebook.integrations.graphrag.service.GraphRAGService.query_strict", qs
    )
    return qs


def _patch_db(monkeypatch, db: FaithfulCanonicalDB):
    monkeypatch.setattr("open_notebook.database.repository.db_connection", db.connection)


def _post(client, *, message="synthetic question", sources=(S1, S2)):
    context = {"sources": [{"id": s} for s in sources], "notes": []}
    return client.post(
        "/api/chat/execute",
        json={"session_id": "abc", "message": message, "context": context},
    )


def _prov_ids(body):
    gp = body.get("graph_provenance")
    return None if gp is None else gp["source_ids"]


# =========================================================================== flag / config gating


def test_flag_off_zero_work_composed(monkeypatch, client):
    """§17/§18: GraphRAG OFF → baseline Chat, no provenance, and ZERO provenance work (no service
    construction, no query, no DB connection), through the real router+helper."""
    _set_graph_env(monkeypatch, enabled=False, base_url="http://sidecar.invalid:9999")
    graph = _install_router(monkeypatch)
    db = FaithfulCanonicalDB()
    _patch_db(monkeypatch, db)
    qs = _patch_graph_query(monkeypatch, result=_graph_result([S2]))
    # Prove the service is never even constructed when disabled.
    service_spy = MagicMock(side_effect=AssertionError("GraphRAGService constructed while OFF"))
    monkeypatch.setattr(
        "open_notebook.integrations.graphrag.service.GraphRAGService", service_spy
    )

    resp = _post(client, sources=(S1, S2))

    assert resp.status_code == 200
    body = resp.json()
    assert body["graph_provenance"] is None
    assert body["messages"][0]["content"] == "the canonical answer"
    assert graph.invoke.call_count == 1
    assert qs.call_count == 0
    assert db.open_count == 0
    service_spy.assert_not_called()


def test_incomplete_config_degrades_to_baseline(monkeypatch, client):
    """§37: enabled but base_url unset → config.configured False → no provenance, no query, baseline."""
    _set_graph_env(monkeypatch, enabled=True, base_url=None)
    _install_router(monkeypatch)
    db = FaithfulCanonicalDB()
    _patch_db(monkeypatch, db)
    qs = _patch_graph_query(monkeypatch, result=_graph_result([S2]))

    resp = _post(client, sources=(S1, S2))

    assert resp.status_code == 200
    assert resp.json()["graph_provenance"] is None
    assert qs.call_count == 0
    assert db.open_count == 0


def test_maintenance_gate_does_not_enable_product_query(monkeypatch, client):
    """§38: enabled=false + base_url SET (deletion-drain is reachable on base_url alone) must NOT
    enable the 09F product query path — it does zero work."""
    _set_graph_env(monkeypatch, enabled=False, base_url="http://sidecar.invalid:9999")
    _install_router(monkeypatch)
    db = FaithfulCanonicalDB()
    _patch_db(monkeypatch, db)
    qs = _patch_graph_query(monkeypatch, result=_graph_result([S2]))

    resp = _post(client, sources=(S1, S2))

    assert resp.status_code == 200
    assert resp.json()["graph_provenance"] is None
    assert qs.call_count == 0
    assert db.open_count == 0


def test_empty_context_no_graph_query(monkeypatch, client):
    """§36: empty canonical answer-context → short-circuit before any graph query / DB / validator."""
    _set_graph_env(monkeypatch, enabled=True, base_url="http://sidecar.invalid:9999")
    _install_router(monkeypatch)
    db = FaithfulCanonicalDB()
    _patch_db(monkeypatch, db)
    qs = _patch_graph_query(monkeypatch, result=_graph_result([S2]))

    resp = _post(client, sources=())

    assert resp.status_code == 200
    assert resp.json()["graph_provenance"] is None
    assert qs.call_count == 0
    assert db.open_count == 0


# =========================================================================== positive / double-gate


def test_positive_double_gate_intersection(monkeypatch, client):
    """§19: context {S1,S2}, graph {S2,S3}, both live+member(A) → product == [S2] (S3 dropped:
    graph-only, not in chat answer context). Real helper + real 09E validator + real repo_query."""
    _set_graph_env(monkeypatch, enabled=True, base_url="http://sidecar.invalid:9999")
    _install_router(monkeypatch, notebook_out=NB_A)
    db = FaithfulCanonicalDB()
    for s in (S2, S3):
        db.add_source(s)
        db.add_member(s, NB_A)
    _patch_db(monkeypatch, db)
    qs = _patch_graph_query(monkeypatch, result=_graph_result([S2, S3]))

    resp = _post(client, sources=(S1, S2))

    assert resp.status_code == 200
    body = resp.json()
    assert _prov_ids(body) == [_skey(S2)]
    assert body["graph_provenance"]["has_validated_graph_provenance"] is True
    assert body["graph_provenance"]["count"] == 1
    assert qs.call_count == 1
    assert db.open_count >= 1  # real repo_query opened the (faked) connection


def test_client_context_cannot_bypass_09e(monkeypatch, client):
    """§20 CRITICAL: a source present in the client chat context AND returned by the graph, whose
    canonical source exists but is NOT a member of the server-resolved notebook, gets NO provenance."""
    _set_graph_env(monkeypatch, enabled=True, base_url="http://sidecar.invalid:9999")
    _install_router(monkeypatch, notebook_out=NB_A)
    db = FaithfulCanonicalDB()
    db.add_source(S_FOREIGN)  # exists, but NO membership in notebook A
    _patch_db(monkeypatch, db)
    _patch_graph_query(monkeypatch, result=_graph_result([S_FOREIGN]))

    resp = _post(client, sources=(S_FOREIGN,))

    assert resp.status_code == 200
    assert resp.json()["graph_provenance"] is None


def test_server_notebook_scope_isolation(monkeypatch, client):
    """§21/§42: notebook id derived through the real router session→notebook resolution. S is a member
    of B only: session-resolved A → no provenance; session-resolved B → provenance S."""
    # Session resolves to notebook A.
    _set_graph_env(monkeypatch, enabled=True, base_url="http://sidecar.invalid:9999")
    _install_router(monkeypatch, notebook_out=NB_A)
    db = FaithfulCanonicalDB()
    db.add_source(S1)
    db.add_member(S1, NB_B)  # member of B only
    _patch_db(monkeypatch, db)
    _patch_graph_query(monkeypatch, result=_graph_result([S1]))
    resp_a = _post(client, sources=(S1,))
    assert resp_a.status_code == 200
    assert resp_a.json()["graph_provenance"] is None

    # Independently, session resolves to notebook B.
    _install_router(monkeypatch, notebook_out=NB_B)
    _patch_graph_query(monkeypatch, result=_graph_result([S1]))
    resp_b = _post(client, sources=(S1,))
    assert resp_b.status_code == 200
    assert _prov_ids(resp_b.json()) == [_skey(S1)]


# =========================================================================== lifecycle → provenance


def test_removed_membership_propagates_to_product(monkeypatch, client):
    """§22 (09D/09E→09F): membership removal in canonical state propagates to the product surface
    even while the graph result stays stale."""
    _set_graph_env(monkeypatch, enabled=True, base_url="http://sidecar.invalid:9999")
    _install_router(monkeypatch, notebook_out=NB_A)
    db = FaithfulCanonicalDB()
    db.add_source(S1)
    db.add_member(S1, NB_A)
    _patch_db(monkeypatch, db)
    _patch_graph_query(monkeypatch, result=_graph_result([S1]))

    resp1 = _post(client, sources=(S1,))
    assert _prov_ids(resp1.json()) == [_skey(S1)]

    # Canonical state transition: remove only A's membership; graph stays stale.
    db.remove_member(S1, NB_A)
    _patch_graph_query(monkeypatch, result=_graph_result([S1]))
    resp2 = _post(client, sources=(S1,))
    assert resp2.json()["graph_provenance"] is None


def test_deleted_source_propagates_to_product(monkeypatch, client):
    """§23: a deleted (absent) canonical source with a stale graph reference yields no provenance."""
    _set_graph_env(monkeypatch, enabled=True, base_url="http://sidecar.invalid:9999")
    _install_router(monkeypatch, notebook_out=NB_A)
    db = FaithfulCanonicalDB()
    db.add_member(S1, NB_A)  # stale membership edge, but source row absent
    _patch_db(monkeypatch, db)
    _patch_graph_query(monkeypatch, result=_graph_result([S1]))

    resp = _post(client, sources=(S1,))

    assert resp.status_code == 200
    assert resp.json()["graph_provenance"] is None


def test_shared_source_isolation_end_to_end(monkeypatch, client):
    """§24: S shared by A and B. Both validate; after removing only A's membership, A loses provenance
    while B retains it."""
    _set_graph_env(monkeypatch, enabled=True, base_url="http://sidecar.invalid:9999")
    db = FaithfulCanonicalDB()
    db.add_source(S1)
    db.add_member(S1, NB_A)
    db.add_member(S1, NB_B)
    _patch_db(monkeypatch, db)

    _install_router(monkeypatch, notebook_out=NB_A)
    _patch_graph_query(monkeypatch, result=_graph_result([S1]))
    assert _prov_ids(_post(client, sources=(S1,)).json()) == [_skey(S1)]

    _install_router(monkeypatch, notebook_out=NB_B)
    _patch_graph_query(monkeypatch, result=_graph_result([S1]))
    assert _prov_ids(_post(client, sources=(S1,)).json()) == [_skey(S1)]

    # Remove only A's membership.
    db.remove_member(S1, NB_A)
    _install_router(monkeypatch, notebook_out=NB_A)
    _patch_graph_query(monkeypatch, result=_graph_result([S1]))
    assert _post(client, sources=(S1,)).json()["graph_provenance"] is None

    _install_router(monkeypatch, notebook_out=NB_B)
    _patch_graph_query(monkeypatch, result=_graph_result([S1]))
    assert _prov_ids(_post(client, sources=(S1,)).json()) == [_skey(S1)]


# =========================================================================== failure → baseline


def test_typed_graph_failure_degrades_to_baseline(monkeypatch, client):
    """§26 (09C→09F): a real typed GraphRAGError from the query seam leaves canonical Chat intact and
    emits no provenance."""
    _set_graph_env(monkeypatch, enabled=True, base_url="http://sidecar.invalid:9999")
    graph = _install_router(monkeypatch, notebook_out=NB_A)
    db = FaithfulCanonicalDB()
    _patch_db(monkeypatch, db)
    _patch_graph_query(monkeypatch, exc=GraphRAGUnavailableError("sidecar down"))

    resp = _post(client, sources=(S1, S2))

    assert resp.status_code == 200
    body = resp.json()
    assert body["graph_provenance"] is None
    assert body["messages"][0]["content"] == "the canonical answer"
    assert graph.invoke.call_count == 1


def test_canonical_validation_failure_degrades_to_baseline(monkeypatch, client):
    """§28/§29: the graph returns valid refs but the canonical repository boundary raises. The REAL
    09E validator fails closed (no mocked validation result); product emits no provenance; Chat intact."""
    _set_graph_env(monkeypatch, enabled=True, base_url="http://sidecar.invalid:9999")
    graph = _install_router(monkeypatch, notebook_out=NB_A)
    db = FaithfulCanonicalDB()
    db.add_source(S1)
    db.add_member(S1, NB_A)
    db.fail_source = True  # real repo_query -> real validator -> fail closed
    _patch_db(monkeypatch, db)
    _patch_graph_query(monkeypatch, result=_graph_result([S1]))

    resp = _post(client, sources=(S1,))

    assert resp.status_code == 200
    body = resp.json()
    assert body["graph_provenance"] is None
    assert body["messages"][0]["content"] == "the canonical answer"
    assert graph.invoke.call_count == 1


# =========================================================================== content suppression


def test_graph_answer_sentinel_suppressed(monkeypatch, client):
    """§30: GraphQueryResult.answer sentinel is dropped by the real _default_graph_query seam and
    never appears in messages, provenance, or the serialized response."""
    _set_graph_env(monkeypatch, enabled=True, base_url="http://sidecar.invalid:9999")
    _install_router(monkeypatch, notebook_out=NB_A)
    db = FaithfulCanonicalDB()
    db.add_source(S2, title="clean title")
    db.add_member(S2, NB_A)
    _patch_db(monkeypatch, db)
    _patch_graph_query(
        monkeypatch, result=_graph_result([S2], answer=GRAPH_ANSWER_SENTINEL)
    )

    resp = _post(client, sources=(S1, S2))

    assert resp.status_code == 200
    assert _prov_ids(resp.json()) == [_skey(S2)]
    assert GRAPH_ANSWER_SENTINEL not in resp.text


def test_graph_excerpt_sentinel_suppressed(monkeypatch, client):
    """§31: GraphReference excerpt sentinel never reaches the product response."""
    _set_graph_env(monkeypatch, enabled=True, base_url="http://sidecar.invalid:9999")
    _install_router(monkeypatch, notebook_out=NB_A)
    db = FaithfulCanonicalDB()
    db.add_source(S2)
    db.add_member(S2, NB_A)
    _patch_db(monkeypatch, db)
    _patch_graph_query(
        monkeypatch,
        result=_graph_result([S2], excerpts={S2: [f"snippet {GRAPH_EXCERPT_SENTINEL}"]}),
    )

    resp = _post(client, sources=(S1, S2))

    assert resp.status_code == 200
    assert _prov_ids(resp.json()) == [_skey(S2)]
    assert GRAPH_EXCERPT_SENTINEL not in resp.text
    assert "rid-should-not-surface" not in resp.text


def test_graph_relationship_raw_sentinel_suppressed(monkeypatch, client):
    """§32: relationship/entity/score metadata in GraphQueryResult.raw never reaches product metadata
    (the seam returns only .references; raw is discarded)."""
    _set_graph_env(monkeypatch, enabled=True, base_url="http://sidecar.invalid:9999")
    _install_router(monkeypatch, notebook_out=NB_A)
    db = FaithfulCanonicalDB()
    db.add_source(S2)
    db.add_member(S2, NB_A)
    _patch_db(monkeypatch, db)
    raw = {
        "relationships": [{"label": GRAPH_RELATIONSHIP_SENTINEL, "score": 0.99}],
        "entities": [GRAPH_RELATIONSHIP_SENTINEL],
    }
    _patch_graph_query(monkeypatch, result=_graph_result([S2], raw=raw))

    resp = _post(client, sources=(S1, S2))

    assert resp.status_code == 200
    assert _prov_ids(resp.json()) == [_skey(S2)]
    assert GRAPH_RELATIONSHIP_SENTINEL not in resp.text


# =========================================================================== parity / compatibility


def test_chat_model_path_parity(monkeypatch, client):
    """§33: with identical canonical Chat inputs, provenance OFF vs ON leaves chat_graph.invoke input
    and call count identical; graph_provenance is the only additive response difference."""
    context_sources = (S1, S2)

    # Run A: provenance OFF.
    _set_graph_env(monkeypatch, enabled=False, base_url=None)
    graph_off = _install_router(monkeypatch, notebook_out=NB_A)
    db_off = FaithfulCanonicalDB()
    _patch_db(monkeypatch, db_off)
    body_off = _post(client, sources=context_sources).json()
    assert graph_off.invoke.call_count == 1
    ctx_off = graph_off.invoke.call_args.kwargs["input"]["context"]
    assert body_off["graph_provenance"] is None

    # Run B: provenance ON with a successful synthetic path.
    _set_graph_env(monkeypatch, enabled=True, base_url="http://sidecar.invalid:9999")
    graph_on = _install_router(monkeypatch, notebook_out=NB_A)
    db_on = FaithfulCanonicalDB()
    db_on.add_source(S2)
    db_on.add_member(S2, NB_A)
    _patch_db(monkeypatch, db_on)
    _patch_graph_query(monkeypatch, result=_graph_result([S2]))
    body_on = _post(client, sources=context_sources).json()
    assert graph_on.invoke.call_count == 1
    ctx_on = graph_on.invoke.call_args.kwargs["input"]["context"]

    # The canonical chat answer-context handed to the model is identical; only provenance differs.
    assert ctx_off == ctx_on
    assert body_off["messages"] == body_on["messages"]
    assert body_on["graph_provenance"] is not None
    assert _prov_ids(body_on) == [_skey(S2)]


def test_response_backward_compatible_optional_extension(monkeypatch, client):
    """§34/§35: absent → graph_provenance is null (optional extension, not byte-identical legacy JSON);
    present → an object; required fields (session_id, messages) unchanged."""
    # Absent (flag off).
    _set_graph_env(monkeypatch, enabled=False, base_url=None)
    _install_router(monkeypatch, notebook_out=NB_A)
    _patch_db(monkeypatch, FaithfulCanonicalDB())
    body_absent = _post(client, sources=(S1, S2)).json()
    assert body_absent["session_id"] == "abc"
    assert body_absent["messages"][0]["content"] == "the canonical answer"
    assert body_absent["graph_provenance"] is None

    # Present (flag on, validated intersection).
    _set_graph_env(monkeypatch, enabled=True, base_url="http://sidecar.invalid:9999")
    _install_router(monkeypatch, notebook_out=NB_A)
    db = FaithfulCanonicalDB()
    db.add_source(S2)
    db.add_member(S2, NB_A)
    _patch_db(monkeypatch, db)
    _patch_graph_query(monkeypatch, result=_graph_result([S2]))
    body_present = _post(client, sources=(S1, S2)).json()
    assert body_present["session_id"] == "abc"
    assert body_present["graph_provenance"] == {
        "has_validated_graph_provenance": True,
        "source_ids": [_skey(S2)],
        "count": 1,
    }
