"""PN02D-B1-EW8 — scientific result observability (CLI-first, observability-only).

EVALUATION-ONLY, ZERO provider traffic. EF5 proved the EXEC #11 gap: the frozen evaluator
ALREADY computes the scientific result into ``outcome.report`` (stage1/leakage, retrieval/
multihop verdicts, scientific_outputs, membership_removal, workload_ledger_snapshot), but the
CLI dropped those keys. EW8 adds a PURE PROJECTION (``cli_live_pn02d._project_scientific_result``)
that serializes those already-computed fields into the payload — it NEVER recomputes the
verdicts, invents NO synthetic overall PASS boolean, and keeps ``state="COMPLETE"`` technical-only.

These tests drive the projection directly with synthetic report dicts shaped like
``reportpn02.build_report`` output. They also cover the EW7->EW8 governance repoint (current
approved checkpoint == EW8, EW7/EW6/... historical), lifecycle State A/B, and substitution
rejection. No scientific-logic/provider/envelope change; artifact persistence deferred.
"""
from __future__ import annotations

import json

import graphrag_pn02db0cb_common as C
import pytest

from open_notebook.integrations.graphrag.eval import authb1r2pn02d as B
from open_notebook.integrations.graphrag.eval.authlivepn02d import (
    PN02_PROVIDER_RUN_AUTHORIZED,
)
from open_notebook.integrations.graphrag.eval.authmintlivepn02d import (
    EXPECTED_B1_R2_CHECKPOINT_TAG,
    EXPECTED_EW1_CHECKPOINT_TAG,
    EXPECTED_EW2_CHECKPOINT_TAG,
    EXPECTED_EW3_CHECKPOINT_TAG,
    EXPECTED_EW4_CHECKPOINT_TAG,
    EXPECTED_EW5_CHECKPOINT_TAG,
    EXPECTED_EW6_CHECKPOINT_TAG,
    EXPECTED_EW7_CHECKPOINT_TAG,
    EXPECTED_EW8_CHECKPOINT_TAG,
    EXPECTED_PF1_CHECKPOINT_TAG,
    RealTrustedB1R2Reader,
    current_approved_b1_r2_checkpoint,
    verify_b1_r2_checkpoint,
)
from open_notebook.integrations.graphrag.eval.cli_live_pn02d import (
    _project_scientific_result,
)

# A source-text-like sentinel that must NEVER appear in the projection (content safety).
_SOURCE_TEXT_SENTINEL = "SECRET_SOURCE_DOCUMENT_BODY_agribank_customer_pii"
_SECRETISH = ["sk-or-v1-deadbeefdeadbeefdeadbeef", "Bearer sk-or-abc", "Authorization: Bearer x"]


def _report(
    *,
    leak: int = 0,
    retrieval_verdict: str = "NO",
    multihop_verdict: str = "INCONCLUSIVE",
    qe_spent: int = 24,
    gd_spent: int = 24,
    vec_spent: int = 24,
) -> dict:
    """A synthetic ``outcome.report`` shaped like ``reportpn02.build_report`` for a
    technically-complete run. ``leak>0`` models stage1 isolation FAILURE (fail-closed: value
    verdicts NOT_EVALUATED, retrieval/multihop absent), exactly as ``run_offline_evaluation``."""
    isolation = "YES" if leak == 0 else "NO"
    r: dict = {
        "report_kind": "graphrag_pn02_b1_offline_report",
        "stage1": {
            "stage1_status": "PASS" if leak == 0 else "FAIL",
            "stage2_authorized_by_result": leak == 0,
            "violations": [] if leak == 0 else ["cross_notebook_leakage"],
            "metrics": {
                "probe_count": 24,
                "cross_notebook_leak_query_count": leak,
                "cross_notebook_leakage_rate": round(leak / 24, 6),
            },
        },
        "scientific_outputs": {
            "PER_NOTEBOOK_ISOLATION_EVIDENCED": isolation,
            "PER_NOTEBOOK_GRAPH_RETRIEVAL_VALUE_EVIDENCED": (
                retrieval_verdict if leak == 0 else "NOT_EVALUATED"
            ),
            "PER_NOTEBOOK_GRAPH_QA_VALUE_EVIDENCED": "NOT_EVALUATED",
            "PER_NOTEBOOK_MULTIHOP_INCREMENTAL_VALUE_EVIDENCED": (
                multihop_verdict if leak == 0 else "NOT_EVALUATED"
            ),
            "notes": [] if leak == 0 else ["stage1_failed", "cross_notebook_leakage"],
        },
        "membership_removal": {
            "shared_source": "SH_AB",
            "postcondition_removed_holds": True,
            "postcondition_retained_holds": True,
        },
        "driver": {
            "b0b_offline": True,
            "workload_ledger_snapshot": {
                "QUERY_EMBEDDING": {"spent": qe_spent, "cap": 26},
                "GD_QUERY": {"spent": gd_spent, "cap": 26},
                "VECTOR_QUERY": {"spent": vec_spent, "cap": 26},
            },
        },
    }
    if leak == 0:  # isolation evidenced -> stage-2 dimensional verdicts present
        r["retrieval"] = {"verdict": retrieval_verdict, "rule": "R2", "new_req": 3}
        r["multihop"] = {"verdict": multihop_verdict, "rule": "M3", "multihop_incremental_required_recovery": 0}
    return r


# --------------------------------------------------------------------------- #
# §19/§22/§23-§28 — projection surfaces the evaluator's already-computed result
# --------------------------------------------------------------------------- #


def test_tech_complete_isolation_evidenced_surfaced():
    # §19: technical-complete + leakage==0 + isolation evidenced -> all authoritative values surfaced.
    sci = _project_scientific_result(_report(leak=0, retrieval_verdict="NO", multihop_verdict="YES"))
    assert sci["stage1_status"] == "PASS"
    assert sci["stage2_authorized"] is True
    assert sci["isolation_evidenced"] == "YES"
    assert sci["leakage_count"] == 0
    assert sci["leakage_rate"] == 0.0
    assert sci["retrieval_verdict"] == "NO"
    assert sci["multihop_verdict"] == "YES"
    assert sci["scientific_outputs"]["PER_NOTEBOOK_ISOLATION_EVIDENCED"] == "YES"
    assert sci["membership_removal"]["postcondition_removed_holds"] is True


def test_tech_complete_scientific_fail_distinct():
    # §20: technical-complete + leakage>0 -> isolation NOT evidenced, stage2 unauthorized, value
    # verdicts NOT_EVALUATED. The projection surfaces the scientific ISOLATION FAILURE while the
    # (separate) technical status stays COMPLETED.
    sci = _project_scientific_result(_report(leak=3))
    assert sci["stage1_status"] == "FAIL"
    assert sci["stage2_authorized"] is False
    assert sci["isolation_evidenced"] == "NO"
    assert sci["leakage_count"] == 3
    assert sci["leakage_rate"] > 0
    assert "cross_notebook_leakage" in sci["violations"]
    assert sci["retrieval_verdict"] is None  # fail-closed: retrieval absent
    assert sci["scientific_outputs"]["PER_NOTEBOOK_GRAPH_RETRIEVAL_VALUE_EVIDENCED"] == "NOT_EVALUATED"


def test_tech_blocked_remains_distinct():
    # §21: a technical block before the query stage has no stage1/scientific keys -> the projection
    # fabricates NO completed verdicts (all None), so BLOCKED stays distinct from a scientific FAIL.
    blocked = {"report_kind": "graphrag_pn02_b1_offline_report",
               "driver": {"failure_reason": "index_completeness_below_24_of_24"}}
    sci = _project_scientific_result(blocked)
    assert sci["stage1_status"] is None
    assert sci["isolation_evidenced"] is None
    assert sci["leakage_count"] is None
    assert sci["retrieval_verdict"] is None
    assert sci["multihop_verdict"] is None
    assert sci["scientific_outputs"] is None


def test_cli_serializes_not_recomputes():
    # §22: the projection equals the report's OWN values exactly (serialization, not recomputation).
    # It must faithfully reflect an internally-INCONSISTENT report rather than "correct" it — proof
    # that no leakage/retrieval/multihop rule is re-evaluated in the CLI.
    r = _report(leak=0, retrieval_verdict="NO")
    r["stage1"]["metrics"]["cross_notebook_leak_query_count"] = 7  # deliberately inconsistent
    r["retrieval"]["verdict"] = "YES"
    sci = _project_scientific_result(r)
    assert sci["leakage_count"] == 7  # echoed verbatim, NOT recomputed to 0
    assert sci["retrieval_verdict"] == "YES"  # echoed verbatim


def test_leakage_count_and_rate_surfaced():
    sci = _project_scientific_result(_report(leak=2))
    assert sci["leakage_count"] == 2
    assert sci["leakage_rate"] == round(2 / 24, 6)


def test_retrieval_multihop_scientific_outputs_surfaced():
    sci = _project_scientific_result(_report(leak=0, retrieval_verdict="INCONCLUSIVE", multihop_verdict="NO"))
    assert sci["retrieval_verdict"] == "INCONCLUSIVE"
    assert sci["multihop_verdict"] == "NO"
    assert set(sci["scientific_outputs"]).issuperset(
        {
            "PER_NOTEBOOK_ISOLATION_EVIDENCED",
            "PER_NOTEBOOK_GRAPH_RETRIEVAL_VALUE_EVIDENCED",
            "PER_NOTEBOOK_GRAPH_QA_VALUE_EVIDENCED",
            "PER_NOTEBOOK_MULTIHOP_INCREMENTAL_VALUE_EVIDENCED",
        }
    )


def test_query_gd_vector_counters_surfaced_from_ledger():
    # §10/§11/§12: calls come from the authoritative workload-ledger snapshot (no success inflation).
    sci = _project_scientific_result(_report(qe_spent=26, gd_spent=25, vec_spent=24))
    assert sci["query_embedding_attempts"] == 26
    assert sci["gd_calls"] == 25
    assert sci["vector_queries"] == 24
    # no success counters are asserted/inflated
    assert "gd_successes" not in sci
    assert "vector_successes" not in sci


def test_membership_removal_result_surfaced():
    sci = _project_scientific_result(_report(leak=0))
    assert sci["membership_removal"]["shared_source"] == "SH_AB"
    assert sci["membership_removal"]["postcondition_retained_holds"] is True


def test_scientific_result_secret_and_content_safe():
    # §29/§30: even if a report were polluted with source text / secret-like strings in fields the
    # projection does NOT read, they must not appear in the projected output.
    r = _report(leak=0)
    r["raw_source_text"] = _SOURCE_TEXT_SENTINEL  # a key the projection never reads
    r["stage1"]["metrics"]["debug_body"] = _SECRETISH[0]  # not a projected metric
    sci = _project_scientific_result(r)
    blob = json.dumps(sci)
    assert _SOURCE_TEXT_SENTINEL not in blob
    for s in _SECRETISH:
        assert s not in blob
    for banned in ("Authorization", "Bearer", "sk-or-", "OPENROUTER_API_KEY", "raw_source_text"):
        assert banned not in blob


def test_exec11_shape_now_observable():
    # §32: a report shaped like EXEC #11 (technical-complete, isolation evidenced) now yields a
    # payload sufficient to read leakage, isolation, verdicts, and query/GD/vector counters.
    sci = _project_scientific_result(_report(leak=0, retrieval_verdict="NO", multihop_verdict="INCONCLUSIVE"))
    for key in (
        "stage1_status",
        "isolation_evidenced",
        "leakage_count",
        "leakage_rate",
        "retrieval_verdict",
        "multihop_verdict",
        "scientific_outputs",
        "membership_removal",
        "query_embedding_attempts",
        "gd_calls",
        "vector_queries",
    ):
        assert key in sci, key
    assert sci["leakage_count"] is not None
    assert sci["isolation_evidenced"] == "YES"


# --------------------------------------------------------------------------- #
# §37/§38/§39/§40 — EW7->EW8 governance repoint + successor lifecycle
# --------------------------------------------------------------------------- #


def test_ew8_governance_repoint_complete():
    assert (
        EXPECTED_EW8_CHECKPOINT_TAG
        == "graphrag-pn02db1ew8-scientific-result-observability-approved"
    )
    assert EXPECTED_EW8_CHECKPOINT_TAG != EXPECTED_EW7_CHECKPOINT_TAG
    assert current_approved_b1_r2_checkpoint() == EXPECTED_EW8_CHECKPOINT_TAG
    assert B.B1_R2_EXPECTED_CHECKPOINT_TAG == EXPECTED_EW8_CHECKPOINT_TAG
    assert current_approved_b1_r2_checkpoint() != EXPECTED_EW7_CHECKPOINT_TAG


def test_ew8_successor_tag_git_state_is_lifecycle_valid():
    # §40: lifecycle-aware across THREE states — never a permanent tag-absence OR permanent
    # tag-at-HEAD assumption. STATE A (pre-checkpoint): EW8 tag absent -> empty peel. STATE 1
    # (checkpoint-current): exact tag present, valid 40-hex peel == authorized HEAD. STATE 2
    # (successor-head): a later successor commit moved HEAD, so the EW8 tag still exists and peels
    # to its IMMUTABLE approved commit but peel != HEAD — HISTORICAL checkpoint validity, NOT
    # current-HEAD execution authorization (the B1 gate must fail closed with the exact reason).
    _EW8_COMMIT = "95670adce1ac243f894b14de74c564cabc5325b4"
    obs = RealTrustedB1R2Reader().observe(EXPECTED_EW8_CHECKPOINT_TAG)
    if not obs.observed_tag_exists:
        assert obs.observed_tag_peel == ""  # STATE A
    else:
        assert obs.checkpoint_tag == EXPECTED_EW8_CHECKPOINT_TAG
        assert len(obs.observed_tag_peel) == 40 and all(
            c in "0123456789abcdef" for c in obs.observed_tag_peel
        )
        if obs.observed_tag_peel == obs.observed_head:
            pass  # STATE 1 — the EW8 checkpoint is the current authorized HEAD
        else:
            # STATE 2 — EW8 remains the IMMUTABLE historical checkpoint (peels to its own approved
            # commit) but HEAD has moved past it. Historical validity != authorization: the
            # current-HEAD B1 trust gate FAILS CLOSED with the exact not-at-HEAD reason.
            assert obs.observed_tag_peel == _EW8_COMMIT
            reasons = verify_b1_r2_checkpoint(
                reader=RealTrustedB1R2Reader(),
                operator_grant=C.frozen_test_grant(b1_r2_checkpoint=EXPECTED_EW8_CHECKPOINT_TAG),
                approved_expected_checkpoint=EXPECTED_EW8_CHECKPOINT_TAG,
                git_baseline=C.clean_git_baseline(
                    commit=obs.observed_head, tag=EXPECTED_EW8_CHECKPOINT_TAG
                ),
            )
            assert "b1_r2_tag_not_at_authorized_head" in reasons


def test_ew8_exact_tag_with_peel_is_accepted_by_git_gate():
    future = "e8e8e8e8" + "0" * 32
    grant = C.frozen_test_grant(b1_r2_checkpoint=EXPECTED_EW8_CHECKPOINT_TAG)
    reasons = verify_b1_r2_checkpoint(
        reader=C.b1r2_reader_ok(tag=EXPECTED_EW8_CHECKPOINT_TAG, peel=future, head=future),
        operator_grant=grant,
        approved_expected_checkpoint=EXPECTED_EW8_CHECKPOINT_TAG,
        git_baseline=C.clean_git_baseline(commit=future, tag=EXPECTED_EW8_CHECKPOINT_TAG),
    )
    assert reasons == []


def test_wrong_ew8_peel_fails_closed():
    head = "aaaa1111" + "0" * 32
    wrong = "bbbb2222" + "0" * 32
    grant = C.frozen_test_grant(b1_r2_checkpoint=EXPECTED_EW8_CHECKPOINT_TAG)
    reasons = verify_b1_r2_checkpoint(
        reader=C.b1r2_reader_ok(tag=EXPECTED_EW8_CHECKPOINT_TAG, peel=wrong, head=head),
        operator_grant=grant,
        approved_expected_checkpoint=EXPECTED_EW8_CHECKPOINT_TAG,
        git_baseline=C.clean_git_baseline(commit=head, tag=EXPECTED_EW8_CHECKPOINT_TAG),
    )
    assert "b1_r2_tag_not_at_authorized_head" in reasons


@pytest.mark.parametrize(
    "substitute",
    [
        "graphrag-arbitrary-unrelated-tag",
        EXPECTED_EW7_CHECKPOINT_TAG,
        EXPECTED_EW6_CHECKPOINT_TAG,
        EXPECTED_EW5_CHECKPOINT_TAG,
        EXPECTED_EW4_CHECKPOINT_TAG,
        EXPECTED_EW3_CHECKPOINT_TAG,
        EXPECTED_EW2_CHECKPOINT_TAG,
        EXPECTED_EW1_CHECKPOINT_TAG,
        EXPECTED_PF1_CHECKPOINT_TAG,
        EXPECTED_B1_R2_CHECKPOINT_TAG,
    ],
)
def test_historical_or_arbitrary_tag_cannot_substitute_for_ew8(substitute):
    future = "e8e8e8e8" + "0" * 32
    grant = C.frozen_test_grant(b1_r2_checkpoint=EXPECTED_EW8_CHECKPOINT_TAG)
    reasons = verify_b1_r2_checkpoint(
        reader=C.b1r2_reader_ok(tag=substitute, peel=future, head=future),
        operator_grant=grant,
        approved_expected_checkpoint=substitute,
        git_baseline=C.clean_git_baseline(commit=future, tag=substitute),
    )
    assert reasons, f"{substitute} must not satisfy the EW8 checkpoint"


def test_ew7_is_distinct_historical_identity():
    assert EXPECTED_EW7_CHECKPOINT_TAG != EXPECTED_EW8_CHECKPOINT_TAG
    assert (
        EXPECTED_EW7_CHECKPOINT_TAG == "graphrag-pn02db1ew7-index-observation-timing-approved"
    )
    ids = {
        EXPECTED_EW8_CHECKPOINT_TAG,
        EXPECTED_EW7_CHECKPOINT_TAG,
        EXPECTED_EW6_CHECKPOINT_TAG,
        EXPECTED_EW5_CHECKPOINT_TAG,
        EXPECTED_EW4_CHECKPOINT_TAG,
        EXPECTED_EW3_CHECKPOINT_TAG,
        EXPECTED_EW2_CHECKPOINT_TAG,
        EXPECTED_EW1_CHECKPOINT_TAG,
        EXPECTED_PF1_CHECKPOINT_TAG,
        EXPECTED_B1_R2_CHECKPOINT_TAG,
    }
    assert len(ids) == 10


def test_ew8_tag_alone_does_not_authorize_provider_run():
    assert PN02_PROVIDER_RUN_AUTHORIZED is False
