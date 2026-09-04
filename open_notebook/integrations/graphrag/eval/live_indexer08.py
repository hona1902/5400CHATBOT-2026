"""GraphRAG-08E.4 live per-cell diagnostic INDEXER (EVALUATION-ONLY).

Nothing in production imports this. It is the missing execution leaf the GraphRAG-08E
bounded live concurrency diagnostic needs: given ONE already-provisioned, attested,
ready diagnostic cell (a fresh LightRAG process + unique workspace + owned storage from
``cell_provisioner08``), it indexes that cell's frozen Source subset against THAT cell's
own sidecar and returns content-free ``AttemptRecord``s. It never provisions a cell,
never starts a sidecar, never decides H1/H2/H3, and — in this offline phase — makes NO
provider/network/DB call (the LightRAG index client is INJECTED; a fake drives every
test). The real client is used only under an explicitly-authorized live run.

Frozen reuse (no reimplementation, task §31/§33/§34):
  * retry decision  -> ``concurrency_diag08.characterize_failure`` -> the frozen
    ``index_retry08.is_transient_reason`` (a test asserts zero divergence);
  * failure family  -> the frozen 08E diagnostic taxonomy (inside characterize_failure);
  * per-Source attempt cap = 2 (mirrors ``runner08`` — a test pins the equality).

Endpoint ownership (task §8/§9/§26): the indexer reaches a cell ONLY through the
provisioner's ownership-bound ``active_provisioned_cell`` accessor, cross-checked against
the entered ``DiagnosticCell``'s own identity + provision, and it refuses any endpoint
that is not the exact owned, loopback cell sidecar. It NEVER reads a global/default
GraphRAG base URL.

Raw-error containment (task §35): a provider/LightRAG error string exists only
transiently to compute the content-safe characterization; it is NEVER stored on an
``AttemptRecord``, logged, or returned.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Dict, List, Optional, Protocol
from urllib.parse import urlparse

from open_notebook.integrations.graphrag.eval.cell_isolation08 import (
    CellIdentity,
    DiagnosticCell,
)
from open_notebook.integrations.graphrag.eval.concurrency_diag08 import (
    TERMINAL_FAILED,
    TERMINAL_SUCCESS,
    TERMINAL_TIMEOUT,
    AttemptRecord,
    characterize_failure,
    select_diagnostic_sources,
)

#: Frozen per-Source attempt cap (mirrors ``runner08`` EvalRunConfig08 default). With 30
#: expected submissions and at most one transient retry each, the worst legal provider
#: submission count is 60 <= the 64 hard cap (``concurrency_diag08.MAX_TOTAL_SUBMISSIONS``).
MAX_INDEX_ATTEMPTS_PER_SOURCE = 2

#: The only hosts a diagnostic cell sidecar may resolve to (task §10/§26): loopback.
_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})

# internal poll sentinel (never a real LightRAG state)
_STATE_PROCESSED = "PROCESSED"
_STATE_FAILED = "FAILED"
_STATE_IN_PROGRESS = "IN_PROGRESS"
_STATE_TIMEOUT = "TIMEOUT"


class LiveIndexError(RuntimeError):
    """Base class for live-indexer failures (all fail-closed)."""


class CellEndpointError(LiveIndexError):
    """A cell's sidecar endpoint could not be established with proven ownership."""


class CellNotReadyError(LiveIndexError):
    """The indexer was handed a cell that is not fully provisioned/valid."""


# ---------------------------------------------------------------------------
# Ownership-bound cell endpoint (content-free) + resolution.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CellEndpoint:
    """The exact owned cell sidecar the indexer may talk to (content-free)."""

    run_id: str
    cell_id: str
    base_url: str
    port: int
    workspace: str
    storage_dir: str
    container_identity: str


class ProvisionerLike(Protocol):
    """The narrow read-only capability the indexer needs: an ownership-bound accessor to
    the active provisioned cell (``cell_provisioner08.LightRagCellProvisioner`` satisfies
    it structurally). The indexer never provisions or disposes."""

    def active_provisioned_cell(self, identity: CellIdentity): ...


def resolve_cell_endpoint(provisioner: ProvisionerLike, cell: DiagnosticCell) -> CellEndpoint:
    """Resolve the EXACT owned endpoint for an entered cell, or fail closed (task §9).

    The endpoint is taken ONLY from the provisioner's ownership-bound accessor and must
    agree, on every field, with the ``DiagnosticCell`` that ``run_sweep`` entered and
    validated — same run_id, cell_id, workspace, and owned storage dir. A bare/foreign/
    non-loopback endpoint is rejected (task §10/§26/§45)."""
    if not cell.validity.valid:
        raise CellNotReadyError(f"cell {cell.identity.cell_id} is not valid — no indexing")
    pc = provisioner.active_provisioned_cell(cell.identity)
    if pc is None or not getattr(pc, "ready", False):
        raise CellEndpointError(
            f"cell {cell.identity.cell_id} has no active provisioned endpoint"
        )
    if (
        pc.run_id != cell.identity.run_id
        or pc.cell_id != cell.identity.cell_id
        or pc.workspace != cell.identity.workspace
        or pc.workspace != cell.provision.workspace
        or pc.storage_dir != cell.provision.storage_dir
    ):
        raise CellEndpointError(
            f"cell {cell.identity.cell_id} endpoint ownership mismatch — fail closed"
        )
    host = urlparse(pc.base_url).hostname
    if host not in _LOOPBACK_HOSTS:
        raise CellEndpointError(
            f"cell {cell.identity.cell_id} endpoint host {host!r} is not a loopback "
            "diagnostic sidecar — fail closed"
        )
    if not isinstance(pc.port, int) or pc.port <= 0:
        raise CellEndpointError(f"cell {cell.identity.cell_id} endpoint port invalid")
    return CellEndpoint(
        run_id=pc.run_id, cell_id=pc.cell_id, base_url=pc.base_url, port=pc.port,
        workspace=pc.workspace, storage_dir=pc.storage_dir,
        container_identity=pc.process_identifier,
    )


# ---------------------------------------------------------------------------
# Injected LightRAG index client (per-cell). A fake drives every offline test.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class IndexSubmitResult:
    """One index-submission outcome. ``detail`` is an EPHEMERAL raw string used only to
    characterize a rejection; it is never stored on an AttemptRecord."""

    accepted: bool
    track_id: Optional[str]
    detail: Optional[str] = None


@dataclass(frozen=True)
class IndexStatusResult:
    """One poll result. ``detail`` (only meaningful on FAILED) is an EPHEMERAL raw error
    string, consumed immediately by ``characterize_failure`` and never retained."""

    state: str  # PROCESSED | FAILED | IN_PROGRESS | TIMEOUT
    detail: Optional[str] = None


class CellIndexClient(Protocol):
    """Submit one Source to a cell's sidecar and poll its status. Bound to ONE cell
    endpoint. Offline tests inject a fake; the real client wraps the pinned GraphRAG
    service pointed at the cell's base_url."""

    async def submit(self, *, source_id: str, canonical_text: str) -> IndexSubmitResult: ...

    async def status(self, *, track_id: str) -> IndexStatusResult: ...


class CellIndexClientFactory(Protocol):
    """Build a per-cell index client bound to the given owned endpoint (task §25/§26)."""

    def __call__(self, endpoint: CellEndpoint) -> CellIndexClient: ...


# ---------------------------------------------------------------------------
# Diagnostic Source (canonical id + frozen key + frozen text).
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DiagnosticSource:
    """One diagnostic Source. ``key`` is the frozen logical id (content-safe, reported);
    ``canonical_id`` is the isolated canonical Surreal source id submitted to LightRAG;
    ``text`` is the frozen Source content — NEVER logged or persisted."""

    key: str
    canonical_id: str
    text: str


@dataclass(frozen=True)
class LiveIndexerConfig:
    """Bounded, content-free indexer timings. NOT the frozen retry policy."""

    index_ready_timeout_s: float = 600.0
    poll_interval_s: float = 5.0


# ---------------------------------------------------------------------------
# The live per-cell indexer.
# ---------------------------------------------------------------------------


class LiveCellIndexer08:
    """Index a valid provisioned cell's frozen Source subset against THAT cell's sidecar.

    Responsibility is ONE thing (task §11/§21): index attempts + content-safe
    characterization for one cell. It does not provision, does not sweep, and does not
    interpret hypotheses. It is called by an ``index_cell_fn`` closure inside
    ``run_sweep`` (which owns the cell loop + budget + cleanup)."""

    def __init__(
        self,
        *,
        benchmark,
        sources: Dict[str, DiagnosticSource],
        client_factory: CellIndexClientFactory,
        config: Optional[LiveIndexerConfig] = None,
    ) -> None:
        self._benchmark = benchmark
        self._sources = sources
        self._client_factory = client_factory
        self._config = config or LiveIndexerConfig()
        #: content-free counter of real index SUBMISSIONS (incl. retries) — the caller
        #: asserts this never exceeds the frozen 64 hard cap (task §36/§83).
        self.submission_count = 0

    async def index_cell(
        self, level, cell: DiagnosticCell, repetition: int, *, provisioner: ProvisionerLike
    ) -> List[AttemptRecord]:
        """Index the cell's frozen Source subset CONCURRENTLY at the treatment level.

        ``level`` is a ``concurrency_diag08.DiagnosticLevel``. The Source subset is the
        committed deterministic selection (S001-anchored); the concurrency LEVEL is the
        only treatment. Runs ONLY inside an entered, valid, ownership-verified cell."""
        endpoint = resolve_cell_endpoint(provisioner, cell)
        keys = select_diagnostic_sources(self._benchmark, level.source_count)
        subset = [self._sources[k] for k in keys]  # KeyError = missing frozen source
        results = await asyncio.gather(
            *[
                self.index_source_attempt(
                    endpoint=endpoint,
                    concurrency_level=level.concurrency,
                    repetition=repetition,
                    source=src,
                )
                for src in subset
            ]
        )
        return list(results)

    async def index_source_attempt(
        self,
        *,
        endpoint: CellEndpoint,
        concurrency_level: int,
        repetition: int,
        source: DiagnosticSource,
    ) -> AttemptRecord:
        """One Source through the cell sidecar, with the frozen bounded (<=2) transient
        retry. Returns ONE content-free ``AttemptRecord`` (the terminal outcome). A
        CancelledError propagates (task §30) — it is never a provider-failure record."""
        client = self._client_factory(endpoint)
        attempts = 0
        terminal = TERMINAL_FAILED
        characterization = None
        started = time.monotonic()
        while attempts < MAX_INDEX_ATTEMPTS_PER_SOURCE:
            attempts += 1
            self.submission_count += 1
            # -- submit (a transient/exception here may retry within the cap) --
            try:
                submit = await client.submit(
                    source_id=source.canonical_id, canonical_text=source.text
                )
            except asyncio.CancelledError:
                raise  # propagate cancellation; not a provider-failure classification
            except Exception as exc:  # noqa: BLE001 - raw text consumed transiently only
                # The retry decision is the FROZEN 08E decision-twin
                # (``characterize_failure`` -> ``is_transient_reason``), applied uniformly
                # to the submit AND track surfaces — the single classifier this
                # concurrency diagnostic is built on (concurrency_diag08.retry_decision).
                # (The full-run runner08 ALSO uses a typed submit classifier; that richer
                # submit path is out of scope for this track-surface diagnostic, whose
                # failures of interest arise at the track surface.)
                characterization = characterize_failure(f"{type(exc).__name__}: {exc}")
                if attempts < MAX_INDEX_ATTEMPTS_PER_SOURCE and characterization.retryable:
                    continue
                terminal = TERMINAL_FAILED
                break
            if not submit.accepted or not submit.track_id:
                # A deterministic rejection is NOT retried (mirrors runner08).
                characterization = characterize_failure(submit.detail)
                terminal = TERMINAL_FAILED
                break
            # -- poll to terminal (bounded; no infinite loop, task §29) --
            status = await self._poll(client, submit.track_id)
            if status.state == _STATE_PROCESSED:
                terminal = TERMINAL_SUCCESS
                characterization = None
                break
            if status.state == _STATE_TIMEOUT:
                terminal = TERMINAL_TIMEOUT
                characterization = characterize_failure(None)  # bounded, content-safe
                break
            # FAILED: characterize (raw text transient) + FROZEN retry decision.
            characterization = characterize_failure(status.detail)
            if attempts < MAX_INDEX_ATTEMPTS_PER_SOURCE and characterization.retryable:
                continue
            terminal = TERMINAL_FAILED
            break
        duration_ms = int((time.monotonic() - started) * 1000)
        return AttemptRecord(
            run_id=endpoint.run_id,
            concurrency_level=concurrency_level,
            logical_source_id=source.key,
            repetition=repetition,
            attempt_number=attempts,
            terminal_status=terminal,
            duration_ms=duration_ms,
            characterization=characterization if terminal != TERMINAL_SUCCESS else None,
        )

    async def _poll(self, client: CellIndexClient, track_id: str) -> IndexStatusResult:
        deadline = time.monotonic() + max(1.0, self._config.index_ready_timeout_s)
        while True:
            status = await client.status(track_id=track_id)
            if status.state in (_STATE_PROCESSED, _STATE_FAILED):
                return status
            if time.monotonic() >= deadline:
                return IndexStatusResult(state=_STATE_TIMEOUT, detail=None)
            await asyncio.sleep(max(0.01, self._config.poll_interval_s))


# ---------------------------------------------------------------------------
# Real (live-only) per-cell index client. NEVER exercised offline. Bound to the cell's
# OWN base_url — never a global/default GraphRAG URL (task §25/§26).
# ---------------------------------------------------------------------------


class RealCellIndexClient:
    """Wraps the pinned GraphRAG service pointed at ONE cell's sidecar base_url."""

    def __init__(self, endpoint: CellEndpoint, *, api_key: Optional[str] = None) -> None:
        from open_notebook.integrations.graphrag.config import GraphRAGConfig
        from open_notebook.integrations.graphrag.service import GraphRAGService

        # Bound to THIS cell's own base_url — never a global/default GraphRAG URL.
        self._cfg = GraphRAGConfig(
            enabled=True, base_url=endpoint.base_url, timeout=30.0, api_key=api_key
        )
        self._svc = GraphRAGService(self._cfg)

    async def submit(self, *, source_id: str, canonical_text: str) -> IndexSubmitResult:
        ack = await self._svc.index_source(
            source_id=source_id, canonical_text=canonical_text
        )
        return IndexSubmitResult(
            accepted=bool(ack.accepted), track_id=ack.track_id or None, detail=ack.detail
        )

    async def status(self, *, track_id: str) -> IndexStatusResult:
        # ``IndexStatus`` carries no error text and ``track_status`` discards the FAILED
        # doc's raw error string, so on FAILED we read it with the FROZEN, content-
        # contained ``_fetch_failed_reason_ex`` (the same GET /documents/track_status
        # reader ``runner08`` uses via ``diagnose_failed_track``). Without this the
        # frozen classifier + 08E taxonomy would see only TRACK_TEXT_ABSENT and the
        # sweep would be diagnostically void (review H1). The raw text is returned ONLY
        # to be consumed immediately by ``characterize_failure`` — never stored/logged.
        from open_notebook.integrations.graphrag.eval.index_retry08 import (
            _fetch_failed_reason_ex,
        )
        from open_notebook.integrations.graphrag.models import IndexState

        st = await self._svc.track_status(track_id)
        if st.state == IndexState.PROCESSED:
            return IndexStatusResult(state=_STATE_PROCESSED)
        if st.state == IndexState.FAILED:
            presence, text = await _fetch_failed_reason_ex(self._cfg, track_id)
            return IndexStatusResult(
                state=_STATE_FAILED, detail=text if presence == "PRESENT" else None
            )
        return IndexStatusResult(state=_STATE_IN_PROGRESS)


def real_cell_index_client_factory(
    *, api_key: Optional[str] = None
) -> CellIndexClientFactory:
    """A live client factory bound per-cell to the exact owned endpoint (task §26)."""

    def _factory(endpoint: CellEndpoint) -> CellIndexClient:
        return RealCellIndexClient(endpoint, api_key=api_key)

    return _factory


__all__ = [
    "MAX_INDEX_ATTEMPTS_PER_SOURCE",
    "LiveIndexError",
    "CellEndpointError",
    "CellNotReadyError",
    "CellEndpoint",
    "ProvisionerLike",
    "resolve_cell_endpoint",
    "IndexSubmitResult",
    "IndexStatusResult",
    "CellIndexClient",
    "CellIndexClientFactory",
    "DiagnosticSource",
    "LiveIndexerConfig",
    "LiveCellIndexer08",
    "RealCellIndexClient",
    "real_cell_index_client_factory",
]
