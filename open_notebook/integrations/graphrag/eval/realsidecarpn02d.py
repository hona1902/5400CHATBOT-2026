"""PN02D-A REAL LightRAG v1.5.6 sidecar provisioning, identity & lifecycle.

EVALUATION-ONLY. Nothing in production imports this. Where PN02C provisioned
provider-free stdlib micro-runtimes, PN02D-A provisions the REAL Docker LightRAG
``v1.5.6`` engine — but ONLY to prove it can boot under the per-notebook topology
with ZERO provider traffic and ZERO data before any indexing is ever authorized
(task §3/§7/§14). No document is inserted, no query is run, no embedding/LLM call
is made.

Provider-free-by-construction boot design (verified against the real image):

  * each sidecar runs on its OWN run-owned ``--internal`` Docker network, which has
    no route off the host — external provider egress is impossible by construction
    (mirrors PN02C's "0 by construction"); this also means no host port is
    published, so the engine is probed via ``docker exec`` on the container's own
    loopback (task §4/§5);
  * provider bindings (``LLM_BINDING`` / ``EMBEDDING_BINDING`` and their host/key
    vars) are set EMPTY, so there is no provider target even if egress were possible
    (defense in depth, task §24/§25);
  * no ``LIGHTRAG_API_KEY`` is set — the sidecar carries no secret at all, holds no
    data, and is reachable only via host-side ``docker exec`` (task §6). Future
    provider/auth configuration is inspected by env-var NAME only, never here.

Content-safety (task §6/§26): the generated ``docker run`` argv contains only the
pinned image, the internal network name, the opaque workspace hash, empty binding
vars, and an owned storage mount — NO secret value. Probes use targeted ``docker
exec``/``inspect`` that read a status code, the installed package version, the
``WORKSPACE`` value (a non-secret hash), and the coarse socket table — never a
request body, an ``Authorization`` header, or a provider payload.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Protocol, Sequence, Tuple

from open_notebook.integrations.graphrag.eval.provisionpn02c import (
    _reject_forbidden_storage,
    allocate_storage_root,
)
from open_notebook.integrations.graphrag.eval.sidecar_diag08 import (
    SidecarObservation,
    parse_inspect_line,
    with_health,
)

# Nil-safe variant of ``sidecar_diag08.INSPECT_FORMAT``: the real LightRAG v1.5.6
# image defines NO HEALTHCHECK, so ``.State.Health`` is nil and the frozen template's
# ``{{.State.Health.Status}}`` would error out (empty stdout, nonzero rc) — which
# would be misread as "container not created". Guarding ``.State.Health`` yields
# ``none`` (which ``parse_inspect_line`` already understands) while keeping the exact
# ``Running|ExitCode|Health|RestartCount`` shape. Container health is determined by
# the HTTP ``/health`` probe, not this field, so ``none`` here is expected and fine.
NILSAFE_INSPECT_FORMAT = (
    "{{.State.Running}}|{{.State.ExitCode}}|"
    "{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}|"
    "{{.RestartCount}}"
)

# --------------------------------------------------------------------------- #
# Pinned real engine (task §3) — the repository-approved image, never :latest.
# --------------------------------------------------------------------------- #
REAL_LIGHTRAG_IMAGE = "ghcr.io/hkuds/lightrag:v1.5.6"
EXPECTED_IMAGE_VERSION_LABEL = "v1.5.6"
IMAGE_VERSION_LABEL_KEY = "org.opencontainers.image.version"
SIDECAR_INTERNAL_PORT = 9621
WORKSPACE_ENV = "WORKSPACE"

# Owned-resource name prefixes so ownership/cleanup is unambiguous (task §28/§29).
CONTAINER_PREFIX = "pn02da_c_"
NETWORK_PREFIX = "pn02da_net_"
ENDPOINT_SCHEME = "lightrag+docker"

# Provider-binding env vars, all forced EMPTY at boot (defense in depth, task §24).
EMPTY_BINDING_ENV: Tuple[str, ...] = (
    "LLM_BINDING",
    "LLM_MODEL",
    "LLM_BINDING_HOST",
    "LLM_BINDING_API_KEY",
    "EMBEDDING_BINDING",
    "EMBEDDING_MODEL",
    "EMBEDDING_BINDING_HOST",
    "EMBEDDING_BINDING_API_KEY",
)

# Provider-target substrings that must NEVER appear (nonempty) in a boot command —
# their presence would mean the sidecar could reach a model provider (task §27).
_FORBIDDEN_BINDING_VALUES: Tuple[str, ...] = (
    "openai",
    "openrouter",
    "http://",
    "https://",
    "api.",
    "azure",
    "anthropic",
    "ollama",
)

# Provider-free probe payloads (pure strings; parsing lives in attest/egress).
VERSION_PROBE_PY = "import lightrag;print(getattr(lightrag,'__version__','NONE'))"
HEALTH_PROBE_PY = (
    "import urllib.request,json\n"
    "try:\n"
    " r=urllib.request.urlopen('http://127.0.0.1:9621/health',timeout=6)\n"
    " b=r.read().decode('utf-8','replace')\n"
    " print('STATUS='+str(r.getcode()))\n"
    " import json as _j\n"
    " d=_j.loads(b)\n"
    " print('CORE_VERSION='+str(d.get('core_version','NONE')))\n"
    " print('HEALTH_STATUS_FIELD='+str(d.get('status','NONE')))\n"
    "except Exception as e:\n"
    " print('STATUS=ERR:'+type(e).__name__)\n"
)
PROC_NET_SH = "cat /proc/net/tcp /proc/net/tcp6 2>/dev/null"


class RealSidecarError(RuntimeError):
    """A real LightRAG sidecar could not be provisioned/attested safely."""


class ProviderBindingInCommand(RealSidecarError):
    """A boot command carried a nonempty provider binding target (task §27)."""


class SidecarStartError(RealSidecarError):
    """The real sidecar container did not start / become healthy (task §7)."""


# --------------------------------------------------------------------------- #
# Injectable command runner (real subprocess in a live run; a fake in tests)
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class CommandResult:
    returncode: int
    stdout: str
    stderr: str


class CommandRunner(Protocol):
    def __call__(self, argv: Sequence[str], timeout: float = ...) -> CommandResult: ...


def subprocess_runner(argv: Sequence[str], timeout: float = 60.0) -> CommandResult:
    """Default runner: invoke docker via subprocess with a list argv (no shell).

    A list argv is passed straight to the OS process table, so there is no shell /
    MSYS path rewriting and no argument-injection surface.
    """
    try:
        proc = subprocess.run(
            list(argv),
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return CommandResult(proc.returncode, proc.stdout, proc.stderr)
    except subprocess.TimeoutExpired:
        return CommandResult(124, "", "timeout")
    except FileNotFoundError as exc:  # docker not installed
        return CommandResult(127, "", str(exc))


# --------------------------------------------------------------------------- #
# Boot command generation (pure — unit-tested offline, task §33)
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class RealSidecarSpec:
    """Content-safe spec for one real sidecar (no secrets — hashes/names only)."""

    notebook_id: str
    notebook_record_id: str
    workspace_id: str
    container_name: str
    network_name: str
    storage_root: str
    run_id: str
    image: str = REAL_LIGHTRAG_IMAGE


def build_run_command(spec: RealSidecarSpec) -> List[str]:
    """Generate the exact ``docker run`` argv for one provider-free real sidecar.

    Pinned image, run-owned ``--internal`` network, opaque WORKSPACE hash, EMPTY
    provider bindings, owned storage mount, NO published port, NO api key, NO
    provider host/model. Fully content-safe (no secret value).
    """
    argv: List[str] = [
        "docker",
        "run",
        "-d",
        "--name",
        spec.container_name,
        "--network",
        spec.network_name,
        "--label",
        f"pn02da_run={spec.run_id}",
        "--label",
        f"pn02da_notebook={spec.notebook_id}",
    ]
    argv += ["-e", f"{WORKSPACE_ENV}={spec.workspace_id}"]
    for name in EMPTY_BINDING_ENV:
        argv += ["-e", f"{name}="]
    argv += ["-v", f"{spec.storage_root}:/app/data/rag_storage"]
    argv += [spec.image]
    return argv


def assert_no_provider_binding_in_command(argv: Sequence[str]) -> None:
    """Refuse any boot command whose ``-e`` binding vars carry a nonempty provider
    target — the structural guarantee that the boot cannot reach a provider (§27)."""
    it = iter(range(len(argv)))
    for i in it:
        if argv[i] != "-e" or i + 1 >= len(argv):
            continue
        env = argv[i + 1]
        name, _, value = env.partition("=")
        value = value.strip()
        if not value:
            continue
        # A binding var with a nonempty value, or any value that looks like a
        # provider endpoint, is forbidden. The only nonempty -e allowed is WORKSPACE.
        if name in EMPTY_BINDING_ENV:
            raise ProviderBindingInCommand(
                f"boot command sets provider binding {name!r} to a nonempty value"
            )
        low = value.lower()
        if name != WORKSPACE_ENV and any(tok in low for tok in _FORBIDDEN_BINDING_VALUES):
            raise ProviderBindingInCommand(
                f"boot command env {name!r} carries a provider-endpoint-like value"
            )


def redact_command(argv: Sequence[str]) -> List[str]:
    """Mask any nonempty ``*API_KEY=``/``*TOKEN=`` value for content-safe logging.

    The generated command carries no secret (all binding vars are empty), but this
    is applied to anything logged so a future key can never leak (task §6/§26).
    """
    out: List[str] = []
    for tok in argv:
        name, sep, value = tok.partition("=")
        if sep and value and (
            name.upper().endswith("API_KEY") or name.upper().endswith("TOKEN")
        ):
            out.append(f"{name}=<redacted>")
        else:
            out.append(tok)
    return out


# --------------------------------------------------------------------------- #
# Identity (duck-compatible with routingpn02c.route_query / attest_runtime)
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class RealRuntimeIdentity:
    """Identity of one REAL LightRAG runtime. Carries the SAME attribute surface as
    ``provisionpn02c.RuntimeIdentity`` so the PN02C routing/attestation functions
    apply unchanged (task §19). No secrets."""

    notebook_id: str
    notebook_record_id: str
    workspace_id: str
    storage_root: str
    storage_identity: str
    endpoint: str
    network_name: str
    container_name: str
    image: str
    image_version_label: str
    lightrag_version_config: str  # the ATTESTED runtime version (e.g. "1.5.6")
    synthetic_only: bool
    runtime_id: str
    container_id: Optional[str] = None
    runtime_version_observed: Optional[str] = None

    def as_safe_dict(self) -> Dict[str, object]:
        """Manifest view — storage ROOT path dropped (identity hash only)."""
        return {
            "notebook_id": self.notebook_id,
            "workspace_id": self.workspace_id,
            "storage_identity": self.storage_identity,
            "endpoint": self.endpoint,
            "network_name": self.network_name,
            "container_name": self.container_name,
            "image": self.image,
            "image_version_label": self.image_version_label,
            "runtime_version_observed": self.runtime_version_observed,
            "synthetic_only": self.synthetic_only,
            "runtime_id": self.runtime_id,
        }


def endpoint_for(container_name: str) -> str:
    """A distinct, ownership-verifiable, NOT-externally-openable endpoint identity."""
    return f"{ENDPOINT_SCHEME}://{container_name}"


def _short(seed: str) -> str:
    return hashlib.sha256(seed.encode("utf-8")).hexdigest()[:12]


def make_spec(
    *,
    run_id: str,
    notebook_id: str,
    notebook_record_id: str,
    workspace_id: str,
    storage_root: str,
) -> RealSidecarSpec:
    """Build a content-safe spec with deterministic owned container/network names."""
    tag = _short(f"{run_id}:{notebook_id}:{workspace_id}")
    return RealSidecarSpec(
        notebook_id=notebook_id,
        notebook_record_id=notebook_record_id,
        workspace_id=workspace_id,
        container_name=f"{CONTAINER_PREFIX}{tag}",
        network_name=f"{NETWORK_PREFIX}{tag}",
        storage_root=storage_root,
        run_id=run_id,
    )


# --------------------------------------------------------------------------- #
# Docker CLI wrapper — targeted, content-safe commands only (never full inspect)
# --------------------------------------------------------------------------- #

class DockerCLI:
    """Thin, injectable wrapper over ``docker`` with ONLY content-safe operations.

    Every inspect uses a targeted ``-f`` template (never the full JSON, which
    carries env), and every exec runs a fixed provider-free probe (task §26)."""

    def __init__(self, runner: Optional[CommandRunner] = None) -> None:
        self._run = runner or subprocess_runner

    # ---- availability / image ----
    def available(self) -> bool:
        r = self._run(["docker", "version", "-f", "{{.Server.Version}}"])
        return r.returncode == 0 and bool(r.stdout.strip())

    def image_present(self, image: str = REAL_LIGHTRAG_IMAGE) -> bool:
        r = self._run(["docker", "image", "inspect", "-f", "{{.Id}}", image])
        return r.returncode == 0

    def image_version_label(self, image: str = REAL_LIGHTRAG_IMAGE) -> str:
        r = self._run(
            [
                "docker",
                "image",
                "inspect",
                "-f",
                '{{index .Config.Labels "%s"}}' % IMAGE_VERSION_LABEL_KEY,
                image,
            ]
        )
        return r.stdout.strip() if r.returncode == 0 else ""

    # ---- network ----
    def network_create_internal(self, name: str) -> bool:
        r = self._run(
            ["docker", "network", "create", "--internal", "--driver", "bridge", name]
        )
        return r.returncode == 0

    def network_is_internal(self, name: str) -> Optional[bool]:
        r = self._run(["docker", "network", "inspect", "-f", "{{.Internal}}", name])
        if r.returncode != 0:
            return None
        return r.stdout.strip().lower() == "true"

    def network_exists(self, name: str) -> bool:
        r = self._run(["docker", "network", "inspect", "-f", "{{.Id}}", name])
        return r.returncode == 0

    def network_rm(self, name: str) -> bool:
        r = self._run(["docker", "network", "rm", name])
        return r.returncode == 0

    # ---- container lifecycle ----
    def run(self, argv: Sequence[str], timeout: float = 90.0) -> CommandResult:
        return self._run(list(argv), timeout=timeout)

    def inspect_state(self, name: str) -> SidecarObservation:
        r = self._run(["docker", "inspect", "-f", NILSAFE_INSPECT_FORMAT, name])
        if r.returncode != 0 or not r.stdout.strip():
            return parse_inspect_line(None)
        return parse_inspect_line(r.stdout.strip())

    def container_exists(self, name: str) -> bool:
        r = self._run(["docker", "inspect", "-f", "{{.Id}}", name])
        return r.returncode == 0

    def mount_source(self, name: str) -> str:
        """The host source path of the container's rag_storage bind (targeted, safe).

        Uses a ``-f`` range over ``.Mounts`` (never the full JSON, which carries env)
        to attest the RUNTIME's actual storage root, not merely our intent (§10/§11)."""
        r = self._run(
            [
                "docker",
                "inspect",
                "-f",
                '{{range .Mounts}}{{if eq .Destination "/app/data/rag_storage"}}'
                "{{.Source}}{{end}}{{end}}",
                name,
            ]
        )
        return r.stdout.strip() if r.returncode == 0 else ""

    def exec(self, name: str, argv: Sequence[str], timeout: float = 20.0) -> CommandResult:
        return self._run(["docker", "exec", name, *argv], timeout=timeout)

    def stop(self, name: str, timeout: float = 20.0) -> bool:
        r = self._run(["docker", "stop", "-t", "3", name], timeout=timeout)
        return r.returncode == 0

    def rm(self, name: str, force: bool = True) -> bool:
        cmd = ["docker", "rm"]
        if force:
            cmd.append("-f")
        cmd.append(name)
        r = self._run(cmd)
        return r.returncode == 0

    # ---- provider-free probes ----
    def runtime_version(self, name: str) -> str:
        r = self.exec(name, ["python", "-c", VERSION_PROBE_PY])
        return r.stdout.strip() if r.returncode == 0 else ""

    def workspace_env(self, name: str) -> str:
        r = self.exec(name, ["printenv", WORKSPACE_ENV])
        return r.stdout.strip() if r.returncode == 0 else ""

    def health(self, name: str) -> Tuple[Optional[int], str, str]:
        """Return (status_code|None, core_version, status_field) from /health."""
        r = self.exec(name, ["python", "-c", HEALTH_PROBE_PY])
        code: Optional[int] = None
        core = ""
        status = ""
        for line in r.stdout.splitlines():
            if line.startswith("STATUS="):
                raw = line[len("STATUS="):].strip()
                if raw.isdigit():
                    code = int(raw)
            elif line.startswith("CORE_VERSION="):
                core = line[len("CORE_VERSION="):].strip()
            elif line.startswith("HEALTH_STATUS_FIELD="):
                status = line[len("HEALTH_STATUS_FIELD="):].strip()
        return code, core, status

    def proc_net_tcp(self, name: str) -> str:
        r = self.exec(name, ["sh", "-c", PROC_NET_SH])
        return r.stdout if r.returncode == 0 else ""


def wait_healthy(
    docker: DockerCLI,
    name: str,
    *,
    timeout_seconds: float = 120.0,
    poll_seconds: float = 2.0,
    now: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> Tuple[bool, float, SidecarObservation]:
    """Poll container state + /health until healthy or timeout (task §7).

    Returns (healthy, elapsed_seconds, last_observation). Never inspects full JSON;
    ``inspect_state`` uses the coarse template and ``health`` reads only a status
    code. ``now``/``sleep`` are injectable for deterministic tests."""
    start = now()
    last = docker.inspect_state(name)
    while True:
        obs = docker.inspect_state(name)
        code = None
        if obs.container_running:
            code, _core, _status = docker.health(name)
        healthy = bool(obs.container_running) and code is not None and 200 <= code < 300
        elapsed = now() - start
        last = with_health(
            obs,
            port_open=None,
            health_reachable=(code is not None) if obs.container_running else None,
            health_status_code=code,
            healthy=healthy,
            timeout_reached=elapsed >= timeout_seconds,
        )
        if healthy:
            return True, elapsed, last
        if obs.container_running is False or elapsed >= timeout_seconds:
            return False, elapsed, last
        sleep(poll_seconds)


# --------------------------------------------------------------------------- #
# Topology + cleanup (task §14/§28/§29)
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class RealCleanupReport:
    processes_remaining: int
    storage_residue: int
    networks_remaining: int

    @property
    def clean(self) -> bool:
        return (
            self.processes_remaining == 0
            and self.storage_residue == 0
            and self.networks_remaining == 0
        )


@dataclass
class RealTopology:
    """One or more provisioned REAL sidecars + their owned resources (task §14)."""

    run_id: str
    docker: DockerCLI
    identities: Dict[str, RealRuntimeIdentity] = field(default_factory=dict)

    @property
    def notebook_ids(self) -> Tuple[str, ...]:
        return tuple(self.identities.keys())

    @property
    def endpoints(self) -> Dict[str, str]:
        return {nb: i.endpoint for nb, i in self.identities.items()}

    @property
    def workspace_ids(self) -> Dict[str, str]:
        return {nb: i.workspace_id for nb, i in self.identities.items()}

    @property
    def storage_identities(self) -> Dict[str, str]:
        return {nb: i.storage_identity for nb, i in self.identities.items()}

    @property
    def storage_roots(self) -> Dict[str, str]:
        return {nb: i.storage_root for nb, i in self.identities.items()}

    @property
    def container_names(self) -> Dict[str, str]:
        return {nb: i.container_name for nb, i in self.identities.items()}

    @property
    def network_names(self) -> Dict[str, str]:
        return {nb: i.network_name for nb, i in self.identities.items()}

    def owner_of_endpoint(self, endpoint: str) -> Optional[str]:
        for nb, i in self.identities.items():
            if i.endpoint == endpoint:
                return nb
        return None

    def cleanup(self, *, remove_storage: bool = True) -> RealCleanupReport:
        """Stop+remove every owned container, remove every owned network, and delete
        every owned storage root. Only PN02D-A-owned resources are touched (§28/§29)."""
        for ident in self.identities.values():
            try:
                self.docker.stop(ident.container_name)
            except Exception:  # pragma: no cover - best-effort
                pass
            try:
                self.docker.rm(ident.container_name, force=True)
            except Exception:  # pragma: no cover
                pass
        processes_remaining = sum(
            1
            for ident in self.identities.values()
            if self.docker.container_exists(ident.container_name)
        )
        networks_remaining = 0
        for ident in self.identities.values():
            try:
                self.docker.network_rm(ident.network_name)
            except Exception:  # pragma: no cover
                pass
            if self.docker.network_exists(ident.network_name):
                networks_remaining += 1
        residue = 0
        if remove_storage:
            for ident in self.identities.values():
                root = ident.storage_root
                if os.path.isdir(root):
                    shutil.rmtree(root, ignore_errors=True)
                if os.path.isdir(root):
                    residue += 1
        return RealCleanupReport(
            processes_remaining=processes_remaining,
            storage_residue=residue,
            networks_remaining=networks_remaining,
        )

    def __enter__(self) -> "RealTopology":
        return self

    def __exit__(self, *exc: object) -> None:
        self.cleanup()


def allocate_real_storage_root(
    base_dir: Optional[str], notebook_id: str
) -> str:
    """Fresh, run-owned, forbidden-root-guarded storage root (reuses PN02C §31)."""
    root = allocate_storage_root(base_dir, f"pn02da_{notebook_id}")
    _reject_forbidden_storage(root)
    return root


__all__ = [
    "REAL_LIGHTRAG_IMAGE",
    "EXPECTED_IMAGE_VERSION_LABEL",
    "IMAGE_VERSION_LABEL_KEY",
    "SIDECAR_INTERNAL_PORT",
    "WORKSPACE_ENV",
    "CONTAINER_PREFIX",
    "NETWORK_PREFIX",
    "ENDPOINT_SCHEME",
    "EMPTY_BINDING_ENV",
    "VERSION_PROBE_PY",
    "HEALTH_PROBE_PY",
    "PROC_NET_SH",
    "NILSAFE_INSPECT_FORMAT",
    "RealSidecarError",
    "ProviderBindingInCommand",
    "SidecarStartError",
    "CommandResult",
    "CommandRunner",
    "subprocess_runner",
    "RealSidecarSpec",
    "build_run_command",
    "assert_no_provider_binding_in_command",
    "redact_command",
    "RealRuntimeIdentity",
    "endpoint_for",
    "make_spec",
    "DockerCLI",
    "wait_healthy",
    "RealCleanupReport",
    "RealTopology",
    "allocate_real_storage_root",
]
