"""PN02D-B0C-B — per-adapter HTTP/contract tests + the §53 failure matrix.

EVALUATION-ONLY. Every adapter is exercised against a mock httpx transport / fake DB /
fake embedder — ZERO provider traffic, ZERO normal-DB access.
"""

from __future__ import annotations

import inspect
import json

import graphrag_pn02db0cb_common as C
import httpx
import pytest

from open_notebook.integrations.graphrag.config import GraphRAGConfig
from open_notebook.integrations.graphrag.eval.attestpn02d import (
    mint_real_preflight_authorization,
)
from open_notebook.integrations.graphrag.eval.authlivepn02d import (
    ProviderRunNotAuthorized,
    RunIdConsistencyError,
)
from open_notebook.integrations.graphrag.eval.authmintlivepn02d import (
    B1R2CheckpointError,
    GitBaselineError,
    LiveProviderRunAuthorizationError,
    LiveProviderRunNotAuthorized,
    OperatorGrantError,
    OperatorRunGrant,
    RealTrustedB1R2Reader,
    TrustedB1R2Observation,
    current_approved_b1_r2_checkpoint,
    mint_live_provider_run_authorization,
    require_live_provider_run_authorization,
    verify_b1_r2_checkpoint,
)
from open_notebook.integrations.graphrag.eval.deleteadapterpn02d import (
    RealDeleteAdapterError,
    RealPN02DeleteBackend,
    build_real_delete_backend_factory,
)
from open_notebook.integrations.graphrag.eval.driver_live_pn02d import RealB1Driver
from open_notebook.integrations.graphrag.eval.driverpn02d import (
    build_simulation_provider_run_authorization,
)
from open_notebook.integrations.graphrag.eval.gdadapterpn02d import (
    RealGDAdapterError,
    RealPN02GDBackend,
    build_real_gd_backend_factory,
)
from open_notebook.integrations.graphrag.eval.gdlivepn02d import (
    GDBackendError,
    GDQueryExecutor,
)
from open_notebook.integrations.graphrag.eval.indexadapterpn02d import (
    RealIndexAdapterError,
    RealPN02IndexClient,
    build_real_index_client_factory,
    cell_endpoint_for_route,
)
from open_notebook.integrations.graphrag.eval.live_indexer08 import CellEndpoint
from open_notebook.integrations.graphrag.eval.normalizepn02 import normalize_graph
from open_notebook.integrations.graphrag.eval.routelivepn02d import (
    PN02Router,
    attest_route,
    build_route_table,
)
from open_notebook.integrations.graphrag.eval.vectoradapterpn02d import (
    ActiveEmbeddingModelAttestation,
    ActiveEmbeddingModelMismatch,
    RealPN02VectorBackend,
    build_real_vector_backend_factory,
    cosine_similarity,
)


def _endpoint(base_url="http://127.0.0.1:9621"):
    return CellEndpoint(
        run_id="", cell_id="NB_A", base_url=base_url, port=9621,
        workspace="ws", storage_dir="s", container_identity="c",
    )


def _capturing_transport(handler):
    captured = []

    def _h(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return handler(request)

    return httpx.MockTransport(_h), captured


# --------------------------------------------------------------------------- #
# Index adapter — HTTP contract + fixture-key guard (§17/§18/§49)
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_index_submit_wire_contract():
    def handler(req):
        return httpx.Response(200, json={"status": "success", "track_id": "t1", "message": "ok"})

    transport, captured = _capturing_transport(handler)
    client = RealPN02IndexClient(
        _endpoint(), allowed_source_keys={"A1"}, api_key="secret-key", transport=transport
    )
    result = await client.submit(source_id="A1", canonical_text="synthetic text")
    assert result.accepted is True
    assert result.track_id == "t1"
    req = captured[0]
    assert req.method == "POST"
    assert req.url.path == "/documents/text"
    body = json.loads(req.content)
    assert body == {"text": "synthetic text", "file_source": "A1"}
    assert req.headers.get("X-API-Key") == "secret-key"


@pytest.mark.asyncio
async def test_index_status_maps_states():
    states = {"cur": "processed"}

    def handler(req):
        return httpx.Response(
            200, json={"documents": [{"status": states["cur"]}], "total_count": 1}
        )

    transport, _ = _capturing_transport(handler)
    client = RealPN02IndexClient(
        _endpoint(), allowed_source_keys={"A1"}, transport=transport
    )
    assert (await client.status(track_id="t")).state == "PROCESSED"
    states["cur"] = "pending"
    assert (await client.status(track_id="t")).state == "IN_PROGRESS"
    states["cur"] = "failed"
    assert (await client.status(track_id="t")).state == "FAILED"


@pytest.mark.asyncio
async def test_index_fixture_key_guard_rejects_unknown_key():
    def handler(req):  # pragma: no cover - never reached; submit rejected pre-wire
        return httpx.Response(200, json={"status": "success", "track_id": "t"})

    transport, captured = _capturing_transport(handler)
    client = RealPN02IndexClient(
        _endpoint(), allowed_source_keys={"A1", "A2"}, transport=transport
    )
    # Arbitrary file_source, ON record ids, paths, URLs all rejected before HTTP (M4).
    for bad in ("NOT_A_KEY", "source:A1", "../../secret", "https://internal/doc"):
        result = await client.submit(source_id=bad, canonical_text="x")
        assert result.accepted is False
    assert captured == []  # no HTTP request was ever issued


@pytest.mark.asyncio
async def test_index_client_requires_mandatory_allowlist():
    # B0CB-M4: the real PN02 index client cannot be constructed without a fixture-key
    # allowlist (no guard-less real mode).
    transport, _ = _capturing_transport(lambda req: httpx.Response(200, json={}))
    with pytest.raises(RealIndexAdapterError):
        RealPN02IndexClient(_endpoint(), allowed_source_keys=set(), transport=transport)


def test_index_factory_requires_mandatory_allowlist():
    live = C.mint_test_live_auth()
    with pytest.raises(RealIndexAdapterError):
        build_real_index_client_factory(live_auth=live, allowed_source_keys=set())


def test_cell_endpoint_for_route_rejects_placeholder():
    fx = C.fixture()
    route = PN02Router(build_route_table(fx)).route_for("NB_A")  # eval-null:// endpoint
    with pytest.raises(RealIndexAdapterError):
        cell_endpoint_for_route(route)


# --------------------------------------------------------------------------- #
# GD adapter — HTTP contract + error mapping + provenance (§22/§24/§49)
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_gd_query_wire_contract_only_need_context():
    def handler(req):
        return httpx.Response(
            200, json={"data": {"chunks": [{"file_path": "A1"}], "references": []}}
        )

    transport, captured = _capturing_transport(handler)
    backend = RealPN02GDBackend(
        GraphRAGConfig(enabled=True, base_url="http://127.0.0.1:9621", timeout=5.0, api_key="k"),
        transport=transport,
    )
    result = await backend.query_evidence("question", benchmark_ids={"A1", "A2"})
    assert list(result.candidate_source_ids) == ["A1"]
    req = captured[0]
    assert req.method == "POST"
    assert req.url.path == "/query/data"
    body = json.loads(req.content)
    assert body["only_need_context"] is True
    assert body["mode"] == "hybrid"
    assert req.headers.get("X-API-Key") == "k"


@pytest.mark.asyncio
async def test_gd_http_500_maps_to_backend_error():
    def handler(req):
        return httpx.Response(500, json={"detail": "boom"})

    transport, _ = _capturing_transport(handler)
    backend = RealPN02GDBackend(
        GraphRAGConfig(enabled=True, base_url="http://127.0.0.1:9621", timeout=5.0, api_key=None),
        transport=transport,
    )
    with pytest.raises(GDBackendError):
        await backend.query_evidence("q", benchmark_ids={"A1"})


@pytest.mark.asyncio
async def test_gd_malformed_response_maps_to_backend_error():
    def handler(req):
        return httpx.Response(200, content=b"not-json")

    transport, _ = _capturing_transport(handler)
    backend = RealPN02GDBackend(
        GraphRAGConfig(enabled=True, base_url="http://127.0.0.1:9621", timeout=5.0, api_key=None),
        transport=transport,
    )
    with pytest.raises(GDBackendError):
        await backend.query_evidence("q", benchmark_ids={"A1"})


@pytest.mark.asyncio
async def test_gd_network_denial_maps_to_backend_error():
    def handler(req):
        raise httpx.ConnectError("no network")

    transport, _ = _capturing_transport(handler)
    backend = RealPN02GDBackend(
        GraphRAGConfig(enabled=True, base_url="http://127.0.0.1:9621", timeout=5.0, api_key=None),
        transport=transport,
    )
    with pytest.raises(GDBackendError):
        await backend.query_evidence("q", benchmark_ids={"A1"})


@pytest.mark.asyncio
async def test_gd_foreign_provenance_dropped_by_normalizer():
    def handler(req):
        return httpx.Response(
            200,
            json={"data": {"chunks": [{"file_path": "A1"}, {"file_path": "FOREIGN"}]}},
        )

    transport, _ = _capturing_transport(handler)
    backend = RealPN02GDBackend(
        GraphRAGConfig(enabled=True, base_url="http://127.0.0.1:9621", timeout=5.0, api_key=None),
        transport=transport,
    )
    # The backend returns the RAW STRONG anchors (incl. the foreign id); the PN02
    # executor's normalize_graph(allowlist=fixture.source_keys) drops foreign (§12).
    result = await backend.query_evidence("q", benchmark_ids={"A1"})
    assert "A1" in result.candidate_source_ids
    evidence = normalize_graph(list(result.candidate_source_ids), allowlist={"A1"})
    assert evidence.as_set() == frozenset({"A1"})  # FOREIGN dropped by the normalizer
    assert evidence.stats.foreign == 1


# --------------------------------------------------------------------------- #
# Vector adapter — Approach B, adversarial global-top-K, dim/model (§25-§29)
# --------------------------------------------------------------------------- #

def _vector_backend(embeddings, *, embed_vec, attestor=None):
    async def _embed(_q):
        return list(embed_vec)

    async def _fetch(record_ids, _q):
        # Honours WHERE source IN $ids — only requested ids returned.
        return [(r, embeddings[r]) for r in record_ids if r in embeddings]

    async def _default_attestor():
        return ActiveEmbeddingModelAttestation("openrouter", "openai/text-embedding-3-small", C.EMBED_DIM)

    return RealPN02VectorBackend(
        member_id_resolver=C.record_id_for_key,
        query_embed_fn=_embed,
        member_row_fetcher=_fetch,
        model_attestor=attestor or _default_attestor,
    )


def _basis(i):
    v = [0.0] * C.EMBED_DIM
    v[i] = 1.0
    return v


@pytest.mark.asyncio
async def test_vector_ranks_members_by_cosine():
    embeddings = {
        C.record_id_for_key("A1"): _basis(0),
        C.record_id_for_key("A2"): _basis(1),
    }
    backend = _vector_backend(embeddings, embed_vec=_basis(0))  # query == A1
    emb = await backend.embed_query("q")
    ranked = await backend.rank_members(query_embedding=emb, candidate_source_ids=["A1", "A2"])
    assert ranked[0] == "A1"
    assert set(ranked) == {"A1", "A2"}


@pytest.mark.asyncio
async def test_vector_adversarial_foreign_never_fetched():
    # A foreign record has an IDENTICAL-to-query embedding (would dominate a global top-K),
    # but it is not a member → never fetched → never ranked.
    embeddings = {
        C.record_id_for_key("A1"): _basis(0),
        C.record_id_for_key("A2"): _basis(1),
        "source:FOREIGN": _basis(0),  # cosine 1.0 vs the query
    }
    fetched_ids = {}

    async def _embed(_q):
        return _basis(0)

    async def _fetch(record_ids, _q):
        fetched_ids["ids"] = list(record_ids)
        return [(r, embeddings[r]) for r in record_ids if r in embeddings]

    async def _attest():
        return ActiveEmbeddingModelAttestation("openrouter", "openai/text-embedding-3-small", C.EMBED_DIM)

    backend = RealPN02VectorBackend(
        member_id_resolver=C.record_id_for_key,
        query_embed_fn=_embed,
        member_row_fetcher=_fetch,
        model_attestor=_attest,
    )
    emb = await backend.embed_query("q")
    ranked = await backend.rank_members(query_embedding=emb, candidate_source_ids=["A1", "A2"])
    assert "FOREIGN" not in ranked
    assert "source:FOREIGN" not in ranked
    # The fetcher was asked ONLY for member record ids (never the global corpus).
    assert set(fetched_ids["ids"]) == {C.record_id_for_key("A1"), C.record_id_for_key("A2")}


@pytest.mark.asyncio
async def test_vector_dimension_mismatch_fails_before_ranking():
    backend = _vector_backend({}, embed_vec=[1.0, 2.0, 3.0])  # wrong dim
    with pytest.raises(ActiveEmbeddingModelMismatch):
        await backend.embed_query("q")


@pytest.mark.asyncio
async def test_vector_model_mismatch_fails_before_embed(monkeypatch):
    async def _bad_attest():
        return ActiveEmbeddingModelAttestation("ollama", "nomic-embed", 768)

    calls = {"embed": 0}

    async def _embed(_q):
        calls["embed"] += 1
        return _basis(0)

    async def _fetch(record_ids, _q):
        return []

    backend = RealPN02VectorBackend(
        member_id_resolver=C.record_id_for_key,
        query_embed_fn=_embed,
        member_row_fetcher=_fetch,
        model_attestor=_bad_attest,
    )
    with pytest.raises(ActiveEmbeddingModelMismatch):
        await backend.embed_query("q")
    assert calls["embed"] == 0  # attested BEFORE the provider embedding


def test_cosine_parity():
    a = [1.0, 2.0, 3.0]
    b = [2.0, 4.0, 6.0]
    assert cosine_similarity(a, b) == pytest.approx(1.0)
    assert cosine_similarity([1.0, 0.0], [0.0, 1.0]) == pytest.approx(0.0)


# --------------------------------------------------------------------------- #
# Delete adapter — state mapping + shared-source isolation (§35/§36/§37)
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status,expected",
    [
        ("deletion_started", True),
        ("not_found", True),
        ("busy", False),
        ("not_allowed", False),
        ("weird_unexpected", False),
    ],
)
async def test_delete_state_mapping(status, expected):
    def handler(req):
        return httpx.Response(200, json={"status": status, "message": "m"})

    transport, _ = _capturing_transport(handler)
    backend = RealPN02DeleteBackend(
        GraphRAGConfig(enabled=True, base_url="http://127.0.0.1:9621", timeout=5.0, api_key=None),
        transport=transport,
    )
    result = await backend.delete_document(derived_document_id="doc-abc")
    assert result.succeeded is expected


@pytest.mark.asyncio
async def test_delete_wire_contract():
    def handler(req):
        return httpx.Response(200, json={"status": "deletion_started"})

    transport, captured = _capturing_transport(handler)
    backend = RealPN02DeleteBackend(
        GraphRAGConfig(enabled=True, base_url="http://127.0.0.1:9621", timeout=5.0, api_key="k"),
        transport=transport,
    )
    await backend.delete_document(derived_document_id="doc-xyz")
    req = captured[0]
    assert req.method == "DELETE"
    assert req.url.path == "/documents/delete_document"
    assert json.loads(req.content) == {"doc_ids": ["doc-xyz"]}


@pytest.mark.asyncio
async def test_shared_source_delete_isolation():
    # SH_AB deleted at NB_A's endpoint only; NB_B's GD still returns SH_AB (§37).
    fx = C.fixture()
    base_a = "http://127.0.0.1:40001"
    base_b = "http://127.0.0.1:40002"
    transport = C.build_mock_transport(fx, {base_a: "NB_A", base_b: "NB_B"})
    from open_notebook.integrations.graphrag.eval.docidpn02d import (
        compute_derived_document_id,
    )

    # Delete SH_AB at NB_A ONLY.
    del_a = RealPN02DeleteBackend(
        GraphRAGConfig(enabled=True, base_url=base_a, timeout=5.0, api_key=None),
        transport=transport,
    )
    res = await del_a.delete_document(
        derived_document_id=compute_derived_document_id("SH_AB")
    )
    assert res.succeeded is True

    # Probe both notebooks with a SHARED_SOURCE question referencing SH_AB.
    sh_query = next(
        q for q in fx.queries
        if q.notebook_id == "NB_A" and "SH_AB" in q.required_source_ids
    )
    gd_a = RealPN02GDBackend(
        GraphRAGConfig(enabled=True, base_url=base_a, timeout=5.0, api_key=None),
        transport=transport,
    )
    gd_b = RealPN02GDBackend(
        GraphRAGConfig(enabled=True, base_url=base_b, timeout=5.0, api_key=None),
        transport=transport,
    )
    a_after = await gd_a.query_evidence(sh_query.question, benchmark_ids=fx.source_keys)
    b_after = await gd_b.query_evidence(sh_query.question, benchmark_ids=fx.source_keys)
    assert "SH_AB" not in a_after.candidate_source_ids  # NB_A lost it
    assert "SH_AB" in b_after.candidate_source_ids  # NB_B kept it


# --------------------------------------------------------------------------- #
# Auth mint / live-capability negatives (§13/§14/§16/§60)
# --------------------------------------------------------------------------- #

def _preflight(run_id=C.TEST_RUN_ID):
    ok, detail = C.verify_fixture_hash()
    return mint_real_preflight_authorization(
        gate0_passed=True, gate1_passed=True,
        fixture_hash=detail if ok else "UNVERIFIED", run_id=run_id, runtime_count=3,
    )


def test_mint_live_auth_happy_path():
    auth = C.mint_test_live_auth()
    # exposes the underlying capability the shared executors consume
    assert auth.provider_run_authorization.run_id == C.TEST_RUN_ID
    assert require_live_provider_run_authorization(auth) is auth


def _fixture_hash():
    ok, detail = C.verify_fixture_hash()
    return detail if ok else "UNVERIFIED"


def test_mint_rejects_dict_lookalike_grant():
    with pytest.raises(OperatorGrantError):
        mint_live_provider_run_authorization(
            operator_grant={"run_id": "x"},  # type: ignore[arg-type]
            real_preflight_auth=_preflight(),
            git_baseline_attestation=C.clean_git_baseline(),
            observed_fixture_hash=_fixture_hash(),
        )


def test_mint_rejects_simulation_baseline():
    grant = C.frozen_test_grant(commit="SIMULATION", tag="SIMULATION")
    with pytest.raises(LiveProviderRunAuthorizationError):
        mint_live_provider_run_authorization(
            operator_grant=grant, real_preflight_auth=_preflight(),
            git_baseline_attestation=C.clean_git_baseline(commit="SIMULATION", tag="SIMULATION"),
            observed_fixture_hash=_fixture_hash(),
        )


def test_mint_rejects_run_id_mismatch():
    grant = C.frozen_test_grant(run_id="grant-run")
    with pytest.raises(LiveProviderRunAuthorizationError):
        mint_live_provider_run_authorization(
            operator_grant=grant, real_preflight_auth=_preflight(run_id="different-run"),
            git_baseline_attestation=C.clean_git_baseline(),
            observed_fixture_hash=_fixture_hash(),
        )


# --- B0CB-H1: full fixture-identity chain (preflight cap bound to the fixture) ------

def test_mint_rejects_preflight_minted_for_wrong_fixture():
    # H1: a real-preflight capability minted for ANOTHER fixture (same run_id) must not
    # authorize a run for the frozen fixture.
    grant = C.frozen_test_grant()
    wrong_preflight = mint_real_preflight_authorization(
        gate0_passed=True, gate1_passed=True,
        fixture_hash="deadbeef" * 8, run_id=C.TEST_RUN_ID, runtime_count=3,
    )
    with pytest.raises(LiveProviderRunAuthorizationError):
        mint_live_provider_run_authorization(
            operator_grant=grant, real_preflight_auth=wrong_preflight,
            git_baseline_attestation=C.clean_git_baseline(),
            observed_fixture_hash=_fixture_hash(),
        )


def test_mint_rejects_wrong_observed_fixture_hash():
    grant = C.frozen_test_grant()
    with pytest.raises(LiveProviderRunAuthorizationError):
        mint_live_provider_run_authorization(
            operator_grant=grant, real_preflight_auth=_preflight(),
            git_baseline_attestation=C.clean_git_baseline(),
            observed_fixture_hash="deadbeef" * 8,
        )


def test_mint_rejects_wrong_grant_fixture_hash():
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
    with pytest.raises(LiveProviderRunAuthorizationError):
        mint_live_provider_run_authorization(
            operator_grant=bad, real_preflight_auth=_preflight(),
            git_baseline_attestation=C.clean_git_baseline(),
            observed_fixture_hash=_fixture_hash(),
        )


# --- B0CB-H2: clean + approved git baseline attestation -----------------------------

def test_mint_rejects_dirty_worktree():
    grant = C.frozen_test_grant()
    with pytest.raises(GitBaselineError):
        mint_live_provider_run_authorization(
            operator_grant=grant, real_preflight_auth=_preflight(),
            git_baseline_attestation=C.dirty_git_baseline(),
            observed_fixture_hash=_fixture_hash(),
        )


def test_mint_rejects_baseline_mismatch_even_when_clean():
    grant = C.frozen_test_grant()
    with pytest.raises(GitBaselineError):
        mint_live_provider_run_authorization(
            operator_grant=grant, real_preflight_auth=_preflight(),
            git_baseline_attestation=C.clean_git_baseline(commit="OTHER_COMMIT"),
            observed_fixture_hash=_fixture_hash(),
        )


def test_mint_rejects_bare_boolean_baseline():
    # A caller-supplied boolean is NOT a valid baseline attestation (H2).
    grant = C.frozen_test_grant()
    with pytest.raises(GitBaselineError):
        mint_live_provider_run_authorization(
            operator_grant=grant, real_preflight_auth=_preflight(),
            git_baseline_attestation=True,  # type: ignore[arg-type]
            observed_fixture_hash=_fixture_hash(),
        )


def test_mint_rejects_wrong_provider_fingerprint():
    grant = C.frozen_test_grant()
    bad = OperatorRunGrant(
        run_id=grant.run_id, fixture_hash=grant.fixture_hash,
        implementation_checkpoint_commit=grant.implementation_checkpoint_commit,
        implementation_checkpoint_tag=grant.implementation_checkpoint_tag,
        b1_r2_checkpoint=grant.b1_r2_checkpoint,
        provider_config_fingerprint="pbf_wrong0000000000000000",
        workload_caps=grant.workload_caps, operation_allowlist=grant.operation_allowlist,
        approved_git_commit=grant.approved_git_commit, approved_git_tag=grant.approved_git_tag,
    )
    # The fingerprint check is AFTER the B1-R2 gate, so patch governance so 6b passes and
    # the mint reaches (and fails at) the provider-fingerprint check.
    with C.approved_b1r2_governance(), pytest.raises(LiveProviderRunAuthorizationError):
        mint_live_provider_run_authorization(
            operator_grant=bad, real_preflight_auth=_preflight(),
            git_baseline_attestation=C.clean_git_baseline(),
            observed_fixture_hash=_fixture_hash(),
        )


def test_require_live_auth_rejects_simulation_auth_and_none():
    # a plain (simulation) PN02ProviderRunAuthorization is the WRONG type for a live seam
    sim = build_simulation_provider_run_authorization(run_id="sim")
    with pytest.raises(LiveProviderRunNotAuthorized):
        require_live_provider_run_authorization(sim)
    with pytest.raises(LiveProviderRunNotAuthorized):
        require_live_provider_run_authorization(None)
    with pytest.raises(LiveProviderRunNotAuthorized):
        require_live_provider_run_authorization({"run_id": "x"})


# --------------------------------------------------------------------------- #
# run_id (L-1) + capability-type (L-2) hardening on the shared executors
# --------------------------------------------------------------------------- #

def _gd_executor(fx, *, provider_run_auth, query_auth):
    router = PN02Router(build_route_table(fx))
    return GDQueryExecutor(
        router=router, budget=__import__(
            "open_notebook.integrations.graphrag.eval.budgetlivepn02d",
            fromlist=["StatefulBudgetGuard"],
        ).StatefulBudgetGuard(),
        provider_run_auth=provider_run_auth, query_auth=query_auth,
        attestations={nb: attest_route(router.route_for(nb)) for nb in router.notebook_ids()},
        backend_factory=lambda r: None, source_allowlist=fx.source_keys,
    )


def test_executor_run_id_cross_check_rejects_mismatch():
    from open_notebook.integrations.graphrag.eval.authlivepn02d import (
        mint_indexing_authorization,
        mint_query_authorization,
    )

    fx = C.fixture()
    auth = build_simulation_provider_run_authorization(run_id="r1")
    ia = mint_indexing_authorization(auth, binding_attested=True, run_id="r2")
    qa = mint_query_authorization(ia, indexed_memberships=24, planned_memberships=24, run_id="r2")
    with pytest.raises(RunIdConsistencyError):
        _gd_executor(fx, provider_run_auth=auth, query_auth=qa)


def test_executor_capability_type_rejects_lookalike():
    from open_notebook.integrations.graphrag.eval.authlivepn02d import (
        mint_indexing_authorization,
        mint_query_authorization,
    )

    fx = C.fixture()
    auth = build_simulation_provider_run_authorization(run_id="r1")
    ia = mint_indexing_authorization(auth, binding_attested=True, run_id="r1")
    qa = mint_query_authorization(ia, indexed_memberships=24, planned_memberships=24, run_id="r1")

    class _Lookalike:
        run_id = "r1"
        allowed_operation_classes = frozenset()

    with pytest.raises(ProviderRunNotAuthorized):
        _gd_executor(fx, provider_run_auth=_Lookalike(), query_auth=qa)


# --------------------------------------------------------------------------- #
# Factories reject a non-live capability (§12/§16)
# --------------------------------------------------------------------------- #

def test_factories_reject_simulation_auth():
    sim = build_simulation_provider_run_authorization(run_id="sim")
    for builder in (
        build_real_gd_backend_factory,
        build_real_delete_backend_factory,
    ):
        with pytest.raises(LiveProviderRunNotAuthorized):
            builder(live_auth=sim)
    with pytest.raises(LiveProviderRunNotAuthorized):
        build_real_vector_backend_factory(
            live_auth=sim, member_id_resolver=lambda k: k,
            query_embed_fn=None, member_row_fetcher=None, model_attestor=None,
        )


def test_gd_delete_factories_reject_placeholder_endpoint():
    fx = C.fixture()
    live = C.mint_test_live_auth()
    route = PN02Router(build_route_table(fx)).route_for("NB_A")  # eval-null://
    gd_factory = build_real_gd_backend_factory(live_auth=live)
    del_factory = build_real_delete_backend_factory(live_auth=live)
    with pytest.raises(RealGDAdapterError):
        gd_factory(route)
    with pytest.raises(RealDeleteAdapterError):
        del_factory(route)


# --------------------------------------------------------------------------- #
# Corpus budget exhaustion (§33)
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_corpus_budget_exhaustion():
    from open_notebook.integrations.graphrag.eval.corpuslivepn02d import (
        CorpusBudgetExceeded,
        CorpusEmbeddingBudget,
    )

    budget = CorpusEmbeddingBudget(cap=2)
    budget.reserve(1)
    budget.reserve(1)
    with pytest.raises(CorpusBudgetExceeded):
        budget.reserve(1)


@pytest.mark.asyncio
async def test_corpus_provisioner_rejects_non_live_auth():
    from open_notebook.integrations.graphrag.eval.corpuslivepn02d import (
        RealPN02CorpusProvisioner,
    )

    fx = C.fixture()
    sim = build_simulation_provider_run_authorization(run_id="sim")

    async def _noop(*a, **k):
        return ""

    with pytest.raises(LiveProviderRunNotAuthorized):
        RealPN02CorpusProvisioner(
            fx, live_auth=sim, source_creator=_noop, reference_linker=_noop,
            source_embedder=_noop, notebook_record_ids={},
        )


# --------------------------------------------------------------------------- #
# B0CB-RR4-H1: the mint OWNS the trusted read — NO caller trust-root injection
# --------------------------------------------------------------------------- #
#
# Trust flow (task §3/§4/§5): the mint resolves BOTH trust roots (the governance-approved
# identity + the real-Git reader) INTERNALLY via ``b1_r2_refusal_reasons``. Neither the
# mint nor ``RealB1Driver.run`` exposes a trust-root parameter. These tests exercise the
# REAL mint boundary directly (bypassing the CLI). Where a test must drive a specific
# validator branch it calls the pure validator ``verify_b1_r2_checkpoint`` (which returns
# reasons and CANNOT mint), and the future-positive path patches the mint's INTERNAL
# governance + reader (never a public parameter).

#: The real current repository HEAD + the real B0C-A design tag (peels to that HEAD).
REAL_HEAD = "cb766883f6421e5c692317f286e2ff071bf82fe8"
B0CA_TAG = "graphrag-pn02db0ca-real-provider-wiring-design-approved"


def _mint_default(*, grant=None, baseline=None):
    """Call the PRODUCTION mint signature (NO trust-root params). Default governance is
    ``None`` (NOT_STARTED) so this fails closed unless wrapped in a governance patch."""
    grant = grant if grant is not None else C.frozen_test_grant()
    return mint_live_provider_run_authorization(
        operator_grant=grant,
        real_preflight_auth=_preflight(),
        git_baseline_attestation=(baseline if baseline is not None else C.clean_git_baseline()),
        observed_fixture_hash=_fixture_hash(),
    )


class _LookalikeReader:
    """A reader whose ``observe`` returns a look-alike object (NOT a TrustedB1R2Observation)."""

    def __init__(self, tag=C.TEST_B1R2_TAG):
        self._tag = tag

    def observe(self, checkpoint_tag):
        from types import SimpleNamespace

        return SimpleNamespace(
            checkpoint_tag=self._tag, observed_tag_exists=True,
            observed_tag_peel=C.TEST_COMMIT, observed_head=C.TEST_COMMIT,
        )


class _WrongTagReader:
    """Returns a GENUINE trusted observation for a DIFFERENT tag than requested."""

    def __init__(self, other_tag, head=C.TEST_COMMIT):
        self._inner = C.b1r2_reader_ok(tag=other_tag, peel=head, head=head)
        self._other = other_tag

    def observe(self, checkpoint_tag):
        return self._inner.observe(self._other)  # ignores the asked tag


# --- §16 type hardening: the trusted observation is unforgeable -------------- #

def test_trusted_observation_cannot_be_constructed_directly():
    # A self-constructed public object can NEVER be a trusted observation (§7/§16).
    with pytest.raises(PermissionError):
        TrustedB1R2Observation(
            object(), checkpoint_tag=C.TEST_B1R2_TAG, observed_tag_exists=True,
            observed_tag_peel=C.TEST_COMMIT, observed_head=C.TEST_COMMIT,
        )
    with pytest.raises(TypeError):
        TrustedB1R2Observation(  # missing the capability key entirely
            checkpoint_tag=C.TEST_B1R2_TAG, observed_tag_exists=True,  # type: ignore[call-arg]
            observed_tag_peel=C.TEST_COMMIT, observed_head=C.TEST_COMMIT,
        )


def test_current_approved_b1_r2_checkpoint_is_expected_tag():
    # Governance (post PN02D-B1-R2 §7): the approved identity is now FROZEN to the exact
    # expected B1-R2 tag. It is non-None, but the tag does not exist in Git yet, so the
    # mint still fails closed (see the direct-mint tests). No caller can override it.
    from open_notebook.integrations.graphrag.eval.authmintlivepn02d import (
        EXPECTED_B1_R2_CHECKPOINT_TAG,
    )

    assert current_approved_b1_r2_checkpoint() == EXPECTED_B1_R2_CHECKPOINT_TAG


# --- §4/§5/§15/§16: NO trust-root injection on the public/live APIs ---------- #

def test_mint_signature_has_no_trust_root_parameters():
    # §4: the production mint signature exposes neither trust root.
    params = inspect.signature(mint_live_provider_run_authorization).parameters
    assert "trusted_b1_r2_reader" not in params
    assert "approved_expected_b1_r2_checkpoint" not in params
    assert "b1_r2_checkpoint_attestation" not in params


def test_mint_rejects_old_trust_injection_keywords():
    # §15: the OLD injection keywords are rejected by the production mint signature.
    base = dict(
        operator_grant=C.frozen_test_grant(),
        real_preflight_auth=_preflight(),
        git_baseline_attestation=C.clean_git_baseline(),
        observed_fixture_hash=_fixture_hash(),
    )
    for bad in (
        {"trusted_b1_r2_reader": C.b1r2_reader_ok()},
        {"approved_expected_b1_r2_checkpoint": C.TEST_B1R2_TAG},
        {"b1_r2_checkpoint_attestation": object()},
    ):
        with pytest.raises(TypeError):
            mint_live_provider_run_authorization(**base, **bad)  # type: ignore[arg-type]


def test_real_b1_driver_run_signature_has_no_trust_root_parameters():
    # §5/§16: RealB1Driver.run exposes neither trust root (no reader, no approved id).
    params = inspect.signature(RealB1Driver.run).parameters
    assert "trusted_b1_r2_reader" not in params
    assert "approved_expected_b1_r2_checkpoint" not in params
    assert "b1_r2_checkpoint_attestation" not in params


@pytest.mark.parametrize(
    "legacy_kwarg",
    ["trusted_b1_r2_reader", "approved_expected_b1_r2_checkpoint", "b1_r2_checkpoint_attestation"],
)
def test_real_b1_driver_run_rejects_legacy_trust_kwargs_at_runtime(legacy_kwarg):
    # B0CB-RR5-L1: an EXPLICIT runtime negative test (not just a signature assertion).
    # Passing a removed trust-root keyword to the REAL RealB1Driver.run callable — alongside
    # the valid required arguments — fails at CALL BINDING with TypeError, BEFORE any
    # coroutine runs (so no mint, no provider call, no runtime boot, no backend/DB effect).
    fx = C.fixture()
    driver = RealB1Driver(fx, C.build_live_seams(fx).seams)
    with pytest.raises(TypeError):
        # no await: binding raises TypeError before a coroutine object is even created
        driver.run(
            operator_grant=C.frozen_test_grant(),
            git_baseline_attestation=C.clean_git_baseline(),
            **{legacy_kwarg: object()},  # type: ignore[arg-type]
        )


# --- §10: a grant's B1-R2 identity must equal the governance-approved identity ---- #
# These are CHECKPOINT-LIFECYCLE robust: the load-bearing property is that a grant whose
# B1-R2 identity differs from the governance-frozen approved identity is rejected
# (`b1_r2_grant_identity_mismatch`) — independent of whether the real approved tag exists
# in Git yet. (The default test grant uses the synthetic C.TEST_B1R2_TAG, which never
# equals the real EXPECTED governance identity.)

def test_direct_mint_default_grant_identity_rejected():
    # A default grant (synthetic B1-R2 identity) does not match the governance-approved
    # identity → fail closed. Stays true before and after the real B1-R2 tag is created.
    with pytest.raises(B1R2CheckpointError) as ei:
        _mint_default()
    assert "b1_r2_grant_identity_mismatch" in str(ei.value)


def test_direct_mint_b0ca_in_grant_fails_closed():
    # §20.2: B0C-A named in the grant cannot substitute — it does not equal the governance
    # approved B1-R2 identity, so the grant identity is rejected.
    with pytest.raises(B1R2CheckpointError) as ei:
        _mint_default(grant=C.frozen_test_grant(b1_r2_checkpoint=B0CA_TAG))
    assert "b1_r2_grant_identity_mismatch" in str(ei.value)


def test_direct_mint_arbitrary_real_tag_in_grant_fails_closed():
    # §20.4: an arbitrary real tag named in the grant cannot authorize — it does not equal
    # the governance approved B1-R2 identity, so the grant identity is rejected.
    with pytest.raises(B1R2CheckpointError) as ei:
        _mint_default(grant=C.frozen_test_grant(b1_r2_checkpoint="graphrag-05-forensic-approved"))
    assert "b1_r2_grant_identity_mismatch" in str(ei.value)


def test_caller_controlled_reader_attack_has_no_public_seam():
    # §12: the Codex-#4 attack (caller passes a scripted RealTrustedB1R2Reader + approved
    # string) has NO public entry. The old kwargs are rejected, and the default path (real
    # governance + real reader) fails closed even though the B0C-A tag genuinely sits at
    # the real current HEAD.
    with pytest.raises(TypeError):
        mint_live_provider_run_authorization(
            operator_grant=C.frozen_test_grant(b1_r2_checkpoint=B0CA_TAG),
            real_preflight_auth=_preflight(),
            git_baseline_attestation=C.clean_git_baseline(),
            observed_fixture_hash=_fixture_hash(),
            trusted_b1_r2_reader=C.b1r2_reader_ok(tag=B0CA_TAG, peel=REAL_HEAD, head=REAL_HEAD),
            approved_expected_b1_r2_checkpoint=B0CA_TAG,  # type: ignore[call-arg]
        )
    with pytest.raises(B1R2CheckpointError):
        _mint_default(grant=C.frozen_test_grant(b1_r2_checkpoint=B0CA_TAG))


# --- validator-level adversarial cases (verify_b1_r2_checkpoint cannot mint) -- #

def test_b1r2_b0ca_rejected_even_when_supplied_as_approved():
    # §11: even if the approved-expected identity were B0C-A (which peels to the real HEAD),
    # the safety denylist rejects it — B0C-A can never be a B1-R2 checkpoint.
    reasons = verify_b1_r2_checkpoint(
        reader=RealTrustedB1R2Reader(),  # real git: B0C-A truly exists at HEAD
        operator_grant=C.frozen_test_grant(b1_r2_checkpoint=B0CA_TAG),
        approved_expected_checkpoint=B0CA_TAG,
        git_baseline=C.clean_git_baseline(commit=REAL_HEAD, tag=B0CA_TAG),
    )
    assert "b1_r2_approved_identity_is_known_non_b1_r2_checkpoint" in reasons


def test_b1r2_reader_observed_wrong_tag_rejected():
    reasons = verify_b1_r2_checkpoint(
        reader=_WrongTagReader(other_tag="graphrag-pn02db1r2-decoy"),
        operator_grant=C.frozen_test_grant(b1_r2_checkpoint=C.TEST_B1R2_TAG),
        approved_expected_checkpoint=C.TEST_B1R2_TAG, git_baseline=C.clean_git_baseline(),
    )
    assert "b1_r2_reader_observed_wrong_tag" in reasons


def test_b1r2_correct_tag_wrong_peel_rejected():
    reasons = verify_b1_r2_checkpoint(
        reader=C.b1r2_reader_ok(tag=C.TEST_B1R2_TAG, peel="d" * 40, head=C.TEST_COMMIT),
        operator_grant=C.frozen_test_grant(),
        approved_expected_checkpoint=C.TEST_B1R2_TAG, git_baseline=C.clean_git_baseline(),
    )
    assert "b1_r2_tag_not_at_authorized_head" in reasons


def test_b1r2_correct_peel_wrong_baseline_head_rejected():
    # §12/§10: reader observes the tag at HEAD X, but the approved git baseline is at Y.
    other = "e" * 40
    reasons = verify_b1_r2_checkpoint(
        reader=C.b1r2_reader_ok(tag=C.TEST_B1R2_TAG, peel=other, head=other),
        operator_grant=C.frozen_test_grant(),
        approved_expected_checkpoint=C.TEST_B1R2_TAG,
        git_baseline=C.clean_git_baseline(),  # head_commit == TEST_COMMIT != other
    )
    assert "b1_r2_head_not_bound_to_approved_baseline" in reasons


def test_b1r2_reader_observes_no_tag_rejected():
    reasons = verify_b1_r2_checkpoint(
        reader=RealTrustedB1R2Reader(
            git_runner=C.b1r2_git_runner(tag=C.TEST_B1R2_TAG, exists=False, head=C.TEST_COMMIT)
        ),
        operator_grant=C.frozen_test_grant(),
        approved_expected_checkpoint=C.TEST_B1R2_TAG, git_baseline=C.clean_git_baseline(),
    )
    assert "b1_r2_tag_not_observed_in_git" in reasons


def test_b1r2_untagged_state_rejected():
    reasons = verify_b1_r2_checkpoint(
        reader=RealTrustedB1R2Reader(
            git_runner=C.b1r2_git_runner(tag=C.TEST_B1R2_TAG, exists=False, head=C.TEST_COMMIT)
        ),
        operator_grant=C.frozen_test_grant(),
        approved_expected_checkpoint=C.TEST_B1R2_TAG, git_baseline=C.clean_git_baseline(),
    )
    assert "b1_r2_tag_not_observed_in_git" in reasons
    assert "b1_r2_tag_peel_not_observed" in reasons


def test_b1r2_lookalike_observation_object_rejected():
    # §16: a reader returning a look-alike (not a TrustedB1R2Observation) is rejected.
    reasons = verify_b1_r2_checkpoint(
        reader=_LookalikeReader(),
        operator_grant=C.frozen_test_grant(),
        approved_expected_checkpoint=C.TEST_B1R2_TAG, git_baseline=C.clean_git_baseline(),
    )
    assert "b1_r2_observation_not_trusted_reader_output" in reasons


def test_b1r2_grant_identity_must_equal_approved_expected():
    reasons = verify_b1_r2_checkpoint(
        reader=C.b1r2_reader_ok(tag=C.TEST_B1R2_TAG),
        operator_grant=C.frozen_test_grant(b1_r2_checkpoint="a-different-tag"),
        approved_expected_checkpoint=C.TEST_B1R2_TAG, git_baseline=C.clean_git_baseline(),
    )
    assert "b1_r2_grant_identity_mismatch" in reasons


def test_b1r2_identity_equals_implementation_checkpoint_rejected():
    # The B1-R2 checkpoint must be DISTINCT from the implementation checkpoint (§8).
    reasons = verify_b1_r2_checkpoint(
        reader=C.b1r2_reader_ok(tag=C.TEST_TAG),
        operator_grant=C.frozen_test_grant(b1_r2_checkpoint=C.TEST_TAG),  # == impl tag
        approved_expected_checkpoint=C.TEST_TAG, git_baseline=C.clean_git_baseline(),
    )
    assert "b1_r2_identity_equals_implementation_checkpoint_tag" in reasons


@pytest.mark.parametrize(
    "sentinel",
    ["", "   ", "NOT_STARTED", "NOT_AUTHORIZED", "TBD", "pending", "future",
     "B1-R2-NOT-STARTED"],
)
def test_b1r2_sentinel_approved_cannot_verify(sentinel):
    reasons = verify_b1_r2_checkpoint(
        reader=C.b1r2_reader_ok(tag=sentinel),
        operator_grant=C.frozen_test_grant(b1_r2_checkpoint=sentinel),
        approved_expected_checkpoint=sentinel, git_baseline=C.clean_git_baseline(),
    )
    assert "b1_r2_not_approved_fail_closed" in reasons


# --- §13/§14: the future-positive test uses the PRODUCTION mint signature ----- #

def test_b1r2_positive_future_simulation_uses_production_signature():
    # §14: exercise the REAL mint via its PRODUCTION signature (no trust-root params). A
    # future B1-R2 approval is simulated by patching the mint's INTERNAL governance +
    # reader (module boundary), NOT by passing trust roots.
    synthetic = "synthetic-test-b1-r2-tag"
    grant = C.frozen_test_grant(b1_r2_checkpoint=synthetic)
    with C.approved_b1r2_governance(tag=synthetic, head=C.TEST_COMMIT):
        auth = mint_live_provider_run_authorization(
            operator_grant=grant,
            real_preflight_auth=_preflight(),
            git_baseline_attestation=C.clean_git_baseline(),
            observed_fixture_hash=_fixture_hash(),
        )
    assert auth.b1_r2_checkpoint == synthetic
    assert auth.as_public_dict()["b1_r2_checkpoint_attested"] is True


def test_b1r2_positive_verify_unit_returns_no_reasons():
    synthetic = "synthetic-test-b1-r2-tag"
    grant = C.frozen_test_grant(b1_r2_checkpoint=synthetic)
    reader = C.b1r2_reader_ok(tag=synthetic)
    assert verify_b1_r2_checkpoint(
        reader=reader, operator_grant=grant,
        approved_expected_checkpoint=synthetic, git_baseline=C.clean_git_baseline(),
    ) == []


def test_b1r2_patched_wrong_reader_observation_still_fails_under_future_governance():
    # §14: even with a synthetic future approval patched in, a reader observing the WRONG
    # peel fails — the production mint still verifies the real observation.
    synthetic = "synthetic-test-b1-r2-tag"
    grant = C.frozen_test_grant(b1_r2_checkpoint=synthetic)
    import unittest.mock as _mock

    from open_notebook.integrations.graphrag.eval import authmintlivepn02d as _authmint

    wrong_reader = C.b1r2_reader_ok(tag=synthetic, peel="f" * 40, head="f" * 40)
    with _mock.patch.object(
        _authmint, "current_approved_b1_r2_checkpoint", return_value=synthetic
    ), _mock.patch.object(
        _authmint, "_build_trusted_b1_r2_reader", return_value=wrong_reader
    ), pytest.raises(B1R2CheckpointError):
        mint_live_provider_run_authorization(
            operator_grant=grant,
            real_preflight_auth=_preflight(),
            git_baseline_attestation=C.clean_git_baseline(),  # head TEST_COMMIT != fff...
            observed_fixture_hash=_fixture_hash(),
        )
