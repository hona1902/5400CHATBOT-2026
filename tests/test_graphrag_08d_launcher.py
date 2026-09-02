"""GraphRAG-08D launcher import-path preflight tests.

Offline / eval-only (no provider, no live sidecar, no DB, no DEV/HOLDOUT). Covers
task §5-§11/§18: deterministic repo-root resolution, the exact ``commands`` import
surface required by ``runner08._vector_embed_all``, faithful reproduction of the
attempt-#4 bad launcher context (``No module named 'commands'``), correction, cwd
independence, process-local sys.path (no PYTHONPATH/env leak), the fail-closed
ordering guarantee (no normal-DB read / sidecar / isolation / Source creation
before the import preflight), and the methodology freeze.
"""

from __future__ import annotations

import asyncio
import contextlib
import importlib
import inspect
import json
import os
import sys
from pathlib import Path

import pytest

from open_notebook.integrations.graphrag.eval import dataset08 as d
from open_notebook.integrations.graphrag.eval import launcher_preflight08 as lp
from open_notebook.integrations.graphrag.eval import precheck08 as pc
from open_notebook.integrations.graphrag.eval import preflight08 as pf
from open_notebook.integrations.graphrag.eval import runner08

FIXTURE_HASH = "a58a68535c345e18f0263904f818e4e2068a164056408665d8bb9233eceb143d"


# ---- helpers ---------------------------------------------------------------


@contextlib.contextmanager
def _simulate_attempt4_context(repo_root: Path):
    """Reproduce the attempt-#4 import environment inside this process.

    Removes every ``sys.path`` entry that resolves to the repository root (the
    absolute entry AND the ``''`` cwd entry when cwd is the root) and purges the
    ``commands`` package from ``sys.modules``, so ``commands`` becomes unimportable
    exactly as it was for a scratchpad driver whose ``sys.path[0]`` was not the repo
    root. Everything is restored on exit."""
    target = repo_root.resolve()
    saved_path = list(sys.path)
    saved_modules = {
        name: mod
        for name, mod in sys.modules.items()
        if name == "commands" or name.startswith("commands.")
    }
    kept = []
    for entry in sys.path:
        try:
            resolved = Path(entry).resolve() if entry else Path.cwd().resolve()
        except OSError:
            kept.append(entry)
            continue
        if resolved == target:
            continue  # drop the repo-root entry (incl. '' when cwd == root)
        kept.append(entry)
    sys.path[:] = kept
    for name in list(sys.modules):
        if name == "commands" or name.startswith("commands."):
            del sys.modules[name]
    importlib.invalidate_caches()
    try:
        yield
    finally:
        sys.path[:] = saved_path
        for name in list(sys.modules):
            if name == "commands" or name.startswith("commands."):
                del sys.modules[name]
        sys.modules.update(saved_modules)
        importlib.invalidate_caches()


# ---- §18 repo-root resolution ----------------------------------------------


def test_resolve_repo_root_finds_markers():
    root = lp.resolve_repo_root()
    assert root is not None
    for marker in ("pyproject.toml", "commands/__init__.py", "open_notebook/__init__.py"):
        assert (root / marker).exists(), marker


def test_resolve_repo_root_is_cwd_independent(tmp_path, monkeypatch):
    """§6: resolution derives from the module file, not the shell cwd."""
    baseline = lp.resolve_repo_root()
    monkeypatch.chdir(tmp_path)  # a sibling/foreign working directory
    assert lp.resolve_repo_root() == baseline
    # and again from a nested foreign dir
    nested = tmp_path / "a" / "b"
    nested.mkdir(parents=True)
    monkeypatch.chdir(nested)
    assert lp.resolve_repo_root() == baseline


# ---- §18 commands importability in a corrected context ---------------------


def test_full_index_import_surface_importable_by_default():
    """In the normal (pytest) context the exact surface imports and is ready."""
    result = lp.run_launcher_preflight()
    assert result.ready is True
    assert result.repo_root_resolved is True
    assert result.commands_importable is True
    assert result.full_index_import_surface_ready is True
    assert result.reason_code == lp.LauncherReasonCode.READY


def test_import_surface_matches_runner08_vector_embed_all():
    """§9 non-fake: the declared surface is EXACTLY what the runner imports."""
    src = inspect.getsource(runner08.GraphRAG08EvalRunner._vector_embed_all)
    assert "from commands.embedding_commands import" in src
    (module, attrs), = lp.FULL_INDEX_IMPORT_SURFACE
    assert module == "commands.embedding_commands"
    for attr in attrs:
        assert attr in src, attr


# ---- §10 historical attempt-#4 bad context is DETECTED ---------------------


def test_attempt4_bad_context_detected(monkeypatch):
    root = lp.resolve_repo_root()
    assert root is not None
    with _simulate_attempt4_context(root):
        # sanity: the raw context truly cannot import commands
        with pytest.raises(ModuleNotFoundError):
            importlib.import_module("commands")
        # observe-only preflight must catch it (no self-heal)
        result = lp.run_launcher_preflight(mutate_sys_path=False)
        assert result.ready is False
        assert result.commands_importable is False
        assert result.full_index_import_surface_ready is False
        assert result.reason_code == lp.LauncherReasonCode.COMMANDS_NOT_IMPORTABLE
        with pytest.raises(lp.LauncherImportPathError) as ei:
            lp.require_launcher_ready(result)
        assert ei.value.reason_code == lp.LauncherReasonCode.COMMANDS_NOT_IMPORTABLE


def test_attempt4_bad_context_self_heals_when_allowed(monkeypatch):
    """§4: from the same bad context, the default self-healing preflight fixes it."""
    root = lp.resolve_repo_root()
    assert root is not None
    with _simulate_attempt4_context(root):
        result = lp.run_launcher_preflight(mutate_sys_path=True)
        assert result.added_repo_root_to_sys_path is True
        assert result.ready is True
        assert result.commands_importable is True
        assert result.reason_code == lp.LauncherReasonCode.READY
        lp.require_launcher_ready(result)  # does not raise


def test_repo_root_unresolved_reason(monkeypatch):
    monkeypatch.setattr(lp, "resolve_repo_root", lambda: None)
    result = lp.run_launcher_preflight()
    assert result.ready is False
    assert result.repo_root_resolved is False
    assert result.repo_root is None
    assert result.reason_code == lp.LauncherReasonCode.REPO_ROOT_UNRESOLVED
    with pytest.raises(lp.LauncherImportPathError):
        lp.require_launcher_ready(result)


def test_surface_missing_symbol_reason(monkeypatch):
    """commands importable but a required symbol absent -> surface-not-ready."""
    monkeypatch.setattr(
        lp, "FULL_INDEX_IMPORT_SURFACE",
        (("commands.embedding_commands", ("embed_source_command", "___absent___")),),
    )
    result = lp.run_launcher_preflight()
    assert result.commands_importable is True
    assert result.full_index_import_surface_ready is False
    assert result.reason_code == lp.LauncherReasonCode.FULL_INDEX_SURFACE_NOT_IMPORTABLE


# ---- §4 process-local only: no PYTHONPATH / env mutation -------------------


def test_ensure_repo_root_does_not_touch_environment(monkeypatch):
    root = lp.resolve_repo_root()
    assert root is not None
    env_before = dict(os.environ)
    saved_path = list(sys.path)
    try:
        lp.ensure_repo_root_on_sys_path(root)
        # PYTHONPATH (and the wider environment) is untouched — process-local only.
        assert os.environ.get("PYTHONPATH") == env_before.get("PYTHONPATH")
        assert dict(os.environ) == env_before
    finally:
        sys.path[:] = saved_path


def test_ensure_repo_root_idempotent_when_present():
    root = lp.resolve_repo_root()
    assert root is not None
    # already importable in the pytest context -> no insert
    assert lp.ensure_repo_root_on_sys_path(root) is False


# ---- §8 ordering: preflight fails BEFORE any normal-DB / sidecar / source ---


def test_launcher_failure_blocks_before_db_sidecar_and_source(tmp_path, monkeypatch):
    calls = {"baseline": 0, "sidecar": 0, "seed": 0}

    def bad_preflight(*, mutate_sys_path=True):
        return lp.LauncherPreflight(
            repo_root=None, repo_root_resolved=False, import_path_ready=False,
            commands_importable=False, full_index_import_surface_ready=False,
            added_repo_root_to_sys_path=False,
            reason_code=lp.LauncherReasonCode.COMMANDS_NOT_IMPORTABLE,
        )

    async def spy_baseline():
        calls["baseline"] += 1
        return None

    def spy_sidecar():
        calls["sidecar"] += 1

    async def spy_seed():
        calls["seed"] += 1
        return "m", None

    monkeypatch.setattr(lp, "run_launcher_preflight", bad_preflight)
    monkeypatch.setattr(pf, "read_normal_db_baseline", spy_baseline)
    monkeypatch.setattr(pc, "start_sidecar", spy_sidecar)
    monkeypatch.setattr(pc, "seed_temp_embedding_model", spy_seed)

    st = asyncio.run(
        pc.run_full_benchmark(authorization_label="REAUTHORIZATION_5", artifact_dir=tmp_path)
    )

    # PROVIDER / SIDECAR / SOURCE-CREATION BEFORE PREFLIGHT = NO. Isolation is a
    # local import (not a module attribute); isolation_entered / created_ids below
    # prove the isolated runtime was never entered.
    assert calls == {"baseline": 0, "sidecar": 0, "seed": 0}
    assert st.state == "PREFLIGHT_FAIL"
    assert st.preflight_blocked is True
    assert st.failure_stage == "PREFLIGHT"
    assert st.failure_reason_code == lp.LauncherReasonCode.COMMANDS_NOT_IMPORTABLE
    assert st.isolation_entered is False
    assert st.sidecar_started is False
    assert st.created_ids == []
    assert st.normal_db_unchanged == pf.UNCHANGED_NOT_PROVEN

    # a durable, content-free preflight artifact was written before any live work
    art = json.loads((tmp_path / "preflight_failure.json").read_text())
    assert art["isolation_entered"] is False
    assert art["failure_stage"] == "PREFLIGHT"
    assert art["failure_reason_code"] == lp.LauncherReasonCode.COMMANDS_NOT_IMPORTABLE
    assert art["dev_executed"] == 0 and art["holdout_executed"] == 0
    assert art["value_decision_made"] is False
    assert art["launcher_preflight"]["reason_code"] == (
        lp.LauncherReasonCode.COMMANDS_NOT_IMPORTABLE
    )


def test_unexpected_launcher_error_fails_closed(tmp_path, monkeypatch):
    """A non-LauncherImportPathError (e.g. an unresolvable cwd) still fails closed
    under the umbrella code, before any live work, with durable telemetry."""
    calls = {"baseline": 0, "sidecar": 0}

    def boom(*, mutate_sys_path=True):
        raise RuntimeError("cwd vanished")

    async def spy_baseline():
        calls["baseline"] += 1
        return None

    def spy_sidecar():
        calls["sidecar"] += 1

    monkeypatch.setattr(lp, "run_launcher_preflight", boom)
    monkeypatch.setattr(pf, "read_normal_db_baseline", spy_baseline)
    monkeypatch.setattr(pc, "start_sidecar", spy_sidecar)

    st = asyncio.run(pc.run_full_benchmark(artifact_dir=tmp_path))
    assert calls == {"baseline": 0, "sidecar": 0}
    assert st.state == "PREFLIGHT_FAIL"
    assert st.preflight_blocked is True
    assert st.failure_stage == "PREFLIGHT"
    assert st.failure_reason_code == lp.LauncherReasonCode.IMPORT_PATH_INVALID
    art = json.loads((tmp_path / "preflight_failure.json").read_text())
    assert art["failure_reason_code"] == lp.LauncherReasonCode.IMPORT_PATH_INVALID
    # no raw exception text ("cwd vanished") leaked into the telemetry
    assert "cwd vanished" not in json.dumps(art)


def test_launcher_preflight_runs_before_normal_db_read(tmp_path, monkeypatch):
    """Positive ordering: when the launcher is READY the run proceeds to the
    normal-DB read (which we stub to fail-closed so no live work happens)."""
    order = []

    real_preflight = lp.run_launcher_preflight

    def tracking_preflight(*, mutate_sys_path=True):
        order.append("launcher")
        return real_preflight(mutate_sys_path=mutate_sys_path)

    async def stub_baseline():
        order.append("normal_db")
        # unreadable -> 08C gate stops the run before sidecar; keeps this test offline
        return pf.NormalDbBaseline(
            identity_readable=False, count_readable=False, model_baseline_readable=False,
            namespace=None, database=None, source_count=None,
            default_embedding_model_present=None,
            reason_code=pf.NormalDbReasonCode.IDENTITY_UNREADABLE,
        )

    def no_sidecar():
        order.append("sidecar")

    monkeypatch.setattr(lp, "run_launcher_preflight", tracking_preflight)
    monkeypatch.setattr(pf, "read_normal_db_baseline", stub_baseline)
    monkeypatch.setattr(pc, "start_sidecar", no_sidecar)

    st = asyncio.run(pc.run_full_benchmark(artifact_dir=tmp_path))
    assert order[0] == "launcher"
    assert "normal_db" in order and "sidecar" not in order
    assert order.index("launcher") < order.index("normal_db")
    assert st.state == "PREFLIGHT_FAIL"  # stopped by the 08C normal-DB gate
    assert st.failure_stage == "PREFLIGHT"


# ---- content-safe telemetry -------------------------------------------------


def test_launcher_preflight_dict_is_content_free():
    result = lp.run_launcher_preflight()
    blob = json.dumps(result.as_dict())
    # only the resolved repo-root path (non-secret) and coded booleans/strings
    assert set(result.as_dict()) == {
        "repo_root", "repo_root_resolved", "import_path_ready",
        "commands_importable", "full_index_import_surface_ready",
        "added_repo_root_to_sys_path", "reason_code",
    }
    assert "Traceback" not in blob and "Error" not in blob
    assert result.reason_code.startswith("LAUNCHER_")


# ---- §11 08C env/config + normal-DB fail-closed regression (not regressed) --


def test_08c_normal_db_gate_still_fail_closed():
    with pytest.raises(pf.NormalDbBaselineError):
        pf.require_readable_baseline(
            pf.NormalDbBaseline(
                identity_readable=False, count_readable=False,
                model_baseline_readable=False, namespace=None, database=None,
                source_count=None, default_embedding_model_present=None,
                reason_code=pf.NormalDbReasonCode.IDENTITY_UNREADABLE,
            )
        )
    assert pf.compare_normal_db(None, None) == pf.UNCHANGED_NOT_PROVEN


# ---- §15/§16 methodology + retry freeze -------------------------------------


def test_methodology_and_retry_freeze_unchanged():
    from open_notebook.integrations.graphrag.eval.runner08 import EvalRunConfig08

    ok, h = d.verify_integrity()
    assert ok and h == FIXTURE_HASH
    bench = d.load_benchmark08()
    assert len(bench.sources) == 75 and len(bench.queries) == 60
    assert EvalRunConfig08().max_index_attempts_per_source == 2
