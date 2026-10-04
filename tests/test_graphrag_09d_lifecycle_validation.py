"""GraphRAG-09D production-shaped lifecycle validation (provider-free, test-only).

These are COMPOSED scenarios that exercise the REAL production lifecycle functions
(``drain.drain_pending_deletions`` / ``drain.converge_tombstone`` and
``lifecycle.index_source``) under production-shaped conditions, concentrating on the two
gaps the 09D forensic classified PARTIAL: durable-tombstone RESTART recovery and the
composed DELETE-before-final-insert race. The already-proven 03A->03E unit matrices
(normal index/reindex/supersede, deletion fail-closed, single-snapshot absence, drain
fairness/arm_id CAS, reconcile AUDIT/REPAIR, rebuild PLAN/EXECUTE gates + secondary lock,
record-id identity) are REUSED, not duplicated.

Provider-free by construction: the sidecar is an ``httpx.MockTransport`` fake; the durable
``graphrag_deletion`` table + canonical ``source`` rows are an in-memory store that OUTLIVES
the service/worker objects (so a fresh instance re-drives from durable state, not Python
memory). No real model provider, no real external sidecar, no real SurrealDB, no internal
data, no persistent production-flag change. Races are driven by deterministic MockTransport
callbacks, never by sleeps.
"""

import httpx
import pytest

from open_notebook.integrations.graphrag import drain as drain_mod
from open_notebook.integrations.graphrag.client import GraphRAGClient
from open_notebook.integrations.graphrag.config import GraphRAGConfig
from open_notebook.integrations.graphrag.deletion import DeletionTombstone
from open_notebook.integrations.graphrag.drain import (
    DrainOutcome,
    converge_tombstone,
    drain_pending_deletions,
)
from open_notebook.integrations.graphrag.lifecycle import IndexResult, index_source
from open_notebook.integrations.graphrag.service import GraphRAGService

BASE_URL = "http://graphrag-sidecar.invalid:9621"
_ARM = "11111111-1111-1111-1111-111111111111"


def _config(**overrides) -> GraphRAGConfig:
    defaults = dict(enabled=True, base_url=BASE_URL, timeout=5.0, api_key=None)
    defaults.update(overrides)
    return GraphRAGConfig(**defaults)  # type: ignore[arg-type]


def _service(handler, **config_overrides) -> GraphRAGService:
    """A REAL GraphRAGService whose only fake is the sidecar transport.

    A fresh call builds a brand-new service AND a brand-new client, so re-running a drain
    with a new ``_service(...)`` carries NO in-memory lifecycle state between instances.
    """
    cfg = _config(**config_overrides)
    return GraphRAGService(config=cfg, client=GraphRAGClient(cfg, transport=httpx.MockTransport(handler)))


def _empty_absent_page(request: httpx.Request) -> httpx.Response:
    """A complete single-page listing proving the corpus is empty -> ABSENT_CONFIRMED."""
    return httpx.Response(
        200,
        json={
            "documents": [],
            "pagination": {"total_count": 0, "total_pages": 1, "has_next": False},
        },
    )


class DurableStore:
    """In-memory stand-in for the graphrag_deletion table + canonical source rows.

    It is the 'database': it lives in the test, OUTSIDE any service/worker object, so
    discarding a worker and building a fresh one cannot lose a pending tombstone. Its methods
    implement the exact deletion.* contract the real drain relies on (arm_id CAS, due-set by
    next_attempt_at, current-text predicate). A controllable ``now`` advances deterministically
    (no wall-clock, no sleep).
    """

    def __init__(self) -> None:
        self.tombstones: dict = {}  # source_id -> {"arm_id","status","next_attempt_at"}
        self.sources: dict = {}  # source_id -> {"full_text": str}  (canonical)
        self.now: float = 1000.0

    def arm(self, source_id: str, arm_id: str = _ARM) -> None:
        self.tombstones[source_id] = {"arm_id": arm_id, "status": "pending", "next_attempt_at": None}

    def pending(self, source_id: str) -> bool:
        t = self.tombstones.get(source_id)
        return t is not None and t["status"] == "pending"

    # --- deletion.* shims (patched onto drain_mod.deletion) ------------------------- #
    async def list_due_deletions(self, limit: int):
        due = [
            DeletionTombstone(
                source_id=sid, status="pending", arm_id=t["arm_id"],
                next_attempt_at=t["next_attempt_at"],
            )
            for sid, t in self.tombstones.items()
            if t["status"] == "pending"
            and (t["next_attempt_at"] is None or t["next_attempt_at"] <= self.now)
        ]
        due.sort(key=lambda d: (d.next_attempt_at or 0.0, d.source_id))
        return due[: max(1, int(limit))]

    async def resolve_tombstone_cas(self, source_id: str, arm_id: str) -> bool:
        t = self.tombstones.get(source_id)
        if t and t["arm_id"] == arm_id:
            del self.tombstones[source_id]
            return True
        return False

    async def resolve_current_tombstone_cas(self, source_id: str, arm_id: str, expected_text: str) -> bool:
        t = self.tombstones.get(source_id)
        src = self.sources.get(source_id)
        if t and t["arm_id"] == arm_id and src is not None and src.get("full_text") == expected_text:
            del self.tombstones[source_id]
            return True
        return False

    async def defer_tombstone_cas(self, arm_id: str, delay_seconds: int) -> bool:
        for t in self.tombstones.values():
            if t["arm_id"] == arm_id:
                t["next_attempt_at"] = self.now + delay_seconds
                return True
        return False

    async def repo_query(self, sql: str, vars: dict | None = None):
        # The drain only ever issues `SELECT * FROM $id` against a canonical source.
        rid = str((vars or {}).get("id", ""))
        src = self.sources.get(rid)
        return [dict(src)] if src is not None else []


@pytest.fixture
def durable(monkeypatch):
    """Install a DurableStore behind the real drain's deletion.* + repo_query seams."""
    store = DurableStore()
    monkeypatch.setattr(drain_mod.deletion, "list_due_deletions", store.list_due_deletions)
    monkeypatch.setattr(drain_mod.deletion, "resolve_tombstone_cas", store.resolve_tombstone_cas)
    monkeypatch.setattr(
        drain_mod.deletion, "resolve_current_tombstone_cas", store.resolve_current_tombstone_cas
    )
    monkeypatch.setattr(drain_mod.deletion, "defer_tombstone_cas", store.defer_tombstone_cas)
    monkeypatch.setattr(drain_mod, "repo_query", store.repo_query)
    return store


# =============================================================================
# Restart recovery: a pending deletion survives a simulated process restart
# =============================================================================


@pytest.mark.asyncio
async def test_transient_delete_survives_restart_then_resolves(durable):
    """RESTART_SIMULATION_METHOD = FRESH_SERVICE_SAME_DURABLE_DB.

    Worker #1 hits an unreachable sidecar -> the real drain DEFERS the tombstone (it stays
    durably pending). The worker/service is then DISCARDED and a brand-new one is built
    against the SAME DurableStore with a healthy sidecar; the re-drive discovers and resolves
    the pre-existing tombstone with NO reliance on the old worker's memory.
    """
    durable.arm("source:s1")  # a canonical source was deleted -> tombstone armed
    # canonical source is ABSENT (deleted); durable.sources has no "source:s1"
    assert durable.pending("source:s1")  # TOMBSTONE_PENDING_BEFORE_RESTART

    # --- Worker #1: sidecar unreachable -> defer, nothing resolved -------------------
    def unreachable(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("sidecar down", request=request)

    worker1 = _service(unreachable)
    summary1 = await drain_pending_deletions(worker1)
    assert summary1.deferred == 1 and summary1.resolved_absent == 0
    assert durable.pending("source:s1")  # still pending after a failed attempt

    # --- Simulate restart: discard worker #1, advance time past the defer window -----
    del worker1
    durable.now += 10_000  # the deferred tombstone becomes due again
    assert durable.pending("source:s1")  # TOMBSTONE_PRESENT_AFTER_RESTART (durable state)

    # --- Worker #2: a completely fresh service + client, healthy sidecar -------------
    worker2 = _service(_empty_absent_page)
    summary2 = await drain_pending_deletions(worker2)
    assert summary2.resolved_absent == 1
    assert not durable.pending("source:s1")  # TOMBSTONE_RESOLVED_AFTER_REDRIVE

    # --- Idempotent re-drive: a third fresh worker finds nothing due -----------------
    worker3 = _service(_empty_absent_page)
    summary3 = await drain_pending_deletions(worker3)
    assert summary3.scanned == 0  # DRAIN_REDRIVE_IDEMPOTENT: resolved row is gone, no re-work


@pytest.mark.asyncio
async def test_flag_off_maintenance_converges_deleted_source(durable):
    """FLAG_OFF maintenance convergence: with indexing DISABLED but base_url present, a
    deleted source's tombstone still converges to ABSENT (no Boundary-B egress)."""
    durable.arm("source:s2")  # deleted source
    worker = _service(_empty_absent_page, enabled=False)  # flag OFF
    summary = await drain_pending_deletions(worker)
    assert summary.resolved_absent == 1
    assert not durable.pending("source:s2")


@pytest.mark.asyncio
async def test_flag_off_live_nonempty_source_does_not_reindex_egress(durable):
    """FLAG_OFF + live non-empty source: converge takes the ABSENT branch, NOT a reindex, so
    no POST /documents/text egress happens while the feature is disabled."""
    durable.arm("source:s3")
    durable.sources["source:s3"] = {"full_text": "live content"}  # live + non-empty

    def no_index_egress(request: httpx.Request) -> httpx.Response:
        assert not request.url.path.endswith(
            "/documents/text"
        ), "disabled branch must not reindex (no Boundary-B egress)"
        return _empty_absent_page(request)

    worker = _service(no_index_egress, enabled=False)
    summary = await drain_pending_deletions(worker)
    # live+non-empty+disabled converges to ABSENT (confirm-absent resolves), never reindexes.
    assert summary.resolved_absent == 1
    assert not durable.pending("source:s3")


# =============================================================================
# Composed DELETE-before-final-insert race (deterministic, no sleep)
# =============================================================================


@pytest.mark.asyncio
async def test_delete_before_final_insert_no_resurrection(durable):
    """The canonical source is deleted DURING the reindex delete round-trip; the second
    confirm_current must detect it and SUPERSEDE, so stale text is never inserted."""
    state = {"present": True}

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/documents/delete_document"):
            # The canonical delete lands while we are mid-delete round-trip.
            state["present"] = False
            return httpx.Response(200, json={"status": "deletion_started"})
        if path.endswith("/documents/text"):
            raise AssertionError("stale text must NOT be inserted after source deletion")
        return httpx.Response(200, json={"status": "ok"})

    service = _service(handler)
    calls = {"n": 0}

    async def confirm_current() -> bool:
        calls["n"] += 1
        return state["present"]

    outcome = await index_source(
        service, source_id="source:s4", canonical_text="stale text", confirm_current=confirm_current
    )
    assert outcome.result is IndexResult.SUPERSEDED
    assert calls["n"] == 2  # confirmed before delete AND again before (skipped) insert


@pytest.mark.asyncio
async def test_modified_during_reindex_superseded(durable):
    """The canonical TEXT changes during the delete round-trip; the second confirm_current
    rejects the now-stale generation and the old content is not reinserted."""
    current = {"text": "v1"}
    shipped = "v1"

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/documents/delete_document"):
            current["text"] = "v2"  # a newer edit lands mid-reindex
            return httpx.Response(200, json={"status": "deletion_started"})
        if path.endswith("/documents/text"):
            raise AssertionError("stale v1 content must NOT be reinserted after a v2 edit")
        return httpx.Response(200, json={"status": "ok"})

    service = _service(handler)

    async def confirm_current() -> bool:
        return current["text"] == shipped

    outcome = await index_source(
        service, source_id="source:s5", canonical_text=shipped, confirm_current=confirm_current
    )
    assert outcome.result is IndexResult.SUPERSEDED


# =============================================================================
# converge_tombstone direct: delete/reindex overlap never falsely completes
# =============================================================================


@pytest.mark.asyncio
async def test_unconfirmed_absence_defers_never_false_complete(durable):
    """A sidecar that cannot prove absence (multi-page/unknown) must DEFER, never resolve:
    an overlapping delete/reindex can never be reported as falsely converged."""
    durable.arm("source:s6")  # deleted source

    def multipage(request: httpx.Request) -> httpx.Response:
        # total_pages > 1 => absence is UNKNOWN by construction; delete is attempted then
        # we still cannot confirm -> DEFERRED.
        if request.url.path.endswith("/documents/paginated"):
            return httpx.Response(
                200,
                json={
                    "documents": [{"id": "doc-other", "file_path": "source:other", "status": "processed"}],
                    "pagination": {"total_count": 500, "total_pages": 3, "has_next": True},
                },
            )
        if request.url.path.endswith("/documents/delete_document"):
            return httpx.Response(200, json={"status": "deletion_started"})
        return httpx.Response(200, json={"status": "ok"})

    tomb = DeletionTombstone(source_id="source:s6", status="pending", arm_id=_ARM, next_attempt_at=None)
    outcome = await converge_tombstone(_service(multipage), tomb)
    assert outcome is DrainOutcome.DEFERRED
    assert durable.pending("source:s6")  # never resolved on an unconfirmed (deletion_started) ack
