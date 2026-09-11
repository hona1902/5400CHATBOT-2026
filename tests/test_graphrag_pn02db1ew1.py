"""PN02D-B1-EW1 — real B1 execution seams wiring tests.

EVALUATION-ONLY, ZERO provider traffic. Proves the missing production composition boundary
now exists: ``build_real_b1_live_seams`` assembles the complete real ``LiveB1Seams`` from
approved components (no fakes/placeholders), the authorized ``execute-b1-live`` CLI reaches
``RealB1Driver.run`` (no more ``REASON_NO_LIVE_SEAMS``), a missing provider secret fails
closed BEFORE any boot, and the EW1 successor governance supersedes PF1 while historical
PF1/B1-R2 can never substitute. A provider-network sentinel fails any test that would touch
a real provider/HTTP transport, and a secret-safe-logging check forbids secret values in
payloads.

Every external edge (Docker, HTTP, DB, provider, isolation) is REPLACED by an injected
fake — the REAL builder/runner/CLI are the code under test. ``EXTERNAL_PROVIDER_NETWORK_
CALLS = 0``, ``REAL_PROVIDER_EMBEDDINGS = 0``, ``PROVIDER_BOUND_LIGHTRAG_BOOT_COUNT = 0``,
``NORMAL_DB_MUTATIONS = 0``.
"""

from __future__ import annotations

import ast
import dataclasses
import inspect
import json
from contextlib import asynccontextmanager
from typing import Dict
from unittest import mock

import graphrag_pn02db0cb_common as C
import httpx
import pytest

from open_notebook.integrations.graphrag.eval import authb1r2pn02d as B
from open_notebook.integrations.graphrag.eval import cli_live_pn02d as cli
from open_notebook.integrations.graphrag.eval import realseamspn02d as R
from open_notebook.integrations.graphrag.eval.authmintlivepn02d import (
    EXPECTED_B1_R2_CHECKPOINT_TAG,
    EXPECTED_EW1_CHECKPOINT_TAG,
    EXPECTED_PF1_CHECKPOINT_TAG,
    RealTrustedB1R2Reader,
    current_approved_b1_r2_checkpoint,
    mint_live_provider_run_authorization,
    verify_b1_r2_checkpoint,
)
from open_notebook.integrations.graphrag.eval.driver_live_pn02d import LiveB1Seams
from open_notebook.integrations.graphrag.eval.realseamspn02d import (
    build_real_b1_live_seams,
    build_real_model_attestor,
    run_live_b1_execution,
)

FUTURE_COMMIT = "e1e1e1e1" + "0" * 32
B0CB_TAG = "graphrag-pn02db0cb-real-provider-wiring-approved"


def _fixture_hash() -> str:
    ok, detail = C.verify_fixture_hash()
    return detail if ok else "UNVERIFIED"


# --------------------------------------------------------------------------- #
# §33 — provider network sentinel (autouse): any real provider/HTTP egress fails
# --------------------------------------------------------------------------- #

@pytest.fixture(autouse=True)
def provider_network_sentinel(monkeypatch):
    """Fail-fast if a test would make a REAL provider embedding or outbound httpx request.

    The mock ``httpx.MockTransport`` bypasses ``AsyncHTTPTransport`` (so injected transports
    still work); an ACCIDENTAL real transport or real ``generate_embedding`` call raises.
    """
    async def _blocked_embed(*_a, **_k):
        raise AssertionError("real provider embedding attempted (EXTERNAL_PROVIDER_CALL)")

    async def _blocked_http(*_a, **_k):
        raise AssertionError("real outbound httpx request attempted (EXTERNAL_NETWORK_CALL)")

    import open_notebook.utils.embedding as _emb

    monkeypatch.setattr(_emb, "generate_embedding", _blocked_embed, raising=True)
    monkeypatch.setattr(
        httpx.AsyncHTTPTransport, "handle_async_request", _blocked_http, raising=True
    )
    yield


# --------------------------------------------------------------------------- #
# Fake external-edge kwargs for the REAL builder (mirrors the mock topology)
# --------------------------------------------------------------------------- #

def _fake_edge_kwargs(fx, *, port_base: int = C.PORT_BASE):
    controller = C.FakeProcessController()
    prober = C.FakeHealthProber()
    preflight_runner = C.FakePreflightRunner()
    corpus_db = C.FakeCorpusDB()
    http_calls: Dict[str, int] = {}
    notebook_ids = list(fx.notebook_ids)
    base_url_to_notebook = {
        f"http://127.0.0.1:{port_base + i}": nb for i, nb in enumerate(notebook_ids)
    }
    transport = C.build_mock_transport(fx, base_url_to_notebook, calls=http_calls)
    kwargs = dict(
        process_controller=controller,
        preflight_runner=preflight_runner,
        health_prober=prober,
        version_signal_reader=C.fake_version_signal_reader,
        port_allocator=C.DeterministicPortAllocator(port_base),
        storage_dir_allocator=C.fake_storage_dir_allocator,
        corpus_seams={
            "source_creator": corpus_db.source_creator(),
            "reference_linker": corpus_db.reference_linker(),
            "source_embedder": corpus_db.source_embedder(fx),
        },
        member_row_fetcher=corpus_db.member_row_fetcher(),
        query_embed_fn=C.make_query_embed_fn(fx),
        model_attestor=C.make_model_attestor(),
        index_transport=transport,
        gd_transport=transport,
        delete_transport=transport,
        lightrag_api_key="dummy-test-key",
        present_secret_envs=frozenset({"OPENROUTER_API_KEY"}),
        corpus_teardown=corpus_db.teardown,
    )
    return kwargs, http_calls, corpus_db, controller, preflight_runner


@asynccontextmanager
async def _noop_isolation(_run_id):
    yield None


# --------------------------------------------------------------------------- #
# §27 — LIVE_SEAMS_COMPLETENESS_TEST (dynamic; fails if a new seam is unpopulated)
# --------------------------------------------------------------------------- #

def test_live_seams_completeness_all_required_fields_populated():
    fx = C.fixture()
    kwargs, *_ = _fake_edge_kwargs(fx)
    seams = build_real_b1_live_seams(fx, run_id="ew1-complete", **kwargs)

    fields = dataclasses.fields(LiveB1Seams)
    required = [
        f.name
        for f in fields
        if f.default is dataclasses.MISSING
        and f.default_factory is dataclasses.MISSING  # type: ignore[misc]
    ]
    # The full LiveB1Seams contract has 12 required (no-default) fields; every one must be
    # populated by the production builder (dynamic — a newly-added required seam fails here).
    assert len(required) == 12
    for name in required:
        assert getattr(seams, name) is not None, f"required seam not populated: {name}"

    # The three corpus seams are composed from the (injected) corpus-seams mapping.
    assert seams.source_creator is not None
    assert seams.reference_linker is not None
    assert seams.source_embedder is not None
    # notebook_record_ids is composed from the fixture (PN02-scope-bound, 3 notebooks).
    assert set(seams.notebook_record_ids) == set(fx.notebook_ids)


def test_notebook_record_ids_are_fixture_scope_bound():
    fx = C.fixture()
    kwargs, *_ = _fake_edge_kwargs(fx)
    seams = build_real_b1_live_seams(fx, run_id="ew1-scope", **kwargs)
    assert seams.notebook_record_ids == {
        nb.notebook_id: nb.record_id for nb in fx.notebooks
    }


# --------------------------------------------------------------------------- #
# §28 — NO_TEST_OR_PLACEHOLDER_SEAMS (real production producers, never fakes)
# --------------------------------------------------------------------------- #

def test_default_seam_producers_are_real_production_symbols():
    from open_notebook.integrations.graphrag.eval.cell_provisioner08 import (
        DockerCellProcessController,
        EphemeralPortAllocator,
        LightRagCellHealthProber,
    )
    from open_notebook.integrations.graphrag.eval.corpuslivepn02d import (
        build_in_isolation_corpus_seams,
    )
    from open_notebook.integrations.graphrag.eval.realsidecarpn02d import (
        DockerCLI,
        allocate_real_storage_root,
    )
    from open_notebook.integrations.graphrag.eval.runtimelivepn02d import (
        RealProviderFreePreflightRunner,
    )
    from open_notebook.integrations.graphrag.eval.vectoradapterpn02d import (
        build_repo_query_member_row_fetcher,
    )

    p = R.REAL_SEAM_DEFAULT_PRODUCERS
    # Every default producer originates in production (never a ``tests`` module).
    for name, producer in p.items():
        module = getattr(producer, "__module__", "")
        assert module.startswith("open_notebook"), (name, module)
        assert "tests" not in module and "graphrag_pn02db0cb_common" not in module

    # Identity of the genuinely-external default producers (no fakes/placeholders).
    assert p["process_controller"] is DockerCellProcessController
    assert p["preflight_runner"] is RealProviderFreePreflightRunner
    assert p["health_prober"] is LightRagCellHealthProber
    assert p["port_allocator"] is EphemeralPortAllocator
    assert p["storage_dir_allocator"] is allocate_real_storage_root
    assert p["corpus_seams"] is build_in_isolation_corpus_seams
    assert p["member_row_fetcher"] is build_repo_query_member_row_fetcher
    assert p["docker"] is DockerCLI


def test_real_model_attestor_reads_active_model_and_is_not_always_true():
    # §15: the real attestor reads the CONFIGURED default embedding model — a different
    # provider/model fails the frozen match (never an always-true stub); a mismatched model
    # reports dimension 0, so it can never masquerade as the frozen 1536.
    import asyncio

    import open_notebook.ai.models as M

    async def _attest(provider: str, name: str):
        dm = mock.Mock()
        dm.default_embedding_model = "model:x"
        model = mock.Mock()
        model.provider = provider
        model.name = name
        with mock.patch.object(
            M.DefaultModels, "get_instance", new=mock.AsyncMock(return_value=dm)
        ), mock.patch.object(M.Model, "get", new=mock.AsyncMock(return_value=model)):
            return await build_real_model_attestor()()

    frozen = asyncio.run(_attest("openrouter", "openai/text-embedding-3-small"))
    assert frozen.matches_frozen is True
    assert frozen.dimension == 1536

    other = asyncio.run(_attest("openai", "text-embedding-ada-002"))
    assert other.matches_frozen is False
    assert other.dimension == 0


# --------------------------------------------------------------------------- #
# §6 — PRODUCTION_IMPORTS_TEST_MODULES = NO
# --------------------------------------------------------------------------- #

def test_realseams_module_imports_no_test_modules():
    source = inspect.getsource(R)
    tree = ast.parse(source)
    imported: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.extend(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.append(node.module)
    for module in imported:
        assert not module.startswith("tests"), module
        assert "graphrag_pn02db0cb_common" not in module
        assert not module.startswith("test_"), module


# --------------------------------------------------------------------------- #
# §32 — PRODUCTION_COMPOSITION_ORCHESTRATION_TEST (CLI-less: runner → builder → driver)
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_production_composition_orchestration_reaches_driver():
    fx = C.fixture()
    kwargs, http_calls, corpus_db, controller, preflight_runner = _fake_edge_kwargs(fx)

    with C.approved_b1r2_governance():
        outcome = await run_live_b1_execution(
            operator_grant=C.frozen_test_grant(),
            git_baseline_attestation=C.clean_git_baseline(),
            observed_fixture_hash=_fixture_hash(),
            fx=fx,
            seams_builder=build_real_b1_live_seams,  # the REAL builder (not C.build_live_seams)
            isolation=_noop_isolation,
            builder_kwargs=kwargs,
        )

    # The real two-boot flow reached a COMPLETE run through the frozen orchestrator.
    assert outcome.state == "COMPLETE"
    assert outcome.evaluation is not None
    assert outcome.index_completion is not None and outcome.index_completion.succeeded == 24
    # Wire accounting proves the real adapters ran through the composed seams.
    assert http_calls.get("index_submit") == 24
    assert http_calls.get("query_data") == 26
    assert http_calls.get("delete") == 1
    assert corpus_db.embed_calls == 21
    assert corpus_db.torn_down is True
    # Two-boot lifecycle exercised through the composed seams.
    assert len(preflight_runner.launched) == 3
    assert len(controller.started) == 3
    assert len(controller.terminated) == 3


# --------------------------------------------------------------------------- #
# §29 — REAL_EXECUTION_CLI_REACHABLE (no longer REASON_NO_LIVE_SEAMS)
# --------------------------------------------------------------------------- #

def _reachable_runner(fx, kwargs):
    import asyncio

    def _runner(*, operator_grant, git_baseline, observed_fixture_hash, env):
        return asyncio.run(
            run_live_b1_execution(
                operator_grant=operator_grant,
                git_baseline_attestation=git_baseline,
                observed_fixture_hash=observed_fixture_hash,
                fx=fx,
                seams_builder=build_real_b1_live_seams,
                isolation=_noop_isolation,
                builder_kwargs=kwargs,
            )
        )

    return _runner


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


def test_real_execution_cli_reachable(tmp_path):
    fx = C.fixture()
    kwargs, http_calls, *_ = _fake_edge_kwargs(fx)
    grant = C.frozen_test_grant()
    path = _write_manifest(tmp_path, grant)

    with C.approved_b1r2_governance():
        code, payload = cli.evaluate_execute_b1_live(
            manifest_path=path,
            explicit_authorize=True,
            env={"PN02_PROVIDER_RUN_AUTHORIZED": "YES", "OPENROUTER_API_KEY": "dummy"},
            git_baseline_reader=lambda: C.clean_git_baseline(),
            fixture_hash_reader=C.verify_fixture_hash,
            live_runner=_reachable_runner(fx, kwargs),
        )

    assert code == 0
    assert payload["result"] == "COMPLETE"
    assert payload.get("reasons", []) != [cli.REASON_NO_LIVE_SEAMS]
    assert payload["provider_bound"] is True
    assert payload["runtime_booted"] is True
    assert http_calls.get("query_data") == 26


# --------------------------------------------------------------------------- #
# §30 — CLI_MISSING_SECRET_FAIL_CLOSED (no boot, no provider)
# --------------------------------------------------------------------------- #

def test_cli_missing_provider_secret_fails_closed(tmp_path):
    fx = C.fixture()
    kwargs, *_ = _fake_edge_kwargs(fx)
    grant = C.frozen_test_grant()
    path = _write_manifest(tmp_path, grant)

    calls = {"n": 0}

    def _counting_runner(**_kw):
        calls["n"] += 1
        raise AssertionError("live_runner must not run when the secret is missing")

    with C.approved_b1r2_governance():
        code, payload = cli.evaluate_execute_b1_live(
            manifest_path=path,
            explicit_authorize=True,
            env={"PN02_PROVIDER_RUN_AUTHORIZED": "YES"},  # no OPENROUTER_API_KEY
            git_baseline_reader=lambda: C.clean_git_baseline(),
            fixture_hash_reader=C.verify_fixture_hash,
            live_runner=_counting_runner,
        )
    assert code == 3
    assert payload["reasons"] == [cli.REASON_PROVIDER_SECRET_MISSING]
    assert payload["provider_bound"] is False
    assert payload["runtime_booted"] is False
    assert calls["n"] == 0  # PROVIDER_CALLS_IN_MISSING_SECRET_TEST = 0


# --------------------------------------------------------------------------- #
# §31 — CLI_WRONG_CHECKPOINT_FAIL_CLOSED (historical PF1 cannot substitute for EW1)
# --------------------------------------------------------------------------- #

def test_cli_wrong_checkpoint_historical_pf1_refused(tmp_path):
    fx = C.fixture()
    kwargs, *_ = _fake_edge_kwargs(fx)
    # A grant naming the HISTORICAL PF1 checkpoint as its B1-R2 identity, while governance
    # approves EW1 → refused at input validation (b1_r2 mismatch), before any boot.
    grant = C.frozen_test_grant(b1_r2_checkpoint=EXPECTED_PF1_CHECKPOINT_TAG)
    path = _write_manifest(tmp_path, grant)

    def _forbidden_runner(**_kw):
        raise AssertionError("live_runner must not run on a wrong-checkpoint grant")

    # governance approves EW1 (real), but the reader is patched to observe the PF1 tag so we
    # isolate the identity-mismatch refusal (not merely tag-absence).
    with C.governance_expects_tag(EXPECTED_EW1_CHECKPOINT_TAG):
        code, payload = cli.evaluate_execute_b1_live(
            manifest_path=path,
            explicit_authorize=True,
            env={"PN02_PROVIDER_RUN_AUTHORIZED": "YES", "OPENROUTER_API_KEY": "dummy"},
            git_baseline_reader=lambda: C.clean_git_baseline(),
            fixture_hash_reader=C.verify_fixture_hash,
            live_runner=_forbidden_runner,
        )
    assert code == 2
    assert "b1_r2_grant_identity_mismatch" in payload["reasons"]
    assert payload["provider_bound"] is False


# --------------------------------------------------------------------------- #
# §41 — successor governance regression (exact identity + trusted reader binding)
# --------------------------------------------------------------------------- #

def test_governance_current_is_ew1_pf1_and_b1r2_historical():
    assert current_approved_b1_r2_checkpoint() == EXPECTED_EW1_CHECKPOINT_TAG
    assert B.B1_R2_EXPECTED_CHECKPOINT_TAG == EXPECTED_EW1_CHECKPOINT_TAG
    # All three identities are distinct; PF1 and B1-R2 are retained as HISTORICAL only.
    assert len({
        EXPECTED_EW1_CHECKPOINT_TAG,
        EXPECTED_PF1_CHECKPOINT_TAG,
        EXPECTED_B1_R2_CHECKPOINT_TAG,
    }) == 3


@pytest.mark.parametrize(
    "historical_tag",
    [EXPECTED_PF1_CHECKPOINT_TAG, EXPECTED_B1_R2_CHECKPOINT_TAG, B0CB_TAG],
)
def test_historical_or_arbitrary_tag_cannot_substitute_for_ew1(historical_tag):
    # Even if the reader observes SOME tag present at HEAD, a grant naming a non-EW1
    # identity is rejected because governance-approved == EW1 and the grant's B1-R2 identity
    # must EXACTLY equal it (exact-identity primary authority, not a denylist — §42).
    reader = C.b1r2_reader_ok(tag=historical_tag, peel="a" * 40, head="a" * 40)
    grant = C.frozen_test_grant(b1_r2_checkpoint=historical_tag)
    reasons = verify_b1_r2_checkpoint(
        reader=reader,
        operator_grant=grant,
        approved_expected_checkpoint=EXPECTED_EW1_CHECKPOINT_TAG,
        git_baseline=C.clean_git_baseline(commit="a" * 40, tag=historical_tag),
    )
    assert "b1_r2_grant_identity_mismatch" in reasons


def test_missing_ew1_tag_fails_closed_in_real_git():
    # The real Git repo has no EW1 tag (NOT_STARTED) → the trusted reader observes its
    # absence and the verifier refuses; a caller cannot supply the missing tag.
    reader = RealTrustedB1R2Reader()
    reasons = verify_b1_r2_checkpoint(
        reader=reader,
        operator_grant=C.frozen_test_grant(b1_r2_checkpoint=EXPECTED_EW1_CHECKPOINT_TAG),
        approved_expected_checkpoint=EXPECTED_EW1_CHECKPOINT_TAG,
        git_baseline=C.clean_git_baseline(),
    )
    assert "b1_r2_tag_not_observed_in_git" in reasons


def test_exact_ew1_tag_with_peel_is_the_only_accepted_identity():
    # The exact EW1 identity, trust-observed at the authorized HEAD, with a matching
    # baseline, is accepted (no real tag created — the reader is scripted).
    reader = C.b1r2_reader_ok(
        tag=EXPECTED_EW1_CHECKPOINT_TAG, peel=FUTURE_COMMIT, head=FUTURE_COMMIT
    )
    grant = B.build_b1_r2_operator_grant(approved_git_commit=FUTURE_COMMIT)
    reasons = verify_b1_r2_checkpoint(
        reader=reader,
        operator_grant=grant,
        approved_expected_checkpoint=EXPECTED_EW1_CHECKPOINT_TAG,
        git_baseline=C.clean_git_baseline(
            commit=FUTURE_COMMIT, tag=EXPECTED_EW1_CHECKPOINT_TAG
        ),
    )
    assert reasons == []


def test_mint_and_runner_expose_no_trust_root_injection():
    # §23/§41: neither the mint nor the CLI can be handed an approved identity or a reader.
    mint_params = inspect.signature(mint_live_provider_run_authorization).parameters
    assert "trusted_b1_r2_reader" not in mint_params
    assert "approved_expected_b1_r2_checkpoint" not in mint_params
    cli_params = inspect.signature(cli.evaluate_execute_b1_live).parameters
    assert "trusted_b1_r2_reader" not in cli_params
    assert "approved_expected_b1_r2_checkpoint" not in cli_params


# --------------------------------------------------------------------------- #
# §36 — SECRET_SAFE_LOGGING (no secret VALUE in payloads / outcome report)
# --------------------------------------------------------------------------- #

def test_secret_safe_no_secret_value_in_cli_payload(tmp_path):
    fx = C.fixture()
    kwargs, *_ = _fake_edge_kwargs(fx)
    grant = C.frozen_test_grant()
    path = _write_manifest(tmp_path, grant)

    with C.approved_b1r2_governance():
        _code, payload = cli.evaluate_execute_b1_live(
            manifest_path=path,
            explicit_authorize=True,
            env={"PN02_PROVIDER_RUN_AUTHORIZED": "YES", "OPENROUTER_API_KEY": "sk-secret-value"},
            git_baseline_reader=lambda: C.clean_git_baseline(),
            fixture_hash_reader=C.verify_fixture_hash,
            live_runner=_reachable_runner(fx, kwargs),
        )
    blob = json.dumps(payload)
    assert "sk-secret-value" not in blob
    assert "dummy-test-key" not in blob  # local sidecar auth never surfaces either


@pytest.mark.asyncio
async def test_secret_safe_no_secret_value_in_outcome_report():
    fx = C.fixture()
    kwargs, *_ = _fake_edge_kwargs(fx)
    with C.approved_b1r2_governance():
        outcome = await run_live_b1_execution(
            operator_grant=C.frozen_test_grant(),
            git_baseline_attestation=C.clean_git_baseline(),
            observed_fixture_hash=_fixture_hash(),
            fx=fx,
            seams_builder=build_real_b1_live_seams,
            isolation=_noop_isolation,
            builder_kwargs=kwargs,
        )
    blob = json.dumps(outcome.report)
    assert "dummy-test-key" not in blob
    assert "sk-" not in blob


# --------------------------------------------------------------------------- #
# Local-vs-provider secret separation (§4)
# --------------------------------------------------------------------------- #

def test_local_sidecar_auth_env_is_not_the_provider_credential():
    assert R.LIGHTRAG_LOCAL_AUTH_ENV == "GRAPHRAG_POC_API_KEY"
    assert R.LIGHTRAG_LOCAL_AUTH_ENV != "OPENROUTER_API_KEY"


def test_builder_resolves_local_auth_by_name_from_env_only():
    fx = C.fixture()
    kwargs, *_ = _fake_edge_kwargs(fx)
    # Drop the injected local key so the env-name resolution path is exercised.
    kwargs.pop("lightrag_api_key")
    seams = build_real_b1_live_seams(
        fx, run_id="ew1-localauth", env={"GRAPHRAG_POC_API_KEY": "local-token"}, **kwargs
    )
    assert seams.lightrag_api_key == "local-token"
    # Absent → None (sidecar without auth), never a provider credential.
    kwargs2, *_ = _fake_edge_kwargs(fx)
    kwargs2.pop("lightrag_api_key")
    seams2 = build_real_b1_live_seams(fx, run_id="ew1-noauth", env={}, **kwargs2)
    assert seams2.lightrag_api_key is None
