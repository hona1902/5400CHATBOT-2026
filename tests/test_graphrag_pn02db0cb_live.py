"""PN02D-B0C-B — full real-wiring mock simulation, two-boot lifecycle, CLI, counters.

EVALUATION-ONLY. Exercises the REAL adapter/driver classes with mocked external
boundaries only: ``EXTERNAL_PROVIDER_NETWORK_CALLS = 0``, ``REAL_PROVIDER_CALLS = 0``,
``PROVIDER_BOUND_LIGHTRAG_BOOT_COUNT = 0``, ``NORMAL_DB_MUTATIONS = 0``.
"""

from __future__ import annotations

import json
from unittest import mock

import graphrag_pn02db0cb_common as C
import pytest

from open_notebook.integrations.graphrag.eval import authmintlivepn02d as authmint
from open_notebook.integrations.graphrag.eval import cli_live_pn02d as cli
from open_notebook.integrations.graphrag.eval.authlivepn02d import (
    frozen_provider_config_id,
)
from open_notebook.integrations.graphrag.eval.authmintlivepn02d import (
    EXPECTED_PROVIDER_CONFIG_ID,
    LiveProviderRunNotAuthorized,
    RealTrustedB1R2Reader,
    attest_approved_clean_baseline,
    current_approved_b1_r2_checkpoint,
    verify_b1_r2_checkpoint,
)
from open_notebook.integrations.graphrag.eval.cli_live_pn02d import read_git_baseline
from open_notebook.integrations.graphrag.eval.corpuslivepn02d import (
    derive_corpus_workload,
)
from open_notebook.integrations.graphrag.eval.driver_live_pn02d import (
    RealB1Driver,
    materialize_live_provider_binding,
    plan_live_b1,
)
from open_notebook.integrations.graphrag.eval.runtimelivepn02d import (
    PreflightRunError,
    RealPN02RuntimeManager,
    RealProviderFreePreflightRunner,
    TwoBootOrderError,
)

# --------------------------------------------------------------------------- #
# §52 — full real-wiring mock simulation
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_full_real_wiring_mock_simulation_completes():
    fx = C.fixture()
    bundle = C.build_live_seams(fx)
    driver = RealB1Driver(fx, bundle.seams)
    grant = C.frozen_test_grant()

    # B0CB-RR4-H1: the production driver/mint expose NO trust-root parameter; a future
    # B1-R2 approval is simulated by patching the mint's INTERNAL governance + reader.
    with C.approved_b1r2_governance():
        outcome = await driver.run(
            operator_grant=grant,
            git_baseline_attestation=C.clean_git_baseline(),
        )

    # The reused scientific orchestrator reached a COMPLETE run + the frozen evaluator.
    assert outcome.state == "COMPLETE"
    assert outcome.evaluation is not None
    assert outcome.index_completion is not None
    assert outcome.index_completion.complete is True
    assert outcome.index_completion.succeeded == 24

    # Wire-call accounting: 24 index submits, 26 GD /query/data (24 + 2 reprobes), 1 delete.
    assert bundle.http_calls.get("index_submit") == 24
    assert bundle.http_calls.get("query_data") == 26
    assert bundle.http_calls.get("delete") == 1

    # Corpus: 21 canonical Sources embedded ONCE each + 24 reference edges (fake DB).
    assert bundle.corpus_db.embed_calls == 21
    assert len(bundle.corpus_db.created) == 21
    assert len(bundle.corpus_db.edges) == 24
    assert bundle.corpus_db.torn_down is True

    # Two-boot: 3 provider-free preflight runtimes (via the preflight runner, no port,
    # no secret) + 3 provider-bound execution containers (via the controller), cleaned.
    assert len(bundle.preflight_runner.launched) == 3
    assert len(bundle.preflight_runner.terminated) == 3
    assert len(bundle.controller.started) == 3
    assert len(bundle.controller.terminated) == 3


@pytest.mark.asyncio
async def test_full_sim_scientific_isolation_evidenced():
    # The notebook-scoped mock GD + member-scoped vector produce leak-free evidence, so
    # the frozen evaluator evidences per-notebook isolation.
    fx = C.fixture()
    bundle = C.build_live_seams(fx)
    with C.approved_b1r2_governance():
        outcome = await RealB1Driver(fx, bundle.seams).run(
            operator_grant=C.frozen_test_grant(),
            git_baseline_attestation=C.clean_git_baseline(),
        )
    assert outcome.evaluation is not None
    assert outcome.evaluation.isolation_evidenced is True


# --------------------------------------------------------------------------- #
# §51 — 24/24 completeness gate feeds the real wiring
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_23_of_24_fails_before_query_no_gd_or_vector_calls():
    fx = C.fixture()
    # Make exactly one membership's index FAIL at the track surface.
    a_member = sorted(fx.members_of("NB_A"))[0]
    bundle = C.build_live_seams(fx)
    # Rebuild the transport with one failing source, reusing the same call counter/map.
    failing_transport = C.build_mock_transport(
        fx, bundle.base_url_to_notebook,
        failing_source_keys={a_member}, calls=bundle.http_calls,
    )
    bundle.seams.index_transport = failing_transport
    bundle.seams.gd_transport = failing_transport
    bundle.seams.delete_transport = failing_transport

    with C.approved_b1r2_governance():
        outcome = await RealB1Driver(fx, bundle.seams).run(
            operator_grant=C.frozen_test_grant(),
            git_baseline_attestation=C.clean_git_baseline(),
        )
    assert outcome.state == "FAILED"
    assert outcome.technical_status.value == "FAILED_BEFORE_QUERY"
    assert outcome.index_completion.succeeded == 23
    # No GD or vector query ran (query authorization was never minted).
    assert bundle.http_calls.get("query_data", 0) == 0


# --------------------------------------------------------------------------- #
# §47 — two-boot lifecycle (mock)
# --------------------------------------------------------------------------- #

def _runtime_manager(fx, bundle):
    return RealPN02RuntimeManager(
        fx=fx,
        process_controller=bundle.controller,
        health_prober=bundle.prober,
        version_signal_reader=C.fake_version_signal_reader,
        port_allocator=C.DeterministicPortAllocator(C.PORT_BASE),
        storage_dir_allocator=C.fake_storage_dir_allocator,
        preflight_runner=bundle.preflight_runner,
    )


@pytest.mark.asyncio
async def test_two_boot_lifecycle_preflight_then_execution():
    from open_notebook.integrations.graphrag.eval.realsidecarpn02d import (
        assert_no_provider_binding_in_command,
    )

    fx = C.fixture()
    bundle = C.build_live_seams(fx)
    mgr = _runtime_manager(fx, bundle)

    # Boot 1: provider-FREE preflight → created, attested, torn down.
    preflight = await mgr.boot_preflight(run_id="two-boot-test")
    assert preflight.passed is True
    assert preflight.runtime_count == 3
    assert preflight.torn_down is True
    # B0CB-M1: preflight ran via the provider-free runner (NOT the exec controller),
    # each boot command is provider-free (no provider binding, no published port).
    assert len(bundle.preflight_runner.launched) == 3
    assert len(bundle.controller.started) == 0  # execution controller not used yet
    for argv in bundle.preflight_runner.launched:
        assert_no_provider_binding_in_command(argv)  # raises if any provider binding
        assert "-p" not in argv  # no published port (B0CB-M1)
        assert not any("OPENROUTER_API_KEY" in tok and tok.strip().endswith("=") is False
                       and "=" in tok and tok.split("=", 1)[1] for tok in argv)  # no secret value
    assert len(bundle.preflight_runner.terminated) == 3  # preflight cleaned

    # Route table unavailable until execution attestation.
    with pytest.raises(Exception):
        mgr.route_table()

    # Boot 2 requires a live capability.
    live_auth = C.mint_test_live_auth()
    from open_notebook.integrations.graphrag.eval.driver_live_pn02d import (
        materialize_live_provider_binding,
    )

    materialized = materialize_live_provider_binding(
        live_auth, present_secret_envs=frozenset({"OPENROUTER_API_KEY"})
    )
    attestations = await mgr.boot_execution(
        live_auth=live_auth, materialized_binding=materialized
    )
    assert len(attestations) == 3
    assert all(a.attested for a in attestations.values())

    exec_specs = list(bundle.controller.started)
    assert len(exec_specs) == 3
    assert all(s.provider_binding is not None for s in exec_specs)  # provider-bound

    # SAME_CONTAINER = NO: the provider-bound execution runtimes are booted through a
    # different mechanism (the controller) than the provider-free preflight runtimes
    # (the preflight runner), and their container identities never overlap.
    preflight_ids = set(bundle.preflight_runner.terminated)
    exec_cell_ids = {s.cell_id for s in exec_specs}
    assert preflight_ids.isdisjoint(exec_cell_ids)

    # Route table now available with real loopback base_urls.
    routes = mgr.route_table()
    assert all(r.endpoint.startswith("http://127.0.0.1:") for r in routes.values())


@pytest.mark.asyncio
async def test_boot_execution_requires_preflight_first():
    fx = C.fixture()
    bundle = C.build_live_seams(fx)
    mgr = _runtime_manager(fx, bundle)
    live_auth = C.mint_test_live_auth()
    from open_notebook.integrations.graphrag.eval.driver_live_pn02d import (
        materialize_live_provider_binding,
    )

    materialized = materialize_live_provider_binding(
        live_auth, present_secret_envs=frozenset({"OPENROUTER_API_KEY"})
    )
    with pytest.raises(TwoBootOrderError):
        await mgr.boot_execution(live_auth=live_auth, materialized_binding=materialized)


@pytest.mark.asyncio
async def test_boot_execution_requires_live_auth():
    fx = C.fixture()
    bundle = C.build_live_seams(fx)
    mgr = _runtime_manager(fx, bundle)
    await mgr.boot_preflight()

    class _Materialized:
        attested = True

    with pytest.raises(LiveProviderRunNotAuthorized):
        await mgr.boot_execution(
            live_auth=None, materialized_binding=_Materialized()  # type: ignore[arg-type]
        )


@pytest.mark.asyncio
async def test_failed_preflight_cannot_boot_execution():
    # B0CB-M2: a preflight that did NOT pass attestation cannot boot execution, even
    # with a valid live capability + attested binding.
    from open_notebook.integrations.graphrag.eval.driver_live_pn02d import (
        materialize_live_provider_binding,
    )

    fx = C.fixture()
    bundle = C.build_live_seams(fx)
    bundle.preflight_runner.version_attested = False  # preflight fails attestation
    mgr = _runtime_manager(fx, bundle)

    preflight = await mgr.boot_preflight()
    assert preflight.passed is False

    live_auth = C.mint_test_live_auth()
    materialized = materialize_live_provider_binding(
        live_auth, present_secret_envs=frozenset({"OPENROUTER_API_KEY"})
    )
    with pytest.raises(TwoBootOrderError):
        await mgr.boot_execution(live_auth=live_auth, materialized_binding=materialized)


@pytest.mark.asyncio
async def test_partial_corpus_failure_triggers_owned_cleanup():
    # B0CB-M3: a failure DURING corpus provisioning still runs the full owned-only
    # cleanup (runtime torn down + corpus teardown invoked), leaving no run-owned residue.
    fx = C.fixture()
    bundle = C.build_live_seams(fx)
    real_embed = bundle.seams.source_embedder
    state = {"n": 0}

    async def failing_embed(source_record_id: str) -> int:
        state["n"] += 1
        if state["n"] >= 3:
            raise RuntimeError("corpus embed failed mid-provisioning")
        return await real_embed(source_record_id)

    bundle.seams.source_embedder = failing_embed

    with C.approved_b1r2_governance(), pytest.raises(RuntimeError):
        await RealB1Driver(fx, bundle.seams).run(
            operator_grant=C.frozen_test_grant(),
            git_baseline_attestation=C.clean_git_baseline(),
        )
    # Corpus teardown ran despite the mid-provisioning failure, and the provider-bound
    # execution runtimes were torn down — no run-owned residue.
    assert bundle.corpus_db.torn_down is True
    assert len(bundle.controller.terminated) == 3


# --------------------------------------------------------------------------- #
# §59/§60 — live CLI (execute-b1-live authorization gate + dry-run)
# --------------------------------------------------------------------------- #

def _write_manifest(tmp_path, grant) -> str:
    manifest = {
        "run_id": grant.run_id,
        "fixture_hash": grant.fixture_hash,
        "implementation_checkpoint_commit": grant.implementation_checkpoint_commit,
        "implementation_checkpoint_tag": grant.implementation_checkpoint_tag,
        "b1_r2_checkpoint": grant.b1_r2_checkpoint,
        "provider_config_fingerprint": grant.provider_config_fingerprint,
        "workload_caps": dict(grant.workload_caps),
        "operation_allowlist": sorted(grant.operation_allowlist),
        "approved_git_commit": grant.approved_git_commit,
        "approved_git_tag": grant.approved_git_tag,
        "synthetic_only": grant.synthetic_only,
        "real_internal_data_allowed": grant.real_internal_data_allowed,
    }
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    return str(path)


def _valid_readers():
    return (
        lambda: C.clean_git_baseline(),
        C.verify_fixture_hash,
    )


def test_cli_execute_b1_live_without_manifest_refused():
    code, payload = cli.evaluate_execute_b1_live(
        manifest_path=None, explicit_authorize=False, env={}
    )
    assert code == 2
    assert payload["result"] == "REFUSED"
    assert cli.REASON_MANIFEST_MISSING in payload["reasons"]
    assert payload["runtime_booted"] is False
    assert payload["provider_bound"] is False


# NOTE (B0CB-RR4-H1): the CLI exposes NO trust-root parameter. Tests that need the B1-R2
# gate to PASS wrap the call in ``C.approved_b1r2_governance()`` (patches the mint's
# INTERNAL governance identity + reader); they never pass a trust root through the CLI.

def test_cli_execute_b1_live_valid_grant_but_not_authorized(tmp_path):
    grant = C.frozen_test_grant()
    path = _write_manifest(tmp_path, grant)
    git_reader, fx_reader = _valid_readers()
    with C.approved_b1r2_governance():
        code, payload = cli.evaluate_execute_b1_live(
            manifest_path=path, explicit_authorize=False, env={},
            git_baseline_reader=git_reader, fixture_hash_reader=fx_reader,
        )
    # Inputs valid; refused at the governance gate (PN02_PROVIDER_RUN_AUTHORIZED = NO).
    assert code == 3
    assert payload["reasons"] == [cli.REASON_NOT_AUTHORIZED]
    assert payload["provider_bound"] is False


def test_cli_execute_b1_live_simulation_baseline_refused(tmp_path):
    from open_notebook.integrations.graphrag.eval.authmintlivepn02d import (
        OperatorRunGrant,
    )

    grant = C.frozen_test_grant()
    sim = OperatorRunGrant(
        run_id=grant.run_id, fixture_hash=grant.fixture_hash,
        implementation_checkpoint_commit=grant.implementation_checkpoint_commit,
        implementation_checkpoint_tag=grant.implementation_checkpoint_tag,
        b1_r2_checkpoint=grant.b1_r2_checkpoint,
        provider_config_fingerprint=grant.provider_config_fingerprint,
        workload_caps=grant.workload_caps, operation_allowlist=grant.operation_allowlist,
        approved_git_commit="SIMULATION", approved_git_tag="SIMULATION",
    )
    path = _write_manifest(tmp_path, sim)
    _, fx_reader = _valid_readers()
    with C.approved_b1r2_governance():
        code, payload = cli.evaluate_execute_b1_live(
            manifest_path=path, explicit_authorize=True,
            env={"PN02_PROVIDER_RUN_AUTHORIZED": "YES"},
            git_baseline_reader=lambda: C.clean_git_baseline(commit="SIMULATION", tag="SIMULATION"),
            fixture_hash_reader=fx_reader,
        )
    assert code == 2
    assert "simulation_or_missing_git_commit_baseline" in payload["reasons"]


def test_cli_execute_b1_live_wrong_fixture_hash_refused(tmp_path):
    from open_notebook.integrations.graphrag.eval.authmintlivepn02d import (
        OperatorRunGrant,
    )

    grant = C.frozen_test_grant()
    bad = OperatorRunGrant(
        run_id=grant.run_id, fixture_hash="deadbeef" * 8,
        implementation_checkpoint_commit=grant.implementation_checkpoint_commit,
        implementation_checkpoint_tag=grant.implementation_checkpoint_tag,
        b1_r2_checkpoint=grant.b1_r2_checkpoint,
        provider_config_fingerprint=grant.provider_config_fingerprint,
        workload_caps=grant.workload_caps, operation_allowlist=grant.operation_allowlist,
        approved_git_commit=grant.approved_git_commit, approved_git_tag=grant.approved_git_tag,
    )
    path = _write_manifest(tmp_path, bad)
    git_reader, fx_reader = _valid_readers()
    with C.approved_b1r2_governance():
        code, payload = cli.evaluate_execute_b1_live(
            manifest_path=path, explicit_authorize=True,
            env={"PN02_PROVIDER_RUN_AUTHORIZED": "YES"},
            git_baseline_reader=git_reader, fixture_hash_reader=fx_reader,
        )
    assert code == 2
    assert "grant_fixture_hash_mismatch" in payload["reasons"]


def test_cli_execute_b1_live_wrong_provider_fingerprint_refused(tmp_path):
    from open_notebook.integrations.graphrag.eval.authmintlivepn02d import (
        OperatorRunGrant,
    )

    grant = C.frozen_test_grant()
    bad = OperatorRunGrant(
        run_id=grant.run_id, fixture_hash=grant.fixture_hash,
        implementation_checkpoint_commit=grant.implementation_checkpoint_commit,
        implementation_checkpoint_tag=grant.implementation_checkpoint_tag,
        b1_r2_checkpoint=grant.b1_r2_checkpoint,
        provider_config_fingerprint="pbf_wrongfingerprint000000",
        workload_caps=grant.workload_caps, operation_allowlist=grant.operation_allowlist,
        approved_git_commit=grant.approved_git_commit, approved_git_tag=grant.approved_git_tag,
    )
    path = _write_manifest(tmp_path, bad)
    git_reader, fx_reader = _valid_readers()
    with C.approved_b1r2_governance():
        code, payload = cli.evaluate_execute_b1_live(
            manifest_path=path, explicit_authorize=True,
            env={"PN02_PROVIDER_RUN_AUTHORIZED": "YES"},
            git_baseline_reader=git_reader, fixture_hash_reader=fx_reader,
        )
    assert code == 2
    assert "provider_config_fingerprint_mismatch" in payload["reasons"]


def test_cli_execute_b1_live_dirty_git_baseline_refused(tmp_path):
    grant = C.frozen_test_grant()
    path = _write_manifest(tmp_path, grant)
    _, fx_reader = _valid_readers()
    with C.approved_b1r2_governance():
        code, payload = cli.evaluate_execute_b1_live(
            manifest_path=path, explicit_authorize=True,
            env={"PN02_PROVIDER_RUN_AUTHORIZED": "YES"},
            git_baseline_reader=lambda: C.dirty_git_baseline(),
            fixture_hash_reader=fx_reader,
        )
    assert code == 2
    assert "unstaged_changes_present" in payload["reasons"]


def test_cli_never_boots_or_binds_in_b0c_b(tmp_path):
    # Even with the env token + flag set, this build wires no live seams → refused,
    # never booting a runtime or binding a provider (task §43).
    grant = C.frozen_test_grant()
    path = _write_manifest(tmp_path, grant)
    git_reader, fx_reader = _valid_readers()
    with C.approved_b1r2_governance():
        code, payload = cli.evaluate_execute_b1_live(
            manifest_path=path, explicit_authorize=True,
            env={"PN02_PROVIDER_RUN_AUTHORIZED": "YES"},
            git_baseline_reader=git_reader, fixture_hash_reader=fx_reader,
        )
    assert code == 3
    assert payload["reasons"] == [cli.REASON_NO_LIVE_SEAMS]
    assert payload["provider_bound"] is False
    assert payload["runtime_booted"] is False


def test_live_cli_has_execute_b1_live_verb():
    parser = cli.build_parser()
    args = parser.parse_args(["execute-b1-live", "--manifest", "x.json"])
    assert args.command == "execute-b1-live"


def test_offline_cli_still_has_no_execute_b1_verb():
    # The offline CLI invariant is preserved (execute-b1-live lives in a SEPARATE module).
    from open_notebook.integrations.graphrag.eval import liveclipn02d

    parser = liveclipn02d.build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["execute-b1"])
    with pytest.raises(SystemExit):
        parser.parse_args(["execute-b1-live"])


def test_dry_run_live_plan_is_provider_zero():
    plan = plan_live_b1(run_id="DRYRUN")
    assert plan["provider_traffic"] == 0
    assert plan["pn02_provider_run_authorized"] is False
    assert plan["two_boot_lifecycle"]["same_container"] == "NO"
    assert plan["concurrency"] == {"index": 1, "gd": 1, "vector": 1}
    assert plan["corpus_embedding_workload"]["planned_source_embedding_operations"] == 21
    # No secret value anywhere in the plan.
    blob = json.dumps(plan)
    assert "dummy-test-key" not in blob
    assert "sk-" not in blob


def test_frozen_provider_fingerprint_pinned():
    assert frozen_provider_config_id() == EXPECTED_PROVIDER_CONFIG_ID


def test_corpus_workload_derivation():
    fx = C.fixture()
    workload = derive_corpus_workload(fx)
    assert workload.canonical_source_count == 21
    assert workload.reference_edge_count == 24
    assert workload.planned_source_embedding_operations == 21
    assert workload.max_source_embedding_operations == 21
    assert workload.embedded_once_per_isolated_corpus is True


# =========================================================================== #
# Codex re-review remediation cycle #2
# =========================================================================== #

# --- B0CB-H2-R1: real tag identity (no branch-name fallback) ----------------- #

_APPROVED_COMMIT = "a1b2c3d4" + "0" * 32
_APPROVED_TAG = "graphrag-pn02db0cb-approved"


def _baseline_reasons(git_runner, *, commit=_APPROVED_COMMIT, tag=_APPROVED_TAG):
    # Exercises the REAL parser (read_git_baseline) + the REAL attest logic.
    attestation = read_git_baseline(git_runner=git_runner)
    return attest_approved_clean_baseline(
        attestation, approved_commit=commit, approved_tag=tag
    )


def test_h2r1_A_clean_exact_tagged_head_passes():
    reasons = _baseline_reasons(
        C.fake_git_runner(head=_APPROVED_COMMIT, branch="feature/graphrag-lifecycle",
                          exact_tag=_APPROVED_TAG, tag_peel=_APPROVED_COMMIT)
    )
    assert reasons == []


def test_h2r1_B_clean_untagged_head_fails():
    reasons = _baseline_reasons(
        C.fake_git_runner(head=_APPROVED_COMMIT, branch="feature/graphrag-lifecycle",
                          exact_tag="", tag_peel="")
    )
    assert "no_exact_tag_on_head" in reasons


def test_h2r1_D_branch_name_as_tag_but_no_real_tag_fails():
    # LOAD-BEARING: manifest approves the branch name as the tag; no real tag exists.
    reasons = _baseline_reasons(
        C.fake_git_runner(head=_APPROVED_COMMIT, branch="feature/graphrag-lifecycle",
                          exact_tag="", tag_peel=""),
        tag="feature/graphrag-lifecycle",
    )
    assert "no_exact_tag_on_head" in reasons


def test_h2r1_E_wrong_exact_tag_fails():
    reasons = _baseline_reasons(
        C.fake_git_runner(head=_APPROVED_COMMIT, branch="feature/x",
                          exact_tag="othertag", tag_peel=_APPROVED_COMMIT)
    )
    assert "git_tag_baseline_mismatch" in reasons


def test_h2r1_F_tag_peels_to_other_commit_fails():
    reasons = _baseline_reasons(
        C.fake_git_runner(head=_APPROVED_COMMIT, branch="feature/x",
                          exact_tag=_APPROVED_TAG, tag_peel="b" * 40)
    )
    assert "tag_peel_mismatch" in reasons


def test_h2r1_G_head_differs_from_tag_peel_fails():
    reasons = _baseline_reasons(
        C.fake_git_runner(head="c" * 40, branch="feature/x",
                          exact_tag=_APPROVED_TAG, tag_peel=_APPROVED_COMMIT)
    )
    assert "git_commit_baseline_mismatch" in reasons


def test_h2r1_H_head_correct_but_approved_tag_differs_fails():
    reasons = _baseline_reasons(
        C.fake_git_runner(head=_APPROVED_COMMIT, branch="feature/x",
                          exact_tag="realtag", tag_peel=_APPROVED_COMMIT),
        tag="differenttag",
    )
    assert "git_tag_baseline_mismatch" in reasons


def test_h2r1_I_staged_changes_fail():
    reasons = _baseline_reasons(
        C.fake_git_runner(head=_APPROVED_COMMIT, branch="feature/x",
                          exact_tag=_APPROVED_TAG, tag_peel=_APPROVED_COMMIT,
                          porcelain="M  open_notebook/x.py")
    )
    assert "staged_changes_present" in reasons


def test_h2r1_J_unstaged_changes_fail():
    reasons = _baseline_reasons(
        C.fake_git_runner(head=_APPROVED_COMMIT, branch="feature/x",
                          exact_tag=_APPROVED_TAG, tag_peel=_APPROVED_COMMIT,
                          porcelain=" M open_notebook/x.py")
    )
    assert "unstaged_changes_present" in reasons


def test_h2r1_K_untracked_execution_affecting_fail():
    reasons = _baseline_reasons(
        C.fake_git_runner(head=_APPROVED_COMMIT, branch="feature/x",
                          exact_tag=_APPROVED_TAG, tag_peel=_APPROVED_COMMIT,
                          porcelain="?? open_notebook/new_module.py")
    )
    assert "untracked_execution_affecting_present" in reasons


# --- B0CB-M1M2-R1: real preflight runner fail-closed + 3 independent signals -- #

def _preflight_argv(name="pf-cell", net="pf-net"):
    return [
        "docker", "run", "-d", "--name", name, "--network", net,
        "-e", "WORKSPACE=nb_x", "-e", "LLM_BINDING_API_KEY=",
        "-v", "/s:/app/data/rag_storage", "img",
    ]


@pytest.mark.asyncio
async def test_m1_network_create_failure_fails_closed():
    docker = C.FakeDockerCLI(net_ok=False)
    runner = RealProviderFreePreflightRunner(docker=docker)
    with pytest.raises(PreflightRunError):
        await runner.launch(_preflight_argv())
    assert docker.nets_created == []  # no owned network recorded


@pytest.mark.asyncio
async def test_m1_docker_run_nonzero_fails_closed_and_cleans():
    docker = C.FakeDockerCLI(run_returncode=1)
    runner = RealProviderFreePreflightRunner(docker=docker)
    with pytest.raises(PreflightRunError):
        await runner.launch(_preflight_argv())
    assert docker.nets_created == ["pf-net"]
    assert "pf-net" in docker.nets_removed  # partial owned state cleaned
    assert "pf-cell" in docker.removed


@pytest.mark.asyncio
async def test_m1_docker_run_exception_fails_closed():
    docker = C.FakeDockerCLI(run_raises=True)
    runner = RealProviderFreePreflightRunner(docker=docker)
    with pytest.raises(PreflightRunError):
        await runner.launch(_preflight_argv())
    assert "pf-net" in docker.nets_removed


@pytest.mark.asyncio
async def test_m1_container_not_running_fails_closed():
    docker = C.FakeDockerCLI(container_running=False)
    runner = RealProviderFreePreflightRunner(docker=docker)
    with pytest.raises(PreflightRunError):
        await runner.launch(_preflight_argv())
    assert "pf-cell" in docker.removed and "pf-net" in docker.nets_removed


@pytest.mark.asyncio
async def test_m1_health_unavailable_fails_closed():
    docker = C.FakeDockerCLI(health_code=None)
    runner = RealProviderFreePreflightRunner(docker=docker)
    handle = await runner.launch(_preflight_argv())
    with pytest.raises(PreflightRunError):
        await runner.version_signals(handle)


@pytest.mark.asyncio
async def test_m1_installed_version_missing_fails_closed():
    docker = C.FakeDockerCLI(installed_version="")
    runner = RealProviderFreePreflightRunner(docker=docker)
    handle = await runner.launch(_preflight_argv())
    with pytest.raises(PreflightRunError):
        await runner.version_signals(handle)


@pytest.mark.asyncio
async def test_m1_image_label_missing_fails_closed():
    docker = C.FakeDockerCLI(image_label="")
    runner = RealProviderFreePreflightRunner(docker=docker)
    handle = await runner.launch(_preflight_argv())
    with pytest.raises(PreflightRunError):
        await runner.version_signals(handle)


@pytest.mark.asyncio
async def test_m1_three_signals_are_independent():
    # All three come from distinct DockerCLI methods; changing one does not change others.
    docker = C.FakeDockerCLI(health_core="1.5.6", installed_version="1.5.6", image_label="v1.5.6")
    runner = RealProviderFreePreflightRunner(docker=docker)
    handle = await runner.launch(_preflight_argv())
    assert await runner.version_signals(handle) == ("1.5.6", "1.5.6", "v1.5.6")

    docker2 = C.FakeDockerCLI(health_core="9.9.9", installed_version="1.5.6", image_label="v1.5.6")
    runner2 = RealProviderFreePreflightRunner(docker=docker2)
    handle2 = await runner2.launch(_preflight_argv())
    core, installed, label = await runner2.version_signals(handle2)
    assert core == "9.9.9" and installed == "1.5.6" and label == "v1.5.6"


def _manager_with_real_runner(fx, docker):
    return RealPN02RuntimeManager(
        fx=fx,
        process_controller=C.FakeProcessController(),
        health_prober=C.FakeHealthProber(),
        version_signal_reader=C.fake_version_signal_reader,
        port_allocator=C.DeterministicPortAllocator(),
        storage_dir_allocator=C.fake_storage_dir_allocator,
        preflight_runner=RealProviderFreePreflightRunner(docker=docker),
    )


@pytest.mark.asyncio
async def test_m1m2_image_label_correct_but_no_real_boot_fails_and_blocks_execution():
    # Codex scenario 14: image label correct but docker run fails → preflight NOT passed
    # → provider-bound execution refused.
    fx = C.fixture()
    docker = C.FakeDockerCLI(run_returncode=1, image_label="v1.5.6")
    mgr = _manager_with_real_runner(fx, docker)
    preflight = await mgr.boot_preflight()
    assert preflight.passed is False

    live_auth = C.mint_test_live_auth()
    materialized = materialize_live_provider_binding(
        live_auth, present_secret_envs=frozenset({"OPENROUTER_API_KEY"})
    )
    with pytest.raises(TwoBootOrderError):
        await mgr.boot_execution(live_auth=live_auth, materialized_binding=materialized)


@pytest.mark.asyncio
async def test_m1m2_one_version_signal_mismatch_fails_preflight():
    # 2/3 signals correct, health core mismatched → attest_version fails → passed False.
    fx = C.fixture()
    docker = C.FakeDockerCLI(health_core="9.9.9", installed_version="1.5.6", image_label="v1.5.6")
    mgr = _manager_with_real_runner(fx, docker)
    preflight = await mgr.boot_preflight()
    assert preflight.passed is False


@pytest.mark.asyncio
async def test_m1m2_all_three_signals_correct_real_logic_passes():
    # Positive real-logic path (§19): no provider, no real Docker; fake transport only.
    fx = C.fixture()
    docker = C.FakeDockerCLI()  # defaults: running, health 200/1.5.6, installed 1.5.6, label v1.5.6
    mgr = _manager_with_real_runner(fx, docker)
    preflight = await mgr.boot_preflight()
    assert preflight.passed is True
    assert preflight.runtime_count == 3


# =========================================================================== #
# Codex re-review #4 remediation cycle #5 — no CLI/mint trust-root injection (RR4-H1)
# =========================================================================== #

def test_cli_b1_r2_default_governance_path_refused(tmp_path):
    # §14 defense-in-depth (checkpoint-lifecycle robust): the CLI default wiring (mint-owned
    # real reader + governance) refuses when the approved B1-R2 tag is absent from real Git.
    # To keep this PERMANENT before/after the real checkpoint, governance is pinned to the
    # SYNTHETIC C.TEST_B1R2_TAG (never a real Git tag) — the real reader always reports it
    # absent → `b1_r2_tag_not_observed_in_git`. The CLI still has NO trust-root parameter.
    grant = C.frozen_test_grant()  # grant b1_r2 == C.TEST_B1R2_TAG (matches pinned governance)
    path = _write_manifest(tmp_path, grant)
    _, fx_reader = _valid_readers()
    with C.governance_expects_tag(C.TEST_B1R2_TAG):
        code, payload = cli.evaluate_execute_b1_live(
            manifest_path=path,
            explicit_authorize=True,
            env={"PN02_PROVIDER_RUN_AUTHORIZED": "YES"},
            git_baseline_reader=lambda: C.clean_git_baseline(),
            fixture_hash_reader=fx_reader,  # real trusted reader + pinned synthetic governance
        )
    assert code == 2
    assert "b1_r2_tag_not_observed_in_git" in payload["reasons"]


def test_cli_b1_r2_real_reader_observes_no_tag_refused(tmp_path):
    # Even with governance patched to expect a tag, the mint-owned REAL git reader observes
    # no such tag in this repo (NOT_STARTED) → refused. Governance is patched at the module
    # boundary; the reader factory is NOT patched, so it stays the real-Git reader.
    grant = C.frozen_test_grant()
    path = _write_manifest(tmp_path, grant)
    _, fx_reader = _valid_readers()
    with mock.patch.object(
        authmint, "current_approved_b1_r2_checkpoint", return_value=C.TEST_B1R2_TAG
    ):
        code, payload = cli.evaluate_execute_b1_live(
            manifest_path=path,
            explicit_authorize=True,
            env={"PN02_PROVIDER_RUN_AUTHORIZED": "YES"},
            git_baseline_reader=lambda: C.clean_git_baseline(),
            fixture_hash_reader=fx_reader,
        )
    assert code == 2
    assert "b1_r2_tag_not_observed_in_git" in payload["reasons"]


def test_cli_has_no_b1_r2_trust_injection_parameters():
    # B0CB-RR4-H1: the CLI entrypoint accepts NO trust-root override — old kwargs rejected.
    with pytest.raises(TypeError):
        cli.evaluate_execute_b1_live(
            manifest_path=None, explicit_authorize=False, env={},
            trusted_b1_r2_reader=C.b1r2_reader_ok(),  # type: ignore[call-arg]
        )
    with pytest.raises(TypeError):
        cli.evaluate_execute_b1_live(
            manifest_path=None, explicit_authorize=False, env={},
            approved_expected_b1_r2_checkpoint=C.TEST_B1R2_TAG,  # type: ignore[call-arg]
        )


def test_current_repo_cannot_verify_b1_r2_checkpoint():
    # Governance (§16): in the CURRENT repository (B1-R2 NOT_STARTED) no approved B1-R2
    # tag exists, so the REAL trusted reader + REAL verifier fail closed even if the
    # approved-expected identity is supplied — a live authorization is not mintable.
    reader = RealTrustedB1R2Reader()  # real git
    reasons = verify_b1_r2_checkpoint(
        reader=reader,
        operator_grant=C.frozen_test_grant(),
        approved_expected_checkpoint=C.TEST_B1R2_TAG,
        git_baseline=C.clean_git_baseline(),
    )
    assert "b1_r2_tag_not_observed_in_git" in reasons
    # governance (the real path) is now frozen to the expected B1-R2 tag, but that tag does
    # not exist in Git yet, so the mint still fails closed.
    from open_notebook.integrations.graphrag.eval.authmintlivepn02d import (
        EXPECTED_B1_R2_CHECKPOINT_TAG,
    )

    assert current_approved_b1_r2_checkpoint() == EXPECTED_B1_R2_CHECKPOINT_TAG


def test_current_governance_flags_unchanged():
    # The presence of B0C-B implementation must not flip governance.
    from open_notebook.integrations.graphrag.eval.authlivepn02d import (
        PN02_PROVIDER_RUN_AUTHORIZED,
    )

    assert PN02_PROVIDER_RUN_AUTHORIZED is False
