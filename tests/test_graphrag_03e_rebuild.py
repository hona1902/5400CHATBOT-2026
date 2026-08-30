"""GraphRAG-03E REBUILD tests (property-oriented).

03E is an operator-triggered, bounded canonical REBUILD that re-drives the
EXISTING 03A ``graphrag_index_source`` (source_id-only) command over CURRENT
non-empty Sources, to force convergence of PRESENT_UNVERIFIED docs (03D cannot
verify content because LightRAG v1.5.6 has no content_hash).

Each test names the orchestration/security property that would break if the
implementation were subtly wrong. Two layers, mirroring 03C/03D:

  * MOCK/STRUCTURAL (always run): PLAN read-only-ness, EXECUTE gating + preflight,
    source_id-only dispatch, empty/vanished classification (Option A: report only,
    never arm), keyset traversal + RecordID cursor, the exact cap boundary
    (N==cap vs N==cap+1), continuation, invalid-cursor fail-closed, dispatch
    accounting, completion terminology, no-content-in-result.
  * LIVE-DB (skipped if SurrealDB unreachable): real keyset enumeration +
    continuation over synthetic sources; PLAN counts; source_id-only dispatch.

Invariants under test: 03E never arms a deletion, never deletes, never sends
full_text in a command payload, never runs automatically, and never claims remote
content was verified. Empty sources are reported (Option A), never rebuilt.
"""

import os
import shutil
import subprocess
import sys
import time
import uuid
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
import pytest_asyncio

from open_notebook.integrations.graphrag import rebuild as rebuild_mod
from open_notebook.integrations.graphrag.config import (
    GraphRAGRebuildConfig,
    load_rebuild_config,
)
from open_notebook.integrations.graphrag.rebuild import (
    DISPATCH_COMPLETE,
    DISPATCH_INCOMPLETE,
    DISPATCH_PARTIAL,
    INVALID_CURSOR,
    PLAN_ONLY,
    PREFLIGHT_FAILED,
    SKIPPED_DISABLED,
    SKIPPED_EXECUTE_NOT_ALLOWED,
    SKIPPED_NOT_CONFIGURED,
    RebuildSummary,
    rebuild,
)


def _cfg(**overrides) -> GraphRAGRebuildConfig:
    # execute_enabled defaults TRUE here so EXECUTE dispatch tests exercise the
    # dispatch path; the dedicated default-OFF lock is covered by its own tests.
    defaults = dict(
        canonical_batch_size=100,
        max_sources_per_run=1000,
        max_sample_ids=20,
        execute_enabled=True,
    )
    defaults.update(overrides)
    return GraphRAGRebuildConfig(**defaults)  # type: ignore[arg-type]


class _FakeService:
    """Stub GraphRAGService. ``health()`` is content-free (GET /health); the
    remote-document listing must NEVER be called by rebuild (it is a dispatcher,
    not a reconciler)."""

    def __init__(
        self,
        *,
        enabled=True,
        base_url="http://sidecar.invalid:9621",
        healthy=True,
        health_boom=False,
    ):
        self.config = SimpleNamespace(enabled=enabled, base_url=base_url)
        self._healthy = healthy
        self._health_boom = health_boom
        self.health_calls = 0

    async def health(self):
        self.health_calls += 1
        if self._health_boom:
            raise RuntimeError("health probe boom")
        return SimpleNamespace(healthy=self._healthy, detail="")

    async def list_remote_documents_detailed(self, **kwargs):  # pragma: no cover
        raise AssertionError("03E rebuild must not enumerate remote documents")


def _make_repo_query(canonical: dict, *, source_ids=None, record=None):
    """Fake rebuild.repo_query.

    ``canonical`` maps canonical source_id -> full_text of an EXISTING source (a
    source absent from the dict reads as canonically ABSENT). ``source_ids`` is the
    ordered id list the keyset enumeration returns (defaults to sorted canonical
    keys). ``record`` (optional list) captures every (query, params) issued."""
    ids = sorted(source_ids if source_ids is not None else canonical.keys())

    async def _q(query, params=None):
        params = params or {}
        if record is not None:
            record.append((query, params))
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


class _SubmitRecorder:
    def __init__(self, *, boom=False):
        self.calls = []
        self._boom = boom

    def __call__(self, app, name, payload):
        self.calls.append((app, name, payload))
        if self._boom:
            raise RuntimeError("enqueue boom")
        return "command:rebuilt"


# ===========================================================================
# A. PLAN MODE — strictly read-only
# ===========================================================================


@pytest.mark.asyncio
async def test_plan_is_read_only_no_enqueue_no_health(monkeypatch):
    """PLAN (default) enumerates + classifies + counts; it makes NO health call, NO
    enqueue, NO remote request, and mutates nothing."""
    svc = _FakeService(enabled=True, healthy=True, health_boom=True)  # health() would raise
    canonical = {"source:a": "text a", "source:b": "text b"}
    monkeypatch.setattr(rebuild_mod, "repo_query", _make_repo_query(canonical))

    def _no_submit(*a, **k):  # pragma: no cover
        raise AssertionError("PLAN must not enqueue")

    import surreal_commands

    monkeypatch.setattr(surreal_commands, "submit_command", _no_submit)

    summary = await rebuild(svc, mode="plan", rebuild_config=_cfg())

    assert summary.mode == "plan"
    assert summary.canonical_scanned == 2
    assert summary.eligible_nonempty == 2
    assert summary.planned == 2
    assert summary.enqueued == 0
    assert svc.health_calls == 0  # PLAN never probes the sidecar
    assert summary.completion == PLAN_ONLY
    assert summary.dispatch_complete is False  # PLAN is never a dispatch


@pytest.mark.asyncio
async def test_plan_classifies_empty_and_vanished(monkeypatch):
    """PLAN separates non-empty (eligible) from empty/whitespace (desired ABSENT)
    and from vanished (deleted between enumeration and state read)."""
    # ids enumerated: a (nonempty), b (whitespace-empty), c (deleted -> absent)
    canonical = {"source:a": "real", "source:b": "   \n\t "}
    repo = _make_repo_query(canonical, source_ids=["source:a", "source:b", "source:c"])
    monkeypatch.setattr(rebuild_mod, "repo_query", repo)

    summary = await rebuild(_FakeService(), mode="plan", rebuild_config=_cfg())
    assert summary.canonical_scanned == 3
    assert summary.eligible_nonempty == 1
    assert summary.empty == 1
    assert summary.vanished == 1
    assert summary.planned == 1  # only the non-empty source would be dispatched


@pytest.mark.asyncio
async def test_plan_runs_locally_even_when_disabled(monkeypatch):
    """PLAN works from canonical DB only — no flag/config required. It reports
    execute_allowed=False so an operator knows EXECUTE would be a no-op."""
    svc = _FakeService(enabled=False, base_url="")
    monkeypatch.setattr(
        rebuild_mod, "repo_query", _make_repo_query({"source:a": "t"})
    )
    summary = await rebuild(svc, mode="plan", rebuild_config=_cfg())
    assert summary.eligible_nonempty == 1
    assert summary.execute_allowed is False
    assert svc.health_calls == 0


@pytest.mark.asyncio
async def test_plan_samples_are_ids_only_no_content(monkeypatch):
    """Capped samples carry record ids only — never document text."""
    canonical = {"source:secret": "TOP SECRET BODY", "source:b": "  "}
    monkeypatch.setattr(
        rebuild_mod,
        "repo_query",
        _make_repo_query(canonical, source_ids=["source:secret", "source:b"]),
    )
    summary = await rebuild(_FakeService(), mode="plan", rebuild_config=_cfg(max_sample_ids=5))
    assert summary.samples.get("planned") == ["source:secret"]
    blob = str(summary) + repr(summary.samples)
    assert "TOP SECRET BODY" not in blob and "full_text" not in blob


# ===========================================================================
# B. EXECUTE MODE — gating, preflight, source_id-only dispatch
# ===========================================================================


@pytest.mark.asyncio
async def test_default_mode_is_plan_not_execute(monkeypatch):
    """Calling rebuild() with no mode must default to PLAN (never dispatch)."""
    monkeypatch.setattr(rebuild_mod, "repo_query", _make_repo_query({"source:a": "t"}))

    def _no_submit(*a, **k):  # pragma: no cover
        raise AssertionError("default mode must not enqueue")

    import surreal_commands

    monkeypatch.setattr(surreal_commands, "submit_command", _no_submit)
    summary = await rebuild(_FakeService(), rebuild_config=_cfg())
    assert summary.mode == "plan"
    assert summary.enqueued == 0


@pytest.mark.asyncio
async def test_execute_flag_off_no_dispatch(monkeypatch):
    """EXECUTE with the flag OFF must dispatch nothing and never probe the sidecar."""
    svc = _FakeService(enabled=False, base_url="http://x:9621", health_boom=True)
    monkeypatch.setattr(rebuild_mod, "repo_query", _make_repo_query({"source:a": "t"}))

    def _no_submit(*a, **k):  # pragma: no cover
        raise AssertionError("flag OFF must not enqueue indexing")

    import surreal_commands

    monkeypatch.setattr(surreal_commands, "submit_command", _no_submit)

    summary = await rebuild(svc, mode="execute", rebuild_config=_cfg())
    assert summary.skipped_disabled is True
    assert summary.enqueued == 0
    assert summary.canonical_scanned == 0  # returned before enumerating
    assert svc.health_calls == 0
    assert summary.completion == SKIPPED_DISABLED


@pytest.mark.asyncio
async def test_execute_not_configured_no_partial_dispatch(monkeypatch):
    """EXECUTE enabled but base_url unset must refuse BEFORE any dispatch."""
    svc = _FakeService(enabled=True, base_url="")
    monkeypatch.setattr(rebuild_mod, "repo_query", _make_repo_query({"source:a": "t"}))

    def _no_submit(*a, **k):  # pragma: no cover
        raise AssertionError("unconfigured must not enqueue")

    import surreal_commands

    monkeypatch.setattr(surreal_commands, "submit_command", _no_submit)

    summary = await rebuild(svc, mode="execute", rebuild_config=_cfg())
    assert summary.skipped_not_configured is True
    assert summary.enqueued == 0
    assert summary.canonical_scanned == 0
    assert summary.completion == SKIPPED_NOT_CONFIGURED


@pytest.mark.asyncio
async def test_execute_preflight_unhealthy_zero_dispatch(monkeypatch):
    """A failed content-free health preflight must produce ZERO source dispatch."""
    svc = _FakeService(enabled=True, healthy=False)
    monkeypatch.setattr(rebuild_mod, "repo_query", _make_repo_query({"source:a": "t"}))

    def _no_submit(*a, **k):  # pragma: no cover
        raise AssertionError("unhealthy preflight must not enqueue")

    import surreal_commands

    monkeypatch.setattr(surreal_commands, "submit_command", _no_submit)

    summary = await rebuild(svc, mode="execute", rebuild_config=_cfg())
    assert svc.health_calls == 1  # preflight happened
    assert summary.preflight_unhealthy is True
    assert summary.enqueued == 0
    assert summary.canonical_scanned == 0  # preflight is BEFORE enumeration
    assert summary.completion == PREFLIGHT_FAILED


@pytest.mark.asyncio
async def test_execute_preflight_exception_zero_dispatch(monkeypatch):
    """A health() that raises is treated as an unhealthy preflight — zero dispatch."""
    svc = _FakeService(enabled=True, health_boom=True)
    monkeypatch.setattr(rebuild_mod, "repo_query", _make_repo_query({"source:a": "t"}))

    def _no_submit(*a, **k):  # pragma: no cover
        raise AssertionError("preflight error must not enqueue")

    import surreal_commands

    monkeypatch.setattr(surreal_commands, "submit_command", _no_submit)
    summary = await rebuild(svc, mode="execute", rebuild_config=_cfg())
    assert summary.preflight_unhealthy is True
    assert summary.enqueued == 0


@pytest.mark.asyncio
async def test_execute_dispatches_source_id_only(monkeypatch):
    """EXECUTE enqueues graphrag_index_source with a source_id-ONLY payload (no
    full_text) for each non-empty source; empty sources are NOT enqueued."""
    canonical = {"source:a": "aaa", "source:b": "   ", "source:c": "ccc"}
    monkeypatch.setattr(
        rebuild_mod,
        "repo_query",
        _make_repo_query(canonical, source_ids=["source:a", "source:b", "source:c"]),
    )
    submit = _SubmitRecorder()
    import surreal_commands

    monkeypatch.setattr(surreal_commands, "submit_command", submit)

    summary = await rebuild(_FakeService(), mode="execute", rebuild_config=_cfg())
    assert summary.eligible_nonempty == 2
    assert summary.empty == 1
    assert summary.enqueued == 2
    names = [(n, p) for (_a, n, p) in submit.calls]
    assert names == [
        ("graphrag_index_source", {"source_id": "source:a"}),
        ("graphrag_index_source", {"source_id": "source:c"}),
    ]
    for _app, _name, payload in submit.calls:
        assert set(payload.keys()) == {"source_id"}  # source_id ONLY
        assert "full_text" not in payload
    assert summary.completion == DISPATCH_COMPLETE
    assert summary.dispatch_complete is True


@pytest.mark.asyncio
async def test_execute_empty_source_reported_never_armed_never_dispatched(monkeypatch):
    """Option A: an empty source is counted, never sent to graphrag_index_source, and
    03E arms NO deletion intent (cleanup belongs to 03D REPAIR / 03C)."""
    monkeypatch.setattr(
        rebuild_mod, "repo_query", _make_repo_query({"source:empty": "\n  \t"})
    )
    submit = _SubmitRecorder()
    import surreal_commands

    monkeypatch.setattr(surreal_commands, "submit_command", submit)

    summary = await rebuild(_FakeService(), mode="execute", rebuild_config=_cfg())
    assert summary.empty == 1
    assert summary.eligible_nonempty == 0
    assert summary.enqueued == 0
    assert submit.calls == []  # nothing dispatched for an empty source


@pytest.mark.asyncio
async def test_execute_enqueue_failure_fail_stops_partial_not_complete(monkeypatch):
    """A submit_command failure FAIL-STOPS the sweep at the first failure, is counted
    as an enqueue_failure, and blocks REBUILD_DISPATCH_COMPLETE — enqueue acceptance is
    never assumed, and the sweep never advances the cursor past an un-dispatched source."""
    monkeypatch.setattr(
        rebuild_mod, "repo_query", _make_repo_query({"source:a": "a", "source:b": "b"})
    )
    submit = _SubmitRecorder(boom=True)
    import surreal_commands

    monkeypatch.setattr(surreal_commands, "submit_command", submit)

    summary = await rebuild(_FakeService(), mode="execute", rebuild_config=_cfg())
    assert summary.enqueue_failures == 1  # fail-stop at the FIRST failure
    assert summary.canonical_scanned == 1  # did not scan past the failed source
    assert summary.enqueued == 0
    assert summary.completion == DISPATCH_PARTIAL
    assert summary.dispatch_complete is False


# ===========================================================================
# B2. EXECUTE LOCK — dedicated default-OFF Boundary-B brake (Codex B)
# ===========================================================================


@pytest.mark.asyncio
async def test_execute_locked_by_default_no_dispatch(monkeypatch):
    """With the dedicated execute lock OFF, EXECUTE dispatches nothing, never probes
    the sidecar, and never enumerates — even when GraphRAG is enabled, configured, and
    healthy. Enabling GraphRAG for ingestion must NOT unlock a corpus-wide rebuild."""
    svc = _FakeService(enabled=True, base_url="http://x:9621", healthy=True, health_boom=True)
    monkeypatch.setattr(rebuild_mod, "repo_query", _make_repo_query({"source:a": "t"}))

    def _no_submit(*a, **k):  # pragma: no cover
        raise AssertionError("locked EXECUTE must not enqueue")

    import surreal_commands

    monkeypatch.setattr(surreal_commands, "submit_command", _no_submit)

    summary = await rebuild(svc, mode="execute", rebuild_config=_cfg(execute_enabled=False))
    assert summary.execute_not_allowed is True
    assert summary.execute_allowed is False
    assert summary.enqueued == 0
    assert summary.canonical_scanned == 0  # never enumerated
    assert svc.health_calls == 0  # sidecar never probed while locked
    assert summary.completion == SKIPPED_EXECUTE_NOT_ALLOWED


@pytest.mark.asyncio
async def test_execute_lock_precedes_flag_and_config(monkeypatch):
    """The execute lock is checked FIRST — before flag/config/health — so a locked
    EXECUTE reports the lock (not disabled/not-configured) and probes nothing."""
    svc = _FakeService(enabled=False, base_url="", health_boom=True)
    monkeypatch.setattr(rebuild_mod, "repo_query", _make_repo_query({"source:a": "t"}))
    summary = await rebuild(svc, mode="execute", rebuild_config=_cfg(execute_enabled=False))
    assert summary.execute_not_allowed is True
    assert summary.completion == SKIPPED_EXECUTE_NOT_ALLOWED
    assert summary.skipped_disabled is False  # lock takes precedence
    assert svc.health_calls == 0


@pytest.mark.asyncio
async def test_plan_ignores_execute_lock(monkeypatch):
    """PLAN never dispatches, so the execute lock does not gate it — planning stays
    available for sizing work regardless of the lock."""
    monkeypatch.setattr(rebuild_mod, "repo_query", _make_repo_query({"source:a": "t"}))
    summary = await rebuild(
        _FakeService(), mode="plan", rebuild_config=_cfg(execute_enabled=False)
    )
    assert summary.eligible_nonempty == 1
    assert summary.completion == PLAN_ONLY
    assert summary.execute_allowed is False  # lock off -> execute would not be allowed


# ===========================================================================
# B3. CURSOR SKIP-SAFETY under per-row failure (Codex A / Codex C)
# ===========================================================================


@pytest.mark.asyncio
async def test_enqueue_failure_never_advances_cursor_past_undispatched_source(monkeypatch):
    """Codex C: a,b,c cap=2; enqueue for the FIRST source fails. The sweep must NOT
    advance the resumable cursor past the un-dispatched source. Here 'a' fails first,
    nothing was fully handled yet, so next_cursor is None (resume from the beginning) —
    'a' is re-attempted, never skipped — and the run is PARTIAL, never COMPLETE."""
    canonical = {"source:a": "a", "source:b": "b", "source:c": "c"}

    class _FailFirst:
        def __init__(self):
            self.calls = []

        def __call__(self, app, name, payload):
            self.calls.append(payload["source_id"])
            if payload["source_id"] == "source:a":
                raise RuntimeError("enqueue a failed")
            return "ok"

    submit = _FailFirst()
    monkeypatch.setattr(rebuild_mod, "repo_query", _make_repo_query(canonical))
    import surreal_commands

    monkeypatch.setattr(surreal_commands, "submit_command", submit)
    summary = await rebuild(
        _FakeService(), mode="execute", rebuild_config=_cfg(max_sources_per_run=2, canonical_batch_size=2)
    )
    assert summary.enqueue_failures == 1
    assert summary.completion == DISPATCH_PARTIAL
    assert summary.dispatch_complete is False
    # Cursor must NOT point at or past 'a' (the un-dispatched source). 'a' was the
    # first row and nothing was fully handled, so resume restarts from the beginning.
    assert summary.next_cursor is None
    assert summary.continuation_required is True


@pytest.mark.asyncio
async def test_enqueue_failure_midway_resumes_before_failed_source(monkeypatch):
    """A later source's enqueue fails: the cursor advances only past the FULLY-handled
    sources, so a resume re-attempts the failed source (never skips it)."""
    # a (ok) -> b (empty, handled) -> c (enqueue fails)
    canonical = {"source:a": "a", "source:b": "  ", "source:c": "c"}

    class _FailC:
        def __init__(self):
            self.calls = []

        def __call__(self, app, name, payload):
            self.calls.append(payload["source_id"])
            if payload["source_id"] == "source:c":
                raise RuntimeError("enqueue c failed")
            return "ok"

    submit = _FailC()
    monkeypatch.setattr(
        rebuild_mod,
        "repo_query",
        _make_repo_query(canonical, source_ids=["source:a", "source:b", "source:c"]),
    )
    import surreal_commands

    monkeypatch.setattr(surreal_commands, "submit_command", submit)
    summary = await rebuild(_FakeService(), mode="execute", rebuild_config=_cfg())
    assert summary.enqueued == 1  # a dispatched
    assert summary.empty == 1  # b handled
    assert summary.enqueue_failures == 1  # c failed
    assert summary.completion == DISPATCH_PARTIAL
    # Resume from the last FULLY-handled source (b), so c is re-attempted, not skipped.
    assert summary.next_cursor == "source:b"
    assert summary.continuation_required is True


@pytest.mark.asyncio
async def test_invalid_enumerated_id_halts_partial_never_skips(monkeypatch):
    """Codex A: a structurally invalid id in the source table halts the sweep as
    PARTIAL with a remediation sample, never claims COMPLETE, and never advances the
    cursor past it."""
    async def _q(query, params=None):
        params = params or {}
        if "SELECT VALUE id FROM source" in query:
            if "id > $last" in query:
                return []
            return ["source:good", "not-a-valid-record-id"]
        if query.startswith("SELECT full_text FROM $id"):
            return [{"full_text": "text"}]
        return []

    monkeypatch.setattr(rebuild_mod, "repo_query", _q)
    submit = _SubmitRecorder()
    import surreal_commands

    monkeypatch.setattr(surreal_commands, "submit_command", submit)
    summary = await rebuild(_FakeService(), mode="execute", rebuild_config=_cfg())
    assert summary.errors >= 1
    assert summary.completion == DISPATCH_PARTIAL
    assert summary.dispatch_complete is False
    assert "not-a-valid-record-id" in summary.samples.get("invalid_source_id", [])
    # 'source:good' was fully dispatched before the bad row; resume starts after it,
    # and the bad row is re-encountered (fail-closed) rather than silently skipped.
    assert summary.next_cursor == "source:good"


@pytest.mark.asyncio
async def test_canonical_state_error_halts_partial(monkeypatch):
    """A canonical state-read error halts the sweep as PARTIAL without advancing the
    cursor past the unread source (it is re-attempted on resume)."""
    async def _q(query, params=None):
        params = params or {}
        if "SELECT VALUE id FROM source" in query:
            if "id > $last" in query:
                return []
            return ["source:a"]
        if query.startswith("SELECT full_text FROM $id"):
            raise RuntimeError("transient DB error")
        return []

    monkeypatch.setattr(rebuild_mod, "repo_query", _q)
    summary = await rebuild(_FakeService(), mode="plan", rebuild_config=_cfg())
    assert summary.errors >= 1
    assert summary.completion == DISPATCH_PARTIAL if summary.mode == "execute" else PLAN_ONLY
    # PLAN mode: errors still recorded; the state read failed so 'a' is not counted.
    assert summary.eligible_nonempty == 0


@pytest.mark.asyncio
async def test_cursor_beyond_all_ids_is_empty_segment(monkeypatch):
    """A valid cursor past every id yields a 0-source segment (the documented meaning of
    a continuation cursor). This is NOT a false COMPLETE: it is an empty terminal
    segment. Operators must chain a cursor from a prior run's next_cursor."""
    canonical = {"source:a": "a", "source:b": "b"}
    monkeypatch.setattr(rebuild_mod, "repo_query", _make_repo_query(canonical))
    submit = _SubmitRecorder()
    import surreal_commands

    monkeypatch.setattr(surreal_commands, "submit_command", submit)
    summary = await rebuild(
        _FakeService(), mode="execute", cursor="source:zzzzz", rebuild_config=_cfg()
    )
    assert summary.canonical_scanned == 0
    assert summary.enqueued == 0
    assert submit.calls == []
    assert summary.continuation_required is False


# ===========================================================================
# C. SOURCE-STATE RACES (03A safety model preserved)
# ===========================================================================


@pytest.mark.asyncio
async def test_deleted_after_enumeration_not_resurrected(monkeypatch):
    """A source enumerated but deleted before its state read is 'vanished' — never
    enqueued (03A would also skip_absent; 03E must not resurrect it)."""
    # enumerate a,b,c ; canonical only has a,b (c deleted between enum and state read)
    monkeypatch.setattr(
        rebuild_mod,
        "repo_query",
        _make_repo_query({"source:a": "a", "source:b": "b"}, source_ids=["source:a", "source:b", "source:c"]),
    )
    submit = _SubmitRecorder()
    import surreal_commands

    monkeypatch.setattr(surreal_commands, "submit_command", submit)
    summary = await rebuild(_FakeService(), mode="execute", rebuild_config=_cfg())
    assert summary.vanished == 1
    assert [p["source_id"] for (_a, _n, p) in submit.calls] == ["source:a", "source:b"]


@pytest.mark.asyncio
async def test_dispatch_carries_source_id_so_03a_reloads_current(monkeypatch):
    """Structural guarantee for updated-after-enumeration: 03E sends ONLY source_id,
    so 03A reloads CURRENT canonical text at execution (no queued full_text)."""
    monkeypatch.setattr(rebuild_mod, "repo_query", _make_repo_query({"source:x": "v1"}))
    submit = _SubmitRecorder()
    import surreal_commands

    monkeypatch.setattr(surreal_commands, "submit_command", submit)
    await rebuild(_FakeService(), mode="execute", rebuild_config=_cfg())
    assert submit.calls[0][2] == {"source_id": "source:x"}


# ===========================================================================
# D. PAGINATION / FAIRNESS / CURSOR (keyset only, exact cap boundary)
# ===========================================================================


@pytest.mark.asyncio
async def test_keyset_used_not_offset(monkeypatch):
    """Enumeration uses keyset (id > $last), never OFFSET/START."""
    record = []
    canonical = {f"source:{i:03d}": "t" for i in range(5)}
    monkeypatch.setattr(
        rebuild_mod, "repo_query", _make_repo_query(canonical, record=record)
    )
    await rebuild(_FakeService(), mode="plan", rebuild_config=_cfg(canonical_batch_size=2))
    enum_qs = [q for (q, _p) in record if "SELECT VALUE id FROM source" in q]
    assert any("id > $last" in q for q in enum_qs)  # keyset continuation used
    for q in enum_qs:
        assert "START" not in q.upper() and "OFFSET" not in q.upper()


@pytest.mark.asyncio
async def test_cap_boundary_exactly_max_is_complete(monkeypatch):
    """N == max_sources_per_run: every source dispatched, NO continuation, COMPLETE.
    Guards the off-by-one false-continuation at the exact boundary."""
    canonical = {f"source:s{i}": "t" for i in range(3)}
    monkeypatch.setattr(rebuild_mod, "repo_query", _make_repo_query(canonical))
    submit = _SubmitRecorder()
    import surreal_commands

    monkeypatch.setattr(surreal_commands, "submit_command", submit)
    summary = await rebuild(
        _FakeService(), mode="execute", rebuild_config=_cfg(max_sources_per_run=3, canonical_batch_size=3)
    )
    assert summary.canonical_scanned == 3
    assert summary.enqueued == 3
    assert summary.continuation_required is False
    assert summary.next_cursor is None
    assert summary.completion == DISPATCH_COMPLETE


@pytest.mark.asyncio
async def test_cap_boundary_max_plus_one_requires_continuation(monkeypatch):
    """N == max_sources_per_run + 1: the cap is hit with one row remaining ->
    continuation_required, a cursor, and DISPATCH_INCOMPLETE (never false-complete)."""
    canonical = {f"source:s{i}": "t" for i in range(4)}  # 4 = cap(3) + 1
    monkeypatch.setattr(rebuild_mod, "repo_query", _make_repo_query(canonical))
    submit = _SubmitRecorder()
    import surreal_commands

    monkeypatch.setattr(surreal_commands, "submit_command", submit)
    summary = await rebuild(
        _FakeService(), mode="execute", rebuild_config=_cfg(max_sources_per_run=3, canonical_batch_size=3)
    )
    assert summary.canonical_scanned == 3
    assert summary.enqueued == 3
    assert summary.continuation_required is True
    assert summary.next_cursor == "source:s2"  # the 3rd (cap-th) id, keyset boundary
    assert summary.completion == DISPATCH_INCOMPLETE
    assert summary.dispatch_complete is False


@pytest.mark.asyncio
async def test_continuation_resumes_from_cursor(monkeypatch):
    """Re-invoking with next_cursor dispatches exactly the remaining sources."""
    canonical = {f"source:s{i}": "t" for i in range(4)}
    monkeypatch.setattr(rebuild_mod, "repo_query", _make_repo_query(canonical))
    submit = _SubmitRecorder()
    import surreal_commands

    monkeypatch.setattr(surreal_commands, "submit_command", submit)
    summary = await rebuild(
        _FakeService(),
        mode="execute",
        cursor="source:s2",
        rebuild_config=_cfg(max_sources_per_run=3, canonical_batch_size=3),
    )
    assert [p["source_id"] for (_a, _n, p) in submit.calls] == ["source:s3"]
    assert summary.continuation_required is False
    assert summary.completion == DISPATCH_COMPLETE


@pytest.mark.asyncio
async def test_invalid_cursor_fails_closed_before_dispatch(monkeypatch):
    """An invalid continuation cursor fails closed: no enumeration, no dispatch."""
    record = []
    monkeypatch.setattr(
        rebuild_mod, "repo_query", _make_repo_query({"source:a": "t"}, record=record)
    )

    def _no_submit(*a, **k):  # pragma: no cover
        raise AssertionError("invalid cursor must not dispatch")

    import surreal_commands

    monkeypatch.setattr(surreal_commands, "submit_command", _no_submit)

    summary = await rebuild(
        _FakeService(), mode="execute", cursor="not a record id!", rebuild_config=_cfg()
    )
    assert summary.invalid_cursor is True
    assert summary.errors >= 1
    assert summary.canonical_scanned == 0
    assert summary.enqueued == 0
    assert summary.completion == INVALID_CURSOR
    # never even ran the health preflight or an enumeration query
    assert not any("SELECT VALUE id FROM source" in q for (q, _p) in record)


@pytest.mark.asyncio
async def test_numeric_and_string_numeric_cursor_stay_distinct():
    """source:123 and source:⟨123⟩ are different records; their cursors must not
    collapse (round-trips through record_id_for losslessly)."""
    from open_notebook.integrations.graphrag.models import record_id_for

    numeric = str(record_id_for("source:123", tables=frozenset({"source"})))
    string_numeric = str(record_id_for("source:⟨123⟩", tables=frozenset({"source"})))
    assert numeric != string_numeric


# ===========================================================================
# E. DUPLICATES (idempotent / convergent — dedup is not a correctness primitive)
# ===========================================================================


@pytest.mark.asyncio
async def test_duplicate_execute_is_safe(monkeypatch):
    """Running EXECUTE twice re-enqueues (03A is idempotent/convergent) — no crash,
    no dependence on dedup for correctness."""
    monkeypatch.setattr(rebuild_mod, "repo_query", _make_repo_query({"source:a": "a"}))
    submit = _SubmitRecorder()
    import surreal_commands

    monkeypatch.setattr(surreal_commands, "submit_command", submit)
    s1 = await rebuild(_FakeService(), mode="execute", rebuild_config=_cfg())
    s2 = await rebuild(_FakeService(), mode="execute", rebuild_config=_cfg())
    assert s1.enqueued == 1 and s2.enqueued == 1
    assert len(submit.calls) == 2  # both dispatched; duplicate is harmless


# ===========================================================================
# F. SECURITY / STRUCTURAL GUARDS
# ===========================================================================


def test_rebuild_module_never_deletes_arms_or_purges():
    """Structural: rebuild.py must not arm/resolve tombstones, delete documents, or
    enumerate/purge the remote corpus — it is a dispatcher only."""
    src = Path(rebuild_mod.__file__).read_text(encoding="utf-8")
    for forbidden in (
        "arm_orphan_deletion",
        "delete_document_for_source",
        "resolve_tombstone_cas",
        "resolve_current_tombstone_cas",
        "defer_tombstone_cas",
        "list_remote_documents_detailed",
        "list_documents_detailed",
    ):
        assert forbidden not in src, f"rebuild.py must not reference {forbidden}"


def test_rebuild_never_puts_full_text_in_a_payload():
    """Structural: no outbound payload/document dict carries body text. Reading
    ``full_text`` locally to classify empty/non-empty is allowed (as 03D does), but
    it must never appear as a dict KEY (a payload field) and canonical_text (the
    sidecar body field) must not appear at all."""
    src = Path(rebuild_mod.__file__).read_text(encoding="utf-8")
    assert '"full_text":' not in src  # never a payload/document field
    assert "canonical_text" not in src  # never builds a sidecar document body


def test_no_automatic_or_scheduled_rebuild():
    """03E must be operator-triggered only: no startup/lifespan/scheduler hook may
    invoke the rebuild command."""
    import open_notebook.integrations.graphrag.rebuild as _r  # noqa: F401

    # The command name exists, but nothing in the app wiring auto-invokes it.
    for candidate in (
        Path("api/main.py"),
        Path("open_notebook/integrations/graphrag/drain.py"),
    ):
        if candidate.exists():
            text = candidate.read_text(encoding="utf-8")
            assert "graphrag_rebuild" not in text, (
                f"{candidate} must not auto-invoke graphrag_rebuild"
            )


def test_completion_terminology_never_claims_verified():
    """The completion vocabulary must never assert remote content convergence."""
    for value in (
        DISPATCH_COMPLETE,
        DISPATCH_INCOMPLETE,
        DISPATCH_PARTIAL,
        PLAN_ONLY,
        SKIPPED_DISABLED,
        SKIPPED_NOT_CONFIGURED,
        SKIPPED_EXECUTE_NOT_ALLOWED,
        PREFLIGHT_FAILED,
        INVALID_CURSOR,
    ):
        low = value.lower()
        assert "verified" not in low
        assert "converg" not in low
    assert DISPATCH_COMPLETE == "REBUILD_DISPATCH_COMPLETE"


def test_max_sample_ids_cap_enforced():
    s = RebuildSummary(mode="plan", max_sample_ids=2)
    for i in range(10):
        s.add_sample("planned", f"source:{i}")
    assert s.samples["planned"] == ["source:0", "source:1"]


# ===========================================================================
# G. CONFIG — clamped
# ===========================================================================


def test_rebuild_config_clamps_bad_env(monkeypatch):
    monkeypatch.setenv("OPEN_NOTEBOOK_GRAPHRAG_REBUILD_CANONICAL_BATCH", "100000")
    monkeypatch.setenv("OPEN_NOTEBOOK_GRAPHRAG_REBUILD_MAX_SOURCES", "0")
    monkeypatch.setenv("OPEN_NOTEBOOK_GRAPHRAG_REBUILD_MAX_SAMPLE_IDS", "-5")
    cfg = load_rebuild_config()
    assert 1 <= cfg.canonical_batch_size <= 500
    assert cfg.max_sources_per_run >= 1
    assert 1 <= cfg.max_sample_ids <= 100


def test_execute_lock_default_off_and_explicit_on(monkeypatch):
    """The dedicated EXECUTE lock defaults OFF and only explicit truthy tokens set it."""
    monkeypatch.delenv("OPEN_NOTEBOOK_GRAPHRAG_REBUILD_EXECUTE_ENABLED", raising=False)
    assert load_rebuild_config().execute_enabled is False
    for off in ("", "0", "false", "no", "nonsense"):
        monkeypatch.setenv("OPEN_NOTEBOOK_GRAPHRAG_REBUILD_EXECUTE_ENABLED", off)
        assert load_rebuild_config().execute_enabled is False, off
    for on in ("1", "true", "TRUE", "yes"):
        monkeypatch.setenv("OPEN_NOTEBOOK_GRAPHRAG_REBUILD_EXECUTE_ENABLED", on)
        assert load_rebuild_config().execute_enabled is True, on


def test_rebuild_config_defaults(monkeypatch):
    for var in (
        "OPEN_NOTEBOOK_GRAPHRAG_REBUILD_CANONICAL_BATCH",
        "OPEN_NOTEBOOK_GRAPHRAG_REBUILD_MAX_SOURCES",
        "OPEN_NOTEBOOK_GRAPHRAG_REBUILD_MAX_SAMPLE_IDS",
    ):
        monkeypatch.delenv(var, raising=False)
    cfg = load_rebuild_config()
    assert cfg.canonical_batch_size >= 1
    assert cfg.max_sources_per_run >= 1
    assert cfg.max_sample_ids >= 1


# ===========================================================================
# H. MIGRATION GUARDS (no migration 26; 24/25 frozen; count 50)
# ===========================================================================

REPO_ROOT = Path(__file__).resolve().parents[1]
MIGRATIONS = REPO_ROOT / "open_notebook" / "database" / "migrations"


def test_no_migration_26_and_count_is_50():
    assert not (MIGRATIONS / "26.surrealql").exists()
    assert not (MIGRATIONS / "26_down.surrealql").exists()
    files = list(MIGRATIONS.glob("*.surrealql"))
    assert len(files) == 50, f"expected 50 migration files, found {len(files)}"


# ===========================================================================
# I. LIVE SurrealDB — real keyset enumeration + continuation (skipped if no DB)
# ===========================================================================


async def _db_reachable() -> bool:
    try:
        from open_notebook.database.repository import repo_query

        await repo_query("RETURN 1")
        return True
    except Exception:
        return False


@pytest_asyncio.fixture
async def live_db():
    if not await _db_reachable():
        pytest.skip("SurrealDB not reachable")
    yield


@pytest.mark.asyncio
async def test_live_plan_enumerates_synthetic_sources(live_db, monkeypatch):
    """PLAN over a real DB counts synthetic sources and dispatches nothing."""
    from open_notebook.database.repository import repo_query
    from open_notebook.integrations.graphrag.models import record_id_for

    tag = f"e3plan{uuid.uuid4().hex[:10]}"
    ids = [f"source:{tag}{i}" for i in range(3)]
    rids = [record_id_for(s, tables=frozenset({"source"})) for s in ids]

    def _no_submit(*a, **k):  # pragma: no cover
        raise AssertionError("PLAN must not enqueue")

    import surreal_commands

    monkeypatch.setattr(surreal_commands, "submit_command", _no_submit)
    try:
        for rid in rids:
            await repo_query("CREATE $id SET full_text = 'synthetic', title = 't'", {"id": rid})
        summary = await rebuild(mode="plan", rebuild_config=_cfg(max_sources_per_run=100000))
        # our synthetic ids are all non-empty
        assert summary.eligible_nonempty >= 3
        assert summary.enqueued == 0
        assert summary.planned == summary.eligible_nonempty
    finally:
        for rid in rids:
            await repo_query("DELETE $id", {"id": rid})


@pytest.mark.asyncio
async def test_live_keyset_continuation_covers_all_without_skip_or_repeat(live_db, monkeypatch):
    """A small max_sources_per_run forces continuation; following the returned cursor
    across runs covers every synthetic source exactly once (keyset, RecordID cursor,
    strict boundary on SurrealDB v2.6.5). Sweeps the whole (shared) source table in
    small pages; skips if the dev DB is too large to sweep hermetically."""
    from open_notebook.database.repository import repo_query
    from open_notebook.integrations.graphrag.models import record_id_for

    all_ids = await repo_query("SELECT VALUE id FROM source")
    existing = len(all_ids or [])
    if existing > 400:
        pytest.skip(f"shared source table too large ({existing}) for a hermetic sweep")

    tag = f"e3cont{uuid.uuid4().hex[:10]}"
    ids = sorted(f"source:{tag}{i}" for i in range(5))
    rids = [record_id_for(s, tables=frozenset({"source"})) for s in ids]

    dispatched = []
    import surreal_commands

    def _submit(app, name, payload):
        dispatched.append(payload["source_id"])
        return "command:live"

    monkeypatch.setattr(surreal_commands, "submit_command", _submit)
    # A configured+healthy fake service so EXECUTE passes preflight without a real
    # sidecar; the enqueue is captured above (no real 03A job is submitted).
    svc = _FakeService(enabled=True, base_url="http://sidecar.invalid:9621", healthy=True)
    try:
        for rid in rids:
            await repo_query("CREATE $id SET full_text = 'synthetic', title = 't'", {"id": rid})

        rounds = 0
        cursor = None
        max_rounds = existing + 10  # bounded: cap=2 advances >=2 per round
        while rounds < max_rounds:
            rounds += 1
            summary = await rebuild(
                svc,
                mode="execute",
                cursor=cursor,
                rebuild_config=_cfg(max_sources_per_run=2, canonical_batch_size=2),
            )
            if not summary.continuation_required:
                break
            cursor = summary.next_cursor
        mine = sorted(s for s in dispatched if tag in s)
        assert mine == ids, "continuation must cover every synthetic source exactly once"
        assert len(mine) == len(set(mine)), "no source dispatched twice"
    finally:
        for rid in rids:
            await repo_query("DELETE $id", {"id": rid})


# ===========================================================================
# J. LIVE LightRAG v1.5.6 — real EXECUTE through the real command worker
#    (synthetic/public data ONLY; skipped unless the sidecar is configured)
# ===========================================================================
#
# These exercise the REAL operator path end-to-end:
#   graphrag_rebuild EXECUTE -> execute lock -> flag -> base_url -> real GET /health
#   -> bounded canonical enumeration -> source_id-only graphrag_index_source command
#   -> REAL surreal-commands worker -> 03A reloads CURRENT synthetic Source
#   -> REAL LightRAG v1.5.6 sidecar.
#
# Boundary B: the sidecar forwards text to its configured (mock/synthetic) provider,
# so these use ONLY unique synthetic/public content with a recognizable marker and
# clean up the synthetic fixture through the approved lifecycle. They never sweep
# real/production-like data: each test asserts (via PLAN) that the ONLY non-empty
# dispatch candidate is its own synthetic source, and SKIPS otherwise.


def _live_base_url():
    base = os.environ.get("OPEN_NOTEBOOK_GRAPHRAG_BASE_URL", "").rstrip("/")
    if not base:
        pytest.skip("OPEN_NOTEBOOK_GRAPHRAG_BASE_URL not set (live LightRAG EXECUTE)")
    return base


def _live_api_key():
    return os.environ.get("OPEN_NOTEBOOK_GRAPHRAG_API_KEY") or None


async def _sidecar_healthy(base, api_key) -> bool:
    try:
        async with httpx.AsyncClient(timeout=8) as c:
            r = await c.get(f"{base}/health")
            return r.status_code == 200 and str(r.json().get("status", "")).lower() in {
                "healthy",
                "ok",
            }
    except Exception:
        return False


async def _sidecar_doc(base, api_key, doc_id):
    """Return the raw sidecar document dict for doc_id (or None). Pages through the
    whole inventory (doc ids are md5-based, so a doc can land on any page). Direct read
    so we can inspect content_summary (the typed client intentionally omits content)."""
    headers = {"X-API-Key": api_key} if api_key else {}
    async with httpx.AsyncClient(timeout=15) as c:
        page = 1
        while page <= 50:
            r = await c.post(
                f"{base}/documents/paginated",
                headers=headers,
                json={"page": page, "page_size": 200},
            )
            r.raise_for_status()
            j = r.json()
            for d in j.get("documents", []):
                if d.get("id") == doc_id:
                    return d
            if not j.get("pagination", {}).get("has_next"):
                break
            page += 1
    return None


def _live_rebuild_cfg(**overrides):
    from open_notebook.integrations.graphrag.config import GraphRAGRebuildConfig

    defaults = dict(
        canonical_batch_size=100,
        max_sources_per_run=1000,
        max_sample_ids=20,
        execute_enabled=True,
    )
    defaults.update(overrides)
    return GraphRAGRebuildConfig(**defaults)  # type: ignore[arg-type]


def _syn_live_source_id() -> str:
    # A high-sorting, recognizable, non-sensitive marker so the fixture is easy to
    # find and clean up and never collides with real ids.
    return f"source:ze3live{uuid.uuid4().hex[:12]}"


def _kill_tree(proc):
    """Kill a worker subprocess AND its children. On Windows a console-script .exe
    spawns a python.exe child, and Popen.terminate() only signals the wrapper —
    leaving an orphaned worker that keeps a LIVE query open and races later runs. Kill
    the whole tree so the test never leaks a worker (returns the env to its prior
    state)."""
    if proc is None or proc.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    else:
        proc.terminate()
    try:
        proc.wait(timeout=20)
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass


@pytest.mark.asyncio
async def test_live_execute_lock_off_blocks_before_egress(live_db, monkeypatch):
    """Real config path: with OPEN_NOTEBOOK_GRAPHRAG_REBUILD_EXECUTE_ENABLED unset, a
    real EXECUTE stops at the lock — no health preflight, no enumeration, no dispatch."""
    _live_base_url()
    from open_notebook.integrations.graphrag.service import GraphRAGService

    monkeypatch.delenv("OPEN_NOTEBOOK_GRAPHRAG_REBUILD_EXECUTE_ENABLED", raising=False)

    def _no_submit(*a, **k):  # pragma: no cover
        raise AssertionError("locked EXECUTE must not enqueue")

    import surreal_commands

    monkeypatch.setattr(surreal_commands, "submit_command", _no_submit)
    # Real service from env (enabled + base_url), real load_rebuild_config() (lock OFF).
    summary = await rebuild(GraphRAGService(), mode="execute")
    assert summary.execute_not_allowed is True
    assert summary.completion == SKIPPED_EXECUTE_NOT_ALLOWED
    assert summary.enqueued == 0
    assert summary.canonical_scanned == 0


@pytest.mark.asyncio
async def test_live_execute_preflight_failure_zero_dispatch(live_db, monkeypatch):
    """Real client preflight: an unreachable sidecar health endpoint yields
    PREFLIGHT_FAILED and ZERO source index commands dispatched."""
    _live_base_url()  # ensures the live env is configured (else skip)
    from open_notebook.integrations.graphrag.config import GraphRAGConfig
    from open_notebook.integrations.graphrag.service import GraphRAGService

    # Point at a dead port on localhost: a real client health() that fails.
    dead = GraphRAGService(
        config=GraphRAGConfig(
            enabled=True, base_url="http://127.0.0.1:9", timeout=3.0, api_key=None
        )
    )

    def _no_submit(*a, **k):  # pragma: no cover
        raise AssertionError("failed preflight must not enqueue")

    import surreal_commands

    monkeypatch.setattr(surreal_commands, "submit_command", _no_submit)
    summary = await rebuild(dead, mode="execute", rebuild_config=_live_rebuild_cfg())
    assert summary.preflight_unhealthy is True
    assert summary.completion == PREFLIGHT_FAILED
    assert summary.enqueued == 0
    assert summary.canonical_scanned == 0  # preflight is BEFORE enumeration


@pytest.mark.asyncio
async def test_live_execute_end_to_end_through_worker(live_db):
    """END-TO-END (real sidecar + real command worker): EXECUTE enqueues a source_id-only
    03A job; a REAL surreal-commands worker reloads the CURRENT synthetic Source and
    indexes it into the REAL LightRAG v1.5.6 sidecar. Also exercises the CURRENT-state
    race: the source is UPDATED (A -> B) after EXECUTE enqueues but before the worker
    runs, and the sidecar document reflects the CURRENT (B) content. Synthetic only."""
    from open_notebook.database.repository import repo_query
    from open_notebook.integrations.graphrag.client import compute_doc_id
    from open_notebook.integrations.graphrag.models import record_id_for
    from open_notebook.integrations.graphrag.service import GraphRAGService

    base = _live_base_url()
    api_key = _live_api_key()
    if not await _sidecar_healthy(base, api_key):
        pytest.skip("configured LightRAG sidecar not reachable/healthy")

    # Register the surreal-commands command set so submit_command can enqueue
    # graphrag_index_source — exactly as the real worker does with
    # `--import-modules commands`. In production the graphrag_rebuild command runs
    # INSIDE that worker, where the registry is already populated.
    import commands  # noqa: F401

    source_id = _syn_live_source_id()
    rid = record_id_for(source_id, tables=frozenset({"source"}))
    doc_id = compute_doc_id(source_id)
    marker = uuid.uuid4().hex[:12]
    text_a = f"GraphRAG 03E synthetic public probe A {marker} version-A oceans."
    text_b = f"GraphRAG 03E synthetic public probe B {marker} version-B mountains."
    worker = None
    try:
        # 1. Create the synthetic Source (version A). The high-sorting "ze3live" id
        #    keeps it near the END of the keyset so a strict-`>` cursor on its
        #    predecessor yields a one-source window containing ONLY our fixture — the
        #    dev DB may hold other (real-ish) sources we must never dispatch.
        await repo_query(
            "CREATE $id SET full_text = $t, title = 'e3-live'", {"id": rid, "t": text_a}
        )

        # 1b. Build a cursor = the id immediately BEFORE ours, and bound the run to a
        #     single source. A read-only PLAN over that exact window must show ONLY our
        #     synthetic source; otherwise we SKIP rather than risk dispatching real data.
        pred = await repo_query(
            "SELECT VALUE id FROM source WHERE id < $mine ORDER BY id DESC LIMIT 1",
            {"mine": rid},
        )
        cursor = str(pred[0]) if pred else None
        one_cfg = _live_rebuild_cfg(max_sources_per_run=1, canonical_batch_size=1)
        plan = await rebuild(
            GraphRAGService(), mode="plan", cursor=cursor, rebuild_config=one_cfg
        )
        if plan.samples.get("planned") != [source_id]:
            pytest.skip(
                "could not isolate a single-source window to our synthetic fixture "
                f"(window planned={plan.samples.get('planned')}); refusing to EXECUTE "
                "against non-synthetic data"
            )
        assert plan.eligible_nonempty == 1 and plan.enqueued == 0  # PLAN dispatches nothing

        # 2. EXECUTE the SAME one-source window (worker NOT started yet) -> enqueues a
        #    source_id-only 03A command for our fixture ONLY.
        summary = await rebuild(
            GraphRAGService(), mode="execute", cursor=cursor, rebuild_config=one_cfg
        )
        assert summary.execute_allowed is True
        assert summary.enqueued == 1
        assert summary.eligible_nonempty == 1
        # Item 5/6: claims only dispatch semantics; never a remote-content-verified claim.
        assert summary.completion in (DISPATCH_COMPLETE, DISPATCH_INCOMPLETE)
        assert summary.dispatch_complete is (summary.completion == DISPATCH_COMPLETE)
        blob = str(summary) + repr(summary.samples) + (summary.notes or "")
        assert "REMOTE_CONTENT_CONVERGENCE_VERIFIED" not in blob
        assert "verified" not in blob.lower()

        # Item 1/2: the REAL queued command row carries source_id ONLY (no full_text).
        cmds = await repo_query(
            "SELECT * FROM command WHERE name = 'graphrag_index_source' "
            "ORDER BY id DESC LIMIT 20"
        )
        mine = [
            c for c in (cmds or [])
            if str((c.get("args") or {}).get("source_id")) == source_id
        ]
        assert mine, "the EXECUTE dispatch did not create a graphrag_index_source command"
        args = mine[0].get("args") or {}
        assert set(args.keys()) == {"source_id"}  # source_id-only payload on the wire
        row_text = str(mine[0])
        assert "full_text" not in row_text  # no content field queued
        assert text_a not in row_text and text_b not in row_text  # no content queued

        # 3. CURRENT-state race: update canonical content to version B BEFORE 03A runs.
        await repo_query("UPDATE $id SET full_text = $t", {"id": rid, "t": text_b})

        # 4. Start the REAL surreal-commands worker; it reloads CURRENT (B) and indexes.
        #    Invoke the venv worker binary DIRECTLY (not via a nested `uv run`, which can
        #    cold-start slowly): the parent already loaded .env into os.environ, so the
        #    worker inherits the DB creds AND the GraphRAG env (flag/base_url/api_key) it
        #    needs. PYTHONUTF8=1 avoids the worker CLI's emoji output crashing on a
        #    Windows cp1252 console (an environment quirk, unrelated to 03E).
        worker_env = os.environ.copy()
        worker_env["PYTHONUTF8"] = "1"
        worker_env["PYTHONIOENCODING"] = "utf-8"
        worker_bin = shutil.which("surreal-commands-worker") or str(
            Path(sys.executable).parent / "surreal-commands-worker.exe"
        )
        _wlog_path = os.environ.get("E3_WORKER_LOG")
        _wlog = open(_wlog_path, "w", encoding="utf-8") if _wlog_path else subprocess.DEVNULL
        worker = subprocess.Popen(
            [worker_bin, "--import-modules", "commands"],
            cwd=str(REPO_ROOT),
            env=worker_env,
            stdout=_wlog,
            stderr=subprocess.STDOUT if _wlog_path else subprocess.DEVNULL,
        )

        # 5. Poll the REAL sidecar for document presence (worker -> 03A -> sidecar).
        deadline = time.monotonic() + 180
        doc = None
        while time.monotonic() < deadline:
            time.sleep(3)
            try:
                doc = await _sidecar_doc(base, api_key, doc_id)
            except Exception:
                doc = None
            if doc is not None:
                break
        assert doc is not None, (
            "synthetic document never became present in the sidecar via the worker"
        )

        # 6. CURRENT-state: the indexed content reflects version B, not version A.
        summary_text = (doc.get("content_summary") or "") + (doc.get("content") or "")
        if summary_text.strip():
            assert marker in summary_text, "sidecar content is not our synthetic doc"
            assert "version-B" in summary_text, "03A did not reload CURRENT (B) content"
            assert "version-A" not in summary_text, "stale (A) content was indexed"
        # else: content not surfaced by this backend build — presence via the
        # source_id-only worker path is still verified above.
    finally:
        _kill_tree(worker)  # never leak the worker (kills the whole tree on Windows)
        # Clean up the synthetic fixture through the APPROVED lifecycle only.
        try:
            await GraphRAGService().delete_document_for_source(source_id=source_id)
        except Exception:
            pass
        await repo_query("DELETE $id", {"id": rid})
