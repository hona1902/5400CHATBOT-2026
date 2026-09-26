"""Thin LIVE B1 driver + dependency builder for PN02D-B0C-B.

EVALUATION-ONLY. Nothing in production imports this (``PRODUCTION_IMPORTS_EVAL =
NO``). This is the ``RealB1Driver`` wrapper of design §15/§39: it runs the two-boot
lifecycle, mints the real authorization chain, provisions the isolated corpus, boots
the provider-bound runtimes, builds the REAL backend factories, and then invokes the
**existing** ``driverpn02d.B1OfflineDriver`` — it duplicates NO scientific
orchestration (Stage-1 / R0-R4 / M0-M3 stay entirely inside the reused orchestrator +
``evaluatepn02``).

Frozen live flow (design §5/§7/§15):

  provider-free preflight (Boot 1)  → mint RealLightRAGPreflightAuthorization
  → mint LiveProviderRunAuthorization (operator grant + observed git/fixture)
  → materialize provider binding (live-gated, secret NAMES only)
  → boot provider-bound execution runtimes (Boot 2) → real route table
  → provision isolated corpus (source_embedding rows)
  → build real index/GD/vector/delete factories → reuse B1OfflineDriver.run
  → owned-only cleanup (finally).

Every external boundary is INJECTED via ``LiveB1Seams``, so B0C-B's full-real-wiring
mock simulation drives the whole path against a mock controller/prober/HTTP transports
+ a fake DB/embedder with ZERO provider traffic. No real provider run is authorized by
constructing or running this (``PN02_PROVIDER_RUN_AUTHORIZED`` stays NO).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Awaitable, Callable, Dict, FrozenSet, Mapping, Optional

import httpx

from open_notebook.integrations.graphrag.eval.attestpn02d import (
    mint_real_preflight_authorization,
)
from open_notebook.integrations.graphrag.eval.authmintlivepn02d import (
    EXPECTED_FIXTURE_HASH,
    GitBaselineAttestation,
    LiveProviderRunAuthorization,
    OperatorRunGrant,
    mint_live_provider_run_authorization,
    require_live_provider_run_authorization,
)
from open_notebook.integrations.graphrag.eval.budgetlivepn02d import b1_caps_dict
from open_notebook.integrations.graphrag.eval.corpuslivepn02d import (
    ProvisionedCorpus,
    RealPN02CorpusProvisioner,
    ReferenceLinker,
    SourceCreator,
    SourceEmbedder,
    derive_corpus_workload,
)
from open_notebook.integrations.graphrag.eval.datasetpn02 import (
    FixturePN02,
    load_fixture,
    membership_removal_scenario,
)
from open_notebook.integrations.graphrag.eval.deleteadapterpn02d import (
    build_real_delete_backend_factory,
)
from open_notebook.integrations.graphrag.eval.driverpn02d import (
    B1DriverDeps,
    B1OfflineDriver,
    B1RunOutcome,
    QAStageSeam,
)
from open_notebook.integrations.graphrag.eval.evidence_materialization_pn02d import (
    EvidenceMaterializerFactory,
)
from open_notebook.integrations.graphrag.eval.gdadapterpn02d import (
    build_real_gd_backend_factory,
)
from open_notebook.integrations.graphrag.eval.indexadapterpn02d import (
    build_real_index_client_factory,
)
from open_notebook.integrations.graphrag.eval.live_seam_pn02 import FinalAnswerSeam
from open_notebook.integrations.graphrag.eval.provbindpn02d import (
    MaterializedProviderBinding,
    materialize_provider_binding,
)
from open_notebook.integrations.graphrag.eval.provider_binding08 import (
    frozen_provider_binding,
)
from open_notebook.integrations.graphrag.eval.qastagepn02db2 import (
    bind_treatment_materializer,
)
from open_notebook.integrations.graphrag.eval.runtime_import_readiness_pn02d import (
    assert_live_runtime_import_readiness,
)
from open_notebook.integrations.graphrag.eval.runtimelivepn02d import (
    HealthProberLike,
    PortAllocator,
    PreflightRunnerLike,
    ProcessControllerLike,
    RealPN02RuntimeManager,
    StorageDirAllocator,
    VersionSignalReader,
)
from open_notebook.integrations.graphrag.eval.vectoradapterpn02d import (
    EmbeddingModelAttestor,
    MemberRowFetcher,
    QueryEmbedFn,
    build_real_vector_backend_factory,
)


class LiveDriverError(RuntimeError):
    """The live driver could not assemble/run the real path (fail-closed, content-free)."""


#: PN02D-B3G: the shared one-shot consumption enforcer type. Injected only in provider-free
#: tests (backed by a temporary ledger / spy); production leaves it ``None`` so the driver uses
#: the real durable ledger. Takes the operator grant + execution kind + optional ledger path.
OneShotEnforcer = Callable[..., None]


def _default_one_shot_enforcer(
    *, operator_grant: object, execution_kind: str, ledger_path: Optional[str] = None
) -> None:
    """Default consumer = the real durable SQLite one-shot ledger (lazy import so importing
    this driver has no ledger/config side effect). Raises ``GrantAlreadyConsumedError`` on a
    replay and fails closed on any ledger open/lock/corruption/schema failure."""
    from open_notebook.integrations.graphrag.eval.authledgerpn02d import (
        enforce_one_shot,
    )

    enforce_one_shot(
        operator_grant=operator_grant,
        execution_kind=execution_kind,
        ledger_path=ledger_path,
    )


def materialize_live_provider_binding(
    live_auth: object,
    *,
    binding=None,
    present_secret_envs: Optional[FrozenSet[str]] = None,
) -> MaterializedProviderBinding:
    """Materialize the frozen binding — REJECTS before the secret-NAME check (§11/§12).

    The LIVE capability is required FIRST (a boolean or simulation auth is rejected),
    then the frozen ``materialize_provider_binding`` runs against the underlying
    provider-run authorization. No secret VALUE is ever read here (names only).
    """
    live = require_live_provider_run_authorization(live_auth)
    return materialize_provider_binding(
        live.provider_run_authorization,
        binding=binding,
        present_secret_envs=present_secret_envs,
    )


@dataclass
class LiveB1Seams:
    """All injected boundaries for a live B1 run (real components; mocks in B0C-B)."""

    # runtime (Boot 1 + Boot 2)
    process_controller: ProcessControllerLike
    health_prober: HealthProberLike
    version_signal_reader: VersionSignalReader
    port_allocator: PortAllocator
    storage_dir_allocator: StorageDirAllocator
    # corpus provisioning
    source_creator: SourceCreator
    reference_linker: ReferenceLinker
    source_embedder: SourceEmbedder
    notebook_record_ids: Mapping[str, str]
    # vector Approach B
    query_embed_fn: QueryEmbedFn
    member_row_fetcher: MemberRowFetcher
    model_attestor: EmbeddingModelAttestor
    # B0CB-M1: provider-free preflight runner (fake in tests; None → real default).
    preflight_runner: Optional[PreflightRunnerLike] = None
    # HTTP transports (mock in tests; None → real httpx in a live run)
    index_transport: Optional[httpx.AsyncBaseTransport] = None
    gd_transport: Optional[httpx.AsyncBaseTransport] = None
    delete_transport: Optional[httpx.AsyncBaseTransport] = None
    # secrets / env (names only here; the value is a dummy in tests, resolved late live)
    lightrag_api_key: Optional[str] = None
    present_secret_envs: Optional[FrozenSet[str]] = None
    corpus_teardown: Optional[Callable[[], Awaitable[None]]] = None
    #: OPTIONAL ON-owned final-answer seam (PN02D-B2 Option A). None on a B1 run →
    #: no QA stage is wired and B1 stays byte-identical. A B2 run supplies it and the
    #: B2 QA stage (built by the B2 seams builder) consumes it.
    final_answer_seam: Optional[FinalAnswerSeam] = None


def build_live_b1_driver_deps(
    *,
    live_auth: LiveProviderRunAuthorization,
    corpus: ProvisionedCorpus,
    seams: LiveB1Seams,
    cleanup_fn: Optional[Callable[[], Awaitable[Mapping[str, object]]]] = None,
    qa_stage_seam: Optional[QAStageSeam] = None,
) -> B1DriverDeps:
    """Build the ``B1DriverDeps`` (real factories) for the reused orchestrator (§39).

    ``qa_stage_seam`` is the OPTIONAL PN02D-B2 Stage-2 QA seam (Option A, additive).
    None (a B1 run) → the reused orchestrator receives ``qa_results=None`` and is
    byte-identical to the EW8-approved B1 path.
    """
    require_live_provider_run_authorization(live_auth)
    index_factory = build_real_index_client_factory(
        live_auth=live_auth,
        api_key=seams.lightrag_api_key,
        transport=seams.index_transport,
        allowed_source_keys=frozenset(corpus.record_id_by_key.keys()),
    )
    gd_factory = build_real_gd_backend_factory(
        live_auth=live_auth,
        api_key=seams.lightrag_api_key,
        transport=seams.gd_transport,
    )
    delete_factory = build_real_delete_backend_factory(
        live_auth=live_auth,
        api_key=seams.lightrag_api_key,
        transport=seams.delete_transport,
    )
    vector_factory = build_real_vector_backend_factory(
        live_auth=live_auth,
        member_id_resolver=corpus.member_id_resolver(),
        query_embed_fn=seams.query_embed_fn,
        member_row_fetcher=seams.member_row_fetcher,
        model_attestor=seams.model_attestor,
    )
    return B1DriverDeps(
        index_client_factory=index_factory,
        gd_backend_factory=gd_factory,
        vector_backend_factory=vector_factory,
        delete_backend_factory=delete_factory,
        present_secret_envs=seams.present_secret_envs,
        cleanup_fn=cleanup_fn,
        qa_stage_seam=qa_stage_seam,
    )


class RealB1Driver:
    """Thin live wrapper that reuses ``B1OfflineDriver`` with REAL injected deps (§15)."""

    def __init__(
        self,
        fx: FixturePN02,
        seams: LiveB1Seams,
        *,
        qa_stage_seam: Optional[QAStageSeam] = None,
        mint_fn: Callable[..., LiveProviderRunAuthorization] = (
            mint_live_provider_run_authorization
        ),
        execution_kind: str = "B1",
        one_shot_enforcer: Optional[OneShotEnforcer] = None,
        oneshot_ledger_path: Optional[str] = None,
        evidence_materializer_factory: Optional[EvidenceMaterializerFactory] = None,
    ) -> None:
        self._fx = fx
        self._seams = seams
        # OPTIONAL PN02D-B2 Stage-2 QA seam (Option A). None → byte-identical B1.
        self._qa_stage_seam = qa_stage_seam
        # OPTIONAL PN02D-B3P-R1 (M1) generation-evidence-materialization TREATMENT factory.
        # Default None = CONTROL (byte-identical). It is a POST-PROVISION factory (not a
        # pre-built materializer) because the per-run ``record_id_by_key`` does not exist until
        # corpus provisioning; ``run`` constructs + injects the materializer AFTER provisioning
        # and BEFORE the QA stage runs (see below). Selection is NOT authorization.
        self._evidence_materializer_factory = evidence_materializer_factory
        # The LIVE mint entry. Defaults to the B1 mint (EW8 gate + B1 caps/allowlist) →
        # byte-identical B1. A B2 run injects ``mint_live_b2_provider_run_authorization``
        # (B2 checkpoint gate + b2 caps + b2 allowlist). Both resolve their trust roots
        # INTERNALLY; this is a mint SELECTOR, not a trust-root override.
        self._mint_fn = mint_fn
        # PN02D-B3G one-shot: the profile/execution kind bound into the consumption digest
        # ("B1"/"B2"/"B3B") and the enforcer that atomically consumes the grant at the shared
        # execution boundary. ``one_shot_enforcer`` defaults to the real durable ledger; tests
        # inject a temporary-ledger-backed / spy enforcer. Enforcement is shared across B1/B2/B3.
        self._execution_kind = execution_kind
        self._one_shot_enforcer = one_shot_enforcer
        self._oneshot_ledger_path = oneshot_ledger_path
        self._cleaned = False
        rt_kwargs: Dict[str, object] = dict(
            fx=fx,
            process_controller=seams.process_controller,
            health_prober=seams.health_prober,
            version_signal_reader=seams.version_signal_reader,
            port_allocator=seams.port_allocator,
            storage_dir_allocator=seams.storage_dir_allocator,
        )
        if seams.preflight_runner is not None:
            rt_kwargs["preflight_runner"] = seams.preflight_runner
        self._runtime = RealPN02RuntimeManager(**rt_kwargs)  # type: ignore[arg-type]

    async def run(
        self,
        *,
        operator_grant: OperatorRunGrant,
        git_baseline_attestation: GitBaselineAttestation,
        observed_fixture_hash: str = EXPECTED_FIXTURE_HASH,
    ) -> B1RunOutcome:
        """Execute the full two-boot live flow, reusing the frozen orchestrator (§15).

        ``git_baseline_attestation`` is OBSERVED repository state (B0CB-H2). The B1-R2
        checkpoint is verified INSIDE the mint by a mint-owned trusted reader
        (B0CB-RR3-H1/RR4-H1) — this driver accepts NO B1-R2 trust-root parameter (no
        reader, no approved identity) and forwards none to the mint. The mint resolves both
        trust roots internally from governance; the approved identity is the EW4 successor
        tag, and while that annotated tag is ABSENT from real Git the mint refuses before any
        binding/runtime boot. The mint also refuses a dirty tree or a baseline mismatch first.
        """
        try:
            # -- Boot 1: provider-free preflight → mint the real preflight capability.
            preflight = await self._runtime.boot_preflight(run_id=operator_grant.run_id)
            preflight_auth = mint_real_preflight_authorization(
                gate0_passed=preflight.gate0_passed,
                gate1_passed=preflight.gate1_passed,
                fixture_hash=observed_fixture_hash,
                run_id=operator_grant.run_id,
                runtime_count=preflight.runtime_count,
            )
            if preflight_auth is None:
                raise LiveDriverError("provider-free preflight did not pass both gates")

            # -- mint the LIVE capability (grant + full fixture chain H1 + clean baseline H2
            #    + mint-owned trusted B1-R2 read RR3-H1/RR4-H1). NO trust-root parameter is
            #    passed: the mint resolves the governance identity + real Git reader
            #    internally and fails closed while the EW4 successor tag is ABSENT from Git.
            live_auth = self._mint_fn(
                operator_grant=operator_grant,
                real_preflight_auth=preflight_auth,
                git_baseline_attestation=git_baseline_attestation,
                observed_fixture_hash=observed_fixture_hash,
            )

            # -- PN02D-B3Y preclaim runtime import-readiness guard. Provider-free; runs AFTER
            #    the mint validation PASS and BEFORE the irreversible one-shot claim below, so a
            #    broken launch import environment (e.g. repo root absent from sys.path → the
            #    first-party ``commands`` package unresolvable, the B3Y root cause) FAILS CLOSED
            #    here and NEVER consumes the grant or contacts a provider. It resolves an
            #    explicit, reviewable set of live-path imports (no recursive whole-app import);
            #    it performs no ledger mutation and no provider I/O. A failure is a LOCAL runtime
            #    import problem (LiveRuntimeImportReadinessError), never a provider failure.
            assert_live_runtime_import_readiness()

            # -- PN02D-B3G ONE-SHOT consumption (PN02DB3D-ER1-H1 closure). AFTER mint
            #    validation PASS, BEFORE the first provider-bound action (materialize binding /
            #    Boot 2). The mint stays side-effect-free; consumption lives ONLY here at the
            #    canonical execution boundary — so B1, B2, and B3 (which reuses this same driver
            #    via the B2 engine) each claim EXACTLY ONCE. A replay raises
            #    GrantAlreadyConsumedError and any ledger failure fails closed, BEFORE any
            #    provider binding. Boot-1/preflight failures above never reach this point, so a
            #    pre-consumption refusal never consumes the grant.
            enforcer = (
                self._one_shot_enforcer
                if self._one_shot_enforcer is not None
                else _default_one_shot_enforcer
            )
            enforcer(
                operator_grant=operator_grant,
                execution_kind=self._execution_kind,
                ledger_path=self._oneshot_ledger_path,
            )

            # -- materialize provider binding (live-gated; secret NAMES only).
            materialized = materialize_live_provider_binding(
                live_auth, present_secret_envs=self._seams.present_secret_envs
            )
            if not materialized.attested:
                raise LiveDriverError(
                    "provider binding not attested: "
                    + (";".join(materialized.failure_reasons) or "unknown")
                )

            # -- Boot 2: provider-bound execution runtimes → real route table.
            await self._runtime.boot_execution(
                live_auth=live_auth,
                materialized_binding=materialized,
                binding=frozen_provider_binding(),
            )
            route_table = self._runtime.route_table()

            # -- provision the isolated vector corpus (source_embedding rows).
            corpus_prov = RealPN02CorpusProvisioner(
                self._fx,
                live_auth=live_auth,
                source_creator=self._seams.source_creator,
                reference_linker=self._seams.reference_linker,
                source_embedder=self._seams.source_embedder,
                notebook_record_ids=self._seams.notebook_record_ids,
            )
            corpus = await corpus_prov.provision(run_id=live_auth.run_id)

            # -- PN02D-B3P-R1 (M1): construct + inject the generation-evidence-materialization
            # TREATMENT now that the real per-run corpus mapping exists (post-provision,
            # pre-QA). No-op CONTROL when no factory was supplied (default) — this is the ONLY
            # place record_id_by_key is available, so no future code edit is needed to run
            # treatment: an authorized turn just supplies the factory. It never alters selected
            # source ids; membership is still enforced inside the QA stage before materializing.
            bind_treatment_materializer(
                self._qa_stage_seam,
                self._evidence_materializer_factory,
                corpus.record_id_by_key,
            )

            # -- build real deps + reuse the frozen orchestrator (NO new science).
            deps = build_live_b1_driver_deps(
                live_auth=live_auth,
                corpus=corpus,
                seams=self._seams,
                cleanup_fn=self._cleanup,
                qa_stage_seam=self._qa_stage_seam,
            )
            driver = B1OfflineDriver(
                self._fx,
                deps,
                git_baseline_commit=operator_grant.approved_git_commit,
                git_baseline_tag=operator_grant.approved_git_tag,
                route_table=route_table,
            )
            return await driver.run(
                run_id=live_auth.run_id,
                provider_run_auth=live_auth.provider_run_authorization,
                present_secret_envs=self._seams.present_secret_envs,
            )
        finally:
            # B0CB-M3: the outer finally runs the FULL owned-only cleanup (runtime +
            # corpus) so corpus teardown happens on ANY failure path — including a
            # failure DURING corpus provisioning, before deps/cleanup_fn were wired.
            # Idempotent: a second invocation (from the orchestrator's cleanup_fn) is a
            # no-op.
            await self._cleanup()

    async def _cleanup(self) -> Dict[str, object]:
        if self._cleaned:
            return {"cleanup": "already_invoked"}
        self._cleaned = True
        report = dict(await self._runtime.cleanup())
        # Corpus teardown is attempted whenever a teardown seam exists (the isolated
        # namespace may have been opened at seam-build time, before run()), idempotently.
        if self._seams.corpus_teardown is not None:
            try:
                await self._seams.corpus_teardown()
                report["corpus_teardown"] = "invoked"
            except Exception as exc:  # noqa: BLE001 - cleanup must not raise
                report["corpus_teardown_error"] = type(exc).__name__
        return report


def plan_live_b1(
    fx: Optional[FixturePN02] = None, *, run_id: str = "DRYRUN"
) -> Dict[str, object]:
    """Content-safe provider-ZERO dry-run plan for the LIVE path (design §59).

    Extends the offline ``plan_b1`` with the two-boot lifecycle plan and the derived
    corpus-embedding workload. Shows fixture hash, 3 routes, index/GD/vector/delete
    plans, provider/model identity, concurrency 1/1/1, and the authorization
    requirements — with NO provider binding value and NO execution.
    """
    from open_notebook.integrations.graphrag.eval.driverpn02d import plan_b1

    fx = fx or load_fixture()
    plan = plan_b1(fx, run_id=run_id)
    scenario = membership_removal_scenario(fx)
    workload = derive_corpus_workload(fx)
    plan["plan_kind"] = "graphrag_pn02_b1_live_dry_run_plan"
    plan["two_boot_lifecycle"] = {
        "same_container": "NO",
        "boot_1_preflight": "provider-free (empty binding, no published port) → mint "
        "RealLightRAGPreflightAuthorization → teardown",
        "boot_2_execution": "provider-bound (frozen binding, published loopback port) → "
        "version/workspace/storage/health attestation → operations",
    }
    plan["corpus_embedding_workload"] = workload.as_dict()
    plan["concurrency"] = {"index": 1, "gd": 1, "vector": 1}
    plan["query_embedding_cap"] = b1_caps_dict().get("QUERY_EMBEDDING")
    plan["removal_reprobes"] = {
        "gd": list(scenario.reprobe_query_ids),
        "vector": list(scenario.reprobe_query_ids),
    }
    plan["live_authorization_requirements"] = [
        "RealLightRAGPreflightAuthorization (real Gate 0 ∧ Gate 1)",
        "OperatorRunGrant (run_id + approved checkpoint/baseline + frozen fingerprint)",
        "LiveProviderRunAuthorization (unforgeable; a simulation auth is rejected)",
        "attested provider binding (secret NAMES only)",
        "IndexingAuthorization (post binding attestation)",
        "QueryAuthorization (24/24 completeness)",
    ]
    plan["pn02_provider_run_authorized"] = False
    return plan


__all__ = [
    "LiveDriverError",
    "materialize_live_provider_binding",
    "LiveB1Seams",
    "build_live_b1_driver_deps",
    "RealB1Driver",
    "plan_live_b1",
]
