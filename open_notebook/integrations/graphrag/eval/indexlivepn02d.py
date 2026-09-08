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
    require_indexing_authorization,
    require_operation_allowed,
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

STATE_PROCESSED = "PROCESSED"
STATE_FAILED = "FAILED"
STATE_IN_PROGRESS = "IN_PROGRESS"
STATE_TIMEOUT = "TIMEOUT"

#: Coarse, content-safe error categories (never raw provider text, design §12/§20).
ERR_TRANSIENT = "TRANSIENT"
ERR_NON_RETRYABLE = "NON_RETRYABLE"
ERR_CAP = "CAP_EXHAUSTED"
ERR_ROUTING = "ROUTING"

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
        self._provider_run_auth = provider_run_auth
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

        while attempts < self._max_attempts:
            # Budget: reserve one ATTEMPT before dispatch; cap exhaustion refuses it.
            try:
                self._budget.reserve(BudgetClass.GRAPH_INDEX_ATTEMPT)
            except WorkloadCapExceeded:
                terminal = DriverTechnicalOutcome.FAILED_INDEX_CAP
                last_error_category = ERR_CAP
                break
            attempts += 1

            outcome, category, retryable = await self._one_attempt(
                client, canonical_source_id=canonical_source_id, text=text
            )
            last_error_category = category
            if outcome is DriverTechnicalOutcome.COMPLETED:
                terminal = outcome
                last_error_category = None
                break
            terminal = outcome
            if not (retryable and attempts < self._max_attempts):
                break
            # else: loop and retry (delete-then-insert semantics in a real run, §17)

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
        self, client: CellIndexClient, *, canonical_source_id: str, text: str
    ) -> Tuple[DriverTechnicalOutcome, Optional[str], bool]:
        """One submit+poll attempt. Returns (outcome, error_category, retryable)."""
        submit: IndexSubmitResult = await client.submit(
            source_id=canonical_source_id, canonical_text=text
        )
        if not submit.accepted or not submit.track_id:
            # detail is EPHEMERAL — consumed only to classify, never stored (§32).
            retryable = is_transient_reason(submit.detail)
            return (
                DriverTechnicalOutcome.FAILED_INDEX_SUBMIT,
                ERR_TRANSIENT if retryable else ERR_NON_RETRYABLE,
                retryable,
            )

        for _ in range(self._max_polls):
            status: IndexStatusResult = await client.status(track_id=submit.track_id)
            if status.state == STATE_PROCESSED:
                return (DriverTechnicalOutcome.COMPLETED, None, False)
            if status.state == STATE_FAILED:
                retryable = is_transient_reason(status.detail)
                return (
                    DriverTechnicalOutcome.FAILED_INDEX_COMPLETION,
                    ERR_TRANSIENT if retryable else ERR_NON_RETRYABLE,
                    retryable,
                )
            if status.state == STATE_TIMEOUT:
                # A timeout is a transient completion failure (retry if budget allows).
                return (
                    DriverTechnicalOutcome.FAILED_INDEX_COMPLETION,
                    ERR_TRANSIENT,
                    True,
                )
            # IN_PROGRESS -> keep polling.
        # Ran out of polls without a terminal state -> completion failure (transient).
        return (DriverTechnicalOutcome.FAILED_INDEX_COMPLETION, ERR_TRANSIENT, True)

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
    "IndexClientFactory",
    "IndexOperationRecord",
    "IndexCompletionReport",
    "MembershipIndexExecutor",
]
