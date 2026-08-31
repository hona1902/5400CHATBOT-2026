"""GraphRAG-08A temporary-Surreal-isolation tests.

Unit tests (no DB/provider) cover the safety guards: temp-name validity/uniqueness,
the normal-DB equality guard, the Option-A live-path block, cleanup ownership
guards, and reentrancy refusal.

Live tests (gated — skipped if no local SurrealDB is reachable) prove the real
mechanism end to end against a TEMPORARY namespace only: schema bootstrap via the
canonical migration path, isolated read/write, the NORMAL namespace unchanged,
owned cleanup + idempotency, and env restoration. They NEVER touch the normal
application namespace and make NO provider calls / start no sidecar.
"""

from __future__ import annotations

import asyncio
import os

import pytest

from open_notebook.integrations.graphrag.eval import isolation08 as iso


def _surreal_ready() -> bool:
    try:
        from dotenv import load_dotenv

        load_dotenv(".env")
        from open_notebook.database.repository import repo_query

        asyncio.run(repo_query("RETURN true;"))
        return True
    except Exception:
        return False


LIVE = _surreal_ready()
live_only = pytest.mark.skipif(not LIVE, reason="local SurrealDB not reachable")


# ------------------------- unit (no DB) -------------------------------------

def test_temp_names_valid_unique_bounded():
    a = iso.temp_names(iso.make_run_id())
    b = iso.temp_names(iso.make_run_id())
    assert a != b
    for ns, db in (a, b):
        assert ns.startswith("graphrag_eval_") and db.startswith("graphrag_08_")
        assert iso._valid_identifier(ns) and iso._valid_identifier(db)
        assert len(ns) <= 63 and len(db) <= 63


def test_temp_names_rejects_bad_run_id():
    for bad in ("bad-id", "has space", "semi;colon", "x" * 40, ""):
        with pytest.raises(iso.IsolationConfigurationError):
            iso.temp_names(bad)


def test_assert_not_normal_guard():
    ns, db = iso.temp_names(iso.make_run_id())
    iso.assert_not_normal(ns, db)  # temp differs from normal — ok
    norm_ns, norm_db = iso.normal_identity()
    with pytest.raises(iso.IsolationOwnershipError):
        iso.assert_not_normal(norm_ns, norm_db)


def test_assert_not_normal_rejects_shared_namespace(monkeypatch):
    # Even a different database inside the NORMAL namespace must be rejected at
    # enter (mirrors the cleanup guard).
    ns, db = iso.temp_names(iso.make_run_id())
    monkeypatch.setattr(iso, "normal_identity", lambda: (ns, "some_other_db"))
    with pytest.raises(iso.IsolationOwnershipError):
        iso.assert_not_normal(ns, db)


def test_require_active_isolation_blocks_when_inactive():
    assert iso.is_active() is False
    with pytest.raises(iso.IsolationOwnershipError):
        iso.require_active_isolation()


def test_cleanup_ownership_guards_reject_unsafe_targets():
    run_id = iso.make_run_id()
    ns, db = iso.temp_names(run_id)
    norm_ns, norm_db = iso.normal_identity()

    async def call(namespace, database, rid, nns=norm_ns, ndb=norm_db):
        await iso.cleanup_isolated(
            namespace, database, run_id=rid, normal_namespace=nns, normal_database=ndb
        )

    # normal target -> refuse
    with pytest.raises(iso.IsolationOwnershipError):
        asyncio.run(call(norm_ns, norm_db, run_id))
    # run_id mismatch -> refuse
    with pytest.raises(iso.IsolationOwnershipError):
        asyncio.run(call(ns, db, "0000deadbeef"))
    # invalid identifier -> refuse
    with pytest.raises(iso.IsolationOwnershipError):
        asyncio.run(call("bad-ns", db, run_id))
    # empty -> refuse
    with pytest.raises(iso.IsolationOwnershipError):
        asyncio.run(call("", "", run_id))


def test_reentrancy_refused(monkeypatch):
    # Simulate an already-active context; entering again must refuse before any DB
    # work. We drive the async generator's first step directly.
    monkeypatch.setattr(iso, "_ACTIVE", True, raising=False)
    cm = iso.isolated_surreal_eval_runtime()
    with pytest.raises(iso.IsolationConfigurationError):
        asyncio.run(cm.__aenter__())
    # _ACTIVE remains True because entry refused before flipping it; restore.
    monkeypatch.setattr(iso, "_ACTIVE", False, raising=False)


def test_restore_env_helper_restores_and_pops():
    prior_ns = os.environ.get("SURREAL_NAMESPACE")
    prior_db = os.environ.get("SURREAL_DATABASE")
    try:
        # case: no prior -> pop
        os.environ.pop("SURREAL_NAMESPACE", None)
        os.environ["SURREAL_NAMESPACE"] = "temp_override"
        iso._restore_env(None, None)
        assert "SURREAL_NAMESPACE" not in os.environ
        # case: prior value -> restore exact
        os.environ["SURREAL_NAMESPACE"] = "temp_override2"
        iso._restore_env("orig_ns", "orig_db")
        assert os.environ["SURREAL_NAMESPACE"] == "orig_ns"
        assert os.environ["SURREAL_DATABASE"] == "orig_db"
    finally:
        iso._restore_env(prior_ns, prior_db)


# ------------------------- live (gated) -------------------------------------

@live_only
def test_full_isolation_cycle_normal_db_untouched():
    async def main():
        from open_notebook.database.repository import repo_query

        async def normal_count():
            rows = await repo_query("SELECT VALUE id FROM source")
            return len(rows or [])

        before = await normal_count()
        seen_ns = seen_db = None
        isolated_count = None
        async with iso.isolated_surreal_eval_runtime() as ctx:
            seen_ns, seen_db = ctx.namespace, ctx.database
            assert os.environ["SURREAL_NAMESPACE"] == ctx.namespace
            assert os.environ["SURREAL_DATABASE"] == ctx.database
            # schema parity
            from open_notebook.database.async_migrate import AsyncMigrationManager

            mgr = AsyncMigrationManager()
            assert await mgr.get_current_version() == len(mgr.up_migrations)
            # write a harmless synthetic record into the isolated db
            await repo_query(
                "CREATE source:gr08a_probe SET full_text='synthetic', "
                "title='probe', topics=['__gr08a_probe__']"
            )
            rows = await repo_query("SELECT VALUE id FROM source")
            isolated_count = len(rows or [])
        # env restored
        assert os.environ.get("SURREAL_NAMESPACE") == ctx.prior_namespace or (
            ctx.prior_namespace is None
            and os.environ.get("SURREAL_NAMESPACE") == ctx.normal_namespace
        )
        after = await normal_count()
        assert after == before  # NORMAL DB unchanged
        # temp namespace dropped
        dbs = await iso.namespace_databases(seen_ns)
        assert seen_db not in dbs
        assert isolated_count == 1

    asyncio.run(main())


@live_only
def test_cleanup_idempotent():
    async def main():
        run_id = iso.make_run_id()
        ns, db = iso.temp_names(run_id)
        norm_ns, norm_db = iso.normal_identity()
        # create the namespace by bootstrapping through the context, then drop twice
        async with iso.isolated_surreal_eval_runtime(run_id=run_id):
            pass  # context already drops on exit
        # second explicit cleanup must be a safe no-op (already absent)
        await iso.cleanup_isolated(
            ns, db, run_id=run_id, normal_namespace=norm_ns, normal_database=norm_db
        )

    asyncio.run(main())


@live_only
def test_bootstrap_failure_restores_env_and_surfaces(monkeypatch):
    # A bootstrap failure must: raise IsolationBootstrapError, restore the normal
    # env, and leave the normal DB unchanged (§44).
    from open_notebook.database.repository import repo_query

    async def normal_count():
        rows = await repo_query("SELECT VALUE id FROM source")
        return len(rows or [])

    async def boom():
        raise RuntimeError("induced bootstrap failure")

    monkeypatch.setattr(iso, "_bootstrap_schema", boom)
    norm_ns_before = os.environ.get("SURREAL_NAMESPACE")

    async def main():
        before = await normal_count()
        with pytest.raises(iso.IsolationBootstrapError):
            async with iso.isolated_surreal_eval_runtime():
                pass  # should never reach here
        assert os.environ.get("SURREAL_NAMESPACE") == norm_ns_before
        assert iso.is_active() is False
        after = await normal_count()
        assert after == before

    asyncio.run(main())


@live_only
def test_runner_option_a_guard_blocks_without_isolation():
    # runner08.create_and_index must refuse when no isolated runtime is active.
    from open_notebook.integrations.graphrag.config import GraphRAGConfig
    from open_notebook.integrations.graphrag.eval import dataset08 as d
    from open_notebook.integrations.graphrag.eval.gd_seam import GDQueryClient
    from open_notebook.integrations.graphrag.eval.runner08 import GraphRAG08EvalRunner

    bench = d.load_benchmark08()
    cfg = GraphRAGConfig(enabled=True, base_url="http://x", timeout=5.0, api_key=None)
    runner = GraphRAG08EvalRunner(
        bench,
        service=object(),
        gd_client=GDQueryClient(cfg),
        selected_source_keys=("S001",),
        selected_query_ids=("GR08Q01",),
    )
    assert iso.is_active() is False
    with pytest.raises(iso.IsolationOwnershipError):
        asyncio.run(runner.create_and_index())


@live_only
def test_require_active_isolation_passes_inside_context():
    # Regression for the HIGH review finding: the guard must NOT self-trip while an
    # isolated context is active with the env overridden to the temp identity.
    async def main():
        async with iso.isolated_surreal_eval_runtime():
            assert iso.is_active() is True
            # Must not raise: active env == temp identity != captured normal.
            iso.require_active_isolation()
        # And once outside, it blocks again.
        assert iso.is_active() is False
        with pytest.raises(iso.IsolationOwnershipError):
            iso.require_active_isolation()

    asyncio.run(main())
