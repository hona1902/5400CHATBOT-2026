"""GraphRAG-08D launcher import-path preflight (EVALUATION-ONLY).

Nothing in production imports this. It restores a TRUSTWORTHY Python import
environment invariant for the authorized full-run harness, fixing the attempt-#4
defect where a standalone driver could import ``open_notebook`` (an installed
editable package) yet fail — only AFTER creating all 75 canonical Sources — on
the lazy ``from commands.embedding_commands import ...`` inside
``runner08._vector_embed_all`` with ``ModuleNotFoundError: No module named
'commands'``.

Root cause (verified against the checkout):
  * ``commands`` is a TOP-LEVEL repository package (``commands/__init__.py``), a
    sibling of ``open_notebook/``.
  * ``pyproject.toml`` declares ``[tool.setuptools] package-dir = {"open_notebook"
    = "open_notebook"}``; the editable install therefore exposes ONLY
    ``open_notebook`` (a package-scoped ``__editable__`` finder). The repository
    root is NOT placed on ``sys.path`` by the install.
  * So ``commands`` is importable ONLY when the repository root is explicitly on
    ``sys.path`` (as pytest and ``uv run uvicorn`` / the worker arrange). A driver
    launched as ``uv run python <script>`` gets the SCRIPT's directory on
    ``sys.path[0]`` — not the repository root — so ``commands`` cannot be resolved.
  * ``_vector_embed_all`` imports ``commands`` LAZILY, so the failure surfaces late
    (after Source creation), never at a cheap preflight.

This module makes that failure impossible to reach at run time by resolving the
repository root DETERMINISTICALLY from this module's own file location (never from
``os.getcwd()`` / ``sys.path[0]`` / the shell), ensuring it is importable
(process-local ``sys.path`` insert only — never a persisted ``PYTHONPATH`` or
environment edit), and then verifying the EXACT import surface the full-index path
requires (task §9 — not a synthetic probe). If the surface cannot be imported the
full run FAILS CLOSED before any provider traffic, sidecar startup, temporary DB
creation, Source creation, or embedding (task §5/§8).

It records only content-free fields (module names are non-secret; no raw exception
text, provider payload, or secret is ever stored — task §5/§12).
"""

from __future__ import annotations

import importlib
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Tuple

# ---- content-safe reason codes (task §5) -----------------------------------


class LauncherReasonCode:
    READY = "LAUNCHER_IMPORT_PATH_READY"
    REPO_ROOT_UNRESOLVED = "LAUNCHER_REPO_ROOT_UNRESOLVED"
    #: The exact attempt-#4 signature: ``commands`` top-level package unresolvable.
    COMMANDS_NOT_IMPORTABLE = "LAUNCHER_COMMANDS_MODULE_NOT_IMPORTABLE"
    #: ``commands`` resolved but the required embedding-command symbols are absent.
    FULL_INDEX_SURFACE_NOT_IMPORTABLE = (
        "LAUNCHER_FULL_INDEX_IMPORT_SURFACE_NOT_IMPORTABLE"
    )
    #: Umbrella code named by task §5 for a generically invalid import path.
    IMPORT_PATH_INVALID = "LAUNCHER_IMPORT_PATH_INVALID"


#: Files/dirs that uniquely identify the repository root. Crucially this INCLUDES
#: the top-level ``commands`` package that the editable install does not expose, so
#: the resolver can never mistake ``open_notebook/`` (an installed package dir) or a
#: parent that merely contains a ``pyproject.toml`` for the true root.
_REPO_ROOT_MARKERS: Tuple[str, ...] = (
    "pyproject.toml",
    "commands/__init__.py",
    "open_notebook/__init__.py",
)

#: The EXACT import surface that ``runner08._vector_embed_all`` requires. Verifying
#: THIS (rather than a synthetic ``import open_notebook``) is what catches the
#: historical attempt-#4 configuration (task §9). (module, required attributes).
FULL_INDEX_IMPORT_SURFACE: Tuple[Tuple[str, Tuple[str, ...]], ...] = (
    ("commands.embedding_commands", ("EmbedSourceInput", "embed_source_command")),
)

#: The top-level package whose absence was the attempt-#4 failure.
_COMMANDS_PACKAGE = "commands"


class LauncherImportPathError(RuntimeError):
    """The launcher import environment is invalid (fail closed before live work)."""

    def __init__(self, reason_code: str, message: str) -> None:
        super().__init__(message)
        self.reason_code = reason_code


@dataclass(frozen=True)
class LauncherPreflight:
    """Content-free launcher import-path observation (task §7).

    ``repo_root`` is a filesystem path string (non-secret) or ``None`` when the
    repository root could not be resolved. No raw exception text is ever retained.
    """

    repo_root: Optional[str]
    repo_root_resolved: bool
    import_path_ready: bool
    commands_importable: bool
    full_index_import_surface_ready: bool
    added_repo_root_to_sys_path: bool
    reason_code: str

    @property
    def ready(self) -> bool:
        """All safety-critical import checks pass (task §7)."""
        return (
            self.repo_root_resolved
            and self.commands_importable
            and self.full_index_import_surface_ready
        )

    def as_dict(self) -> dict:
        return {
            "repo_root": self.repo_root,
            "repo_root_resolved": self.repo_root_resolved,
            "import_path_ready": self.import_path_ready,
            "commands_importable": self.commands_importable,
            "full_index_import_surface_ready": self.full_index_import_surface_ready,
            "added_repo_root_to_sys_path": self.added_repo_root_to_sys_path,
            "reason_code": self.reason_code,
        }


def _resolved(path: str) -> Optional[Path]:
    """Best-effort ``Path.resolve``; unreadable/invalid entries -> None."""
    if not path:
        return None
    try:
        return Path(path).resolve()
    except (OSError, ValueError, RuntimeError):
        return None


def resolve_repo_root() -> Optional[Path]:
    """Resolve the repository root DETERMINISTICALLY from this module's location.

    Walks up this file's ancestors and returns the first directory that contains
    the full marker set (task §4). Never consults ``os.getcwd()``, ``sys.path``, or
    the shell working directory, so it is invariant to how the driver was launched
    (task §6). Returns ``None`` if no ancestor satisfies the markers.
    """
    here = Path(__file__).resolve()
    for candidate in (here.parent, *here.parents):
        if all((candidate / marker).exists() for marker in _REPO_ROOT_MARKERS):
            return candidate
    return None


def repo_root_on_sys_path(repo_root: Path) -> bool:
    """True if the (resolved) repository root is already an importable ``sys.path``
    entry. The empty-string entry (cwd) counts only when cwd IS the repo root."""
    target = repo_root.resolve()
    for entry in sys.path:
        resolved = _resolved(entry) if entry else _resolved(str(Path.cwd()))
        if resolved is not None and resolved == target:
            return True
    return False


def ensure_repo_root_on_sys_path(repo_root: Path) -> bool:
    """Ensure the repository root is importable, process-locally.

    Inserts the root at ``sys.path[0]`` only when it is not already present. This is
    a PROCESS-LOCAL mutation only: it never writes ``PYTHONPATH`` or any environment
    variable and never persists beyond the current interpreter (task §4). Returns
    True if it added the entry, False if it was already importable.
    """
    if repo_root_on_sys_path(repo_root):
        return False
    sys.path.insert(0, str(repo_root))
    importlib.invalidate_caches()
    return True


def _importable(module: str, attrs: Tuple[str, ...] = ()) -> bool:
    """True if ``module`` imports and exposes every attribute in ``attrs``.

    Never leaks the underlying exception (task §5/§12): a failed import or a missing
    attribute both return False. Does NOT execute any provider call — importing a
    ``commands.*`` module only registers command specs and defines callables. (It
    does trigger ``commands/__init__``'s ``ensure_internal_no_proxy()``, a benign,
    idempotent, process-local ``no_proxy`` env touch that API startup / the worker /
    the real ``_vector_embed_all`` all perform anyway — never a ``.env`` write.)"""
    try:
        mod = importlib.import_module(module)
    except Exception:  # noqa: BLE001 - any import failure -> not importable
        return False
    return all(hasattr(mod, attr) for attr in attrs)


def run_launcher_preflight(*, mutate_sys_path: bool = True) -> LauncherPreflight:
    """Resolve the repo root, (optionally) make it importable, verify the surface.

    With ``mutate_sys_path=True`` (the run-time default) the preflight SELF-HEALS a
    driver launched from the wrong directory by inserting the deterministically
    resolved repo root onto ``sys.path`` (process-local), then verifies the exact
    full-index import surface. Fail-closed: if the root cannot be resolved, or the
    surface still cannot import, ``ready`` is False and ``reason_code`` says why.

    With ``mutate_sys_path=False`` it OBSERVES the current environment without
    changing it — used by the attempt-#4 regression to prove the raw bad context is
    detected (task §10).
    """
    repo_root = resolve_repo_root()
    if repo_root is None:
        return LauncherPreflight(
            repo_root=None,
            repo_root_resolved=False,
            import_path_ready=False,
            commands_importable=False,
            full_index_import_surface_ready=False,
            added_repo_root_to_sys_path=False,
            reason_code=LauncherReasonCode.REPO_ROOT_UNRESOLVED,
        )

    added = ensure_repo_root_on_sys_path(repo_root) if mutate_sys_path else False
    import_path_ready = repo_root_on_sys_path(repo_root)

    commands_importable = _importable(_COMMANDS_PACKAGE)
    surface_ready = commands_importable and all(
        _importable(module, attrs) for module, attrs in FULL_INDEX_IMPORT_SURFACE
    )

    if not commands_importable:
        reason = LauncherReasonCode.COMMANDS_NOT_IMPORTABLE
    elif not surface_ready:
        reason = LauncherReasonCode.FULL_INDEX_SURFACE_NOT_IMPORTABLE
    else:
        reason = LauncherReasonCode.READY

    return LauncherPreflight(
        repo_root=str(repo_root),
        repo_root_resolved=True,
        import_path_ready=import_path_ready,
        commands_importable=commands_importable,
        full_index_import_surface_ready=surface_ready,
        added_repo_root_to_sys_path=added,
        reason_code=reason,
    )


def require_launcher_ready(preflight: Optional[LauncherPreflight]) -> None:
    """Hard gate (task §5/§8): raise unless the launcher import surface is ready.

    Called BEFORE the normal-DB baseline read, sidecar startup, isolation, Source
    creation, and any provider traffic, so a full run can never begin — and can
    never reach ``_vector_embed_all`` — in an import environment that would abort it
    mid-corpus."""
    if preflight is None or not preflight.ready:
        reason = (
            preflight.reason_code
            if preflight is not None
            else LauncherReasonCode.IMPORT_PATH_INVALID
        )
        raise LauncherImportPathError(
            reason,
            f"launcher import path invalid ({reason}); refusing to start full run",
        )


__all__: List[str] = [
    "LauncherReasonCode",
    "LauncherImportPathError",
    "LauncherPreflight",
    "FULL_INDEX_IMPORT_SURFACE",
    "resolve_repo_root",
    "repo_root_on_sys_path",
    "ensure_repo_root_on_sys_path",
    "run_launcher_preflight",
    "require_launcher_ready",
]
