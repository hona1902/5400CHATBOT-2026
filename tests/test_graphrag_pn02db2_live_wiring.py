"""PN02D-B2 — live QA wiring (mechanical resume) provider-free tests.

EVALUATION-ONLY. Exercises the REAL B2 driver/CLI/projection against mocked external
boundaries only: no network, no secret, no real provider auth. Proves the B2 CLI fails
closed today (B2 checkpoint tag absent), that under a simulated future B2 approval the QA
answers reach the EXISTING evaluator (qa_not_supplied gone), that the content-safe
projection leaks no answer/question/context/source/completion text, and that final-answer
provider errors surface as safe structured fields.
"""

from __future__ import annotations

import json
from unittest import mock

import graphrag_pn02db0cb_common as C
import pytest

from open_notebook.integrations.graphrag.eval import authmintlivepn02d as authmint
from open_notebook.integrations.graphrag.eval import cli_live_pn02d as cli
from open_notebook.integrations.graphrag.eval.authmintlivepn02d import (
    B2_ALLOWED_OPERATION_VALUES,
    EXPECTED_B2_CHECKPOINT_TAG,
    b2_r2_refusal_reasons,
    frozen_b2_operator_grant_template,
)
from open_notebook.integrations.graphrag.eval.budgetlivepn02d import b2_caps_dict
from open_notebook.integrations.graphrag.eval.driverb2pn02d import RealB2Driver
from open_notebook.integrations.graphrag.eval.real_final_answer_seam_pn02d import (
    FinalAnswerProviderError,
    RealFinalAnswerSeam,
)


def _b2_grant(commit: str = C.TEST_COMMIT):
    return frozen_b2_operator_grant_template(
        run_id="pn02db2-6469b191-db13-4d2f-864a-4079578efcf4",
        implementation_checkpoint_commit=commit,
        implementation_checkpoint_tag=C.TEST_TAG,
        b1_r2_checkpoint=EXPECTED_B2_CHECKPOINT_TAG,
        approved_git_commit=commit,
        approved_git_tag=C.TEST_TAG,
    )


def _b2_state_b(*, head: str = C.TEST_COMMIT):
    reader = C.b1r2_reader_ok(tag=EXPECTED_B2_CHECKPOINT_TAG, peel=head, head=head)

    class _MP:
        def __enter__(self):
            self._a = mock.patch.object(
                authmint, "current_approved_b2_checkpoint",
                return_value=EXPECTED_B2_CHECKPOINT_TAG,
            )
            self._b = mock.patch.object(
                authmint, "_build_trusted_b1_r2_reader", return_value=reader
            )
            self._a.__enter__()
            self._b.__enter__()
            return self

        def __exit__(self, *exc):
            self._b.__exit__(*exc)
            self._a.__exit__(*exc)
            return False

    return _MP()


# --------------------------------------------------------------------------- #
# CLI fails closed today (B2 checkpoint tag absent)
# --------------------------------------------------------------------------- #


def test_execute_b2_live_fails_closed_before_b2_checkpoint(tmp_path):
    # Isolate the B2-checkpoint reason with a CLEAN baseline reader + a runner sentinel that
    # explodes if ever called — proving no mint / no provider use happens.
    grant = _b2_grant()
    man = tmp_path / "b2.json"
    man.write_text(json.dumps(_grant_to_manifest(grant)), encoding="utf-8")

    def _boom(**_kw):  # must never be reached
        raise AssertionError("live_runner must NOT run when the B2 checkpoint is absent")

    code, payload = cli._evaluate_execute_b1_live_composed(
        manifest_path=str(man),
        explicit_authorize=True,
        env={"PN02_PROVIDER_RUN_AUTHORIZED": "YES", "OPENROUTER_API_KEY": "x"},
        git_baseline_reader=lambda: C.clean_git_baseline(),
        live_runner=_boom,
        command="execute-b2-live",
        refusal_fn=b2_r2_refusal_reasons,
        allowlist=B2_ALLOWED_OPERATION_VALUES,
        expected_caps_dict=b2_caps_dict,
        projector=cli._project_b2_scientific_result,
    )
    assert code == 2
    assert payload["command"] == "execute-b2-live"
    assert payload["result"] == "REFUSED"
    assert "b1_r2_tag_not_observed_in_git" in payload["reasons"]
    assert payload["runtime_booted"] is False
    assert payload["provider_bound"] is False


def test_evaluate_execute_b2_live_public_refuses_today(tmp_path):
    # The PUBLIC B2 evaluator (real readers) also refuses today (B2 tag absent), no boot.
    grant = _b2_grant()
    man = tmp_path / "b2.json"
    man.write_text(json.dumps(_grant_to_manifest(grant)), encoding="utf-8")
    code, payload = cli.evaluate_execute_b2_live(
        manifest_path=str(man),
        explicit_authorize=True,
        env={"PN02_PROVIDER_RUN_AUTHORIZED": "YES", "OPENROUTER_API_KEY": "x"},
    )
    assert code == 2 and payload["result"] == "REFUSED"
    assert "b1_r2_tag_not_observed_in_git" in payload["reasons"]
    assert payload["runtime_booted"] is False


# --------------------------------------------------------------------------- #
# Provider-free live harness — qa_results reach the EXISTING evaluator
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_b2_live_harness_qa_results_reach_existing_evaluator():
    fx = C.fixture()
    bundle = C.build_live_seams(fx)
    calls = {"n": 0}

    async def _fake(prompt: str) -> str:
        calls["n"] += 1
        return "The answer.\nCITATIONS: \nABSTAIN: NO"

    driver = RealB2Driver(fx, bundle.seams, completion_fn=_fake)
    with _b2_state_b():
        outcome = await driver.run(
            operator_grant=_b2_grant(),
            git_baseline_attestation=C.clean_git_baseline(),
        )
    assert outcome.state == "COMPLETE"
    assert outcome.evaluation is not None
    # qa_results reached the evaluator: the NOT_EVALUATED/qa_not_supplied state is GONE.
    assert "qa_not_supplied" not in outcome.evaluation.scientific.notes
    assert (
        outcome.evaluation.scientific.per_notebook_graph_qa_value_evidenced.value
        != "NOT_EVALUATED"
    )
    # 24 queries × 3 arms = 72 final-answer calls (the frozen plan), no retry inflation.
    assert calls["n"] == 72

    # PN02DB2-R1-H1: the canonical report now carries the evaluator's QA block, and the
    # content-safe projection surfaces a POPULATED verdict + arms (key-presence is NOT enough).
    assert isinstance(outcome.report, dict) and isinstance(outcome.report.get("qa"), dict)
    projected = cli._project_b2_scientific_result(outcome.report)
    assert projected["qa_value_verdict"] is not None
    assert projected["qa_value_verdict"] in {"YES", "NO", "INCONCLUSIVE", "NOT_EVALUATED"}
    assert projected["qa_decision_rule"] is not None
    arms = projected["qa_arms"]
    assert isinstance(arms, list) and arms  # non-empty
    arm_ids = {a.get("arm") for a in arms}
    # baseline QA-V + graph arms QA-GD and QA-V+GD are all represented.
    assert {"QA-V", "QA-GD", "QA-V+GD"} <= arm_ids
    # AUTHORITATIVE B2 final-answer spend (from the B2QAStage budget, NOT the B1 ledger),
    # with cap kept distinct from spend.
    assert projected["final_answer_calls"] == 72
    assert projected["final_answer_cap"] == 72


@pytest.mark.asyncio
async def test_b2_projection_is_content_safe_with_sentinels():
    fx = C.fixture()
    bundle = C.build_live_seams(fx)

    async def _sentinel(prompt: str) -> str:
        # answer text carries sentinels that must NEVER reach the safe projection/JSON.
        return "DO_NOT_SERIALIZE_ANSWER body\nCITATIONS: DO_NOT_SERIALIZE_SOURCE\nABSTAIN: NO"

    driver = RealB2Driver(fx, bundle.seams, completion_fn=_sentinel)
    with _b2_state_b():
        outcome = await driver.run(
            operator_grant=_b2_grant(),
            git_baseline_attestation=C.clean_git_baseline(),
        )
    projected = cli._project_b2_scientific_result(outcome.report)
    blob = json.dumps(projected, sort_keys=True)
    for sentinel in (
        "DO_NOT_SERIALIZE_ANSWER", "DO_NOT_SERIALIZE_SOURCE",
        "DO_NOT_SERIALIZE_QUESTION", "DO_NOT_SERIALIZE_CONTEXT",
    ):
        assert sentinel not in blob
    # Three-layer result present, no synthetic global pass key.
    assert projected["isolation_hard_gate"] in {"PASS", "FAIL", "NOT_EVALUATED"}
    # PN02DB2-R1-H1: populated (not merely present) — a completed B2 run has a real verdict.
    assert projected["qa_value_verdict"] is not None
    assert projected["qa_arms"]  # non-empty
    assert "overall_pass" not in projected and "b2_pass" not in projected


# --------------------------------------------------------------------------- #
# PN02DB2-RR2-M1 — partial failure reports COMPLETED answers, not reserved 72
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("fail_at", [1, 72])
@pytest.mark.asyncio
async def test_b2_partial_failure_reports_completed_not_reserved(fail_at):
    fx = C.fixture()
    bundle = C.build_live_seams(fx)
    calls = {"n": 0}

    async def _fail_at(prompt: str) -> str:
        calls["n"] += 1
        if calls["n"] == fail_at:
            raise RuntimeError("synthetic final-answer provider failure")
        return "The answer.\nCITATIONS: \nABSTAIN: NO"

    driver = RealB2Driver(fx, bundle.seams, completion_fn=_fail_at)
    with _b2_state_b():
        outcome = await driver.run(
            operator_grant=_b2_grant(),
            git_baseline_attestation=C.clean_git_baseline(),
        )
    # Technical FAILURE; ZERO retry — the failing call is not repeated (seam hit exactly fail_at).
    assert outcome.state == "FAILED"
    assert calls["n"] == fail_at
    # RESERVED (budget) counts the failing attempt; COMPLETED counts only successful appends.
    b2fa = outcome.report["b2_final_answer"]
    assert b2fa["reserved_attempts"] == fail_at
    assert b2fa["completed_answers"] == fail_at - 1
    assert b2fa["spent"] == fail_at - 1  # spent == completed, NOT reserved/planned 72
    assert b2fa["cap"] == 72
    # The content-safe projection reports the ACTUAL completed count (never inflated to 72).
    projected = cli._project_b2_scientific_result(outcome.report)
    assert projected["final_answer_calls"] == fail_at - 1
    assert projected["final_answer_reserved_attempts"] == fail_at
    assert projected["final_answer_cap"] == 72
    # No completed QA scientific verdict is fabricated on a failed run.
    assert "qa" not in outcome.report
    assert projected["qa_value_verdict"] is None


# --------------------------------------------------------------------------- #
# final-answer error path is secret-safe
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_final_answer_error_is_secret_safe():
    async def _raise(prompt: str) -> str:
        raise RuntimeError("Authorization: Bearer sk-or-SECRETVALUE 401 unauthorized")

    seam = RealFinalAnswerSeam(completion_fn=_raise)
    with pytest.raises(FinalAnswerProviderError) as ei:
        await seam.answer("NB_A", "q?", ["A1"])
    msg = str(ei.value)
    assert "sk-or-SECRETVALUE" not in msg
    assert "Bearer" not in msg
    assert "Authorization" not in msg


# --------------------------------------------------------------------------- #
# B1 regression — execute-b1-live path unchanged
# --------------------------------------------------------------------------- #


def test_execute_b1_live_still_refuses_today_and_labels_b1(tmp_path):
    grant = C.frozen_test_grant()
    man = tmp_path / "b1.json"
    man.write_text(json.dumps(_grant_to_manifest(grant)), encoding="utf-8")
    code, payload = cli.evaluate_execute_b1_live(
        manifest_path=str(man),
        explicit_authorize=True,
        env={"PN02_PROVIDER_RUN_AUTHORIZED": "YES", "OPENROUTER_API_KEY": "x"},
    )
    assert payload["command"] == "execute-b1-live"
    assert payload["result"] == "REFUSED"  # EW8 tag not at this dirty HEAD


def _grant_to_manifest(g) -> dict:
    return {
        "run_id": g.run_id,
        "fixture_hash": g.fixture_hash,
        "implementation_checkpoint_commit": g.implementation_checkpoint_commit,
        "implementation_checkpoint_tag": g.implementation_checkpoint_tag,
        "b1_r2_checkpoint": g.b1_r2_checkpoint,
        "provider_config_fingerprint": g.provider_config_fingerprint,
        "workload_caps": dict(g.workload_caps),
        "operation_allowlist": sorted(g.operation_allowlist),
        "approved_git_commit": g.approved_git_commit,
        "approved_git_tag": g.approved_git_tag,
        "synthetic_only": g.synthetic_only,
        "real_internal_data_allowed": g.real_internal_data_allowed,
    }
