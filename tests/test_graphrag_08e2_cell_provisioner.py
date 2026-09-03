"""GraphRAG-08E.2 live cell-provisioner tests (PURE OFFLINE, all mocked).

No provider, no real sidecar, no DB. The process/health primitives are INJECTED
fakes; a real local child-process tree is used ONLY to prove the process-tree kill
abstraction (§49) — never a LightRAG/provider process. An autouse guard fails any test
that opens an httpx client, so an accidental provider/network call cannot pass silently
(§57). Covers task §40-§61 plus the happy-path lifecycle and run_sweep integration.
"""

from __future__ import annotations

import asyncio
import os
import socket
import sys
import textwrap

import pytest

from open_notebook.integrations.graphrag.eval import cell_isolation08 as ci
from open_notebook.integrations.graphrag.eval import cell_provisioner08 as cp
from open_notebook.integrations.graphrag.eval import concurrency_diag08 as cd
from open_notebook.integrations.graphrag.eval import dataset08 as d

FIXTURE_HASH = "a58a68535c345e18f0263904f818e4e2068a164056408665d8bb9233eceb143d"
RUN = "run08e2a"
# Real LightRAG v1.5.6 /health reports the release WITHOUT a leading "v" (confirmed by
# the 08E Stage-A real-sidecar smoke). The fake mirrors that reality; the provisioner's
# pinned expected_version ("v1.5.6") must canonicalize to it (GraphRAG-08E.3).
VERSION = "1.5.6"


# ---------------------------------------------------------------------------
# provider/network guard (§57): any real httpx use fails the test.
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    import httpx

    def _boom(*a, **k):  # noqa: ANN001
        raise AssertionError("offline 08E.2 test attempted a real HTTP/provider call")

    monkeypatch.setattr(httpx.AsyncClient, "__init__", _boom)
    monkeypatch.setattr(httpx.Client, "__init__", _boom)
    yield


# ---------------------------------------------------------------------------
# Fakes: a shared cluster the fake controller writes and the fake prober reads.
# ---------------------------------------------------------------------------


class FakeCluster:
    def __init__(
        self,
        *,
        version=VERSION,
        healthy_after=0,
        never_healthy=False,
        force_workspace="__ECHO__",
        report_workspace=True,
    ):
        self.by_port: dict = {}
        self.version = version
        self.healthy_after = healthy_after
        self.never_healthy = never_healthy
        self.force_workspace = force_workspace
        self.report_workspace = report_workspace
        self.started: list = []
        self.terminated: list = []
        self.probe_counts: dict = {}


class FakeController:
    def __init__(self, cluster, *, start_raises=False, never_dies=False, pid=4321):
        self.c = cluster
        self.start_raises = start_raises
        self.never_dies = never_dies
        self.pid = pid
        self._alive: dict = {}  # identifier -> bool (instance-scoped, no cross-test leak)

    async def start(self, spec):
        if self.start_raises:
            raise RuntimeError("fake spawn failure")
        self.c.started.append(spec.cell_id)
        self.c.by_port[spec.port] = {"workspace": spec.workspace}
        handle = cp.CellProcessHandle(
            identifier=f"fake_{spec.cell_id}", kind="fake", pid=self.pid
        )
        self._alive[handle.identifier] = True
        return handle

    async def is_alive(self, handle):
        return self._alive.get(handle.identifier, False)

    async def terminate(self, handle, *, graceful_timeout_s):
        self.c.terminated.append(handle.identifier)
        if self.never_dies:
            return cp.TerminationResult(stopped=False, forced=True)
        self._alive[handle.identifier] = False
        return cp.TerminationResult(stopped=True, forced=False)


class FakeProber:
    def __init__(self, cluster):
        self.c = cluster

    async def probe(self, *, base_url, host, port):
        rec = self.c.by_port.get(port)
        n = self.c.probe_counts.get(port, 0) + 1
        self.c.probe_counts[port] = n
        if rec is None:
            return cp.CellHealthObservation(
                reachable=False, healthy=False, version=None,
                reported_workspace=None, working_dir=None,
            )
        healthy = (not self.c.never_healthy) and (n > self.c.healthy_after)
        ws = None
        if self.c.report_workspace:
            ws = (
                rec["workspace"]
                if self.c.force_workspace == "__ECHO__"
                else self.c.force_workspace
            )
        return cp.CellHealthObservation(
            reachable=True, healthy=healthy, version=self.c.version,
            reported_workspace=ws, working_dir=None,
        )


def _mk(cluster, tmp_path, controller=None, **cfg):
    ctl = controller or FakeController(cluster)
    prober = FakeProber(cluster)
    config = cp.ProvisionerConfig(eval_root=str(tmp_path), **cfg)
    prov = cp.LightRagCellProvisioner(
        config, process_controller=ctl, health_prober=prober
    )
    return prov, ctl


def _ident(conc=1, rep=1):
    return ci.CellIdentity(RUN, conc, rep)


# ---------------------------------------------------------------------------
# happy path + state machine
# ---------------------------------------------------------------------------


def test_happy_path_provision_ready_then_dispose(tmp_path):
    cluster = FakeCluster()
    prov, ctl = _mk(cluster, tmp_path, startup_timeout_s=1.0, health_poll_interval_s=0.05)

    async def _run():
        ident = _ident(2, 1)
        cell = await prov.provision_cell(ident)
        assert cell.ready and cell.state == cp.ProvisionState.PROVISIONED
        assert cell.workspace == ident.workspace
        assert cell.version == VERSION
        assert cell.base_url.endswith(str(cell.port))
        assert os.path.isdir(cell.storage_dir)
        ep = await prov.endpoint(ident)
        assert ep is not None and ep[1] == cell.port
        disposal = await prov.dispose(ident, cell.to_provision())
        assert disposal.owned and disposal.disposed
        assert not os.path.exists(cell.storage_dir)  # cell workspace disposed
        assert os.path.isdir(cell.working_dir)        # run root NOT deleted
        return cell

    cell = asyncio.run(_run())
    assert cluster.started == [cell.cell_id]
    assert cluster.terminated == [cell.process_identifier]


def test_health_becomes_ready_after_polls(tmp_path):
    cluster = FakeCluster(healthy_after=2)  # unhealthy for 2 probes, then healthy
    prov, _ = _mk(cluster, tmp_path, startup_timeout_s=2.0, health_poll_interval_s=0.05)

    async def _run():
        cell = await prov.provision_cell(_ident(1, 1))
        return cell.ready

    assert asyncio.run(_run()) is True


# ---------------------------------------------------------------------------
# §40 unique workspace / storage / identity for all eight frozen cells
# ---------------------------------------------------------------------------


def test_all_eight_cells_unique_paths(tmp_path):
    root = str(tmp_path)
    cells = [_ident(c, r) for c in (1, 2, 4, 8) for r in (1, 2)]
    triples = [
        cp.assert_cell_paths_safe(
            eval_root=root, run_id=c.run_id, workspace=c.workspace
        )
        for c in cells
    ]
    storages = [t[2] for t in triples]
    assert len(storages) == 8 and len(set(storages)) == 8
    working = {t[1] for t in triples}
    assert len(working) == 1  # one shared run-owned working dir
    for st in storages:
        assert cp.is_within_root(st, next(iter(working)))


# ---------------------------------------------------------------------------
# §41 pre-existing workspace → fail closed BEFORE process use
# ---------------------------------------------------------------------------


def test_preexisting_workspace_fails_closed_before_start(tmp_path):
    cluster = FakeCluster()
    prov, ctl = _mk(cluster, tmp_path)
    ident = _ident(1, 1)
    _, _, storage = cp.assert_cell_paths_safe(
        eval_root=str(tmp_path), run_id=ident.run_id, workspace=ident.workspace
    )
    os.makedirs(storage)  # synthetic pre-existing workspace

    async def _run():
        with pytest.raises(cp.CellFreshnessError):
            await prov.provision_cell(ident)

    asyncio.run(_run())
    assert cluster.started == []  # no process ever started
    assert os.path.isdir(storage)  # a pre-existing dir is NOT silently deleted


# ---------------------------------------------------------------------------
# §42 nonempty owned-looking workspace (cache file) → reject, no process
# §43 cache-file physical freshness → FAIL
# ---------------------------------------------------------------------------


def test_nonempty_workspace_with_cache_rejected(tmp_path):
    cluster = FakeCluster()
    prov, _ = _mk(cluster, tmp_path)
    ident = _ident(4, 2)
    _, _, storage = cp.assert_cell_paths_safe(
        eval_root=str(tmp_path), run_id=ident.run_id, workspace=ident.workspace
    )
    os.makedirs(storage)
    with open(os.path.join(storage, "kv_store_llm_response_cache.json"), "w") as fh:
        fh.write("{}")

    async def _run():
        with pytest.raises(cp.CellFreshnessError):
            await prov.provision_cell(ident)

    asyncio.run(_run())
    assert cluster.started == []


def test_scan_workspace_freshness_detects_stale_cache(tmp_path):
    storage = str(tmp_path / "ws")
    os.makedirs(storage)
    with open(os.path.join(storage, "kv_store_llm_response_cache.json"), "w") as fh:
        fh.write("{}")
    res = cp.scan_workspace_freshness(storage)
    assert res.dir_existed and not res.fresh
    assert "kv_store_llm_response_cache.json" in res.offending_stores
    # absent dir is fresh; empty dir is (owned-reservation) fresh
    assert cp.scan_workspace_freshness(str(tmp_path / "absent")).fresh
    empty = str(tmp_path / "empty")
    os.makedirs(empty)
    assert cp.scan_workspace_freshness(empty).fresh


# ---------------------------------------------------------------------------
# §44 partial start failure → storage cleanup attempted + verified, no residue
# ---------------------------------------------------------------------------


def test_partial_start_failure_self_cleans(tmp_path):
    cluster = FakeCluster()
    ctl = FakeController(cluster, start_raises=True)
    prov, _ = _mk(cluster, tmp_path, controller=ctl)
    ident = _ident(2, 1)
    _, _, storage = cp.assert_cell_paths_safe(
        eval_root=str(tmp_path), run_id=ident.run_id, workspace=ident.workspace
    )

    async def _run():
        with pytest.raises(cp.CellProcessStartError):
            await prov.provision_cell(ident)

    asyncio.run(_run())
    assert not os.path.exists(storage)  # reserved storage disposed on failure
    assert ident.cell_id not in prov._active


# ---------------------------------------------------------------------------
# §45 health failure → bounded timeout, process terminated, storage disposed
# ---------------------------------------------------------------------------


def test_health_never_ready_times_out_and_cleans(tmp_path):
    cluster = FakeCluster(never_healthy=True)
    ctl = FakeController(cluster)
    prov, _ = _mk(
        cluster, tmp_path, controller=ctl,
        startup_timeout_s=0.2, health_poll_interval_s=0.05,
    )
    ident = _ident(1, 1)
    _, _, storage = cp.assert_cell_paths_safe(
        eval_root=str(tmp_path), run_id=ident.run_id, workspace=ident.workspace
    )

    async def _run():
        with pytest.raises(cp.CellHealthError):
            await prov.provision_cell(ident)

    asyncio.run(_run())
    assert cluster.terminated  # process was terminated
    assert not os.path.exists(storage)  # storage disposed
    assert ident.cell_id not in prov._active


# ---------------------------------------------------------------------------
# M1 (review): a raising injected primitive still self-cleans (atomic rollback on
# ANY exception, not only CellProvisionError). Pins the LOW-2 guarantee.
# ---------------------------------------------------------------------------


class _RaisingProber:
    async def probe(self, *, base_url, host, port):
        raise RuntimeError("prober boom")  # a NON-CellProvisionError


class _RaisingAllocator:
    def allocate(self, host):
        raise RuntimeError("allocate boom")  # a NON-CellProvisionError


def test_raising_prober_still_self_cleans(tmp_path):
    cluster = FakeCluster()
    ctl = FakeController(cluster)
    prober = _RaisingProber()
    config = cp.ProvisionerConfig(
        eval_root=str(tmp_path), startup_timeout_s=0.2, health_poll_interval_s=0.05
    )
    prov = cp.LightRagCellProvisioner(
        config, process_controller=ctl, health_prober=prober
    )
    ident = _ident(1, 1)
    _, _, storage = cp.assert_cell_paths_safe(
        eval_root=str(tmp_path), run_id=ident.run_id, workspace=ident.workspace
    )

    async def _run():
        with pytest.raises(cp.CellProvisionError):  # normalised to CellHealthError
            await prov.provision_cell(ident)

    asyncio.run(_run())
    assert cluster.terminated              # started process was torn down
    assert not os.path.exists(storage)     # reserved workspace disposed
    assert ident.cell_id not in prov._active


def test_raising_allocator_non_provision_error_still_self_cleans(tmp_path):
    # A NON-CellProvisionError raised after storage reservation must STILL trigger
    # atomic rollback (the M1 defect: rollback used to fire only on CellProvisionError).
    cluster = FakeCluster()
    ctl = FakeController(cluster)
    config = cp.ProvisionerConfig(eval_root=str(tmp_path))
    prov = cp.LightRagCellProvisioner(
        config, process_controller=ctl, health_prober=FakeProber(cluster),
        port_allocator=_RaisingAllocator(),
    )
    ident = _ident(2, 1)
    _, _, storage = cp.assert_cell_paths_safe(
        eval_root=str(tmp_path), run_id=ident.run_id, workspace=ident.workspace
    )

    async def _run():
        with pytest.raises(RuntimeError):  # raw non-provision error re-raised
            await prov.provision_cell(ident)

    asyncio.run(_run())
    assert not os.path.exists(storage)     # reserved workspace disposed on rollback
    assert cluster.started == []           # never reached process start
    assert ident.cell_id not in prov._active


# ---------------------------------------------------------------------------
# §46 version mismatch → fail, terminate, dispose, verify (no fallback)
# ---------------------------------------------------------------------------


def test_version_mismatch_fails_and_cleans(tmp_path):
    cluster = FakeCluster(version="v9.9.9")
    ctl = FakeController(cluster)
    prov, _ = _mk(cluster, tmp_path, controller=ctl, startup_timeout_s=1.0,
                  health_poll_interval_s=0.05)
    ident = _ident(2, 2)
    _, _, storage = cp.assert_cell_paths_safe(
        eval_root=str(tmp_path), run_id=ident.run_id, workspace=ident.workspace
    )

    async def _run():
        with pytest.raises(cp.CellVersionMismatchError):
            await prov.provision_cell(ident)

    asyncio.run(_run())
    assert cluster.terminated and not os.path.exists(storage)


# ---------------------------------------------------------------------------
# §47 workspace mismatch (and unverifiable) → fail closed + cleanup
# ---------------------------------------------------------------------------


def test_workspace_mismatch_fails_closed(tmp_path):
    cluster = FakeCluster(force_workspace="foreign_ws")
    ctl = FakeController(cluster)
    prov, _ = _mk(cluster, tmp_path, controller=ctl, startup_timeout_s=1.0,
                  health_poll_interval_s=0.05)
    ident = _ident(4, 1)
    _, _, storage = cp.assert_cell_paths_safe(
        eval_root=str(tmp_path), run_id=ident.run_id, workspace=ident.workspace
    )

    async def _run():
        with pytest.raises(cp.CellWorkspaceMismatchError):
            await prov.provision_cell(ident)

    asyncio.run(_run())
    assert cluster.terminated and not os.path.exists(storage)


def test_workspace_unverifiable_blocks_readiness(tmp_path):
    cluster = FakeCluster(report_workspace=False)  # health cannot report workspace
    prov, _ = _mk(cluster, tmp_path, startup_timeout_s=1.0, health_poll_interval_s=0.05)

    async def _run():
        with pytest.raises(cp.CellWorkspaceMismatchError):
            await prov.provision_cell(_ident(1, 1))

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# §48 port already occupied → fail BEFORE start, no foreign process touched
# ---------------------------------------------------------------------------


class _FixedPortAllocator:
    def __init__(self, port):
        self.port = port

    def allocate(self, host):
        return self.port


def test_occupied_port_fails_before_start(tmp_path):
    cluster = FakeCluster()
    ctl = FakeController(cluster)
    prober = FakeProber(cluster)
    # bind a real loopback socket to occupy a port
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    s.listen(1)
    occupied = s.getsockname()[1]
    try:
        config = cp.ProvisionerConfig(eval_root=str(tmp_path))
        prov = cp.LightRagCellProvisioner(
            config, process_controller=ctl, health_prober=prober,
            port_allocator=_FixedPortAllocator(occupied),
        )

        async def _run():
            with pytest.raises(cp.CellProvisionConfigurationError):
                await prov.provision_cell(_ident(1, 1))

        asyncio.run(_run())
        assert cluster.started == []  # foreign process never targeted, no start
    finally:
        s.close()


# ---------------------------------------------------------------------------
# §49 process-tree cleanup on a REAL local fake child tree (no provider)
# ---------------------------------------------------------------------------


def test_process_tree_controller_kills_parent_and_child(tmp_path):
    # Parent spawns a long-lived child, prints the child pid, then sleeps.
    script = textwrap.dedent(
        """
        import subprocess, sys, time
        child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"])
        print(child.pid, flush=True)
        time.sleep(120)
        """
    )
    parent = __import__("subprocess").Popen(
        [sys.executable, "-c", script],
        stdout=__import__("subprocess").PIPE,
        text=True,
    )
    try:
        line = parent.stdout.readline().strip()
        child_pid = int(line)
        assert cp.ProcessTreeController.is_alive(parent.pid)
        assert cp.ProcessTreeController.is_alive(child_pid)
        res = cp.ProcessTreeController.terminate_tree(parent.pid, graceful_timeout_s=8.0)
        assert res.stopped
        assert not cp.ProcessTreeController.is_alive(parent.pid)
        # The child of the killed tree must also be gone (the /T tree kill / killpg).
        assert not cp.ProcessTreeController.is_alive(child_pid)
    finally:
        try:
            parent.kill()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# §50 double cleanup → idempotent, no broader deletion
# ---------------------------------------------------------------------------


def test_double_cleanup_idempotent(tmp_path):
    cluster = FakeCluster()
    prov, _ = _mk(cluster, tmp_path, startup_timeout_s=1.0, health_poll_interval_s=0.05)

    async def _run():
        ident = _ident(1, 1)
        cell = await prov.provision_cell(ident)
        d1 = await prov.dispose(ident, cell.to_provision())
        d2 = await prov.dispose(ident, cell.to_provision())  # second time
        return cell, d1, d2

    cell, d1, d2 = asyncio.run(_run())
    assert d1.disposed and d1.owned
    assert d2.disposed and d2.owned  # safe no-op, not a broader delete
    assert os.path.isdir(cell.working_dir)  # run root intact


# ---------------------------------------------------------------------------
# §51 wrong ownership → fail closed, no foreign/owned resource deleted
# ---------------------------------------------------------------------------


def test_wrong_ownership_dispose_fails_closed(tmp_path):
    cluster = FakeCluster()
    prov, _ = _mk(cluster, tmp_path, startup_timeout_s=1.0, health_poll_interval_s=0.05)

    async def _run():
        ident = _ident(2, 1)
        cell = await prov.provision_cell(ident)
        # tamper the provision handed back to dispose (workspace/storage mismatch)
        tampered = ci.CellProvision(
            workspace="foreign_ws",
            storage_dir=cell.storage_dir,
            fresh_extraction_state=True,
            owned=True,
        )
        disposal = await prov.dispose(ident, tampered)
        return cell, disposal

    cell, disposal = asyncio.run(_run())
    assert disposal.owned is False and disposal.disposed is False
    assert os.path.isdir(cell.storage_dir)  # NOT deleted on unproven ownership


# ---------------------------------------------------------------------------
# §52 root escape → reject malicious/invalid identifiers and delete targets
# ---------------------------------------------------------------------------


def test_path_escape_rejected(tmp_path):
    root = str(tmp_path)
    for bad in ("..", "../foreign", "a/b", "a\\b", "C:evil", "\\\\unc\\share"):
        with pytest.raises(cp.CellPathSafetyError):
            cp.assert_cell_paths_safe(eval_root=root, run_id=RUN, workspace=bad)
    with pytest.raises(cp.CellPathSafetyError):
        cp.assert_cell_paths_safe(eval_root=root, run_id="..", workspace="ws_ok")
    # relative eval_root is a configuration error (must be absolute)
    with pytest.raises(cp.CellProvisionConfigurationError):
        cp.assert_cell_paths_safe(eval_root="rel/root", run_id=RUN, workspace="ws")


def test_is_within_root_semantics(tmp_path):
    root = str(tmp_path / "run")
    os.makedirs(root)
    child = os.path.join(root, "cell")
    os.makedirs(child)
    assert cp.is_within_root(child, root) is True
    assert cp.is_within_root(root, root) is False  # equal is not "within"
    assert cp.is_within_root(str(tmp_path / "other"), root) is False


# ---------------------------------------------------------------------------
# §53 symlink/junction safety (path-resolution guard; skipped if unsupported)
# ---------------------------------------------------------------------------


def test_symlink_target_escape_refused(tmp_path):
    root = str(tmp_path / "run")
    os.makedirs(root)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "important.txt").write_text("keep me")
    link = os.path.join(root, "cell_ws")
    try:
        os.symlink(str(outside), link, target_is_directory=True)
    except (OSError, NotImplementedError, AttributeError):
        pytest.skip("symlink creation not permitted on this platform/run")
    # a symlink that resolves OUTSIDE the owned root must not be judged within it
    assert cp.is_within_root(link, root) is False
    # and the outside content is untouched by the guard
    assert (outside / "important.txt").exists()


# ---------------------------------------------------------------------------
# §54 provision success then caller exception → finally teardown, cleanup PASS
# ---------------------------------------------------------------------------


def test_context_manager_teardown_on_caller_exception(tmp_path):
    cluster = FakeCluster()
    prov, _ = _mk(cluster, tmp_path, startup_timeout_s=1.0, health_poll_interval_s=0.05)
    seen = {}

    async def _run():
        ident = _ident(1, 1)
        with pytest.raises(ValueError):
            async with cp.provision_diagnostic_cell(prov, ident) as cell:
                seen["storage"] = cell.storage_dir
                assert cell.ready
                raise ValueError("body_marker")
        return ident

    ident = asyncio.run(_run())
    assert not os.path.exists(seen["storage"])  # workspace absent after teardown
    assert ident.cell_id not in prov._active


def test_context_manager_success_disposes(tmp_path):
    cluster = FakeCluster()
    prov, _ = _mk(cluster, tmp_path, startup_timeout_s=1.0, health_poll_interval_s=0.05)

    async def _run():
        ident = _ident(2, 1)
        async with cp.provision_diagnostic_cell(prov, ident) as cell:
            storage = cell.storage_dir
            assert os.path.isdir(storage)
        assert not os.path.exists(storage)
        return True

    assert asyncio.run(_run()) is True


# ---------------------------------------------------------------------------
# §55 cleanup failure → surfaced, cell not marked CLEANED, process stop attempted
# ---------------------------------------------------------------------------


def test_cleanup_failure_is_surfaced(tmp_path, monkeypatch):
    cluster = FakeCluster()
    prov, _ = _mk(cluster, tmp_path, startup_timeout_s=1.0, health_poll_interval_s=0.05)

    # simulate storage removal failure at teardown
    monkeypatch.setattr(prov, "_dispose_storage", lambda rt: False)

    async def _run():
        ident = _ident(1, 1)
        cell = await prov.provision_cell(ident)
        disposal = await prov.dispose(ident, cell.to_provision())
        return disposal

    disposal = asyncio.run(_run())
    assert disposal.owned is True         # ownership WAS proven
    assert disposal.disposed is False     # but cleanup did not verify → FAIL
    assert cluster.terminated             # process stop still attempted


def test_terminate_error_becomes_verifiable_cleanup_failure(tmp_path):
    # An injected controller.terminate that RAISES must not escape dispose raw (L3):
    # it becomes disposed=False (owned=True), a fail-closed cleanup result.
    cluster = FakeCluster()

    class _RaisingTerminate(FakeController):
        async def terminate(self, handle, *, graceful_timeout_s):
            self.c.terminated.append(handle.identifier)
            raise RuntimeError("terminate boom")

    ctl = _RaisingTerminate(cluster)
    prov, _ = _mk(cluster, tmp_path, controller=ctl, startup_timeout_s=1.0,
                  health_poll_interval_s=0.05)

    async def _run():
        ident = _ident(1, 1)
        cell = await prov.provision_cell(ident)
        disposal = await prov.dispose(ident, cell.to_provision())  # must NOT raise
        return disposal

    disposal = asyncio.run(_run())
    assert disposal.owned is True and disposal.disposed is False
    assert cluster.terminated  # stop was attempted


def test_cleanup_failure_under_context_manager_raises(tmp_path, monkeypatch):
    cluster = FakeCluster()
    # process that never dies → teardown cannot verify → cleanup FAIL surfaced
    ctl = FakeController(cluster, never_dies=True)
    prov, _ = _mk(cluster, tmp_path, controller=ctl, startup_timeout_s=1.0,
                  health_poll_interval_s=0.05)

    async def _run():
        with pytest.raises(cp.CellCleanupError):
            async with cp.provision_diagnostic_cell(prov, _ident(1, 1)):
                pass

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# §56 no provider/process/DB on import
# ---------------------------------------------------------------------------


def test_import_has_no_side_effects():
    import importlib

    mod = importlib.import_module(
        "open_notebook.integrations.graphrag.eval.cell_provisioner08"
    )
    # constructing with fakes must not start any process
    cluster = FakeCluster()
    ctl = FakeController(cluster)
    prov = mod.LightRagCellProvisioner(
        mod.ProvisionerConfig(eval_root=os.path.abspath(os.sep + "gr08e2_root")),
        process_controller=ctl, health_prober=FakeProber(cluster),
    )
    assert prov is not None
    assert cluster.started == []


# ---------------------------------------------------------------------------
# §58 indexer cannot bypass provisioner  ·  §59 provisioner ≠ authorization
# ---------------------------------------------------------------------------


def test_run_sweep_without_provisioner_fails_closed():
    plan = cd.ConcurrencyDiagnosticPlan(levels=(cd.DiagnosticLevel(1, 1, 1),))

    async def must_not_run(level, cell, rep):
        raise AssertionError("indexer must not run without a provisioner")

    with pytest.raises(cd.LiveDiagnosticNotAuthorizedError):
        asyncio.run(cd.run_sweep(
            plan, run_id=RUN, index_cell_fn=must_not_run,
            cell_provisioner=None, working_dir="/tmp/x",
            authorized_live=True, require_isolation=False,
        ))


def test_run_sweep_not_authorized_does_not_index(tmp_path):
    cluster = FakeCluster()
    prov, _ = _mk(cluster, tmp_path)
    plan = cd.ConcurrencyDiagnosticPlan(levels=(cd.DiagnosticLevel(1, 1, 1),))

    async def must_not_run(level, cell, rep):
        raise AssertionError("indexer must not run when not authorized")

    with pytest.raises(cd.LiveDiagnosticNotAuthorizedError):
        asyncio.run(cd.run_sweep(
            plan, run_id=RUN, index_cell_fn=must_not_run,
            cell_provisioner=prov, working_dir=str(tmp_path / RUN),
            authorized_live=False, require_isolation=False,
        ))
    assert cluster.started == []  # provisioner never even engaged


# ---------------------------------------------------------------------------
# §34 integration: real provisioner realizes the isolation contract in run_sweep
# ---------------------------------------------------------------------------


def test_run_sweep_integration_with_real_provisioner(tmp_path):
    cluster = FakeCluster()
    working_dir = str(tmp_path / RUN)
    config = cp.ProvisionerConfig(
        eval_root=str(tmp_path), startup_timeout_s=1.0, health_poll_interval_s=0.05
    )
    prov = cp.LightRagCellProvisioner(
        config, process_controller=FakeController(cluster), health_prober=FakeProber(cluster)
    )
    plan = cd.ConcurrencyDiagnosticPlan(
        levels=(cd.DiagnosticLevel(1, 1, 1), cd.DiagnosticLevel(2, 2, 1))
    )
    ran_in = []

    async def index_cell(level, cell, rep):
        # the indexer ONLY ever runs inside an entered, validated, provisioned cell
        assert cell.validity.valid
        assert prov._active.get(cell.identity.cell_id) is not None
        ran_in.append(cell.identity.cell_id)
        return [
            cd.AttemptRecord(
                run_id=RUN, concurrency_level=level.concurrency,
                logical_source_id="S001", repetition=rep, attempt_number=1,
                terminal_status=cd.TERMINAL_SUCCESS, duration_ms=1, characterization=None,
            )
            for _ in range(level.source_count)
        ]

    async def _run():
        return await cd.run_sweep(
            plan, run_id=RUN, index_cell_fn=index_cell,
            cell_provisioner=prov, working_dir=working_dir,
            authorized_live=True, require_isolation=False,
        )

    result = asyncio.run(_run())
    assert len(ran_in) == 2 and len(set(ran_in)) == 2  # two distinct cells
    assert result.aggregate["total_attempts"] == 3
    # every provisioned cell was disposed; the run root remains, no cell workspace left
    assert prov._active == {}
    for entry in os.listdir(working_dir) if os.path.isdir(working_dir) else []:
        assert not entry.startswith(ci.CELL_WORKSPACE_PREFIX)


# ---------------------------------------------------------------------------
# §60/§61 frozen experiment plan + fixture integrity unchanged
# ---------------------------------------------------------------------------


def test_frozen_plan_and_fixture_unchanged():
    plan = cd.default_plan()
    assert [lvl.concurrency for lvl in plan.levels] == [1, 2, 4, 8]
    assert all(lvl.repetitions == 2 for lvl in plan.levels)
    assert plan.total_submissions == 30
    assert cd.MAX_TOTAL_SUBMISSIONS == 64
    ok, h = d.verify_integrity()
    assert ok and h == FIXTURE_HASH
    # frozen retry classifier is unchanged (no reimplementation in the provisioner)
    from open_notebook.integrations.graphrag.eval import index_retry08 as ir
    assert cd.retry_decision("429 rate limit") == ir.is_transient_reason("429 rate limit")
