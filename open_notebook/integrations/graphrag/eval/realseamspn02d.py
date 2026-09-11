"""Canonical REAL composition of ``LiveB1Seams`` + the real B1 execution runner (PN02D-B1-EW1).

EVALUATION-ONLY. Nothing in production imports this (``PRODUCTION_IMPORTS_EVAL = NO``).
This module is the missing composition boundary the PN02D-B1 real execution attempt hit:
``RealB1Driver`` (the orchestrator) and ``LiveB1Seams`` (the execution boundary) both
exist, but before EW1 no PRODUCTION path assembled ALL required real seams and invoked
``RealB1Driver.run`` — the only ``LiveB1Seams`` constructions were test fakes, and the
``execute-b1-live`` CLI intentionally returned ``REASON_NO_LIVE_SEAMS``.

``build_real_b1_live_seams`` composes the ONE canonical, all-real ``LiveB1Seams`` from the
already-approved B0C-B / PF1 components — it introduces NO new provider client, NO new
Docker orchestration, and NO new scientific logic. Every genuinely EXTERNAL boundary
(Docker CLI + controller, the provider-free preflight runner, the HTTP health prober, the
LightRAG httpx transports, the isolated-namespace ``repo_query``, the corpus create/embed
seams, the query-embedding provider call, the active-model reader) is an injected edge that
DEFAULTS to its real production implementation; a controlled offline test may replace ONLY
those edges to exercise ``CLI → builder → LiveB1Seams → RealB1Driver.run`` with ZERO
provider traffic. The derived seams (health-observation adapter, Boot-2 version-signal
reader, port/storage allocators, member-scoped row fetcher, query-embed wrapper, the real
active-embedding-model attestor, the notebook-record-id map, secret-name presence) are
composed internally from those edges — never faked in production.

Secret model (task §4/§18):

  * ``OPENROUTER_API_KEY`` — the PROVIDER credential. Resolved by NAME only for presence;
    its VALUE is inherited late by the sidecar launch boundary (``DockerCellProcess
    Controller``) and never read, printed, logged, serialized, or placed on argv here.
  * ``GRAPHRAG_POC_API_KEY`` (``LIGHTRAG_LOCAL_AUTH_ENV``) — the LOCAL LightRAG sidecar
    auth token (NOT the provider credential). Its value is read into memory ONLY to send as
    the ``Authorization`` header from the index/GD/delete adapters to the loopback sidecar;
    it is never printed/serialized/committed. The two are distinct (``SECRETS_CONFLATED = NO``).

Zero-provider posture of THIS module: importing/constructing/composing performs NO provider
call, NO real Docker boot, and NO normal-DB access; the real external effects happen only
inside ``RealB1Driver.run`` under a future authorized live run (``PN02_PROVIDER_RUN_
AUTHORIZED`` stays NO).
"""

from __future__ import annotations

from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import dataclass, field
from typing import (
    AsyncIterator,
    Awaitable,
    Callable,
    Dict,
    FrozenSet,
    Mapping,
    Optional,
    Protocol,
    Sequence,
    Tuple,
)

import httpx

from open_notebook.integrations.graphrag.eval.authmintlivepn02d import (
    EXPECTED_FIXTURE_HASH,
    GitBaselineAttestation,
    OperatorRunGrant,
)
from open_notebook.integrations.graphrag.eval.cell_provisioner08 import (
    CellProcessHandle,
    CellProcessSpec,
    DockerCellProcessController,
    EphemeralPortAllocator,
    LightRagCellHealthProber,
)
from open_notebook.integrations.graphrag.eval.corpuslivepn02d import (
    build_in_isolation_corpus_seams,
)
from open_notebook.integrations.graphrag.eval.datasetpn02 import (
    FixturePN02,
    load_fixture,
)
from open_notebook.integrations.graphrag.eval.driver_live_pn02d import (
    LiveB1Seams,
    RealB1Driver,
)
from open_notebook.integrations.graphrag.eval.driverpn02d import B1RunOutcome
from open_notebook.integrations.graphrag.eval.provbindpn02d import (
    env_present_secret_names,
)
from open_notebook.integrations.graphrag.eval.provider_binding08 import (
    FROZEN_EMBEDDING_DIM,
    FROZEN_EMBEDDING_MODEL,
    frozen_provider_binding,
)
from open_notebook.integrations.graphrag.eval.realsidecarpn02d import (
    REAL_LIGHTRAG_IMAGE,
    DockerCLI,
    allocate_real_storage_root,
)
from open_notebook.integrations.graphrag.eval.runtimelivepn02d import (
    HealthObservation,
    HealthProberLike,
    PortAllocator,
    PreflightRunnerLike,
    ProcessControllerLike,
    RealProviderFreePreflightRunner,
    StorageDirAllocator,
    VersionSignalReader,
)
from open_notebook.integrations.graphrag.eval.vectoradapterpn02d import (
    ActiveEmbeddingModelAttestation,
    EmbeddingModelAttestor,
    MemberRowFetcher,
    QueryEmbedFn,
    build_repo_query_member_row_fetcher,
)

_LOOPBACK_HOST = "127.0.0.1"

#: The LOCAL LightRAG sidecar auth env NAME (NOT the provider credential — task §4). Its
#: value authenticates the loopback index/GD/delete HTTP calls; ``DockerCellProcessController``
#: uses the SAME env to inherit ``LIGHTRAG_API_KEY`` into the sidecar.
LIGHTRAG_LOCAL_AUTH_ENV = "GRAPHRAG_POC_API_KEY"

#: The exec-runtime container cell-id prefix used by ``RealPN02RuntimeManager.boot_execution``
#: (``exec-<notebook_id>``). The recording controller keys captured container names by cell id.
_EXEC_CELL_PREFIX = "exec-"


class RealSeamCompositionError(RuntimeError):
    """A required real seam could not be composed (fail-closed, content-free)."""


# --------------------------------------------------------------------------- #
# Recording process controller — captures the started container name per cell
# --------------------------------------------------------------------------- #

@dataclass
class RecordingProcessController:
    """Wrap a real ``ProcessControllerLike`` and record each started container name.

    The Boot-2 ``version_signal_reader`` is handed only a ``notebook_id`` by the runtime
    manager, but the two version signals must be read from the ACTUAL exec container. This
    wrapper records ``spec.cell_id -> handle.identifier`` at ``start`` time (which the
    manager always calls before the version read), so the reader resolves the real container
    name without hard-coding the controller's private naming convention.
    """

    inner: ProcessControllerLike
    container_name_by_cell: Dict[str, str] = field(default_factory=dict)

    async def start(self, spec: CellProcessSpec) -> CellProcessHandle:
        handle = await self.inner.start(spec)
        self.container_name_by_cell[spec.cell_id] = handle.identifier
        return handle

    async def terminate(
        self, handle: CellProcessHandle, *, graceful_timeout_s: float
    ) -> object:
        return await self.inner.terminate(handle, graceful_timeout_s=graceful_timeout_s)


# --------------------------------------------------------------------------- #
# Health-observation adapter (CellHealthObservation -> HealthObservation)
# --------------------------------------------------------------------------- #

class _CellProberLike(Protocol):
    """The narrow probe surface ``LightRagCellHealthProber`` satisfies (returns any obs)."""

    async def probe(self, *, base_url: str, host: str, port: int) -> object: ...


@dataclass
class HealthProberAdapter:
    """Adapt a real ``LightRagCellHealthProber`` to the runtime manager's ``HealthProberLike``.

    ``LightRagCellHealthProber.probe`` returns a ``CellHealthObservation`` (``version`` /
    ``reported_workspace``); the runtime manager reads ``core_version`` / ``workspace`` off a
    ``HealthObservation``. This maps the fields — no behaviour change, no fabricated health.
    """

    inner: _CellProberLike

    async def probe(self, *, base_url: str, host: str, port: int) -> HealthObservation:
        obs = await self.inner.probe(base_url=base_url, host=host, port=port)
        return HealthObservation(
            reachable=bool(getattr(obs, "reachable", False)),
            healthy=bool(getattr(obs, "healthy", False)),
            core_version=str(getattr(obs, "version", "") or ""),
            workspace=str(getattr(obs, "reported_workspace", "") or ""),
        )


# --------------------------------------------------------------------------- #
# Composed real seams (built from injected external edges)
# --------------------------------------------------------------------------- #

def build_version_signal_reader(
    *, controller: RecordingProcessController, docker: DockerCLI, image: str
) -> VersionSignalReader:
    """Compose the Boot-2 ``version_signal_reader`` from real ``DockerCLI`` signals.

    Returns ``(import_version, image_label)`` for a notebook's exec runtime: the in-container
    installed ``lightrag.__version__`` (``DockerCLI.runtime_version``) and the pinned image's
    OCI version label (``DockerCLI.image_version_label``) — the two provider-free signals the
    runtime manager combines with the ``/health`` core version for ``attest_version``. The
    exec container name is resolved from the recording controller (never hard-coded).
    """

    async def _read(notebook_id: str) -> Tuple[str, str]:
        cell_id = f"{_EXEC_CELL_PREFIX}{notebook_id}"
        name = controller.container_name_by_cell.get(cell_id)
        if not name:
            raise RealSeamCompositionError(
                "version_signal_reader invoked before the exec container was started"
            )
        installed = docker.runtime_version(name)
        image_label = docker.image_version_label(image)
        return (installed, image_label)

    return _read


def build_real_query_embed_fn() -> QueryEmbedFn:
    """Compose the real query-embedding fn over ``utils.embedding.generate_embedding``.

    ``generate_embedding`` routes through the DefaultModels / ModelManager provisioning
    abstraction (never an ad-hoc provider client). The frozen provider fingerprint / model
    identity is enforced by the model attestor (below) + the runtime provider binding; the
    exact embedding dimension (1536) is independently enforced by
    ``RealPN02VectorBackend.embed_query`` before ranking.
    """

    async def _embed(question: str) -> Sequence[float]:
        from open_notebook.utils.embedding import generate_embedding

        return await generate_embedding(question)

    return _embed


def build_real_model_attestor() -> EmbeddingModelAttestor:
    """Compose the REAL active-embedding-model attestor (task §15). Not an always-true stub.

    Reads the CURRENTLY-configured default embedding model from ``DefaultModels`` →
    ``Model`` (provider + name) — a genuine runtime read that FAILS the frozen match when a
    different provider/model is configured. The dimension is reported as the known frozen
    dimension (1536) ONLY when the active model name equals the frozen embedding model, else
    ``0`` (so a mismatch never masquerades as the frozen dimension); the ACTUAL runtime
    embedding length is independently enforced by ``RealPN02VectorBackend.embed_query``.
    """

    async def _attest() -> ActiveEmbeddingModelAttestation:
        from open_notebook.ai.models import DefaultModels, Model

        defaults = await DefaultModels.get_instance()
        model_id = getattr(defaults, "default_embedding_model", None)
        if not model_id:
            return ActiveEmbeddingModelAttestation(provider="", model="", dimension=0)
        model = await Model.get(model_id)
        provider = str(getattr(model, "provider", "") or "")
        name = str(getattr(model, "name", "") or "")
        dimension = FROZEN_EMBEDDING_DIM if name == FROZEN_EMBEDDING_MODEL else 0
        return ActiveEmbeddingModelAttestation(
            provider=provider, model=name, dimension=dimension
        )

    return _attest


def default_notebook_record_ids(fx: FixturePN02) -> Dict[str, str]:
    """The PN02-scope-bound notebook_id -> fixture record-id map (task §14).

    Bound to the frozen synthetic three-notebook fixture (``NotebookPN02.record_id``); it
    never accepts caller-supplied production notebook ids. A ``RELATE $src->reference->$nb``
    edge does not require a separately-created ``notebook`` record — the id string suffices.
    """
    return {nb.notebook_id: nb.record_id for nb in fx.notebooks}


def _default_repo_query() -> Callable[..., Awaitable[Sequence[Mapping[str, object]]]]:
    from open_notebook.database.repository import repo_query

    return repo_query


# --------------------------------------------------------------------------- #
# THE canonical real-seams builder (task §7/§8)
# --------------------------------------------------------------------------- #

def build_real_b1_live_seams(
    fx: FixturePN02,
    *,
    run_id: str,
    host: str = _LOOPBACK_HOST,
    image: str = REAL_LIGHTRAG_IMAGE,
    storage_base_dir: Optional[str] = None,
    env: Optional[Mapping[str, str]] = None,
    # ---- external boundaries (REAL by default; injected ONLY for controlled tests) ----
    docker: Optional[DockerCLI] = None,
    process_controller: Optional[ProcessControllerLike] = None,
    preflight_runner: Optional[PreflightRunnerLike] = None,
    health_prober: Optional[HealthProberLike] = None,
    version_signal_reader: Optional[VersionSignalReader] = None,
    port_allocator: Optional[PortAllocator] = None,
    storage_dir_allocator: Optional[StorageDirAllocator] = None,
    corpus_seams: Optional[Mapping[str, object]] = None,
    repo_query: Optional[Callable[..., Awaitable[Sequence[Mapping[str, object]]]]] = None,
    member_row_fetcher: Optional[MemberRowFetcher] = None,
    query_embed_fn: Optional[QueryEmbedFn] = None,
    model_attestor: Optional[EmbeddingModelAttestor] = None,
    notebook_record_ids: Optional[Mapping[str, str]] = None,
    index_transport: Optional[httpx.AsyncBaseTransport] = None,
    gd_transport: Optional[httpx.AsyncBaseTransport] = None,
    delete_transport: Optional[httpx.AsyncBaseTransport] = None,
    lightrag_api_key: Optional[str] = None,
    present_secret_envs: Optional[FrozenSet[str]] = None,
    corpus_teardown: Optional[Callable[[], Awaitable[None]]] = None,
) -> LiveB1Seams:
    """Assemble the ONE canonical, all-real ``LiveB1Seams`` (task §7/§8/§9-§18).

    Composition only — it wires already-approved components; it adds no provider client,
    Docker orchestration, or scientific logic, and puts no evaluation policy here (that
    stays in ``RealB1Driver``/the reused orchestrator). Every argument is an EXTERNAL edge
    with a REAL production default; a controlled offline test injects fakes/mocks for the
    Docker/HTTP/DB/provider edges ONLY. When called with real defaults for the corpus seams
    it MUST run inside an active isolated Surreal namespace (``build_in_isolation_corpus_
    seams`` asserts active isolation) — ``run_live_b1_execution`` enters that isolation.

    No required execution seam is a placeholder: the three transports default to ``None``,
    which the index/GD/delete adapters interpret as the REAL httpx transport (task §17) — a
    documented real path, not a fake fallback.
    """
    env = dict(env) if env is not None else None
    docker = docker if docker is not None else DockerCLI()

    # Runtime / Docker seams (Boot 2 + Boot 1).
    inner_controller: ProcessControllerLike = (
        process_controller
        if process_controller is not None
        else DockerCellProcessController()
    )
    recording_controller = (
        inner_controller
        if isinstance(inner_controller, RecordingProcessController)
        else RecordingProcessController(inner_controller)
    )
    preflight_runner = (
        preflight_runner
        if preflight_runner is not None
        else RealProviderFreePreflightRunner(docker=docker, image=image)
    )
    if health_prober is None:
        local_auth = (
            lightrag_api_key
            if lightrag_api_key is not None
            else _read_local_sidecar_auth(env)
        )
        health_prober = HealthProberAdapter(
            LightRagCellHealthProber(api_key=local_auth)
        )
    if version_signal_reader is None:
        version_signal_reader = build_version_signal_reader(
            controller=recording_controller, docker=docker, image=image
        )
    if port_allocator is None:
        allocator = EphemeralPortAllocator()
        port_allocator = lambda: allocator.allocate(host)  # noqa: E731 - tiny adapter
    if storage_dir_allocator is None:
        storage_dir_allocator = lambda cell_id: allocate_real_storage_root(  # noqa: E731
            storage_base_dir, cell_id
        )

    # Corpus provisioning seams (create Source / RELATE reference / vectorize once).
    if corpus_seams is None:
        corpus_seams = build_in_isolation_corpus_seams(run_id=run_id)
    source_creator = corpus_seams["source_creator"]
    reference_linker = corpus_seams["reference_linker"]
    source_embedder = corpus_seams["source_embedder"]

    # Vector Approach-B seams.
    if member_row_fetcher is None:
        rq = repo_query if repo_query is not None else _default_repo_query()
        member_row_fetcher = build_repo_query_member_row_fetcher(rq)
    if query_embed_fn is None:
        query_embed_fn = build_real_query_embed_fn()
    if model_attestor is None:
        model_attestor = build_real_model_attestor()

    # Identity + secrets (NAMES only; the local sidecar value is used only as an auth header).
    if notebook_record_ids is None:
        notebook_record_ids = default_notebook_record_ids(fx)
    if lightrag_api_key is None:
        lightrag_api_key = _read_local_sidecar_auth(env)
    if present_secret_envs is None:
        required = frozen_provider_binding().required_secret_envs()
        if env is None:
            present_secret_envs = frozenset(env_present_secret_names(set(required)))
        else:
            present_secret_envs = frozenset(
                n for n in required if (env.get(n, "") or "").strip()
            )

    return LiveB1Seams(
        process_controller=recording_controller,
        health_prober=health_prober,
        version_signal_reader=version_signal_reader,
        port_allocator=port_allocator,
        storage_dir_allocator=storage_dir_allocator,
        source_creator=source_creator,  # type: ignore[arg-type]
        reference_linker=reference_linker,  # type: ignore[arg-type]
        source_embedder=source_embedder,  # type: ignore[arg-type]
        notebook_record_ids=notebook_record_ids,
        query_embed_fn=query_embed_fn,
        member_row_fetcher=member_row_fetcher,
        model_attestor=model_attestor,
        preflight_runner=preflight_runner,
        index_transport=index_transport,
        gd_transport=gd_transport,
        delete_transport=delete_transport,
        lightrag_api_key=lightrag_api_key,
        present_secret_envs=present_secret_envs,
        corpus_teardown=corpus_teardown,
    )


def _read_local_sidecar_auth(env: Optional[Mapping[str, str]]) -> Optional[str]:
    """Read the LOCAL LightRAG sidecar auth token by NAME (never the provider credential).

    Returns the value (used only as a loopback ``Authorization`` header) or ``None`` when the
    sidecar runs without auth. Never printed/serialized/committed (task §18/§36).
    """
    import os

    source = env if env is not None else os.environ
    value = (source.get(LIGHTRAG_LOCAL_AUTH_ENV, "") or "").strip()
    return value or None


#: The production default producers, exported for the no-placeholder/completeness assertions
#: (task §27/§28). Every entry is a REAL production symbol under ``open_notebook`` — never a
#: test fake. A test asserts these identities + that none originate in a ``tests`` module.
REAL_SEAM_DEFAULT_PRODUCERS: Dict[str, object] = {
    "process_controller": DockerCellProcessController,
    "preflight_runner": RealProviderFreePreflightRunner,
    "health_prober": LightRagCellHealthProber,
    "port_allocator": EphemeralPortAllocator,
    "storage_dir_allocator": allocate_real_storage_root,
    "corpus_seams": build_in_isolation_corpus_seams,
    "member_row_fetcher": build_repo_query_member_row_fetcher,
    "query_embed_fn": build_real_query_embed_fn,
    "model_attestor": build_real_model_attestor,
    "version_signal_reader": build_version_signal_reader,
    "docker": DockerCLI,
}


# --------------------------------------------------------------------------- #
# The real B1 execution runner (task §21/§24/§25) — owns isolation lifecycle
# --------------------------------------------------------------------------- #

#: A factory: run_id -> an async context manager that opens/tears down the isolated runtime.
IsolationFactory = Callable[[str], AbstractAsyncContextManager[object]]


@asynccontextmanager
async def _default_isolation(run_id: str) -> AsyncIterator[object]:
    """Enter the real isolated Surreal eval runtime (drops the temp namespace on exit)."""
    from open_notebook.integrations.graphrag.eval.isolation08 import (
        isolated_surreal_eval_runtime,
    )

    async with isolated_surreal_eval_runtime(run_id) as ctx:
        yield ctx


async def run_live_b1_execution(
    *,
    operator_grant: OperatorRunGrant,
    git_baseline_attestation: GitBaselineAttestation,
    observed_fixture_hash: str = EXPECTED_FIXTURE_HASH,
    fx: Optional[FixturePN02] = None,
    seams_builder: Callable[..., LiveB1Seams] = build_real_b1_live_seams,
    isolation: Optional[IsolationFactory] = None,
    builder_kwargs: Optional[Mapping[str, object]] = None,
    env: Optional[Mapping[str, str]] = None,
) -> B1RunOutcome:
    """Run the real two-boot B1 execution IN-PROCESS (task §21/§24/§25).

    Owns the isolated-namespace lifecycle (real by default; injectable), builds the ONE
    canonical real ``LiveB1Seams`` inside that isolation (the corpus seams require it), and
    hands them to ``RealB1Driver.run`` — which OWNS the security ordering (provider-free
    preflight → mint → provider-bound Boot 2 → execute → cleanup). This runner adds NO
    orchestration of its own and does NOT re-mint or bypass the driver. The
    ``LiveProviderRunAuthorization`` remains IN-MEMORY-EPHEMERAL (minted inside the driver in
    this same process); nothing is persisted (``AUTHORIZATION_PERSISTENCE_ADDED = NO``).

    ``seams_builder``/``isolation``/``builder_kwargs`` default to the real production path; a
    controlled offline test injects fakes for the external edges (via ``builder_kwargs``) and
    a no-op isolation to prove reachability with ZERO provider traffic.
    """
    fx = fx or load_fixture()
    isolation_factory: IsolationFactory = (
        isolation if isolation is not None else _default_isolation
    )
    extra = dict(builder_kwargs or {})
    async with isolation_factory(operator_grant.run_id):
        seams = seams_builder(fx, run_id=operator_grant.run_id, env=env, **extra)
        driver = RealB1Driver(fx, seams)
        return await driver.run(
            operator_grant=operator_grant,
            git_baseline_attestation=git_baseline_attestation,
            observed_fixture_hash=observed_fixture_hash,
        )


__all__ = [
    "LIGHTRAG_LOCAL_AUTH_ENV",
    "RealSeamCompositionError",
    "RecordingProcessController",
    "HealthProberAdapter",
    "build_version_signal_reader",
    "build_real_query_embed_fn",
    "build_real_model_attestor",
    "default_notebook_record_ids",
    "build_real_b1_live_seams",
    "REAL_SEAM_DEFAULT_PRODUCERS",
    "run_live_b1_execution",
]
