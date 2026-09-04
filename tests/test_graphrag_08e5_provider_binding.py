"""GraphRAG-08E.5 provider-binding injection tests (PURE OFFLINE, all mocked).

No provider, no real container, no DB. Proves the frozen OpenRouter LLM/embedding bindings
are transported into each cell container's launch config + attested at runtime, WITHOUT any
secret value reaching argv/repr/logs/attestation/AttemptRecord (task §31-§54). An autouse
guard fails any real HTTP call.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from open_notebook.integrations.graphrag.eval import cell_isolation08 as ci
from open_notebook.integrations.graphrag.eval import cell_provisioner08 as cp
from open_notebook.integrations.graphrag.eval import provider_binding08 as pb

RUN = "run08e5a"
MOUNT = cp.LIGHTRAG_WORKING_DIR_MOUNT
SECRET = "TEST_SECRET_08E5_DO_NOT_LEAK"


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    import httpx

    def _boom(*a, **k):  # noqa: ANN001
        raise AssertionError("offline 08E.5 test attempted a real HTTP/provider call")

    monkeypatch.setattr(httpx.AsyncClient, "__init__", _boom)
    monkeypatch.setattr(httpx.Client, "__init__", _boom)
    yield


def _ident(conc=1, rep=1):
    return ci.CellIdentity(RUN, conc, rep)


# ===========================================================================
# §31/§33/§36 frozen binding spec + drift rejection
# ===========================================================================


def test_frozen_binding_is_the_benchmark_config():
    b = pb.frozen_provider_binding()
    assert b.llm_binding == "openai" and b.llm_model == "openai/gpt-4o-mini"
    assert b.embedding_binding == "openai"
    assert b.embedding_model == "openai/text-embedding-3-small"
    assert b.embedding_dim == 1536
    assert b.llm_host and b.embedding_host  # public host present (no localhost fallback)
    assert b.llm_secret_env == "OPENROUTER_API_KEY"  # NAME only
    b.validate()  # frozen -> no raise


def test_binding_validation_rejects_drift():
    import dataclasses as dc

    base = pb.frozen_provider_binding()
    # §33 wrong LLM model
    with pytest.raises(pb.ProviderBindingError):
        dc.replace(base, llm_model="openai/gpt-4o").validate()
    # §36 wrong embedding model
    with pytest.raises(pb.ProviderBindingError):
        dc.replace(base, embedding_model="text-embedding-3-large").validate()
    # §32 missing LLM binding
    with pytest.raises(pb.ProviderBindingError):
        dc.replace(base, llm_binding="").validate()
    # §34 missing LLM host (the #4 blocker: no Ollama/localhost fallback)
    with pytest.raises(pb.ProviderBindingError):
        dc.replace(base, llm_host="").validate()
    # §35 missing LLM secret env name
    with pytest.raises(pb.ProviderBindingError):
        dc.replace(base, llm_secret_env="").validate()
    # §37 missing embedding host
    with pytest.raises(pb.ProviderBindingError):
        dc.replace(base, embedding_host="").validate()
    # wrong dimension
    with pytest.raises(pb.ProviderBindingError):
        dc.replace(base, embedding_dim=3072).validate()


def test_binding_public_dict_has_no_secret_value():
    b = pb.frozen_provider_binding()
    blob = json.dumps(b.as_public_dict()) + repr(b)
    # only env NAMES appear, never a value
    assert "OPENROUTER_API_KEY" in blob  # the NAME is content-safe
    assert SECRET not in blob


# ===========================================================================
# §10/§11/§42 Docker start: secret via env-inheritance, NEVER on argv
# ===========================================================================


def _capture_docker(monkeypatch):
    calls = {}

    def _fake_run(args, **kwargs):
        calls["args"] = list(args)
        calls["env"] = dict(kwargs.get("env") or {})
        import types
        return types.SimpleNamespace(returncode=0, stdout="containerid\n", stderr="")

    monkeypatch.setattr(cp.subprocess, "run", _fake_run)
    return calls


def test_docker_start_injects_bindings_secret_safe(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", SECRET)
    monkeypatch.setenv("GRAPHRAG_POC_API_KEY", "SIDECAR_" + SECRET)
    calls = _capture_docker(monkeypatch)
    spec = cp.CellProcessSpec(
        cell_id=_ident().cell_id, workspace="gr08e_ws", working_dir="/wd",
        host="127.0.0.1", port=51000, image=cp.DEFAULT_LIGHTRAG_IMAGE,
        provider_binding=pb.frozen_provider_binding(),
    )
    asyncio.run(cp.DockerCellProcessController().start(spec))
    argv = calls["args"]
    argv_str = " ".join(argv)
    # public bindings are on argv (safe)
    assert "LLM_BINDING=openai" in argv and "LLM_MODEL=openai/gpt-4o-mini" in argv
    assert "EMBEDDING_MODEL=openai/text-embedding-3-small" in argv
    assert f"LLM_BINDING_HOST={pb.FROZEN_OPENROUTER_HOST}" in argv
    # secret VALUES never appear on argv (neither provider nor sidecar key)
    assert SECRET not in argv_str and ("SIDECAR_" + SECRET) not in argv_str
    # secret var NAMES appear as BARE -e (name only), not NAME=value
    assert "LLM_BINDING_API_KEY" in argv
    assert not any(a.startswith("LLM_BINDING_API_KEY=") for a in argv)
    assert not any(a.startswith("LIGHTRAG_API_KEY=") for a in argv)
    # the VALUE is in the subprocess env (inherited by Docker), not argv
    assert calls["env"]["LLM_BINDING_API_KEY"] == SECRET
    assert calls["env"]["EMBEDDING_BINDING_API_KEY"] == SECRET
    assert calls["env"]["LIGHTRAG_API_KEY"] == "SIDECAR_" + SECRET


def test_docker_start_missing_provider_secret_fails_named(monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    _capture_docker(monkeypatch)
    spec = cp.CellProcessSpec(
        cell_id=_ident().cell_id, workspace="gr08e_ws", working_dir="/wd",
        host="127.0.0.1", port=51000, image=cp.DEFAULT_LIGHTRAG_IMAGE,
        provider_binding=pb.frozen_provider_binding(),
    )
    with pytest.raises(cp.CellProcessStartError) as ei:
        asyncio.run(cp.DockerCellProcessController().start(spec))
    # names the missing variable, never a value
    assert "OPENROUTER_API_KEY" in str(ei.value) and SECRET not in str(ei.value)


# ===========================================================================
# §20/§21/§43 runtime binding + secret-presence parsing (secret non-leak)
# ===========================================================================


def test_parse_runtime_inspect_binding_and_secret_presence():
    binding_env = (
        "LLM_BINDING=openai\nLLM_MODEL=openai/gpt-4o-mini\n"
        "EMBEDDING_BINDING=openai\nEMBEDDING_MODEL=openai/text-embedding-3-small"
    )
    att = cp.parse_runtime_inspect(
        identity="gr08e2_c", inspect_ok=True, state_running="true",
        workspace_env="gr08e_ws", mounts_text=f"{MOUNT}|/wd", storage_dest=MOUNT,
        binding_env_text=binding_env,
        llm_secret_present_raw="PRESENT", embedding_secret_present_raw="PRESENT",
    )
    assert att.llm_binding == "openai" and att.llm_model == "openai/gpt-4o-mini"
    assert att.embedding_model == "openai/text-embedding-3-small"
    assert att.llm_secret_present is True and att.embedding_secret_present is True
    # §43 a whitelist template never yields the API key; presence templates never yield a
    # value — the parser only ever sees PRESENT/absent, never the secret.
    att2 = cp.parse_runtime_inspect(
        identity="c", inspect_ok=True, state_running="true", workspace_env="w",
        mounts_text=f"{MOUNT}|/wd", storage_dest=MOUNT,
        binding_env_text="LLM_BINDING=openai", llm_secret_present_raw="",
        embedding_secret_present_raw="",
    )
    assert att2.llm_secret_present is False and att2.embedding_secret_present is False
    assert SECRET not in json.dumps(att2.__dict__)


def test_binding_template_is_whitelist_not_full_env():
    tmpl = cp._BINDING_ENV_TEMPLATE
    for k in ("LLM_BINDING", "LLM_MODEL", "EMBEDDING_BINDING", "EMBEDDING_MODEL"):
        assert k in tmpl
    # it must NOT range-print secret vars
    assert "LLM_BINDING_API_KEY" not in tmpl
    # presence template prints only the sentinel, never a value
    assert "PRESENT" in cp._presence_template("LLM_BINDING_API_KEY")


# ===========================================================================
# provisioner lifecycle: binding attestation gate  (§22/§32-§38/§41)
# ===========================================================================


class FakeController:
    def __init__(self):
        self._alive = {}
        self.started = []

    async def start(self, spec):
        self.started.append(spec)
        h = cp.CellProcessHandle(identifier=f"fake_{spec.cell_id}", kind="fake")
        self._alive[h.identifier] = True
        return h

    async def is_alive(self, handle):
        return self._alive.get(handle.identifier, False)

    async def terminate(self, handle, *, graceful_timeout_s):
        self._alive[handle.identifier] = False
        return cp.TerminationResult(stopped=True, forced=False)


class FakeProber:
    async def probe(self, *, base_url, host, port):
        return cp.CellHealthObservation(
            reachable=True, healthy=True, version="1.5.6",
            reported_workspace=None, working_dir=None,
        )


class FakeBindingAttestor:
    def __init__(self, *, llm_binding="openai", llm_model="openai/gpt-4o-mini",
                 emb_binding="openai", emb_model="openai/text-embedding-3-small",
                 llm_host=pb.FROZEN_OPENROUTER_HOST, emb_host=pb.FROZEN_OPENROUTER_HOST,
                 llm_secret=True, emb_secret=True, evidence=True):
        self.kw = dict(llm_binding=llm_binding, llm_model=llm_model,
                       emb_binding=emb_binding, emb_model=emb_model,
                       llm_host=llm_host, emb_host=emb_host,
                       llm_secret=llm_secret, emb_secret=emb_secret, evidence=evidence)

    async def attest(self, handle):
        k = self.kw
        return cp.CellRuntimeAttestation(
            evidence_available=k["evidence"], container_identity=handle.identifier,
            running=True, workspace_config="ws", storage_source="/wd", storage_dest=MOUNT,
            llm_binding=k["llm_binding"], llm_model=k["llm_model"], llm_host=k["llm_host"],
            embedding_binding=k["emb_binding"], embedding_model=k["emb_model"],
            embedding_host=k["emb_host"],
            llm_secret_present=k["llm_secret"], embedding_secret_present=k["emb_secret"],
        )


def _mk_prov(tmp_path, *, attestor, binding=None, require_binding=True):
    cfg = cp.ProvisionerConfig(
        eval_root=str(tmp_path), startup_timeout_s=1.0, health_poll_interval_s=0.05,
        require_runtime_workspace=False,  # isolate the binding gate
        provider_binding=binding if binding is not None else pb.frozen_provider_binding(),
        require_provider_binding=require_binding,
    )
    return cp.LightRagCellProvisioner(
        cfg, process_controller=FakeController(), health_prober=FakeProber(),
        runtime_attestor=attestor,
    )


def test_provision_requires_binding_when_flagged(tmp_path):
    # require_provider_binding=True but no binding configured -> construction fails closed
    cfg = cp.ProvisionerConfig(
        eval_root=str(tmp_path), require_provider_binding=True, provider_binding=None,
    )
    with pytest.raises(cp.CellProviderBindingError):
        cp.LightRagCellProvisioner(
            cfg, process_controller=FakeController(), health_prober=FakeProber(),
            runtime_attestor=FakeBindingAttestor(),
        )


def test_provision_binding_attested_ok(tmp_path):
    prov = _mk_prov(tmp_path, attestor=FakeBindingAttestor())

    async def _run():
        cell = await prov.provision_cell(_ident(1, 1))
        return cell

    cell = asyncio.run(_run())
    assert cell.ready and cell.provider_binding_verified is True


def test_provision_wrong_runtime_model_fails_closed(tmp_path):
    prov = _mk_prov(tmp_path, attestor=FakeBindingAttestor(llm_model="openai/gpt-4o"))
    ident = _ident(2, 1)
    _, _, storage = cp.assert_cell_paths_safe(
        eval_root=str(tmp_path), run_id=ident.run_id, workspace=ident.workspace
    )

    async def _run():
        with pytest.raises(cp.CellProviderBindingError):
            await prov.provision_cell(ident)

    asyncio.run(_run())
    assert not __import__("os").path.exists(storage)  # cleaned up


def test_provision_no_ollama_fallback(tmp_path):
    # attestor reports an Ollama-ish binding -> fail closed (directly covers the #4 blocker)
    prov = _mk_prov(
        tmp_path, attestor=FakeBindingAttestor(llm_binding="ollama", llm_model="mistral-nemo")
    )

    async def _run():
        with pytest.raises(cp.CellProviderBindingError):
            await prov.provision_cell(_ident(4, 1))

    asyncio.run(_run())


def test_provision_wrong_runtime_host_fails_closed(tmp_path):
    # review LOW-1: a wrong/empty binding host must fail the runtime binding attestation
    prov = _mk_prov(tmp_path, attestor=FakeBindingAttestor(llm_host="http://localhost:11434"))

    async def _run():
        with pytest.raises(cp.CellProviderBindingError):
            await prov.provision_cell(_ident(1, 1))

    asyncio.run(_run())


def test_provision_foreign_binding_container_fails_closed(tmp_path):
    # review LOW-3: binding evidence from a foreign container is rejected
    class _Foreign(FakeBindingAttestor):
        async def attest(self, handle):
            att = await super().attest(handle)
            import dataclasses as dc
            return dc.replace(att, container_identity="foreign_container")

    prov = _mk_prov(tmp_path, attestor=_Foreign())

    async def _run():
        with pytest.raises(cp.CellProvisionOwnershipError):
            await prov.provision_cell(_ident(1, 1))

    asyncio.run(_run())


def test_provision_missing_provider_secret_presence_fails(tmp_path):
    prov = _mk_prov(tmp_path, attestor=FakeBindingAttestor(llm_secret=False))

    async def _run():
        with pytest.raises(cp.CellProviderBindingError):
            await prov.provision_cell(_ident(1, 1))

    asyncio.run(_run())


def test_provision_binding_unverifiable_without_attestor_fails(tmp_path):
    cfg = cp.ProvisionerConfig(
        eval_root=str(tmp_path), startup_timeout_s=1.0, health_poll_interval_s=0.05,
        require_runtime_workspace=False, provider_binding=pb.frozen_provider_binding(),
        require_provider_binding=True,
    )
    prov = cp.LightRagCellProvisioner(
        cfg, process_controller=FakeController(), health_prober=FakeProber(),
        runtime_attestor=None,  # cannot attest binding
    )

    async def _run():
        with pytest.raises(cp.CellProviderBindingError):
            await prov.provision_cell(_ident(1, 1))

    asyncio.run(_run())


# ===========================================================================
# §40 all 8 cells receive the frozen binding via their spec
# ===========================================================================


def test_all_eight_cells_receive_binding(tmp_path):
    ctl = FakeController()
    cfg = cp.ProvisionerConfig(
        eval_root=str(tmp_path), startup_timeout_s=1.0, health_poll_interval_s=0.05,
        require_runtime_workspace=False, provider_binding=pb.frozen_provider_binding(),
        require_provider_binding=True,
    )
    prov = cp.LightRagCellProvisioner(
        cfg, process_controller=ctl, health_prober=FakeProber(),
        runtime_attestor=FakeBindingAttestor(),
    )

    async def _run():
        for c in (1, 2, 4, 8):
            for r in (1, 2):
                ident = _ident(c, r)
                cell = await prov.provision_cell(ident)
                await prov.dispose(ident, cell.to_provision())

    asyncio.run(_run())
    assert len(ctl.started) == 8
    for spec in ctl.started:  # every cell's spec carries the identical frozen binding
        assert spec.provider_binding == pb.frozen_provider_binding()


# ===========================================================================
# §44 compose parity  ·  §29/§30/§53 frozen experiment + retry unchanged
# ===========================================================================


def test_compose_parity_of_binding_vars():
    import pathlib
    compose = pathlib.Path(
        "deploy/graphrag-poc/docker-compose.graphrag.yml"
    ).read_text(encoding="utf-8")
    # every container var the injector sets must be a var the compose config also sets,
    # proving the per-cell path matches the pinned image's binding contract (no drift).
    b = pb.frozen_provider_binding()
    for var in list(b.container_public_env().keys()) + list(
        b.container_secret_env_map().keys()
    ):
        assert var in compose, f"binding var {var} not in compose (drift)"


def test_frozen_experiment_and_retry_unchanged():
    from open_notebook.integrations.graphrag.eval import concurrency_diag08 as cd
    from open_notebook.integrations.graphrag.eval import dataset08 as d
    from open_notebook.integrations.graphrag.eval import index_retry08 as ir
    from open_notebook.integrations.graphrag.eval import live_indexer08 as li

    plan = cd.default_plan()
    assert plan.total_submissions == 30 and cd.MAX_TOTAL_SUBMISSIONS == 64
    assert li.MAX_INDEX_ATTEMPTS_PER_SOURCE == 2
    ok, h = d.verify_integrity()
    assert ok and h == "a58a68535c345e18f0263904f818e4e2068a164056408665d8bb9233eceb143d"
    assert cd.retry_decision("429") == ir.is_transient_reason("429")


# ===========================================================================
# §16/§35/§45/§46/§47 orchestrator gates with the binding
# ===========================================================================


def test_orchestrator_missing_binding_fails(monkeypatch):
    from open_notebook.integrations.graphrag.eval import dataset08 as d
    from open_notebook.integrations.graphrag.eval import live_orchestrator08 as lo

    bench = d.load_benchmark08()
    deps = lo.OrchestratorDeps(
        isolation_runtime_factory=lambda: None, model_seeder=None, model_restorer=None,
        source_preparer=None, provisioner_factory=lambda a, b, c: None,
        client_factory=lambda ep: None, runtime_attestor=object(),
        provider_binding=None,  # missing -> fail closed before isolation/provider
    )
    orch = lo.LiveDiagnosticOrchestrator08(bench, deps)
    with pytest.raises(lo.LiveDiagnosticConfigError):
        asyncio.run(orch.run(working_dir="/wd", authorized_live=True))


def test_orchestrator_missing_secret_reports_name(monkeypatch):
    from open_notebook.integrations.graphrag.eval import dataset08 as d
    from open_notebook.integrations.graphrag.eval import live_orchestrator08 as lo

    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    bench = d.load_benchmark08()
    deps = lo.OrchestratorDeps(
        isolation_runtime_factory=lambda: None, model_seeder=None, model_restorer=None,
        source_preparer=None, provisioner_factory=lambda a, b, c: None,
        client_factory=lambda ep: None, runtime_attestor=object(),
        provider_binding=pb.frozen_provider_binding(),
    )
    orch = lo.LiveDiagnosticOrchestrator08(bench, deps)
    with pytest.raises(lo.LiveDiagnosticConfigError) as ei:
        asyncio.run(orch.run(working_dir="/wd", authorized_live=True))
    assert "REQUIRED_RUNTIME_SECRET_MISSING=OPENROUTER_API_KEY" in str(ei.value)
    assert SECRET not in str(ei.value)


def test_binding_presence_does_not_authorize_live(monkeypatch):
    # §45: valid binding + secret available + authorized_live=False -> deny (no isolation)
    from open_notebook.integrations.graphrag.eval import concurrency_diag08 as cd
    from open_notebook.integrations.graphrag.eval import dataset08 as d
    from open_notebook.integrations.graphrag.eval import live_orchestrator08 as lo

    monkeypatch.setenv("OPENROUTER_API_KEY", SECRET)
    entered = {"iso": False}

    class _Iso:
        async def __aenter__(self):
            entered["iso"] = True
            return object()

        async def __aexit__(self, *a):
            return False

    bench = d.load_benchmark08()
    deps = lo.OrchestratorDeps(
        isolation_runtime_factory=lambda: _Iso(), model_seeder=None, model_restorer=None,
        source_preparer=None, provisioner_factory=lambda a, b, c: None,
        client_factory=lambda ep: None, runtime_attestor=object(),
        provider_binding=pb.frozen_provider_binding(),
    )
    orch = lo.LiveDiagnosticOrchestrator08(bench, deps)
    with pytest.raises(cd.LiveDiagnosticNotAuthorizedError):
        asyncio.run(orch.run(working_dir="/wd", authorized_live=False))
    assert entered["iso"] is False  # no isolation/provider work
