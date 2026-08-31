"""GraphRAG-08C preflight-safety + sidecar-readiness observability tests.

Offline / eval-only (no provider, no live sidecar, no DEV/HOLDOUT). Covers task
§14-§24: the normal-DB baseline hard gate, rejection of null-equality, content-safe
SIDECAR_START diagnostics + reason codes, pre-isolation failure telemetry, cleanup on
startup timeout, authorization-label-is-metadata-only, and the methodology freeze.
"""

from __future__ import annotations

import asyncio
import inspect
import json

import pytest

from open_notebook.integrations.graphrag.eval import dataset08 as d
from open_notebook.integrations.graphrag.eval import precheck08 as pc
from open_notebook.integrations.graphrag.eval import preflight08 as pf
from open_notebook.integrations.graphrag.eval import sidecar_diag08 as sd

FIXTURE_HASH = "a58a68535c345e18f0263904f818e4e2068a164056408665d8bb9233eceb143d"


def _baseline(
    *, count, ns="open_notebook", db="open_notebook", model_present=True,
    identity=True, count_ok=None, model_ok=True,
):
    count_ok = (count is not None) if count_ok is None else count_ok
    return pf.NormalDbBaseline(
        identity_readable=identity,
        count_readable=count_ok,
        model_baseline_readable=model_ok,
        namespace=ns,
        database=db,
        source_count=count,
        default_embedding_model_present=model_present,
        reason_code=pf._reason_for(identity, count_ok, model_ok),
    )


# ---- §15 normal DB readable -> YES -----------------------------------------

def test_normal_db_readable_unchanged_yes():
    before = _baseline(count=7)
    after = _baseline(count=7)
    assert before.readable and after.readable
    assert pf.compare_normal_db(before, after) == pf.UNCHANGED_YES


def test_readable_baseline_passes_gate():
    pf.require_readable_baseline(_baseline(count=0))  # concrete 0 is a valid count


# ---- §16 before unreadable -> blocks before sidecar -------------------------

def test_require_readable_baseline_raises_on_unreadable():
    with pytest.raises(pf.NormalDbBaselineError):
        pf.require_readable_baseline(_baseline(count=None, count_ok=False))
    with pytest.raises(pf.NormalDbBaselineError):
        pf.require_readable_baseline(None)


def test_full_run_blocks_before_sidecar_when_baseline_unreadable(tmp_path, monkeypatch):
    called = {"sidecar": 0}

    async def fake_unreadable():
        return _baseline(count=None, count_ok=False)

    def fake_start():
        called["sidecar"] += 1

    monkeypatch.setattr(pf, "read_normal_db_baseline", fake_unreadable)
    monkeypatch.setattr(pc, "start_sidecar", fake_start)

    st = asyncio.run(pc.run_full_benchmark(artifact_dir=tmp_path))
    assert called["sidecar"] == 0            # FULL_EXECUTION_STARTED = NO
    assert st.preflight_blocked is True
    assert st.state == "PREFLIGHT_FAIL"
    assert st.normal_db_unchanged == pf.UNCHANGED_NOT_PROVEN
    assert st.failure_stage == "PREFLIGHT"
    # a durable content-free preflight artifact was written before any sidecar action
    art = json.loads((tmp_path / "preflight_failure.json").read_text())
    assert art["isolation_entered"] is False
    assert art["dev_executed"] == 0 and art["holdout_executed"] == 0
    assert art["value_decision_made"] is False


# ---- §17 before valid, after unreadable -> NOT_PROVEN -----------------------

def test_valid_before_unreadable_after_not_proven():
    before = _baseline(count=3)
    after = _baseline(count=None, count_ok=False)
    assert pf.compare_normal_db(before, after) == pf.UNCHANGED_NOT_PROVEN


def test_identity_or_model_drift_not_proven():
    assert pf.compare_normal_db(_baseline(count=3), _baseline(count=3, db="other")) == pf.UNCHANGED_NOT_PROVEN
    assert pf.compare_normal_db(
        _baseline(count=3, model_present=True), _baseline(count=3, model_present=False)
    ) == pf.UNCHANGED_NOT_PROVEN


# ---- §18 null equality must NEVER be YES ------------------------------------

def test_null_equality_rejected():
    assert pf.compare_normal_db(None, None) == pf.UNCHANGED_NOT_PROVEN
    both_null = _baseline(count=None, count_ok=False)
    assert pf.compare_normal_db(both_null, both_null) == pf.UNCHANGED_NOT_PROVEN
    # even if a stray baseline claimed readable with a None count, concreteness guard holds
    sneaky = pf.NormalDbBaseline(
        identity_readable=True, count_readable=True, model_baseline_readable=True,
        namespace="n", database="db", source_count=None,
        default_embedding_model_present=True, reason_code=pf.NormalDbReasonCode.READABLE,
    )
    assert pf.compare_normal_db(sneaky, sneaky) == pf.UNCHANGED_NOT_PROVEN


# ---- §19 sidecar health timeout --------------------------------------------

def test_sidecar_health_timeout_diagnostic():
    obs = sd.SidecarObservation(
        container_created=True, container_running=True,
        container_health_state="starting", container_exit_code=None,
        container_restart_count=0, port_open=True, health_http_reachable=True,
        health_http_status_class="2XX", healthy=False, timeout_reached=True,
    )
    diag = sd.build_sidecar_diagnostic(obs, timeout_seconds=120.0, elapsed_seconds=121.0)
    assert diag.failure_reason_code in (
        sd.SIDECAR_CONTAINER_RUNNING_NOT_HEALTHY, sd.SIDECAR_HEALTH_TIMEOUT
    )
    assert diag.healthy is False and diag.timeout_reached is True
    assert diag.elapsed_bucket == "GT_120"

    # no docker healthcheck ("none") + timeout -> HEALTH_TIMEOUT
    obs2 = sd.SidecarObservation(
        container_created=True, container_running=True, container_health_state="none",
        container_exit_code=None, container_restart_count=0, port_open=True,
        health_http_reachable=True, health_http_status_class=None,
        healthy=False, timeout_reached=True,
    )
    assert sd.classify_sidecar_start(obs2) == sd.SIDECAR_HEALTH_TIMEOUT


# ---- §20 sidecar exit distinguished from running-but-not-healthy ------------

def test_sidecar_exit_distinct_from_running_not_healthy():
    exited = sd.SidecarObservation(
        container_created=True, container_running=False,
        container_health_state=None, container_exit_code=1, container_restart_count=3,
        port_open=None, health_http_reachable=None, health_http_status_class=None,
        healthy=False, timeout_reached=False,
    )
    assert sd.classify_sidecar_start(exited) == sd.SIDECAR_CONTAINER_EXITED

    running_unhealthy = sd.SidecarObservation(
        container_created=True, container_running=True,
        container_health_state="unhealthy", container_exit_code=None,
        container_restart_count=0, port_open=True, health_http_reachable=True,
        health_http_status_class="2XX", healthy=False, timeout_reached=True,
    )
    assert sd.classify_sidecar_start(running_unhealthy) == sd.SIDECAR_CONTAINER_RUNNING_NOT_HEALTHY
    assert sd.classify_sidecar_start(exited) != sd.classify_sidecar_start(running_unhealthy)


def test_other_sidecar_reason_codes():
    def obs(**kw):
        base = dict(
            container_created=True, container_running=True, container_health_state="none",
            container_exit_code=None, container_restart_count=0, port_open=True,
            health_http_reachable=True, health_http_status_class="2XX",
            healthy=False, timeout_reached=False,
        )
        base.update(kw)
        return sd.SidecarObservation(**base)

    assert sd.classify_sidecar_start(obs(healthy=True)) == sd.SIDECAR_HEALTHY
    assert sd.classify_sidecar_start(obs(container_created=None)) == sd.SIDECAR_INSPECT_UNAVAILABLE
    assert sd.classify_sidecar_start(obs(container_created=False)) == sd.SIDECAR_CONTAINER_NOT_CREATED
    assert sd.classify_sidecar_start(obs(port_open=False)) == sd.SIDECAR_PORT_NOT_OPEN
    assert sd.classify_sidecar_start(obs(health_http_reachable=False)) == sd.SIDECAR_HEALTH_HTTP_UNREACHABLE
    assert sd.classify_sidecar_start(obs(health_http_status_class="5XX")) == sd.SIDECAR_HEALTH_NON_SUCCESS
    assert sd.classify_sidecar_start(obs(timeout_reached=False)) == sd.SIDECAR_START_UNKNOWN


# ---- §21 raw log / health-body containment ---------------------------------

def test_raw_sidecar_data_never_leaks_into_diagnostic():
    secrets = [
        "sk-live-SECRET-123",
        "Authorization: Bearer TOKENXYZ",
        "user@example.com",
        "Helix Robotics Talos-9 controller",  # source/query-like
        "password=hunter2",
    ]
    # A secret-laden "docker inspect" line: parser reads only 4 positional tokens and
    # coerces types, so none of this survives into the observation.
    raw_line = " | ".join(secrets)
    obs = sd.parse_inspect_line(raw_line)
    obs = sd.with_health(
        obs, port_open=True, health_reachable=True,
        health_status_code=503, healthy=False, timeout_reached=True,
    )
    diag = sd.build_sidecar_diagnostic(obs, timeout_seconds=120.0, elapsed_seconds=99.0)
    blob = json.dumps(diag.as_dict())
    for s in secrets:
        assert s not in blob, s
    assert "Bearer" not in blob and "sk-live" not in blob and "hunter2" not in blob
    # health body/status text never stored — only a coarse status class
    assert diag.health_http_status_class == "5XX"


def test_inspect_line_none_is_inspect_unavailable():
    obs = sd.parse_inspect_line(None)
    assert obs.container_created is None
    assert sd.classify_sidecar_start(obs) == sd.SIDECAR_INSPECT_UNAVAILABLE


def test_parse_inspect_line_well_formed():
    obs = sd.parse_inspect_line("true|0|healthy|2")
    assert obs.container_created is True and obs.container_running is True
    assert obs.container_health_state == "healthy" and obs.container_exit_code == 0
    assert obs.container_restart_count == 2


# ---- §22 failure BEFORE isolation still produces durable telemetry ----------

def _readable_baseline_patch(monkeypatch):
    async def fake_readable():
        return _baseline(count=1)
    monkeypatch.setattr(pf, "read_normal_db_baseline", fake_readable)


def test_sidecar_failure_before_isolation_writes_telemetry(tmp_path, monkeypatch):
    _readable_baseline_patch(monkeypatch)
    events = {"start": 0, "stop": 0}

    def fake_start():
        events["start"] += 1

    async def fake_health(config, *, timeout_s=120.0):
        raise RuntimeError("sidecar not healthy within 120s: <discarded>")

    async def fake_gather(config, *, timeout_seconds, elapsed_seconds):
        obs = sd.SidecarObservation(
            container_created=True, container_running=True,
            container_health_state="starting", container_exit_code=None,
            container_restart_count=0, port_open=True, health_http_reachable=True,
            health_http_status_class="2XX", healthy=False, timeout_reached=True,
        )
        return sd.build_sidecar_diagnostic(
            obs, timeout_seconds=timeout_seconds, elapsed_seconds=elapsed_seconds
        )

    def fake_stop():
        events["stop"] += 1

    monkeypatch.setattr(pc, "start_sidecar", fake_start)
    monkeypatch.setattr(pc, "await_sidecar_health", fake_health)
    monkeypatch.setattr(pc, "gather_sidecar_diagnostic", fake_gather)
    monkeypatch.setattr(pc, "stop_sidecar", fake_stop)
    monkeypatch.setattr(pc, "sidecar_running", lambda: False)

    st = asyncio.run(
        pc.run_full_benchmark(authorization_label="REAUTHORIZATION_4", artifact_dir=tmp_path)
    )
    assert st.isolation_entered is False
    assert st.state == "FAILED"
    assert st.sidecar_diagnostic is not None
    art = json.loads((tmp_path / "preflight_failure.json").read_text())
    assert art["isolation_entered"] is False
    assert art["failure_stage"] == "SIDECAR_START"
    assert art["dev_executed"] == 0 and art["holdout_executed"] == 0
    assert art["value_decision_made"] is False
    assert art["authorization_label"] == "REAUTHORIZATION_4"
    assert art["sidecar_diagnostic"]["failure_reason_code"] in (
        sd.SIDECAR_CONTAINER_RUNNING_NOT_HEALTHY, sd.SIDECAR_HEALTH_TIMEOUT
    )
    # telemetry is content-free
    blob = json.dumps(art)
    assert "Helix" not in blob and "Bearer" not in blob


# ---- §23 cleanup runs on startup timeout -----------------------------------

def test_sidecar_cleanup_runs_on_timeout(tmp_path, monkeypatch):
    _readable_baseline_patch(monkeypatch)
    events = {"stop": 0}

    async def fake_health(config, *, timeout_s=120.0):
        raise RuntimeError("timeout")

    async def fake_gather(config, *, timeout_seconds, elapsed_seconds):
        return sd.build_sidecar_diagnostic(
            sd.parse_inspect_line("true|0|starting|0"),
            timeout_seconds=timeout_seconds, elapsed_seconds=elapsed_seconds,
        )

    monkeypatch.setattr(pc, "start_sidecar", lambda: None)
    monkeypatch.setattr(pc, "await_sidecar_health", fake_health)
    monkeypatch.setattr(pc, "gather_sidecar_diagnostic", fake_gather)
    monkeypatch.setattr(pc, "stop_sidecar", lambda: events.__setitem__("stop", events["stop"] + 1))
    monkeypatch.setattr(pc, "sidecar_running", lambda: False)

    st = asyncio.run(pc.run_full_benchmark(artifact_dir=tmp_path))
    assert events["stop"] >= 1                 # compose down still ran
    assert st.sidecar_stopped is True          # SIDECAR_RUNNING_AFTER = NO
    assert st.created_ids == []                # no benchmark DB mutation


# ---- §14 authorization label is metadata-only ------------------------------

def test_authorization_label_is_metadata_only():
    st_a = pc.PrecheckState(authorization_label="REAUTHORIZATION_3")
    st_b = pc.PrecheckState(authorization_label="REAUTHORIZATION_9")
    art_a = pc.build_preisolation_failure_artifact(
        st_a, failure_stage="SIDECAR_START", failure_reason_code="X"
    )
    art_b = pc.build_preisolation_failure_artifact(
        st_b, failure_stage="SIDECAR_START", failure_reason_code="X"
    )
    diffs = {k for k in art_a if art_a[k] != art_b.get(k)}
    assert diffs == {"authorization_label"}   # ONLY the provenance label differs
    assert art_a["authorization_label"] == "REAUTHORIZATION_3"


def test_authorization_label_default_is_unspecified():
    assert pc.PrecheckState().authorization_label == "UNSPECIFIED"
    sig = inspect.signature(pc.run_full_benchmark)
    assert sig.parameters["authorization_label"].default == "UNSPECIFIED"


# ---- §24 methodology freeze -------------------------------------------------

def test_methodology_freeze_unchanged():
    from open_notebook.integrations.graphrag.eval import index_retry08 as ir
    from open_notebook.integrations.graphrag.eval.runner08 import EvalRunConfig08

    # fixture frozen
    ok, h = d.verify_integrity()
    assert ok and h == FIXTURE_HASH
    bench = d.load_benchmark08()
    assert len(bench.sources) == 75 and len(bench.queries) == 60

    # retry policy: 2 attempts per source (unchanged)
    assert EvalRunConfig08().max_index_attempts_per_source == 2

    # sidecar health timeout: 120s (unchanged / frozen — task §9)
    assert inspect.signature(pc.await_sidecar_health).parameters["timeout_s"].default == 120.0

    # transient allowlist unchanged (spot check the frozen decision)
    assert ir.is_transient_reason("Error code: 429") is True
    assert ir.is_transient_reason("authentication failed") is False
