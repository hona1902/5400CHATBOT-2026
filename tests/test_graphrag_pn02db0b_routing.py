"""PN02D-B0B — routing + document identity + shared-source isolation (design §11-§18)."""

from __future__ import annotations

import graphrag_pn02db0b_common as C
import pytest

from open_notebook.integrations.graphrag.eval.docidpn02d import (
    ENDPOINT_IDENTITY_IS_DOCUMENT_IDENTITY,
    DerivedDocMappingStore,
    compute_derived_document_id,
)
from open_notebook.integrations.graphrag.eval.manifestpn02 import workspace_id_for
from open_notebook.integrations.graphrag.eval.routelivepn02d import (
    UnauthorizedMembershipError,
    build_route_table,
)
from open_notebook.integrations.graphrag.eval.routingpn02c import (
    WrongEndpointRoutingError,
    WrongWorkspaceRoutingError,
)


def test_endpoint_is_not_identity_flag():
    assert ENDPOINT_IDENTITY_IS_DOCUMENT_IDENTITY is False


def test_derived_doc_id_is_deterministic_and_content_independent():
    assert compute_derived_document_id("SH_AB") == compute_derived_document_id("SH_AB")
    import hashlib

    assert compute_derived_document_id("A1") == "doc-" + hashlib.md5(b"A1").hexdigest()


def test_workspace_id_from_record_id_not_theme():
    fx = C.fixture()
    routes = build_route_table(fx)
    for nb in fx.notebooks:
        assert routes[nb.notebook_id].workspace_id == workspace_id_for(nb.record_id)
        # not derived from the display theme
        assert nb.theme not in routes[nb.notebook_id].workspace_id


def test_routes_are_distinct():
    fx = C.fixture()
    router = C.router_for(fx)
    ws = {router.route_for(nb).workspace_id for nb in router.notebook_ids()}
    eps = {router.route_for(nb).endpoint for nb in router.notebook_ids()}
    st = {router.route_for(nb).storage_identity for nb in router.notebook_ids()}
    assert len(ws) == len(eps) == len(st) == 3


def test_wrong_endpoint_rejected():
    fx = C.fixture()
    router = C.router_for(fx)
    other_ep = router.route_for("NB_B").endpoint
    with pytest.raises(WrongEndpointRoutingError):
        router.validate_route(
            "NB_A", target_endpoint=other_ep,
            target_workspace_id=router.route_for("NB_A").workspace_id,
        )


def test_wrong_workspace_rejected():
    fx = C.fixture()
    router = C.router_for(fx)
    with pytest.raises(WrongWorkspaceRoutingError):
        router.validate_route(
            "NB_A", target_endpoint=router.route_for("NB_A").endpoint,
            target_workspace_id=router.route_for("NB_B").workspace_id,
        )


def test_unauthorized_membership_rejected():
    fx = C.fixture()
    router = C.router_for(fx)
    # A1 belongs to NB_A, never NB_B.
    with pytest.raises(UnauthorizedMembershipError):
        router.resolve_membership_route("A1", "NB_B", memberships=frozenset(fx.memberships))


def test_authorized_membership_resolves():
    fx = C.fixture()
    router = C.router_for(fx)
    route = router.resolve_membership_route(
        "SH_AB", "NB_A", memberships=frozenset(fx.memberships)
    )
    assert route.notebook_id == "NB_A"


def test_shared_source_distinct_derived_records_same_doc_id():
    fx = C.fixture()
    router = C.router_for(fx)
    store = DerivedDocMappingStore()
    wa = router.route_for("NB_A").workspace_id
    wb = router.route_for("NB_B").workspace_id
    ra = store.register(canonical_source_id="SH_AB", workspace_id=wa, notebook_id="NB_A")
    rb = store.register(canonical_source_id="SH_AB", workspace_id=wb, notebook_id="NB_B")
    # Distinct logical rows, SAME vendor doc id (safe under workspace isolation).
    assert ra.logical_key() != rb.logical_key()
    assert ra.derived_document_id == rb.derived_document_id
    assert len(store) == 2


def test_delete_target_bound_to_own_workspace():
    fx = C.fixture()
    router = C.router_for(fx)
    store = DerivedDocMappingStore()
    wa = router.route_for("NB_A").workspace_id
    store.register(canonical_source_id="SH_AB", workspace_id=wa, notebook_id="NB_A")
    target = store.resolve_delete_target(wa, "SH_AB")
    assert target.workspace_id == wa
    assert target.notebook_id == "NB_A"
