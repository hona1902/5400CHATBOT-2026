"""PN02D-B0B — membership index executor: retry, caps, 24/24 gate, outcome mapping."""

from __future__ import annotations

import graphrag_pn02db0b_common as C
import pytest

from open_notebook.integrations.graphrag.eval.budgetlivepn02d import (
    BudgetClass,
    StatefulBudgetGuard,
)
from open_notebook.integrations.graphrag.eval.docidpn02d import DerivedDocMappingStore
from open_notebook.integrations.graphrag.eval.fakeslivepn02d import (
    FakeAttempt,
    FakeCellIndexClient,
    make_index_client_factory,
)
from open_notebook.integrations.graphrag.eval.indexlivepn02d import (
    MembershipIndexExecutor,
)
from open_notebook.integrations.graphrag.eval.outcomespn02d import (
    DriverTechnicalOutcome,
    is_evaluator_boundary_outcome,
    to_technical_outcome,
)
from open_notebook.integrations.graphrag.eval.schemaspn02 import TechnicalOutcome


def _executor(fx, client, *, budget=None):
    router = C.router_for(fx)
    auth, ia, _qa = C.query_auth()
    return MembershipIndexExecutor(
        router=router,
        budget=budget or StatefulBudgetGuard(),
        mapping_store=DerivedDocMappingStore(),
        indexing_auth=ia,
        provider_run_auth=auth,
        client_factory=make_index_client_factory(client),
        memberships=fx.memberships,
    )


@pytest.mark.asyncio
async def test_index_success_single_attempt():
    fx = C.fixture()
    client = FakeCellIndexClient()  # default PROCESSED
    ex = _executor(fx, client)
    rec = await ex.index_membership(
        canonical_source_id="A1", notebook_id="NB_A", text="t", operation_id="op0"
    )
    assert rec.succeeded
    assert rec.technical_status is DriverTechnicalOutcome.COMPLETED
    assert rec.attempt_count == 1
    assert rec.derived_document_id.startswith("doc-")


@pytest.mark.asyncio
async def test_index_transient_then_success_retries_once():
    fx = C.fixture()
    client = FakeCellIndexClient(
        {"A1": [FakeAttempt(terminal_state="FAILED", status_detail="429 rate limit exceeded"),
                FakeAttempt(terminal_state="PROCESSED")]}
    )
    ex = _executor(fx, client)
    rec = await ex.index_membership(
        canonical_source_id="A1", notebook_id="NB_A", text="t", operation_id="op1"
    )
    assert rec.succeeded
    assert rec.attempt_count == 2


@pytest.mark.asyncio
async def test_index_non_retryable_submit_stops_at_one_attempt():
    fx = C.fixture()
    client = FakeCellIndexClient(
        {"A1": [FakeAttempt(submit_accepted=False, submit_detail="invalid schema field")]}
    )
    ex = _executor(fx, client)
    rec = await ex.index_membership(
        canonical_source_id="A1", notebook_id="NB_A", text="t", operation_id="op2"
    )
    assert not rec.succeeded
    assert rec.technical_status is DriverTechnicalOutcome.FAILED_INDEX_SUBMIT
    assert rec.attempt_count == 1


@pytest.mark.asyncio
async def test_index_retry_capped_at_two_attempts():
    fx = C.fixture()
    # Always-transient failure would loop forever without the per-op cap.
    client = FakeCellIndexClient(
        default_attempt=FakeAttempt(terminal_state="FAILED", status_detail="timeout")
    )
    ex = _executor(fx, client)
    rec = await ex.index_membership(
        canonical_source_id="A1", notebook_id="NB_A", text="t", operation_id="op3"
    )
    assert rec.attempt_count == 2  # MAX_INDEX_ATTEMPTS_PER_OPERATION
    assert rec.technical_status is DriverTechnicalOutcome.FAILED_INDEX_COMPLETION


@pytest.mark.asyncio
async def test_index_attempt_budget_exhaustion_refuses_before_op():
    fx = C.fixture()
    budget = StatefulBudgetGuard()
    for _ in range(48):
        budget.reserve(BudgetClass.GRAPH_INDEX_ATTEMPT)  # exhaust the 48-attempt budget
    client = FakeCellIndexClient()
    ex = _executor(fx, client, budget=budget)
    rec = await ex.index_membership(
        canonical_source_id="A1", notebook_id="NB_A", text="t", operation_id="op4"
    )
    assert rec.technical_status is DriverTechnicalOutcome.FAILED_INDEX_CAP
    assert rec.attempt_count == 0
    assert client.submit_calls == 0  # backend never invoked


@pytest.mark.asyncio
async def test_index_unauthorized_membership_never_hits_backend():
    fx = C.fixture()
    client = FakeCellIndexClient()
    ex = _executor(fx, client)
    # A1 is not a member of NB_B.
    rec = await ex.index_membership(
        canonical_source_id="A1", notebook_id="NB_B", text="t", operation_id="op5"
    )
    assert rec.technical_status is DriverTechnicalOutcome.FAILED_RUNTIME_ATTESTATION
    assert client.submit_calls == 0


@pytest.mark.asyncio
async def test_index_all_24_of_24_complete():
    fx = C.fixture()
    ex = _executor(fx, FakeCellIndexClient())
    plan = [(s, nb, fx.source_text(s)) for (s, nb) in fx.memberships]
    records, report = await ex.index_all(plan)
    assert len(records) == 24
    assert report.complete is True
    assert report.succeeded == 24


@pytest.mark.asyncio
async def test_index_all_23_of_24_not_complete():
    fx = C.fixture()
    client = FakeCellIndexClient(
        {"A1": [FakeAttempt(submit_accepted=False, submit_detail="permanent 400 bad request")]}
    )
    ex = _executor(fx, client)
    plan = [(s, nb, fx.source_text(s)) for (s, nb) in fx.memberships]
    _records, report = await ex.index_all(plan)
    assert report.complete is False
    assert report.succeeded == 23


def test_outcome_mapping_is_lossless_and_never_a_science_verdict():
    # Every driver outcome maps to a TechnicalOutcome or is a driver-only state.
    for o in DriverTechnicalOutcome:
        mapped = to_technical_outcome(o)  # raises KeyError if unmapped
        if is_evaluator_boundary_outcome(o):
            assert isinstance(mapped, TechnicalOutcome)
        else:
            assert mapped is None
    # Spot-check the frozen §33 table.
    assert to_technical_outcome(DriverTechnicalOutcome.COMPLETED) is TechnicalOutcome.COMPLETED
    assert to_technical_outcome(DriverTechnicalOutcome.FAILED_INDEX_SUBMIT) is TechnicalOutcome.FAILED_INDEXING
    assert to_technical_outcome(DriverTechnicalOutcome.FAILED_GD_QUERY) is TechnicalOutcome.FAILED_GD_QUERY
    assert to_technical_outcome(DriverTechnicalOutcome.FAILED_MEMBERSHIP_REMOVAL) is None
