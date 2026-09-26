"""PN02D-B3Y — provider-free runtime import-readiness guard regression tests.

Kept SEPARATE from the B3D execution-surface suite (which wholesale-fakes the real engine
and therefore never exercises the live boot import path). These tests exercise the REAL
pre-claim import-readiness guard and its wiring inside ``RealB1Driver.run``:

Root cause reproduced: a broken launch import environment (repo root absent from sys.path
so the first-party top-level ``commands`` package is unresolvable) must FAIL CLOSED AFTER
mint validation but BEFORE the irreversible one-shot ledger claim — consuming no grant and
contacting no provider (the B3Y grant-burn condition).

No network, no real provider secrets, no real durable ledger, no mint of a reusable
authorization for a real run.
"""

from __future__ import annotations

import sys
from contextlib import contextmanager
from unittest import mock

import graphrag_pn02db0cb_common as C
import pytest

import open_notebook.integrations.graphrag.eval.authmintlivepn02d as authmint
from open_notebook.integrations.graphrag.eval.authmintlivepn02d import (
    EXPECTED_B3B_CHECKPOINT_TAG,
    frozen_b3b_operator_grant_template,
    mint_live_b3_provider_run_authorization,
)
from open_notebook.integrations.graphrag.eval.datasetpn02 import load_fixture
from open_notebook.integrations.graphrag.eval.driver_live_pn02d import RealB1Driver
from open_notebook.integrations.graphrag.eval.runtime_import_readiness_pn02d import (
    REQUIRED_LIVE_IMPORTS,
    LiveRuntimeImportReadinessError,
    assert_live_runtime_import_readiness,
    check_live_runtime_import_readiness,
)

_FORBIDDEN_DIAGNOSTIC_TOKENS = (
    "prompt",
    "source_content",
    "answer",
    "api_key",
    "apikey",
    "token",
    "secret",
    "sk-",
)


class _BlockCommandsFinder:
    """A sys.meta_path finder that makes the first-party top-level ``commands`` package
    unresolvable — reproducing a launch where the repo root is absent from sys.path."""

    def find_spec(self, name, path=None, target=None):  # noqa: D401,ANN001
        if name == "commands" or name.startswith("commands."):
            raise ModuleNotFoundError(f"No module named {name!r}", name="commands")
        return None


@contextmanager
def _block_top_level_commands():
    finder = _BlockCommandsFinder()
    saved = {k: v for k, v in sys.modules.items() if k == "commands" or k.startswith("commands.")}
    for k in list(saved):
        del sys.modules[k]
    sys.meta_path.insert(0, finder)
    try:
        yield
    finally:
        try:
            sys.meta_path.remove(finder)
        except ValueError:
            pass
        sys.modules.update(saved)


@contextmanager
def _trust_patch(tag, reader):
    with mock.patch.object(
        authmint, "current_approved_b3b_checkpoint", return_value=tag
    ), mock.patch.object(authmint, "_build_trusted_b1_r2_reader", return_value=reader):
        yield


class _EnforcerRecorder:
    """Fake one-shot enforcer: records the (would-be) claim and STOPS before it runs, so no
    real ledger claim or provider action occurs. Reaching it proves the guard passed."""

    def __init__(self) -> None:
        self.calls: list = []

    def __call__(self, *, operator_grant, execution_kind, ledger_path):  # noqa: ANN001
        self.calls.append((operator_grant.run_id, execution_kind))
        raise _StopAtClaim()


class _StopAtClaim(Exception):
    pass


def _grant(run_id: str):
    return frozen_b3b_operator_grant_template(
        run_id=run_id,
        implementation_checkpoint_commit=C.TEST_COMMIT,
        implementation_checkpoint_tag=C.TEST_TAG,
        b1_r2_checkpoint=EXPECTED_B3B_CHECKPOINT_TAG,
        approved_git_commit=C.TEST_COMMIT,
        approved_git_tag=C.TEST_TAG,
    )


# --------------------------------------------------------------------------- #
# guard-level tests (no driver)
# --------------------------------------------------------------------------- #


def test_b3_live_boot_path_lazy_imports_resolvable_preclaim():
    # Valid launch env (repo root on sys.path): the full explicit live-path import set
    # resolves, including the exact previously-failing surface.
    report = check_live_runtime_import_readiness()
    assert report.status == "OK"
    assert report.failed_import is None
    assert "commands.embedding_commands" in report.checked_imports
    # the guard must PASS silently (no exception) on a valid launch.
    ok = assert_live_runtime_import_readiness()
    assert ok.status == "OK"


def test_guard_checks_the_exact_b3y_failing_surface_and_symbols():
    # The exact B3Y surface is checked FIRST with both required symbols.
    module_name, symbols = REQUIRED_LIVE_IMPORTS[0]
    assert module_name == "commands.embedding_commands"
    assert symbols == ("EmbedSourceInput", "embed_source_command")


def test_b3_live_boot_path_missing_commands_fails_closed():
    # Reproduce the B3Y invalid-launch condition: `commands` unresolvable.
    with _block_top_level_commands():
        with pytest.raises(LiveRuntimeImportReadinessError) as ei:
            assert_live_runtime_import_readiness()
    report = ei.value.report
    assert report.status == "FAIL"
    assert report.failed_import == "commands.embedding_commands"
    assert report.missing_module_name == "commands"
    assert report.error_type == "ModuleNotFoundError"


def test_module_not_found_name_diagnostic_is_content_safe():
    with _block_top_level_commands():
        with pytest.raises(LiveRuntimeImportReadinessError) as ei:
            assert_live_runtime_import_readiness()
    safe = ei.value.as_safe_dict()
    assert safe["error_type"] == "ModuleNotFoundError"
    assert safe["missing_module_name"] == "commands"
    assert safe["classification"] == "LOCAL_RUNTIME_IMPORT_UNRESOLVABLE"
    # module NAME is captured; NO raw source/prompt/answer/credential content anywhere.
    blob = repr(safe).lower()
    for tok in _FORBIDDEN_DIAGNOSTIC_TOKENS:
        assert tok not in blob


# --------------------------------------------------------------------------- #
# driver-level tests (real guard wired into RealB1Driver.run, fake seams)
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_preclaim_guard_prevents_grant_burn(tmp_path):
    # Invalid launch env -> guard fails AFTER mint, BEFORE the one-shot claim.
    fx = load_fixture()
    reader = C.b1r2_reader_ok(tag=EXPECTED_B3B_CHECKPOINT_TAG, peel=C.TEST_COMMIT, head=C.TEST_COMMIT)
    bundle = C.build_live_seams(fx)
    enforcer = _EnforcerRecorder()
    ledger_path = str(tmp_path / "live_auth_consumption.sqlite")

    with _trust_patch(EXPECTED_B3B_CHECKPOINT_TAG, reader), _block_top_level_commands():
        with pytest.raises(LiveRuntimeImportReadinessError) as ei:
            await RealB1Driver(
                fx,
                bundle.seams,
                execution_kind="B3B",
                mint_fn=mint_live_b3_provider_run_authorization,
                one_shot_enforcer=enforcer,
                oneshot_ledger_path=ledger_path,
            ).run(
                operator_grant=_grant("pn02d-b3y-guard-missing"),
                git_baseline_attestation=C.clean_git_baseline(),
            )

    assert ei.value.report.missing_module_name == "commands"
    # the one-shot claim was NEVER reached -> grant not burned.
    assert enforcer.calls == []
    # provider-bound Boot 2 was NEVER reached.
    assert len(bundle.controller.started) == 0
    # no durable ledger was created/mutated by the failed pre-claim run.
    import os

    assert not os.path.exists(ledger_path)


@pytest.mark.asyncio
async def test_valid_import_readiness_allows_claim_stage(tmp_path):
    # Valid launch env -> guard passes -> flow REACHES the one-shot claim seam (recorded),
    # proving the guard does not block a healthy launch. No real claim/provider is performed.
    fx = load_fixture()
    reader = C.b1r2_reader_ok(tag=EXPECTED_B3B_CHECKPOINT_TAG, peel=C.TEST_COMMIT, head=C.TEST_COMMIT)
    bundle = C.build_live_seams(fx)
    enforcer = _EnforcerRecorder()
    ledger_path = str(tmp_path / "live_auth_consumption.sqlite")

    with _trust_patch(EXPECTED_B3B_CHECKPOINT_TAG, reader):
        with pytest.raises(_StopAtClaim):
            await RealB1Driver(
                fx,
                bundle.seams,
                execution_kind="B3B",
                mint_fn=mint_live_b3_provider_run_authorization,
                one_shot_enforcer=enforcer,
                oneshot_ledger_path=ledger_path,
            ).run(
                operator_grant=_grant("pn02d-b3y-guard-valid"),
                git_baseline_attestation=C.clean_git_baseline(),
            )

    # guard passed -> claim seam reached exactly once, before Boot 2.
    assert enforcer.calls == [("pn02d-b3y-guard-valid", "B3B")]
    assert len(bundle.controller.started) == 0
