"""Membership-removal lifecycle evaluator + defense-in-depth (task §32-§34).

EVALUATION-ONLY. Nothing in production imports this. Offline evaluation of the
frozen lifecycle transition (PN02A §11a): remove SH_AB from NB_A, retain it in
NB_B. Two layers are checked, matching the design's defense-in-depth:

  * Layer 1 (retriever isolation) — after a SUCCESSFUL per-workspace graph delete,
    GD for NB_A no longer returns SH_AB (fed to the Stage-1 leakage gate as clean
    re-probes).
  * Layer 2 (ON backstop) — even if the derived-store delete FAILS and stale
    SH_AB is still returned by GD, ON membership post-validation rejects it, so
    ``STALE_GRAPH_EVIDENCE_ACCEPTED_AS_VALID = 0`` (task §33/§34). This is an
    OFFLINE simulation — no real deletion call is ever made.

Post-validation is the pure function ``post_validate`` (accepted = returned ∩
current members); it is the same canonical-membership authorization ON applies to
every returned Source.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import AbstractSet, List

from open_notebook.integrations.graphrag.eval.datasetpn02 import (
    MembershipRemovalScenario,
)
from open_notebook.integrations.graphrag.eval.schemaspn02 import (
    RemovalPhase,
    RemovalProbeResult,
)
from open_notebook.integrations.graphrag.eval.stage1pn02 import IsolationProbe


def post_validate(
    returned_sources: AbstractSet[str], current_members: AbstractSet[str]
) -> frozenset[str]:
    """ON canonical-membership authorization: keep only current members.

    This is the backstop that makes stale derived-store evidence inert even if a
    graph delete fails (task §33).
    """
    return frozenset(returned_sources) & frozenset(current_members)


@dataclass(frozen=True)
class RemovalReport:
    shared_source: str
    removed_from_notebook: str
    retained_notebook: str
    accepted_evidence_removed_nb: frozenset[str]
    accepted_evidence_retained_nb: frozenset[str]
    #: Postcondition A: SH_AB is NOT valid evidence/citation for NB_A after removal.
    postcondition_removed_holds: bool
    #: Postcondition B: SH_AB REMAINS valid evidence for NB_B.
    postcondition_retained_holds: bool
    graph_delete_succeeded_removed_nb: bool
    stale_graph_evidence_accepted_as_valid: int

    @property
    def both_postconditions_hold(self) -> bool:
        return self.postcondition_removed_holds and self.postcondition_retained_holds


def evaluate_removal(
    scenario: MembershipRemovalScenario,
    removed_nb_after: RemovalProbeResult,
    retained_nb_after: RemovalProbeResult,
) -> RemovalReport:
    """Evaluate the after-removal re-probes and prove both postconditions.

    ``removed_nb_after`` is the re-probe in NB_A (SH_AB removed);
    ``retained_nb_after`` is the re-probe in NB_B (SH_AB retained). Works whether
    or not the simulated graph delete succeeded — the ON backstop is applied
    regardless.
    """
    shared = scenario.shared_source
    if removed_nb_after.notebook_id != scenario.removed_from_notebook:
        raise ValueError("removed_nb_after is not for the removed-from notebook")
    if retained_nb_after.notebook_id != scenario.retained_notebook:
        raise ValueError("retained_nb_after is not for the retained notebook")
    if removed_nb_after.phase is not RemovalPhase.AFTER:
        raise ValueError("removed_nb_after must be an AFTER-phase probe")
    if retained_nb_after.phase is not RemovalPhase.AFTER:
        raise ValueError("retained_nb_after must be an AFTER-phase probe")

    members_a_after = scenario.members_after_removed_nb
    members_b_after = scenario.members_after_retained_nb

    # Apply the ON backstop to the (possibly stale) returned GD evidence.
    accepted_a = post_validate(
        removed_nb_after.gd_evidence.as_set(), members_a_after
    )
    accepted_b = post_validate(
        retained_nb_after.gd_evidence.as_set(), members_b_after
    )

    # Postcondition A: removed shared source is NOT accepted for NB_A.
    post_a = shared not in accepted_a
    # Postcondition B: shared source IS still accepted for NB_B.
    post_b = shared in accepted_b

    # STALE: the removed source was returned by GD (delete failed / stale) but
    # would have been ACCEPTED — must be 0 because post_validate drops it.
    stale_present_in_raw = shared in removed_nb_after.gd_evidence.as_set()
    stale_accepted = 1 if (stale_present_in_raw and shared in accepted_a) else 0

    return RemovalReport(
        shared_source=shared,
        removed_from_notebook=scenario.removed_from_notebook,
        retained_notebook=scenario.retained_notebook,
        accepted_evidence_removed_nb=accepted_a,
        accepted_evidence_retained_nb=accepted_b,
        postcondition_removed_holds=post_a,
        postcondition_retained_holds=post_b,
        graph_delete_succeeded_removed_nb=removed_nb_after.graph_delete_succeeded,
        stale_graph_evidence_accepted_as_valid=stale_accepted,
    )


def removal_reprobe_isolation_probes(
    scenario: MembershipRemovalScenario,
    removed_nb_after: RemovalProbeResult,
    retained_nb_after: RemovalProbeResult,
) -> List[IsolationProbe]:
    """Build the 2 after-removal Stage-1 IsolationProbes (task §20/§17a).

    ``accepted_citation_sources`` is the post-validated (member-filtered) evidence,
    so a stale returned Source never becomes an accepted citation. ``members`` is
    the CURRENT (post-removal) member set. The removed Source is flagged in
    ``stale_accepted_sources`` so the Stage-1 STALE counter can prove it was
    rejected.
    """
    probes: List[IsolationProbe] = []
    for probe, members in (
        (removed_nb_after, scenario.members_after_removed_nb),
        (retained_nb_after, scenario.members_after_retained_nb),
    ):
        accepted = post_validate(probe.gd_evidence.as_set(), members)
        probes.append(
            IsolationProbe(
                query_id=probe.query_id,
                notebook_id=probe.notebook_id,
                members=frozenset(members),
                gd_candidate_sources=frozenset(probe.gd_evidence.as_set()),
                provenance_foreign=probe.gd_evidence.stats.foreign,
                provenance_malformed=probe.gd_evidence.stats.malformed,
                accepted_citation_sources=accepted,
                stale_accepted_sources=frozenset({scenario.shared_source}),
            )
        )
    return probes


__all__ = [
    "post_validate",
    "RemovalReport",
    "evaluate_removal",
    "removal_reprobe_isolation_probes",
]
