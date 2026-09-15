"""GraphRAG-PN02D-B1-EW4 — Boot-2 (provider-bound execution) readiness parity.

EXEC #4 was BLOCKED (INCOMPLETE, 0 provider traffic) at Boot-2 execution-runtime attestation:
``RealPN02RuntimeManager.boot_execution`` started each LightRAG execution container and
IMMEDIATELY probed ``/health`` with no readiness wait, so cold cells (EF1 repro: ~0.34s to
RUNNING, ~6.97s to healthy) were observed unhealthy and every cell failed attestation →
``route_table()`` raised ``RuntimeLifecycleError``. Boot 1 (``boot_preflight``) already waited
(PF1 ``wait_ready``); Boot 2 did not.

EW4 fix (offline, provider-free): insert a bounded, fail-closed readiness wait BEFORE the
existing canonical attestation probe in ``boot_execution``, reusing the PF1 readiness POLICY
(timeout/poll). Readiness success does NOT itself attest a cell — the canonical
health/version/workspace/endpoint/storage attestation stays load-bearing and all three cells
must still attest before ``route_table()`` is available.

These unit tests (no Docker / no provider) drive the readiness race deterministically with a
scripted health prober + injected clock/sleep, and cover the EW4 governance successor lifecycle
(EW4 is current; EW3/EW2/EW1/PF1/B1-R2 cannot substitute; State A tag absent → fail closed;
State B exact tag at HEAD → Git gate satisfiable but still no provider authorization). ZERO
provider traffic throughout.
"""

from __future__ import annotations

import socket
from dataclasses import dataclass, field
from typing import Dict, List, Optional

import graphrag_pn02db0cb_common as C
import pytest

from open_notebook.integrations.graphrag.eval.driver_live_pn02d import (
    materialize_live_provider_binding,
)
from open_notebook.integrations.graphrag.eval.runtimelivepn02d import (
    EXECUTION_READINESS_POLL_S,
    EXECUTION_READINESS_TIMEOUT_S,
    PREFLIGHT_READINESS_POLL_S,
    PREFLIGHT_READINESS_TIMEOUT_S,
    HealthObservation,
    RealPN02RuntimeManager,
    RuntimeLifecycleError,
)

_LOOPBACK = {"127.0.0.1", "::1", "localhost", "0.0.0.0"}


@pytest.fixture(autouse=True)
def _no_external_network(monkeypatch):
    """Fail immediately on any outbound connect to a non-loopback host (no provider traffic)."""
    real_connect = socket.socket.connect

    def guarded(self, address):
        host = address[0] if isinstance(address, (tuple, list)) else address
        if host not in _LOOPBACK:
            raise AssertionError(
                f"EW4 offline sentinel: blocked external network connect to {host!r}"
            )
        return real_connect(self, address)

    monkeypatch.setattr(socket.socket, "connect", guarded)


# --------------------------------------------------------------------------- #
# Deterministic readiness test doubles
# --------------------------------------------------------------------------- #

async def _noop_sleep(_delay: float) -> None:
    """A no-op async poll delay so the readiness race runs with no real wall-clock wait."""
    return None


class _FakeClock:
    """A deterministic monotonic clock that advances ``step`` seconds per read."""

    def __init__(self, step: float = 0.0) -> None:
        self.t = 0.0
        self.step = step

    def __call__(self) -> float:
        v = self.t
        self.t += self.step
        return v


class _ScriptedClock:
    """A deterministic clock returning successive values from ``values`` (last repeats).

    Read order in ``_wait_execution_runtime_ready`` is: one read to compute the deadline,
    then one read at the top of each poll iteration. This lets a test place the readiness
    clock EXACTLY at / past / just-before the hard deadline (B1EW4-R1-M1 boundary)."""

    def __init__(self, values):
        self._values = list(values)
        self._i = 0

    def __call__(self) -> float:
        v = self._values[min(self._i, len(self._values) - 1)]
        self._i += 1
        return v


@dataclass
class ScriptedHealthProber:
    """A per-port scripted ``/health`` prober driving the cold-start readiness race.

    ``plan`` maps a cell's port to a list of health booleans consumed one per probe (the last
    value repeats). Ports not in ``plan`` use ``default_healthy``. ``core_version`` is reported
    when healthy (a mismatching value drives the post-readiness version-attestation failure).
    Records probed base_urls so a test can assert only loopback endpoints were contacted.
    """

    plan: Dict[int, List[bool]] = field(default_factory=dict)
    default_healthy: bool = True
    core_version: str = "1.5.6"
    counts: Dict[int, int] = field(default_factory=dict)
    probed_base_urls: List[str] = field(default_factory=list)

    async def probe(self, *, base_url: str, host: str, port: int) -> HealthObservation:
        self.probed_base_urls.append(base_url)
        i = self.counts.get(port, 0)
        self.counts[port] = i + 1
        seq = self.plan.get(port)
        if seq is None:
            healthy = self.default_healthy
        else:
            healthy = seq[i] if i < len(seq) else seq[-1]
        return HealthObservation(
            reachable=healthy,
            healthy=healthy,
            core_version=self.core_version if healthy else "",
            workspace="",
        )


def _mgr(
    fx,
    *,
    prober,
    controller: Optional[C.FakeProcessController] = None,
    now=None,
    sleep=None,
    timeout_s: Optional[float] = None,
    poll_s: float = 0.0,
) -> RealPN02RuntimeManager:
    return RealPN02RuntimeManager(
        fx=fx,
        process_controller=controller if controller is not None else C.FakeProcessController(),
        health_prober=prober,
        version_signal_reader=C.fake_version_signal_reader,
        port_allocator=C.DeterministicPortAllocator(C.PORT_BASE),
        storage_dir_allocator=C.fake_storage_dir_allocator,
        preflight_runner=C.FakePreflightRunner(),
        readiness_now=now if now is not None else _FakeClock(step=0.0),
        readiness_sleep=sleep if sleep is not None else _noop_sleep,
        execution_readiness_timeout_s=(
            timeout_s if timeout_s is not None else EXECUTION_READINESS_TIMEOUT_S
        ),
        execution_readiness_poll_s=poll_s,
    )


async def _preflight_and_binding(mgr):
    """Run Boot 1 (fake preflight passes) and mint a test live capability + binding."""
    preflight = await mgr.boot_preflight()
    assert preflight.passed is True
    live_auth = C.mint_test_live_auth()
    materialized = materialize_live_provider_binding(
        live_auth, present_secret_envs=frozenset({"OPENROUTER_API_KEY"})
    )
    return live_auth, materialized


# --------------------------------------------------------------------------- #
# §6/§7 — Boot-2 readiness reuses the Boot-1 (PF1) policy; no retuning
# --------------------------------------------------------------------------- #

def test_boot2_readiness_policy_matches_boot1():
    # §6: the execution readiness policy REUSES the PF1 policy values (not new/retuned).
    assert EXECUTION_READINESS_TIMEOUT_S == PREFLIGHT_READINESS_TIMEOUT_S == 120.0
    assert EXECUTION_READINESS_POLL_S == PREFLIGHT_READINESS_POLL_S == 2.0


# --------------------------------------------------------------------------- #
# §18 — cold-start race: immediate unhealthy → healthy → attests
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_boot2_cold_start_race_waits_then_attests():
    fx = C.fixture()
    # Each cell is unhealthy for the first two probes, then healthy (the readiness loop
    # must poll past the cold start; the final attestation probe then sees it healthy).
    plan = {C.PORT_BASE + i: [False, False, True] for i in range(3)}
    prober = ScriptedHealthProber(plan=plan)
    mgr = _mgr(fx, prober=prober)
    live_auth, materialized = await _preflight_and_binding(mgr)

    attestations = await mgr.boot_execution(
        live_auth=live_auth, materialized_binding=materialized
    )
    assert len(attestations) == 3
    assert all(a.attested for a in attestations.values())
    # Route table available only after all three attested.
    routes = mgr.route_table()
    assert len(routes) == 3
    # Each cell was probed at least 3 readiness attempts + 1 attestation probe.
    for i in range(3):
        assert prober.counts[C.PORT_BASE + i] >= 4


# --------------------------------------------------------------------------- #
# §19 — all three cells eventually ready → route table only after all
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_boot2_three_cell_eventual_readiness():
    fx = C.fixture()
    # nb_a ready on poll 1, nb_b after 1 unhealthy, nb_c after 2 unhealthy.
    plan = {
        C.PORT_BASE + 0: [True],
        C.PORT_BASE + 1: [False, True],
        C.PORT_BASE + 2: [False, False, True],
    }
    prober = ScriptedHealthProber(plan=plan)
    mgr = _mgr(fx, prober=prober)
    live_auth, materialized = await _preflight_and_binding(mgr)

    attestations = await mgr.boot_execution(
        live_auth=live_auth, materialized_binding=materialized
    )
    assert len(attestations) == 3 and all(a.attested for a in attestations.values())
    assert len(mgr.route_table()) == 3


# --------------------------------------------------------------------------- #
# §20 — one cell never ready → fail closed; no partial route table
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_boot2_one_cell_never_ready_fails_closed():
    fx = C.fixture()
    # nb_a/nb_b healthy immediately; nb_c never healthy → readiness times out.
    plan = {
        C.PORT_BASE + 0: [True],
        C.PORT_BASE + 1: [True],
        C.PORT_BASE + 2: [False],
    }
    prober = ScriptedHealthProber(plan=plan)
    # Advancing clock so the bounded deadline is reached deterministically.
    mgr = _mgr(fx, prober=prober, now=_FakeClock(step=50.0))
    live_auth, materialized = await _preflight_and_binding(mgr)

    with pytest.raises(RuntimeLifecycleError) as ei:
        await mgr.boot_execution(live_auth=live_auth, materialized_binding=materialized)
    assert "did not become healthy" in str(ei.value)
    # No partial routing: route table remains unavailable.
    with pytest.raises(RuntimeLifecycleError):
        mgr.route_table()


# --------------------------------------------------------------------------- #
# B1EW4-R1-M2 — hard-deadline boundary: healthy at/after deadline → fail closed
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_boot2_healthy_exactly_at_deadline_rejected():
    # Clock reads: [deadline-calc=0 → deadline=10] [loop1 top=0 <10 → probe1 unhealthy]
    # [loop2 top=10 >=10 → FAIL CLOSED before probe2 (which WOULD be healthy)].
    fx = C.fixture()
    port = C.PORT_BASE
    prober = ScriptedHealthProber(plan={port: [False, True]})
    mgr = _mgr(fx, prober=prober, now=_ScriptedClock([0.0, 0.0, 10.0]), timeout_s=10.0)
    with pytest.raises(RuntimeLifecycleError) as ei:
        await mgr._wait_execution_runtime_ready(
            base_url=f"http://127.0.0.1:{port}", host="127.0.0.1", port=port
        )
    assert "did not become healthy" in str(ei.value)
    # The healthy observation (probe2) was NEVER consumed — rejected at the deadline.
    assert prober.counts[port] == 1


@pytest.mark.asyncio
async def test_boot2_healthy_after_deadline_rejected():
    # loop2 top = 11 > deadline 10 → fail closed even though probe2 would be healthy.
    fx = C.fixture()
    port = C.PORT_BASE
    prober = ScriptedHealthProber(plan={port: [False, True]})
    mgr = _mgr(fx, prober=prober, now=_ScriptedClock([0.0, 0.0, 11.0]), timeout_s=10.0)
    with pytest.raises(RuntimeLifecycleError):
        await mgr._wait_execution_runtime_ready(
            base_url=f"http://127.0.0.1:{port}", host="127.0.0.1", port=port
        )
    assert prober.counts[port] == 1


@pytest.mark.asyncio
async def test_boot2_healthy_just_before_deadline_accepted():
    # Non-regression counterpart: loop1 top = 9.999 < deadline 10 → probe1 healthy → accept.
    # Prevents "fixing" M1 by rejecting all final-window success.
    fx = C.fixture()
    port = C.PORT_BASE
    prober = ScriptedHealthProber(plan={port: [True]})
    mgr = _mgr(fx, prober=prober, now=_ScriptedClock([0.0, 9.999]), timeout_s=10.0)
    # Must NOT raise.
    await mgr._wait_execution_runtime_ready(
        base_url=f"http://127.0.0.1:{port}", host="127.0.0.1", port=port
    )
    assert prober.counts[port] == 1


# --------------------------------------------------------------------------- #
# §21 — version mismatch AFTER readiness → attestation still fails
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_boot2_ready_but_version_mismatch_not_attested():
    fx = C.fixture()
    # Healthy immediately, but the reported core version mismatches the frozen v1.5.6.
    prober = ScriptedHealthProber(default_healthy=True, core_version="9.9.9")
    mgr = _mgr(fx, prober=prober)
    live_auth, materialized = await _preflight_and_binding(mgr)

    attestations = await mgr.boot_execution(
        live_auth=live_auth, materialized_binding=materialized
    )
    assert not any(a.attested for a in attestations.values())
    assert all("version_not_attested" in a.failure_reasons for a in attestations.values())
    with pytest.raises(RuntimeLifecycleError):
        mgr.route_table()


# --------------------------------------------------------------------------- #
# §22 — health regression AFTER the readiness wait → not attested (final probe load-bearing)
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_boot2_health_regression_after_ready_not_attested():
    fx = C.fixture()
    # Readiness probe #1 sees healthy (returns), but the FINAL attestation probe #2 is
    # unhealthy — proving the final canonical health observation remains load-bearing.
    plan = {C.PORT_BASE + i: [True, False] for i in range(3)}
    prober = ScriptedHealthProber(plan=plan)
    mgr = _mgr(fx, prober=prober)
    live_auth, materialized = await _preflight_and_binding(mgr)

    attestations = await mgr.boot_execution(
        live_auth=live_auth, materialized_binding=materialized
    )
    assert not any(a.attested for a in attestations.values())
    assert all("unhealthy" in a.failure_reasons for a in attestations.values())
    with pytest.raises(RuntimeLifecycleError):
        mgr.route_table()


# --------------------------------------------------------------------------- #
# §23 — mid-boot readiness failure still cleans up started cells
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_boot2_mid_boot_failure_cleans_up_started_cells():
    fx = C.fixture()
    controller = C.FakeProcessController()
    # First cell ready; second cell never ready → boot fails after two cells started.
    plan = {
        C.PORT_BASE + 0: [True],
        C.PORT_BASE + 1: [False],
        C.PORT_BASE + 2: [True],
    }
    prober = ScriptedHealthProber(plan=plan)
    mgr = _mgr(fx, prober=prober, controller=controller, now=_FakeClock(step=50.0))
    live_auth, materialized = await _preflight_and_binding(mgr)

    with pytest.raises(RuntimeLifecycleError):
        await mgr.boot_execution(live_auth=live_auth, materialized_binding=materialized)
    # Two execution cells were started before the failure; canonical cleanup removes both.
    assert len(controller.started) == 2
    await mgr.cleanup()
    assert len(controller.terminated) == 2
    assert {h for h in controller.terminated} == {
        s.identifier for s in _started_handles(controller)
    }


def _started_handles(controller: C.FakeProcessController):
    # FakeProcessController issues ids as f"fakecell-{n}-{cell_id}"; reconstruct from specs.
    class _H:
        def __init__(self, identifier):
            self.identifier = identifier

    return [
        _H(f"fakecell-{i + 1}-{s.cell_id}") for i, s in enumerate(controller.started)
    ]


# --------------------------------------------------------------------------- #
# §24 — readiness contacts ONLY local loopback health endpoints (no provider)
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_boot2_readiness_only_contacts_loopback():
    fx = C.fixture()
    plan = {C.PORT_BASE + i: [False, True] for i in range(3)}
    prober = ScriptedHealthProber(plan=plan)
    mgr = _mgr(fx, prober=prober)
    live_auth, materialized = await _preflight_and_binding(mgr)

    await mgr.boot_execution(live_auth=live_auth, materialized_binding=materialized)
    assert prober.probed_base_urls  # readiness actually probed
    for url in prober.probed_base_urls:
        assert url.startswith("http://127.0.0.1:")


# --------------------------------------------------------------------------- #
# Non-regression: healthy-from-start still attests in ONE probe-round path
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_boot2_healthy_immediately_still_attests():
    fx = C.fixture()
    bundle = C.build_live_seams(fx)  # FakeHealthProber(healthy=True)
    mgr = RealPN02RuntimeManager(
        fx=fx,
        process_controller=bundle.controller,
        health_prober=bundle.prober,
        version_signal_reader=C.fake_version_signal_reader,
        port_allocator=C.DeterministicPortAllocator(C.PORT_BASE),
        storage_dir_allocator=C.fake_storage_dir_allocator,
        preflight_runner=bundle.preflight_runner,
    )
    live_auth, materialized = await _preflight_and_binding(mgr)
    attestations = await mgr.boot_execution(
        live_auth=live_auth, materialized_binding=materialized
    )
    assert all(a.attested for a in attestations.values())
    assert len(mgr.route_table()) == 3


# --------------------------------------------------------------------------- #
# EW4 governance: EW4 is the current successor; historical tags cannot substitute (§30/§31)
# --------------------------------------------------------------------------- #

def test_governance_current_is_ew4_successor():
    from open_notebook.integrations.graphrag.eval import authb1r2pn02d as B
    from open_notebook.integrations.graphrag.eval.authmintlivepn02d import (
        EXPECTED_B1_R2_CHECKPOINT_TAG,
        EXPECTED_EW1_CHECKPOINT_TAG,
        EXPECTED_EW2_CHECKPOINT_TAG,
        EXPECTED_EW3_CHECKPOINT_TAG,
        EXPECTED_EW4_CHECKPOINT_TAG,
        EXPECTED_PF1_CHECKPOINT_TAG,
        current_approved_b1_r2_checkpoint,
    )

    assert current_approved_b1_r2_checkpoint() == EXPECTED_EW4_CHECKPOINT_TAG
    assert B.B1_R2_EXPECTED_CHECKPOINT_TAG == EXPECTED_EW4_CHECKPOINT_TAG
    assert EXPECTED_EW4_CHECKPOINT_TAG == "graphrag-pn02db1ew4-boot2-readiness-approved"
    # EW3/EW2/EW1/PF1/B1-R2 are retained HISTORICAL identities, all distinct from EW4.
    assert len({
        EXPECTED_EW4_CHECKPOINT_TAG,
        EXPECTED_EW3_CHECKPOINT_TAG,
        EXPECTED_EW2_CHECKPOINT_TAG,
        EXPECTED_EW1_CHECKPOINT_TAG,
        EXPECTED_PF1_CHECKPOINT_TAG,
        EXPECTED_B1_R2_CHECKPOINT_TAG,
    }) == 6


def _ew4_future_grant(**overrides):
    from open_notebook.integrations.graphrag.eval import authb1r2pn02d as B

    kwargs = dict(approved_git_commit="e4e4e4e4" + "0" * 32)
    kwargs.update(overrides)
    return B.build_b1_r2_operator_grant(**kwargs)


def test_ew3_ew2_ew1_pf1_b1r2_cannot_substitute_for_ew4():
    # §31: no historical identity (EW3/EW2/EW1/PF1/B1-R2) or arbitrary tag can substitute for
    # the EW4 successor — naming any as the approved-expected identity is refused.
    from open_notebook.integrations.graphrag.eval.authmintlivepn02d import (
        EXPECTED_B1_R2_CHECKPOINT_TAG,
        EXPECTED_EW1_CHECKPOINT_TAG,
        EXPECTED_EW2_CHECKPOINT_TAG,
        EXPECTED_EW3_CHECKPOINT_TAG,
        EXPECTED_EW4_CHECKPOINT_TAG,
        EXPECTED_PF1_CHECKPOINT_TAG,
        RealTrustedB1R2Reader,
        verify_b1_r2_checkpoint,
    )

    future = "e4e4e4e4" + "0" * 32
    for substitute in (
        "graphrag-arbitrary-unrelated-tag",
        EXPECTED_EW3_CHECKPOINT_TAG,
        EXPECTED_EW2_CHECKPOINT_TAG,
        EXPECTED_EW1_CHECKPOINT_TAG,
        EXPECTED_PF1_CHECKPOINT_TAG,
        EXPECTED_B1_R2_CHECKPOINT_TAG,
    ):
        reasons = verify_b1_r2_checkpoint(
            reader=RealTrustedB1R2Reader(),
            operator_grant=_ew4_future_grant(approved_git_commit=future),
            approved_expected_checkpoint=substitute,
            git_baseline=C.clean_git_baseline(
                commit=future, tag=EXPECTED_EW4_CHECKPOINT_TAG
            ),
        )
        assert reasons, f"{substitute} must not satisfy the EW4 checkpoint"


# --------------------------------------------------------------------------- #
# EW4 checkpoint-lifecycle — lifecycle-aware from day one (§32/§33)
# --------------------------------------------------------------------------- #

def test_ew4_successor_tag_git_state_is_lifecycle_valid():
    # §33/§4: the CURRENT approved EW4 identity's real-Git state is LIFECYCLE-AWARE — never a
    # permanent real-tag-absence assumption (that anti-pattern flips the instant the checkpoint
    # tag is created; see checkpoint attempt #1). STATE A (pre-checkpoint): the successor tag is
    # absent → empty peel. STATE B (post-checkpoint): the EXACT tag is present, peels to a valid
    # 40-hex commit == the authorized HEAD. Mirrors the lifecycle-aware EW3 successor-tag test.
    from open_notebook.integrations.graphrag.eval.authmintlivepn02d import (
        EXPECTED_EW4_CHECKPOINT_TAG,
        RealTrustedB1R2Reader,
        current_approved_b1_r2_checkpoint,
    )

    approved = current_approved_b1_r2_checkpoint()
    assert approved == EXPECTED_EW4_CHECKPOINT_TAG
    obs = RealTrustedB1R2Reader().observe(approved)
    if not obs.observed_tag_exists:
        # STATE A — pre-checkpoint: successor tag absent → Git prerequisite not satisfied.
        assert obs.observed_tag_peel == ""
    else:
        # STATE B — post-checkpoint: EXACT tag present, valid peeled commit, at HEAD.
        assert obs.checkpoint_tag == approved
        assert len(obs.observed_tag_peel) == 40 and all(
            c in "0123456789abcdef" for c in obs.observed_tag_peel
        )
        assert obs.observed_tag_peel == obs.observed_head  # tag at authorized HEAD


def test_ew4_state_a_mint_fails_closed_when_tag_absent():
    # §32/§33 State A (deterministic): a synthetic approved identity whose tag is absent from
    # Git fails closed with `b1_r2_tag_not_observed_in_git` (real reader retained; governance
    # patched only) — not dependent on the developer's Git currently lacking the EW4 tag.
    from open_notebook.integrations.graphrag.eval.attestpn02d import (
        mint_real_preflight_authorization,
    )
    from open_notebook.integrations.graphrag.eval.authmintlivepn02d import (
        B1R2CheckpointError,
        mint_live_provider_run_authorization,
    )

    fixture_hash = C.verify_fixture_hash()[1]
    grant = C.frozen_test_grant(b1_r2_checkpoint=C.TEST_B1R2_TAG)
    preflight = mint_real_preflight_authorization(
        gate0_passed=True, gate1_passed=True, fixture_hash=fixture_hash,
        run_id=C.TEST_RUN_ID, runtime_count=3,
    )
    with C.governance_expects_tag(C.TEST_B1R2_TAG):
        with pytest.raises(B1R2CheckpointError) as ei:
            mint_live_provider_run_authorization(
                operator_grant=grant, real_preflight_auth=preflight,
                git_baseline_attestation=C.clean_git_baseline(),
                observed_fixture_hash=fixture_hash,
            )
    assert "b1_r2_tag_not_observed_in_git" in str(ei.value)


def test_ew4_state_b_git_gate_satisfiable_but_no_provider_auth():
    # §32 State B: a synthetic reader observing the EXACT EW4 tag peeling to the authorized
    # HEAD makes the Git checkpoint prerequisite satisfiable — but that is ONLY the
    # control-plane Git gate; the provider-run governance flag stays NO. NO real tag created.
    from open_notebook.integrations.graphrag.eval.authlivepn02d import (
        PN02_PROVIDER_RUN_AUTHORIZED,
    )
    from open_notebook.integrations.graphrag.eval.authmintlivepn02d import (
        EXPECTED_EW4_CHECKPOINT_TAG,
        verify_b1_r2_checkpoint,
    )

    future = "e4e4e4e4" + "0" * 32
    reasons = verify_b1_r2_checkpoint(
        reader=C.b1r2_reader_ok(tag=EXPECTED_EW4_CHECKPOINT_TAG, peel=future, head=future),
        operator_grant=_ew4_future_grant(approved_git_commit=future),
        approved_expected_checkpoint=EXPECTED_EW4_CHECKPOINT_TAG,
        git_baseline=C.clean_git_baseline(commit=future, tag=EXPECTED_EW4_CHECKPOINT_TAG),
    )
    assert reasons == []  # Git gate satisfiable (§32 State B)
    assert PN02_PROVIDER_RUN_AUTHORIZED is False  # but provider run NOT authorized


def test_ew4_checkpoint_identity_separate_from_operator_grant():
    # §32: the EW4 checkpoint identity is governance-owned; a grant carrying the right B1-R2
    # identity is NOT itself provider authorization (that needs the full mint + governance).
    from open_notebook.integrations.graphrag.eval.authmintlivepn02d import (
        EXPECTED_EW4_CHECKPOINT_TAG,
    )

    grant = _ew4_future_grant()
    assert grant.b1_r2_checkpoint == EXPECTED_EW4_CHECKPOINT_TAG
    # The grant is ordinary operator INPUT — it mints nothing on its own.
    from open_notebook.integrations.graphrag.eval.authlivepn02d import (
        PN02_PROVIDER_RUN_AUTHORIZED,
    )

    assert PN02_PROVIDER_RUN_AUTHORIZED is False
