"""PN02D-B3G — provider-free tests for the durable one-shot live-authorization ledger.

Covers the ledger primitive (atomic claim / replay / concurrent double-claim / process-restart /
crash-after-claim / distinct grants / fail-closed on unavailable-corrupt-schema / content-safety /
digest binding) AND the shared B1/B2/B3 execution-boundary enforcement (claim exactly once, before
the first provider-bound action; a replay is refused; mint stays side-effect-free).

ZERO provider traffic / ZERO real execution: the ledger uses temporary DB files and the shared
driver runs against the full mock seam bundle.
"""

from __future__ import annotations

import sqlite3
import threading
from pathlib import Path
from types import SimpleNamespace

import graphrag_pn02db0cb_common as C
import pytest

import open_notebook.integrations.graphrag.eval.authmintlivepn02d as authmint
from open_notebook.config import DATA_FOLDER
from open_notebook.integrations.graphrag.eval import authledgerpn02d as L
from open_notebook.integrations.graphrag.eval.authmintlivepn02d import (
    EXPECTED_B2_CHECKPOINT_TAG,
    EXPECTED_B3B_CHECKPOINT_TAG,
    frozen_b2_operator_grant_template,
    frozen_b3b_operator_grant_template,
    mint_live_b2_provider_run_authorization,
    mint_live_b3_provider_run_authorization,
)
from open_notebook.integrations.graphrag.eval.driver_live_pn02d import RealB1Driver


def _ledger(tmp_path):
    return str(tmp_path / "live_auth_consumption.sqlite")


def _identity(*, kind="B1", run="pn02d-run-A", ckpt="tag-x", commit="c" * 40,
              fixture=C.verify_fixture_hash()[1], provider="prov-fp"):
    return L.GrantIdentity(
        execution_kind=kind, run_id=run, b1_r2_checkpoint=ckpt,
        approved_git_commit=commit, fixture_hash=fixture, provider_config_fingerprint=provider,
    )


# --------------------------------------------------------------------------- #
# Digest — stability + binding (§61-§64)
# --------------------------------------------------------------------------- #


def test_digest_algorithm_and_canonical_encoding():
    d = L.compute_grant_digest(_identity())
    assert isinstance(d, str) and len(d) == 64  # SHA-256 hex
    assert d == L.compute_grant_digest(_identity())  # stable across calls/instances


def test_digest_run_binding():
    assert L.compute_grant_digest(_identity(run="A")) != L.compute_grant_digest(_identity(run="B"))


def test_digest_profile_binding():
    assert L.compute_grant_digest(_identity(kind="B2")) != L.compute_grant_digest(_identity(kind="B3B"))


def test_digest_envelope_does_not_change_claim_key():
    # B3H (PN02DB3G-OR1-M1): the claim identity is profile+run_id ONLY. Changing any execution-
    # envelope field (checkpoint tag/commit, fixture, provider fingerprint) must NOT change the
    # digest — so an envelope change can never mint a second claimable key for the same grant.
    base = L.compute_grant_digest(_identity())
    assert L.compute_grant_digest(_identity(ckpt="different-tag")) == base
    assert L.compute_grant_digest(_identity(commit="a" * 40)) == base
    assert L.compute_grant_digest(_identity(fixture="different-fixture")) == base
    assert L.compute_grant_digest(_identity(provider="different-provider")) == base


# --------------------------------------------------------------------------- #
# Claim / replay / restart / crash (§41, §42, §44, §46)
# --------------------------------------------------------------------------- #


def test_basic_first_claim(tmp_path):
    assert L.claim_grant(_identity(), ledger_path=_ledger(tmp_path)) is L.ClaimResult.CLAIMED


def test_second_claim_already_consumed(tmp_path):
    p = _ledger(tmp_path)
    assert L.claim_grant(_identity(), ledger_path=p) is L.ClaimResult.CLAIMED
    assert L.claim_grant(_identity(), ledger_path=p) is L.ClaimResult.ALREADY_CONSUMED


def test_process_restart_replay(tmp_path):
    # Each claim_grant opens+closes its own connection => "restart"; state is durable on disk.
    p = _ledger(tmp_path)
    assert L.claim_grant(_identity(), ledger_path=p) is L.ClaimResult.CLAIMED
    assert L.is_consumed(_identity(), ledger_path=p) is True
    # A fresh process/connection sees it consumed.
    assert L.claim_grant(_identity(), ledger_path=p) is L.ClaimResult.ALREADY_CONSUMED


def test_crash_after_claim_stays_consumed(tmp_path):
    # Model a crash: claim, then "no completion update", then a new connection retries.
    p = _ledger(tmp_path)
    L.claim_grant(_identity(), ledger_path=p)
    # Simulate a completely new process by only re-opening the file.
    assert L.claim_grant(_identity(), ledger_path=p) is L.ClaimResult.ALREADY_CONSUMED


def test_distinct_grants_independent(tmp_path):
    p = _ledger(tmp_path)
    assert L.claim_grant(_identity(run="A"), ledger_path=p) is L.ClaimResult.CLAIMED
    # A different legitimate grant remains claimable.
    assert L.claim_grant(_identity(run="B"), ledger_path=p) is L.ClaimResult.CLAIMED


def test_concurrent_double_claim(tmp_path):
    # Two INDEPENDENT connections race the SAME digest; exactly one CLAIMED, one ALREADY_CONSUMED.
    p = _ledger(tmp_path)
    L.claim_grant(_identity(run="warm"), ledger_path=p)  # create schema first
    ident = _identity(run="RACE")
    barrier = threading.Barrier(2)
    results = []

    def _worker():
        barrier.wait()
        results.append(L.claim_grant(ident, ledger_path=p))

    workers = [threading.Thread(target=_worker) for _ in range(2)]
    for w in workers:
        w.start()
    for w in workers:
        w.join()
    assert results.count(L.ClaimResult.CLAIMED) == 1
    assert results.count(L.ClaimResult.ALREADY_CONSUMED) == 1


# --------------------------------------------------------------------------- #
# Fail-closed: unavailable / corrupt / schema (§51, §52, §53)
# --------------------------------------------------------------------------- #


def test_ledger_unavailable_fails_closed(tmp_path):
    # A ledger whose parent directory cannot be created (parent path is an existing FILE) fails
    # closed. (A merely-missing parent dir is legitimately created for the production first run.)
    blocker = tmp_path / "blocker_file"
    blocker.write_text("not a directory", encoding="utf-8")
    bad = str(blocker / "sub" / "ledger.sqlite")
    with pytest.raises(L.LedgerUnavailableError):
        L.claim_grant(_identity(), ledger_path=bad)


def test_ledger_corruption_fails_closed_no_recreate(tmp_path):
    p = _ledger(tmp_path)
    with open(p, "wb") as fh:
        fh.write(b"this is definitely not a sqlite database file" * 8)
    with pytest.raises(L.LedgerCorruptError):
        L.claim_grant(_identity(), ledger_path=p)


def test_ledger_future_schema_fails_closed(tmp_path):
    p = _ledger(tmp_path)
    L.claim_grant(_identity(run="warm"), ledger_path=p)  # init schema v1
    conn = sqlite3.connect(p)
    conn.execute("UPDATE meta SET schema_version = 999")
    conn.commit()
    conn.close()
    with pytest.raises(L.LedgerSchemaError):
        L.claim_grant(_identity(run="new"), ledger_path=p)


# --------------------------------------------------------------------------- #
# Content safety + auditability (§60, §36)
# --------------------------------------------------------------------------- #


def test_ledger_content_safe_and_auditable(tmp_path):
    p = _ledger(tmp_path)
    grant = SimpleNamespace(
        run_id="pn02d-run-audit", b1_r2_checkpoint=EXPECTED_B2_CHECKPOINT_TAG,
        approved_git_commit="c" * 40, fixture_hash=C.verify_fixture_hash()[1],
        provider_config_fingerprint="prov-fp",
        openrouter_api_key="sk-SECRET-do-not-store", password="hunter2",
    )
    L.enforce_one_shot(operator_grant=grant, execution_kind="B2", ledger_path=p)
    conn = sqlite3.connect(p)
    cols = [r[1] for r in conn.execute("PRAGMA table_info(consumed_grants)").fetchall()]
    row = conn.execute("SELECT * FROM consumed_grants").fetchall()
    conn.close()
    # Auditable content-safe fields present...
    for c in ("grant_digest", "profile", "run_id", "checkpoint_commit", "checkpoint_tag",
              "execution_kind", "consumed_at", "schema_version"):
        assert c in cols
    # ...and NO secret ever persisted.
    blob = repr(row)
    assert "sk-SECRET" not in blob and "hunter2" not in blob and "openrouter_api_key" not in blob


# --------------------------------------------------------------------------- #
# enforce_one_shot at the abstraction level (§45 failed-execution replay)
# --------------------------------------------------------------------------- #


def test_enforce_one_shot_then_replay_refused(tmp_path):
    p = _ledger(tmp_path)
    grant = C.frozen_test_grant()
    L.enforce_one_shot(operator_grant=grant, execution_kind="B1", ledger_path=p)  # first: consumes
    # Simulated later failure does not restore; a retry of the same grant is refused.
    with pytest.raises(L.GrantAlreadyConsumedError):
        L.enforce_one_shot(operator_grant=grant, execution_kind="B1", ledger_path=p)


# --------------------------------------------------------------------------- #
# Shared execution-boundary integration: B1 / B2 / B3 claim EXACTLY ONCE, replay refused,
# claim happens BEFORE the first provider-bound action, mint stays side-effect-free.
# --------------------------------------------------------------------------- #


class _Patches:
    def __init__(self, *ctxs):
        self._ctxs = ctxs

    def __enter__(self):
        for c in self._ctxs:
            c.__enter__()
        return self

    def __exit__(self, *exc):
        for c in reversed(self._ctxs):
            c.__exit__(*exc)
        return False


def _gov(tag):
    from unittest import mock
    resolver = "current_approved_b2_checkpoint" if tag == EXPECTED_B2_CHECKPOINT_TAG else "current_approved_b3b_checkpoint"
    reader = C.b1r2_reader_ok(tag=tag, peel=C.TEST_COMMIT, head=C.TEST_COMMIT)
    return _Patches(
        mock.patch.object(authmint, resolver, return_value=tag),
        mock.patch.object(authmint, "_build_trusted_b1_r2_reader", return_value=reader),
    )


def _b2_grant():
    return frozen_b2_operator_grant_template(
        run_id="pn02d-b2-oneshot", implementation_checkpoint_commit=C.TEST_COMMIT,
        implementation_checkpoint_tag=C.TEST_TAG, b1_r2_checkpoint=EXPECTED_B2_CHECKPOINT_TAG,
        approved_git_commit=C.TEST_COMMIT, approved_git_tag=C.TEST_TAG,
    )


def _b3_grant():
    return frozen_b3b_operator_grant_template(
        run_id="pn02d-b3-oneshot", implementation_checkpoint_commit=C.TEST_COMMIT,
        implementation_checkpoint_tag=C.TEST_TAG, b1_r2_checkpoint=EXPECTED_B3B_CHECKPOINT_TAG,
        approved_git_commit=C.TEST_COMMIT, approved_git_tag=C.TEST_TAG,
    )


@pytest.mark.asyncio
async def test_b1_one_shot_enforced_claim_once_then_replay_refused(tmp_path):
    p = _ledger(tmp_path)
    fx = C.fixture()

    # First canonical B1 attempt reaches COMPLETE and consumes the grant exactly once.
    bundle = C.build_live_seams(fx)
    grant = C.frozen_test_grant()
    with C.approved_b1r2_governance():
        outcome = await RealB1Driver(fx, bundle.seams, oneshot_ledger_path=p).run(
            operator_grant=grant, git_baseline_attestation=C.clean_git_baseline())
    assert outcome.state == "COMPLETE"
    assert len(bundle.controller.started) == 3  # Boot-2 reached on the first attempt

    # Replay: a fresh process/driver with the SAME grant + SAME ledger is refused BEFORE Boot 2.
    bundle2 = C.build_live_seams(fx)
    with C.approved_b1r2_governance(), pytest.raises(L.GrantAlreadyConsumedError):
        await RealB1Driver(fx, bundle2.seams, oneshot_ledger_path=p).run(
            operator_grant=C.frozen_test_grant(), git_baseline_attestation=C.clean_git_baseline())
    assert len(bundle2.controller.started) == 0  # never reached the first provider-bound action


@pytest.mark.asyncio
async def test_b2_one_shot_enforced_claim_once_then_replay_refused(tmp_path):
    p = _ledger(tmp_path)
    fx = C.fixture()
    bundle = C.build_live_seams(fx)
    with _gov(EXPECTED_B2_CHECKPOINT_TAG):
        outcome = await RealB1Driver(
            fx, bundle.seams, execution_kind="B2", mint_fn=mint_live_b2_provider_run_authorization,
            oneshot_ledger_path=p,
        ).run(operator_grant=_b2_grant(), git_baseline_attestation=C.clean_git_baseline())
    assert outcome.state == "COMPLETE"
    bundle2 = C.build_live_seams(fx)
    with _gov(EXPECTED_B2_CHECKPOINT_TAG), pytest.raises(L.GrantAlreadyConsumedError):
        await RealB1Driver(
            fx, bundle2.seams, execution_kind="B2", mint_fn=mint_live_b2_provider_run_authorization,
            oneshot_ledger_path=p,
        ).run(operator_grant=_b2_grant(), git_baseline_attestation=C.clean_git_baseline())
    assert len(bundle2.controller.started) == 0


@pytest.mark.asyncio
async def test_b3_one_shot_enforced_and_no_double_claim(tmp_path):
    p = _ledger(tmp_path)
    fx = C.fixture()
    bundle = C.build_live_seams(fx)
    with _gov(EXPECTED_B3B_CHECKPOINT_TAG):
        outcome = await RealB1Driver(
            fx, bundle.seams, execution_kind="B3B", mint_fn=mint_live_b3_provider_run_authorization,
            oneshot_ledger_path=p,
        ).run(operator_grant=_b3_grant(), git_baseline_attestation=C.clean_git_baseline())
    assert outcome.state == "COMPLETE"
    # Exactly ONE claim recorded for the whole B3 attempt (no double claim by a B3 wrapper).
    conn = sqlite3.connect(p)
    count = conn.execute("SELECT COUNT(*) FROM consumed_grants").fetchone()[0]
    conn.close()
    assert count == 1
    # Replay refused.
    bundle2 = C.build_live_seams(fx)
    with _gov(EXPECTED_B3B_CHECKPOINT_TAG), pytest.raises(L.GrantAlreadyConsumedError):
        await RealB1Driver(
            fx, bundle2.seams, execution_kind="B3B", mint_fn=mint_live_b3_provider_run_authorization,
            oneshot_ledger_path=p,
        ).run(operator_grant=_b3_grant(), git_baseline_attestation=C.clean_git_baseline())


@pytest.mark.asyncio
async def test_mint_is_side_effect_free_no_consumption(tmp_path):
    # A validation-only mint (no canonical real execution) must NOT consume the grant.
    p = _ledger(tmp_path)
    auth = C.mint_test_live_auth()  # mints a LiveProviderRunAuthorization, runs NO driver
    assert auth is not None
    # The B1 grant identity is still unconsumed (mint has no ledger side effect).
    grant = C.frozen_test_grant()
    ident = L.grant_identity_from(grant, execution_kind="B1")
    # Nothing was ever written to this ledger path.
    assert L.is_consumed(ident, ledger_path=p) is False


@pytest.mark.asyncio
async def test_claim_happens_before_first_provider_call_ordering(tmp_path):
    # A spy enforcer records call order relative to Boot-2 (controller.started).
    fx = C.fixture()
    bundle = C.build_live_seams(fx)
    order = []

    def _spy(*, operator_grant, execution_kind, ledger_path=None):
        order.append(("claim", execution_kind, len(bundle.controller.started)))

    with C.approved_b1r2_governance():
        await RealB1Driver(fx, bundle.seams, one_shot_enforcer=_spy).run(
            operator_grant=C.frozen_test_grant(), git_baseline_attestation=C.clean_git_baseline())
    # The claim fired exactly once, for B1, BEFORE any provider-bound execution runtime started.
    assert order == [("claim", "B1", 0)]
    assert len(bundle.controller.started) == 3  # Boot-2 happened AFTER the claim


def test_no_unconsume_or_reset_api_exported():
    # The runtime module exposes NO normal-path unconsume/reset/delete.
    for forbidden in ("delete_consumed_grant", "reset_grant", "mark_unused", "unconsume", "clear"):
        assert not hasattr(L, forbidden)
        assert forbidden not in L.__all__


# --------------------------------------------------------------------------- #
# B3H — PN02DB3G-OR1-H1: authoritative path is NOT publicly redirectable, lives outside ./data.
# The autouse fixture patches default_ledger_path (an internal seam); the REAL production resolver
# _production_ledger_path is intentionally NOT patched, so these tests exercise it directly.
# --------------------------------------------------------------------------- #


def test_public_env_redirection_has_no_effect(monkeypatch):
    # The exact Codex H1 scenario: a public env var must NOT redirect the authoritative path.
    before = L._production_ledger_path()
    monkeypatch.setenv("OPEN_NOTEBOOK_LIVE_AUTH_ONESHOT_LEDGER", str(Path("x") / "fresh-empty.db"))
    after = L._production_ledger_path()
    assert after == before  # env value ignored entirely
    # And no public path-override symbol/env constant is exposed by the module.
    assert not hasattr(L, "LEDGER_PATH_ENV")


def test_production_path_outside_data_folder():
    prod = Path(L._production_ledger_path()).resolve()
    data_root = Path(DATA_FOLDER).resolve()
    surreal_root = Path("surreal_data").resolve()
    assert data_root not in prod.parents and prod != data_root
    assert surreal_root not in prod.parents
    # Resolves under the per-user security-state dir, deterministically.
    assert prod.parent.name == "security" and prod.parent.parent.name == ".open-notebook"
    assert prod.parent.parent.parent == Path.home().resolve()


def test_production_path_resolver_direct_and_stable():
    # A direct assertion of the true production resolver, NOT masked by the autouse test fixture.
    assert L._production_ledger_path() == L._production_ledger_path()  # deterministic
    assert L._production_ledger_path().endswith("live_auth_consumption.sqlite")


def test_backup_boundary_path_not_under_ordinary_data(monkeypatch):
    # Even if someone sets the (now-ignored) env to a data/ path, the authoritative path stays out.
    monkeypatch.setenv("OPEN_NOTEBOOK_LIVE_AUTH_ONESHOT_LEDGER", str(Path(DATA_FOLDER) / "x.db"))
    prod = Path(L._production_ledger_path()).resolve()
    assert Path(DATA_FOLDER).resolve() not in prod.parents


# --------------------------------------------------------------------------- #
# B3H — PN02DB3G-OR1-M1: profile+run_id is the immutable claim identity; new attempt => new run_id.
# --------------------------------------------------------------------------- #


def test_same_run_different_envelope_replay_refused(tmp_path):
    p = _ledger(tmp_path)
    assert L.claim_grant(_identity(kind="B2", run="R", ckpt="t1", commit="a" * 40,
                                   fixture="f1", provider="p1"), ledger_path=p) is L.ClaimResult.CLAIMED
    # Same profile+run_id, DIFFERENT audit envelope -> still ALREADY_CONSUMED (envelope cannot reset).
    assert L.claim_grant(_identity(kind="B2", run="R", ckpt="t2", commit="b" * 40,
                                   fixture="f2", provider="p2"), ledger_path=p) is L.ClaimResult.ALREADY_CONSUMED


def test_new_run_is_new_grant(tmp_path):
    p = _ledger(tmp_path)
    assert L.claim_grant(_identity(kind="B2", run="R1"), ledger_path=p) is L.ClaimResult.CLAIMED
    assert L.claim_grant(_identity(kind="B2", run="R2"), ledger_path=p) is L.ClaimResult.CLAIMED


def test_cross_profile_same_run_id_distinct(tmp_path):
    # Same textual run_id under distinct auth profiles is a distinct claim identity (kind is bound).
    p = _ledger(tmp_path)
    assert L.claim_grant(_identity(kind="B1", run="R"), ledger_path=p) is L.ClaimResult.CLAIMED
    assert L.claim_grant(_identity(kind="B2", run="R"), ledger_path=p) is L.ClaimResult.CLAIMED
    assert L.claim_grant(_identity(kind="B3B", run="R"), ledger_path=p) is L.ClaimResult.CLAIMED


# --------------------------------------------------------------------------- #
# B3H — PN02DB3G-OR1-H2: strict fresh-vs-existing schema; partial/invalid existing fails closed.
# --------------------------------------------------------------------------- #


def _make_db(path, sql_statements):
    conn = sqlite3.connect(path)
    for stmt in sql_statements:
        conn.execute(stmt)
    conn.commit()
    conn.close()


def test_schema_matrix_A_nonexistent_creates_ok(tmp_path):
    p = _ledger(tmp_path)  # does not exist yet
    assert L.claim_grant(_identity(), ledger_path=p) is L.ClaimResult.CLAIMED


def test_schema_matrix_B_existing_valid_opens(tmp_path):
    p = _ledger(tmp_path)
    L.claim_grant(_identity(run="warm"), ledger_path=p)  # creates a valid v2 ledger
    assert L.claim_grant(_identity(run="second"), ledger_path=p) is L.ClaimResult.CLAIMED


def test_schema_matrix_C_zero_byte_fails_closed(tmp_path):
    p = _ledger(tmp_path)
    Path(p).touch()  # pre-existing zero-byte file
    with pytest.raises(L.LedgerSchemaError):
        L.claim_grant(_identity(), ledger_path=p)


def test_schema_matrix_D_only_meta_fails_closed(tmp_path):
    p = _ledger(tmp_path)
    _make_db(p, ["CREATE TABLE meta (schema_version INTEGER NOT NULL)",
                 f"INSERT INTO meta(schema_version) VALUES({L.LEDGER_SCHEMA_VERSION})"])
    with pytest.raises(L.LedgerSchemaError):
        L.claim_grant(_identity(), ledger_path=p)


def test_schema_matrix_E_only_consumed_grants_fails_closed(tmp_path):
    p = _ledger(tmp_path)
    _make_db(p, ["CREATE TABLE consumed_grants (grant_digest TEXT PRIMARY KEY)"])
    with pytest.raises(L.LedgerSchemaError):
        L.claim_grant(_identity(), ledger_path=p)


def test_schema_matrix_F_missing_column_fails_closed(tmp_path):
    p = _ledger(tmp_path)
    _make_db(p, [
        "CREATE TABLE meta (schema_version INTEGER NOT NULL)",
        f"INSERT INTO meta(schema_version) VALUES({L.LEDGER_SCHEMA_VERSION})",
        # consumed_grants missing most required columns
        "CREATE TABLE consumed_grants (grant_digest TEXT PRIMARY KEY, run_id TEXT)",
    ])
    with pytest.raises(L.LedgerSchemaError):
        L.claim_grant(_identity(), ledger_path=p)


def test_schema_matrix_G_future_version_fails_closed(tmp_path):
    p = _ledger(tmp_path)
    L.claim_grant(_identity(run="warm"), ledger_path=p)
    conn = sqlite3.connect(p)
    conn.execute("UPDATE meta SET schema_version = 999")
    conn.commit()
    conn.close()
    with pytest.raises(L.LedgerSchemaError):
        L.claim_grant(_identity(run="new"), ledger_path=p)


def test_schema_matrix_H_malformed_fails_closed(tmp_path):
    p = _ledger(tmp_path)
    with open(p, "wb") as fh:
        fh.write(b"not a sqlite database" * 8)
    with pytest.raises((L.LedgerCorruptError, L.LedgerSchemaError)):
        L.claim_grant(_identity(), ledger_path=p)


def test_partial_schema_not_mutated_on_failure(tmp_path):
    # An existing DB with only meta must NOT have consumed_grants silently created by the failure.
    p = _ledger(tmp_path)
    _make_db(p, ["CREATE TABLE meta (schema_version INTEGER NOT NULL)",
                 f"INSERT INTO meta(schema_version) VALUES({L.LEDGER_SCHEMA_VERSION})"])
    with pytest.raises(L.LedgerSchemaError):
        L.claim_grant(_identity(), ledger_path=p)
    conn = sqlite3.connect(p)
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    conn.close()
    assert "consumed_grants" not in tables  # runtime did not recreate/repair
