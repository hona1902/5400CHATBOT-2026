"""PN02D-B3 — content-safe P1 fact-recall observability (OBSERVABILITY ONLY).

Localizes, per ``(query, arm)``, WHERE a required answer fact disappears in the P1
pipeline that was observed as ``0.0`` across all three arms in the scientifically
CLOSED_VALID B2 run (run ``pn02db2-6469b191-db13-4d2f-864a-4079578efcf4``,
checkpoint ``bc73fc9``). This module ONLY OBSERVES — it changes no retrieval,
prompt, answer-generation, grader, decision-rule, or budget behavior (B3 task
§3-§5 / §14 / §17-§19). It is additive and standalone: nothing in the frozen B2
runtime, evaluator, or artifact imports it, so the real B2 path stays byte-identical.

Design invariants:

* Fact -> Source provenance is DETERMINISTIC and derived from the frozen fixture
  (B3 §7-§9): the fixture guarantees (``datasetpn02`` load validation) that every
  ``expected_answer_facts`` token is a substring of the concatenated ``text`` of a
  query's REQUIRED member Sources. Provenance for a token is therefore the set of
  Source keys whose fixture text contains it — a pure, case-sensitive substring
  scan mirroring ``metricspn02.fact_present``. NO LLM, NO fixture mutation.
* The answer/grader side REUSES the deterministic grader's ``AnswerGrade``
  (B3 §10): ``grader_recognized_fact_ids`` are exactly the grader's recognized
  expected facts. ``answer_contained`` and ``grader_recognized`` SHARE the P1
  matcher (``fact_present`` over ``answer_text``); they are recorded as one signal,
  never pretended to be independent (B3 §11).
* A separate, observability-ONLY normalized (casefold) presence signal surfaces the
  LATENT strict-matcher false-negative risk (``grading_mismatch_candidate_fact_ids``)
  WITHOUT changing the grader or P1 (B3 §22).

Content-safety (B3 §6/§13): only IDs, booleans, counts, arm/query/notebook IDs,
Source keys, and synthetic fixture fact tokens are retained. Raw answer text and
provider content are NEVER persisted by this module.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Dict, List, Optional, Sequence, Tuple

from open_notebook.integrations.graphrag.eval.datasetpn02 import FixturePN02, QueryPN02
from open_notebook.integrations.graphrag.eval.metricspn02 import AnswerGrade
from open_notebook.integrations.graphrag.eval.schemaspn02 import ArmId, QAAnswerResult


class P1FactLayer(str, Enum):
    """Deterministic per-fact failure-layer classification (B3 §15). NO LLM.

    A distinct "grading mismatch" (GR0) layer is intentionally NOT a value here,
    because the answer-side recognition reuses the P1 matcher, so it is not an
    independent signal (B3 §11). The latent grading false-negative risk is surfaced
    separately via ``P1QueryArmDiagnostic.grading_mismatch_candidate_fact_ids``.
    """

    RETRIEVAL_MISSING = "R0"    # required fact absent from the arm's retrieved evidence
    GENERATION_MISSING = "G0"   # required fact present in evidence but not recognized in the answer
    OK = "OK"                   # required fact present in evidence AND recognized in the answer


# --------------------------------------------------------------------------- #
# Deterministic fact -> Source provenance (frozen-fixture truth; no LLM)
# --------------------------------------------------------------------------- #


def fact_source_provenance(fx: FixturePN02, token: str) -> Tuple[str, ...]:
    """Source keys whose fixture text contains ``token`` (case-sensitive substring,
    mirroring ``metricspn02.fact_present``). Deterministic; synthetic-fixture only."""
    return tuple(k for k in sorted(fx.source_keys) if token in fx.source_text(k))


def evidence_contains_fact(
    fx: FixturePN02, evidence_source_ids: Sequence[str], token: str
) -> bool:
    """Whether ``token`` is a substring of the concatenated text of the arm's
    evidence Sources. Content-safe (returns only a boolean). Unknown Source ids
    (not in the fixture) contribute nothing."""
    return any(
        token in fx.source_text(sid)
        for sid in evidence_source_ids
        if sid in fx.source_keys
    )


def _normalized_present(answer_text: Optional[str], token: str) -> bool:
    """Observability-ONLY lenient (casefold substring) presence. NEVER used by P1
    or the grader — it exists solely to surface strict-matcher false negatives."""
    if answer_text is None:
        return False
    return token.casefold() in answer_text.casefold()


# --------------------------------------------------------------------------- #
# Per-(query, arm) diagnostic
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class P1QueryArmDiagnostic:
    """Content-safe per-(query, arm) P1 diagnostic (B3 §12/§16). Fact-SET based:
    multi-fact queries keep the underlying token id sets, never a single boolean."""

    query_id: str
    notebook_id: str
    arm: ArmId
    required_fact_ids: Tuple[str, ...]
    evidence_source_ids: Tuple[str, ...]
    #: Required facts whose token is contained in the arm evidence Source text.
    evidence_contained_fact_ids: Tuple[str, ...]
    #: Required facts the deterministic grader recognized in the answer. This is
    #: ALSO ``answer_contained`` (same matcher — see module docstring / B3 §11).
    grader_recognized_fact_ids: Tuple[str, ...]
    #: Required facts NOT recognized by the grader.
    missing_fact_ids: Tuple[str, ...]
    #: Observability-ONLY: required facts the strict grader MISSED but a normalized
    #: (casefold) scan of the answer would match — the latent false-negative risk
    #: (B3 §22). Never feeds P1 or the grade.
    grading_mismatch_candidate_fact_ids: Tuple[str, ...]
    #: Deterministic per-fact layer classification: (fact_id, P1FactLayer value).
    per_fact_layer: Tuple[Tuple[str, str], ...]

    @property
    def answer_contained_fact_ids(self) -> Tuple[str, ...]:
        """Facts contained in the answer. Identical to ``grader_recognized_fact_ids``
        because they share the deterministic P1 matcher (B3 §11)."""
        return self.grader_recognized_fact_ids


def diagnose_query_arm(
    *,
    fx: FixturePN02,
    query: QueryPN02,
    arm: ArmId,
    evidence_source_ids: Sequence[str],
    grade: AnswerGrade,
    result: Optional[QAAnswerResult] = None,
) -> P1QueryArmDiagnostic:
    """Build the content-safe diagnostic for one positive (query, arm).

    Reuses ``grade`` (the deterministic grader's ``AnswerGrade``) for the answer
    side and derives evidence containment from the frozen fixture. ``result`` is
    used ONLY for the observability-only normalized signal; its ``answer_text`` is
    never persisted.
    """
    required = tuple(query.expected_answer_facts)
    recognized = tuple(t for t in required if t in grade.recognized_expected_facts)
    missing = tuple(t for t in required if t in grade.missing_expected_facts)
    ev_contained = tuple(
        t for t in required if evidence_contains_fact(fx, evidence_source_ids, t)
    )
    answer_text = result.answer_text if result is not None else None
    recognized_set = set(recognized)
    mismatch = tuple(
        t
        for t in required
        if t not in recognized_set and _normalized_present(answer_text, t)
    )

    ev_set = set(ev_contained)
    layers: List[Tuple[str, str]] = []
    for t in required:
        if t in recognized_set:
            layers.append((t, P1FactLayer.OK.value))
        elif t not in ev_set:
            layers.append((t, P1FactLayer.RETRIEVAL_MISSING.value))
        else:
            layers.append((t, P1FactLayer.GENERATION_MISSING.value))

    return P1QueryArmDiagnostic(
        query_id=query.query_id,
        notebook_id=query.notebook_id,
        arm=arm,
        required_fact_ids=required,
        evidence_source_ids=tuple(evidence_source_ids),
        evidence_contained_fact_ids=ev_contained,
        grader_recognized_fact_ids=recognized,
        missing_fact_ids=missing,
        grading_mismatch_candidate_fact_ids=mismatch,
        per_fact_layer=tuple(layers),
    )


# --------------------------------------------------------------------------- #
# Aggregate (B3 §14)
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class P1DiagnosticAggregate:
    """Deterministic observability aggregates over positive (query, arm) diagnostics."""

    positive_query_arm_count: int
    required_fact_total: int
    required_fact_in_evidence_count: int
    #: grader-recognized == required-fact-in-answer (shared matcher, B3 §11).
    required_fact_in_answer_count: int
    grader_recognized_count: int
    #: R0 facts (not in evidence).
    retrieval_missing_count: int
    #: G0 facts (in evidence but not recognized in the answer).
    generation_missing_after_evidence_count: int
    #: Latent grading false-negative candidates (normalized-present, strict-missed).
    grading_mismatch_candidate_count: int
    ok_count: int


def aggregate_p1_diagnostics(
    diagnostics: Sequence[P1QueryArmDiagnostic],
) -> P1DiagnosticAggregate:
    """Aggregate per-fact layer counts across positive (query, arm) diagnostics."""
    required_total = 0
    in_evidence = 0
    recognized = 0
    r0 = 0
    g0 = 0
    ok = 0
    mismatch = 0
    for d in diagnostics:
        required_total += len(d.required_fact_ids)
        in_evidence += len(d.evidence_contained_fact_ids)
        recognized += len(d.grader_recognized_fact_ids)
        mismatch += len(d.grading_mismatch_candidate_fact_ids)
        for _fact, layer in d.per_fact_layer:
            if layer == P1FactLayer.OK.value:
                ok += 1
            elif layer == P1FactLayer.RETRIEVAL_MISSING.value:
                r0 += 1
            elif layer == P1FactLayer.GENERATION_MISSING.value:
                g0 += 1
    return P1DiagnosticAggregate(
        positive_query_arm_count=len(diagnostics),
        required_fact_total=required_total,
        required_fact_in_evidence_count=in_evidence,
        required_fact_in_answer_count=recognized,
        grader_recognized_count=recognized,
        retrieval_missing_count=r0,
        generation_missing_after_evidence_count=g0,
        grading_mismatch_candidate_count=mismatch,
        ok_count=ok,
    )


# --------------------------------------------------------------------------- #
# Content-safe projection (B3 §6/§13/§17 — additive; IDs/tokens/counts only)
# --------------------------------------------------------------------------- #


def _project_one(d: P1QueryArmDiagnostic) -> Dict[str, object]:
    return {
        "query_id": d.query_id,
        "notebook_id": d.notebook_id,
        "arm": d.arm.value,
        "required_fact_ids": list(d.required_fact_ids),
        "evidence_source_ids": list(d.evidence_source_ids),
        "evidence_contained_fact_ids": list(d.evidence_contained_fact_ids),
        "grader_recognized_fact_ids": list(d.grader_recognized_fact_ids),
        "answer_contained_fact_ids": list(d.answer_contained_fact_ids),
        "missing_fact_ids": list(d.missing_fact_ids),
        "grading_mismatch_candidate_fact_ids": list(
            d.grading_mismatch_candidate_fact_ids
        ),
        "per_fact_layer": [list(pair) for pair in d.per_fact_layer],
    }


def project_p1_diagnostics(
    diagnostics: Sequence[P1QueryArmDiagnostic],
    aggregate: Optional[P1DiagnosticAggregate] = None,
) -> Dict[str, object]:
    """Content-safe projection ready to embed ADDITIVELY in a future B3 artifact.

    Contains ONLY ids/tokens/booleans/counts — never answer text or provider
    content. ``answer_contained_shares_grader_matcher`` documents that the
    answer-contained and grader-recognized signals use the same matcher (B3 §11)."""
    agg = aggregate if aggregate is not None else aggregate_p1_diagnostics(diagnostics)
    return {
        "b3_mode": "OBSERVABILITY_ONLY",
        "answer_contained_shares_grader_matcher": True,
        "per_query_arm": [_project_one(d) for d in diagnostics],
        "aggregate": {
            "positive_query_arm_count": agg.positive_query_arm_count,
            "required_fact_total": agg.required_fact_total,
            "required_fact_in_evidence_count": agg.required_fact_in_evidence_count,
            "required_fact_in_answer_count": agg.required_fact_in_answer_count,
            "grader_recognized_count": agg.grader_recognized_count,
            "retrieval_missing_count": agg.retrieval_missing_count,
            "generation_missing_after_evidence_count": (
                agg.generation_missing_after_evidence_count
            ),
            "grading_mismatch_candidate_count": agg.grading_mismatch_candidate_count,
            "ok_count": agg.ok_count,
        },
    }


__all__ = [
    "P1FactLayer",
    "P1QueryArmDiagnostic",
    "P1DiagnosticAggregate",
    "fact_source_provenance",
    "evidence_contains_fact",
    "diagnose_query_arm",
    "aggregate_p1_diagnostics",
    "project_p1_diagnostics",
]
