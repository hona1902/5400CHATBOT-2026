"""GraphRAG-PN02 Stage-1 isolation gate + fail-closed tests (task §49, §50, §21).

OFFLINE. Five distinct synthetic failing result sets each deterministically FAIL
Stage 1; one all-clean set PASSES; and Stage 2 is structurally unreachable after a
Stage-1 failure.
"""

from __future__ import annotations

import pytest

from open_notebook.integrations.graphrag.eval.normalizepn02 import normalize_graph
from open_notebook.integrations.graphrag.eval.stage1pn02 import (
    IsolationProbe,
    Stage1Authorization,
    Stage2Blocked,
    compute_stage1_metrics,
    evaluate_stage1,
    require_stage2_authorization,
)

MEMBERS_A = frozenset({"A1", "A2", "A3", "A4", "A5", "A6", "SH_AB", "SH_AC"})
ALLOW = MEMBERS_A | {"B1", "B2", "B3", "C1", "SH_BC"}
HASH = "deadbeef"


def _clean_probe(qid: str = "PN02Q01") -> IsolationProbe:
    ev = normalize_graph(["A1", "A2"], allowlist=ALLOW)
    return IsolationProbe(
        query_id=qid,
        notebook_id="NB_A",
        members=MEMBERS_A,
        gd_candidate_sources=ev.as_set(),
        provenance_foreign=ev.stats.foreign,
        provenance_malformed=ev.stats.malformed,
    )


def test_positive_all_clean_passes() -> None:
    probes = [_clean_probe(f"Q{i}") for i in range(5)]
    decision, auth = evaluate_stage1(probes, fixture_hash=HASH)
    assert decision.stage1_status == "PASS"
    assert decision.stage2_authorized_by_result is True
    assert decision.violations == ()
    assert isinstance(auth, Stage1Authorization)
    m = decision.metrics
    assert m.cross_notebook_leakage_rate == 0
    assert m.cross_notebook_leak_query_count == 0
    assert m.cross_notebook_leak_source_occurrences == 0
    assert m.provenance_foreign == 0
    assert m.provenance_malformed == 0
    assert m.citation_membership_invalid == 0
    assert m.stale_graph_evidence_accepted_as_valid == 0


def test_negative_cross_notebook_leakage_fails() -> None:
    # A-only query receives a B-only Source (B3 is a valid fixture Source, non-member).
    ev = normalize_graph(["A1", "B3"], allowlist=ALLOW)
    probe = IsolationProbe(
        query_id="Q", notebook_id="NB_A", members=MEMBERS_A,
        gd_candidate_sources=ev.as_set(),
    )
    decision, auth = evaluate_stage1([probe], fixture_hash=HASH)
    assert decision.stage1_status == "FAIL"
    assert auth is None
    assert decision.metrics.cross_notebook_leak_query_count == 1
    assert decision.metrics.cross_notebook_leak_source_occurrences == 1


def test_negative_foreign_provenance_fails() -> None:
    ev = normalize_graph(["A1", "ZZ_not_a_fixture_source"], allowlist=ALLOW)
    probe = IsolationProbe(
        query_id="Q", notebook_id="NB_A", members=MEMBERS_A,
        gd_candidate_sources=ev.as_set(),
        provenance_foreign=ev.stats.foreign,
    )
    assert ev.stats.foreign == 1
    decision, auth = evaluate_stage1([probe], fixture_hash=HASH)
    assert decision.stage1_status == "FAIL"
    assert auth is None
    assert decision.metrics.provenance_foreign == 1


def test_negative_malformed_provenance_fails() -> None:
    ev = normalize_graph(["A1", "", None], allowlist=ALLOW)  # type: ignore[list-item]
    probe = IsolationProbe(
        query_id="Q", notebook_id="NB_A", members=MEMBERS_A,
        gd_candidate_sources=ev.as_set(),
        provenance_malformed=ev.stats.malformed,
    )
    assert ev.stats.malformed == 2
    decision, auth = evaluate_stage1([probe], fixture_hash=HASH)
    assert decision.stage1_status == "FAIL"
    assert auth is None
    assert decision.metrics.provenance_malformed == 2


def test_negative_invalid_citation_membership_fails() -> None:
    # Evidence is clean, but the arm CLAIMS a non-member citation (SH_BC in NB_A).
    ev = normalize_graph(["A1"], allowlist=ALLOW)
    probe = IsolationProbe(
        query_id="Q", notebook_id="NB_A", members=MEMBERS_A,
        gd_candidate_sources=ev.as_set(),
        accepted_citation_sources=frozenset({"A1", "SH_BC"}),
    )
    decision, auth = evaluate_stage1([probe], fixture_hash=HASH)
    assert decision.stage1_status == "FAIL"
    assert auth is None
    assert decision.metrics.citation_membership_invalid == 1


def test_negative_stale_removed_source_accepted_fails() -> None:
    # SH_AB was removed from NB_A, yet the arm ACCEPTS it as a valid citation.
    members_after = MEMBERS_A - {"SH_AB"}
    ev = normalize_graph(["A1", "SH_AB"], allowlist=ALLOW)
    probe = IsolationProbe(
        query_id="Q", notebook_id="NB_A", members=members_after,
        gd_candidate_sources=ev.as_set(),
        accepted_citation_sources=frozenset({"A1", "SH_AB"}),  # backstop failed
        stale_accepted_sources=frozenset({"SH_AB"}),
    )
    decision, auth = evaluate_stage1([probe], fixture_hash=HASH)
    assert decision.stage1_status == "FAIL"
    assert auth is None
    assert decision.metrics.stale_graph_evidence_accepted_as_valid == 1
    # (also flagged as leakage + citation-invalid — all three are non-zero)
    assert decision.metrics.cross_notebook_leak_query_count == 1


def test_authorization_cannot_be_forged() -> None:
    with pytest.raises(PermissionError):
        Stage1Authorization(object(), "hash")  # wrong key


def test_require_stage2_authorization_blocks_without_auth() -> None:
    with pytest.raises(Stage2Blocked):
        require_stage2_authorization(None)


def test_require_stage2_authorization_allows_with_auth() -> None:
    decision, auth = evaluate_stage1([_clean_probe()], fixture_hash=HASH)
    assert auth is not None
    assert require_stage2_authorization(auth) is auth


def test_empty_probes_raise() -> None:
    with pytest.raises(ValueError):
        compute_stage1_metrics([])
