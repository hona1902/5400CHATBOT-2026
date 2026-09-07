"""GraphRAG-PN02 routing / attestation / Boundary-B / version-pin tests (task §40-§45).

OFFLINE. No endpoint is opened; every check is a pure comparison against frozen
expectations.
"""

from __future__ import annotations

import pytest

from open_notebook.integrations.graphrag.eval import datasetpn02 as ds
from open_notebook.integrations.graphrag.eval import manifestpn02 as mf

FX = ds.load_fixture()


def test_workspace_id_deterministic_and_from_record_id() -> None:
    a = FX.notebooks[0]
    ws1 = mf.workspace_id_for(a.record_id)
    ws2 = mf.workspace_id_for(a.record_id)
    assert ws1 == ws2
    assert ws1.startswith("nb_") and len(ws1) == 3 + 16
    # NOT derived from the display theme.
    assert mf.workspace_id_for(a.theme) != ws1


def test_routing_manifest_three_distinct_workspaces() -> None:
    routes = mf.routing_manifest(FX)
    assert set(routes) == {"NB_A", "NB_B", "NB_C"}
    ws = {r.workspace_id for r in routes.values()}
    assert len(ws) == 3
    for r in routes.values():
        assert r.endpoint_placeholder.startswith("eval-null://")


def test_attestation_pass_and_require() -> None:
    route = mf.routing_manifest(FX)["NB_A"]
    att = mf.attest_workspace(
        route,
        observed_workspace_id=route.workspace_id,
        observed_endpoint_identity=route.endpoint_placeholder,
        observed_storage_identity="store_A",
        observed_lightrag_version="v1.5.6",
        expected_storage_identity="store_A",
        safe_provider_config_identity="openrouter:llm=x;emb=y",
    )
    assert att.attested is True
    assert att.failure_reasons == ()
    assert att.provider_binding_fingerprint.startswith("pbf_")
    mf.require_attested_before_query(att)  # does not raise


def test_attestation_failure_blocks_query() -> None:
    route = mf.routing_manifest(FX)["NB_A"]
    att = mf.attest_workspace(
        route,
        observed_workspace_id="nb_wrong",
        observed_endpoint_identity=route.endpoint_placeholder,
        observed_storage_identity="store_A",
        observed_lightrag_version="v1.5.6",
        expected_storage_identity="store_A",
        safe_provider_config_identity="cfg",
    )
    assert att.attested is False
    assert "workspace_id_mismatch" in att.failure_reasons
    with pytest.raises(mf.UnattestedWorkspaceError):
        mf.require_attested_before_query(att)


def test_version_pin() -> None:
    mf.validate_lightrag_version("v1.5.6")
    mf.validate_lightrag_version("1.5.6")  # tolerate the historical 'v' mismatch
    with pytest.raises(mf.LightRAGVersionError):
        mf.validate_lightrag_version("1.5.7")


def test_boundary_b_guard() -> None:
    mf.validate_boundary_b(mf.DEFAULT_BOUNDARY_B)
    with pytest.raises(mf.BoundaryBViolation):
        mf.validate_boundary_b(
            mf.BoundaryBDeclaration(mf.DatasetClass.REAL_INTERNAL, False, True)
        )
    with pytest.raises(mf.BoundaryBViolation):
        mf.validate_boundary_b(
            mf.BoundaryBDeclaration(mf.DatasetClass.SYNTHETIC_FIXTURE, True, True)
        )
    with pytest.raises(mf.BoundaryBViolation):
        mf.validate_boundary_b(
            mf.BoundaryBDeclaration(mf.DatasetClass.SYNTHETIC_FIXTURE, False, False)
        )


def test_run_manifest_validates_and_is_content_free() -> None:
    manifest = mf.build_run_manifest(
        FX, run_id="r1", fixture_hash=ds.compute_fixture_hash(FX), git_commit="abc123"
    )
    manifest.validate()
    assert manifest.lightrag_version_expected == "v1.5.6"
    assert manifest.query_count == 24
    assert manifest.boundary_b.synthetic_only is True
    # No secret material anywhere in the manifest routing/ids.
    blob = repr(manifest)
    for banned in ("OPENROUTER_API_KEY", "sk-", "Bearer "):
        assert banned not in blob


def test_run_manifest_rejects_real_internal() -> None:
    with pytest.raises(mf.BoundaryBViolation):
        mf.build_run_manifest(
            FX,
            run_id="r1",
            fixture_hash=ds.compute_fixture_hash(FX),
            git_commit="abc",
            boundary_b=mf.BoundaryBDeclaration(mf.DatasetClass.REAL_INTERNAL, True, False),
        )
