"""PN02D POST-B3Y-R3 — durable, atomic, content-safe result-artifact retention tests.

Provider-free. NO real provider calls, NO real one-shot-ledger mutation, NO real secrets.
Every artifact is a synthetic temporary artifact under ``tmp_path`` — the historical B3Y-R3
result is NEVER reconstructed or backfilled.

Covers the retention contract that closes ``RESULT_ARTIFACT_PERSISTENCE_GAP``:
successful atomic finalization; the preclaim readiness guard preventing a grant burn;
stdout truncation not affecting the durable artifact; postclaim failure artifacts; a
postclaim persistence failure never being reported as COMPLETE (no retry); content-safe
secret exclusion; duplicate-run-id refusal; crash/temp recovery state; identity-mismatch
fail-closed; and completeness-failure rejection.
"""

from __future__ import annotations

import json
import os
from typing import Any, cast
from unittest import mock

import graphrag_pn02db0cb_common as C
import pytest

import open_notebook.integrations.graphrag.eval.result_artifact_pn02d as R
from open_notebook.integrations.graphrag.eval.authmintlivepn02d import (
    EXPECTED_B3B_CHECKPOINT_TAG,
    frozen_b3b_operator_grant_template,
    mint_live_b3_provider_run_authorization,
)
from open_notebook.integrations.graphrag.eval.datasetpn02 import load_fixture
from open_notebook.integrations.graphrag.eval.driver_live_pn02d import RealB1Driver

_FORBIDDEN_TOKENS = ("sk-live-", "sk-", "secret-token", "prompt:", "raw answer")


def _identity(run_id: str = "rid-test", *, treatment: bool = True) -> R.ResultArtifactIdentity:
    return R.ResultArtifactIdentity(
        run_id=run_id,
        head_commit="406181b47d35623e2e0e8d54e795a9c6266d5b01",
        live_auth_tag="graphrag-pn02db3yr1-b3-live-auth-successor-approved",
        implementation_checkpoint_commit="5abeaaa09b7157232b1ac5a234c9d8c50b542585",
        implementation_checkpoint_tag="graphrag-pn02db0cb-real-provider-wiring-approved",
        fixture_hash="9ce7df742810424d9ef7b7b34962187d9061245f81471f5fb1d7f2608f6899a6",
        treatment=treatment,
        execution_kind="B3",
    )


def _complete_payload() -> dict:
    """A synthetic content-safe COMPLETE B3 payload (ids/labels/counts only)."""
    return {
        "result": "COMPLETE",
        "provider_traffic": 158,
        "technical_status": "COMPLETED",
        "index_metrics": {"indexed": 24, "expected": 24},
        "scientific_result": {
            "scientific_outputs": {"PER_NOTEBOOK_ISOLATION_EVIDENCED": "YES"},
            "query_embedding_attempts": 26,
            "gd_calls": 26,
            "vector_queries": 26,
            "b3_observability": {
                "report_kind": "PN02_B3_FACT_RECALL_OBSERVABILITY",
                "completeness": "COMPLETE",
                "diagnostics": {
                    "aggregate": {"ok_count": 41, "positive_query_arm_count": 21},
                    "per_query_arm": [],
                },
                "qa_value_observability": {
                    "qa_value_observability_complete": True,
                    "qa_decision": {"rule": "Q1", "verdict": "YES"},
                },
            },
        },
    }


# --------------------------------------------------------------------------- #
# §46 — successful atomic finalization
# --------------------------------------------------------------------------- #
def test_successful_atomic_result_finalization(tmp_path):
    ident = _identity("rid-ok")
    written = R.finalize_result_artifact(
        status=R.STATUS_COMPLETED,
        command="execute-b3-observability-live",
        identity=ident,
        payload=_complete_payload(),
        base_dir=str(tmp_path),
    )
    # finalized file present + parseable + verifies (identity + completeness + hash).
    assert written.path.name == "result.json"
    artifact: Any = R.read_result_artifact(written.path)
    R.verify_artifact(artifact, expected_identity=ident)
    assert artifact["status"] == R.STATUS_COMPLETED
    assert artifact["claimed"] is True
    assert artifact["artifact_schema_version"] == R.RESULT_ARTIFACT_SCHEMA_VERSION
    assert artifact["artifact_sha256"] == written.sha256
    # success marker written ONLY after finalization; no leftover .tmp.
    assert written.success_marker is not None and written.success_marker.exists()
    run_dir = R.run_artifact_dir("rid-ok", base_dir=str(tmp_path))
    assert not any(p.name.endswith(".tmp") for p in run_dir.iterdir())
    # the scientific aggregates + provider accounting are durably retained.
    sci = artifact["result"]["scientific_result"]
    assert sci["b3_observability"]["diagnostics"]["aggregate"]["ok_count"] == 41
    assert sci["b3_observability"]["qa_value_observability"]["qa_decision"]["verdict"] == "YES"
    assert artifact["provider_traffic"] == 158


def test_readiness_ok_probe_is_real_storage_and_cleans_up(tmp_path):
    report = R.assert_result_retention_readiness("rid-probe", base_dir=str(tmp_path))
    assert report.status == "OK"
    assert "atomic_link_create_compatible" in report.checks_passed
    # a real probe was performed then cleaned up: no probe temp files linger.
    run_dir = R.run_artifact_dir("rid-probe", base_dir=str(tmp_path))
    assert not any(p.name.endswith(".tmp") for p in run_dir.iterdir())


# --------------------------------------------------------------------------- #
# §48 — stdout truncation does not affect the durable artifact
# --------------------------------------------------------------------------- #
def test_stdout_truncation_does_not_affect_artifact(tmp_path):
    ident = _identity("rid-trunc")
    written = R.finalize_result_artifact(
        status=R.STATUS_COMPLETED,
        command="execute-b3-observability-live",
        identity=ident,
        payload=_complete_payload(),
        base_dir=str(tmp_path),
    )
    # Simulate an operator who only kept the LAST N bytes of stdout (the B3Y-R3 tail -c mistake).
    full_stdout = json.dumps(_complete_payload(), indent=2, sort_keys=True)
    truncated_tail = full_stdout[-40:]
    assert len(truncated_tail) < len(full_stdout)  # stdout capture was lossy
    # The durable artifact is the system of record and remains complete + valid regardless.
    artifact: Any = R.read_result_artifact(written.path)
    R.verify_artifact(artifact, expected_identity=ident)  # complete despite stdout loss
    assert artifact["result"]["scientific_result"]["b3_observability"]["diagnostics"]["aggregate"]["ok_count"] == 41


# --------------------------------------------------------------------------- #
# §49 — postclaim failure artifact
# --------------------------------------------------------------------------- #
def test_postclaim_failure_artifact(tmp_path):
    ident = _identity("rid-postfail")
    payload = {
        "result": "FAILED",
        "provider_traffic": 12,
        "technical_status": "FAILED",
        "failure_classification": "LIVE_EXECUTION_ERROR",
        "reasons": ["live_execution_error"],
        "error_type": "RuntimeError",
    }
    written = R.write_failure_artifact(
        status=R.STATUS_POSTCLAIM_FAILURE,
        command="execute-b3-observability-live",
        identity=ident,
        payload=payload,
        claimed=True,
        base_dir=str(tmp_path),
    )
    artifact: Any = R.read_result_artifact(written.path)
    R.verify_artifact(artifact, expected_identity=ident)  # failure artifact has no completeness req
    assert artifact["status"] == R.STATUS_POSTCLAIM_FAILURE
    assert artifact["claimed"] is True  # ledger was consumed
    assert artifact["result"]["failure_classification"] == "LIVE_EXECUTION_ERROR"
    assert artifact["provider_traffic"] == 12
    # no _SUCCESS marker for a failure artifact.
    assert not R._success_marker_path("rid-postfail", str(tmp_path)).exists()


def test_preclaim_failure_artifact_marks_not_claimed(tmp_path):
    ident = _identity("rid-prefail")
    written = R.write_failure_artifact(
        status=R.STATUS_PRECLAIM_FAILURE,
        command="execute-b3-observability-live",
        identity=ident,
        payload={"result": "FAILED", "provider_traffic": 999, "failure_classification": "X"},
        claimed=False,
        base_dir=str(tmp_path),
    )
    artifact: Any = R.read_result_artifact(written.path)
    assert artifact["claimed"] is False
    assert artifact["provider_traffic"] == 0  # preclaim: no provider traffic, forced to 0


# --------------------------------------------------------------------------- #
# §50 — postclaim persistence failure is NOT reported as COMPLETE (no retry)
# --------------------------------------------------------------------------- #
def test_postclaim_persistence_failure_fails_closed(tmp_path):
    ident = _identity("rid-persistfail")
    # Force the read-after-write verification to fail (simulated corrupt/incomplete persistence).
    with mock.patch.object(
        R, "verify_artifact", side_effect=R.ResultArtifactPersistenceError("hash_mismatch")
    ):
        with pytest.raises(R.ResultArtifactPersistenceError):
            R.finalize_result_artifact(
                status=R.STATUS_COMPLETED,
                command="execute-b3-observability-live",
                identity=ident,
                payload=_complete_payload(),
                base_dir=str(tmp_path),
            )
    # fail-closed: NO finalized artifact was promoted and NO success marker written.
    assert not R._final_path("rid-persistfail", str(tmp_path)).exists()
    assert not R._success_marker_path("rid-persistfail", str(tmp_path)).exists()


def _b3_grant_manifest(tmp_path, run_id: str = "rid-cli-persist") -> str:
    from open_notebook.integrations.graphrag.eval.authmintlivepn02d import (
        frozen_b3b_operator_grant_template,
    )

    g = frozen_b3b_operator_grant_template(
        run_id=run_id,
        implementation_checkpoint_commit="5abeaaa09b7157232b1ac5a234c9d8c50b542585",
        implementation_checkpoint_tag="graphrag-pn02db0cb-real-provider-wiring-approved",
        b1_r2_checkpoint=EXPECTED_B3B_CHECKPOINT_TAG,
        approved_git_commit=C.TEST_COMMIT,
        approved_git_tag=C.TEST_TAG,
    )
    manifest = {
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
    p = tmp_path / "b3_manifest.json"
    p.write_text(json.dumps(manifest), encoding="utf-8")
    return str(p)


def test_cli_maps_persistence_failure_to_postclaim_and_no_retry(tmp_path, monkeypatch):
    # An in-memory COMPLETE outcome whose durable finalization fails must NOT return COMPLETE,
    # and there must be NO automatic retry. Driven through the composed evaluator using its
    # documented controlled-test injectables (fake git baseline / fixture / runner / trust).
    from types import SimpleNamespace

    import open_notebook.integrations.graphrag.eval.cli_live_pn02d as cli
    from open_notebook.integrations.graphrag.eval.authmintlivepn02d import (
        B2_ALLOWED_OPERATION_VALUES,
        EXPECTED_FIXTURE_HASH,
    )
    from open_notebook.integrations.graphrag.eval.budgetlivepn02d import b2_caps_dict
    from open_notebook.integrations.graphrag.eval.provider_binding08 import (
        frozen_provider_binding,
    )

    manifest_path = _b3_grant_manifest(tmp_path)
    required = frozen_provider_binding().required_secret_envs()
    env = {cli.PROVIDER_RUN_AUTHORIZED_ENV: "YES"}
    env.update({name: "dummy-not-a-real-secret" for name in required})

    outcome = SimpleNamespace(
        state="COMPLETE",
        run_id="rid-cli-persist",
        technical_status=SimpleNamespace(value="COMPLETED"),
        report={"report_kind": "PN02_B3_FACT_RECALL_OBSERVABILITY", **{
            "scientific_outputs": {"PER_NOTEBOOK_ISOLATION_EVIDENCED": "YES"}
        }},
        failure_reason=None,
        index_completion=None,
        index_records=[],
    )

    calls = {"n": 0}

    def _boom(**kwargs):
        calls["n"] += 1
        raise R.ResultArtifactPersistenceError("incomplete_completed_artifact")

    monkeypatch.setattr(cli, "finalize_result_artifact", _boom)

    exit_code, payload = cli._evaluate_execute_b1_live_composed(
        manifest_path=manifest_path,
        explicit_authorize=True,
        env=env,
        git_baseline_reader=lambda: C.clean_git_baseline(),
        fixture_hash_reader=lambda: (True, EXPECTED_FIXTURE_HASH),
        live_runner=cast(Any, lambda **kwargs: outcome),
        command="execute-b3-observability-live",
        refusal_fn=lambda grant, baseline: [],  # controlled-test: bypass trust gate
        allowlist=B2_ALLOWED_OPERATION_VALUES,
        expected_caps_dict=b2_caps_dict,
        projector=cli._project_b3_observability_result,
        result_retention=R.ResultRetentionConfig(
            enabled=True, execution_kind="B3", treatment=False, base_dir=str(tmp_path)
        ),
    )

    assert exit_code == 4  # NOT the COMPLETE exit code (0)
    assert payload["result"] == "FAILED"
    assert payload["failure_classification"] == "POSTCLAIM_RESULT_PERSISTENCE_FAILURE"
    assert calls["n"] == 1  # NO automatic retry


# --------------------------------------------------------------------------- #
# §51 — content-safe secret exclusion
# --------------------------------------------------------------------------- #
def test_result_artifact_secret_exclusion(tmp_path):
    ident = _identity("rid-secret")
    payload = _complete_payload()
    # Inject secret-shaped / raw-content-shaped values into NON-allowlisted payload keys and
    # into an exception-like field. None of these may reach the durable artifact.
    payload["provider_api_key"] = "sk-live-DEADBEEFdeadbeef0123456789"
    payload["raw_answer"] = "raw answer: the customer account balance is ..."
    payload["raw_prompt"] = "prompt: system ... secret-token=sk-abc"
    payload["dotenv"] = "OPENROUTER_API_KEY=sk-live-should-never-persist"
    written = R.finalize_result_artifact(
        status=R.STATUS_COMPLETED,
        command="execute-b3-observability-live",
        identity=ident,
        payload=payload,
        base_dir=str(tmp_path),
    )
    blob = written.path.read_text(encoding="utf-8")
    for tok in _FORBIDDEN_TOKENS:
        assert tok not in blob
    artifact = json.loads(blob)
    # only allowlisted result keys survived.
    assert set(artifact["result"].keys()) <= set(R.ALLOWED_TOP_LEVEL_PAYLOAD_KEYS)
    assert "provider_api_key" not in artifact["result"]
    assert "raw_answer" not in artifact["result"]


def test_oversized_string_tripwire_fails_closed(tmp_path):
    ident = _identity("rid-oversize")
    payload = _complete_payload()
    # An accidental raw-content regression: a huge string smuggled into an allowlisted block.
    payload["scientific_result"]["leaked_blob"] = "x" * (R.MAX_SAFE_STRING_LEN + 1)
    with pytest.raises(R.ResultArtifactPersistenceError):
        R.finalize_result_artifact(
            status=R.STATUS_COMPLETED,
            command="execute-b3-observability-live",
            identity=ident,
            payload=payload,
            base_dir=str(tmp_path),
        )


# --------------------------------------------------------------------------- #
# §52 — duplicate run_id refused (no overwrite)
# --------------------------------------------------------------------------- #
def test_duplicate_run_id_artifact_refused(tmp_path):
    ident = _identity("rid-dup")
    R.finalize_result_artifact(
        status=R.STATUS_COMPLETED,
        command="execute-b3-observability-live",
        identity=ident,
        payload=_complete_payload(),
        base_dir=str(tmp_path),
    )
    first = R._final_path("rid-dup", str(tmp_path)).read_bytes()
    with pytest.raises(R.ResultArtifactAlreadyExistsError):
        R.finalize_result_artifact(
            status=R.STATUS_COMPLETED,
            command="execute-b3-observability-live",
            identity=ident,
            payload=_complete_payload(),
            base_dir=str(tmp_path),
        )
    # existing scientific artifact bytes are untouched (no overwrite).
    assert R._final_path("rid-dup", str(tmp_path)).read_bytes() == first
    # readiness ALSO fails closed on the collision (defense-in-depth with the ledger).
    report = R.check_result_retention_readiness("rid-dup", base_dir=str(tmp_path))
    assert report.status == "FAIL"
    assert report.failed_check == "run_id_collision"


# --------------------------------------------------------------------------- #
# §53 — crash / temp recovery state discernible
# --------------------------------------------------------------------------- #
def test_incomplete_temp_artifact_recovery_state(tmp_path):
    # absent
    assert R.inspect_run_artifact_state("rid-rec", base_dir=str(tmp_path)).state == R.RECOVERY_ABSENT
    run_dir = R._prepare_run_dir("rid-rec", str(tmp_path))
    # temp only (crash mid-write): a .tmp exists but no finalized artifact / no marker.
    (run_dir / "result.abc.tmp").write_text("{partial", encoding="utf-8")
    state = R.inspect_run_artifact_state("rid-rec", base_dir=str(tmp_path))
    assert state.state == R.RECOVERY_TEMP_ONLY
    assert state.temp_present is True and state.final_present is False
    # a fully finalized run is FINAL_COMPLETE.
    R.finalize_result_artifact(
        status=R.STATUS_COMPLETED,
        command="execute-b3-observability-live",
        identity=_identity("rid-rec2"),
        payload=_complete_payload(),
        base_dir=str(tmp_path),
    )
    assert (
        R.inspect_run_artifact_state("rid-rec2", base_dir=str(tmp_path)).state
        == R.RECOVERY_FINAL_COMPLETE
    )


# --------------------------------------------------------------------------- #
# §54 — identity mismatch fails closed
# --------------------------------------------------------------------------- #
def test_artifact_identity_mismatch_fails_closed(tmp_path):
    written = R.finalize_result_artifact(
        status=R.STATUS_COMPLETED,
        command="execute-b3-observability-live",
        identity=_identity("rid-idmm"),
        payload=_complete_payload(),
        base_dir=str(tmp_path),
    )
    artifact: Any = R.read_result_artifact(written.path)
    # A validator run with a DIFFERENT expected identity (tampered run_id / head / tag) rejects.
    for tampered in (
        _identity("rid-DIFFERENT"),
        R.ResultArtifactIdentity(
            run_id="rid-idmm",
            head_commit="deadbeef",  # tampered HEAD
            live_auth_tag="graphrag-pn02db3yr1-b3-live-auth-successor-approved",
            implementation_checkpoint_commit="5abeaaa09b7157232b1ac5a234c9d8c50b542585",
            implementation_checkpoint_tag="graphrag-pn02db0cb-real-provider-wiring-approved",
            fixture_hash="9ce7df742810424d9ef7b7b34962187d9061245f81471f5fb1d7f2608f6899a6",
            treatment=True,
            execution_kind="B3",
        ),
    ):
        with pytest.raises(R.ResultArtifactPersistenceError):
            R.verify_artifact(artifact, expected_identity=tampered)


def test_tampered_body_fails_hash_check(tmp_path):
    written = R.finalize_result_artifact(
        status=R.STATUS_COMPLETED,
        command="execute-b3-observability-live",
        identity=_identity("rid-tamper"),
        payload=_complete_payload(),
        base_dir=str(tmp_path),
    )
    artifact: Any = R.read_result_artifact(written.path)
    artifact["result"]["scientific_result"]["b3_observability"]["diagnostics"]["aggregate"][
        "ok_count"
    ] = 999
    with pytest.raises(R.ResultArtifactPersistenceError):
        R.verify_artifact(artifact)  # hash no longer matches the tampered body


# --------------------------------------------------------------------------- #
# §55 — an incomplete COMPLETED artifact is rejected
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "drop_path",
    [
        ("scientific_result", "b3_observability", "diagnostics"),  # fact-recall
        ("scientific_result", "b3_observability", "qa_value_observability"),  # QA-value
        ("scientific_result", "scientific_outputs"),  # scientific outputs / isolation
        ("scientific_result", "gd_calls"),  # provider accounting
    ],
)
def test_incomplete_completed_artifact_rejected(tmp_path, drop_path):
    ident = _identity("rid-incomplete")
    payload = _complete_payload()
    # Remove a mandatory content-safe block, then attempt to finalize as COMPLETED.
    target = payload
    for key in drop_path[:-1]:
        target = target[key]
    del target[drop_path[-1]]
    with pytest.raises(R.ResultArtifactPersistenceError):
        R.finalize_result_artifact(
            status=R.STATUS_COMPLETED,
            command="execute-b3-observability-live",
            identity=ident,
            payload=payload,
            base_dir=str(tmp_path),
        )
    # nothing was promoted.
    assert not R._final_path("rid-incomplete", str(tmp_path)).exists()


# --------------------------------------------------------------------------- #
# §47 — preclaim retention readiness prevents a grant burn (driver-level)
# --------------------------------------------------------------------------- #
class _EnforcerRecorder:
    def __init__(self) -> None:
        self.calls: list = []

    def __call__(self, *, operator_grant, execution_kind, ledger_path):  # noqa: ANN001
        self.calls.append((operator_grant.run_id, execution_kind))
        raise AssertionError("one-shot claim must NOT be reached when retention is unready")


def _grant(run_id: str):
    return frozen_b3b_operator_grant_template(
        run_id=run_id,
        implementation_checkpoint_commit=C.TEST_COMMIT,
        implementation_checkpoint_tag=C.TEST_TAG,
        b1_r2_checkpoint=EXPECTED_B3B_CHECKPOINT_TAG,
        approved_git_commit=C.TEST_COMMIT,
        approved_git_tag=C.TEST_TAG,
    )


@pytest.mark.asyncio
async def test_retention_readiness_prevents_grant_burn(tmp_path):
    from unittest import mock as _mock

    import open_notebook.integrations.graphrag.eval.authmintlivepn02d as authmint

    fx = load_fixture()
    reader = C.b1r2_reader_ok(tag=EXPECTED_B3B_CHECKPOINT_TAG, peel=C.TEST_COMMIT, head=C.TEST_COMMIT)
    bundle = C.build_live_seams(fx)
    enforcer = _EnforcerRecorder()
    ledger_path = str(tmp_path / "live_auth_consumption.sqlite")
    order: list = []

    def _failing_retention(run_id):  # provider-free; simulates an unusable artifact store
        order.append(("retention_readiness", run_id))
        raise R.LiveResultRetentionReadinessError(
            R.RetentionReadinessReport(
                status="FAIL", run_id=run_id, failed_check="directory_creatable", error_type="OSError"
            )
        )

    with _mock.patch.object(
        authmint, "current_approved_b3b_checkpoint", return_value=EXPECTED_B3B_CHECKPOINT_TAG
    ), _mock.patch.object(authmint, "_build_trusted_b1_r2_reader", return_value=reader):
        with pytest.raises(R.LiveResultRetentionReadinessError):
            await RealB1Driver(
                fx,
                bundle.seams,
                execution_kind="B3B",
                mint_fn=mint_live_b3_provider_run_authorization,
                one_shot_enforcer=enforcer,
                oneshot_ledger_path=ledger_path,
                retention_readiness_fn=_failing_retention,
            ).run(
                operator_grant=_grant("pn02d-retention-unready"),
                git_baseline_attestation=C.clean_git_baseline(),
            )

    # the readiness guard ran; the one-shot claim was NEVER reached -> grant not burned.
    assert order == [("retention_readiness", "pn02d-retention-unready")]
    assert enforcer.calls == []
    # provider-bound Boot 2 never started; no durable ledger created by the failed pre-claim run.
    assert len(bundle.controller.started) == 0
    assert not os.path.exists(ledger_path)


# =========================================================================== #
# PN02D-RH-IR1 REMEDIATION TESTS (Codex Review #1: H1 / H2 / M1 / M2)
# =========================================================================== #
def _fail(msg):
    raise AssertionError(msg)


class _StopAtClaim(Exception):
    pass


class _EnforcerRecorderStop:
    """Fake one-shot enforcer: records the (would-be) claim then STOPS (no real claim)."""

    def __init__(self, order=None) -> None:
        self.calls: list = []
        self._order = order

    def __call__(self, *, operator_grant, execution_kind, ledger_path):  # noqa: ANN001
        self.calls.append((operator_grant.run_id, execution_kind))
        if self._order is not None:
            self._order.append(("claim", operator_grant.run_id))
        raise _StopAtClaim()


# --------------------------------------------------------------------------- #
# H1 — run_id path escape rejected (single safe segment + resolve-and-contain)
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "bad",
    ["../security/x", "..\\security\\x", "a/b", "a\\b", ".", "..", "", "   ", "x\x00y", "run id", "run:id"],
)
def test_run_id_path_traversal_rejected(tmp_path, bad):
    with pytest.raises(R.ResultRetentionError):
        R.validate_run_id(bad)
    with pytest.raises(R.ResultRetentionError):
        R.run_artifact_dir(bad, base_dir=str(tmp_path))
    rep = R.check_result_retention_readiness(bad, base_dir=str(tmp_path))
    assert rep.status == "FAIL"  # readiness fails closed (never crashes) on an unsafe run_id


@pytest.mark.parametrize("bad", ["/tmp/x", "C:/temp/x", "C:\\temp\\x", "\\\\server\\share\\x", "C:x"])
def test_run_id_absolute_and_windows_escape_rejected(tmp_path, bad):
    with pytest.raises(R.ResultRetentionError):
        R.validate_run_id(bad)
    with pytest.raises(R.ResultRetentionError):
        R.run_artifact_dir(bad, base_dir=str(tmp_path))


def test_valid_run_id_accepted(tmp_path):
    for good in ("pn02db3yr3-1ad116f4-b04e-46c9-aedf-cce97ebc114f", "rid_ok-123", "A" * 200):
        assert R.validate_run_id(good) == good
        d = R.run_artifact_dir(good, base_dir=str(tmp_path))
        assert d.resolve().parent == tmp_path.resolve()  # DIRECT child of the artifact root
    with pytest.raises(R.ResultRetentionError):
        R.validate_run_id("A" * 201)  # over-length rejected


def test_run_id_escape_cannot_place_artifact_outside_root(tmp_path):
    base = tmp_path / "eval_results"
    ident = R.ResultArtifactIdentity(
        run_id="../escape", head_commit="h", live_auth_tag="t",
        implementation_checkpoint_commit="c", implementation_checkpoint_tag="ct",
        fixture_hash="fx", treatment=False, execution_kind="B3",
    )
    with pytest.raises(R.ResultRetentionError):
        R.finalize_result_artifact(
            status=R.STATUS_COMPLETED, command="execute-b3-observability-live",
            identity=ident, payload=_complete_payload(), base_dir=str(base),
        )
    assert not (tmp_path / "escape").exists()  # nothing written outside the root


# --------------------------------------------------------------------------- #
# H2 — race-safe no-overwrite (real concurrent finalization via os.link)
# --------------------------------------------------------------------------- #
def _payload_with_ok(ok_count: int) -> dict:
    p = _complete_payload()
    p["scientific_result"]["b3_observability"]["diagnostics"]["aggregate"]["ok_count"] = ok_count
    return p


def _finalize_once(base_dir: str, run_id: str, ok_count: int):
    try:
        w = R.finalize_result_artifact(
            status=R.STATUS_COMPLETED, command="execute-b3-observability-live",
            identity=_identity(run_id), payload=_payload_with_ok(ok_count), base_dir=base_dir,
        )
        return ("ok", w.sha256, ok_count)
    except R.ResultArtifactAlreadyExistsError:
        return ("refused", None, ok_count)


def _run_one_race(base_dir: str, run_id: str):
    import concurrent.futures as cf

    with cf.ThreadPoolExecutor(max_workers=2) as ex:
        futs = [
            ex.submit(_finalize_once, base_dir, run_id, 41),
            ex.submit(_finalize_once, base_dir, run_id, 42),
        ]
        return [f.result() for f in cf.as_completed(futs)]


def test_concurrent_same_run_no_overwrite(tmp_path):
    results = _run_one_race(str(tmp_path), "rid-race")
    assert sorted(r[0] for r in results) == ["ok", "refused"]  # one wins, one fails closed
    final = R._final_path("rid-race", str(tmp_path))
    artifact: Any = R.read_result_artifact(final)  # ONE whole valid artifact, never a mix
    R.verify_artifact(artifact, expected_identity=_identity("rid-race"))
    won_ok = artifact["result"]["scientific_result"]["b3_observability"]["diagnostics"]["aggregate"]["ok_count"]
    assert won_ok in (41, 42)
    assert R._success_marker_path("rid-race", str(tmp_path)).exists()  # exactly one _SUCCESS
    winner = next(r for r in results if r[0] == "ok")
    assert artifact["artifact_sha256"] == winner[1]  # stored hash == canonical winner


def test_concurrency_stress_no_overwrite(tmp_path):
    iterations = 30
    failures = 0
    for i in range(iterations):
        results = _run_one_race(str(tmp_path), f"rid-stress-{i}")
        if sorted(r[0] for r in results) != ["ok", "refused"]:
            failures += 1
            continue
        try:
            art: Any = R.read_result_artifact(R._final_path(f"rid-stress-{i}", str(tmp_path)))
            R.verify_artifact(art, expected_identity=_identity(f"rid-stress-{i}"))
        except Exception:
            failures += 1
    assert failures == 0, f"{failures}/{iterations} race iterations not fail-closed-safe"


def test_loser_does_not_overwrite_or_mislead(tmp_path):
    R.finalize_result_artifact(
        status=R.STATUS_COMPLETED, command="execute-b3-observability-live",
        identity=_identity("rid-loser"), payload=_payload_with_ok(41), base_dir=str(tmp_path),
    )
    winner_bytes = R._final_path("rid-loser", str(tmp_path)).read_bytes()
    with pytest.raises(R.ResultArtifactAlreadyExistsError):
        R.finalize_result_artifact(
            status=R.STATUS_COMPLETED, command="execute-b3-observability-live",
            identity=_identity("rid-loser"), payload=_payload_with_ok(42), base_dir=str(tmp_path),
        )
    assert R._final_path("rid-loser", str(tmp_path)).read_bytes() == winner_bytes  # untouched
    art: Any = R.read_result_artifact(R._final_path("rid-loser", str(tmp_path)))
    R.verify_artifact(art, expected_identity=_identity("rid-loser"))
    assert art["result"]["scientific_result"]["b3_observability"]["diagnostics"]["aggregate"]["ok_count"] == 41


# --------------------------------------------------------------------------- #
# M1 — retention guard is B3-opt-in; B1/B2 acquire no retention behavior
# --------------------------------------------------------------------------- #
def test_default_driver_retention_is_noop():
    import open_notebook.integrations.graphrag.eval.driver_live_pn02d as drv

    fx = load_fixture()
    driver = RealB1Driver(fx, C.build_live_seams(fx).seams)
    assert driver._retention_readiness_fn is drv._noop_retention_readiness


def test_b2_driver_kwargs_omits_retention_by_default_and_includes_when_provided():
    import open_notebook.integrations.graphrag.eval.realseamsb2pn02d as rs

    assert "retention_readiness_fn" not in rs._b2_driver_kwargs(cast(Any, object()))

    def _sentinel(run_id):
        return None

    kw = rs._b2_driver_kwargs(cast(Any, object()), retention_readiness_fn=_sentinel)
    assert kw["retention_readiness_fn"] is _sentinel


@pytest.mark.asyncio
async def test_b1b2_default_run_does_not_probe_retention_storage(tmp_path, monkeypatch):
    # DEFAULT driver retention guard (what B1/B2 use) is a no-op: reaching the claim must NOT
    # invoke the real storage probe — B1/B2 acquire no new filesystem dependency.
    import open_notebook.integrations.graphrag.eval.authmintlivepn02d as authmint
    import open_notebook.integrations.graphrag.eval.result_artifact_pn02d as ra

    probe_calls: list = []

    def _forbidden_probe(run_id, base_dir=None):
        probe_calls.append(run_id)
        _fail("must not probe")

    monkeypatch.setattr(ra, "check_result_retention_readiness", _forbidden_probe)
    fx = load_fixture()
    reader = C.b1r2_reader_ok(tag=EXPECTED_B3B_CHECKPOINT_TAG, peel=C.TEST_COMMIT, head=C.TEST_COMMIT)
    bundle = C.build_live_seams(fx)
    enforcer = _EnforcerRecorderStop()
    with mock.patch.object(
        authmint, "current_approved_b3b_checkpoint", return_value=EXPECTED_B3B_CHECKPOINT_TAG
    ), mock.patch.object(authmint, "_build_trusted_b1_r2_reader", return_value=reader):
        with pytest.raises(_StopAtClaim):
            await RealB1Driver(
                fx, bundle.seams, execution_kind="B3B",
                mint_fn=mint_live_b3_provider_run_authorization,
                one_shot_enforcer=enforcer, oneshot_ledger_path=str(tmp_path / "l.sqlite"),
            ).run(
                operator_grant=_grant("pn02d-default-noop"),
                git_baseline_attestation=C.clean_git_baseline(),
            )
    assert probe_calls == []  # storage probe NEVER called on the default (B1/B2) path
    assert enforcer.calls == [("pn02d-default-noop", "B3B")]  # reached the claim


@pytest.mark.asyncio
async def test_b3_injected_retention_probes_before_claim(tmp_path):
    import open_notebook.integrations.graphrag.eval.authmintlivepn02d as authmint

    order: list = []

    def _recording_probe(run_id):
        order.append(("retention_probe", run_id))
        return None

    fx = load_fixture()
    reader = C.b1r2_reader_ok(tag=EXPECTED_B3B_CHECKPOINT_TAG, peel=C.TEST_COMMIT, head=C.TEST_COMMIT)
    bundle = C.build_live_seams(fx)
    enforcer = _EnforcerRecorderStop(order=order)
    with mock.patch.object(
        authmint, "current_approved_b3b_checkpoint", return_value=EXPECTED_B3B_CHECKPOINT_TAG
    ), mock.patch.object(authmint, "_build_trusted_b1_r2_reader", return_value=reader):
        with pytest.raises(_StopAtClaim):
            await RealB1Driver(
                fx, bundle.seams, execution_kind="B3B",
                mint_fn=mint_live_b3_provider_run_authorization,
                one_shot_enforcer=enforcer, oneshot_ledger_path=str(tmp_path / "l.sqlite"),
                retention_readiness_fn=_recording_probe,
            ).run(
                operator_grant=_grant("pn02d-b3-probe"),
                git_baseline_attestation=C.clean_git_baseline(),
            )
    assert order == [("retention_probe", "pn02d-b3-probe"), ("claim", "pn02d-b3-probe")]


def test_b3_runner_opts_in_to_retention_guard(monkeypatch):
    import asyncio

    import open_notebook.integrations.graphrag.eval.p1diagrunnerpn02db3 as b3run
    import open_notebook.integrations.graphrag.eval.result_artifact_pn02d as ra

    captured: dict = {}

    async def _fake_b2(**kwargs):
        captured.update(kwargs)
        raise _StopAtClaim()

    monkeypatch.setattr(b3run._realseams, "run_live_b2_execution", _fake_b2)
    with pytest.raises(_StopAtClaim):
        asyncio.run(
            b3run.run_live_b3_observability_execution(
                operator_grant=_grant("pn02d-b3-optin"),
                git_baseline_attestation=C.clean_git_baseline(),
            )
        )
    # IR2-H1: the guard is now bound to the finalization root via functools.partial.
    import functools

    fn = captured.get("retention_readiness_fn")
    assert isinstance(fn, functools.partial)
    assert fn.func is ra.assert_result_retention_readiness
    assert fn.keywords == {"base_dir": None}  # default root when no --result-artifact-dir
    assert captured.get("execution_kind") == "B3B"


# --------------------------------------------------------------------------- #
# M2 — nested content-safety (structural allowlist + forbidden-key + tripwire)
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("value", ["sk-test", "token123", "secret", "raw answer", "customer text"])
def test_short_nested_secret_under_unknown_key_rejected(tmp_path, value):
    payload = _complete_payload()
    payload["scientific_result"]["smuggled_field"] = value  # unknown direct child
    with pytest.raises(R.ResultArtifactPersistenceError):
        R.finalize_result_artifact(
            status=R.STATUS_COMPLETED, command="execute-b3-observability-live",
            identity=_identity("rid-m2-unknown"), payload=payload, base_dir=str(tmp_path),
        )
    assert not R._final_path("rid-m2-unknown", str(tmp_path)).exists()


@pytest.mark.parametrize(
    "field",
    ["raw_prompt", "raw_answer", "source_content", "materialized_content", "api_key", "token", "secret", "authorization"],
)
def test_forbidden_nested_fields_rejected(tmp_path, field):
    payload = _complete_payload()
    payload["scientific_result"]["b3_observability"][field] = "x"  # short forbidden nested field
    with pytest.raises(R.ResultArtifactPersistenceError):
        R.finalize_result_artifact(
            status=R.STATUS_COMPLETED, command="execute-b3-observability-live",
            identity=_identity("rid-m2-forbidden"), payload=payload, base_dir=str(tmp_path),
        )
    assert not R._final_path("rid-m2-forbidden", str(tmp_path)).exists()


def test_forbidden_field_deep_in_list_rejected(tmp_path):
    payload = _complete_payload()
    payload["scientific_result"]["b3_observability"]["diagnostics"]["per_query_arm"] = [
        {"query_id": "q1", "api_key": "sk-deep"}
    ]
    with pytest.raises(R.ResultArtifactPersistenceError):
        R.finalize_result_artifact(
            status=R.STATUS_COMPLETED, command="execute-b3-observability-live",
            identity=_identity("rid-m2-deep"), payload=payload, base_dir=str(tmp_path),
        )


def test_safe_scientific_artifact_still_finalizes(tmp_path):
    written = R.finalize_result_artifact(
        status=R.STATUS_COMPLETED, command="execute-b3-observability-live",
        identity=_identity("rid-m2-safe"), payload=_complete_payload(), base_dir=str(tmp_path),
    )
    art: Any = R.read_result_artifact(written.path)
    R.verify_artifact(art, expected_identity=_identity("rid-m2-safe"))
    sci = art["result"]["scientific_result"]
    assert sci["b3_observability"]["diagnostics"]["aggregate"]["ok_count"] == 41
    assert sci["b3_observability"]["qa_value_observability"]["qa_decision"]["verdict"] == "YES"
    assert sci["scientific_outputs"]["PER_NOTEBOOK_ISOLATION_EVIDENCED"] == "YES"


def test_safe_provider_accounting_still_finalizes(tmp_path):
    payload = _complete_payload()
    written = R.finalize_result_artifact(
        status=R.STATUS_COMPLETED, command="execute-b3-observability-live",
        identity=_identity("rid-m2-acct"), payload=payload, base_dir=str(tmp_path),
    )
    art: Any = R.read_result_artifact(written.path)
    sci = art["result"]["scientific_result"]
    assert sci["query_embedding_attempts"] == 26
    assert sci["gd_calls"] == 26
    assert sci["vector_queries"] == 26
    assert art["provider_traffic"] == payload["provider_traffic"]


# =========================================================================== #
# PN02D-RH-IR2 REMEDIATION-2 TESTS (Codex Review #2: IR2-H1 / IR2-M1)
# =========================================================================== #
def _capture_b3_retention_fn(monkeypatch, *, run_id: str, result_artifact_dir):
    """Drive run_live_b3_observability_execution with a faked b2 runner that captures kwargs,
    and return the retention_readiness_fn the B3 path bound (PN02D-RH-IR2-H1)."""
    import asyncio

    import open_notebook.integrations.graphrag.eval.p1diagrunnerpn02db3 as b3run

    captured: dict = {}

    async def _fake_b2(**kwargs):
        captured.update(kwargs)
        raise _StopAtClaim()

    monkeypatch.setattr(b3run._realseams, "run_live_b2_execution", _fake_b2)
    with pytest.raises(_StopAtClaim):
        asyncio.run(
            b3run.run_live_b3_observability_execution(
                operator_grant=_grant(run_id),
                git_baseline_attestation=C.clean_git_baseline(),
                result_artifact_dir=result_artifact_dir,
            )
        )
    return captured["retention_readiness_fn"]


# --------------------------------------------------------------------------- #
# IR2-H1 — preclaim retention root == finalization root (override binding)
# --------------------------------------------------------------------------- #
def test_b3_readiness_bound_to_override_root(tmp_path, monkeypatch):
    override = str(tmp_path / "override")
    fn = _capture_b3_retention_fn(monkeypatch, run_id="rid-ovr-bind", result_artifact_dir=override)
    # the bound guard probes the OVERRIDE root (creates the run dir there), not the default.
    fn("rid-ovr-bind")
    assert (tmp_path / "override" / "rid-ovr-bind").exists()


def test_override_collision_fails_preclaim(tmp_path, monkeypatch):
    override = str(tmp_path / "override")
    # a finalized artifact already exists in the OVERRIDE root for this run_id...
    R.finalize_result_artifact(
        status=R.STATUS_COMPLETED, command="execute-b3-observability-live",
        identity=_identity("rid-ovr-coll"), payload=_complete_payload(), base_dir=override,
    )
    # ...while the DEFAULT root is clean: the OLD (default-bound) probe would MISS it.
    assert R.check_result_retention_readiness("rid-ovr-coll", base_dir=None).status == "OK"
    # the B3-bound guard probes the override → collision → fails closed BEFORE any claim.
    fn = _capture_b3_retention_fn(monkeypatch, run_id="rid-ovr-coll", result_artifact_dir=override)
    with pytest.raises(R.LiveResultRetentionReadinessError):
        fn("rid-ovr-coll")


def test_override_unwritable_fails_preclaim(tmp_path, monkeypatch):
    # override root sits UNDER an existing regular file → cannot be created/written.
    blocker = tmp_path / "blocker_file"
    blocker.write_text("x", encoding="utf-8")
    override = str(blocker / "eval_results")  # a path whose parent is a file
    fn = _capture_b3_retention_fn(monkeypatch, run_id="rid-ovr-unwrite", result_artifact_dir=override)
    with pytest.raises(R.LiveResultRetentionReadinessError):
        fn("rid-ovr-unwrite")


@pytest.mark.asyncio
async def test_override_valid_reaches_claim_seam(tmp_path):
    # A valid override root → the bound guard passes → the B3 flow REACHES the one-shot claim seam.
    import functools

    import open_notebook.integrations.graphrag.eval.authmintlivepn02d as authmint

    override = str(tmp_path / "override_valid")
    fx = load_fixture()
    reader = C.b1r2_reader_ok(tag=EXPECTED_B3B_CHECKPOINT_TAG, peel=C.TEST_COMMIT, head=C.TEST_COMMIT)
    bundle = C.build_live_seams(fx)
    enforcer = _EnforcerRecorderStop()
    bound = functools.partial(R.assert_result_retention_readiness, base_dir=override)
    with mock.patch.object(
        authmint, "current_approved_b3b_checkpoint", return_value=EXPECTED_B3B_CHECKPOINT_TAG
    ), mock.patch.object(authmint, "_build_trusted_b1_r2_reader", return_value=reader):
        with pytest.raises(_StopAtClaim):
            await RealB1Driver(
                fx, bundle.seams, execution_kind="B3B",
                mint_fn=mint_live_b3_provider_run_authorization,
                one_shot_enforcer=enforcer, oneshot_ledger_path=str(tmp_path / "l.sqlite"),
                retention_readiness_fn=bound,
            ).run(
                operator_grant=_grant("rid-ovr-valid"),
                git_baseline_attestation=C.clean_git_baseline(),
            )
    assert enforcer.calls == [("rid-ovr-valid", "B3B")]  # reached the claim seam
    assert (tmp_path / "override_valid" / "rid-ovr-valid").exists()  # probed the override root


def test_default_root_still_valid(tmp_path, monkeypatch):
    # No override → the bound guard uses the default root (None) and passes.
    fn = _capture_b3_retention_fn(monkeypatch, run_id="rid-default", result_artifact_dir=None)
    rep = fn("rid-default")  # assert_* returns the OK report
    assert rep.status == "OK"


# --------------------------------------------------------------------------- #
# IR2-M1 — genuinely structural nested content-safety (closed allowlist)
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "variant_key",
    ["rawAnswer", "raw-answer", "raw_answer_text", "sourceText", "source_content_text",
     "apiKey", "access_token", "bearer_token", "secret_value", "secretValue"],
)
def test_variant_nested_secret_keys_rejected(tmp_path, variant_key):
    payload = _complete_payload()
    payload["scientific_result"]["b3_observability"][variant_key] = "x"
    with pytest.raises(R.ResultArtifactPersistenceError):
        R.finalize_result_artifact(
            status=R.STATUS_COMPLETED, command="execute-b3-observability-live",
            identity=_identity("rid-variant"), payload=payload, base_dir=str(tmp_path),
        )
    assert not R._final_path("rid-variant", str(tmp_path)).exists()


def test_arbitrary_unknown_deep_key_rejected(tmp_path):
    payload = _complete_payload()
    payload["scientific_result"]["b3_observability"]["harmless_looking_key"] = {
        "another_key": {"value": "customer text"}
    }
    with pytest.raises(R.ResultArtifactPersistenceError):
        R.finalize_result_artifact(
            status=R.STATUS_COMPLETED, command="execute-b3-observability-live",
            identity=_identity("rid-deepkey"), payload=payload, base_dir=str(tmp_path),
        )


def test_short_raw_unapproved_deep_key_rejected(tmp_path):
    # a SHORT raw-like value under an innocuous UNAPPROVED deep key must not persist.
    payload = _complete_payload()
    payload["scientific_result"]["b3_observability"]["diagnostics"]["note"] = "customer text"
    with pytest.raises(R.ResultArtifactPersistenceError):
        R.finalize_result_artifact(
            status=R.STATUS_COMPLETED, command="execute-b3-observability-live",
            identity=_identity("rid-shortraw"), payload=payload, base_dir=str(tmp_path),
        )


def test_arbitrary_object_deep_rejected(tmp_path):
    payload = _complete_payload()
    payload["scientific_result"]["b3_observability"]["diagnostics"]["aggregate"]["ok_count"] = object()
    with pytest.raises(R.ResultArtifactPersistenceError):
        R.finalize_result_artifact(
            status=R.STATUS_COMPLETED, command="execute-b3-observability-live",
            identity=_identity("rid-arbobj"), payload=payload, base_dir=str(tmp_path),
        )


def test_safe_science_and_accounting_still_finalizes(tmp_path):
    # The stricter nested allowlist must NOT drop legitimate governed science/accounting (§29).
    written = R.finalize_result_artifact(
        status=R.STATUS_COMPLETED, command="execute-b3-observability-live",
        identity=_identity("rid-ir2-safe"), payload=_complete_payload(), base_dir=str(tmp_path),
    )
    art: Any = R.read_result_artifact(written.path)
    R.verify_artifact(art, expected_identity=_identity("rid-ir2-safe"))
    sci = art["result"]["scientific_result"]
    assert sci["b3_observability"]["diagnostics"]["aggregate"]["ok_count"] == 41  # fact-recall
    assert sci["b3_observability"]["qa_value_observability"]["qa_decision"]["verdict"] == "YES"
    assert sci["scientific_outputs"]["PER_NOTEBOOK_ISOLATION_EVIDENCED"] == "YES"  # isolation
    assert sci["gd_calls"] == 26 and sci["vector_queries"] == 26  # accounting
    assert art["result"]["index_metrics"] == {"indexed": 24, "expected": 24}  # index metrics


# =========================================================================== #
# PN02D-RH-IR3-M1 REMEDIATION-3 TESTS (Codex Review #3: path-sensitive schema)
# =========================================================================== #
def _finalize_expect_rejected(tmp_path, run_id, payload):
    with pytest.raises(R.ResultArtifactPersistenceError):
        R.finalize_result_artifact(
            status=R.STATUS_COMPLETED, command="execute-b3-observability-live",
            identity=_identity(run_id), payload=payload, base_dir=str(tmp_path),
        )
    # rejection happens BEFORE canonical publication: no final artifact, no _SUCCESS marker.
    assert not R._final_path(run_id, str(tmp_path)).exists()
    assert not R._success_marker_path(run_id, str(tmp_path)).exists()


def test_qa_decision_under_scientific_outputs_rejected(tmp_path):
    payload = _complete_payload()
    payload["scientific_result"]["scientific_outputs"]["qa_decision"] = {"rule": "Q1", "verdict": "YES"}
    _finalize_expect_rejected(tmp_path, "rid-reloc-1", payload)


def test_qa_decision_under_diagnostics_rejected(tmp_path):
    payload = _complete_payload()
    payload["scientific_result"]["b3_observability"]["diagnostics"]["qa_decision"] = {"rule": "Q1"}
    _finalize_expect_rejected(tmp_path, "rid-reloc-2", payload)


def test_forbidden_fact_count_wrong_path_rejected(tmp_path):
    # forbidden_fact_count is a qa_value field; it must NOT be accepted under diagnostics.
    payload = _complete_payload()
    payload["scientific_result"]["b3_observability"]["diagnostics"]["forbidden_fact_count"] = 0
    _finalize_expect_rejected(tmp_path, "rid-reloc-3", payload)


def test_arm_metrics_wrong_path_rejected(tmp_path):
    # arm_metrics is a qa_value field; it must NOT be accepted under diagnostics.
    payload = _complete_payload()
    payload["scientific_result"]["b3_observability"]["diagnostics"]["arm_metrics"] = []
    _finalize_expect_rejected(tmp_path, "rid-reloc-4", payload)


def test_provider_key_under_scientific_outputs_rejected(tmp_path):
    # a provider-accounting key (a scientific_result direct child) relocated into scientific_outputs.
    payload = _complete_payload()
    payload["scientific_result"]["scientific_outputs"]["gd_calls"] = 26
    _finalize_expect_rejected(tmp_path, "rid-reloc-5", payload)


def test_diagnostics_only_key_under_qa_value_rejected(tmp_path):
    # a diagnostics-only aggregate key relocated into qa_value_observability.
    payload = _complete_payload()
    payload["scientific_result"]["b3_observability"]["qa_value_observability"]["ok_count"] = 41
    _finalize_expect_rejected(tmp_path, "rid-reloc-6", payload)


def test_known_key_in_wrong_block_rejected(tmp_path):
    # summary case for the report flag TEST_KNOWN_KEY_IN_WRONG_BLOCK_REJECTED.
    payload = _complete_payload()
    payload["scientific_result"]["scientific_outputs"]["arm_metrics"] = []
    _finalize_expect_rejected(tmp_path, "rid-reloc-known", payload)


def test_same_name_different_path_allowed(tmp_path):
    # per_query_arm is legitimately present in BOTH diagnostics and qa_value; each validated by its
    # OWN block schema. A safe artifact carrying both finalizes (same-name-different-path, §22).
    payload = _complete_payload()
    payload["scientific_result"]["b3_observability"]["diagnostics"]["per_query_arm"] = [
        {"query_id": "q1", "arm": "QA-V", "ok_count": 1}
    ]
    payload["scientific_result"]["b3_observability"]["qa_value_observability"]["per_query_arm"] = [
        {"query_id": "q1", "arm": "QA-V", "is_negative": False, "abstained": False}
    ]
    written = R.finalize_result_artifact(
        status=R.STATUS_COMPLETED, command="execute-b3-observability-live",
        identity=_identity("rid-samename"), payload=payload, base_dir=str(tmp_path),
    )
    art: Any = R.read_result_artifact(written.path)
    R.verify_artifact(art, expected_identity=_identity("rid-samename"))


def test_realistic_complete_safe_artifact_finalizes(tmp_path):
    # A fuller realistic governed B3 artifact exercising fact-recall + QA-value + qa_decision +
    # scientific_outputs + isolation + provider accounting + index metrics — must finalize with
    # NO data loss under the path-sensitive schema.
    payload = {
        "result": "COMPLETE",
        "provider_traffic": 158,
        "technical_status": "COMPLETED",
        "index_metrics": {"indexed": 24, "expected": 24, "reposts": 0, "resumed": 1},
        "scientific_result": {
            "stage1_status": "PASS",
            "isolation_evidenced": "YES",
            "leakage_count": 0,
            "leakage_rate": 0.0,
            "violations": [],
            "retrieval_verdict": "INCONCLUSIVE",
            "multihop_verdict": "NO",
            "scientific_outputs": {
                "PER_NOTEBOOK_ISOLATION_EVIDENCED": "YES",
                "PER_NOTEBOOK_GRAPH_QA_VALUE_EVIDENCED": "YES",
                "PER_NOTEBOOK_GRAPH_RETRIEVAL_VALUE_EVIDENCED": "INCONCLUSIVE",
                "PER_NOTEBOOK_MULTIHOP_INCREMENTAL_VALUE_EVIDENCED": "NO",
            },
            "query_embedding_attempts": 26,
            "gd_calls": 26,
            "vector_queries": 26,
            "b3_observability": {
                "report_kind": "PN02_B3_FACT_RECALL_OBSERVABILITY",
                "mode": "OBSERVABILITY_ONLY",
                "expected_pair_count": 72,
                "completed_diagnostic_pair_count": 72,
                "completeness": "COMPLETE",
                "diagnostics": {
                    "aggregate": {"ok_count": 41, "positive_query_arm_count": 21},
                    "per_query_arm": [
                        {"query_id": "q1", "arm": "QA-V", "ok_count": 1, "notebook_id": "nb1"}
                    ],
                },
                "qa_value_observability": {
                    "version": 1,
                    "mode": "OBSERVABILITY_ONLY",
                    "isolation_evidenced": "YES",
                    "expected_pair_count": 72,
                    "observed_pair_count": 72,
                    "applicability": {"P1_fact_recall": "positive", "S3_forbidden_facts": "all"},
                    "arm_metrics": [
                        {"arm": "QA-GD", "answer_required_fact_recall": 0.65, "forbidden_fact_count": 0}
                    ],
                    "per_query_arm": [{"query_id": "q1", "arm": "QA-GD", "is_negative": False}],
                    "forbidden_fact_count": 0,
                    "qa_decision": {"rule": "Q1", "verdict": "YES", "positive_arms": ["QA-GD", "QA-V+GD"]},
                    "qa_value_observability_complete": True,
                },
            },
        },
    }
    written = R.finalize_result_artifact(
        status=R.STATUS_COMPLETED, command="execute-b3-observability-live",
        identity=_identity("rid-realistic"), payload=payload, base_dir=str(tmp_path),
    )
    art: Any = R.read_result_artifact(written.path)  # persisted bytes reparse
    R.verify_artifact(art, expected_identity=_identity("rid-realistic"))
    sci = art["result"]["scientific_result"]
    assert sci["b3_observability"]["diagnostics"]["aggregate"]["ok_count"] == 41  # fact recall
    assert sci["b3_observability"]["qa_value_observability"]["qa_decision"]["verdict"] == "YES"
    assert sci["b3_observability"]["qa_value_observability"]["arm_metrics"][0]["forbidden_fact_count"] == 0
    assert sci["scientific_outputs"]["PER_NOTEBOOK_GRAPH_QA_VALUE_EVIDENCED"] == "YES"
    assert sci["gd_calls"] == 26  # provider accounting
    assert art["result"]["index_metrics"]["reposts"] == 0  # index metrics


# =========================================================================== #
# PN02D-RH-IR4 REMEDIATION-4 TESTS (Codex Review #4: type-confusion + non-string keys)
# =========================================================================== #
# --------------------------------------------------------------------------- #
# IR4-H1 — a named governed block cannot be a scalar and still finalize COMPLETE
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "block_path,scalar",
    [
        (("b3_observability", "diagnostics"), "not-a-dict"),
        (("b3_observability", "qa_value_observability"), 1),
        (("scientific_outputs",), "YES"),
        (("b3_observability",), "scalar"),
    ],
)
def test_named_block_as_scalar_rejected(tmp_path, block_path, scalar):
    payload = _complete_payload()
    node = payload["scientific_result"]
    for k in block_path[:-1]:
        node = node[k]
    node[block_path[-1]] = scalar  # replace a governed block with a scalar
    _finalize_expect_rejected(tmp_path, "rid-ir4-scalar", payload)


def test_qa_decision_as_scalar_rejected(tmp_path):
    payload = _complete_payload()
    payload["scientific_result"]["b3_observability"]["qa_value_observability"]["qa_decision"] = "YES"
    _finalize_expect_rejected(tmp_path, "rid-ir4-qadec", payload)


def test_verify_artifact_rejects_completed_with_scalar_block(tmp_path):
    # Defense-in-depth: even a hand-built artifact whose mandatory block is a scalar must fail
    # completeness verification (mandatory_block_not_a_dict).
    ident = _identity("rid-ir4-verify")
    envelope = {
        "artifact_schema_version": R.RESULT_ARTIFACT_SCHEMA_VERSION,
        "status": R.STATUS_COMPLETED,
        "command": "execute-b3-observability-live",
        "identity": ident.as_dict(),
        "claimed": True,
        "provider_traffic": 0,
        "technical_status": "COMPLETED",
        "result_state": "COMPLETE",
        "result": {
            "scientific_result": {
                "scientific_outputs": "YES",  # scalar where a dict block is mandatory
                "query_embedding_attempts": 26, "gd_calls": 26, "vector_queries": 26,
                "b3_observability": {"diagnostics": {}, "qa_value_observability": {}},
            }
        },
        "written_at_utc": "2026-01-01T00:00:00+00:00",
    }
    with pytest.raises(R.ResultArtifactPersistenceError):
        R.verify_artifact(cast(Any, envelope))


# --------------------------------------------------------------------------- #
# IR4-M1 — non-string dict keys are rejected (never coerced+persisted)
# --------------------------------------------------------------------------- #
def test_non_string_key_in_named_block_rejected(tmp_path):
    payload = _complete_payload()
    payload["scientific_result"]["b3_observability"]["diagnostics"][1] = "smuggled"  # int key
    _finalize_expect_rejected(tmp_path, "rid-ir4-intkey", payload)


def test_non_string_key_inside_list_element_rejected(tmp_path):
    payload = _complete_payload()
    payload["scientific_result"]["b3_observability"]["diagnostics"]["per_query_arm"] = [
        {1: "smuggled", "query_id": "q1"}  # non-string key inside a list element
    ]
    _finalize_expect_rejected(tmp_path, "rid-ir4-listkey", payload)


def test_non_string_key_in_index_metrics_rejected(tmp_path):
    # index_metrics is validated content-safe (allowed_keys=None) but non-string keys still rejected.
    payload = _complete_payload()
    payload["index_metrics"] = {1: "x", "indexed": 24}
    _finalize_expect_rejected(tmp_path, "rid-ir4-idxkey", payload)


def test_bool_key_rejected(tmp_path):
    # bool is an int subclass; a bool dict key must also be rejected as non-string.
    payload = _complete_payload()
    payload["scientific_result"]["b3_observability"]["diagnostics"][True] = "x"
    _finalize_expect_rejected(tmp_path, "rid-ir4-boolkey", payload)


def test_safe_artifact_still_finalizes_after_ir4(tmp_path):
    # regression: the IR4 hardening must not reject a legitimate all-string, all-dict-block artifact.
    written = R.finalize_result_artifact(
        status=R.STATUS_COMPLETED, command="execute-b3-observability-live",
        identity=_identity("rid-ir4-safe"), payload=_complete_payload(), base_dir=str(tmp_path),
    )
    art: Any = R.read_result_artifact(written.path)
    R.verify_artifact(art, expected_identity=_identity("rid-ir4-safe"))
    assert R._success_marker_path("rid-ir4-safe", str(tmp_path)).exists()
