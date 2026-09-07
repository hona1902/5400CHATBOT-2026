"""Offline result schemas for the PN02 evaluator (task §15, §26, §56, §57).

EVALUATION-ONLY. Nothing in production imports this. These are the serializable,
content-safe records a FUTURE live runner would emit and that the offline
evaluator consumes; PN02B itself calls no LLM and no retriever, so tests
construct these directly as deterministic synthetic inputs (task §26).

Two vocabularies are kept strictly separate (task §56/§57):

  * ``TechnicalOutcome`` — how a step went technically (COMPLETED / FAILED_*).
  * ``ScienceVerdict``   — the scientific answer (YES/NO/INCONCLUSIVE/NOT_EVALUATED).

A technical failure MUST NOT be encoded as a scientific NO (task §56/§57): it
normally leaves the affected value ``NOT_EVALUATED`` / ``INCONCLUSIVE`` per the
frozen decision semantics.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional, Tuple

from open_notebook.integrations.graphrag.eval.normalizepn02 import (
    NormalizedEvidencePN02,
)


class TechnicalOutcome(str, Enum):
    """Machine-readable technical status (task §56) — NOT a scientific result."""

    COMPLETED = "COMPLETED"
    FAILED_BEFORE_INDEX = "FAILED_BEFORE_INDEX"
    FAILED_WORKSPACE_ATTESTATION = "FAILED_WORKSPACE_ATTESTATION"
    FAILED_INDEXING = "FAILED_INDEXING"
    FAILED_STAGE1_ISOLATION = "FAILED_STAGE1_ISOLATION"
    FAILED_GD_QUERY = "FAILED_GD_QUERY"
    FAILED_VECTOR_QUERY = "FAILED_VECTOR_QUERY"
    FAILED_FINAL_ANSWER = "FAILED_FINAL_ANSWER"


class ScienceVerdict(str, Enum):
    """The four scientific-output states (task §57)."""

    YES = "YES"
    NO = "NO"
    INCONCLUSIVE = "INCONCLUSIVE"
    NOT_EVALUATED = "NOT_EVALUATED"


class ArmId(str, Enum):
    """Stage-2 QA arms (PN02A §10a)."""

    QA_V = "QA-V"
    QA_GD = "QA-GD"
    QA_VGD = "QA-V+GD"


class RemovalPhase(str, Enum):
    BEFORE = "before"
    AFTER = "after"


# --------------------------------------------------------------------------- #
# Stage-1 evidence results
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class VectorEvidenceResult:
    """Notebook-scoped vector baseline (V) evidence for one query.

    ``evidence`` is a RANKED, deduped candidate list (K=3 and K=5 are slices of
    it — PN02A §17). By §8b the vector seam is structurally confined to current
    members, so a well-formed V result cannot leak by construction; the schema
    still carries the full evidence so the evaluator can verify that.
    """

    query_id: str
    notebook_id: str
    evidence: NormalizedEvidencePN02
    latency_ms: Optional[int] = None
    outcome: TechnicalOutcome = TechnicalOutcome.COMPLETED

    def __post_init__(self) -> None:
        if self.outcome is TechnicalOutcome.COMPLETED and not self.evidence.ordered:
            raise ValueError("vector evidence must be ordered (ranked)")


@dataclass(frozen=True)
class GDEvidenceResult:
    """Notebook-isolated LightRAG ``/query/data`` (GD) evidence for one query.

    ``evidence`` is an UNORDERED Source SET (PN02A §18). No rank/score field
    exists — ``GD_EXPOSES_VALID_RANK/SCORE = NO``.
    """

    query_id: str
    notebook_id: str
    evidence: NormalizedEvidencePN02
    latency_ms: Optional[int] = None
    outcome: TechnicalOutcome = TechnicalOutcome.COMPLETED

    def __post_init__(self) -> None:
        if self.evidence.ordered:
            raise ValueError(
                "GD evidence must be UNORDERED — /query/data exposes no valid rank "
                "(PN02A §18)"
            )


# --------------------------------------------------------------------------- #
# Stage-2 QA answer results
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class QAAnswerResult:
    """Deterministic answer record for one (query, arm) (task §26).

    Grading is deterministic (tokens + citations, PN02A §10c) and is performed by
    the QA evaluator against fixture ground truth — this record only carries what
    a live runner would produce. Provide EITHER ``answer_text`` (the grader
    substring-scans it for the fixture's expected/forbidden tokens) OR
    ``emitted_answer_facts`` (a pre-extracted token set, convenient for synthetic
    tests). ``answer_text`` is content and is NEVER written to a content-safe
    artifact (task §55).
    """

    query_id: str
    notebook_id: str
    arm: ArmId
    abstained: bool
    citation_source_ids: Tuple[str, ...] = ()
    answer_text: Optional[str] = None
    emitted_answer_facts: Optional[Tuple[str, ...]] = None
    latency_ms: Optional[int] = None
    outcome: TechnicalOutcome = TechnicalOutcome.COMPLETED


# --------------------------------------------------------------------------- #
# Membership-removal probe results (task §32/§33)
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class RemovalProbeResult:
    """One SHARED_SOURCE re-probe of a notebook before/after removal.

    ``graph_delete_succeeded`` models the defense-in-depth case (task §33/§34):
    when False, the derived graph store still contains the removed Source, so
    ``gd_evidence`` may still carry it — ON membership post-validation must still
    reject it as valid evidence/citation.
    """

    query_id: str
    notebook_id: str
    phase: RemovalPhase
    gd_evidence: NormalizedEvidencePN02
    vector_evidence: NormalizedEvidencePN02
    graph_delete_succeeded: bool = True


# --------------------------------------------------------------------------- #
# Scientific output bundle (task §57)
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class ScientificOutputs:
    """The four independent scientific verdicts (task §57). Kept separate from any
    technical outcome; a technical failure leaves value NOT_EVALUATED/INCONCLUSIVE.
    """

    per_notebook_isolation_evidenced: ScienceVerdict
    per_notebook_graph_retrieval_value_evidenced: ScienceVerdict
    per_notebook_graph_qa_value_evidenced: ScienceVerdict
    per_notebook_multihop_incremental_value_evidenced: ScienceVerdict
    #: Non-decisional annotations (e.g. which QA arm(s) were positive).
    notes: Tuple[str, ...] = field(default_factory=tuple)


__all__ = [
    "TechnicalOutcome",
    "ScienceVerdict",
    "ArmId",
    "RemovalPhase",
    "VectorEvidenceResult",
    "GDEvidenceResult",
    "QAAnswerResult",
    "RemovalProbeResult",
    "ScientificOutputs",
]
