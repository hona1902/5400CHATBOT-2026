"""GraphRAG-PN02D-B1-EW2 — isolated embedding-model seed for real B1 execution.

Covers the remediation of the defect that BLOCKED the first authorized B1 real-provider
execution: a fresh isolated Surreal namespace has no ``model`` records and
``DefaultModels.default_embedding_model == None``, so the normal embedding stack
(``embed_source_command`` → ``generate_embeddings`` → ``model_manager.get_embedding_model``)
and the real frozen-model attestor fail closed with
``ValueError("No embedding model configured …")``.

Unit tests (no DB / no provider): the shared seed helper's frozen identity + single source
of truth, its conflict policy (exact-match reuse / fail-closed on mismatch), the active-
isolation guard, provider-free behaviour, the private ownership-bound teardown, that NO
public cleanup authority object/API remains to forge (RR3-H1 / Cycle #4), ``precheck08``
delegation to the shared context manager, the run_live ordering (isolation < seed < seams <
driver), and the real default seed identity.

Live test (gated — skipped if no local SurrealDB is reachable): against a TEMPORARY
namespace only, proves the defect reproduces before the seed and is removed after it —
``get_embedding_model`` resolves the seeded frozen model, the real attestor matches frozen
(dim 1536), the corpus embedding path reaches the provider boundary (old ValueError gone),
and teardown restores/deletes the seed. It NEVER touches the normal namespace, uses a DUMMY
non-secret provider key, and makes ZERO real provider calls (an autouse socket sentinel
blocks any non-loopback connect).
"""

from __future__ import annotations

import asyncio
import os
import socket
from contextlib import asynccontextmanager, contextmanager
from unittest import mock

import graphrag_pn02db0cb_common as C
import pytest

from open_notebook.integrations.graphrag.eval import isolated_model_seed as S
from open_notebook.integrations.graphrag.eval import realseamspn02d as R
from open_notebook.integrations.graphrag.eval.provider_binding08 import (
    FROZEN_EMBEDDING_DIM,
    FROZEN_EMBEDDING_MODEL,
)
from open_notebook.integrations.graphrag.eval.vectoradapterpn02d import (
    FROZEN_EMBEDDING_PROVIDER,
)

_LOOPBACK = {"127.0.0.1", "::1", "localhost", "0.0.0.0"}

_REQUIRE_ACTIVE_ISOLATION = (
    "open_notebook.integrations.graphrag.eval.isolation08.require_active_isolation"
)


@pytest.fixture(autouse=True)
def _no_external_network(monkeypatch):
    """§24 sentinel: fail immediately on any outbound connect to a non-loopback host.

    SurrealDB (127.0.0.1) is allowed; a provider endpoint (openrouter.ai / openai) is not,
    so an accidental real provider call fails the test instead of leaking traffic.
    """
    real_connect = socket.socket.connect

    def guarded(self, address):
        host = address[0] if isinstance(address, (tuple, list)) else address
        if host not in _LOOPBACK:
            raise AssertionError(
                f"EW2 offline sentinel: blocked external network connect to {host!r}"
            )
        return real_connect(self, address)

    monkeypatch.setattr(socket.socket, "connect", guarded)


# --------------------------------------------------------------------------- #
# Shared fakes (no DB / no provider)
# --------------------------------------------------------------------------- #

class _FakeModelRec:
    """A stand-in pre-existing ``Model`` record: frozen identity by default; per-field
    overridable (used only for the conflict-policy reuse/mismatch pre-existing rows)."""

    def __init__(self, mid, *, name=None, provider=None, type=None):
        self.id = mid
        self.name = name if name is not None else FROZEN_EMBEDDING_MODEL
        self.provider = provider if provider is not None else FROZEN_EMBEDDING_PROVIDER
        self.type = type if type is not None else "embedding"
        self.deleted = False

    async def save(self):
        pass

    async def delete(self):
        self.deleted = True


def _set_isolation_env(ns, db):
    """Set/clear the active isolation identity env vars; return the previous values."""
    prev = (os.environ.get("SURREAL_NAMESPACE"), os.environ.get("SURREAL_DATABASE"))
    if ns is None:
        os.environ.pop("SURREAL_NAMESPACE", None)
    else:
        os.environ["SURREAL_NAMESPACE"] = ns
    if db is None:
        os.environ.pop("SURREAL_DATABASE", None)
    else:
        os.environ["SURREAL_DATABASE"] = db
    return prev


# --------------------------------------------------------------------------- #
# Frozen identity + single source of truth (§7/§10)
# --------------------------------------------------------------------------- #

def test_seed_frozen_identity_is_single_source_of_truth():
    # The seed derives its identity from the SAME constants the runtime binding and the
    # attestor validate against — it can never drift from the frozen match.
    assert FROZEN_EMBEDDING_MODEL == "openai/text-embedding-3-small"
    assert FROZEN_EMBEDDING_PROVIDER == "openrouter"
    assert S.FROZEN_EMBEDDING_TYPE == "embedding"
    assert FROZEN_EMBEDDING_DIM == 1536


def test_is_frozen_embedding_identity_matcher():
    ok = mock.Mock(name="openai/text-embedding-3-small")
    ok.name = FROZEN_EMBEDDING_MODEL
    ok.provider = FROZEN_EMBEDDING_PROVIDER
    ok.type = "embedding"
    assert S.is_frozen_embedding_identity(ok) is True
    for bad in (
        {"name": "text-embedding-ada-002", "provider": "openrouter", "type": "embedding"},
        {"name": FROZEN_EMBEDDING_MODEL, "provider": "openai", "type": "embedding"},
        {"name": FROZEN_EMBEDDING_MODEL, "provider": "openrouter", "type": "chat"},
    ):
        m = mock.Mock()
        m.name, m.provider, m.type = bad["name"], bad["provider"], bad["type"]
        assert S.is_frozen_embedding_identity(m) is False


# --------------------------------------------------------------------------- #
# No forgeable cleanup surface (§36 / RR3-H1 — Cycle #4)
# --------------------------------------------------------------------------- #

def test_capability_forgery_surface_absent():
    # Cleanup authority is collapsed into the private context manager: there is NO
    # public/exported cleanup object, function, token, or construction key to forge or
    # retarget. CAPABILITY_FORGERY_SURFACE = ABSENT.
    for removed in (
        "SeededEmbeddingModelHandle",          # ownership dataclass — gone
        "create_frozen_embedding_model_and_bind",  # public writer — gone
        "restore_seeded_embedding_model",      # public cleanup entrypoint — gone
        "restore_default_and_delete",          # raw-id cleanup writer — gone
        "_SEED_OWNERSHIP_KEY",                 # construction key — gone
        "SeedOwnershipError",                  # handle-era error — renamed away
        "_teardown_owned_seed",                # module-level teardown primitive — gone (RR4-M1)
    ):
        assert not hasattr(S, removed), f"forgeable cleanup surface still present: {removed}"

    # The only public lifecycle entrypoint is the context manager; teardown is a closure
    # LEXICAL to each CM invocation — there is no module-level destructive primitive at all.
    assert "seeded_frozen_embedding_model" in S.__all__
    assert not any(
        n.startswith(("create", "restore", "teardown", "cleanup")) for n in S.__all__
    ), "no public create/restore/teardown/cleanup authority may be exported"
    # No module-level coroutine function reachable for cleanup (only the CM factory).
    import inspect

    teardownish = [
        n for n in dir(S)
        if not n.startswith("__")
        and inspect.iscoroutinefunction(getattr(S, n))
        and n != "seeded_frozen_embedding_model"
    ]
    assert teardownish == [], f"unexpected module-level coroutine(s): {teardownish}"


# --------------------------------------------------------------------------- #
# Coherent in-memory model DB — drives the CM's create AND its private cleanup
# closure end to end against the SAME state (no direct teardown call anywhere).
# --------------------------------------------------------------------------- #

class _FakeDefaults:
    """Stand-in for the DefaultModels singleton: a mutable default + async update()."""

    def __init__(self, default=None):
        self.default_embedding_model = default
        self.update_calls = 0
        self.update_error = None  # set to an Exception to fail a later update()

    async def update(self):
        self.update_calls += 1
        if self.update_error is not None:
            raise self.update_error


@contextmanager
def _fake_model_db(
    *, active_ns="graphrag_eval_t", active_db="graphrag_08_t",
    initial_default=None, preexisting=None,
):
    """Patch DefaultModels/Model with a COHERENT in-memory store + active isolation identity
    + a no-op ``require_active_isolation``. The CM's create path and its private cleanup
    closure both run against this store, so cleanup is exercised THROUGH the public CM —
    there is no module-level teardown to call. Yields ``(dm, store, ModelCls)``."""
    import open_notebook.ai.models as M
    from open_notebook.exceptions import NotFoundError

    store: dict = dict(preexisting or {})
    dm = _FakeDefaults(initial_default)

    class _Model:
        def __init__(self, *, name=None, provider=None, type=None, credential=None):
            self.name = name
            self.provider = provider
            self.type = type
            self.credential = credential
            self.id = None
            self.deleted = False
            self.delete_error = None  # set to an Exception to fail delete()

        async def save(self):
            self.id = f"model:seed-{len(store) + 1}"
            store[str(self.id)] = self

        @classmethod
        async def get(cls, mid):
            rec = store.get(str(mid))
            if rec is None:
                raise NotFoundError(f"no model {mid}")
            return rec

        async def delete(self):
            if self.delete_error is not None:
                raise self.delete_error
            self.deleted = True
            store.pop(str(self.id), None)

    prev = _set_isolation_env(active_ns, active_db)
    try:
        with mock.patch.object(M, "Model", _Model), mock.patch.object(
            M.DefaultModels, "get_instance", new=mock.AsyncMock(return_value=dm)
        ), mock.patch(_REQUIRE_ACTIVE_ISOLATION, new=mock.Mock()):
            yield dm, store, _Model
    finally:
        _set_isolation_env(*prev)


# --------------------------------------------------------------------------- #
# Context manager lifecycle — creates exact frozen fields, binds, owns teardown
# (§7/§8/§18) — cleanup runs THROUGH the CM from lexical state (no mocked teardown)
# --------------------------------------------------------------------------- #

def test_cm_creates_exact_frozen_fields_binds_and_owns_teardown():
    seen = {}
    with _fake_model_db(initial_default=None) as (dm, store, _ModelCls), mock.patch(
        "open_notebook.utils.embedding.generate_embedding",
        new=mock.AsyncMock(side_effect=AssertionError("seed must not call the provider")),
    ):
        async def _run():
            async with S.seeded_frozen_embedding_model() as mid:
                rec = store[str(mid)]
                seen.update(
                    mid=mid, name=rec.name, provider=rec.provider, type=rec.type,
                    credential=rec.credential, bound=dm.default_embedding_model,
                )

        asyncio.run(_run())

    assert seen["name"] == FROZEN_EMBEDDING_MODEL
    assert seen["provider"] == FROZEN_EMBEDDING_PROVIDER
    assert seen["type"] == "embedding"
    assert seen["credential"] is None  # env-fallback credential, none stored (§8/§25)
    assert seen["bound"] == seen["mid"]  # §18 default bound to the created model in-scope
    # Real teardown ran on CM exit (from lexical state): prior default (None) restored and
    # the owned model deleted — never from any caller-supplied object/id.
    assert dm.default_embedding_model is None
    assert store == {}


# --------------------------------------------------------------------------- #
# Conflict policy (§15) — exact-match reuse / fail-closed on mismatch
# --------------------------------------------------------------------------- #

def test_conflict_policy_reuses_exact_frozen_match_and_does_not_teardown():
    existing = _FakeModelRec("model:existing")  # frozen identity by default
    with _fake_model_db(
        initial_default="model:existing", preexisting={"model:existing": existing},
    ) as (dm, store, _ModelCls):

        async def _run():
            async with S.seeded_frozen_embedding_model() as mid:
                assert mid == "model:existing"

        asyncio.run(_run())
    # A reused pre-existing seed is NOT owned by this scope → left fully intact.
    assert existing.deleted is False
    assert dm.default_embedding_model == "model:existing"
    assert store == {"model:existing": existing}  # nothing created, nothing deleted


def test_conflict_policy_fail_closed_on_mismatch():
    other = _FakeModelRec("model:other", name="text-embedding-ada-002", provider="openai")
    with _fake_model_db(
        initial_default="model:other", preexisting={"model:other": other},
    ) as (dm, store, _ModelCls):

        async def _run():
            async with S.seeded_frozen_embedding_model():
                pass

        with pytest.raises(S.ConflictingEmbeddingModelError):
            asyncio.run(_run())
    # Never silently overwrite/delete a mismatched model; never create a second one.
    assert other.deleted is False
    assert dm.default_embedding_model == "model:other"
    assert store == {"model:other": other}


# --------------------------------------------------------------------------- #
# Active-isolation guard (§12) — never seed the normal namespace
# --------------------------------------------------------------------------- #

def test_seed_requires_active_isolation_fail_closed():
    from open_notebook.integrations.graphrag.eval.isolation08 import (
        IsolationOwnershipError,
    )

    async def _run():
        async with S.seeded_frozen_embedding_model():
            pass

    # No active isolation → the (unconditional) guard refuses before any DB access.
    with pytest.raises(IsolationOwnershipError):
        asyncio.run(_run())


def test_seed_guards_isolation_before_any_db_access():
    # RR3-H1 / RR5-H1: the CM (the only entrypoint) guards active isolation FIRST — before
    # any DB access, UNCONDITIONALLY (no caller bypass) — so an in-process caller outside
    # isolated_surreal_eval_runtime cannot mutate the normal namespace. Prove: raises, and
    # neither Model nor DefaultModels touched.
    import open_notebook.ai.models as M
    from open_notebook.integrations.graphrag.eval.isolation08 import (
        IsolationOwnershipError,
    )

    model_ctor = mock.Mock(side_effect=AssertionError("Model must not be constructed"))
    get_instance = mock.AsyncMock(
        side_effect=AssertionError("DefaultModels must not be read")
    )
    with mock.patch.object(M, "Model", model_ctor), mock.patch.object(
        M.DefaultModels, "get_instance", new=get_instance
    ):
        async def _run():
            async with S.seeded_frozen_embedding_model():
                pass

        with pytest.raises(IsolationOwnershipError):
            asyncio.run(_run())

    assert model_ctor.call_count == 0  # MODEL_ROWS_CREATED = 0
    assert get_instance.await_count == 0  # DEFAULT_MODELS never even read


# --------------------------------------------------------------------------- #
# B1EW2-RR5-H1 (Cycle #6) — NO caller-controlled isolation bypass on create/bind
# --------------------------------------------------------------------------- #

def test_seed_signature_has_no_isolation_bypass_parameter():
    # The production create/bind CM must expose no guard-disable switch of any kind.
    import inspect

    params = set(inspect.signature(S.seeded_frozen_embedding_model).parameters)
    assert params == set(), f"seed CM must take no parameters; found {params}"
    for forbidden in (
        "require_isolation", "skip_isolation", "allow_unisolated",
        "bypass_guard", "unsafe", "test_mode", "require_guard", "validate_isolation",
    ):
        assert forbidden not in params, forbidden


def test_seed_rejects_legacy_require_isolation_kwarg_before_any_db_access():
    # A legacy caller passing require_isolation=False is rejected at call binding (TypeError)
    # BEFORE any isolation check / DB access / seed creation — the bypass simply does not exist.
    import open_notebook.ai.models as M

    model_ctor = mock.Mock(side_effect=AssertionError("Model must not be constructed"))
    get_instance = mock.AsyncMock(
        side_effect=AssertionError("DefaultModels must not be read")
    )
    with mock.patch.object(M, "Model", model_ctor), mock.patch.object(
        M.DefaultModels, "get_instance", new=get_instance
    ):
        async def _run():
            async with S.seeded_frozen_embedding_model(require_isolation=False):
                pass

        with pytest.raises(TypeError):
            asyncio.run(_run())

    assert model_ctor.call_count == 0  # DB_ACCESS_BEFORE_ISOLATION_REJECTION = 0
    assert get_instance.await_count == 0

    assert model_ctor.call_count == 0  # MODEL_ROWS_CREATED = 0
    assert get_instance.await_count == 0  # DEFAULT_MODELS_MUTATED = NO (never even read)


# --------------------------------------------------------------------------- #
# B1EW2-R1-H2 — public live entrypoint exposes no composition / trust-root override
# --------------------------------------------------------------------------- #

def test_public_run_live_has_no_composition_or_trust_root_params():
    import inspect

    params = set(inspect.signature(R.run_live_b1_execution).parameters)
    assert params == {
        "operator_grant",
        "git_baseline_attestation",
        "observed_fixture_hash",
        "env",
    }
    for forbidden in (
        "model_attestor",
        "model_seed",
        "isolation",
        "seams_builder",
        "builder_kwargs",
        "fx",
    ):
        assert forbidden not in params, forbidden


@pytest.mark.parametrize(
    "kwargs",
    [
        {"model_attestor": lambda: None},
        {"model_seed": lambda: None},
        {"isolation": lambda _r: None},
        {"seams_builder": lambda *a, **k: None},
        {"builder_kwargs": {"model_attestor": lambda: None}},
    ],
)
def test_public_run_live_rejects_injection_kwargs(kwargs):
    # A live caller cannot substitute any composition / trust-root override: the public
    # signature rejects it at call binding (TypeError) BEFORE any isolation/seed/mint/boot.
    grant = mock.Mock()
    grant.run_id = "ew2-reject"
    with pytest.raises(TypeError):
        asyncio.run(
            R.run_live_b1_execution(
                operator_grant=grant,
                git_baseline_attestation=mock.Mock(),
                **kwargs,
            )
        )


def test_builder_rejects_model_attestor_override():
    # The seams builder no longer accepts a model_attestor override (trust root owned
    # internally); a legacy kwarg is rejected at call binding. (That the builder DOES resolve
    # the real module-level build_real_model_attestor is proven by the EW1 composition tests,
    # which patch it and run the driver successfully.)
    from open_notebook.integrations.graphrag.eval.datasetpn02 import load_fixture

    with pytest.raises(TypeError):
        R.build_real_b1_live_seams(
            load_fixture(), run_id="ew2-reject", model_attestor=lambda: None
        )


# --------------------------------------------------------------------------- #
# B1EW2-RR4-M1 (Cycle #5) — teardown is a LEXICAL closure inside the CM (there is
# NO module-level teardown primitive to import/call). Every cleanup scenario below
# is driven THROUGH the public context manager, isolation- + ownership- + state-
# bound, restore-before-delete.
# --------------------------------------------------------------------------- #

def test_cm_cleanup_normal_restores_prior_then_deletes_owned():
    # §30: on normal exit the prior default (None on a fresh namespace) is restored FIRST,
    # then the owned model is deleted.
    rec_box = {}
    with _fake_model_db(initial_default=None) as (dm, store, _ModelCls):

        async def _run():
            async with S.seeded_frozen_embedding_model() as mid:
                rec_box["rec"] = store[str(mid)]

        asyncio.run(_run())
    assert dm.default_embedding_model is None    # prior restored
    assert dm.update_calls >= 2                   # create-bind + cleanup-restore
    assert rec_box["rec"].deleted is True         # owned model deleted
    assert store == {}


def test_cm_cleanup_changed_default_fails_closed():
    # §22: if the default is changed to X during the scope, cleanup fails closed — X is
    # preserved and the owned model is NOT deleted.
    rec_box = {}
    with _fake_model_db(initial_default=None) as (dm, store, _ModelCls):

        async def _run():
            async with S.seeded_frozen_embedding_model() as mid:
                rec_box["rec"] = store[str(mid)]
                dm.default_embedding_model = "model:someone_else"  # changed during scope

        with pytest.raises(S.SeedCleanupOwnershipError):
            asyncio.run(_run())
    assert dm.default_embedding_model == "model:someone_else"  # NEW default preserved
    assert rec_box["rec"].deleted is False                     # no unrelated delete


def test_cm_cleanup_mutated_model_identity_fails_closed():
    # §24: same id but the owned model's identity was mutated → refuse the destructive delete.
    rec_box = {}
    with _fake_model_db(initial_default=None) as (dm, store, _ModelCls):

        async def _run():
            async with S.seeded_frozen_embedding_model() as mid:
                rec_box["rec"] = store[str(mid)]
                rec_box["rec"].name = "text-embedding-3-large"  # mutate frozen identity

        with pytest.raises(S.SeedCleanupOwnershipError):
            asyncio.run(_run())
    assert rec_box["rec"].deleted is False


def test_cm_cleanup_cross_namespace_fails_closed():
    # §21: if the active isolation identity changes between creation and cleanup, teardown
    # fails closed (no cross-namespace mutation of an unrelated namespace's state).
    rec_box = {}
    with _fake_model_db(
        active_ns="graphrag_eval_A", active_db="graphrag_08_A", initial_default=None,
    ) as (dm, store, _ModelCls):

        async def _run():
            async with S.seeded_frozen_embedding_model() as mid:
                rec_box["rec"] = store[str(mid)]
                _set_isolation_env("graphrag_eval_B", "graphrag_08_B")  # switch identity

        with pytest.raises(S.SeedCleanupOwnershipError):
            asyncio.run(_run())
    assert rec_box["rec"].deleted is False


def test_cm_cleanup_restore_failure_does_not_delete():
    # §17 load-bearing: if restoring the prior default fails, the owned model is NEVER deleted
    # (restore-before-delete), so the default can never dangle on a removed record.
    rec_box = {}
    with _fake_model_db(initial_default=None) as (dm, store, _ModelCls):

        async def _run():
            async with S.seeded_frozen_embedding_model() as mid:
                rec_box["rec"] = store[str(mid)]
                dm.update_error = RuntimeError("restore boom")  # fail the cleanup restore

        with pytest.raises(RuntimeError):
            asyncio.run(_run())
    assert rec_box["rec"].deleted is False


def test_cm_cleanup_delete_failure_after_prior_restored():
    # §18 load-bearing: if the delete fails, the prior default has ALREADY been restored
    # (restore-before-delete), so the default never dangles; the error still surfaces.
    rec_box = {}
    with _fake_model_db(initial_default=None) as (dm, store, _ModelCls):

        async def _run():
            async with S.seeded_frozen_embedding_model() as mid:
                rec_box["rec"] = store[str(mid)]
                rec_box["rec"].delete_error = RuntimeError("delete boom")

        with pytest.raises(RuntimeError):
            asyncio.run(_run())
    assert dm.default_embedding_model is None    # prior default restored before the delete
    assert dm.update_calls >= 2                   # create-bind + cleanup-restore both ran
    assert rec_box["rec"].deleted is False        # delete raised


def test_cm_runs_cleanup_on_exception_for_created_seed():
    # §22: an exception inside the seeded scope still runs the lexical cleanup on exit —
    # owned model deleted, prior default restored — with no module-level teardown involved.
    rec_box = {}
    with _fake_model_db(initial_default=None) as (dm, store, _ModelCls):

        async def _run():
            async with S.seeded_frozen_embedding_model() as mid:
                rec_box["rec"] = store[str(mid)]
                raise RuntimeError("boom inside seeded scope")

        with pytest.raises(RuntimeError):
            asyncio.run(_run())
    assert rec_box["rec"].deleted is True
    assert dm.default_embedding_model is None
    assert store == {}


# --------------------------------------------------------------------------- #
# B1EW2-RR1-H2 — public CLI evaluator exposes no trust-root / runner override
# --------------------------------------------------------------------------- #

def test_public_cli_evaluator_has_no_trust_root_overrides():
    import inspect

    from open_notebook.integrations.graphrag.eval import cli_live_pn02d as cli

    params = set(inspect.signature(cli.evaluate_execute_b1_live).parameters)
    assert params == {"manifest_path", "explicit_authorize", "env"}
    for forbidden in ("git_baseline_reader", "fixture_hash_reader", "live_runner"):
        assert forbidden not in params, forbidden


@pytest.mark.parametrize(
    "override",
    [
        {"git_baseline_reader": lambda: None},
        {"fixture_hash_reader": lambda: (True, "x")},
        {"live_runner": lambda **_k: None},
    ],
)
def test_public_cli_evaluator_rejects_reader_and_runner_overrides(override):
    # A public in-process caller cannot substitute the trusted Git/fixture observation or the
    # live runner: the public evaluator rejects the removed override at call binding (TypeError)
    # before any git validation / isolation / preflight / mint / Docker boot / provider traffic.
    from open_notebook.integrations.graphrag.eval import cli_live_pn02d as cli

    with pytest.raises(TypeError):
        cli.evaluate_execute_b1_live(
            manifest_path=None,
            explicit_authorize=False,
            env={},
            **override,
        )


# --------------------------------------------------------------------------- #
# precheck08 single-source delegation (§22) — enters the shared private seed CM
# --------------------------------------------------------------------------- #

def test_precheck08_uses_shared_seed_context_manager():
    from open_notebook.integrations.graphrag.eval import precheck08 as P

    # precheck08 no longer defines its own seed/restore wrappers (RR3-H1 / Cycle #4): it
    # imports and enters the SAME private seed context manager (via an AsyncExitStack), so the
    # cleanup authority is the single source of truth and cannot diverge.
    assert P.seeded_frozen_embedding_model is S.seeded_frozen_embedding_model
    assert not hasattr(P, "seed_temp_embedding_model")
    assert not hasattr(P, "restore_default_and_delete_model")


def test_precheck08_seed_enter_is_immediately_followed_by_cleanup_finally():
    # B1EW2-RR4-L1: at EVERY precheck08 seed site, the successful `enter_async_context` of the
    # seed CM is IMMEDIATELY followed by a `try` whose `finally` closes seed_stack — there is
    # no statement between a successful enter and the cleanup-owning scope, so an exception in
    # the former gap (dim probe / runner construction) can never skip aclose(). Structural /
    # DB-free proof (isolation is a local import, so this cannot be a plain offline unit test).
    import ast
    import inspect

    from open_notebook.integrations.graphrag.eval import precheck08 as P

    tree = ast.parse(inspect.getsource(P))

    def _is_seed_enter(stmt) -> bool:
        # ``st.temp_model_id = await seed_stack.enter_async_context(...)``
        val = getattr(stmt, "value", None)
        if not isinstance(val, ast.Await) or not isinstance(val.value, ast.Call):
            return False
        fn = val.value.func
        return isinstance(fn, ast.Attribute) and fn.attr == "enter_async_context"

    def _closes_seed_stack(node) -> bool:
        for n in ast.walk(node):
            if (
                isinstance(n, ast.Call)
                and isinstance(n.func, ast.Attribute)
                and n.func.attr == "aclose"
                and isinstance(n.func.value, ast.Name)
                and n.func.value.id == "seed_stack"
            ):
                return True
        return False

    sites = 0
    for parent in ast.walk(tree):
        block = getattr(parent, "body", None)
        if not isinstance(block, list):
            continue
        for i, stmt in enumerate(block):
            if isinstance(stmt, ast.Assign) and _is_seed_enter(stmt):
                sites += 1
                assert i + 1 < len(block), "seed enter is the last statement (no cleanup)"
                nxt = block[i + 1]
                assert isinstance(nxt, ast.Try), (
                    "seed enter is not immediately wrapped by a try/finally (RR4-L1 gap)"
                )
                assert any(_closes_seed_stack(s) for s in nxt.finalbody), (
                    "the wrapping try has no finally that closes seed_stack (RR4-L1 gap)"
                )
    assert sites >= 2, f"expected >=2 precheck08 seed sites, found {sites}"


# --------------------------------------------------------------------------- #
# run_live_b1_execution ordering (§9/§23): isolation < seed < seams < driver
# --------------------------------------------------------------------------- #

def test_run_live_b1_execution_seeds_inside_isolation_before_driver():
    from open_notebook.integrations.graphrag.eval.datasetpn02 import load_fixture

    events: list[str] = []

    @asynccontextmanager
    async def _rec_isolation(_run_id):
        events.append("isolation_enter")
        try:
            yield None
        finally:
            events.append("isolation_exit")

    @asynccontextmanager
    async def _rec_seed():
        events.append("seed_enter")
        try:
            yield "model:seed"
        finally:
            events.append("seed_exit")

    def _rec_seams_builder(fx, *, run_id, env=None, **kwargs):
        events.append("seams_built")
        return mock.sentinel.seams

    class _FakeDriver:
        def __init__(self, fx, seams):
            events.append("driver_ctor")

        async def run(self, **_kw):
            events.append("driver_run")
            return mock.sentinel.outcome

    grant = mock.Mock()
    grant.run_id = "ew2-order"

    with mock.patch.object(R, "RealB1Driver", _FakeDriver):
        outcome = asyncio.run(
            R._run_live_b1_execution_composed(
                operator_grant=grant,
                git_baseline_attestation=mock.Mock(),
                observed_fixture_hash="hash",
                fx=load_fixture(),
                seams_builder=_rec_seams_builder,
                isolation=_rec_isolation,
                model_seed=_rec_seed,
                builder_kwargs={},
            )
        )

    assert outcome is mock.sentinel.outcome
    # Strict ordering: isolation entered, THEN seed, THEN seams built, THEN driver run;
    # teardown reverses (seed exits before isolation).
    assert events == [
        "isolation_enter",
        "seed_enter",
        "seams_built",
        "driver_ctor",
        "driver_run",
        "seed_exit",
        "isolation_exit",
    ]


def test_default_model_seed_is_the_real_shared_seed():
    # The production default seed enters the shared frozen-embedding seed context (not a
    # fake): patch the shared CM and confirm _default_model_seed drives it.
    entered = {"n": 0}

    @asynccontextmanager
    async def _fake_seed():
        entered["n"] += 1
        yield "model:frozen"

    with mock.patch.object(S, "seeded_frozen_embedding_model", _fake_seed):
        async def _run():
            async with R._default_model_seed() as mid:
                return mid

        assert asyncio.run(_run()) == "model:frozen"
    assert entered["n"] == 1


# --------------------------------------------------------------------------- #
# Governance: EW2 is now a HISTORICAL checkpoint (its annotated tag exists at 707c8782);
# the PN02D-B1-EW3 isolation-runtime-id fix superseded it, and after the EW3 -> EW4 -> EW5 -> EW6 -> EW7 -> EW8
# supersession chain the CURRENT approved identity is EW8. The current-successor governance +
# checkpoint-lifecycle (State-A/State-B) coverage lives in tests/test_graphrag_pn02db1ew7.py.
# Test-only, zero provider traffic.
# --------------------------------------------------------------------------- #

def test_governance_ew2_is_historical_current_is_ew8():
    from open_notebook.integrations.graphrag.eval import authb1r2pn02d as B
    from open_notebook.integrations.graphrag.eval.authmintlivepn02d import (
        EXPECTED_B1_R2_CHECKPOINT_TAG,
        EXPECTED_EW1_CHECKPOINT_TAG,
        EXPECTED_EW2_CHECKPOINT_TAG,
        EXPECTED_EW3_CHECKPOINT_TAG,
        EXPECTED_EW4_CHECKPOINT_TAG,
        EXPECTED_EW5_CHECKPOINT_TAG,
        EXPECTED_EW6_CHECKPOINT_TAG,
        EXPECTED_EW7_CHECKPOINT_TAG,
    EXPECTED_EW8_CHECKPOINT_TAG,
        EXPECTED_PF1_CHECKPOINT_TAG,
        current_approved_b1_r2_checkpoint,
    )

    # EW8 is now the frozen approved provider-authorization identity (EW6 superseded); EW2
    # remains a retained HISTORICAL identity (its constant/tag string are unchanged).
    assert current_approved_b1_r2_checkpoint() == EXPECTED_EW8_CHECKPOINT_TAG
    assert B.B1_R2_EXPECTED_CHECKPOINT_TAG == EXPECTED_EW8_CHECKPOINT_TAG
    assert EXPECTED_EW2_CHECKPOINT_TAG == "graphrag-pn02db1ew2-isolated-model-seed-approved"
    assert current_approved_b1_r2_checkpoint() != EXPECTED_EW2_CHECKPOINT_TAG
    # EW7/EW6/EW5/EW4/EW3/EW2/EW1/PF1/B1-R2 are all distinct identities.
    assert len({
        EXPECTED_EW8_CHECKPOINT_TAG,
        EXPECTED_EW7_CHECKPOINT_TAG,
        EXPECTED_EW6_CHECKPOINT_TAG,
        EXPECTED_EW5_CHECKPOINT_TAG,
        EXPECTED_EW4_CHECKPOINT_TAG,
        EXPECTED_EW3_CHECKPOINT_TAG,
        EXPECTED_EW2_CHECKPOINT_TAG,
        EXPECTED_EW1_CHECKPOINT_TAG,
        EXPECTED_PF1_CHECKPOINT_TAG,
        EXPECTED_B1_R2_CHECKPOINT_TAG,
    }) == 10


def test_ew2_historical_tag_immutable_and_cannot_substitute_for_ew8():
    # The EW2 annotated tag is HISTORICAL: when present in real Git it ALWAYS peels to the EW2
    # checkpoint commit 707c8782 (never moved). It is NO LONGER the current approved identity
    # (that is EW8), and naming it as the approved-expected identity for a later/successor
    # checkpoint is refused.
    from open_notebook.integrations.graphrag.eval import authb1r2pn02d as B
    from open_notebook.integrations.graphrag.eval.authmintlivepn02d import (
        EXPECTED_EW2_CHECKPOINT_TAG,
        RealTrustedB1R2Reader,
        current_approved_b1_r2_checkpoint,
        verify_b1_r2_checkpoint,
    )

    assert EXPECTED_EW2_CHECKPOINT_TAG != current_approved_b1_r2_checkpoint()
    obs = RealTrustedB1R2Reader().observe(EXPECTED_EW2_CHECKPOINT_TAG)
    if obs.observed_tag_exists:
        assert obs.observed_tag_peel == "707c8782a2ea34e59700ae4cd4d6d1ea403dd2c0"

    # EW2 cannot serve as the approved-expected identity: a grant bound to a DIFFERENT
    # (successor) commit, verified against approved_expected=EW2, is refused (identity mismatch).
    future = "e3e3e3e3" + "0" * 32
    grant = B.build_b1_r2_operator_grant(approved_git_commit=future)  # grant identity == a successor commit (!= EW2)
    reasons = verify_b1_r2_checkpoint(
        reader=C.b1r2_reader_ok(tag=EXPECTED_EW2_CHECKPOINT_TAG, peel=future, head=future),
        operator_grant=grant,
        approved_expected_checkpoint=EXPECTED_EW2_CHECKPOINT_TAG,
        git_baseline=C.clean_git_baseline(commit=future, tag=EXPECTED_EW2_CHECKPOINT_TAG),
    )
    assert reasons, "EW2 must not satisfy the EW3 checkpoint"


# --------------------------------------------------------------------------- #
# LIVE (gated) — real isolated namespace end-to-end
# --------------------------------------------------------------------------- #

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


@live_only
def test_live_defect_reproduced_then_seed_resolves_and_reaches_provider_boundary(monkeypatch):
    # §25: a DUMMY non-secret provider key so client construction never uses the real secret.
    monkeypatch.setenv("OPENROUTER_API_KEY", "dummy-ew2-nonsecret-key")
    # §16 speed: a single embed attempt (no retry sleeps) before the wrapping RuntimeError.
    monkeypatch.setattr("open_notebook.utils.embedding.EMBEDDING_MAX_RETRIES", 1)

    from open_notebook.ai.models import DefaultModels, Model, model_manager
    from open_notebook.integrations.graphrag.eval.isolation08 import (
        isolated_surreal_eval_runtime,
    )
    from open_notebook.integrations.graphrag.eval.realseamspn02d import (
        build_real_model_attestor,
    )
    from open_notebook.utils.embedding import generate_embeddings

    async def _run():
        async with isolated_surreal_eval_runtime():
            # (§3) DEFECT reproduced: fresh isolated namespace has no default + no resolution.
            d0 = await DefaultModels.get_instance()
            assert d0.default_embedding_model is None
            assert await model_manager.get_embedding_model() is None
            with pytest.raises(ValueError):
                await generate_embeddings(["ew2 pre-seed probe"])

            async with S.seeded_frozen_embedding_model() as model_id:
                # (§18) default bound to the seeded model.
                d1 = await DefaultModels.get_instance()
                assert d1.default_embedding_model == model_id
                # (§19) the normal stack resolves the frozen model.
                em = await model_manager.get_embedding_model()
                assert em is not None
                seeded = await Model.get(model_id)
                assert S.is_frozen_embedding_identity(seeded)
                # (§20) the REAL attestor reads the SAME seeded state and matches frozen.
                att = await build_real_model_attestor()()
                assert att.matches_frozen is True
                assert att.dimension == FROZEN_EMBEDDING_DIM

                # (§21) the corpus embedding path now reaches the PROVIDER boundary (old
                # ValueError gone) — a sentinel at the provider embed stops it there with
                # ZERO real provider traffic.
                calls = {"n": 0}

                async def _boom(self, *_a, **_k):
                    calls["n"] += 1
                    raise RuntimeError("EW2_PROVIDER_SENTINEL")

                monkeypatch.setattr(type(em), "aembed", _boom, raising=False)
                try:
                    await generate_embeddings(["ew2 provider-boundary probe"])
                    raise AssertionError("sentinel should have stopped the embed")
                except Exception as exc:  # noqa: BLE001
                    assert "No embedding model configured" not in str(exc)
                assert calls["n"] >= 1  # provider boundary reached (model resolved)

            # (§13) teardown: default restored to None and temp Model deleted (Model.get
            # raises NotFoundError for a missing record rather than returning None).
            from open_notebook.exceptions import NotFoundError

            d2 = await DefaultModels.get_instance()
            assert d2.default_embedding_model is None
            with pytest.raises(NotFoundError):
                await Model.get(model_id)

    asyncio.run(_run())


@live_only
def test_live_precheck_seed_cleaned_up_when_post_entry_raises(monkeypatch):
    # B1EW2-RR4-L1 (functional): a raise in the FORMER gap (the embedding-dim probe, which
    # runs after the seed CM is entered but before the inner try) still runs the seed CM's
    # teardown — the temp Model is deleted and the default restored to None. Sidecar and
    # normal-DB probes are stubbed; the REAL isolated runtime + REAL seed CM run against the
    # local SurrealDB. NO sidecar/Docker and ZERO provider traffic.
    from open_notebook.integrations.graphrag.eval import precheck08 as P

    captured = {}

    async def _probe_boom():
        # Prove the seed default is live at the moment of the gap-raise, then blow up.
        from open_notebook.ai.models import DefaultModels

        d = await DefaultModels.get_instance()
        captured["mid_at_raise"] = d.default_embedding_model
        raise RuntimeError("dim probe boom in the former enter->cleanup gap")

    async def _zero_sources():
        return 0

    monkeypatch.setattr(P, "start_sidecar", lambda: None)
    monkeypatch.setattr(P, "stop_sidecar", lambda: None)
    monkeypatch.setattr(P, "sidecar_running", lambda: False)
    monkeypatch.setattr(
        P, "await_sidecar_health",
        mock.AsyncMock(return_value={"version": "v1.5.6"}),
    )
    monkeypatch.setattr(P, "_normal_source_count", _zero_sources)
    monkeypatch.setattr(P, "_embedding_dim_probe", _probe_boom)

    st = asyncio.run(P.run_micro_precheck(artifact_dir=None))

    # The seed WAS created (its default was live in the temp namespace at the gap-raise)…
    assert captured.get("mid_at_raise")
    # …and the seed CM teardown STILL ran despite the gap-raise (restore prior + delete owned,
    # inside the still-active isolation): cleanup succeeded — no enter->cleanup gap (RR4-L1).
    assert st.temp_model_cleanup_ok is True
    assert st.state == "FAILED"  # the probe failure is recorded, run fails closed
