"""GraphRAG-08E.7 burst-reproduction SCREENING runner (EVALUATION-ONLY).

Nothing in production imports this. It executes (or fail-closes) the frozen GraphRAG-08E.7A
burst screening ladder with **SUBMIT_ALL_THEN_POLL** semantics. Every provider/DB/Docker/
sidecar seam is INJECTED (a fake drives every offline test); importing or constructing this
performs NO provider call, NO sidecar start, NO DB mutation, NO network. The real live path
runs only under an explicit ``authorized_live=True`` with real dependencies.

No parallel GraphRAG live stack — it REUSES the approved stack:
  * Option-A isolation (``isolation08``) + temp model + Source prep via the SAME
    ``live_orchestrator08.OrchestratorDeps`` / ``default_live_deps`` wiring;
  * frozen provider binding (``provider_binding08``), ``DockerRuntimeAttestor`` + cell
    provisioner (``cell_provisioner08``), ownership-bound endpoint
    (``live_indexer08.resolve_cell_endpoint``), fresh per-rung sidecar
    (``cell_isolation08.diagnostic_cell08``);
  * content-free ``AttemptRecord`` + ``characterize_failure`` + the FROZEN retry decision
    (``concurrency_diag08`` -> ``index_retry08.is_transient_reason``).

The 08E.7A tightening (task §10/§11): submission and polling are TWO explicit phases — the
whole C-Source wave is submitted before ANY poll (never gather-per-source). An early failure
never cancels the entered burst (current-rung drain); the ladder stops at the rung boundary.
"""

from __future__ import annotations

import asyncio
import os
import time
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from loguru import logger

from open_notebook.integrations.graphrag.eval.burst_plan08 import (
    MAX_INDEX_ATTEMPTS_PER_SOURCE,
    TOP_RUNG,
    BurstBudgetError,
    BurstBudgetGuard,
    BurstExperimentResult,
    BurstPlan,
    BurstRungResult,
    budget_precheck,
    classify_rung,
    default_burst_plan,
    select_burst_prefix,
    validate_burst_plan,
)
from open_notebook.integrations.graphrag.eval.cell_isolation08 import (
    CellIdentity,
    CellIsolationError,
    CellOwnershipError,
    CellRegistry,
    DiagnosticCellIsolationFailure,
    diagnostic_cell08,
)
from open_notebook.integrations.graphrag.eval.concurrency_diag08 import (
    TERMINAL_FAILED,
    TERMINAL_SUCCESS,
    TERMINAL_TIMEOUT,
    AttemptRecord,
    FailureCharacterization,
    characterize_failure,
)
from open_notebook.integrations.graphrag.eval.live_indexer08 import (
    CellEndpoint,
    CellEndpointError,
    CellIndexClient,
    CellIndexClientFactory,
    CellNotReadyError,
    DiagnosticSource,
    IndexStatusResult,
    LiveIndexerConfig,
    resolve_cell_endpoint,
)

# Poll sentinels (match live_indexer08's private state strings; the injected client emits them).
_POLL_PROCESSED = "PROCESSED"
_POLL_FAILED = "FAILED"
_POLL_TIMEOUT = "TIMEOUT"


class BurstRunnerError(RuntimeError):
    """Base class for burst-runner failures (all fail-closed)."""


# ---------------------------------------------------------------------------
# Content-safe submission handle + per-Source rung state.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SubmissionHandle:
    """Content-safe handle for an accepted Source submission (task §17). Carries NO Source
    content, raw response, provider key, or raw error text."""

    logical_source_id: str
    canonical_source_id: str
    track_id: str
    cell_level: int
    attempt_number: int


@dataclass
class _SourceState:
    source: DiagnosticSource
    handle: Optional[SubmissionHandle]
    attempts: int
    terminal: Optional[str]
    characterization: Optional[FailureCharacterization]
    started_monotonic: float
    duration_ms: Optional[int]


# ---------------------------------------------------------------------------
# The SUBMIT_ALL_THEN_POLL wave indexer for ONE rung / ONE owned sidecar.
# ---------------------------------------------------------------------------


class BurstWaveIndexer08:
    """Index ONE rung's C-Source wave against ONE owned cell sidecar, SUBMIT_ALL_THEN_POLL.

    Phase A (``submit_wave``) submits every Source once, in historical order, and does NOT
    poll. Phase B (``drain_wave``) polls all established submissions to terminal and applies
    the frozen bounded retry. This structural separation is what distinguishes 08E.7 from the
    committed gather-per-source ``LiveCellIndexer08.index_cell`` (task §11)."""

    def __init__(
        self,
        *,
        endpoint: CellEndpoint,
        client_factory: CellIndexClientFactory,
        config: LiveIndexerConfig,
        budget: BurstBudgetGuard,
        level: int,
        run_id: str,
    ) -> None:
        self._client: CellIndexClient = client_factory(endpoint)
        self._config = config
        self._budget = budget
        self._level = level
        self._run_id = run_id
        #: content-free event trace (phase, source key) — proves submit-all precedes poll.
        self.events: List[Tuple[str, str]] = []

    def _key(self, source_key: str) -> str:
        # per-treatment (rung-scoped) key so the per-Source 2-attempt cap resets each rung.
        return f"c{self._level}_{source_key}"

    async def submit_wave(
        self, sources: List[DiagnosticSource]
    ) -> Tuple[List[_SourceState], bool]:
        """PHASE A — submit ALL Sources; NO poll. Returns (states, full_wave_established).

        A submit that RAISES or is rejected (``accepted=False`` / no track_id) means the
        Source never entered the indexing contract → the full initial wave is NOT established
        (task §14/§15 case B). A submit that is accepted enters the contract (case A); a later
        terminal FAILED during drain is a VALID Source failure, not a wave-establishment gap."""
        states: List[_SourceState] = []
        for src in sources:
            st = _SourceState(
                source=src,
                handle=None,
                attempts=0,
                terminal=None,
                characterization=None,
                started_monotonic=time.monotonic(),
                duration_ms=None,
            )
            # Budget: the initial submit is an index attempt (counts toward 406, per-source ≤2).
            self._budget.record_attempt(self._key(src.key))
            st.attempts = 1
            self.events.append(("submit", src.key))
            try:
                res = await self._client.submit(
                    source_id=src.canonical_id, canonical_text=src.text
                )
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - transport failure before contract entry
                states.append(st)
                return states, False
            if not res.accepted or not res.track_id:
                states.append(st)
                return states, False
            st.handle = SubmissionHandle(
                logical_source_id=src.key,
                canonical_source_id=src.canonical_id,
                track_id=res.track_id,
                cell_level=self._level,
                attempt_number=1,
            )
            states.append(st)
        established = len(states) == len(sources) and all(s.handle for s in states)
        return states, established

    async def drain_wave(self, states: List[_SourceState]) -> None:
        """PHASE B — poll ALL established Sources to bounded terminal outcomes. An early
        failure NEVER cancels the remaining already-submitted Sources (task §19)."""
        for st in states:
            if st.handle is None:
                continue
            await self._drain_source(st)

    async def _drain_source(self, st: _SourceState) -> None:
        while True:
            assert st.handle is not None
            self.events.append(("poll", st.source.key))
            status = await self._poll(st.handle.track_id)
            if status.state == _POLL_PROCESSED:
                st.terminal = TERMINAL_SUCCESS
                st.characterization = None
                break
            if status.state == _POLL_TIMEOUT:
                st.terminal = TERMINAL_TIMEOUT
                st.characterization = characterize_failure(None)  # bounded, content-safe
                break
            # FAILED: characterize (raw text transient only) + FROZEN retry decision.
            ch = characterize_failure(status.detail)
            st.characterization = ch
            if st.attempts < MAX_INDEX_ATTEMPTS_PER_SOURCE and ch.retryable:
                # A retry is a NEW index attempt — it happens ONLY here, AFTER the full wave
                # was established (task §22), so it never alters the initial-burst fidelity.
                self._budget.record_attempt(self._key(st.source.key))
                st.attempts += 1
                self.events.append(("submit_retry", st.source.key))
                try:
                    res = await self._client.submit(
                        source_id=st.source.canonical_id, canonical_text=st.source.text
                    )
                except asyncio.CancelledError:
                    raise
                except Exception:  # noqa: BLE001 - retry transport failure = terminal
                    st.terminal = TERMINAL_FAILED
                    break
                if not res.accepted or not res.track_id:
                    st.terminal = TERMINAL_FAILED
                    break
                st.handle = SubmissionHandle(
                    logical_source_id=st.source.key,
                    canonical_source_id=st.source.canonical_id,
                    track_id=res.track_id,
                    cell_level=self._level,
                    attempt_number=st.attempts,
                )
                continue
            st.terminal = TERMINAL_FAILED
            break
        st.duration_ms = int((time.monotonic() - st.started_monotonic) * 1000)

    async def _poll(self, track_id: str) -> IndexStatusResult:
        deadline = time.monotonic() + max(1.0, self._config.index_ready_timeout_s)
        while True:
            status = await self._client.status(track_id=track_id)
            if status.state in (_POLL_PROCESSED, _POLL_FAILED):
                return status
            if time.monotonic() >= deadline:
                return IndexStatusResult(state=_POLL_TIMEOUT, detail=None)
            await asyncio.sleep(max(0.01, self._config.poll_interval_s))

    def records(self, states: List[_SourceState]) -> Tuple[AttemptRecord, ...]:
        """Content-free terminal records — only for Sources that entered the contract."""
        out: List[AttemptRecord] = []
        for st in states:
            if st.handle is None:
                continue  # never established → not experimental evidence
            out.append(
                AttemptRecord(
                    run_id=self._run_id,
                    concurrency_level=self._level,
                    logical_source_id=st.source.key,
                    repetition=1,
                    attempt_number=st.attempts,
                    terminal_status=st.terminal or TERMINAL_FAILED,
                    duration_ms=st.duration_ms,
                    characterization=(
                        st.characterization if st.terminal != TERMINAL_SUCCESS else None
                    ),
                )
            )
        return tuple(out)


# ---------------------------------------------------------------------------
# The ladder runner: fresh sidecar per rung, submit-all-then-poll, drain, ladder stop.
# ---------------------------------------------------------------------------


class BurstLadderRunner08:
    """Run the frozen burst ladder over fresh, isolated per-rung sidecars. Fail-closed.

    Each rung enters a ``diagnostic_cell08`` (fresh LightRAG process + unique workspace +
    owned storage via the injected provisioner), submits the full C-Source wave, drains it,
    classifies the rung, and disposes the cell. A rung with any legitimate Source failure
    stops the ladder (no next rung). A provisioning/attestation/cleanup/budget/partial-wave
    failure is a RUNTIME stop, not experimental evidence (task §9/§14/§40)."""

    def __init__(
        self,
        *,
        benchmark,
        sources: Dict[str, DiagnosticSource],
        plan: Optional[BurstPlan] = None,
        client_factory: CellIndexClientFactory,
        config: Optional[LiveIndexerConfig] = None,
        budget: Optional[BurstBudgetGuard] = None,
    ) -> None:
        self._benchmark = benchmark
        self._sources = sources
        self._plan = plan or default_burst_plan()
        validate_burst_plan(self._plan)
        self._client_factory = client_factory
        self._config = config or LiveIndexerConfig()
        self._budget = budget or BurstBudgetGuard()

    async def run(
        self,
        *,
        run_id: str,
        provisioner,
        working_dir: str,
        registry: Optional[CellRegistry] = None,
    ) -> BurstExperimentResult:
        registry = registry or CellRegistry()
        rungs: List[BurstRungResult] = []
        entered: List[int] = []
        first_failure: Optional[int] = None
        clean_through: Optional[int] = None
        runtime_valid = True
        runtime_stop: Optional[str] = None
        cleanup_ok = True

        for lvl in self._plan.levels:
            level = lvl.source_count
            keys = select_burst_prefix(self._benchmark, level)
            try:
                subset = [self._sources[k] for k in keys]  # KeyError = missing frozen source
            except KeyError as exc:
                runtime_valid = False
                runtime_stop = f"MISSING_SOURCE:{type(exc).__name__}"
                break
            # Reserve the rung's planned Source workload (203 cap) BEFORE entering the cell.
            try:
                self._budget.reserve_workload(level)
            except BurstBudgetError as exc:
                runtime_valid = False
                runtime_stop = f"WORKLOAD_BUDGET:{type(exc).__name__}"
                break
            identity = CellIdentity(run_id=run_id, concurrency=level, repetition=1)
            try:
                async with diagnostic_cell08(
                    identity,
                    provisioner=provisioner,
                    registry=registry,
                    working_dir=working_dir,
                ) as cell:
                    endpoint = resolve_cell_endpoint(provisioner, cell)
                    rung = await self._run_rung(level, subset, endpoint, run_id)
            except asyncio.CancelledError:
                raise
            except (
                DiagnosticCellIsolationFailure,
                CellOwnershipError,
                CellIsolationError,
                CellEndpointError,
                CellNotReadyError,
                BurstBudgetError,
            ) as exc:
                runtime_valid = False
                runtime_stop = type(exc).__name__
                if isinstance(exc, (CellOwnershipError, CellIsolationError)):
                    cleanup_ok = False
                break
            except Exception as exc:  # noqa: BLE001 - keep every abort inside a content-safe
                # result (fail-closed). The cell CM has already run its owned cleanup; a truly
                # unexpected error (e.g. a broken injected dependency) becomes a runtime stop,
                # never experimental evidence and never a raw-text leak (review LOW-2).
                runtime_valid = False
                runtime_stop = f"UNEXPECTED:{type(exc).__name__}"
                break
            rungs.append(rung)
            entered.append(level)
            if not rung.treatment_valid:
                runtime_valid = False
                runtime_stop = rung.runtime_stop_reason
                break
            if rung.failure_count > 0:
                first_failure = level
                break  # ladder stop — do NOT enter the next rung (task §31)
        else:
            clean_through = self._plan.levels[-1].level

        valid_rungs = [r for r in rungs if r.treatment_valid]
        return BurstExperimentResult(
            run_id=run_id,
            levels_planned=tuple(lv.level for lv in self._plan.levels),
            levels_entered=tuple(entered),
            first_failure_rung=first_failure,
            clean_through_level=clean_through,
            planned_source_workload_count=self._budget.planned_source_workload_count,
            index_attempt_count=self._budget.actual_index_attempt_count,
            classifier_signature_reproduced=any(
                r.classifier_signature_reproduced for r in valid_rungs
            ),
            strict_s001_event_reproduced=any(
                r.strict_s001_event_reproduced for r in valid_rungs
            ),
            historical_attempt_number_match=any(
                r.historical_attempt_number_match for r in valid_rungs
            ),
            novel_failure_observed=any(r.novel_failure_observed for r in valid_rungs),
            runtime_valid=runtime_valid,
            runtime_stop_reason=runtime_stop,
            cleanup_ok=cleanup_ok,
            rungs=tuple(rungs),
        )

    async def _run_rung(
        self,
        level: int,
        subset: List[DiagnosticSource],
        endpoint: CellEndpoint,
        run_id: str,
    ) -> BurstRungResult:
        indexer = BurstWaveIndexer08(
            endpoint=endpoint,
            client_factory=self._client_factory,
            config=self._config,
            budget=self._budget,
            level=level,
            run_id=run_id,
        )
        states, established = await indexer.submit_wave(subset)
        submitted = sum(1 for s in states if s.handle is not None)
        if not established:
            # Partial wave — INVALID treatment; no reproduction classification (task §14).
            recs = indexer.records(states)
            return classify_rung(
                level,
                planned_source_count=level,
                initial_wave_submitted_count=submitted,
                full_initial_wave_established=False,
                records=recs,
                index_attempt_count=sum(s.attempts for s in states),
                retry_count=0,
                cleanup_ok=True,
                runtime_stop_reason="FULL_INITIAL_WAVE_NOT_ESTABLISHED",
            )
        await indexer.drain_wave(states)
        recs = indexer.records(states)
        retry_count = sum(max(0, s.attempts - 1) for s in states)
        return classify_rung(
            level,
            planned_source_count=level,
            initial_wave_submitted_count=submitted,
            full_initial_wave_established=True,
            records=recs,
            index_attempt_count=sum(s.attempts for s in states),
            retry_count=retry_count,
            cleanup_ok=True,
        )


# ---------------------------------------------------------------------------
# The orchestrator — reuses OrchestratorDeps / default_live_deps (no new deps code, task §83).
# ---------------------------------------------------------------------------


class BurstReproductionOrchestrator08:
    """Fail-closed orchestrator for the bounded live burst-reproduction screening sweep.

    Deny-by-default: requires ``authorized_live=True`` AND a runtime attestor AND a valid
    frozen provider binding AND the provider secret present, all BEFORE any provider path.
    Reuses the SAME ``OrchestratorDeps`` shape as the 08E.4/08E.5 live diagnostic — a future
    authorized run is ``BurstReproductionOrchestrator08(bench, default_live_deps(eval_root=…))
    .run(authorized_live=True)`` with NO new glue (task §83)."""

    def __init__(self, benchmark, deps) -> None:
        self._benchmark = benchmark
        self._deps = deps
        self._plan = default_burst_plan()

    async def run(
        self,
        *,
        working_dir: str,
        authorized_live: bool = False,
        run_id: Optional[str] = None,
    ) -> BurstExperimentResult:
        # Lazy imports keep construction/import side-effect-free.
        from open_notebook.integrations.graphrag.eval import dataset08 as d
        from open_notebook.integrations.graphrag.eval.live_orchestrator08 import (
            FROZEN_FIXTURE_HASH,
            LiveDiagnosticConfigError,
            LiveDiagnosticNotAuthorizedError,
            LiveDiagnosticPreflightError,
        )

        # -- 1) preflight: frozen fixture + bounded plan (BEFORE any gate/provider) --
        ok, h = d.verify_integrity()
        if not ok or h != FROZEN_FIXTURE_HASH:
            raise LiveDiagnosticPreflightError("frozen fixture integrity mismatch")
        validate_burst_plan(self._plan)
        budget_precheck()
        # required real-sidecar attestor + frozen provider binding (before any provider path).
        if self._deps.runtime_attestor is None:
            raise LiveDiagnosticConfigError(
                "DockerRuntimeAttestor is required for the burst runner (missing) — fail closed"
            )
        binding = self._deps.provider_binding
        if binding is None:
            raise LiveDiagnosticConfigError(
                "provider binding is required for the burst runner (missing) — fail closed"
            )
        binding.validate()
        # -- 2) live-authorization gate: deny by default, BEFORE isolation/provider --
        if not authorized_live:
            raise LiveDiagnosticNotAuthorizedError(
                "burst runner requires explicit authorized_live=True (deny by default)"
            )
        # Fail FAST + content-safe on a missing provider secret (name only, still pre-provider).
        for secret_env in binding.required_secret_envs():
            if not os.environ.get(secret_env, "").strip():
                raise LiveDiagnosticConfigError(
                    f"REQUIRED_RUNTIME_SECRET_MISSING={secret_env}"
                )

        rid = run_id or "gr08e7run"
        budget = BurstBudgetGuard()
        # -- 3) Option-A isolation MUST wrap all provider/DB work --
        async with self._deps.isolation_runtime_factory() as ctx:
            rid = run_id or getattr(ctx, "run_id", None) or rid
            model_id: Optional[str] = None
            prior_default: Optional[str] = None
            try:
                # -- 4) temp embedding Model (inside isolation only) --
                model_id, prior_default = await self._deps.model_seeder()
                # -- 5) frozen diagnostic Sources (the full 75-prefix union) + canonical embedding --
                keys = select_burst_prefix(self._benchmark, TOP_RUNG)
                sources = await self._deps.source_preparer(self._benchmark, keys)
                # -- 6) per-rung provisioner + injected attestor + provider binding --
                provisioner = self._deps.provisioner_factory(
                    working_dir, self._deps.runtime_attestor, binding
                )
                runner = BurstLadderRunner08(
                    benchmark=self._benchmark,
                    sources=dict(sources),
                    plan=self._plan,
                    client_factory=self._deps.client_factory,
                    config=self._deps.indexer_config,
                    budget=budget,
                )
                # -- 7) run the ladder (fresh sidecar per rung; submit-all-then-poll) --
                result = await runner.run(
                    run_id=rid, provisioner=provisioner, working_dir=working_dir
                )
                self._write_artifact(result)
                logger.info(
                    f"[gr08e7] burst screening COMPLETE run={rid} "
                    f"entered={list(result.levels_entered)} "
                    f"first_failure_rung={result.first_failure_rung} "
                    f"workload={result.planned_source_workload_count} "
                    f"attempts={result.index_attempt_count}"
                )
                return result
            finally:
                if model_id is not None:
                    try:
                        await self._deps.model_restorer(model_id, prior_default)
                    except Exception as exc:  # noqa: BLE001
                        logger.warning(
                            f"[gr08e7] temp model restore error: {type(exc).__name__}"
                        )
            # exiting the isolation CM drops the temp namespace (Sources + Model gone)

    def _write_artifact(self, result: BurstExperimentResult) -> None:
        if self._deps.artifact_writer is not None:
            self._deps.artifact_writer(result.as_dict())


def default_burst_deps(*, eval_root: str, api_key: Optional[str] = None):
    """Wire the REAL live seams for a future authorized burst run — the SAME wiring the
    08E.4/08E.5 live diagnostic uses (task §3/§83). Constructing it starts nothing."""
    from open_notebook.integrations.graphrag.eval.live_orchestrator08 import (
        default_live_deps,
    )

    return default_live_deps(eval_root=eval_root, api_key=api_key)


__all__ = [
    "BurstRunnerError",
    "SubmissionHandle",
    "BurstWaveIndexer08",
    "BurstLadderRunner08",
    "BurstReproductionOrchestrator08",
    "default_burst_deps",
]
