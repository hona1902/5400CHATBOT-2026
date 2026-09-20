"""PN02D-B2 thin RealB2Driver adaptor.

EVALUATION-ONLY. Nothing in production imports this. ``RealB2Driver`` is a THIN adaptor:
it constructs the shared ``RealB1Driver`` with the B2 mint selector
(``mint_live_b2_provider_run_authorization``) and the B2 Stage-2 QA seam, and forwards
``run``. It contains NO grading, NO QAArmMetrics, NO Q0-Q3 verdict, NO leakage gate, NO
index/GD/vector/auth implementation — all of that stays in the shared pipeline and the
EXISTING ``evaluatepn02`` evaluator. It exists only so a B2 run configures the shared
two-boot/mint/index orchestration with the B2 identity + QA stage.
"""

from __future__ import annotations

from open_notebook.integrations.graphrag.eval.authmintlivepn02d import (
    OperatorRunGrant,
    mint_live_b2_provider_run_authorization,
)
from open_notebook.integrations.graphrag.eval.datasetpn02 import FixturePN02
from open_notebook.integrations.graphrag.eval.driver_live_pn02d import (
    EXPECTED_FIXTURE_HASH,
    B1RunOutcome,
    GitBaselineAttestation,
    LiveB1Seams,
    RealB1Driver,
)
from open_notebook.integrations.graphrag.eval.qastagepn02db2 import B2QAStage
from open_notebook.integrations.graphrag.eval.real_final_answer_seam_pn02d import (
    CompletionFn,
    RealFinalAnswerSeam,
)
from open_notebook.integrations.graphrag.eval.realseamsb2pn02d import (
    attach_b2_final_answer_spend,
)


class RealB2Driver:
    """Thin B2 wrapper over ``RealB1Driver`` (B2 mint + B2 QA stage; no new science)."""

    def __init__(
        self,
        fx: FixturePN02,
        seams: LiveB1Seams,
        *,
        completion_fn: CompletionFn,
    ) -> None:
        # Keep a reference to the QA stage so its AUTHORITATIVE final-answer spend can be
        # attached to the report after the run (PN02DB2-R1-H1); the B1 orchestrator budget
        # never sees these calls (it keeps FINAL_ANSWER=0).
        self._qa_stage = B2QAStage(
            answer_seam=RealFinalAnswerSeam(completion_fn=completion_fn)
        )
        self._inner = RealB1Driver(
            fx,
            seams,
            qa_stage_seam=self._qa_stage,
            mint_fn=mint_live_b2_provider_run_authorization,
        )

    async def run(
        self,
        *,
        operator_grant: OperatorRunGrant,
        git_baseline_attestation: GitBaselineAttestation,
        observed_fixture_hash: str = EXPECTED_FIXTURE_HASH,
    ) -> B1RunOutcome:
        outcome = await self._inner.run(
            operator_grant=operator_grant,
            git_baseline_attestation=git_baseline_attestation,
            observed_fixture_hash=observed_fixture_hash,
        )
        return attach_b2_final_answer_spend(outcome, self._qa_stage)


__all__ = ["RealB2Driver"]
