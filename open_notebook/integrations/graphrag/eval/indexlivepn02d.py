"""Per-membership graph-index executor for the PN02 live driver (PN02D-B0B).

EVALUATION-ONLY. Nothing in production imports this (``PRODUCTION_IMPORTS_EVAL =
NO``). One ``(source_key, notebook_id)`` membership edge -> one index op against
THAT notebook's routed sidecar; submit -> poll -> terminal; bounded retry (design
§5-C/§12/§16/§17). The LightRAG index client is INJECTED (a fake drives every B0B
test; the real ``live_indexer08.RealCellIndexClient`` factory satisfies the same
``CellIndexClient`` Protocol so a future B1 needs no new code). Completion is
observed via the track-status surface — submit acceptance is NEVER treated as
completion (design §17/§26). Retries use the frozen ``index_retry08.is_transient_reason``
decision-twin (design §16); the stateful budget guard is checked BEFORE every attempt
(design §21/§23). Raw error text is consumed transiently to classify and is never
stored on a record (design §12/§32).
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional, Tuple

from open_notebook.integrations.graphrag.eval.authlivepn02d import (
    IndexingAuthorization,
    PN02ProviderRunAuthorization,
    ProviderOperationClass,
    assert_run_ids_consistent,
    require_indexing_authorization,
    require_operation_allowed,
    require_provider_run_authorization,
)
from open_notebook.integrations.graphrag.eval.budgetlivepn02d import (
    BudgetClass,
    StatefulBudgetGuard,
    WorkloadCapExceeded,
)
from open_notebook.integrations.graphrag.eval.docidpn02d import (
    INDEX_STATUS_FAILED,
    INDEX_STATUS_PROCESSED,
    DerivedDocMappingStore,
)
from open_notebook.integrations.graphrag.eval.index_retry08 import is_transient_reason
from open_notebook.integrations.graphrag.eval.live_indexer08 import (
    CellIndexClient,
    IndexStatusResult,
    IndexSubmitResult,
)
from open_notebook.integrations.graphrag.eval.outcomespn02d import (
    DriverTechnicalOutcome,
)
from open_notebook.integrations.graphrag.eval.routelivepn02d import (
    NotebookRuntimeRoute,
    PN02Router,
)
from open_notebook.integrations.graphrag.eval.workloadpn02 import (
    MAX_INDEX_ATTEMPTS_PER_OPERATION,
    PLANNED_GRAPH_INDEX_OPERATIONS,
)
from open_notebook.integrations.graphrag.models import GraphRAGConflictError

STATE_PROCESSED = "PROCESSED"
STATE_FAILED = "FAILED"
STATE_IN_PROGRESS = "IN_PROGRESS"
STATE_TIMEOUT = "TIMEOUT"

#: Coarse, content-safe error categories (never raw provider text, design §12/§20).
ERR_TRANSIENT = "TRANSIENT"
ERR_NON_RETRYABLE = "NON_RETRYABLE"
ERR_CAP = "CAP_EXHAUSTED"
ERR_ROUTING = "ROUTING"
#: PN02D-B1-EW6 (index-conflict recovery): a LightRAG HTTP 409 ("document for this
#: file_source already exists") observed on a FRESH submit that this operation holds no
#: prior accepted track for. Fail-closed, non-retryable — never delete/guess/blind-success.
ERR_CONFLICT = "CONFLICT"

#: () -> per-route index client. In B1 this wraps ``live_indexer08.RealCellIndexClient``
#: bound to the route's endpoint; in B0B a fake is injected.
IndexClientFactory = Callable[[NotebookRuntimeRoute], CellIndexClient]


@dataclass(frozen=True)
class IndexOperationRecord:
    """Content-safe per-membership index result (design §20). No raw payload/secret."""

    operation_id: str
    canonical_source_id: str
    notebook_id: str
    workspace_id: str
    derived_document_id: str
    technical_status: DriverTechnicalOutcome
    attempt_count: int
    elapsed_ms: Optional[int] = None
    error_category: Optional[str] = None

    @property
    def succeeded(self) -> bool:
        return self.technical_status is DriverTechnicalOutcome.COMPLETED

    def as_dict(self) -> Dict[str, object]:
        return {
            "operation_id": self.operation_id,
            "canonical_source_id": self.canonical_source_id,
            "notebook_id": self.notebook_id,
            "workspace_id": self.workspace_id,
            "derived_document_id": self.derived_document_id,
            "technical_status": self.technical_status.value,
            "attempt_count": self.attempt_count,
            "elapsed_ms": self.elapsed_ms,
            "error_category": self.error_category,
        }


@dataclass(frozen=True)
class IndexCompletionReport:
    """The hard 24/24 completeness gate outcome (design §15/§24)."""

    planned: int
    succeeded: int
    complete: bool

    def as_dict(self) -> Dict[str, object]:
        return {
            "planned_graph_index_operations": self.planned,
            "indexed_workspace_memberships": self.succeeded,
            "complete_24_of_24": self.complete,
        }


class MembershipIndexExecutor:
    """Executes the 24 membership index ops with bounded retry + budget enforcement."""

    def __init__(
        self,
        *,
        router: PN02Router,
        budget: StatefulBudgetGuard,
        mapping_store: DerivedDocMappingStore,
        indexing_auth: IndexingAuthorization,
        provider_run_auth: PN02ProviderRunAuthorization,
        client_factory: IndexClientFactory,
        memberships: Tuple[Tuple[str, str], ...],
        max_attempts_per_operation: int = MAX_INDEX_ATTEMPTS_PER_OPERATION,
        max_polls: int = 10,
    ) -> None:
        self._router = router
        self._budget = budget
        self._store = mapping_store
        self._indexing_auth = require_indexing_authorization(indexing_auth)
        # L-2 (design §8): validate the provider-run capability TYPE at init, so a
        # real (or offline) executor can never be constructed with a look-alike object
        # that merely has similar fields (unforgeable ``_AUTH_KEY``-minted capability).
        self._provider_run_auth = require_provider_run_authorization(provider_run_auth)
        # L-1 (design §8): the run identity is the provider-run capability's run_id;
        # every other capability must agree (cross-checked again before each dispatch).
        self._run_id = self._provider_run_auth.run_id
        assert_run_ids_consistent(
            self._run_id, self._provider_run_auth, self._indexing_auth
        )
        self._client_factory = client_factory
        self._memberships = frozenset(memberships)
        self._max_attempts = max_attempts_per_operation
        self._max_polls = max_polls

    async def index_membership(
        self,
        *,
        canonical_source_id: str,
        notebook_id: str,
        text: str,
        operation_id: str,
    ) -> IndexOperationRecord:
        """Index ONE membership edge; submit -> poll -> terminal, bounded retry (§16/§17)."""
        require_indexing_authorization(self._indexing_auth)
        # L-1: fail closed BEFORE dispatch if any capability's run_id drifted.
        assert_run_ids_consistent(
            self._run_id, self._provider_run_auth, self._indexing_auth
        )
        require_operation_allowed(
            self._provider_run_auth, ProviderOperationClass.GRAPH_INDEX
        )

        # Route (rejects an unauthorized/non-member workspace BEFORE any op, §40.6).
        try:
            route = self._router.resolve_membership_route(
                canonical_source_id, notebook_id, memberships=self._memberships
            )
        except Exception:
            record = IndexOperationRecord(
                operation_id=operation_id,
                canonical_source_id=canonical_source_id,
                notebook_id=notebook_id,
                workspace_id="",
                derived_document_id="",
                technical_status=DriverTechnicalOutcome.FAILED_RUNTIME_ATTESTATION,
                attempt_count=0,
                error_category=ERR_ROUTING,
            )
            return record

        mapping = self._store.register(
            canonical_source_id=canonical_source_id,
            workspace_id=route.workspace_id,
            notebook_id=notebook_id,
        )
        mapping.index_operation_id = operation_id

        # One logical index operation (design §57 — logical, separate from attempts).
        self._budget.reserve(BudgetClass.GRAPH_INDEX_OPERATION)

        client = self._client_factory(route)
        started = time.monotonic()
        attempts = 0
        last_error_category: Optional[str] = None
        terminal = DriverTechnicalOutcome.FAILED_INDEX_SUBMIT
        # PN02D-B1-EW6 (index-conflict recovery, Option A — non-destructive): once LightRAG
        # ACCEPTS a submit and issues a track_id, a retry must RESUME/observe that exact
        # accepted track — never re-POST the same ``file_source`` (the re-POST is what
        # produced the EXEC #8 HTTP 409 → GraphRAGConflictError). ``resume_track_id`` holds
        # that accepted track for the NEXT bounded attempt. It lives ONLY in this operation's
        # scope, bound to THIS route's own client, so a resume can never cross a cell/workspace
        # or a source (§9/§23/§24). No delete, no extra attempt budget: the frozen envelope
        # (MAX_INDEX_ATTEMPTS_PER_OPERATION=2 / MAX_GRAPH_INDEX_ATTEMPTS=48 / GRAPH_DELETE) is
        # unchanged. Option A changes only what a retry attempt DOES — observe the accepted
        # track's status instead of re-POSTing — not how many attempts an operation may spend:
        # a resume iteration still reserves its OWN GRAPH_INDEX_ATTEMPT below and is bounded by
        # the same per-operation (2) and total (48) caps. POST and status polling are distinct;
        # no second POST occurs merely because the first bounded poll window exhausted.
        resume_track_id: Optional[str] = None

        while attempts < self._max_attempts:
            # Budget: reserve one ATTEMPT before dispatch; cap exhaustion refuses it.
            try:
                self._budget.reserve(BudgetClass.GRAPH_INDEX_ATTEMPT)
            except WorkloadCapExceeded:
                terminal = DriverTechnicalOutcome.FAILED_INDEX_CAP
                last_error_category = ERR_CAP
                break
            attempts += 1

            outcome, category, retryable, accepted_track_id, resumable = (
                await self._one_attempt(
                    client,
                    canonical_source_id=canonical_source_id,
                    text=text,
                    resume_track_id=resume_track_id,
                )
            )
            last_error_category = category
            if outcome is DriverTechnicalOutcome.COMPLETED:
                terminal = outcome
                last_error_category = None
                break
            terminal = outcome
            # EW6: carry the accepted track forward ONLY while it is still resumable
            # (accepted + not yet terminal — poll window exhausted / still processing). A
            # terminal completion failure clears it, so the pre-existing transient retry
            # semantics are preserved (a genuinely non-accepted/terminal op re-submits as
            # before). A non-resumable attempt therefore starts the next attempt fresh.
            resume_track_id = accepted_track_id if (resumable and accepted_track_id) else None
            if not (retryable and attempts < self._max_attempts):
                break

        elapsed_ms = int((time.monotonic() - started) * 1000)
        mapping.index_status = (
            INDEX_STATUS_PROCESSED
            if terminal is DriverTechnicalOutcome.COMPLETED
            else INDEX_STATUS_FAILED
        )
        return IndexOperationRecord(
            operation_id=operation_id,
            canonical_source_id=canonical_source_id,
            notebook_id=notebook_id,
            workspace_id=route.workspace_id,
            derived_document_id=mapping.derived_document_id,
            technical_status=terminal,
            attempt_count=attempts,
            elapsed_ms=elapsed_ms,
            error_category=last_error_category,
        )

    async def _one_attempt(
        self,
        client: CellIndexClient,
        *,
        canonical_source_id: str,
        text: str,
        resume_track_id: Optional[str] = None,
    ) -> Tuple[DriverTechnicalOutcome, Optional[str], bool, Optional[str], bool]:
        """One index attempt. Returns
        ``(outcome, error_category, retryable, accepted_track_id, resumable)``.

        PN02D-B1-EW6 index-conflict recovery (Option A — non-destructive, envelope-preserving):

        * ``resume_track_id`` set  -> RESUME: bounded-poll THAT already-accepted track. No
          submit, no re-POST — this is the recovery for the EXEC #8 defect (a prior accepted
          submit is observed to completion instead of blindly re-POSTing the same
          ``file_source``, which is what produced the LightRAG HTTP 409). Identity is proven
          by construction: this operation's own route-bound ``client`` + its own issued
          ``track_id`` (§9/§23/§24 — never another cell/workspace or source).
        * ``resume_track_id`` None -> fresh submit, then bounded-poll the issued track. A
          ``GraphRAGConflictError`` (HTTP 409) here means the document exists but this
          operation holds NO prior accepted track for it — fail closed (§7/§8/§22): never
          delete, guess, adopt an unrelated document, or mark indexing complete.

        ``accepted_track_id`` is the track LightRAG issued (or the resumed track) so the
        caller can continue observing THAT exact operation; ``resumable`` is True only when
        the attempt ended accepted-but-not-yet-terminal (poll window exhausted / still
        processing), so a terminal completion failure keeps the pre-existing retry semantics.
        """
        if resume_track_id is not None:
            # RESUME the exact prior accepted track (NO submit / NO re-POST).
            return await self._poll_track(client, resume_track_id)

        try:
            submit: IndexSubmitResult = await client.submit(
                source_id=canonical_source_id, canonical_text=text
            )
        except GraphRAGConflictError:
            # EW6 §8/§22: 409 on a FRESH submit with no prior accepted track for this
            # operation -> fail closed (non-retryable). Never delete / guess / blind-success.
            return (
                DriverTechnicalOutcome.FAILED_INDEX_SUBMIT,
                ERR_CONFLICT,
                False,
                None,
                False,
            )
        if not submit.accepted or not submit.track_id:
            # detail is EPHEMERAL — consumed only to classify, never stored (§32).
            retryable = is_transient_reason(submit.detail)
            return (
                DriverTechnicalOutcome.FAILED_INDEX_SUBMIT,
                ERR_TRANSIENT if retryable else ERR_NON_RETRYABLE,
                retryable,
                None,
                False,
            )
        return await self._poll_track(client, submit.track_id)

    async def _poll_track(
        self, client: CellIndexClient, track_id: str
    ) -> Tuple[DriverTechnicalOutcome, Optional[str], bool, Optional[str], bool]:
        """Bounded observation of ONE accepted track (acceptance != completion, §17/§26).

        Returns the same 5-tuple as ``_one_attempt``. Poll-window exhaustion or an explicit
        TIMEOUT (still processing, never reached a terminal state) returns ``resumable=True``
        carrying the SAME ``track_id`` so the next bounded attempt RESUMES it (no re-POST);
        a terminal FAILED returns ``resumable=False`` (pre-existing retry semantics — the op
        may re-submit as before, §13). Bounded by ``self._max_polls`` per attempt AND by
        ``self._max_attempts`` overall, so a preserved track can never be polled unbounded
        (§5/§26).
        """
        for _ in range(self._max_polls):
            status: IndexStatusResult = await client.status(track_id=track_id)
            if status.state == STATE_PROCESSED:
                return (DriverTechnicalOutcome.COMPLETED, None, False, track_id, False)
            if status.state == STATE_FAILED:
                retryable = is_transient_reason(status.detail)
                return (
                    DriverTechnicalOutcome.FAILED_INDEX_COMPLETION,
                    ERR_TRANSIENT if retryable else ERR_NON_RETRYABLE,
                    retryable,
                    track_id,
                    False,  # terminal completion failure — not resumable (§13)
                )
            if status.state == STATE_TIMEOUT:
                # A timeout is a transient completion failure; the accepted track stays
                # resumable (resume the SAME track next attempt — never re-POST).
                return (
                    DriverTechnicalOutcome.FAILED_INDEX_COMPLETION,
                    ERR_TRANSIENT,
                    True,
                    track_id,
                    True,
                )
            # IN_PROGRESS -> keep polling.
        # Ran out of polls without a terminal state -> still processing; resumable.
        return (
            DriverTechnicalOutcome.FAILED_INDEX_COMPLETION,
            ERR_TRANSIENT,
            True,
            track_id,
            True,
        )

    async def index_all(
        self, plan: List[Tuple[str, str, str]]
    ) -> Tuple[Tuple[IndexOperationRecord, ...], IndexCompletionReport]:
        """Index every planned membership (deterministic order) + completeness gate.

        ``plan`` is a list of ``(canonical_source_id, notebook_id, text)`` in the frozen
        iteration order (design §24). Returns the records and the 24/24 report.
        """
        records: List[IndexOperationRecord] = []
        for i, (source_id, notebook_id, text) in enumerate(plan):
            record = await self.index_membership(
                canonical_source_id=source_id,
                notebook_id=notebook_id,
                text=text,
                operation_id=f"idx-{i:03d}",
            )
            records.append(record)
        succeeded = sum(1 for r in records if r.succeeded)
        report = IndexCompletionReport(
            planned=len(plan),
            succeeded=succeeded,
            complete=(len(plan) == PLANNED_GRAPH_INDEX_OPERATIONS and succeeded == len(plan)),
        )
        return tuple(records), report


__all__ = [
    "STATE_PROCESSED",
    "STATE_FAILED",
    "STATE_IN_PROGRESS",
    "STATE_TIMEOUT",
    "ERR_TRANSIENT",
    "ERR_NON_RETRYABLE",
    "ERR_CAP",
    "ERR_ROUTING",
    "ERR_CONFLICT",
    "IndexClientFactory",
    "IndexOperationRecord",
    "IndexCompletionReport",
    "MembershipIndexExecutor",
]
