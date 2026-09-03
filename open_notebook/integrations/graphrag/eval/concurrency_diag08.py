"""GraphRAG-08E concurrent-indexing / load-interaction DIAGNOSTIC harness (EVAL-ONLY).

Nothing in production imports this. It is an OBSERVATIONAL diagnostic instrument — NOT
a replacement indexing engine, NOT the production indexing path, and it NEVER tunes
benchmark concurrency, the retry policy, the allowlist, or any frozen parameter.

Motivation (see GRAPHRAG_08E_CONCURRENT_INDEXING_FORENSIC.md): frozen synthetic Source
S001 indexes cleanly in isolation but failed at the TRACK surface under the 75-Source
concurrent burst in full-run attempt #5 (``TRACK_TEXT_PRESENT_NO_ALLOWLIST_MATCH`` /
NON_RETRYABLE). ``CONCURRENCY_LOAD_CAUSE = SUPPORTED``; ``ROOT_CAUSE_CONFIRMED = NO``. Three
hypotheses remain UNCONFIRMED:

  H1 — load-induced transient provider failure whose wording falls OUTSIDE the frozen 08B
       retry allowlist (a classifier gap).
  H2 — genuinely non-transient failure that manifests only under contention (empty/
       malformed/parse).
  H3 — LightRAG v1.5.6 concurrency / shared-state behaviour.

This module models a FUTURE, separately-authorized, bounded concurrency sweep that would
discriminate H1/H2/H3. The live orchestrator here is fail-closed (requires explicit live
authorization AND active Option-A isolation, and an injected indexer/reader — it calls no
provider or sidecar itself), so importing or unit-testing this module performs NO provider
traffic, NO sidecar start, and NO DB mutation.

Design invariants (enforced by tests):
  * The retry DECISION (retryable yes/no) is ALWAYS the frozen 08B classifier
    (``index_retry08.is_transient_reason``) — never reimplemented here. The richer error
    FAMILY taxonomy below is diagnostic labelling only and cannot change that decision.
  * All output is content-free: ids, levels, coarse families, reason codes, counts,
    timings, buckets — never raw source/query/chunk text, generated answers, error text,
    or credentials.
  * A raw TRACK error, if read at all by the future live path, lives ONLY in memory for the
    duration of one ``characterize_failure`` call and is discarded; only the content-safe
    characterization persists.
  * Every plan is mechanically bounded (levels/sources/repetitions/total submissions);
    an over-cap or malformed plan fails closed.
  * Hypothesis interpretation is conservative — SUPPORTED / WEAKLY_SUPPORTED /
    NOT_SUPPORTED / INCONCLUSIVE — and NEVER emits a confirmed root cause.
"""

from __future__ import annotations

import re
import statistics
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from open_notebook.integrations.graphrag.eval.index_retry08 import (
    ReasonCode,
    _length_bucket,
    is_transient_reason,
)

# ---------------------------------------------------------------------------
# Bounded-plan caps (task §15). A plan exceeding ANY cap fails closed.
# ---------------------------------------------------------------------------

#: Allowed diagnostic concurrency treatment levels (task §4). These are DIAGNOSTIC
#: treatment levels only — never a benchmark/full-run concurrency setting (task §4/§12).
ALLOWED_LEVELS: Tuple[int, ...] = (1, 2, 4, 8)
MAX_DIAGNOSTIC_LEVELS = 4
MAX_SOURCES_PER_LEVEL = 8
MAX_REPETITIONS_PER_LEVEL = 3
MAX_TOTAL_SUBMISSIONS = 64

#: The historically-relevant Source is always included first (task §5).
ANCHOR_SOURCE_KEY = "S001"


# ---------------------------------------------------------------------------
# Diagnostic error-family taxonomy (task §7). Diagnostic labelling ONLY — it does
# NOT and CANNOT change the frozen retry decision (that is is_transient_reason).
# ---------------------------------------------------------------------------


class ErrorFamily:
    PROVIDER_RATE_OR_CAPACITY = "PROVIDER_RATE_OR_CAPACITY"
    PROVIDER_TIMEOUT_OR_NETWORK = "PROVIDER_TIMEOUT_OR_NETWORK"
    EMPTY_OR_MALFORMED_RESPONSE = "EMPTY_OR_MALFORMED_RESPONSE"
    PARSE_OR_SCHEMA_FAILURE = "PARSE_OR_SCHEMA_FAILURE"
    LIGHTRAG_INTERNAL = "LIGHTRAG_INTERNAL"
    UNKNOWN_SAFE = "UNKNOWN_SAFE"


# Ordered (first match wins). Provider-transient families are checked first so a
# load/capacity signal is recognised even when other words co-occur; parse/empty and
# lightrag-internal follow; anything else is UNKNOWN_SAFE. These patterns are for
# DIAGNOSTIC grouping and are intentionally broader than the frozen retry allowlist.
_FAMILY_PATTERNS: Tuple[Tuple[str, "re.Pattern[str]"], ...] = (
    (
        ErrorFamily.PROVIDER_RATE_OR_CAPACITY,
        re.compile(
            r"(429|rate[ _-]?limit|too many requests|quota|insufficient[_ ]?quota|"
            r"over ?capacity|overloaded|server is busy|please try again)",
            re.IGNORECASE,
        ),
    ),
    (
        ErrorFamily.PROVIDER_TIMEOUT_OR_NETWORK,
        re.compile(
            r"(time ?out|timed out|gateway timeout|connection (?:reset|aborted|refused)|"
            r"temporarily unavailable|service unavailable|bad gateway|"
            r"http[ /]?5\d\d|status(?: ?code)?[ :=]+5\d\d|internal server error|network)",
            re.IGNORECASE,
        ),
    ),
    (
        ErrorFamily.PARSE_OR_SCHEMA_FAILURE,
        re.compile(
            r"(json|parse|decode|deserial|expecting value|schema|unexpected field|"
            r"missing (?:field|key)|validation|invalid (?:json|input|value|argument))",
            re.IGNORECASE,
        ),
    ),
    (
        ErrorFamily.EMPTY_OR_MALFORMED_RESPONSE,
        re.compile(
            r"(empty (?:response|result|output)|no content|null response|"
            r"malformed|truncat|incomplete|returned nothing|no entit|no relationship)",
            re.IGNORECASE,
        ),
    ),
    (
        ErrorFamily.LIGHTRAG_INTERNAL,
        re.compile(
            r"(lightrag|keyerror|nonetype|none.?type|attributeerror|indexerror|"
            r"typeerror|traceback|assertion)",
            re.IGNORECASE,
        ),
    ),
)


def classify_error_family(error_text: Optional[str]) -> str:
    """Coarse DIAGNOSTIC family for one failure. Content-safe: returns a family label
    only, never the text. Absent/empty text -> UNKNOWN_SAFE (fail-safe, not a decision).

    NOTE: this is diagnostic labelling; it has NO effect on the retry decision, which is
    ``retry_decision`` -> frozen ``is_transient_reason`` (task §7/§9/§12)."""
    if not error_text or not error_text.strip():
        return ErrorFamily.UNKNOWN_SAFE
    for family, pattern in _FAMILY_PATTERNS:
        if pattern.search(error_text):
            return family
    return ErrorFamily.UNKNOWN_SAFE


# ---------------------------------------------------------------------------
# Decision-twin: the retry decision is the FROZEN classifier, never reimplemented.
# ---------------------------------------------------------------------------


def retry_decision(error_text: Optional[str]) -> bool:
    """Retryable yes/no for a TRACK failure — DELEGATED to the frozen 08B classifier.

    Mirrors ``index_retry08`` TRACK semantics exactly: retry ONLY when a present error
    text positively matches the frozen transient allowlist; absent/unreadable/non-matching
    -> not retryable (fail closed). A test asserts zero divergence from
    ``is_transient_reason`` (task §9)."""
    return is_transient_reason(error_text)


@dataclass(frozen=True)
class FailureCharacterization:
    """Content-safe characterization of ONE failure. Holds no raw text.

    ``family`` is the diagnostic taxonomy label; ``retryable`` and ``retry_reason_code``
    come from the FROZEN classifier decision (task §9)."""

    error_text_present: bool
    error_text_length_bucket: str
    family: str
    retryable: bool
    retry_reason_code: str

    def as_dict(self) -> Dict[str, object]:
        return {
            "error_text_present": self.error_text_present,
            "error_text_length_bucket": self.error_text_length_bucket,
            "family": self.family,
            "retryable": self.retryable,
            "retry_reason_code": self.retry_reason_code,
        }


def characterize_failure(error_text: Optional[str]) -> FailureCharacterization:
    """Derive the content-safe characterization of a failure from its raw error text.

    The raw ``error_text`` is used ONLY within this call (to compute the family, the
    frozen retry decision, and coarse buckets) and is NOT retained by the returned object
    (task §8). Callers MUST NOT store/log/persist ``error_text`` — pass it in transiently."""
    present = bool(error_text and error_text.strip())
    retryable = retry_decision(error_text)
    if not present:
        reason = ReasonCode.TRACK_TEXT_ABSENT
    elif retryable:
        reason = ReasonCode.TRACK_TRANSIENT_ALLOWLIST_MATCH
    else:
        reason = ReasonCode.TRACK_TEXT_PRESENT_NO_ALLOWLIST_MATCH
    return FailureCharacterization(
        error_text_present=present,
        error_text_length_bucket=_length_bucket(len(error_text or "")),
        family=classify_error_family(error_text),
        retryable=retryable,
        retry_reason_code=reason,
    )


# ---------------------------------------------------------------------------
# Bounded diagnostic plan (task §4/§5/§15). Pure data + validation; runs nothing.
# ---------------------------------------------------------------------------


class DiagnosticPlanError(ValueError):
    """The requested diagnostic plan is malformed or exceeds a bound (fail closed)."""


@dataclass(frozen=True)
class DiagnosticLevel:
    """One diagnostic treatment: target concurrency + subset size + repetitions."""

    concurrency: int
    source_count: int
    repetitions: int

    @property
    def submissions(self) -> int:
        return self.source_count * self.repetitions


@dataclass(frozen=True)
class ConcurrencyDiagnosticPlan:
    """A bounded, validated plan for a FUTURE concurrency sweep. Executing it requires a
    separate live authorization + active Option-A isolation (see ``run_sweep``)."""

    levels: Tuple[DiagnosticLevel, ...]

    @property
    def total_submissions(self) -> int:
        return sum(lvl.submissions for lvl in self.levels)

    def as_dict(self) -> Dict[str, object]:
        return {
            "levels": [
                {
                    "concurrency": lvl.concurrency,
                    "source_count": lvl.source_count,
                    "repetitions": lvl.repetitions,
                    "submissions": lvl.submissions,
                }
                for lvl in self.levels
            ],
            "total_submissions": self.total_submissions,
        }


def validate_plan(plan: ConcurrencyDiagnosticPlan) -> None:
    """Fail closed unless every bound holds (task §15). Raises ``DiagnosticPlanError``."""
    if not plan.levels:
        raise DiagnosticPlanError("plan has no diagnostic levels")
    if len(plan.levels) > MAX_DIAGNOSTIC_LEVELS:
        raise DiagnosticPlanError(
            f"{len(plan.levels)} levels > MAX_DIAGNOSTIC_LEVELS {MAX_DIAGNOSTIC_LEVELS}"
        )
    seen: set[int] = set()
    for lvl in plan.levels:
        if lvl.concurrency not in ALLOWED_LEVELS:
            raise DiagnosticPlanError(
                f"concurrency {lvl.concurrency} not in ALLOWED_LEVELS {ALLOWED_LEVELS}"
            )
        if lvl.concurrency in seen:
            raise DiagnosticPlanError(f"duplicate concurrency level {lvl.concurrency}")
        seen.add(lvl.concurrency)
        if lvl.source_count < lvl.concurrency:
            raise DiagnosticPlanError(
                f"level {lvl.concurrency}: source_count {lvl.source_count} < concurrency "
                "(cannot realise that parallelism)"
            )
        if lvl.source_count > MAX_SOURCES_PER_LEVEL:
            raise DiagnosticPlanError(
                f"level {lvl.concurrency}: source_count {lvl.source_count} > "
                f"MAX_SOURCES_PER_LEVEL {MAX_SOURCES_PER_LEVEL}"
            )
        if not (1 <= lvl.repetitions <= MAX_REPETITIONS_PER_LEVEL):
            raise DiagnosticPlanError(
                f"level {lvl.concurrency}: repetitions {lvl.repetitions} out of "
                f"[1, {MAX_REPETITIONS_PER_LEVEL}]"
            )
    if plan.total_submissions > MAX_TOTAL_SUBMISSIONS:
        raise DiagnosticPlanError(
            f"total submissions {plan.total_submissions} > MAX_TOTAL_SUBMISSIONS "
            f"{MAX_TOTAL_SUBMISSIONS}"
        )


def default_plan() -> ConcurrencyDiagnosticPlan:
    """A conservative bounded default sweep over the allowed levels (task §4/§10).

    Diagnostic treatment levels only — NOT a proposed full-run concurrency (task §4/§12)."""
    plan = ConcurrencyDiagnosticPlan(
        levels=(
            DiagnosticLevel(concurrency=1, source_count=1, repetitions=2),
            DiagnosticLevel(concurrency=2, source_count=2, repetitions=2),
            DiagnosticLevel(concurrency=4, source_count=4, repetitions=2),
            DiagnosticLevel(concurrency=8, source_count=8, repetitions=2),
        )
    )
    validate_plan(plan)
    return plan


def estimate_budget(plan: ConcurrencyDiagnosticPlan) -> Dict[str, int]:
    """STATIC provider-budget estimate for a plan (task §15). No provider call.

    Reports bounded indexing-SUBMISSION counts only (one embedding + one graph-index
    submission per Source submission is the shape of the future live path). NOTE: this
    bounds the number of documents submitted, NOT the per-submission LLM fan-out that
    LightRAG performs internally during extraction — that is provider-/content-dependent
    and is not caps-bounded here (review LOW-1)."""
    validate_plan(plan)
    total = plan.total_submissions
    return {
        "total_source_submissions": total,
        "max_embedding_submissions": total,
        "max_graph_index_submissions": total,
        "max_total_submissions_cap": MAX_TOTAL_SUBMISSIONS,
    }


# ---------------------------------------------------------------------------
# Deterministic synthetic Source selection (task §5). Selection only — no execution.
# ---------------------------------------------------------------------------


def select_diagnostic_sources(benchmark, count: int) -> Tuple[str, ...]:
    """Deterministically pick ``count`` synthetic Source keys, S001 first (task §5).

    Uses ONLY fixture Source keys (synthetic); never HOLDOUT queries, never retrieval.
    Order: the anchor (S001) then remaining keys in sorted order. Fails closed if the
    count is out of the per-level bound or the corpus lacks the anchor."""
    if not (1 <= count <= MAX_SOURCES_PER_LEVEL):
        raise DiagnosticPlanError(
            f"source count {count} out of [1, {MAX_SOURCES_PER_LEVEL}]"
        )
    keys = sorted(s.key for s in benchmark.sources)
    if ANCHOR_SOURCE_KEY not in keys:
        raise DiagnosticPlanError(f"anchor {ANCHOR_SOURCE_KEY} absent from corpus")
    ordered = [ANCHOR_SOURCE_KEY] + [k for k in keys if k != ANCHOR_SOURCE_KEY]
    if count > len(ordered):
        raise DiagnosticPlanError(f"count {count} exceeds corpus size {len(ordered)}")
    return tuple(ordered[:count])


# ---------------------------------------------------------------------------
# Content-free per-attempt record + aggregation (task §6).
# ---------------------------------------------------------------------------

TERMINAL_SUCCESS = "PROCESSED"
TERMINAL_FAILED = "FAILED"
TERMINAL_TIMEOUT = "TIMEOUT"


@dataclass(frozen=True)
class AttemptRecord:
    """Content-free record of one diagnostic indexing attempt (task §6)."""

    run_id: str
    concurrency_level: int
    logical_source_id: str
    repetition: int
    attempt_number: int
    terminal_status: str  # PROCESSED | FAILED | TIMEOUT
    duration_ms: Optional[int]
    characterization: Optional[FailureCharacterization]  # only on FAILED

    def as_dict(self) -> Dict[str, object]:
        return {
            "run_id": self.run_id,
            "concurrency_level": self.concurrency_level,
            "logical_source_id": self.logical_source_id,
            "repetition": self.repetition,
            "attempt_number": self.attempt_number,
            "terminal_status": self.terminal_status,
            "duration_ms": self.duration_ms,
            "characterization": (
                self.characterization.as_dict() if self.characterization else None
            ),
        }


def _latency_summary(durations: List[int]) -> Dict[str, Optional[float]]:
    if not durations:
        return {"count": 0, "min_ms": None, "median_ms": None, "max_ms": None}
    return {
        "count": len(durations),
        "min_ms": float(min(durations)),
        "median_ms": float(statistics.median(durations)),
        "max_ms": float(max(durations)),
    }


def aggregate_level(level: int, records: List[AttemptRecord]) -> Dict[str, object]:
    """Content-free aggregate for one concurrency level (task §6)."""
    lvl_records = [r for r in records if r.concurrency_level == level]
    total = len(lvl_records)
    successes = [r for r in lvl_records if r.terminal_status == TERMINAL_SUCCESS]
    failures = [r for r in lvl_records if r.terminal_status != TERMINAL_SUCCESS]
    family_dist: Dict[str, int] = {}
    retryable_dist = {"retryable": 0, "non_retryable": 0}
    for r in failures:
        fam = r.characterization.family if r.characterization else ErrorFamily.UNKNOWN_SAFE
        family_dist[fam] = family_dist.get(fam, 0) + 1
        if r.characterization and r.characterization.retryable:
            retryable_dist["retryable"] += 1
        else:
            retryable_dist["non_retryable"] += 1
    durations = [r.duration_ms for r in lvl_records if isinstance(r.duration_ms, int)]
    return {
        "concurrency_level": level,
        "attempts": total,
        "success_count": len(successes),
        "failure_count": len(failures),
        "failure_rate": (len(failures) / total) if total else None,
        "failure_family_distribution": family_dist,
        "retry_classification_distribution": retryable_dist,
        "latency_summary": _latency_summary(durations),
    }


def aggregate(records: List[AttemptRecord]) -> Dict[str, object]:
    """Full content-free aggregate: per-level + failure-rate-vs-concurrency (task §6)."""
    levels = sorted({r.concurrency_level for r in records})
    per_level = [aggregate_level(lvl, records) for lvl in levels]
    return {
        "levels": per_level,
        "failure_rate_by_concurrency": {
            lvl["concurrency_level"]: lvl["failure_rate"] for lvl in per_level
        },
        "total_attempts": len(records),
        "total_failures": sum(lvl["failure_count"] for lvl in per_level),
    }


# ---------------------------------------------------------------------------
# Conservative hypothesis interpretation (task §11). NEVER "confirmed".
# ---------------------------------------------------------------------------

SUPPORTED = "SUPPORTED"
WEAKLY_SUPPORTED = "WEAKLY_SUPPORTED"
NOT_SUPPORTED = "NOT_SUPPORTED"
INCONCLUSIVE = "INCONCLUSIVE"

_PROVIDER_FAMILIES = frozenset(
    {ErrorFamily.PROVIDER_RATE_OR_CAPACITY, ErrorFamily.PROVIDER_TIMEOUT_OR_NETWORK}
)
_CONTENT_FAMILIES = frozenset(
    {ErrorFamily.EMPTY_OR_MALFORMED_RESPONSE, ErrorFamily.PARSE_OR_SCHEMA_FAILURE}
)


def _rate_increases_with_load(agg: Dict[str, object]) -> Optional[bool]:
    """Does failure rate trend UP with concurrency? None if too few data points."""
    by_conc = agg.get("failure_rate_by_concurrency", {})
    pairs = sorted((k, v) for k, v in by_conc.items() if isinstance(v, (int, float)))
    if len(pairs) < 2:
        return None
    first = pairs[0][1]
    last = pairs[-1][1]
    return last > first


def _family_totals(agg: Dict[str, object]) -> Dict[str, int]:
    totals: Dict[str, int] = {}
    for lvl in agg.get("levels", []):
        for fam, n in lvl.get("failure_family_distribution", {}).items():
            totals[fam] = totals.get(fam, 0) + n
    return totals


def interpret_hypotheses(agg: Dict[str, object]) -> Dict[str, object]:
    """Conservative H1/H2/H3 verdicts from aggregates (task §11).

    Returns verdicts in {SUPPORTED, WEAKLY_SUPPORTED, NOT_SUPPORTED, INCONCLUSIVE}. It
    NEVER returns a confirmed root cause: even the strongest verdict is SUPPORTED, and a
    caveat flag records that a single sweep is not proof (task §11/§12/§17)."""
    total_failures = int(agg.get("total_failures", 0) or 0)
    trend = _rate_increases_with_load(agg)
    fam_totals = _family_totals(agg)
    fam_sum = sum(fam_totals.values()) or 0

    # Not enough signal to say anything.
    if total_failures == 0 or fam_sum == 0 or trend is None:
        return {
            "H1_classifier_allowlist_gap": INCONCLUSIVE,
            "H2_load_induced_non_transient": INCONCLUSIVE,
            "H3_lightrag_concurrency_behaviour": INCONCLUSIVE,
            "root_cause_confirmed": False,
            "note": "insufficient data (needs failures across >=2 levels)",
        }

    provider = sum(fam_totals.get(f, 0) for f in _PROVIDER_FAMILIES)
    content = sum(fam_totals.get(f, 0) for f in _CONTENT_FAMILIES)
    lightrag = fam_totals.get(ErrorFamily.LIGHTRAG_INTERNAL, 0)
    prov_frac = provider / fam_sum
    content_frac = content / fam_sum
    lr_frac = lightrag / fam_sum

    # H1: provider-family failures that the FROZEN classifier still marks non-retryable,
    # rising with load. Non-retryable-despite-provider-family is the allowlist-gap tell.
    non_retryable = sum(
        lvl.get("retry_classification_distribution", {}).get("non_retryable", 0)
        for lvl in agg.get("levels", [])
    )
    def verdict(fraction: float, trending: bool) -> str:
        if fraction >= 0.6 and trending:
            return SUPPORTED
        if fraction >= 0.6 or (fraction >= 0.3 and trending):
            return WEAKLY_SUPPORTED
        if fraction == 0:
            return NOT_SUPPORTED
        return INCONCLUSIVE

    h1 = verdict(prov_frac, bool(trend))
    # H1 requires the classifier-gap signal: some provider-family failures are marked
    # non-retryable. Without that, downgrade a SUPPORTED to WEAKLY_SUPPORTED.
    if h1 == SUPPORTED and non_retryable == 0:
        h1 = WEAKLY_SUPPORTED
    h2 = verdict(content_frac, bool(trend))
    h3 = verdict(lr_frac, bool(trend))

    return {
        "H1_classifier_allowlist_gap": h1,
        "H2_load_induced_non_transient": h2,
        "H3_lightrag_concurrency_behaviour": h3,
        "root_cause_confirmed": False,  # NEVER True from this harness (task §11/§12)
        "note": (
            "verdicts are conservative and derived from ONE bounded sweep; a single "
            "observation is not causal proof (task §11/§17)"
        ),
    }


# ---------------------------------------------------------------------------
# Live orchestration seam (task §2/§4/§13/§14) — DESIGNED, fail-closed, NOT run here.
# ---------------------------------------------------------------------------


class LiveDiagnosticNotAuthorizedError(RuntimeError):
    """The live concurrency diagnostic was invoked without the required guards."""


class DiagnosticBudgetExceededError(RuntimeError):
    """An injected indexer returned more attempts than the validated plan allows."""


@dataclass
class SweepResult:
    run_id: str
    plan: Dict[str, object]
    records: List[AttemptRecord] = field(default_factory=list)
    aggregate: Dict[str, object] = field(default_factory=dict)
    interpretation: Dict[str, object] = field(default_factory=dict)


async def run_sweep(
    plan: ConcurrencyDiagnosticPlan,
    *,
    run_id: str,
    index_cell_fn,
    cell_provisioner,
    working_dir: str,
    authorized_live: bool = False,
    require_isolation: bool = True,
) -> SweepResult:
    """Execute a bounded concurrency sweep — FAIL-CLOSED, cell-isolated (task §21).

    This is the FUTURE live path, designed but never called in the 08E.1 implementation
    phase (no caller passes ``authorized_live=True``). It performs NO provider/sidecar/DB
    work itself. Every experimental cell — one (concurrency level, repetition) — is wrapped
    in a ``cell_isolation08.diagnostic_cell08`` context that (via the injected
    ``cell_provisioner``) gives the cell a FRESH LightRAG process + a UNIQUE per-cell
    workspace, so no prior cell's LLM cache / graph state can leak in. The injected indexer
    cannot bypass cell isolation: it is only ever called INSIDE an entered, validated cell.

    Guards:
      * ``authorized_live`` must be True (explicit per-run live authorization);
      * active Option-A isolation is required (``isolation08.require_active_isolation``)
        unless ``require_isolation`` is disabled for a mock unit test;
      * the plan must pass ``validate_plan`` (bounded);
      * each cell must pass the cell-isolation validity gate — a failure raises
        ``DiagnosticCellIsolationFailure`` and STOPS the sweep (no mid-run repair, §19);
      * a duplicate cell identity fails closed via the ``CellRegistry`` (§22).
    It NEVER mutates concurrency/retry/allowlist and NEVER decides a root cause.

    ``index_cell_fn(level, cell, repetition) -> List[AttemptRecord]`` receives the entered
    ``DiagnosticCell`` (with its isolated workspace) and owns the real indexing within it."""
    from open_notebook.integrations.graphrag.eval.cell_isolation08 import (
        CellIdentity,
        CellRegistry,
        diagnostic_cell08,
    )

    validate_plan(plan)
    if not authorized_live:
        raise LiveDiagnosticNotAuthorizedError(
            "run_sweep requires explicit per-run live authorization (authorized_live=True)"
        )
    # A provisioned cell is MANDATORY before the injected indexer can be reached
    # (task §35/§58): the indexer runs only INSIDE an entered, validated cell, so a
    # missing provisioner fails closed here — never a bypass to a bare live indexer.
    if cell_provisioner is None:
        raise LiveDiagnosticNotAuthorizedError(
            "run_sweep requires a live cell provisioner (no provisioned cell, no indexing)"
        )
    if not (
        callable(getattr(cell_provisioner, "provision", None))
        and callable(getattr(cell_provisioner, "dispose", None))
    ):
        raise LiveDiagnosticNotAuthorizedError(
            "cell_provisioner must implement the provision/dispose contract"
        )
    if require_isolation:
        from open_notebook.integrations.graphrag.eval.isolation08 import (
            require_active_isolation,
        )

        require_active_isolation()  # normal-DB path blocked (Option-A required)

    registry = CellRegistry()  # defense-in-depth cell-identity uniqueness (§22)
    records: List[AttemptRecord] = []
    for lvl in plan.levels:
        for rep in range(1, lvl.repetitions + 1):
            identity = CellIdentity(
                run_id=run_id, concurrency=lvl.concurrency, repetition=rep
            )
            # Each cell gets fresh, isolated LightRAG state; the indexer runs ONLY inside.
            async with diagnostic_cell08(
                identity,
                provisioner=cell_provisioner,
                registry=registry,
                working_dir=working_dir,
            ) as cell:
                cell_records = await index_cell_fn(lvl, cell, rep)
                # Defense in depth (review LOW-2): a misbehaving injected indexer must not
                # exceed the validated plan.
                if len(cell_records) > lvl.source_count:
                    raise DiagnosticBudgetExceededError(
                        f"level {lvl.concurrency}: indexer returned {len(cell_records)} "
                        f"records > source_count {lvl.source_count}"
                    )
                records.extend(cell_records)
                if len(records) > plan.total_submissions:
                    raise DiagnosticBudgetExceededError(
                        f"cumulative records {len(records)} > plan total "
                        f"{plan.total_submissions}"
                    )
    agg = aggregate(records)
    return SweepResult(
        run_id=run_id,
        plan=plan.as_dict(),
        records=records,
        aggregate=agg,
        interpretation=interpret_hypotheses(agg),
    )


__all__ = [
    "ALLOWED_LEVELS",
    "MAX_DIAGNOSTIC_LEVELS",
    "MAX_SOURCES_PER_LEVEL",
    "MAX_REPETITIONS_PER_LEVEL",
    "MAX_TOTAL_SUBMISSIONS",
    "ANCHOR_SOURCE_KEY",
    "ErrorFamily",
    "classify_error_family",
    "retry_decision",
    "FailureCharacterization",
    "characterize_failure",
    "DiagnosticPlanError",
    "DiagnosticLevel",
    "ConcurrencyDiagnosticPlan",
    "validate_plan",
    "default_plan",
    "estimate_budget",
    "select_diagnostic_sources",
    "AttemptRecord",
    "TERMINAL_SUCCESS",
    "TERMINAL_FAILED",
    "TERMINAL_TIMEOUT",
    "aggregate_level",
    "aggregate",
    "SUPPORTED",
    "WEAKLY_SUPPORTED",
    "NOT_SUPPORTED",
    "INCONCLUSIVE",
    "interpret_hypotheses",
    "LiveDiagnosticNotAuthorizedError",
    "DiagnosticBudgetExceededError",
    "SweepResult",
    "run_sweep",
]
