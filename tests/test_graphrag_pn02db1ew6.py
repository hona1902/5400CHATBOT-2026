"""PN02D-B1-EW6 — LightRAG index 409-conflict recovery (Option A: non-destructive track resume).

EVALUATION-ONLY, ZERO provider traffic. Proves the EXEC #8 defect (a retry re-POSTed an
already-accepted ``file_source`` -> LightRAG HTTP 409 -> GraphRAGConflictError escaped uncaught
-> the run fatally blocked) is closed by an ENVELOPE-PRESERVING, NON-DESTRUCTIVE recovery:

  * once a submit is ACCEPTED and a track_id is issued, a retry RESUMES/observes that exact
    accepted track (bounded) instead of re-POSTing (no second POST, no delete);
  * a 409 on a FRESH submit with NO prior accepted track for the operation fails closed
    (never delete / guess / adopt / blind-success);
  * reconciliation is identity-scoped by construction (this operation's own route-bound client
    + its own issued track_id) — never another cell/workspace or source.

No scientific-envelope change: MAX_INDEX_ATTEMPTS_PER_OPERATION (2) / MAX_GRAPH_INDEX_ATTEMPTS
(48) / GRAPH_DELETE_OPERATIONS (1) are untouched; a resume is a status observation of an
already-counted attempt's track, not a new POST and not a delete.

Also covers the completed EW5->EW6 governance repoint (current approved checkpoint == EW6, EW5
historical) and the EW6 successor-checkpoint lifecycle (State A/B). No provider/model change, no
delete-then-insert, no OpenRouter calls.
"""
from __future__ import annotations

from typing import List

import graphrag_pn02db0b_common as CB
import graphrag_pn02db0cb_common as C
import pytest

from open_notebook.integrations.graphrag.eval import authb1r2pn02d as B
from open_notebook.integrations.graphrag.eval.authlivepn02d import (
    PN02_PROVIDER_RUN_AUTHORIZED,
)
from open_notebook.integrations.graphrag.eval.authmintlivepn02d import (
    EXPECTED_B1_R2_CHECKPOINT_TAG,
    EXPECTED_EW1_CHECKPOINT_TAG,
    EXPECTED_EW2_CHECKPOINT_TAG,
    EXPECTED_EW3_CHECKPOINT_TAG,
    EXPECTED_EW4_CHECKPOINT_TAG,
    EXPECTED_EW5_CHECKPOINT_TAG,
    EXPECTED_EW6_CHECKPOINT_TAG,
    EXPECTED_PF1_CHECKPOINT_TAG,
    RealTrustedB1R2Reader,
    current_approved_b1_r2_checkpoint,
    verify_b1_r2_checkpoint,
)
from open_notebook.integrations.graphrag.eval.budgetlivepn02d import StatefulBudgetGuard
from open_notebook.integrations.graphrag.eval.docidpn02d import DerivedDocMappingStore
from open_notebook.integrations.graphrag.eval.fakeslivepn02d import (
    make_index_client_factory,
)
from open_notebook.integrations.graphrag.eval.indexlivepn02d import (
    ERR_CONFLICT,
    MembershipIndexExecutor,
)
from open_notebook.integrations.graphrag.eval.live_indexer08 import (
    CellIndexClient,
    IndexStatusResult,
    IndexSubmitResult,
)
from open_notebook.integrations.graphrag.eval.outcomespn02d import (
    DriverTechnicalOutcome,
)
from open_notebook.integrations.graphrag.models import GraphRAGConflictError

# --------------------------------------------------------------------------- #
# Provider-free fakes modelling the EXEC #8 wire behaviour
# --------------------------------------------------------------------------- #


class _ResumeFake(CellIndexClient):
    """Attempt 1 submit is ACCEPTED (track ``t1``); status(t1) stays IN_PROGRESS for
    ``pending_polls`` STATUS calls then goes PROCESSED. A SECOND submit RAISES
    ``GraphRAGConflictError`` (409) — so if the executor ever re-POSTs (the pre-fix bug) the
    test fails loudly; the fix must resume the accepted track and never call submit twice."""

    def __init__(self, *, pending_polls: int, terminal_after: str = "PROCESSED") -> None:
        self.submit_calls = 0
        self.status_calls = 0
        self.polled_track_ids: List[str] = []
        self._pending = pending_polls
        self._terminal = terminal_after
        self._track = "t1"

    async def submit(self, *, source_id: str, canonical_text: str) -> IndexSubmitResult:
        self.submit_calls += 1
        if self.submit_calls == 1:
            return IndexSubmitResult(accepted=True, track_id=self._track, detail=None)
        # A re-POST of the same already-accepted file_source is exactly the EXEC #8 defect.
        raise GraphRAGConflictError(
            "GraphRAG sidecar rejected the request with HTTP 409 "
            "(document for this file_source already exists)"
        )

    async def status(self, *, track_id: str) -> IndexStatusResult:
        self.status_calls += 1
        self.polled_track_ids.append(track_id)
        if self.status_calls <= self._pending:
            return IndexStatusResult(state="IN_PROGRESS", detail=None)
        detail = None if self._terminal == "PROCESSED" else "permanent schema error"
        return IndexStatusResult(state=self._terminal, detail=detail)


class _AlwaysProcessingFake(_ResumeFake):
    """status(t1) is IN_PROGRESS forever — proves the resumed track is BOUNDED (finite)."""

    def __init__(self) -> None:
        super().__init__(pending_polls=10_000)


class _Conflict409OnFirstSubmit(CellIndexClient):
    """The VERY FIRST submit raises 409 with no prior accepted track for the op (fail closed)."""

    def __init__(self) -> None:
        self.submit_calls = 0
        self.status_calls = 0

    async def submit(self, *, source_id: str, canonical_text: str) -> IndexSubmitResult:
        self.submit_calls += 1
        raise GraphRAGConflictError(
            "GraphRAG sidecar rejected the request with HTTP 409 "
            "(document for this file_source already exists)"
        )

    async def status(self, *, track_id: str) -> IndexStatusResult:  # pragma: no cover
        self.status_calls += 1
        return IndexStatusResult(state="IN_PROGRESS", detail=None)


def _executor(fx, client, *, budget=None) -> MembershipIndexExecutor:
    router = CB.router_for(fx)
    auth, ia, _qa = CB.query_auth()
    return MembershipIndexExecutor(
        router=router,
        budget=budget or StatefulBudgetGuard(),
        mapping_store=DerivedDocMappingStore(),
        indexing_auth=ia,
        provider_run_auth=auth,
        client_factory=make_index_client_factory(client),
        memberships=fx.memberships,
    )


def _first_membership(fx):
    return sorted(fx.memberships)[0]


# --------------------------------------------------------------------------- #
# §16/§17/§18/§19/§20 — resume the accepted track instead of re-POSTing
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_no_repost_after_accepted_track():
    # §17: attempt 1 is accepted; attempt 1's poll window is exhausted (still IN_PROGRESS). The
    # PRE-FIX behaviour re-POSTed (submit #2) and hit the fake's 409. The fix RESUMES the
    # accepted track: submit is called EXACTLY ONCE (no re-POST -> the 409 is never even raised).
    fx = CB.fixture()
    src, nb = _first_membership(fx)
    fake = _ResumeFake(pending_polls=10)  # attempt1 exhausts (10), attempt2 resume -> PROCESSED
    ex = _executor(fx, fake)
    rec = await ex.index_membership(
        canonical_source_id=src, notebook_id=nb, text="t", operation_id="op"
    )
    assert fake.submit_calls == 1  # NO second POST
    assert rec.succeeded


@pytest.mark.asyncio
async def test_document_post_calls_is_one_in_conflict_scenario():
    # §18: exactly ONE document POST in the reproduced scenario (status polling continues on the
    # SAME accepted track under the bounded lifecycle).
    fx = CB.fixture()
    src, nb = _first_membership(fx)
    fake = _ResumeFake(pending_polls=10)
    ex = _executor(fx, fake)
    await ex.index_membership(canonical_source_id=src, notebook_id=nb, text="t", operation_id="op")
    assert fake.submit_calls == 1


@pytest.mark.asyncio
async def test_track_id_resume_to_success():
    # §19: POST accepted -> track_id -> first polling window exhausted -> resume the SAME track ->
    # eventually PROCESSED. Two membership-level attempts, one POST.
    fx = CB.fixture()
    src, nb = _first_membership(fx)
    fake = _ResumeFake(pending_polls=10)
    ex = _executor(fx, fake)
    rec = await ex.index_membership(
        canonical_source_id=src, notebook_id=nb, text="t", operation_id="op"
    )
    assert rec.succeeded
    assert rec.technical_status is DriverTechnicalOutcome.COMPLETED
    assert rec.attempt_count == 2
    assert fake.submit_calls == 1


@pytest.mark.asyncio
async def test_track_id_preserved_across_retry_boundary():
    # §20: the EXACT same track_id is polled before AND after the retry/resume boundary.
    fx = CB.fixture()
    src, nb = _first_membership(fx)
    fake = _ResumeFake(pending_polls=10)
    ex = _executor(fx, fake)
    await ex.index_membership(canonical_source_id=src, notebook_id=nb, text="t", operation_id="op")
    assert fake.status_calls == 11  # 10 (attempt1) + 1 (attempt2 resume -> PROCESSED)
    assert set(fake.polled_track_ids) == {"t1"}  # only ever the accepted track


@pytest.mark.asyncio
async def test_identity_proven_409_reconcile_by_resume():
    # §6/§21: in the identity-proven situation (a prior ACCEPTED track for this exact op), the
    # executor reconciles by RESUMING that track — the 409 is never raised (submit called once),
    # no delete occurs, and there is no blind success (completion is observed on the real track).
    fx = CB.fixture()
    src, nb = _first_membership(fx)
    fake = _ResumeFake(pending_polls=10)
    budget = StatefulBudgetGuard()
    ex = _executor(fx, fake, budget=budget)
    rec = await ex.index_membership(
        canonical_source_id=src, notebook_id=nb, text="t", operation_id="op"
    )
    assert rec.succeeded and fake.submit_calls == 1
    # No delete budget was ever spent (non-destructive recovery).
    assert budget.snapshot()["GRAPH_DELETE"]["spent"] == 0


# --------------------------------------------------------------------------- #
# §7/§8/§22/§23/§24 — fail closed without a prior accepted track; no cross recon
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_409_without_prior_accept_fails_closed():
    # §8/§22: a 409 on the FIRST submit (no prior accepted track for this op) fails closed —
    # non-retryable, ERR_CONFLICT, one submit, NOT succeeded. Never delete / guess / blind-success.
    fx = CB.fixture()
    src, nb = _first_membership(fx)
    fake = _Conflict409OnFirstSubmit()
    ex = _executor(fx, fake)
    rec = await ex.index_membership(
        canonical_source_id=src, notebook_id=nb, text="t", operation_id="op"
    )
    assert not rec.succeeded
    assert rec.technical_status is DriverTechnicalOutcome.FAILED_INDEX_SUBMIT
    assert rec.error_category == ERR_CONFLICT
    assert rec.attempt_count == 1  # non-retryable -> no second attempt
    assert fake.submit_calls == 1


@pytest.mark.asyncio
async def test_409_cross_op_cannot_reconcile_using_another_ops_track():
    # §23/§24: a prior ACCEPTED track belongs ONLY to its own operation (a local, route-bound
    # value). A DIFFERENT membership op whose first submit 409s cannot borrow it — it fails
    # closed. (Executing op1 first, then a separate op2 with a fresh fake, proves no shared state
    # across cells/sources.)
    fx = CB.fixture()
    memberships = sorted(fx.memberships)
    (src1, nb1) = memberships[0]
    (src2, nb2) = next((s, n) for (s, n) in memberships if s != src1)
    # op1: an accepted+resumable op (leaves an accepted track in ITS own scope only).
    ex1 = _executor(fx, _ResumeFake(pending_polls=10))
    await ex1.index_membership(canonical_source_id=src1, notebook_id=nb1, text="t", operation_id="op1")
    # op2: a DIFFERENT source whose first submit 409s -> must fail closed (no borrowed track).
    fake2 = _Conflict409OnFirstSubmit()
    ex2 = _executor(fx, fake2)
    rec2 = await ex2.index_membership(
        canonical_source_id=src2, notebook_id=nb2, text="t", operation_id="op2"
    )
    assert not rec2.succeeded
    assert rec2.error_category == ERR_CONFLICT
    assert fake2.submit_calls == 1


# --------------------------------------------------------------------------- #
# §13/§25/§26/§27 — terminal failure, boundedness, normal fast path
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_resumed_track_terminal_failure_fails_closed():
    # §13/§25: a preserved track that reaches a terminal FAILED (non-transient) is NOT treated
    # as success — the membership fails.
    fx = CB.fixture()
    src, nb = _first_membership(fx)
    fake = _ResumeFake(pending_polls=10, terminal_after="FAILED")
    ex = _executor(fx, fake)
    rec = await ex.index_membership(
        canonical_source_id=src, notebook_id=nb, text="t", operation_id="op"
    )
    assert not rec.succeeded
    assert fake.submit_calls == 1  # still no re-POST


@pytest.mark.asyncio
async def test_resumed_track_is_bounded():
    # §5/§26: a track that stays IN_PROGRESS forever terminates FINITELY — bounded by
    # max_polls per attempt (10) AND max_attempts per op (2) -> 20 status observations, one POST.
    fx = CB.fixture()
    src, nb = _first_membership(fx)
    fake = _AlwaysProcessingFake()
    ex = _executor(fx, fake)
    rec = await ex.index_membership(
        canonical_source_id=src, notebook_id=nb, text="t", operation_id="op"
    )
    assert not rec.succeeded
    assert rec.attempt_count == 2
    assert fake.status_calls == 20  # 10 + 10, finite
    assert fake.submit_calls == 1


@pytest.mark.asyncio
async def test_normal_index_fast_path_unchanged():
    # §27: the normal case (POST accepted -> PROCESSED inside the first polling window) is
    # unchanged: one attempt, one POST, success.
    fx = CB.fixture()
    src, nb = _first_membership(fx)
    fake = _ResumeFake(pending_polls=0)  # PROCESSED on the first poll
    ex = _executor(fx, fake)
    rec = await ex.index_membership(
        canonical_source_id=src, notebook_id=nb, text="t", operation_id="op"
    )
    assert rec.succeeded
    assert rec.attempt_count == 1
    assert fake.submit_calls == 1  # DOCUMENT_POST_CALLS_FAST_PATH = 1


# --------------------------------------------------------------------------- #
# §28/§29/§30 — shared-source topology, full 24/24 index stage, driver no-escape
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_plan_21_sources_24_memberships():
    # §24/§28: the frozen fixture is 21 canonical sources over 24 membership edges, with 3
    # shared sources; each membership indexes into its notebook's own isolated cell/workspace.
    fx = CB.fixture()
    assert len(fx.memberships) == 24
    assert len({s for (s, _n) in fx.memberships}) == 21


@pytest.mark.asyncio
async def test_full_index_stage_24_of_24_and_shared_sources_no_false_conflict():
    # §28/§29/§30: a full provider-free index stage over all 24 memberships completes 24/24 with
    # ZERO failures using a factory that returns a FRESH resumable fake per route call — every
    # membership (including the 3 shared sources routed to DIFFERENT isolated cells) resumes its
    # own accepted track to PROCESSED, never re-POSTing, never cross-reconciling. index_all does
    # not raise (so the driver's step 6 cannot fatally escape on a resumable conflict, §30).
    fx = CB.fixture()
    router = CB.router_for(fx)
    auth, ia, _qa = CB.query_auth()

    def _fresh_factory(_route):
        return _ResumeFake(pending_polls=10)  # each op: 1 POST, resume to PROCESSED

    ex = MembershipIndexExecutor(
        router=router,
        budget=StatefulBudgetGuard(),
        mapping_store=DerivedDocMappingStore(),
        indexing_auth=ia,
        provider_run_auth=auth,
        client_factory=_fresh_factory,
        memberships=fx.memberships,
    )
    plan = [(s, n, "text") for (s, n) in sorted(fx.memberships)]
    records, report = await ex.index_all(plan)
    assert report.planned == 24
    assert report.succeeded == 24
    assert report.complete is True
    assert sum(1 for r in records if not r.succeeded) == 0


# --------------------------------------------------------------------------- #
# §31/§32/§33/§34/§35/§36 — EW6 governance repoint + successor lifecycle
# --------------------------------------------------------------------------- #


def test_ew6_governance_repoint_complete():
    # §32: the EW5->EW6 governance repoint is COMPLETE in-repo — the current approved
    # provider-authorization identity is the exact EW6 successor tag; the re-exported
    # grant/manifest identity follows it. EW5 is now HISTORICAL (not current).
    assert (
        EXPECTED_EW6_CHECKPOINT_TAG == "graphrag-pn02db1ew6-index-conflict-recovery-approved"
    )
    assert EXPECTED_EW6_CHECKPOINT_TAG != EXPECTED_EW5_CHECKPOINT_TAG
    assert current_approved_b1_r2_checkpoint() == EXPECTED_EW6_CHECKPOINT_TAG
    assert B.B1_R2_EXPECTED_CHECKPOINT_TAG == EXPECTED_EW6_CHECKPOINT_TAG
    assert current_approved_b1_r2_checkpoint() != EXPECTED_EW5_CHECKPOINT_TAG


def test_ew6_successor_tag_git_state_is_lifecycle_valid():
    # §33/§36: lifecycle-aware — never a permanent real-tag-absence assumption. STATE A
    # (pre-checkpoint): the EW6 tag is absent -> empty peel. STATE B (post-checkpoint): the EXACT
    # tag is present, peels to a 40-hex commit == the authorized HEAD.
    obs = RealTrustedB1R2Reader().observe(EXPECTED_EW6_CHECKPOINT_TAG)
    if not obs.observed_tag_exists:
        assert obs.observed_tag_peel == ""  # STATE A
    else:
        assert obs.checkpoint_tag == EXPECTED_EW6_CHECKPOINT_TAG
        assert len(obs.observed_tag_peel) == 40 and all(
            c in "0123456789abcdef" for c in obs.observed_tag_peel
        )
        assert obs.observed_tag_peel == obs.observed_head  # STATE B


def test_ew5_cannot_substitute_at_a_future_head():
    # §33: once EW6 source moves HEAD, the EW5 tag (peeling to its OWN commit 8b0325a) no longer
    # peels to the authorized HEAD, so it cannot authorize the modified HEAD (fail-closed).
    future = "e6e6e6e6" + "0" * 32
    ew5_commit = "8b0325a7" + "0" * 32
    grant = C.frozen_test_grant(b1_r2_checkpoint=EXPECTED_EW5_CHECKPOINT_TAG)
    reasons = verify_b1_r2_checkpoint(
        reader=C.b1r2_reader_ok(tag=EXPECTED_EW5_CHECKPOINT_TAG, peel=ew5_commit, head=future),
        operator_grant=grant,
        approved_expected_checkpoint=EXPECTED_EW5_CHECKPOINT_TAG,
        git_baseline=C.clean_git_baseline(commit=future, tag=EXPECTED_EW5_CHECKPOINT_TAG),
    )
    assert "b1_r2_tag_not_at_authorized_head" in reasons


def test_ew6_exact_tag_with_peel_is_accepted_by_git_gate():
    # §32: the EXACT EW6 successor identity, trust-observed at the authorized HEAD with a matching
    # baseline, satisfies the control-plane Git gate (no real tag created).
    future = "e6e6e6e6" + "0" * 32
    grant = C.frozen_test_grant(b1_r2_checkpoint=EXPECTED_EW6_CHECKPOINT_TAG)
    reasons = verify_b1_r2_checkpoint(
        reader=C.b1r2_reader_ok(tag=EXPECTED_EW6_CHECKPOINT_TAG, peel=future, head=future),
        operator_grant=grant,
        approved_expected_checkpoint=EXPECTED_EW6_CHECKPOINT_TAG,
        git_baseline=C.clean_git_baseline(commit=future, tag=EXPECTED_EW6_CHECKPOINT_TAG),
    )
    assert reasons == []


def test_wrong_ew6_peel_fails_closed():
    # §32/§34: the exact EW6 tag observed with a peel that does NOT match the authorized HEAD
    # fails closed.
    head = "aaaa1111" + "0" * 32
    wrong = "bbbb2222" + "0" * 32
    grant = C.frozen_test_grant(b1_r2_checkpoint=EXPECTED_EW6_CHECKPOINT_TAG)
    reasons = verify_b1_r2_checkpoint(
        reader=C.b1r2_reader_ok(tag=EXPECTED_EW6_CHECKPOINT_TAG, peel=wrong, head=head),
        operator_grant=grant,
        approved_expected_checkpoint=EXPECTED_EW6_CHECKPOINT_TAG,
        git_baseline=C.clean_git_baseline(commit=head, tag=EXPECTED_EW6_CHECKPOINT_TAG),
    )
    assert "b1_r2_tag_not_at_authorized_head" in reasons


@pytest.mark.parametrize(
    "substitute",
    [
        "graphrag-arbitrary-unrelated-tag",
        EXPECTED_EW5_CHECKPOINT_TAG,
        EXPECTED_EW4_CHECKPOINT_TAG,
        EXPECTED_EW3_CHECKPOINT_TAG,
        EXPECTED_EW2_CHECKPOINT_TAG,
        EXPECTED_EW1_CHECKPOINT_TAG,
        EXPECTED_PF1_CHECKPOINT_TAG,
        EXPECTED_B1_R2_CHECKPOINT_TAG,
    ],
)
def test_historical_or_arbitrary_tag_cannot_substitute_for_ew6(substitute):
    # §31/§33/§35: naming ANY historical (EW5/EW4/EW3/EW2/EW1/PF1/B1-R2) or arbitrary tag as the
    # approved-expected identity cannot satisfy the EW6 checkpoint — the grant's B1-R2 identity
    # (the current EW6 successor) must EXACTLY equal the governance-approved identity.
    future = "e6e6e6e6" + "0" * 32
    grant = C.frozen_test_grant(b1_r2_checkpoint=EXPECTED_EW6_CHECKPOINT_TAG)
    reasons = verify_b1_r2_checkpoint(
        reader=C.b1r2_reader_ok(tag=substitute, peel=future, head=future),
        operator_grant=grant,
        approved_expected_checkpoint=substitute,
        git_baseline=C.clean_git_baseline(commit=future, tag=substitute),
    )
    assert reasons, f"{substitute} must not satisfy the EW6 checkpoint"


def test_ew6_tag_alone_does_not_authorize_provider_run():
    # §35: even with the Git gate satisfiable (exact EW6 tag at HEAD), the provider-run
    # governance flag stays NO — a valid checkpoint tag ALONE can never mint a live run.
    assert PN02_PROVIDER_RUN_AUTHORIZED is False
