"""GraphRAG-08E.2 live diagnostic-cell PROVISIONER (EVALUATION-ONLY).

Nothing in production imports this. It realizes the 08E.1 cell-isolation CONTRACT
(``cell_isolation08``) with a real process/storage LIFECYCLE: for each experimental
cell — one ``(concurrency level, repetition)`` pair — it reserves a cell-owned,
provably-fresh LightRAG workspace, starts a FRESH pinned LightRAG process bound to
that workspace, verifies the process's identity/version/workspace before it may be
used, and on teardown stops that exact process tree and disposes ONLY that cell's own
workspace subdirectory — never the run root, never another cell, never foreign storage.

Selected isolation strategy (frozen at 08E.1, do not reopen): OPTION B — one FRESH
LightRAG process per cell + one UNIQUE per-cell WORKSPACE + one run-owned storage
boundary. Fresh process ⇒ no in-memory cache carryover; unique workspace subdirectory
⇒ no on-disk carryover of the LLM response cache, graph, KV, vector, or doc-status
state (pinned v1.5.6 source forensic, ``cell_isolation08`` module docstring).

This phase implements EXECUTION PLUMBING ONLY. It NEVER indexes a Source, calls a
provider, embeds, or performs GraphRAG insertion — those belong to a future,
separately-authorized live diagnostic. Importing this module and the offline test
suite start NO real process, make NO provider/network call, and mutate NO database:
the process/health primitives are INJECTED (a fake drives the lifecycle tests), and
the real Docker/health primitives are used only under an explicitly-authorized live
run. Readiness of a provisioned cell NEVER implies live authorization (that stays a
separate fail-closed guard in ``concurrency_diag08.run_sweep``).

Hard requirements carried from the 08E.1 independent review (enforced by tests here):
  * LOW-2 — provisioning is ATOMIC / self-cleaning: any failure AFTER the storage
    reservation disposes every partial resource itself and only then re-raises; a cell
    is reported PROVISIONED only when every stage verified AND no cleanup is pending.
  * LOW-3 — physical freshness: ``fresh_extraction_state`` is set only after the cell's
    workspace directory is verified ABSENT-or-created-empty on disk (never trusted from
    a boolean), so a stale workspace left by a crashed prior run cannot masquerade as
    fresh.

All logging/telemetry is content-free (ids, level, repetition, workspace, port, state,
timings) — never a credential, provider secret, Source/query text, or raw provider error.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import socket
import subprocess
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import (
    AsyncIterator,
    Dict,
    Optional,
    Protocol,
    Set,
    Tuple,
)

from loguru import logger

from open_notebook.integrations.graphrag.config import VERIFIED_LIGHTRAG_VERSION
from open_notebook.integrations.graphrag.eval.cell_isolation08 import (
    _LIGHTRAG_STORE_FILES,
    CellDisposal,
    CellIdentity,
    CellProvision,
    cell_storage_dir,
    sanitize_workspace,
)

#: Pinned diagnostic sidecar image (mirrors deploy/graphrag-poc compose; live-only).
DEFAULT_LIGHTRAG_IMAGE = f"ghcr.io/hkuds/lightrag:{VERIFIED_LIGHTRAG_VERSION}"
#: LightRAG's in-container HTTP port (deploy/graphrag-poc compose).
LIGHTRAG_CONTAINER_PORT = 9621
#: LightRAG's in-container working dir the run-owned storage root mounts onto.
LIGHTRAG_WORKING_DIR_MOUNT = "/app/data/rag_storage"
#: Loopback host only — a diagnostic cell is NEVER exposed to a broader network.
DEFAULT_DIAGNOSTIC_HOST = "127.0.0.1"

# Bounded lifecycle timings (content-free; a bad value can never create an infinite
# wait or a zero-delay hot loop). These are DIAGNOSTIC infrastructure knobs — NOT the
# frozen benchmark/full-run timeout, retry, or concurrency policy.
DEFAULT_STARTUP_TIMEOUT_S = 120.0
DEFAULT_HEALTH_POLL_INTERVAL_S = 3.0
DEFAULT_GRACEFUL_STOP_TIMEOUT_S = 15.0


# ---------------------------------------------------------------------------
# Provisioner error model (task §62). Content-safe: a message carries only ids /
# coarse labels, never a credential, provider payload, or raw error text.
# ---------------------------------------------------------------------------


class CellProvisionError(RuntimeError):
    """Base class for every provisioner failure (all fail-closed)."""


class CellProvisionConfigurationError(CellProvisionError):
    """The provisioner was configured with an unsafe/invalid parameter."""


class CellPathSafetyError(CellProvisionError):
    """A workspace/storage path escaped the run-owned diagnostic root (fail closed)."""


class CellFreshnessError(CellProvisionError):
    """The cell workspace could not be PROVEN physically fresh (LOW-3, fail closed)."""


class CellProvisionOwnershipError(CellProvisionError):
    """A cleanup/ownership boundary could not be proven — no destructive action taken."""


class CellProcessStartError(CellProvisionError):
    """The fresh LightRAG process could not be started."""


class CellHealthError(CellProvisionError):
    """The process never became healthy within the bounded startup timeout."""


class CellVersionMismatchError(CellProvisionError):
    """The started process reported a LightRAG version other than the pinned one."""


class CellWorkspaceMismatchError(CellProvisionError):
    """The started process is not bound to the cell's expected workspace (or it is
    unverifiable) — readiness is BLOCKED (task §16)."""


class CellCleanupError(CellProvisionError):
    """Teardown could not be POSITIVELY verified — residue may remain (fail closed)."""


# ---------------------------------------------------------------------------
# Explicit provision state machine (task §27). String constants (repo style).
# ---------------------------------------------------------------------------


class ProvisionState:
    PLANNED = "PLANNED"
    STORAGE_RESERVED = "STORAGE_RESERVED"
    PROCESS_STARTING = "PROCESS_STARTING"
    PROCESS_STARTED = "PROCESS_STARTED"
    HEALTH_VERIFIED = "HEALTH_VERIFIED"
    VERSION_VERIFIED = "VERSION_VERIFIED"
    WORKSPACE_VERIFIED = "WORKSPACE_VERIFIED"
    PROVISIONED = "PROVISIONED"
    TEARDOWN_STARTED = "TEARDOWN_STARTED"
    PROCESS_STOPPED = "PROCESS_STOPPED"
    STORAGE_DISPOSED = "STORAGE_DISPOSED"
    CLEANED = "CLEANED"
    # failure states
    FRESHNESS_FAILED = "FRESHNESS_FAILED"
    START_FAILED = "START_FAILED"
    HEALTH_FAILED = "HEALTH_FAILED"
    VERSION_FAILED = "VERSION_FAILED"
    WORKSPACE_VERIFY_FAILED = "WORKSPACE_VERIFY_FAILED"
    OWNERSHIP_FAILED = "OWNERSHIP_FAILED"
    CLEANUP_FAILED = "CLEANUP_FAILED"


# ---------------------------------------------------------------------------
# Path safety (task §24/§25). Every destructive action is gated on proving the
# target resolves BENEATH the exact run-owned diagnostic root.
# ---------------------------------------------------------------------------

_SAFE_IDENT = frozenset(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_"
)


def _is_safe_identifier(value: str) -> bool:
    """A content-free path SEGMENT: non-empty alnum/underscore only. Rejects ``..``,
    path separators, drive letters, and UNC roots by construction."""
    return bool(value) and all(ch in _SAFE_IDENT for ch in value)


def _norm(path: str) -> str:
    """Case/relative-normalised REAL path (resolves symlinks/junctions, §25/§68)."""
    return os.path.normcase(os.path.realpath(path))


def is_within_root(target: str, root: str) -> bool:
    """True iff ``target`` resolves strictly beneath ``root`` (both realpath-resolved,
    case-normalised for Windows). A path equal to the root, on another drive, or
    reachable only by escaping via ``..``/symlink is NOT within (task §24/§25/§52)."""
    nt, nr = _norm(target), _norm(root)
    if nt == nr:
        return False
    try:
        return os.path.commonpath([nt, nr]) == nr
    except ValueError:
        # Different drives / mixed absolute-relative → cannot be within.
        return False


def assert_cell_paths_safe(
    *, eval_root: str, run_id: str, workspace: str
) -> Tuple[str, str, str]:
    """Validate + derive the run-owned paths for a cell, failing closed on any escape.

    Returns ``(run_root, working_dir, cell_storage)`` where ``working_dir`` is the
    run-owned LightRAG working dir (``eval_root/run_id``) shared by the run's cells and
    ``cell_storage`` is this cell's own workspace subdirectory (``working_dir/workspace``)
    — the ONLY path the provisioner ever creates or destroys for the cell.

    Raises ``CellPathSafetyError`` if ``run_id``/``workspace`` are not content-free
    segments or the derived cell storage does not resolve beneath ``eval_root/run_id``
    beneath ``eval_root`` (task §24/§52)."""
    if not eval_root or not os.path.isabs(eval_root):
        raise CellProvisionConfigurationError(
            "eval_root must be a non-empty absolute diagnostic root"
        )
    if not _is_safe_identifier(run_id):
        raise CellPathSafetyError(f"unsafe run_id segment {run_id!r}")
    if workspace != sanitize_workspace(workspace) or not _is_safe_identifier(workspace):
        raise CellPathSafetyError(f"unsafe workspace segment {workspace!r}")
    run_root = os.path.join(eval_root, run_id)
    working_dir = run_root  # the run-owned LightRAG working dir (shared by the run)
    cell_storage = cell_storage_dir(working_dir, workspace)
    # The cell storage must resolve beneath the run root beneath the eval root, even
    # when the parents do not yet exist (validate the intended, un-created path too).
    intended_parent = os.path.dirname(os.path.normpath(cell_storage))
    if os.path.normcase(os.path.normpath(intended_parent)) != os.path.normcase(
        os.path.normpath(working_dir)
    ):
        raise CellPathSafetyError(
            f"workspace {workspace!r} does not sit directly under the run working dir"
        )
    return run_root, working_dir, cell_storage


# ---------------------------------------------------------------------------
# Physical freshness (task §5/§6/§7/§43, review LOW-3).
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FreshnessResult:
    """Physical (not logical) freshness of a cell's workspace directory."""

    dir_existed: bool
    empty: bool
    offending_stores: Tuple[str, ...]  # any prior mutable LightRAG state found

    @property
    def fresh(self) -> bool:
        # Fresh iff the directory did not pre-exist, OR it exists but is empty with no
        # known mutable LightRAG store. A pre-existing NON-empty workspace is never
        # fresh; a pre-existing empty one is only tolerated as the owned, just-created
        # reservation (state B, task §5).
        return not self.offending_stores and (not self.dir_existed or self.empty)


def scan_workspace_freshness(cell_storage: str) -> FreshnessResult:
    """PHYSICALLY inspect the cell workspace dir for prior mutable LightRAG state.

    Directory-level guarantee is primary (task §7): a pre-existing NON-empty workspace
    is not fresh. The explicit ``_LIGHTRAG_STORE_FILES`` scan is defense-in-depth
    (task §6) — correctness does NOT depend on that list being complete, because ANY
    entry in a pre-existing directory already makes it non-empty ⇒ non-fresh."""
    if not os.path.exists(cell_storage):
        return FreshnessResult(dir_existed=False, empty=True, offending_stores=())
    if not os.path.isdir(cell_storage):
        # A file where the workspace dir should be is prior/foreign state → not fresh.
        return FreshnessResult(
            dir_existed=True, empty=False, offending_stores=("NOT_A_DIRECTORY",)
        )
    try:
        entries = os.listdir(cell_storage)
    except OSError as exc:  # unreadable → cannot prove fresh (fail closed)
        raise CellFreshnessError(
            f"cannot read cell workspace to prove freshness ({type(exc).__name__})"
        ) from exc
    offending = tuple(sorted(e for e in entries if e in set(_LIGHTRAG_STORE_FILES)))
    return FreshnessResult(
        dir_existed=True, empty=(len(entries) == 0), offending_stores=offending
    )


# ---------------------------------------------------------------------------
# Injected primitives: process controller (task §12/§13/§20) + health prober
# (task §15/§16/§29). Offline tests inject fakes; the live path composes the real
# Docker/health implementations below. Neither the protocol nor this module calls a
# provider or auto-indexes.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CellProcessSpec:
    """Content-free spec for one fresh LightRAG cell process (NO credential)."""

    cell_id: str
    workspace: str
    working_dir: str  # host run-owned dir mounted as the LightRAG working dir
    host: str
    port: int
    image: str


@dataclass(frozen=True)
class CellProcessHandle:
    """Content-free handle to the exact process started for a cell."""

    identifier: str  # container name / owned token (content-free)
    kind: str  # "docker" | "subprocess" | "fake"
    pid: Optional[int] = None


@dataclass(frozen=True)
class TerminationResult:
    """Outcome of stopping a cell's process tree (content-free)."""

    stopped: bool
    forced: bool  # graceful stop failed → forceful tree kill was used


class CellProcessController(Protocol):
    """Start / probe-liveness / terminate the exact process tree for one cell.

    The LIVE implementation starts a FRESH pinned LightRAG process per cell and, on
    teardown, stops that exact process tree (graceful then forceful; on Windows a
    process-tree kill, never a bare parent kill). It NEVER targets an unrelated
    process. Offline tests inject a fake."""

    async def start(self, spec: CellProcessSpec) -> CellProcessHandle: ...

    async def is_alive(self, handle: CellProcessHandle) -> bool: ...

    async def terminate(
        self, handle: CellProcessHandle, *, graceful_timeout_s: float
    ) -> TerminationResult: ...


@dataclass(frozen=True)
class CellHealthObservation:
    """Content-free health/identity readout from a cell's LightRAG process."""

    reachable: bool
    healthy: bool
    version: Optional[str]
    reported_workspace: Optional[str]
    working_dir: Optional[str]


class CellHealthProber(Protocol):
    """Probe a cell process's liveness + identity (version, bound workspace).

    Content-safe: returns only coarse booleans / a version string / a workspace id —
    never a health-response body, credential, or provider text. Offline tests inject a
    fake; the live prober reuses the pinned GraphRAG health client."""

    async def probe(
        self, *, base_url: str, host: str, port: int
    ) -> CellHealthObservation: ...


class PortAllocator(Protocol):
    """Allocate a content-free ephemeral loopback port for a cell (task §14)."""

    def allocate(self, host: str) -> int: ...


# ---- real (live-only) port allocator ---------------------------------------


class EphemeralPortAllocator:
    """Default allocator: an OS-assigned free loopback port, tracked so the provisioner
    never hands the same port to two cells within a run. Content-free (task §14)."""

    def __init__(self) -> None:
        self._used: Set[int] = set()

    def allocate(self, host: str) -> int:
        for _ in range(64):
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                s.bind((host, 0))
                port = int(s.getsockname()[1])
            if port not in self._used:
                self._used.add(port)
                return port
        raise CellProvisionConfigurationError("could not allocate a free diagnostic port")


def port_is_open(host: str, port: int, *, timeout_s: float = 1.0) -> bool:
    """True iff a TCP connection to ``host:port`` succeeds (task §14/§48)."""
    try:
        with socket.create_connection((host, port), timeout=timeout_s):
            return True
    except OSError:
        return False


# ---------------------------------------------------------------------------
# Process-tree safe termination (task §13). Reused by any subprocess-launch
# controller and covered by a real fake-child-tree test (§49). The Docker live path
# instead relies on container teardown, which removes the whole in-container tree.
# ---------------------------------------------------------------------------


class ProcessTreeController:
    """Terminate a local process AND its children safely and cross-platform.

    Windows: ``taskkill /F /T /PID`` (the /T flag kills the whole tree — a bare parent
    kill orphans children on Windows). POSIX: SIGTERM the process group, then SIGKILL
    if it outlives the graceful window. Only the exact owned pid is targeted; no
    foreign process is ever named.

    Ownership caveat: this targets a bare pid, so the CALLER must own the pid and stop
    it promptly after spawn — across a long delay the OS could recycle the pid onto an
    unrelated process. The WIRED live path does NOT use this: it is
    ``DockerCellProcessController``, which targets the exact owned CONTAINER name (no pid
    reuse). This utility is the safe process-tree primitive for a subprocess-launch mode
    and is covered by a real fake-child-tree test."""

    @staticmethod
    def is_alive(pid: int) -> bool:
        if os.name == "nt":
            out = subprocess.run(
                ["tasklist", "/FI", f"PID eq {pid}", "/NH"],
                capture_output=True,
                text=True,
                timeout=15,
            )
            return str(pid) in (out.stdout or "")
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False

    @classmethod
    def terminate_tree(
        cls, pid: int, *, graceful_timeout_s: float = DEFAULT_GRACEFUL_STOP_TIMEOUT_S
    ) -> TerminationResult:
        if not cls.is_alive(pid):
            return TerminationResult(stopped=True, forced=False)
        forced = False
        if os.name == "nt":
            # Graceful first (no /F), then forceful tree kill (/F /T).
            subprocess.run(
                ["taskkill", "/T", "/PID", str(pid)],
                capture_output=True,
                text=True,
                timeout=30,
            )
            if cls._await_dead(pid, graceful_timeout_s):
                return TerminationResult(stopped=True, forced=False)
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(pid)],
                capture_output=True,
                text=True,
                timeout=30,
            )
            forced = True
        else:
            import signal

            def _signal_group(sig: int) -> None:
                try:
                    # POSIX-only branch (os.name != "nt"); the process-group calls do
                    # not exist on Windows, hence the platform type-ignores.
                    os.killpg(os.getpgid(pid), sig)  # type: ignore[attr-defined]
                except OSError:
                    try:
                        os.kill(pid, sig)
                    except OSError:
                        pass

            _signal_group(signal.SIGTERM)
            if cls._await_dead(pid, graceful_timeout_s):
                return TerminationResult(stopped=True, forced=False)
            _signal_group(signal.SIGKILL)  # type: ignore[attr-defined]
            forced = True
        stopped = cls._await_dead(pid, graceful_timeout_s)
        return TerminationResult(stopped=stopped, forced=forced)

    @classmethod
    def _await_dead(cls, pid: int, timeout_s: float) -> bool:
        deadline = time.monotonic() + max(0.0, timeout_s)
        while time.monotonic() < deadline:
            if not cls.is_alive(pid):
                return True
            time.sleep(0.2)
        return not cls.is_alive(pid)


# ---------------------------------------------------------------------------
# Real (live-only) Docker process controller + LightRAG health prober. NEVER
# exercised by the offline suite (no import-time or test-time process start). Each
# method is a thin, content-free wrapper; a fresh container per cell = a fresh process
# per cell, and ``docker rm -f`` removes the container's whole in-container tree.
# ---------------------------------------------------------------------------


class DockerCellProcessController:
    """Live per-cell LightRAG process = a fresh pinned Docker container.

    The container's LIGHTRAG_API_KEY is read from the environment at start time and is
    NEVER stored on the handle, spec, or any log (task §10/§63)."""

    def __init__(self, *, api_key_env: str = "GRAPHRAG_POC_API_KEY") -> None:
        self._api_key_env = api_key_env

    async def start(self, spec: CellProcessSpec) -> CellProcessHandle:
        name = f"gr08e2_{spec.cell_id}"
        api_key = os.environ.get(self._api_key_env, "").strip()
        args = [
            "docker", "run", "-d", "--name", name,
            "-p", f"{spec.host}:{spec.port}:{LIGHTRAG_CONTAINER_PORT}",
            "-e", f"WORKSPACE={spec.workspace}",
            "-v", f"{spec.working_dir}:{LIGHTRAG_WORKING_DIR_MOUNT}",
        ]
        if api_key:
            args += ["-e", f"LIGHTRAG_API_KEY={api_key}"]
        args.append(spec.image)
        out = subprocess.run(args, capture_output=True, text=True, timeout=120)
        if out.returncode != 0:
            # Never echo stderr (may carry env/secret); surface only the return code.
            raise CellProcessStartError(
                f"docker run failed for cell {spec.cell_id} (rc={out.returncode})"
            )
        return CellProcessHandle(identifier=name, kind="docker")

    async def is_alive(self, handle: CellProcessHandle) -> bool:
        out = subprocess.run(
            ["docker", "inspect", "-f", "{{.State.Running}}", handle.identifier],
            capture_output=True,
            text=True,
            timeout=30,
        )
        return out.returncode == 0 and (out.stdout or "").strip().lower() == "true"

    async def terminate(
        self, handle: CellProcessHandle, *, graceful_timeout_s: float
    ) -> TerminationResult:
        # ``docker stop`` sends SIGTERM (graceful) then SIGKILL after the timeout; a
        # follow-up ``rm -f`` guarantees the container (and its whole process tree) is
        # gone. Only this cell's owned container name is ever targeted.
        subprocess.run(
            ["docker", "stop", "-t", str(int(max(1, graceful_timeout_s))), handle.identifier],
            capture_output=True,
            text=True,
            timeout=graceful_timeout_s + 30,
        )
        out = subprocess.run(
            ["docker", "rm", "-f", handle.identifier],
            capture_output=True,
            text=True,
            timeout=60,
        )
        stopped = out.returncode == 0 and not await self.is_alive(handle)
        return TerminationResult(stopped=stopped, forced=True)


class LightRagCellHealthProber:
    """Live health prober: reuses the pinned GraphRAG health client for liveness +
    version, and reads a bound-workspace/working-dir hint from the health payload when
    v1.5.6 exposes it (authenticated /health config). Content-safe."""

    def __init__(self, *, api_key: Optional[str] = None) -> None:
        self._api_key = api_key

    async def probe(
        self, *, base_url: str, host: str, port: int
    ) -> CellHealthObservation:
        from open_notebook.integrations.graphrag.client import GraphRAGClient
        from open_notebook.integrations.graphrag.config import GraphRAGConfig

        cfg = GraphRAGConfig(
            enabled=True, base_url=base_url, timeout=10.0, api_key=self._api_key
        )
        client = GraphRAGClient(cfg)
        try:
            health = await client.health()
        except Exception:  # noqa: BLE001 - unreachable → coarse reachable=False
            return CellHealthObservation(
                reachable=False, healthy=False, version=None,
                reported_workspace=None, working_dir=None,
            )
        return CellHealthObservation(
            reachable=True,
            healthy=bool(getattr(health, "healthy", False)),
            version=getattr(health, "version", None),
            # v1.5.6 /health does not reliably expose the bound workspace to an
            # unauthenticated caller; when it is absent the provisioner BLOCKS
            # readiness (task §16) rather than assuming a match.
            reported_workspace=getattr(health, "workspace", None),
            working_dir=getattr(health, "working_directory", None),
        )


# ---------------------------------------------------------------------------
# Provisioner configuration + the provisioned-cell handle.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ProvisionerConfig:
    """Content-free configuration for the diagnostic cell provisioner."""

    eval_root: str  # absolute, run-owned diagnostic storage root (never content-derived)
    image: str = DEFAULT_LIGHTRAG_IMAGE
    host: str = DEFAULT_DIAGNOSTIC_HOST
    expected_version: str = VERIFIED_LIGHTRAG_VERSION
    startup_timeout_s: float = DEFAULT_STARTUP_TIMEOUT_S
    health_poll_interval_s: float = DEFAULT_HEALTH_POLL_INTERVAL_S
    graceful_stop_timeout_s: float = DEFAULT_GRACEFUL_STOP_TIMEOUT_S
    #: When True the runtime workspace must be positively confirmed by the health
    #: prober; if it cannot be read, readiness is BLOCKED (task §16). Only a test with
    #: a prober that DOES report the workspace should relax this.
    require_runtime_workspace: bool = True


@dataclass(frozen=True)
class ProvisionedCell:
    """A READY, isolated, cell-owned LightRAG process — returned to future diagnostic
    code ONLY after every stage verified (task §28). Content-free (no credential)."""

    run_id: str
    cell_id: str
    workspace: str
    working_dir: str
    storage_dir: str
    base_url: str
    host: str
    port: int
    version: str
    process_identifier: str
    process_pid: Optional[int]
    started_at: str
    state: str
    fresh_extraction_state: bool = True
    owned: bool = True

    @property
    def ready(self) -> bool:
        return self.state == ProvisionState.PROVISIONED

    def to_provision(self) -> CellProvision:
        """Adapt to the frozen 08E.1 ``CellProvision`` contract (task §34)."""
        return CellProvision(
            workspace=self.workspace,
            storage_dir=self.storage_dir,
            fresh_extraction_state=self.fresh_extraction_state,
            owned=self.owned,
        )

    def as_dict(self) -> Dict[str, object]:
        return {
            "run_id": self.run_id,
            "cell_id": self.cell_id,
            "workspace": self.workspace,
            "working_dir": self.working_dir,
            "storage_dir": self.storage_dir,
            "base_url": self.base_url,
            "host": self.host,
            "port": self.port,
            "version": self.version,
            "process_identifier": self.process_identifier,
            "process_pid": self.process_pid,
            "started_at": self.started_at,
            "state": self.state,
            "fresh_extraction_state": self.fresh_extraction_state,
            "owned": self.owned,
            "ready": self.ready,
        }


@dataclass
class _CellRuntime:
    """Mutable per-cell provisioning bookkeeping (never returned to callers)."""

    identity: CellIdentity
    run_root: str
    working_dir: str
    storage_dir: str
    state: str = ProvisionState.PLANNED
    port: Optional[int] = None
    handle: Optional[CellProcessHandle] = None
    storage_created: bool = False
    version: Optional[str] = None
    started_at: Optional[str] = None


@dataclass
class CleanupOutcome:
    """Positive-verification teardown result (task §21)."""

    process_stopped: bool
    port_released: bool
    storage_absent: bool
    forced: bool = False

    @property
    def ok(self) -> bool:
        return self.process_stopped and self.port_released and self.storage_absent


# ---------------------------------------------------------------------------
# The provisioner. Realizes cell_isolation08.CellProvisioner (provision/dispose) AND
# exposes a richer ProvisionedCell via ``provision_diagnostic_cell`` (task §10/§34).
# ---------------------------------------------------------------------------


class LightRagCellProvisioner:
    """Atomic, self-cleaning per-cell LightRAG provisioner (OPTION B).

    Satisfies the ``cell_isolation08.CellProvisioner`` protocol so it plugs directly
    into ``concurrency_diag08.run_sweep`` (the injected indexer runs only INSIDE an
    entered, validated cell). The process/health/port primitives are injected: offline
    tests drive the full lifecycle with fakes; the live path composes the Docker/health
    implementations above. This class NEVER indexes, embeds, or calls a provider."""

    def __init__(
        self,
        config: ProvisionerConfig,
        *,
        process_controller: CellProcessController,
        health_prober: CellHealthProber,
        port_allocator: Optional[PortAllocator] = None,
    ) -> None:
        if not config.eval_root or not os.path.isabs(config.eval_root):
            raise CellProvisionConfigurationError(
                "ProvisionerConfig.eval_root must be a non-empty absolute path"
            )
        self._cfg = config
        self._proc = process_controller
        self._prober = health_prober
        self._ports = port_allocator or EphemeralPortAllocator()
        self._active: Dict[str, _CellRuntime] = {}

    # -- public contract (cell_isolation08.CellProvisioner) ------------------

    async def provision(self, identity: CellIdentity) -> CellProvision:
        cell = await self._provision_cell(identity)
        return cell.to_provision()

    async def dispose(
        self, identity: CellIdentity, provision: CellProvision
    ) -> CellDisposal:
        rt = self._active.get(identity.cell_id)
        if rt is None:
            # Idempotent: an already-cleaned (or never-provisioned) cell is a safe
            # no-op — NEVER a broader/fallback deletion (task §22/§50).
            return CellDisposal(disposed=True, owned=True)
        # Ownership fail-closed BEFORE any destructive action (task §23): the provision
        # handed back must match this cell's own identity + owned storage path.
        if not self._ownership_proven(identity, provision, rt):
            logger.warning(
                f"[gr08e2] cell {identity.cell_id} dispose ownership NOT proven — "
                "refusing destructive cleanup"
            )
            return CellDisposal(disposed=False, owned=False)
        outcome = await self._teardown(rt)
        # Drop the runtime ONLY once cleanup positively verified, so a failed teardown
        # keeps the cell tracked for a retry/operator diagnosis rather than silently
        # losing the residue (task §21/§26). A second dispose after success is a no-op.
        if outcome.ok:
            self._active.pop(identity.cell_id, None)
        return CellDisposal(disposed=outcome.ok, owned=True)

    # -- richer handle API (task §10) ----------------------------------------

    async def provision_cell(self, identity: CellIdentity) -> ProvisionedCell:
        """Provision one cell and return the full READY handle (task §10/§28)."""
        return await self._provision_cell(identity)

    async def endpoint(self, identity: CellIdentity) -> Optional[Tuple[str, int]]:
        """Content-free ``(base_url, port)`` for an active cell, or None if not
        provisioned. Future live indexing addresses the cell's own sidecar through
        this — never a shared/default endpoint."""
        rt = self._active.get(identity.cell_id)
        if rt is None or rt.port is None or rt.state != ProvisionState.PROVISIONED:
            return None
        return self._base_url(rt.port), rt.port

    # -- provisioning state machine (atomic / self-cleaning, LOW-2) ----------

    async def _provision_cell(self, identity: CellIdentity) -> ProvisionedCell:
        if identity.cell_id in self._active:
            raise CellProvisionOwnershipError(
                f"cell {identity.cell_id} already provisioned (duplicate)"
            )
        run_root, working_dir, storage_dir = assert_cell_paths_safe(
            eval_root=self._cfg.eval_root,
            run_id=identity.run_id,
            workspace=identity.workspace,
        )
        rt = _CellRuntime(
            identity=identity,
            run_root=run_root,
            working_dir=working_dir,
            storage_dir=storage_dir,
        )
        try:
            self._reserve_storage(rt)          # STORAGE_RESERVED (physical freshness)
            await self._start_process(rt)      # PROCESS_STARTED
            await self._verify_health(rt)      # HEALTH/VERSION/WORKSPACE verified
            rt.state = ProvisionState.PROVISIONED
            rt.started_at = datetime.now(timezone.utc).isoformat()
            self._active[identity.cell_id] = rt
            logger.info(
                f"[gr08e2] cell {identity.cell_id} PROVISIONED "
                f"ws={identity.workspace} port={rt.port} v={rt.version}"
            )
            return self._provisioned_cell(rt)
        except BaseException:
            # ATOMIC self-clean (LOW-2): dispose whatever partial resources exist, then
            # re-raise. A raise here means diagnostic_cell08.__aenter__ never stored a
            # provision, so no outer __aexit__ will clean up — this layer MUST. Catching
            # BaseException (not just CellProvisionError) makes rollback cover an async
            # CancelledError, a timeout, or an unexpected primitive error too — any of
            # which could otherwise strand a started process / reserved workspace.
            await self._atomic_rollback(rt)
            raise

    def _reserve_storage(self, rt: _CellRuntime) -> None:
        rt.state = ProvisionState.STORAGE_RESERVED
        # The run-owned working dir may already exist (a prior cell in the same run);
        # that is OURS by run_id. Create it if absent.
        os.makedirs(rt.working_dir, exist_ok=True)
        # PHYSICAL freshness of THIS cell's workspace subdir (LOW-3, task §5/§7/§43):
        # a pre-existing (esp. non-empty / cache-bearing) workspace fails CLOSED — never
        # silently deleted and reused.
        fresh = scan_workspace_freshness(rt.storage_dir)
        if fresh.dir_existed:
            rt.state = ProvisionState.FRESHNESS_FAILED
            raise CellFreshnessError(
                f"cell {rt.identity.cell_id} workspace pre-exists "
                f"(offending={list(fresh.offending_stores)}) — refusing reuse"
            )
        # Create the exact owned, empty workspace dir (state B) and re-verify emptiness.
        # exist_ok=False closes a TOCTOU: if another actor created the dir between the
        # freshness scan and here, we fail closed rather than adopt foreign state.
        try:
            os.makedirs(rt.storage_dir, exist_ok=False)
        except OSError as exc:
            rt.state = ProvisionState.FRESHNESS_FAILED
            raise CellFreshnessError(
                f"cell {rt.identity.cell_id} could not reserve a fresh workspace dir "
                f"({type(exc).__name__})"
            ) from exc
        rt.storage_created = True
        post = scan_workspace_freshness(rt.storage_dir)
        if not post.fresh or not post.empty:
            rt.state = ProvisionState.FRESHNESS_FAILED
            raise CellFreshnessError(
                f"cell {rt.identity.cell_id} workspace not empty after creation"
            )

    async def _start_process(self, rt: _CellRuntime) -> None:
        port = self._ports.allocate(self._cfg.host)
        # Port ownership (task §14/§48): an intended port already occupied by an
        # unrelated process fails CLOSED — we NEVER kill a foreign process for a port.
        if port_is_open(self._cfg.host, port):
            rt.state = ProvisionState.START_FAILED
            raise CellProvisionConfigurationError(
                f"cell {rt.identity.cell_id}: diagnostic port {port} already occupied"
            )
        rt.port = port
        rt.state = ProvisionState.PROCESS_STARTING
        spec = CellProcessSpec(
            cell_id=rt.identity.cell_id,
            workspace=rt.identity.workspace,
            working_dir=rt.working_dir,
            host=self._cfg.host,
            port=port,
            image=self._cfg.image,
        )
        try:
            rt.handle = await self._proc.start(spec)
        except CellProvisionError:
            raise
        except Exception as exc:  # noqa: BLE001 - normalise to a content-safe error
            rt.state = ProvisionState.START_FAILED
            raise CellProcessStartError(
                f"cell {rt.identity.cell_id} process start failed ({type(exc).__name__})"
            ) from exc
        rt.state = ProvisionState.PROCESS_STARTED

    async def _verify_health(self, rt: _CellRuntime) -> None:
        assert rt.port is not None
        base_url = self._base_url(rt.port)
        # Honor the configured timeout exactly (a tiny positive floor only guards a
        # non-positive value); the loop always probes at least once so a very small
        # timeout still gets one real attempt. NO infinite loop (task §30).
        timeout_s = self._cfg.startup_timeout_s if self._cfg.startup_timeout_s > 0 else 0.1
        deadline = time.monotonic() + timeout_s
        obs: Optional[CellHealthObservation] = None
        while True:
            try:
                obs = await self._prober.probe(
                    base_url=base_url, host=self._cfg.host, port=rt.port
                )
            except Exception:  # noqa: BLE001 - a probe error mid-startup == not-ready-yet
                obs = CellHealthObservation(
                    reachable=False, healthy=False, version=None,
                    reported_workspace=None, working_dir=None,
                )
            if obs.reachable and obs.healthy:
                break
            if time.monotonic() >= deadline:
                break
            await asyncio.sleep(max(0.05, self._cfg.health_poll_interval_s))
        if obs is None or not (obs.reachable and obs.healthy):
            rt.state = ProvisionState.HEALTH_FAILED
            raise CellHealthError(
                f"cell {rt.identity.cell_id} not healthy within {timeout_s}s"
            )
        rt.state = ProvisionState.HEALTH_VERIFIED
        # Version pin (task §15): a healthy process reporting the wrong version fails
        # BEFORE the cell is usable — no fallback/upgrade.
        if obs.version != self._cfg.expected_version:
            rt.state = ProvisionState.VERSION_FAILED
            raise CellVersionMismatchError(
                f"cell {rt.identity.cell_id} version {obs.version!r} != "
                f"{self._cfg.expected_version!r}"
            )
        rt.version = obs.version
        rt.state = ProvisionState.VERSION_VERIFIED
        # Runtime workspace binding (task §16): a health-only 200 is NOT enough; the
        # process must be proven bound to THIS cell's workspace, else readiness BLOCKS.
        if self._cfg.require_runtime_workspace:
            if obs.reported_workspace is None:
                rt.state = ProvisionState.WORKSPACE_VERIFY_FAILED
                raise CellWorkspaceMismatchError(
                    f"cell {rt.identity.cell_id} runtime workspace unverifiable — "
                    "blocking readiness"
                )
            if obs.reported_workspace != rt.identity.workspace:
                rt.state = ProvisionState.WORKSPACE_VERIFY_FAILED
                raise CellWorkspaceMismatchError(
                    f"cell {rt.identity.cell_id} bound to workspace "
                    f"{obs.reported_workspace!r} != {rt.identity.workspace!r}"
                )
        rt.state = ProvisionState.WORKSPACE_VERIFIED

    # -- teardown (task §19/§20/§21) -----------------------------------------

    async def _teardown(self, rt: _CellRuntime) -> CleanupOutcome:
        rt.state = ProvisionState.TEARDOWN_STARTED
        # 1) stop the exact process tree (graceful → forceful) and verify dead. Any
        #    error from an injected/real controller becomes a verifiable cleanup FAILURE
        #    (process_stopped=False) — never a raw escape, so both teardown callers see a
        #    consistent outcome and a stranded process is reported, not masked (§21).
        process_stopped = True
        forced = False
        if rt.handle is not None:
            try:
                term = await self._proc.terminate(
                    rt.handle, graceful_timeout_s=self._cfg.graceful_stop_timeout_s
                )
                forced = term.forced
                process_stopped = term.stopped and not await self._proc.is_alive(
                    rt.handle
                )
            except Exception as exc:  # noqa: BLE001 - a controller error == not stopped
                logger.warning(
                    f"[gr08e2] cell {rt.identity.cell_id} terminate error "
                    f"({type(exc).__name__}) — treating process as NOT stopped"
                )
                process_stopped = False
            if process_stopped:
                rt.state = ProvisionState.PROCESS_STOPPED
        # 2) verify the diagnostic port is released (only meaningful once stopped).
        port_released = True
        if rt.port is not None:
            port_released = not port_is_open(self._cfg.host, rt.port)
        # 3) dispose ONLY this cell's owned workspace subdir — never while a process may
        #    still be writing (only after the process is verified stopped, task §19).
        storage_absent = True
        if rt.storage_created:
            if not process_stopped:
                # Do not delete storage under a still-live process.
                storage_absent = not os.path.exists(rt.storage_dir)
            else:
                try:
                    storage_absent = self._dispose_storage(rt)
                except Exception as exc:  # noqa: BLE001 - a delete error == not disposed
                    logger.warning(
                        f"[gr08e2] cell {rt.identity.cell_id} storage dispose error "
                        f"({type(exc).__name__}) — treating storage as PRESENT"
                    )
                    storage_absent = False
                if storage_absent:
                    rt.state = ProvisionState.STORAGE_DISPOSED
        outcome = CleanupOutcome(
            process_stopped=process_stopped,
            port_released=port_released,
            storage_absent=storage_absent,
            forced=forced,
        )
        rt.state = ProvisionState.CLEANED if outcome.ok else ProvisionState.CLEANUP_FAILED
        logger.info(
            f"[gr08e2] cell {rt.identity.cell_id} teardown ok={outcome.ok} "
            f"stopped={process_stopped} port_released={port_released} "
            f"storage_absent={storage_absent} forced={forced}"
        )
        return outcome

    def _dispose_storage(self, rt: _CellRuntime) -> bool:
        """Guarded recursive delete of the cell's OWN workspace subdir (task §24/§25).

        Refuses unless the target resolves strictly beneath the run root beneath the
        eval root, refuses to delete a symlink/junction target, and never follows a
        reparse point out of the owned root. Returns True iff the dir is absent after."""
        target = rt.storage_dir
        if not os.path.exists(target):
            return True
        # Fail-closed ownership of the delete target (task §23/§24).
        if not is_within_root(target, rt.run_root) or not is_within_root(
            rt.run_root, self._cfg.eval_root
        ):
            logger.warning(
                f"[gr08e2] cell {rt.identity.cell_id} storage path outside owned root — "
                "refusing delete"
            )
            return False
        if os.path.islink(target):
            logger.warning(
                f"[gr08e2] cell {rt.identity.cell_id} storage is a symlink — refusing delete"
            )
            return False
        # Refuse if any nested reparse point would escape the owned root (§25).
        for root, dirs, _files in os.walk(target, followlinks=False):
            for name in dirs:
                p = os.path.join(root, name)
                if os.path.islink(p) and not is_within_root(p, rt.run_root):
                    logger.warning(
                        f"[gr08e2] cell {rt.identity.cell_id} nested junction escapes "
                        "owned root — refusing delete"
                    )
                    return False
        shutil.rmtree(target, ignore_errors=False)
        return not os.path.exists(target)

    async def _atomic_rollback(self, rt: _CellRuntime) -> None:
        """Dispose partial resources after a failed provision, then leave the cell out
        of ``_active``. If teardown cannot be verified, raise ``CellCleanupError`` so a
        PROVISIONED result is NEVER reported over uncertain residue (LOW-2)."""
        try:
            outcome = await self._teardown(rt)
        except Exception as exc:  # noqa: BLE001 - a teardown crash is itself a failure
            raise CellCleanupError(
                f"cell {rt.identity.cell_id} rollback failed ({type(exc).__name__})"
            ) from exc
        if not outcome.ok:
            raise CellCleanupError(
                f"cell {rt.identity.cell_id} rollback left residue "
                f"(stopped={outcome.process_stopped} port={outcome.port_released} "
                f"storage_absent={outcome.storage_absent})"
            )

    # -- helpers -------------------------------------------------------------

    def _base_url(self, port: int) -> str:
        return f"http://{self._cfg.host}:{port}"

    def _ownership_proven(
        self, identity: CellIdentity, provision: CellProvision, rt: _CellRuntime
    ) -> bool:
        expected_dir = cell_storage_dir(rt.working_dir, identity.workspace)
        return (
            provision.workspace == identity.workspace
            and provision.storage_dir == expected_dir
            and rt.storage_dir == expected_dir
            and rt.identity.cell_id == identity.cell_id
            and is_within_root(rt.storage_dir, rt.run_root)
            and is_within_root(rt.run_root, self._cfg.eval_root)
        )

    def _provisioned_cell(self, rt: _CellRuntime) -> ProvisionedCell:
        assert rt.port is not None and rt.version is not None and rt.handle is not None
        assert rt.started_at is not None
        return ProvisionedCell(
            run_id=rt.identity.run_id,
            cell_id=rt.identity.cell_id,
            workspace=rt.identity.workspace,
            working_dir=rt.working_dir,
            storage_dir=rt.storage_dir,
            base_url=self._base_url(rt.port),
            host=self._cfg.host,
            port=rt.port,
            version=rt.version,
            process_identifier=rt.handle.identifier,
            process_pid=rt.handle.pid,
            started_at=rt.started_at,
            state=rt.state,
        )


# ---------------------------------------------------------------------------
# Convenience single-cell context manager (task §10/§54). Guarantees teardown on a
# caller exception and surfaces an unverified cleanup as CellCleanupError (task §55).
# ---------------------------------------------------------------------------


@asynccontextmanager
async def provision_diagnostic_cell(
    provisioner: LightRagCellProvisioner, identity: CellIdentity
) -> AsyncIterator[ProvisionedCell]:
    """Provision one cell, yield its READY handle, and ALWAYS tear it down.

    On a caller exception inside the ``async with`` body the cell is still disposed
    (finally); if that disposal cannot be POSITIVELY verified, a ``CellCleanupError`` is
    raised, chaining the original body cause so it is never lost (task §54/§55)."""
    cell = await provisioner.provision_cell(identity)
    body_exc: Optional[BaseException] = None
    try:
        yield cell
    except BaseException as exc:
        body_exc = exc
        raise
    finally:
        disposal = await provisioner.dispose(identity, cell.to_provision())
        if not disposal.owned:
            raise CellProvisionOwnershipError(
                f"cell {identity.cell_id} cleanup ownership not proven"
            ) from body_exc
        if not disposal.disposed:
            raise CellCleanupError(
                f"cell {identity.cell_id} cleanup did not verify"
            ) from body_exc


__all__ = [
    "DEFAULT_LIGHTRAG_IMAGE",
    "LIGHTRAG_CONTAINER_PORT",
    "LIGHTRAG_WORKING_DIR_MOUNT",
    "DEFAULT_DIAGNOSTIC_HOST",
    "DEFAULT_STARTUP_TIMEOUT_S",
    "DEFAULT_HEALTH_POLL_INTERVAL_S",
    "DEFAULT_GRACEFUL_STOP_TIMEOUT_S",
    "CellProvisionError",
    "CellProvisionConfigurationError",
    "CellPathSafetyError",
    "CellFreshnessError",
    "CellProvisionOwnershipError",
    "CellProcessStartError",
    "CellHealthError",
    "CellVersionMismatchError",
    "CellWorkspaceMismatchError",
    "CellCleanupError",
    "ProvisionState",
    "is_within_root",
    "assert_cell_paths_safe",
    "FreshnessResult",
    "scan_workspace_freshness",
    "CellProcessSpec",
    "CellProcessHandle",
    "TerminationResult",
    "CellProcessController",
    "CellHealthObservation",
    "CellHealthProber",
    "PortAllocator",
    "EphemeralPortAllocator",
    "port_is_open",
    "ProcessTreeController",
    "DockerCellProcessController",
    "LightRagCellHealthProber",
    "ProvisionerConfig",
    "ProvisionedCell",
    "CleanupOutcome",
    "LightRagCellProvisioner",
    "provision_diagnostic_cell",
]
