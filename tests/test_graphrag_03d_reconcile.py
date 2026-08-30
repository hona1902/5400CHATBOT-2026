"""GraphRAG-03D reconcile tests (property-oriented).

Each test names the reconciliation/security property that would break if the
implementation were subtly wrong. Three layers, mirroring 03C:

  * MOCK/STRUCTURAL (always run): ownership classification, the streaming remote
    sweep + classification/repair branches, the authoritative missing-detection
    snapshot gate, AUDIT-vs-REPAIR, inventory completeness/incompleteness, and the
    client detailed-listing parse — all with httpx.MockTransport and stubbed DB /
    lifecycle helpers, no live services.
  * LIVE-DB (skipped if SurrealDB unreachable): the orphan-arming helper
    (DB-generated arm_id, no re-arm churn, deterministic identity, event
    interoperability, numeric/string-numeric distinctness).
  * LIVE-LIGHTRAG (skipped if the pinned sidecar is unreachable): the real
    /documents/paginated detailed contract + ownership of a really-inserted
    synthetic document against v1.5.6.

Ownership rule under test: a remote document is Open-Notebook-owned ONLY when its
file_path is a lossless canonical `source` id AND compute_doc_id(file_path) equals
the doc id. Everything else is FOREIGN / UNKNOWN_OWNERSHIP and is never a
destructive target. 03D never deletes remotely and never resolves a tombstone.
"""

import os
import uuid
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
import pytest_asyncio

from open_notebook.integrations.graphrag import deletion as deletion_mod
from open_notebook.integrations.graphrag import reconcile as reconcile_mod
from open_notebook.integrations.graphrag.client import (
    ABSENCE_PROBE_PAGE_SIZE,
    GraphRAGClient,
    compute_doc_id,
)
from open_notebook.integrations.graphrag.config import (
    GraphRAGConfig,
    GraphRAGReconcileConfig,
)
from open_notebook.integrations.graphrag.models import (
    GraphRAGProtocolError,
    RemoteDocument,
    RemoteDocumentsPage,
)
from open_notebook.integrations.graphrag.reconcile import (
    OwnershipClass,
    classify_ownership,
    reconcile,
)


def _cfg(**overrides) -> GraphRAGReconcileConfig:
    defaults = dict(
        remote_page_size=200, canonical_batch_size=100, max_records=2000, max_sample_ids=20
    )
    defaults.update(overrides)
    return GraphRAGReconcileConfig(**defaults)  # type: ignore[arg-type]


def _owned_doc(source_id: str, status: str = "processed") -> RemoteDocument:
    """A remote doc that WE would have created: file_path == source_id and
    doc_id == compute_doc_id(source_id)."""
    return RemoteDocument(
        doc_id=compute_doc_id(source_id), file_path=source_id, status=status
    )


def _page(docs, *, total_count=None, total_pages=1, has_next=False, page=1, page_size=200):
    return RemoteDocumentsPage(
        documents=tuple(docs),
        page=page,
        page_size=page_size,
        total_count=len(docs) if total_count is None else total_count,
        total_pages=total_pages,
        has_next=has_next,
    )


class _FakeService:
    """Stub GraphRAGService: serves prebuilt remote pages by 1-based page index,
    no HTTP. The missing-repair path re-lists a FRESH snapshot (page 1), so a fake
    that returns the same page for every page-1 call models a stable corpus; tests
    that need a raced-in change subclass this to vary by call count."""

    def __init__(self, pages, *, base_url="http://sidecar.invalid:9621", enabled=True):
        self.config = SimpleNamespace(base_url=base_url, enabled=enabled)
        self._pages = list(pages)
        self.list_calls = []

    async def list_remote_documents_detailed(self, *, page, page_size):
        self.list_calls.append(page)
        idx = page - 1
        if 0 <= idx < len(self._pages):
            return self._pages[idx]
        return _page([], total_count=0, total_pages=1, has_next=False, page=page)


def _make_repo_query(canonical: dict, source_ids=None):
    """Fake reconcile.repo_query.

    ``canonical`` maps a canonical source_id string -> its full_text (a source that
    EXISTS). A source_id absent from the dict is treated as canonically ABSENT.
    ``source_ids`` is the ordered id list returned by the canonical keyset
    enumeration (defaults to the existing sources)."""
    ids = sorted(source_ids if source_ids is not None else canonical.keys())

    async def _q(query, params=None):
        params = params or {}
        if query.startswith("SELECT full_text FROM $id"):
            sid = str(params["id"])
            if sid not in canonical:
                return []
            return [{"full_text": canonical[sid]}]
        if "SELECT VALUE id FROM source" in query:
            n = params.get("n", len(ids))
            if "id > $last" in query:
                last = str(params["last"])
                remaining = [s for s in ids if s > last]
            else:
                remaining = ids
            return remaining[:n]
        return []

    return _q


class _ArmRecorder:
    """Records arm_orphan_deletion calls; ``pending`` marks ids already pending."""

    def __init__(self, pending=()):
        self.calls = []
        self._pending = set(pending)

    async def __call__(self, source_id: str) -> bool:
        self.calls.append(source_id)
        if source_id in self._pending:
            return False  # already pending -> no re-arm
        self._pending.add(source_id)
        return True


@pytest.fixture
def no_side_effects(monkeypatch):
    """Fail loudly if reconcile tries to resolve/defer a tombstone or delete
    remotely — 03D must do NONE of those."""
    async def _boom(*a, **k):  # pragma: no cover - only fires on a regression
        raise AssertionError("03D must not resolve/defer/delete")

    monkeypatch.setattr(deletion_mod, "resolve_tombstone_cas", _boom)
    monkeypatch.setattr(deletion_mod, "resolve_current_tombstone_cas", _boom)
    monkeypatch.setattr(deletion_mod, "defer_tombstone_cas", _boom)


# ===========================================================================
# A. OWNERSHIP CLASSIFICATION (pure function)
# ===========================================================================


class TestOwnership:
    def test_owned_when_file_path_and_doc_id_match(self):
        doc = _owned_doc("source:abc123")
        kind, sid = classify_ownership(doc)
        assert kind is OwnershipClass.OWNED
        assert sid == "source:abc123"

    def test_foreign_when_no_file_path(self):
        doc = RemoteDocument(doc_id="doc-anything", file_path=None)
        assert classify_ownership(doc) == (OwnershipClass.FOREIGN, None)

    def test_foreign_when_file_path_not_a_source_id(self):
        # A path/URL-shaped file_path is NOT a canonical source id -> never ours.
        for fp in ("/etc/passwd", "https://internal/doc?token=x", "note:123", "../secret"):
            doc = RemoteDocument(doc_id="doc-x", file_path=fp)
            assert classify_ownership(doc) == (OwnershipClass.FOREIGN, None), fp

    def test_doc_prefixed_foreign_never_owned(self):
        # A doc whose id merely starts with "doc-" is NOT owned on that basis.
        doc = RemoteDocument(doc_id="doc-deadbeef", file_path="source:realone")
        kind, sid = classify_ownership(doc)
        assert kind is OwnershipClass.UNKNOWN_OWNERSHIP  # valid src id, id mismatch
        assert sid is None

    def test_mismatched_doc_id_is_unknown_ownership(self):
        doc = RemoteDocument(doc_id="doc-" + "0" * 32, file_path="source:abc")
        assert classify_ownership(doc) == (OwnershipClass.UNKNOWN_OWNERSHIP, None)

    def test_numeric_and_string_numeric_stay_distinct(self):
        numeric = _owned_doc("source:123")
        string_numeric = _owned_doc("source:⟨123⟩")
        assert numeric.doc_id != string_numeric.doc_id
        assert classify_ownership(numeric)[1] == "source:123"
        assert classify_ownership(string_numeric)[1] == "source:⟨123⟩"

    def test_empty_doc_id_is_foreign(self):
        doc = RemoteDocument(doc_id="", file_path="source:abc")
        assert classify_ownership(doc) == (OwnershipClass.FOREIGN, None)


# ===========================================================================
# B. REMOTE SWEEP: classification + repair branches
# ===========================================================================


@pytest.mark.asyncio
async def test_owned_orphan_detected_and_armed_in_repair(monkeypatch, no_side_effects):
    """Owned doc + canonical source ABSENT -> orphan -> REPAIR arms a durable
    deletion intent (never a direct delete)."""
    svc = _FakeService([_page([_owned_doc("source:gone")])], enabled=True)
    monkeypatch.setattr(reconcile_mod, "repo_query", _make_repo_query({}))  # source absent
    arm = _ArmRecorder()
    monkeypatch.setattr(reconcile_mod.deletion, "arm_orphan_deletion", arm)
    woke = []
    async def _wake():
        woke.append(True)
    monkeypatch.setattr(reconcile_mod, "enqueue_drain_if_pending", _wake)

    summary = await reconcile(svc, repair=True, reconcile_config=_cfg())

    assert summary.owned_orphan == 1
    assert summary.deletion_intents_armed == 1
    assert arm.calls == ["source:gone"]
    assert woke == [True]  # armed -> wakes 03C drain
    assert "source:gone" in summary.samples.get("owned_orphan", [])


@pytest.mark.asyncio
async def test_audit_detects_but_never_arms(monkeypatch, no_side_effects):
    """AUDIT (default) mutates NOTHING."""
    svc = _FakeService([_page([_owned_doc("source:gone")])], enabled=True)
    monkeypatch.setattr(reconcile_mod, "repo_query", _make_repo_query({}))

    async def _no_arm(*a, **k):  # pragma: no cover
        raise AssertionError("AUDIT must not arm")

    monkeypatch.setattr(reconcile_mod.deletion, "arm_orphan_deletion", _no_arm)

    summary = await reconcile(svc, repair=False, reconcile_config=_cfg())
    assert summary.mode == "audit"
    assert summary.owned_orphan == 1
    assert summary.deletion_intents_armed == 0


@pytest.mark.asyncio
async def test_existing_pending_tombstone_not_re_armed(monkeypatch, no_side_effects):
    """An orphan whose tombstone is already pending is NOT re-armed (no churn)."""
    svc = _FakeService([_page([_owned_doc("source:gone")])], enabled=True)
    monkeypatch.setattr(reconcile_mod, "repo_query", _make_repo_query({}))
    arm = _ArmRecorder(pending={"source:gone"})
    monkeypatch.setattr(reconcile_mod.deletion, "arm_orphan_deletion", arm)
    monkeypatch.setattr(reconcile_mod, "enqueue_drain_if_pending", lambda: _noop())

    summary = await reconcile(svc, repair=True, reconcile_config=_cfg())
    assert summary.deletion_intents_armed == 0
    assert summary.deletion_intents_already_pending == 1


async def _noop():
    return None


@pytest.mark.asyncio
async def test_live_empty_owned_is_should_be_absent_and_armed(monkeypatch, no_side_effects):
    """Owned doc + canonical live but EMPTY text -> desired state ABSENT -> armed."""
    svc = _FakeService([_page([_owned_doc("source:empty")])], enabled=True)
    monkeypatch.setattr(
        reconcile_mod, "repo_query", _make_repo_query({"source:empty": "   \n  "})
    )
    arm = _ArmRecorder()
    monkeypatch.setattr(reconcile_mod.deletion, "arm_orphan_deletion", arm)
    monkeypatch.setattr(reconcile_mod, "enqueue_drain_if_pending", lambda: _noop())

    summary = await reconcile(svc, repair=True, reconcile_config=_cfg())
    assert summary.owned_should_be_absent == 1
    assert arm.calls == ["source:empty"]


@pytest.mark.asyncio
async def test_flag_off_owned_live_nonempty_is_should_be_absent(monkeypatch, no_side_effects):
    """Flag OFF + owned live non-empty -> desired ABSENT -> armed (no text egress)."""
    svc = _FakeService([_page([_owned_doc("source:live")])], enabled=False)
    monkeypatch.setattr(
        reconcile_mod, "repo_query", _make_repo_query({"source:live": "real text"})
    )
    arm = _ArmRecorder()
    monkeypatch.setattr(reconcile_mod.deletion, "arm_orphan_deletion", arm)
    monkeypatch.setattr(reconcile_mod, "enqueue_drain_if_pending", lambda: _noop())

    def _no_submit(*a, **k):  # pragma: no cover - flag off must never index
        raise AssertionError("flag OFF must not enqueue indexing")

    import surreal_commands

    monkeypatch.setattr(surreal_commands, "submit_command", _no_submit)

    summary = await reconcile(svc, repair=True, reconcile_config=_cfg())
    assert summary.owned_should_be_absent == 1
    assert arm.calls == ["source:live"]
    assert summary.index_repairs_enqueued == 0  # missing sweep skipped when flag off


@pytest.mark.asyncio
async def test_present_unverified_is_no_action(monkeypatch, no_side_effects):
    """Owned + live non-empty + flag ON -> PRESENT_UNVERIFIED -> no repair (03E)."""
    svc = _FakeService(
        [_page([_owned_doc("source:present")])], enabled=True
    )
    monkeypatch.setattr(
        reconcile_mod, "repo_query", _make_repo_query({"source:present": "text"})
    )

    async def _no_arm(*a, **k):  # pragma: no cover
        raise AssertionError("present-unverified must not arm")

    monkeypatch.setattr(reconcile_mod.deletion, "arm_orphan_deletion", _no_arm)
    # canonical sweep would also run (flag ON): the present doc must be counted
    # present, not missing. Snapshot page 1 == the present doc.
    summary = await reconcile(svc, repair=True, reconcile_config=_cfg())
    assert summary.owned_present_unverified == 1
    assert summary.owned_orphan == 0
    assert summary.deletion_intents_armed == 0


@pytest.mark.asyncio
async def test_foreign_and_unknown_reported_never_touched(monkeypatch, no_side_effects):
    """Foreign / unknown-ownership docs are counted and sampled, never armed."""
    docs = [
        RemoteDocument(doc_id="doc-foreign", file_path="/var/data/x.txt"),  # foreign
        RemoteDocument(doc_id="doc-noprov", file_path=None),  # foreign
        RemoteDocument(doc_id="doc-" + "0" * 32, file_path="source:realmismatch"),  # unknown
    ]
    svc = _FakeService([_page(docs)], enabled=True)
    monkeypatch.setattr(reconcile_mod, "repo_query", _make_repo_query({}))

    async def _no_arm(*a, **k):  # pragma: no cover
        raise AssertionError("foreign/unknown must not arm")

    monkeypatch.setattr(reconcile_mod.deletion, "arm_orphan_deletion", _no_arm)

    summary = await reconcile(svc, repair=True, reconcile_config=_cfg())
    assert summary.foreign == 2
    assert summary.unknown_ownership == 1
    assert summary.deletion_intents_armed == 0


# ===========================================================================
# C. INVENTORY COMPLETENESS / MULTI-PAGE / BOUNDS
# ===========================================================================


@pytest.mark.asyncio
async def test_multi_page_sweep_visits_all_pages(monkeypatch, no_side_effects):
    """A 2-page remote corpus is fully swept for orphans (positive detection)."""
    p1 = _page([_owned_doc("source:a")], total_count=2, total_pages=2, has_next=True, page=1)
    p2 = _page([_owned_doc("source:b")], total_count=2, total_pages=2, has_next=False, page=2)
    svc = _FakeService([p1, p2], enabled=False)  # flag off -> skip canonical sweep
    monkeypatch.setattr(reconcile_mod, "repo_query", _make_repo_query({}))
    arm = _ArmRecorder()
    monkeypatch.setattr(reconcile_mod.deletion, "arm_orphan_deletion", arm)
    monkeypatch.setattr(reconcile_mod, "enqueue_drain_if_pending", lambda: _noop())

    summary = await reconcile(svc, repair=True, reconcile_config=_cfg())
    assert summary.remote_scanned == 2
    assert sorted(arm.calls) == ["source:a", "source:b"]
    assert svc.list_calls == [1, 2]  # both pages fetched


@pytest.mark.asyncio
async def test_max_records_cap_marks_incomplete(monkeypatch, no_side_effects):
    """Hitting max_records stops the sweep and marks the run INCOMPLETE (never
    read as 'no drift')."""
    docs = [_owned_doc(f"source:{i}") for i in range(5)]
    svc = _FakeService([_page(docs)], enabled=False)
    monkeypatch.setattr(reconcile_mod, "repo_query", _make_repo_query({}))
    monkeypatch.setattr(reconcile_mod.deletion, "arm_orphan_deletion", _ArmRecorder())

    summary = await reconcile(svc, repair=True, reconcile_config=_cfg(max_records=2))
    assert summary.remote_scanned == 2
    assert summary.incomplete_inventory is True


@pytest.mark.asyncio
async def test_remote_listing_error_is_incomplete_not_healthy(monkeypatch, no_side_effects):
    from open_notebook.integrations.graphrag.models import GraphRAGUnavailableError

    class _BoomService(_FakeService):
        async def list_remote_documents_detailed(self, *, page, page_size):
            raise GraphRAGUnavailableError("sidecar down")

    svc = _BoomService([], enabled=False)
    summary = await reconcile(svc, repair=True, reconcile_config=_cfg())
    assert summary.incomplete_inventory is True
    assert summary.errors >= 1
    assert summary.owned_orphan == 0  # nothing claimed


@pytest.mark.asyncio
async def test_base_url_unset_is_incomplete_not_no_drift(no_side_effects):
    svc = _FakeService([], base_url="", enabled=True)
    summary = await reconcile(svc, repair=True, reconcile_config=_cfg())
    assert summary.incomplete_inventory is True
    assert svc.list_calls == []  # never even tried to enumerate


# ===========================================================================
# D. MISSING DETECTION (authoritative single-response only)
# ===========================================================================


@pytest.mark.asyncio
async def test_missing_confirmed_single_snapshot_repairs_source_id_only(
    monkeypatch, no_side_effects
):
    """Flag ON + complete single-page snapshot + live non-empty source whose
    doc_id is absent -> missing_confirmed -> REPAIR enqueues source_id ONLY."""
    present = _owned_doc("source:present")
    snapshot = _page([present], total_count=1, total_pages=1)
    svc = _FakeService([snapshot], enabled=True)
    canonical = {"source:present": "t1", "source:missing": "t2"}
    monkeypatch.setattr(reconcile_mod, "repo_query", _make_repo_query(canonical))
    monkeypatch.setattr(reconcile_mod.deletion, "arm_orphan_deletion", _ArmRecorder())

    submitted = []
    import surreal_commands

    def _submit(app, name, payload):
        submitted.append((app, name, payload))
        return "cmd:1"

    monkeypatch.setattr(surreal_commands, "submit_command", _submit)

    summary = await reconcile(svc, repair=True, reconcile_config=_cfg())
    assert summary.missing_confirmed == 1
    assert summary.present_confirmed == 1
    assert summary.index_repairs_enqueued == 1
    assert submitted == [("open_notebook", "graphrag_index_source", {"source_id": "source:missing"})]
    # source_id ONLY — no full_text egress in the reconcile payload.
    assert "full_text" not in submitted[0][2]


@pytest.mark.asyncio
async def test_missing_above_ceiling_is_unknown_never_reindex(monkeypatch, no_side_effects):
    """A multi-page corpus cannot prove absence -> NO missing classification, NO
    reindex (never REBUILD creep)."""
    # Phase A: page1 (has_next) then page2. Phase B snapshot: page1 says total_pages=2.
    p1 = _page([_owned_doc("source:a")], total_count=2, total_pages=2, has_next=True, page=1)
    p2 = _page([_owned_doc("source:b")], total_count=2, total_pages=2, has_next=False, page=2)
    # Phase B calls page=1 again -> returns p1 (total_pages=2 -> incomplete).
    svc = _FakeService([p1, p2], enabled=True)
    canonical = {"source:a": "t", "source:b": "t", "source:c": "t"}
    monkeypatch.setattr(reconcile_mod, "repo_query", _make_repo_query(canonical))
    monkeypatch.setattr(reconcile_mod.deletion, "arm_orphan_deletion", _ArmRecorder())

    def _no_submit(*a, **k):  # pragma: no cover
        raise AssertionError("must not reindex on uncertain absence")

    import surreal_commands

    monkeypatch.setattr(surreal_commands, "submit_command", _no_submit)

    summary = await reconcile(svc, repair=True, reconcile_config=_cfg())
    assert summary.missing_confirmed == 0
    assert summary.missing_inventory_incomplete is True
    assert summary.incomplete_inventory is True
    assert summary.index_repairs_enqueued == 0


@pytest.mark.asyncio
async def test_audit_missing_does_not_enqueue(monkeypatch, no_side_effects):
    present = _owned_doc("source:present")
    svc = _FakeService([_page([present], total_count=1, total_pages=1)], enabled=True)
    monkeypatch.setattr(
        reconcile_mod,
        "repo_query",
        _make_repo_query({"source:present": "t", "source:missing": "t"}),
    )

    def _no_submit(*a, **k):  # pragma: no cover
        raise AssertionError("AUDIT must not enqueue")

    import surreal_commands

    monkeypatch.setattr(surreal_commands, "submit_command", _no_submit)

    summary = await reconcile(svc, repair=False, reconcile_config=_cfg())
    assert summary.missing_confirmed == 1  # detected
    assert summary.index_repairs_enqueued == 0  # but not repaired


class _RaceMissingService(_FakeService):
    """Serves ``before`` for the Phase-A sweep + Phase-B gate (calls 1-2) and
    ``after`` for the repair-time fresh snapshot (calls 3+), to model a corpus that
    changed between the Phase-B snapshot and the per-candidate repair decision."""

    def __init__(self, before, after):
        super().__init__([before], enabled=True)
        self._before = before
        self._after = after
        self._n = 0

    async def list_remote_documents_detailed(self, *, page, page_size):
        self._n += 1
        self.list_calls.append(page)
        return self._after if self._n >= 3 else self._before


@pytest.mark.asyncio
async def test_missing_repair_reconfirms_and_skips_raced_in_doc(monkeypatch, no_side_effects):
    """Codex-B #1: the Phase-B snapshot can go stale before the repair decision. The
    repair re-lists a FRESH complete snapshot; a candidate that raced in (now
    present) must NOT be reindexed."""
    present = _owned_doc("source:present")
    missing = _owned_doc("source:missing")
    before = _page([present], total_count=1, total_pages=1)  # source:missing absent
    after = _page([present, missing], total_count=2, total_pages=1)  # raced in
    svc = _RaceMissingService(before, after)
    monkeypatch.setattr(
        reconcile_mod,
        "repo_query",
        _make_repo_query({"source:present": "t", "source:missing": "t"}),
    )
    monkeypatch.setattr(reconcile_mod.deletion, "arm_orphan_deletion", _ArmRecorder())

    def _no_submit(*a, **k):  # pragma: no cover
        raise AssertionError("a raced-in (now-present) doc must not be reindexed")

    import surreal_commands

    monkeypatch.setattr(surreal_commands, "submit_command", _no_submit)

    summary = await reconcile(svc, repair=True, reconcile_config=_cfg())
    assert summary.missing_confirmed == 1  # flagged by the (now-stale) Phase-B snapshot
    assert summary.index_repairs_enqueued == 0  # ...but fresh re-confirm saw it present


@pytest.mark.asyncio
async def test_missing_repair_incomplete_fresh_snapshot_no_reindex(
    monkeypatch, no_side_effects
):
    """If the FRESH repair-time snapshot is not authoritatively complete (e.g. it now
    advertises another page), the repair must NOT reindex and must mark incomplete."""
    present = _owned_doc("source:present")
    before = _page([present], total_count=1, total_pages=1)
    after = RemoteDocumentsPage(
        documents=(present,),
        page=1,
        page_size=200,
        total_count=1,
        total_pages=1,
        has_next=True,  # no longer an authoritative complete snapshot
    )
    svc = _RaceMissingService(before, after)
    monkeypatch.setattr(
        reconcile_mod,
        "repo_query",
        _make_repo_query({"source:present": "t", "source:missing": "t"}),
    )
    monkeypatch.setattr(reconcile_mod.deletion, "arm_orphan_deletion", _ArmRecorder())

    def _no_submit(*a, **k):  # pragma: no cover
        raise AssertionError("uncertain fresh snapshot must not reindex")

    import surreal_commands

    monkeypatch.setattr(surreal_commands, "submit_command", _no_submit)

    summary = await reconcile(svc, repair=True, reconcile_config=_cfg())
    assert summary.index_repairs_enqueued == 0
    assert summary.incomplete_inventory is True


@pytest.mark.asyncio
async def test_phase_b_canonical_state_error_marks_incomplete(monkeypatch, no_side_effects):
    """Codex-B #2: a canonical-state read error in the missing sweep must set
    incomplete_inventory (never silently produce a clean/no-drift result)."""
    present = _owned_doc("source:present")
    svc = _FakeService([_page([present], total_count=1, total_pages=1)], enabled=True)

    async def _q(query, params=None):
        params = params or {}
        if query.startswith("SELECT full_text FROM $id"):
            raise RuntimeError("transient DB error")
        if "SELECT VALUE id FROM source" in query:
            return ["source:present"] if "id > $last" not in query else []
        return []

    monkeypatch.setattr(reconcile_mod, "repo_query", _q)
    monkeypatch.setattr(reconcile_mod.deletion, "arm_orphan_deletion", _ArmRecorder())

    summary = await reconcile(svc, repair=True, reconcile_config=_cfg())
    assert summary.errors >= 1
    assert summary.incomplete_inventory is True


@pytest.mark.asyncio
async def test_malformed_remote_page_marks_incomplete(monkeypatch, no_side_effects):
    """Codex-B #3: a page whose non-dict rows were dropped (malformed) must mark the
    run incomplete rather than be read as a complete sweep."""
    page = RemoteDocumentsPage(
        documents=(_owned_doc("source:a"),),
        page=1,
        page_size=200,
        total_count=2,  # server said 2, we only read 1
        total_pages=1,
        has_next=False,
        malformed=True,
    )
    svc = _FakeService([page], enabled=False)
    monkeypatch.setattr(reconcile_mod, "repo_query", _make_repo_query({}))
    monkeypatch.setattr(reconcile_mod.deletion, "arm_orphan_deletion", _ArmRecorder())
    monkeypatch.setattr(reconcile_mod, "enqueue_drain_if_pending", lambda: _noop())

    summary = await reconcile(svc, repair=True, reconcile_config=_cfg())
    assert summary.incomplete_inventory is True


@pytest.mark.asyncio
async def test_unreadable_row_empty_doc_id_marks_incomplete(monkeypatch, no_side_effects):
    """A dict row with no usable id is unreadable — marked incomplete, not counted
    as a foreign document."""
    docs = [RemoteDocument(doc_id="", file_path="source:x")]
    svc = _FakeService([_page(docs, total_count=1, total_pages=1)], enabled=False)
    monkeypatch.setattr(reconcile_mod, "repo_query", _make_repo_query({}))
    monkeypatch.setattr(reconcile_mod.deletion, "arm_orphan_deletion", _ArmRecorder())

    summary = await reconcile(svc, repair=True, reconcile_config=_cfg())
    assert summary.incomplete_inventory is True
    assert summary.foreign == 0  # not miscounted as foreign


@pytest.mark.asyncio
async def test_empty_page_loop_is_bounded(monkeypatch, no_side_effects):
    """Codex-B #4: a backend returning empty pages with has_next=True forever must
    NOT loop unbounded — the page bound stops it and marks incomplete."""

    class _EmptyPagerService(_FakeService):
        def __init__(self):
            super().__init__([], enabled=False)

        async def list_remote_documents_detailed(self, *, page, page_size):
            self.list_calls.append(page)
            # Always empty, always claims more pages -> would loop forever unbounded.
            return _page(
                [], total_count=1000, total_pages=10_000, has_next=True, page=page
            )

    svc = _EmptyPagerService()
    monkeypatch.setattr(reconcile_mod, "repo_query", _make_repo_query({}))

    summary = await reconcile(svc, repair=True, reconcile_config=_cfg(remote_page_size=10, max_records=50))
    assert summary.incomplete_inventory is True
    # Page bound = max_records//page_size + 1 = 6 -> a small, finite number of calls.
    assert len(svc.list_calls) <= 8


@pytest.mark.asyncio
async def test_inconsistent_has_next_still_sweeps_all_pages(monkeypatch, no_side_effects):
    """Codex-C P2: has_next=False while total_pages>1 must NOT stop the sweep after
    page 1 (which would miss later-page orphans and falsely look complete). The sweep
    fails closed on total_pages and visits every page."""
    p1 = _page(
        [_owned_doc("source:a")], total_count=2, total_pages=2, has_next=False, page=1
    )
    p2 = _page(
        [_owned_doc("source:b")], total_count=2, total_pages=2, has_next=False, page=2
    )
    svc = _FakeService([p1, p2], enabled=False)  # flag off -> only the remote sweep
    monkeypatch.setattr(reconcile_mod, "repo_query", _make_repo_query({}))
    arm = _ArmRecorder()
    monkeypatch.setattr(reconcile_mod.deletion, "arm_orphan_deletion", arm)
    monkeypatch.setattr(reconcile_mod, "enqueue_drain_if_pending", lambda: _noop())

    summary = await reconcile(svc, repair=True, reconcile_config=_cfg())
    assert svc.list_calls == [1, 2]  # page 2 fetched despite has_next=False on page 1
    assert sorted(arm.calls) == ["source:a", "source:b"]  # page-2 orphan not missed
    assert summary.remote_scanned == 2


@pytest.mark.asyncio
async def test_missing_snapshot_has_next_conflict_is_incomplete(monkeypatch, no_side_effects):
    """Codex-C P2 (reverse): a Phase-B snapshot that still advertises another page
    (has_next=True) must NOT be treated as a complete single-response inventory even
    if total_pages==1 and counts match — missing detection fails closed (incomplete,
    no reindex)."""
    present = _owned_doc("source:present")
    snap = RemoteDocumentsPage(
        documents=(present,),
        page=1,
        page_size=200,
        total_count=1,
        total_pages=1,
        has_next=True,  # contradicts total_pages==1
    )
    svc = _FakeService([snap], enabled=True)
    monkeypatch.setattr(
        reconcile_mod,
        "repo_query",
        _make_repo_query({"source:present": "t", "source:missing": "t"}),
    )
    monkeypatch.setattr(reconcile_mod.deletion, "arm_orphan_deletion", _ArmRecorder())

    def _no_submit(*a, **k):  # pragma: no cover
        raise AssertionError("must not reindex from a has_next-conflicted snapshot")

    import surreal_commands

    monkeypatch.setattr(surreal_commands, "submit_command", _no_submit)

    summary = await reconcile(svc, repair=True, reconcile_config=_cfg())
    assert summary.missing_inventory_incomplete is True
    assert summary.incomplete_inventory is True
    assert summary.index_repairs_enqueued == 0


@pytest.mark.asyncio
async def test_phase_b_snapshot_failure_counts_error(monkeypatch, no_side_effects):
    """Codex-C P3: a Phase-B snapshot listing failure must be reported as an error
    (not errors==0) and mark the run incomplete."""
    from open_notebook.integrations.graphrag.models import GraphRAGUnavailableError

    class _PhaseBFailService(_FakeService):
        def __init__(self):
            super().__init__([], enabled=True)
            self._n = 0

        async def list_remote_documents_detailed(self, *, page, page_size):
            self._n += 1
            if self._n == 1:
                # Phase A remote sweep: an empty, complete page.
                return _page(
                    [], total_count=0, total_pages=1, has_next=False, page=page
                )
            # Phase B missing-detection snapshot listing fails.
            raise GraphRAGUnavailableError("snapshot failed")

    svc = _PhaseBFailService()
    monkeypatch.setattr(reconcile_mod, "repo_query", _make_repo_query({}))
    monkeypatch.setattr(reconcile_mod.deletion, "arm_orphan_deletion", _ArmRecorder())

    summary = await reconcile(svc, repair=True, reconcile_config=_cfg())
    assert summary.errors >= 1  # truthfully reported (was 0 before the fix)
    assert summary.incomplete_inventory is True
    assert summary.missing_inventory_incomplete is True


def test_is_complete_snapshot_fails_closed_on_contradictions():
    """The single completeness oracle accepts ONLY an authoritative one-page result
    and fails closed on every pagination/count/readability contradiction."""
    from open_notebook.integrations.graphrag.reconcile import _is_complete_snapshot

    def _p(docs, **kw):
        base = dict(page=1, page_size=200, total_count=len(docs), total_pages=1, has_next=False)
        base.update(kw)
        return RemoteDocumentsPage(documents=tuple(docs), **base)

    a = _owned_doc("source:a")
    assert _is_complete_snapshot(_p([a])) is True
    assert _is_complete_snapshot(_p([], total_count=0, total_pages=0)) is True  # empty ok
    assert _is_complete_snapshot(_p([a], total_pages=0)) is False  # 0 pages with docs
    assert _is_complete_snapshot(_p([a], total_pages=-1)) is False  # negative pages
    assert _is_complete_snapshot(_p([a], total_pages=2)) is False  # multi-page
    assert _is_complete_snapshot(_p([a], has_next=True)) is False  # advertises more
    assert _is_complete_snapshot(_p([a], total_count=2)) is False  # count mismatch
    assert _is_complete_snapshot(_p([a], malformed=True)) is False  # dropped rows
    idless = _p([RemoteDocument(doc_id="", file_path="source:a")], total_count=1)
    assert _is_complete_snapshot(idless) is False  # unreadable row


@pytest.mark.asyncio
async def test_remote_sweep_total_count_exceeds_scanned_marks_incomplete(
    monkeypatch, no_side_effects
):
    """Codex-C P2 round-2: a terminal page whose total_count exceeds what we could
    read means the inventory was truncated -> incomplete (never false-complete)."""
    p = _page([_owned_doc("source:a")], total_count=3, total_pages=1, has_next=False)
    svc = _FakeService([p], enabled=False)
    monkeypatch.setattr(reconcile_mod, "repo_query", _make_repo_query({}))
    monkeypatch.setattr(reconcile_mod.deletion, "arm_orphan_deletion", _ArmRecorder())
    monkeypatch.setattr(reconcile_mod, "enqueue_drain_if_pending", lambda: _noop())

    summary = await reconcile(svc, repair=True, reconcile_config=_cfg())
    assert summary.incomplete_inventory is True


@pytest.mark.asyncio
async def test_remote_sweep_completeness_semantics(monkeypatch, no_side_effects):
    """A sweep claims completeness (incomplete_inventory False) ONLY for a single
    internally-consistent page; a multi-page sweep or an under-reported terminal
    page (2 rows but total_count=1) is honestly INCOMPLETE."""
    monkeypatch.setattr(reconcile_mod, "repo_query", _make_repo_query({}))
    monkeypatch.setattr(reconcile_mod.deletion, "arm_orphan_deletion", _ArmRecorder())
    monkeypatch.setattr(reconcile_mod, "enqueue_drain_if_pending", lambda: _noop())

    # (a) single authoritative page -> complete
    one = _page([_owned_doc("source:a")], total_count=1, total_pages=1, has_next=False)
    s1 = await reconcile(_FakeService([one], enabled=False), repair=True, reconcile_config=_cfg())
    assert s1.incomplete_inventory is False

    # (b) under-reported terminal metadata (2 rows, total_count=1) -> incomplete
    under = _page(
        [_owned_doc("source:a"), _owned_doc("source:b")],
        total_count=1,
        total_pages=1,
        has_next=False,
    )
    s2 = await reconcile(_FakeService([under], enabled=False), repair=True, reconcile_config=_cfg())
    assert s2.incomplete_inventory is True

    # (c) multi-page corpus -> cannot prove full coverage -> incomplete
    p1 = _page([_owned_doc("source:a")], total_count=2, total_pages=2, has_next=True, page=1)
    p2 = _page([_owned_doc("source:b")], total_count=2, total_pages=2, has_next=False, page=2)
    s3 = await reconcile(_FakeService([p1, p2], enabled=False), repair=True, reconcile_config=_cfg())
    assert s3.incomplete_inventory is True


@pytest.mark.asyncio
async def test_repair_snapshot_failure_counts_error(monkeypatch, no_side_effects):
    """Codex-C P3 round-2: a repair-time fresh-snapshot listing failure must be
    reported as an error and mark incomplete, and must NOT reindex."""
    from open_notebook.integrations.graphrag.models import GraphRAGUnavailableError

    present = _owned_doc("source:present")
    before = _page([present], total_count=1, total_pages=1)

    class _RepairFail(_FakeService):
        def __init__(self):
            super().__init__([before], enabled=True)
            self._n = 0

        async def list_remote_documents_detailed(self, *, page, page_size):
            self._n += 1
            self.list_calls.append(page)
            if self._n >= 3:  # the repair-time fresh snapshot
                raise GraphRAGUnavailableError("repair snapshot failed")
            return before

    svc = _RepairFail()
    monkeypatch.setattr(
        reconcile_mod,
        "repo_query",
        _make_repo_query({"source:present": "t", "source:missing": "t"}),
    )
    monkeypatch.setattr(reconcile_mod.deletion, "arm_orphan_deletion", _ArmRecorder())

    def _no_submit(*a, **k):  # pragma: no cover
        raise AssertionError("must not reindex when the repair snapshot failed")

    import surreal_commands

    monkeypatch.setattr(surreal_commands, "submit_command", _no_submit)

    summary = await reconcile(svc, repair=True, reconcile_config=_cfg())
    assert summary.errors >= 1
    assert summary.incomplete_inventory is True
    assert summary.index_repairs_enqueued == 0


# ===========================================================================
# E. CLIENT DETAILED LISTING (httpx.MockTransport) + 03C regression
# ===========================================================================


def _client(handler) -> GraphRAGClient:
    cfg = GraphRAGConfig(enabled=True, base_url="http://x.invalid:9621", timeout=5.0, api_key=None)
    return GraphRAGClient(cfg, transport=httpx.MockTransport(handler))


def _raw_page(docs, *, total_count=None, total_pages=1, has_next=False):
    return {
        "documents": docs,
        "pagination": {
            "page": 1,
            "page_size": 200,
            "total_count": len(docs) if total_count is None else total_count,
            "total_pages": total_pages,
            "has_next": has_next,
            "has_prev": False,
        },
        "status_counts": {},
    }


class TestClientDetailed:
    @pytest.mark.asyncio
    async def test_detailed_preserves_file_path_and_status(self):
        def handler(req):
            return httpx.Response(
                200,
                json=_raw_page(
                    [{"id": "doc-1", "file_path": "source:1", "status": "processed"}]
                ),
            )

        page = await _client(handler).list_documents_detailed()
        assert page.documents[0] == RemoteDocument(
            doc_id="doc-1", file_path="source:1", status="processed"
        )
        assert page.has_next is False

    @pytest.mark.asyncio
    async def test_detailed_drops_non_dict_and_blank_file_path(self):
        def handler(req):
            return httpx.Response(
                200,
                json=_raw_page(
                    ["not-a-dict", {"id": "doc-2", "file_path": "   "}], total_count=2
                ),
            )

        page = await _client(handler).list_documents_detailed()
        assert len(page.documents) == 1  # non-dict dropped
        assert page.documents[0].doc_id == "doc-2"
        assert page.documents[0].file_path is None  # blank -> None

    @pytest.mark.asyncio
    async def test_detailed_blank_id_is_unreadable(self):
        """A whitespace-only id must parse to "" (unreadable), so completeness
        checks fail closed instead of counting it as a real document."""
        def handler(req):
            return httpx.Response(
                200, json=_raw_page([{"id": "   ", "file_path": "source:a"}], total_count=1)
            )

        page = await _client(handler).list_documents_detailed()
        assert page.documents[0].doc_id == ""

    @pytest.mark.asyncio
    async def test_detailed_missing_pagination_is_protocol_error(self):
        def handler(req):
            return httpx.Response(200, json={"documents": []})

        with pytest.raises(GraphRAGProtocolError):
            await _client(handler).list_documents_detailed()

    @pytest.mark.asyncio
    async def test_list_documents_page_projection_unchanged(self):
        """03C regression: list_documents_page still yields ids, and a doc with no
        id shortens doc_ids so the completeness check fails closed."""
        def handler(req):
            return httpx.Response(
                200,
                json=_raw_page(
                    [
                        {"id": "doc-a", "file_path": "source:a"},
                        {"file_path": "source:b"},  # no id -> excluded from doc_ids
                    ],
                    total_count=2,
                ),
            )

        page = await _client(handler).list_documents_page()
        assert page.doc_ids == ("doc-a",)
        assert page.total_count == 2  # len(doc_ids) < total_count -> caller sees UNKNOWN

    @pytest.mark.asyncio
    async def test_page_size_ceiling_matches_probe_constant(self):
        assert ABSENCE_PROBE_PAGE_SIZE == 200


# ===========================================================================
# F. SECURITY: samples/result carry no content
# ===========================================================================


@pytest.mark.asyncio
async def test_samples_are_ids_only_no_content(monkeypatch, no_side_effects):
    svc = _FakeService(
        [_page([_owned_doc("source:gone"), RemoteDocument(doc_id="doc-f", file_path="/p")])],
        enabled=False,
    )
    monkeypatch.setattr(reconcile_mod, "repo_query", _make_repo_query({}))
    monkeypatch.setattr(reconcile_mod.deletion, "arm_orphan_deletion", _ArmRecorder())
    monkeypatch.setattr(reconcile_mod, "enqueue_drain_if_pending", lambda: _noop())

    summary = await reconcile(svc, repair=True, reconcile_config=_cfg(max_sample_ids=5))
    # Only record ids / doc ids appear as samples — never document text.
    assert summary.samples.get("owned_orphan") == ["source:gone"]
    assert summary.samples.get("foreign") == ["doc-f"]
    text = str(summary)
    assert "real text" not in text and "full_text" not in text


def test_reconcile_module_never_resolves_or_deletes():
    """Structural guard: 03D reconcile must not RESOLVE/DEFER a tombstone or DELETE
    remotely — those belong to 03C only. (A read-only absence probe,
    confirm_source_document_absent, IS allowed: reconcile uses it to FRESH-confirm a
    missing candidate before an 03A reindex; it neither resolves nor deletes.)"""
    from pathlib import Path

    src = Path(reconcile_mod.__file__).read_text(encoding="utf-8")
    for forbidden in (
        "resolve_tombstone_cas",
        "resolve_current_tombstone_cas",
        "defer_tombstone_cas",
        "delete_document_for_source",
    ):
        assert forbidden not in src, f"reconcile.py must not call {forbidden}"


def test_max_sample_ids_cap_enforced():
    from open_notebook.integrations.graphrag.reconcile import ReconcileSummary

    s = ReconcileSummary(mode="audit", max_sample_ids=2)
    for i in range(10):
        s.add_sample("k", f"source:{i}")
    assert s.samples["k"] == ["source:0", "source:1"]


# ===========================================================================
# G. LIVE SurrealDB — orphan-arming helper (skipped if DB unreachable)
# ===========================================================================


async def _db_reachable() -> bool:
    try:
        from open_notebook.database.repository import repo_query

        await repo_query("RETURN 1")
        return True
    except Exception:
        return False


REPO_ROOT = Path(__file__).resolve().parents[1]
MIGRATIONS = REPO_ROOT / "open_notebook" / "database" / "migrations"


@pytest_asyncio.fixture
async def live_db():
    if not await _db_reachable():
        pytest.skip("SurrealDB not reachable")
    from open_notebook.database.async_migrate import AsyncMigration
    from open_notebook.database.repository import repo_query

    # Ensure migrations 24 + 25 are applied (idempotent) so the SCHEMAFULL
    # graphrag_deletion table has the next_attempt_at field the arming helper sets.
    await repo_query(AsyncMigration.from_file(str(MIGRATIONS / "24.surrealql")).sql)
    await repo_query(AsyncMigration.from_file(str(MIGRATIONS / "25.surrealql")).sql)
    yield


def _syn_source_id() -> str:
    return f"source:d3recon{uuid.uuid4().hex[:12]}"


@pytest.mark.asyncio
async def test_live_arm_orphan_creates_one_row_with_db_arm_id(live_db):
    from open_notebook.database.repository import repo_query
    from open_notebook.integrations.graphrag.deletion import (
        DELETION_TABLE,
        arm_orphan_deletion,
        pending_deletion_exists,
    )
    from open_notebook.integrations.graphrag.models import record_id_for

    sid = _syn_source_id()
    rid = record_id_for(sid, tables=frozenset({"source"}))
    try:
        assert await pending_deletion_exists(sid) is False
        armed = await arm_orphan_deletion(sid)
        assert armed is True
        assert await pending_deletion_exists(sid) is True

        rows = await repo_query(
            f"SELECT * FROM {DELETION_TABLE} WHERE source_id = $sid", {"sid": rid}
        )
        assert len(rows) == 1
        row = rows[0]
        assert row["status"] == "pending"
        assert row["arm_id"]  # DB-generated uuid, non-empty
        assert row["next_attempt_at"] is not None
        # No document content on the row.
        assert "full_text" not in row and "content" not in row

        # Re-arm is suppressed while a pending intent exists (no churn).
        first_arm = row["arm_id"]
        assert await arm_orphan_deletion(sid) is False
        rows2 = await repo_query(
            f"SELECT * FROM {DELETION_TABLE} WHERE source_id = $sid", {"sid": rid}
        )
        assert len(rows2) == 1
        assert rows2[0]["arm_id"] == first_arm  # unchanged -> no re-arm
    finally:
        await repo_query(
            f"DELETE {DELETION_TABLE} WHERE source_id = $sid", {"sid": rid}
        )


@pytest.mark.asyncio
async def test_live_arm_orphan_interoperates_with_delete_event(live_db):
    """A real source delete (event arms a tombstone) then arm_orphan_deletion for
    the same id must NOT create a second row — one effective tombstone."""
    from open_notebook.database.repository import repo_query
    from open_notebook.integrations.graphrag.deletion import (
        DELETION_TABLE,
        arm_orphan_deletion,
    )
    from open_notebook.integrations.graphrag.models import record_id_for

    sid = _syn_source_id()
    rid = record_id_for(sid, tables=frozenset({"source"}))
    try:
        # Create then delete a real source -> the migration event arms a tombstone.
        await repo_query(
            "CREATE $id SET full_text = 'synthetic', title = 't'", {"id": rid}
        )
        await repo_query("DELETE $id", {"id": rid})
        rows = await repo_query(
            f"SELECT * FROM {DELETION_TABLE} WHERE source_id = $sid", {"sid": rid}
        )
        assert len(rows) == 1  # event armed exactly one
        event_arm = rows[0]["arm_id"]

        # Reconcile rediscovers the same orphan: it must detect the pending row and
        # NOT create a duplicate / churn the arm.
        assert await arm_orphan_deletion(sid) is False
        rows2 = await repo_query(
            f"SELECT * FROM {DELETION_TABLE} WHERE source_id = $sid", {"sid": rid}
        )
        assert len(rows2) == 1
        assert rows2[0]["arm_id"] == event_arm
    finally:
        await repo_query(f"DELETE {DELETION_TABLE} WHERE source_id = $sid", {"sid": rid})


@pytest.mark.asyncio
async def test_live_numeric_and_string_numeric_arm_distinct_rows(live_db):
    from open_notebook.database.repository import repo_query
    from open_notebook.integrations.graphrag.deletion import (
        DELETION_TABLE,
        arm_orphan_deletion,
    )
    from open_notebook.integrations.graphrag.models import record_id_for

    n = uuid.uuid4().int % 10_000_000
    numeric = f"source:{n}"
    string_numeric = f"source:⟨{n}⟩"
    rids = [record_id_for(s, tables=frozenset({"source"})) for s in (numeric, string_numeric)]
    try:
        assert await arm_orphan_deletion(numeric) is True
        assert await arm_orphan_deletion(string_numeric) is True
        count = 0
        for rid in rids:
            rows = await repo_query(
                f"SELECT * FROM {DELETION_TABLE} WHERE source_id = $sid", {"sid": rid}
            )
            count += len(rows)
        assert count == 2  # two DISTINCT tombstones, not merged
    finally:
        for rid in rids:
            await repo_query(
                f"DELETE {DELETION_TABLE} WHERE source_id = $sid", {"sid": rid}
            )


@pytest.mark.asyncio
async def test_live_canonical_keyset_recordid_cursor_is_strict(live_db):
    """Phase-B enumerates canonical ids by keyset with a RECORDID cursor. On real
    SurrealDB v2.6.5, binding a RecordID (record_id_for) makes `id > $last` STRICT
    (excludes the boundary); binding the stringified id repo_query returns does NOT
    — that was a live-only bug the mocks could not catch. This guards the fix so the
    missing sweep advances without repeating or skipping a source."""
    from open_notebook.database.repository import repo_query
    from open_notebook.integrations.graphrag.models import record_id_for

    tag = f"d3kset{uuid.uuid4().hex[:10]}"
    ids = [f"source:{tag}{i}" for i in range(3)]
    rids = [record_id_for(s, tables=frozenset({"source"})) for s in ids]
    try:
        for rid in rids:
            await repo_query(
                "CREATE $id SET full_text = 'x', title = 't'", {"id": rid}
            )
        allids = await repo_query(
            "SELECT VALUE id FROM source ORDER BY id ASC LIMIT $n", {"n": 100000}
        )
        mine = sorted(s for s in (str(r) for r in allids) if tag in s)
        assert mine == sorted(ids)

        # RecordID cursor -> strict `>` (boundary excluded), ordered.
        cursor = record_id_for(mine[0], tables=frozenset({"source"}))
        gt = await repo_query(
            "SELECT VALUE id FROM source WHERE id > $last ORDER BY id ASC LIMIT $n",
            {"last": cursor, "n": 100000},
        )
        gt_mine = sorted(s for s in (str(r) for r in gt) if tag in s)
        assert mine[0] not in gt_mine, "RecordID cursor must strictly exclude boundary"
        assert gt_mine == mine[1:], "keyset must return exactly the greater ids, in order"
    finally:
        for rid in rids:
            await repo_query("DELETE $id", {"id": rid})


# ===========================================================================
# H. LIVE LightRAG — real /documents/paginated detailed contract
# ===========================================================================


def _live_config():
    base = os.environ.get("OPEN_NOTEBOOK_GRAPHRAG_BASE_URL", "").rstrip("/")
    if not base:
        pytest.skip("OPEN_NOTEBOOK_GRAPHRAG_BASE_URL not set (live LightRAG test)")
    return GraphRAGConfig(
        enabled=True,
        base_url=base,
        timeout=15.0,
        api_key=os.environ.get("OPEN_NOTEBOOK_GRAPHRAG_API_KEY") or None,
    )


async def _sidecar_reachable(client) -> bool:
    try:
        health = await client.health()
        return health.healthy
    except Exception:
        return False


@pytest.mark.asyncio
async def test_live_paginated_detailed_contract():
    """The real v1.5.6 paginated endpoint returns id + file_path + a pagination
    block with total_count/total_pages (the fields reconcile depends on)."""
    client = GraphRAGClient(_live_config())
    if not await _sidecar_reachable(client):
        pytest.skip("configured LightRAG sidecar not reachable/healthy")
    page = await client.list_documents_detailed(page=1, page_size=200)
    assert page.page_size == 200
    assert isinstance(page.total_count, int) and isinstance(page.total_pages, int)
    for doc in page.documents:
        assert isinstance(doc.doc_id, str)


@pytest.mark.asyncio
async def test_live_page_size_over_ceiling_is_rejected():
    """page_size 201 must be rejected by the real endpoint (single-response
    ceiling is a real server bound, not just our constant)."""
    from open_notebook.integrations.graphrag.models import GraphRAGError

    client = GraphRAGClient(_live_config())
    if not await _sidecar_reachable(client):
        pytest.skip("configured LightRAG sidecar not reachable/healthy")
    with pytest.raises(GraphRAGError):
        await client.list_documents_detailed(page=1, page_size=201)


@pytest.mark.asyncio
async def test_live_owned_synthetic_doc_is_classified_owned():
    """Insert a synthetic document and confirm reconcile's ownership contract
    recognizes it (doc.id == compute_doc_id(file_path), file_path == our id)."""
    from open_notebook.integrations.graphrag.service import GraphRAGService

    cfg = _live_config()
    client = GraphRAGClient(cfg)
    if not await _sidecar_reachable(client):
        pytest.skip("configured LightRAG sidecar not reachable/healthy")

    source_id = f"source:d3live{uuid.uuid4().hex[:12]}"
    service = GraphRAGService(config=cfg)
    ack = await service.index_synthetic_document(
        source_id=source_id, canonical_text="Synthetic public reconcile probe."
    )
    assert ack.track_id
    # Give the sidecar a brief moment to register the document row.
    import asyncio

    found = None
    for _ in range(25):
        page = await client.list_documents_detailed(page=1, page_size=200)
        found = next(
            (d for d in page.documents if d.doc_id == compute_doc_id(source_id)), None
        )
        if found is not None:
            break
        await asyncio.sleep(1.0)
    if found is None:
        pytest.skip("sidecar did not register the synthetic doc (no provider)")

    kind, sid = classify_ownership(found)
    assert kind is OwnershipClass.OWNED
    assert sid == source_id
    # Clean up.
    try:
        await service.delete_document_for_source(source_id=source_id)
    except Exception:
        pass


@pytest.mark.asyncio
async def test_live_owned_orphan_end_to_end_arms_intent_not_direct_delete(
    live_db, monkeypatch
):
    """END-TO-END (real sidecar + real DB): a synthetic document whose canonical
    source does NOT exist is positively observed as an owned ORPHAN, and REPAIR
    arms a durable deletion intent (03B/03C) — it does NOT delete remotely itself
    (the doc is still present after reconcile; 03C performs the delete later).

    Flag OFF so the missing-detection sweep (which could enqueue real index jobs on
    the shared dev DB) is skipped; orphan arming is flag-independent. The 03C drain
    wake-up is stubbed so no real worker is triggered and the assertion window is
    stable."""
    from open_notebook.database.repository import repo_query
    from open_notebook.integrations.graphrag.deletion import (
        DELETION_TABLE,
        pending_deletion_exists,
    )
    from open_notebook.integrations.graphrag.models import record_id_for
    from open_notebook.integrations.graphrag.service import GraphRAGService

    cfg = _live_config()  # base_url + api_key from local env
    client = GraphRAGClient(cfg)
    if not await _sidecar_reachable(client):
        pytest.skip("configured LightRAG sidecar not reachable/healthy")

    # A source_id that is NOT present in canonical SurrealDB -> its indexed doc is
    # an orphan by construction.
    source_id = f"source:d3orph{uuid.uuid4().hex[:12]}"
    rid = record_id_for(source_id, tables=frozenset({"source"}))
    assert not await repo_query("SELECT id FROM $id", {"id": rid})  # truly absent

    index_service = GraphRAGService(config=cfg)  # enabled=True: allowed to insert
    ack = await index_service.index_synthetic_document(
        source_id=source_id, canonical_text="Synthetic public orphan probe."
    )
    assert ack.track_id

    import asyncio

    doc_id = compute_doc_id(source_id)
    present = False
    for _ in range(25):
        page = await client.list_documents_detailed(page=1, page_size=200)
        if any(d.doc_id == doc_id for d in page.documents):
            present = True
            break
        await asyncio.sleep(1.0)
    if not present:
        pytest.skip("sidecar did not register the synthetic doc (no provider)")

    # Reconcile with indexing FLAG OFF (orphan arming is flag-independent), and stub
    # the drain wake-up so no real worker deletes the doc before we assert.
    async def _no_wake():
        return None

    monkeypatch.setattr(reconcile_mod, "enqueue_drain_if_pending", _no_wake)
    recon_cfg = _cfg(max_records=500)
    flag_off_service = GraphRAGService(
        config=GraphRAGConfig(
            enabled=False, base_url=cfg.base_url, timeout=cfg.timeout, api_key=cfg.api_key
        )
    )
    try:
        summary = await reconcile(
            flag_off_service, repair=True, reconcile_config=recon_cfg
        )
        assert summary.owned_orphan >= 1
        assert summary.deletion_intents_armed >= 1
        # Durable intent exists...
        assert await pending_deletion_exists(source_id) is True
        # ...and 03D did NOT delete the remote doc itself (03C does that later).
        page = await client.list_documents_detailed(page=1, page_size=200)
        assert any(d.doc_id == doc_id for d in page.documents), (
            "reconcile must not delete remotely; the doc must still be present"
        )
        assert summary.index_repairs_enqueued == 0  # flag OFF -> no indexing
    finally:
        await repo_query(f"DELETE {DELETION_TABLE} WHERE source_id = $sid", {"sid": rid})
        try:
            await index_service.delete_document_for_source(source_id=source_id)
        except Exception:
            pass
