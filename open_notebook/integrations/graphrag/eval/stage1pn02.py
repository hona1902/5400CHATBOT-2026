"""Stage-1 isolation/evidence-validity evaluator + HARD fail-closed gate.

EVALUATION-ONLY (task §19-§21; PN02A §9/§2/§42). Nothing in production imports
this. Stage 1 is the security/isolation gate that MUST pass before any Stage-2 QA
evaluation. The fail-closed property is STRUCTURAL, not a printed warning
(task §21): Stage-2 entrypoints require a ``Stage1Authorization`` capability that
``evaluate_stage1`` mints ONLY on a full PASS. A failed Stage 1 returns no
authorization, so Stage 2 cannot be reached by any normal path
(``STAGE_2_AFTER_STAGE_1_FAILURE_POSSIBLE = NO``).

The gate is exact ``= 0`` on every frozen hard condition (PN02A §9b + task §20):
CROSS_NOTEBOOK_LEAKAGE_RATE, CROSS_NOTEBOOK_LEAK_QUERY_COUNT,
CROSS_NOTEBOOK_LEAK_SOURCE_OCCURRENCES, PROVENANCE_FOREIGN, PROVENANCE_MALFORMED,
CITATION_MEMBERSHIP_INVALID, STALE_GRAPH_EVIDENCE_ACCEPTED_AS_VALID. There is NO
averaging away a single leak (PN02A §9b).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import FrozenSet, List, Optional, Sequence, Tuple

from open_notebook.integrations.graphrag.eval.metricspn02 import (
    citation_invalid_sources,
    leaked_sources,
)

STAGE1_PASS = "PASS"
STAGE1_FAIL = "FAIL"


class Stage2Blocked(RuntimeError):
    """Stage 2 was attempted without a valid Stage-1 authorization (fail-closed)."""


# Private, unforgeable minting key — only this module holds it.
_AUTH_KEY = object()


class Stage1Authorization:
    """Capability proving Stage 1 PASSED. Minted ONLY by ``evaluate_stage1``.

    It cannot be constructed directly (a manual attempt raises), so Stage-2
    functions that require it cannot run after a Stage-1 failure (task §21).
    """

    __slots__ = ("fixture_hash",)

    def __init__(self, key: object, fixture_hash: str) -> None:
        if key is not _AUTH_KEY:
            raise PermissionError(
                "Stage1Authorization is minted only by evaluate_stage1 on a PASS; "
                "it cannot be constructed directly (fail-closed, task §21)"
            )
        self.fixture_hash = fixture_hash


@dataclass(frozen=True)
class IsolationProbe:
    """One isolation observation for the Stage-1 gate.

    Covers a baseline query OR a membership-removal re-probe (26 total). ``members``
    is the CURRENT member set (already reduced for a removal re-probe).
    ``gd_candidate_sources`` is the normalized VALID fixture Source set returned by
    GD (the system under test). ``accepted_citation_sources`` is what ON would
    accept as valid citations after membership post-validation (default: the
    member subset of the GD candidates). ``stale_accepted_sources`` are Sources
    accepted despite having been REMOVED from this notebook — must be empty in a
    correct system (defense-in-depth, task §33/§34).
    """

    query_id: str
    notebook_id: str
    members: FrozenSet[str]
    gd_candidate_sources: FrozenSet[str]
    provenance_foreign: int = 0
    provenance_malformed: int = 0
    accepted_citation_sources: Optional[FrozenSet[str]] = None
    stale_accepted_sources: FrozenSet[str] = frozenset()

    def effective_citations(self) -> FrozenSet[str]:
        if self.accepted_citation_sources is not None:
            return self.accepted_citation_sources
        # Default honest behavior: cite only member candidates.
        return frozenset(self.gd_candidate_sources) & frozenset(self.members)


@dataclass(frozen=True)
class Stage1Metrics:
    probe_count: int
    cross_notebook_leakage_rate: float
    cross_notebook_leak_query_count: int
    cross_notebook_leak_source_occurrences: int
    provenance_foreign: int
    provenance_malformed: int
    citation_membership_invalid: int
    stale_graph_evidence_accepted_as_valid: int


@dataclass(frozen=True)
class Stage1Decision:
    metrics: Stage1Metrics
    stage1_status: str
    stage2_authorized_by_result: bool
    violations: Tuple[str, ...]


def compute_stage1_metrics(probes: Sequence[IsolationProbe]) -> Stage1Metrics:
    """Exact §9a/§9b metrics over all probes (24 baseline + 2 removal re-probes)."""
    if not probes:
        raise ValueError("Stage-1 requires >=1 probe")
    leak_query_count = 0
    leak_source_occurrences = 0
    provenance_foreign = 0
    provenance_malformed = 0
    citation_invalid = 0
    stale_accepted = 0

    for p in probes:
        leaks = leaked_sources(p.gd_candidate_sources, p.members)
        if leaks:
            leak_query_count += 1
            leak_source_occurrences += len(leaks)
        provenance_foreign += p.provenance_foreign
        provenance_malformed += p.provenance_malformed
        citation_invalid += len(
            citation_invalid_sources(p.effective_citations(), p.members)
        )
        # Stale accepted = accepted citations that are non-members AND were flagged
        # as removed-yet-accepted (defense-in-depth). By construction a subset of
        # citation-invalid, but reported separately (task §20/§33).
        stale = frozenset(p.stale_accepted_sources) - frozenset(p.members)
        stale_accepted += len(stale & p.effective_citations()) if stale else 0

    return Stage1Metrics(
        probe_count=len(probes),
        cross_notebook_leakage_rate=round(leak_query_count / len(probes), 6),
        cross_notebook_leak_query_count=leak_query_count,
        cross_notebook_leak_source_occurrences=leak_source_occurrences,
        provenance_foreign=provenance_foreign,
        provenance_malformed=provenance_malformed,
        citation_membership_invalid=citation_invalid,
        stale_graph_evidence_accepted_as_valid=stale_accepted,
    )


def _violations(m: Stage1Metrics) -> List[str]:
    v: List[str] = []
    if m.cross_notebook_leak_query_count != 0:
        v.append(f"CROSS_NOTEBOOK_LEAK_QUERY_COUNT={m.cross_notebook_leak_query_count}")
    if m.cross_notebook_leak_source_occurrences != 0:
        v.append(
            f"CROSS_NOTEBOOK_LEAK_SOURCE_OCCURRENCES={m.cross_notebook_leak_source_occurrences}"
        )
    if m.cross_notebook_leakage_rate != 0:
        v.append(f"CROSS_NOTEBOOK_LEAKAGE_RATE={m.cross_notebook_leakage_rate}")
    if m.provenance_foreign != 0:
        v.append(f"PROVENANCE_FOREIGN={m.provenance_foreign}")
    if m.provenance_malformed != 0:
        v.append(f"PROVENANCE_MALFORMED={m.provenance_malformed}")
    if m.citation_membership_invalid != 0:
        v.append(f"CITATION_MEMBERSHIP_INVALID={m.citation_membership_invalid}")
    if m.stale_graph_evidence_accepted_as_valid != 0:
        v.append(
            f"STALE_GRAPH_EVIDENCE_ACCEPTED_AS_VALID={m.stale_graph_evidence_accepted_as_valid}"
        )
    return v


def evaluate_stage1(
    probes: Sequence[IsolationProbe], *, fixture_hash: str
) -> Tuple[Stage1Decision, Optional[Stage1Authorization]]:
    """Evaluate Stage 1 and, only on PASS, mint a Stage-2 authorization.

    Returns (decision, authorization). ``authorization`` is None on FAIL — the sole
    structural means to reach Stage 2 (task §21).
    """
    metrics = compute_stage1_metrics(probes)
    violations = _violations(metrics)
    passed = not violations
    decision = Stage1Decision(
        metrics=metrics,
        stage1_status=STAGE1_PASS if passed else STAGE1_FAIL,
        stage2_authorized_by_result=passed,
        violations=tuple(violations),
    )
    auth = Stage1Authorization(_AUTH_KEY, fixture_hash) if passed else None
    return decision, auth


def require_stage2_authorization(auth: Optional[Stage1Authorization]) -> Stage1Authorization:
    """Guard every Stage-2 entrypoint. Raises ``Stage2Blocked`` without a valid auth.

    This is the enforcement seam that makes STAGE_2_AFTER_STAGE_1_FAILURE_POSSIBLE
    = NO (task §21).
    """
    if not isinstance(auth, Stage1Authorization):
        raise Stage2Blocked(
            "Stage 2 is blocked: no valid Stage-1 authorization (Stage 1 did not "
            "PASS). Fail-closed by design (task §21)."
        )
    return auth


__all__ = [
    "STAGE1_PASS",
    "STAGE1_FAIL",
    "Stage2Blocked",
    "Stage1Authorization",
    "IsolationProbe",
    "Stage1Metrics",
    "Stage1Decision",
    "compute_stage1_metrics",
    "evaluate_stage1",
    "require_stage2_authorization",
]
