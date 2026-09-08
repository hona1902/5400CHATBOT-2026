"""Driver technical-outcome taxonomy + lossless boundary mapping (PN02D-B0B).

EVALUATION-ONLY. Nothing in production imports this (``PRODUCTION_IMPORTS_EVAL =
NO``). Implements the frozen driver-internal technical taxonomy (design §33/§54,
task §54) and its EXPLICIT, lossless mapping down to the evaluator-boundary
``schemaspn02.TechnicalOutcome``. A technical failure is a TECHNICAL outcome only —
it is NEVER encoded as a scientific ``NO`` (the scientific verdicts are owned solely
by the PN02B evaluator; a technical failure leaves the affected value
``NOT_EVALUATED`` / ``INCONCLUSIVE``). Two driver states (membership-removal and
cleanup) are post-/off-evaluator-boundary and deliberately map to ``None`` — exactly
as the frozen §33 table shows them with no ``TechnicalOutcome`` member.
"""

from __future__ import annotations

from enum import Enum
from typing import Dict, Optional

from open_notebook.integrations.graphrag.eval.schemaspn02 import TechnicalOutcome


class DriverTechnicalOutcome(str, Enum):
    """Rich driver-internal technical outcomes (design §33). Never a science verdict."""

    FAILED_PRECHECK = "FAILED_PRECHECK"
    FAILED_PROVIDER_AUTHORIZATION = "FAILED_PROVIDER_AUTHORIZATION"
    FAILED_PROVIDER_BINDING = "FAILED_PROVIDER_BINDING"
    FAILED_RUNTIME_ATTESTATION = "FAILED_RUNTIME_ATTESTATION"
    FAILED_INDEX_SUBMIT = "FAILED_INDEX_SUBMIT"
    FAILED_INDEX_COMPLETION = "FAILED_INDEX_COMPLETION"
    FAILED_INDEX_CAP = "FAILED_INDEX_CAP"
    FAILED_BEFORE_QUERY = "FAILED_BEFORE_QUERY"
    FAILED_GD_QUERY = "FAILED_GD_QUERY"
    FAILED_VECTOR_QUERY = "FAILED_VECTOR_QUERY"
    FAILED_MEMBERSHIP_REMOVAL = "FAILED_MEMBERSHIP_REMOVAL"
    FAILED_STAGE1_ISOLATION = "FAILED_STAGE1_ISOLATION"
    FAILED_CLEANUP = "FAILED_CLEANUP"
    COMPLETED = "COMPLETED"


#: Frozen §33 mapping to the evaluator-boundary ``TechnicalOutcome``. ``None`` marks a
#: driver-only state that never reaches the evaluator boundary (removal report /
#: post-run cleanup) — faithful to the "(removal report failure)" / "(post-run)"
#: rows of the frozen table.
_BOUNDARY_MAP: Dict[DriverTechnicalOutcome, Optional[TechnicalOutcome]] = {
    DriverTechnicalOutcome.FAILED_PRECHECK: TechnicalOutcome.FAILED_BEFORE_INDEX,
    DriverTechnicalOutcome.FAILED_PROVIDER_AUTHORIZATION: TechnicalOutcome.FAILED_BEFORE_INDEX,
    DriverTechnicalOutcome.FAILED_PROVIDER_BINDING: TechnicalOutcome.FAILED_BEFORE_INDEX,
    DriverTechnicalOutcome.FAILED_RUNTIME_ATTESTATION: TechnicalOutcome.FAILED_WORKSPACE_ATTESTATION,
    DriverTechnicalOutcome.FAILED_INDEX_SUBMIT: TechnicalOutcome.FAILED_INDEXING,
    DriverTechnicalOutcome.FAILED_INDEX_COMPLETION: TechnicalOutcome.FAILED_INDEXING,
    DriverTechnicalOutcome.FAILED_INDEX_CAP: TechnicalOutcome.FAILED_INDEXING,
    DriverTechnicalOutcome.FAILED_BEFORE_QUERY: TechnicalOutcome.FAILED_INDEXING,
    DriverTechnicalOutcome.FAILED_GD_QUERY: TechnicalOutcome.FAILED_GD_QUERY,
    DriverTechnicalOutcome.FAILED_VECTOR_QUERY: TechnicalOutcome.FAILED_VECTOR_QUERY,
    DriverTechnicalOutcome.FAILED_STAGE1_ISOLATION: TechnicalOutcome.FAILED_STAGE1_ISOLATION,
    DriverTechnicalOutcome.FAILED_MEMBERSHIP_REMOVAL: None,
    DriverTechnicalOutcome.FAILED_CLEANUP: None,
    DriverTechnicalOutcome.COMPLETED: TechnicalOutcome.COMPLETED,
}


def to_technical_outcome(
    outcome: DriverTechnicalOutcome,
) -> Optional[TechnicalOutcome]:
    """Map a driver outcome to the evaluator-boundary ``TechnicalOutcome`` (design §33).

    Returns ``None`` for driver-only states that never cross the evaluator boundary.
    Raises ``KeyError`` if a new driver outcome is added without a mapping (the §54
    lossless-mapping test pins this so a member can never be silently unmapped).
    """
    return _BOUNDARY_MAP[outcome]


def is_evaluator_boundary_outcome(outcome: DriverTechnicalOutcome) -> bool:
    return _BOUNDARY_MAP[outcome] is not None


__all__ = [
    "DriverTechnicalOutcome",
    "to_technical_outcome",
    "is_evaluator_boundary_outcome",
]
