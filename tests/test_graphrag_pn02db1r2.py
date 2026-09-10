"""PN02D-B1-R2 — provider-authorization re-preflight & checkpoint-preparation tests.

EVALUATION-ONLY, ZERO provider traffic. Proves the future B1 authorization envelope is
frozen and consistent, and that a live provider-run authorization is NOT currently
mintable (the expected B1-R2 annotated tag does not exist in real Git). The future-positive
path is exercised by patching the mint's INTERNAL governance + trusted-reader (module
boundary) to a SYNTHETIC future tag — never by injecting a trust root and never by creating
the real tag.
"""

from __future__ import annotations

import inspect
import json

import graphrag_pn02db0cb_common as C
import pytest

from open_notebook.integrations.graphrag.eval import authb1r2pn02d as B
from open_notebook.integrations.graphrag.eval.attestpn02d import (
    mint_real_preflight_authorization,
)
from open_notebook.integrations.graphrag.eval.authlivepn02d import (
    PN02_PROVIDER_RUN_AUTHORIZED,
)
from open_notebook.integrations.graphrag.eval.authmintlivepn02d import (
    EXPECTED_B1_R2_CHECKPOINT_TAG,
    EXPECTED_FIXTURE_HASH,
    EXPECTED_PF1_CHECKPOINT_TAG,
    B1R2CheckpointError,
    GitBaselineError,
    LiveProviderRunAuthorization,
    RealTrustedB1R2Reader,
    current_approved_b1_r2_checkpoint,
    mint_live_provider_run_authorization,
    verify_b1_r2_checkpoint,
)

# A SYNTHETIC future B1-R2 checkpoint commit (does NOT exist in real Git).
FUTURE_B1_R2_COMMIT = "b1b1b1b1" + "0" * 32
B0CA_TAG = "graphrag-pn02db0ca-real-provider-wiring-design-approved"
B0CB_TAG = "graphrag-pn02db0cb-real-provider-wiring-approved"
RETIRED_RUN_ID = "pn02db1-daf6b760-7d68-4674-9222-ac9f962ef6c4"


def _fixture_hash():
    ok, detail = C.verify_fixture_hash()
    return detail if ok else "UNVERIFIED"


def _future_grant(**overrides):
    kwargs = dict(approved_git_commit=FUTURE_B1_R2_COMMIT)
    kwargs.update(overrides)
    return B.build_b1_r2_operator_grant(**kwargs)


# --------------------------------------------------------------------------- #
# Frozen envelope: identity, run_id, fixture, fingerprint, caps, allowlist
# --------------------------------------------------------------------------- #

def test_expected_b1_r2_tag_frozen_in_governance():
    # PN02D-B1-PF1: governance now approves the SUCCESSOR PF1 checkpoint (the readiness fix
    # moves HEAD past the historical B1-R2 commit, superseding the B1-R2 Git gate). The
    # historical B1-R2 identity is a DISTINCT, retained constant.
    assert current_approved_b1_r2_checkpoint() == EXPECTED_PF1_CHECKPOINT_TAG
    assert B.B1_R2_EXPECTED_CHECKPOINT_TAG == EXPECTED_PF1_CHECKPOINT_TAG
    assert EXPECTED_PF1_CHECKPOINT_TAG != EXPECTED_B1_R2_CHECKPOINT_TAG


def test_current_approved_checkpoint_git_state_is_valid():
    # CHECKPOINT-LIFECYCLE aware for the CURRENT approved identity (PF1). State A = the
    # successor tag is absent (pre-checkpoint) → gate unsatisfied; State B = the exact PF1
    # tag exists at the authorized HEAD. Stays green before AND after the PF1 checkpoint,
    # and is UNAFFECTED by the historical B1-R2 tag (see the immutability test below).
    approved = current_approved_b1_r2_checkpoint()
    assert approved == EXPECTED_PF1_CHECKPOINT_TAG
    obs = RealTrustedB1R2Reader().observe(approved)
    if not obs.observed_tag_exists:
        # STATE A — pre-checkpoint: successor tag absent → Git prerequisite NOT satisfied.
        assert obs.observed_tag_peel == ""
        assert B.b1_r2_preflight().real_b1_r2_tag_exists is False
    else:
        # STATE B — post-checkpoint: EXACT tag present, valid peeled commit, at HEAD.
        assert obs.checkpoint_tag == approved
        assert len(obs.observed_tag_peel) == 40 and all(
            c in "0123456789abcdef" for c in obs.observed_tag_peel
        )
        assert obs.observed_tag_peel == obs.observed_head  # tag at authorized HEAD
    # Neither state authorizes a provider run — checkpoint existence != authorization.
    assert PN02_PROVIDER_RUN_AUTHORIZED is False


def test_historical_b1_r2_tag_is_immutable_and_not_current_approved():
    # PN02D-B1-PF1 §4/§21: the historical B1-R2 annotated tag permanently records approved
    # commit 611532c and must never be moved. It is NO LONGER the governance-approved
    # identity (PF1 superseded it). When present in Git it ALWAYS peels to 611532c
    # regardless of the current HEAD; after the PF1 commit moves HEAD, peel != HEAD (which
    # is exactly why the B1-R2 gate is superseded and a successor was required).
    assert EXPECTED_B1_R2_CHECKPOINT_TAG != current_approved_b1_r2_checkpoint()
    obs = RealTrustedB1R2Reader().observe(EXPECTED_B1_R2_CHECKPOINT_TAG)
    if obs.observed_tag_exists:
        assert obs.observed_tag_peel == "611532c22b74ed931ad7bb92bdc7e9b0a1431b0a"


def test_implementation_checkpoint_binds_to_b0cb():
    assert B.APPROVED_IMPLEMENTATION_CHECKPOINT_TAG == B0CB_TAG
    assert B.APPROVED_IMPLEMENTATION_CHECKPOINT_COMMIT == (
        "5abeaaa09b7157232b1ac5a234c9d8c50b542585"
    )


def test_new_run_id_is_not_the_retired_one():
    assert B.B1_RUN_ID != RETIRED_RUN_ID
    assert B.B1_RUN_ID.startswith("pn02db1-")


def test_fixture_hash_frozen():
    ok, detail = C.verify_fixture_hash()
    assert ok is True
    assert detail == EXPECTED_FIXTURE_HASH


def test_manifest_frozen_and_content_safe():
    m = B.b1_r2_authorization_manifest()
    assert m["expected_b1_r2_checkpoint_tag"] == EXPECTED_PF1_CHECKPOINT_TAG
    assert m["b1_r2_tag_exists_in_git_now"] is False
    assert m["approved_implementation_checkpoint"]["tag"] == B0CB_TAG
    assert m["run_id"] == B.B1_RUN_ID
    assert m["old_run_id_reused"] is False
    assert m["fixture_hash"] == EXPECTED_FIXTURE_HASH
    assert m["synthetic_only"] is True
    assert m["real_internal_data_allowed"] is False
    assert m["provider_config_fingerprint"] == "pbf_1811d0bfd5cfad2743ffaa69"
    assert m["workload_caps"] == B.b1_caps_dict()
    assert m["corpus_source_embedding"]["planned"] == 21
    assert m["corpus_source_embedding"]["separate_from_query_embedding_budget"] is True
    assert m["concurrency"] == {"index": 1, "gd": 1, "vector": 1}
    assert m["final_answer_calls"] == 0
    assert m["live_provider_authorization_minted"] is False
    assert m["pn02_provider_run_authorized"] is False


def test_workload_caps_match_approved_pn02():
    caps = B.b1_caps_dict()
    assert caps == {
        "GRAPH_INDEX_OPERATION": 24, "GRAPH_INDEX_ATTEMPT": 48, "GRAPH_DELETE": 1,
        "GD_QUERY": 26, "VECTOR_QUERY": 26, "QUERY_EMBEDDING": 26,
        "FINAL_ANSWER": 0, "CLIENT_QUERY": 0, "JUDGE_MODEL": 0,
    }


def test_operation_allowlist_frozen():
    m = B.b1_r2_authorization_manifest()
    assert set(m["operation_allowlist"]) == {
        "VECTOR_QUERY_EMBEDDING", "GD_QUERY_DATA", "GRAPH_DELETE",
        "INDEX_REQUIRED_EMBEDDING", "GRAPH_INDEX", "VECTOR_NOTEBOOK_QUERY",
    }


def test_no_secret_values_in_manifest():
    blob = json.dumps(B.b1_r2_authorization_manifest())
    # Env NAMES are allowed; secret VALUES are not.
    assert "OPENROUTER_API_KEY" in blob  # name only (in provider binding)
    assert "sk-" not in blob
    assert "dummy-test-key" not in blob
    assert "Bearer " not in blob


# --------------------------------------------------------------------------- #
# §7/§15 — checkpoint-lifecycle-aware Git-gate state + permanent negatives
# --------------------------------------------------------------------------- #

def test_preflight_reports_git_gate_state():
    # State-aware (pre/post checkpoint). The frozen envelope fields are invariant; only the
    # Git-gate observation and its consequence change with the real tag's existence.
    rep = B.b1_r2_preflight()
    assert rep.expected_tag_configured is True
    assert rep.fixture_hash_match is True
    assert rep.provider_fingerprint_match is True
    assert rep.workload_caps_match is True
    assert rep.old_run_id_reused is False
    if not rep.real_b1_r2_tag_exists:
        # STATE A — pre-checkpoint: Git gate unsatisfied → not mintable, reason present.
        assert rep.live_provider_authorization_mintable is False
        assert "b1_r2_tag_not_observed_in_git" in rep.fail_closed_reasons
    else:
        # STATE B — post-checkpoint: the B1-R2 GIT gate is satisfiable (no fail reasons).
        # `mintable` here reflects only that control-plane gate — NOT that a provider run
        # is authorized (see the separation test + PN02_PROVIDER_RUN_AUTHORIZED below).
        assert rep.fail_closed_reasons == []
        assert rep.live_provider_authorization_mintable is True
    assert PN02_PROVIDER_RUN_AUTHORIZED is False


def test_real_mint_fails_closed_when_approved_tag_absent():
    # PERMANENT negative (checkpoint-lifecycle robust): an approved identity whose tag is
    # absent from REAL Git fails closed with `b1_r2_tag_not_observed_in_git`. Uses the
    # SYNTHETIC C.TEST_B1R2_TAG (never a real Git tag) via a governance-ONLY patch (real
    # reader retained), so it holds before AND after the real B1-R2 checkpoint tag exists.
    grant = C.frozen_test_grant(b1_r2_checkpoint=C.TEST_B1R2_TAG)
    baseline = C.clean_git_baseline()  # TEST_COMMIT / TEST_TAG (matches the grant baseline)
    preflight = mint_real_preflight_authorization(
        gate0_passed=True, gate1_passed=True, fixture_hash=_fixture_hash(),
        run_id=C.TEST_RUN_ID, runtime_count=3,
    )
    with C.governance_expects_tag(C.TEST_B1R2_TAG):
        with pytest.raises(B1R2CheckpointError) as ei:
            mint_live_provider_run_authorization(
                operator_grant=grant, real_preflight_auth=preflight,
                git_baseline_attestation=baseline, observed_fixture_hash=_fixture_hash(),
            )
    assert "b1_r2_tag_not_observed_in_git" in str(ei.value)


def test_git_gate_satisfiable_does_not_authorize_provider_run():
    # §8/§15 separation: even simulating the POST-checkpoint Git gate as fully satisfiable
    # (trusted reader observes the EXACT tag at HEAD), that is only the control-plane Git
    # prerequisite — the provider-run governance flag stays NO.
    reader = C.b1r2_reader_ok(
        tag=EXPECTED_PF1_CHECKPOINT_TAG, peel=FUTURE_B1_R2_COMMIT, head=FUTURE_B1_R2_COMMIT
    )
    assert verify_b1_r2_checkpoint(
        reader=reader, operator_grant=_future_grant(approved_git_commit=FUTURE_B1_R2_COMMIT),
        approved_expected_checkpoint=EXPECTED_PF1_CHECKPOINT_TAG,
        git_baseline=C.clean_git_baseline(commit=FUTURE_B1_R2_COMMIT, tag=EXPECTED_PF1_CHECKPOINT_TAG),
    ) == []  # Git gate satisfiable
    assert PN02_PROVIDER_RUN_AUTHORIZED is False  # but provider run NOT authorized


def test_mint_has_no_trust_root_injection_parameters():
    # RR4/RR5 preserved: no way to inject an approved identity or reader into the mint.
    params = inspect.signature(mint_live_provider_run_authorization).parameters
    assert "trusted_b1_r2_reader" not in params
    assert "approved_expected_b1_r2_checkpoint" not in params
    assert "b1_r2_checkpoint_attestation" not in params


# --------------------------------------------------------------------------- #
# §20 — envelope negatives (validate_b1_r2_grant) + git-observed negatives
# --------------------------------------------------------------------------- #

def test_valid_prepared_grant_passes_envelope():
    assert B.validate_b1_r2_grant(_future_grant()) == []


def test_envelope_rejects_wrong_implementation_checkpoint():
    from open_notebook.integrations.graphrag.eval.authmintlivepn02d import (
        OperatorRunGrant,
    )

    g = _future_grant()
    bad = OperatorRunGrant(
        run_id=g.run_id, fixture_hash=g.fixture_hash,
        implementation_checkpoint_commit="c" * 40,
        implementation_checkpoint_tag="graphrag-pn02db0ca-real-provider-wiring-design-approved",
        b1_r2_checkpoint=g.b1_r2_checkpoint,
        provider_config_fingerprint=g.provider_config_fingerprint,
        workload_caps=g.workload_caps, operation_allowlist=g.operation_allowlist,
        approved_git_commit=g.approved_git_commit, approved_git_tag=g.approved_git_tag,
    )
    reasons = B.validate_b1_r2_grant(bad)
    assert "b1_r2_wrong_implementation_checkpoint_tag" in reasons
    assert "b1_r2_wrong_implementation_checkpoint_commit" in reasons


def test_envelope_rejects_retired_run_id():
    reasons = B.validate_b1_r2_grant(_future_grant(run_id=RETIRED_RUN_ID))
    assert "b1_r2_retired_run_id_reused" in reasons


def test_envelope_rejects_wrong_run_id():
    reasons = B.validate_b1_r2_grant(_future_grant(run_id="pn02db1-some-other-run"))
    assert "b1_r2_wrong_run_id" in reasons


def test_envelope_rejects_wrong_baseline_tag():
    reasons = B.validate_b1_r2_grant(_future_grant(approved_git_tag="some-other-tag"))
    assert "b1_r2_wrong_approved_baseline_tag" in reasons


# --- B1R2-M1: structural approved_git_commit validation ---------------------- #

@pytest.mark.parametrize(
    "bad_commit",
    [
        "",                          # empty
        "   ",                       # whitespace
        "NOT_STARTED",               # sentinel
        "TBD",                       # sentinel
        "PENDING_B1_R2_CHECKPOINT_COMMIT",  # manifest placeholder — not a real commit
        "just some arbitrary text",  # arbitrary text
        "a" * 39,                    # 39 hex (too short)
        "a" * 41,                    # 41 hex (too long)
        "g" * 40,                    # 40 non-hex chars
        "5ABEAAA09B7157232B1AC5A234C9D8C50B542585",  # 40 UPPER-hex (non-canonical)
        "HEAD",                      # symbolic ref
        "main",                      # branch name
        "feature/graphrag-lifecycle",  # branch name
        "graphrag-pn02db0cb-real-provider-wiring-approved",  # tag name
    ],
)
def test_envelope_rejects_invalid_approved_git_commit(bad_commit):
    reasons = B.validate_b1_r2_grant(_future_grant(approved_git_commit=bad_commit))
    assert "b1_r2_approved_git_commit_invalid" in reasons


def test_envelope_accepts_the_b0cb_commit_as_valid_shape():
    # The real approved B0C-B commit is a structurally valid 40-hex SHA.
    reasons = B.validate_b1_r2_grant(
        _future_grant(approved_git_commit="5abeaaa09b7157232b1ac5a234c9d8c50b542585")
    )
    assert "b1_r2_approved_git_commit_invalid" not in reasons


def test_envelope_accepts_arbitrary_valid_40hex_commit_not_hardcoded():
    # A DIFFERENT structurally valid 40-hex commit also passes → the check is structural,
    # NOT hard-coded to the current SHA.
    reasons = B.validate_b1_r2_grant(_future_grant(approved_git_commit="0" * 40))
    assert "b1_r2_approved_git_commit_invalid" not in reasons


def test_is_valid_commit_sha_unit():
    assert B.is_valid_commit_sha("5abeaaa09b7157232b1ac5a234c9d8c50b542585") is True
    assert B.is_valid_commit_sha("b1b1b1b1" + "0" * 32) is True
    for bad in (None, 123, "", "   ", "HEAD", "main", "a" * 39, "a" * 41, "g" * 40,
                "5ABEAAA09B7157232B1AC5A234C9D8C50B542585"):
        assert B.is_valid_commit_sha(bad) is False


def test_superseded_historical_b1_r2_identity_cannot_substitute_for_pf1():
    # PN02D-B1-PF1 §21: the historical B1-R2 tag is superseded — a grant/verify path naming
    # it as the approved identity is rejected because the current grant identity is the PF1
    # successor (they must match). This holds regardless of the B1-R2 tag existing in Git.
    reasons = verify_b1_r2_checkpoint(
        reader=C.b1r2_reader_ok(tag=EXPECTED_B1_R2_CHECKPOINT_TAG),
        operator_grant=_future_grant(),  # grant.b1_r2_checkpoint == PF1 (current approved)
        approved_expected_checkpoint=EXPECTED_B1_R2_CHECKPOINT_TAG,
        git_baseline=C.clean_git_baseline(),
    )
    assert "b1_r2_grant_identity_mismatch" in reasons


@pytest.mark.parametrize("bad_expected", [B0CA_TAG, B0CB_TAG, "arbitrary-tag", "graphrag-05-forensic-approved"])
def test_verify_rejects_non_b1r2_approved_identity(bad_expected):
    # If the approved-expected identity were anything but the exact B1-R2 tag (B0C-A, the
    # B0C-B impl tag, or an arbitrary tag), the verifier refuses. B0C-A and B0C-B are on the
    # safety denylist; any other tag is absent from Git.
    reasons = verify_b1_r2_checkpoint(
        reader=RealTrustedB1R2Reader(),
        operator_grant=_future_grant(),
        approved_expected_checkpoint=bad_expected,
        git_baseline=C.clean_git_baseline(commit=C.TEST_COMMIT, tag=EXPECTED_B1_R2_CHECKPOINT_TAG),
    )
    assert reasons  # non-empty → cannot authorize


def test_b0ca_and_b0cb_on_denylist():
    for tag in (B0CA_TAG, B0CB_TAG):
        reasons = verify_b1_r2_checkpoint(
            reader=C.b1r2_reader_ok(tag=tag),
            operator_grant=_future_grant(),
            approved_expected_checkpoint=tag,
            git_baseline=C.clean_git_baseline(),
        )
        assert "b1_r2_approved_identity_is_known_non_b1_r2_checkpoint" in reasons


def test_verify_correct_tag_wrong_peel_rejected():
    # Reader observes the expected tag but it peels to a non-HEAD commit.
    reader = C.b1r2_reader_ok(tag=EXPECTED_B1_R2_CHECKPOINT_TAG, peel="d" * 40, head=FUTURE_B1_R2_COMMIT)
    reasons = verify_b1_r2_checkpoint(
        reader=reader, operator_grant=_future_grant(),
        approved_expected_checkpoint=EXPECTED_B1_R2_CHECKPOINT_TAG,
        git_baseline=C.clean_git_baseline(commit=FUTURE_B1_R2_COMMIT, tag=EXPECTED_B1_R2_CHECKPOINT_TAG),
    )
    assert "b1_r2_tag_not_at_authorized_head" in reasons


def test_verify_correct_peel_wrong_baseline_head_rejected():
    other = "e" * 40
    reader = C.b1r2_reader_ok(tag=EXPECTED_B1_R2_CHECKPOINT_TAG, peel=other, head=other)
    reasons = verify_b1_r2_checkpoint(
        reader=reader, operator_grant=_future_grant(),
        approved_expected_checkpoint=EXPECTED_B1_R2_CHECKPOINT_TAG,
        git_baseline=C.clean_git_baseline(commit=FUTURE_B1_R2_COMMIT, tag=EXPECTED_B1_R2_CHECKPOINT_TAG),
    )
    assert "b1_r2_head_not_bound_to_approved_baseline" in reasons


def test_dirty_tree_fails_mint():
    # §20.8: a dirty working tree is refused by the git-baseline gate before B1-R2.
    grant = _future_grant(approved_git_commit=C.TEST_COMMIT)
    dirty = C.dirty_git_baseline(commit=C.TEST_COMMIT, tag=EXPECTED_B1_R2_CHECKPOINT_TAG)
    preflight = mint_real_preflight_authorization(
        gate0_passed=True, gate1_passed=True, fixture_hash=_fixture_hash(),
        run_id=B.B1_RUN_ID, runtime_count=3,
    )
    with pytest.raises(GitBaselineError):
        mint_live_provider_run_authorization(
            operator_grant=grant, real_preflight_auth=preflight,
            git_baseline_attestation=dirty, observed_fixture_hash=_fixture_hash(),
        )


def test_envelope_rejects_wrong_fixture_and_fingerprint_and_boundary():
    from open_notebook.integrations.graphrag.eval.authmintlivepn02d import (
        OperatorRunGrant,
    )

    g = _future_grant()
    bad = OperatorRunGrant(
        run_id=g.run_id, fixture_hash="deadbeef" * 8,
        implementation_checkpoint_commit=g.implementation_checkpoint_commit,
        implementation_checkpoint_tag=g.implementation_checkpoint_tag,
        b1_r2_checkpoint=g.b1_r2_checkpoint,
        provider_config_fingerprint="pbf_wrong0000000000000000",
        workload_caps=g.workload_caps, operation_allowlist=g.operation_allowlist,
        approved_git_commit=g.approved_git_commit, approved_git_tag=g.approved_git_tag,
        synthetic_only=False, real_internal_data_allowed=True,
    )
    reasons = B.validate_b1_r2_grant(bad)
    assert "b1_r2_wrong_fixture_hash" in reasons
    assert "b1_r2_wrong_provider_fingerprint" in reasons
    assert "b1_r2_synthetic_only_false" in reasons
    assert "b1_r2_real_internal_data_allowed" in reasons


def test_envelope_rejects_cap_and_allowlist_escalation():
    from open_notebook.integrations.graphrag.eval.authmintlivepn02d import (
        OperatorRunGrant,
    )

    g = _future_grant()
    escalated_caps = dict(g.workload_caps)
    escalated_caps["FINAL_ANSWER"] = 5  # escalation
    bad = OperatorRunGrant(
        run_id=g.run_id, fixture_hash=g.fixture_hash,
        implementation_checkpoint_commit=g.implementation_checkpoint_commit,
        implementation_checkpoint_tag=g.implementation_checkpoint_tag,
        b1_r2_checkpoint=g.b1_r2_checkpoint,
        provider_config_fingerprint=g.provider_config_fingerprint,
        workload_caps=escalated_caps,
        operation_allowlist=frozenset({"GRAPH_INDEX", "UNAUTHORIZED_OP"}),
        approved_git_commit=g.approved_git_commit, approved_git_tag=g.approved_git_tag,
    )
    reasons = B.validate_b1_r2_grant(bad)
    assert "b1_r2_workload_caps_mismatch" in reasons
    assert "b1_r2_operation_allowlist_mismatch" in reasons


# --------------------------------------------------------------------------- #
# §21 — future-positive simulation (patched module internals; production signature)
# --------------------------------------------------------------------------- #

def test_future_positive_verify_becomes_satisfiable_via_trusted_reader():
    # With a trusted reader observing the EXACT expected tag at the future commit, and a
    # baseline at that commit, the B1-R2 verifier passes. No real tag is created.
    reader = C.b1r2_reader_ok(
        tag=EXPECTED_PF1_CHECKPOINT_TAG, peel=FUTURE_B1_R2_COMMIT, head=FUTURE_B1_R2_COMMIT
    )
    reasons = verify_b1_r2_checkpoint(
        reader=reader, operator_grant=_future_grant(),
        approved_expected_checkpoint=EXPECTED_PF1_CHECKPOINT_TAG,
        git_baseline=C.clean_git_baseline(commit=FUTURE_B1_R2_COMMIT, tag=EXPECTED_PF1_CHECKPOINT_TAG),
    )
    assert reasons == []


def test_future_positive_full_mint_via_production_signature_under_patch():
    # §21: the WHOLE envelope becomes satisfiable ONLY inside a controlled patch of the
    # mint's INTERNAL governance + trusted reader (module boundary) to a SYNTHETIC future
    # tag/commit. It uses the PRODUCTION mint signature (no trust-root params) and creates
    # NO real tag. The unpatched/real path stays fail-closed (see the pre-tag test).
    grant = _future_grant(approved_git_commit=FUTURE_B1_R2_COMMIT)
    baseline = C.clean_git_baseline(commit=FUTURE_B1_R2_COMMIT, tag=EXPECTED_PF1_CHECKPOINT_TAG)
    preflight = mint_real_preflight_authorization(
        gate0_passed=True, gate1_passed=True, fixture_hash=_fixture_hash(),
        run_id=B.B1_RUN_ID, runtime_count=3,
    )
    with C.approved_b1r2_governance(tag=EXPECTED_PF1_CHECKPOINT_TAG, head=FUTURE_B1_R2_COMMIT):
        auth = mint_live_provider_run_authorization(
            operator_grant=grant, real_preflight_auth=preflight,
            git_baseline_attestation=baseline, observed_fixture_hash=_fixture_hash(),
        )
    assert isinstance(auth, LiveProviderRunAuthorization)
    assert auth.run_id == B.B1_RUN_ID
    assert auth.b1_r2_checkpoint == EXPECTED_PF1_CHECKPOINT_TAG
    assert auth.as_public_dict()["b1_r2_checkpoint_attested"] is True


def test_governance_flag_not_flipped_to_authorized():
    # The presence of the B1-R2 envelope must NOT flip the provider-run governance flag.
    assert PN02_PROVIDER_RUN_AUTHORIZED is False
