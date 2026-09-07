"""PN02C provider-free provisioning of the per-notebook runtime topology (task §3/§10/§11/§27-§31).

EVALUATION-ONLY. Nothing in production imports this. PN02C proves the approved
topology can be provisioned and attested with ZERO provider traffic BEFORE any
live evaluation. It does NOT boot the real LightRAG retrieval sidecar (a Docker
image whose startup provider-safety is not guaranteed — task §5); instead it
allocates REAL, run-owned local resources:

  * a distinct loopback endpoint per notebook (a real ``127.0.0.1:<port>`` bound
    by an owned, provider-free micro-runtime — task §9/§30),
  * a distinct run-owned storage root per notebook (task §10/§31),
  * a distinct owned runtime process per notebook (task §11).

The micro-runtime is a stdlib ``ThreadingHTTPServer`` that serves ONLY
``GET /health`` and ``GET /identity`` and returns 404 for everything else
(``/query/data``, ``/documents``, ...). It imports no provider SDK and makes no
outbound call, so ``PROVIDER_TRAFFIC`` is 0 by construction (task §5/§37-§39).
The LightRAG version it reports is the CONFIGURED pin (``v1.5.6``) — a config
attestation, NOT a live engine probe; a real-runtime version probe is deferred to
the future live-authorization gate (task §4/§48/§56).
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
import threading
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Dict, Optional, Tuple

from open_notebook.integrations.graphrag.eval.datasetpn02 import FixturePN02
from open_notebook.integrations.graphrag.eval.manifestpn02 import (
    LIGHTRAG_EVAL_VERSION,
    canonical_version,
    routing_manifest,
)

LOOPBACK_HOST = "127.0.0.1"
STORAGE_ROOT_PREFIX = "pn02c_"
#: Substrings a run-owned storage root must NEVER contain — production / normal
#: LightRAG / GraphRAG-08 benchmark roots must not be reused (task §10/§31).
FORBIDDEN_STORAGE_SUBSTRINGS: Tuple[str, ...] = (
    "rag_storage",
    "lightrag_storage",
    "graphrag-08",
    "graphrag_08",
    "benchmark",
    "attempt6",
    "attempt_6",
)


class ProvisionError(RuntimeError):
    """A PN02C runtime/endpoint/storage could not be provisioned safely."""


class EndpointCollisionError(ProvisionError):
    """Two runtimes were assigned the same endpoint, or a port was already owned."""


class StorageCollisionError(ProvisionError):
    """Two runtimes were assigned the same/overlapping storage root (task §31)."""


class PartialStartupError(ProvisionError):
    """Startup failed after some runtimes started; all were cleaned up (task §29)."""


class ProviderTrafficAttempted(RuntimeError):
    """A provider-backed op was attempted during PN02C (must never happen)."""


# --------------------------------------------------------------------------- #
# Provider-traffic guard (task §5/§53/§54)
# --------------------------------------------------------------------------- #

@dataclass
class ProviderTrafficGuard:
    """A fail-closed counter for provider-backed traffic (must stay 0).

    PN02C constructs no provider client, so this is never incremented. It exists
    so any FUTURE seam that tried to make provider traffic during preflight would
    trip ``record()`` (which raises) rather than silently succeed.
    """

    external_provider_network_calls: int = 0

    def record(self, seam: str) -> None:  # pragma: no cover - defensive
        self.external_provider_network_calls += 1
        raise ProviderTrafficAttempted(
            f"provider traffic attempted via {seam!r} during PN02C preflight "
            "(PROVIDER_TRAFFIC must be 0)"
        )

    def assert_zero(self) -> None:
        if self.external_provider_network_calls != 0:
            raise ProviderTrafficAttempted(
                f"EXTERNAL_PROVIDER_NETWORK_CALLS={self.external_provider_network_calls} != 0"
            )


# --------------------------------------------------------------------------- #
# Storage identity (task §10/§31/§32)
# --------------------------------------------------------------------------- #

def storage_identity_for(storage_root: str) -> str:
    """Opaque, content-safe identity of an absolute storage root path."""
    real = os.path.realpath(storage_root)
    return "st_" + hashlib.sha256(real.encode("utf-8")).hexdigest()[:16]


def _reject_forbidden_storage(real_root: str) -> None:
    lowered = real_root.replace("\\", "/").lower()
    for bad in FORBIDDEN_STORAGE_SUBSTRINGS:
        if bad in lowered:
            raise StorageCollisionError(
                f"storage root {real_root!r} matches a forbidden production/benchmark "
                f"root marker ({bad!r})"
            )


def allocate_storage_root(base_dir: Optional[str], notebook_id: str) -> str:
    """Create and own a fresh storage root for one notebook (task §10/§31).

    Uses ``mkdtemp`` so the root is guaranteed unique and run-owned; refuses any
    path that looks like a production / GraphRAG-08 / normal-LightRAG root.
    """
    if base_dir is not None:
        os.makedirs(base_dir, exist_ok=True)
    prefix = f"{STORAGE_ROOT_PREFIX}{notebook_id}_"
    root = tempfile.mkdtemp(prefix=prefix, dir=base_dir)
    real = os.path.realpath(root)
    _reject_forbidden_storage(real)
    return real


# --------------------------------------------------------------------------- #
# Runtime identity (task §11/§32)
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class RuntimeIdentity:
    """Content-safe identity of one owned PN02C runtime (task §11). No secrets."""

    notebook_id: str
    notebook_record_id: str
    workspace_id: str
    storage_root: str
    storage_identity: str
    endpoint: str
    host: str
    port: int
    lightrag_version_config: str
    synthetic_only: bool
    runtime_id: str

    def as_safe_dict(self) -> Dict[str, object]:
        """Manifest view — storage ROOT path is dropped (identity hash only)."""
        return {
            "notebook_id": self.notebook_id,
            "workspace_id": self.workspace_id,
            "storage_identity": self.storage_identity,
            "endpoint": self.endpoint,
            "lightrag_version_config": self.lightrag_version_config,
            "synthetic_only": self.synthetic_only,
            "runtime_id": self.runtime_id,
        }


# --------------------------------------------------------------------------- #
# Provider-free micro-runtime (task §11/§27/§36)
# --------------------------------------------------------------------------- #

class _IdentityHTTPServer(ThreadingHTTPServer):
    """A ThreadingHTTPServer carrying one runtime's identity payload."""

    daemon_threads = True
    identity_payload: Dict[str, object] = {}


class _IdentityHandler(BaseHTTPRequestHandler):
    """Serves ONLY /health + /identity; 404 for anything else (no retrieval).

    Deliberately has NO POST handler beyond a hard 404, so /query/data and
    /documents can never do anything (task §37/§39). No provider code path.
    """

    server_version = "PN02CProviderFreeRuntime/0"

    def _send_json(self, code: int, payload: Dict[str, object]) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802 (stdlib naming)
        if self.path == "/health":
            self._send_json(200, {"status": "ok"})
        elif self.path == "/identity":
            self._send_json(200, dict(self.server.identity_payload))  # type: ignore[attr-defined]
        else:
            self._send_json(404, {"error": "not_found", "path": self.path})

    def do_POST(self) -> None:  # noqa: N802
        # No retrieval / indexing surface exists on a PN02C runtime.
        self._send_json(404, {"error": "not_supported", "path": self.path})

    def log_message(self, *args: object) -> None:  # silence stdlib logging
        return


class ProviderFreeRuntime:
    """A real, owned, provider-free runtime bound to a loopback port (task §11)."""

    def __init__(self, identity_seed: Dict[str, object], host: str = LOOPBACK_HOST):
        self._host = host
        self._identity_seed = dict(identity_seed)
        self._server: Optional[_IdentityHTTPServer] = None
        self._thread: Optional[threading.Thread] = None
        self.port: Optional[int] = None

    def start(self) -> int:
        """Bind an OS-assigned loopback port and start serving. Returns the port.

        A bind failure (port already owned by an unrelated process) surfaces as an
        ``OSError`` — the caller treats it as a collision and never kills anything
        (task §30).
        """
        server = _IdentityHTTPServer((self._host, 0), _IdentityHandler)
        server.identity_payload = dict(self._identity_seed)
        self.port = server.server_address[1]
        self._server = server
        self._thread = threading.Thread(
            target=server.serve_forever, name="pn02c-runtime", daemon=True
        )
        self._thread.start()
        return self.port

    @property
    def alive(self) -> bool:
        return bool(self._thread and self._thread.is_alive() and self._server)

    @property
    def endpoint(self) -> str:
        if self.port is None:
            raise ProvisionError("runtime not started")
        return f"http://{self._host}:{self.port}"

    def fetch_identity(self, timeout: float = 2.0) -> Dict[str, object]:
        """Provider-free loopback health/identity read (task §36/§53).

        Uses a stdlib loopback GET — NEVER an external host, NEVER a retrieval or
        embedding call. This is the only network op PN02C performs.
        """
        import urllib.request

        url = f"{self.endpoint}/identity"
        if not url.startswith("http://127.0.0.1:") and not url.startswith(
            "http://localhost:"
        ):  # pragma: no cover - defensive
            raise ProvisionError(f"refusing non-loopback identity fetch: {url}")
        with urllib.request.urlopen(url, timeout=timeout) as resp:  # noqa: S310 (loopback only)
            return json.loads(resp.read().decode("utf-8"))

    def stop(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
        if self._thread is not None:
            self._thread.join(timeout=5.0)
        self._server = None
        self._thread = None


# --------------------------------------------------------------------------- #
# Topology (task §3/§11/§44)
# --------------------------------------------------------------------------- #

@dataclass
class CleanupReport:
    processes_remaining: int
    storage_residue: int


@dataclass
class ProvisionedTopology:
    """The three provisioned per-notebook runtimes + their owned resources."""

    run_id: str
    identities: Dict[str, RuntimeIdentity]
    runtimes: Dict[str, ProviderFreeRuntime] = field(default_factory=dict)
    guard: ProviderTrafficGuard = field(default_factory=ProviderTrafficGuard)
    _stopped: bool = False

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

    def owner_of_endpoint(self, endpoint: str) -> Optional[str]:
        for nb, i in self.identities.items():
            if i.endpoint == endpoint:
                return nb
        return None

    def cleanup(self, remove_storage: bool = True) -> CleanupReport:
        """Stop every owned runtime and remove every run-owned storage root (§44)."""
        for rt in self.runtimes.values():
            try:
                rt.stop()
            except Exception:  # pragma: no cover - best-effort teardown
                pass
        processes_remaining = sum(1 for rt in self.runtimes.values() if rt.alive)
        residue = 0
        if remove_storage:
            for ident in self.identities.values():
                root = ident.storage_root
                if os.path.isdir(root):
                    shutil.rmtree(root, ignore_errors=True)
                if os.path.isdir(root):
                    residue += 1
        self._stopped = True
        return CleanupReport(
            processes_remaining=processes_remaining, storage_residue=residue
        )

    def __enter__(self) -> "ProvisionedTopology":
        return self

    def __exit__(self, *exc: object) -> None:
        self.cleanup()


# --------------------------------------------------------------------------- #
# Collision checks (task §30/§31)
# --------------------------------------------------------------------------- #

def endpoint_collision_check(endpoints: Dict[str, str]) -> None:
    """Refuse if any two notebooks share an endpoint identity (task §30)."""
    seen: Dict[str, str] = {}
    for nb, ep in endpoints.items():
        if ep in seen:
            raise EndpointCollisionError(
                f"endpoint {ep} owned by both {seen[ep]} and {nb}"
            )
        seen[ep] = nb


def storage_collision_check(storage_roots: Dict[str, str]) -> None:
    """Refuse identical, nested, or forbidden storage roots (task §31)."""
    reals = {nb: os.path.realpath(p) for nb, p in storage_roots.items()}
    items = list(reals.items())
    for nb, real in items:
        _reject_forbidden_storage(real)
    for i in range(len(items)):
        for j in range(i + 1, len(items)):
            (nb_a, ra), (nb_b, rb) = items[i], items[j]
            if ra == rb:
                raise StorageCollisionError(
                    f"storage root shared by {nb_a} and {nb_b}: {ra}"
                )
            na, nb2 = ra.rstrip("/\\") + os.sep, rb.rstrip("/\\") + os.sep
            if na.startswith(nb2) or nb2.startswith(na):
                raise StorageCollisionError(
                    f"storage roots for {nb_a} and {nb_b} are nested: {ra} / {rb}"
                )


# --------------------------------------------------------------------------- #
# Provisioning (task §27/§28/§29)
# --------------------------------------------------------------------------- #

def provision_runtimes(
    fx: FixturePN02,
    *,
    run_id: str,
    base_storage_dir: Optional[str] = None,
    start: bool = True,
    guard: Optional[ProviderTrafficGuard] = None,
) -> ProvisionedTopology:
    """Provision the three per-notebook runtimes in the frozen order A->B->C.

    After each runtime starts (task §28) it is verified: alive, endpoint owns the
    expected workspace (loopback /identity), storage identity matches, version
    config matches, provider traffic still 0. On ANY failure after one has
    started, every started runtime is cleaned up and ``PartialStartupError`` is
    raised (task §29) — no partial topology is left running.
    """
    guard = guard or ProviderTrafficGuard()
    routes = routing_manifest(fx)
    identities: Dict[str, RuntimeIdentity] = {}
    runtimes: Dict[str, ProviderFreeRuntime] = {}

    def _rollback() -> None:
        for rt in runtimes.values():
            try:
                rt.stop()
            except Exception:  # pragma: no cover
                pass
        for ident in identities.values():
            shutil.rmtree(ident.storage_root, ignore_errors=True)

    # Frozen deterministic order (task §28): NB_A, NB_B, NB_C as in the fixture.
    for nb in fx.notebooks:
        nb_id = nb.notebook_id
        route = routes[nb_id]
        # In-flight resources for THIS notebook, not yet registered into the
        # topology dicts. They must be cleaned on any verification failure between
        # creation and registration, since ``_rollback`` only sees registered ones
        # (fixes the review MEDIUM: a fetch_identity/verify failure would otherwise
        # leak this runtime's socket/thread + temp storage — task §29/§44).
        pending_runtime: Optional[ProviderFreeRuntime] = None
        pending_storage: Optional[str] = None
        try:
            storage_root = allocate_storage_root(base_storage_dir, nb_id)
            pending_storage = storage_root
            storage_id = storage_identity_for(storage_root)
            runtime_id = "rt_" + hashlib.sha256(
                f"{run_id}:{nb_id}:{route.workspace_id}:{storage_id}".encode("utf-8")
            ).hexdigest()[:16]
            seed: Dict[str, object] = {
                "notebook_id": nb_id,
                "workspace_id": route.workspace_id,
                "storage_identity": storage_id,
                "lightrag_version_config": LIGHTRAG_EVAL_VERSION,
                "synthetic_only": True,
                "runtime_id": runtime_id,
            }
            # Structural (unbound) default: a DISTINCT, deterministic, non-openable
            # endpoint identity per runtime so routing/collision checks are
            # meaningful even without a bound socket. Overwritten with the real
            # loopback endpoint when start=True.
            endpoint = f"pn02c-unbound://{runtime_id}"
            runtime: Optional[ProviderFreeRuntime] = None
            if start:
                runtime = ProviderFreeRuntime(seed)
                pending_runtime = runtime
                port = runtime.start()
                endpoint = runtime.endpoint
                # Verify the runtime is alive and owns the expected identity.
                if not runtime.alive:
                    raise ProvisionError(f"{nb_id} runtime did not stay alive")
                observed = runtime.fetch_identity()
                if observed.get("workspace_id") != route.workspace_id:
                    raise ProvisionError(
                        f"{nb_id} endpoint reported workspace "
                        f"{observed.get('workspace_id')!r} != {route.workspace_id!r}"
                    )
                if observed.get("storage_identity") != storage_id:
                    raise ProvisionError(f"{nb_id} storage identity mismatch")
                if canonical_version(
                    str(observed.get("lightrag_version_config"))
                ) != canonical_version(LIGHTRAG_EVAL_VERSION):
                    raise ProvisionError(f"{nb_id} version config mismatch")
                guard.assert_zero()
                port_val = port
                host_val = LOOPBACK_HOST
            else:
                port_val = 0
                host_val = LOOPBACK_HOST
            identities[nb_id] = RuntimeIdentity(
                notebook_id=nb_id,
                notebook_record_id=route.notebook_record_id,
                workspace_id=route.workspace_id,
                storage_root=storage_root,
                storage_identity=storage_id,
                endpoint=endpoint,
                host=host_val,
                port=port_val,
                lightrag_version_config=LIGHTRAG_EVAL_VERSION,
                synthetic_only=True,
                runtime_id=runtime_id,
            )
            if runtime is not None:
                runtimes[nb_id] = runtime
            # Now owned by the topology dicts; _rollback will handle them.
            pending_runtime = None
            pending_storage = None
        except Exception as exc:  # partial startup -> full rollback (task §29)
            # Clean this notebook's in-flight (unregistered) resources first, then
            # every already-registered runtime/storage — no partial residue (§44).
            if pending_runtime is not None:
                try:
                    pending_runtime.stop()
                except Exception:  # pragma: no cover - best-effort teardown
                    pass
            if pending_storage is not None:
                shutil.rmtree(pending_storage, ignore_errors=True)
            _rollback()
            raise PartialStartupError(
                f"provisioning failed at {nb_id}: {exc}"
            ) from exc

    # Whole-topology collision guards (task §30/§31).
    try:
        endpoint_collision_check({nb: i.endpoint for nb, i in identities.items()})
        storage_collision_check({nb: i.storage_root for nb, i in identities.items()})
    except Exception:
        _rollback()
        raise

    guard.assert_zero()
    return ProvisionedTopology(
        run_id=run_id, identities=identities, runtimes=runtimes, guard=guard
    )


__all__ = [
    "LOOPBACK_HOST",
    "FORBIDDEN_STORAGE_SUBSTRINGS",
    "ProvisionError",
    "EndpointCollisionError",
    "StorageCollisionError",
    "PartialStartupError",
    "ProviderTrafficAttempted",
    "ProviderTrafficGuard",
    "storage_identity_for",
    "allocate_storage_root",
    "RuntimeIdentity",
    "ProviderFreeRuntime",
    "CleanupReport",
    "ProvisionedTopology",
    "endpoint_collision_check",
    "storage_collision_check",
    "provision_runtimes",
]
