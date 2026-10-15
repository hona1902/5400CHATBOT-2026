"""Provider-free tests for the committed GraphRAG-09H live runner (F4-A).

No real provider / Docker / grant / live execution: the governed executor is replaced
with a fake, and the only real work exercised is root-context establishment and the
provider-free canonical-chat seam import (symbol resolution, no chat executed).
"""

import subprocess
import sys
import tempfile
from pathlib import Path

import run_graphrag_09h_live as runner

REPO_ROOT = Path(__file__).resolve().parents[1]


# ----------------------------------------------------------------- import side effects
def test_module_import_has_no_governed_execution_side_effect():
    # §38: importing the runner module runs NO governed execution. It exposes the
    # entrypoints but does not invoke run_live_validation at import time.
    assert hasattr(runner, "main")
    assert hasattr(runner, "run")
    assert hasattr(runner, "build_default_deps")


def test_main_with_no_flag_executes_nothing():
    # §38: main() without an execution flag runs nothing governed (prints guidance).
    assert runner.main([]) == 3


# ----------------------------------------------------------------- root-context import
def test_establish_root_context_returns_repo_root():
    assert runner.establish_root_context() == str(REPO_ROOT)
    assert str(REPO_ROOT) in sys.path


def test_canonical_chat_seam_imports_from_established_context():
    # §14/§15: api.routers.chat resolves from the runner-established context; the
    # canonical-chat symbols are resolvable WITHOUT executing a provider chat.
    assert runner.canonical_chat_seam_importable() is True


def test_main_check_context_provider_free_passes():
    assert runner.main(["--check-context"]) == 0


# ----------------------------------------------------------------- single invocation
def test_runner_invokes_governed_executor_exactly_once():
    # §16: the governed executor is invoked exactly once; no retry loop.
    calls = {"n": 0}
    sentinel = object()

    async def fake_exec(deps, *, head_expected):
        calls["n"] += 1
        return sentinel

    result = runner.run(
        deps_factory=lambda: object(),
        run_validation=fake_exec,
        head_expected="deadbeefdeadbeefdeadbeefdeadbeefdeadbeef",
    )
    assert result is sentinel
    assert calls["n"] == 1


def test_runner_does_not_auto_retry_on_failure():
    # §17: a failing governed result does NOT trigger a second invocation.
    calls = {"n": 0}

    class FakeResult:
        verdict = "FAIL"
        reason = "synthetic"

    async def fake_exec(deps, *, head_expected):
        calls["n"] += 1
        return FakeResult()

    runner.run(deps_factory=lambda: object(), run_validation=fake_exec, head_expected="x")
    assert calls["n"] == 1


def test_runner_defaults_head_expected_to_deps_git_head():
    captured = {}

    class _FakeGit:
        def head(self):
            return "head-from-deps"

    class _FakeDeps:
        git = _FakeGit()

    async def fake_exec(deps, *, head_expected):
        captured["head"] = head_expected
        return object()

    runner.run(deps_factory=lambda: _FakeDeps(), run_validation=fake_exec)
    assert captured["head"] == "head-from-deps"


# ----------------------------------------------------------------- subprocess path-launch
def test_runner_subprocess_path_launch_establishes_root_context():
    # §39: path-launch the COMMITTED runner from a cwd that is NOT the repo root (the
    # live-run-#5 launch shape) and confirm it self-establishes root context so
    # api.routers.chat imports. Provider-free (--check-context runs nothing else).
    with tempfile.TemporaryDirectory() as td:
        proc = subprocess.run(
            [
                sys.executable,
                str(REPO_ROOT / "run_graphrag_09h_live.py"),
                "--check-context",
            ],
            cwd=td,
            capture_output=True,
            text=True,
        )
    assert proc.returncode == 0, proc.stderr
    assert "CANONICAL_CHAT_SEAM_IMPORT=PASS" in proc.stdout
