"""Shared builders for the PN02D-B0B offline live-driver tests (not a test module).

EVALUATION-ONLY test support. Builds the authorization chain + router + executors
over fake backends so each test can drive one layer without re-wiring the whole
orchestrator. No provider/network/DB access.
"""

from __future__ import annotations

from open_notebook.integrations.graphrag.eval.authlivepn02d import (
    mint_indexing_authorization,
    mint_query_authorization,
)
from open_notebook.integrations.graphrag.eval.datasetpn02 import load_fixture
from open_notebook.integrations.graphrag.eval.driverpn02d import (
    build_simulation_provider_run_authorization,
)
from open_notebook.integrations.graphrag.eval.routelivepn02d import (
    PN02Router,
    attest_route,
    build_route_table,
)


def fixture():
    return load_fixture()


def router_for(fx):
    return PN02Router(build_route_table(fx))


def attestations_for(fx, router):
    return {nb: attest_route(router.route_for(nb)) for nb in router.notebook_ids()}


def provider_run_auth(run_id="test-run"):
    return build_simulation_provider_run_authorization(run_id=run_id)


def query_auth(run_id="test-run", *, indexed=24, planned=24):
    auth = provider_run_auth(run_id)
    ia = mint_indexing_authorization(auth, binding_attested=True, run_id=run_id)
    qa = mint_query_authorization(
        ia, indexed_memberships=indexed, planned_memberships=planned, run_id=run_id
    )
    return auth, ia, qa
