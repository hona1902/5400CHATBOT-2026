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
    load_fixture,
)
from open_notebook.integrations.graphrag.eval.driver_live_pn02d import (
    GitBaselineAttestation,
)
from open_notebook.integrations.graphrag.eval.driverpn02d import B1RunOutcome
from open_notebook.integrations.graphrag.eval.metricspn02 import grade_answer
from open_notebook.integrations.graphrag.eval.p1diagpn02db3 import (
    P1QueryArmDiagnostic,
    aggregate_p1_diagnostics,
    diagnose_query_arm,
    project_p1_diagnostics,
)
from open_notebook.integrations.graphrag.eval.qastagepn02db2 import B2QAExecutionRecord
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


def build_b3_diagnostics(
    fx: FixturePN02, records: Sequence[B2QAExecutionRecord]
) -> List[P1QueryArmDiagnostic]:
    """Grade each captured record with the EXISTING grader and transform through the
    CHECKPOINTED diagnostic. FAILS CLOSED (raises ``B3ObservabilityError``) if a record
    references an unknown query or cannot be graded/diagnosed."""
    diagnostics: List[P1QueryArmDiagnostic] = []
    for rec in records:
        try:
            query = fx.query(rec.query_id)
            members = fx.members_of(rec.notebook_id)
            grade = grade_answer(rec.result, query, members)  # EXISTING grader; no regrade logic
            diag = diagnose_query_arm(
                fx=fx,
                query=query,
                arm=rec.arm,
                evidence_source_ids=rec.evidence_source_ids,
                grade=grade,
                result=rec.result,
            )
        except Exception as exc:  # noqa: BLE001 - fail closed with a content-safe message
            raise B3ObservabilityError(
                f"B3 diagnostic failed for {rec.query_id}/{rec.arm.value}: {type(exc).__name__}"
            ) from exc
        diagnostics.append(diag)
    return diagnostics


def build_b3_observability_artifact(
    fx: FixturePN02,
    records: Sequence[B2QAExecutionRecord],
    *,
    observation_run_id: str,
    reference_b2_run_id: str = REFERENCE_B2_RUN_ID,
    expected_pair_count: int = EXPECTED_QUERY_ARM_PAIRS,
) -> Dict[str, object]:
    """Build the content-safe, DISTINCT B3 observability artifact from captured records.

    The observation run id must be distinct from the referenced B2 scientific run id
    (task B3B §16). Completeness is reported explicitly and honestly (task B3B §25): a
    partial run yields ``completeness=PARTIAL`` with the true completed count — never a
    fabricated complete set. All diagnostics come from the checkpointed projection."""
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
    diagnostics = build_b3_diagnostics(fx, records)  # fail-closed
    aggregate = aggregate_p1_diagnostics(diagnostics)
    completed = len(diagnostics)
    return {
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
