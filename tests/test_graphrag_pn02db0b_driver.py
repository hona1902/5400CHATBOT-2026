"""PN02D-B0B — full offline orchestrator (design §40/§42/§43/§51/§55/§60)."""

from __future__ import annotations

import json
import os
import socket
from pathlib import Path

import graphrag_pn02db0b_common as C
import pytest

from open_notebook.integrations.graphrag.eval.driverpn02d import (
    B1DriverDeps,
    B1OfflineDriver,
    build_simulation_provider_run_authorization,
    plan_b1,
    run_offline_b1_simulation,
)
from open_notebook.integrations.graphrag.eval.fakeslivepn02d import (
    FakeAttempt,
    FakeCellIndexClient,
    build_clean_fake_topology,
    make_index_client_factory,
)
from open_notebook.integrations.graphrag.eval.outcomespn02d import (
    DriverTechnicalOutcome,
)
from open_notebook.integrations.graphrag.eval.provider_binding08 import (
    FROZEN_PROVIDER_SECRET_ENV,
)

B0B_MODULES = [
    "docidpn02d", "budgetlivepn02d", "authlivepn02d", "provbindpn02d",
    "routelivepn02d", "indexlivepn02d", "gdlivepn02d", "vectorlivepn02d",
    "removallivepn02d", "outcomespn02d", "fakeslivepn02d", "driverpn02d",
    "liveclipn02d",
]


@pytest.mark.asyncio
async def test_full_offline_b1_simulation_reaches_evaluator():
    outcome = await run_offline_b1_simulation(run_id="e2e")
    assert outcome.state == "COMPLETE"
    assert outcome.technical_status is DriverTechnicalOutcome.COMPLETED
    assert outcome.index_completion.complete is True
    r = outcome.report
    # Reached Stage-1 -> R0-R4 -> M0-M3 through the frozen PN02B evaluator.
    assert r["stage1"]["stage1_status"] == "PASS"
    assert r["scientific_outputs"]["PER_NOTEBOOK_ISOLATION_EVIDENCED"] == "YES"
    assert "retrieval" in r and r["retrieval"]["rule"].startswith("R")
    assert "multihop" in r and r["multihop"]["rule"].startswith("M")
    # Exact workload (design §58).
    snap = outcome.budget_snapshot
    assert snap["GRAPH_INDEX_OPERATION"] == {"spent": 24, "cap": 24}
    assert snap["GD_QUERY"] == {"spent": 26, "cap": 26}
    assert snap["VECTOR_QUERY"] == {"spent": 26, "cap": 26}
    assert snap["QUERY_EMBEDDING"] == {"spent": 26, "cap": 26}
    assert snap["GRAPH_DELETE"] == {"spent": 1, "cap": 1}
    assert snap["FINAL_ANSWER"] == {"spent": 0, "cap": 0}
    assert snap["CLIENT_QUERY"] == {"spent": 0, "cap": 0}
    assert snap["JUDGE_MODEL"] == {"spent": 0, "cap": 0}
    assert r["driver"]["provider_traffic"] == 0


@pytest.mark.asyncio
async def test_missing_provider_authorization_fails_closed_zero_index():
    fx = C.fixture()
    router = C.router_for(fx)
    topo = build_clean_fake_topology(fx, router)
    deps = B1DriverDeps(
        index_client_factory=topo.index_client_factory(),
        gd_backend_factory=topo.gd_backend_factory(),
        vector_backend_factory=topo.vector_backend_factory(),
        delete_backend_factory=topo.delete_backend_factory(),
        present_secret_envs=frozenset({FROZEN_PROVIDER_SECRET_ENV}),
    )
    driver = B1OfflineDriver(fx, deps)
    outcome = await driver.run(run_id="noauth", provider_run_auth=None)
    assert outcome.technical_status is DriverTechnicalOutcome.FAILED_PROVIDER_AUTHORIZATION
    assert outcome.budget_snapshot["GRAPH_INDEX_OPERATION"]["spent"] == 0
    assert topo.index_client.submit_calls == 0


@pytest.mark.asyncio
async def test_missing_secret_fails_at_binding():
    fx = C.fixture()
    router = C.router_for(fx)
    topo = build_clean_fake_topology(fx, router)
    deps = B1DriverDeps(
        index_client_factory=topo.index_client_factory(),
        gd_backend_factory=topo.gd_backend_factory(),
        vector_backend_factory=topo.vector_backend_factory(),
        delete_backend_factory=topo.delete_backend_factory(),
        present_secret_envs=frozenset(),  # simulated: secret absent
    )
    driver = B1OfflineDriver(fx, deps)
    auth = build_simulation_provider_run_authorization(run_id="nosec")
    outcome = await driver.run(run_id="nosec", provider_run_auth=auth)
    assert outcome.technical_status is DriverTechnicalOutcome.FAILED_PROVIDER_BINDING
    assert topo.index_client.submit_calls == 0


@pytest.mark.asyncio
async def test_23_of_24_blocks_all_queries():
    fx = C.fixture()
    router = C.router_for(fx)
    topo = build_clean_fake_topology(fx, router)
    failing = FakeCellIndexClient(
        {"A1": [FakeAttempt(submit_accepted=False, submit_detail="permanent 400 bad request")]}
    )
    deps = B1DriverDeps(
        index_client_factory=make_index_client_factory(failing),
        gd_backend_factory=topo.gd_backend_factory(),
        vector_backend_factory=topo.vector_backend_factory(),
        delete_backend_factory=topo.delete_backend_factory(),
        present_secret_envs=frozenset({FROZEN_PROVIDER_SECRET_ENV}),
    )
    driver = B1OfflineDriver(fx, deps)
    auth = build_simulation_provider_run_authorization(run_id="p23")
    outcome = await driver.run(run_id="p23", provider_run_auth=auth)
    assert outcome.technical_status is DriverTechnicalOutcome.FAILED_BEFORE_QUERY
    assert outcome.index_completion.succeeded == 23
    # No GD/vector query dispatched.
    assert outcome.budget_snapshot["GD_QUERY"]["spent"] == 0
    assert outcome.budget_snapshot["VECTOR_QUERY"]["spent"] == 0
    assert "scientific_outputs" not in outcome.report  # science NOT_EVALUATED


@pytest.mark.asyncio
async def test_determinism_of_scientific_report():
    o1 = await run_offline_b1_simulation(run_id="det")
    o2 = await run_offline_b1_simulation(run_id="det")

    def strip(report):
        import copy

        r = copy.deepcopy(report)
        r.pop("driver", None)
        if "manifest" in r:
            r["manifest"].pop("created_at", None)
        return r

    assert strip(o1.report) == strip(o2.report)


@pytest.mark.asyncio
async def test_network_denial_full_run(monkeypatch):
    def _no_socket(*a, **k):
        raise AssertionError("network access attempted during offline B0B run")

    monkeypatch.setattr(socket, "socket", _no_socket)
    monkeypatch.setattr(socket, "create_connection", _no_socket)
    outcome = await run_offline_b1_simulation(run_id="netdeny")
    assert outcome.state == "COMPLETE"


@pytest.mark.asyncio
async def test_secret_safe_artifacts():
    outcome = await run_offline_b1_simulation(run_id="secret")
    blob = json.dumps(outcome.report)
    real = os.environ.get(FROZEN_PROVIDER_SECRET_ENV, "").strip()
    if real:
        assert real not in blob
    for token in ("sk-", "bearer ", "-----begin"):
        assert token not in blob.lower()


@pytest.mark.asyncio
async def test_cleanup_invoked_on_failure_path():
    fx = C.fixture()
    router = C.router_for(fx)
    topo = build_clean_fake_topology(fx, router)
    calls = {"n": 0}

    async def _cleanup():
        calls["n"] += 1
        return {"owned_processes_remaining": 0, "networks_remaining": 0}

    deps = B1DriverDeps(
        index_client_factory=topo.index_client_factory(),
        gd_backend_factory=topo.gd_backend_factory(),
        vector_backend_factory=topo.vector_backend_factory(),
        delete_backend_factory=topo.delete_backend_factory(),
        present_secret_envs=frozenset({FROZEN_PROVIDER_SECRET_ENV}),
        cleanup_fn=_cleanup,
    )
    driver = B1OfflineDriver(fx, deps)
    # Fail early (no auth) — cleanup must still run.
    outcome = await driver.run(run_id="cln", provider_run_auth=None)
    assert calls["n"] == 1
    assert outcome.cleanup["invoked"] is True


def test_dry_run_plan_is_content_safe_and_complete():
    plan = plan_b1(run_id="dry")
    assert plan["provider_traffic"] == 0
    assert len(plan["index_plan"]) == 24
    assert len(plan["routes"]) == 3
    assert plan["delete_plan"]["at_notebook"] == "NB_A"
    assert plan["provider_models"]["embedding_dim"] == 1536
    blob = json.dumps(plan)
    real = os.environ.get(FROZEN_PROVIDER_SECRET_ENV, "").strip()
    if real:
        assert real not in blob


def test_production_does_not_import_b0b_eval_modules():
    repo = Path(__file__).resolve().parents[1]
    eval_dir = repo / "open_notebook" / "integrations" / "graphrag" / "eval"
    scanned = 0
    for base in ("open_notebook", "api", "commands"):
        root = repo / base
        if not root.exists():
            continue
        for py in root.rglob("*.py"):
            if eval_dir in py.parents:
                continue  # eval may import eval; production may not
            text = py.read_text(encoding="utf-8", errors="ignore")
            scanned += 1
            for mod in B0B_MODULES:
                assert f".eval.{mod}" not in text, f"{py} imports b0b eval module {mod}"
    assert scanned > 0
