"""PN02D-B0B — authorization chain + provider-operation allowlist (design §6/§7/§8)."""

from __future__ import annotations

import graphrag_pn02db0b_common as C
import pytest

from open_notebook.integrations.graphrag.eval.attestpn02d import (
    mint_real_preflight_authorization,
)
from open_notebook.integrations.graphrag.eval.authlivepn02d import (
    B1_ALLOWED_OPERATION_CLASSES,
    B1_FORBIDDEN_OPERATION_CLASSES,
    PN02_PROVIDER_RUN_AUTHORIZED,
    ForbiddenOperationClass,
    IndexingAuthorization,
    PN02ProviderRunAuthorization,
    ProviderOperationClass,
    ProviderRunAuthorizationError,
    ProviderRunNotAuthorized,
    QueryAuthorization,
    QueryAuthorizationMissing,
    frozen_provider_config_id,
    mint_indexing_authorization,
    mint_provider_run_authorization,
    mint_query_authorization,
    require_operation_allowed,
    require_provider_run_authorization,
    require_query_authorization,
)
from open_notebook.integrations.graphrag.eval.driverpn02d import EXPECTED_FIXTURE_HASH


def _preflight(run_id="r", fixture_hash=EXPECTED_FIXTURE_HASH):
    return mint_real_preflight_authorization(
        gate0_passed=True, gate1_passed=True, fixture_hash=fixture_hash,
        run_id=run_id, runtime_count=3,
    )


def _caps():
    from open_notebook.integrations.graphrag.eval.budgetlivepn02d import b1_caps_dict

    return b1_caps_dict()


def test_governance_posture_is_no():
    assert PN02_PROVIDER_RUN_AUTHORIZED is False


def test_provider_run_capability_not_directly_constructible():
    with pytest.raises(PermissionError):
        PN02ProviderRunAuthorization(
            object(),
            fixture_hash="x", git_baseline_commit="c", git_baseline_tag="t",
            synthetic_only=True, real_internal_data_allowed=False,
            approved_provider_config_id="p", workload_caps={},
            allowed_operation_classes=B1_ALLOWED_OPERATION_CLASSES, run_id="r",
        )


def test_mint_requires_real_preflight():
    with pytest.raises(Exception):  # IndexingGateBlocked
        mint_provider_run_authorization(
            real_preflight_auth=None,
            fixture_hash=EXPECTED_FIXTURE_HASH,
            expected_fixture_hash=EXPECTED_FIXTURE_HASH,
            git_baseline_commit="c", git_baseline_tag="t", run_id="r",
            workload_caps=_caps(),
        )


def test_mint_rejects_fixture_hash_mismatch():
    with pytest.raises(ProviderRunAuthorizationError):
        mint_provider_run_authorization(
            real_preflight_auth=_preflight(fixture_hash="WRONG"),
            fixture_hash="WRONG",
            expected_fixture_hash=EXPECTED_FIXTURE_HASH,
            git_baseline_commit="c", git_baseline_tag="t", run_id="r",
            workload_caps=_caps(),
        )


def test_mint_rejects_boundary_b_violation():
    with pytest.raises(ProviderRunAuthorizationError):
        mint_provider_run_authorization(
            real_preflight_auth=_preflight(),
            fixture_hash=EXPECTED_FIXTURE_HASH,
            expected_fixture_hash=EXPECTED_FIXTURE_HASH,
            git_baseline_commit="c", git_baseline_tag="t", run_id="r",
            workload_caps=_caps(), real_internal_data_allowed=True,
        )


def test_mint_rejects_wrong_provider_config_id():
    with pytest.raises(ProviderRunAuthorizationError):
        mint_provider_run_authorization(
            real_preflight_auth=_preflight(),
            fixture_hash=EXPECTED_FIXTURE_HASH,
            expected_fixture_hash=EXPECTED_FIXTURE_HASH,
            git_baseline_commit="c", git_baseline_tag="t", run_id="r",
            workload_caps=_caps(), approved_provider_config_id="pbf_wrong",
        )


def test_require_provider_run_authorization_rejects_none():
    with pytest.raises(ProviderRunNotAuthorized):
        require_provider_run_authorization(None)


def test_provider_config_id_matches_frozen_binding():
    auth = C.provider_run_auth("cfg")
    assert auth.approved_provider_config_id == frozen_provider_config_id()


def test_allowlist_partition_is_complete_and_disjoint():
    allowed = set(B1_ALLOWED_OPERATION_CLASSES)
    forbidden = set(B1_FORBIDDEN_OPERATION_CLASSES)
    assert allowed.isdisjoint(forbidden)
    assert allowed | forbidden == set(ProviderOperationClass)
    # The exact frozen allowlist (design §8).
    assert allowed == {
        ProviderOperationClass.GRAPH_INDEX,
        ProviderOperationClass.INDEX_REQUIRED_EMBEDDING,
        ProviderOperationClass.GD_QUERY_DATA,
        ProviderOperationClass.VECTOR_QUERY_EMBEDDING,
        ProviderOperationClass.VECTOR_NOTEBOOK_QUERY,
        ProviderOperationClass.GRAPH_DELETE,
    }


@pytest.mark.parametrize(
    "op",
    [
        ProviderOperationClass.CLIENT_QUERY,
        ProviderOperationClass.LIGHTRAG_FINAL_ANSWER,
        ProviderOperationClass.QA_V,
        ProviderOperationClass.QA_GD,
        ProviderOperationClass.QA_V_GD,
        ProviderOperationClass.JUDGE_MODEL,
        ProviderOperationClass.PRODUCTION_ASK,
    ],
)
def test_forbidden_operation_classes_rejected(op):
    auth = C.provider_run_auth("op")
    with pytest.raises(ForbiddenOperationClass):
        require_operation_allowed(auth, op)


@pytest.mark.parametrize("op", sorted(B1_ALLOWED_OPERATION_CLASSES, key=lambda c: c.value))
def test_allowed_operation_classes_pass(op):
    auth = C.provider_run_auth("op2")
    require_operation_allowed(auth, op)  # no raise


def test_indexing_auth_requires_binding_attested():
    auth = C.provider_run_auth("ia")
    assert mint_indexing_authorization(auth, binding_attested=False, run_id="ia") is None
    ia = mint_indexing_authorization(auth, binding_attested=True, run_id="ia")
    assert isinstance(ia, IndexingAuthorization)


def test_query_auth_minted_only_at_24_of_24():
    auth = C.provider_run_auth("qa")
    ia = mint_indexing_authorization(auth, binding_attested=True, run_id="qa")
    assert mint_query_authorization(ia, indexed_memberships=23, planned_memberships=24, run_id="qa") is None
    qa = mint_query_authorization(ia, indexed_memberships=24, planned_memberships=24, run_id="qa")
    assert isinstance(qa, QueryAuthorization)
    assert qa.indexed_memberships == 24


def test_query_auth_not_directly_constructible():
    with pytest.raises(PermissionError):
        QueryAuthorization(object(), fixture_hash="h", run_id="r", indexed_memberships=24)


def test_require_query_authorization_rejects_none():
    with pytest.raises(QueryAuthorizationMissing):
        require_query_authorization(None)
