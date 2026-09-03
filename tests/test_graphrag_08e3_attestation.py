"""GraphRAG-08E.3 real-sidecar attestation-hardening tests (PURE OFFLINE, all mocked).

Pins the two real-sidecar defects found at 08E Reauthorization #2 Stage A:
  A. version-string mismatch — /health reports "1.5.6", the pin is "v1.5.6";
  B. runtime workspace/storage attestation — now evidence-based (DIRECT /health
     configuration.workspace and/or an owned-container runtime attestor), fail-closed.
No provider, no real sidecar, no DB. An autouse guard fails any real HTTP call.
"""

from __future__ import annotations

import asyncio
import json
import os

import pytest

from open_notebook.integrations.graphrag.eval import cell_provisioner08 as cp
from open_notebook.integrations.graphrag.eval.cell_isolation08 import CellIdentity

RUN = "run08e3a"
MOUNT = cp.LIGHTRAG_WORKING_DIR_MOUNT


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    import httpx

    def _boom(*a, **k):  # noqa: ANN001
        raise AssertionError("offline 08E.3 test attempted a real HTTP/provider call")

    monkeypatch.setattr(httpx.AsyncClient, "__init__", _boom)
    monkeypatch.setattr(httpx.Client, "__init__", _boom)
    yield


# ---------------------------------------------------------------------------
# fakes
# ---------------------------------------------------------------------------


class FakeController:
    def __init__(self, cluster):
        self.c = cluster
        self._alive = {}

    async def start(self, spec):
        self.c["started"].append(spec.cell_id)
        h = cp.CellProcessHandle(identifier=f"fake_{spec.cell_id}", kind="fake", pid=7)
        self._alive[h.identifier] = True
        return h

    async def is_alive(self, handle):
        return self._alive.get(handle.identifier, False)

    async def terminate(self, handle, *, graceful_timeout_s):
        self.c["terminated"].append(handle.identifier)
        self._alive[handle.identifier] = False
        return cp.TerminationResult(stopped=True, forced=False)


class FakeProber:
    def __init__(self, *, version="1.5.6", workspace=None, healthy=True,
                 reachable=True, working_dir=MOUNT):
        self.version = version
        self.workspace = workspace
        self.healthy = healthy
        self.reachable = reachable
        self.working_dir = working_dir

    async def probe(self, *, base_url, host, port):
        return cp.CellHealthObservation(
            reachable=self.reachable, healthy=self.healthy, version=self.version,
            reported_workspace=self.workspace, working_dir=self.working_dir,
        )


class FakeAttestor:
    def __init__(self, *, evidence=True, identity=None, running=True,
                 workspace=None, storage_source=None, storage_dest=MOUNT):
        self.evidence = evidence
        self.identity = identity
        self.running = running
        self.workspace = workspace
        self.storage_source = storage_source
        self.storage_dest = storage_dest

    async def attest(self, handle):
        return cp.CellRuntimeAttestation(
            evidence_available=self.evidence,
            container_identity=(self.identity if self.identity is not None
                                else handle.identifier),
            running=self.running,
            workspace_config=self.workspace,
            storage_source=self.storage_source,
            storage_dest=self.storage_dest if self.evidence else None,
        )


def _paths(tmp_path, ident):
    return cp.assert_cell_paths_safe(
        eval_root=str(tmp_path), run_id=ident.run_id, workspace=ident.workspace
    )  # (run_root, working_dir, storage_dir)


def _mk(tmp_path, *, prober, attestor=None, **cfg):
    cluster = {"started": [], "terminated": []}
    config = cp.ProvisionerConfig(
        eval_root=str(tmp_path), startup_timeout_s=1.0, health_poll_interval_s=0.05, **cfg
    )
    prov = cp.LightRagCellProvisioner(
        config, process_controller=FakeController(cluster),
        health_prober=prober, runtime_attestor=attestor,
    )
    return prov, cluster


# ===========================================================================
# Defect A — version canonicalization (§21/§22/§23/§43)
# ===========================================================================


def test_versions_equivalent_v_prefix_and_rejections():
    assert cp.versions_equivalent("v1.5.6", "1.5.6") is True   # §21 the real forms
    assert cp.versions_equivalent("1.5.6", "1.5.6") is True
    assert cp.versions_equivalent("v1.5.6", "v1.5.6") is True
    # §22/§43 wrong release rejected
    assert cp.versions_equivalent("v1.5.6", "1.5.7") is False
    assert cp.versions_equivalent("v1.5.6", "1.5.5") is False
    assert cp.versions_equivalent("v1.5.6", "2.0.0") is False
    assert cp.versions_equivalent("v1.5.6", "1.5") is False    # not the same release
    assert cp.versions_equivalent("v1.5.6", "1.5.6.7.8") is False  # valid but different
    assert cp.versions_equivalent("v1.5.6", "1.5.6rc1") is False   # prerelease != release
    assert cp.versions_equivalent("v1.5.6", "1.5.6+build") is False  # local != release
    # §23 malformed / absent fail closed (never normalized into 1.5.6)
    for bad in (None, "", "abc", "v", "1.5.6-broken?", "latest"):
        assert cp.versions_equivalent("v1.5.6", bad) is False
    # whitespace tolerated on an otherwise-valid version
    assert cp.versions_equivalent("v1.5.6", "  1.5.6  ") is True


def test_provision_passes_with_real_version_form(tmp_path):
    ident = CellIdentity(RUN, 1, 1)
    _, working_dir, _ = _paths(tmp_path, ident)
    prober = FakeProber(version="1.5.6", workspace=ident.workspace)  # real form, no 'v'
    prov, _ = _mk(tmp_path, prober=prober)  # expected_version default = 'v1.5.6'

    async def _run():
        cell = await prov.provision_cell(ident)
        return cell

    cell = asyncio.run(_run())
    assert cell.ready and cell.version == "1.5.6"
    assert cell.workspace_direct_runtime_report is True
    assert "DIRECT_HEALTH_CONFIG" in cell.workspace_attestation_kind


def test_wrong_version_fails_and_cleans(tmp_path):
    ident = CellIdentity(RUN, 1, 1)
    _, _, storage = _paths(tmp_path, ident)
    prober = FakeProber(version="1.5.7", workspace=ident.workspace)
    prov, cluster = _mk(tmp_path, prober=prober)

    async def _run():
        with pytest.raises(cp.CellVersionMismatchError):
            await prov.provision_cell(ident)

    asyncio.run(_run())
    assert cluster["terminated"] and not os.path.exists(storage)


# ===========================================================================
# Defect B — health payload parse (§32) + DIRECT report
# ===========================================================================

# Representative, SECRET-FREE subset of the real v1.5.6 /health shape (Stage-A obs).
REAL_HEALTH = {
    "status": "healthy",
    "core_version": "1.5.6",
    "api_version": "0187",
    "working_directory": "/app/data/rag_storage",
    "configuration": {
        "workspace": "gr08e_runX_c1_r1",
        "storage_workspaces": ["gr08e_runX_c1_r1"],
        "llm_binding": "ollama",
        "llm_binding_host": "http://localhost:11434",
        "embedding_model": "bge-m3",
    },
}


def test_parse_health_payload_extracts_only_safe_fields():
    obs = cp.parse_health_payload(REAL_HEALTH)
    assert obs.reachable and obs.healthy
    assert obs.version == "1.5.6"
    assert obs.reported_workspace == "gr08e_runX_c1_r1"   # from configuration.workspace
    assert obs.working_dir == "/app/data/rag_storage"
    # binding/host/model are structurally absent from the content-safe observation
    blob = json.dumps(obs.__dict__)
    assert "ollama" not in blob and "11434" not in blob and "bge-m3" not in blob


def test_parse_health_payload_missing_config_and_nondict():
    no_cfg = cp.parse_health_payload({"status": "healthy", "core_version": "1.5.6"})
    assert no_cfg.healthy and no_cfg.reported_workspace is None
    bad = cp.parse_health_payload(["not", "a", "dict"])
    assert bad.reachable and not bad.healthy and bad.reported_workspace is None


# ===========================================================================
# Defect B — derived owned-container attestation (§24/§25/§26/§27/§28/§30)
# ===========================================================================


def test_derived_attestation_pass_direct_absent(tmp_path):
    # §24/§25: /health does NOT report workspace, but the owned-container attestor does
    ident = CellIdentity(RUN, 2, 1)
    _, working_dir, _ = _paths(tmp_path, ident)
    prober = FakeProber(workspace=None)  # no direct report
    attestor = FakeAttestor(workspace=ident.workspace, storage_source=working_dir)
    prov, _ = _mk(tmp_path, prober=prober, attestor=attestor)

    async def _run():
        return await prov.provision_cell(ident)

    cell = asyncio.run(_run())
    assert cell.ready
    assert cell.workspace_direct_runtime_report is False          # §35 DIRECT report = NO
    assert cell.workspace_attestation_kind == "DERIVED_OWNED_CONTAINER_CONFIG"


def test_absent_workspace_without_attestor_fails_closed(tmp_path):
    # §24: absence must NOT imply PASS; with no attestor and no direct report -> blocked
    ident = CellIdentity(RUN, 1, 1)
    _, _, storage = _paths(tmp_path, ident)
    prov, cluster = _mk(tmp_path, prober=FakeProber(workspace=None))  # no attestor

    async def _run():
        with pytest.raises(cp.CellWorkspaceMismatchError):
            await prov.provision_cell(ident)

    asyncio.run(_run())
    assert cluster["terminated"] and not os.path.exists(storage)


def test_workspace_config_mismatch_fails_closed(tmp_path):
    # §26: attestor reports a DIFFERENT workspace than expected
    ident = CellIdentity(RUN, 4, 1)
    _, working_dir, storage = _paths(tmp_path, ident)
    attestor = FakeAttestor(workspace="gr08e_foreign_cell", storage_source=working_dir)
    prov, cluster = _mk(tmp_path, prober=FakeProber(workspace=None), attestor=attestor)

    async def _run():
        with pytest.raises(cp.CellWorkspaceMismatchError):
            await prov.provision_cell(ident)

    asyncio.run(_run())
    assert cluster["terminated"] and not os.path.exists(storage)


def test_storage_mount_mismatch_fails_closed(tmp_path):
    # §27: attestor's storage mount source is NOT our owned run root
    ident = CellIdentity(RUN, 2, 2)
    _, _, storage = _paths(tmp_path, ident)
    attestor = FakeAttestor(
        workspace=ident.workspace, storage_source=str(tmp_path / "foreign_root")
    )
    prov, cluster = _mk(tmp_path, prober=FakeProber(workspace=None), attestor=attestor)

    async def _run():
        with pytest.raises(cp.CellWorkspaceMismatchError):
            await prov.provision_cell(ident)

    asyncio.run(_run())
    assert cluster["terminated"] and not os.path.exists(storage)


def test_missing_runtime_evidence_fails_closed(tmp_path):
    # §28: attestor cannot retrieve evidence -> fail closed (no fallback to input)
    ident = CellIdentity(RUN, 1, 1)
    _, _, storage = _paths(tmp_path, ident)
    attestor = FakeAttestor(evidence=False)
    prov, cluster = _mk(tmp_path, prober=FakeProber(workspace=None), attestor=attestor)

    async def _run():
        with pytest.raises(cp.CellWorkspaceMismatchError):
            await prov.provision_cell(ident)

    asyncio.run(_run())
    assert cluster["terminated"] and not os.path.exists(storage)


def test_foreign_container_evidence_rejected(tmp_path):
    # §30: attestation must come from the EXACT owned container
    ident = CellIdentity(RUN, 8, 1)
    _, working_dir, storage = _paths(tmp_path, ident)
    attestor = FakeAttestor(
        identity="foreign_container", workspace=ident.workspace,
        storage_source=working_dir,
    )
    prov, cluster = _mk(tmp_path, prober=FakeProber(workspace=None), attestor=attestor)

    async def _run():
        with pytest.raises(cp.CellProvisionOwnershipError):
            await prov.provision_cell(ident)

    asyncio.run(_run())
    assert cluster["terminated"] and not os.path.exists(storage)


def test_direct_and_derived_agree(tmp_path):
    ident = CellIdentity(RUN, 2, 1)
    _, working_dir, _ = _paths(tmp_path, ident)
    prober = FakeProber(workspace=ident.workspace)
    attestor = FakeAttestor(workspace=ident.workspace, storage_source=working_dir)
    prov, _ = _mk(tmp_path, prober=prober, attestor=attestor)

    cell = asyncio.run(prov.provision_cell(ident))
    assert cell.workspace_direct_runtime_report is True
    assert cell.workspace_attestation_kind == (
        "DIRECT_HEALTH_CONFIG+DERIVED_OWNED_CONTAINER_CONFIG"
    )


def test_direct_report_contradicts_expected_fails(tmp_path):
    # §26: a DIRECT /health workspace that disagrees with expected fails, even healthy
    ident = CellIdentity(RUN, 1, 1)
    _, _, storage = _paths(tmp_path, ident)
    prov, cluster = _mk(tmp_path, prober=FakeProber(workspace="gr08e_other"))

    async def _run():
        with pytest.raises(cp.CellWorkspaceMismatchError):
            await prov.provision_cell(ident)

    asyncio.run(_run())
    assert cluster["terminated"] and not os.path.exists(storage)


# ===========================================================================
# §31 cleanup after attestation failure (real Stage-A second-blocker shape)
# ===========================================================================


def test_cleanup_after_attestation_failure(tmp_path):
    ident = CellIdentity(RUN, 1, 1)
    _, working_dir, storage = _paths(tmp_path, ident)
    # version passes, but workspace attestation fails
    attestor = FakeAttestor(workspace="gr08e_wrong", storage_source=working_dir)
    prov, cluster = _mk(tmp_path, prober=FakeProber(workspace=None), attestor=attestor)

    async def _run():
        with pytest.raises(cp.CellWorkspaceMismatchError):
            await prov.provision_cell(ident)

    asyncio.run(_run())
    assert cluster["terminated"]                 # exact process stopped
    assert not os.path.exists(storage)           # workspace disposed
    assert ident.cell_id not in prov._active     # no provisioned handle retained


# ===========================================================================
# §29 secret redaction in runtime inspect parsing + §45 review
# ===========================================================================


def test_runtime_inspect_parser_never_leaks_secret():
    # A secret sits in an UNRELATED mount line; the parser must extract only the
    # rag-storage mount + the (separately templated) WORKSPACE value.
    mounts = (
        f"{MOUNT}|C:\\owned\\run\\cell\n"
        "/secret|OPENROUTER_API_KEY=TEST_SECRET_SHOULD_NOT_LEAK"
    )
    att = cp.parse_runtime_inspect(
        identity="gr08e2_c", inspect_ok=True, state_running="true",
        workspace_env="gr08e_run_c1_r1", mounts_text=mounts, storage_dest=MOUNT,
    )
    assert att.evidence_available and att.running is True
    assert att.workspace_config == "gr08e_run_c1_r1"
    assert att.storage_source == "C:\\owned\\run\\cell"
    assert att.storage_dest == MOUNT
    blob = json.dumps(att.__dict__) + repr(att)
    assert "TEST_SECRET_SHOULD_NOT_LEAK" not in blob
    assert "OPENROUTER_API_KEY" not in blob


def test_runtime_inspect_parser_missing_evidence():
    att = cp.parse_runtime_inspect(
        identity="c", inspect_ok=False, state_running=None,
        workspace_env=None, mounts_text=None, storage_dest=MOUNT,
    )
    assert att.evidence_available is False


def test_workspace_env_template_is_filtered_not_full_dump():
    # Structural secret-safety: the inspect template extracts ONLY the WORKSPACE var,
    # never a bare range over all of .Config.Env (which would print provider keys).
    tmpl = cp._WORKSPACE_ENV_TEMPLATE
    assert "WORKSPACE" in tmpl and ".Config.Env" in tmpl and "if eq" in tmpl


# ===========================================================================
# §47 scientific boundary: attestation change does not touch the frozen harness
# ===========================================================================


def test_frozen_harness_unchanged():
    from open_notebook.integrations.graphrag.eval import concurrency_diag08 as cd
    from open_notebook.integrations.graphrag.eval import dataset08 as d

    plan = cd.default_plan()
    assert [lvl.concurrency for lvl in plan.levels] == [1, 2, 4, 8]
    assert plan.total_submissions == 30 and cd.MAX_TOTAL_SUBMISSIONS == 64
    ok, h = d.verify_integrity()
    assert ok and h == "a58a68535c345e18f0263904f818e4e2068a164056408665d8bb9233eceb143d"
