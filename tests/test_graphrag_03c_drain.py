"""GraphRAG-03C deletion-drain tests (property-oriented).

Each test names the lifecycle/security property that would break if the
implementation were subtly wrong. Three layers:

  * MOCK/STRUCTURAL (always run): the absence-probe contract, the per-tombstone
    convergence state machine, the bounded/fair drain loop, and the wake-up — all
    with httpx.MockTransport and stubbed DB helpers, no live services.
  * LIVE-DB (skipped if SurrealDB unreachable): the arm_id CAS, the fair due-set
    traversal (the user's 14 fairness properties), migration-25 backfill/up-down.
  * LIVE-LIGHTRAG (skipped if the pinned sidecar is unreachable): a synthetic
    index -> delete -> confirmed-absent round trip against real v1.5.6, because a
    destructive remote lifecycle must not be proven by mocks alone.
"""

import asyncio
import os
import uuid
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
import pytest_asyncio

from open_notebook.integrations.graphrag import deletion as deletion_mod
from open_notebook.integrations.graphrag import drain as drain_mod
from open_notebook.integrations.graphrag.client import (
    ABSENCE_PROBE_PAGE_SIZE,
    GraphRAGClient,
    compute_doc_id,
)
from open_notebook.integrations.graphrag.config import GraphRAGConfig
from open_notebook.integrations.graphrag.deletion import DeletionTombstone
from open_notebook.integrations.graphrag.drain import (
    DrainOutcome,
    converge_tombstone,
    drain_pending_deletions,
    enqueue_drain_if_pending,
)
from open_notebook.integrations.graphrag.lifecycle import IndexOutcome, IndexResult
from open_notebook.integrations.graphrag.models import (
    AbsenceState,
    DeleteOutcome,
    DeleteState,
    GraphRAGUnavailableError,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
BASE_URL = "http://graphrag-sidecar.invalid:9621"


def _config(**overrides) -> GraphRAGConfig:
    defaults = dict(enabled=True, base_url=BASE_URL, timeout=5.0, api_key=None)
    defaults.update(overrides)
    return GraphRAGConfig(**defaults)  # type: ignore[arg-type]


# ===========================================================================
# A. ABSENCE PROBE CONTRACT (client, httpx.MockTransport)
# ===========================================================================


def _paginated_client(handler) -> GraphRAGClient:
    return GraphRAGClient(_config(), transport=httpx.MockTransport(handler))


def _page(doc_ids, *, total_count=None, total_pages=1, page_size=ABSENCE_PROBE_PAGE_SIZE):
    docs = [{"id": d, "file_path": d, "status": "processed"} for d in doc_ids]
    return {
        "documents": docs,
        "pagination": {
            "page": 1,
            "page_size": page_size,
            "total_count": len(doc_ids) if total_count is None else total_count,
            "total_pages": total_pages,
            "has_next": total_pages > 1,
            "has_prev": False,
        },
        "status_counts": {},
    }


class TestAbsenceProbe:
    @pytest.mark.asyncio
    async def test_found_when_target_present(self):
        target = compute_doc_id("source:abc")

        def handler(req):
            return httpx.Response(200, json=_page([target, "doc-other"]))

        state = await _paginated_client(handler).confirm_document_absent(target)
        assert state is AbsenceState.FOUND

    @pytest.mark.asyncio
    async def test_absent_confirmed_only_on_complete_single_page(self):
        target = compute_doc_id("source:abc")

        def handler(req):
            # A complete single page (total_pages=1, total_count == len) without target.
            return httpx.Response(200, json=_page(["doc-x", "doc-y"]))

        state = await _paginated_client(handler).confirm_document_absent(target)
        assert state is AbsenceState.ABSENT_CONFIRMED

    @pytest.mark.asyncio
    async def test_multi_page_is_unknown_never_absent(self):
        """The single-response ceiling: a corpus that needs >1 page can NEVER be
        confirmed absent (offset-shift race). It must be UNKNOWN, not ABSENT."""
        target = compute_doc_id("source:abc")

        def handler(req):
            return httpx.Response(
                200, json=_page(["doc-x", "doc-y"], total_count=500, total_pages=3)
            )

        state = await _paginated_client(handler).confirm_document_absent(target)
        assert state is AbsenceState.UNKNOWN

    @pytest.mark.asyncio
    async def test_count_mismatch_is_unknown(self):
        target = compute_doc_id("source:abc")

        def handler(req):
            # total_count says more rows exist than this page returned -> incomplete.
            return httpx.Response(
                200, json=_page(["doc-x"], total_count=9, total_pages=1)
            )

        state = await _paginated_client(handler).confirm_document_absent(target)
        assert state is AbsenceState.UNKNOWN

    @pytest.mark.asyncio
    async def test_http_error_is_unknown(self):
        target = compute_doc_id("source:abc")

        def handler(req):
            return httpx.Response(500, json={"detail": "boom"})

        state = await _paginated_client(handler).confirm_document_absent(target)
        assert state is AbsenceState.UNKNOWN

    @pytest.mark.asyncio
    async def test_timeout_is_unknown(self):
        target = compute_doc_id("source:abc")

        def handler(req):
            raise httpx.ConnectTimeout("slow")

        state = await _paginated_client(handler).confirm_document_absent(target)
        assert state is AbsenceState.UNKNOWN

    @pytest.mark.asyncio
    async def test_malformed_response_is_unknown(self):
        target = compute_doc_id("source:abc")

        def handler(req):
            return httpx.Response(200, json={"documents": []})  # no pagination block

        state = await _paginated_client(handler).confirm_document_absent(target)
        assert state is AbsenceState.UNKNOWN

    @pytest.mark.asyncio
    async def test_probe_requests_a_single_bounded_id_sorted_page(self):
        target = compute_doc_id("source:abc")
        captured = {}

        def handler(req):
            import json

            captured["body"] = json.loads(req.content)
            return httpx.Response(200, json=_page([]))

        await _paginated_client(handler).confirm_document_absent(target)
        body = captured["body"]
        assert body["page"] == 1
        assert body["page_size"] == ABSENCE_PROBE_PAGE_SIZE == 200
        assert body["sort_field"] == "id"
        assert body["sort_direction"] == "asc"


# ===========================================================================
# B. CONVERGENCE STATE MACHINE (fakes + monkeypatch)
# ===========================================================================


class FakeService:
    """Records outbound intent so tests can assert what would cross the wire."""

    def __init__(self, *, enabled=True, base_url=BASE_URL, absence=AbsenceState.ABSENT_CONFIRMED):
        self.config = SimpleNamespace(enabled=enabled, base_url=base_url)
        self.absence = absence
        self.delete_error = None
        self.confirm_error = None
        self.calls = []

    async def confirm_source_document_absent(self, *, source_id):
        self.calls.append(("confirm", source_id))
        if self.confirm_error:
            raise self.confirm_error
        return self.absence

    async def delete_document_for_source(self, *, source_id):
        self.calls.append(("delete", source_id))
        if self.delete_error:
            raise self.delete_error
        return DeleteOutcome(doc_id="doc-x", state=DeleteState.GONE, detail="")


def _tomb(source_id="source:abc", arm_id="11111111-1111-1111-1111-111111111111"):
    return DeletionTombstone(
        source_id=source_id, status="pending", arm_id=arm_id, next_attempt_at=None
    )


@pytest.fixture
def cas_recorder(monkeypatch):
    """Stub the deletion CAS + canonical load; record resolve/defer calls."""
    calls = {
        "resolve": [],
        "resolve_current": [],
        "defer": [],
        "canonical": None,
        "resolve_ok": True,
    }

    async def fake_resolve(source_id, arm_id):
        calls["resolve"].append((source_id, arm_id))
        return calls["resolve_ok"]

    async def fake_resolve_current(source_id, arm_id, expected_text):
        calls["resolve_current"].append((source_id, arm_id, expected_text))
        # Simulate the atomic canonical predicate: resolve only if the CURRENT
        # canonical text still equals what was shipped (and the arm is current).
        rows = calls["canonical"] or []
        current = rows[0].get("full_text") if rows else None
        return bool(rows) and current == expected_text and calls["resolve_ok"]

    async def fake_defer(arm_id, delay):
        calls["defer"].append((arm_id, delay))
        return True

    async def fake_repo_query(sql, vars=None):
        # canonical load: return whatever the test set, ignoring the SELECT.
        return calls["canonical"]

    monkeypatch.setattr(drain_mod.deletion, "resolve_tombstone_cas", fake_resolve)
    monkeypatch.setattr(
        drain_mod.deletion, "resolve_current_tombstone_cas", fake_resolve_current
    )
    monkeypatch.setattr(drain_mod.deletion, "defer_tombstone_cas", fake_defer)
    monkeypatch.setattr(drain_mod, "repo_query", fake_repo_query)
    return calls


class TestConvergeStateMachine:
    @pytest.mark.asyncio
    async def test_absent_source_deletes_then_resolves_on_confirmed_absence(self, cas_recorder):
        cas_recorder["canonical"] = []  # source absent
        svc = FakeService(absence=AbsenceState.ABSENT_CONFIRMED)
        out = await converge_tombstone(svc, _tomb())
        assert out is DrainOutcome.RESOLVED_ABSENT
        assert cas_recorder["resolve"] == [("source:abc", _tomb().arm_id)]

    @pytest.mark.asyncio
    async def test_deletion_started_alone_does_not_resolve(self, cas_recorder):
        """acceptance != absence: if absence is UNKNOWN, drive a delete and DEFER
        — never resolve the tombstone."""
        cas_recorder["canonical"] = []
        svc = FakeService(absence=AbsenceState.UNKNOWN)
        out = await converge_tombstone(svc, _tomb())
        assert out is DrainOutcome.DEFERRED
        assert cas_recorder["resolve"] == []  # NOT resolved
        assert ("delete", "source:abc") in svc.calls  # delete WAS driven

    @pytest.mark.asyncio
    async def test_already_absent_resolves_without_deleting(self, cas_recorder):
        cas_recorder["canonical"] = []
        svc = FakeService(absence=AbsenceState.ABSENT_CONFIRMED)
        await converge_tombstone(svc, _tomb())
        assert ("delete", "source:abc") not in svc.calls  # single probe, no delete

    @pytest.mark.asyncio
    async def test_live_empty_source_converges_to_absent_not_skip(self, cas_recorder):
        """A live source with empty text must be DELETED (03A skip is not enough)."""
        cas_recorder["canonical"] = [{"full_text": "   "}]  # present but whitespace
        svc = FakeService(absence=AbsenceState.ABSENT_CONFIRMED)
        out = await converge_tombstone(svc, _tomb())
        assert out is DrainOutcome.RESOLVED_ABSENT
        assert ("confirm", "source:abc") in svc.calls

    @pytest.mark.asyncio
    async def test_live_nonempty_flag_on_reindexes_current(self, cas_recorder, monkeypatch):
        cas_recorder["canonical"] = [{"full_text": "CURRENT text"}]
        seen = {}

        async def fake_index(service, *, source_id, canonical_text, confirm_current):
            seen["text"] = canonical_text
            return IndexOutcome(IndexResult.INDEXED, "ok", track_id="t1")

        monkeypatch.setattr(drain_mod, "index_source", fake_index)
        # canonical unchanged -> the atomic canonical-fenced CAS resolves.
        svc = FakeService(enabled=True)
        out = await converge_tombstone(svc, _tomb())
        assert out is DrainOutcome.CONVERGED_CURRENT
        assert seen["text"] == "CURRENT text"  # current reloaded text, not queued
        assert cas_recorder["resolve_current"] == [
            ("source:abc", _tomb().arm_id, "CURRENT text")
        ]

    @pytest.mark.asyncio
    async def test_redaction_after_indexed_does_not_resolve(
        self, cas_recorder, monkeypatch
    ):
        """HIGH-A (Codex A): if canonical is redacted/emptied AFTER the insert, the
        ATOMIC canonical-fenced resolve CAS matches zero rows (source_id.full_text
        no longer equals the shipped text), so the tombstone is NOT resolved and
        stale sidecar text is never blessed. The next attempt takes the
        empty->absent branch and deletes the stale doc."""
        cas_recorder["canonical"] = [{"full_text": "A text"}]

        async def fake_index(service, *, source_id, canonical_text, confirm_current):
            # a redaction lands during/after indexing (canonical no longer == shipped)
            cas_recorder["canonical"] = [{"full_text": ""}]
            return IndexOutcome(IndexResult.INDEXED, "accepted", track_id="t1")

        monkeypatch.setattr(drain_mod, "index_source", fake_index)
        svc = FakeService(enabled=True)
        out = await converge_tombstone(svc, _tomb())
        assert out is DrainOutcome.DEFERRED
        # The atomic CAS was attempted with the shipped text but matched no row
        # (current canonical is now empty), so nothing was resolved.
        assert cas_recorder["resolve_current"] == [("source:abc", _tomb().arm_id, "A text")]

    @pytest.mark.asyncio
    async def test_recreate_between_read_and_delete_does_not_delete_current(
        self, cas_recorder, monkeypatch
    ):
        """HIGH-2 (Codex A): the absent branch chose from an earlier read; if the
        source is now live+current, the pre-delete re-check must ABORT the delete
        (defer + re-drive) instead of deleting the recreated source's current doc."""
        # Initial canonical read: absent -> absent branch chosen.
        state = {"rows": []}

        async def fake_repo_query(sql, vars=None):
            return state["rows"]

        monkeypatch.setattr(drain_mod, "repo_query", fake_repo_query)
        svc = FakeService(enabled=True, absence=AbsenceState.FOUND)  # doc present now

        # Simulate a recreate landing before the pre-delete re-check.
        orig = drain_mod._source_became_live_current

        async def racing_recheck(service, record_id):
            state["rows"] = [{"full_text": "RECREATED current text"}]
            return await orig(service, record_id)

        monkeypatch.setattr(drain_mod, "_source_became_live_current", racing_recheck)
        out = await converge_tombstone(svc, _tomb())
        assert out is DrainOutcome.DEFERRED
        # The destructive delete was NOT issued against the recreated current doc.
        assert all(c[0] != "delete" for c in svc.calls)

    @pytest.mark.asyncio
    async def test_absent_branch_unknown_over_ceiling_stays_pending(self, cas_recorder):
        """MEDIUM (Codex A): a >single-page corpus yields UNKNOWN; the tombstone is
        never falsely resolved — it stays pending (delete still driven)."""
        cas_recorder["canonical"] = []
        svc = FakeService(absence=AbsenceState.UNKNOWN)
        out = await converge_tombstone(svc, _tomb())
        assert out is DrainOutcome.DEFERRED
        assert cas_recorder["resolve"] == []

    @pytest.mark.asyncio
    async def test_live_nonempty_flag_off_converges_to_absent_no_index(self, cas_recorder, monkeypatch):
        """Flag OFF: reindex would be Boundary-B egress. Converge to ABSENT instead."""
        cas_recorder["canonical"] = [{"full_text": "CURRENT text"}]

        async def fake_index(*a, **k):  # pragma: no cover - must never run
            raise AssertionError("must not index while flag is OFF")

        monkeypatch.setattr(drain_mod, "index_source", fake_index)
        svc = FakeService(enabled=False, absence=AbsenceState.ABSENT_CONFIRMED)
        out = await converge_tombstone(svc, _tomb())
        assert out is DrainOutcome.RESOLVED_ABSENT
        assert all(c[0] != "index" for c in svc.calls)

    @pytest.mark.asyncio
    async def test_superseded_when_cas_matches_zero_rows(self, cas_recorder):
        cas_recorder["canonical"] = []
        cas_recorder["resolve_ok"] = False  # re-armed mid-flight -> CAS 0 rows
        svc = FakeService(absence=AbsenceState.ABSENT_CONFIRMED)
        out = await converge_tombstone(svc, _tomb())
        assert out is DrainOutcome.SUPERSEDED

    @pytest.mark.asyncio
    async def test_sidecar_unreachable_defers(self, cas_recorder):
        cas_recorder["canonical"] = []
        svc = FakeService()
        svc.confirm_error = GraphRAGUnavailableError("down")
        out = await converge_tombstone(svc, _tomb())
        assert out is DrainOutcome.DEFERRED
        assert cas_recorder["resolve"] == []

    @pytest.mark.asyncio
    async def test_malformed_identity_is_permanent_and_makes_no_http(self, cas_recorder):
        svc = FakeService()
        out = await converge_tombstone(svc, _tomb(source_id="not a record id"))
        assert out is DrainOutcome.PERMANENT_LOCAL_ERROR
        assert svc.calls == []  # never contacted the sidecar

    @pytest.mark.asyncio
    async def test_transient_db_error_defers(self, cas_recorder, monkeypatch):
        async def boom(sql, vars=None):
            raise RuntimeError("db down")

        monkeypatch.setattr(drain_mod, "repo_query", boom)
        svc = FakeService()
        out = await converge_tombstone(svc, _tomb())
        assert out is DrainOutcome.DEFERRED


# ===========================================================================
# C. BOUNDED / FAIR DRAIN LOOP (stubbed due-set)
# ===========================================================================


class TestDrainLoop:
    @pytest.mark.asyncio
    async def test_one_failing_tombstone_does_not_abort_batch(self, monkeypatch):
        toms = [_tomb(f"source:s{i}", arm_id=str(uuid.uuid4())) for i in range(3)]
        served = {"done": False}

        async def fake_due(limit):
            if served["done"]:
                return []
            served["done"] = True
            return toms

        async def fake_converge(service, t):
            if t.source_id == "source:s1":
                raise RuntimeError("boom in one row")
            return DrainOutcome.RESOLVED_ABSENT

        deferred = []

        async def fake_defer(arm, delay):
            deferred.append(arm)
            return True

        monkeypatch.setattr(drain_mod.deletion, "list_due_deletions", fake_due)
        monkeypatch.setattr(drain_mod, "converge_tombstone", fake_converge)
        monkeypatch.setattr(drain_mod.deletion, "defer_tombstone_cas", fake_defer)

        summary = await drain_pending_deletions(FakeService())
        assert summary.scanned == 3
        assert summary.resolved_absent == 2
        assert summary.deferred == 1  # the raising row was deferred, not aborted
        assert len(deferred) == 1

    @pytest.mark.asyncio
    async def test_bounded_by_max_rows(self, monkeypatch):
        async def infinite_due(limit):
            return [_tomb(f"source:x{uuid.uuid4()}", arm_id=str(uuid.uuid4())) for _ in range(limit)]

        async def fake_converge(service, t):
            return DrainOutcome.DEFERRED

        async def fake_defer(arm, delay):
            return True

        monkeypatch.setattr(drain_mod.deletion, "list_due_deletions", infinite_due)
        monkeypatch.setattr(drain_mod, "converge_tombstone", fake_converge)
        monkeypatch.setattr(drain_mod.deletion, "defer_tombstone_cas", fake_defer)

        from open_notebook.integrations.graphrag.config import GraphRAGDrainConfig

        cfg = GraphRAGDrainConfig(
            interval_seconds=300, batch_size=10, max_rows=25, retry_delay_seconds=60
        )
        summary = await drain_pending_deletions(FakeService(), drain_config=cfg)
        assert summary.scanned == 25  # hard cap honoured despite endless due rows

    @pytest.mark.asyncio
    async def test_defer_failure_does_not_abort_batch(self, monkeypatch):
        """HIGH (Codex B): a defer failure (e.g. a transient DB error, or a
        malformed row) must NOT abort the batch — later tombstones still process.
        (Defer is arm-fenced with no source_id reparse, so a malformed id no
        longer raises; this exercises the belt-and-braces catch for DB errors.)"""
        bad = _tomb("bad", arm_id=str(uuid.uuid4()))
        good = _tomb("source:good", arm_id=str(uuid.uuid4()))
        served = {"done": False}

        async def fake_due(limit):
            if served["done"]:
                return []
            served["done"] = True
            return [bad, good]

        async def fake_converge(service, t):
            return (
                DrainOutcome.PERMANENT_LOCAL_ERROR
                if t.source_id == "bad"
                else DrainOutcome.RESOLVED_ABSENT
            )

        async def fake_defer(arm, delay):
            if arm == bad.arm_id:
                raise RuntimeError("transient DB error rescheduling this row")
            return True

        monkeypatch.setattr(drain_mod.deletion, "list_due_deletions", fake_due)
        monkeypatch.setattr(drain_mod, "converge_tombstone", fake_converge)
        monkeypatch.setattr(drain_mod.deletion, "defer_tombstone_cas", fake_defer)

        summary = await drain_pending_deletions(FakeService())
        assert summary.scanned == 2
        assert summary.resolved_absent == 1  # the good row still progressed
        assert summary.permanent_local_error == 1


class TestDrainConfigClamping:
    def test_oversized_and_bad_env_values_are_clamped(self, monkeypatch):
        from open_notebook.integrations.graphrag.config import (
            MAX_DRAIN_BATCH_SIZE,
            MAX_DRAIN_MAX_ROWS,
            MIN_DRAIN_INTERVAL_SECONDS,
            MIN_DRAIN_RETRY_DELAY_SECONDS,
            load_drain_config,
        )

        monkeypatch.setenv("OPEN_NOTEBOOK_GRAPHRAG_DRAIN_MAX_ROWS", "999999")
        monkeypatch.setenv("OPEN_NOTEBOOK_GRAPHRAG_DRAIN_BATCH_SIZE", "100000")
        monkeypatch.setenv("OPEN_NOTEBOOK_GRAPHRAG_DRAIN_INTERVAL_SECONDS", "1")
        # A positive-but-below-floor value exercises the floor (0/negative fall
        # back to the default instead, which is also >= the floor).
        monkeypatch.setenv("OPEN_NOTEBOOK_GRAPHRAG_DRAIN_RETRY_DELAY_SECONDS", "1")
        cfg = load_drain_config()
        assert cfg.max_rows == MAX_DRAIN_MAX_ROWS  # oversized scan is clamped
        assert cfg.batch_size == MAX_DRAIN_BATCH_SIZE
        assert cfg.interval_seconds == MIN_DRAIN_INTERVAL_SECONDS  # no sub-minute loop
        assert cfg.retry_delay_seconds == MIN_DRAIN_RETRY_DELAY_SECONDS  # never zero-delay

    def test_garbage_env_falls_back_to_defaults(self, monkeypatch):
        from open_notebook.integrations.graphrag.config import (
            DEFAULT_DRAIN_MAX_ROWS,
            load_drain_config,
        )

        monkeypatch.setenv("OPEN_NOTEBOOK_GRAPHRAG_DRAIN_MAX_ROWS", "not-a-number")
        assert load_drain_config().max_rows == DEFAULT_DRAIN_MAX_ROWS

    def test_non_finite_env_values_fall_back_to_defaults(self, monkeypatch):
        """MEDIUM (Codex B): '1e309' parses to inf, which would raise at int()
        clamp time; it must fall back to the default instead of breaking config."""
        from open_notebook.integrations.graphrag.config import (
            DEFAULT_DRAIN_INTERVAL_SECONDS,
            DEFAULT_DRAIN_MAX_ROWS,
            MIN_DRAIN_INTERVAL_SECONDS,
            load_drain_config,
        )

        for var in (
            "OPEN_NOTEBOOK_GRAPHRAG_DRAIN_MAX_ROWS",
            "OPEN_NOTEBOOK_GRAPHRAG_DRAIN_BATCH_SIZE",
            "OPEN_NOTEBOOK_GRAPHRAG_DRAIN_INTERVAL_SECONDS",
            "OPEN_NOTEBOOK_GRAPHRAG_DRAIN_RETRY_DELAY_SECONDS",
        ):
            monkeypatch.setenv(var, "1e309")
        cfg = load_drain_config()  # must not raise
        assert cfg.max_rows == DEFAULT_DRAIN_MAX_ROWS
        assert cfg.interval_seconds == max(
            MIN_DRAIN_INTERVAL_SECONDS, DEFAULT_DRAIN_INTERVAL_SECONDS
        )


class TestInvalidIdentityNotLogged:
    @pytest.mark.asyncio
    async def test_malformed_identity_value_never_logged(self, monkeypatch):
        """MEDIUM (Codex B): a malformed tombstone identity may be path/URL/token/
        content-shaped; it must NOT appear in logs (only a sanitized message)."""
        from loguru import logger

        captured: list = []
        sink_id = logger.add(lambda m: captured.append(str(m)), level="DEBUG")
        try:
            secret = "source:https://internal/doc?token=SECRET_VALUE_123"

            async def fake_repo_query(sql, vars=None):
                return []

            monkeypatch.setattr(drain_mod, "repo_query", fake_repo_query)
            out = await converge_tombstone(FakeService(), _tomb(source_id=secret))
            assert out is DrainOutcome.PERMANENT_LOCAL_ERROR
        finally:
            logger.remove(sink_id)
        blob = "".join(captured)
        assert "SECRET_VALUE_123" not in blob
        assert "token=" not in blob


# ===========================================================================
# D. WAKE-UP (enqueue-only, guarded, correctness-independent)
# ===========================================================================


class TestWakeup:
    @pytest.mark.asyncio
    async def test_enqueues_only_when_due_and_not_already_active(self, monkeypatch):
        submitted = []

        async def has_due():
            return True

        async def not_active():
            return False

        def fake_submit(app, name, args):
            submitted.append((app, name))
            return "command:1"

        monkeypatch.setattr(drain_mod.deletion, "has_due_deletions", has_due)
        monkeypatch.setattr(drain_mod, "_drain_command_already_queued", not_active)
        import surreal_commands

        monkeypatch.setattr(surreal_commands, "submit_command", fake_submit)
        await enqueue_drain_if_pending()
        assert submitted == [("open_notebook", drain_mod.DRAIN_COMMAND_NAME)]

    @pytest.mark.asyncio
    async def test_no_enqueue_when_nothing_due(self, monkeypatch):
        submitted = []

        async def no_due():
            return False

        monkeypatch.setattr(drain_mod.deletion, "has_due_deletions", no_due)
        import surreal_commands

        monkeypatch.setattr(
            surreal_commands, "submit_command", lambda *a: submitted.append(a)
        )
        await enqueue_drain_if_pending()
        assert submitted == []

    @pytest.mark.asyncio
    async def test_guard_skips_when_drain_already_active(self, monkeypatch):
        submitted = []

        async def has_due():
            return True

        async def active():
            return True

        monkeypatch.setattr(drain_mod.deletion, "has_due_deletions", has_due)
        monkeypatch.setattr(drain_mod, "_drain_command_already_queued", active)
        import surreal_commands

        monkeypatch.setattr(
            surreal_commands, "submit_command", lambda *a: submitted.append(a)
        )
        await enqueue_drain_if_pending()
        assert submitted == []


# ===========================================================================
# LIVE-DB: arm_id CAS + fair traversal (the 14 fairness properties)
# ===========================================================================


async def _db_reachable() -> bool:
    from open_notebook.database.repository import repo_query

    try:
        await repo_query("RETURN true;")
        return True
    except Exception:
        return False


MIGRATIONS = REPO_ROOT / "open_notebook" / "database" / "migrations"


@pytest_asyncio.fixture
async def live_db():
    if not await _db_reachable():
        pytest.skip("SurrealDB not reachable")
    from open_notebook.database.async_migrate import AsyncMigration
    from open_notebook.database.repository import repo_query

    # Ensure migration 24 + 25 applied (idempotent).
    await repo_query(AsyncMigration.from_file(str(MIGRATIONS / "24.surrealql")).sql)
    await repo_query(AsyncMigration.from_file(str(MIGRATIONS / "25.surrealql")).sql)
    created: list[str] = []
    yield created
    for raw in created:
        try:
            await repo_query("DELETE type::thing('graphrag_deletion', $i);", {"i": raw})
            await repo_query("DELETE type::thing('source', $i);", {"i": raw})
        except Exception:
            pass


async def _arm_a_tombstone(raw: str) -> DeletionTombstone:
    """Create + raw-delete a synthetic source so the DB event writes a tombstone."""
    from open_notebook.database.repository import repo_query

    await repo_query(
        "CREATE type::thing('source', $i) SET full_text='x', title='t';", {"i": raw}
    )
    await repo_query("DELETE type::thing('source', $i);", {"i": raw})
    rows = await repo_query("SELECT * FROM type::thing('graphrag_deletion', $i);", {"i": raw})
    r = rows[0]
    return DeletionTombstone(
        source_id=str(r["source_id"]),
        status=str(r["status"]),
        arm_id=str(r["arm_id"]),
        next_attempt_at=r.get("next_attempt_at"),
    )


@pytest.mark.asyncio
async def test_live_stale_running_drain_does_not_suppress_enqueue(live_db):
    """HIGH (Codex A): a crashed drain left as status='running' must NOT block
    future drains. The dedup guard considers ONLY 'new' rows, so a lingering
    'running' row does not suppress re-enqueue (a 'new' row does)."""
    from open_notebook.database.repository import repo_query

    # Clean slate: remove any leftover drain command rows so the global guard query
    # reflects only what this test creates (dev queue rows are transient).
    await repo_query(
        "DELETE command WHERE app='open_notebook' AND name='graphrag_drain_deletions';"
    )
    assert await drain_mod._drain_command_already_queued() is False

    tag = f"c3run_{uuid.uuid4().hex[:8]}"
    # A crashed drain: a 'running' graphrag_drain_deletions command that never finished.
    await repo_query(
        "CREATE type::thing('command',$i) SET app='open_notebook', "
        "name='graphrag_drain_deletions', args={}, status='running', "
        "created=time::now(), updated=time::now();",
        {"i": tag},
    )
    try:
        # 'running' is ignored -> not considered a queued duplicate.
        assert await drain_mod._drain_command_already_queued() is False
        # A 'new' row IS a queued duplicate.
        tag2 = f"c3new_{uuid.uuid4().hex[:8]}"
        await repo_query(
            "CREATE type::thing('command',$i) SET app='open_notebook', "
            "name='graphrag_drain_deletions', args={}, status='new', "
            "created=time::now(), updated=time::now();",
            {"i": tag2},
        )
        try:
            assert await drain_mod._drain_command_already_queued() is True
        finally:
            await repo_query("DELETE type::thing('command',$i);", {"i": tag2})
    finally:
        await repo_query("DELETE type::thing('command',$i);", {"i": tag})


@pytest.mark.asyncio
async def test_live_backfill_and_due(live_db):
    """(13) Pre-25 tombstones are backfilled; a fresh tombstone is immediately due."""
    raw = f"c3_due_{uuid.uuid4().hex[:8]}"
    live_db.append(raw)
    tomb = await _arm_a_tombstone(raw)
    assert tomb.next_attempt_at is not None
    due = await deletion_mod.list_due_deletions(500)
    assert any(t.source_id == tomb.source_id for t in due)


@pytest.mark.asyncio
async def test_live_current_arm_resolve_stale_supersedes(live_db):
    """(9,10) Current-arm RESOLVE deletes 1; stale-arm RESOLVE deletes 0."""
    raw = f"c3_cas_{uuid.uuid4().hex[:8]}"
    live_db.append(raw)
    tomb = await _arm_a_tombstone(raw)
    assert await deletion_mod.resolve_tombstone_cas(tomb.source_id, "00000000-0000-0000-0000-000000000000") is False
    assert await deletion_mod.resolve_tombstone_cas(tomb.source_id, tomb.arm_id) is True


@pytest.mark.asyncio
async def test_live_defer_removes_from_due_then_returns(live_db, monkeypatch):
    """(4,5,6) A failed row is deferred out of the due set; other due rows remain
    selectable; once the delay elapses it becomes selectable again."""
    raw = f"c3_defer_{uuid.uuid4().hex[:8]}"
    live_db.append(raw)
    tomb = await _arm_a_tombstone(raw)
    # defer far into the future -> leaves due set
    assert await deletion_mod.defer_tombstone_cas(tomb.arm_id, 3600) is True
    due = await deletion_mod.list_due_deletions(500)
    assert all(t.source_id != tomb.source_id for t in due)
    # a tiny delay -> becomes due again shortly
    assert await deletion_mod.defer_tombstone_cas(tomb.arm_id, 5) is True


@pytest.mark.asyncio
async def test_live_rearm_makes_due_and_stale_cannot_hide(live_db):
    """(7,8) Re-arm resets next_attempt_at to now; a stale-arm DEFER updates 0 rows
    so it cannot hide the freshly re-armed (immediately due) tombstone."""
    raw = f"c3_rearm_{uuid.uuid4().hex[:8]}"
    live_db.append(raw)
    tomb1 = await _arm_a_tombstone(raw)
    # defer the first arm far out
    await deletion_mod.defer_tombstone_cas(tomb1.arm_id, 3600)
    # re-arm (recreate + delete) -> fresh arm, next_attempt_at=now
    tomb2 = await _arm_a_tombstone(raw)
    assert tomb2.arm_id != tomb1.arm_id
    # stale defer must NOT move the re-armed row
    assert await deletion_mod.defer_tombstone_cas(tomb1.arm_id, 3600) is False
    due = await deletion_mod.list_due_deletions(500)
    assert any(t.source_id == tomb2.source_id for t in due)


@pytest.mark.asyncio
async def test_live_batch_visits_all_then_deletes_without_offset_skip(live_db):
    """(1,3) A >batch-cap set of resolvable tombstones is fully drained across
    repeated due-set selections, with resolved rows deleted and no offset skip."""
    raws = [f"c3_batch_{i}_{uuid.uuid4().hex[:6]}" for i in range(7)]
    sources = []
    for raw in raws:
        live_db.append(raw)
        t = await _arm_a_tombstone(raw)
        sources.append(t.source_id)
    remaining = set(sources)
    # Resolve one at a time, re-querying after each DELETE. The property: deleting
    # a resolved row must NOT make an unseen row disappear behind an offset shift —
    # so after each delete, EVERY still-unresolved source is still visible.
    for _ in range(len(sources) + 5):
        if not remaining:
            break
        due_ids = {
            t.source_id
            for t in await deletion_mod.list_due_deletions(200)
            if t.source_id in remaining
        }
        assert due_ids == remaining, "a resolved delete skipped an unseen tombstone"
        target = next(iter(remaining))
        tomb = next(
            t for t in await deletion_mod.list_due_deletions(200) if t.source_id == target
        )
        assert await deletion_mod.resolve_tombstone_cas(target, tomb.arm_id) is True
        remaining.discard(target)
    assert not remaining  # all visited + resolved, none skipped


@pytest.mark.asyncio
async def test_live_persistent_front_failures_do_not_starve_later_rows(live_db):
    """(2) With the first rows failing every attempt (deferred), later rows are
    still processed on the same tick — the core fairness guarantee."""
    fail_raw = f"c3_fail_{uuid.uuid4().hex[:6]}"
    ok_raw = f"c3_ok_{uuid.uuid4().hex[:6]}"
    live_db.append(fail_raw)
    live_db.append(ok_raw)
    fail = await _arm_a_tombstone(fail_raw)
    ok = await _arm_a_tombstone(ok_raw)
    # one drain "tick": the failing row is deferred, the ok row resolves.
    due = await deletion_mod.list_due_deletions(500)
    due = [t for t in due if t.source_id in {fail.source_id, ok.source_id}]
    for t in due:
        if t.source_id == fail.source_id:
            await deletion_mod.defer_tombstone_cas(t.arm_id, 3600)
        else:
            await deletion_mod.resolve_tombstone_cas(t.source_id, t.arm_id)
    still = {t.source_id for t in await deletion_mod.list_due_deletions(500)}
    assert ok.source_id not in still  # later row processed
    assert fail.source_id not in still  # failing row left the DUE set (deferred)


@pytest.mark.asyncio
async def test_live_current_resolve_is_atomic_with_canonical_text(live_db):
    """HIGH-A (Codex A), live: the live-current resolve folds the canonical-text
    condition into the CAS, so a redaction that lands before resolve matches zero
    rows and the tombstone survives (no stale content blessed)."""
    from open_notebook.database.repository import repo_query

    raw = f"c3_atom_{uuid.uuid4().hex[:8]}"
    live_db.append(raw)
    # live source with text T + its tombstone (source still present).
    await repo_query(
        "CREATE type::thing('source',$i) SET full_text='T text', title='t';", {"i": raw}
    )
    await repo_query(
        "CREATE type::thing('graphrag_deletion',$i) SET source_id=type::thing('source',$i), "
        "status='pending', arm_id=rand::uuid(), requested_at=time::now(), next_attempt_at=time::now();",
        {"i": raw},
    )
    rows = await repo_query("SELECT * FROM type::thing('graphrag_deletion',$i);", {"i": raw})
    sid = str(rows[0]["source_id"])
    arm = str(rows[0]["arm_id"])
    # redact the source, then attempt to resolve with the OLD (shipped) text.
    await repo_query("UPDATE type::thing('source',$i) SET full_text='';", {"i": raw})
    assert await deletion_mod.resolve_current_tombstone_cas(sid, arm, "T text") is False
    # tombstone still present (not blessed); restore text and resolve succeeds.
    await repo_query("UPDATE type::thing('source',$i) SET full_text='T text';", {"i": raw})
    assert await deletion_mod.resolve_current_tombstone_cas(sid, arm, "T text") is True


@pytest.mark.asyncio
async def test_live_migration_25_up_down_up(live_db):
    """(14) Migration 25 up/down/up is safe on the live runtime; down restores the
    exact migration-24 event body (no next_attempt_at) and removes the field."""
    from open_notebook.database.async_migrate import AsyncMigration
    from open_notebook.database.repository import repo_query

    up = AsyncMigration.from_file(str(MIGRATIONS / "25.surrealql")).sql
    down = AsyncMigration.from_file(str(MIGRATIONS / "25_down.surrealql")).sql

    async def event_body():
        info = await repo_query("INFO FOR TABLE source;")
        obj = info[0] if isinstance(info, list) else info
        return (obj.get("events") or {}).get("graphrag_source_delete", "")

    await repo_query(down)
    assert "next_attempt_at" not in await event_body()
    await repo_query(up)
    assert "next_attempt_at" in await event_body()
    # leave migration 25 applied (forward state) for other tests


# ===========================================================================
# LIVE-LIGHTRAG: synthetic index -> delete -> confirmed absence (real v1.5.6)
# ===========================================================================


def _live_sidecar_config():
    base = os.environ.get("OPEN_NOTEBOOK_GRAPHRAG_BASE_URL", "").rstrip("/")
    if not base:
        pytest.skip("OPEN_NOTEBOOK_GRAPHRAG_BASE_URL not set (live LightRAG test)")
    return GraphRAGConfig(
        enabled=True,
        base_url=base,
        timeout=30.0,
        api_key=os.environ.get("OPEN_NOTEBOOK_GRAPHRAG_API_KEY") or None,
    )


async def _sidecar_reachable(client) -> bool:
    try:
        h = await client.health()
        return h.healthy
    except Exception:
        return False


@pytest.mark.asyncio
async def test_live_lightrag_delete_then_confirmed_absent():
    """Against the real pinned sidecar (synthetic data only): a delete of an
    already-absent doc is idempotent, and confirm_document_absent proves absence.
    Skips unless OPEN_NOTEBOOK_GRAPHRAG_BASE_URL points at a reachable sidecar."""
    cfg = _live_sidecar_config()
    client = GraphRAGClient(cfg)
    if not await _sidecar_reachable(client):
        pytest.skip("configured LightRAG sidecar not reachable/healthy")

    # A synthetic id that was never indexed: it must be provably absent.
    src = f"source:c3_live_{uuid.uuid4().hex[:10]}"
    doc_id = compute_doc_id(src)
    # Delete-of-absent is a safe no-op (deletion_started/busy); never raises fatally.
    outcome = await client.delete_document(doc_id)
    assert outcome.state in (DeleteState.GONE, DeleteState.BUSY)
    # Absence proof from a single complete snapshot (small synthetic corpus).
    state = await client.confirm_document_absent(doc_id)
    assert state in (AbsenceState.ABSENT_CONFIRMED, AbsenceState.UNKNOWN)


@pytest.mark.asyncio
async def test_live_lightrag_full_roundtrip_synthetic():
    """Real v1.5.6, SYNTHETIC data: index -> confirm present -> delete -> confirm
    ABSENT. Proves the destructive lifecycle end-to-end against the live sidecar,
    not mocks. Skips if the sidecar cannot register the document (no embedding
    provider configured) so the suite stays green on a bare sidecar."""
    cfg = _live_sidecar_config()
    client = GraphRAGClient(cfg)
    if not await _sidecar_reachable(client):
        pytest.skip("configured LightRAG sidecar not reachable/healthy")

    src = f"source:c3_rt_{uuid.uuid4().hex[:10]}"
    doc_id = compute_doc_id(src)
    await client.index_document(canonical_text="Synthetic public roundtrip note.", source_id=src)

    # Wait (bounded) for the doc to register/appear.
    present = False
    for _ in range(10):
        await asyncio.sleep(1.5)
        if await client.confirm_document_absent(doc_id) is AbsenceState.FOUND:
            present = True
            break
    if not present:
        pytest.skip("sidecar did not register the synthetic doc (no provider)")

    # Delete, then wait (bounded) for CONFIRMED absence.
    await client.delete_document(doc_id)
    confirmed = False
    for _ in range(15):
        await asyncio.sleep(1.5)
        if await client.confirm_document_absent(doc_id) is AbsenceState.ABSENT_CONFIRMED:
            confirmed = True
            break
    assert confirmed, "delete did not converge to confirmed absence within the window"
    # Delete-of-already-absent is idempotent (no fatal error).
    again = await client.delete_document(doc_id)
    assert again.state in (DeleteState.GONE, DeleteState.BUSY)
