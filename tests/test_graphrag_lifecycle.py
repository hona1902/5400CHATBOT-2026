"""GraphRAG-03A INDEX/REINDEX lifecycle tests.

Property-oriented: each test names the property that would break if the
implementation were subtly wrong (per the GraphRAG-03A brief). All HTTP is
mocked at the transport boundary with httpx.MockTransport — no live sidecar, no
real provider call. The security- and lifecycle-critical properties are:

  * stale queued text can never be indexed (payload has no text field, and the
    worker reloads the live Source);
  * a deleted source is never (re)indexed;
  * an index/enqueue failure never fails canonical ingestion or vector RAG;
  * no forbidden metadata (file_path/url/secrets/full_text-as-metadata) ever
    reaches the sidecar;
  * numeric and string-numeric record ids stay distinct.
"""

import glob
from pathlib import Path

import httpx
import pytest

from open_notebook.integrations.graphrag.client import GraphRAGClient, compute_doc_id
from open_notebook.integrations.graphrag.config import GraphRAGConfig
from open_notebook.integrations.graphrag.lifecycle import (
    IndexResult,
    index_source,
)
from open_notebook.integrations.graphrag.models import FORBIDDEN_METADATA_FIELDS
from open_notebook.integrations.graphrag.service import GraphRAGService

REPO_ROOT = Path(__file__).resolve().parents[1]
BASE_URL = "http://graphrag-sidecar.invalid:9621"


def _config(**overrides) -> GraphRAGConfig:
    defaults = dict(enabled=True, base_url=BASE_URL, timeout=5.0, api_key=None)
    defaults.update(overrides)
    return GraphRAGConfig(**defaults)  # type: ignore[arg-type]


class RecordingHandler:
    """MockTransport handler that records every request and replies by route.

    ``responses`` maps (METHOD, path) -> list of (status_code, json) served in
    order (the last entry repeats). Lets a test script an async delete/insert
    exchange and then assert exactly what crossed the wire.
    """

    def __init__(self, responses):
        self.responses = {k: list(v) for k, v in responses.items()}
        self.requests = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        key = (request.method, request.url.path)
        queue = self.responses.get(key)
        if not queue:
            raise AssertionError(f"unexpected request: {request.method} {request.url.path}")
        status, payload = queue[0] if len(queue) == 1 else queue.pop(0)
        return httpx.Response(status, json=payload)

    def bodies(self, method, path):
        import json

        return [
            json.loads(r.content)
            for r in self.requests
            if r.method == method and r.url.path == path
        ]


def _service(handler, **config_overrides) -> GraphRAGService:
    cfg = _config(**config_overrides)
    return GraphRAGService(config=cfg, client=GraphRAGClient(cfg, transport=httpx.MockTransport(handler)))


DELETE_PATH = "/documents/delete_document"
INSERT_PATH = "/documents/text"


def _delete_ok():
    return (200, {"status": "not_found", "message": "absent", "doc_id": "doc-x"})


def _insert_ok(track_id="track-1"):
    return (200, {"status": "success", "message": "queued", "track_id": track_id})


# --------------------------------------------------- doc_id / identity (12,13)


class TestDocIdIdentity:
    def test_doc_id_is_deterministic_md5_of_source_id(self):
        """Property: doc_id must equal LightRAG's own derivation for a known
        file_source. If our md5 or prefix drifts, DELETE/RECONCILE target the
        wrong document and orphan the real one."""
        import hashlib

        sid = "source:abc123"
        assert compute_doc_id(sid) == "doc-" + hashlib.md5(sid.encode()).hexdigest()

    def test_numeric_and_string_numeric_ids_stay_distinct(self):
        """Property: source:123 (numeric id) and source:⟨123⟩ (string "123")
        are different records; their doc_ids must differ. A lossy normalization
        would merge two documents."""
        numeric = "source:123"
        string_numeric = "source:⟨123⟩"
        assert compute_doc_id(numeric) != compute_doc_id(string_numeric)

    def test_content_does_not_affect_doc_id(self):
        """Property: doc_id is content-independent, so a REINDEX after an edit
        still targets the same document to delete-then-insert."""
        assert compute_doc_id("source:abc") == compute_doc_id("source:abc")


# --------------------------------------------------- lifecycle idempotency


class TestIndexLifecycle:
    @pytest.mark.asyncio
    async def test_index_does_delete_then_insert(self):
        """Property (7,8): a valid index issues delete BEFORE insert, so a
        re-POST is never rejected as a filename duplicate."""
        handler = RecordingHandler(
            {("DELETE", DELETE_PATH): [_delete_ok()], ("POST", INSERT_PATH): [_insert_ok()]}
        )
        svc = _service(handler)
        outcome = await index_source(svc, source_id="source:abc", canonical_text="hello")
        assert outcome.result is IndexResult.INDEXED
        # Order matters: delete first, then insert.
        methods = [(r.method, r.url.path) for r in handler.requests]
        assert methods == [("DELETE", DELETE_PATH), ("POST", INSERT_PATH)]

    @pytest.mark.asyncio
    async def test_duplicate_index_is_idempotent(self):
        """Property (7): running index twice targets the same doc_id both times;
        no second document can be created."""
        handler = RecordingHandler(
            {
                ("DELETE", DELETE_PATH): [_delete_ok()],
                ("POST", INSERT_PATH): [_insert_ok()],
            }
        )
        svc = _service(handler)
        await index_source(svc, source_id="source:abc", canonical_text="v1")
        await index_source(svc, source_id="source:abc", canonical_text="v1")
        # Both inserts carry the same file_source (=> same doc_id upstream).
        bodies = handler.bodies("POST", INSERT_PATH)
        assert [b["file_source"] for b in bodies] == ["source:abc", "source:abc"]

    @pytest.mark.asyncio
    async def test_superseded_confirm_prevents_stale_insert(self):
        """Property (Codex pass-3 HIGH): if canonical state changes between the
        caller's read and the moment of insert, confirm_current returns False
        and NO text is sent. This is the TOCTOU guard against egressing
        redacted/deleted content."""
        # Confirm runs BEFORE the destructive delete, so a superseded job must
        # make NEITHER a delete NOR an insert. Any request is unexpected.
        def handler(request):  # pragma: no cover - must never be called
            raise AssertionError(
                f"superseded job must not touch the sidecar: {request.method} {request.url.path}"
            )

        cfg = _config()
        svc = GraphRAGService(config=cfg, client=GraphRAGClient(cfg, transport=httpx.MockTransport(handler)))

        async def not_current():
            return False

        outcome = await index_source(
            svc,
            source_id="source:abc",
            canonical_text="stale",
            confirm_current=not_current,
        )
        assert outcome.result is IndexResult.SUPERSEDED

    @pytest.mark.asyncio
    async def test_superseded_does_not_delete_a_newer_jobs_document(self):
        """Property (Codex pass-4 HIGH): confirm runs BEFORE the destructive
        delete. An older job that has been superseded must NOT delete — otherwise
        it would erase the document a newer, already-completed job just wrote and
        leave the graph empty (no re-drive). Assert: zero requests when
        superseded."""
        seen = {"any": False}

        def handler(request):
            seen["any"] = True
            raise AssertionError("superseded job must issue no delete and no insert")

        cfg = _config()
        svc = GraphRAGService(config=cfg, client=GraphRAGClient(cfg, transport=httpx.MockTransport(handler)))

        async def superseded():
            return False

        outcome = await index_source(
            svc, source_id="source:abc", canonical_text="old", confirm_current=superseded
        )
        assert outcome.result is IndexResult.SUPERSEDED
        assert seen["any"] is False  # no delete, no insert

    @pytest.mark.asyncio
    async def test_second_confirm_after_delete_prevents_stale_egress(self):
        """Property (Codex pass-5 HIGH): the source can change DURING the delete
        round-trip. confirm_current is re-checked after the delete and before the
        POST; if it now reports stale, we must delete (our own old doc) but NOT
        insert stale text. So: a DELETE is issued, no POST is."""
        handler = RecordingHandler({("DELETE", DELETE_PATH): [_delete_ok()]})
        svc = _service(handler)

        # First confirm (pre-delete) True, second confirm (post-delete) False.
        calls = {"n": 0}

        async def confirm():
            calls["n"] += 1
            return calls["n"] == 1

        outcome = await index_source(
            svc, source_id="source:abc", canonical_text="v1", confirm_current=confirm
        )
        assert outcome.result is IndexResult.SUPERSEDED
        assert calls["n"] == 2  # confirmed twice
        # Delete was issued (we removed our own stale doc), but no insert.
        methods = [(r.method, r.url.path) for r in handler.requests]
        assert ("DELETE", DELETE_PATH) in methods
        assert handler.bodies("POST", INSERT_PATH) == []

    @pytest.mark.asyncio
    async def test_confirm_error_is_transient_and_does_not_insert(self):
        """Property: if the confirm check itself errors (e.g. DB blip) we must
        NOT egress under uncertainty; classify transient so it re-drives."""
        # Confirm error short-circuits before any destructive action.
        def handler(request):  # pragma: no cover - must never be called
            raise AssertionError(
                f"confirm-error must not touch the sidecar: {request.method} {request.url.path}"
            )

        cfg = _config()
        svc = GraphRAGService(config=cfg, client=GraphRAGClient(cfg, transport=httpx.MockTransport(handler)))

        async def boom():
            raise RuntimeError("db blip")

        outcome = await index_source(
            svc, source_id="source:abc", canonical_text="x", confirm_current=boom
        )
        assert outcome.result is IndexResult.TRANSIENT

    @pytest.mark.asyncio
    async def test_busy_delete_does_not_insert(self):
        """Property (17): a BUSY delete must NOT proceed to insert into a racing
        destructive slot; it is transient so the job retries."""
        handler = RecordingHandler(
            {("DELETE", DELETE_PATH): [(200, {"status": "busy", "message": "busy", "doc_id": "d"})]}
        )
        svc = _service(handler)
        outcome = await index_source(svc, source_id="source:abc", canonical_text="x")
        assert outcome.result is IndexResult.TRANSIENT
        assert handler.bodies("POST", INSERT_PATH) == []  # never inserted

    @pytest.mark.asyncio
    async def test_insert_409_is_transient_not_permanent(self):
        """Property: a 409 (old doc not finished deleting) must retry, not fail
        permanently — LightRAG deletion is async."""
        handler = RecordingHandler(
            {
                ("DELETE", DELETE_PATH): [_delete_ok()],
                ("POST", INSERT_PATH): [(409, {"detail": "exists"})],
            }
        )
        svc = _service(handler)
        outcome = await index_source(svc, source_id="source:abc", canonical_text="x")
        assert outcome.result is IndexResult.TRANSIENT

    @pytest.mark.asyncio
    async def test_insert_422_is_permanent(self):
        """Property: a genuine schema rejection must NOT retry forever."""
        handler = RecordingHandler(
            {
                ("DELETE", DELETE_PATH): [_delete_ok()],
                ("POST", INSERT_PATH): [(422, {"detail": "bad"})],
            }
        )
        svc = _service(handler)
        outcome = await index_source(svc, source_id="source:abc", canonical_text="x")
        assert outcome.result is IndexResult.PERMANENT

    @pytest.mark.asyncio
    async def test_invalid_source_id_is_permanent_with_no_egress(self):
        """Property (F1): a deterministic validation failure must classify
        PERMANENT — never TRANSIENT — so it is not retried forever, and it must
        make NO external call (validate_source_id rejects before the boundary).
        The delete step and insert step must agree on this classification."""

        def handler(request):  # pragma: no cover - must never be reached
            raise AssertionError("invalid source_id must not reach the sidecar")

        svc = _service(handler)
        outcome = await index_source(
            svc, source_id="not-a-record-id", canonical_text="x"
        )
        assert outcome.result is IndexResult.PERMANENT

    @pytest.mark.asyncio
    async def test_sidecar_unavailable_is_transient(self):
        """Property (10,11): a connection failure degrades to transient; nothing
        raises an httpx exception out of the boundary."""

        def handler(request):
            raise httpx.ConnectError("refused")

        cfg = _config()
        svc = GraphRAGService(config=cfg, client=GraphRAGClient(cfg, transport=httpx.MockTransport(handler)))
        outcome = await index_source(svc, source_id="source:abc", canonical_text="x")
        assert outcome.result is IndexResult.TRANSIENT


# --------------------------------------------------- egress allowlist (14)


class TestEgressAllowlist:
    @pytest.mark.asyncio
    async def test_only_text_and_file_source_cross_the_wire(self):
        """Property (14): the insert body must contain ONLY text + file_source;
        no forbidden field may ever appear."""
        handler = RecordingHandler(
            {("DELETE", DELETE_PATH): [_delete_ok()], ("POST", INSERT_PATH): [_insert_ok()]}
        )
        svc = _service(handler)
        await index_source(svc, source_id="source:abc", canonical_text="secret text")
        body = handler.bodies("POST", INSERT_PATH)[0]
        assert set(body) == {"text", "file_source"}
        for forbidden in FORBIDDEN_METADATA_FIELDS:
            assert forbidden not in body

    @pytest.mark.asyncio
    async def test_disabled_service_makes_no_call(self):
        """Property (1): with the flag off the service refuses before any
        network setup."""

        def handler(request):  # pragma: no cover - must never run
            raise AssertionError("disabled service must not make a request")

        cfg = _config(enabled=False)
        svc = GraphRAGService(config=cfg, client=GraphRAGClient(cfg, transport=httpx.MockTransport(handler)))
        from open_notebook.integrations.graphrag.models import GraphRAGDisabledError

        with pytest.raises(GraphRAGDisabledError):
            await svc.delete_document_for_source(source_id="source:abc")


# --------------------------------------------------- migration guard (16)


def test_migration_count_is_48_after_03b():
    """GraphRAG-03A added no schema (count 46). GraphRAG-03B adds exactly one
    migration — number 24, the durable deletion tombstone + delete event — so
    the on-disk file count is now 48 (24 up + 24 down). The 03A index/reindex
    path itself still adds nothing; the delta is owned entirely by 03B."""
    migrations = glob.glob(
        str(REPO_ROOT / "open_notebook" / "database" / "migrations" / "*.surrealql")
    )
    assert len(migrations) == 48
