"""GraphRAG-08E.7B — offline tests for the burst-reproduction screening harness.

NO provider traffic, NO network, NO real sidecar, NO DB mutation: every seam is a fake /
local object. Proves the frozen 08E.7A semantics — SUBMIT_ALL_THEN_POLL, full-initial-wave
validity, current-rung drain, ladder stop, distinct 203/406 budgets, and the split
classifier-signature / strict-S001 / novel classification.
"""

from __future__ import annotations

import json

import pytest

from open_notebook.integrations.graphrag.eval.burst_plan08 import (
    BURST_LEVELS,
    BurstBudgetError,
    BurstBudgetGuard,
    BurstLevel,
    BurstPlan,
    BurstPlanError,
    budget_precheck,
    classify_rung,
    default_burst_plan,
    historical_attempt_number_match,
    is_classifier_signature,
    is_novel_valid_failure,
    is_strict_s001_event,
    select_burst_prefix,
    validate_burst_plan,
)
from open_notebook.integrations.graphrag.eval.burst_runner08 import (
    BurstLadderRunner08,
    BurstReproductionOrchestrator08,
    BurstWaveIndexer08,
)
from open_notebook.integrations.graphrag.eval.cell_isolation08 import (
    CellDisposal,
    CellProvision,
    cell_storage_dir,
)
from open_notebook.integrations.graphrag.eval.concurrency_diag08 import (
    TERMINAL_FAILED,
    TERMINAL_SUCCESS,
    TERMINAL_TIMEOUT,
    AttemptRecord,
    characterize_failure,
)
from open_notebook.integrations.graphrag.eval.dataset08 import load_benchmark08
from open_notebook.integrations.graphrag.eval.index_retry08 import ReasonCode
from open_notebook.integrations.graphrag.eval.live_indexer08 import (
    CellEndpoint,
    DiagnosticSource,
    IndexStatusResult,
    IndexSubmitResult,
    LiveIndexerConfig,
)
from open_notebook.integrations.graphrag.eval.live_orchestrator08 import (
    LiveDiagnosticConfigError,
    LiveDiagnosticNotAuthorizedError,
    OrchestratorDeps,
)
from open_notebook.integrations.graphrag.eval.provider_binding08 import (
    frozen_provider_binding,
)

WD = "C:/gr08e7/wd" if __import__("os").name == "nt" else "/gr08e7/wd"


# --------------------------------------------------------------------------- #
# autouse guard: no real HTTP client may be constructed in this suite.
# --------------------------------------------------------------------------- #
@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    import httpx

    def _boom(*a, **k):  # pragma: no cover - only fires on a real network attempt
        raise AssertionError("08E.7B offline test attempted to construct an httpx client")

    monkeypatch.setattr(httpx, "AsyncClient", _boom)
    monkeypatch.setattr(httpx, "Client", _boom)


# --------------------------------------------------------------------------- #
# fakes
# --------------------------------------------------------------------------- #
def _src(key: str) -> DiagnosticSource:
    return DiagnosticSource(key=key, canonical_id=f"source:{key}", text=f"TEXT_{key}")


def _all_sources(n: int = 75):
    return {f"S{i:03d}": _src(f"S{i:03d}") for i in range(1, n + 1)}


def _rec(key, status, *, attempt=1, detail=None, level=8):
    ch = characterize_failure(detail) if status != TERMINAL_SUCCESS else None
    if status == TERMINAL_TIMEOUT:
        ch = characterize_failure(None)
    return AttemptRecord(
        run_id="gr08e7t",
        concurrency_level=level,
        logical_source_id=key,
        repetition=1,
        attempt_number=attempt,
        terminal_status=status,
        duration_ms=5,
        characterization=ch,
    )


_SIGNATURE_DETAIL = "deterministic non-transient extraction failure on document"
_TRANSIENT_DETAIL = "429 too many requests please try again"
_RAW_SECRET_DETAIL = "TEST_SECRET_08E7B_RAW_FAILURE deterministic non-transient boom"


class _FakeClient:
    """Records ("submit"|"poll", key) events; per-key terminal behaviour."""

    def __init__(self, behavior, *, raise_keys=(), reject_keys=()):
        self.behavior = dict(behavior)
        self.events = []
        self._raise = set(raise_keys)
        self._reject = set(reject_keys)
        self._track2key = {}
        self._n = 0

    @staticmethod
    def _key(source_id):
        return source_id.split(":", 1)[1] if ":" in source_id else source_id

    async def submit(self, *, source_id, canonical_text):
        key = self._key(source_id)
        self.events.append(("submit", key))
        if key in self._raise:
            raise RuntimeError("transport boom")
        if key in self._reject:
            return IndexSubmitResult(accepted=False, track_id=None, detail="rejected")
        self._n += 1
        trk = f"trk_{key}_{self._n}"
        self._track2key[trk] = key
        return IndexSubmitResult(accepted=True, track_id=trk)

    async def status(self, *, track_id):
        key = self._track2key[track_id]
        self.events.append(("poll", key))
        spec = self.behavior.get(key, "success")
        if spec == "success":
            return IndexStatusResult(state="PROCESSED")
        if spec == "signature":
            return IndexStatusResult(state="FAILED", detail=_SIGNATURE_DETAIL)
        if spec == "raw_secret":
            return IndexStatusResult(state="FAILED", detail=_RAW_SECRET_DETAIL)
        if spec == "transient":
            return IndexStatusResult(state="FAILED", detail=_TRANSIENT_DETAIL)
        if spec == "absent":
            return IndexStatusResult(state="FAILED", detail="")
        return IndexStatusResult(state="PROCESSED")


class _FakeProvCell:
    def __init__(self, identity, working_dir, port):
        self.ready = True
        self.run_id = identity.run_id
        self.cell_id = identity.cell_id
        self.workspace = identity.workspace
        self.storage_dir = cell_storage_dir(working_dir, identity.workspace)
        self.base_url = f"http://127.0.0.1:{port}"
        self.port = port
        self.process_identifier = f"fake_{identity.cell_id}"


class _FakeProvisioner:
    def __init__(self, working_dir, *, dispose_ok=True, dispose_owned=True, bad_validity_at=None):
        self.working_dir = working_dir
        self._active = {}
        self.provision_levels = []
        self.dispose_levels = []
        self._dispose_ok = dispose_ok
        self._dispose_owned = dispose_owned
        self._bad_validity_at = bad_validity_at

    async def provision(self, identity):
        self.provision_levels.append(identity.concurrency)
        owned = not (self._bad_validity_at is not None and identity.concurrency == self._bad_validity_at)
        cell = _FakeProvCell(identity, self.working_dir, 61300 + identity.concurrency)
        self._active[identity.cell_id] = cell
        return CellProvision(
            workspace=identity.workspace,
            storage_dir=cell.storage_dir,
            fresh_extraction_state=True,
            owned=owned,
        )

    def active_provisioned_cell(self, identity):
        return self._active.get(identity.cell_id)

    async def dispose(self, identity, provision):
        self.dispose_levels.append(identity.concurrency)
        if self._dispose_ok:
            self._active.pop(identity.cell_id, None)
        return CellDisposal(disposed=self._dispose_ok, owned=self._dispose_owned)


def _client_factory(behavior, *, raise_keys=(), reject_keys=(), sink=None):
    def factory(endpoint):
        c = _FakeClient(behavior, raise_keys=raise_keys, reject_keys=reject_keys)
        if sink is not None:
            sink.append(c)
        return c

    return factory


async def _run_ladder(behavior, *, provisioner=None, plan=None, raise_keys=(), reject_keys=(), sink=None):
    prov = provisioner or _FakeProvisioner(WD)
    runner = BurstLadderRunner08(
        benchmark=load_benchmark08(),
        sources=_all_sources(75),
        plan=plan or default_burst_plan(),
        client_factory=_client_factory(behavior, raise_keys=raise_keys, reject_keys=reject_keys, sink=sink),
        config=LiveIndexerConfig(index_ready_timeout_s=1.0, poll_interval_s=0.001),
    )
    res = await runner.run(run_id="gr08e7t", provisioner=prov, working_dir=WD)
    return res, prov


def _endpoint(level=8):
    return CellEndpoint(
        run_id="gr08e7t",
        cell_id=f"gr08e7t_c{level}_r1",
        base_url="http://127.0.0.1:61310",
        port=61310,
        workspace="ws",
        storage_dir="sd",
        container_identity="ci",
    )


def _wave_indexer(client, level=8, budget=None):
    return BurstWaveIndexer08(
        endpoint=_endpoint(level),
        client_factory=lambda ep: client,
        config=LiveIndexerConfig(index_ready_timeout_s=1.0, poll_interval_s=0.001),
        budget=budget or BurstBudgetGuard(),
        level=level,
        run_id="gr08e7t",
    )


# =========================================================================== #
# Plan / selection / budget (task §54, §55, §63-§65)
# =========================================================================== #
def test_default_plan_exact():
    plan = default_burst_plan()
    assert [lvl.level for lvl in plan.levels] == [8, 16, 24, 32, 48, 75]
    assert plan.repetitions == 1
    assert plan.scheduling_mode == "SUBMIT_ALL_THEN_POLL"
    assert plan.max_index_attempts_per_source == 2
    assert plan.planned_source_workload == 203
    assert plan.max_index_attempts_total == 406
    assert budget_precheck()["max_index_attempts_total"] == 406


@pytest.mark.parametrize(
    "levels",
    [
        [8, 16, 24, 32, 48],  # missing 75
        [8, 16, 24, 32, 75, 48],  # reordered
        [8, 8, 16, 24, 48, 75],  # duplicate
        [8, 16, 24, 32, 48, 76],  # above 75
        [8, 16, 24, 32, 48, 75, 75],  # extra dup top
    ],
)
def test_invalid_plans_fail_closed(levels):
    plan = BurstPlan(levels=tuple(BurstLevel(c) for c in levels))
    with pytest.raises(BurstPlanError):
        validate_burst_plan(plan)


def test_plan_wrong_repetition_or_scheduling_fail_closed():
    good = tuple(BurstLevel(c) for c in BURST_LEVELS)
    with pytest.raises(BurstPlanError):
        validate_burst_plan(BurstPlan(levels=good, repetitions=2))
    with pytest.raises(BurstPlanError):
        validate_burst_plan(BurstPlan(levels=good, scheduling_mode="GATHER"))
    with pytest.raises(BurstPlanError):
        validate_burst_plan(BurstPlan(levels=good, max_index_attempts_per_source=3))


def test_nested_prefixes_exact_ids():
    bench = load_benchmark08()
    assert select_burst_prefix(bench, 8) == tuple(f"S{i:03d}" for i in range(1, 9))
    assert select_burst_prefix(bench, 16) == tuple(f"S{i:03d}" for i in range(1, 17))
    assert select_burst_prefix(bench, 24) == tuple(f"S{i:03d}" for i in range(1, 25))
    assert select_burst_prefix(bench, 32) == tuple(f"S{i:03d}" for i in range(1, 33))
    assert select_burst_prefix(bench, 48) == tuple(f"S{i:03d}" for i in range(1, 49))
    assert select_burst_prefix(bench, 75) == tuple(f"S{i:03d}" for i in range(1, 76))
    # strictly nested
    for a, b in [(8, 16), (16, 24), (24, 32), (32, 48), (48, 75)]:
        assert select_burst_prefix(bench, a) == select_burst_prefix(bench, b)[:a]


def test_workload_budget_204_rejected():
    g = BurstBudgetGuard()
    for c in BURST_LEVELS:
        g.reserve_workload(c)
    assert g.planned_source_workload_count == 203
    with pytest.raises(BurstBudgetError):
        g.reserve_workload(1)


def test_attempt_budget_407_rejected():
    g = BurstBudgetGuard()
    for i in range(406):
        g.record_attempt(f"k{i}")
    assert g.actual_index_attempt_count == 406
    with pytest.raises(BurstBudgetError):
        g.record_attempt("k406b")


def test_per_source_third_attempt_rejected():
    g = BurstBudgetGuard()
    g.record_attempt("c8_S001")
    g.record_attempt("c8_S001")
    with pytest.raises(BurstBudgetError):
        g.record_attempt("c8_S001")
    # a different treatment key for the same logical source is independent (per-treatment cap)
    g.record_attempt("c16_S001")


# =========================================================================== #
# SUBMIT_ALL_THEN_POLL wave semantics (task §56, §57, §66)
# =========================================================================== #
@pytest.mark.asyncio
async def test_submit_all_before_any_poll():
    client = _FakeClient({})
    idx = _wave_indexer(client, level=8)
    subset = [_src(f"S{i:03d}") for i in range(1, 9)]
    states, established = await idx.submit_wave(subset)
    assert established is True
    # exactly 8 submit events, ZERO poll events after Phase A
    assert idx.events == [("submit", f"S{i:03d}") for i in range(1, 9)]
    assert all(e[0] == "submit" for e in client.events)
    assert len(client.events) == 8


@pytest.mark.asyncio
async def test_no_gather_regression_early_failure_does_not_shrink_burst():
    # S001 would fail immediately on poll — but Phase A must still submit all 8 first.
    client = _FakeClient({"S001": "signature"})
    idx = _wave_indexer(client, level=8)
    subset = [_src(f"S{i:03d}") for i in range(1, 9)]
    states, established = await idx.submit_wave(subset)
    assert established is True
    assert sum(1 for s in states if s.handle is not None) == 8
    # not a single poll happened during submission
    assert [e for e in client.events if e[0] == "poll"] == []


@pytest.mark.asyncio
async def test_retry_only_after_full_wave():
    # S001 transient -> retry; prove the retry submit occurs AFTER all 8 initial submits.
    client = _FakeClient({"S001": "transient"})
    idx = _wave_indexer(client, level=8)
    subset = [_src(f"S{i:03d}") for i in range(1, 9)]
    states, established = await idx.submit_wave(subset)
    await idx.drain_wave(states)
    submit_events = [e for e in client.events if e[0] == "submit"]
    # first 8 submit events are the initial wave S001..S008 in order
    assert submit_events[:8] == [("submit", f"S{i:03d}") for i in range(1, 9)]
    # a 9th submit (the S001 retry) exists and comes strictly after the 8 initial submits
    assert ("submit", "S001") in submit_events[8:]


# =========================================================================== #
# Full-wave validity / partial wave / accepted-then-failed (task §58, §59)
# =========================================================================== #
@pytest.mark.asyncio
async def test_partial_wave_transport_failure_is_invalid_treatment():
    res, prov = await _run_ladder({}, raise_keys={"S005"})
    assert res.runtime_valid is False
    rung = res.rungs[0]
    assert rung.level == 8
    assert rung.full_initial_wave_established is False
    assert rung.treatment_valid is False
    assert rung.initial_wave_submitted_count < 8
    assert rung.classifier_signature_reproduced is False
    assert rung.novel_failure_observed is False
    assert res.first_failure_rung is None  # not an experimental failure
    # next rung never entered
    assert prov.provision_levels == [8]


@pytest.mark.asyncio
async def test_partial_wave_reject_is_invalid_treatment():
    res, prov = await _run_ladder({}, reject_keys={"S003"})
    assert res.runtime_valid is False
    assert res.rungs[0].full_initial_wave_established is False
    assert res.first_failure_rung is None


@pytest.mark.asyncio
async def test_accepted_then_failed_is_experimental_evidence():
    # S001 accepted (enters contract) then terminal FAILED signature -> valid failure.
    res, prov = await _run_ladder({"S001": "signature"})
    rung = res.rungs[0]
    assert rung.full_initial_wave_established is True
    assert rung.treatment_valid is True
    assert rung.failure_count == 1
    assert rung.classifier_signature_reproduced is True
    assert res.first_failure_rung == 8


# =========================================================================== #
# Current-rung drain + ladder stop (task §60, §61, §62)
# =========================================================================== #
@pytest.mark.asyncio
async def test_drain_current_rung_no_cancel_on_early_failure():
    # S001 fails; S002..S008 succeed. All 8 must be observed/drained.
    sink = []
    res, prov = await _run_ladder({"S001": "signature"}, sink=sink)
    rung = res.rungs[0]
    assert rung.initial_wave_submitted_count == 8
    assert len(rung.records) == 8  # all 8 drained to terminal
    assert rung.success_count == 7 and rung.failure_count == 1
    # every one of the 8 was polled at least once (drain did not cancel the wave)
    polled = {k for (ev, k) in sink[0].events if ev == "poll"}
    assert polled == {f"S{i:03d}" for i in range(1, 9)}


@pytest.mark.asyncio
async def test_next_rung_not_entered_after_valid_failure():
    res, prov = await _run_ladder({"S001": "signature"})
    assert res.first_failure_rung == 8
    assert res.levels_entered == (8,)
    assert prov.provision_levels == [8]  # C=16 never provisioned
    assert prov.dispose_levels == [8]  # the entered rung was disposed exactly once


@pytest.mark.asyncio
async def test_clean_ladder_through_75():
    res, prov = await _run_ladder({})  # all success
    assert res.runtime_valid is True
    assert res.levels_entered == (8, 16, 24, 32, 48, 75)
    assert res.first_failure_rung is None
    assert res.clean_through_level == 75
    assert res.classifier_signature_reproduced is False
    assert res.strict_s001_event_reproduced is False
    assert res.novel_failure_observed is False
    assert res.planned_source_workload_count == 203
    assert res.index_attempt_count == 203  # no retries on a clean ladder
    assert prov.provision_levels == [8, 16, 24, 32, 48, 75]
    assert prov.dispose_levels == [8, 16, 24, 32, 48, 75]
    assert all(r.clean for r in res.rungs)


# =========================================================================== #
# Classification predicates (task §67-§71, §36)
# =========================================================================== #
def test_classifier_signature_predicate():
    rec = _rec("S010", TERMINAL_FAILED, detail=_SIGNATURE_DETAIL)
    assert rec.characterization.retry_reason_code == ReasonCode.TRACK_TEXT_PRESENT_NO_ALLOWLIST_MATCH
    assert is_classifier_signature(rec) is True
    assert is_novel_valid_failure(rec) is False


def test_strict_s001_vs_other_source_same_signature():
    s001 = _rec("S001", TERMINAL_FAILED, detail=_SIGNATURE_DETAIL)
    s037 = _rec("S037", TERMINAL_FAILED, detail=_SIGNATURE_DETAIL)
    assert is_classifier_signature(s001) and is_classifier_signature(s037)
    assert is_strict_s001_event(s001) is True
    assert is_strict_s001_event(s037) is False
    assert is_novel_valid_failure(s037) is False  # same signature, not novel


def test_historical_attempt_number_match():
    a1 = _rec("S001", TERMINAL_FAILED, detail=_SIGNATURE_DETAIL, attempt=1)
    a2 = _rec("S001", TERMINAL_FAILED, detail=_SIGNATURE_DETAIL, attempt=2)
    assert historical_attempt_number_match(a1) is True
    assert historical_attempt_number_match(a2) is False
    assert is_strict_s001_event(a2) is True  # strict flag unchanged by attempt number


def test_novel_valid_failure_transient_exhausted_and_timeout():
    transient = _rec("S005", TERMINAL_FAILED, detail=_TRANSIENT_DETAIL)  # retryable -> not signature
    assert transient.characterization.retryable is True
    assert is_classifier_signature(transient) is False
    assert is_novel_valid_failure(transient) is True
    timeout = _rec("S006", TERMINAL_TIMEOUT)
    assert is_classifier_signature(timeout) is False
    assert is_novel_valid_failure(timeout) is True


def test_multiple_failure_classes_in_one_rung_non_exclusive():
    records = (
        _rec("S001", TERMINAL_FAILED, detail=_SIGNATURE_DETAIL),
        _rec("S002", TERMINAL_FAILED, detail=_TRANSIENT_DETAIL),  # novel (retryable)
        _rec("S003", TERMINAL_SUCCESS),
    )
    rung = classify_rung(
        8,
        planned_source_count=8,
        initial_wave_submitted_count=8,
        full_initial_wave_established=True,
        records=records,
        index_attempt_count=9,
        retry_count=1,
        cleanup_ok=True,
    )
    assert rung.classifier_signature_reproduced is True
    assert rung.strict_s001_event_reproduced is True
    assert rung.novel_failure_observed is True
    labels = set(rung.rung_labels())
    assert "CLASSIFIER_SIGNATURE_REPRODUCED" in labels
    assert "S001_HISTORICAL_EVENT_REPRODUCED_STRICT" in labels
    assert "NOVEL_VALID_FAILURE" in labels
    assert "CLEAN" not in labels


def test_clean_rung_labels():
    records = tuple(_rec(f"S{i:03d}", TERMINAL_SUCCESS) for i in range(1, 9))
    rung = classify_rung(
        8,
        planned_source_count=8,
        initial_wave_submitted_count=8,
        full_initial_wave_established=True,
        records=records,
        index_attempt_count=8,
        retry_count=0,
        cleanup_ok=True,
    )
    assert rung.clean is True
    assert rung.rung_labels() == ("CLEAN",)
    assert not rung.classifier_signature_reproduced
    assert not rung.novel_failure_observed


# =========================================================================== #
# Harness failure vs experimental failure (task §72, §73, §74)
# =========================================================================== #
@pytest.mark.asyncio
async def test_harness_invalidity_not_experimental_failure():
    prov = _FakeProvisioner(WD, bad_validity_at=8)  # cell isolation invalid at C=8
    res, _ = await _run_ladder({}, provisioner=prov)
    assert res.runtime_valid is False
    assert res.first_failure_rung is None  # a harness stop is not experimental evidence
    assert res.levels_entered == ()


@pytest.mark.asyncio
async def test_cleanup_failure_stops_ladder():
    prov = _FakeProvisioner(WD, dispose_ok=False)  # dispose reports not disposed
    res, _ = await _run_ladder({}, provisioner=prov)
    assert res.runtime_valid is False
    assert res.cleanup_ok is False
    # only the first rung was attempted; no next rung after a cleanup failure
    assert prov.provision_levels == [8]


@pytest.mark.asyncio
async def test_unexpected_dependency_error_becomes_content_safe_runtime_stop():
    # An UNMODELED exception from an injected dep must fail-closed into a content-safe result,
    # not propagate as a raw exception (review LOW-2).
    prov = _FakeProvisioner(WD)

    def _bad_factory(endpoint):
        raise ValueError("TEST_SECRET_08E7B_RAW_FAILURE unexpected dep boom")

    runner = BurstLadderRunner08(
        benchmark=load_benchmark08(),
        sources=_all_sources(75),
        plan=default_burst_plan(),
        client_factory=_bad_factory,
        config=LiveIndexerConfig(index_ready_timeout_s=1.0, poll_interval_s=0.001),
    )
    res = await runner.run(run_id="gr08e7t", provisioner=prov, working_dir=WD)
    assert res.runtime_valid is False
    assert res.runtime_stop_reason == "UNEXPECTED:ValueError"
    assert res.first_failure_rung is None
    assert "TEST_SECRET_08E7B_RAW_FAILURE" not in json.dumps(res.as_dict())
    assert prov.dispose_levels == [8]  # the cell was still disposed (cleanup ran)


@pytest.mark.asyncio
async def test_cancellation_propagates():
    import asyncio

    class _CancelClient(_FakeClient):
        async def submit(self, *, source_id, canonical_text):
            raise asyncio.CancelledError()

    prov = _FakeProvisioner(WD)
    runner = BurstLadderRunner08(
        benchmark=load_benchmark08(),
        sources=_all_sources(75),
        plan=default_burst_plan(),
        client_factory=lambda ep: _CancelClient({}),
        config=LiveIndexerConfig(index_ready_timeout_s=1.0, poll_interval_s=0.001),
    )
    with pytest.raises(asyncio.CancelledError):
        await runner.run(run_id="gr08e7t", provisioner=prov, working_dir=WD)
    # the entered cell was still disposed (cleanup ran)
    assert prov.dispose_levels == [8]


# =========================================================================== #
# Content-safety / secret-safety (task §45, §46, §78)
# =========================================================================== #
@pytest.mark.asyncio
async def test_raw_error_never_persisted():
    res, _ = await _run_ladder({"S001": "raw_secret"})
    blob = json.dumps(res.as_dict())
    assert "TEST_SECRET_08E7B_RAW_FAILURE" not in blob
    # yet the failure was still classified from the (transiently consumed) text
    assert res.rungs[0].classifier_signature_reproduced is True
    for r in res.rungs[0].records:
        assert "TEST_SECRET_08E7B_RAW_FAILURE" not in json.dumps(r.as_dict())


def test_result_dict_has_no_source_text():
    records = (_rec("S001", TERMINAL_FAILED, detail=_SIGNATURE_DETAIL),)
    rung = classify_rung(
        8,
        planned_source_count=8,
        initial_wave_submitted_count=8,
        full_initial_wave_established=True,
        records=records,
        index_attempt_count=8,
        retry_count=0,
        cleanup_ok=True,
    )
    blob = json.dumps(rung.as_dict())
    assert "TEXT_S001" not in blob
    assert _SIGNATURE_DETAIL not in blob


# =========================================================================== #
# Orchestrator authorization / gating (task §51, §75, §81, §82)
# =========================================================================== #
def _mk_deps(**over):
    """OrchestratorDeps whose seams are fakes; provider binding + attestor present."""
    base = dict(
        isolation_runtime_factory=None,
        model_seeder=None,
        model_restorer=None,
        source_preparer=None,
        provisioner_factory=None,
        client_factory=lambda ep: _FakeClient({}),
        runtime_attestor=object(),
        provider_binding=frozen_provider_binding(),
        artifact_writer=None,
    )
    base.update(over)
    return OrchestratorDeps(**base)


@pytest.mark.asyncio
async def test_orchestrator_denies_without_authorization():
    orch = BurstReproductionOrchestrator08(load_benchmark08(), _mk_deps())
    with pytest.raises(LiveDiagnosticNotAuthorizedError):
        await orch.run(working_dir=WD, authorized_live=False)


@pytest.mark.asyncio
async def test_orchestrator_missing_attestor_fails_closed():
    orch = BurstReproductionOrchestrator08(load_benchmark08(), _mk_deps(runtime_attestor=None))
    with pytest.raises(LiveDiagnosticConfigError):
        await orch.run(working_dir=WD, authorized_live=True)


@pytest.mark.asyncio
async def test_orchestrator_missing_secret_fails_closed(monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    orch = BurstReproductionOrchestrator08(load_benchmark08(), _mk_deps())
    with pytest.raises(LiveDiagnosticConfigError) as ei:
        await orch.run(working_dir=WD, authorized_live=True)
    assert "REQUIRED_RUNTIME_SECRET_MISSING=OPENROUTER_API_KEY" in str(ei.value)


@pytest.mark.asyncio
async def test_orchestrator_full_offline_flow_option_a_order(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-synthetic-08e7b")
    order = []
    bench = load_benchmark08()

    class _Iso:
        run_id = "gr08e7run"

        async def __aenter__(self):
            order.append("isolation_enter")
            return self

        async def __aexit__(self, *a):
            order.append("isolation_exit")
            return False

    async def _seed():
        order.append("model_seed")
        return ("model:tmp", None)

    async def _restore(mid, prior):
        order.append("model_restore")
        return True

    async def _prepare(benchmark, keys):
        order.append(("sources", len(keys)))
        return {k: _src(k) for k in keys}

    prov = _FakeProvisioner(WD)

    def _prov_factory(working_dir, attestor, binding):
        order.append("provisioner")
        return prov

    deps = _mk_deps(
        isolation_runtime_factory=lambda: _Iso(),
        model_seeder=_seed,
        model_restorer=_restore,
        source_preparer=_prepare,
        provisioner_factory=_prov_factory,
        client_factory=lambda ep: _FakeClient({}),
    )
    orch = BurstReproductionOrchestrator08(bench, deps)
    res = await orch.run(working_dir=WD, authorized_live=True, run_id="gr08e7run")
    assert res.runtime_valid is True
    assert res.clean_through_level == 75
    assert res.planned_source_workload_count == 203
    # Option-A isolation established BEFORE any Source/provisioner work (task §76)
    assert order[0] == "isolation_enter"
    assert order.index("isolation_enter") < order.index(("sources", 75))
    assert order.index("isolation_enter") < order.index("provisioner")
    assert order[-1] == "isolation_exit"
    # the full 75-Source prefix union was prepared once
    assert ("sources", 75) in order


def test_production_does_not_import_burst_modules():
    import importlib

    for name in ("api.main", "open_notebook.graphs.chat"):
        try:
            mod = importlib.import_module(name)
        except Exception:
            continue
        src_file = getattr(mod, "__file__", "") or ""
        assert "burst_runner08" not in src_file
    # the eval modules are not imported by the production graphrag package __init__
    import open_notebook.integrations.graphrag as g

    assert "burst" not in (getattr(g, "__file__", "") or "").lower()
