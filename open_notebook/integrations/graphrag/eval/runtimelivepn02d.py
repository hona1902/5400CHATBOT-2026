"""Real per-notebook runtime manager + two-boot lifecycle for the LIVE driver (B0C-B).

EVALUATION-ONLY. Nothing in production imports this (``PRODUCTION_IMPORTS_EVAL =
NO``). Implements the runtime-manager responsibilities and the frozen two-boot
lifecycle (design §5/§9/§10, task §9/§10/§47): allocate a run-owned
network/port/storage per notebook, derive 3 workspace ids
(``manifestpn02.workspace_id_for``), assign 3 loopback endpoints, boot the runtimes
(via ``cell_provisioner08.DockerCellProcessController`` in a real run), wait readiness,
attest, build the real route table, and own-only cleanup.

**Two-boot lifecycle (design §5, ``SAME_CONTAINER = NO``).** The provider-FREE
preflight runtime is NOT the provider-bound execution runtime:

  * **Boot 1 — preflight (provider-free):** runtimes booted with an EMPTY provider
    binding on a run-owned network with no published port; version + zero-egress
    attested; then TORN DOWN. Its PASS mints the ``RealLightRAGPreflightAuthorization``
    (done by the driver).
  * **Boot 2 — execution (provider-bound):** FRESH runtimes booted with the frozen
    provider binding + a published loopback port; version (three provider-free signals)
    + workspace/endpoint/storage identity + ``/health`` attested. Only after this
    attestation are operations enabled, and the execution container identities are
    NEVER the preflight ones.

Boot 2 requires a genuine ``LiveProviderRunAuthorization`` and an attested materialized
binding — a provider-bound runtime can never start before authorization (task §12).
Every runtime/health/version boundary is INJECTED, so B0C-B tests drive the whole
lifecycle with a mock controller + mock prober and launch NO real container
(``PROVIDER_BOUND_LIGHTRAG_BOOT_COUNT = 0``).
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import (
    Awaitable,
    Callable,
    Dict,
    List,
    Mapping,
    Optional,
    Protocol,
    Sequence,
    Tuple,
)

from open_notebook.integrations.graphrag.eval.attestpn02d import (
    RealVersionAttestation,
    attest_version,
)
from open_notebook.integrations.graphrag.eval.authmintlivepn02d import (
    require_live_provider_run_authorization,
)
from open_notebook.integrations.graphrag.eval.cell_provisioner08 import (
    CellProcessHandle,
    CellProcessSpec,
)
from open_notebook.integrations.graphrag.eval.datasetpn02 import FixturePN02
from open_notebook.integrations.graphrag.eval.manifestpn02 import workspace_id_for
from open_notebook.integrations.graphrag.eval.provbindpn02d import (
    MaterializedProviderBinding,
)
from open_notebook.integrations.graphrag.eval.provider_binding08 import (
    DiagnosticProviderBinding08,
    frozen_provider_binding,
)
from open_notebook.integrations.graphrag.eval.realsidecarpn02d import (
    EXPECTED_IMAGE_VERSION_LABEL,
    REAL_LIGHTRAG_IMAGE,
    SIDECAR_INTERNAL_PORT,
    assert_no_provider_binding_in_command,
    build_run_command,
    make_spec,
    wait_healthy,
)
from open_notebook.integrations.graphrag.eval.routelivepn02d import (
    NotebookRuntimeRoute,
    build_route_table,
)

_LOOPBACK_HOST = "127.0.0.1"

#: PN02D-B1-PF1: bounded readiness wait for a provider-free preflight sidecar. A real
#: LightRAG container reports ``container_running`` from ``docker run -d`` well BEFORE its
#: HTTP app answers ``/health``; the preflight must wait for actual readiness before
#: probing the version signals, or the signals are unavailable and the gate fails closed
#: against a container that would have become healthy. Bounded + fail-closed on timeout.
PREFLIGHT_READINESS_TIMEOUT_S = 120.0
PREFLIGHT_READINESS_POLL_S = 2.0


class RuntimeLifecycleError(RuntimeError):
    """A runtime lifecycle invariant was violated (fail-closed, content-free)."""


class TwoBootOrderError(RuntimeLifecycleError):
    """Boot 2 (execution) was reached without a completed, torn-down Boot 1 (preflight)."""


# --------------------------------------------------------------------------- #
# Injected seams (a real run wires Docker/health; B0C-B injects mocks)
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class HealthObservation:
    """Content-safe health probe result (mirrors the fields we read from a real probe)."""

    reachable: bool
    healthy: bool
    core_version: str
    workspace: str = ""


class ProcessControllerLike(Protocol):
    """The narrow controller surface the manager needs (``DockerCellProcessController``
    satisfies it structurally). B0C-B injects a fake that starts NO container."""

    async def start(self, spec: CellProcessSpec) -> CellProcessHandle: ...

    async def terminate(
        self, handle: CellProcessHandle, *, graceful_timeout_s: float
    ) -> object: ...


class HealthProberLike(Protocol):
    async def probe(
        self, *, base_url: str, host: str, port: int
    ) -> HealthObservation: ...


class PreflightRunError(RuntimeLifecycleError):
    """A provider-free preflight runtime failed to boot/observe (fail-closed, B0CB-M1)."""


class PreflightRunnerLike(Protocol):
    """Runs the PN02D-A provider-FREE preflight sidecar (B0CB-M1).

    A provider-free sidecar is booted from ``realsidecarpn02d.build_run_command`` — an
    ``--internal`` network, EMPTY provider bindings, NO published port, NO secret — so
    it structurally cannot reach a provider or be reached over a published port.
    ``launch`` MUST fail closed (raise) if the network create or ``docker run`` fails or
    the owned container is not actually running — it must never return a handle for a
    failed/nonexistent runtime (B0CB-M1M2-R1). ``version_signals`` MUST observe three
    GENUINELY INDEPENDENT signals from the RUNNING container — health-endpoint core
    version, in-container installed import version, and image label — not one source
    repeated. B0C-B injects a fake / a fake Docker transport; NO real container is
    launched.
    """

    async def launch(self, argv: Sequence[str]) -> CellProcessHandle: ...

    async def wait_ready(self, handle: CellProcessHandle) -> None:
        """Block until the launched container's HTTP app is healthy (PN02D-B1-PF1).

        MUST be awaited AFTER ``launch`` and BEFORE ``version_signals`` — a container that
        is ``running`` (``docker run -d`` returned) is not yet serving ``/health``. MUST
        fail closed (raise ``PreflightRunError``) if readiness is not reached within a
        bounded timeout, so a never-ready runtime can never be attested. It performs NO
        provider call and reads only a health status code (content-safe)."""
        ...

    async def version_signals(
        self, handle: CellProcessHandle
    ) -> Tuple[str, str, str]: ...

    async def terminate(self, handle: CellProcessHandle) -> None: ...


class RealProviderFreePreflightRunner:
    """Default real provider-free preflight runner (reuses PN02D-A ``realsidecarpn02d``).

    Fail-closed (B0CB-M1M2-R1): a network-create failure, a nonzero/raising ``docker
    run``, or a container that is not actually running raises ``PreflightRunError`` and
    returns NO handle (any partial owned state is cleaned first). ``version_signals``
    observes THREE independent signals from the running container — ``DockerCLI.health``
    (runtime-reported core version), ``DockerCLI.runtime_version`` (in-container
    installed import version), and ``DockerCLI.image_version_label`` (pinned image
    label) — each must be present or it fails closed. It publishes NO port and injects
    NO provider secret.

    PN02D-B1-PF1: ``wait_ready`` (a bounded readiness poll reusing
    ``realsidecarpn02d.wait_healthy``) MUST run between ``launch`` and ``version_signals``
    — a real container is ``running`` from ``docker run -d`` before its HTTP app answers
    ``/health``, so probing the version signals immediately (the B0C-B behaviour that was
    only ever exercised against a fake instant-health ``DockerCLI``) fails closed against a
    container that would have become healthy. ``now``/``sleep``/``readiness_timeout_s`` are
    injectable so the readiness race is unit-testable deterministically.
    """

    def __init__(
        self,
        docker: object = None,
        image: str = REAL_LIGHTRAG_IMAGE,
        *,
        readiness_timeout_s: float = PREFLIGHT_READINESS_TIMEOUT_S,
        readiness_poll_s: float = PREFLIGHT_READINESS_POLL_S,
        now: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if docker is None:
            from open_notebook.integrations.graphrag.eval.realsidecarpn02d import (
                DockerCLI,
            )

            docker = DockerCLI()
        self._docker = docker
        self._image = image
        self._readiness_timeout_s = readiness_timeout_s
        self._readiness_poll_s = readiness_poll_s
        self._now = now
        self._sleep = sleep
        self._net_by_container: Dict[str, str] = {}

    @staticmethod
    def _arg_after(argv: Sequence[str], flag: str) -> str:
        argv = list(argv)
        return argv[argv.index(flag) + 1] if flag in argv else ""

    async def launch(self, argv: Sequence[str]) -> CellProcessHandle:
        net = self._arg_after(argv, "--network")
        name = self._arg_after(argv, "--name")
        if not name:
            raise PreflightRunError("provider-free preflight command has no --name")
        created_net = False
        try:
            if net:
                if not self._docker.network_create_internal(net):  # type: ignore[attr-defined]
                    raise PreflightRunError(
                        "provider-free preflight network create failed (fail-closed)"
                    )
                self._net_by_container[name] = net
                created_net = True
            result = self._docker.run(argv)  # type: ignore[attr-defined]
            if getattr(result, "returncode", 1) != 0:
                raise PreflightRunError(
                    "provider-free preflight docker run failed (fail-closed)"
                )
            obs = self._docker.inspect_state(name)  # type: ignore[attr-defined]
            if not getattr(obs, "container_running", False):
                raise PreflightRunError(
                    "provider-free preflight container is not running (fail-closed)"
                )
            return CellProcessHandle(identifier=name, kind="docker")
        except Exception as exc:  # noqa: BLE001 - any launch failure fails closed
            # Fail-closed cleanup of any partial owned state (task §21).
            try:
                self._docker.stop(name)  # type: ignore[attr-defined]
                self._docker.rm(name, force=True)  # type: ignore[attr-defined]
            except Exception:  # noqa: BLE001 - cleanup must not mask the original error
                pass
            if created_net:
                try:
                    self._docker.network_rm(net)  # type: ignore[attr-defined]
                except Exception:  # noqa: BLE001
                    pass
                self._net_by_container.pop(name, None)
            # Normalize ANY failure (nonzero run, raised docker error, missing container)
            # to a content-safe PreflightRunError — never a handle for a failed runtime.
            if isinstance(exc, PreflightRunError):
                raise
            raise PreflightRunError(
                f"provider-free preflight launch failed: {type(exc).__name__}"
            ) from exc

    async def wait_ready(self, handle: CellProcessHandle) -> None:
        """Bounded, fail-closed wait for the launched container to become healthy (PF1).

        Reuses ``realsidecarpn02d.wait_healthy`` (no duplicate polling): polls
        ``inspect_state`` + ``health`` until a 2xx health code (ready) or the bounded
        timeout. On timeout — or if the container stops — it raises ``PreflightRunError``
        so a never-ready runtime can never reach ``version_signals``/attestation. Reads
        only a health status code; performs NO provider call. Cleanup of the (still-owned)
        container is handled by ``terminate``/``teardown_preflight``."""
        name = handle.identifier
        healthy, _elapsed, _obs = wait_healthy(
            self._docker,  # type: ignore[arg-type]
            name,
            timeout_seconds=self._readiness_timeout_s,
            poll_seconds=self._readiness_poll_s,
            now=self._now,
            sleep=self._sleep,
        )
        if not healthy:
            raise PreflightRunError(
                "provider-free preflight container did not become healthy within "
                f"{self._readiness_timeout_s:g}s (fail-closed, PF1)"
            )

    async def version_signals(
        self, handle: CellProcessHandle
    ) -> Tuple[str, str, str]:
        name = handle.identifier
        # Signal 1 — runtime health-endpoint core version (from the running container).
        code, core, _status = self._docker.health(name)  # type: ignore[attr-defined]
        if code is None or not (200 <= int(code) < 300) or not core:
            raise PreflightRunError(
                "provider-free preflight health/core-version unavailable (fail-closed)"
            )
        # Signal 2 — in-container installed import version (independent of the label).
        installed = self._docker.runtime_version(name)  # type: ignore[attr-defined]
        if not installed:
            raise PreflightRunError(
                "provider-free preflight installed runtime version unavailable"
            )
        # Signal 3 — pinned image label (independent metadata source).
        image_label = self._docker.image_version_label(self._image)  # type: ignore[attr-defined]
        if not image_label:
            raise PreflightRunError("provider-free preflight image label unavailable")
        return (core, installed, image_label)

    async def terminate(self, handle: CellProcessHandle) -> None:
        name = handle.identifier
        try:
            self._docker.stop(name)  # type: ignore[attr-defined]
            self._docker.rm(name, force=True)  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001 - cleanup must not raise
            pass
        net = self._net_by_container.pop(name, None)
        if net:
            try:
                self._docker.network_rm(net)  # type: ignore[attr-defined]
            except Exception:  # noqa: BLE001
                pass


#: (notebook_id) -> (import_version, image_label_version) — the two provider-free
#: version signals besides ``/health``'s core_version (a docker-exec import + the OCI
#: image label in a real run; injected in tests).
VersionSignalReader = Callable[[str], Awaitable[Tuple[str, str]]]
#: () -> next run-owned loopback port.
PortAllocator = Callable[[], int]
#: (notebook_id) -> run-owned storage dir (content-safe path string).
StorageDirAllocator = Callable[[str], str]


# --------------------------------------------------------------------------- #
# Runtime + attestation records (content-safe)
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class RuntimeEndpoint:
    notebook_id: str
    notebook_record_id: str
    workspace_id: str
    host: str
    port: int
    base_url: str
    storage_dir: str
    container_identity: str
    provider_bound: bool


@dataclass(frozen=True)
class ExecutionRuntimeAttestation:
    """Attestation of one provider-bound execution runtime (design §5, no egress gate)."""

    notebook_id: str
    workspace_id: str
    endpoint: str
    version: RealVersionAttestation
    workspace_ok: bool
    endpoint_ok: bool
    storage_ok: bool
    healthy: bool
    attested: bool
    failure_reasons: Tuple[str, ...]


@dataclass(frozen=True)
class PreflightRuntimeResult:
    """Boot-1 provider-free preflight outcome (design §5 Stage P)."""

    gate0_passed: bool
    gate1_passed: bool
    runtime_count: int
    container_identities: Tuple[str, ...]
    torn_down: bool

    @property
    def passed(self) -> bool:
        return self.gate0_passed and self.gate1_passed


# --------------------------------------------------------------------------- #
# The runtime manager
# --------------------------------------------------------------------------- #

@dataclass
class RealPN02RuntimeManager:
    """Boots + attests the PN02 runtimes and builds the real route table (design §9)."""

    fx: FixturePN02
    process_controller: ProcessControllerLike
    health_prober: HealthProberLike
    version_signal_reader: VersionSignalReader
    port_allocator: PortAllocator
    storage_dir_allocator: StorageDirAllocator
    #: B0CB-M1: the provider-free preflight runner (defaults to the real PN02D-A path;
    #: a fake is injected in B0C-B tests). Distinct from ``process_controller`` (which
    #: boots the provider-BOUND execution runtimes with a published port).
    preflight_runner: PreflightRunnerLike = field(
        default_factory=RealProviderFreePreflightRunner
    )
    run_id: str = "pn02db0cb"
    host: str = _LOOPBACK_HOST
    image: str = REAL_LIGHTRAG_IMAGE
    graceful_timeout_s: float = 10.0

    _preflight_handles: Dict[str, CellProcessHandle] = field(default_factory=dict)
    _preflight_identities: set = field(default_factory=set)
    _preflight_commands: List[List[str]] = field(default_factory=list)
    _execution_handles: Dict[str, CellProcessHandle] = field(default_factory=dict)
    _execution_endpoints: Dict[str, RuntimeEndpoint] = field(default_factory=dict)
    _preflight_done: bool = False
    _preflight_torn_down: bool = False
    _preflight_passed: bool = False
    _execution_attested: bool = False

    # -- identity ----------------------------------------------------------- #

    def _workspace_id(self, notebook_record_id: str) -> str:
        return workspace_id_for(notebook_record_id)

    def _notebooks(self):
        return list(self.fx.notebooks)

    # -- Boot 1: provider-free preflight (Stage P) -------------------------- #

    async def boot_preflight(self, *, run_id: Optional[str] = None) -> PreflightRuntimeResult:
        """Boot the provider-FREE preflight runtimes, attest, and TEAR THEM DOWN (M1).

        Gate 0 (single) ∧ Gate 1 (three-runtime topology): each runtime is booted from
        the PN02D-A provider-free ``build_run_command`` (``--internal`` network, EMPTY
        provider bindings, NO published port, NO secret). ``assert_no_provider_binding_
        in_command`` structurally refuses any provider binding/secret in the argv BEFORE
        launch, so the preflight can neither reach a provider nor be reached over a
        published port. The version is attested from three provider-free signals (no
        HTTP ``/health`` on a published port). Runtimes are torn down before any
        provider-bound boot. The pass flag (M2) is set ONLY on a fully-attested success
        and NEVER from the ``finally`` teardown.
        """
        rid = run_id or self.run_id
        identities: List[str] = []
        gate_ok = True
        try:
            for nb in self._notebooks():
                # B0CB-M1M2-R1: ANY per-runtime failure (provider-binding guard, launch
                # network/run/container failure, or a missing/mismatched version signal)
                # fails the preflight CLOSED (gate_ok=False) — it never silently passes.
                try:
                    spec = make_spec(
                        run_id=rid,
                        notebook_id=nb.notebook_id,
                        notebook_record_id=nb.record_id,
                        workspace_id=self._workspace_id(nb.record_id),
                        storage_root=self.storage_dir_allocator(
                            f"preflight-{nb.notebook_id}"
                        ),
                    )
                    argv = build_run_command(spec)
                    # Structural provider-free guard: raises if any provider
                    # binding/secret or a published-port-style value is present.
                    assert_no_provider_binding_in_command(argv)
                    if "-p" in argv:  # defence in depth: preflight publishes NO port
                        raise RuntimeLifecycleError(
                            "provider-free preflight boot command must not publish a port"
                        )
                    self._preflight_commands.append(list(argv))
                    handle = await self.preflight_runner.launch(argv)
                    self._preflight_handles[nb.notebook_id] = handle
                    self._preflight_identities.add(handle.identifier)
                    identities.append(handle.identifier)
                    # PN02D-B1-PF1: WAIT for the container's HTTP app to become healthy
                    # BEFORE probing the version signals. `launch` only proves the
                    # container is RUNNING (`docker run -d`); the LightRAG app answers
                    # `/health` seconds later. Probing immediately (the pre-PF1 behaviour)
                    # made the signals unavailable and failed the gate closed against a
                    # container that would have become healthy. Fail-closed on timeout.
                    await self.preflight_runner.wait_ready(handle)
                    # THREE independent signals from the RUNNING, now-READY container.
                    hc, iv, il = await self.preflight_runner.version_signals(handle)
                    version = attest_version(
                        health_core_version=hc,
                        import_version=iv,
                        image_label_version=il,
                        expected_label=EXPECTED_IMAGE_VERSION_LABEL,
                    )
                    if not version.attested:
                        gate_ok = False
                except Exception:  # noqa: BLE001 - any failure fails preflight closed
                    gate_ok = False
        finally:
            await self.teardown_preflight()
        gate0 = gate_ok
        gate1 = gate_ok and len(identities) == len(self._notebooks())
        self._preflight_done = True
        # M2: PASS is set ONLY here, on the normal (non-exception) path, and only when
        # every gate attested — never unconditionally and never in the finally block.
        self._preflight_passed = gate0 and gate1
        return PreflightRuntimeResult(
            gate0_passed=gate0,
            gate1_passed=gate1,
            runtime_count=len(identities),
            container_identities=tuple(identities),
            torn_down=self._preflight_torn_down,
        )

    async def teardown_preflight(self) -> None:
        """Tear down every provider-free preflight runtime (owned-only, idempotent)."""
        for handle in list(self._preflight_handles.values()):
            try:
                await self.preflight_runner.terminate(handle)
            except Exception:  # noqa: BLE001 - cleanup must not raise
                pass
        self._preflight_handles.clear()
        self._preflight_torn_down = True

    # -- Boot 2: provider-bound execution ----------------------------------- #

    async def boot_execution(
        self,
        *,
        live_auth: object,
        materialized_binding: MaterializedProviderBinding,
        binding: Optional[DiagnosticProviderBinding08] = None,
    ) -> Mapping[str, ExecutionRuntimeAttestation]:
        """Boot the FRESH provider-BOUND execution runtimes (design §5 Stage X).

        Requires a genuine ``LiveProviderRunAuthorization`` (task §12) and an attested
        materialized binding. Enforces the two-boot order: the preflight must have run
        and been torn down first (``SAME_CONTAINER = NO``), and no execution container
        may reuse a preflight identity.
        """
        require_live_provider_run_authorization(live_auth)
        if not materialized_binding.attested:
            raise RuntimeLifecycleError(
                "cannot boot provider-bound runtimes from an unattested binding"
            )
        if not self._preflight_done or not self._preflight_torn_down:
            raise TwoBootOrderError(
                "provider-bound execution boot requires a completed + torn-down "
                "provider-free preflight first (two-boot lifecycle, design §5)"
            )
        # B0CB-M2: execution is impossible unless the preflight PASSED (a failed or
        # partial preflight can never boot provider-bound execution).
        if not self._preflight_passed:
            raise TwoBootOrderError(
                "provider-bound execution boot requires a SUCCESSFUL provider-free "
                "preflight (preflight did not pass, B0CB-M2)"
            )
        binding = binding or frozen_provider_binding()
        binding.validate()

        attestations: Dict[str, ExecutionRuntimeAttestation] = {}
        for nb in self._notebooks():
            workspace_id = self._workspace_id(nb.record_id)
            port = self.port_allocator()
            storage_dir = self.storage_dir_allocator(f"exec-{nb.notebook_id}")
            spec = CellProcessSpec(
                cell_id=f"exec-{nb.notebook_id}",
                workspace=workspace_id,
                working_dir=storage_dir,
                host=self.host,
                port=port,
                image=self.image,
                provider_binding=binding,  # provider-BOUND (design §5 Stage X)
            )
            handle = await self.process_controller.start(spec)
            # SAME_CONTAINER = NO: an execution container must not reuse a preflight id.
            if handle.identifier in self._preflight_container_identities():
                raise TwoBootOrderError(
                    "execution runtime reused a preflight container identity "
                    "(SAME_CONTAINER must be NO, design §5)"
                )
            self._execution_handles[nb.notebook_id] = handle
            base_url = f"http://{spec.host}:{spec.port}"
            endpoint = RuntimeEndpoint(
                notebook_id=nb.notebook_id,
                notebook_record_id=nb.record_id,
                workspace_id=workspace_id,
                host=spec.host,
                port=port,
                base_url=base_url,
                storage_dir=storage_dir,
                container_identity=handle.identifier,
                provider_bound=True,
            )
            self._execution_endpoints[nb.notebook_id] = endpoint
            obs = await self.health_prober.probe(
                base_url=base_url, host=spec.host, port=port
            )
            version = await self._attest_runtime_version(nb.notebook_id, obs)
            attestations[nb.notebook_id] = self._attest_execution_runtime(
                endpoint, obs, version
            )
        self._execution_attested = all(a.attested for a in attestations.values())
        return attestations

    def _preflight_container_identities(self) -> frozenset:
        # Captured at boot (persists past teardown, which clears the handle map), so the
        # SAME_CONTAINER=NO guard can reject any execution container that reuses a
        # preflight identity.
        return frozenset(self._preflight_identities)

    async def _attest_runtime_version(
        self, notebook_id: str, obs: HealthObservation
    ) -> RealVersionAttestation:
        import_version, image_label = await self.version_signal_reader(notebook_id)
        return attest_version(
            health_core_version=obs.core_version,
            import_version=import_version,
            image_label_version=image_label,
            expected_label=EXPECTED_IMAGE_VERSION_LABEL,
        )

    def _attest_execution_runtime(
        self,
        endpoint: RuntimeEndpoint,
        obs: HealthObservation,
        version: RealVersionAttestation,
    ) -> ExecutionRuntimeAttestation:
        reasons: List[str] = []
        if not version.attested:
            reasons.append("version_not_attested")
        workspace_ok = obs.workspace in ("", endpoint.workspace_id)
        if not workspace_ok:
            reasons.append("workspace_id_mismatch")
        endpoint_ok = endpoint.base_url == f"http://{endpoint.host}:{endpoint.port}"
        if not endpoint_ok:
            reasons.append("endpoint_ownership_mismatch")
        storage_ok = bool(endpoint.storage_dir)
        if not storage_ok:
            reasons.append("storage_identity_missing")
        healthy = obs.reachable and obs.healthy
        if not healthy:
            reasons.append("unhealthy")
        return ExecutionRuntimeAttestation(
            notebook_id=endpoint.notebook_id,
            workspace_id=endpoint.workspace_id,
            endpoint=endpoint.base_url,
            version=version,
            workspace_ok=workspace_ok,
            endpoint_ok=endpoint_ok,
            storage_ok=storage_ok,
            healthy=healthy,
            attested=not reasons,
            failure_reasons=tuple(reasons),
        )

    # -- route table + cleanup --------------------------------------------- #

    def route_table(self) -> Dict[str, NotebookRuntimeRoute]:
        """The real route table (endpoints = booted loopback base_urls). Post-attestation.

        Operations are enabled only after execution attestation (design §5): this refuses
        to hand out routes until every execution runtime attested.
        """
        if not self._execution_attested:
            raise RuntimeLifecycleError(
                "route table is unavailable until every execution runtime is attested "
                "(operations enabled only after execution attestation, design §5)"
            )
        endpoints = {
            nb: ep.base_url for nb, ep in self._execution_endpoints.items()
        }
        storage = {
            nb: ep.storage_dir for nb, ep in self._execution_endpoints.items()
        }
        return build_route_table(self.fx, endpoints=endpoints, storage_identities=storage)

    def endpoint_for(self, notebook_id: str) -> RuntimeEndpoint:
        ep = self._execution_endpoints.get(notebook_id)
        if ep is None:
            raise RuntimeLifecycleError(f"no execution runtime for {notebook_id!r}")
        return ep

    async def cleanup(self) -> Dict[str, object]:
        """Owned-only, idempotent teardown of every runtime (design §16)."""
        await self.teardown_preflight()
        remaining = 0
        for handle in list(self._execution_handles.values()):
            try:
                await self.process_controller.terminate(
                    handle, graceful_timeout_s=self.graceful_timeout_s
                )
            except Exception:  # noqa: BLE001 - cleanup must not raise
                remaining += 1
        self._execution_handles.clear()
        self._execution_endpoints.clear()
        self._execution_attested = False
        return {
            "runtime_cleanup": "owned_only",
            "execution_runtimes_remaining": remaining,
            "preflight_torn_down": self._preflight_torn_down,
            "sidecar_internal_port": SIDECAR_INTERNAL_PORT,
        }


__all__ = [
    "RuntimeLifecycleError",
    "TwoBootOrderError",
    "PreflightRunError",
    "HealthObservation",
    "ProcessControllerLike",
    "HealthProberLike",
    "PreflightRunnerLike",
    "RealProviderFreePreflightRunner",
    "VersionSignalReader",
    "PortAllocator",
    "StorageDirAllocator",
    "RuntimeEndpoint",
    "ExecutionRuntimeAttestation",
    "PreflightRuntimeResult",
    "RealPN02RuntimeManager",
]
