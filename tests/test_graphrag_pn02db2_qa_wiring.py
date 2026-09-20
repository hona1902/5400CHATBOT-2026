"""PN02D-B2 live QA wiring — provider-free tests (operator Option A, additive seam).

Proves (a) B1 is byte-identical when no QA seam is injected (the mandatory
regression guard) and (b) an injected QA stage's ``qa_results`` reach the EXISTING
evaluator and produce a QA verdict (the blocker-closing injection test), plus the
QA stage arm-evidence, the hard 72 final-answer cap, and the real final-answer
seam's parse / no-retry / safe-error / abstention / citation-preservation behaviour.

ZERO provider traffic: the final-answer seam and the QA stage are driven with
injected fakes; the driver uses the clean fake topology.
"""

from __future__ import annotations

import asyncio
from typing import List

import pytest

from open_notebook.integrations.graphrag.eval.budgetlivepn02d import (
    BudgetClass,
    StatefulBudgetGuard,
    WorkloadCapExceeded,
    b1_caps,
    b2_caps,
)
from open_notebook.integrations.graphrag.eval.datasetpn02 import (
    NOTEBOOK_IDS,
    load_fixture,
)
from open_notebook.integrations.graphrag.eval.driverpn02d import (
    B1DriverDeps,
    B1OfflineDriver,
    build_simulation_provider_run_authorization,
)
from open_notebook.integrations.graphrag.eval.fakeslivepn02d import (
    build_clean_fake_topology,
)
from open_notebook.integrations.graphrag.eval.normalizepn02 import (
    normalize_graph,
    normalize_vector,
)
from open_notebook.integrations.graphrag.eval.qastagepn02db2 import (
    ARM_ORDER,
    B2QAStage,
)
from open_notebook.integrations.graphrag.eval.real_final_answer_seam_pn02d import (
    FinalAnswerProviderError,
    RealFinalAnswerSeam,
    parse_final_answer,
)
from open_notebook.integrations.graphrag.eval.routelivepn02d import (
    PN02Router,
    build_route_table,
)
from open_notebook.integrations.graphrag.eval.schemaspn02 import (
    ArmId,
    GDEvidenceResult,
    QAAnswerResult,
    TechnicalOutcome,
    VectorEvidenceResult,
)

# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #

def _deps(fx, topology, qa_stage_seam=None) -> B1DriverDeps:
    return B1DriverDeps(
        index_client_factory=topology.index_client_factory(),
        gd_backend_factory=topology.gd_backend_factory(),
        vector_backend_factory=topology.vector_backend_factory(),
        delete_backend_factory=topology.delete_backend_factory(),
        present_secret_envs=frozenset({"OPENROUTER_API_KEY"}),
        qa_stage_seam=qa_stage_seam,
    )


async def _run(fx, deps, *, run_id: str):
    driver = B1OfflineDriver(fx, deps)
    auth = build_simulation_provider_run_authorization(run_id=run_id)
    return await driver.run(run_id=run_id, provider_run_auth=auth)


class _GroundTruthFakeStage:
    """Injectable fake QA stage: answers straight from fixture ground truth so the
    EXISTING evaluator has gradeable QA results for all 3 arms. Records call count."""

    def __init__(self) -> None:
        self.calls = 0

    async def __call__(self, *, fx, ordered_queries, vector_results, gd_results):
        self.calls += 1
        out: List[QAAnswerResult] = []
        for q in ordered_queries:
            for arm in ARM_ORDER:
                out.append(
                    QAAnswerResult(
                        query_id=q.query_id,
                        notebook_id=q.notebook_id,
                        arm=arm,
                        abstained=q.is_negative,
                        citation_source_ids=tuple(q.required_citation_source_ids),
                        emitted_answer_facts=tuple(q.expected_answer_facts),
                    )
                )
        return out


# --------------------------------------------------------------------------- #
# B1 regression guard — seam absent → byte-identical (qa_results None)
# --------------------------------------------------------------------------- #

def test_b1_driver_deps_default_has_no_qa_stage():
    fx = load_fixture()
    topology = build_clean_fake_topology(fx, PN02Router(build_route_table(fx)))
    deps = _deps(fx, topology)
    assert deps.qa_stage_seam is None


def test_b1_default_leaves_qa_not_evaluated():
    fx = load_fixture()
    topology = build_clean_fake_topology(fx, PN02Router(build_route_table(fx)))
    outcome = asyncio.run(_run(fx, _deps(fx, topology), run_id="pn02db2-b1regress"))
    assert outcome.state == "COMPLETE"
    sci = outcome.evaluation.scientific
    # B1 excludes QA: with no seam, the evaluator sees qa_results=None → NOT_EVALUATED.
    assert sci.per_notebook_graph_qa_value_evidenced.value == "NOT_EVALUATED"
    assert "qa_not_supplied" in sci.notes


def test_b1_final_answer_cap_remains_zero():
    assert b1_caps()[BudgetClass.FINAL_ANSWER] == 0


# --------------------------------------------------------------------------- #
# B2 injection — qa_results reach the EXISTING evaluator (closes the blocker)
# --------------------------------------------------------------------------- #

def test_b2_injection_qa_results_reach_existing_evaluator():
    fx = load_fixture()
    topology = build_clean_fake_topology(fx, PN02Router(build_route_table(fx)))
    stage = _GroundTruthFakeStage()
    outcome = asyncio.run(_run(fx, _deps(fx, topology, stage), run_id="pn02db2-inject"))
    assert outcome.state == "COMPLETE"
    assert stage.calls == 1  # the seam was invoked exactly once by the orchestrator
    sci = outcome.evaluation.scientific
    # The existing evaluator computed a QA verdict from the injected qa_results —
    # it is NO LONGER NOT_EVALUATED, and qa_not_supplied is gone.
    assert sci.per_notebook_graph_qa_value_evidenced.value in {
        "YES",
        "NO",
        "INCONCLUSIVE",
    }
    assert "qa_not_supplied" not in sci.notes


# --------------------------------------------------------------------------- #
# QA stage arm-evidence construction + hard member prefilter
# --------------------------------------------------------------------------- #

def test_qa_stage_arm_evidence_and_member_prefilter():
    fx = load_fixture()
    nb = NOTEBOOK_IDS[0]
    members = sorted(fx.members_of(nb))
    assert len(members) >= 2
    # a valid Source key that is NOT a member of this notebook (from another nb)
    non_member = next(
        s for s in fx.source_keys if s not in fx.members_of(nb)
    )
    m0, m1 = members[0], members[1]
    q = fx.queries_for_notebook(nb)[0]

    vector_results = {
        q.query_id: VectorEvidenceResult(
            query_id=q.query_id,
            notebook_id=nb,
            evidence=normalize_vector([m0, m1, non_member], allowlist=fx.source_keys),
        )
    }
    gd_results = {
        q.query_id: GDEvidenceResult(
            query_id=q.query_id,
            notebook_id=nb,
            evidence=normalize_graph([m1, non_member], allowlist=fx.source_keys),
        )
    }
    stage = B2QAStage(answer_seam=_UnusedSeam())
    plan = stage.plan(
        fx=fx, ordered_queries=[q], vector_results=vector_results, gd_results=gd_results
    )
    by_arm = {item.arm: item.evidence_source_ids for item in plan}

    # non-member never appears in any arm's evidence
    for arm in ARM_ORDER:
        assert non_member not in by_arm[arm]
    # QA-V = member V(K=5) slice, rank-preserving
    assert by_arm[ArmId.QA_V] == (m0, m1)
    # QA-GD = member GD set in canonical-id order
    assert by_arm[ArmId.QA_GD] == tuple(sorted({m1}))
    # QA-V+GD = frozen combine (members only, V-first)
    assert set(by_arm[ArmId.QA_VGD]) <= set(members)
    assert m0 in by_arm[ArmId.QA_VGD] and m1 in by_arm[ArmId.QA_VGD]


class _UnusedSeam:
    async def answer(self, notebook_id, question, evidence_source_ids):  # pragma: no cover
        raise AssertionError("answer seam must not be called during planning")


# --------------------------------------------------------------------------- #
# Hard 72 final-answer cap (fail-closed) + B1/B2 cap separation
# --------------------------------------------------------------------------- #

def test_b2_final_answer_cap_is_72_and_fails_closed_on_73rd():
    guard = StatefulBudgetGuard(caps=b2_caps())
    assert guard.cap(BudgetClass.FINAL_ANSWER) == 72
    for _ in range(72):
        guard.reserve(BudgetClass.FINAL_ANSWER)
    assert guard.spent(BudgetClass.FINAL_ANSWER) == 72
    with pytest.raises(WorkloadCapExceeded):
        guard.reserve(BudgetClass.FINAL_ANSWER)  # 73rd is impossible


def test_b2_caps_leave_b1_final_answer_zero():
    assert b1_caps()[BudgetClass.FINAL_ANSWER] == 0
    assert b2_caps()[BudgetClass.FINAL_ANSWER] == 72
    # judge stays hard 0 in both
    assert b2_caps()[BudgetClass.JUDGE_MODEL] == 0


def test_qa_stage_makes_exactly_72_calls_over_the_fixture():
    fx = load_fixture()

    class _CountingSeam:
        def __init__(self) -> None:
            self.calls = 0

        async def answer(self, notebook_id, question, evidence_source_ids):
            self.calls += 1
            return QAAnswerResult(
                query_id="", notebook_id=notebook_id, arm=ArmId.QA_V, abstained=False
            )

    seam = _CountingSeam()
    stage = B2QAStage(answer_seam=seam)
    ordered = sorted(fx.queries, key=lambda q: q.query_id)
    vres = {
        q.query_id: VectorEvidenceResult(
            query_id=q.query_id,
            notebook_id=q.notebook_id,
            evidence=normalize_vector(
                sorted(fx.members_of(q.notebook_id)), allowlist=fx.source_keys
            ),
        )
        for q in ordered
    }
    gres = {
        q.query_id: GDEvidenceResult(
            query_id=q.query_id,
            notebook_id=q.notebook_id,
            evidence=normalize_graph(
                sorted(fx.members_of(q.notebook_id)), allowlist=fx.source_keys
            ),
        )
        for q in ordered
    }
    results = asyncio.run(
        stage(fx=fx, ordered_queries=ordered, vector_results=vres, gd_results=gres)
    )
    assert len(ordered) == 24
    assert seam.calls == 72  # 24 queries × 3 arms
    assert len(results) == 72


# --------------------------------------------------------------------------- #
# Real final-answer seam — parse / no-retry / safe error / abstain / citations
# --------------------------------------------------------------------------- #

def test_parse_final_answer_preserves_invalid_citations_verbatim():
    completion = (
        "The answer is 42.\n"
        "CITATIONS: SRC_A, NOT_A_MEMBER, SRC_B\n"
        "ABSTAIN: NO\n"
    )
    text, cites, abstained = parse_final_answer(completion)
    assert text == "The answer is 42."
    # invalid id NOT_A_MEMBER is preserved verbatim (evaluator S2 owns validity)
    assert cites == ("SRC_A", "NOT_A_MEMBER", "SRC_B")
    assert abstained is False


def test_parse_final_answer_abstention():
    _text, cites, abstained = parse_final_answer("I cannot answer.\nCITATIONS:\nABSTAIN: YES\n")
    assert abstained is True
    assert cites == ()


def test_real_seam_calls_completion_exactly_once_no_retry():
    calls = {"n": 0}

    async def _fake_completion(prompt: str) -> str:
        calls["n"] += 1
        return "ok\nCITATIONS: SRC_A\nABSTAIN: NO\n"

    seam = RealFinalAnswerSeam(completion_fn=_fake_completion)
    res = asyncio.run(seam.answer("NB_A", "q?", ["SRC_A"]))
    assert calls["n"] == 1
    assert res.citation_source_ids == ("SRC_A",)
    assert res.outcome == TechnicalOutcome.COMPLETED


def test_real_seam_provider_error_is_content_safe_and_not_retried():
    calls = {"n": 0}

    class _FakeHTTPError(Exception):
        def __init__(self):
            super().__init__("boom sk-secret-should-never-surface")
            self.status_code = 401

    async def _failing_completion(prompt: str) -> str:
        calls["n"] += 1
        raise _FakeHTTPError()

    seam = RealFinalAnswerSeam(completion_fn=_failing_completion)
    with pytest.raises(FinalAnswerProviderError) as ei:
        asyncio.run(seam.answer("NB_A", "q?", ["SRC_A"]))
    assert calls["n"] == 1  # no retry
    diag = ei.value.diagnostic
    assert diag.provider_http_status == 401
    assert diag.provider_error_class == "authentication_error"
    # content-safe: the raw secret-bearing message never appears in the error string
    assert "sk-secret" not in str(ei.value)


# --------------------------------------------------------------------------- #
# QA stage computes no scientific result (structural guard)
# --------------------------------------------------------------------------- #

def test_qa_stage_has_no_scientific_methods():
    stage = B2QAStage(answer_seam=_UnusedSeam())
    for forbidden in ("grade", "grade_answer", "decide", "decide_qa_value", "verdict", "leakage"):
        assert not hasattr(stage, forbidden)
