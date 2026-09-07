"""Exact PN02 metric formulas (task §19, §22, §27; PN02A §9/§9c/§10d).

EVALUATION-ONLY. Pure functions over canonical Source-id SETS and deterministic
answer records — no retriever, no DB, no network. Nothing in production imports
this. Every formula is the exact set/count arithmetic of the frozen PN02A design;
there is NO qualitative classifier and NO fabricated rank/score anywhere (graph
inputs are ``AbstractSet`` so candidate ORDER cannot affect any result — permuting
GD candidates is a no-op by construction, PN02A §18).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import AbstractSet, Iterable, Optional, Sequence

from open_notebook.integrations.graphrag.eval.datasetpn02 import (
    FROZEN_SOURCES_PER_NOTEBOOK,
    QueryPN02,
)
from open_notebook.integrations.graphrag.eval.schemaspn02 import ArmId, QAAnswerResult

# =========================================================================== #
# Stage-1 isolation / evidence-validity primitives (PN02A §9a/§9b)
# =========================================================================== #

def leaked_sources(
    candidate_sources: AbstractSet[str], members: AbstractSet[str]
) -> frozenset[str]:
    """Valid fixture Sources that are NOT current members of the queried notebook.

    Any such Source is a cross-notebook leak (PN02A §5/§9b). ``candidate_sources``
    is the already-normalized VALID fixture set (foreign/malformed are accounted
    separately by provenance).
    """
    return frozenset(candidate_sources) - frozenset(members)


def citation_invalid_sources(
    citation_sources: AbstractSet[str], members: AbstractSet[str]
) -> frozenset[str]:
    """Claimed citations that are not current members (CITATION_MEMBERSHIP_INVALID)."""
    return frozenset(citation_sources) - frozenset(members)


def source_recall(
    candidate_sources: AbstractSet[str], required: AbstractSet[str]
) -> float:
    """|required ∩ candidates| / |required|. Requires a non-empty required set."""
    _require_nonempty(required)
    return len(frozenset(candidate_sources) & frozenset(required)) / len(required)


def source_precision(
    candidate_sources: AbstractSet[str], required: AbstractSet[str]
) -> Optional[float]:
    """|required ∩ candidates| / |candidates| (None if no candidates)."""
    _require_nonempty(required)
    cands = frozenset(candidate_sources)
    if not cands:
        return None
    return len(cands & frozenset(required)) / len(cands)


def set_f1(
    candidate_sources: AbstractSet[str], required: AbstractSet[str]
) -> Optional[float]:
    """Harmonic mean of source_precision and source_recall (None if precision None)."""
    precision = source_precision(candidate_sources, required)
    if precision is None:
        return None
    recall = source_recall(candidate_sources, required)
    if precision + recall == 0:
        return 0.0
    return 2 * precision * recall / (precision + recall)


def full_required_set_recovered(
    candidate_sources: AbstractSet[str], required: AbstractSet[str]
) -> bool:
    """True iff the ENTIRE required set is present. Requires |required|>0."""
    _require_nonempty(required)
    return frozenset(required) <= frozenset(candidate_sources)


def required_source_recovery(
    candidate_sources: AbstractSet[str], required: AbstractSet[str]
) -> float:
    """Fraction of required Sources recovered (== source_recall; distinct name per §9a)."""
    return source_recall(candidate_sources, required)


def member_candidate_count(
    candidate_sources: AbstractSet[str], members: AbstractSet[str]
) -> int:
    """Number of member Sources in the candidate set (the GD member subset GDm)."""
    return len(frozenset(candidate_sources) & frozenset(members))


def candidate_fraction_of_notebook(
    candidate_sources: AbstractSet[str],
    members: AbstractSet[str],
    notebook_size: int = FROZEN_SOURCES_PER_NOTEBOOK,
) -> float:
    """|candidates ∩ members| / notebook_size (denominator = 8, PN02A §9a/§9c).

    A value of exactly 1.0 means the complete member corpus was returned — the
    full-notebook breadth trap (task §24). No 7/8 or 'near-full' heuristic exists.
    """
    if notebook_size <= 0:
        raise ValueError("notebook_size must be > 0")
    return member_candidate_count(candidate_sources, members) / notebook_size


def false_positive_count(
    candidate_sources: AbstractSet[str],
    members: AbstractSet[str],
    required: AbstractSet[str],
    optional: AbstractSet[str],
) -> int:
    """Member candidates that are neither required nor optional-support (PN02A §9a).

    Non-member candidates are counted as LEAKAGE, not false positives; only the
    member subset is scored here.
    """
    member_cands = frozenset(candidate_sources) & frozenset(members)
    allowed = frozenset(required) | frozenset(optional)
    return len(member_cands - allowed)


def returns_member_evidence(
    candidate_sources: AbstractSet[str], members: AbstractSet[str]
) -> bool:
    """True iff the system returned >=1 member Source (for negative return rate)."""
    return bool(frozenset(candidate_sources) & frozenset(members))


def _require_nonempty(required: AbstractSet[str]) -> None:
    if not required:
        raise ValueError(
            "set metric is undefined for a query with no required sources "
            "(negative) — use the negative metrics, do not score 0"
        )


# =========================================================================== #
# Incremental graph-value metrics (PN02A §9c / task §22) — EXACT
# =========================================================================== #

def graph_new_required_sources(
    required: AbstractSet[str],
    gd_member_set: AbstractSet[str],
    v5_set: AbstractSet[str],
) -> int:
    """| (REQ ∩ GDm) \\ V5 | — required Sources GD adds that V5 missed."""
    return len((frozenset(required) & frozenset(gd_member_set)) - frozenset(v5_set))


def increment_set(
    gd_member_set: AbstractSet[str], v5_set: AbstractSet[str]
) -> frozenset[str]:
    """I(q) = GDm \\ V5 — the member increment GD adds beyond V5."""
    return frozenset(gd_member_set) - frozenset(v5_set)


def graph_new_false_positives(
    gd_member_set: AbstractSet[str],
    v5_set: AbstractSet[str],
    required: AbstractSet[str],
    optional: AbstractSet[str],
) -> int:
    """| I(q) \\ (REQ ∪ OPT) | — member non-required/non-optional Sources GD adds beyond V5."""
    inc = increment_set(gd_member_set, v5_set)
    allowed = frozenset(required) | frozenset(optional)
    return len(inc - allowed)


@dataclass(frozen=True)
class IncrementalRetrievalTotals:
    """Aggregate incremental-value counters over the 21 POS + 3 NEG queries (§9c)."""

    new_req: int                       # INCREMENTAL_REQUIRED_SOURCE_RECOVERY
    new_fp: int                        # INCREMENTAL_FALSE_POSITIVE_COUNT
    sum_increment: int                 # Σ |I(q)| over POS
    incremental_graph_precision: Optional[float]  # None if Σ|I(q)| == 0
    n_gain_notebooks_d: int
    neg_return_gd: int                 # negatives where GD returns >=1 member Source
    neg_return_v5: int                 # negatives where V5 returns >=1 member Source
    multihop_incremental_required_recovery: int


@dataclass(frozen=True)
class RetrievalProbe:
    """One notebook-scoped retrieval query for the incremental-value computation.

    ``is_negative`` picks the negative branch; ``is_multihop`` marks the 3
    multi-hop queries. Sets are canonical member subsets already (GDm = GD member
    subset; V5 = the K=5 vector member set — the vector seam is member-scoped, §8b).
    """

    query_id: str
    notebook_id: str
    required: frozenset[str]
    optional: frozenset[str]
    gd_member_set: frozenset[str]
    v5_set: frozenset[str]
    is_negative: bool
    is_multihop: bool
    notebook_size: int = FROZEN_SOURCES_PER_NOTEBOOK


def incremental_retrieval_totals(
    probes: Sequence[RetrievalProbe],
) -> IncrementalRetrievalTotals:
    """Compute the exact §9c aggregate counters. NEG queries are excluded from the
    POS sums and drive only the negative-return counters.
    """
    new_req = 0
    new_fp = 0
    sum_inc = 0
    gain_notebooks: set[str] = set()
    neg_return_gd = 0
    neg_return_v5 = 0
    mh_recovery = 0

    for p in probes:
        if p.is_negative:
            # Negatives: member-return counters only (a member Source returned on
            # an unanswerable query is over-return, PN02A §9c NEGATIVE_EVIDENCE_RETURN_RATE).
            if p.gd_member_set:
                neg_return_gd += 1
            if p.v5_set:
                neg_return_v5 += 1
            continue

        nreq = graph_new_required_sources(p.required, p.gd_member_set, p.v5_set)
        nfp = graph_new_false_positives(
            p.gd_member_set, p.v5_set, p.required, p.optional
        )
        inc = increment_set(p.gd_member_set, p.v5_set)
        gd_frac = (
            len(p.gd_member_set) / p.notebook_size if p.notebook_size > 0 else 0.0
        )
        new_req += nreq
        new_fp += nfp
        sum_inc += len(inc)
        # A "discriminative gain" query: GD added a required Source V5 missed
        # WITHOUT returning the whole notebook (fraction < 1.0). Task §24 trap.
        if nreq >= 1 and gd_frac < 1.0:
            gain_notebooks.add(p.notebook_id)

        if p.is_multihop:
            v5_full = frozenset(p.required) <= frozenset(p.v5_set)
            union_full = frozenset(p.required) <= (
                frozenset(p.v5_set) | frozenset(p.gd_member_set)
            )
            if (not v5_full) and union_full and gd_frac < 1.0:
                mh_recovery += 1

    precision: Optional[float]
    if sum_inc == 0:
        precision = None  # UNDEFINED if Σ|I(q)| == 0 (PN02A §9c)
    else:
        precision = new_req / sum_inc

    return IncrementalRetrievalTotals(
        new_req=new_req,
        new_fp=new_fp,
        sum_increment=sum_inc,
        incremental_graph_precision=precision,
        n_gain_notebooks_d=len(gain_notebooks),
        neg_return_gd=neg_return_gd,
        neg_return_v5=neg_return_v5,
        multihop_incremental_required_recovery=mh_recovery,
    )


# =========================================================================== #
# QA grading + metrics (PN02A §10c/§10d / task §27) — DETERMINISTIC
# =========================================================================== #

def fact_present(result: QAAnswerResult, token: str) -> bool:
    """Deterministic token presence (PN02A §10c — NO LLM judge).

    If ``answer_text`` is given, a case-sensitive substring scan of it is
    authoritative; otherwise the pre-extracted ``emitted_answer_facts`` set is
    used (synthetic tests). Abstention with neither means no facts are present.
    """
    if result.answer_text is not None:
        return token in result.answer_text
    if result.emitted_answer_facts is not None:
        return token in set(result.emitted_answer_facts)
    return False


@dataclass(frozen=True)
class AnswerGrade:
    query_id: str
    arm: ArmId
    is_negative: bool
    recognized_expected_facts: frozenset[str]
    missing_expected_facts: frozenset[str]
    forbidden_facts_present: frozenset[str]
    abstained: bool
    valid_citation_sources: frozenset[str]
    invalid_citation_sources: frozenset[str]
    required_citations_covered: frozenset[str]
    required_citations_missing: frozenset[str]
    #: An answer fact available only from a non-member Source (== forbidden fact
    #: present, by fixture construction — those tokens live only in non-members).
    cross_notebook_answer_leak: bool


def grade_answer(
    result: QAAnswerResult, query: QueryPN02, members: AbstractSet[str]
) -> AnswerGrade:
    """Grade one (query, arm) answer against frozen fixture ground truth."""
    recognized = frozenset(
        t for t in query.expected_answer_facts if fact_present(result, t)
    )
    missing = frozenset(query.expected_answer_facts) - recognized
    forbidden_present = frozenset(
        t for t in query.forbidden_answer_facts if fact_present(result, t)
    )
    cited = frozenset(result.citation_source_ids)
    valid_cit = cited & frozenset(members)
    invalid_cit = cited - frozenset(members)
    req_cit = frozenset(query.required_citation_source_ids)
    covered = req_cit & valid_cit
    missing_cit = req_cit - valid_cit
    return AnswerGrade(
        query_id=query.query_id,
        arm=result.arm,
        is_negative=query.is_negative,
        recognized_expected_facts=recognized,
        missing_expected_facts=missing,
        forbidden_facts_present=forbidden_present,
        abstained=result.abstained,
        valid_citation_sources=valid_cit,
        invalid_citation_sources=invalid_cit,
        required_citations_covered=covered,
        required_citations_missing=missing_cit,
        cross_notebook_answer_leak=bool(forbidden_present),
    )


@dataclass(frozen=True)
class QAArmMetrics:
    """Per-arm aggregate QA metrics (PN02A §10d/§10e). P* = primary, S* = hard-safety."""

    arm: ArmId
    # Primary dimensions (decisional, §17c)
    answer_required_fact_recall: float          # P1 (over POS)
    citation_required_source_coverage: float    # P2 (over POS)
    negative_answer_abstention_rate: float       # P3 (over NEG)
    # Hard-safety dimensions (must be 0 for any positive claim, §10e/§17c)
    cross_notebook_answer_leakage_rate: float    # S1 (over all)
    citation_invalid_count: int                  # S2 (over all)
    hallucinated_forbidden_fact_rate: float      # S3 (over all)
    # Reported-only extras (§27)
    answer_fact_accuracy: Optional[float]
    citation_validity_rate: Optional[float]
    answer_latency_mean_ms: Optional[float]
    pos_count: int
    neg_count: int


def qa_arm_metrics(
    grades: Sequence[AnswerGrade],
    *,
    latencies_ms: Optional[Sequence[Optional[int]]] = None,
    total_citations: Optional[int] = None,
) -> QAArmMetrics:
    """Aggregate one arm's per-query grades into the frozen QA metrics.

    Scope is the OVERALL aggregate across all graded queries (PN02A §17c);
    per-notebook/per-class breakdowns are diagnostics computed elsewhere.
    """
    if not grades:
        raise ValueError("qa_arm_metrics requires >=1 graded query")
    arm = grades[0].arm
    if any(g.arm is not arm for g in grades):
        raise ValueError("qa_arm_metrics received grades from mixed arms")

    pos = [g for g in grades if not g.is_negative]
    neg = [g for g in grades if g.is_negative]
    n_all = len(grades)

    # Aggregation note (review LOW-3): P1/P2 are MACRO averages (mean of the
    # per-query ratio over POS). The design names the metrics but does not pin
    # micro vs macro; macro is applied IDENTICALLY to the baseline and every graph
    # arm, so the strict-'>' / '>=' §17c decision comparisons are unaffected by the
    # choice. P3/S1/S3 are rates over their respective query sets.

    # P1 — required-fact recall over POS.
    p1 = _mean(
        [
            len(g.recognized_expected_facts)
            / (len(g.recognized_expected_facts) + len(g.missing_expected_facts))
            for g in pos
            if (len(g.recognized_expected_facts) + len(g.missing_expected_facts)) > 0
        ]
    )
    # P2 — required-citation coverage over POS.
    p2 = _mean(
        [
            len(g.required_citations_covered)
            / (len(g.required_citations_covered) + len(g.required_citations_missing))
            for g in pos
            if (len(g.required_citations_covered) + len(g.required_citations_missing)) > 0
        ]
    )
    # P3 — abstention over NEG.
    p3 = (sum(1 for g in neg if g.abstained) / len(neg)) if neg else 0.0

    # S1 — cross-notebook answer leakage over ALL.
    s1 = sum(1 for g in grades if g.cross_notebook_answer_leak) / n_all
    # S2 — invalid-citation count over ALL (citations failing membership).
    s2 = sum(len(g.invalid_citation_sources) for g in grades)
    # S3 — forbidden-fact hallucination over ALL.
    s3 = sum(1 for g in grades if g.forbidden_facts_present) / n_all

    # Reported extras.
    acc_terms = [
        len(g.recognized_expected_facts)
        / (len(g.recognized_expected_facts) + len(g.forbidden_facts_present))
        for g in pos
        if (len(g.recognized_expected_facts) + len(g.forbidden_facts_present)) > 0
    ]
    accuracy = _mean(acc_terms)

    total_cit = (
        total_citations
        if total_citations is not None
        else sum(
            len(g.valid_citation_sources) + len(g.invalid_citation_sources)
            for g in grades
        )
    )
    valid_cit = sum(len(g.valid_citation_sources) for g in grades)
    citation_validity = (valid_cit / total_cit) if total_cit > 0 else None

    latency_mean: Optional[float] = None
    if latencies_ms:
        present = [float(v) for v in latencies_ms if v is not None]
        latency_mean = round(sum(present) / len(present), 3) if present else None

    return QAArmMetrics(
        arm=arm,
        answer_required_fact_recall=_round(p1),
        citation_required_source_coverage=_round(p2),
        negative_answer_abstention_rate=_round(p3),
        cross_notebook_answer_leakage_rate=_round(s1),
        citation_invalid_count=s2,
        hallucinated_forbidden_fact_rate=_round(s3),
        answer_fact_accuracy=(_round(accuracy) if accuracy is not None else None),
        citation_validity_rate=(
            _round(citation_validity) if citation_validity is not None else None
        ),
        answer_latency_mean_ms=latency_mean,
        pos_count=len(pos),
        neg_count=len(neg),
    )


def _mean(values: Iterable[float]) -> float:
    vals = list(values)
    return sum(vals) / len(vals) if vals else 0.0


def _round(value: float) -> float:
    return round(value, 6)


__all__ = [
    # Stage-1
    "leaked_sources",
    "citation_invalid_sources",
    "source_recall",
    "source_precision",
    "set_f1",
    "full_required_set_recovered",
    "required_source_recovery",
    "member_candidate_count",
    "candidate_fraction_of_notebook",
    "false_positive_count",
    "returns_member_evidence",
    # Incremental retrieval
    "graph_new_required_sources",
    "graph_new_false_positives",
    "increment_set",
    "RetrievalProbe",
    "IncrementalRetrievalTotals",
    "incremental_retrieval_totals",
    # QA
    "fact_present",
    "AnswerGrade",
    "grade_answer",
    "QAArmMetrics",
    "qa_arm_metrics",
]
