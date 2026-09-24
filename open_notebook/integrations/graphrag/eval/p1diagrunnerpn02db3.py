"""PN02D-B3B — live fact-recall observability EXECUTION ADAPTER (OBSERVABILITY ONLY).

This is the B3-specific surface that turns the per-(query, arm) inputs captured live
during a B2-style run (via the OPTIONAL ``B2QAStage.execution_observer`` hook) into the
content-safe diagnostics of the CHECKPOINTED ``p1diagpn02db3`` module. It exists so a
FUTURE, separately-authorized B3 observational execution can capture what the closed B2
artifact never retained (actual member-filtered ``evidence_source_ids`` + the existing
``AnswerGrade`` per (query, arm)); the post-hoc application to the closed artifact was
correctly BLOCKED (B3A).

Dependency direction (task B3B §47): this ADAPTER imports the B3 diagnostic and the B2
live entrypoint; the shared B2 core (``qastagepn02db2``/``realseamsb2pn02d``) does NOT
import this module or ``p1diagpn02db3`` — the stage takes only a generic callback.

Guarantees:
  * Reuses the EXISTING grader (``metricspn02.grade_answer``) and the CHECKPOINTED
    ``p1diagpn02db3.diagnose_query_arm`` — NO parallel P1 grader, NO duplicated
    provenance/R0/G0/OK/mismatch/aggregate logic (task B3B §9/§26).
  * OBSERVABILITY_ONLY: computes no scientific verdict, changes no B2 metric, creates no
    alternate P1, persists no raw answer text / source body (task B3B §19/§20/§21/§29).
  * A DISTINCT observation run id (never the frozen B2 scientific run id) and a DISTINCT
    ``report_kind`` (task B3B §16/§17).
  * FAIL-CLOSED: if any captured record cannot be graded/diagnosed, artifact construction
    raises ``B3ObservabilityError`` — a B3 run must not emit an apparently-complete artifact
    from a failed capture (task B3B §13/§45).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Mapping, Optional, Sequence, Tuple

from open_notebook.integrations.graphrag.eval import realseamsb2pn02d as _realseams
from open_notebook.integrations.graphrag.eval.authmintlivepn02d import (
    EXPECTED_FIXTURE_HASH,
    OperatorRunGrant,
    mint_live_b3_provider_run_authorization,
)
from open_notebook.integrations.graphrag.eval.datasetpn02 import (
    FixturePN02,
    QueryPN02,
    load_fixture,
)
from open_notebook.integrations.graphrag.eval.driver_live_pn02d import (
    GitBaselineAttestation,
)
from open_notebook.integrations.graphrag.eval.driverpn02d import B1RunOutcome
from open_notebook.integrations.graphrag.eval.metricspn02 import (
    AnswerGrade,
    grade_answer,
)
from open_notebook.integrations.graphrag.eval.p1diagpn02db3 import (
    P1QueryArmDiagnostic,
    aggregate_p1_diagnostics,
    diagnose_query_arm,
    project_p1_diagnostics,
)
from open_notebook.integrations.graphrag.eval.qastagepn02db2 import B2QAExecutionRecord
from open_notebook.integrations.graphrag.eval.qavaluepn02db3 import (
    build_qa_value_projection,
)
from open_notebook.integrations.graphrag.eval.schemaspn02 import ArmId

#: The frozen CLOSED B2 scientific run id (PN02A). A B3 observational run must NEVER reuse
#: it as its own identity; it is retained ONLY as a content-safe reference (task B3B §16).
REFERENCE_B2_RUN_ID = "pn02db2-6469b191-db13-4d2f-864a-4079578efcf4"

#: 24 queries × 3 arms.
EXPECTED_QUERY_ARM_PAIRS = 72

B3_REPORT_KIND = "PN02DB3_FACT_RECALL_OBSERVABILITY"


class B3ObservabilityError(RuntimeError):
    """Raised when B3 observability capture/diagnosis fails — the B3 run FAILS CLOSED
    rather than emitting an apparently-complete diagnostic artifact (task B3B §13/§45)."""


@dataclass
class B3ObservabilityCollector:
    """An ``execution_observer`` that collects one execution record per completed
    (query, arm). Enforces exactly-once (task B3B §24/§42): a duplicate (query_id, arm)
    is a wiring defect and raises. Installed via ``qa_execution_observer=collector``."""

    records: List[B2QAExecutionRecord] = field(default_factory=list)
    _seen: set = field(default_factory=set, init=False)

    def __call__(self, record: B2QAExecutionRecord) -> None:
        key = (record.query_id, record.arm)
        if key in self._seen:
            raise B3ObservabilityError(
                f"duplicate B3 execution record for {record.query_id}/{record.arm.value}"
            )
        self._seen.add(key)
        self.records.append(record)


@dataclass(frozen=True)
class _GradedB3Record:
    """One captured record graded EXACTLY ONCE by the frozen grader (task B3U §5/§6/§53).

    Carries the canonical ``AnswerGrade`` so BOTH the existing P1 fact-recall diagnostic and
    the additive QA-value projection are derived from the SAME grade — grade once, project
    twice, no regrade."""

    record: B2QAExecutionRecord
    query: QueryPN02
    grade: AnswerGrade


def _grade_b3_records(
    fx: FixturePN02, records: Sequence[B2QAExecutionRecord]
) -> List[_GradedB3Record]:
    """SINGLE grading pass: grade each captured record ONCE with the EXISTING grader
    (``metricspn02.grade_answer``). FAILS CLOSED if a record references an unknown query or
    cannot be graded. This is the only place ``grade_answer`` is called per (query, arm)."""
    graded: List[_GradedB3Record] = []
    for rec in records:
        try:
            query = fx.query(rec.query_id)
            members = fx.members_of(rec.notebook_id)
            grade = grade_answer(rec.result, query, members)  # EXISTING grader; graded ONCE
        except Exception as exc:  # noqa: BLE001 - fail closed with a content-safe message
            raise B3ObservabilityError(
                f"B3 grading failed for {rec.query_id}/{rec.arm.value}: {type(exc).__name__}"
            ) from exc
        graded.append(_GradedB3Record(record=rec, query=query, grade=grade))
    return graded


def _diagnostics_from_graded(
    fx: FixturePN02, graded: Sequence[_GradedB3Record]
) -> List[P1QueryArmDiagnostic]:
    """Transform already-graded records through the CHECKPOINTED P1 diagnostic (no regrade)."""
    diagnostics: List[P1QueryArmDiagnostic] = []
    for g in graded:
        try:
            diag = diagnose_query_arm(
                fx=fx,
                query=g.query,
                arm=g.record.arm,
                evidence_source_ids=g.record.evidence_source_ids,
                grade=g.grade,
                result=g.record.result,
            )
        except Exception as exc:  # noqa: BLE001 - fail closed with a content-safe message
            raise B3ObservabilityError(
                f"B3 diagnostic failed for {g.record.query_id}/{g.record.arm.value}: "
                f"{type(exc).__name__}"
            ) from exc
        diagnostics.append(diag)
    return diagnostics


def build_b3_diagnostics(
    fx: FixturePN02, records: Sequence[B2QAExecutionRecord]
) -> List[P1QueryArmDiagnostic]:
    """Grade each captured record with the EXISTING grader and transform through the
    CHECKPOINTED diagnostic. FAILS CLOSED (raises ``B3ObservabilityError``) if a record
    references an unknown query or cannot be graded/diagnosed. Output is byte-identical to
    the pre-B3U behaviour (one grade per record)."""
    return _diagnostics_from_graded(fx, _grade_b3_records(fx, records))


def build_b3_observability_artifact(
    fx: FixturePN02,
    records: Sequence[B2QAExecutionRecord],
    *,
    observation_run_id: str,
    reference_b2_run_id: str = REFERENCE_B2_RUN_ID,
    expected_pair_count: int = EXPECTED_QUERY_ARM_PAIRS,
    isolation_evidenced: Optional[bool] = None,
) -> Dict[str, object]:
    """Build the content-safe, DISTINCT B3 observability artifact from captured records.

    The observation run id must be distinct from the referenced B2 scientific run id
    (task B3B §16). Completeness is reported explicitly and honestly (task B3B §25): a
    partial run yields ``completeness=PARTIAL`` with the true completed count — never a
    fabricated complete set. All diagnostics come from the checkpointed projection.

    PN02D-B3U (GRADE ONCE / PROJECT TWICE): each record is graded ONCE; the canonical
    ``AnswerGrade`` feeds BOTH the existing P1 fact-recall projection and, when
    ``isolation_evidenced`` is supplied, the ADDITIVE ``qa_value_observability`` subsection
    (end-to-end P1/P2/P3/S1/S2/S3 via the frozen aggregator + decision). When
    ``isolation_evidenced`` is None the artifact is byte-identical to the pre-B3U shape
    (backward compatible — no qa_value_observability key)."""
    if not observation_run_id:
        raise B3ObservabilityError("observation_run_id required")
    # M1 (PN02DB3D-ER1-M1): the security invariant is enforced against the IMMUTABLE
    # frozen constant ``REFERENCE_B2_RUN_ID``, NEVER the caller-supplied
    # ``reference_b2_run_id`` metadata — otherwise a caller could disable the guard by
    # overriding the reference and smuggle the real closed B2 run id in as the
    # observation id. The reference metadata is itself pinned to the frozen constant
    # (fail-closed on any override) so the artifact field stays exactly the frozen value.
    if reference_b2_run_id != REFERENCE_B2_RUN_ID:
        raise B3ObservabilityError(
            "reference_b2_run_id must be the frozen closed B2 scientific run id "
            "(caller override of the security reference is refused)"
        )
    if observation_run_id == REFERENCE_B2_RUN_ID:
        raise B3ObservabilityError(
            "B3 observation run id must NOT reuse the frozen B2 scientific run id"
        )
    graded = _grade_b3_records(fx, records)  # SINGLE grading pass — grade once
    diagnostics = _diagnostics_from_graded(fx, graded)  # fail-closed; no regrade
    aggregate = aggregate_p1_diagnostics(diagnostics)
    completed = len(diagnostics)
    artifact: Dict[str, object] = {
        "report_kind": B3_REPORT_KIND,
        "mode": "OBSERVABILITY_ONLY",
        "observation_run_id": observation_run_id,
        "reference_b2_run_id": reference_b2_run_id,
        "expected_pair_count": expected_pair_count,
        "completed_diagnostic_pair_count": completed,
        "completeness": "COMPLETE" if completed == expected_pair_count else "PARTIAL",
        # Content-safe projection from the checkpointed diagnostic (ids/tokens/counts only).
        "diagnostics": project_p1_diagnostics(diagnostics, aggregate),
    }
    # PN02D-B3U: ADDITIVE end-to-end QA-value subsection from the SAME canonical grades.
    # Only when isolation is known (a live run always supplies it); omitted otherwise so the
    # pre-B3U artifact shape stays byte-identical for existing callers/tests.
    if isolation_evidenced is not None:
        artifact["qa_value_observability"] = build_qa_value_projection(
            [(g.query, g.grade) for g in graded],
            isolation_evidenced=isolation_evidenced,
            expected_pair_count=expected_pair_count,
        )
    return artifact


def _extract_isolation_evidenced(report: object) -> Optional[bool]:
    """Recursively read the frozen Stage-1 ``isolation_evidenced`` verdict from the run report.

    Returns True/False for "YES"/"NO" (or a bool), else None (unknown → QA-value subsection is
    omitted rather than guessed). OBSERVABILITY-ONLY: reads the existing verdict, never recomputes."""
    if isinstance(report, dict):
        if "isolation_evidenced" in report:
            value = report["isolation_evidenced"]
            if isinstance(value, bool):
                return value
            if isinstance(value, str):
                if value.upper() == "YES":
                    return True
                if value.upper() == "NO":
                    return False
        for nested in report.values():
            found = _extract_isolation_evidenced(nested)
            if found is not None:
                return found
    elif isinstance(report, list):
        for nested in report:
            found = _extract_isolation_evidenced(nested)
            if found is not None:
                return found
    return None


async def run_live_b3_observability_execution(
    *,
    operator_grant: OperatorRunGrant,
    git_baseline_attestation: GitBaselineAttestation,
    observed_fixture_hash: str = EXPECTED_FIXTURE_HASH,
    env: Optional[Mapping[str, str]] = None,
    reference_b2_run_id: str = REFERENCE_B2_RUN_ID,
    expected_pair_count: int = EXPECTED_QUERY_ARM_PAIRS,
    fx: Optional[FixturePN02] = None,
    treatment_materialization: bool = False,
) -> Tuple[B1RunOutcome, Dict[str, object]]:
    """CANONICAL governed B3 OBSERVABILITY execution (PN02D-B3D). OBSERVABILITY_ONLY.

    Reuses the EXISTING real B2 scientific engine (``realseamsb2pn02d.run_live_b2_execution`` —
    same two-boot / retrieval / generation / grading / budgets / provider accounting) with just
    two additions: (1) the distinct **B3B mint** (``mint_live_b3_provider_run_authorization``),
    so provider authorization gates on the B3B checkpoint at exact HEAD and fails closed
    otherwise — the B2 mint is NOT used and NO second scientific pipeline is created; and (2) the
    ``B3ObservabilityCollector`` injected as ``qa_execution_observer`` to capture per-(query,arm)
    evidence + result during the run. After the run it builds the distinct, content-safe
    ``PN02DB3_FACT_RECALL_OBSERVABILITY`` artifact from the checkpointed diagnostic and attaches it
    additively to ``outcome.report['b3_observability']`` (never rewriting the B2 report shape).

    Fail-closed: if the B3B mint refuses (tag absent / not at HEAD / ancestor / wrong peel / no
    operator grant) ``run_live_b2_execution`` raises before any diagnostics; and
    ``build_b3_observability_artifact`` raises ``B3ObservabilityError`` on any capture/diagnosis
    failure or if ``operator_grant.run_id`` reuses the frozen B2 scientific run id. A partial run
    yields ``completeness=PARTIAL`` with the true completed count. Returns ``(outcome, artifact)``.
    """
    # M1 (PN02DB3D-ER1-M1): reject B2-run-id reuse and any caller override of the
    # security reference BEFORE provider binding / live mint / scientific execution —
    # never only after the run. Both checks use the IMMUTABLE frozen constant.
    if reference_b2_run_id != REFERENCE_B2_RUN_ID:
        raise B3ObservabilityError(
            "reference_b2_run_id must be the frozen closed B2 scientific run id "
            "(caller override of the security reference is refused)"
        )
    if operator_grant.run_id == REFERENCE_B2_RUN_ID:
        raise B3ObservabilityError(
            "B3 observation run id must NOT reuse the frozen B2 scientific run id "
            "(rejected before provider binding)"
        )
    collector = B3ObservabilityCollector()
    outcome = await _realseams.run_live_b2_execution(
        operator_grant=operator_grant,
        git_baseline_attestation=git_baseline_attestation,
        observed_fixture_hash=observed_fixture_hash,
        env=env,
        qa_execution_observer=collector,
        mint_fn=mint_live_b3_provider_run_authorization,
        # PN02D-B3G: the B3 observational run consumes its grant under the "B3B" profile via the
        # SHARED RealB1Driver one-shot claim (reused through run_live_b2_execution) — this wrapper
        # adds NO second claim, so a real B3 attempt consumes the grant exactly once.
        execution_kind="B3B",
        # PN02D-B3P-R1 (M1): EXPLICIT-ONLY generation-evidence-materialization selection (default
        # False = CONTROL). When True, run_live_b2_execution threads the post-provision runtime
        # materializer factory to the driver — no code edit needed by a future authorized turn.
        treatment_materialization=treatment_materialization,
    )
    fixture = fx if fx is not None else load_fixture()
    artifact = build_b3_observability_artifact(
        fixture,
        collector.records,
        observation_run_id=operator_grant.run_id,  # DISTINCT observation id; B2 run id rejected
        reference_b2_run_id=reference_b2_run_id,
        expected_pair_count=expected_pair_count,
        # PN02D-B3U: the isolation gate is decided by the frozen Stage-1 evaluator inside the
        # SAME run; thread its verdict so the additive QA-value subsection reuses the frozen
        # qa_decision (Q0 gates on isolation). Never recomputed here.
        isolation_evidenced=_extract_isolation_evidenced(outcome.report),
    )
    if isinstance(outcome.report, dict):
        # Additive ONLY — the distinct B3 observability block never rewrites B2 report fields.
        outcome.report["b3_observability"] = artifact
    return outcome, artifact


def observed_arm_coverage(records: Sequence[B2QAExecutionRecord]) -> Tuple[ArmId, ...]:
    """Distinct arms observed (for wiring verification). Order-stable by ARM enum order."""
    seen = {rec.arm for rec in records}
    return tuple(a for a in (ArmId.QA_V, ArmId.QA_GD, ArmId.QA_VGD) if a in seen)


__all__ = [
    "REFERENCE_B2_RUN_ID",
    "EXPECTED_QUERY_ARM_PAIRS",
    "B3_REPORT_KIND",
    "B3ObservabilityError",
    "B3ObservabilityCollector",
    "build_b3_diagnostics",
    "build_b3_observability_artifact",
    "run_live_b3_observability_execution",
    "observed_arm_coverage",
]

# NOTE (PN02D-B3U): the end-to-end QA-value projection lives in ``qavaluepn02db3`` and is
# attached additively by ``build_b3_observability_artifact``; this adapter grades ONCE
# (``_grade_b3_records``) and feeds the same ``AnswerGrade`` to both the P1 diagnostic and
# the QA-value projection — grade once, project twice.
