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

The B2 mint gates on ``current_approved_b2_checkpoint()`` (the current expected identity
``graphrag-pn02db2-chat-model-remediation-approved`` — see ``EXPECTED_B2_CHECKPOINT_TAG``).
Two prior B2-era tags are immutable/historical and can NEVER authorize the current run: the
predecessor lifecycle tag ``graphrag-pn02db2-qa-live-wiring-lifecycle-approved`` at commit
``40d3a6f`` (superseded by the chat-model-remediation identity; an ancestor never authorizes a
later HEAD) and the first-B2 tag ``graphrag-pn02db2-qa-live-wiring-approved`` at commit
``41eb3f3``. While the current expected annotated tag is ABSENT from real Git,
``run_live_b2_execution`` FAILS CLOSED inside the driver's mint before any provider binding/boot
— neither EW8 (which peels to a B1 HEAD) nor either historical B2 tag can ever authorize a B2 run.

The ON final-answer transport is an injected ``completion_fn``: provider-free tests pass a
fake; a live run builds the real one lazily from the repository provisioning abstraction
(``provision_langchain_model`` — never an ad-hoc provider client, AGRIBANK §8). This module
performs NO provider I/O and reads NO secret at import time.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncIterator, Mapping, Optional

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
from open_notebook.integrations.graphrag.eval.qastagepn02db2 import (
    B2QAStage,
    QAExecutionObserver,
)
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


@asynccontextmanager
async def _default_b2_model_seed() -> AsyncIterator[object]:
    """Seed BOTH the frozen embedding AND the frozen chat model into the active isolation (B2).

    The B1 default model seed (``realseamspn02d._default_model_seed``) seeds ONLY the embedding
    default, which is why Real B2 Execution #1 failed at the first QA final-answer:
    ``provision_langchain_model(type="chat")`` resolved ``model_id=None`` because the isolated
    namespace had no ``default_chat_model``. This nests the two shared, provider-free seed
    contexts — embedding OUTER, chat INNER — so BOTH ``DefaultModels`` defaults resolve the frozen
    models inside the temp namespace, and both are torn down (chat first, then embedding) on exit.
    It reuses the SINGLE-SOURCE ``isolated_model_seed`` mechanism (no second registry, no ad-hoc
    provider client) and makes NO provider call. B1 is unaffected — it keeps the embedding-only
    default seed.
    """
    from open_notebook.integrations.graphrag.eval.isolated_model_seed import (
        seeded_frozen_chat_model,
        seeded_frozen_embedding_model,
    )

    async with seeded_frozen_embedding_model() as embedding_model_id:
        async with seeded_frozen_chat_model():
            # Yield the embedding id to preserve the B1 model-seed contract (the frozen-model
            # attestor + embedding stack resolve the embedding default); the chat default is
            # bound in the inner scope for the B2 final-answer transport.
            yield embedding_model_id


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


def build_b2_qa_stage(
    *,
    completion_fn: Optional[CompletionFn] = None,
    execution_observer: Optional[QAExecutionObserver] = None,
) -> B2QAStage:
    """Build the B2 Stage-2 QA seam. A live run uses the real ON completion transport; a
    provider-free test injects a fake ``completion_fn``. The stage carries its own frozen
    72-cap budget guard and computes NO scientific result.

    ``execution_observer`` (PN02D-B3B, default ``None`` = no-op) is an OPTIONAL live-
    observability hook the stage calls once per completed (query, arm); it never alters the
    answer/budget/metrics. It is threaded through unchanged for a future B3 observational run.
    """
    fn = completion_fn if completion_fn is not None else build_real_final_answer_completion_fn()
    return B2QAStage(
        answer_seam=RealFinalAnswerSeam(completion_fn=fn),
        execution_observer=execution_observer,
    )


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
    qa_execution_observer: Optional[QAExecutionObserver] = None,
) -> B1RunOutcome:
    """PRODUCTION live B2 entrypoint (used by ``execute-b2-live``).

    Exposes NO composition or trust-root override: the seams builder, isolation, model seed,
    and model attestor are the real production defaults; the B2 QA stage uses the real ON
    completion transport; and the mint is the B2 mint (fails closed until the B2 checkpoint
    tag exists). Reuses the B1 two-boot/mint/index/GD/vector orchestration unchanged.

    ``qa_execution_observer`` (PN02D-B3B, default ``None`` = byte-identical B2) is threaded to
    the QA stage's optional observability hook for a future B3 observational run; it never
    changes retrieval/generation/grading/metrics or the B2 scientific result.
    """
    qa_stage = build_b2_qa_stage(execution_observer=qa_execution_observer)
    outcome = await _run_live_b1_execution_composed(
        operator_grant=operator_grant,
        git_baseline_attestation=git_baseline_attestation,
        observed_fixture_hash=observed_fixture_hash,
        driver_kwargs=_b2_driver_kwargs(qa_stage),
        model_seed=_default_b2_model_seed,
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
    qa_execution_observer: Optional[QAExecutionObserver] = None,
) -> B1RunOutcome:
    """PRIVATE, NON-LIVE B2 composition helper (provider-free tests only).

    Like ``realseamspn02d._run_live_b1_execution_composed`` but injects the B2 QA stage (with
    a fake ``completion_fn``) and the B2 mint. NOT a production entrypoint; the CLI never
    calls this. Any keyword left ``None`` falls back to the shared helper's real default.
    ``qa_execution_observer`` (default ``None`` = no-op) threads the B3B observability hook.
    """
    qa_stage = build_b2_qa_stage(
        completion_fn=completion_fn, execution_observer=qa_execution_observer
    )
    # Default to the B2 combined (embedding + chat) seed so a composed B2 run resolves the chat
    # default too; an explicit ``model_seed`` (e.g. a no-op seed in a fully-faked test) overrides.
    effective_model_seed = model_seed if model_seed is not None else _default_b2_model_seed
    passthrough = {
        k: v
        for k, v in dict(
            fx=fx,
            seams_builder=seams_builder,
            isolation=isolation,
            builder_kwargs=builder_kwargs,
        ).items()
        if v is not None
    }
    outcome = await _run_live_b1_execution_composed(
        operator_grant=operator_grant,
        git_baseline_attestation=git_baseline_attestation,
        observed_fixture_hash=observed_fixture_hash,
        driver_kwargs=_b2_driver_kwargs(qa_stage),
        model_seed=effective_model_seed,
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
