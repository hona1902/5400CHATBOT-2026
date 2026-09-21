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
from types import SimpleNamespace
from typing import Any, cast
from unittest import mock

import graphrag_pn02db0cb_common as C
import pytest

import open_notebook.integrations.graphrag.eval.authmintlivepn02d as authmint
from open_notebook.integrations.graphrag.eval import cli_live_pn02d as cli
from open_notebook.integrations.graphrag.eval import p1diagrunnerpn02db3 as runner
from open_notebook.integrations.graphrag.eval import realseamsb2pn02d as realseams
from open_notebook.integrations.graphrag.eval.authmintlivepn02d import (
    EXPECTED_B2_CHECKPOINT_TAG,
    EXPECTED_B3B_CHECKPOINT_TAG,
    EXPECTED_FIXTURE_HASH,
    RealTrustedB1R2Reader,
    b2_r2_refusal_reasons,
    b3b_r2_refusal_reasons,
    current_approved_b3b_checkpoint,
    frozen_b2_operator_grant_template,
    frozen_b3b_operator_grant_template,
    mint_live_b3_provider_run_authorization,
)
from open_notebook.integrations.graphrag.eval.datasetpn02 import load_fixture
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


def test_b3b_identity_distinct_and_resolves():
    assert EXPECTED_B3B_CHECKPOINT_TAG == "graphrag-pn02db3b-live-observability-wiring-approved"
    assert current_approved_b3b_checkpoint() == EXPECTED_B3B_CHECKPOINT_TAG
    assert EXPECTED_B3B_CHECKPOINT_TAG != EXPECTED_B2_CHECKPOINT_TAG


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


def test_governed_run_injects_b3b_mint_and_observer_and_builds_distinct_artifact(monkeypatch):
    fx = load_fixture()
    q = _first_positive(fx)
    captured = {}

    async def _fake_engine(**kwargs):
        # record the injected mint + observer, then drive the observer like the real stage would
        captured["mint_fn"] = kwargs.get("mint_fn")
        observer = kwargs["qa_execution_observer"]
        captured["observer"] = observer
        for arm in (ArmId.QA_V, ArmId.QA_GD, ArmId.QA_VGD):
            observer(runner.B2QAExecutionRecord(
                query_id=q.query_id, notebook_id=q.notebook_id, arm=arm,
                evidence_source_ids=tuple(q.required_source_ids),
                result=QAAnswerResult(
                    query_id=q.query_id, notebook_id=q.notebook_id, arm=arm,
                    abstained=False, answer_text=" ".join(q.expected_answer_facts),
                    citation_source_ids=()),
            ))
        return SimpleNamespace(report={})

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
    # distinct, content-safe artifact
    assert artifact["report_kind"] == "PN02DB3_FACT_RECALL_OBSERVABILITY"
    assert artifact["mode"] == "OBSERVABILITY_ONLY"
    assert artifact["observation_run_id"] == OBS_RUN_ID
    assert artifact["reference_b2_run_id"] == runner.REFERENCE_B2_RUN_ID
    assert "SECRET" not in json.dumps(artifact) and "answer_text" not in json.dumps(artifact)
    # additively attached to the outcome report (distinct key)
    report = cast(Any, outcome.report)
    assert report["b3_observability"]["report_kind"] == "PN02DB3_FACT_RECALL_OBSERVABILITY"


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


def test_governed_run_partial_completeness(monkeypatch):
    fx = load_fixture()
    q = _first_positive(fx)

    async def _fake_engine(**kwargs):
        kwargs["qa_execution_observer"](runner.B2QAExecutionRecord(
            query_id=q.query_id, notebook_id=q.notebook_id, arm=ArmId.QA_V,
            evidence_source_ids=(), result=QAAnswerResult(
                query_id=q.query_id, notebook_id=q.notebook_id, arm=ArmId.QA_V,
                abstained=True, answer_text="", citation_source_ids=())))
        return SimpleNamespace(report={})

    monkeypatch.setattr(realseams, "run_live_b2_execution", _fake_engine)
    import asyncio
    _outcome, artifact = asyncio.run(runner.run_live_b3_observability_execution(
        operator_grant=_b3_grant(), git_baseline_attestation=C.clean_git_baseline(), fx=fx))
    assert artifact["expected_pair_count"] == 72
    assert artifact["completed_diagnostic_pair_count"] == 1
    assert artifact["completeness"] == "PARTIAL"


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
        "implementation_checkpoint_commit": "3eaf28b",
        "implementation_checkpoint_tag": EXPECTED_B3B_CHECKPOINT_TAG,
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


def test_cli_b3_without_operator_auth_fails_closed_before_boot(tmp_path):
    # A well-formed B3B manifest but NO governance token / --authorize -> REFUSED, never boots.
    manifest = _write_b3_manifest(tmp_path)
    exit_code, payload = cli.evaluate_execute_b3_observability_live(
        manifest_path=manifest, explicit_authorize=False, env={})
    assert payload["result"] == "REFUSED"
    assert payload["command"] == "execute-b3-observability-live"
    assert payload["provider_bound"] is False and payload["runtime_booted"] is False
    assert exit_code in (2, 3)  # validation or governance gate — both fail closed pre-boot
