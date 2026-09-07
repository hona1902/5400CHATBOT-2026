"""Machine-readable PN02 provider-workload ledger + retry policy (task §13/§35-§38).

EVALUATION-ONLY. Nothing in production imports this. Freezes every provider-backed
workload in harness-countable units (PN02A §13) and validates the exact arithmetic
so a FUTURE live runner cannot silently exceed a cap. PN02B performs NO live
indexing, NO queries, and NO final-answer calls — this module only represents and
validates the budget.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from open_notebook.integrations.graphrag.eval.datasetpn02 import (
    FROZEN_CANONICAL_SOURCE_COUNT,
    FROZEN_QUERY_COUNT,
    FROZEN_WORKSPACE_MEMBERSHIP_COUNT,
    FixturePN02,
)

# Frozen caps (PN02A §13 / task §35-§38).
PLANNED_GRAPH_INDEX_OPERATIONS = 24
MAX_INDEX_ATTEMPTS_PER_OPERATION = 2
MAX_GRAPH_INDEX_ATTEMPTS = 48
MEMBERSHIP_REMOVAL_REINDEX_OPS = 0
GRAPH_DELETE_OPERATIONS = 1
MAX_CANONICAL_SOURCE_EMBEDDINGS = 21

BASELINE_STAGE1_GD_QUERIES = 24
MEMBERSHIP_REMOVAL_GD_QUERIES = 2
MAX_GD_QUERIES = 26

BASELINE_STAGE1_VECTOR_QUERIES = 24
MEMBERSHIP_REMOVAL_VECTOR_QUERIES = 2
MAX_VECTOR_QUERY_OPERATIONS = 26
MAX_QUERY_EMBEDDING_OPERATIONS = 26

STAGE2_FINAL_ANSWER_CALLS = 72          # 24 queries × 3 arms
MEMBERSHIP_REMOVAL_FINAL_ANSWER = 0
MAX_FINAL_ANSWER_CALLS = 72
QA_ARMS = 3

JUDGE_MODEL_CALLS = 0
FINAL_ANSWER_RETRIES = 0                 # no retry-to-improve
MAX_QUERY_TECHNICAL_RETRIES = 1          # transport-level only


class WorkloadCapExceeded(RuntimeError):
    """An observed workload count exceeded its frozen cap (fail-closed)."""


class WorkloadLedgerError(ValueError):
    """The frozen workload arithmetic is internally inconsistent."""


@dataclass(frozen=True)
class WorkloadLedger:
    planned_graph_index_operations: int
    max_index_attempts_per_operation: int
    max_graph_index_attempts: int
    membership_removal_reindex_ops: int
    graph_delete_operations: int
    max_canonical_source_embeddings: int
    baseline_stage1_gd_queries: int
    membership_removal_gd_queries: int
    max_gd_queries: int
    baseline_stage1_vector_queries: int
    membership_removal_vector_queries: int
    max_vector_query_operations: int
    max_query_embedding_operations: int
    stage2_final_answer_calls: int
    membership_removal_final_answer: int
    max_final_answer_calls: int
    judge_model_calls: int
    final_answer_retries: int
    max_query_technical_retries: int

    def validate(self) -> None:
        """Enforce the exact §36 arithmetic (fail-closed on any inconsistency)."""
        if (
            self.max_graph_index_attempts
            != self.planned_graph_index_operations * self.max_index_attempts_per_operation
        ):
            raise WorkloadLedgerError(
                "MAX_GRAPH_INDEX_ATTEMPTS != PLANNED_GRAPH_INDEX_OPERATIONS × "
                "MAX_INDEX_ATTEMPTS_PER_OPERATION"
            )
        if self.max_graph_index_attempts != 48:
            raise WorkloadLedgerError("MAX_GRAPH_INDEX_ATTEMPTS != 48")
        if self.membership_removal_reindex_ops != 0:
            raise WorkloadLedgerError("MEMBERSHIP_REMOVAL_REINDEX_OPS != 0")
        if self.graph_delete_operations != 1:
            raise WorkloadLedgerError("GRAPH_DELETE_OPERATIONS != 1")
        # GD: 24 baseline + 2 removal re-probe + 0 other = 26 (no headroom).
        if (
            self.max_gd_queries
            != self.baseline_stage1_gd_queries + self.membership_removal_gd_queries
        ):
            raise WorkloadLedgerError("MAX_GD_QUERIES != baseline + removal")
        if self.max_gd_queries != 26:
            raise WorkloadLedgerError("MAX_GD_QUERIES != 26")
        # Vector: 24 baseline + 2 removal validation = 26.
        if (
            self.max_vector_query_operations
            != self.baseline_stage1_vector_queries
            + self.membership_removal_vector_queries
        ):
            raise WorkloadLedgerError("MAX_VECTOR_QUERY_OPERATIONS != baseline + removal")
        if self.max_vector_query_operations != 26:
            raise WorkloadLedgerError("MAX_VECTOR_QUERY_OPERATIONS != 26")
        if self.max_query_embedding_operations != self.max_vector_query_operations:
            raise WorkloadLedgerError(
                "MAX_QUERY_EMBEDDING_OPERATIONS != MAX_VECTOR_QUERY_OPERATIONS"
            )
        # Final answers: 24 × 3 = 72; removal 0; judge 0; retry 0.
        if self.stage2_final_answer_calls != FROZEN_QUERY_COUNT * QA_ARMS:
            raise WorkloadLedgerError("STAGE2_FINAL_ANSWER_CALLS != 24 × 3")
        if self.membership_removal_final_answer != 0:
            raise WorkloadLedgerError("MEMBERSHIP_REMOVAL_FINAL_ANSWER != 0")
        if self.max_final_answer_calls != self.stage2_final_answer_calls:
            raise WorkloadLedgerError("MAX_FINAL_ANSWER_CALLS != STAGE2_FINAL_ANSWER_CALLS")
        if self.max_final_answer_calls != 72:
            raise WorkloadLedgerError("MAX_FINAL_ANSWER_CALLS != 72")
        if self.judge_model_calls != 0:
            raise WorkloadLedgerError("JUDGE_MODEL_CALLS != 0")
        if self.final_answer_retries != 0:
            raise WorkloadLedgerError("FINAL_ANSWER_RETRIES != 0 (no retry-to-improve)")
        if self.max_query_technical_retries != 1:
            raise WorkloadLedgerError("MAX_QUERY_TECHNICAL_RETRIES != 1")

    def check_cap(self, name: str, observed: int, cap: int) -> None:
        """Raise ``WorkloadCapExceeded`` if an observed count exceeds a cap.

        The single enforcement primitive a future live runner must call before any
        provider-backed op so caps cannot be exceeded silently (task §38).
        """
        if observed > cap:
            raise WorkloadCapExceeded(f"{name}: observed {observed} > cap {cap}")


def frozen_ledger() -> WorkloadLedger:
    """The frozen PN02 workload ledger (already arithmetic-consistent)."""
    ledger = WorkloadLedger(
        planned_graph_index_operations=PLANNED_GRAPH_INDEX_OPERATIONS,
        max_index_attempts_per_operation=MAX_INDEX_ATTEMPTS_PER_OPERATION,
        max_graph_index_attempts=MAX_GRAPH_INDEX_ATTEMPTS,
        membership_removal_reindex_ops=MEMBERSHIP_REMOVAL_REINDEX_OPS,
        graph_delete_operations=GRAPH_DELETE_OPERATIONS,
        max_canonical_source_embeddings=MAX_CANONICAL_SOURCE_EMBEDDINGS,
        baseline_stage1_gd_queries=BASELINE_STAGE1_GD_QUERIES,
        membership_removal_gd_queries=MEMBERSHIP_REMOVAL_GD_QUERIES,
        max_gd_queries=MAX_GD_QUERIES,
        baseline_stage1_vector_queries=BASELINE_STAGE1_VECTOR_QUERIES,
        membership_removal_vector_queries=MEMBERSHIP_REMOVAL_VECTOR_QUERIES,
        max_vector_query_operations=MAX_VECTOR_QUERY_OPERATIONS,
        max_query_embedding_operations=MAX_QUERY_EMBEDDING_OPERATIONS,
        stage2_final_answer_calls=STAGE2_FINAL_ANSWER_CALLS,
        membership_removal_final_answer=MEMBERSHIP_REMOVAL_FINAL_ANSWER,
        max_final_answer_calls=MAX_FINAL_ANSWER_CALLS,
        judge_model_calls=JUDGE_MODEL_CALLS,
        final_answer_retries=FINAL_ANSWER_RETRIES,
        max_query_technical_retries=MAX_QUERY_TECHNICAL_RETRIES,
    )
    ledger.validate()
    return ledger


def validate_against_fixture(
    ledger: WorkloadLedger, fx: FixturePN02
) -> None:
    """Cross-check the ledger's index/embedding counts against the actual fixture.

    PLANNED_GRAPH_INDEX_OPERATIONS = membership edges (18 unique + 6 shared, §37);
    MAX_CANONICAL_SOURCE_EMBEDDINGS = canonical Sources (single-copy in ON store).
    These MUST equal the frozen constants AND the live fixture (task §37).
    """
    membership_edges = len(fx.memberships)
    canonical_sources = len(fx.sources)
    if membership_edges != FROZEN_WORKSPACE_MEMBERSHIP_COUNT:
        raise WorkloadLedgerError(
            f"fixture membership edges {membership_edges} != {FROZEN_WORKSPACE_MEMBERSHIP_COUNT}"
        )
    if ledger.planned_graph_index_operations != membership_edges:
        raise WorkloadLedgerError(
            f"PLANNED_GRAPH_INDEX_OPERATIONS {ledger.planned_graph_index_operations} "
            f"!= fixture membership edges {membership_edges}"
        )
    if canonical_sources != FROZEN_CANONICAL_SOURCE_COUNT:
        raise WorkloadLedgerError(
            f"fixture canonical sources {canonical_sources} != {FROZEN_CANONICAL_SOURCE_COUNT}"
        )
    if ledger.max_canonical_source_embeddings != canonical_sources:
        raise WorkloadLedgerError(
            f"MAX_CANONICAL_SOURCE_EMBEDDINGS {ledger.max_canonical_source_embeddings} "
            f"!= fixture canonical sources {canonical_sources}"
        )
    if len(fx.queries) != FROZEN_QUERY_COUNT:
        raise WorkloadLedgerError("fixture query count != 24")
    if ledger.baseline_stage1_gd_queries != len(fx.queries):
        raise WorkloadLedgerError("BASELINE_STAGE1_GD_QUERIES != fixture query count")


def as_dict(ledger: Optional[WorkloadLedger] = None) -> dict:
    """Content-safe ledger view (counts only) for the run manifest / report."""
    ledger = ledger or frozen_ledger()
    return {
        "PLANNED_GRAPH_INDEX_OPERATIONS": ledger.planned_graph_index_operations,
        "MAX_INDEX_ATTEMPTS_PER_OPERATION": ledger.max_index_attempts_per_operation,
        "MAX_GRAPH_INDEX_ATTEMPTS": ledger.max_graph_index_attempts,
        "MEMBERSHIP_REMOVAL_REINDEX_OPS": ledger.membership_removal_reindex_ops,
        "GRAPH_DELETE_OPERATIONS": ledger.graph_delete_operations,
        "MAX_CANONICAL_SOURCE_EMBEDDINGS": ledger.max_canonical_source_embeddings,
        "BASELINE_STAGE1_GD_QUERIES": ledger.baseline_stage1_gd_queries,
        "MEMBERSHIP_REMOVAL_GD_QUERIES": ledger.membership_removal_gd_queries,
        "MAX_GD_QUERIES": ledger.max_gd_queries,
        "BASELINE_STAGE1_VECTOR_QUERIES": ledger.baseline_stage1_vector_queries,
        "MEMBERSHIP_REMOVAL_VECTOR_QUERIES": ledger.membership_removal_vector_queries,
        "MAX_VECTOR_QUERY_OPERATIONS": ledger.max_vector_query_operations,
        "MAX_QUERY_EMBEDDING_OPERATIONS": ledger.max_query_embedding_operations,
        "STAGE2_FINAL_ANSWER_CALLS": ledger.stage2_final_answer_calls,
        "MEMBERSHIP_REMOVAL_FINAL_ANSWER": ledger.membership_removal_final_answer,
        "MAX_FINAL_ANSWER_CALLS": ledger.max_final_answer_calls,
        "JUDGE_MODEL_CALLS": ledger.judge_model_calls,
        "FINAL_ANSWER_RETRIES": ledger.final_answer_retries,
        "MAX_QUERY_TECHNICAL_RETRIES": ledger.max_query_technical_retries,
    }


__all__ = [
    "PLANNED_GRAPH_INDEX_OPERATIONS",
    "MAX_INDEX_ATTEMPTS_PER_OPERATION",
    "MAX_GRAPH_INDEX_ATTEMPTS",
    "GRAPH_DELETE_OPERATIONS",
    "MAX_CANONICAL_SOURCE_EMBEDDINGS",
    "MAX_GD_QUERIES",
    "MAX_VECTOR_QUERY_OPERATIONS",
    "MAX_QUERY_EMBEDDING_OPERATIONS",
    "MAX_FINAL_ANSWER_CALLS",
    "JUDGE_MODEL_CALLS",
    "FINAL_ANSWER_RETRIES",
    "MAX_QUERY_TECHNICAL_RETRIES",
    "WorkloadCapExceeded",
    "WorkloadLedgerError",
    "WorkloadLedger",
    "frozen_ledger",
    "validate_against_fixture",
    "as_dict",
]
