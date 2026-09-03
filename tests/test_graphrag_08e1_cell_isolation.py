"""GraphRAG-08E.1 experimental-cell isolation tests (PURE OFFLINE, all mocked).

No provider, no sidecar, no DB. Covers task §28-§34: cross-cell LLM-cache/graph
isolation proof, repeated-Source independence, cell-resource uniqueness, cleanup,
failed-cell fail-stop, invalid-isolation fail-closed, and scientific validity.
"""

from __future__ import annotations

import asyncio

import pytest

from open_notebook.integrations.graphrag.eval import cell_isolation08 as ci
from open_notebook.integrations.graphrag.eval import concurrency_diag08 as cd
from open_notebook.integrations.graphrag.eval import dataset08 as d

FIXTURE_HASH = "a58a68535c345e18f0263904f818e4e2068a164056408665d8bb9233eceb143d"
_WD = "/tmp/gr08e_wd"
RUN = "run08e1a"


class _MockProvisioner:
    def __init__(self, *, fresh=True, owned=True, workspace_override=None):
        self.provisioned, self.disposed = [], []
        self._fresh, self._owned, self._ws = fresh, owned, workspace_override

    async def provision(self, identity):
        ws = self._ws or identity.workspace
        self.provisioned.append(ws)
        return ci.CellProvision(
            workspace=ws, storage_dir=ci.cell_storage_dir(_WD, ws),
            fresh_extraction_state=self._fresh, owned=self._owned,
        )

    async def dispose(self, identity, provision):
        self.disposed.append(provision.workspace)
        return ci.CellDisposal(disposed=True, owned=self._owned)


# ---- §28 cross-cell LLM cache reuse is IMPOSSIBLE ---------------------------


def test_cross_cell_llm_cache_isolated():
    a = ci.CellIdentity(RUN, 1, 1)   # C1-R1 indexes S001
    b = ci.CellIdentity(RUN, 2, 1)   # C2-R1 indexes S001 (same content)
    assert a.workspace != b.workspace
    # the LLM response cache path differs between the two cells
    pa = ci.cell_storage_paths(_WD, a.workspace)["kv_store_llm_response_cache.json"]
    pb = ci.cell_storage_paths(_WD, b.workspace)["kv_store_llm_response_cache.json"]
    assert pa != pb
    # ALL storage paths are disjoint -> no cross-cell reuse possible
    assert ci.cross_cell_storage_isolated(_WD, a.workspace, b.workspace) is True


def test_same_workspace_not_isolated_guard():
    # sanity: identical workspace is (correctly) reported NOT isolated
    assert ci.cross_cell_storage_isolated(_WD, "ws_same", "ws_same") is False
    assert ci.cross_cell_storage_isolated(_WD, "", "ws") is False


# ---- §29 repeated Source across levels/reps stays independent ---------------


def test_repeated_source_independent_across_levels_and_reps():
    # S001 recurs in every level (1,2,4,8) and every rep (1,2) -> all distinct cells
    cells = [
        ci.CellIdentity(RUN, c, r) for c in (1, 2, 4, 8) for r in (1, 2)
    ]
    workspaces = [c.workspace for c in cells]
    assert len(workspaces) == 8 and len(set(workspaces)) == 8
    # every pair is storage-isolated
    for i in range(len(workspaces)):
        for j in range(i + 1, len(workspaces)):
            assert ci.cross_cell_storage_isolated(_WD, workspaces[i], workspaces[j])


# ---- §30 cell resource uniqueness across the full plan ----------------------


def test_all_eight_plan_cells_unique():
    plan = cd.default_plan()
    reg = ci.CellRegistry()
    ids = []
    for lvl in plan.levels:
        for rep in range(1, lvl.repetitions + 1):
            ident = ci.CellIdentity(RUN, lvl.concurrency, rep)
            reg.register(ident)  # duplicate would raise
            ids.append(ident)
    assert reg.count == 8
    assert len({i.cell_id for i in ids}) == 8
    assert len({i.workspace for i in ids}) == 8


def test_registry_rejects_duplicate():
    reg = ci.CellRegistry()
    reg.register(ci.CellIdentity(RUN, 4, 1))
    with pytest.raises(ci.CellOwnershipError):
        reg.register(ci.CellIdentity(RUN, 4, 1))


# ---- §31 cleanup: enter/exit disposes the cell's own resources --------------


def test_cell_enter_exit_disposes_owned():
    prov = _MockProvisioner()
    reg = ci.CellRegistry()
    ident = ci.CellIdentity(RUN, 2, 1)

    async def _run():
        async with ci.diagnostic_cell08(
            ident, provisioner=prov, registry=reg, working_dir=_WD
        ) as cell:
            assert cell.validity.valid
            assert cell.identity.workspace == ident.workspace
        return True

    assert asyncio.run(_run()) is True
    assert prov.provisioned == [ident.workspace]
    assert prov.disposed == [ident.workspace]  # exactly its own workspace


# ---- §32 failed cell: body raises -> cleanup still runs, exception propagates -


def test_failed_cell_cleans_up_and_fail_stops_sweep():
    plan = cd.ConcurrencyDiagnosticPlan(
        levels=(cd.DiagnosticLevel(1, 1, 1), cd.DiagnosticLevel(2, 2, 1))
    )
    prov = _MockProvisioner()
    entered = {"n": 0}

    async def failing_index_cell(level, cell, rep):
        entered["n"] += 1
        raise RuntimeError("sidecar_or_provider_exploded")  # only the type would surface

    with pytest.raises(RuntimeError):
        asyncio.run(cd.run_sweep(
            plan, run_id=RUN, index_cell_fn=failing_index_cell,
            cell_provisioner=prov, working_dir=_WD,
            authorized_live=True, require_isolation=False,
        ))
    # fail-stop: only the FIRST cell was entered; the second was never started
    assert entered["n"] == 1
    # the entered cell was still disposed (cleanup on failure)
    assert prov.disposed == prov.provisioned == [ci.CellIdentity(RUN, 1, 1).workspace]


# ---- §33 invalid isolation fails closed BEFORE indexing ---------------------


def test_invalid_cell_fresh_false_fail_stops():
    prov = _MockProvisioner(fresh=False)  # cache NOT fresh
    plan = cd.ConcurrencyDiagnosticPlan(levels=(cd.DiagnosticLevel(1, 1, 1),))

    async def must_not_run(level, cell, rep):
        raise AssertionError("indexer must not run in an invalid cell")

    with pytest.raises(ci.DiagnosticCellIsolationFailure):
        asyncio.run(cd.run_sweep(
            plan, run_id=RUN, index_cell_fn=must_not_run,
            cell_provisioner=prov, working_dir=_WD,
            authorized_live=True, require_isolation=False,
        ))


def test_invalid_cell_workspace_mismatch_fail_stops():
    prov = _MockProvisioner(workspace_override="foreign_ws")  # not the cell's workspace
    plan = cd.ConcurrencyDiagnosticPlan(levels=(cd.DiagnosticLevel(1, 1, 1),))

    async def must_not_run(level, cell, rep):
        raise AssertionError("indexer must not run in an invalid cell")

    with pytest.raises(ci.DiagnosticCellIsolationFailure):
        asyncio.run(cd.run_sweep(
            plan, run_id=RUN, index_cell_fn=must_not_run,
            cell_provisioner=prov, working_dir=_WD,
            authorized_live=True, require_isolation=False,
        ))


def test_cleanup_failure_preserves_body_exception():
    """If the cell body raises AND dispose reports unproven ownership, the ownership
    error chains the original body cause (review LOW-1)."""
    class _BadDispose(_MockProvisioner):
        async def dispose(self, identity, provision):
            return ci.CellDisposal(disposed=False, owned=False)

    prov = _BadDispose()
    ident = ci.CellIdentity(RUN, 1, 1)

    async def _run():
        async with ci.diagnostic_cell08(
            ident, provisioner=prov, registry=ci.CellRegistry(), working_dir=_WD
        ):
            raise ValueError("body_cause_marker")

    with pytest.raises(ci.CellOwnershipError) as ei:
        asyncio.run(_run())
    assert isinstance(ei.value.__cause__, ValueError)
    assert str(ei.value.__cause__) == "body_cause_marker"


def test_unknown_ownership_on_dispose_fail_closed():
    prov = _MockProvisioner(owned=False)  # provision owned=False -> validity fails first
    ident = ci.CellIdentity(RUN, 1, 1)

    async def _run():
        async with ci.diagnostic_cell08(
            ident, provisioner=prov, registry=ci.CellRegistry(), working_dir=_WD
        ):
            pass

    with pytest.raises(ci.CellIsolationError):
        asyncio.run(_run())


# ---- §34 scientific validity: concurrency varies, everything else fixed -----


def test_scientific_validity_invariants():
    # concurrency is the treatment that varies across cells...
    plan = cd.default_plan()
    concs = [lvl.concurrency for lvl in plan.levels]
    assert concs == [1, 2, 4, 8]
    # ...source content is FIXED/deterministic (fixture unchanged, S001 anchored)
    ok, h = d.verify_integrity()
    assert ok and h == FIXTURE_HASH
    bench = d.load_benchmark08()
    assert cd.select_diagnostic_sources(bench, 4)[0] == "S001"
    # ...retry decision remains the FROZEN classifier (no reimplementation)
    from open_notebook.integrations.graphrag.eval import index_retry08 as ir
    assert cd.retry_decision("429 rate limit") == ir.is_transient_reason("429 rate limit")
    # ...and cell state is independent across the full plan
    ws = [ci.CellIdentity(RUN, c, r).workspace for c in concs for r in (1, 2)]
    assert len(set(ws)) == len(ws)


def test_sanitize_workspace_is_lightrag_valid():
    import re
    ws = ci.sanitize_workspace("gr08e_run/../x y!")
    assert re.match(r"^[A-Za-z0-9_]+$", ws)
    # a normal cell workspace is already valid and unchanged
    ident = ci.CellIdentity(RUN, 8, 2)
    assert re.match(r"^[A-Za-z0-9_]+$", ident.workspace)


def test_storage_dir_matches_lightrag_layout():
    # working_dir/[workspace/] per v1.5.6 json_kv_impl.py:142-147
    assert ci.cell_storage_dir(_WD, "ws1").replace("\\", "/") == f"{_WD}/ws1"
    assert ci.cell_storage_dir(_WD, "") == _WD  # empty workspace = shared default (unused)
