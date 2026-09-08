"""GraphRAG-PN02D-A offline tests (task §33).

EVALUATION-ONLY. These tests never boot a real Docker container: a configurable
``FakeDocker`` command runner simulates the docker CLI so Gate 0 / Gate 1 logic,
attestation, egress detection, routing rejection, cleanup ownership, the fixture
hash gate, and the fail-closed no-index authorization can all be exercised
deterministically. The one authorized provider-free real-Docker integration run is
executed separately as part of the PN02D-A gate itself.
"""

from __future__ import annotations

from typing import Dict, List, Optional, Sequence

import pytest

from open_notebook.integrations.graphrag.eval import attestpn02d as at
from open_notebook.integrations.graphrag.eval import datasetpn02 as ds
from open_notebook.integrations.graphrag.eval import egressguardpn02d as eg
from open_notebook.integrations.graphrag.eval import preflightpn02d as pf
from open_notebook.integrations.graphrag.eval import realsidecarpn02d as rs

# Real socket rows captured from the actual v1.5.6 container (clean: LISTEN only).
CLEAN_PROC_NET = (
    "  sl  local_address rem_address   st tx_queue rx_queue tr tm->when retrnsmt\n"
    "   0: 00000000:2595 00000000:0000 0A 00000000:00000000 00:00000000 00000000\n"
    "   1: 0B00007F:9C57 00000000:0000 0A 00000000:00000000 00:00000000 00000000\n"
)
# Dirty: an ESTABLISHED socket to 8.8.8.8:443 (external provider-like egress).
DIRTY_PROC_NET = CLEAN_PROC_NET + (
    "   2: 0100007F:C001 08080808:01BB 01 00000000:00000000 00:00000000 00000000\n"
)


class FakeDocker:
    """A configurable fake ``CommandRunner`` mimicking the docker CLI (offline)."""

    def __init__(
        self,
        *,
        available: bool = True,
        image_present: bool = True,
        image_version: str = "v1.5.6",
        network_internal: bool = True,
        healthy: bool = True,
        health_status_code: int = 200,
        core_version: str = "1.5.6",
        import_version: str = "1.5.6",
        proc_net: str = CLEAN_PROC_NET,
        workspace_override: Optional[str] = None,
        mount_override: Optional[str] = None,
    ) -> None:
        self.available = available
        self.image_present = image_present
        self.image_version = image_version
        self.network_internal = network_internal
        self.healthy = healthy
        self.health_status_code = health_status_code
        self.core_version = core_version
        self.import_version = import_version
        self.proc_net = proc_net
        self.workspace_override = workspace_override
        self.mount_override = mount_override
        self.networks: set[str] = set()
        self.containers: Dict[str, Dict[str, str]] = {}
        self.calls: List[List[str]] = []

    def __call__(self, argv: Sequence[str], timeout: float = 60.0) -> rs.CommandResult:
        a = list(argv)
        self.calls.append(a)

        def ok(out: str = "") -> rs.CommandResult:
            return rs.CommandResult(0, out, "")

        def fail(code: int = 1, err: str = "") -> rs.CommandResult:
            return rs.CommandResult(code, "", err)

        if a[:2] == ["docker", "version"]:
            return ok("28.5.1") if self.available else fail(127)
        if a[:3] == ["docker", "image", "inspect"]:
            if not self.image_present:
                return fail(1)
            if "Labels" in a[4]:
                return ok(self.image_version)
            return ok("sha256:deadbeef")
        if a[:3] == ["docker", "network", "create"]:
            name = a[-1]
            self.networks.add(name)
            return ok("netid_" + name)
        if a[:3] == ["docker", "network", "inspect"]:
            name = a[-1]
            if name not in self.networks:
                return fail(1)
            if "Internal" in a[4]:
                return ok("true" if self.network_internal else "false")
            return ok("netid_" + name)
        if a[:3] == ["docker", "network", "rm"]:
            self.networks.discard(a[-1])
            return ok()
        if a[:2] == ["docker", "run"]:
            name = a[a.index("--name") + 1]
            net = a[a.index("--network") + 1]
            workspace = ""
            mount = ""
            for i, tok in enumerate(a):
                if tok == "-e" and a[i + 1].startswith("WORKSPACE="):
                    workspace = a[i + 1].split("=", 1)[1]
                if tok == "-v":
                    # rsplit so a Windows drive colon (C:\...) is not truncated —
                    # only the ":/app/data/rag_storage" destination suffix is removed.
                    mount = a[i + 1].rsplit(":", 1)[0]
            self.containers[name] = {
                "network": net,
                "workspace": workspace,
                "mount": mount,
            }
            return ok("containerid_" + name)
        if a[:2] == ["docker", "inspect"]:
            name = a[-1]
            if name not in self.containers:
                return fail(1)
            if a[3] == rs.NILSAFE_INSPECT_FORMAT:
                running = "true" if self.healthy else "false"
                return ok(f"{running}|0|{'healthy' if self.healthy else 'unhealthy'}|0")
            if "Mounts" in a[3]:
                if self.mount_override is not None:
                    return ok(self.mount_override)
                return ok(self.containers[name]["mount"])
            return ok("containerid_" + name)  # {{.Id}}
        if a[:2] == ["docker", "exec"]:
            name = a[2]
            rest = a[3:]
            if rest[:2] == ["python", "-c"] and "lightrag" in rest[2] and "__version__" in rest[2]:
                return ok(self.import_version)
            if rest[:2] == ["python", "-c"] and "health" in rest[2].lower():
                if not self.healthy:
                    return ok("STATUS=ERR:URLError")
                return ok(
                    f"STATUS={self.health_status_code}\n"
                    f"CORE_VERSION={self.core_version}\n"
                    "HEALTH_STATUS_FIELD=healthy"
                )
            if rest[:1] == ["printenv"]:
                if self.workspace_override is not None:
                    return ok(self.workspace_override)
                return ok(self.containers[name]["workspace"])
            if rest[:1] == ["sh"]:
                return ok(self.proc_net)
            return ok("")
        if a[:2] == ["docker", "stop"]:
            return ok()
        if a[:2] == ["docker", "rm"]:
            self.containers.pop(a[-1], None)
            return ok()
        return rs.CommandResult(0, "", "")


def _docker(**kw) -> rs.DockerCLI:
    return rs.DockerCLI(runner=FakeDocker(**kw))


# --------------------------------------------------------------------------- #
# Command / config generation (task §33)
# --------------------------------------------------------------------------- #

def _spec() -> rs.RealSidecarSpec:
    return rs.make_spec(
        run_id="t",
        notebook_id="NB_A",
        notebook_record_id="notebook:x",
        workspace_id="nb_deadbeef00000000",
        storage_root="/tmp/pn02da_NB_A_xyz",
    )


def test_build_run_command_is_content_safe_and_pinned() -> None:
    cmd = rs.build_run_command(_spec())
    assert cmd[:3] == ["docker", "run", "-d"]
    assert rs.REAL_LIGHTRAG_IMAGE == cmd[-1] == "ghcr.io/hkuds/lightrag:v1.5.6"
    assert "--network" in cmd and cmd[cmd.index("--network") + 1].startswith("pn02da_net_")
    # No published port (internal network).
    assert "-p" not in cmd
    # Provider bindings are all present but EMPTY (no value, no provider host/key).
    joined = " ".join(cmd)
    for name in rs.EMPTY_BINDING_ENV:
        assert f"{name}=" in cmd  # present
    for tok in cmd:
        key, sep, value = tok.partition("=")
        if key in rs.EMPTY_BINDING_ENV:
            assert value == "", f"{key} must be empty, got {value!r}"
    assert "openai" not in joined.lower()
    assert "http://" not in joined.lower() and "https://" not in joined.lower()
    # WORKSPACE is the only nonempty -e value.
    assert "WORKSPACE=nb_deadbeef00000000" in cmd


def test_assert_no_provider_binding_rejects_injected_binding() -> None:
    bad = rs.build_run_command(_spec())
    i = bad.index("LLM_BINDING=")
    bad[i] = "LLM_BINDING=openai"
    with pytest.raises(rs.ProviderBindingInCommand):
        rs.assert_no_provider_binding_in_command(bad)


def test_redact_command_masks_keys() -> None:
    argv = ["docker", "run", "-e", "LIGHTRAG_API_KEY=supersecret", "img"]
    red = rs.redact_command(argv)
    assert "supersecret" not in " ".join(red)
    assert "LIGHTRAG_API_KEY=<redacted>" in red


# --------------------------------------------------------------------------- #
# Version attestation (task §9)
# --------------------------------------------------------------------------- #

def test_version_attested_three_signals() -> None:
    v = at.attest_version(
        health_core_version="1.5.6", import_version="1.5.6", image_label_version="v1.5.6"
    )
    assert v.attested and v.observed_version == "1.5.6"


@pytest.mark.parametrize(
    "hc,iv,il",
    [
        ("1.5.5", "1.5.6", "v1.5.6"),
        ("1.5.6", "1.5.7", "v1.5.6"),
        ("1.5.6", "1.5.6", "v1.5.5"),
        ("ERR:URLError", "1.5.6", "v1.5.6"),
        ("", "1.5.6", "v1.5.6"),
        ("1.5.6", "NONE", "v1.5.6"),
    ],
)
def test_version_mismatch_rejected(hc: str, iv: str, il: str) -> None:
    v = at.attest_version(health_core_version=hc, import_version=iv, image_label_version=il)
    assert not v.attested


# --------------------------------------------------------------------------- #
# Egress detection (task §4/§5)
# --------------------------------------------------------------------------- #

def test_egress_clean_rows_zero_external() -> None:
    obs = eg.observe_from_text(CLEAN_PROC_NET)
    assert obs.external_peer_sockets == 0
    assert obs.clean


def test_egress_detects_external_socket() -> None:
    obs = eg.observe_from_text(DIRTY_PROC_NET)
    assert obs.external_peer_sockets == 1
    assert not obs.clean


def test_egress_guard_fail_closed() -> None:
    g = eg.ProviderEgressGuard()
    g.observe(eg.observe_from_text(DIRTY_PROC_NET))
    with pytest.raises(eg.ProviderEgressDetected):
        g.assert_zero()


def test_loopback_docker_dns_not_external() -> None:
    # 127.0.0.11 (Docker DNS) must never be counted as external.
    rows = eg.parse_proc_net_tcp(CLEAN_PROC_NET)
    assert all(not r.is_external_connection for r in rows)


def test_ipv6_classification() -> None:
    # ::1 loopback (not external); a non-mapped external IPv6 ending 7F IS external.
    tcp6 = (
        "  sl  local_address                         remote_address\n"
        "   0: 00000000000000000000000000000000:2595 "
        "00000000000000000000000000000000:0000 0A\n"
        "   1: 00000000000000000000000001000000:AA01 "
        "00000000000000000000000001000000:01BB 01\n"  # ::1 -> ::1 loopback peer
        "   2: 00000000000000000000000001000000:AA02 "
        "20010DB8000000000000000000000E7F:01BB 01\n"  # external IPv6 ending 7F
    )
    obs = eg.observe_from_text(tcp6)
    assert obs.external_peer_sockets == 1


# --------------------------------------------------------------------------- #
# Gate 0 — single sidecar (task §7-§13)
# --------------------------------------------------------------------------- #

def test_gate0_passes_clean(tmp_path) -> None:
    g = pf.run_gate0(
        _docker(), run_id="t", base_storage_dir=str(tmp_path),
        poll_seconds=0.0, image_label_version="v1.5.6",
    )
    assert g.passed
    assert g.outcome is pf.OutcomeDA.COMPLETED
    assert g.report["EXTERNAL_PROVIDER_NETWORK_CALLS"] == 0
    assert g.cleanup.clean


def test_gate0_fails_on_unhealthy(tmp_path) -> None:
    g = pf.run_gate0(
        _docker(healthy=False), run_id="t", base_storage_dir=str(tmp_path),
        poll_seconds=0.0, timeout_seconds=0.0, image_label_version="v1.5.6",
    )
    assert not g.passed
    assert g.outcome is pf.OutcomeDA.FAILED_SIDECAR_START
    assert g.cleanup.clean  # still cleans up
    # L2: egress was never sampled on a failed boot -> honest NOT_SAMPLED, and the
    # report must NOT claim provider-free boot proven.
    assert g.report["EXTERNAL_PROVIDER_NETWORK_CALLS"] == "NOT_SAMPLED"
    assert g.report["REAL_LIGHTRAG_PROVIDER_FREE_BOOT_PROVEN"] is False
    assert g.report["egress_samples"] == 0


def test_gate0_fails_on_version_mismatch(tmp_path) -> None:
    g = pf.run_gate0(
        _docker(core_version="1.5.5"), run_id="t", base_storage_dir=str(tmp_path),
        poll_seconds=0.0, image_label_version="v1.5.6",
    )
    assert not g.passed
    assert g.outcome is pf.OutcomeDA.FAILED_VERSION_ATTESTATION


def test_gate0_fails_on_workspace_mismatch(tmp_path) -> None:
    g = pf.run_gate0(
        _docker(workspace_override="nb_wrong"), run_id="t",
        base_storage_dir=str(tmp_path), poll_seconds=0.0, image_label_version="v1.5.6",
    )
    assert not g.passed
    assert g.outcome is pf.OutcomeDA.FAILED_WORKSPACE_ATTESTATION


def test_gate0_fails_on_provider_egress(tmp_path) -> None:
    g = pf.run_gate0(
        _docker(proc_net=DIRTY_PROC_NET), run_id="t",
        base_storage_dir=str(tmp_path), poll_seconds=0.0, image_label_version="v1.5.6",
    )
    assert not g.passed
    assert g.outcome is pf.OutcomeDA.FAILED_PROVIDER_EGRESS


def test_gate0_fails_on_non_internal_network(tmp_path) -> None:
    g = pf.run_gate0(
        _docker(network_internal=False), run_id="t",
        base_storage_dir=str(tmp_path), poll_seconds=0.0, image_label_version="v1.5.6",
    )
    assert not g.passed
    assert g.outcome is pf.OutcomeDA.FAILED_NETWORK_ISOLATION


def test_gate0_fails_on_forbidden_mount(tmp_path) -> None:
    g = pf.run_gate0(
        _docker(mount_override="/var/lib/graphrag-08/rag_storage"), run_id="t",
        base_storage_dir=str(tmp_path), poll_seconds=0.0, image_label_version="v1.5.6",
    )
    assert not g.passed
    assert g.outcome is pf.OutcomeDA.FAILED_STORAGE_ATTESTATION


# --------------------------------------------------------------------------- #
# Gate 1 — three sidecars (task §14-§21)
# --------------------------------------------------------------------------- #

@pytest.fixture()
def fx() -> ds.FixturePN02:
    f = ds.load_fixture()
    ds.validate_fixture(f)
    return f


def test_gate1_passes_and_identities_unique(fx, tmp_path) -> None:
    g = pf.run_gate1(
        _docker(), fx, run_id="t", base_storage_dir=str(tmp_path),
        poll_seconds=0.0, image_label_version="v1.5.6",
    )
    assert g.passed, g.errors
    assert g.report["REAL_LIGHTRAG_RUNTIME_COUNT"] == 3
    assert g.report["REAL_LIGHTRAG_WORKSPACE_COUNT"] == 3
    assert g.report["REAL_LIGHTRAG_STORAGE_ROOT_COUNT"] == 3
    assert g.report["REAL_LIGHTRAG_ENDPOINT_COUNT"] == 3
    assert g.report["REAL_LIGHTRAG_ALL_VERSION_ATTESTED"] == "PASS"
    assert g.report["REAL_LIGHTRAG_ALL_WORKSPACES_ATTESTED"] == "PASS"
    assert g.cleanup.clean


def test_gate1_routing_negatives(fx, tmp_path) -> None:
    g = pf.run_gate1(
        _docker(), fx, run_id="t", base_storage_dir=str(tmp_path),
        poll_seconds=0.0, image_label_version="v1.5.6",
    )
    routing = g.report["routing"]
    assert routing["REAL_WRONG_ENDPOINT_ROUTE_REJECTED"] is True
    assert routing["REAL_WRONG_WORKSPACE_ROUTE_REJECTED"] is True
    assert routing["REAL_UNATTESTED_ROUTE_REJECTED"] is True
    assert routing["CORRECT_ROUTE_ACCEPTED"] is True
    assert routing["REAL_LIGHTRAG_STORAGE_ALIASING"] == 0


def test_gate1_egress_fails_closed(fx, tmp_path) -> None:
    g = pf.run_gate1(
        _docker(proc_net=DIRTY_PROC_NET), fx, run_id="t",
        base_storage_dir=str(tmp_path), poll_seconds=0.0, image_label_version="v1.5.6",
    )
    assert not g.passed
    assert g.outcome is pf.OutcomeDA.FAILED_PROVIDER_EGRESS


# --------------------------------------------------------------------------- #
# Cleanup ownership (task §28/§29)
# --------------------------------------------------------------------------- #

def test_cleanup_only_touches_owned(tmp_path) -> None:
    fake = FakeDocker()
    docker = rs.DockerCLI(runner=fake)
    # Pre-existing unrelated container + network that PN02D-A must NOT remove.
    fake.containers["unrelated_service"] = {"network": "bridge", "workspace": "", "mount": ""}
    fake.networks.add("bridge")
    pf.run_gate0(
        docker, run_id="t", base_storage_dir=str(tmp_path),
        poll_seconds=0.0, image_label_version="v1.5.6",
    )
    assert "unrelated_service" in fake.containers
    assert "bridge" in fake.networks


# --------------------------------------------------------------------------- #
# Fixture hash gate + full orchestrator (task §2/§7)
# --------------------------------------------------------------------------- #

def test_full_preflight_completes_decision_c(tmp_path) -> None:
    res = pf.run_preflight_da(
        _docker(), run_id="t", base_storage_dir=str(tmp_path), poll_seconds=0.0
    )
    assert res.outcome is pf.OutcomeDA.COMPLETED
    assert res.decision is pf.DecisionDA.REAL_LIGHTRAG_PREFLIGHT_PASSED_AND_PROVIDER_RUN_AUTHORIZATION_GATE_JUSTIFIED
    assert res.boot_gate == "PASS"
    assert res.authorization_minted
    d = res.to_dict()
    assert d["PN02_PROVIDER_RUN_AUTHORIZED"] == "NO"
    assert d["PN02_PROVIDER_RUN_AUTHORIZATION_GATE_JUSTIFIED"] == "YES"
    assert d["actual_consumed_counters"]["external_provider_network_calls"] == 0
    assert d["actual_consumed_counters"]["query_data_calls"] == 0
    assert d["actual_consumed_counters"]["document_insert_calls"] == 0


def test_fixture_hash_mismatch_rejected(tmp_path) -> None:
    # A fixture dir whose freeze marker disagrees must hard-stop before any boot.
    import json
    import shutil

    src = ds.default_fixture_dir()
    dst = tmp_path / "fx"
    shutil.copytree(src, dst)
    freeze = json.loads((dst / "freeze.json").read_text(encoding="utf-8"))
    freeze["fixture_sha256"] = "0" * 64
    (dst / "freeze.json").write_text(json.dumps(freeze), encoding="utf-8")
    res = pf.run_preflight_da(
        _docker(), run_id="t", fixture_dir=str(dst), base_storage_dir=str(tmp_path),
        poll_seconds=0.0,
    )
    assert res.outcome is pf.OutcomeDA.FAILED_FIXTURE_HASH
    assert res.decision is pf.DecisionDA.REAL_LIGHTRAG_PREFLIGHT_FAILED


def test_docker_unavailable_rejected(tmp_path) -> None:
    res = pf.run_preflight_da(
        _docker(available=False), run_id="t", base_storage_dir=str(tmp_path),
        poll_seconds=0.0,
    )
    assert res.outcome is pf.OutcomeDA.FAILED_DOCKER_UNAVAILABLE


def test_image_version_mismatch_rejected(tmp_path) -> None:
    res = pf.run_preflight_da(
        _docker(image_version="v1.9.9"), run_id="t", base_storage_dir=str(tmp_path),
        poll_seconds=0.0,
    )
    assert res.outcome is pf.OutcomeDA.FAILED_IMAGE_VERSION


# --------------------------------------------------------------------------- #
# HARD no-index authorization (task §34/§35)
# --------------------------------------------------------------------------- #

def test_authorization_unforgeable() -> None:
    with pytest.raises(PermissionError):
        at.RealLightRAGPreflightAuthorization(
            object(), fixture_hash="x", run_id="r", runtime_count=3
        )


def test_no_index_without_both_gates() -> None:
    assert at.mint_real_preflight_authorization(
        gate0_passed=True, gate1_passed=False, fixture_hash="x", run_id="r", runtime_count=1
    ) is None
    assert at.mint_real_preflight_authorization(
        gate0_passed=False, gate1_passed=True, fixture_hash="x", run_id="r", runtime_count=1
    ) is None
    auth = at.mint_real_preflight_authorization(
        gate0_passed=True, gate1_passed=True, fixture_hash="x", run_id="r", runtime_count=3
    )
    assert auth is not None


def test_indexing_gate_blocks_without_auth() -> None:
    with pytest.raises(at.IndexingGateBlocked):
        at.require_real_preflight_authorization(None)
    with pytest.raises(at.IndexingGateBlocked):
        at.assert_indexing_gated(None)
    auth = at.mint_real_preflight_authorization(
        gate0_passed=True, gate1_passed=True, fixture_hash="x", run_id="r", runtime_count=3
    )
    assert at.require_real_preflight_authorization(auth) is auth


# --------------------------------------------------------------------------- #
# Provider-config report is name-only (task §24/§25)
# --------------------------------------------------------------------------- #

def test_provider_config_name_only(monkeypatch) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-should-never-appear")
    res = pf.run_preflight_da(
        _docker(), run_id="t", poll_seconds=0.0
    )
    blob = pf.json.dumps(res.to_dict())
    assert "sk-should-never-appear" not in blob
    assert res.provider_config["OPENROUTER_KEY_PRESENT"] in ("YES", "NO", "NOT_REQUIRED_FOR_BOOT")


# --------------------------------------------------------------------------- #
# No-provider-seam import guard (task §31)
# --------------------------------------------------------------------------- #

def test_pn02d_modules_have_no_provider_seam() -> None:
    import inspect

    for mod in (eg, rs, at, pf):
        src = inspect.getsource(mod)
        low = src.lower()
        assert "import openai" not in low
        assert "provision_langchain_model" not in low
        assert "langchain" not in low
        # No production Ask/retrieval import.
        assert "open_notebook.graphs" not in low
