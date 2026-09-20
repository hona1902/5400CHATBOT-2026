"""PN02D-B2 — FOCUSED mint-identity security pass.

EVALUATION-ONLY, PROVIDER-FREE. Proves the B2 live authorization gates on a SEPARATE
governance-owned B2 checkpoint identity (``EXPECTED_B2_CHECKPOINT_TAG``) that fails closed
today, and that EW8 (which peels to today's HEAD) can NEVER authorize a B2 run — while the
B1 mint stays byte-identical (EW8 gate, FINAL_ANSWER=0, B1 allowlist). No network, no secret,
no real provider auth: every mint here is a capability object over the SAME production mint
signature (governance ``PN02_PROVIDER_RUN_AUTHORIZED`` stays NO).

The checkpoint gate is exercised through the real authorization path
(``b2_r2_refusal_reasons`` / ``verify_b1_r2_checkpoint`` / ``mint_live_b2_provider_run_authorization``),
never via string constants alone. State A (B2 tag absent now) and a patched State B (future
B2 tag present at HEAD) are both covered — no permanent-absence assumption.
"""

from __future__ import annotations

import inspect
from typing import cast
from unittest import mock

import graphrag_pn02db0cb_common as C
import pytest

from open_notebook.integrations.graphrag.eval import authmintlivepn02d as authmint
from open_notebook.integrations.graphrag.eval.authmintlivepn02d import (
    B1_ALLOWED_OPERATION_VALUES,
    B2_ALLOWED_OPERATION_VALUES,
    EXPECTED_B2_CHECKPOINT_TAG,
    EXPECTED_EW7_CHECKPOINT_TAG,
    EXPECTED_EW8_CHECKPOINT_TAG,
    HISTORICAL_B2_CHECKPOINT_TAG,
    HISTORICAL_B2_LIFECYCLE_CHECKPOINT_TAG,
    LiveProviderRunAuthorization,
    LiveProviderRunAuthorizationError,
    RealTrustedB1R2Reader,
    b2_r2_refusal_reasons,
    current_approved_b1_r2_checkpoint,
    current_approved_b2_checkpoint,
    frozen_b1_operator_grant_template,
    frozen_b2_operator_grant_template,
    mint_live_b2_provider_run_authorization,
    mint_live_provider_run_authorization,
    verify_b1_r2_checkpoint,
)
from open_notebook.integrations.graphrag.eval.budgetlivepn02d import (
    b1_caps_dict,
    b2_caps_dict,
)

# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #


def _b2_grant(
    *, b1_r2: str = EXPECTED_B2_CHECKPOINT_TAG, commit: str = C.TEST_COMMIT
):
    """A frozen B2 OperatorRunGrant (B2 caps + B2 allowlist), b1_r2 = the B2 identity."""
    return frozen_b2_operator_grant_template(
        run_id=C.TEST_RUN_ID,
        implementation_checkpoint_commit=commit,
        implementation_checkpoint_tag=C.TEST_TAG,
        b1_r2_checkpoint=b1_r2,
        approved_git_commit=commit,
        approved_git_tag=C.TEST_TAG,
    )


def _b2_state_b(*, tag: str = EXPECTED_B2_CHECKPOINT_TAG, head: str = C.TEST_COMMIT):
    """Patch the B2 trust roots to simulate a FUTURE B2 approval (State B).

    Mirrors ``C.approved_b1r2_governance`` but for B2: patch ``current_approved_b2_checkpoint``
    (→ the B2 identity) and ``_build_trusted_b1_r2_reader`` (→ a REAL reader over a scripted
    git boundary observing that tag at ``head``). The production mint/CLI signatures are
    unchanged; this patches module internals only.
    """
    reader = C.b1r2_reader_ok(tag=tag, peel=head, head=head)
    return _patch_b2(tag, reader)


def _patch_b2(tag, reader):
    return _MultiPatch(
        mock.patch.object(authmint, "current_approved_b2_checkpoint", return_value=tag),
        mock.patch.object(authmint, "_build_trusted_b1_r2_reader", return_value=reader),
    )


class _MultiPatch:
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


def _b2_full_mint(grant, *, head: str = C.TEST_COMMIT) -> LiveProviderRunAuthorization:
    """Full B2 mint under a patched State-B B2 governance (real preflight, clean baseline)."""
    ok, detail = C.verify_fixture_hash()
    fixture_hash = detail if ok else "UNVERIFIED"
    preflight = C.mint_real_preflight_authorization(
        gate0_passed=True, gate1_passed=True, fixture_hash=fixture_hash,
        run_id=C.TEST_RUN_ID, runtime_count=3,
    )
    with _b2_state_b(head=head):
        return mint_live_b2_provider_run_authorization(
            operator_grant=grant,
            real_preflight_auth=preflight,
            git_baseline_attestation=C.clean_git_baseline(commit=head, tag=C.TEST_TAG),
            observed_fixture_hash=fixture_hash,
        )


@pytest.fixture(autouse=True)
def _maybe_simulate_state_b():
    """Lifecycle harness (provider-free). When ``PN02DB2_SIMULATE_STATE_B=1`` is set, patch the B2
    trust roots MODULE-WIDE so the SUCCESSOR B2 tag is observed PRESENT at a fixed authorized HEAD
    (``C.TEST_COMMIT``) for the whole file. This lets the ENTIRE mint-identity suite be run
    UNFILTERED in a simulated State B (no deselection); with the env var unset the suite runs
    against real Git (State A today). The lifecycle-aware tests branch on the trusted observation,
    so BOTH runs are green — proving no permanent tag-absence assumption. It patches only the two
    module-level B2 trust roots (never the verifier/caps/allowlist/mint logic)."""
    import os

    if os.environ.get("PN02DB2_SIMULATE_STATE_B") == "1":
        with _b2_state_b(tag=EXPECTED_B2_CHECKPOINT_TAG, head=C.TEST_COMMIT):
            yield
    else:
        yield


# --------------------------------------------------------------------------- #
# identity + distinctness
# --------------------------------------------------------------------------- #


def test_expected_b2_checkpoint_tag_exact():
    # CHAT-MODEL-REMEDIATION B2 identity. Three distinct B2-era trust identities now exist: the
    # first-B2 tag and the lifecycle/predecessor tag are both immutable/historical; the current
    # expected identity supersedes BOTH (Real B2 EXEC #1 chat-model remediation; no retroactive
    # exception).
    assert EXPECTED_B2_CHECKPOINT_TAG == "graphrag-pn02db2-chat-model-remediation-approved"
    assert HISTORICAL_B2_CHECKPOINT_TAG == "graphrag-pn02db2-qa-live-wiring-approved"
    assert HISTORICAL_B2_LIFECYCLE_CHECKPOINT_TAG == "graphrag-pn02db2-qa-live-wiring-lifecycle-approved"
    # all three are distinct identities
    assert len({
        EXPECTED_B2_CHECKPOINT_TAG,
        HISTORICAL_B2_CHECKPOINT_TAG,
        HISTORICAL_B2_LIFECYCLE_CHECKPOINT_TAG,
    }) == 3


def test_current_approved_b2_resolves_b2_identity():
    assert current_approved_b2_checkpoint() == EXPECTED_B2_CHECKPOINT_TAG


def test_b1_resolver_still_ew8_and_distinct_from_b2():
    assert current_approved_b1_r2_checkpoint() == EXPECTED_EW8_CHECKPOINT_TAG
    assert current_approved_b1_r2_checkpoint() != current_approved_b2_checkpoint()


# --------------------------------------------------------------------------- #
# State A — B2 fails closed today; EW8/older/arbitrary cannot substitute
# --------------------------------------------------------------------------- #


def test_b2_fails_closed_today_real_reader():
    # LIFECYCLE-AWARE (successor), THREE real Git states — branches on the trusted observation of the
    # B2 tag via the module seam (``authmint._build_trusted_b1_r2_reader`` + ``current_approved_b2_
    # checkpoint``), the same seam the lifecycle harness patches, so this test is valid in EVERY state
    # with NO permanent tag-absence / tag-at-HEAD assumption:
    #   STATE A — expected tag ABSENT: the B2 gate fails closed with ``b1_r2_tag_not_observed_in_git``.
    #   STATE B — expected tag present AT EXACT HEAD: a HEAD-bound B2 grant is ACCEPTED.
    #   STATE C — expected tag present but peeling to a NON-HEAD commit (e.g. an ANCESTOR after a later
    #             PN02D-B3 checkpoint moved HEAD past the B2 tag): the gate MUST FAIL CLOSED with
    #             ``b1_r2_tag_not_at_authorized_head`` — a historical/ancestor checkpoint never
    #             authorizes the current HEAD (no grandfathering). This is exact-HEAD trust, unchanged.
    approved = cast(str, authmint.current_approved_b2_checkpoint())  # frozen non-None B2 identity
    obs = authmint._build_trusted_b1_r2_reader().observe(approved)
    if not obs.observed_tag_exists:
        # STATE A
        reasons = b2_r2_refusal_reasons(_b2_grant(), C.clean_git_baseline())
        assert "b1_r2_tag_not_observed_in_git" in reasons
        return
    assert obs.checkpoint_tag == approved
    assert len(obs.observed_tag_peel) == 40
    if obs.observed_tag_peel == obs.observed_head:
        # STATE B — tag at exact HEAD: HEAD-bound grant accepted (subject to the normal grant rules).
        reasons = b2_r2_refusal_reasons(
            _b2_grant(commit=obs.observed_head),
            C.clean_git_baseline(commit=obs.observed_head, tag=approved),
        )
        assert "b1_r2_tag_not_observed_in_git" not in reasons
        assert reasons == []
    else:
        # STATE C — tag exists but peels to a non-HEAD (ancestor) commit: FAIL CLOSED. Even binding the
        # grant/baseline to the ACTUAL current HEAD cannot rescue it, because the trusted reader sees
        # the tag peel (an ancestor) != HEAD, so exact-HEAD verification refuses it.
        reasons = b2_r2_refusal_reasons(
            _b2_grant(commit=obs.observed_head),
            C.clean_git_baseline(commit=obs.observed_head, tag=approved),
        )
        assert "b1_r2_tag_not_at_authorized_head" in reasons


def test_ew8_cannot_substitute_for_b2():
    # LIFECYCLE-AWARE. A grant citing the EW8 identity, verified against the B2 (successor) approved
    # identity, is refused in BOTH states: the grant's checkpoint must EXACTLY equal the B2 identity,
    # so ``b1_r2_grant_identity_mismatch`` is always present (EW8 peeling to a HEAD is irrelevant —
    # the B2 gate verifies the B2 successor tag, not EW8). STATE A additionally reports
    # ``b1_r2_tag_not_observed_in_git``.
    approved = cast(str, authmint.current_approved_b2_checkpoint())  # frozen non-None B2 identity
    reader = authmint._build_trusted_b1_r2_reader()
    obs = reader.observe(approved)
    commit = obs.observed_head or C.TEST_COMMIT
    reasons = verify_b1_r2_checkpoint(
        reader=reader,
        operator_grant=_b2_grant(b1_r2=EXPECTED_EW8_CHECKPOINT_TAG, commit=commit),
        approved_expected_checkpoint=approved,
        git_baseline=C.clean_git_baseline(commit=commit, tag=approved),
    )
    assert "b1_r2_grant_identity_mismatch" in reasons  # fails closed in BOTH states
    if not obs.observed_tag_exists:
        assert "b1_r2_tag_not_observed_in_git" in reasons  # STATE A only


@pytest.mark.parametrize(
    "bad_tag",
    [EXPECTED_EW7_CHECKPOINT_TAG, "graphrag-pn02db1ew6-index-conflict-recovery-approved",
     "totally-arbitrary-tag", "graphrag-pn02db2-chat-model-remediation-approved-EVIL",
     HISTORICAL_B2_CHECKPOINT_TAG, HISTORICAL_B2_LIFECYCLE_CHECKPOINT_TAG],
)
def test_older_or_arbitrary_tag_cannot_substitute_for_b2(bad_tag):
    reasons = verify_b1_r2_checkpoint(
        reader=RealTrustedB1R2Reader(),
        operator_grant=_b2_grant(b1_r2=bad_tag),
        approved_expected_checkpoint=current_approved_b2_checkpoint(),
        git_baseline=C.clean_git_baseline(),
    )
    assert "b1_r2_grant_identity_mismatch" in reasons


def test_wrong_b2_tag_peel_fails_closed():
    # Even simulating the exact B2 tag present, if it peels to a DIFFERENT commit than HEAD
    # the gate fails closed (no name-only trust).
    reader = C.b1r2_reader_ok(
        tag=EXPECTED_B2_CHECKPOINT_TAG, peel="dead" + "0" * 36, head=C.TEST_COMMIT
    )
    with _patch_b2(EXPECTED_B2_CHECKPOINT_TAG, reader):
        reasons = b2_r2_refusal_reasons(
            _b2_grant(), C.clean_git_baseline(commit=C.TEST_COMMIT)
        )
    assert "b1_r2_tag_not_at_authorized_head" in reasons


def test_b2_ancestor_tag_fails_closed_on_successor_head():
    # STATE C (deterministic, simulated): the exact B2 tag EXISTS and peels to an ANCESTOR commit,
    # while HEAD has advanced to a successor commit (the real shape after the PN02D-B3 checkpoint
    # moved HEAD past the B2 tag). A historically-approved ancestor checkpoint MUST NOT authorize the
    # current HEAD — exact-HEAD trust refuses it with ``b1_r2_tag_not_at_authorized_head`` (no
    # ancestor/merge-base grandfathering). Independent of the actual current Git HEAD.
    ancestor = "a" * 40  # the tag peel (an earlier approved commit)
    successor_head = "b" * 40  # the current HEAD, a descendant of the ancestor
    assert ancestor != successor_head
    reader = C.b1r2_reader_ok(
        tag=EXPECTED_B2_CHECKPOINT_TAG, peel=ancestor, head=successor_head
    )
    with _patch_b2(EXPECTED_B2_CHECKPOINT_TAG, reader):
        # bind the grant/baseline to the ACTUAL successor HEAD — still refused, because the tag peel
        # (the ancestor) does not equal HEAD.
        reasons = b2_r2_refusal_reasons(
            _b2_grant(commit=successor_head),
            C.clean_git_baseline(commit=successor_head, tag=EXPECTED_B2_CHECKPOINT_TAG),
        )
    assert "b1_r2_tag_not_at_authorized_head" in reasons
    assert reasons != []  # fail closed — not accepted


def test_historical_first_b2_tag_cannot_substitute_for_successor():
    # The HISTORICAL first-B2 tag (immutable at commit 41eb3f3) can NEVER authorize the successor
    # B2 run — the grant's checkpoint must EXACTLY equal the successor identity. Proven two ways,
    # in BOTH lifecycle states: (1) a grant citing the historical tag → identity mismatch;
    # (2) even simulating the historical tag PRESENT at HEAD as the approved-expected identity, it
    # is not the successor identity the resolver returns, so it cannot mint. Here we assert the
    # direct identity-mismatch path, which is state-independent.
    reasons = verify_b1_r2_checkpoint(
        reader=RealTrustedB1R2Reader(),
        operator_grant=_b2_grant(b1_r2=HISTORICAL_B2_CHECKPOINT_TAG),
        approved_expected_checkpoint=current_approved_b2_checkpoint(),
        git_baseline=C.clean_git_baseline(),
    )
    assert "b1_r2_grant_identity_mismatch" in reasons
    # The resolver's approved identity is the SUCCESSOR, never the historical first-B2 tag.
    assert authmint.current_approved_b2_checkpoint() != HISTORICAL_B2_CHECKPOINT_TAG


def test_predecessor_lifecycle_tag_cannot_substitute_for_successor():
    # The PREDECESSOR lifecycle tag (``...qa-live-wiring-lifecycle-approved``, immutable at commit
    # 40d3a6f) was the approved identity for the prior turn AND still peels correctly to the current
    # HEAD (40d3a6f) — yet after the successor resolver identity is introduced it is HISTORICAL and can
    # NEVER authorize the chat-model-remediation successor run. The grant's checkpoint must EXACTLY
    # equal the successor identity, so a grant citing the predecessor → ``b1_r2_grant_identity_mismatch``
    # in BOTH lifecycle states (peeling-to-HEAD is irrelevant: the gate verifies the successor tag).
    # There is NO ancestor authorization: a checkpoint valid for a prior HEAD does not authorize a later
    # HEAD even when the commit is identical.
    reasons = verify_b1_r2_checkpoint(
        reader=RealTrustedB1R2Reader(),
        operator_grant=_b2_grant(b1_r2=HISTORICAL_B2_LIFECYCLE_CHECKPOINT_TAG),
        approved_expected_checkpoint=current_approved_b2_checkpoint(),
        git_baseline=C.clean_git_baseline(),
    )
    assert "b1_r2_grant_identity_mismatch" in reasons
    # The resolver's approved identity is the SUCCESSOR, never the predecessor lifecycle tag.
    assert authmint.current_approved_b2_checkpoint() != HISTORICAL_B2_LIFECYCLE_CHECKPOINT_TAG
    # ...and the predecessor remains a DISTINCT immutable historical identity from the first-B2 tag.
    assert HISTORICAL_B2_LIFECYCLE_CHECKPOINT_TAG != HISTORICAL_B2_CHECKPOINT_TAG


# --------------------------------------------------------------------------- #
# State B — future B2 tag present at HEAD satisfies the checkpoint gate
# --------------------------------------------------------------------------- #


def test_b2_state_b_future_tag_satisfies_checkpoint():
    with _b2_state_b(head=C.TEST_COMMIT):
        reasons = b2_r2_refusal_reasons(
            _b2_grant(commit=C.TEST_COMMIT),
            C.clean_git_baseline(commit=C.TEST_COMMIT),
        )
    assert reasons == []  # lifecycle-aware: not a permanent-absence assumption


def test_b2_full_mint_succeeds_at_state_b():
    auth = _b2_full_mint(_b2_grant())
    assert isinstance(auth, LiveProviderRunAuthorization)


# --------------------------------------------------------------------------- #
# tag-alone / operator-grant separation
# --------------------------------------------------------------------------- #


def test_valid_b2_tag_alone_cannot_mint_without_preflight():
    # Even with a valid State-B B2 checkpoint, a missing real-preflight capability fails closed
    # BEFORE any authorization — the tag alone cannot mint.
    with _b2_state_b(), pytest.raises(Exception):
        mint_live_b2_provider_run_authorization(
            operator_grant=_b2_grant(),
            real_preflight_auth=None,
            git_baseline_attestation=C.clean_git_baseline(),
            observed_fixture_hash=authmint.EXPECTED_FIXTURE_HASH,
        )


def test_b1_execution_grant_cannot_authorize_b2():
    # A B1-shaped grant (B1 caps FINAL_ANSWER=0, B1 allowlist) that even names the B2
    # checkpoint identity still cannot mint a B2 run: the B2 caps gate rejects it.
    b1_grant_named_b2 = frozen_b1_operator_grant_template(
        run_id=C.TEST_RUN_ID,
        implementation_checkpoint_commit=C.TEST_COMMIT,
        implementation_checkpoint_tag=C.TEST_TAG,
        b1_r2_checkpoint=EXPECTED_B2_CHECKPOINT_TAG,
        approved_git_commit=C.TEST_COMMIT,
        approved_git_tag=C.TEST_TAG,
    )
    with pytest.raises(LiveProviderRunAuthorizationError):
        _b2_full_mint(b1_grant_named_b2)


# --------------------------------------------------------------------------- #
# caps + allowlist separation
# --------------------------------------------------------------------------- #


def test_b1_cap_zero_b2_cap_72():
    assert b1_caps_dict()["FINAL_ANSWER"] == 0
    assert b2_caps_dict()["FINAL_ANSWER"] == 72


def test_b1_allowlist_unchanged_and_forbids_qa_ops():
    # B1 must still forbid the B2-only QA final-answer operations.
    for op in ("QA_V", "QA_GD", "QA_V_GD"):
        assert op not in B1_ALLOWED_OPERATION_VALUES


def test_b2_allowlist_is_minimal():
    added = B2_ALLOWED_OPERATION_VALUES - B1_ALLOWED_OPERATION_VALUES
    assert added == {"QA_V", "QA_GD", "QA_V_GD"}
    assert B1_ALLOWED_OPERATION_VALUES <= B2_ALLOWED_OPERATION_VALUES


@pytest.mark.parametrize("forbidden", ["LIGHTRAG_FINAL_ANSWER", "CLIENT_QUERY", "JUDGE_MODEL"])
def test_forbidden_ops_stay_forbidden_in_b2(forbidden):
    assert forbidden not in B2_ALLOWED_OPERATION_VALUES


# --------------------------------------------------------------------------- #
# no public trust-root / profile override
# --------------------------------------------------------------------------- #


def test_b2_mint_has_no_trust_root_or_profile_parameter():
    params = inspect.signature(mint_live_b2_provider_run_authorization).parameters
    assert "profile" not in params
    assert "trusted_b1_r2_reader" not in params
    assert "approved_expected_b1_r2_checkpoint" not in params
    with pytest.raises(TypeError):
        mint_live_b2_provider_run_authorization(  # type: ignore[call-arg]
            operator_grant=_b2_grant(), real_preflight_auth=None,
            git_baseline_attestation=C.clean_git_baseline(),
            observed_fixture_hash="x", profile=authmint._B2_AUTH_PROFILE,
        )


def test_b1_mint_signature_unchanged_no_profile():
    params = inspect.signature(mint_live_provider_run_authorization).parameters
    assert set(params) == {
        "operator_grant", "real_preflight_auth", "git_baseline_attestation",
        "observed_fixture_hash", "expected_fixture_hash",
    }


def test_b1_mint_still_uses_b1_profile_and_ew8_identity():
    # B1 regression: the public B1 mint delegates to the B1 profile → EW8 gate, still fails
    # closed today (EW8 tag governance-approved but the gate is unchanged).
    assert authmint._B1_AUTH_PROFILE.refusal_fn is authmint.b1_r2_refusal_reasons
    assert authmint._B1_AUTH_PROFILE.allowlist == B1_ALLOWED_OPERATION_VALUES
    assert authmint._B1_AUTH_PROFILE.expected_caps_dict is b1_caps_dict
    assert current_approved_b1_r2_checkpoint() == EXPECTED_EW8_CHECKPOINT_TAG
