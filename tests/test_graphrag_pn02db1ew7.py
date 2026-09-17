"""PN02D-B1-EW7 — bounded index-observation timing (Option A) + index observability.

EVALUATION-ONLY, ZERO provider traffic. EF4 proved the EXEC #10 <24/24 blocker: the accepted-
track poll loop had ZERO inter-poll delay, so a still-IN_PROGRESS document was abandoned in
~network-RTT time (far below real LightRAG graph-index latency). EW7 adds a deliberate async
wall-clock wait BETWEEN consecutive IN_PROGRESS polls (production 2.0s), leaving the poll count
(10/attempt), attempt count (2/op) and MAX_GRAPH_INDEX_ATTEMPTS (48) UNCHANGED — a timing-policy
change only. It also surfaces content-safe index observability so a future real run reports its
own metrics without a forensic reconstruction.

These tests use a FAKE recording sleeper (TESTS_USE_REAL_MULTISECOND_WAIT=NO): they record the
deliberate waits without blocking. They also cover the EW6->EW7 governance repoint (current
approved checkpoint == EW7, EW6/EW5/... historical), lifecycle State A/B, and substitution
rejection. No provider/model/dimension change, no OpenRouter calls, no envelope change.
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
    EXPECTED_EW7_CHECKPOINT_TAG,
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
    INDEX_POLL_INTERVAL_SECONDS,
    IndexCompletionReport,
    IndexOperationRecord,
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
# Fakes: a recording sleeper + a status-scripted accepted-track client
# --------------------------------------------------------------------------- #


class _RecordingSleeper:
    """Fake async sleeper that RECORDS each requested wait without blocking (§8)."""

    def __init__(self) -> None:
        self.waits: List[float] = []

    async def __call__(self, seconds: float) -> None:
        self.waits.append(seconds)


class _AcceptedThenStatusFake(CellIndexClient):
    """Attempt 1 submit is ACCEPTED (track ``t1``); ``status(t1)`` returns IN_PROGRESS for the
    first ``pending_polls`` STATUS calls, then ``terminal`` (default PROCESSED). A SECOND submit
    RAISES — so any re-POST after acceptance fails the test loudly (EW6 invariant preserved)."""

    def __init__(self, *, pending_polls: int, terminal: str = "PROCESSED") -> None:
        self.submit_calls = 0
        self.status_calls = 0
        self._pending = pending_polls
        self._terminal = terminal
        self._track = "t1"

    async def submit(self, *, source_id: str, canonical_text: str) -> IndexSubmitResult:
        self.submit_calls += 1
        if self.submit_calls == 1:
            return IndexSubmitResult(accepted=True, track_id=self._track, detail=None)
        raise AssertionError("EW7: no second POST after an accepted track")

    async def status(self, *, track_id: str) -> IndexStatusResult:
        self.status_calls += 1
        if self.status_calls <= self._pending:
            return IndexStatusResult(state="IN_PROGRESS", detail=None)
        detail = None if self._terminal == "PROCESSED" else "permanent schema error"
        return IndexStatusResult(state=self._terminal, detail=detail)


class _Conflict409OnSubmit(CellIndexClient):
    """submit RAISES GraphRAGConflictError (a fresh 409 with no prior accepted track)."""

    def __init__(self) -> None:
        self.submit_calls = 0
        self.status_calls = 0

    async def submit(self, *, source_id: str, canonical_text: str) -> IndexSubmitResult:
        self.submit_calls += 1
        raise GraphRAGConflictError("document for this file_source already exists")

    async def status(self, *, track_id: str) -> IndexStatusResult:  # pragma: no cover
        self.status_calls += 1
        return IndexStatusResult(state="IN_PROGRESS", detail=None)


def _executor(
    fx,
    client,
    *,
    sleeper,
    max_attempts: int = 2,
    poll_interval: float = INDEX_POLL_INTERVAL_SECONDS,
    budget=None,
) -> MembershipIndexExecutor:
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
        max_attempts_per_operation=max_attempts,
        poll_interval_seconds=poll_interval,
        sleeper=sleeper,
    )


def _first_membership(fx):
    return sorted(fx.memberships)[0]


async def _run_one(ex, fx, *, op_id="idx-000"):
    src, nb = _first_membership(fx)
    return await ex.index_membership(
        canonical_source_id=src, notebook_id=nb, text="text", operation_id=op_id
    )


# --------------------------------------------------------------------------- #
# §2/§5 — production interval + envelope constants unchanged
# --------------------------------------------------------------------------- #


def test_production_poll_interval_is_two_seconds():
    assert INDEX_POLL_INTERVAL_SECONDS == 2.0


# --------------------------------------------------------------------------- #
# §14/§16/§17 — no sleep before first poll / on terminal (single window)
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_no_sleep_before_first_poll_processed_immediately():
    # §14/§16: PROCESSED on poll #1 → zero deliberate waits, one POST, no resume.
    fx = CB.fixture()
    sleeper = _RecordingSleeper()
    fake = _AcceptedThenStatusFake(pending_polls=0)
    rec = await _run_one(_executor(fx, fake, sleeper=sleeper), fx)
    assert rec.succeeded
    assert sleeper.waits == []
    assert fake.submit_calls == 1
    assert rec.document_post_count == 1
    assert rec.resume_count == 0
    assert rec.status_poll_count == 1


@pytest.mark.asyncio
async def test_terminal_failure_takes_no_extra_sleep():
    # §17: two IN_PROGRESS polls (2 waits) then terminal FAILED → NO wait after the terminal
    # poll. Single window (max_attempts=1) so the non-resumable failure does not re-submit.
    fx = CB.fixture()
    sleeper = _RecordingSleeper()
    fake = _AcceptedThenStatusFake(pending_polls=2, terminal="FAILED")
    rec = await _run_one(_executor(fx, fake, sleeper=sleeper, max_attempts=1), fx)
    assert not rec.succeeded
    assert sleeper.waits == [2.0, 2.0]  # after polls 1 and 2 only; none after the FAILED poll
    assert fake.status_calls == 3


# --------------------------------------------------------------------------- #
# §15/§22 — exactly N-1 waits per exhausted window; 36s max deliberate wait
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_ten_inprogress_polls_take_exactly_nine_waits():
    # §15: one 10-poll window that stays IN_PROGRESS sleeps exactly 9× (never before poll #1,
    # never after the final poll). Single window via max_attempts=1.
    fx = CB.fixture()
    sleeper = _RecordingSleeper()
    fake = _AcceptedThenStatusFake(pending_polls=10)  # never PROCESSED within the window
    rec = await _run_one(_executor(fx, fake, sleeper=sleeper, max_attempts=1), fx)
    assert not rec.succeeded
    assert sleeper.waits == [2.0] * 9
    assert fake.status_calls == 10
    assert fake.submit_calls == 1


@pytest.mark.asyncio
async def test_max_deliberate_wait_is_thirty_six_seconds():
    # §22: full two-window exhaustion (default max_attempts=2) → exactly 18 waits × 2.0s = 36.0s.
    fx = CB.fixture()
    sleeper = _RecordingSleeper()
    fake = _AcceptedThenStatusFake(pending_polls=100)  # IN_PROGRESS through both windows
    rec = await _run_one(_executor(fx, fake, sleeper=sleeper), fx)
    assert not rec.succeeded
    assert len(sleeper.waits) == 18
    assert sum(sleeper.waits) == 36.0
    assert fake.status_calls == 20  # 10 + 10, bounded
    assert fake.submit_calls == 1  # never re-POSTed


# --------------------------------------------------------------------------- #
# §18/§19/§20/§21 — resume across the window boundary at deterministic points
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_resume_after_first_window_no_repost():
    # §18: attempt-1 window exhausts (10 IN_PROGRESS), attempt-2 RESUMES the SAME track and the
    # very first resumed poll is PROCESSED. One POST, one resume, zero reposts, 9 waits.
    fx = CB.fixture()
    sleeper = _RecordingSleeper()
    fake = _AcceptedThenStatusFake(pending_polls=10)
    rec = await _run_one(_executor(fx, fake, sleeper=sleeper), fx)
    assert rec.succeeded
    assert fake.submit_calls == 1
    assert rec.document_post_count == 1
    assert rec.resume_count == 1
    assert rec.document_reposts_after_accepted_track == 0
    assert rec.status_poll_count == 11  # 10 (attempt1) + 1 (attempt2 resume → PROCESSED)
    assert sleeper.waits == [2.0] * 9


@pytest.mark.asyncio
async def test_delayed_completion_within_second_window():
    # §19 (EF4 Case B): PROCESSED during attempt 2 (poll #15) → success, one POST, one resume.
    fx = CB.fixture()
    sleeper = _RecordingSleeper()
    fake = _AcceptedThenStatusFake(pending_polls=14)  # polls 1..14 IN_PROGRESS, poll 15 PROCESSED
    rec = await _run_one(_executor(fx, fake, sleeper=sleeper), fx)
    assert rec.succeeded
    assert fake.submit_calls == 1
    assert rec.resume_count == 1
    assert fake.status_calls == 15
    assert len(sleeper.waits) == 13  # 9 (window1) + 4 (waits after polls 11..14)


@pytest.mark.asyncio
async def test_processed_on_final_allowed_poll_twenty():
    # §20: PROCESSED on poll #20 (the last allowed observation) → success, no premature failure.
    fx = CB.fixture()
    sleeper = _RecordingSleeper()
    fake = _AcceptedThenStatusFake(pending_polls=19)  # poll 20 PROCESSED
    rec = await _run_one(_executor(fx, fake, sleeper=sleeper), fx)
    assert rec.succeeded
    assert fake.status_calls == 20
    assert fake.submit_calls == 1
    assert len(sleeper.waits) == 18  # 9 + 9 (no wait after poll 20)


@pytest.mark.asyncio
async def test_completion_after_final_poll_remains_bounded_failure():
    # §21: still IN_PROGRESS through poll #20 (would PROCESS only afterward) → the executor fails
    # BOUNDEDLY after the allowed observation (EW7 never waits unboundedly).
    fx = CB.fixture()
    sleeper = _RecordingSleeper()
    fake = _AcceptedThenStatusFake(pending_polls=20)  # PROCESSED would be poll 21 (never reached)
    rec = await _run_one(_executor(fx, fake, sleeper=sleeper), fx)
    assert not rec.succeeded
    assert rec.technical_status is DriverTechnicalOutcome.FAILED_INDEX_COMPLETION
    assert fake.status_calls == 20
    assert fake.submit_calls == 1


# --------------------------------------------------------------------------- #
# §23/§24 — normal fast path + fresh 409 fails closed
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_normal_fast_path():
    # §23: POST → accepted → PROCESSED quickly (poll 1). One POST, zero resume, zero waits.
    fx = CB.fixture()
    sleeper = _RecordingSleeper()
    fake = _AcceptedThenStatusFake(pending_polls=0)
    rec = await _run_one(_executor(fx, fake, sleeper=sleeper), fx)
    assert rec.succeeded
    assert rec.document_post_count == 1
    assert rec.resume_count == 0
    assert sleeper.waits == []


@pytest.mark.asyncio
async def test_fresh_409_fails_closed():
    # §24: a 409 on a FRESH submit with no prior accepted track fails closed (no blind success).
    fx = CB.fixture()
    sleeper = _RecordingSleeper()
    fake = _Conflict409OnSubmit()
    rec = await _run_one(_executor(fx, fake, sleeper=sleeper), fx)
    assert not rec.succeeded
    assert rec.technical_status is DriverTechnicalOutcome.FAILED_INDEX_SUBMIT
    assert fake.submit_calls == 1
    assert sleeper.waits == []


# --------------------------------------------------------------------------- #
# §25/§26 — shared-source topology + full provider-free 24/24 index stage
# --------------------------------------------------------------------------- #


def test_plan_21_sources_24_memberships():
    # §25: the frozen fixture has 21 canonical sources and 24 memberships (3 shared).
    fx = CB.fixture()
    assert len({s for (s, _n) in fx.memberships}) == 21
    assert len(fx.memberships) == 24


@pytest.mark.asyncio
async def test_full_index_stage_24_of_24_with_delayed_completion():
    # §26: every membership (incl. the 3 shared sources routed to DIFFERENT isolated cells)
    # exhausts attempt-1's window then resumes its OWN accepted track to PROCESSED in attempt 2 —
    # 24/24 complete, one POST each, no re-POST, no cross-reconciliation, bounded waits.
    fx = CB.fixture()
    router = CB.router_for(fx)
    auth, ia, _qa = CB.query_auth()
    sleeper = _RecordingSleeper()

    def _fresh_factory(route):
        return _AcceptedThenStatusFake(pending_polls=10)  # resume → PROCESSED

    ex = MembershipIndexExecutor(
        router=router,
        budget=StatefulBudgetGuard(),
        mapping_store=DerivedDocMappingStore(),
        indexing_auth=ia,
        provider_run_auth=auth,
        client_factory=_fresh_factory,
        memberships=fx.memberships,
        sleeper=sleeper,
    )
    plan = [(s, n, "text") for (s, n) in sorted(fx.memberships)]
    records, report = await ex.index_all(plan)
    assert report.planned == 24
    assert report.succeeded == 24
    assert report.complete is True
    assert report.document_posts == 24  # exactly one POST per membership
    assert report.track_resume_operations == 24  # each resumed its own accepted track
    assert report.document_reposts_after_accepted_track == 0
    assert sum(1 for r in records if not r.succeeded) == 0


# --------------------------------------------------------------------------- #
# §10/§11/§12/§27/§28/§29 — index observability contract (content-safe)
# --------------------------------------------------------------------------- #


def test_completion_report_surfaces_all_index_metrics():
    # §10/§27: IndexCompletionReport.as_dict() (what the CLI serializes) exposes every required
    # aggregate metric key.
    report = IndexCompletionReport(
        planned=24,
        succeeded=24,
        complete=True,
        attempts=48,
        failed=0,
        document_posts=24,
        status_polls=100,
        track_resume_operations=24,
        document_reposts_after_accepted_track=0,
    )
    d = report.as_dict()
    for key in (
        "graph_index_planned",
        "graph_index_attempts",
        "graph_index_successes",
        "graph_index_failures",
        "index_membership_attempts",
        "document_posts",
        "index_status_polls",
        "track_resume_operations",
        "document_reposts_after_accepted_track",
    ):
        assert key in d, key
    assert d["graph_index_planned"] == 24
    assert d["graph_index_successes"] == 24
    assert d["document_posts"] == 24
    assert d["document_reposts_after_accepted_track"] == 0


def test_failure_report_retains_index_metrics():
    # §28: a failed (incomplete) index stage still surfaces the counters (not dropped).
    report = IndexCompletionReport(
        planned=24, succeeded=20, complete=False, attempts=48, failed=4,
        document_posts=24, status_polls=440, track_resume_operations=24,
        document_reposts_after_accepted_track=0,
    )
    d = report.as_dict()
    assert d["graph_index_successes"] == 20
    assert d["graph_index_failures"] == 4
    assert d["complete_24_of_24"] is False
    assert d["index_status_polls"] == 440


def test_per_membership_record_is_content_safe():
    # §11/§29: a per-membership record exposes ids/statuses/counters ONLY — no source text,
    # provider body, headers or secret. Every value is an id string, an int, or a status.
    rec = IndexOperationRecord(
        operation_id="idx-000",
        canonical_source_id="src-1",
        notebook_id="nb-1",
        workspace_id="ws-1",
        derived_document_id="doc-abc",
        technical_status=DriverTechnicalOutcome.COMPLETED,
        attempt_count=2,
        elapsed_ms=1234,
        error_category=None,
        document_post_count=1,
        status_poll_count=11,
        resume_count=1,
        document_reposts_after_accepted_track=0,
        last_track_status="PROCESSED",
    )
    d = rec.as_dict()
    for key in (
        "document_post_count",
        "status_poll_count",
        "resume_count",
        "document_reposts_after_accepted_track",
        "last_track_status",
    ):
        assert key in d, key
    # content-safety: no free-text/body/secret fields present.
    for banned in ("text", "canonical_text", "body", "response", "authorization", "api_key"):
        assert banned not in d
    assert d["document_post_count"] == 1
    assert d["last_track_status"] == "PROCESSED"


# --------------------------------------------------------------------------- #
# §32/§33/§34/§35/§36 — EW6→EW7 governance repoint + successor lifecycle
# --------------------------------------------------------------------------- #


def test_ew7_governance_repoint_complete():
    # §32: the current approved provider-authorization identity is the exact EW7 successor tag;
    # the re-exported grant/manifest identity follows it. EW6 is now HISTORICAL (not current).
    assert (
        EXPECTED_EW7_CHECKPOINT_TAG == "graphrag-pn02db1ew7-index-observation-timing-approved"
    )
    assert EXPECTED_EW7_CHECKPOINT_TAG != EXPECTED_EW6_CHECKPOINT_TAG
    assert current_approved_b1_r2_checkpoint() == EXPECTED_EW7_CHECKPOINT_TAG
    assert B.B1_R2_EXPECTED_CHECKPOINT_TAG == EXPECTED_EW7_CHECKPOINT_TAG
    assert current_approved_b1_r2_checkpoint() != EXPECTED_EW6_CHECKPOINT_TAG


def test_ew7_successor_tag_git_state_is_lifecycle_valid():
    # §35: lifecycle-aware — never a permanent real-tag-absence assumption. STATE A
    # (pre-checkpoint): the EW7 tag is absent → empty peel. STATE B (post-checkpoint): the EXACT
    # tag is present, peels to a 40-hex commit == the authorized HEAD.
    obs = RealTrustedB1R2Reader().observe(EXPECTED_EW7_CHECKPOINT_TAG)
    if not obs.observed_tag_exists:
        assert obs.observed_tag_peel == ""  # STATE A
    else:
        assert obs.checkpoint_tag == EXPECTED_EW7_CHECKPOINT_TAG
        assert len(obs.observed_tag_peel) == 40 and all(
            c in "0123456789abcdef" for c in obs.observed_tag_peel
        )
        assert obs.observed_tag_peel == obs.observed_head  # STATE B


def test_ew7_exact_tag_with_peel_is_accepted_by_git_gate():
    # §32: the EXACT EW7 successor identity, trust-observed at the authorized HEAD with a matching
    # baseline, satisfies the control-plane Git gate (no real tag created).
    future = "e7e7e7e7" + "0" * 32
    grant = C.frozen_test_grant(b1_r2_checkpoint=EXPECTED_EW7_CHECKPOINT_TAG)
    reasons = verify_b1_r2_checkpoint(
        reader=C.b1r2_reader_ok(tag=EXPECTED_EW7_CHECKPOINT_TAG, peel=future, head=future),
        operator_grant=grant,
        approved_expected_checkpoint=EXPECTED_EW7_CHECKPOINT_TAG,
        git_baseline=C.clean_git_baseline(commit=future, tag=EXPECTED_EW7_CHECKPOINT_TAG),
    )
    assert reasons == []


def test_wrong_ew7_peel_fails_closed():
    # §34: the exact EW7 tag observed with a peel that does NOT match the authorized HEAD fails
    # closed.
    head = "aaaa1111" + "0" * 32
    wrong = "bbbb2222" + "0" * 32
    grant = C.frozen_test_grant(b1_r2_checkpoint=EXPECTED_EW7_CHECKPOINT_TAG)
    reasons = verify_b1_r2_checkpoint(
        reader=C.b1r2_reader_ok(tag=EXPECTED_EW7_CHECKPOINT_TAG, peel=wrong, head=head),
        operator_grant=grant,
        approved_expected_checkpoint=EXPECTED_EW7_CHECKPOINT_TAG,
        git_baseline=C.clean_git_baseline(commit=head, tag=EXPECTED_EW7_CHECKPOINT_TAG),
    )
    assert "b1_r2_tag_not_at_authorized_head" in reasons


@pytest.mark.parametrize(
    "substitute",
    [
        "graphrag-arbitrary-unrelated-tag",
        EXPECTED_EW6_CHECKPOINT_TAG,
        EXPECTED_EW5_CHECKPOINT_TAG,
        EXPECTED_EW4_CHECKPOINT_TAG,
        EXPECTED_EW3_CHECKPOINT_TAG,
        EXPECTED_EW2_CHECKPOINT_TAG,
        EXPECTED_EW1_CHECKPOINT_TAG,
        EXPECTED_PF1_CHECKPOINT_TAG,
        EXPECTED_B1_R2_CHECKPOINT_TAG,
    ],
)
def test_historical_or_arbitrary_tag_cannot_substitute_for_ew7(substitute):
    # §33: naming ANY historical (EW6/EW5/EW4/EW3/EW2/EW1/PF1/B1-R2) or arbitrary tag as the
    # approved-expected identity cannot satisfy the EW7 checkpoint.
    future = "e7e7e7e7" + "0" * 32
    grant = C.frozen_test_grant(b1_r2_checkpoint=EXPECTED_EW7_CHECKPOINT_TAG)
    reasons = verify_b1_r2_checkpoint(
        reader=C.b1r2_reader_ok(tag=substitute, peel=future, head=future),
        operator_grant=grant,
        approved_expected_checkpoint=substitute,
        git_baseline=C.clean_git_baseline(commit=future, tag=substitute),
    )
    assert reasons, f"{substitute} must not satisfy the EW7 checkpoint"


def test_ew6_and_ew5_are_distinct_historical_identities():
    # §33/§36: EW6 and EW5 remain DISTINCT retained-historical constants (identities preserved).
    ids = {
        EXPECTED_EW7_CHECKPOINT_TAG,
        EXPECTED_EW6_CHECKPOINT_TAG,
        EXPECTED_EW5_CHECKPOINT_TAG,
        EXPECTED_EW4_CHECKPOINT_TAG,
        EXPECTED_EW3_CHECKPOINT_TAG,
        EXPECTED_EW2_CHECKPOINT_TAG,
        EXPECTED_EW1_CHECKPOINT_TAG,
        EXPECTED_PF1_CHECKPOINT_TAG,
        EXPECTED_B1_R2_CHECKPOINT_TAG,
    }
    assert len(ids) == 9  # all nine identities are distinct
    assert EXPECTED_EW6_CHECKPOINT_TAG == "graphrag-pn02db1ew6-index-conflict-recovery-approved"


def test_ew7_tag_alone_does_not_authorize_provider_run():
    # §34: even with the Git gate satisfiable (exact EW7 tag at HEAD), the provider-run
    # governance flag stays NO — a valid checkpoint tag ALONE can never mint a live run.
    assert PN02_PROVIDER_RUN_AUTHORIZED is False
