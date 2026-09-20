"""PN02D-B2 REAL Open-Notebook-owned final-answer seam.

EVALUATION-ONLY. Nothing in production imports this. Implements the frozen
``live_seam_pn02.FinalAnswerSeam`` Protocol: ON owns the final answer (PN02A §10,
``FINAL_ANSWER_OWNER = OPEN_NOTEBOOK``); LightRAG ``client.query`` is NOT a final
answer path here. For one (query, arm) it builds a prompt from the arm's ALREADY
notebook-member-filtered evidence Source ids, calls the frozen ON LLM
(``openai/gpt-4o-mini`` via OpenRouter) EXACTLY ONCE, and parses the completion into
a ``QAAnswerResult``.

Hard constraints (PN02A §26 / PN02D-B2 freeze):
  * ONE provider call per invocation; NO retry (``FINAL_ANSWER_RETRIES = 0`` —
    embedding retry policy is NOT inherited); NO judge/rewrite/repair call.
  * Returned citation ids are preserved VERBATIM — invalid ids are NOT dropped or
    repaired here (they are the evaluator's S2 signal, ``metricspn02``).
  * The seam GRADES nothing, computes NO QAArmMetrics, decides NO Q0-Q3 verdict,
    and recomputes NO leakage gate.
  * Provider failures surface as a content-safe structured diagnostic (reusing the
    EW5 ``provider_errors`` classifier); the raw provider body, the ``Authorization``
    header, the bearer token and the ``OPENROUTER_API_KEY`` value are NEVER exposed.

The actual LLM transport is an injected ``completion_fn`` so provider-free tests
drive the full parse/abstain/citation-preservation logic with ZERO network. A live
run builds ``completion_fn`` from the frozen provider binding via
``build_real_final_answer_seam`` (constructed lazily — importing this module makes no
provider call).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Awaitable, Callable, List, Optional, Sequence, Tuple

from open_notebook.integrations.graphrag.eval.schemaspn02 import (
    ArmId,
    QAAnswerResult,
    TechnicalOutcome,
)
from open_notebook.utils.provider_errors import (
    ProviderErrorDiagnostic,
    classify_provider_error,
)

#: async prompt -> raw completion text. Injected (fake in tests; real in a live run).
CompletionFn = Callable[[str], Awaitable[str]]

#: Answer-contract markers the ON prompt instructs the model to emit on their own
#: lines. The parser reads them structurally; everything before them is the answer.
_CITATIONS_MARKER = "CITATIONS:"
_ABSTAIN_MARKER = "ABSTAIN:"


class FinalAnswerProviderError(RuntimeError):
    """Content-safe final-answer provider failure (carries a structured diagnostic).

    NO raw provider body / message / secret is included — only the allowlisted
    ``ProviderErrorDiagnostic`` (status class, retryability, request-reached).
    """

    def __init__(self, diagnostic: ProviderErrorDiagnostic) -> None:
        super().__init__(
            f"final-answer provider error "
            f"(class={diagnostic.provider_error_class}, "
            f"status={diagnostic.provider_http_status})"
        )
        self.diagnostic = diagnostic


def build_final_answer_prompt(
    *, question: str, evidence_source_ids: Sequence[str]
) -> str:
    """Deterministic ON final-answer prompt (content-safe: ids + question only).

    Instructs the model to answer ONLY from the provided evidence Source ids, to
    cite ONLY those ids, and to abstain when the evidence does not answer the
    question. The exact answer text is provider output; this builder never embeds a
    secret and is used identically for every arm.
    """
    ids = ", ".join(evidence_source_ids) if evidence_source_ids else "(none)"
    return (
        "You are the Open Notebook answerer. Answer the question using ONLY the "
        "evidence Sources listed below, and cite ONLY those Source ids. If the "
        "evidence does not answer the question, abstain.\n\n"
        f"Question: {question}\n"
        f"Evidence Source ids: {ids}\n\n"
        "Respond with the answer text, then two final lines exactly:\n"
        f"{_CITATIONS_MARKER} <comma-separated Source ids you used, or empty>\n"
        f"{_ABSTAIN_MARKER} <YES or NO>\n"
    )


def parse_final_answer(completion: str) -> Tuple[str, Tuple[str, ...], bool]:
    """Parse a completion into (answer_text, citation_source_ids, abstained).

    Citation ids are returned VERBATIM (order preserved, blanks dropped) — invalid
    ids are NOT filtered here. Missing markers default to no citations / not
    abstained. Pure and deterministic.
    """
    answer_lines: List[str] = []
    citations: Tuple[str, ...] = ()
    abstained = False
    for line in completion.splitlines():
        stripped = line.strip()
        if stripped.upper().startswith(_CITATIONS_MARKER):
            raw = stripped[len(_CITATIONS_MARKER):].strip()
            citations = tuple(tok.strip() for tok in raw.split(",") if tok.strip())
        elif stripped.upper().startswith(_ABSTAIN_MARKER):
            abstained = stripped[len(_ABSTAIN_MARKER):].strip().upper().startswith("Y")
        else:
            answer_lines.append(line)
    return "\n".join(answer_lines).strip(), citations, abstained


@dataclass
class RealFinalAnswerSeam:
    """Real ON final-answer seam (one provider call per answer; no retry)."""

    completion_fn: CompletionFn
    operation: str = "final_answer"

    async def answer(
        self,
        notebook_id: str,
        question: str,
        evidence_source_ids: Sequence[str],
    ) -> QAAnswerResult:
        prompt = build_final_answer_prompt(
            question=question, evidence_source_ids=evidence_source_ids
        )
        try:
            completion = await self.completion_fn(prompt)  # EXACTLY once; no retry.
        except Exception as exc:  # noqa: BLE001 - classify to a content-safe diagnostic
            diagnostic = classify_provider_error(exc, operation=self.operation)
            raise FinalAnswerProviderError(diagnostic) from None
        answer_text, citations, abstained = parse_final_answer(completion)
        # (query_id, arm) are stamped by the QA stage (the plan owner); the seam
        # returns notebook-scoped answer content only.
        return QAAnswerResult(
            query_id="",
            notebook_id=notebook_id,
            arm=ArmId.QA_V,
            abstained=abstained,
            citation_source_ids=citations,
            answer_text=answer_text,
            emitted_answer_facts=None,
            latency_ms=None,
            outcome=TechnicalOutcome.COMPLETED,
        )


def build_real_final_answer_seam(
    completion_fn: Optional[CompletionFn] = None,
) -> RealFinalAnswerSeam:
    """Build the real seam. With no ``completion_fn`` a live run wires the frozen
    ON LLM (``openai/gpt-4o-mini`` via OpenRouter) lazily; tests pass a fake.

    The real transport is intentionally constructed on first use by the caller that
    already holds the live provider binding (this module performs no provider I/O and
    reads no secret at import/build time).
    """
    if completion_fn is None:
        raise ValueError(
            "a completion_fn (frozen ON LLM transport) must be supplied by the live "
            "B2 seam builder; this module performs no provider I/O by itself"
        )
    return RealFinalAnswerSeam(completion_fn=completion_fn)


__all__ = [
    "CompletionFn",
    "FinalAnswerProviderError",
    "build_final_answer_prompt",
    "parse_final_answer",
    "RealFinalAnswerSeam",
    "build_real_final_answer_seam",
]
