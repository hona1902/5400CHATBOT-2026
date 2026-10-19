"""Provider-free tests for the committed GraphRAG-09H live runner (F4-A).

No real provider / Docker / grant / live execution: the governed executor is replaced
with a fake, and the only real work exercised is root-context establishment and the
provider-free canonical-chat seam import (symbol resolution, no chat executed).
"""

import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

import run_graphrag_09h_live as runner

REPO_ROOT = Path(__file__).resolve().parents[1]


class _FakeVerdict:
    def __init__(self, name, value):
        self.name = name
        self.value = value


class _FakeResult:
    def __init__(self, verdict, reason, evidence):
        self.verdict = verdict
        self.reason = reason
        self.evidence = evidence


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


# ----------------------------------------------------------------- evidence export (runner level)
def test_main_execute_requires_evidence_output():
    # §9: --execute without --evidence-output is a fail-closed preflight failure and
    # runs NOTHING governed (no git/deps touched before the required-arg check).
    assert runner.main(["--execute"]) == runner.EXIT_EVIDENCE_PREFLIGHT_FAIL


def test_main_check_context_ignores_evidence_output(tmp_path):
    # §11: --check-context never creates a live evidence artifact, even if a path is given.
    target = tmp_path / "should_not_exist.json"
    assert runner.main(["--check-context", "--evidence-output", str(target)]) == 0
    assert not target.exists()


def test_execute_and_export_pass_writes_artifact(tmp_path):
    # §12/§29: governed PASS result is exported to the artifact; executed exactly once.
    import json

    out = tmp_path / "ev.json"
    calls = {"n": 0}

    async def fake_exec(deps, *, head_expected):
        calls["n"] += 1
        return _FakeResult(
            _FakeVerdict("PASS_BOUNDED_LIVE_PROVENANCE_PLUMBING", "09H_LIVE_A"),
            "ok",
            {"graphrag_query_count": 1},
        )

    outcome = runner.execute_and_export(
        deps_factory=lambda: object(),
        run_validation=fake_exec,
        head_expected="x",
        evidence_output=str(out),
        head="deadbeef",
        capture_tag="graphrag-09h-run6-provenance-expected-contract-approved",
    )
    assert calls["n"] == 1
    assert outcome.exit_code == runner.EXIT_PASS and outcome.export_ok is True
    doc = json.loads(out.read_bytes().decode("utf-8"))
    assert doc["head"] == "deadbeef"
    assert doc["capture_evidence_tag"].endswith("provenance-expected-contract-approved")
    assert doc["evidence"]["graphrag_query_count"] == 1


def test_execute_and_export_no_overwrite_does_not_execute(tmp_path):
    # §44/§45: a pre-existing target fails preflight BEFORE the governed executor runs.
    out = tmp_path / "ev.json"
    out.write_bytes(b"{}")
    calls = {"n": 0}

    async def fake_exec(deps, *, head_expected):
        calls["n"] += 1
        return _FakeResult(_FakeVerdict("X", "09H_LIVE_A"), "ok", {})

    try:
        runner.execute_and_export(
            deps_factory=lambda: object(),
            run_validation=fake_exec,
            head_expected="x",
            evidence_output=str(out),
        )
        raised = False
    except FileExistsError:
        raised = True
    assert raised is True
    assert calls["n"] == 0


def test_execute_and_export_write_failure_is_runner_failure_no_retry(tmp_path):
    # §23/§24/§46: export failure after a governed result is a distinct non-success exit
    # (5), preserves the governed verdict, and never reruns the executor.
    out = tmp_path / "ev.json"
    calls = {"n": 0}

    async def fake_exec(deps, *, head_expected):
        calls["n"] += 1
        return _FakeResult(
            _FakeVerdict("KILL_SWITCH_TRIGGERED", "09H_LIVE_D"),
            "final_provenance_mismatch",
            {"k": 1},
        )

    def boom(data, output_path, *, overwrite=False):
        raise OSError("synthetic write failure")

    outcome = runner.execute_and_export(
        deps_factory=lambda: object(),
        run_validation=fake_exec,
        head_expected="x",
        evidence_output=str(out),
        _writer=boom,
    )
    assert calls["n"] == 1
    assert outcome.exit_code == runner.EXIT_EVIDENCE_EXPORT_FAIL
    assert outcome.export_ok is False
    assert outcome.verdict_value == "09H_LIVE_D"
    assert outcome.reason == "final_provenance_mismatch"


def test_non_root_evidence_export_path_subprocess():
    # §43: from a cwd that is NOT the repo root, the committed runner imports cleanly
    # (no app-import regression) and creates a safe evidence artifact via the governed
    # export seam driven by a provider-free fake executor.
    import json

    prog = (
        "import sys, asyncio\n"
        f"sys.path.insert(0, {str(REPO_ROOT)!r})\n"
        "import run_graphrag_09h_live as r\n"
        "class V:\n"
        "    def __init__(s, n, v):\n"
        "        s.name = n; s.value = v\n"
        "class R:\n"
        "    verdict = V('PASS_BOUNDED_LIVE_PROVENANCE_PLUMBING', '09H_LIVE_A')\n"
        "    reason = 'ok'\n"
        "    evidence = {'graphrag_query_count': 1}\n"
        "async def fe(deps, *, head_expected):\n"
        "    return R()\n"
        "o = r.execute_and_export(deps_factory=lambda: object(), run_validation=fe,\n"
        "    head_expected='x', evidence_output=sys.argv[1], head='h', capture_tag='t')\n"
        "print('EXIT', o.exit_code, 'OK', o.export_ok)\n"
    )
    with tempfile.TemporaryDirectory() as td:
        artifact = str(Path(td) / "evidence.json")
        proc = subprocess.run(
            [sys.executable, "-c", prog, artifact],
            cwd=td,
            capture_output=True,
            text=True,
        )
        assert proc.returncode == 0, proc.stderr
        assert "EXIT 0 OK True" in proc.stdout
        doc = json.loads(Path(artifact).read_bytes().decode("utf-8"))
        assert doc["schema"] == "09H_live_run_evidence_v1"
        assert doc["evidence"]["graphrag_query_count"] == 1


def test_non_root_path_policy_regression(tmp_path, monkeypatch):
    # §35: from a non-root CWD, a relative --evidence-output is rejected deterministically,
    # an external absolute path is accepted, and nothing is created in the repo.
    monkeypatch.chdir(tmp_path)
    with pytest.raises(runner.EvidenceOutputPolicyError):
        runner.preflight_evidence_output("relative_from_cwd.json")
    abs_out = tmp_path / "abs_evidence.json"
    runner.preflight_evidence_output(str(abs_out))  # external absolute: no raise
    assert not abs_out.exists()  # preflight creates nothing
