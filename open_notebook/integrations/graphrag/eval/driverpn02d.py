"""Offline B1 orchestrator for the PN02 live driver (PN02D-B0B).

EVALUATION-ONLY. Nothing in production imports this (``PRODUCTION_IMPORTS_EVAL =
NO``). Assembles the driver layers A-J (design §5/§40) into ONE runnable-but-
fail-closed orchestrator whose every provider/DB/Docker seam is INJECTED — so
importing this module and running the offline suite performs ZERO provider calls,
ZERO real LightRAG boots, ZERO real indexing/embeddings/``/query/data``, and ZERO
normal-DB mutations (design §39/§41). A future authorized B1 supplies real backends
for the SAME injected Protocols; B0B supplies fakes.

Frozen order (design §40):

  precheck -> provider-run authorization -> routing validation -> provider-binding
  materialization -> runtime attestation -> 24 index ops -> 24/24 completeness gate
  -> 24 GD baseline -> 24 vector baseline -> membership removal (delete + 2 GD + 2
  vector re-probes) -> normalize -> the frozen PN02B evaluator
  (``evaluatepn02.run_offline_evaluation``: Stage-1 -> R0-R4 / M0-M3) -> report ->
  cleanup.

The driver reimplements NO scientific logic (design §42): it emits normalized
``schemaspn02`` records and hands them to the frozen evaluator, which owns every
Stage-1 / R0-R4 / Q0-Q3 / M0-M3 verdict.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import (
    Awaitable,
    Callable,
    Dict,
    FrozenSet,
    List,
    Mapping,
    Optional,
    Tuple,
)

from loguru import logger

from open_notebook.integrations.graphrag.eval.attestpn02d import (
    mint_real_preflight_authorization,
)
from open_notebook.integrations.graphrag.eval.authlivepn02d import (
    PN02ProviderRunAuthorization,
    ProviderRunNotAuthorized,
    mint_indexing_authorization,
    mint_provider_run_authorization,
    mint_query_authorization,
    require_provider_run_authorization,
)
from open_notebook.integrations.graphrag.eval.budgetlivepn02d import (
    StatefulBudgetGuard,
    b1_caps_dict,
)
from open_notebook.integrations.graphrag.eval.datasetpn02 import (
    NOTEBOOK_IDS,
    FixturePN02,
    load_fixture,
    membership_removal_scenario,
    verify_fixture_hash,
)
from open_notebook.integrations.graphrag.eval.docidpn02d import (
    DerivedDocMappingStore,
    compute_derived_document_id,
)
from open_notebook.integrations.graphrag.eval.evaluatepn02 import (
    OfflineEvaluationResult,
    run_offline_evaluation,
)
from open_notebook.integrations.graphrag.eval.gdlivepn02d import (
    GDBackendFactory,
    GDQueryExecutor,
)
from open_notebook.integrations.graphrag.eval.indexlivepn02d import (
    IndexClientFactory,
    IndexCompletionReport,
    IndexOperationRecord,
    MembershipIndexExecutor,
)
from open_notebook.integrations.graphrag.eval.manifestpn02 import (
    DEFAULT_BOUNDARY_B,
    RunManifest,
    build_run_manifest,
    validate_boundary_b,
)
from open_notebook.integrations.graphrag.eval.outcomespn02d import (
    DriverTechnicalOutcome,
)
from open_notebook.integrations.graphrag.eval.provbindpn02d import (
    MaterializedProviderBinding,
    materialize_provider_binding,
)
from open_notebook.integrations.graphrag.eval.removallivepn02d import (
    DeleteBackendFactory,
    MembershipRemovalExecutor,
    MembershipRemovalOutcome,
)
from open_notebook.integrations.graphrag.eval.reportpn02 import build_report
from open_notebook.integrations.graphrag.eval.routelivepn02d import (
    PN02Router,
    attest_route,
    build_route_table,
)
from open_notebook.integrations.graphrag.eval.schemaspn02 import (
    GDEvidenceResult,
    VectorEvidenceResult,
)
from open_notebook.integrations.graphrag.eval.vectorlivepn02d import (
    NotebookLocalVectorExecutor,
    VectorBackendFactory,
)

#: The frozen PN02 fixture hash (design §2). A live drift is refused at precheck/mint.
EXPECTED_FIXTURE_HASH = (
    "9ce7df742810424d9ef7b7b34962187d9061245f81471f5fb1d7f2608f6899a6"
)

CleanupFn = Callable[[], Awaitable[Mapping[str, object]]]


@dataclass
class B1DriverDeps:
    """All provider/DB/Docker seams, injected for testability (design §39/§41)."""

    index_client_factory: IndexClientFactory
    gd_backend_factory: GDBackendFactory
    vector_backend_factory: VectorBackendFactory
    delete_backend_factory: DeleteBackendFactory
    #: injected simulated secret-presence set (NAMES only; a value is never read).
    present_secret_envs: Optional[FrozenSet[str]] = None
    #: optional content-free artifact sink.
    artifact_writer: Optional[Callable[[dict], None]] = None
    #: optional owned-resource cleanup (idempotent). None -> a trivial zero report.
    cleanup_fn: Optional[CleanupFn] = None


@dataclass
class B1RunOutcome:
    """Content-safe result of an orchestrated (offline) B1 run."""

    state: str  # COMPLETE | FAILED
    run_id: str
    technical_status: DriverTechnicalOutcome
    report: Dict[str, object]
    budget_snapshot: Dict[str, Dict[str, int]]
    cleanup: Dict[str, object]
    index_records: Tuple[IndexOperationRecord, ...] = ()
    index_completion: Optional[IndexCompletionReport] = None
    removal: Optional[MembershipRemovalOutcome] = None
    evaluation: Optional[OfflineEvaluationResult] = None
    failure_reason: str = ""


class B1OfflineDriver:
    """The offline B1 orchestrator (design §40)."""

    def __init__(
        self,
        fx: FixturePN02,
        deps: B1DriverDeps,
        *,
        git_baseline_commit: str = "OFFLINE",
        git_baseline_tag: str = "OFFLINE",
    ) -> None:
        self._fx = fx
        self._deps = deps
        self._git_commit = git_baseline_commit
        self._git_tag = git_baseline_tag

    async def run(
        self,
        *,
        run_id: str,
        provider_run_auth: Optional[PN02ProviderRunAuthorization],
        present_secret_envs: Optional[FrozenSet[str]] = None,
    ) -> B1RunOutcome:
        budget = StatefulBudgetGuard()
        cleanup_holder: Dict[str, object] = {
            "invoked": False,
            "owned_processes_remaining": 0,
            "runtime_storage_residue": 0,
            "networks_remaining": 0,
        }
        try:
            outcome = await self._run_body(
                run_id=run_id,
                provider_run_auth=provider_run_auth,
                present_secret_envs=present_secret_envs,
                budget=budget,
                cleanup_holder=cleanup_holder,
            )
        except Exception as exc:  # noqa: BLE001 - fail-closed; type name only, no raw text
            logger.warning(
                f"[pn02db0b] driver FAILED run={run_id} type={type(exc).__name__}"
            )
            outcome = B1RunOutcome(
                state="FAILED",
                run_id=run_id,
                technical_status=DriverTechnicalOutcome.FAILED_CLEANUP,
                report={"report_kind": "graphrag_pn02_b1_offline_report", "error": type(exc).__name__},
                budget_snapshot=budget.snapshot(),
                cleanup=cleanup_holder,
                failure_reason=type(exc).__name__,
            )
        finally:
            await self._cleanup_into(cleanup_holder)
        self._write_artifact(outcome.report)
        return outcome

    async def _run_body(
        self,
        *,
        run_id: str,
        provider_run_auth: Optional[PN02ProviderRunAuthorization],
        present_secret_envs: Optional[FrozenSet[str]],
        budget: StatefulBudgetGuard,
        cleanup_holder: Dict[str, object],
    ) -> B1RunOutcome:
        # -- 1) PRECHECK: fixture-hash hard gate (design §37). --------------------
        ok, detail = verify_fixture_hash()
        if not ok:
            return self._fail(
                run_id, DriverTechnicalOutcome.FAILED_PRECHECK, "fixture_hash_mismatch",
                budget, cleanup_holder,
            )
        fixture_hash = detail

        # -- 2) provider-run authorization (design §6). ---------------------------
        try:
            auth = require_provider_run_authorization(provider_run_auth)
        except ProviderRunNotAuthorized:
            return self._fail(
                run_id, DriverTechnicalOutcome.FAILED_PROVIDER_AUTHORIZATION,
                "provider_run_not_authorized", budget, cleanup_holder,
            )
        validate_boundary_b(DEFAULT_BOUNDARY_B)  # defensive re-check (design §36)

        # -- 3) routing validation (design §11). ----------------------------------
        router = PN02Router(build_route_table(self._fx))

        # -- 4) provider-binding materialization (auth precedes binding, §10). ----
        present = (
            present_secret_envs
            if present_secret_envs is not None
            else self._deps.present_secret_envs
        )
        materialized = materialize_provider_binding(
            auth, present_secret_envs=present
        )
        if not materialized.attested:
            return self._fail(
                run_id, DriverTechnicalOutcome.FAILED_PROVIDER_BINDING,
                ";".join(materialized.failure_reasons), budget, cleanup_holder,
                materialized=materialized,
            )

        # -- 5) runtime attestation per notebook (design §11). --------------------
        attestations = {
            nb: attest_route(router.route_for(nb)) for nb in router.notebook_ids()
        }
        if not all(a.attested for a in attestations.values()):
            return self._fail(
                run_id, DriverTechnicalOutcome.FAILED_RUNTIME_ATTESTATION,
                "runtime_attestation_failed", budget, cleanup_holder,
                materialized=materialized,
            )

        # -- 6) index the 24 memberships (design §12/§15). ------------------------
        indexing_auth = mint_indexing_authorization(
            auth, binding_attested=materialized.attested, run_id=run_id
        )
        mapping_store = DerivedDocMappingStore()
        index_executor = MembershipIndexExecutor(
            router=router,
            budget=budget,
            mapping_store=mapping_store,
            indexing_auth=indexing_auth,  # type: ignore[arg-type]
            provider_run_auth=auth,
            client_factory=self._deps.index_client_factory,
            memberships=self._fx.memberships,
        )
        index_records, index_completion = await index_executor.index_all(
            self._index_plan()
        )

        # -- 7) 24/24 completeness gate (design §15/§24). -------------------------
        query_auth = mint_query_authorization(
            indexing_auth,
            indexed_memberships=index_completion.succeeded,
            planned_memberships=index_completion.planned,
            run_id=run_id,
        )
        if query_auth is None:
            report = self._build_report(
                run_id, fixture_hash, materialized, index_completion, budget,
                removal=None, evaluation=None,
            )
            return B1RunOutcome(
                state="FAILED",
                run_id=run_id,
                technical_status=DriverTechnicalOutcome.FAILED_BEFORE_QUERY,
                report=report,
                budget_snapshot=budget.snapshot(),
                cleanup=cleanup_holder,
                index_records=index_records,
                index_completion=index_completion,
                failure_reason="index_completeness_below_24_of_24",
            )

        # -- 8) 24 GD baseline + 24 vector baseline (design §18/§21/§24). ---------
        gd_exec = GDQueryExecutor(
            router=router,
            budget=budget,
            provider_run_auth=auth,
            query_auth=query_auth,
            attestations=attestations,
            backend_factory=self._deps.gd_backend_factory,
            source_allowlist=self._fx.source_keys,
        )
        vec_exec = NotebookLocalVectorExecutor(
            router=router,
            budget=budget,
            provider_run_auth=auth,
            query_auth=query_auth,
            backend_factory=self._deps.vector_backend_factory,
            source_allowlist=self._fx.source_keys,
        )
        gd_results: Dict[str, GDEvidenceResult] = {}
        vector_results: Dict[str, VectorEvidenceResult] = {}
        for q in self._ordered_queries():
            gd_results[q.query_id] = await gd_exec.query(
                query_id=q.query_id, notebook_id=q.notebook_id, question=q.question
            )
            vector_results[q.query_id] = await vec_exec.query(
                query_id=q.query_id,
                notebook_id=q.notebook_id,
                question=q.question,
                member_source_ids=self._fx.members_of(q.notebook_id),
            )

        # -- 9) membership removal (delete + 2 GD + 2 vector re-probes, §28). -----
        scenario = membership_removal_scenario(self._fx)
        members_after: Dict[str, frozenset] = {
            scenario.removed_from_notebook: scenario.members_after_removed_nb,
            scenario.retained_notebook: scenario.members_after_retained_nb,
        }
        for nb in NOTEBOOK_IDS:
            members_after.setdefault(nb, self._fx.members_of(nb))
        removal_exec = MembershipRemovalExecutor(
            fx=self._fx,
            router=router,
            budget=budget,
            mapping_store=mapping_store,
            provider_run_auth=auth,
            query_auth=query_auth,
            gd_executor=gd_exec,
            vector_executor=vec_exec,
            delete_backend_factory=self._deps.delete_backend_factory,
        )
        removal_outcome = await removal_exec.run(scenario, members_after=members_after)

        # -- 10) normalize -> frozen PN02B evaluator (design §42). ----------------
        evaluation = run_offline_evaluation(
            self._fx,
            fixture_hash=fixture_hash,
            scenario=scenario,
            vector_results=vector_results,
            gd_results=gd_results,
            removal_after_removed_nb=removal_outcome.removed_after,
            removal_after_retained_nb=removal_outcome.retained_after,
            qa_results=None,  # B1 excludes QA (design §43).
        )

        report = self._build_report(
            run_id, fixture_hash, materialized, index_completion, budget,
            removal=removal_outcome, evaluation=evaluation,
        )
        return B1RunOutcome(
            state="COMPLETE",
            run_id=run_id,
            technical_status=DriverTechnicalOutcome.COMPLETED,
            report=report,
            budget_snapshot=budget.snapshot(),
            cleanup=cleanup_holder,
            index_records=index_records,
            index_completion=index_completion,
            removal=removal_outcome,
            evaluation=evaluation,
        )

    # -- helpers -------------------------------------------------------------- #

    def _index_plan(self) -> List[Tuple[str, str, str]]:
        """(canonical_source_id, notebook_id, text) in deterministic order (design §24)."""
        order = {nb: i for i, nb in enumerate(NOTEBOOK_IDS)}
        edges = sorted(
            self._fx.memberships, key=lambda e: (order.get(e[1], 99), e[0])
        )
        return [(src, nb, self._fx.source_text(src)) for (src, nb) in edges]

    def _ordered_queries(self):
        order = {nb: i for i, nb in enumerate(NOTEBOOK_IDS)}
        return sorted(
            self._fx.queries, key=lambda q: (order.get(q.notebook_id, 99), q.query_id)
        )

    def _run_manifest(self, run_id: str, fixture_hash: str) -> RunManifest:
        return build_run_manifest(
            self._fx,
            run_id=run_id,
            fixture_hash=fixture_hash,
            git_commit=self._git_commit,
            systems_enabled=("V", "GD"),
            budget_caps=b1_caps_dict(),
        )

    def _build_report(
        self,
        run_id: str,
        fixture_hash: str,
        materialized: MaterializedProviderBinding,
        index_completion: IndexCompletionReport,
        budget: StatefulBudgetGuard,
        *,
        removal: Optional[MembershipRemovalOutcome],
        evaluation: Optional[OfflineEvaluationResult],
    ) -> Dict[str, object]:
        manifest = self._run_manifest(run_id, fixture_hash)
        isolation = bool(evaluation and evaluation.isolation_evidenced)
        report = build_report(
            manifest=manifest,
            stage1=evaluation.stage1 if evaluation else None,
            retrieval=evaluation.retrieval if (evaluation and isolation) else None,
            multihop=evaluation.multihop if (evaluation and isolation) else None,
            removal=evaluation.removal if evaluation else None,
            scientific=evaluation.scientific if evaluation else None,
        )
        report["report_kind"] = "graphrag_pn02_b1_offline_report"
        report["driver"] = {
            "b0b_offline": True,
            "provider_traffic": 0,
            "index_completion": index_completion.as_dict(),
            "provider_binding": materialized.as_public_dict(),
            "workload_ledger_snapshot": budget.snapshot(),
            "membership_removal": removal.as_dict() if removal else None,
        }
        return report

    def _fail(
        self,
        run_id: str,
        status: DriverTechnicalOutcome,
        reason: str,
        budget: StatefulBudgetGuard,
        cleanup_holder: Dict[str, object],
        *,
        materialized: Optional[MaterializedProviderBinding] = None,
    ) -> B1RunOutcome:
        report: Dict[str, object] = {
            "report_kind": "graphrag_pn02_b1_offline_report",
            "driver": {
                "b0b_offline": True,
                "provider_traffic": 0,
                "technical_status": status.value,
                "failure_reason": reason,
                "provider_binding": materialized.as_public_dict() if materialized else None,
            },
        }
        return B1RunOutcome(
            state="FAILED",
            run_id=run_id,
            technical_status=status,
            report=report,
            budget_snapshot=budget.snapshot(),
            cleanup=cleanup_holder,
            failure_reason=reason,
        )

    async def _cleanup_into(self, holder: Dict[str, object]) -> None:
        if holder.get("invoked"):
            return
        holder["invoked"] = True
        if self._deps.cleanup_fn is None:
            return
        try:
            result = await self._deps.cleanup_fn()
            for k, v in dict(result).items():
                holder[k] = v
        except Exception as exc:  # noqa: BLE001 - cleanup must not raise past here
            holder["cleanup_error_type"] = type(exc).__name__

    def _write_artifact(self, report: Dict[str, object]) -> None:
        if self._deps.artifact_writer is not None:
            self._deps.artifact_writer(report)


# --------------------------------------------------------------------------- #
# Simulation authorization (controlled test/CLI factory, design §45)
# --------------------------------------------------------------------------- #

def build_simulation_provider_run_authorization(
    *,
    run_id: str,
    fixture_hash: Optional[str] = None,
    git_baseline_commit: str = "SIMULATION",
    git_baseline_tag: str = "SIMULATION",
) -> PN02ProviderRunAuthorization:
    """Mint a provider-run auth for the OFFLINE simulation (fakes only, design §45).

    The real-preflight capability is obtained from the frozen PN02D-A mint with
    SIMULATED gate booleans — a controlled factory. It authorizes NO real provider
    run: the driver's backends are fakes, so ``B0B_PROVIDER_TRAFFIC = 0``. Governance
    ``PN02_PROVIDER_RUN_AUTHORIZED`` stays NO.
    """
    if fixture_hash is None:
        ok, detail = verify_fixture_hash()
        fixture_hash = detail if ok else "UNVERIFIED"
    preflight = mint_real_preflight_authorization(
        gate0_passed=True,
        gate1_passed=True,
        fixture_hash=fixture_hash,
        run_id=run_id,
        runtime_count=3,
    )
    return mint_provider_run_authorization(
        real_preflight_auth=preflight,
        fixture_hash=fixture_hash,
        expected_fixture_hash=EXPECTED_FIXTURE_HASH,
        git_baseline_commit=git_baseline_commit,
        git_baseline_tag=git_baseline_tag,
        run_id=run_id,
        workload_caps=b1_caps_dict(),
    )


# --------------------------------------------------------------------------- #
# Dry-run plan (design §47/§50) — content-safe, provider-free, NO execution
# --------------------------------------------------------------------------- #

def plan_b1(
    fx: Optional[FixturePN02] = None,
    *,
    run_id: str = "DRYRUN",
) -> Dict[str, object]:
    """Serialize the full content-safe B1 plan without executing anything (§47/§50)."""
    from open_notebook.integrations.graphrag.eval.authlivepn02d import (
        B1_ALLOWED_OPERATION_CLASSES,
        frozen_provider_config_id,
    )
    from open_notebook.integrations.graphrag.eval.provider_binding08 import (
        frozen_provider_binding,
    )

    fx = fx or load_fixture()
    ok, detail = verify_fixture_hash()
    router = PN02Router(build_route_table(fx))
    binding = frozen_provider_binding()
    order = {nb: i for i, nb in enumerate(NOTEBOOK_IDS)}
    edges = sorted(fx.memberships, key=lambda e: (order.get(e[1], 99), e[0]))

    index_plan = []
    for src, nb in edges:
        route = router.route_for(nb)
        index_plan.append(
            {
                "canonical_source_id": src,
                "notebook_id": nb,
                "workspace_id": route.workspace_id,
                "endpoint": route.endpoint,
                "derived_document_id": compute_derived_document_id(src),
            }
        )
    shared_routing = {
        sk: sorted(fx.notebooks_of(sk)) for sk in fx.shared_source_keys()
    }
    scenario = membership_removal_scenario(fx)
    query_ids = [q.query_id for q in sorted(fx.queries, key=lambda q: (order.get(q.notebook_id, 99), q.query_id))]
    return {
        "plan_kind": "graphrag_pn02_b1_dry_run_plan",
        "run_id": run_id,
        "provider_traffic": 0,
        "fixture_name": fx.fixture_version,
        "fixture_hash": detail,
        "fixture_hash_ok": ok,
        "routes": {
            nb: router.route_for(nb).as_public_dict() for nb in router.notebook_ids()
        },
        "index_plan": index_plan,
        "shared_source_routing": shared_routing,
        "gd_plan": {"baseline": query_ids, "removal_reprobes": list(scenario.reprobe_query_ids)},
        "vector_plan": {"baseline": query_ids, "removal_reprobes": list(scenario.reprobe_query_ids)},
        "delete_plan": {
            "shared_source": scenario.shared_source,
            "at_notebook": scenario.removed_from_notebook,
            "workspace_id": router.route_for(scenario.removed_from_notebook).workspace_id,
            "derived_document_id": compute_derived_document_id(scenario.shared_source),
        },
        "workload_caps": b1_caps_dict(),
        "provider_models": {
            "llm_model": binding.llm_model,
            "embedding_model": binding.embedding_model,
            "embedding_dim": binding.embedding_dim,
            "llm_host": binding.llm_host,
            "provider_config_id": frozen_provider_config_id(),
            "required_secret_envs": list(binding.required_secret_envs()),
        },
        "authorization_requirements": [
            "RealLightRAGPreflightAuthorization",
            "PN02ProviderRunAuthorization",
            "provider_binding_materialization",
            "IndexingAuthorization",
            "QueryAuthorization(24/24)",
        ],
        "allowed_operation_classes": sorted(c.value for c in B1_ALLOWED_OPERATION_CLASSES),
    }


# --------------------------------------------------------------------------- #
# Offline B1 simulation (fakes only) — the §42 end-to-end entrypoint
# --------------------------------------------------------------------------- #

async def run_offline_b1_simulation(
    fx: Optional[FixturePN02] = None,
    *,
    run_id: str = "pn02b0b-sim",
    delete_succeed: bool = True,
    artifact_writer: Optional[Callable[[dict], None]] = None,
) -> B1RunOutcome:
    """Run the ENTIRE B1 orchestrator offline against clean fakes (design §42).

    Zero provider/network/DB traffic; the output reaches the frozen PN02B evaluator
    (Stage-1 / R0-R4 / M0-M3). This is a SIMULATION — governance stays
    ``PN02_PROVIDER_RUN_AUTHORIZED = NO``.
    """
    from open_notebook.integrations.graphrag.eval.fakeslivepn02d import (
        build_clean_fake_topology,
    )

    fx = fx or load_fixture()
    router = PN02Router(build_route_table(fx))
    topology = build_clean_fake_topology(fx, router, delete_succeed=delete_succeed)
    deps = B1DriverDeps(
        index_client_factory=topology.index_client_factory(),
        gd_backend_factory=topology.gd_backend_factory(),
        vector_backend_factory=topology.vector_backend_factory(),
        delete_backend_factory=topology.delete_backend_factory(),
        present_secret_envs=frozenset({"OPENROUTER_API_KEY"}),  # simulated presence
        artifact_writer=artifact_writer,
    )
    driver = B1OfflineDriver(fx, deps)
    auth = build_simulation_provider_run_authorization(run_id=run_id)
    return await driver.run(run_id=run_id, provider_run_auth=auth)


__all__ = [
    "EXPECTED_FIXTURE_HASH",
    "CleanupFn",
    "B1DriverDeps",
    "B1RunOutcome",
    "B1OfflineDriver",
    "build_simulation_provider_run_authorization",
    "plan_b1",
    "run_offline_b1_simulation",
]
