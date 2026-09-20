"""PN02D-B2 REAL live QA wiring — the canonical B2 execution entrypoint.

EVALUATION-ONLY. Nothing in production imports this. It adds the MINIMUM B2 live layer
on top of the approved B1 machinery + the B2 mint-identity security boundary:

  * it REUSES the B1 two-boot / mint-ordering / index / GD / vector / membership-removal
    orchestration (``realseamspn02d._run_live_b1_execution_composed`` +
    ``build_real_b1_live_seams`` + ``RealB1Driver``) — NO duplicated two-boot / mint /
    index / GD / vector pipeline;
  * it injects the OPTIONAL Stage-2 QA seam (``qastagepn02db2.B2QAStage`` over the
    ON-owned ``RealFinalAnswerSeam``) and selects the B2 mint
    (``authmintlivepn02d.mint_live_b2_provider_run_authorization``) via the additive
    ``driver_kwargs`` seam. When those are absent the B1 path is byte-identical.

The B2 mint gates on ``current_approved_b2_checkpoint()`` (the future SUCCESSOR
``graphrag-pn02db2-qa-live-wiring-lifecycle-approved`` tag; the historical first-B2 tag
``graphrag-pn02db2-qa-live-wiring-approved`` at commit ``41eb3f3`` is immutable/superseded).
While that successor annotated tag is ABSENT from real Git, ``run_live_b2_execution`` FAILS
CLOSED inside the driver's mint before any provider binding/boot — neither EW8 (which peels to
a B1 HEAD) nor the historical first-B2 tag can ever authorize a B2 run.

The ON final-answer transport is an injected ``completion_fn``: provider-free tests pass a
fake; a live run builds the real one lazily from the repository provisioning abstraction
(``provision_langchain_model`` — never an ad-hoc provider client, AGRIBANK §8). This module
performs NO provider I/O and reads NO secret at import time.
"""

from __future__ import annotations

from typing import Mapping, Optional

from open_notebook.integrations.graphrag.eval.authmintlivepn02d import (
    OperatorRunGrant,
    mint_live_b2_provider_run_authorization,
)
from open_notebook.integrations.graphrag.eval.driver_live_pn02d import (
    GitBaselineAttestation,
)
from open_notebook.integrations.graphrag.eval.driverpn02d import (
    EXPECTED_FIXTURE_HASH,
    B1RunOutcome,
    QAStageSeam,
)
from open_notebook.integrations.graphrag.eval.qastagepn02db2 import B2QAStage
from open_notebook.integrations.graphrag.eval.real_final_answer_seam_pn02d import (
    CompletionFn,
    RealFinalAnswerSeam,
)
from open_notebook.integrations.graphrag.eval.realseamspn02d import (
    _run_live_b1_execution_composed,
)

#: The default-model category for the ON final-answer chat call (routed through the
#: provisioning abstraction; the frozen model is openai/gpt-4o-mini via OpenRouter).
_FINAL_ANSWER_DEFAULT_TYPE = "chat"


def build_real_final_answer_completion_fn() -> CompletionFn:
    """Compose the real ON final-answer transport (lazy; provider I/O only on call).

    Mirrors ``realseamspn02d.build_real_query_embed_fn``: it returns an async closure that
    routes through the repository model-provisioning abstraction
    (``open_notebook.ai.provision.provision_langchain_model``) — never an ad-hoc provider
    client (AGRIBANK §8). No provider call is made at build/import time; the single chat
    call happens when the QA stage invokes the seam during a live run.
    """

    async def _complete(prompt: str) -> str:
        from open_notebook.ai.provision import provision_langchain_model

        model = await provision_langchain_model(
            prompt, None, _FINAL_ANSWER_DEFAULT_TYPE
        )
        response = await model.ainvoke(prompt)
        content = getattr(response, "content", response)
        return content if isinstance(content, str) else str(content)

    return _complete


def build_b2_qa_stage(*, completion_fn: Optional[CompletionFn] = None) -> B2QAStage:
    """Build the B2 Stage-2 QA seam. A live run uses the real ON completion transport; a
    provider-free test injects a fake ``completion_fn``. The stage carries its own frozen
    72-cap budget guard and computes NO scientific result.
    """
    fn = completion_fn if completion_fn is not None else build_real_final_answer_completion_fn()
    return B2QAStage(answer_seam=RealFinalAnswerSeam(completion_fn=fn))


def attach_b2_final_answer_spend(outcome: B1RunOutcome, qa_stage: B2QAStage) -> B1RunOutcome:
    """Attach the AUTHORITATIVE B2 final-answer spend/cap to the report (PN02DB2-R1-H1).

    The B2 final-answer calls are reserved in the B2QAStage's OWN budget (cap 72), not the
    B1 orchestrator budget (which keeps FINAL_ANSWER=0). Sourcing the count from the B1
    workload ledger would report 0/wrong; instead read the stage's read-only accessors.
    ``spent`` is the COMPLETED-answer count (PN02DB2-RR2-M1): a partial/failing run reports
    the true completed count (< 72), never the reserved/planned 72. ``reserved_attempts`` is
    surfaced separately for accounting. Additive, B2-only: mutates the report dict in place
    with a single ``b2_final_answer`` block; the B1 path never constructs a B2QAStage.
    """
    if isinstance(outcome.report, dict):
        completed = qa_stage.final_answer_completed_answers
        outcome.report["b2_final_answer"] = {
            "cap": qa_stage.final_answer_cap,
            "reserved_attempts": qa_stage.final_answer_reserved_attempts,
            "completed_answers": completed,
            # ``spent`` == COMPLETED answers (the user-facing final-answer count).
            "spent": completed,
        }
    return outcome


def _b2_driver_kwargs(qa_stage_seam: QAStageSeam) -> dict:
    """The additive B2 ``driver_kwargs``: the QA stage + the B2 mint selector. The mint
    resolves its trust roots INTERNALLY (B2 checkpoint gate); this is a mint SELECTOR, not a
    trust-root override.
    """
    return {
        "qa_stage_seam": qa_stage_seam,
        "mint_fn": mint_live_b2_provider_run_authorization,
    }


async def run_live_b2_execution(
    *,
    operator_grant: OperatorRunGrant,
    git_baseline_attestation: GitBaselineAttestation,
    observed_fixture_hash: str = EXPECTED_FIXTURE_HASH,
    env: Optional[Mapping[str, str]] = None,
) -> B1RunOutcome:
    """PRODUCTION live B2 entrypoint (used by ``execute-b2-live``).

    Exposes NO composition or trust-root override: the seams builder, isolation, model seed,
    and model attestor are the real production defaults; the B2 QA stage uses the real ON
    completion transport; and the mint is the B2 mint (fails closed until the B2 checkpoint
    tag exists). Reuses the B1 two-boot/mint/index/GD/vector orchestration unchanged.
    """
    qa_stage = build_b2_qa_stage()
    outcome = await _run_live_b1_execution_composed(
        operator_grant=operator_grant,
        git_baseline_attestation=git_baseline_attestation,
        observed_fixture_hash=observed_fixture_hash,
        driver_kwargs=_b2_driver_kwargs(qa_stage),
        env=env,
    )
    return attach_b2_final_answer_spend(outcome, qa_stage)


async def _run_live_b2_execution_composed(
    *,
    operator_grant: OperatorRunGrant,
    git_baseline_attestation: GitBaselineAttestation,
    observed_fixture_hash: str = EXPECTED_FIXTURE_HASH,
    completion_fn: Optional[CompletionFn] = None,
    fx=None,
    seams_builder=None,
    isolation=None,
    model_seed=None,
    builder_kwargs: Optional[Mapping[str, object]] = None,
    env: Optional[Mapping[str, str]] = None,
) -> B1RunOutcome:
    """PRIVATE, NON-LIVE B2 composition helper (provider-free tests only).

    Like ``realseamspn02d._run_live_b1_execution_composed`` but injects the B2 QA stage (with
    a fake ``completion_fn``) and the B2 mint. NOT a production entrypoint; the CLI never
    calls this. Any keyword left ``None`` falls back to the shared helper's real default.
    """
    qa_stage = build_b2_qa_stage(completion_fn=completion_fn)
    passthrough = {
        k: v
        for k, v in dict(
            fx=fx,
            seams_builder=seams_builder,
            isolation=isolation,
            model_seed=model_seed,
            builder_kwargs=builder_kwargs,
        ).items()
        if v is not None
    }
    outcome = await _run_live_b1_execution_composed(
        operator_grant=operator_grant,
        git_baseline_attestation=git_baseline_attestation,
        observed_fixture_hash=observed_fixture_hash,
        driver_kwargs=_b2_driver_kwargs(qa_stage),
        env=env,
        **passthrough,
    )
    return attach_b2_final_answer_spend(outcome, qa_stage)


__all__ = [
    "build_real_final_answer_completion_fn",
    "build_b2_qa_stage",
    "run_live_b2_execution",
    "attach_b2_final_answer_spend",
]
