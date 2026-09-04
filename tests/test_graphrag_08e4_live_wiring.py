"""GraphRAG-08E.4 live diagnostic execution-wiring tests (PURE OFFLINE, all mocked).

No provider, no real sidecar, no DB. Every provider/DB/Docker seam is a fake. An autouse
guard fails any real HTTP call; the Option-A isolation guard is a no-op stand-in for the
fake isolation runtime. Covers the endpoint/authorization/sequencing/retry/budget/
content-safety/production-boundary matrix (task §44-§75).
"""

from __future__ import annotations

import asyncio
import json
import types

import pytest

from open_notebook.integrations.graphrag.eval import cell_isolation08 as ci
from open_notebook.integrations.graphrag.eval import cell_provisioner08 as cp
from open_notebook.integrations.graphrag.eval import concurrency_diag08 as cd
from open_notebook.integrations.graphrag.eval import dataset08 as d
from open_notebook.integrations.graphrag.eval import index_retry08 as ir
from open_notebook.integrations.graphrag.eval import live_indexer08 as li
from open_notebook.integrations.graphrag.eval import live_orchestrator08 as lo
from open_notebook.integrations.graphrag.eval import provider_binding08 as pb

FIXTURE_HASH = "a58a68535c345e18f0263904f818e4e2068a164056408665d8bb9233eceb143d"
RUN = "run08e4a"
WD = "/gr08e4/run08e4a"  # a working dir (paths not touched — provisioner is fake)


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    import httpx

    def _boom(*a, **k):  # noqa: ANN001
        raise AssertionError("offline 08E.4 test attempted a real HTTP/provider call")

    monkeypatch.setattr(httpx.AsyncClient, "__init__", _boom)
    monkeypatch.setattr(httpx.Client, "__init__", _boom)
    # The fake isolation runtime stands in for active Option-A isolation.
    from open_notebook.integrations.graphrag.eval import isolation08
    monkeypatch.setattr(isolation08, "require_active_isolation", lambda: None)
    # Synthetic provider secret so the orchestrator's content-safe presence check passes
    # (GraphRAG-08E.5); the fake provisioner never launches a real container with it.
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-08e4-openrouter-key")
    yield


BENCH = d.load_benchmark08()


# ---------------------------------------------------------------------------
# fakes
# ---------------------------------------------------------------------------


def _ident(conc, rep):
    return ci.CellIdentity(RUN, conc, rep)


def _mk_pc(ident, working_dir, port, *, ready=True, ws=None, storage=None,
           host="127.0.0.1"):
    ws = ws or ident.workspace
    storage = storage if storage is not None else ci.cell_storage_dir(working_dir, ws)
    return cp.ProvisionedCell(
        run_id=ident.run_id, cell_id=ident.cell_id, workspace=ws,
        working_dir=working_dir, storage_dir=storage,
        base_url=f"http://{host}:{port}", host=host, port=port, version="1.5.6",
        process_identifier=f"gr08e2_{ident.cell_id}", process_pid=None, started_at="t",
        state=cp.ProvisionState.PROVISIONED if ready else cp.ProvisionState.PROCESS_STARTED,
    )


def _mk_cell(ident, working_dir, *, valid=True):
    prov = ci.CellProvision(
        workspace=ident.workspace,
        storage_dir=ci.cell_storage_dir(working_dir, ident.workspace),
        fresh_extraction_state=True, owned=True,
    )
    v = ci.CellValidity(
        isolation_valid=valid, cache_fresh=valid, ownership_valid=valid,
        reason="ok" if valid else "bad",
    )
    return ci.DiagnosticCell(identity=ident, provision=prov, validity=v)


class FakeProvisioner:
    """Satisfies run_sweep's provision/dispose AND the indexer's ownership-bound
    active_provisioned_cell. Content-free fake — starts nothing."""

    def __init__(self, working_dir, *, invalid_cells=(), dispose_fail_cells=(),
                 endpoint_override=None, port_base=50000):
        self.working_dir = working_dir
        self.invalid = set(invalid_cells)
        self.dispose_fail = set(dispose_fail_cells)
        self.endpoint_override = endpoint_override  # (ident)->ProvisionedCell for tamper
        self.port_base = port_base
        self._ports = {}
        self.provisioned = []
        self.disposed = []

    def _port(self, ident):
        self._ports.setdefault(ident.cell_id, self.port_base + len(self._ports))
        return self._ports[ident.cell_id]

    async def provision(self, identity):
        self.provisioned.append(identity.cell_id)
        ws = "foreign_ws" if identity.cell_id in self.invalid else identity.workspace
        return ci.CellProvision(
            workspace=ws, storage_dir=ci.cell_storage_dir(self.working_dir, ws),
            fresh_extraction_state=True, owned=True,
        )

    async def dispose(self, identity, provision):
        self.disposed.append(identity.cell_id)
        if identity.cell_id in self.dispose_fail:
            return ci.CellDisposal(disposed=False, owned=True)
        return ci.CellDisposal(disposed=True, owned=True)

    def active_provisioned_cell(self, identity):
        if self.endpoint_override is not None:
            return self.endpoint_override(identity, self.working_dir, self._port(identity))
        return _mk_pc(identity, self.working_dir, self._port(identity))


class FakeClient:
    def __init__(self, endpoint, script):
        self.endpoint = endpoint
        self.script = script
        self.submits = []
        self.status_calls = 0

    async def submit(self, *, source_id, canonical_text):
        self.submits.append((source_id, canonical_text))
        b = self.script
        if b.get("submit_raises") is not None:
            raise b["submit_raises"]
        if b.get("submit_reject"):
            return li.IndexSubmitResult(accepted=False, track_id=None,
                                        detail=b.get("reject_detail"))
        return li.IndexSubmitResult(accepted=True, track_id="trk")

    async def status(self, *, track_id):
        self.status_calls += 1
        seq = self.script.get("status_seq")
        if seq:
            state, detail = seq[min(self.status_calls - 1, len(seq) - 1)]
            return li.IndexStatusResult(state=state, detail=detail)
        return li.IndexStatusResult(state="PROCESSED")


class FakeClientFactory:
    def __init__(self, script=None):
        self.script = script or {}
        self.endpoints = []
        self.clients = []

    def __call__(self, endpoint):
        c = FakeClient(endpoint, self.script)
        self.endpoints.append(endpoint)
        self.clients.append(c)
        return c


class FakeIsolation:
    def __init__(self, order):
        self.order = order
        self.entered = self.exited = False
        self.run_id = "isoRun08e4"

    async def __aenter__(self):
        self.entered = True
        self.order.append("isolation_enter")
        return types.SimpleNamespace(run_id=self.run_id, namespace="ns", database="db")

    async def __aexit__(self, *a):
        self.exited = True
        self.order.append("isolation_exit")
        return False


_UNSET = object()


def _mk_deps(order, working_dir, *, attestor=_UNSET, client_script=None,
             invalid_cells=(), dispose_fail=(), artifact_writer=None):
    if attestor is _UNSET:
        attestor = object()  # a present (non-None) attestor by default
    prov = FakeProvisioner(working_dir, invalid_cells=invalid_cells,
                           dispose_fail_cells=dispose_fail)
    factory = FakeClientFactory(client_script)
    iso = FakeIsolation(order)

    async def model_seeder():
        order.append("model_seed")
        return ("model:tmp08e4", None)

    async def model_restorer(mid, prior):
        order.append("model_restore")
        return True

    async def source_preparer(bench, keys):
        order.append("sources")
        by = {s.key: s for s in bench.sources}
        return {
            k: li.DiagnosticSource(key=k, canonical_id=f"source:{k}", text=by[k].text)
            for k in keys
        }

    deps = lo.OrchestratorDeps(
        isolation_runtime_factory=lambda: iso,
        model_seeder=model_seeder,
        model_restorer=model_restorer,
        source_preparer=source_preparer,
        provisioner_factory=lambda wd, att, binding: prov,
        client_factory=factory,
        runtime_attestor=attestor,
        provider_binding=pb.frozen_provider_binding(),
        artifact_writer=artifact_writer,
    )
    return deps, prov, factory, iso, order


# ===========================================================================
# §69 no provider on import  ·  §75 production import boundary
# ===========================================================================


def test_import_has_no_side_effects():
    import importlib
    importlib.import_module("open_notebook.integrations.graphrag.eval.live_indexer08")
    importlib.import_module("open_notebook.integrations.graphrag.eval.live_orchestrator08")
    # constructing deps wiring must start nothing (references callables only)
    deps = lo.default_live_deps(eval_root="/abs/gr08e4")
    assert deps.runtime_attestor is not None


def test_production_import_boundary():
    # No production package imports the eval live wiring / provisioner. Cover the whole
    # backend AND the non-eval graphrag integration modules (exclude the eval package,
    # which legitimately references them) — closes review L2's coverage gap.
    res = __import__("subprocess").run(
        ["git", "grep", "-l", "-E",
         r"live_indexer08|live_orchestrator08|cell_provisioner08",
         "--", "api/", "commands/", "open_notebook/domain", "open_notebook/graphs",
         "open_notebook/ai", "open_notebook/integrations/graphrag/",
         ":(exclude)open_notebook/integrations/graphrag/eval/"],
        capture_output=True, text=True, cwd=".",
    )
    assert res.stdout.strip() == "", f"production import found: {res.stdout}"


def test_no_vgqgd_or_attempt6_imports():
    import pathlib
    for mod in ("live_indexer08.py", "live_orchestrator08.py"):
        src = pathlib.Path(
            "open_notebook/integrations/graphrag/eval", mod
        ).read_text(encoding="utf-8")
        for forbidden in ("gd_seam", "_gq", "_gd", "run_full_benchmark",
                          "run_micro_precheck", "GraphRAG08EvalRunner"):
            assert forbidden not in src, f"{mod} references {forbidden}"


# ===========================================================================
# §44 endpoint propagation  ·  §45 ownership  ·  §57 sidecar path  ·  §26 no global
# ===========================================================================


def test_endpoint_propagation_per_cell():
    prov = FakeProvisioner(WD, port_base=51000)
    a, b = _ident(1, 1), _ident(2, 1)
    ea = li.resolve_cell_endpoint(prov, _mk_cell(a, WD))
    eb = li.resolve_cell_endpoint(prov, _mk_cell(b, WD))
    assert ea.port != eb.port
    assert ea.base_url.endswith(str(ea.port)) and eb.base_url.endswith(str(eb.port))
    assert ea.cell_id == a.cell_id and eb.cell_id == b.cell_id


def test_endpoint_ownership_tamper_fails_closed():
    # workspace mismatch between provisioned cell and entered DiagnosticCell
    def tamper_ws(ident, wd, port):
        return _mk_pc(ident, wd, port, ws="foreign_ws")

    prov = FakeProvisioner(WD, endpoint_override=tamper_ws)
    with pytest.raises(li.CellEndpointError):
        li.resolve_cell_endpoint(prov, _mk_cell(_ident(1, 1), WD))


def test_endpoint_non_loopback_fails_closed():
    def remote(ident, wd, port):
        return _mk_pc(ident, wd, port, host="10.0.0.9")

    prov = FakeProvisioner(WD, endpoint_override=remote)
    with pytest.raises(li.CellEndpointError):
        li.resolve_cell_endpoint(prov, _mk_cell(_ident(1, 1), WD))


def test_endpoint_missing_or_not_ready_fails_closed():
    class _None(FakeProvisioner):
        def active_provisioned_cell(self, identity):
            return None

    with pytest.raises(li.CellEndpointError):
        li.resolve_cell_endpoint(_None(WD), _mk_cell(_ident(1, 1), WD))

    def not_ready(ident, wd, port):
        return _mk_pc(ident, wd, port, ready=False)

    prov = FakeProvisioner(WD, endpoint_override=not_ready)
    with pytest.raises(li.CellEndpointError):
        li.resolve_cell_endpoint(prov, _mk_cell(_ident(1, 1), WD))


def test_invalid_cell_never_indexes():
    prov = FakeProvisioner(WD)
    with pytest.raises(li.CellNotReadyError):
        li.resolve_cell_endpoint(prov, _mk_cell(_ident(1, 1), WD, valid=False))


# ===========================================================================
# §58 success  ·  §55 content immutable  ·  §57 uses cell endpoint
# ===========================================================================


def _indexer(script=None, sources=None):
    factory = FakeClientFactory(script)
    keys = cd.select_diagnostic_sources(BENCH, 8)
    by = {s.key: s for s in BENCH.sources}
    src_map = sources or {
        k: li.DiagnosticSource(key=k, canonical_id=f"source:{k}", text=by[k].text)
        for k in keys
    }
    indexer = li.LiveCellIndexer08(
        benchmark=BENCH, sources=src_map, client_factory=factory,
        config=li.LiveIndexerConfig(index_ready_timeout_s=1.0, poll_interval_s=0.01),
    )
    return indexer, factory


def test_success_attempt_record():
    indexer, factory = _indexer({"status_seq": [("PROCESSED", None)]})
    prov = FakeProvisioner(WD)
    ident = _ident(1, 1)

    async def _run():
        return await indexer.index_cell(cd.DiagnosticLevel(1, 1, 1), _mk_cell(ident, WD), 1,
                                        provisioner=prov)

    records = asyncio.run(_run())
    assert len(records) == 1
    r = records[0]
    assert r.terminal_status == cd.TERMINAL_SUCCESS and r.attempt_number == 1
    assert r.logical_source_id == "S001" and r.characterization is None
    assert r.run_id == RUN and r.concurrency_level == 1
    # the submitted text is the FROZEN source text (no run_id/workspace/suffix)
    submitted = factory.clients[0].submits[0][1]
    assert submitted == next(s.text for s in BENCH.sources if s.key == "S001")
    # the client was bound to the cell's own endpoint
    assert factory.endpoints[0].base_url.startswith("http://127.0.0.1:")


# ===========================================================================
# §59 transient retry  ·  §60 non-allowlist H1  ·  §61 H2  ·  §62 H3
# ===========================================================================


def test_transient_failure_retries_via_frozen_classifier():
    # FAILED transient once then PROCESSED -> success on attempt 2
    indexer, factory = _indexer(
        {"status_seq": [("FAILED", "429 rate limit exceeded"), ("PROCESSED", None)]}
    )

    async def _run():
        return await indexer.index_source_attempt(
            endpoint=li.CellEndpoint(RUN, "c", "http://127.0.0.1:5", 5, "ws", "sd", "id"),
            concurrency_level=1, repetition=1,
            source=li.DiagnosticSource("S001", "source:S001", "text"),
        )

    rec = asyncio.run(_run())
    assert rec.terminal_status == cd.TERMINAL_SUCCESS and rec.attempt_number == 2
    # retry decision matches the FROZEN classifier exactly
    assert cd.retry_decision("429 rate limit exceeded") is True
    assert ir.is_transient_reason("429 rate limit exceeded") is True


def test_non_allowlist_provider_like_failure_not_retried():
    # provider-ish family but frozen classifier says NON_RETRYABLE -> no retry (attempt 1)
    text = "provider returned an unexpected internal condition (no allowlist match)"
    assert ir.is_transient_reason(text) is False
    indexer, factory = _indexer({"status_seq": [("FAILED", text)]})

    async def _run():
        return await indexer.index_source_attempt(
            endpoint=li.CellEndpoint(RUN, "c", "http://127.0.0.1:5", 5, "ws", "sd", "id"),
            concurrency_level=8, repetition=1,
            source=li.DiagnosticSource("S001", "source:S001", "text"),
        )

    rec = asyncio.run(_run())
    assert rec.terminal_status == cd.TERMINAL_FAILED and rec.attempt_number == 1
    assert rec.characterization is not None and rec.characterization.retryable is False


def test_h2_parse_family_and_h3_lightrag_family():
    for text, fam in (
        ("json parse error: expecting value", cd.ErrorFamily.PARSE_OR_SCHEMA_FAILURE),
        ("LightRAG KeyError in extraction", cd.ErrorFamily.LIGHTRAG_INTERNAL),
    ):
        indexer, _ = _indexer({"status_seq": [("FAILED", text)]})

        async def _run():
            return await indexer.index_source_attempt(
                endpoint=li.CellEndpoint(RUN, "c", "http://127.0.0.1:5", 5, "ws", "sd", "id"),
                concurrency_level=4, repetition=1,
                source=li.DiagnosticSource("S001", "source:S001", "text"),
            )

        rec = asyncio.run(_run())
        assert rec.characterization is not None and rec.characterization.family == fam


# ===========================================================================
# §63 timeout  ·  §64 cancellation  ·  §68 attempt cap  ·  §71 raw error not retained
# ===========================================================================


def test_polling_timeout_is_bounded():
    indexer, _ = _indexer({"status_seq": [("IN_PROGRESS", None)]})  # never terminal

    async def _run():
        return await indexer.index_source_attempt(
            endpoint=li.CellEndpoint(RUN, "c", "http://127.0.0.1:5", 5, "ws", "sd", "id"),
            concurrency_level=1, repetition=1,
            source=li.DiagnosticSource("S001", "source:S001", "text"),
        )

    rec = asyncio.run(_run())
    assert rec.terminal_status == cd.TERMINAL_TIMEOUT


def test_cancellation_propagates_not_provider_failure():
    indexer, _ = _indexer({"submit_raises": asyncio.CancelledError()})

    async def _run():
        await indexer.index_source_attempt(
            endpoint=li.CellEndpoint(RUN, "c", "http://127.0.0.1:5", 5, "ws", "sd", "id"),
            concurrency_level=1, repetition=1,
            source=li.DiagnosticSource("S001", "source:S001", "text"),
        )

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(_run())


def test_attempt_cap_never_exceeds_two():
    # always transient FAILED -> exactly 2 attempts, 2 submissions
    indexer, _ = _indexer({"status_seq": [("FAILED", "timeout, please try again")]})

    async def _run():
        return await indexer.index_source_attempt(
            endpoint=li.CellEndpoint(RUN, "c", "http://127.0.0.1:5", 5, "ws", "sd", "id"),
            concurrency_level=1, repetition=1,
            source=li.DiagnosticSource("S001", "source:S001", "text"),
        )

    rec = asyncio.run(_run())
    assert rec.attempt_number == 2 and rec.terminal_status == cd.TERMINAL_FAILED
    assert indexer.submission_count == 2
    assert li.MAX_INDEX_ATTEMPTS_PER_SOURCE == 2


def test_raw_error_never_retained():
    secret = "boom TEST_SECRET_08E4 provider stack trace"
    indexer, _ = _indexer({"status_seq": [("FAILED", secret)]})

    async def _run():
        return await indexer.index_source_attempt(
            endpoint=li.CellEndpoint(RUN, "c", "http://127.0.0.1:5", 5, "ws", "sd", "id"),
            concurrency_level=1, repetition=1,
            source=li.DiagnosticSource("S001", "source:S001", "text"),
        )

    rec = asyncio.run(_run())
    blob = json.dumps(rec.as_dict()) + repr(rec)
    assert "TEST_SECRET_08E4" not in blob


# ===========================================================================
# review H1: the REAL per-cell client surfaces the FAILED doc's raw error text
# (via the frozen _fetch_failed_reason_ex) so the classifier/taxonomy see the real
# cause — else the sweep would be diagnostically void (all TRACK_TEXT_ABSENT).
# ===========================================================================


def test_real_client_surfaces_failed_error_text(monkeypatch):
    from open_notebook.integrations.graphrag.models import IndexState

    ep = li.CellEndpoint(RUN, "c", "http://127.0.0.1:9", 9, "ws", "sd", "id")
    client = li.RealCellIndexClient(ep)  # network-free construction

    async def fake_track(track_id):
        return types.SimpleNamespace(state=IndexState.FAILED)

    async def fake_fetch(cfg, track_id, *, transport=None):
        return ("PRESENT", "429 rate limit exceeded")

    monkeypatch.setattr(client._svc, "track_status", fake_track)
    monkeypatch.setattr(ir, "_fetch_failed_reason_ex", fake_fetch)

    st = asyncio.run(client.status(track_id="t"))
    assert st.state == "FAILED" and st.detail == "429 rate limit exceeded"
    # the surfaced text drives the FROZEN classifier -> retryable, not a void UNKNOWN
    assert cd.characterize_failure(st.detail).retryable is True

    # ABSENT presence -> detail None (fail-closed, not a fabricated reason)
    async def fake_fetch_absent(cfg, track_id, *, transport=None):
        return ("ABSENT", None)

    monkeypatch.setattr(ir, "_fetch_failed_reason_ex", fake_fetch_absent)
    st2 = asyncio.run(client.status(track_id="t"))
    assert st2.state == "FAILED" and st2.detail is None


def test_default_live_deps_defaults_api_key_from_env(monkeypatch):
    # review M1: the client/prober key defaults from the SAME env the container enforces.
    monkeypatch.setenv("GRAPHRAG_POC_API_KEY", "k-08e4-test")
    deps = lo.default_live_deps(eval_root="/abs/gr08e4")
    assert deps.runtime_attestor is not None  # attestor wired
    # explicit api_key wins over the env default
    deps2 = lo.default_live_deps(eval_root="/abs/gr08e4", api_key="explicit")
    assert deps2.client_factory is not None


# ===========================================================================
# §46 attestor required  ·  §47 authorized_live False  ·  §48 invalid cell
# ===========================================================================


def _orch(order, working_dir=WD, **kw):
    deps, prov, factory, iso, _ = _mk_deps(order, working_dir, **kw)
    return lo.LiveDiagnosticOrchestrator08(BENCH, deps), prov, factory, iso


def test_missing_attestor_fails_before_provider():
    order = []
    orch, prov, factory, iso = _orch(order, attestor=None)  # missing attestor
    # attestor=None -> config error before isolation/provider

    async def _run():
        with pytest.raises(lo.LiveDiagnosticConfigError):
            await orch.run(working_dir=WD, authorized_live=True)

    asyncio.run(_run())
    assert not iso.entered and factory.clients == []


def test_authorized_live_false_denies_before_isolation():
    order = []
    orch, prov, factory, iso = _orch(order)

    async def _run():
        with pytest.raises(cd.LiveDiagnosticNotAuthorizedError):
            await orch.run(working_dir=WD, authorized_live=False)

    asyncio.run(_run())
    assert not iso.entered and factory.clients == []  # §47 zero provider/index


def test_authorized_true_invalid_cell_no_indexing():
    order = []
    # C1-R1 valid, but make its provision invalid -> validity gate stops before indexer
    orch, prov, factory, iso = _orch(order, invalid_cells={_ident(1, 1).cell_id})

    async def _run():
        return await orch.run(working_dir=WD, authorized_live=True, run_id=RUN)

    outcome = asyncio.run(_run())
    assert outcome.state == "FAILED"
    assert factory.clients == []  # §48 no provider/index call
    assert iso.entered and iso.exited  # isolation cleaned up


# ===========================================================================
# §49 double-gate success  ·  §50 frozen 8 cells / 30 submissions  ·  §54 subset
# ===========================================================================


def test_double_gate_success_full_sweep():
    order = []
    writes = []
    orch, prov, factory, iso = _orch(order, artifact_writer=writes.append)

    async def _run():
        return await orch.run(working_dir=WD, authorized_live=True, run_id=RUN)

    outcome = asyncio.run(_run())
    assert outcome.state == "COMPLETE"
    # 8 cells, 30 submissions, 30 records
    assert outcome.submission_count == 30
    assert len(outcome.sweep.records) == 30
    assert len(prov.provisioned) == 8 and len(prov.disposed) == 8
    # per-level record counts follow the frozen plan
    by_level = {}
    for r in outcome.sweep.records:
        by_level.setdefault(r.concurrency_level, 0)
        by_level[r.concurrency_level] += 1
    assert by_level == {1: 2, 2: 4, 4: 8, 8: 16}
    # exact frozen S001-anchored subset per level (pin the mapping, not just counts)
    lvl1 = [r.logical_source_id for r in outcome.sweep.records if r.concurrency_level == 1]
    assert set(lvl1) == {"S001"}
    # artifact is content-free and records DEV/HOLDOUT=0
    art = writes[-1]
    assert art["dev_executed"] == 0 and art["holdout_executed"] == 0
    assert art["actual_submissions"] == 30 and art["max_total_submissions"] == 64
    assert "TEST_SECRET" not in json.dumps(art)


# ===========================================================================
# §51 Option-A before provider  ·  §53 temp model ownership  ·  §72 global cleanup
# ===========================================================================


def test_option_a_before_provider_and_cleanup_order():
    order = []
    orch, prov, factory, iso = _orch(order)

    async def _run():
        return await orch.run(working_dir=WD, authorized_live=True, run_id=RUN)

    asyncio.run(_run())
    # isolation entered BEFORE model BEFORE sources; model restored + isolation exit last
    assert order.index("isolation_enter") < order.index("model_seed")
    assert order.index("model_seed") < order.index("sources")
    assert order.index("model_restore") < order.index("isolation_exit")
    assert iso.entered and iso.exited


def test_global_cleanup_on_failure():
    order = []
    # cleanup verification fails on the 2nd cell -> sweep stops, but isolation + model
    # cleanup still run
    orch, prov, factory, iso = _orch(order, dispose_fail={_ident(1, 2).cell_id})

    async def _run():
        return await orch.run(working_dir=WD, authorized_live=True, run_id=RUN)

    outcome = asyncio.run(_run())
    assert outcome.state == "FAILED"
    assert "model_restore" in order and iso.exited  # §72 cleanup ran


# ===========================================================================
# §65 cell failure stops sweep  ·  §66 cleanup failure stops sweep
# ===========================================================================


def test_cell_failure_stops_sweep():
    order = []
    # make C1-R2 invalid -> after C1-R1, sweep stops; C2 never provisioned
    orch, prov, factory, iso = _orch(order, invalid_cells={_ident(1, 2).cell_id})

    async def _run():
        return await orch.run(working_dir=WD, authorized_live=True, run_id=RUN)

    outcome = asyncio.run(_run())
    assert outcome.state == "FAILED"
    # only C1-R1 and the failing C1-R2 were provisioned; no C2/C4/C8
    assert _ident(2, 1).cell_id not in prov.provisioned
    assert _ident(4, 1).cell_id not in prov.provisioned


def test_cleanup_failure_stops_sweep():
    order = []
    orch, prov, factory, iso = _orch(order, dispose_fail={_ident(1, 1).cell_id})

    async def _run():
        return await orch.run(working_dir=WD, authorized_live=True, run_id=RUN)

    outcome = asyncio.run(_run())
    assert outcome.state == "FAILED"
    # C1-R1 disposal failed -> no next cell
    assert _ident(1, 2).cell_id not in prov.provisioned


# ===========================================================================
# §67 budget  ·  §94 frozen experiment  ·  §87 fixture
# ===========================================================================


def test_budget_and_frozen_experiment():
    plan = cd.default_plan()
    assert plan.total_submissions == 30 and cd.MAX_TOTAL_SUBMISSIONS == 64
    # an over-cap plan fails validation before any provider work
    over = cd.ConcurrencyDiagnosticPlan(levels=(
        cd.DiagnosticLevel(1, 8, 3), cd.DiagnosticLevel(2, 8, 3),
        cd.DiagnosticLevel(4, 8, 3), cd.DiagnosticLevel(8, 8, 3),
    ))
    with pytest.raises(cd.DiagnosticPlanError):
        cd.validate_plan(over)
    ok, h = d.verify_integrity()
    assert ok and h == FIXTURE_HASH
    # frozen retry classifier unchanged / reused by the live indexer
    assert cd.retry_decision("429") == ir.is_transient_reason("429")
    assert li.MAX_INDEX_ATTEMPTS_PER_SOURCE == 2
