"""GraphRAG-PN02D-B1-EW3 — isolation-runtime-id compatibility for real B1 execution.

The third authorized PN02D-B1 real-provider execution was correctly BLOCKED before any
provider traffic (0 traffic, no mint, no boot): the FROZEN scientific B1 run id
``pn02db1-fe3efb27-e720-48c6-b97e-2c0e6a60969d`` (hyphens, 44 chars) was passed verbatim to
``isolation08.temp_names`` which requires ``^[A-Za-z0-9]{4,32}$``, so isolation entry raised
``IsolationConfigurationError`` before the seed / attestor / mint / provider binding.

EW3 fix (offline, provider-free): the SCIENTIFIC run id stays frozen everywhere it matters
(operator grant, live authorization, results). Only the ISOLATION boundary gets a distinct,
deterministic, domain-separated SHA-256 derived id (``realseamspn02d._derive_isolation_runtime_id``)
that satisfies the canonical ``temp_names`` contract without weakening it.

Unit tests (no DB / no provider): the derivation (exact frozen id, determinism, collision
resistance, domain separation, no-truncation, temp_names acceptance), the production
composition boundary (isolation receives the derived id; seams builder + operator grant keep
the scientific run id), the original isolation-entry blocker removed, EW3 governance (EW3 is
the current successor; EW1/PF1/B1-R2/EW2 cannot substitute), and the EW3 checkpoint-lifecycle
tests (State A tag absent → fail-closed; State B exact tag at HEAD → Git gate satisfiable but
still no provider authorization) — lifecycle-aware from day one, following the r2/ew1/ew2
precedent. ZERO provider traffic throughout.
"""

from __future__ import annotations

import asyncio
import socket
from contextlib import asynccontextmanager
from unittest import mock

import graphrag_pn02db0cb_common as C
import pytest

from open_notebook.integrations.graphrag.eval import realseamspn02d as R
from open_notebook.integrations.graphrag.eval.realseamspn02d import (
    _derive_isolation_runtime_id,
)

_LOOPBACK = {"127.0.0.1", "::1", "localhost", "0.0.0.0"}

#: The FROZEN scientific B1 run id (never changed by EW3) and its expected derived isolation id.
_B1_RUN_ID = "pn02db1-fe3efb27-e720-48c6-b97e-2c0e6a60969d"
_EXPECTED_ISOLATION_ID = "pn02d369c1c2b73339d9362faf1a9fa7"


@pytest.fixture(autouse=True)
def _no_external_network(monkeypatch):
    """Fail immediately on any outbound connect to a non-loopback host (no provider traffic)."""
    real_connect = socket.socket.connect

    def guarded(self, address):
        host = address[0] if isinstance(address, (tuple, list)) else address
        if host not in _LOOPBACK:
            raise AssertionError(
                f"EW3 offline sentinel: blocked external network connect to {host!r}"
            )
        return real_connect(self, address)

    monkeypatch.setattr(socket.socket, "connect", guarded)


def _ew3_future_grant(**overrides):
    """A prepared B1-R2 operator grant whose identity is the CURRENT approved (EW3) checkpoint
    and whose run_id is the FROZEN scientific B1 run id."""
    from open_notebook.integrations.graphrag.eval import authb1r2pn02d as B

    kwargs = dict(approved_git_commit="e3e3e3e3" + "0" * 32)
    kwargs.update(overrides)
    return B.build_b1_r2_operator_grant(**kwargs)


def _ew3_fixture_hash():
    ok, detail = C.verify_fixture_hash()
    return detail if ok else "UNVERIFIED"


# --------------------------------------------------------------------------- #
# Isolation-runtime-id derivation (§6–§9, §16–§19)
# --------------------------------------------------------------------------- #

def test_isolation_id_exact_for_frozen_b1_run_id():
    # §6/§19: the derived id for the frozen B1 run id is exactly the audited value and is a
    # 32-char id ("pn02d" + 27 hex) — verified against the explicit domain-separated formula.
    import hashlib

    derived = _derive_isolation_runtime_id(_B1_RUN_ID)
    assert derived == _EXPECTED_ISOLATION_ID
    assert len(derived) == 32
    digest = hashlib.sha256(f"pn02d-isolation-v1:{_B1_RUN_ID}".encode("utf-8")).hexdigest()
    assert derived == f"pn02d{digest[:27]}"  # domain-separated SHA-256, not a raw transform


def test_isolation_id_accepted_by_canonical_temp_names():
    # §18: the derived id satisfies the ACTUAL isolation naming contract (invoke the canonical
    # validator, not a duplicated regex): temp_names returns the temp (namespace, database).
    from open_notebook.integrations.graphrag.eval.isolation08 import temp_names

    ns, db = temp_names(_derive_isolation_runtime_id(_B1_RUN_ID))
    assert ns.endswith(_EXPECTED_ISOLATION_ID)
    assert db.endswith(_EXPECTED_ISOLATION_ID)


def test_isolation_id_deterministic():
    # §17: same scientific run id → identical isolation id across repeated derivations.
    a = _derive_isolation_runtime_id(_B1_RUN_ID)
    b = _derive_isolation_runtime_id(_B1_RUN_ID)
    assert a == b == _EXPECTED_ISOLATION_ID


def test_isolation_id_collision_resistant_for_similar_run_ids():
    # §16: three run ids differing only in the final char map to THREE distinct isolation ids.
    ids = {
        _derive_isolation_runtime_id("pn02db1-fe3efb27-e720-48c6-b97e-2c0e6a60969d"),
        _derive_isolation_runtime_id("pn02db1-fe3efb27-e720-48c6-b97e-2c0e6a60969e"),
        _derive_isolation_runtime_id("pn02db1-fe3efb27-e720-48c6-b97e-2c0e6a60969f"),
    }
    assert len(ids) == 3
    # Every derived id is Surreal-safe.
    import re

    for i in ids:
        assert re.match(r"^[A-Za-z0-9]{4,32}$", i)


def test_isolation_id_is_not_truncation_only():
    # §7: the mapping is NOT a strip+truncate transform (which could collide on a shared
    # prefix). Two run ids that share the same first 32 chars after removing hyphens still map
    # to different isolation ids, and the derived id differs from the naive truncation.
    run_a = "pn02db1-fe3efb27-e720-48c6-b97e-2c0e6a60969d"
    run_b = "pn02db1-fe3efb27-e720-48c6-b97e-2c0e6a60969dEXTRA"  # same 32-char strip prefix
    naive_a = run_a.replace("-", "")[:32]
    assert _derive_isolation_runtime_id(run_a) != naive_a
    assert _derive_isolation_runtime_id(run_a) != _derive_isolation_runtime_id(run_b)


def test_isolation_id_domain_separated_and_no_secret_or_randomness():
    # §8/§9: the derivation is domain-separated (versioned) and takes ONLY the non-secret run
    # id — no secret material, no randomness (proven by determinism + a signature with one arg).
    import inspect

    assert R._ISOLATION_ID_DOMAIN == "pn02d-isolation-v1"
    params = list(inspect.signature(_derive_isolation_runtime_id).parameters)
    assert params == ["run_id"]  # only the run id; no secret/random/timestamp inputs
    # A different domain would yield a different digest → domain separation is load-bearing.
    import hashlib

    other = hashlib.sha256(f"other-domain:{_B1_RUN_ID}".encode("utf-8")).hexdigest()
    assert _derive_isolation_runtime_id(_B1_RUN_ID) != f"pn02d{other[:27]}"


# --------------------------------------------------------------------------- #
# Production composition boundary (§11/§12/§20/§21) + original blocker (§23)
# --------------------------------------------------------------------------- #

def test_isolation_boundary_receives_derived_id_scientific_id_preserved():
    # §11/§12/§14/§20: the ISOLATION factory receives the DERIVED id; the seams builder and the
    # operator grant handed to the driver keep the FROZEN scientific run id. Proven through the
    # real production composition helper (fake external edges only). No double derivation.
    from open_notebook.integrations.graphrag.eval.datasetpn02 import load_fixture

    seen = {}

    @asynccontextmanager
    async def _spy_isolation(run_id):
        seen.setdefault("isolation_run_ids", []).append(run_id)
        yield None

    @asynccontextmanager
    async def _noop_seed():
        yield "model:seed"

    def _spy_seams(fx, *, run_id, env=None, **kwargs):
        seen["seams_run_id"] = run_id
        return mock.sentinel.seams

    class _FakeDriver:
        def __init__(self, fx, seams):
            pass

        async def run(self, *, operator_grant, git_baseline_attestation, observed_fixture_hash):
            seen["driver_run_id"] = operator_grant.run_id
            return mock.sentinel.outcome

    grant = mock.Mock()
    grant.run_id = _B1_RUN_ID

    with mock.patch.object(R, "RealB1Driver", _FakeDriver):
        outcome = asyncio.run(
            R._run_live_b1_execution_composed(
                operator_grant=grant,
                git_baseline_attestation=mock.Mock(),
                observed_fixture_hash="hash",
                fx=load_fixture(),
                seams_builder=_spy_seams,
                isolation=_spy_isolation,
                model_seed=_noop_seed,
                builder_kwargs={},
            )
        )

    assert outcome is mock.sentinel.outcome
    # §21: derived exactly ONCE at the boundary; it is the expected derived id, not the run id.
    assert seen["isolation_run_ids"] == [_EXPECTED_ISOLATION_ID]
    assert seen["isolation_run_ids"][0] != _B1_RUN_ID
    # scientific id preserved downstream (seams + operator grant → driver).
    assert seen["seams_run_id"] == _B1_RUN_ID
    assert seen["driver_run_id"] == _B1_RUN_ID


def test_original_isolation_entry_blocker_removed_provider_free():
    # §23: the frozen scientific run id would be REJECTED by temp_names (the exact exec#3
    # blocker), but the DERIVED id is ACCEPTED — proving the isolation-entry blocker is removed
    # without any provider traffic and without weakening temp_names.
    from open_notebook.integrations.graphrag.eval.isolation08 import (
        IsolationConfigurationError,
        temp_names,
    )

    with pytest.raises(IsolationConfigurationError):
        temp_names(_B1_RUN_ID)  # the exec#3 failure reproduced (frozen id rejected)
    ns, db = temp_names(_derive_isolation_runtime_id(_B1_RUN_ID))  # derived id accepted
    assert ns and db


# --------------------------------------------------------------------------- #
# EW3 governance: EW3 is the current successor; historical tags cannot substitute (§26/§27)
# --------------------------------------------------------------------------- #

def test_governance_current_is_ew3_successor():
    from open_notebook.integrations.graphrag.eval import authb1r2pn02d as B
    from open_notebook.integrations.graphrag.eval.authmintlivepn02d import (
        EXPECTED_B1_R2_CHECKPOINT_TAG,
        EXPECTED_EW1_CHECKPOINT_TAG,
        EXPECTED_EW2_CHECKPOINT_TAG,
        EXPECTED_EW3_CHECKPOINT_TAG,
        EXPECTED_PF1_CHECKPOINT_TAG,
        current_approved_b1_r2_checkpoint,
    )

    assert current_approved_b1_r2_checkpoint() == EXPECTED_EW3_CHECKPOINT_TAG
    assert B.B1_R2_EXPECTED_CHECKPOINT_TAG == EXPECTED_EW3_CHECKPOINT_TAG
    assert EXPECTED_EW3_CHECKPOINT_TAG == "graphrag-pn02db1ew3-isolation-id-compat-approved"
    # EW2/EW1/PF1/B1-R2 are retained HISTORICAL identities, all distinct from EW3.
    assert len({
        EXPECTED_EW3_CHECKPOINT_TAG,
        EXPECTED_EW2_CHECKPOINT_TAG,
        EXPECTED_EW1_CHECKPOINT_TAG,
        EXPECTED_PF1_CHECKPOINT_TAG,
        EXPECTED_B1_R2_CHECKPOINT_TAG,
    }) == 5


def test_ew1_pf1_b1r2_ew2_cannot_substitute_for_ew3():
    # §27: no historical identity (EW1/PF1/B1-R2/EW2) or arbitrary tag can substitute for the
    # EW3 successor — naming any as the approved-expected identity is refused (non-empty reasons).
    from open_notebook.integrations.graphrag.eval.authmintlivepn02d import (
        EXPECTED_B1_R2_CHECKPOINT_TAG,
        EXPECTED_EW1_CHECKPOINT_TAG,
        EXPECTED_EW2_CHECKPOINT_TAG,
        EXPECTED_PF1_CHECKPOINT_TAG,
        RealTrustedB1R2Reader,
        verify_b1_r2_checkpoint,
    )

    future = "e3e3e3e3" + "0" * 32
    for substitute in (
        "graphrag-arbitrary-unrelated-tag",
        EXPECTED_EW2_CHECKPOINT_TAG,
        EXPECTED_EW1_CHECKPOINT_TAG,
        EXPECTED_PF1_CHECKPOINT_TAG,
        EXPECTED_B1_R2_CHECKPOINT_TAG,
    ):
        reasons = verify_b1_r2_checkpoint(
            reader=RealTrustedB1R2Reader(),
            operator_grant=_ew3_future_grant(approved_git_commit=future),
            approved_expected_checkpoint=substitute,
            git_baseline=C.clean_git_baseline(commit=future, tag="graphrag-pn02db1ew3-isolation-id-compat-approved"),
        )
        assert reasons, f"{substitute} must not satisfy the EW3 checkpoint"


# --------------------------------------------------------------------------- #
# EW3 checkpoint-lifecycle — lifecycle-aware from day one (§28/§29/§30)
# --------------------------------------------------------------------------- #

def test_ew3_successor_tag_git_state_is_lifecycle_valid():
    # §28: real-Git checkpoint identity is lifecycle-aware from day one — never a permanent
    # tag-absence assertion. STATE A (pre-checkpoint): EW3 tag absent → empty peel. STATE B
    # (post-checkpoint): EXACT tag present, valid 40-hex peel == authorized HEAD.
    from open_notebook.integrations.graphrag.eval.authmintlivepn02d import (
        EXPECTED_EW3_CHECKPOINT_TAG,
        RealTrustedB1R2Reader,
        current_approved_b1_r2_checkpoint,
    )

    approved = current_approved_b1_r2_checkpoint()
    assert approved == EXPECTED_EW3_CHECKPOINT_TAG
    obs = RealTrustedB1R2Reader().observe(approved)
    if not obs.observed_tag_exists:
        assert obs.observed_tag_peel == ""
    else:
        assert obs.checkpoint_tag == approved
        assert len(obs.observed_tag_peel) == 40 and all(
            c in "0123456789abcdef" for c in obs.observed_tag_peel
        )
        assert obs.observed_tag_peel == obs.observed_head


def test_ew3_synthetic_state_a_mint_fails_closed_when_tag_absent():
    # §28 State A (deterministic): a synthetic approved identity whose tag is absent from Git
    # fails closed with `b1_r2_tag_not_observed_in_git` (real reader retained; governance-only
    # patch). Not dependent on the developer's real Git currently lacking the EW3 tag.
    from open_notebook.integrations.graphrag.eval.attestpn02d import (
        mint_real_preflight_authorization,
    )
    from open_notebook.integrations.graphrag.eval.authmintlivepn02d import (
        B1R2CheckpointError,
        mint_live_provider_run_authorization,
    )

    grant = C.frozen_test_grant(b1_r2_checkpoint=C.TEST_B1R2_TAG)
    baseline = C.clean_git_baseline()
    preflight = mint_real_preflight_authorization(
        gate0_passed=True, gate1_passed=True, fixture_hash=_ew3_fixture_hash(),
        run_id=C.TEST_RUN_ID, runtime_count=3,
    )
    with C.governance_expects_tag(C.TEST_B1R2_TAG):
        with pytest.raises(B1R2CheckpointError) as ei:
            mint_live_provider_run_authorization(
                operator_grant=grant, real_preflight_auth=preflight,
                git_baseline_attestation=baseline, observed_fixture_hash=_ew3_fixture_hash(),
            )
    assert "b1_r2_tag_not_observed_in_git" in str(ei.value)


def test_ew3_synthetic_state_b_git_gate_satisfiable_but_no_provider_auth():
    # §29: a synthetic reader observing the EXACT EW3 tag peeling to the authorized HEAD makes
    # the Git checkpoint prerequisite satisfiable — but that is ONLY the control-plane Git gate;
    # the provider-run governance flag stays NO. NO real tag is created.
    from open_notebook.integrations.graphrag.eval.authlivepn02d import (
        PN02_PROVIDER_RUN_AUTHORIZED,
    )
    from open_notebook.integrations.graphrag.eval.authmintlivepn02d import (
        EXPECTED_EW3_CHECKPOINT_TAG,
        verify_b1_r2_checkpoint,
    )

    future = "e3e3e3e3" + "0" * 32
    reasons = verify_b1_r2_checkpoint(
        reader=C.b1r2_reader_ok(tag=EXPECTED_EW3_CHECKPOINT_TAG, peel=future, head=future),
        operator_grant=_ew3_future_grant(approved_git_commit=future),
        approved_expected_checkpoint=EXPECTED_EW3_CHECKPOINT_TAG,
        git_baseline=C.clean_git_baseline(commit=future, tag=EXPECTED_EW3_CHECKPOINT_TAG),
    )
    assert reasons == []  # Git gate satisfiable
    assert PN02_PROVIDER_RUN_AUTHORIZED is False  # but provider run NOT authorized


def test_ew3_wrong_tag_peel_fails_closed():
    # §30: the EXACT EW3 tag observed but peeling to a NON-HEAD commit → fail closed.
    from open_notebook.integrations.graphrag.eval.authmintlivepn02d import (
        EXPECTED_EW3_CHECKPOINT_TAG,
        verify_b1_r2_checkpoint,
    )

    future = "e3e3e3e3" + "0" * 32
    reasons = verify_b1_r2_checkpoint(
        reader=C.b1r2_reader_ok(tag=EXPECTED_EW3_CHECKPOINT_TAG, peel="d" * 40, head=future),
        operator_grant=_ew3_future_grant(approved_git_commit=future),
        approved_expected_checkpoint=EXPECTED_EW3_CHECKPOINT_TAG,
        git_baseline=C.clean_git_baseline(commit=future, tag=EXPECTED_EW3_CHECKPOINT_TAG),
    )
    assert "b1_r2_tag_not_at_authorized_head" in reasons


def test_ew3_dirty_tree_cannot_authorize():
    # §30/§13: even with the correct EW3 identity, a DIRTY working tree is refused by the
    # git-baseline gate before the B1-R2 checkpoint gate — no authorization.
    from open_notebook.integrations.graphrag.eval import authb1r2pn02d as B
    from open_notebook.integrations.graphrag.eval.attestpn02d import (
        mint_real_preflight_authorization,
    )
    from open_notebook.integrations.graphrag.eval.authmintlivepn02d import (
        EXPECTED_EW3_CHECKPOINT_TAG,
        GitBaselineError,
        mint_live_provider_run_authorization,
    )

    grant = _ew3_future_grant(approved_git_commit=C.TEST_COMMIT)
    dirty = C.dirty_git_baseline(commit=C.TEST_COMMIT, tag=EXPECTED_EW3_CHECKPOINT_TAG)
    preflight = mint_real_preflight_authorization(
        gate0_passed=True, gate1_passed=True, fixture_hash=_ew3_fixture_hash(),
        run_id=B.B1_RUN_ID, runtime_count=3,
    )
    with pytest.raises(GitBaselineError):
        mint_live_provider_run_authorization(
            operator_grant=grant, real_preflight_auth=preflight,
            git_baseline_attestation=dirty, observed_fixture_hash=_ew3_fixture_hash(),
        )
