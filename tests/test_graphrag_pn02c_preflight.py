"""GraphRAG-PN02C provider-free live-preflight / routing-attestation tests (task §52).

EVALUATION-ONLY. Every test is provider-free: no index, no embedding, no LLM, no
/query/data, no client.query, no document insert. Runtimes bound here are stdlib
loopback micro-servers with no provider code path, always cleaned up.
"""

from __future__ import annotations

import os

import pytest

from open_notebook.integrations.graphrag.eval import datasetpn02 as ds
from open_notebook.integrations.graphrag.eval import manifestpn02 as mf
from open_notebook.integrations.graphrag.eval import preflightpn02c as pf
from open_notebook.integrations.graphrag.eval import provisionpn02c as pv
from open_notebook.integrations.graphrag.eval import routingpn02c as rt

FX = ds.load_fixture()


# --------------------------------------------------------------------------- #
# Fixtures / helpers
# --------------------------------------------------------------------------- #

@pytest.fixture
def structural_topology():
    """A provisioned topology WITHOUT bound sockets (fast, deterministic)."""
    topo = pv.provision_runtimes(FX, run_id="test-struct", start=False)
    try:
        yield topo
    finally:
        topo.cleanup()


@pytest.fixture
def live_topology():
    """A provisioned topology WITH real loopback micro-runtimes."""
    topo = pv.provision_runtimes(FX, run_id="test-live", start=True)
    try:
        yield topo
    finally:
        topo.cleanup()


# --------------------------------------------------------------------------- #
# §52: three distinct workspace / endpoint / storage identities
# --------------------------------------------------------------------------- #

def test_three_distinct_workspace_ids(structural_topology) -> None:
    ws = set(structural_topology.workspace_ids.values())
    assert len(ws) == 3
    assert structural_topology.workspace_ids["NB_A"] == "nb_2801282a94466d17"


def test_three_distinct_storage_identities(structural_topology) -> None:
    assert len(set(structural_topology.storage_identities.values())) == 3
    assert len(set(structural_topology.storage_roots.values())) == 3


def test_three_distinct_endpoint_identities(live_topology) -> None:
    eps = set(live_topology.endpoints.values())
    assert len(eps) == 3
    for ep in eps:
        assert ep.startswith("http://127.0.0.1:")


# --------------------------------------------------------------------------- #
# §52: wrong route + unattested route rejection
# --------------------------------------------------------------------------- #

def test_wrong_endpoint_routing_rejected(structural_topology) -> None:
    ep = structural_topology.endpoints
    ws = structural_topology.workspace_ids
    with pytest.raises(rt.WrongEndpointRoutingError):
        rt.route_query(
            structural_topology, "NB_A", target_endpoint=ep["NB_B"],
            target_workspace_id=ws["NB_A"],
        )


def test_wrong_workspace_routing_rejected(structural_topology) -> None:
    ep = structural_topology.endpoints
    ws = structural_topology.workspace_ids
    with pytest.raises(rt.WrongWorkspaceRoutingError):
        rt.route_query(
            structural_topology, "NB_B", target_endpoint=ep["NB_B"],
            target_workspace_id=ws["NB_C"],
        )


def test_correct_route_accepted(structural_topology) -> None:
    ep = structural_topology.endpoints
    ws = structural_topology.workspace_ids
    ident = rt.route_query(
        structural_topology, "NB_A", target_endpoint=ep["NB_A"],
        target_workspace_id=ws["NB_A"],
    )
    assert ident.notebook_id == "NB_A"


def test_unattested_route_rejected(structural_topology) -> None:
    bad = mf.WorkspaceAttestation(
        notebook_id="NB_A",
        workspace_id=structural_topology.workspace_ids["NB_A"],
        owned_endpoint_identity=structural_topology.endpoints["NB_A"],
        expected_storage_identity=structural_topology.storage_identities["NB_A"],
        lightrag_version="v1.5.6",
        provider_binding_fingerprint="pbf_x",
        attested=False,
        failure_reasons=("simulated",),
    )
    with pytest.raises(mf.UnattestedWorkspaceError):
        rt.authorize_future_gd(structural_topology, "NB_A", bad)


def test_attested_route_authorized_but_never_queries(structural_topology) -> None:
    atts, all_ok = rt.attest_all(structural_topology)
    assert all_ok is True
    ident = rt.authorize_future_gd(structural_topology, "NB_A", atts["NB_A"])
    assert ident.notebook_id == "NB_A"


# --------------------------------------------------------------------------- #
# §52: duplicate endpoint / storage detection
# --------------------------------------------------------------------------- #

def test_duplicate_endpoint_detected() -> None:
    with pytest.raises(pv.EndpointCollisionError):
        pv.endpoint_collision_check(
            {"NB_A": "http://127.0.0.1:9", "NB_B": "http://127.0.0.1:9"}
        )


def test_duplicate_storage_detected(tmp_path) -> None:
    shared = str(tmp_path / "shared")
    os.makedirs(shared, exist_ok=True)
    with pytest.raises(pv.StorageCollisionError):
        pv.storage_collision_check({"NB_A": shared, "NB_B": shared})


def test_nested_storage_detected(tmp_path) -> None:
    parent = str(tmp_path / "p")
    child = str(tmp_path / "p" / "c")
    os.makedirs(child, exist_ok=True)
    with pytest.raises(pv.StorageCollisionError):
        pv.storage_collision_check({"NB_A": parent, "NB_B": child})


def test_forbidden_storage_root_rejected(tmp_path) -> None:
    bad = str(tmp_path / "rag_storage")
    os.makedirs(bad, exist_ok=True)
    with pytest.raises(pv.StorageCollisionError):
        pv.storage_collision_check({"NB_A": bad, "NB_B": str(tmp_path / "ok")})


# --------------------------------------------------------------------------- #
# §52: version mismatch + fixture hash mismatch rejection
# --------------------------------------------------------------------------- #

def test_version_mismatch_rejected(structural_topology) -> None:
    ident = structural_topology.identities["NB_A"]
    att = rt.attest_runtime(
        ident,
        observed_workspace_id=ident.workspace_id,
        observed_endpoint=ident.endpoint,
        observed_storage_identity=ident.storage_identity,
        observed_lightrag_version="v1.5.7",  # wrong
    )
    assert att.attested is False
    assert "lightrag_version_mismatch" in att.failure_reasons


def test_provider_execution_authorized_blocks_attestation(structural_topology) -> None:
    ident = structural_topology.identities["NB_A"]
    att = rt.attest_runtime(
        ident,
        observed_workspace_id=ident.workspace_id,
        observed_endpoint=ident.endpoint,
        observed_storage_identity=ident.storage_identity,
        observed_lightrag_version="v1.5.6",
        provider_execution_authorized=True,  # preflight must stay live-unsafe
    )
    assert att.attested is False
    assert "provider_execution_authorized_true" in att.failure_reasons


def test_fixture_hash_mismatch_rejected(tmp_path) -> None:
    # Point the preflight at an empty dir -> fixture load/verify fails, no run.
    result = pf.run_preflight(
        run_id="hashfail",
        git_commit="x",
        fixture_dir=str(tmp_path),
        start_runtimes=False,
    )
    assert result.outcome in (
        pf.PreflightOutcome.FAILED_PRECHECK,
        pf.PreflightOutcome.FAILED_FIXTURE_HASH,
    )
    assert result.decision is pf.PreflightDecision.PREFLIGHT_FAILED


# --------------------------------------------------------------------------- #
# §52: provider-workload non-consumption + cross-storage aliasing
# --------------------------------------------------------------------------- #

def test_cross_workspace_storage_aliasing_zero(structural_topology) -> None:
    report = rt.cross_workspace_storage_aliasing(structural_topology)
    assert report.aliasing_count == 0
    assert report.distinct_storage_identities == 3


def test_provider_workload_not_consumed() -> None:
    result = pf.run_preflight(run_id="wl", git_commit="x", start_runtimes=False)
    assert result.counters.all_zero()
    consumed = result.workload_report["PN02C_ACTUAL_CONSUMED"]
    for key, val in consumed.items():
        assert val == 0, key


def test_provider_config_attested_by_name_no_calls() -> None:
    rt.attest_provider_config()  # does not raise
    with pytest.raises(rt.ProviderConfigError):
        rt.attest_provider_config(llm_model="anthropic/claude")
    with pytest.raises(rt.ProviderConfigError):
        rt.attest_provider_config(embedding_dim=768)


def test_provider_binding_configured_reports_bool_never_value(monkeypatch) -> None:
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    assert rt.provider_binding_configured() is False
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-secret-should-never-leak")
    assert rt.provider_binding_configured() is True


# --------------------------------------------------------------------------- #
# §52: cleanup semantics + manifest secret safety
# --------------------------------------------------------------------------- #

def test_cleanup_leaves_zero_processes_and_residue() -> None:
    topo = pv.provision_runtimes(FX, run_id="cleanup", start=True)
    roots = list(topo.storage_roots.values())
    report = topo.cleanup()
    assert report.processes_remaining == 0
    assert report.storage_residue == 0
    for r in roots:
        assert not os.path.isdir(r)


def test_partial_startup_cleans_in_flight_runtime_and_storage(tmp_path, monkeypatch) -> None:
    """A verify failure on the 2nd runtime must leak NO socket/thread or temp dir.

    Regression for the review MEDIUM: an in-flight (not-yet-registered) runtime and
    its storage must be rolled back, keeping the 0-process / 0-residue guarantee even
    on a failed provisioning run.
    """
    import threading
    import time

    orig = pv.ProviderFreeRuntime.fetch_identity
    calls = {"n": 0}

    def flaky(self, timeout: float = 2.0):
        calls["n"] += 1
        if calls["n"] == 2:  # NB_B: fail after NB_A succeeded
            raise OSError("simulated loopback failure")
        return orig(self, timeout)

    monkeypatch.setattr(pv.ProviderFreeRuntime, "fetch_identity", flaky)
    base_threads = sum(1 for t in threading.enumerate() if t.name == "pn02c-runtime")

    with pytest.raises(pv.PartialStartupError):
        pv.provision_runtimes(
            FX, run_id="partial", base_storage_dir=str(tmp_path), start=True
        )

    # No leftover run-owned storage dirs (NB_A registered + NB_B in-flight both gone).
    residue = [p for p in os.listdir(tmp_path) if p.startswith("pv.")] + [
        p for p in os.listdir(tmp_path) if p.startswith("pn02c_")
    ]
    assert residue == [], residue
    # No leaked runtime threads.
    time.sleep(0.3)
    after_threads = sum(1 for t in threading.enumerate() if t.name == "pn02c-runtime")
    assert after_threads == base_threads


def test_manifest_is_secret_safe(monkeypatch) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-should-never-appear")
    result = pf.run_preflight(run_id="secret", git_commit="deadbeef", start_runtimes=True)
    blob = pf.json.dumps(result.to_dict())
    for banned in ("sk-should-never-appear", "OPENROUTER_API_KEY=", "Bearer ", "sk-"):
        assert banned not in blob


# --------------------------------------------------------------------------- #
# §52: provisioning / shared-source / removal plans (computed, not executed)
# --------------------------------------------------------------------------- #

def test_provisioning_plan_8_8_8() -> None:
    plan = pf.build_provisioning_plan(FX)
    assert plan.per_notebook_index_ops == {"NB_A": 8, "NB_B": 8, "NB_C": 8}
    assert plan.total_index_ops == 24


def test_shared_source_plan_ok() -> None:
    plan = pf.build_provisioning_plan(FX)
    assert plan.shared_source_plan_ok is True
    assert plan.shared_source_plan["SH_AB"] == ["NB_A", "NB_B"]
    assert plan.shared_source_plan["SH_AC"] == ["NB_A", "NB_C"]
    assert plan.shared_source_plan["SH_BC"] == ["NB_B", "NB_C"]


def test_removal_plan_frozen() -> None:
    plan = pf.build_provisioning_plan(FX)
    r = plan.removal_plan
    assert r["GRAPH_DELETE_OPERATIONS"] == 1
    assert r["REFERENCE_EDGE_DELETE_OPERATIONS"] == 1
    assert r["POST_REMOVAL_GD_QUERIES"] == 2
    assert r["POST_REMOVAL_VECTOR_QUERIES"] == 2
    assert r["POST_REMOVAL_FINAL_ANSWER_CALLS"] == 0
    assert r["removed_from"] and r["retained_in"] and r["shared_source_is_SH_AB"]


# --------------------------------------------------------------------------- #
# §52: full preflight happy path (live runtimes) + reproducibility
# --------------------------------------------------------------------------- #

def test_full_preflight_completes_and_decision_c() -> None:
    result = pf.run_preflight(run_id="full-1", git_commit="abc", start_runtimes=True)
    assert result.outcome is pf.PreflightOutcome.COMPLETED
    assert result.decision is (
        pf.PreflightDecision.PREFLIGHT_PASSED_AND_LIVE_AUTHORIZATION_GATE_JUSTIFIED
    )
    ar = result.attestation_report
    assert ar["ALL_WORKSPACES_ATTESTED"] is True
    rr = result.routing_report
    assert rr["WRONG_ENDPOINT_ROUTING_REJECTED"] is True
    assert rr["WRONG_WORKSPACE_ROUTING_REJECTED"] is True
    assert rr["UNATTESTED_QUERY_AUTHORIZATION_REJECTED"] is True
    assert rr["CROSS_WORKSPACE_STORAGE_ALIASING"] == 0
    assert result.cleanup["PN02C_OWNED_PROCESSES_REMAINING"] == 0
    assert result.cleanup["PN02C_RUNTIME_STORAGE_RESIDUE"] == 0
    m = result.manifest
    assert m["pn02_live_authorized"] is False
    # Precise runtime terminology — must NOT overstate a real LightRAG boot.
    assert m["ATTESTATION_RUNTIME_COUNT"] == 3
    assert m["REAL_LIGHTRAG_RUNTIME_COUNT"] == 0
    assert m["REAL_LIGHTRAG_RUNTIME_BOOTED"] is False
    assert m["REAL_LIGHTRAG_PROVIDER_FREE_BOOT_PROVEN"] is False
    assert m["REAL_LIGHTRAG_RUNTIME_VERSION"] == "NOT_PROBED"
    assert m["REAL_LIGHTRAG_RUNTIME_VERSION_ATTESTED"] is False
    assert m["LIGHTRAG_CONFIG_VERSION_PIN_MATCH"] == "PASS"
    assert m["REAL_LIGHTRAG_BOOT_GATE_REQUIRED_BEFORE_INDEX"] is True
    # The report must not carry a claim of a real-runtime version match.
    assert "LIGHTRAG_RUNTIME_VERSION_MATCH" not in m


def test_preflight_reproducible_twice_clean() -> None:
    r1 = pf.run_preflight(run_id="rep-1", git_commit="abc", start_runtimes=True)
    r2 = pf.run_preflight(run_id="rep-2", git_commit="abc", start_runtimes=True)
    assert r1.outcome is pf.PreflightOutcome.COMPLETED
    assert r2.outcome is pf.PreflightOutcome.COMPLETED
    # Workspace ids are deterministic across runs; endpoints/storage are fresh.
    assert r1.manifest["attestation_runtimes"]["NB_A"]["workspace_id"] == (
        r2.manifest["attestation_runtimes"]["NB_A"]["workspace_id"]
    )
    for r in (r1, r2):
        assert r.cleanup["PN02C_OWNED_PROCESSES_REMAINING"] == 0
        assert r.cleanup["PN02C_RUNTIME_STORAGE_RESIDUE"] == 0


# --------------------------------------------------------------------------- #
# §37-§39: static guard — PN02C modules contain no provider/retrieval seam
# --------------------------------------------------------------------------- #

def test_pn02c_modules_have_no_provider_seam() -> None:
    """No PN02C module imports a provider SDK or the GraphRAG HTTP client.

    Substring bans are import/symbol shaped so legitimate prose in docstrings (which
    references ``/query/data``) and the ``openai/...`` model-NAME constants do not
    trip the guard. The real provider-free guarantee is structural (no client is
    ever constructed); this is a regression fence.
    """
    import inspect

    for mod in (pv, rt, pf):
        src = inspect.getsource(mod)
        for banned in (
            "import httpx",
            "import openai",
            "from openai",
            "provision_langchain_model",
            "GraphRAGClient",
            "integrations.graphrag.client",
        ):
            assert banned not in src, f"{mod.__name__} contains {banned!r}"
