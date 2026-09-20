"""PN02D-B2 Stage-2 QA stage — the OPTIONAL QA seam injected into the shared B1
orchestrator (operator Option A, additive).

EVALUATION-ONLY. Nothing in production imports this. This stage is the ONLY new
executable "science-adjacent" surface B2 adds, and it deliberately computes NO
scientific result: for each frozen (query, arm) it constructs the arm's
notebook-member-filtered evidence, asks the ON-owned ``FinalAnswerSeam`` for one
answer, and collects the ``QAAnswerResult``. Grading (``metricspn02.grade_answer``),
the QA metrics (``QAArmMetrics``), the Q0-Q3 verdict (``decisionspn02``) and the
isolation/leakage gate (``stage1pn02``) all remain in the EXISTING
``evaluatepn02`` evaluator — this stage recomputes none of them.

Frozen behaviour (PN02A §10a/§10b/§26):

  * Arms, in deterministic order: QA-V, QA-GD, QA-V+GD.
  * QA-V evidence      = ordered V(K=5) members.
  * QA-GD evidence     = GD ``/query/data`` member Sources (unordered set,
                         rendered in canonical-id order for a stable prompt).
  * QA-V+GD evidence   = the frozen ``combinepn02.combine_v_gd`` result (cap 8).
  * HARD notebook-member filter is applied to EVERY arm BEFORE the answer call, so
    a non-member Source can never reach the final-answer seam.
  * 24 queries × 3 arms = 72 final-answer calls; a stateful B2 budget guard
    (``FINAL_ANSWER`` cap 72) reserves BEFORE each call, so a 73rd is impossible.
  * No retry (``FINAL_ANSWER_RETRIES = 0``); no judge; no rewrite/repair call.
  * Returned citation Source ids are preserved VERBATIM (invalid ones are NOT
    dropped/repaired — they must reach the evaluator's S2 metric).
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Callable, List, Mapping, Optional, Sequence, Tuple

from open_notebook.integrations.graphrag.eval.budgetlivepn02d import (
    BudgetClass,
    StatefulBudgetGuard,
    b2_caps,
)
from open_notebook.integrations.graphrag.eval.combinepn02 import combine_v_gd
from open_notebook.integrations.graphrag.eval.datasetpn02 import FixturePN02, QueryPN02
from open_notebook.integrations.graphrag.eval.live_seam_pn02 import FinalAnswerSeam
from open_notebook.integrations.graphrag.eval.schemaspn02 import (
    ArmId,
    GDEvidenceResult,
    QAAnswerResult,
    VectorEvidenceResult,
)

#: Deterministic arm order (PN02A §10a). The evaluator is order-independent, but a
#: frozen order makes the 72-plan and provider-call accounting reproducible.
ARM_ORDER: Tuple[ArmId, ...] = (ArmId.QA_V, ArmId.QA_GD, ArmId.QA_VGD)

#: Stage-2 vector slice (PN02A §8c / evaluatepn02.VECTOR_K_STAGE2).
STAGE2_K = 5


@dataclass(frozen=True)
class B2AnswerPlanItem:
    """One (query, arm) unit of the deterministic 72-item plan (content-safe)."""

    query_id: str
    notebook_id: str
    arm: ArmId
    evidence_source_ids: Tuple[str, ...]


@dataclass(frozen=True)
class B2QAExecutionRecord:
    """Transient record of one COMPLETED (query, arm) execution, handed to an
    OPTIONAL observer AFTER the answer is produced (PN02D-B3B live-observability
    seam). Carries the exact member-filtered ``evidence_source_ids`` the stage
    passed to the answer seam plus the produced ``QAAnswerResult`` (which may hold
    transient ``answer_text``) — so a downstream B3 observer can reuse the EXISTING
    grader and the checkpointed diagnostic. A PERSISTING consumer MUST project
    content-safe and NEVER persist ``answer_text`` (task B3B §20). This record is
    NOT part of any scientific result and NOT persisted by the stage."""

    query_id: str
    notebook_id: str
    arm: ArmId
    evidence_source_ids: Tuple[str, ...]
    result: QAAnswerResult


#: An OPTIONAL, side-effect-only observer invoked once per successfully-produced
#: (query, arm) answer. Default is ``None`` (a strict no-op — the normal B2 path is
#: byte-identical). It is a GENERIC callback type: this module does NOT import the
#: B3 diagnostic; a B3 execution adapter installs the observer (task B3B §3/§47).
QAExecutionObserver = Callable[[B2QAExecutionRecord], None]


@dataclass
class B2QAStage:
    """The injectable Stage-2 QA seam (implements ``driverpn02d.QAStageSeam``).

    ``answer_seam`` is the ON-owned final-answer seam (real in a live run; a fake in
    provider-free tests). A fresh ``StatefulBudgetGuard(caps=b2_caps())`` enforces the
    FINAL_ANSWER=72 cap fail-closed. The B1 budget guard inside the orchestrator is
    untouched (it keeps FINAL_ANSWER=0), so B1 remains byte-identical when no stage
    is injected.
    """

    answer_seam: FinalAnswerSeam
    budget: StatefulBudgetGuard = field(
        default_factory=lambda: StatefulBudgetGuard(caps=b2_caps())
    )
    #: OPTIONAL live-observability hook (PN02D-B3B). Default ``None`` = strict no-op
    #: (normal B2 path byte-identical). When set, it is called ONCE per successfully
    #: produced (query, arm) answer, AFTER the result is appended and counted — it
    #: never regrades, alters the answer/citations, or affects the budget/metrics.
    execution_observer: Optional[QAExecutionObserver] = None
    #: COMPLETED final answers — incremented ONLY after a QAAnswerResult is successfully
    #: produced (PN02DB2-RR2-M1). Distinct from the budget's RESERVED count: the guard
    #: reserves BEFORE each provider call (to keep the 72-cap fail-closed), so on a partial
    #: failure reserved > completed. Never set on the await/exception path.
    _completed_answers: int = field(default=0, init=False)

    @property
    def final_answer_completed_answers(self) -> int:
        """READ-ONLY count of final answers ACTUALLY produced (PN02DB2-RR2-M1). A partial/
        failing run reports the true completed count (< 72), NOT the planned/reserved 72."""
        return self._completed_answers

    @property
    def final_answer_reserved_attempts(self) -> int:
        """READ-ONLY count of budget RESERVATIONS (provider answer attempts admitted by the
        guard). On a run that fails at call N this is N while completed is N-1."""
        return self.budget.spent(BudgetClass.FINAL_ANSWER)

    @property
    def final_answer_cap(self) -> int:
        """READ-ONLY frozen FINAL_ANSWER cap (72). Distinct from reserved/completed above."""
        return self.budget.cap(BudgetClass.FINAL_ANSWER)

    def plan(
        self,
        *,
        fx: FixturePN02,
        ordered_queries: Sequence[QueryPN02],
        vector_results: Mapping[str, VectorEvidenceResult],
        gd_results: Mapping[str, GDEvidenceResult],
    ) -> List[B2AnswerPlanItem]:
        """Build the deterministic 24×3 plan with member-filtered arm evidence.

        Pure and provider-free — constructs no answers, makes no calls.
        """
        items: List[B2AnswerPlanItem] = []
        for q in ordered_queries:
            members = frozenset(fx.members_of(q.notebook_id))
            v_res = vector_results.get(q.query_id)
            g_res = gd_results.get(q.query_id)
            # V(K=5) member slice (rank-preserving), GD member set — hard-filtered.
            v5_ranked = tuple(
                s for s in (v_res.evidence.top_k(STAGE2_K) if v_res else ()) if s in members
            )
            gd_set = frozenset(
                s for s in (g_res.evidence.as_set() if g_res else frozenset()) if s in members
            )
            for arm in ARM_ORDER:
                evidence = self._arm_evidence(arm, v5_ranked, gd_set, members)
                items.append(
                    B2AnswerPlanItem(
                        query_id=q.query_id,
                        notebook_id=q.notebook_id,
                        arm=arm,
                        evidence_source_ids=evidence,
                    )
                )
        return items

    async def __call__(
        self,
        *,
        fx: FixturePN02,
        ordered_queries: Sequence[QueryPN02],
        vector_results: Mapping[str, VectorEvidenceResult],
        gd_results: Mapping[str, GDEvidenceResult],
    ) -> List[QAAnswerResult]:
        plan = self.plan(
            fx=fx,
            ordered_queries=ordered_queries,
            vector_results=vector_results,
            gd_results=gd_results,
        )
        results: List[QAAnswerResult] = []
        for item in plan:
            # Reserve BEFORE the call — the 73rd raises WorkloadCapExceeded (fail-closed).
            self.budget.reserve(BudgetClass.FINAL_ANSWER)
            raw = await self.answer_seam.answer(
                notebook_id=item.notebook_id,
                question=fx.query(item.query_id).question,
                evidence_source_ids=list(item.evidence_source_ids),
            )
            # The seam produces answer/citations/abstained; the STAGE is authoritative
            # for (query_id, notebook_id, arm). Citations are preserved verbatim.
            results.append(
                replace(
                    raw,
                    query_id=item.query_id,
                    notebook_id=item.notebook_id,
                    arm=item.arm,
                )
            )
            # Count a COMPLETED answer ONLY after a successful append (PN02DB2-RR2-M1). If the
            # await above raised, this is not reached, so completed stays < reserved.
            self._completed_answers += 1
            # OPTIONAL B3B live-observability hook — runs AFTER the answer is produced and
            # counted; exactly once per completed (query, arm); no-op when unset (byte-identical
            # B2). It observes only; it never regrades or mutates the result/budget/metrics.
            if self.execution_observer is not None:
                self.execution_observer(
                    B2QAExecutionRecord(
                        query_id=item.query_id,
                        notebook_id=item.notebook_id,
                        arm=item.arm,
                        evidence_source_ids=item.evidence_source_ids,
                        result=results[-1],
                    )
                )
        return results

    @staticmethod
    def _arm_evidence(
        arm: ArmId,
        v5_ranked: Tuple[str, ...],
        gd_set: "frozenset[str]",
        members: "frozenset[str]",
    ) -> Tuple[str, ...]:
        if arm is ArmId.QA_V:
            return v5_ranked
        if arm is ArmId.QA_GD:
            # Unordered GD set rendered in canonical-id order (stable prompt).
            return tuple(sorted(gd_set))
        if arm is ArmId.QA_VGD:
            combined = combine_v_gd(list(v5_ranked), gd_set, members)
            return combined.ordered_source_ids
        raise ValueError(f"unknown QA arm {arm!r}")


__all__ = [
    "ARM_ORDER",
    "STAGE2_K",
    "B2AnswerPlanItem",
    "B2QAExecutionRecord",
    "QAExecutionObserver",
    "B2QAStage",
]
