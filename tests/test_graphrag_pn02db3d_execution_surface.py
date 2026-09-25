"""PN02D-B3D — provider-free tests for the governed real-B3 observability execution surface.

Covers: the distinct B3B exact-checkpoint auth profile (absent / exact-HEAD / ancestor /
wrong-peel fail-closed; valid tag alone cannot mint; B2 profile unchanged and still fail-closed
at a successor HEAD; B2<->B3B grants do not cross-authorize), the canonical governed run function
(reuses the B2 engine with the B3B mint + observer injection, distinct content-safe artifact,
distinct run id, fail-closed), and the distinct CLI verb (dispatch + fail-closed without auth).

ZERO provider traffic / ZERO real execution: the B2 engine is replaced by an async fake.
"""

from __future__ import annotations

import json
import subprocess
from types import SimpleNamespace
from typing import Any, cast
from unittest import mock

import graphrag_pn02db0cb_common as C
import pytest

import open_notebook.integrations.graphrag.eval.authmintlivepn02d as authmint
from open_notebook.integrations.graphrag.eval import cli_live_pn02d as cli
from open_notebook.integrations.graphrag.eval import p1diagrunnerpn02db3 as runner
from open_notebook.integrations.graphrag.eval import realseamsb2pn02d as realseams
from open_notebook.integrations.graphrag.eval.authb1r2pn02d import (
    APPROVED_IMPLEMENTATION_CHECKPOINT_COMMIT,
    APPROVED_IMPLEMENTATION_CHECKPOINT_TAG,
)
from open_notebook.integrations.graphrag.eval.authmintlivepn02d import (
    EXPECTED_B2_CHECKPOINT_TAG,
    EXPECTED_B3_LIVE_CHECKPOINT_TAG,
    EXPECTED_B3B_CHECKPOINT_TAG,
    EXPECTED_FIXTURE_HASH,
    HISTORICAL_B3B_CHECKPOINT_TAG,
    HISTORICAL_B3J_CHECKPOINT_TAG,
    HISTORICAL_B3Q_CHECKPOINT_TAG,
    HISTORICAL_B3V_CHECKPOINT_TAG,
    HISTORICAL_B3XR2_CHECKPOINT_TAG,
    RealTrustedB1R2Reader,
    b2_r2_refusal_reasons,
    b3b_r2_refusal_reasons,
    current_approved_b3b_checkpoint,
    frozen_b2_operator_grant_template,
    frozen_b3b_operator_grant_template,
    mint_live_b3_provider_run_authorization,
)
from open_notebook.integrations.graphrag.eval.datasetpn02 import load_fixture
from open_notebook.integrations.graphrag.eval.qavaluepn02db3 import (
    QAValueObservabilityError,
)
from open_notebook.integrations.graphrag.eval.schemaspn02 import ArmId, QAAnswerResult

OBS_RUN_ID = "pn02db3-obs-aaaaaaaaaaaaaaaaaaaaaaaaaaaa"
SUCCESSOR = "b" * 40
ANCESTOR = "a" * 40


# --------------------------------------------------------------------------- #
# helpers (mirror the B2 mint-identity lifecycle harness, but for B3B)
# --------------------------------------------------------------------------- #


def _b3_grant(*, b1_r2: str = EXPECTED_B3B_CHECKPOINT_TAG, commit: str = C.TEST_COMMIT, run_id: str = OBS_RUN_ID):
    return frozen_b3b_operator_grant_template(
        run_id=run_id,
        implementation_checkpoint_commit=commit,
        implementation_checkpoint_tag=C.TEST_TAG,
        b1_r2_checkpoint=b1_r2,
        approved_git_commit=commit,
        approved_git_tag=C.TEST_TAG,
    )


def _patch_b3(tag, reader):
    return _MultiPatch(
        mock.patch.object(authmint, "current_approved_b3b_checkpoint", return_value=tag),
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


# --------------------------------------------------------------------------- #
# B3B checkpoint identity
# --------------------------------------------------------------------------- #


def test_b3_live_identity_is_b3xr4_successor_and_historical_tags_separate():
    # PN02D-B3X-R4: the governed B3 LIVE authorization identity is the predeclared B3X-R4 successor tag,
    # DISTINCT from the historical B3B wiring tag AND the historical B3J/B3Q/B3V/B3X-R2 successor tags (all
    # now ancestors / immutable evidence, NEVER the current live identity).
    assert EXPECTED_B3_LIVE_CHECKPOINT_TAG == "graphrag-pn02db3xr4-b3-live-auth-successor-approved"
    assert HISTORICAL_B3B_CHECKPOINT_TAG == "graphrag-pn02db3b-live-observability-wiring-approved"
    assert HISTORICAL_B3J_CHECKPOINT_TAG == "graphrag-pn02db3j-b3-live-auth-successor-approved"
    assert HISTORICAL_B3Q_CHECKPOINT_TAG == "graphrag-pn02db3q-b3-live-auth-successor-approved"
    assert HISTORICAL_B3V_CHECKPOINT_TAG == "graphrag-pn02db3v-b3-live-auth-successor-approved"
    assert HISTORICAL_B3XR2_CHECKPOINT_TAG == "graphrag-pn02db3xr2-b3-live-auth-successor-approved"
    assert EXPECTED_B3_LIVE_CHECKPOINT_TAG != HISTORICAL_B3B_CHECKPOINT_TAG
    assert EXPECTED_B3_LIVE_CHECKPOINT_TAG != HISTORICAL_B3J_CHECKPOINT_TAG
    assert EXPECTED_B3_LIVE_CHECKPOINT_TAG != HISTORICAL_B3Q_CHECKPOINT_TAG
    assert EXPECTED_B3_LIVE_CHECKPOINT_TAG != HISTORICAL_B3V_CHECKPOINT_TAG
    assert EXPECTED_B3_LIVE_CHECKPOINT_TAG != HISTORICAL_B3XR2_CHECKPOINT_TAG
    # the five historical successor/wiring tags are mutually distinct
    assert len({
        HISTORICAL_B3B_CHECKPOINT_TAG,
        HISTORICAL_B3J_CHECKPOINT_TAG,
        HISTORICAL_B3Q_CHECKPOINT_TAG,
        HISTORICAL_B3V_CHECKPOINT_TAG,
        HISTORICAL_B3XR2_CHECKPOINT_TAG,
    }) == 5
    # resolver + backward-compat alias both point at the LIVE (B3X-R4) identity, distinct from B2
    assert current_approved_b3b_checkpoint() == EXPECTED_B3_LIVE_CHECKPOINT_TAG
    assert EXPECTED_B3B_CHECKPOINT_TAG == EXPECTED_B3_LIVE_CHECKPOINT_TAG
    assert EXPECTED_B3_LIVE_CHECKPOINT_TAG != EXPECTED_B2_CHECKPOINT_TAG


# --------------------------------------------------------------------------- #
# PN02D-B3X-R3A: tri-state real-Git live-auth lifecycle classifier (TEST-ONLY).
#
# The governed B3 live-auth identity moves through THREE legitimate lifecycle states relative to
# HEAD, and a real-Git test that hard-codes any single one becomes stale at the next governed
# transition (the B3X-R3 checkpoint proved this):
#   TAG_ABSENT     — successor identity predeclared but its tag not yet created  -> FAIL_CLOSED
#   EXACT_HEAD     — expected live tag peels exactly to HEAD                      -> TRUST PASS
#   ANCESTOR_STALE — HEAD advanced past the live tag (impl checkpoint, no successor tag yet) -> FAIL_CLOSED
# This classifier INDEPENDENTLY inspects Git (existence / peel / HEAD / ancestry) — it does NOT
# derive the state from the production verifier result, and it does NOT reimplement the trust
# algorithm. Any relationship that is neither exact-HEAD nor a true ancestor is an explicit failure
# (never silently normalized). It lives in the test module only.
# --------------------------------------------------------------------------- #


def _git_is_ancestor(ancestor: str, descendant: str) -> bool:
    """True iff ``ancestor`` is a real-Git ancestor of ``descendant`` (exit code 0)."""
    try:
        proc = subprocess.run(  # noqa: S603 - fixed argv, no shell
            ["git", "merge-base", "--is-ancestor", ancestor, descendant],
            capture_output=True, text=True, timeout=10,
        )
        return proc.returncode == 0
    except Exception:  # noqa: BLE001
        return False


def _classify_live_tag_lifecycle(reader, tag, head_commit, *, is_ancestor=_git_is_ancestor):
    """Independently classify EXPECTED live tag vs HEAD into TAG_ABSENT / EXACT_HEAD / ANCESTOR_STALE.

    Raises AssertionError for any unrecognized relationship (peel neither HEAD nor a true ancestor):
    such states are never silently accepted.
    """
    obs = reader.observe(tag)
    if not obs.observed_tag_exists:
        return "TAG_ABSENT"
    peel = obs.observed_tag_peel
    if peel == obs.observed_head == head_commit:
        return "EXACT_HEAD"
    if peel and is_ancestor(peel, head_commit):
        return "ANCESTOR_STALE"
    raise AssertionError(
        f"unrecognized live-tag/HEAD relationship for {tag!r}: peel={peel!r} head={head_commit!r}"
    )


def _b3_live_grant_for(tag, baseline):
    return frozen_b3b_operator_grant_template(
        run_id=OBS_RUN_ID,
        implementation_checkpoint_commit=C.TEST_COMMIT,
        implementation_checkpoint_tag=C.TEST_TAG,
        b1_r2_checkpoint=tag,
        approved_git_commit=baseline.head_commit,
        approved_git_tag=baseline.head_tag,
    )


def test_real_git_b3_live_profile_matches_current_lifecycle_state():
    # PN02D-B3X-R3A (tri-state lifecycle-invariant): independently classify the CURRENT landed
    # relationship of EXPECTED_B3_LIVE_CHECKPOINT_TAG vs HEAD, then assert the SHARED production trust
    # result matches that state. This is invariant across ALL legitimate governed transitions (a
    # successor implementation leaves the predeclared tag ABSENT; an implementation checkpoint leaves
    # the live tag an ANCESTOR; a successor checkpoint restores EXACT_HEAD) — no per-checkpoint test
    # edit is ever required. Non-tautological: the state is observed from Git, the assertion depends on
    # the production verifier's refusal set.
    baseline = cli.read_git_baseline()
    reader = authmint._build_trusted_b1_r2_reader()
    state = _classify_live_tag_lifecycle(reader, EXPECTED_B3_LIVE_CHECKPOINT_TAG, baseline.head_commit)
    reasons = b3b_r2_refusal_reasons(_b3_live_grant_for(EXPECTED_B3_LIVE_CHECKPOINT_TAG, baseline), baseline)
    if state == "TAG_ABSENT":
        assert "b1_r2_tag_not_observed_in_git" in reasons  # fail closed, no grandfathering
    elif state == "EXACT_HEAD":
        assert "b1_r2_tag_not_observed_in_git" not in reasons
        assert "b1_r2_tag_not_at_authorized_head" not in reasons
        assert reasons == []  # exact-head trust PASS
    elif state == "ANCESTOR_STALE":
        assert "b1_r2_tag_not_at_authorized_head" in reasons  # stale successor, fail closed
    else:  # pragma: no cover - classifier only returns the three states or raises
        pytest.fail(f"unexpected lifecycle state {state!r}")
    # the resolver always points at the current expected live identity, distinct from the historical tags
    assert current_approved_b3b_checkpoint() == EXPECTED_B3_LIVE_CHECKPOINT_TAG
    assert current_approved_b3b_checkpoint() != HISTORICAL_B3V_CHECKPOINT_TAG
    assert current_approved_b3b_checkpoint() != HISTORICAL_B3Q_CHECKPOINT_TAG
    assert current_approved_b3b_checkpoint() != HISTORICAL_B3J_CHECKPOINT_TAG
    assert current_approved_b3b_checkpoint() != HISTORICAL_B3B_CHECKPOINT_TAG


def test_real_git_b3p_implementation_tag_is_ancestor_and_cannot_authorize_b3_live():
    # PN02D-B3X-R2 (§9/§12): the B3P implementation checkpoint tag is an IMPLEMENTATION tag, NOT the
    # governed B3 live-auth identity (the B3X-R4 successor tag, currently absent). The B3P tag peels to an
    # ANCESTOR (no longer current HEAD). A grant naming the B3P tag as its B3 live checkpoint must STILL
    # fail closed on identity mismatch — neither an ancestor tag nor a non-live implementation tag can
    # authorize the B3 live profile.
    B3P_IMPL_TAG = "graphrag-pn02db3p-materialization-treatment-approved"
    baseline = cli.read_git_baseline()
    reader = authmint._build_trusted_b1_r2_reader()
    b3p_obs = reader.observe(B3P_IMPL_TAG)
    assert b3p_obs.observed_tag_exists is True
    assert b3p_obs.observed_tag_peel != b3p_obs.observed_head  # ANCESTOR, not current HEAD
    assert b3p_obs.observed_head == baseline.head_commit
    assert B3P_IMPL_TAG != current_approved_b3b_checkpoint()  # NOT the live identity
    reasons = b3b_r2_refusal_reasons(
        frozen_b3b_operator_grant_template(
            run_id=OBS_RUN_ID,
            implementation_checkpoint_commit=C.TEST_COMMIT,
            implementation_checkpoint_tag=C.TEST_TAG,
            b1_r2_checkpoint=B3P_IMPL_TAG,  # try to use the impl tag as the live identity
            approved_git_commit=baseline.head_commit,
            approved_git_tag=baseline.head_tag,
        ),
        baseline,
    )
    # the grant's B3-live identity does not match the approved B3X-R4 identity -> fail closed
    assert "b1_r2_grant_identity_mismatch" in reasons


def test_real_git_b3u_implementation_tag_is_ancestor_and_cannot_authorize_b3_live():
    # PN02D-B3X-R2 (real-git sync): the B3U implementation tag is IMPLEMENTATION-evidence, NOT the
    # governed B3 live-auth identity (the B3X-R4 successor tag, currently absent). The B3U tag peels to an
    # ANCESTOR (no longer current HEAD — HEAD advanced past it at the B3V and B3X-R1 checkpoints). A
    # grant naming the B3U tag as its B3 live checkpoint must STILL fail closed on identity mismatch —
    # neither an ancestor tag nor a non-live implementation tag can authorize the B3 live profile.
    B3U_IMPL_TAG = "graphrag-pn02db3u-qa-value-observability-approved"
    baseline = cli.read_git_baseline()
    reader = authmint._build_trusted_b1_r2_reader()
    b3u_obs = reader.observe(B3U_IMPL_TAG)
    assert b3u_obs.observed_tag_exists is True
    assert b3u_obs.observed_tag_peel != b3u_obs.observed_head  # ANCESTOR, not current HEAD
    assert b3u_obs.observed_head == baseline.head_commit
    assert B3U_IMPL_TAG != current_approved_b3b_checkpoint()  # NOT the live identity
    reasons = b3b_r2_refusal_reasons(
        frozen_b3b_operator_grant_template(
            run_id=OBS_RUN_ID,
            implementation_checkpoint_commit=C.TEST_COMMIT,
            implementation_checkpoint_tag=C.TEST_TAG,
            b1_r2_checkpoint=B3U_IMPL_TAG,  # try to use the exact-HEAD impl tag as the live identity
            approved_git_commit=baseline.head_commit,
            approved_git_tag=baseline.head_tag,
        ),
        baseline,
    )
    # exact-HEAD impl tag is NOT the B3X-R4 live identity -> fail closed on identity mismatch
    assert "b1_r2_grant_identity_mismatch" in reasons


def test_real_git_b3xr1_implementation_tag_is_ancestor_and_cannot_authorize_b3_live():
    # PN02D-B3X-R3 (lifecycle-invariant, landed state): the B3X-R1 QA-value runtime-wiring tag is
    # IMPLEMENTATION-evidence, NOT the governed B3 live-auth identity (the current B3X-R4 successor tag).
    # After the approved B3X-R2 governance checkpoint advanced HEAD past the B3X-R1 commit, the B3X-R1 tag
    # peels to an ANCESTOR (no longer current HEAD). A grant naming the B3X-R1 tag as its B3 live checkpoint
    # must STILL fail closed on identity mismatch — neither an ancestor tag nor a non-live implementation
    # tag can authorize the B3 live profile. (The phase-independent security semantic "an implementation
    # tag sitting at EXACT HEAD cannot substitute for the expected live identity" is covered synthetically
    # by ``test_synthetic_exact_head_implementation_tag_cannot_authorize`` below.)
    B3XR1_IMPL_TAG = "graphrag-pn02db3xr1-qa-value-runtime-wiring-approved"
    baseline = cli.read_git_baseline()
    reader = authmint._build_trusted_b1_r2_reader()
    b3xr1_obs = reader.observe(B3XR1_IMPL_TAG)
    assert b3xr1_obs.observed_tag_exists is True
    assert b3xr1_obs.observed_tag_peel != b3xr1_obs.observed_head  # ANCESTOR, not current HEAD
    assert b3xr1_obs.observed_head == baseline.head_commit
    assert B3XR1_IMPL_TAG != current_approved_b3b_checkpoint()  # NOT the live identity
    reasons = b3b_r2_refusal_reasons(
        frozen_b3b_operator_grant_template(
            run_id=OBS_RUN_ID,
            implementation_checkpoint_commit=C.TEST_COMMIT,
            implementation_checkpoint_tag=C.TEST_TAG,
            b1_r2_checkpoint=B3XR1_IMPL_TAG,  # try to use the ancestor impl tag as the live identity
            approved_git_commit=baseline.head_commit,
            approved_git_tag=baseline.head_tag,
        ),
        baseline,
    )
    # ancestor implementation-evidence tag is NOT the B3X-R4 live identity -> fail closed
    assert "b1_r2_grant_identity_mismatch" in reasons


def test_synthetic_exact_head_implementation_tag_cannot_authorize():
    # PN02D-B3X-R3 (§10/§11 — remediation of PN02DB3XR3-IR1-M1): FAITHFULLY model an
    # IMPLEMENTATION-evidence tag that ACTUALLY peels to EXACT HEAD, while the separately-named expected
    # live-auth identity (B3X-R4) is ABSENT, and prove FAIL_CLOSED through the SHARED production trust
    # logic (b3b_r2_refusal_reasons over a RealTrustedB1R2Reader). This is the phase-independent security
    # semantic: an implementation tag sitting at exact HEAD must NOT substitute for the expected live
    # identity. A scripted Git boundary models BOTH tags (impl present@HEAD / B3X-R4 absent), so the
    # assertion depends on the production refusal RESULT, not a bare constant compare. Stays true
    # regardless of real Git history / which commit HEAD currently points at.
    HEAD_X = C.TEST_COMMIT
    impl_tag_at_head = "graphrag-pn02db3xr1-qa-value-runtime-wiring-approved"
    expected_live = EXPECTED_B3B_CHECKPOINT_TAG  # the current B3X-R4 successor identity
    assert impl_tag_at_head != expected_live

    def _multi_tag_runner(args):
        a = list(args)
        if a[:2] == ["rev-parse", "HEAD"]:
            return HEAD_X
        if a[:2] == ["tag", "--list"]:
            name = a[2] if len(a) > 2 else ""
            # the implementation-evidence tag exists; the expected live (B3X-R4) tag is ABSENT
            return name if name == impl_tag_at_head else ""
        if a[:2] == ["rev-list", "-n"]:
            ref = a[3] if len(a) > 3 else ""
            # the implementation-evidence tag peels EXACTLY to HEAD
            return HEAD_X if ref == f"refs/tags/{impl_tag_at_head}" else ""
        return ""

    reader = RealTrustedB1R2Reader(git_runner=_multi_tag_runner)
    # the synthetic reader GENUINELY models the implementation tag AT exact HEAD ...
    impl_obs = reader.observe(impl_tag_at_head)
    assert impl_obs.observed_tag_exists is True
    assert impl_obs.observed_tag_peel == impl_obs.observed_head == HEAD_X  # impl tag peel == HEAD
    # ... while the expected live identity (B3X-R4) is ABSENT
    live_obs = reader.observe(expected_live)
    assert live_obs.observed_tag_exists is False
    # a grant naming the (correct) expected live identity B3X-R4 fails closed because that tag is ABSENT,
    # even though an implementation-evidence tag peels exactly to HEAD -> the impl tag does NOT substitute
    with _patch_b3(expected_live, reader):
        reasons = b3b_r2_refusal_reasons(
            _b3_grant(b1_r2=expected_live, commit=HEAD_X),
            C.clean_git_baseline(commit=HEAD_X, tag=C.TEST_TAG),
        )
    assert "b1_r2_tag_not_observed_in_git" in reasons


# --------------------------------------------------------------------------- #
# B3B auth trust states
# --------------------------------------------------------------------------- #


def test_b3b_auth_tag_absent_fails_closed():
    # deterministic absence: a fabricated B3B tag that is never in real Git
    absent = "graphrag-pn02db3b-DEFINITELY-ABSENT"
    with _patch_b3(absent, RealTrustedB1R2Reader()):
        reasons = b3b_r2_refusal_reasons(_b3_grant(b1_r2=absent), C.clean_git_baseline())
    assert "b1_r2_tag_not_observed_in_git" in reasons


def test_b3b_auth_exact_head_accepted():
    reader = C.b1r2_reader_ok(tag=EXPECTED_B3B_CHECKPOINT_TAG, peel=C.TEST_COMMIT, head=C.TEST_COMMIT)
    with _patch_b3(EXPECTED_B3B_CHECKPOINT_TAG, reader):
        reasons = b3b_r2_refusal_reasons(
            _b3_grant(commit=C.TEST_COMMIT),
            C.clean_git_baseline(commit=C.TEST_COMMIT, tag=C.TEST_TAG),
        )
    assert reasons == []  # checkpoint identity valid (still needs grant+preflight to mint)


def test_b3b_auth_ancestor_fails_closed():
    reader = C.b1r2_reader_ok(tag=EXPECTED_B3B_CHECKPOINT_TAG, peel=ANCESTOR, head=SUCCESSOR)
    with _patch_b3(EXPECTED_B3B_CHECKPOINT_TAG, reader):
        reasons = b3b_r2_refusal_reasons(
            _b3_grant(commit=SUCCESSOR),
            C.clean_git_baseline(commit=SUCCESSOR, tag=C.TEST_TAG),
        )
    assert "b1_r2_tag_not_at_authorized_head" in reasons  # no ancestor grandfathering


def test_b3b_auth_wrong_peel_fails_closed():
    reader = C.b1r2_reader_ok(tag=EXPECTED_B3B_CHECKPOINT_TAG, peel="dead" + "0" * 36, head=C.TEST_COMMIT)
    with _patch_b3(EXPECTED_B3B_CHECKPOINT_TAG, reader):
        reasons = b3b_r2_refusal_reasons(_b3_grant(), C.clean_git_baseline(commit=C.TEST_COMMIT))
    assert "b1_r2_tag_not_at_authorized_head" in reasons


def test_valid_b3b_tag_alone_cannot_mint_without_preflight():
    reader = C.b1r2_reader_ok(tag=EXPECTED_B3B_CHECKPOINT_TAG, peel=C.TEST_COMMIT, head=C.TEST_COMMIT)
    with _patch_b3(EXPECTED_B3B_CHECKPOINT_TAG, reader), pytest.raises(Exception):
        mint_live_b3_provider_run_authorization(
            operator_grant=_b3_grant(),
            real_preflight_auth=None,  # no real preflight capability -> fail closed
            git_baseline_attestation=C.clean_git_baseline(commit=C.TEST_COMMIT, tag=C.TEST_TAG),
            observed_fixture_hash=EXPECTED_FIXTURE_HASH,
        )


# --------------------------------------------------------------------------- #
# Cross-authorization + B2 profile unchanged
# --------------------------------------------------------------------------- #


def test_b3b_grant_and_b2_grant_do_not_cross_authorize():
    reader = C.b1r2_reader_ok(tag=EXPECTED_B3B_CHECKPOINT_TAG, peel=C.TEST_COMMIT, head=C.TEST_COMMIT)
    # a B2-identity grant verified against the B3B expected identity -> identity mismatch
    b2_grant = frozen_b2_operator_grant_template(
        run_id=OBS_RUN_ID, implementation_checkpoint_commit=C.TEST_COMMIT,
        implementation_checkpoint_tag=C.TEST_TAG, b1_r2_checkpoint=EXPECTED_B2_CHECKPOINT_TAG,
        approved_git_commit=C.TEST_COMMIT, approved_git_tag=C.TEST_TAG,
    )
    with _patch_b3(EXPECTED_B3B_CHECKPOINT_TAG, reader):
        reasons = b3b_r2_refusal_reasons(b2_grant, C.clean_git_baseline(commit=C.TEST_COMMIT, tag=C.TEST_TAG))
    assert "b1_r2_grant_identity_mismatch" in reasons


def test_b2_profile_unchanged_and_fails_closed_at_ancestor():
    # B2 expected identity is untouched by B3D
    assert EXPECTED_B2_CHECKPOINT_TAG == "graphrag-pn02db2-chat-model-remediation-approved"
    # a B2 tag peeling to an ancestor of HEAD still fails closed (exact-HEAD, no grandfathering)
    reader = C.b1r2_reader_ok(tag=EXPECTED_B2_CHECKPOINT_TAG, peel=ANCESTOR, head=SUCCESSOR)
    with mock.patch.object(authmint, "current_approved_b2_checkpoint", return_value=EXPECTED_B2_CHECKPOINT_TAG), \
         mock.patch.object(authmint, "_build_trusted_b1_r2_reader", return_value=reader):
        reasons = b2_r2_refusal_reasons(
            frozen_b2_operator_grant_template(
                run_id=C.TEST_RUN_ID, implementation_checkpoint_commit=SUCCESSOR,
                implementation_checkpoint_tag=C.TEST_TAG, b1_r2_checkpoint=EXPECTED_B2_CHECKPOINT_TAG,
                approved_git_commit=SUCCESSOR, approved_git_tag=C.TEST_TAG),
            C.clean_git_baseline(commit=SUCCESSOR, tag=C.TEST_TAG),
        )
    assert "b1_r2_tag_not_at_authorized_head" in reasons


# --------------------------------------------------------------------------- #
# Governed run function — observer injection + B3B mint + distinct artifact
# --------------------------------------------------------------------------- #


def _first_positive(fx):
    return sorted((q for q in fx.queries if q.answerable and q.expected_answer_facts),
                  key=lambda q: q.query_id)[0]


def _good_record(fx, q, arm):
    """A member-filtered, correctly-answered (positive) / abstaining (negative) execution record."""
    members = fx.members_of(q.notebook_id)
    ev = tuple(s for s in q.required_source_ids if s in members)
    if q.is_negative:
        result = QAAnswerResult(
            query_id=q.query_id, notebook_id=q.notebook_id, arm=arm,
            abstained=True, answer_text=None, citation_source_ids=())
    else:
        result = QAAnswerResult(
            query_id=q.query_id, notebook_id=q.notebook_id, arm=arm, abstained=False,
            answer_text=" ".join(q.expected_answer_facts),
            citation_source_ids=q.required_citation_source_ids)
    return runner.B2QAExecutionRecord(
        query_id=q.query_id, notebook_id=q.notebook_id, arm=arm,
        evidence_source_ids=ev, result=result)


def _drive_all_72(observer, fx):
    """Drive the observer over all 24 queries x 3 arms = 72 records, as the real stage would."""
    for q in sorted(fx.queries, key=lambda x: x.query_id):
        for arm in (ArmId.QA_V, ArmId.QA_GD, ArmId.QA_VGD):
            observer(_good_record(fx, q, arm))


# PN02D-B3X-R1: a REALISTIC raw pre-CLI runtime report. Isolation lives ONLY under the raw
# evaluator field ``scientific_outputs.PER_NOTEBOOK_ISOLATION_EVIDENCED`` — NOT the CLI-projected
# top-level ``isolation_evidenced`` (which does not exist at the live runner seam). This is the
# exact shape that caused the B3X qa_value_observability omission.
def _raw_report_with_isolation(verdict="YES"):
    return {"scientific_outputs": {"PER_NOTEBOOK_ISOLATION_EVIDENCED": verdict}}


def test_governed_run_injects_b3b_mint_and_observer_and_builds_distinct_artifact(monkeypatch):
    # PN02D-B3X-R1 (§21-§26/§31): realistic governed run — the fake engine returns a RAW report
    # whose isolation lives under PER_NOTEBOOK_ISOLATION_EVIDENCED (no synthetic top-level shortcut),
    # and the observer is driven over the full 72 query-arm records. Proves the governed live path
    # now emits the qa_value_observability block (the B3X defect is fixed).
    fx = load_fixture()
    captured = {}

    async def _fake_engine(**kwargs):
        captured["mint_fn"] = kwargs.get("mint_fn")
        observer = kwargs["qa_execution_observer"]
        captured["observer"] = observer
        _drive_all_72(observer, fx)
        return SimpleNamespace(report=_raw_report_with_isolation("YES"))

    monkeypatch.setattr(realseams, "run_live_b2_execution", _fake_engine)

    import asyncio
    outcome, artifact = asyncio.run(runner.run_live_b3_observability_execution(
        operator_grant=_b3_grant(),
        git_baseline_attestation=C.clean_git_baseline(),
        fx=fx,
    ))
    # B3B mint selected (not the B2 mint); observer is the B3 collector
    assert captured["mint_fn"] is mint_live_b3_provider_run_authorization
    assert isinstance(captured["observer"], runner.B3ObservabilityCollector)
    # distinct, content-safe fact-recall artifact
    assert artifact["report_kind"] == "PN02DB3_FACT_RECALL_OBSERVABILITY"
    assert artifact["mode"] == "OBSERVABILITY_ONLY"
    assert artifact["observation_run_id"] == OBS_RUN_ID
    assert artifact["reference_b2_run_id"] == runner.REFERENCE_B2_RUN_ID
    assert "SECRET" not in json.dumps(artifact) and "answer_text" not in json.dumps(artifact)
    # B3X-R1: the QA-value block IS emitted, complete, six dimensions + frozen decision present
    qv = cast(Any, artifact["qa_value_observability"])
    assert qv["version"] == 1
    assert qv["observed_pair_count"] == 72
    assert qv["observed_positive_count"] == 63
    assert qv["observed_negative_count"] == 9
    assert qv["qa_value_observability_complete"] is True
    arms = {m["arm"] for m in qv["arm_metrics"]}
    assert arms == {"QA-V", "QA-GD", "QA-V+GD"}  # per-arm QAArmMetrics (P1..S3)
    assert "qa_decision" in qv and "verdict" in qv["qa_decision"]  # frozen qa_decision reached
    assert "forbidden_fact_count" in qv and "forbidden_citation_count" not in qv
    # additively attached to the outcome report (distinct key)
    report = cast(Any, outcome.report)
    assert report["b3_observability"]["report_kind"] == "PN02DB3_FACT_RECALL_OBSERVABILITY"
    assert "qa_value_observability" in report["b3_observability"]


def test_governed_run_fails_closed_when_isolation_evidence_absent(monkeypatch):
    # PN02D-B3X-R1 (§19/§27): the governed live QA-value path must FAIL CLOSED (never silently omit
    # the QA-value block) when the raw report carries NO derivable isolation verdict.
    fx = load_fixture()

    async def _fake_engine(**kwargs):
        _drive_all_72(kwargs["qa_execution_observer"], fx)
        return SimpleNamespace(report={})  # no isolation evidence anywhere

    monkeypatch.setattr(realseams, "run_live_b2_execution", _fake_engine)
    import asyncio
    with pytest.raises(runner.B3ObservabilityError):
        asyncio.run(runner.run_live_b3_observability_execution(
            operator_grant=_b3_grant(), git_baseline_attestation=C.clean_git_baseline(), fx=fx))


def test_runner_isolation_derivation_from_raw_per_notebook_field():
    # PN02D-B3X-R1 (§41): the SOURCE-LEVEL defect — a raw report with ONLY
    # PER_NOTEBOOK_ISOLATION_EVIDENCED (the exact B3X shape, no top-level isolation_evidenced) must
    # now yield a non-None isolation bool so build_b3_observability_artifact emits the QA-value block.
    raw = _raw_report_with_isolation("YES")
    assert "isolation_evidenced" not in raw  # the field the B3X code searched for is ABSENT
    assert runner._extract_isolation_evidenced(raw) is True
    assert runner._extract_isolation_evidenced(_raw_report_with_isolation("NO")) is False
    assert runner._extract_isolation_evidenced(_raw_report_with_isolation("NOT_EVALUATED")) is None
    assert runner._extract_isolation_evidenced({}) is None


def test_runner_vs_cli_isolation_parity():
    # PN02D-B3X-R1 (§29): the runner-derived isolation must agree with the CLI projector's isolation
    # for the same raw report (both use PER_NOTEBOOK_ISOLATION_EVIDENCED == "YES").
    raw = _raw_report_with_isolation("YES")
    runner_bool = runner._extract_isolation_evidenced(raw)
    cli_val = cli._project_scientific_result(raw).get("isolation_evidenced")
    assert runner_bool is True and cli_val == "YES"  # same source, consistent semantics
    raw_no = _raw_report_with_isolation("NO")
    assert runner._extract_isolation_evidenced(raw_no) is False
    assert cli._project_scientific_result(raw_no).get("isolation_evidenced") == "NO"


def test_generic_builder_backward_compatible_without_isolation():
    # PN02D-B3X-R1 (§18/§43): the GENERIC builder stays backward-compatible — with no isolation
    # supplied it omits the optional QA-value block (does not fail). Only the governed live path is strict.
    fx = load_fixture()
    recs = [_good_record(fx, q, arm)
            for q in sorted(fx.queries, key=lambda x: x.query_id)
            for arm in (ArmId.QA_V, ArmId.QA_GD, ArmId.QA_VGD)]
    art = runner.build_b3_observability_artifact(
        fx, recs, observation_run_id="pn02db3-generic-compat")
    assert art["completeness"] == "COMPLETE"
    assert "qa_value_observability" not in art  # optional block omitted, backward-compatible
    # and when isolation IS supplied, the block appears
    art2 = runner.build_b3_observability_artifact(
        fx, recs, observation_run_id="pn02db3-generic-compat", isolation_evidenced=True)
    qv2 = cast(Any, art2["qa_value_observability"])
    assert qv2["qa_value_observability_complete"] is True


def test_governed_run_rejects_b2_run_id(monkeypatch):
    fx = load_fixture()

    async def _fake_engine(**kwargs):
        return SimpleNamespace(report={})

    monkeypatch.setattr(realseams, "run_live_b2_execution", _fake_engine)
    import asyncio
    with pytest.raises(runner.B3ObservabilityError):
        asyncio.run(runner.run_live_b3_observability_execution(
            operator_grant=_b3_grant(run_id=runner.REFERENCE_B2_RUN_ID),  # forbidden reuse
            git_baseline_attestation=C.clean_git_baseline(), fx=fx,
        ))


def test_generic_builder_partial_completeness_no_qa_value_block():
    # PN02D-B3X-R1 (§18/§43): the GENERIC builder still reports PARTIAL completeness for a partial
    # fact-recall capture (backward-compatible) and, with no isolation supplied, omits the optional
    # QA-value block — it does NOT crash. Only the GOVERNED live path is strict.
    fx = load_fixture()
    q = _first_positive(fx)
    rec = runner.B2QAExecutionRecord(
        query_id=q.query_id, notebook_id=q.notebook_id, arm=ArmId.QA_V,
        evidence_source_ids=(), result=QAAnswerResult(
            query_id=q.query_id, notebook_id=q.notebook_id, arm=ArmId.QA_V,
            abstained=True, answer_text="", citation_source_ids=()))
    artifact = runner.build_b3_observability_artifact(
        fx, [rec], observation_run_id="pn02db3-partial-generic")
    assert artifact["expected_pair_count"] == 72
    assert artifact["completed_diagnostic_pair_count"] == 1
    assert artifact["completeness"] == "PARTIAL"
    assert "qa_value_observability" not in artifact  # optional block omitted (no isolation)


def test_governed_run_fails_closed_on_partial_even_with_isolation(monkeypatch):
    # PN02D-B3X-R1 (§20): the governed live path must NOT emit a QA-value-complete artifact for a
    # partial run. Even WITH isolation evidence, a partial capture (<72) fails closed — the QA-value
    # projection requires the full 72 records, so no misleadingly-complete artifact can be produced.
    fx = load_fixture()
    q = _first_positive(fx)

    async def _fake_engine(**kwargs):
        kwargs["qa_execution_observer"](runner.B2QAExecutionRecord(
            query_id=q.query_id, notebook_id=q.notebook_id, arm=ArmId.QA_V,
            evidence_source_ids=(), result=QAAnswerResult(
                query_id=q.query_id, notebook_id=q.notebook_id, arm=ArmId.QA_V,
                abstained=True, answer_text="", citation_source_ids=())))
        return SimpleNamespace(report=_raw_report_with_isolation("YES"))  # isolation present, but partial

    monkeypatch.setattr(realseams, "run_live_b2_execution", _fake_engine)
    import asyncio
    with pytest.raises((runner.B3ObservabilityError, QAValueObservabilityError)):
        asyncio.run(runner.run_live_b3_observability_execution(
            operator_grant=_b3_grant(), git_baseline_attestation=C.clean_git_baseline(), fx=fx))


# --------------------------------------------------------------------------- #
# M1 (PN02DB3D-ER1-M1) — frozen B2 run-id rejection cannot be bypassed via the
# exported reference_b2_run_id override; rejected before provider binding.
# --------------------------------------------------------------------------- #


def test_m1_artifact_builder_reference_override_cannot_bypass_frozen_b2_run_id():
    """Direct-Python bypass reproduction: supply the CLOSED B2 run id as the observation
    id while OVERRIDING reference_b2_run_id to a different value. Must FAIL CLOSED, because
    the guard is checked against the immutable frozen REFERENCE_B2_RUN_ID constant."""
    fx = load_fixture()
    with pytest.raises(runner.B3ObservabilityError):
        runner.build_b3_observability_artifact(
            fx,
            [],
            observation_run_id=runner.REFERENCE_B2_RUN_ID,  # the real closed B2 run id
            reference_b2_run_id="pn02db3-not-the-frozen-reference",  # attacker override
        )


def test_m1_run_function_reference_override_cannot_bypass_before_provider(monkeypatch):
    """Same bypass through the exported run function: a different reference_b2_run_id must
    not disable the guard, and the closed B2 run id is rejected BEFORE the engine is
    reached (no provider binding)."""
    fx = load_fixture()
    engine_called = {"v": False}

    async def _fake_engine(**kwargs):
        engine_called["v"] = True
        return SimpleNamespace(report={})

    monkeypatch.setattr(realseams, "run_live_b2_execution", _fake_engine)
    import asyncio
    with pytest.raises(runner.B3ObservabilityError):
        asyncio.run(runner.run_live_b3_observability_execution(
            operator_grant=_b3_grant(run_id=runner.REFERENCE_B2_RUN_ID),
            git_baseline_attestation=C.clean_git_baseline(),
            reference_b2_run_id="pn02db3-not-the-frozen-reference",
            fx=fx,
        ))
    assert engine_called["v"] is False  # rejected before provider binding / execution


def test_m1_reference_metadata_is_frozen_constant():
    """The reference metadata field is pinned to the frozen constant; overriding it to a
    different value fails closed (it cannot be repurposed as caller-controlled metadata)."""
    assert runner.REFERENCE_B2_RUN_ID == "pn02db2-6469b191-db13-4d2f-864a-4079578efcf4"
    fx = load_fixture()
    with pytest.raises(runner.B3ObservabilityError):
        runner.build_b3_observability_artifact(
            fx, [], observation_run_id=OBS_RUN_ID,
            reference_b2_run_id="pn02db3-different-reference")
    # the default (frozen) path builds and stamps exactly the frozen reference
    art = runner.build_b3_observability_artifact(fx, [], observation_run_id=OBS_RUN_ID)
    assert art["reference_b2_run_id"] == runner.REFERENCE_B2_RUN_ID


# --------------------------------------------------------------------------- #
# L1 (PN02DB3D-ER1-L1) — the live B3 authorization is BOUND to the observation
# run id: a preflight prepared for run B cannot authorize a grant for run A.
# --------------------------------------------------------------------------- #


def _b3_preflight(run_id: str):
    from open_notebook.integrations.graphrag.eval.attestpn02d import (
        mint_real_preflight_authorization,
    )
    return mint_real_preflight_authorization(
        gate0_passed=True, gate1_passed=True,
        fixture_hash=EXPECTED_FIXTURE_HASH, run_id=run_id, runtime_count=3)


def test_l1_b3_auth_binds_observation_run_id():
    """Auth/profile data-flow proof (not artifact-builder validation): the B3B mint binds
    the minted capability to the operator grant's run id via the shared run-id cross-check.
    Matched run id mints; a preflight prepared for a DIFFERENT run cannot authorize it."""
    run_a = "pn02db3-obs-" + "a" * 28
    run_b = "pn02db3-obs-" + "b" * 28
    reader = C.b1r2_reader_ok(tag=EXPECTED_B3B_CHECKPOINT_TAG, peel=C.TEST_COMMIT, head=C.TEST_COMMIT)
    with _patch_b3(EXPECTED_B3B_CHECKPOINT_TAG, reader):
        # positive control: grant run A + preflight run A -> mints, capability bound to A
        auth = mint_live_b3_provider_run_authorization(
            operator_grant=_b3_grant(run_id=run_a, commit=C.TEST_COMMIT),
            real_preflight_auth=_b3_preflight(run_a),
            git_baseline_attestation=C.clean_git_baseline(commit=C.TEST_COMMIT, tag=C.TEST_TAG),
            observed_fixture_hash=EXPECTED_FIXTURE_HASH,
        )
        assert auth.run_id == run_a
        # negative: an authorization prepared for run B cannot authorize grant run A
        with pytest.raises(authmint.LiveProviderRunAuthorizationError):
            mint_live_b3_provider_run_authorization(
                operator_grant=_b3_grant(run_id=run_a, commit=C.TEST_COMMIT),
                real_preflight_auth=_b3_preflight(run_b),  # mismatched run id
                git_baseline_attestation=C.clean_git_baseline(commit=C.TEST_COMMIT, tag=C.TEST_TAG),
                observed_fixture_hash=EXPECTED_FIXTURE_HASH,
            )


# --------------------------------------------------------------------------- #
# PN02DB3D-ER1-H1 closure (PN02D-B3G) — a previously-replayable B3 authorization can no
# longer start a SECOND canonical real attempt: the shared durable one-shot ledger consumes
# the grant at the execution boundary, so the B3 path (via the shared driver) refuses a replay.
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_h1_closure_b3_authorization_cannot_start_second_attempt(tmp_path):
    from open_notebook.integrations.graphrag.eval import authledgerpn02d as L
    from open_notebook.integrations.graphrag.eval.driver_live_pn02d import RealB1Driver

    p = str(tmp_path / "live_auth_consumption.sqlite")
    fx = load_fixture()
    reader = C.b1r2_reader_ok(tag=EXPECTED_B3B_CHECKPOINT_TAG, peel=C.TEST_COMMIT, head=C.TEST_COMMIT)
    grant = frozen_b3b_operator_grant_template(
        run_id="pn02d-b3-h1", implementation_checkpoint_commit=C.TEST_COMMIT,
        implementation_checkpoint_tag=C.TEST_TAG, b1_r2_checkpoint=EXPECTED_B3B_CHECKPOINT_TAG,
        approved_git_commit=C.TEST_COMMIT, approved_git_tag=C.TEST_TAG,
    )

    # First canonical B3B attempt consumes the grant (reaches COMPLETE through the shared driver).
    bundle = C.build_live_seams(fx)
    with _patch_b3(EXPECTED_B3B_CHECKPOINT_TAG, reader):
        outcome = await RealB1Driver(
            fx, bundle.seams, execution_kind="B3B",
            mint_fn=mint_live_b3_provider_run_authorization, oneshot_ledger_path=p,
        ).run(operator_grant=grant, git_baseline_attestation=C.clean_git_baseline())
    assert outcome.state == "COMPLETE"

    # A SECOND canonical attempt with the same grant is refused BEFORE any provider-bound action.
    bundle2 = C.build_live_seams(fx)
    with _patch_b3(EXPECTED_B3B_CHECKPOINT_TAG, reader), pytest.raises(L.GrantAlreadyConsumedError):
        await RealB1Driver(
            fx, bundle2.seams, execution_kind="B3B",
            mint_fn=mint_live_b3_provider_run_authorization, oneshot_ledger_path=p,
        ).run(operator_grant=grant, git_baseline_attestation=C.clean_git_baseline())
    assert len(bundle2.controller.started) == 0  # replay never reached Boot 2


# --------------------------------------------------------------------------- #
# Default B2 engine unchanged
# --------------------------------------------------------------------------- #


def test_default_b2_engine_mint_and_observer_defaults_unchanged():
    # _b2_driver_kwargs default mint_fn is the B2 mint; observer default None
    kw = realseams._b2_driver_kwargs(cast(Any, object()))
    assert kw["mint_fn"] is realseams.mint_live_b2_provider_run_authorization
    import inspect
    sig = inspect.signature(realseams.run_live_b2_execution)
    assert sig.parameters["qa_execution_observer"].default is None
    assert sig.parameters["mint_fn"].default is realseams.mint_live_b2_provider_run_authorization


# --------------------------------------------------------------------------- #
# CLI verb
# --------------------------------------------------------------------------- #


def test_cli_verb_dispatches_to_b3_only():
    parser = cli.build_parser()
    ns = parser.parse_args(["execute-b3-observability-live", "--manifest", "x", "--authorize"])
    assert ns.func is cli.cmd_execute_b3_observability_live


def test_cli_b3_without_manifest_fails_closed():
    exit_code, payload = cli.evaluate_execute_b3_observability_live(
        manifest_path=None, explicit_authorize=False, env={})
    assert exit_code == 2
    assert payload["result"] == "REFUSED"
    assert payload["command"] == "execute-b3-observability-live"
    assert payload["provider_bound"] is False and payload["runtime_booted"] is False


def _write_b3_manifest(tmp_path, *, run_id=OBS_RUN_ID, b1_r2=EXPECTED_B3B_CHECKPOINT_TAG):
    from open_notebook.integrations.graphrag.eval.authmintlivepn02d import (
        B2_ALLOWED_OPERATION_VALUES,
        frozen_provider_config_id,
    )
    from open_notebook.integrations.graphrag.eval.budgetlivepn02d import b2_caps_dict

    manifest = {
        "run_id": run_id,
        "fixture_hash": EXPECTED_FIXTURE_HASH,
        # PN02D-B3Q-R1 (IR1-L1): the implementation checkpoint identity is the frozen B0C-B
        # baseline — DISTINCT from the B3 live-auth identity (b1_r2_checkpoint). Using the live-auth
        # tag here previously conflated the two (and would trip the impl==live equality refusal).
        "implementation_checkpoint_commit": APPROVED_IMPLEMENTATION_CHECKPOINT_COMMIT,
        "implementation_checkpoint_tag": APPROVED_IMPLEMENTATION_CHECKPOINT_TAG,
        "b1_r2_checkpoint": b1_r2,
        "provider_config_fingerprint": frozen_provider_config_id(),
        "workload_caps": b2_caps_dict(),
        "operation_allowlist": sorted(B2_ALLOWED_OPERATION_VALUES),
        "approved_git_commit": "3eaf28bcce98a1b6c34afd04e18aa506f65e7f83",
        "approved_git_tag": EXPECTED_B3B_CHECKPOINT_TAG,
        "synthetic_only": True,
        "real_internal_data_allowed": False,
    }
    p = tmp_path / "b3_manifest.json"
    p.write_text(json.dumps(manifest), encoding="utf-8")
    return str(p)


def test_b3_manifest_identity_separation_and_no_equality_refusal(tmp_path):
    # PN02D-B3X-R3A (tri-state lifecycle-invariant): a correctly-built B3 manifest keeps the
    # implementation checkpoint identity (frozen B0C-B baseline) DISTINCT from the current B3 live-auth
    # identity (derived from EXPECTED_B3_LIVE_CHECKPOINT_TAG). The identity-separation invariant holds in
    # EVERY lifecycle state and is asserted UNCONDITIONALLY; the trust/refusal outcome is
    # lifecycle-DEPENDENT and is asserted per the independently-classified current state (so this test
    # never re-stales when a governed transition moves HEAD relative to the live tag).
    manifest_path = _write_b3_manifest(tmp_path, b1_r2=EXPECTED_B3_LIVE_CHECKPOINT_TAG)
    with open(manifest_path, encoding="utf-8") as fh:
        m = json.loads(fh.read())
    # --- ALWAYS-INVARIANT: identity separation (never lifecycle-dependent) ---
    assert m["implementation_checkpoint_tag"] == "graphrag-pn02db0cb-real-provider-wiring-approved"
    assert m["implementation_checkpoint_commit"] == "5abeaaa09b7157232b1ac5a234c9d8c50b542585"
    assert m["b1_r2_checkpoint"] == EXPECTED_B3_LIVE_CHECKPOINT_TAG
    assert m["implementation_checkpoint_tag"] != m["b1_r2_checkpoint"]
    assert m["implementation_checkpoint_commit"] != m["b1_r2_checkpoint"]
    baseline = cli.read_git_baseline()
    reasons = b3b_r2_refusal_reasons(cli.parse_operator_grant(m), baseline)
    # identity separation holds in all states: the impl==live equality refusals are never raised
    assert "b1_r2_identity_equals_implementation_checkpoint_tag" not in reasons
    assert "b1_r2_identity_equals_implementation_checkpoint_commit" not in reasons
    # --- LIFECYCLE-DEPENDENT: trust outcome per independently-classified current state ---
    reader = authmint._build_trusted_b1_r2_reader()
    state = _classify_live_tag_lifecycle(reader, EXPECTED_B3_LIVE_CHECKPOINT_TAG, baseline.head_commit)
    if state == "TAG_ABSENT":
        assert "b1_r2_tag_not_observed_in_git" in reasons
    elif state == "EXACT_HEAD":
        assert "b1_r2_tag_not_observed_in_git" not in reasons
        assert "b1_r2_tag_not_at_authorized_head" not in reasons
        assert reasons == []  # empty refusals required ONLY in exact-head state
    elif state == "ANCESTOR_STALE":
        assert "b1_r2_tag_not_at_authorized_head" in reasons
    else:  # pragma: no cover
        pytest.fail(f"unexpected lifecycle state {state!r}")


# --------------------------------------------------------------------------- #
# PN02D-B3X-R3A: tri-state classifier coverage + lifecycle transition/manifest matrices
# --------------------------------------------------------------------------- #

_ABSENT_LIVE_TAG = "graphrag-pn02db3xrX-DEFINITELY-ABSENT"


def test_lifecycle_classifier_tri_state_coverage():
    # §36: the test-only classifier maps each physical relationship to the right lifecycle state and
    # fails EXPLICITLY on an unrecognized one (never silently normalized). Ancestry is injected so the
    # classifier itself is exercised deterministically without depending on real Git history.
    T = EXPECTED_B3B_CHECKPOINT_TAG
    # missing tag -> TAG_ABSENT
    r_absent = RealTrustedB1R2Reader(git_runner=C.b1r2_git_runner(tag=T, exists=False, peel="", head=C.TEST_COMMIT))
    assert _classify_live_tag_lifecycle(r_absent, T, C.TEST_COMMIT, is_ancestor=lambda a, b: False) == "TAG_ABSENT"
    # peel == HEAD -> EXACT_HEAD
    r_exact = RealTrustedB1R2Reader(git_runner=C.b1r2_git_runner(tag=T, exists=True, peel=C.TEST_COMMIT, head=C.TEST_COMMIT))
    assert _classify_live_tag_lifecycle(r_exact, T, C.TEST_COMMIT, is_ancestor=lambda a, b: False) == "EXACT_HEAD"
    # peel is a (true) ancestor of HEAD -> ANCESTOR_STALE
    r_anc = RealTrustedB1R2Reader(git_runner=C.b1r2_git_runner(tag=T, exists=True, peel=ANCESTOR, head=SUCCESSOR))
    assert _classify_live_tag_lifecycle(r_anc, T, SUCCESSOR, is_ancestor=lambda a, b: True) == "ANCESTOR_STALE"
    # peel neither HEAD nor ancestor -> explicit failure (no silent normalization)
    r_bad = RealTrustedB1R2Reader(git_runner=C.b1r2_git_runner(tag=T, exists=True, peel="dead" + "0" * 36, head=C.TEST_COMMIT))
    with pytest.raises(AssertionError):
        _classify_live_tag_lifecycle(r_bad, T, C.TEST_COMMIT, is_ancestor=lambda a, b: False)


def test_lifecycle_transition_matrix():
    # §42: the full governed lifecycle — EXACT_HEAD (current / successor checkpoint), ANCESTOR_STALE
    # (implementation checkpoint advanced HEAD), TAG_ABSENT (successor predeclared, tag not yet created)
    # — each classified independently AND run through the shared production trust path to the expected
    # outcome. Proves a single unedited test suite survives every legitimate transition.
    T = EXPECTED_B3B_CHECKPOINT_TAG
    # EXACT_HEAD -> PASS
    r = C.b1r2_reader_ok(tag=T, peel=C.TEST_COMMIT, head=C.TEST_COMMIT)
    with _patch_b3(T, r):
        assert _classify_live_tag_lifecycle(r, T, C.TEST_COMMIT, is_ancestor=lambda a, b: False) == "EXACT_HEAD"
        assert b3b_r2_refusal_reasons(_b3_grant(commit=C.TEST_COMMIT), C.clean_git_baseline(commit=C.TEST_COMMIT, tag=C.TEST_TAG)) == []
    # ANCESTOR_STALE -> FAIL_CLOSED (stale successor)
    r = C.b1r2_reader_ok(tag=T, peel=ANCESTOR, head=SUCCESSOR)
    with _patch_b3(T, r):
        assert _classify_live_tag_lifecycle(r, T, SUCCESSOR, is_ancestor=lambda a, b: True) == "ANCESTOR_STALE"
        assert "b1_r2_tag_not_at_authorized_head" in b3b_r2_refusal_reasons(_b3_grant(commit=SUCCESSOR), C.clean_git_baseline(commit=SUCCESSOR, tag=C.TEST_TAG))
    # TAG_ABSENT -> FAIL_CLOSED (tag not observed)
    r = RealTrustedB1R2Reader(git_runner=C.b1r2_git_runner(tag=_ABSENT_LIVE_TAG, exists=False, peel="", head=C.TEST_COMMIT))
    with _patch_b3(_ABSENT_LIVE_TAG, r):
        assert _classify_live_tag_lifecycle(r, _ABSENT_LIVE_TAG, C.TEST_COMMIT, is_ancestor=lambda a, b: False) == "TAG_ABSENT"
        assert "b1_r2_tag_not_observed_in_git" in b3b_r2_refusal_reasons(_b3_grant(b1_r2=_ABSENT_LIVE_TAG, commit=C.TEST_COMMIT), C.clean_git_baseline(commit=C.TEST_COMMIT, tag=C.TEST_TAG))


def _manifest_grant(*, live_tag, commit):
    # a B3 grant with the FROZEN B0C-B implementation identity + the given live-auth identity
    return frozen_b3b_operator_grant_template(
        run_id=OBS_RUN_ID,
        implementation_checkpoint_commit="5abeaaa09b7157232b1ac5a234c9d8c50b542585",
        implementation_checkpoint_tag="graphrag-pn02db0cb-real-provider-wiring-approved",
        b1_r2_checkpoint=live_tag,
        approved_git_commit=commit,
        approved_git_tag=C.TEST_TAG,
    )


def test_manifest_lifecycle_matrix():
    # §43: manifest identity-separation is invariant across ALL three lifecycle states (the impl==live
    # equality refusals never fire), while the trust outcome is lifecycle-dependent. Confirms B0C-B stays
    # the implementation identity and the current successor stays the live identity, never conflated.
    T = EXPECTED_B3B_CHECKPOINT_TAG
    # EXACT_HEAD
    r = C.b1r2_reader_ok(tag=T, peel=C.TEST_COMMIT, head=C.TEST_COMMIT)
    with _patch_b3(T, r):
        reasons = b3b_r2_refusal_reasons(_manifest_grant(live_tag=T, commit=C.TEST_COMMIT), C.clean_git_baseline(commit=C.TEST_COMMIT, tag=C.TEST_TAG))
    assert "b1_r2_identity_equals_implementation_checkpoint_tag" not in reasons
    assert "b1_r2_identity_equals_implementation_checkpoint_commit" not in reasons
    assert reasons == []
    # ANCESTOR_STALE
    r = C.b1r2_reader_ok(tag=T, peel=ANCESTOR, head=SUCCESSOR)
    with _patch_b3(T, r):
        reasons = b3b_r2_refusal_reasons(_manifest_grant(live_tag=T, commit=SUCCESSOR), C.clean_git_baseline(commit=SUCCESSOR, tag=C.TEST_TAG))
    assert "b1_r2_identity_equals_implementation_checkpoint_tag" not in reasons
    assert "b1_r2_identity_equals_implementation_checkpoint_commit" not in reasons
    assert "b1_r2_tag_not_at_authorized_head" in reasons
    # TAG_ABSENT
    r = RealTrustedB1R2Reader(git_runner=C.b1r2_git_runner(tag=_ABSENT_LIVE_TAG, exists=False, peel="", head=C.TEST_COMMIT))
    with _patch_b3(_ABSENT_LIVE_TAG, r):
        reasons = b3b_r2_refusal_reasons(_manifest_grant(live_tag=_ABSENT_LIVE_TAG, commit=C.TEST_COMMIT), C.clean_git_baseline(commit=C.TEST_COMMIT, tag=C.TEST_TAG))
    assert "b1_r2_identity_equals_implementation_checkpoint_tag" not in reasons
    assert "b1_r2_identity_equals_implementation_checkpoint_commit" not in reasons
    assert "b1_r2_tag_not_observed_in_git" in reasons


def test_cli_b3_without_operator_auth_fails_closed_before_boot(tmp_path):
    # A well-formed B3B manifest but NO governance token / --authorize -> REFUSED, never boots.
    manifest = _write_b3_manifest(tmp_path)
    exit_code, payload = cli.evaluate_execute_b3_observability_live(
        manifest_path=manifest, explicit_authorize=False, env={})
    assert payload["result"] == "REFUSED"
    assert payload["command"] == "execute-b3-observability-live"
    assert payload["provider_bound"] is False and payload["runtime_booted"] is False
    assert exit_code in (2, 3)  # validation or governance gate — both fail closed pre-boot
