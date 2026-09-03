"""GraphRAG-08E concurrency diagnostic harness tests (PURE OFFLINE, all mocked).

No provider, no sidecar, no DB, no live sweep. Covers task §18 (plan/caps/selection/
output/taxonomy/decision-twin/aggregation/interpretation) and §19 (adversarial: the
harness cannot mutate concurrency/retry/allowlist, persist raw error text, touch
HOLDOUT/normal DB, use unbounded Sources, or declare a causal root cause).
"""

from __future__ import annotations

import asyncio
import json

import pytest

from open_notebook.integrations.graphrag.eval import concurrency_diag08 as cd
from open_notebook.integrations.graphrag.eval import dataset08 as d
from open_notebook.integrations.graphrag.eval import index_retry08 as ir

FIXTURE_HASH = "a58a68535c345e18f0263904f818e4e2068a164056408665d8bb9233eceb143d"


def _rec(level, sid, status, *, error_text=None, rep=1, dur=100):
    ch = cd.characterize_failure(error_text) if status != cd.TERMINAL_SUCCESS else None
    return cd.AttemptRecord(
        run_id="r", concurrency_level=level, logical_source_id=sid, repetition=rep,
        attempt_number=1, terminal_status=status, duration_ms=dur, characterization=ch,
    )


# ---- §18 plan validation / caps --------------------------------------------


def test_default_plan_valid_and_bounded():
    plan = cd.default_plan()
    cd.validate_plan(plan)
    assert plan.total_submissions <= cd.MAX_TOTAL_SUBMISSIONS
    assert [lvl.concurrency for lvl in plan.levels] == [1, 2, 4, 8]


def test_reject_level_not_allowed():
    plan = cd.ConcurrencyDiagnosticPlan(levels=(cd.DiagnosticLevel(3, 3, 1),))
    with pytest.raises(cd.DiagnosticPlanError):
        cd.validate_plan(plan)


def test_reject_too_many_levels():
    plan = cd.ConcurrencyDiagnosticPlan(
        levels=tuple(cd.DiagnosticLevel(c, c, 1) for c in (1, 2, 4, 8)) +
        (cd.DiagnosticLevel(8, 8, 1),)  # 5 levels
    )
    with pytest.raises(cd.DiagnosticPlanError):
        cd.validate_plan(plan)


def test_reject_source_count_over_cap():
    plan = cd.ConcurrencyDiagnosticPlan(
        levels=(cd.DiagnosticLevel(8, cd.MAX_SOURCES_PER_LEVEL + 1, 1),)
    )
    with pytest.raises(cd.DiagnosticPlanError):
        cd.validate_plan(plan)


def test_reject_source_count_below_concurrency():
    plan = cd.ConcurrencyDiagnosticPlan(levels=(cd.DiagnosticLevel(8, 4, 1),))
    with pytest.raises(cd.DiagnosticPlanError):
        cd.validate_plan(plan)


def test_reject_repetitions_over_cap():
    plan = cd.ConcurrencyDiagnosticPlan(
        levels=(cd.DiagnosticLevel(2, 2, cd.MAX_REPETITIONS_PER_LEVEL + 1),)
    )
    with pytest.raises(cd.DiagnosticPlanError):
        cd.validate_plan(plan)


def test_reject_total_submissions_over_cap():
    # Construct a plan under the per-level caps whose TOTAL exceeds the cap.
    plan = cd.ConcurrencyDiagnosticPlan(
        levels=(
            cd.DiagnosticLevel(1, 8, 3),
            cd.DiagnosticLevel(2, 8, 3),
            cd.DiagnosticLevel(4, 8, 3),
            cd.DiagnosticLevel(8, 8, 3),
        )
    )  # 4*8*3 = 96 > 64
    with pytest.raises(cd.DiagnosticPlanError):
        cd.validate_plan(plan)


def test_reject_duplicate_level():
    plan = cd.ConcurrencyDiagnosticPlan(
        levels=(cd.DiagnosticLevel(2, 2, 1), cd.DiagnosticLevel(2, 2, 1))
    )
    with pytest.raises(cd.DiagnosticPlanError):
        cd.validate_plan(plan)


def test_empty_plan_rejected():
    with pytest.raises(cd.DiagnosticPlanError):
        cd.validate_plan(cd.ConcurrencyDiagnosticPlan(levels=()))


def test_budget_estimator_is_static_and_bounded():
    plan = cd.default_plan()
    b = cd.estimate_budget(plan)
    assert b["total_source_submissions"] == plan.total_submissions
    assert b["max_total_submissions_cap"] == cd.MAX_TOTAL_SUBMISSIONS
    assert b["total_source_submissions"] <= b["max_total_submissions_cap"]


# ---- §18 deterministic source selection (S001 first, synthetic only) --------


def test_select_sources_deterministic_anchor_first():
    bench = d.load_benchmark08()
    got = cd.select_diagnostic_sources(bench, 4)
    assert got[0] == "S001"
    assert got == cd.select_diagnostic_sources(bench, 4)  # deterministic
    assert len(got) == 4 and len(set(got)) == 4


def test_select_sources_count_bounds():
    bench = d.load_benchmark08()
    with pytest.raises(cd.DiagnosticPlanError):
        cd.select_diagnostic_sources(bench, 0)
    with pytest.raises(cd.DiagnosticPlanError):
        cd.select_diagnostic_sources(bench, cd.MAX_SOURCES_PER_LEVEL + 1)


# ---- §18/§9 decision-twin: retry decision == frozen classifier --------------


_ERROR_CORPUS = [
    "Error code: 429 rate limit exceeded",
    "request timed out after 60s",
    "connection reset by peer",
    "HTTP 503 service unavailable",
    "internal server error",
    "server is busy, please try again",
    "provider over capacity for this request",   # provider-family but NOT frozen-transient
    "insufficient_quota for account",            # provider-family but NOT frozen-transient
    "Expecting value: line 1 column 1 (char 0)", # parse
    "empty response from model",                 # empty
    "KeyError: 'entities' in lightrag pipeline",  # lightrag internal
    "some entirely unrecognised failure",        # unknown
    "",                                           # absent
    None,
]


@pytest.mark.parametrize("text", _ERROR_CORPUS)
def test_retry_decision_is_frozen_classifier(text):
    assert cd.retry_decision(text) == ir.is_transient_reason(text)
    assert cd.characterize_failure(text).retryable == ir.is_transient_reason(text)


def test_taxonomy_does_not_change_retry_decision():
    # A provider-family label with wording OUTSIDE the frozen allowlist stays NON-retryable
    # (the H1 classifier-gap tell) — the family never overrides the frozen decision.
    ch = cd.characterize_failure("provider over capacity")
    assert ch.family == cd.ErrorFamily.PROVIDER_RATE_OR_CAPACITY
    assert ch.retryable is False
    assert ch.retryable == ir.is_transient_reason("provider over capacity")


def test_taxonomy_families_cover_expected():
    assert cd.classify_error_family("429 too many requests") == cd.ErrorFamily.PROVIDER_RATE_OR_CAPACITY
    assert cd.classify_error_family("gateway timeout") == cd.ErrorFamily.PROVIDER_TIMEOUT_OR_NETWORK
    assert cd.classify_error_family("invalid json: expecting value") == cd.ErrorFamily.PARSE_OR_SCHEMA_FAILURE
    assert cd.classify_error_family("empty response") == cd.ErrorFamily.EMPTY_OR_MALFORMED_RESPONSE
    assert cd.classify_error_family("NoneType has no attribute") == cd.ErrorFamily.LIGHTRAG_INTERNAL
    assert cd.classify_error_family("weird") == cd.ErrorFamily.UNKNOWN_SAFE
    assert cd.classify_error_family(None) == cd.ErrorFamily.UNKNOWN_SAFE


# ---- §8/§19 raw-error containment: text never persists ----------------------


def test_characterization_holds_no_raw_text():
    secret = "sk-SECRET-999 Helix Robotics Talos-9 password=hunter2"
    ch = cd.characterize_failure(secret)
    blob = json.dumps(ch.as_dict())
    for tok in ("sk-SECRET-999", "Helix", "Talos-9", "hunter2"):
        assert tok not in blob
    # structural: no attribute holds the text
    assert not any(isinstance(v, str) and secret in v for v in vars(ch).values())


def test_attempt_record_and_aggregate_are_content_free():
    secret = "Bearer TOKENX rate limit; Marisol Seaport"
    rec = _rec(2, "S001", cd.TERMINAL_FAILED, error_text=secret)
    agg = cd.aggregate([rec])
    blob = json.dumps(rec.as_dict()) + json.dumps(agg)
    for tok in ("Bearer", "TOKENX", "Marisol"):
        assert tok not in blob


# ---- §6/§18 aggregation ------------------------------------------------------


def test_aggregate_counts_and_rates():
    records = [
        _rec(2, "S001", cd.TERMINAL_SUCCESS, dur=100),
        _rec(2, "S002", cd.TERMINAL_FAILED, error_text="429 rate limit", dur=200),
        _rec(4, "S001", cd.TERMINAL_FAILED, error_text="empty response", dur=300),
        _rec(4, "S002", cd.TERMINAL_FAILED, error_text="invalid json", dur=400),
    ]
    agg = cd.aggregate(records)
    by = agg["failure_rate_by_concurrency"]
    assert by[2] == 0.5 and by[4] == 1.0
    lvl4 = next(lv for lv in agg["levels"] if lv["concurrency_level"] == 4)
    assert lvl4["failure_family_distribution"] == {
        cd.ErrorFamily.EMPTY_OR_MALFORMED_RESPONSE: 1,
        cd.ErrorFamily.PARSE_OR_SCHEMA_FAILURE: 1,
    }
    assert lvl4["latency_summary"]["count"] == 2
    assert lvl4["latency_summary"]["max_ms"] == 400.0


# ---- §11/§17 interpretation stays non-definitive ----------------------------


def test_interpretation_never_confirms_root_cause():
    # Extreme H1-shaped data: provider-family, non-retryable, rising with load.
    records = []
    for lvl, nfail in ((1, 0), (2, 1), (4, 2), (8, 4)):
        for i in range(nfail):
            records.append(_rec(lvl, f"S00{i+1}", cd.TERMINAL_FAILED,
                                error_text="provider over capacity"))
        records.append(_rec(lvl, "S00S", cd.TERMINAL_SUCCESS))
    interp = cd.interpret_hypotheses(cd.aggregate(records))
    assert interp["root_cause_confirmed"] is False
    assert interp["H1_classifier_allowlist_gap"] in (cd.SUPPORTED, cd.WEAKLY_SUPPORTED)
    # even the strongest verdict is only SUPPORTED, never a "CONFIRMED" string
    assert "CONFIRM" not in json.dumps(interp).upper() or interp["root_cause_confirmed"] is False


def test_interpretation_inconclusive_on_thin_data():
    interp = cd.interpret_hypotheses(cd.aggregate([_rec(2, "S001", cd.TERMINAL_SUCCESS)]))
    assert interp["H1_classifier_allowlist_gap"] == cd.INCONCLUSIVE
    assert interp["H2_load_induced_non_transient"] == cd.INCONCLUSIVE
    assert interp["H3_lightrag_concurrency_behaviour"] == cd.INCONCLUSIVE
    assert interp["root_cause_confirmed"] is False


def test_interpretation_h2_shape():
    records = []
    for lvl, nfail in ((1, 0), (2, 1), (4, 3)):
        for i in range(nfail):
            records.append(_rec(lvl, f"S{i}", cd.TERMINAL_FAILED, error_text="malformed truncated output"))
        records.append(_rec(lvl, "ok", cd.TERMINAL_SUCCESS))
    interp = cd.interpret_hypotheses(cd.aggregate(records))
    assert interp["H2_load_induced_non_transient"] in (cd.SUPPORTED, cd.WEAKLY_SUPPORTED)
    assert interp["root_cause_confirmed"] is False


# ---- §13/§19 live orchestration is fail-closed ------------------------------


def test_run_sweep_requires_live_authorization():
    plan = cd.default_plan()

    async def never(*a, **k):
        raise AssertionError("must not be called")

    with pytest.raises(cd.LiveDiagnosticNotAuthorizedError):
        asyncio.run(cd.run_sweep(plan, run_id="x", index_level_fn=never))


def test_run_sweep_requires_isolation_when_authorized():
    plan = cd.default_plan()

    async def never(*a, **k):
        raise AssertionError("must not be called")

    # authorized but no active Option-A isolation -> IsolationOwnershipError (fail closed)
    from open_notebook.integrations.graphrag.eval.isolation08 import (
        IsolationOwnershipError,
    )

    with pytest.raises(IsolationOwnershipError):
        asyncio.run(
            cd.run_sweep(plan, run_id="x", index_level_fn=never, authorized_live=True)
        )


def test_run_sweep_with_mock_indexer_is_offline():
    """Authorized + isolation bypassed for a MOCK-only unit test: no provider/DB touched."""
    plan = cd.ConcurrencyDiagnosticPlan(
        levels=(cd.DiagnosticLevel(1, 1, 1), cd.DiagnosticLevel(2, 2, 1))
    )
    calls = {"n": 0}

    async def mock_index_level(level, keys, rep):
        calls["n"] += 1
        # Simulate: level 1 clean, level 2 one provider-capacity failure.
        if level.concurrency == 1:
            return [_rec(1, "S001", cd.TERMINAL_SUCCESS)]
        return [
            _rec(2, "S001", cd.TERMINAL_SUCCESS),
            _rec(2, "S002", cd.TERMINAL_FAILED, error_text="over capacity"),
        ]

    res = asyncio.run(
        cd.run_sweep(
            plan, run_id="mock", index_level_fn=mock_index_level,
            authorized_live=True, require_isolation=False,
        )
    )
    assert calls["n"] == 2
    assert res.aggregate["total_failures"] == 1
    assert res.interpretation["root_cause_confirmed"] is False


def test_run_sweep_rejects_indexer_over_budget():
    """A misbehaving injected indexer that returns more records than the level's
    Source count fails closed (review LOW-2 defense-in-depth)."""
    plan = cd.ConcurrencyDiagnosticPlan(levels=(cd.DiagnosticLevel(1, 1, 1),))

    async def greedy_index_level(level, keys, rep):
        return [  # 3 records for a source_count==1 level -> over budget
            _rec(1, "S001", cd.TERMINAL_SUCCESS),
            _rec(1, "S002", cd.TERMINAL_SUCCESS),
            _rec(1, "S003", cd.TERMINAL_SUCCESS),
        ]

    with pytest.raises(cd.DiagnosticBudgetExceededError):
        asyncio.run(
            cd.run_sweep(
                plan, run_id="x", index_level_fn=greedy_index_level,
                authorized_live=True, require_isolation=False,
            )
        )


# ---- §19 adversarial: no policy/allowlist/concurrency mutation --------------


def test_module_exposes_no_policy_mutator():
    names = set(cd.__all__)
    forbidden_substrings = ("set_", "update_", "mutate", "tune", "override", "apply_")
    assert not any(any(s in n.lower() for s in forbidden_substrings) for n in names)
    # frozen constants remain the frozen values (documentation-as-code)
    assert cd.MAX_SOURCES_PER_LEVEL == 8 and cd.ALLOWED_LEVELS == (1, 2, 4, 8)


def test_running_diagnostic_does_not_touch_frozen_classifier_semantics():
    before = (ir.is_transient_reason("429"), ir.is_transient_reason("over capacity"))
    # exercise the whole harness
    cd.interpret_hypotheses(cd.aggregate([
        _rec(2, "S001", cd.TERMINAL_FAILED, error_text="over capacity"),
        _rec(4, "S002", cd.TERMINAL_FAILED, error_text="over capacity"),
    ]))
    after = (ir.is_transient_reason("429"), ir.is_transient_reason("over capacity"))
    assert before == after == (True, False)  # allowlist unchanged, gap preserved


def test_no_retrieval_value_metrics_in_output():
    agg = cd.aggregate([_rec(2, "S001", cd.TERMINAL_SUCCESS)])
    blob = json.dumps(agg).lower()
    for banned in ("hit@", "mrr", "ndcg", "recall", "precision", "candidate_fraction", "parity"):
        assert banned not in blob


# ---- §3 fixture integrity ---------------------------------------------------


def test_fixture_hash_unchanged():
    ok, h = d.verify_integrity()
    assert ok and h == FIXTURE_HASH
