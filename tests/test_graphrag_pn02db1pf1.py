"""PN02D-B1-PF1 — provider-free preflight READINESS remediation tests.

Closes the real-Docker readiness race that blocked the B1 authorization: the provider-free
preflight probed ``version_signals`` immediately after ``docker run -d`` (before the
LightRAG app answered ``/health``), so against a real container the signals were
unavailable and Gate 0/Gate 1 failed closed. The fix adds a bounded, fail-closed
``wait_ready`` (reusing ``realsidecarpn02d.wait_healthy``) BETWEEN launch and
``version_signals`` in ``boot_preflight``.

Offline unit tests (deterministic injected clock, fake Docker) prove the ordering, the
delayed-startup wait, and the fail-closed timeout + cleanup. Docker-gated integration tests
(skipped when Docker/the pinned image is absent) prove the real container path now attests.
ZERO provider traffic — provider-free sidecars only (no secret, no OpenRouter/OpenAI).
"""

from __future__ import annotations

import os
import shutil

import graphrag_pn02db0cb_common as C
import pytest

from open_notebook.integrations.graphrag.eval.attestpn02d import (
    RealLightRAGPreflightAuthorization,
    mint_real_preflight_authorization,
)
from open_notebook.integrations.graphrag.eval.datasetpn02 import load_fixture
from open_notebook.integrations.graphrag.eval.realsidecarpn02d import (
    REAL_LIGHTRAG_IMAGE,
    DockerCLI,
    allocate_real_storage_root,
)
from open_notebook.integrations.graphrag.eval.runtimelivepn02d import (
    PreflightRunError,
    RealPN02RuntimeManager,
    RealProviderFreePreflightRunner,
)

# --------------------------------------------------------------------------- #
# Deterministic clock + docker fakes (offline)
# --------------------------------------------------------------------------- #

class FakeClock:
    """Monotonic clock whose ``sleep`` only advances virtual time (no real waiting)."""

    def __init__(self) -> None:
        self.t = 0.0

    def now(self) -> float:
        return self.t

    def sleep(self, seconds: float) -> None:
        self.t += seconds


class TransitioningDockerCLI(C.FakeDockerCLI):
    """A fake Docker whose /health is NOT ready for the first ``not_ready_polls`` health
    reads, then returns 200 — models a real container that becomes healthy only after
    ``docker run -d`` returns (the exact race PF1 fixes)."""

    def __init__(self, *, not_ready_polls: int = 3, **kw) -> None:
        super().__init__(**kw)
        self._not_ready_polls = not_ready_polls
        self.health_calls = 0

    def health(self, name: str):
        self.health_calls += 1
        if self.health_calls <= self._not_ready_polls:
            return (None, "", "")  # app not serving /health yet
        return (200, self.health_core, "healthy")


def _fast_runner(docker) -> RealProviderFreePreflightRunner:
    clk = FakeClock()
    return RealProviderFreePreflightRunner(
        docker=docker,
        readiness_timeout_s=1.0,
        readiness_poll_s=0.05,
        now=clk.now,
        sleep=clk.sleep,
    )


def _preflight_argv() -> list:
    return [
        "docker", "run", "-d", "--name", "pf-cell", "--network", "pf-net",
        "-e", "WORKSPACE=w", "-v", "/s:/app/data/rag_storage", REAL_LIGHTRAG_IMAGE,
    ]


def _manager(fx, preflight_runner, storage_alloc=None):
    return RealPN02RuntimeManager(
        fx=fx,
        process_controller=C.FakeProcessController(),
        health_prober=C.FakeHealthProber(),
        version_signal_reader=C.fake_version_signal_reader,
        port_allocator=C.DeterministicPortAllocator(),
        storage_dir_allocator=storage_alloc or C.fake_storage_dir_allocator,
        preflight_runner=preflight_runner,
    )


# --------------------------------------------------------------------------- #
# §11 — ordering: wait_ready BEFORE version_signals (deterministic)
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_readiness_runs_before_version_signals_in_boot_preflight():
    fx = load_fixture()
    runner = C.FakePreflightRunner()
    mgr = _manager(fx, runner)
    result = await mgr.boot_preflight(run_id="pf1-order")
    assert result.passed is True
    # For EVERY launched handle, wait_ready must appear before version_signals.
    for handle_id in {c.split(":", 1)[1] for c in runner.calls}:
        order = [c.split(":", 1)[0] for c in runner.calls if c.endswith(handle_id)]
        assert order == ["wait_ready", "version_signals"], (handle_id, runner.calls)
    # And it must be called once per notebook (3), each before its signals read.
    assert runner.calls.count("wait_ready:" + runner.calls[0].split(":", 1)[1]) == 1
    assert len([c for c in runner.calls if c.startswith("wait_ready:")]) == len(list(fx.notebooks))


# --------------------------------------------------------------------------- #
# §12 — delayed startup: wait_ready polls, then succeeds
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_delayed_startup_wait_ready_polls_then_succeeds():
    docker = TransitioningDockerCLI(not_ready_polls=3)
    runner = _fast_runner(docker)
    handle = await runner.launch(_preflight_argv())
    # Not-ready for the first 3 health reads, then healthy — wait_ready must NOT raise.
    await runner.wait_ready(handle)
    assert docker.health_calls >= 4  # polled past the not-ready window
    # After readiness, the three signals are available and attest.
    core, installed, label = await runner.version_signals(handle)
    assert (core, installed, label) == ("1.5.6", "1.5.6", "v1.5.6")


@pytest.mark.asyncio
async def test_version_signals_would_have_failed_without_readiness_wait():
    # Regression witness for the exact defect: probing signals on a still-starting container
    # (health not ready) fails; the readiness wait is what makes it succeed.
    docker = TransitioningDockerCLI(not_ready_polls=3)
    runner = _fast_runner(docker)
    handle = await runner.launch(_preflight_argv())
    with pytest.raises(PreflightRunError):
        await runner.version_signals(handle)  # immediate probe, no wait → unavailable


# --------------------------------------------------------------------------- #
# §13 — timeout fail-closed + cleanup
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_never_ready_wait_ready_fails_closed():
    docker = C.FakeDockerCLI(health_code=None)  # never healthy
    runner = _fast_runner(docker)
    handle = await runner.launch(_preflight_argv())
    with pytest.raises(PreflightRunError):
        await runner.wait_ready(handle)


@pytest.mark.asyncio
async def test_boot_preflight_never_ready_gate_fails_closed_and_cleans_up():
    fx = load_fixture()
    docker = C.FakeDockerCLI(health_code=None)  # containers run but never become healthy
    runner = _fast_runner(docker)
    mgr = _manager(fx, runner)
    result = await mgr.boot_preflight(run_id="pf1-timeout")
    # Fail-closed: no gate passes, no capability is mintable.
    assert result.gate0_passed is False and result.gate1_passed is False
    assert result.passed is False
    assert (
        mint_real_preflight_authorization(
            gate0_passed=result.gate0_passed, gate1_passed=result.gate1_passed,
            fixture_hash="h", run_id="pf1-timeout", runtime_count=result.runtime_count,
        )
        is None
    )
    # Cleanup still occurs: every launched container + its network is stopped/removed.
    assert result.torn_down is True
    assert docker.removed  # containers removed
    assert docker.nets_removed  # networks removed


@pytest.mark.asyncio
async def test_boot_preflight_delayed_startup_gates_pass():
    # With a readiness wait, a container that only becomes healthy AFTER run -d still passes.
    fx = load_fixture()
    docker = TransitioningDockerCLI(not_ready_polls=2)
    runner = _fast_runner(docker)
    mgr = _manager(fx, runner)
    result = await mgr.boot_preflight(run_id="pf1-delayed")
    assert result.gate0_passed is True and result.gate1_passed is True
    assert result.runtime_count == len(list(fx.notebooks))
    assert result.torn_down is True


# --------------------------------------------------------------------------- #
# Docker-gated real integration (skipped when Docker / the pinned image is absent)
# --------------------------------------------------------------------------- #

def _docker_ready() -> bool:
    try:
        d = DockerCLI()
        return bool(d.available()) and bool(d.image_present(REAL_LIGHTRAG_IMAGE))
    except Exception:
        return False


DOCKER = _docker_ready()
docker_only = pytest.mark.skipif(
    not DOCKER, reason="Docker daemon unreachable or pinned lightrag:v1.5.6 image absent"
)


@docker_only
@pytest.mark.asyncio
async def test_real_docker_wait_ready_then_version_attests():
    from open_notebook.integrations.graphrag.eval.attestpn02d import attest_version
    from open_notebook.integrations.graphrag.eval.realsidecarpn02d import (
        EXPECTED_IMAGE_VERSION_LABEL,
        assert_no_provider_binding_in_command,
        build_run_command,
        make_spec,
    )

    fx = load_fixture()
    nb = list(fx.notebooks)[0]
    base = os.environ.get("TEMP") or os.environ.get("TMP")
    storage = allocate_real_storage_root(base, "pf1diag")
    from open_notebook.integrations.graphrag.eval.manifestpn02 import workspace_id_for

    spec = make_spec(
        run_id="pf1-real", notebook_id=nb.notebook_id, notebook_record_id=nb.record_id,
        workspace_id=workspace_id_for(nb.record_id), storage_root=storage,
    )
    argv = build_run_command(spec)
    assert_no_provider_binding_in_command(argv)  # provider-free (no secret)
    runner = RealProviderFreePreflightRunner(docker=DockerCLI())
    handle = None
    try:
        handle = await runner.launch(argv)
        await runner.wait_ready(handle)  # real readiness wait
        core, installed, label = await runner.version_signals(handle)
        v = attest_version(
            health_core_version=core, import_version=installed,
            image_label_version=label, expected_label=EXPECTED_IMAGE_VERSION_LABEL,
        )
        assert v.attested is True
        assert core == "1.5.6" and installed == "1.5.6" and label == "v1.5.6"
    finally:
        if handle is not None:
            await runner.terminate(handle)
        shutil.rmtree(storage, ignore_errors=True)


@docker_only
@pytest.mark.asyncio
async def test_real_docker_boot_preflight_gates_pass_and_capability():
    fx = load_fixture()
    base = os.environ.get("TEMP") or os.environ.get("TMP")
    allocated: list = []

    def _alloc(name: str) -> str:
        root = allocate_real_storage_root(base, name)
        allocated.append(root)
        return root

    mgr = _manager(fx, RealProviderFreePreflightRunner(docker=DockerCLI()), storage_alloc=_alloc)
    try:
        result = await mgr.boot_preflight(run_id="pf1-realboot")
        assert result.gate0_passed is True and result.gate1_passed is True
        assert result.runtime_count == len(list(fx.notebooks)) == 3
        assert result.torn_down is True
        # Three-workspace isolation: distinct container identities per notebook.
        assert len(set(result.container_identities)) == 3
        # A genuine preflight capability is now mintable — but NO live provider run auth.
        cap = mint_real_preflight_authorization(
            gate0_passed=result.gate0_passed, gate1_passed=result.gate1_passed,
            fixture_hash="h", run_id="pf1-realboot", runtime_count=result.runtime_count,
        )
        assert isinstance(cap, RealLightRAGPreflightAuthorization)
        # No provider-bound boot, no live authorization here.
        from open_notebook.integrations.graphrag.eval.authlivepn02d import (
            PN02_PROVIDER_RUN_AUTHORIZED,
        )

        assert PN02_PROVIDER_RUN_AUTHORIZED is False
        # 0 residue: no owned pn02da containers/networks remain.
        d = DockerCLI()
        ps = d._run(["docker", "ps", "-a", "--filter", "name=pn02da_", "--format", "{{.Names}}"])
        assert ps.stdout.strip() == ""
    finally:
        for root in allocated:
            shutil.rmtree(root, ignore_errors=True)
