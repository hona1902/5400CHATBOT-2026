"""GraphRAG-08E.7 burst-reproduction SCREENING plan, budgets, selection, classification.

EVALUATION-ONLY. Nothing in production imports this. Pure data + validation + predicates —
it starts nothing (no provider call, no sidecar, no DB, no network). It implements the
FROZEN GraphRAG-08E.7A design (docs/agribank/development/GRAPHRAG_08E7A_BURST_REPRODUCTION_DESIGN.md);
the live wave/ladder execution lives in ``burst_runner08``.

Design invariants (frozen, enforced by ``validate_burst_plan`` + tests):
  * ladder ``[8,16,24,32,48,75]``, top rung 75, ONE screening repetition;
  * deterministic nested-prefix selection over the attempt-#5 order S001…S075 (S001 first);
  * SUBMIT_ALL_THEN_POLL scheduling (the runner enforces the phase separation);
  * DISTINCT budgets — planned Source workload = 203 (=sum of levels) and index attempts =
    406 (=203 × MAX_INDEX_ATTEMPTS_PER_SOURCE(2)) — mechanically guarded by ``BurstBudgetGuard``;
  * reproduction is SPLIT into a broad classifier-signature flag and a strict S001-event flag,
    with the historical attempt number tracked separately; a signature match is NOT a mechanism
    or root-cause claim.

It changes NO frozen 08E parameter and does NOT widen the 08E ``[1,2,4,8]×2`` diagnostic caps
(those live in ``concurrency_diag08`` and are untouched).
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from open_notebook.integrations.graphrag.eval.concurrency_diag08 import (
    TERMINAL_FAILED,
    TERMINAL_SUCCESS,
    AttemptRecord,
)
from open_notebook.integrations.graphrag.eval.index_retry08 import ReasonCode

# ---------------------------------------------------------------------------
# Frozen 08E.7A design constants (do NOT change without a new approved design).
# ---------------------------------------------------------------------------

#: The exact burst ladder (each level C = one concurrent wave of C Sources into ONE sidecar).
BURST_LEVELS: Tuple[int, ...] = (8, 16, 24, 32, 48, 75)
TOP_RUNG: int = 75
SCREENING_REPETITIONS_PER_LEVEL: int = 1
MAX_INDEX_ATTEMPTS_PER_SOURCE: int = 2
#: DISTINCT budgets — planned unique-Source workload vs actual index attempts (§12/§37 of 08E.7A).
DESIGN_MAX_PLANNED_SOURCE_WORKLOAD: int = 203  # == sum(BURST_LEVELS)
DESIGN_MAX_INDEX_ATTEMPTS_TOTAL: int = 406  # == 203 × MAX_INDEX_ATTEMPTS_PER_SOURCE
BURST_SCHEDULING_MODE: str = "SUBMIT_ALL_THEN_POLL"
ANCHOR_SOURCE_KEY: str = "S001"

#: The reconstructed historical attempt-#5 S001 signature (08E.7A §15), reason code from the
#: committed classifier. Raw text is NEVER used here (only the safe reason code).
HISTORICAL_REASON_CODE: str = ReasonCode.TRACK_TEXT_PRESENT_NO_ALLOWLIST_MATCH
HISTORICAL_S001_KEY: str = "S001"
HISTORICAL_ATTEMPT_NUMBER: int = 1

#: Rung runtime-invalidity marker (a harness/runtime stop is NOT experimental evidence).
RUNG_INVALID_RUNTIME: str = "INVALID_RUNTIME"


class BurstPlanError(ValueError):
    """The requested burst plan is malformed or diverges from the frozen design (fail closed)."""


class BurstBudgetError(RuntimeError):
    """A planned-workload or index-attempt budget bound would be exceeded (fail closed)."""


# ---------------------------------------------------------------------------
# Frozen plan (pure data + validation).
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class BurstLevel:
    """One burst treatment rung. ``level`` == the single-wave Source count for the rung."""

    level: int

    @property
    def source_count(self) -> int:
        return self.level


@dataclass(frozen=True)
class BurstPlan:
    """A frozen, validated burst-reproduction plan. Executing it requires a separate live
    authorization + active Option-A isolation (see ``burst_runner08``)."""

    levels: Tuple[BurstLevel, ...]
    repetitions: int = SCREENING_REPETITIONS_PER_LEVEL
    scheduling_mode: str = BURST_SCHEDULING_MODE
    max_index_attempts_per_source: int = MAX_INDEX_ATTEMPTS_PER_SOURCE

    @property
    def planned_source_workload(self) -> int:
        return sum(lvl.source_count for lvl in self.levels) * self.repetitions

    @property
    def max_index_attempts_total(self) -> int:
        return self.planned_source_workload * self.max_index_attempts_per_source

    def as_dict(self) -> Dict[str, object]:
        return {
            "levels": [lvl.level for lvl in self.levels],
            "top_rung": self.levels[-1].level if self.levels else None,
            "repetitions": self.repetitions,
            "scheduling_mode": self.scheduling_mode,
            "max_index_attempts_per_source": self.max_index_attempts_per_source,
            "planned_source_workload": self.planned_source_workload,
            "max_index_attempts_total": self.max_index_attempts_total,
        }


def default_burst_plan() -> BurstPlan:
    """The single frozen 08E.7A screening plan. Constructed + validated."""
    plan = BurstPlan(levels=tuple(BurstLevel(c) for c in BURST_LEVELS))
    validate_burst_plan(plan)
    return plan


def validate_burst_plan(plan: BurstPlan) -> None:
    """Fail closed unless the plan is EXACTLY the frozen 08E.7A design (task §7)."""
    if not plan.levels:
        raise BurstPlanError("burst plan has no levels")
    levels = [lvl.level for lvl in plan.levels]
    if any(x <= 0 for x in levels):
        raise BurstPlanError("burst levels must be positive")
    if any(x > TOP_RUNG for x in levels):
        raise BurstPlanError(f"burst level above TOP_RUNG {TOP_RUNG}")
    if len(set(levels)) != len(levels):
        raise BurstPlanError("duplicate burst level")
    if levels != sorted(levels):
        raise BurstPlanError("burst levels must be strictly ascending (no reorder)")
    if levels[-1] != TOP_RUNG:
        raise BurstPlanError(f"missing top rung {TOP_RUNG}")
    if tuple(levels) != BURST_LEVELS:
        raise BurstPlanError(f"burst levels {tuple(levels)} != frozen {BURST_LEVELS}")
    if plan.repetitions != SCREENING_REPETITIONS_PER_LEVEL:
        raise BurstPlanError(
            f"repetitions {plan.repetitions} != frozen {SCREENING_REPETITIONS_PER_LEVEL}"
        )
    if plan.scheduling_mode != BURST_SCHEDULING_MODE:
        raise BurstPlanError(
            f"scheduling_mode {plan.scheduling_mode!r} != frozen {BURST_SCHEDULING_MODE!r}"
        )
    if plan.max_index_attempts_per_source != MAX_INDEX_ATTEMPTS_PER_SOURCE:
        raise BurstPlanError(
            f"max_index_attempts_per_source {plan.max_index_attempts_per_source} != "
            f"frozen {MAX_INDEX_ATTEMPTS_PER_SOURCE}"
        )
    if plan.planned_source_workload != DESIGN_MAX_PLANNED_SOURCE_WORKLOAD:
        raise BurstPlanError(
            f"planned_source_workload {plan.planned_source_workload} != frozen "
            f"{DESIGN_MAX_PLANNED_SOURCE_WORKLOAD}"
        )
    if plan.max_index_attempts_total != DESIGN_MAX_INDEX_ATTEMPTS_TOTAL:
        raise BurstPlanError(
            f"max_index_attempts_total {plan.max_index_attempts_total} != frozen "
            f"{DESIGN_MAX_INDEX_ATTEMPTS_TOTAL}"
        )


def budget_precheck() -> Dict[str, int]:
    """Static mathematical bound check (task §29). No provider work. Fails closed on drift."""
    if sum(BURST_LEVELS) != DESIGN_MAX_PLANNED_SOURCE_WORKLOAD:
        raise BurstPlanError("sum(BURST_LEVELS) != 203")
    if DESIGN_MAX_PLANNED_SOURCE_WORKLOAD * MAX_INDEX_ATTEMPTS_PER_SOURCE != (
        DESIGN_MAX_INDEX_ATTEMPTS_TOTAL
    ):
        raise BurstPlanError("203 × 2 != 406")
    return {
        "planned_source_workload": DESIGN_MAX_PLANNED_SOURCE_WORKLOAD,
        "max_index_attempts_total": DESIGN_MAX_INDEX_ATTEMPTS_TOTAL,
        "max_index_attempts_per_source": MAX_INDEX_ATTEMPTS_PER_SOURCE,
    }


# ---------------------------------------------------------------------------
# Deterministic nested-prefix selection (task §8/§9).
# ---------------------------------------------------------------------------


def select_burst_prefix(benchmark, count: int) -> Tuple[str, ...]:
    """The first ``count`` Sources in the frozen attempt-#5 order (S001 first).

    For the frozen fixture the corpus order, the ascending-key order, and the attempt-#5
    submission order coincide, so the nested prefix is ``S001..S00count``. Fails closed on a
    missing anchor or an out-of-range count."""
    if count < 1:
        raise BurstPlanError(f"burst prefix count {count} < 1")
    keys = sorted(s.key for s in benchmark.sources)
    if ANCHOR_SOURCE_KEY not in keys:
        raise BurstPlanError(f"anchor {ANCHOR_SOURCE_KEY} absent from corpus")
    if keys[0] != ANCHOR_SOURCE_KEY:
        raise BurstPlanError(
            f"anchor {ANCHOR_SOURCE_KEY} is not first in the frozen order — refusing"
        )
    if count > len(keys):
        raise BurstPlanError(f"count {count} exceeds corpus size {len(keys)}")
    return tuple(keys[:count])


# ---------------------------------------------------------------------------
# DISTINCT budget guard: planned workload (203) vs index attempts (406) + per-source cap (2).
# ---------------------------------------------------------------------------


class BurstBudgetGuard:
    """Two independent, mechanically-enforced hard caps (task §24-§28).

    ``planned_source_workload_count`` counts each planned unique Source workload entry across
    entered rungs (cap 203). ``actual_index_attempt_count`` counts every index attempt —
    initial AND retry — (cap 406). A per-treatment per-Source cap (2) is enforced separately.
    These are NOT one ambiguous "submission" counter (task §27)."""

    def __init__(
        self,
        *,
        max_planned_workload: int = DESIGN_MAX_PLANNED_SOURCE_WORKLOAD,
        max_index_attempts: int = DESIGN_MAX_INDEX_ATTEMPTS_TOTAL,
        max_attempts_per_source: int = MAX_INDEX_ATTEMPTS_PER_SOURCE,
    ) -> None:
        self._max_planned_workload = max_planned_workload
        self._max_index_attempts = max_index_attempts
        self._max_attempts_per_source = max_attempts_per_source
        self.planned_source_workload_count = 0
        self.actual_index_attempt_count = 0
        self._per_treatment_source: Dict[str, int] = {}

    def reserve_workload(self, n: int) -> None:
        """Reserve ``n`` planned Source workload entries (called at rung entry). Fail closed."""
        if n < 0:
            raise BurstBudgetError("workload reservation must be non-negative")
        if self.planned_source_workload_count + n > self._max_planned_workload:
            raise BurstBudgetError(
                f"planned workload {self.planned_source_workload_count + n} > "
                f"{self._max_planned_workload}"
            )
        self.planned_source_workload_count += n

    def record_attempt(self, treatment_source_key: str) -> int:
        """Record ONE index attempt (initial or retry) for a per-treatment Source key. Fail
        closed on the global 406 cap OR the per-treatment per-Source cap (2). Returns the
        new per-Source attempt number."""
        used = self._per_treatment_source.get(treatment_source_key, 0)
        if used + 1 > self._max_attempts_per_source:
            raise BurstBudgetError(
                f"source {treatment_source_key} attempts {used + 1} > "
                f"{self._max_attempts_per_source}"
            )
        if self.actual_index_attempt_count + 1 > self._max_index_attempts:
            raise BurstBudgetError(
                f"index attempts {self.actual_index_attempt_count + 1} > "
                f"{self._max_index_attempts}"
            )
        self._per_treatment_source[treatment_source_key] = used + 1
        self.actual_index_attempt_count += 1
        return used + 1


# ---------------------------------------------------------------------------
# Reproduction classification predicates (pure; operate on content-safe AttemptRecords).
# ---------------------------------------------------------------------------


def is_classifier_signature(record: AttemptRecord) -> bool:
    """The broad classifier-signature predicate (task §34): a terminal FAILED whose
    content-safe characterization is present-text, non-retryable, TRACK_TEXT_PRESENT_NO_
    ALLOWLIST_MATCH. This is a SIGNATURE match only — NOT a mechanism/root-cause claim, and
    NOT (by itself) the historical S001 event."""
    if record.terminal_status != TERMINAL_FAILED:
        return False
    ch = record.characterization
    return bool(
        ch is not None
        and ch.error_text_present is True
        and ch.retryable is False
        and ch.retry_reason_code == HISTORICAL_REASON_CODE
    )


def is_strict_s001_event(record: AttemptRecord) -> bool:
    """The strict S001-identity + signature predicate (task §35). Attempt number is NOT part
    of this flag — it is tracked separately by ``historical_attempt_number_match``."""
    return (
        record.logical_source_id == HISTORICAL_S001_KEY
        and is_classifier_signature(record)
    )


def historical_attempt_number_match(record: AttemptRecord) -> bool:
    """Whether a strict S001 event reproduced on the historical attempt number (#1). Recorded
    separately (task §35); never folded into the strict flag."""
    return is_strict_s001_event(record) and (
        record.attempt_number == HISTORICAL_ATTEMPT_NUMBER
    )


def is_novel_valid_failure(record: AttemptRecord) -> bool:
    """A legitimate terminal failure (any non-SUCCESS terminal — incl. a retryable-exhausted
    FAILED or a TIMEOUT) that does NOT match the classifier signature (task §37). A
    same-signature failure on any Source is NOT novel."""
    return record.terminal_status != TERMINAL_SUCCESS and not is_classifier_signature(record)


def _latency_summary(durations: List[int]) -> Dict[str, Optional[float]]:
    if not durations:
        return {"count": 0, "min_ms": None, "median_ms": None, "max_ms": None}
    return {
        "count": len(durations),
        "min_ms": float(min(durations)),
        "median_ms": float(statistics.median(durations)),
        "max_ms": float(max(durations)),
    }


# ---------------------------------------------------------------------------
# Content-safe result objects (per rung + overall).
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class BurstRungResult:
    """Content-safe result of ONE burst rung (task §42). Non-exclusive reproduction flags."""

    level: int
    planned_source_count: int
    initial_wave_submitted_count: int
    full_initial_wave_established: bool
    treatment_valid: bool
    index_attempt_count: int
    success_count: int
    failure_count: int
    retry_count: int
    classifier_signature_reproduced: bool
    strict_s001_event_reproduced: bool
    historical_attempt_number_match: bool
    novel_failure_observed: bool
    clean: bool
    first_failure_source_id: Optional[str]
    first_failure_reason_code: Optional[str]
    latency_summary: Dict[str, Optional[float]]
    cleanup_ok: bool
    runtime_stop_reason: Optional[str]
    records: Tuple[AttemptRecord, ...] = field(default_factory=tuple)

    def rung_labels(self) -> Tuple[str, ...]:
        if not self.treatment_valid:
            return (RUNG_INVALID_RUNTIME,)
        labels: List[str] = []
        if self.clean:
            labels.append("CLEAN")
        if self.classifier_signature_reproduced:
            labels.append("CLASSIFIER_SIGNATURE_REPRODUCED")
        if self.strict_s001_event_reproduced:
            labels.append("S001_HISTORICAL_EVENT_REPRODUCED_STRICT")
        if self.novel_failure_observed:
            labels.append("NOVEL_VALID_FAILURE")
        return tuple(labels)

    def as_dict(self) -> Dict[str, object]:
        return {
            "level": self.level,
            "planned_source_count": self.planned_source_count,
            "initial_wave_submitted_count": self.initial_wave_submitted_count,
            "full_initial_wave_established": self.full_initial_wave_established,
            "treatment_valid": self.treatment_valid,
            "index_attempt_count": self.index_attempt_count,
            "success_count": self.success_count,
            "failure_count": self.failure_count,
            "retry_count": self.retry_count,
            "classifier_signature_reproduced": self.classifier_signature_reproduced,
            "strict_s001_event_reproduced": self.strict_s001_event_reproduced,
            "historical_attempt_number_match": self.historical_attempt_number_match,
            "novel_failure_observed": self.novel_failure_observed,
            "clean": self.clean,
            "first_failure_source_id": self.first_failure_source_id,
            "first_failure_reason_code": self.first_failure_reason_code,
            "latency_summary": self.latency_summary,
            "cleanup_ok": self.cleanup_ok,
            "runtime_stop_reason": self.runtime_stop_reason,
            "rung_labels": list(self.rung_labels()),
            "records": [r.as_dict() for r in self.records],
        }


def classify_rung(
    level: int,
    *,
    planned_source_count: int,
    initial_wave_submitted_count: int,
    full_initial_wave_established: bool,
    records: Tuple[AttemptRecord, ...],
    index_attempt_count: int,
    retry_count: int,
    cleanup_ok: bool,
    runtime_stop_reason: Optional[str] = None,
) -> BurstRungResult:
    """Build a content-safe rung result from its terminal per-Source records. A rung is a
    VALID treatment only if the full initial wave was established; otherwise it is a runtime
    stop and carries no reproduction classification (task §14/§40)."""
    treatment_valid = full_initial_wave_established and runtime_stop_reason is None
    if not treatment_valid:
        return BurstRungResult(
            level=level,
            planned_source_count=planned_source_count,
            initial_wave_submitted_count=initial_wave_submitted_count,
            full_initial_wave_established=full_initial_wave_established,
            treatment_valid=False,
            index_attempt_count=index_attempt_count,
            success_count=0,
            failure_count=0,
            retry_count=retry_count,
            classifier_signature_reproduced=False,
            strict_s001_event_reproduced=False,
            historical_attempt_number_match=False,
            novel_failure_observed=False,
            clean=False,
            first_failure_source_id=None,
            first_failure_reason_code=None,
            latency_summary=_latency_summary([]),
            cleanup_ok=cleanup_ok,
            runtime_stop_reason=runtime_stop_reason or "FULL_INITIAL_WAVE_NOT_ESTABLISHED",
            records=records,
        )
    successes = [r for r in records if r.terminal_status == TERMINAL_SUCCESS]
    failures = [r for r in records if r.terminal_status != TERMINAL_SUCCESS]
    sig = any(is_classifier_signature(r) for r in failures)
    strict = any(is_strict_s001_event(r) for r in failures)
    attempt_match = any(historical_attempt_number_match(r) for r in failures)
    novel = any(is_novel_valid_failure(r) for r in failures)
    first_fail = failures[0] if failures else None
    first_code = (
        first_fail.characterization.retry_reason_code
        if (first_fail is not None and first_fail.characterization is not None)
        else (None if first_fail is None else "NONE")
    )
    durations = [r.duration_ms for r in records if isinstance(r.duration_ms, int)]
    return BurstRungResult(
        level=level,
        planned_source_count=planned_source_count,
        initial_wave_submitted_count=initial_wave_submitted_count,
        full_initial_wave_established=True,
        treatment_valid=True,
        index_attempt_count=index_attempt_count,
        success_count=len(successes),
        failure_count=len(failures),
        retry_count=retry_count,
        classifier_signature_reproduced=sig,
        strict_s001_event_reproduced=strict,
        historical_attempt_number_match=attempt_match,
        novel_failure_observed=novel,
        clean=(len(failures) == 0),
        first_failure_source_id=(first_fail.logical_source_id if first_fail else None),
        first_failure_reason_code=first_code,
        latency_summary=_latency_summary(durations),
        cleanup_ok=cleanup_ok,
        runtime_stop_reason=None,
        records=records,
    )


@dataclass(frozen=True)
class BurstExperimentResult:
    """Content-safe overall result of a burst screening ladder (task §43). Carries NO
    H1/H2/H3 root-cause inference (that is a higher-level, separately-gated interpretation)."""

    run_id: str
    levels_planned: Tuple[int, ...]
    levels_entered: Tuple[int, ...]
    first_failure_rung: Optional[int]
    clean_through_level: Optional[int]
    planned_source_workload_count: int
    index_attempt_count: int
    classifier_signature_reproduced: bool
    strict_s001_event_reproduced: bool
    historical_attempt_number_match: bool
    novel_failure_observed: bool
    runtime_valid: bool
    runtime_stop_reason: Optional[str]
    cleanup_ok: bool
    rungs: Tuple[BurstRungResult, ...] = field(default_factory=tuple)

    def as_dict(self) -> Dict[str, object]:
        return {
            "run_type": "GRAPHRAG_08E7_BURST_REPRODUCTION_SCREENING",
            "run_id": self.run_id,
            "levels_planned": list(self.levels_planned),
            "levels_entered": list(self.levels_entered),
            "first_failure_rung": self.first_failure_rung,
            "clean_through_level": self.clean_through_level,
            "planned_source_workload_count": self.planned_source_workload_count,
            "index_attempt_count": self.index_attempt_count,
            "classifier_signature_reproduced": self.classifier_signature_reproduced,
            "strict_s001_event_reproduced": self.strict_s001_event_reproduced,
            "historical_attempt_number_match": self.historical_attempt_number_match,
            "novel_failure_observed": self.novel_failure_observed,
            "runtime_valid": self.runtime_valid,
            "runtime_stop_reason": self.runtime_stop_reason,
            "cleanup_ok": self.cleanup_ok,
            "root_cause_confirmed": False,
            "rungs": [r.as_dict() for r in self.rungs],
        }


__all__ = [
    "BURST_LEVELS",
    "TOP_RUNG",
    "SCREENING_REPETITIONS_PER_LEVEL",
    "MAX_INDEX_ATTEMPTS_PER_SOURCE",
    "DESIGN_MAX_PLANNED_SOURCE_WORKLOAD",
    "DESIGN_MAX_INDEX_ATTEMPTS_TOTAL",
    "BURST_SCHEDULING_MODE",
    "ANCHOR_SOURCE_KEY",
    "HISTORICAL_REASON_CODE",
    "HISTORICAL_S001_KEY",
    "HISTORICAL_ATTEMPT_NUMBER",
    "RUNG_INVALID_RUNTIME",
    "BurstPlanError",
    "BurstBudgetError",
    "BurstLevel",
    "BurstPlan",
    "default_burst_plan",
    "validate_burst_plan",
    "budget_precheck",
    "select_burst_prefix",
    "BurstBudgetGuard",
    "is_classifier_signature",
    "is_strict_s001_event",
    "historical_attempt_number_match",
    "is_novel_valid_failure",
    "BurstRungResult",
    "classify_rung",
    "BurstExperimentResult",
]
