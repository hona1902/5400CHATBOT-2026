"""Stateful pre-op workload budget guard for the PN02 live driver (PN02D-B0B).

EVALUATION-ONLY. Nothing in production imports this (``PRODUCTION_IMPORTS_EVAL =
NO``). The frozen ``workloadpn02.WorkloadLedger.check_cap`` is a STATELESS pre-op
guard (``observed > cap``) with no accumulator (design §3.3). The B1 driver needs a
STATEFUL guard that increments a per-class counter and checks the projected count
BEFORE dispatching each op, so a cap can never be exceeded silently and no op runs
first and is counted afterward (design §26/§G, task §22/§23).

This wraps (does not replace) the frozen ledger: every cap comes from
``workloadpn02`` constants, and each reservation calls the frozen
``check_cap`` primitive. The one deliberate B1 override is the final-answer cap
(design §26): ``workloadpn02.MAX_FINAL_ANSWER_CALLS = 72`` is the Stage-2 total,
but B1 excludes QA, so the B1 final-answer cap is 0. ``client.query`` and judge
calls are likewise hard-0.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Dict

from open_notebook.integrations.graphrag.eval import workloadpn02
from open_notebook.integrations.graphrag.eval.workloadpn02 import (
    WorkloadCapExceeded,
    WorkloadLedger,
    frozen_ledger,
)


class BudgetClass(str, Enum):
    """Independently-metered workload counters (design §57 — tracked separately)."""

    #: One logical graph-index operation per membership edge (cap 24).
    GRAPH_INDEX_OPERATION = "GRAPH_INDEX_OPERATION"
    #: One graph-index ATTEMPT (submit+track); retries count here (cap 48 = 24×2).
    GRAPH_INDEX_ATTEMPT = "GRAPH_INDEX_ATTEMPT"
    #: Per-workspace derived-document delete (cap 1).
    GRAPH_DELETE = "GRAPH_DELETE"
    #: A GD /query/data logical query (cap 26 = 24 baseline + 2 removal).
    GD_QUERY = "GD_QUERY"
    #: A notebook-local vector query (cap 26 = 24 baseline + 2 removal).
    VECTOR_QUERY = "VECTOR_QUERY"
    #: A query embedding (cap 26; K=3/K=5 share one embedding per vector query).
    QUERY_EMBEDDING = "QUERY_EMBEDDING"
    #: Final-answer generation — HARD 0 in B1 (QA excluded, design §26/§43).
    FINAL_ANSWER = "FINAL_ANSWER"
    #: ``client.query()`` / ``/query`` — HARD 0 (forbidden, design §8).
    CLIENT_QUERY = "CLIENT_QUERY"
    #: Judge-model calls — HARD 0 (design §26).
    JUDGE_MODEL = "JUDGE_MODEL"


def b1_caps() -> Dict[BudgetClass, int]:
    """Frozen B1 caps, sourced from ``workloadpn02`` (design §26/§58).

    The final-answer/client-query/judge caps are the B1 override (0), NOT the
    ledger's Stage-2 72.
    """
    return {
        BudgetClass.GRAPH_INDEX_OPERATION: workloadpn02.PLANNED_GRAPH_INDEX_OPERATIONS,
        BudgetClass.GRAPH_INDEX_ATTEMPT: workloadpn02.MAX_GRAPH_INDEX_ATTEMPTS,
        BudgetClass.GRAPH_DELETE: workloadpn02.GRAPH_DELETE_OPERATIONS,
        BudgetClass.GD_QUERY: workloadpn02.MAX_GD_QUERIES,
        BudgetClass.VECTOR_QUERY: workloadpn02.MAX_VECTOR_QUERY_OPERATIONS,
        BudgetClass.QUERY_EMBEDDING: workloadpn02.MAX_QUERY_EMBEDDING_OPERATIONS,
        BudgetClass.FINAL_ANSWER: 0,
        BudgetClass.CLIENT_QUERY: 0,
        BudgetClass.JUDGE_MODEL: 0,
    }


def b1_caps_dict() -> Dict[str, int]:
    """Content-safe caps view (for the run manifest / provider-run capability)."""
    return {k.value: v for k, v in b1_caps().items()}


@dataclass
class StatefulBudgetGuard:
    """A stateful, fail-closed per-class counter that reserves BEFORE each op.

    Usage is always ``guard.reserve(cls)`` immediately before dispatching a
    provider-backed op; the reservation raises ``WorkloadCapExceeded`` (from the
    frozen ledger) on the op that WOULD exceed the cap, so the backend is never
    invoked (design §23). ``spent`` exposes the running counts for the manifest.
    """

    def __init__(self, ledger: WorkloadLedger | None = None) -> None:
        self._ledger = ledger or frozen_ledger()
        self._caps = b1_caps()
        self._counts: Dict[BudgetClass, int] = {c: 0 for c in BudgetClass}

    def cap(self, cls: BudgetClass) -> int:
        return self._caps[cls]

    def spent(self, cls: BudgetClass) -> int:
        return self._counts[cls]

    def would_exceed(self, cls: BudgetClass, *, n: int = 1) -> bool:
        return self._counts[cls] + n > self._caps[cls]

    def reserve(self, cls: BudgetClass, *, n: int = 1) -> None:
        """Reserve ``n`` units of a class, or refuse (raise) before any op runs.

        Reservation is checked against the frozen ledger primitive on the PROJECTED
        (post-op) count; only if it passes is the count committed. So an op that
        would exceed a cap never mutates the counter and never dispatches.
        """
        projected = self._counts[cls] + n
        # Frozen primitive: raises WorkloadCapExceeded if projected > cap.
        self._ledger.check_cap(cls.value, projected, self._caps[cls])
        self._counts[cls] = projected

    def snapshot(self) -> Dict[str, Dict[str, int]]:
        """Content-safe ledger snapshot (spent + cap per class) for artifacts."""
        return {
            c.value: {"spent": self._counts[c], "cap": self._caps[c]}
            for c in BudgetClass
        }


__all__ = [
    "BudgetClass",
    "WorkloadCapExceeded",
    "b1_caps",
    "b1_caps_dict",
    "StatefulBudgetGuard",
]
