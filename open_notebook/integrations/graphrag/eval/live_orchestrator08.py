"""GraphRAG-08E.4 live diagnostic ORCHESTRATOR (EVALUATION-ONLY).

Nothing in production imports this. It assembles the already-approved pieces into ONE
runnable-but-fail-closed bounded concurrency diagnostic:

  preflight (frozen fixture + bounded plan + required attestor)
    -> live-authorization gate (deny by default)
      -> Option-A isolated Surreal runtime (08A)
        -> temp embedding Model (inside isolation only)
          -> frozen diagnostic Source preparation + canonical embedding (inside isolation)
            -> per-cell provisioner + injected DockerRuntimeAttestor (08E.2/08E.3)
              -> run_sweep(...) with a per-cell LiveCellIndexer08 (08E.4)
                -> content-free artifact
                  -> global cleanup + runtime restoration

Every provider/DB/Docker/sidecar dependency is INJECTED, so importing this module and
the offline test suite perform NO provider call, NO DB mutation, NO sidecar start
(task §18). A real run requires BOTH ``authorized_live=True`` AND a valid provisioned
cell (task §20) — and even then this phase never calls ``run(authorized_live=True)``
against real providers; the offline tests drive it with fakes.

The orchestrator does NOT reimplement retry classification, the failure taxonomy, the
plan/budget, or hypothesis interpretation — those stay in the frozen 08E layer.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import (
    Awaitable,
    Callable,
    Dict,
    Optional,
    Protocol,
    Tuple,
)

from loguru import logger

from open_notebook.integrations.graphrag.eval.concurrency_diag08 import (
    MAX_TOTAL_SUBMISSIONS,
    ConcurrencyDiagnosticPlan,
    DiagnosticBudgetExceededError,
    LiveDiagnosticNotAuthorizedError,
    SweepResult,
    default_plan,
    interpret_hypotheses,
    run_sweep,
    select_diagnostic_sources,
    validate_plan,
)
from open_notebook.integrations.graphrag.eval.live_indexer08 import (
    CellIndexClientFactory,
    DiagnosticSource,
    LiveCellIndexer08,
    LiveIndexerConfig,
    ProvisionerLike,
)

FROZEN_FIXTURE_HASH = (
    "a58a68535c345e18f0263904f818e4e2068a164056408665d8bb9233eceb143d"
)


class LiveOrchestratorError(RuntimeError):
    """Base class for orchestrator failures (all fail-closed)."""


class LiveDiagnosticPreflightError(LiveOrchestratorError):
    """A preflight invariant (fixture / plan) failed — STOP before any provider work."""


class LiveDiagnosticConfigError(LiveOrchestratorError):
    """A required dependency (e.g. the DockerRuntimeAttestor) is missing (task §23/§46)."""


# ---------------------------------------------------------------------------
# Injected dependencies. Real wiring is in ``default_live_deps``; tests inject fakes.
# ---------------------------------------------------------------------------


class IsolationRuntime(Protocol):
    """An async context manager yielding an Option-A isolated Surreal runtime whose
    context exposes at least ``run_id``/``namespace``/``database`` and whose exit drops
    the temporary namespace (removing temp Model + Sources)."""

    async def __aenter__(self): ...

    async def __aexit__(self, exc_type, exc, tb): ...


@dataclass
class OrchestratorDeps:
    """All provider/DB/Docker seams, injected for testability (task §12/§13/§15/§43)."""

    #: () -> async CM yielding the Option-A isolated runtime (08A).
    isolation_runtime_factory: Callable[[], IsolationRuntime]
    #: () -> (temp_model_id, prior_default) — creates the temp embedding Model INSIDE
    #: isolation (no provider call). Frozen: OpenRouter openai/text-embedding-3-small.
    model_seeder: Callable[[], Awaitable[Tuple[str, Optional[str]]]]
    #: (temp_model_id, prior_default) -> ok — best-effort restore (namespace drop also
    #: removes it).
    model_restorer: Callable[[str, Optional[str]], Awaitable[bool]]
    #: (benchmark, keys) -> {key: DiagnosticSource} — creates the canonical Sources +
    #: canonical embedding INSIDE isolation (the ONLY provider embedding call path).
    source_preparer: Callable[[object, Tuple[str, ...]], Awaitable[Dict[str, DiagnosticSource]]]
    #: (working_dir, runtime_attestor) -> a cell provisioner (real: LightRagCellProvisioner
    #: with DockerCellProcessController + LightRagCellHealthProber + the attestor).
    provisioner_factory: Callable[[str, object], ProvisionerLike]
    #: per-cell LightRAG index client factory (bound to each cell endpoint, 08E.4).
    client_factory: CellIndexClientFactory
    #: the REQUIRED owned-container runtime attestor (08E.3). None -> fail closed.
    runtime_attestor: Optional[object]
    #: optional content-free artifact sink (never receives raw content).
    artifact_writer: Optional[Callable[[dict], None]] = None
    indexer_config: LiveIndexerConfig = field(default_factory=LiveIndexerConfig)


@dataclass
class DiagnosticOutcome:
    """Content-free result of an orchestrated diagnostic run."""

    state: str  # COMPLETE | FAILED | BLOCKED
    run_id: str
    authorized_live: bool
    submission_count: int
    artifact: Dict[str, object] = field(default_factory=dict)
    sweep: Optional[SweepResult] = None
    failure_stage: str = ""
    failure_type: str = ""


# ---------------------------------------------------------------------------
# The orchestrator.
# ---------------------------------------------------------------------------


class LiveDiagnosticOrchestrator08:
    """Fail-closed orchestrator for the bounded live concurrency diagnostic."""

    def __init__(self, benchmark, deps: OrchestratorDeps) -> None:
        self._benchmark = benchmark
        self._deps = deps

    def _union_source_keys(self, plan: ConcurrencyDiagnosticPlan) -> Tuple[str, ...]:
        """The deterministic union of every level's frozen Source subset (S001-anchored).
        The widest level's selection is a superset of every narrower one (task §54)."""
        widest = max(lvl.source_count for lvl in plan.levels)
        return select_diagnostic_sources(self._benchmark, widest)

    async def run(
        self,
        *,
        working_dir: str,
        authorized_live: bool = False,
        run_id: Optional[str] = None,
    ) -> DiagnosticOutcome:
        """Execute (or fail-closed) the bounded diagnostic. DENY by default (task §19)."""
        # -- 1) preflight: frozen fixture + bounded plan (BEFORE any gate/provider) --
        from open_notebook.integrations.graphrag.eval import dataset08 as d

        ok, h = d.verify_integrity()
        if not ok or h != FROZEN_FIXTURE_HASH:
            raise LiveDiagnosticPreflightError("frozen fixture integrity mismatch")
        plan = default_plan()
        validate_plan(plan)
        # required real-sidecar attestor (task §23/§46) — before any provider path.
        if self._deps.runtime_attestor is None:
            raise LiveDiagnosticConfigError(
                "DockerRuntimeAttestor is required for live indexing (missing) — fail closed"
            )
        # -- 2) live-authorization gate: deny by default, BEFORE isolation/provider --
        if not authorized_live:
            raise LiveDiagnosticNotAuthorizedError(
                "live diagnostic requires explicit authorized_live=True (deny by default)"
            )

        rid = run_id or "gr08e4run"
        indexer: Optional[LiveCellIndexer08] = None
        # -- 3) Option-A isolation MUST wrap all provider/DB work (task §14) --
        async with self._deps.isolation_runtime_factory() as ctx:
            rid = run_id or getattr(ctx, "run_id", None) or rid
            model_id: Optional[str] = None
            prior_default: Optional[str] = None
            try:
                # -- 4) temp embedding Model (inside isolation only, task §15) --
                model_id, prior_default = await self._deps.model_seeder()
                # -- 5) frozen diagnostic Sources + canonical embedding (task §16/§17) --
                keys = self._union_source_keys(plan)
                sources = await self._deps.source_preparer(self._benchmark, keys)
                built = LiveCellIndexer08(
                    benchmark=self._benchmark, sources=dict(sources),
                    client_factory=self._deps.client_factory,
                    config=self._deps.indexer_config,
                )
                indexer = built
                # -- 6) per-cell provisioner + injected attestor (task §23) --
                provisioner = self._deps.provisioner_factory(
                    working_dir, self._deps.runtime_attestor
                )

                # -- 7) per-cell indexer closure; run ONLY inside a valid cell --
                async def index_cell_fn(lvl, cell, rep):
                    return await built.index_cell(
                        lvl, cell, rep, provisioner=provisioner
                    )

                # -- 8) bounded sweep (double gate: authorized_live + ready cell) --
                sweep = await run_sweep(
                    plan,
                    run_id=rid,
                    index_cell_fn=index_cell_fn,
                    cell_provisioner=provisioner,
                    working_dir=working_dir,
                    authorized_live=True,
                    require_isolation=True,
                )
                # -- budget: worst legal submissions (<= 64) never silently exceeded --
                if indexer.submission_count > MAX_TOTAL_SUBMISSIONS:
                    raise DiagnosticBudgetExceededError(
                        f"submissions {indexer.submission_count} > "
                        f"MAX_TOTAL_SUBMISSIONS {MAX_TOTAL_SUBMISSIONS}"
                    )
                artifact = self._build_artifact(rid, plan, sweep, indexer, complete=True)
                self._write_artifact(artifact)
                logger.info(
                    f"[gr08e4] diagnostic COMPLETE run={rid} "
                    f"submissions={indexer.submission_count}"
                )
                return DiagnosticOutcome(
                    state="COMPLETE", run_id=rid, authorized_live=True,
                    submission_count=indexer.submission_count, artifact=artifact,
                    sweep=sweep,
                )
            except Exception as exc:  # noqa: BLE001 - type only; never raw provider text
                # Partial/failed sweep: H1/H2/H3 stay INCONCLUSIVE (task §42). Content-free.
                count = indexer.submission_count if indexer is not None else 0
                artifact = self._build_failure_artifact(
                    rid, plan, count, failure_type=type(exc).__name__
                )
                self._write_artifact(artifact)
                logger.warning(
                    f"[gr08e4] diagnostic FAILED run={rid} stage=SWEEP "
                    f"type={type(exc).__name__}"
                )
                return DiagnosticOutcome(
                    state="FAILED", run_id=rid, authorized_live=True,
                    submission_count=count, artifact=artifact,
                    failure_stage="SWEEP", failure_type=type(exc).__name__,
                )
            finally:
                # Temp Model restore/removal (the namespace drop also removes it). Normal
                # DB is NEVER touched (task §39/§52).
                if model_id is not None:
                    try:
                        await self._deps.model_restorer(model_id, prior_default)
                    except Exception as exc:  # noqa: BLE001
                        logger.warning(
                            f"[gr08e4] temp model restore error: {type(exc).__name__}"
                        )
            # exiting the isolation CM drops the temp namespace (Sources + Model gone)

    # -- content-free artifact (task §40) ------------------------------------

    def _build_artifact(
        self, run_id, plan, sweep: SweepResult, indexer, *, complete: bool
    ) -> Dict[str, object]:
        return {
            "run_type": "GRAPHRAG_08E_LIVE_CONCURRENCY_DIAGNOSTIC",
            "run_id": run_id,
            "state": "COMPLETE" if complete else "FAILED",
            "diagnostic_levels": [1, 2, 4, 8],
            "repetitions_per_level": 2,
            "expected_total_submissions": plan.total_submissions,
            "max_total_submissions": MAX_TOTAL_SUBMISSIONS,
            "actual_submissions": indexer.submission_count,
            "dev_executed": 0,
            "holdout_executed": 0,
            "records": [r.as_dict() for r in sweep.records],
            "aggregate": sweep.aggregate,
            "interpretation": sweep.interpretation,
        }

    def _build_failure_artifact(
        self, run_id, plan, submission_count: int, *, failure_type: str
    ) -> Dict[str, object]:
        return {
            "run_type": "GRAPHRAG_08E_LIVE_CONCURRENCY_DIAGNOSTIC",
            "run_id": run_id,
            "state": "FAILED",
            "failure_stage": "SWEEP",
            "failure_type": failure_type,  # type name only — never raw text
            "expected_total_submissions": plan.total_submissions,
            "max_total_submissions": MAX_TOTAL_SUBMISSIONS,
            "actual_submissions": submission_count,
            "dev_executed": 0,
            "holdout_executed": 0,
            # An incomplete sweep yields no confirmed hypothesis (task §42).
            "interpretation": interpret_hypotheses({}),
        }

    def _write_artifact(self, artifact: Dict[str, object]) -> None:
        if self._deps.artifact_writer is not None:
            self._deps.artifact_writer(artifact)


# ---------------------------------------------------------------------------
# Real (live-only) dependency wiring. Importing/constructing this performs NO provider
# call, NO DB mutation, NO sidecar start — it only references the real callables.
# ---------------------------------------------------------------------------


def default_live_deps(
    *,
    eval_root: str,
    api_key: Optional[str] = None,
) -> OrchestratorDeps:
    """Wire the REAL Option-A / temp-Model / canonical-Source / provisioner / attestor /
    per-cell-client seams. NOT invoked in 08E.4 — provided so a future authorized run can
    execute without new glue (task §78). Constructing it starts nothing."""
    import os

    # Reconcile the index-client/health-prober key with the key the container ENFORCES:
    # DockerCellProcessController sets the sidecar's LIGHTRAG_API_KEY from
    # GRAPHRAG_POC_API_KEY, and POST /documents/text requires X-API-Key even though
    # /health is open. Defaulting the client key from the same env avoids a 401 on every
    # index submit against a key-protected sidecar (review M1).
    api_key = api_key or os.environ.get("GRAPHRAG_POC_API_KEY") or None

    from open_notebook.integrations.graphrag.eval import precheck08 as pc
    from open_notebook.integrations.graphrag.eval.cell_provisioner08 import (
        DockerCellProcessController,
        DockerRuntimeAttestor,
        LightRagCellHealthProber,
        LightRagCellProvisioner,
        ProvisionerConfig,
    )
    from open_notebook.integrations.graphrag.eval.isolation08 import (
        isolated_surreal_eval_runtime,
    )
    from open_notebook.integrations.graphrag.eval.live_indexer08 import (
        real_cell_index_client_factory,
    )

    async def _prepare_sources(benchmark, keys: Tuple[str, ...]) -> Dict[str, DiagnosticSource]:
        # Create canonical Sources inside the active Option-A isolation + canonical
        # embedding, then map each frozen key -> DiagnosticSource. Source text is frozen.
        from commands.embedding_commands import EmbedSourceInput, embed_source_command
        from open_notebook.database.repository import repo_query
        from open_notebook.integrations.graphrag.eval.isolation08 import (
            require_active_isolation,
        )
        from open_notebook.integrations.graphrag.models import record_id_for

        require_active_isolation()
        by_key = {s.key: s for s in benchmark.sources}
        tag = benchmark.namespace_tag
        out: Dict[str, DiagnosticSource] = {}
        for i, key in enumerate(keys):
            src = by_key[key]
            rid = record_id_for(f"source:gr08e4_{i:02d}", tables=frozenset({"source"}))
            canonical = str(rid)
            await repo_query(
                "CREATE $id SET full_text = $t, title = $title, topics = $topics",
                {"id": rid, "t": src.text, "title": src.title, "topics": [tag]},
            )
            emb = await embed_source_command(EmbedSourceInput(source_id=canonical))
            if not emb.success or emb.chunks_created <= 0:
                raise LiveOrchestratorError(f"canonical embedding failed for {key}")
            out[key] = DiagnosticSource(key=key, canonical_id=canonical, text=src.text)
        return out

    def _provisioner_factory(working_dir: str, attestor):
        cfg = ProvisionerConfig(eval_root=eval_root)
        return LightRagCellProvisioner(
            cfg,
            process_controller=DockerCellProcessController(),
            health_prober=LightRagCellHealthProber(api_key=api_key),
            runtime_attestor=attestor,
        )

    return OrchestratorDeps(
        isolation_runtime_factory=isolated_surreal_eval_runtime,
        model_seeder=pc.seed_temp_embedding_model,
        model_restorer=pc.restore_default_and_delete_model,
        source_preparer=_prepare_sources,
        provisioner_factory=_provisioner_factory,
        client_factory=real_cell_index_client_factory(api_key=api_key),
        runtime_attestor=DockerRuntimeAttestor(),
    )


__all__ = [
    "FROZEN_FIXTURE_HASH",
    "LiveOrchestratorError",
    "LiveDiagnosticPreflightError",
    "LiveDiagnosticConfigError",
    "IsolationRuntime",
    "OrchestratorDeps",
    "DiagnosticOutcome",
    "LiveDiagnosticOrchestrator08",
    "default_live_deps",
]
