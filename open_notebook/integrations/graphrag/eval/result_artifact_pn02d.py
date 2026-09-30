"""PN02D POST-B3Y-R3 — durable, atomic, content-safe result-artifact retention.

Closes ``RESULT_ARTIFACT_PERSISTENCE_GAP``. The B3Y-R3 governed real run produced the
first EVALUABLE QA-value result, but the ONLY copy of that result was the process stdout,
which was truncated by an operator pipe (``tail -c``). Because the run is one-shot-consumed
and reconstruction is forbidden, the lost fact-recall aggregates were permanently
unrecoverable.

This module makes a durable on-disk artifact the SYSTEM OF RECORD for a governed real
execution; stdout becomes a presentation mirror only. It provides:

* :func:`assert_result_retention_readiness` — a provider-free PRECLAIM storage probe wired
  into ``RealB1Driver.run`` AFTER the runtime import-readiness guard and BEFORE the
  one-shot ledger claim, so an unwritable / colliding result store FAILS CLOSED before the
  grant is ever burned (parity with the import-readiness guard).
* :func:`finalize_result_artifact` — an ATOMIC finalization (canonical serialize → temp
  write → flush → fsync → close → reopen → parse → identity/completeness/hash verify →
  ``os.replace`` → ``_SUCCESS`` marker). A partial file is never treated as final; an
  existing finalized artifact is never overwritten; a verification failure fails closed
  (drives ``POSTCLAIM_RESULT_PERSISTENCE_FAILURE`` at the caller — never a normal COMPLETE).
* :func:`write_failure_artifact` — durable content-safe postclaim/preclaim failure capture.
* :func:`inspect_run_artifact_state` — provider-free crash/recovery state inspection.

Content-safety is ALLOWLIST-based (:data:`ALLOWED_TOP_LEVEL_PAYLOAD_KEYS`): only the
already-content-safe governed payload fields (ids / labels / counts / verdicts / hashes)
are copied — never raw prompts, answers, source content, materialized content, API keys,
tokens, credentials or ``.env`` contents. Arbitrary objects and raw exception messages are
never serialized. This layer NEVER recomputes any science (fact recall, QA-value,
qa_decision, isolation): it persists the already-governed result verbatim.

It performs NO provider I/O, reads NO secret VALUE, and mutates NO one-shot ledger.
Artifact storage (``~/.open-notebook/eval_results``) and authorization-consumption storage
(``~/.open-notebook/security`` ledger) are deliberately SEPARATE; correlation is by
``run_id`` only, with NO ledger-schema change.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence, Tuple

#: Durable result-artifact schema version — SEPARATE from the QA-value observability version
#: (``qavaluepn02db3``) so the two can evolve independently.
RESULT_ARTIFACT_SCHEMA_VERSION = 1

#: Repo-external artifact root: a sibling of the security-state ledger dir
#: (``~/.open-notebook/security``). Never inside the tracked repository.
_ARTIFACT_ROOT_DIRNAME = ".open-notebook"
_ARTIFACT_ROOT_SUBDIR = "eval_results"

#: Canonical filenames within a per-run directory.
_FINAL_ARTIFACT_FILENAME = "result.json"
_SUCCESS_MARKER_FILENAME = "_SUCCESS"
_TMP_SUFFIX = ".tmp"

#: Lifecycle / status model (task §28). PREPARED/CLAIMED/RUNNING are transient driver states
#: not persisted here; the durable statuses are the terminal ones a reader can find on disk.
STATUS_COMPLETED = "COMPLETED"
STATUS_POSTCLAIM_FAILURE = "POSTCLAIM_FAILURE"
STATUS_PRECLAIM_FAILURE = "PRECLAIM_FAILURE"
_FAILURE_STATUSES = frozenset({STATUS_POSTCLAIM_FAILURE, STATUS_PRECLAIM_FAILURE})
_ALL_STATUSES = frozenset({STATUS_COMPLETED, *_FAILURE_STATUSES})

#: Recovery states discernible by inspecting the on-disk per-run directory (task §38).
RECOVERY_ABSENT = "ABSENT"  # no directory
RECOVERY_EMPTY = "EMPTY"  # directory exists, nothing useful
RECOVERY_TEMP_ONLY = "TEMP_ONLY"  # a .tmp exists but no finalized artifact (crash mid-write)
RECOVERY_FINAL_WITHOUT_SUCCESS = "FINAL_WITHOUT_SUCCESS"  # final exists, marker missing
RECOVERY_FINAL_COMPLETE = "FINAL_COMPLETE"  # final artifact + _SUCCESS marker present

#: ALLOWLIST — the ONLY top-level governed-payload keys copied into the durable ``result``
#: section. Every one is already content-safe by construction (the CLI projector produces
#: ids/labels/counts/verdicts only). Anything not listed is dropped.
ALLOWED_TOP_LEVEL_PAYLOAD_KEYS: Tuple[str, ...] = (
    "scientific_result",
    "index_metrics",
    "index_membership_records",
)

#: ALLOWLIST — content-safe failure diagnostic keys copied into a failure artifact's
#: ``failure`` section. NEVER includes a raw exception message / args / traceback.
ALLOWED_FAILURE_KEYS: Tuple[str, ...] = (
    "result",
    "reasons",
    "failure_classification",
    "failure_reason",
    "error_type",
    "missing_module_name",
    "import_readiness",
    "retention_readiness",
    "provider_error",
    "technical_status",
    "report_kind",
    "index_metrics",
    "index_membership_records",
    "scientific_result",
)

#: Defensive tripwire: no persisted string value may exceed this length. Content-safe
#: projections are ids/labels/counts/short verdicts, so this can only trip on an accidental
#: raw-content regression upstream — in which case we fail closed rather than persist it.
MAX_SAFE_STRING_LEN = 4096

#: PN02D-RH-IR1-H1 — run_id must be exactly ONE safe filesystem segment. Explicit allowlist:
#: ASCII letters/digits/hyphen/underscore, bounded length. This rejects "", ".", "..", any path
#: separator ("/", "\\"), drive/UNC/rooted/absolute forms, embedded traversal and NUL — because
#: none of those characters are in the class. Compatible with the historical generated run_ids
#: (e.g. ``pn02db3yr3-1ad116f4-b04e-46c9-aedf-cce97ebc114f``). Do NOT broaden.
_RUN_ID_MAX_LEN = 200
_RUN_ID_ALLOWED_RE = re.compile(r"\A[A-Za-z0-9_-]{1,%d}\Z" % _RUN_ID_MAX_LEN)

#: PN02D-RH-IR1-M2 — the EXPLICIT allowlist of direct child keys of ``scientific_result`` a B3
#: observability projection emits (``cli_live_pn02d._project_b3_observability_result`` = the base
#: ``_project_scientific_result`` keys + the ``b3_*`` keys). An unknown direct child FAILS CLOSED
#: (task §25). Deeper governed sub-blocks are content-safety-validated recursively (below).
ALLOWED_SCIENTIFIC_RESULT_KEYS: frozenset = frozenset(
    {
        # base _project_scientific_result
        "stage1_status",
        "stage2_authorized",
        "isolation_evidenced",
        "leakage_count",
        "leakage_rate",
        "violations",
        "retrieval_verdict",
        "multihop_verdict",
        "scientific_outputs",
        "retrieval",
        "multihop",
        "membership_removal",
        "query_embedding_attempts",
        "gd_calls",
        "vector_queries",
        # b3 observability additions
        "b3_report_kind",
        "b3_mode",
        "b3_observation_run_id",
        "b3_reference_b2_run_id",
        "b3_expected_pair_count",
        "b3_completed_diagnostic_pair_count",
        "b3_completeness",
        "b3_observability",
    }
)

#: PN02D-RH-IR1-M2 — content-bearing field names that must NEVER be persisted, matched EXACTLY
#: (case-insensitive) at ANY nesting depth. Exact match (not substring) so legitimate governed
#: keys like ``answer_required_fact_recall`` are NOT falsely rejected. This is the SECONDARY
#: defense-in-depth layer; the PRIMARY mechanism is the structural allowlist above.
_FORBIDDEN_CONTENT_KEYS: frozenset = frozenset(
    {
        "prompt",
        "raw_prompt",
        "prompt_text",
        "answer",
        "raw_answer",
        "answer_text",
        "source",
        "source_content",
        "raw_source",
        "content",
        "raw_content",
        "materialized",
        "materialized_content",
        "raw_materialized_content",
        "text",
        "query_text",
        "question_text",
        "context",
        "api_key",
        "apikey",
        "token",
        "access_token",
        "refresh_token",
        "authorization",
        "auth_token",
        "bearer",
        "secret",
        "credential",
        "credentials",
        "password",
        "passphrase",
        "private_key",
        "dotenv",
        "env_dump",
    }
)

#: PN02D-RH-IR3-M1 — PATH-SENSITIVE structural schema. Each governed nested block validates ONLY
#: the keys that legitimately appear in ITS OWN subtree (its vocabulary), so a key valid in one
#: block (e.g. ``qa_decision`` / ``forbidden_fact_count`` / ``arm_metrics`` in qa_value) CANNOT be
#: relocated into a DIFFERENT block (diagnostics / scientific_outputs / accounting) and still pass
#: — the wrong block's schema does not contain it → FAIL CLOSED. This replaces the earlier flat
#: global-union model (which accepted cross-block relocation). :data:`_BLOCK_SCHEMAS` maps a
#: governed block NAME to its allowed keys; entering a value under a block-named key switches the
#: active schema to that block's set (path-sensitive), so the SAME key name is validated per the
#: block it actually appears in (task §22). Keys sourced from the governed content-safe projectors:
#: ``project_p1_diagnostics`` (diagnostics), ``build_qa_value_projection`` (qa_value + qa_decision),
#: the ``scientific_outputs`` verdicts, the ``b3_observability`` wrapper, and the evaluator
#: retrieval/multihop/membership metric blocks.

#: scientific_outputs — the four per-notebook verdicts + the content-safe ``notes`` list
#: (``scientific_outputs_report`` emits ``notes = list(outputs.notes)``, e.g.
#: ``["qa_positive_arms=QA-GD,QA-V+GD"]``). ``notes`` is validated as list[str] by
#: :data:`_BLOCK_KEY_TYPES` (PN02D-RASD: real-B3 persisted field, forensic field #5).
_SCIENTIFIC_OUTPUTS_SCHEMA: frozenset = frozenset(
    {
        "PER_NOTEBOOK_GRAPH_QA_VALUE_EVIDENCED",
        "PER_NOTEBOOK_GRAPH_RETRIEVAL_VALUE_EVIDENCED",
        "PER_NOTEBOOK_ISOLATION_EVIDENCED",
        "PER_NOTEBOOK_MULTIHOP_INCREMENTAL_VALUE_EVIDENCED",
        "notes",
    }
)

#: qa_decision — the frozen decision shape ONLY (build_qa_value_projection line ~328).
_QA_DECISION_SCHEMA: frozenset = frozenset({"verdict", "rule", "positive_arms"})

#: b3_observability wrapper — its direct children (diagnostics / qa_value_observability are nested
#: named blocks with their OWN schemas).
_B3_OBSERVABILITY_SCHEMA: frozenset = frozenset(
    {
        "report_kind", "mode", "observation_run_id", "reference_b2_run_id",
        "expected_pair_count", "completed_diagnostic_pair_count", "completeness",
        "diagnostics", "qa_value_observability",
    }
)

#: diagnostics subtree vocabulary (project_p1_diagnostics). Does NOT contain qa_decision /
#: forbidden_fact_count / arm_metrics (those are qa_value-only) → cross-block relocation rejected.
_DIAGNOSTICS_SCHEMA: frozenset = frozenset(
    {
        # PN02D-RASD (forensic field #1): ``project_p1_diagnostics`` emits ``b3_mode``
        # (e.g. "OBSERVABILITY_ONLY") as a diagnostics key; validated str by _BLOCK_KEY_TYPES.
        "b3_mode",
        "aggregate", "answer_contained_fact_ids", "answer_contained_shares_grader_matcher",
        "arm", "evidence_contained_fact_ids", "evidence_source_ids",
        "generation_missing_after_evidence_count", "grader_recognized_count",
        "grader_recognized_fact_ids", "grading_mismatch_candidate_count",
        "grading_mismatch_candidate_fact_ids", "missing_fact_ids", "notebook_id", "ok_count",
        "per_fact_layer", "per_query_arm", "positive_query_arm_count", "query_id",
        "required_fact_ids", "required_fact_in_answer_count", "required_fact_in_evidence_count",
        "required_fact_total", "retrieval_missing_count",
    }
)

#: qa_value_observability subtree vocabulary (build_qa_value_projection, incl. applicability /
#: per_query_arm / arm_metrics item fields). qa_decision is a nested named block (own schema).
_QA_VALUE_SCHEMA: frozenset = frozenset(
    {
        "version", "mode", "isolation_evidenced", "expected_pair_count", "expected_positive_count",
        "expected_negative_count", "observed_pair_count", "observed_positive_count",
        "observed_negative_count", "applicability", "per_query_arm", "arm_metrics",
        "negative_abstention_pass_count", "negative_abstention_fail_count",
        "citation_coverage_baseline", "invalid_citation_count", "forbidden_fact_count",
        "cross_notebook_citation_count", "qa_decision", "qa_value_status",
        "qa_value_observability_complete",
        # applicability sub-keys + per-arm / per-observation item fields (non-named nested dicts):
        "P1_fact_recall", "P2_citation_coverage", "P3_negative_abstention",
        "S1_cross_notebook_leakage", "S2_invalid_citations", "S3_forbidden_facts",
        "arm", "query_id", "notebook_id", "is_negative", "abstained", "answer_required_fact_recall",
        "citation_required_source_coverage", "cross_notebook_answer_leak",
        "cross_notebook_answer_leakage_rate", "negative_answer_abstention_rate",
        "hallucinated_forbidden_fact_rate", "citation_invalid_count", "recognized_fact_count",
        "missing_fact_count", "required_fact_count", "required_citation_count",
        "required_citations_covered_count", "required_citations_covered_ids",
        "required_citations_missing_count", "required_citations_missing_ids",
        "valid_citation_count", "valid_citation_source_ids", "invalid_citation_source_ids",
        "forbidden_facts_present_count", "forbidden_facts_present_ids", "neg_count", "pos_count",
        "positive_arms", "rule", "verdict",
    }
)

#: retrieval / multihop / membership_removal evaluator metric blocks (reportpn02/metricspn02).
_METRICS_SCHEMA: frozenset = frozenset(
    {
        "metrics", "arm", "arms", "verdict", "violations", "stage_status", "query_count",
        "probe_count", "notebook_record_id", "shared_source", "dataset_class", "neg_count",
        "pos_count", "positive_arms", "accepted_evidence_removed_nb", "accepted_evidence_retained_nb",
        "answer_fact_accuracy", "answer_latency_mean_ms", "citation_membership_invalid",
        "citation_validity_rate", "cross_notebook_leak_query_count",
        "cross_notebook_leak_source_occurrences", "cross_notebook_leakage_rate",
        "cross_notebook_answer_leakage_rate", "graph_delete_succeeded_removed_nb",
        "multihop_incremental_required_recovery", "n_gain_notebooks_d", "neg_return_gd", "new_fp",
        "new_req", "postcondition_removed_holds", "postcondition_retained_holds",
        "provenance_foreign", "provenance_malformed", "removed_from_notebook", "retained_notebook",
        "stale_graph_evidence_accepted_as_valid", "vector_k_values", "answer_required_fact_recall",
        "negative_answer_abstention_rate", "hallucinated_forbidden_fact_rate",
        "citation_required_source_coverage",
    }
)

#: PN02D-RASD PATH-SPECIFIC metric schemas. The shared :data:`_METRICS_SCHEMA` base stays the
#: membership_removal vocabulary and is NOT broadened (so ``neg_return_v5`` can never be legal in
#: multihop / membership_removal). ``retrieval_report`` emits ``rule`` + ``neg_return_v5`` (forensic
#: fields #4/#3); ``multihop_report`` emits ``rule`` only (forensic field #2, NO ``neg_return_v5``);
#: ``removal_report`` emits neither. ``rule`` is str and ``neg_return_v5`` is bool-excluding int per
#: :data:`_BLOCK_KEY_TYPES`.
_RETRIEVAL_SCHEMA: frozenset = _METRICS_SCHEMA | {"rule", "neg_return_v5"}
_MULTIHOP_SCHEMA: frozenset = _METRICS_SCHEMA | {"rule"}

#: The path-sensitive dispatch table: a governed block NAME → the allowed keys within that block's
#: own subtree. When the validator recurses into a value stored under one of these names, it
#: switches to that block's schema (see :func:`_assert_block`). ``scientific_result`` root uses
#: :data:`ALLOWED_SCIENTIFIC_RESULT_KEYS`. Any name NOT resolving to a schema keeps the parent
#: block's vocabulary for its nested dicts.
_BLOCK_SCHEMAS: Dict[str, frozenset] = {
    "scientific_result": ALLOWED_SCIENTIFIC_RESULT_KEYS,
    "b3_observability": _B3_OBSERVABILITY_SCHEMA,
    "diagnostics": _DIAGNOSTICS_SCHEMA,
    "qa_value_observability": _QA_VALUE_SCHEMA,
    "qa_decision": _QA_DECISION_SCHEMA,
    "scientific_outputs": _SCIENTIFIC_OUTPUTS_SCHEMA,
    "retrieval": _RETRIEVAL_SCHEMA,
    "multihop": _MULTIHOP_SCHEMA,
    "membership_removal": _METRICS_SCHEMA,
}

#: NON-AUTHORITATIVE documentation union of every governed nested key across all block schemas.
#: Retained only for reference/back-compat exports; the AUTHORITY is the path-sensitive
#: :data:`_BLOCK_SCHEMAS` (task §4 GLOBAL_UNION_IS_PRIMARY_SCHEMA=NO).
ALLOWED_NESTED_KEYS: frozenset = frozenset().union(
    ALLOWED_SCIENTIFIC_RESULT_KEYS,
    _SCIENTIFIC_OUTPUTS_SCHEMA,
    _QA_DECISION_SCHEMA,
    _B3_OBSERVABILITY_SCHEMA,
    _DIAGNOSTICS_SCHEMA,
    _QA_VALUE_SCHEMA,
    _METRICS_SCHEMA,
    _RETRIEVAL_SCHEMA,
    _MULTIHOP_SCHEMA,
)


# --------------------------------------------------------------------------------------
# PN02D-RASD PATH-SPECIFIC VALUE-TYPE constraints (narrow, additive). The path-sensitive
# key allowlist (:data:`_BLOCK_SCHEMAS`) governs WHICH keys may appear in a block; these
# predicates additionally govern the VALUE TYPE of exactly the remediated leaf keys at their
# legitimate block path (no other existing field's semantics change). Each predicate returns
# ``(ok, content_safe_detail)`` — the detail carries only a type NAME, never a value.
# --------------------------------------------------------------------------------------
def _vt_str(value: object) -> Tuple[bool, str]:
    return isinstance(value, str), f"type={type(value).__name__}"


def _vt_int_excluding_bool(value: object) -> Tuple[bool, str]:
    # PN02D-RASD-IR1-M2: Python ``bool`` is a subclass of ``int``; a count field must reject
    # True/False. isinstance(int) alone would accept them.
    return (isinstance(value, int) and not isinstance(value, bool)), f"type={type(value).__name__}"


def _vt_list_of_str(value: object) -> Tuple[bool, str]:
    if not isinstance(value, list):
        return False, f"type={type(value).__name__}"
    for idx, item in enumerate(value):
        if not isinstance(item, str):
            return False, f"element[{idx}]_type={type(item).__name__}"
    return True, ""


#: Per-block leaf-key value-type contracts. Keyed by the governed block NAME then the leaf key,
#: so a type is enforced ONLY at the legitimate path (path-specific, mirrors _BLOCK_SCHEMAS).
_BLOCK_KEY_TYPES: Dict[str, Dict[str, Callable[[object], Tuple[bool, str]]]] = {
    "diagnostics": {"b3_mode": _vt_str},
    "retrieval": {"rule": _vt_str, "neg_return_v5": _vt_int_excluding_bool},
    "multihop": {"rule": _vt_str},
    "scientific_outputs": {"notes": _vt_list_of_str},
}

#: PN02D-RH-IR2-M1 — normalized forms of forbidden content-bearing names (separators stripped,
#: lowercased) so ADVERSARIAL variants collapse to the same token (``rawAnswer``/``raw-answer``/
#: ``raw_answer`` → ``rawanswer``; ``bearer_token`` → ``bearertoken``; ``secret_value`` →
#: ``secretvalue``). Matched EXACTLY on the normalized form (never substring, so governed
#: ``answer_required_fact_recall`` is unaffected). Secondary defense-in-depth beneath the
#: structural allowlist.
_NORMALIZED_FORBIDDEN_KEYS: frozenset = frozenset(
    _k.replace("_", "")
    for _k in (
        _FORBIDDEN_CONTENT_KEYS
        | {
            "bearer_token",
            "secret_value",
            "raw_answer_text",
            "source_content_text",
            "prompt_text",
            "answer_text",
        }
    )
)

#: Mandatory content-safe blocks a COMPLETED B3 observability artifact MUST carry (task §55).
#: Dotted paths under the durable envelope's ``result`` section. Fact-recall aggregates live
#: under ``b3_observability.diagnostics``; the QA-value block under
#: ``b3_observability.qa_value_observability``; provider-accounting spend counts are the
#: workload-ledger ``*_attempts`` / ``*_calls`` / ``*_queries`` fields.
MANDATORY_B3_COMPLETED_PATHS: Tuple[Tuple[str, ...], ...] = (
    ("scientific_result",),
    ("scientific_result", "scientific_outputs"),
    ("scientific_result", "b3_observability"),
    ("scientific_result", "b3_observability", "diagnostics"),
    ("scientific_result", "b3_observability", "qa_value_observability"),
    ("scientific_result", "query_embedding_attempts"),
    ("scientific_result", "gd_calls"),
    ("scientific_result", "vector_queries"),
)

#: PN02D-RH-IR4-H1 — the subset of mandatory paths that MUST resolve to a dict (governed nested
#: blocks). Completeness requires these to be present AND structurally a dict, not merely a
#: non-None scalar, so a COMPLETED artifact cannot finalize with a governed block replaced by a
#: scalar (type confusion / silent science loss).
_MANDATORY_B3_DICT_PATHS: Tuple[Tuple[str, ...], ...] = (
    ("scientific_result",),
    ("scientific_result", "scientific_outputs"),
    ("scientific_result", "b3_observability"),
    ("scientific_result", "b3_observability", "diagnostics"),
    ("scientific_result", "b3_observability", "qa_value_observability"),
)


# --------------------------------------------------------------------------------------
# Errors (all content-safe).
# --------------------------------------------------------------------------------------
class ResultRetentionError(RuntimeError):
    """Base class for all durable result-artifact retention failures (content-safe)."""


class LiveResultRetentionReadinessError(ResultRetentionError):
    """Raised (fail-closed) by the PRECLAIM readiness guard when the local artifact store
    is not usable (unresolvable/unwritable root, run-id collision, temp/atomic-write probe
    failure). This is a LOCAL storage/launch-environment failure — NOT a provider failure —
    and must never be classified as one. Carries only content-safe identifiers.
    """

    def __init__(self, report: "RetentionReadinessReport") -> None:
        self.report = report
        super().__init__(
            "governed real run pre-claim result-retention readiness failed "
            f"(LOCAL_RESULT_RETENTION_UNREADY; {report.failed_check}: {report.error_type})"
        )

    def as_safe_dict(self) -> Dict[str, object]:
        return self.report.as_safe_dict()


class ResultArtifactAlreadyExistsError(ResultRetentionError):
    """Raised (fail-closed) when a finalized artifact already exists for the run_id.

    A governed run_id is single-use (the one-shot ledger already guarantees this); the
    artifact store enforces the same at the persistence layer. Never overwrites.
    """

    def __init__(self, run_id: str, path: Path) -> None:
        self.run_id = run_id
        self.path = path
        super().__init__(
            f"finalized result artifact already exists for run_id {run_id!r} "
            "(no-overwrite fail-closed)"
        )

    def as_safe_dict(self) -> Dict[str, object]:
        return {
            "classification": "RESULT_ARTIFACT_ALREADY_EXISTS",
            "run_id": self.run_id,
        }


class ResultArtifactPersistenceError(ResultRetentionError):
    """Raised (fail-closed) when durable finalization cannot be verified (completeness /
    identity / read-after-write / hash). Drives ``POSTCLAIM_RESULT_PERSISTENCE_FAILURE`` at
    the caller: an in-memory result that cannot be durably persisted must NEVER be reported
    as a normal COMPLETE, and there is NO automatic retry.
    """

    def __init__(self, reason: str, detail: str = "") -> None:
        self.reason = reason
        self.detail = detail
        super().__init__(
            f"durable result artifact finalization failed ({reason}"
            + (f": {detail}" if detail else "")
            + ")"
        )

    def as_safe_dict(self) -> Dict[str, object]:
        return {
            "classification": "POSTCLAIM_RESULT_PERSISTENCE_FAILURE",
            "reason": self.reason,
        }


# --------------------------------------------------------------------------------------
# Value objects.
# --------------------------------------------------------------------------------------
@dataclass(frozen=True)
class ResultArtifactIdentity:
    """Content-safe identity bound into every artifact (task §11)."""

    run_id: str
    head_commit: str
    live_auth_tag: str
    implementation_checkpoint_commit: str
    implementation_checkpoint_tag: str
    fixture_hash: str
    treatment: bool
    execution_kind: str

    def as_dict(self) -> Dict[str, object]:
        return {
            "run_id": self.run_id,
            "head_commit": self.head_commit,
            "live_auth_tag": self.live_auth_tag,
            "implementation_checkpoint_commit": self.implementation_checkpoint_commit,
            "implementation_checkpoint_tag": self.implementation_checkpoint_tag,
            "fixture_hash": self.fixture_hash,
            "treatment": self.treatment,
            "execution_kind": self.execution_kind,
        }


@dataclass(frozen=True)
class RetentionReadinessReport:
    """Content-safe structured result of the preclaim storage probe."""

    status: str  # "OK" | "FAIL"
    run_id: str
    checks_passed: Tuple[str, ...] = ()
    failed_check: Optional[str] = None
    error_type: Optional[str] = None

    def as_safe_dict(self) -> Dict[str, object]:
        return {
            "status": self.status,
            "run_id": self.run_id,
            "checks_passed": list(self.checks_passed),
            "failed_check": self.failed_check,
            "error_type": self.error_type,
            "classification": "LOCAL_RESULT_RETENTION_UNREADY",
        }


@dataclass(frozen=True)
class WrittenArtifact:
    """Result of a successful atomic finalization."""

    path: Path
    sha256: str
    status: str
    success_marker: Optional[Path] = None


@dataclass(frozen=True)
class ResultRetentionConfig:
    """Threaded into the shared composed evaluator to enable durable retention for a specific
    governed command. B1/B2 pass ``None`` (durable retention scoped to the B3 QA-value run,
    the gap that lost B3Y-R3); B3 passes an enabled config. ``base_dir`` None → default root.
    """

    enabled: bool
    execution_kind: str
    treatment: bool
    base_dir: Optional[str] = None
    mandatory_completed_paths: Tuple[Tuple[str, ...], ...] = MANDATORY_B3_COMPLETED_PATHS


@dataclass(frozen=True)
class RecoveryState:
    """Provider-free on-disk inspection result (task §38)."""

    run_id: str
    state: str
    final_present: bool
    success_marker_present: bool
    temp_present: bool
    dir_path: str = field(default="")


# --------------------------------------------------------------------------------------
# Path resolution.
# --------------------------------------------------------------------------------------
def default_result_artifact_dir() -> Path:
    """Return the repo-external artifact root (``~/.open-notebook/eval_results``)."""
    return Path.home() / _ARTIFACT_ROOT_DIRNAME / _ARTIFACT_ROOT_SUBDIR


def _resolve_base_dir(base_dir: Optional[str]) -> Path:
    return Path(base_dir) if base_dir else default_result_artifact_dir()


def validate_run_id(run_id: str) -> str:
    """PN02D-RH-IR1-H1: fail closed unless ``run_id`` is exactly ONE safe filesystem segment.

    Rejects empty, non-str, ``.``/``..``, any path separator, drive/UNC/rooted/absolute forms,
    embedded traversal and NUL — via a strict single-segment allowlist. Returns the validated id.
    """
    if not isinstance(run_id, str) or not run_id or not run_id.strip():
        raise ResultRetentionError("run_id must be a non-empty string")
    if not _RUN_ID_ALLOWED_RE.match(run_id):
        raise ResultRetentionError(
            "run_id is not a single safe filesystem segment "
            "(allowed: ASCII letters/digits/hyphen/underscore, 1-"
            f"{_RUN_ID_MAX_LEN} chars); refusing to build an artifact path"
        )
    # The regex already excludes separators/dots-only/drive markers, but re-reject the reserved
    # single/double-dot names defensively (they match no allowed char anyway).
    if run_id in (".", ".."):
        raise ResultRetentionError("run_id must not be a dot segment")
    return run_id


def run_artifact_dir(run_id: str, *, base_dir: Optional[str] = None) -> Path:
    """Return the unique per-run artifact directory (task §10). One run_id → one dir.

    Defense-in-depth: syntactic single-segment validation (:func:`validate_run_id`) PLUS a
    resolve-and-contain check asserting the directory is a DIRECT child of the artifact root
    (never relying on string-prefix comparison alone).
    """
    validate_run_id(run_id)
    base = _resolve_base_dir(base_dir)
    candidate = base / run_id
    # Resolve-and-contain (task §5): the resolved candidate must be a direct child of the
    # resolved root. resolve(strict=False) works on not-yet-existing paths.
    resolved_base = base.resolve()
    resolved_candidate = candidate.resolve()
    if resolved_candidate.parent != resolved_base:
        raise ResultRetentionError(
            "run_id artifact directory escapes the artifact root (containment check failed)"
        )
    return candidate


def _final_path(run_id: str, base_dir: Optional[str]) -> Path:
    return run_artifact_dir(run_id, base_dir=base_dir) / _FINAL_ARTIFACT_FILENAME


def _success_marker_path(run_id: str, base_dir: Optional[str]) -> Path:
    return run_artifact_dir(run_id, base_dir=base_dir) / _SUCCESS_MARKER_FILENAME


# --------------------------------------------------------------------------------------
# Content-safe serialization (ALLOWLIST-based; no arbitrary/exception serialization).
# --------------------------------------------------------------------------------------
def _normalize_key(key: str) -> str:
    """Collapse a key to its adversarial-variant-invariant form (lowercase, separators removed)
    so ``rawAnswer``/``raw-answer``/``raw_answer`` → ``rawanswer`` (PN02D-RH-IR2-M1 §22)."""
    return key.strip().lower().replace("_", "").replace("-", "").replace(" ", "")


def _assert_content_safe(
    obj: object,
    *,
    path: str = "result",
    allowed_keys: Optional[frozenset] = None,
) -> None:
    """PN02D-RH-IR1-M2 / IR2-M1 recursive content-safety validator (fail-closed) at EVERY depth.

    Enforces, structurally:
      * when ``allowed_keys`` is given (the scientific_result tree), EVERY dict key must be in it
        — an unknown/variant/raw nested key FAILS CLOSED (``unknown_nested_key``); this is the
        PRIMARY structural nested allowlist so no generic deep dict / short raw value under an
        unapproved key can persist (task §13/§21/§24/§26);
      * NO forbidden content-bearing key name, matched on the NORMALIZED form
        (:data:`_NORMALIZED_FORBIDDEN_KEYS`) so camelCase/hyphen/underscore variants collapse
        (task §22/§26/§32) — secondary defense, also applied to schema-bound dicts;
      * NO string value longer than :data:`MAX_SAFE_STRING_LEN` (retained tripwire, task §30/§34);
      * NO arbitrary object — only JSON-native primitives + dict/list/tuple (task §21/§23/§36).

    ``allowed_keys=None`` (index_metrics / index_membership_records / failure diagnostics) applies
    everything EXCEPT unknown-key rejection, because those are produced by strict schema-bound
    dataclasses / bounded safe-dict builders (task §20 REFERENCE_EXISTING_STRICT_SCHEMA).
    """
    if obj is None or isinstance(obj, (bool, int, float)):
        return
    if isinstance(obj, str):
        if len(obj) > MAX_SAFE_STRING_LEN:
            raise ResultArtifactPersistenceError(
                "oversized_string_value", f"at {path} (len={len(obj)})"
            )
        return
    if isinstance(obj, dict):
        for key, value in obj.items():
            # PN02D-RH-IR4-M1: a non-string dict key would otherwise skip the allowlist and be
            # coerced to a string by json.dumps, persisting an unapproved field. Fail closed.
            if not isinstance(key, str):
                raise ResultArtifactPersistenceError(
                    "non_string_dict_key", f"at {path} (key={key!r})"
                )
            if _normalize_key(key) in _NORMALIZED_FORBIDDEN_KEYS:
                raise ResultArtifactPersistenceError(
                    "forbidden_content_key", f"at {path}.{key}"
                )
            if allowed_keys is not None and key not in allowed_keys:
                raise ResultArtifactPersistenceError(
                    "unknown_nested_key", f"at {path}.{key}"
                )
            _assert_content_safe(value, path=f"{path}.{key}", allowed_keys=allowed_keys)
        return
    if isinstance(obj, (list, tuple)):
        for idx, value in enumerate(obj):
            _assert_content_safe(value, path=f"{path}[{idx}]", allowed_keys=allowed_keys)
        return
    # Any non-JSON-native / arbitrary object is refused (no arbitrary object serialization).
    raise ResultArtifactPersistenceError(
        "non_content_safe_value_type", f"at {path} (type={type(obj).__name__})"
    )


def _assert_block(
    obj: object, *, allowed: frozenset, path: str, block_name: str = "scientific_result"
) -> None:
    """PN02D-RH-IR3-M1 PATH-SENSITIVE structural validator (fail-closed) for the
    ``scientific_result`` tree. At each dict node EVERY key must be in ``allowed`` — the schema of
    the block we are currently inside — so a governed key valid in one block cannot be relocated
    into a different block (its wrong-block schema does not contain it). When a key names a nested
    governed block (:data:`_BLOCK_SCHEMAS`), recursion SWITCHES to that block's schema (so the same
    name is validated per the block it appears in, task §22); otherwise nested dicts keep the
    current block's vocabulary. Also enforces normalized-forbidden key rejection, the oversized
    tripwire, list-element validation, and JSON-native-only values at every depth.

    ``block_name`` names the governed block whose schema is currently ``allowed`` (root =
    ``scientific_result``); it selects the PN02D-RASD per-key value-type contracts in
    :data:`_BLOCK_KEY_TYPES`, so a remediated leaf key's TYPE is enforced ONLY at its legitimate
    block path (path-specific, exactly like the key allowlist).
    """
    if obj is None or isinstance(obj, (bool, int, float)):
        return
    if isinstance(obj, str):
        if len(obj) > MAX_SAFE_STRING_LEN:
            raise ResultArtifactPersistenceError(
                "oversized_string_value", f"at {path} (len={len(obj)})"
            )
        return
    if isinstance(obj, dict):
        for key, value in obj.items():
            # PN02D-RH-IR4-M1: a non-string dict key would skip the per-block allowlist and be
            # coerced to a string by json.dumps, persisting an unapproved field. Fail closed.
            if not isinstance(key, str):
                raise ResultArtifactPersistenceError(
                    "non_string_dict_key", f"at {path} (key={key!r})"
                )
            if _normalize_key(key) in _NORMALIZED_FORBIDDEN_KEYS:
                raise ResultArtifactPersistenceError(
                    "forbidden_content_key", f"at {path}.{key}"
                )
            if key not in allowed:
                raise ResultArtifactPersistenceError(
                    "wrong_block_or_unknown_key", f"at {path}.{key}"
                )
            # PN02D-RASD PATH-SPECIFIC value-type contract for a remediated leaf key at its
            # legitimate block path (content-safe: detail is a type NAME, never a value).
            _key_type_check = _BLOCK_KEY_TYPES.get(block_name, {}).get(key)
            if _key_type_check is not None:
                _ok, _detail = _key_type_check(value)
                if not _ok:
                    raise ResultArtifactPersistenceError(
                        "wrong_value_type_for_key", f"at {path}.{key} ({_detail})"
                    )
            # Path-sensitive switch: entering a nested named block uses ITS schema.
            if key in _BLOCK_SCHEMAS:
                # PN02D-RH-IR4-H1: a named governed block MUST be a dict (or None = legitimately
                # absent / not-evaluated). A scalar/list where a block is expected is structural
                # type confusion (it would silently drop the block's governed content) → fail closed.
                if value is not None and not isinstance(value, dict):
                    raise ResultArtifactPersistenceError(
                        "named_block_not_a_dict",
                        f"at {path}.{key} (type={type(value).__name__})",
                    )
                child_allowed = _BLOCK_SCHEMAS[key]
                child_block = key
            else:
                child_allowed = allowed
                child_block = block_name
            _assert_block(
                value, allowed=child_allowed, path=f"{path}.{key}", block_name=child_block
            )
        return
    if isinstance(obj, (list, tuple)):
        for idx, value in enumerate(obj):
            _assert_block(
                value, allowed=allowed, path=f"{path}[{idx}]", block_name=block_name
            )
        return
    raise ResultArtifactPersistenceError(
        "non_content_safe_value_type", f"at {path} (type={type(obj).__name__})"
    )


def _coerce_int(value: object) -> int:
    """Content-safe int coercion for a count-only field; non-numeric → 0."""
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().lstrip("-").isdigit():
        return int(value)
    return 0


def _canonical_bytes(obj: Dict[str, object]) -> bytes:
    """Deterministic canonical JSON encoding used for hashing and on-disk bytes."""
    return (
        json.dumps(obj, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    ).encode("utf-8")


def _hash_domain(envelope: Dict[str, object]) -> Dict[str, object]:
    """The envelope minus the two fields that cannot participate in its own hash:
    ``artifact_sha256`` (self-referential) and ``written_at_utc`` (non-deterministic)."""
    return {k: v for k, v in envelope.items() if k not in ("artifact_sha256", "written_at_utc")}


def _compute_sha256(envelope: Dict[str, object]) -> str:
    return hashlib.sha256(_canonical_bytes(_hash_domain(envelope))).hexdigest()


def build_content_safe_artifact(
    *,
    status: str,
    command: str,
    identity: ResultArtifactIdentity,
    payload: Dict[str, object],
    claimed: bool,
) -> Dict[str, object]:
    """Build the canonical content-safe artifact envelope from an already-governed payload.

    Structural ALLOWLIST is the PRIMARY content-safety mechanism (task §27): copies only
    :data:`ALLOWED_TOP_LEVEL_PAYLOAD_KEYS` (COMPLETED) / :data:`ALLOWED_FAILURE_KEYS` (failure).
    The nested ``scientific_result`` tree is validated by the PATH-SENSITIVE per-block schema
    (:func:`_assert_block` over :data:`_BLOCK_SCHEMAS`), so each governed block validates only its
    own keys and a key valid in one block cannot be relocated into another (task §16/§17/§20/§25).
    ``index_metrics`` / ``index_membership_records`` / failure diagnostics are strict schema-bound
    and validated content-safe (:func:`_assert_content_safe`, no unknown-key rejection, task §20).
    Recomputes NO science; never serializes an arbitrary object or a raw exception.
    """
    if status not in _ALL_STATUSES:
        raise ResultRetentionError(f"unknown artifact status {status!r}")

    allow = ALLOWED_FAILURE_KEYS if status in _FAILURE_STATUSES else ALLOWED_TOP_LEVEL_PAYLOAD_KEYS
    result_section: Dict[str, object] = {}
    for key in allow:
        if key in payload:
            result_section[key] = payload[key]

    # PN02D-RH-IR3-M1 PRIMARY content-safety = PATH-SENSITIVE structural schema. The nested
    # ``scientific_result`` tree is validated by :func:`_assert_block` starting from the
    # ``scientific_result`` schema, so each governed block validates ONLY its own keys and a key
    # valid in one block cannot be relocated into another (wrong-block key FAILS CLOSED). This
    # replaces the earlier flat global-union model. ``index_metrics`` / ``index_membership_records``
    # and the failure diagnostic dicts are strict schema-bound (dataclass ``as_dict`` / bounded
    # safe-dict builders, task §20), so they get the content-safe checks WITHOUT unknown-key
    # rejection.
    for key, value in result_section.items():
        if key == "scientific_result":
            if not isinstance(value, dict):
                raise ResultArtifactPersistenceError("scientific_result_not_a_dict")
            _assert_block(
                value,
                allowed=_BLOCK_SCHEMAS["scientific_result"],
                path="result.scientific_result",
                block_name="scientific_result",
            )
        else:
            _assert_content_safe(value, path=f"result.{key}", allowed_keys=None)

    envelope: Dict[str, object] = {
        "artifact_schema_version": RESULT_ARTIFACT_SCHEMA_VERSION,
        "status": status,
        "command": command,
        "identity": identity.as_dict(),
        "claimed": bool(claimed),
        "provider_traffic": _coerce_int(payload.get("provider_traffic", 0)),
        "technical_status": payload.get("technical_status"),
        "result_state": payload.get("result"),
        "result": result_section,
        "written_at_utc": datetime.now(timezone.utc).isoformat(),
    }
    envelope["artifact_sha256"] = _compute_sha256(envelope)
    return envelope


# --------------------------------------------------------------------------------------
# Validation (identity / completeness / hash) — used on read-after-write and by tests.
# --------------------------------------------------------------------------------------
def _dig(obj: object, path: Sequence[str]) -> Tuple[bool, object]:
    cur: object = obj
    for key in path:
        if not isinstance(cur, dict) or key not in cur:
            return False, None
        cur = cur[key]
    return True, cur


def verify_artifact(
    artifact: Dict[str, object],
    *,
    expected_identity: Optional[ResultArtifactIdentity] = None,
    mandatory_completed_paths: Tuple[Tuple[str, ...], ...] = MANDATORY_B3_COMPLETED_PATHS,
) -> None:
    """Fail closed unless the artifact is schema-valid, hash-consistent, identity-matched
    and (for COMPLETED) complete. Raises :class:`ResultArtifactPersistenceError` otherwise.
    """
    if not isinstance(artifact, dict):
        raise ResultArtifactPersistenceError("not_a_json_object")
    if artifact.get("artifact_schema_version") != RESULT_ARTIFACT_SCHEMA_VERSION:
        raise ResultArtifactPersistenceError("schema_version_mismatch")
    status = artifact.get("status")
    if status not in _ALL_STATUSES:
        raise ResultArtifactPersistenceError("unknown_status")

    stored_hash = artifact.get("artifact_sha256")
    if not isinstance(stored_hash, str) or stored_hash != _compute_sha256(artifact):
        raise ResultArtifactPersistenceError("hash_mismatch")

    if expected_identity is not None:
        if artifact.get("identity") != expected_identity.as_dict():
            raise ResultArtifactPersistenceError("identity_mismatch")

    if status == STATUS_COMPLETED:
        result_section = artifact.get("result")
        if not isinstance(result_section, dict):
            raise ResultArtifactPersistenceError("missing_result_section")
        for dotted in mandatory_completed_paths:
            found, value = _dig(result_section, dotted)
            if not found or value is None:
                raise ResultArtifactPersistenceError(
                    "incomplete_completed_artifact", ".".join(dotted)
                )
        # PN02D-RH-IR4-H1: mandatory governed BLOCK paths must be dicts (not a scalar standing in
        # for the block), so a COMPLETED artifact cannot finalize having silently lost the block's
        # governed content to type confusion.
        for dotted in _MANDATORY_B3_DICT_PATHS:
            found, value = _dig(result_section, dotted)
            if not found or not isinstance(value, dict):
                raise ResultArtifactPersistenceError(
                    "mandatory_block_not_a_dict", ".".join(dotted)
                )


def read_result_artifact(path: Path) -> Dict[str, object]:
    """Read + parse a finalized artifact file (provider-free)."""
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ResultArtifactPersistenceError("not_a_json_object")
    return raw


# --------------------------------------------------------------------------------------
# Atomic write core.
# --------------------------------------------------------------------------------------
def _atomic_write_and_verify(
    *,
    run_id: str,
    run_dir: Path,
    final_path: Path,
    envelope: Dict[str, object],
    expected_identity: Optional[ResultArtifactIdentity],
    mandatory_completed_paths: Tuple[Tuple[str, ...], ...],
    require_completeness: bool,
) -> str:
    """Serialize → temp write → flush → fsync → close → reopen → parse → verify → ATOMIC
    create-if-absent publication via ``os.link``. Returns the finalized sha256.

    PN02D-RH-IR1-H2 (race-safe no-overwrite): the final artifact is published with
    ``os.link(tmp, final)`` — a single atomic create-that-fails-if-the-name-exists operation
    (POSIX ``link(2)`` / Windows ``CreateHardLinkW``). It is NOT ``os.replace`` (which overwrites)
    and NOT a TOCTOU ``exists()``-then-rename. Two concurrent finalizers for the same run_id
    therefore both verify their temps, but only ONE ``os.link`` succeeds; the loser gets
    ``FileExistsError`` → :class:`ResultArtifactAlreadyExistsError` (fail closed), and the winner's
    finalized artifact is never overwritten. Because ``final`` is created as a link to the
    already-fully-written+verified temp, a reader never observes a partial ``final``. A partial or
    unverified temp is never linked into place; on any failure the temp is left for forensics.
    """
    canonical = _canonical_bytes(envelope)

    # Same-directory temp so the hard link targets the same filesystem.
    fd, tmp_name = tempfile.mkstemp(
        prefix="result.", suffix=_TMP_SUFFIX, dir=str(run_dir)
    )
    tmp_path = Path(tmp_name)
    linked = False
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(canonical)
            fh.flush()
            os.fsync(fh.fileno())  # durability of the bytes before publication

        # Read-after-write BEFORE publishing: parse + verify the ON-DISK bytes.
        on_disk = read_result_artifact(tmp_path)
        completed_paths = mandatory_completed_paths if require_completeness else ()
        verify_artifact(
            on_disk,
            expected_identity=expected_identity,
            mandatory_completed_paths=completed_paths,
        )

        # ATOMIC create-if-absent publication. os.link fails closed if ``final`` already exists.
        try:
            os.link(tmp_path, final_path)
            linked = True
        except FileExistsError:
            raise ResultArtifactAlreadyExistsError(run_id, final_path)
    finally:
        # The temp is only unlinked once its content is safely published as ``final``; on any
        # failure (verification or a lost race) it is LEFT in place for forensic inspection.
        if linked:
            try:
                tmp_path.unlink()
            except OSError:  # pragma: no cover - best effort
                pass

    # Best-effort directory durability of the publication (POSIX; no-op/unsupported on Windows).
    try:
        dir_fd = os.open(str(run_dir), os.O_RDONLY)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
    except (OSError, AttributeError):  # pragma: no cover - platform dependent
        pass

    stored_hash = envelope.get("artifact_sha256")
    return stored_hash if isinstance(stored_hash, str) else ""


def _prepare_run_dir(run_id: str, base_dir: Optional[str]) -> Path:
    run_dir = run_artifact_dir(run_id, base_dir=base_dir)
    run_dir.mkdir(parents=True, exist_ok=True)
    # Best-effort restrictive permissions on POSIX; default ACL on Windows (chmod is a no-op
    # for the owner-bits model there). Never broadens access.
    try:  # pragma: no cover - platform dependent
        os.chmod(run_dir, 0o700)
    except OSError:
        pass
    return run_dir


# --------------------------------------------------------------------------------------
# Public finalization + failure capture.
# --------------------------------------------------------------------------------------
def finalize_result_artifact(
    *,
    status: str,
    command: str,
    identity: ResultArtifactIdentity,
    payload: Dict[str, object],
    base_dir: Optional[str] = None,
    mandatory_completed_paths: Tuple[Tuple[str, ...], ...] = MANDATORY_B3_COMPLETED_PATHS,
) -> WrittenArtifact:
    """Durably finalize a governed result as the SYSTEM OF RECORD (task §15/§17/§29/§30).

    ``status`` is :data:`STATUS_COMPLETED` (a claimed run that produced a complete result)
    or :data:`STATUS_POSTCLAIM_FAILURE` (a claimed run whose execution failed). Both are
    post-claim, so ``claimed=True``. Completeness is enforced ONLY for COMPLETED.

    Fails closed (no overwrite) if a finalized artifact already exists. A ``_SUCCESS`` marker
    is written ONLY after a COMPLETED artifact is fully finalized + verified, so a caller may
    emit its success marker only after this returns.
    """
    if status not in (STATUS_COMPLETED, STATUS_POSTCLAIM_FAILURE):
        raise ResultRetentionError(
            f"finalize_result_artifact accepts COMPLETED/POSTCLAIM_FAILURE, got {status!r}"
        )
    final_path = _final_path(identity.run_id, base_dir)
    if final_path.exists() or _success_marker_path(identity.run_id, base_dir).exists():
        raise ResultArtifactAlreadyExistsError(identity.run_id, final_path)

    envelope = build_content_safe_artifact(
        status=status, command=command, identity=identity, payload=payload, claimed=True
    )
    run_dir = _prepare_run_dir(identity.run_id, base_dir)
    sha = _atomic_write_and_verify(
        run_id=identity.run_id,
        run_dir=run_dir,
        final_path=final_path,
        envelope=envelope,
        expected_identity=identity,
        mandatory_completed_paths=mandatory_completed_paths,
        require_completeness=(status == STATUS_COMPLETED),
    )

    # PN02D-RH-IR1-H2/§12: the ``_SUCCESS`` marker is written ONLY by the writer that WON
    # publication (a losing concurrent writer raised at os.link above and never reaches here) and
    # ONLY after the final artifact was atomically published + verified — so it can never certify
    # an unvalidated result or a result the losing writer did not establish. Created with
    # exclusive-create; a pre-existing marker is benign (the winner's own artifact stands).
    marker: Optional[Path] = None
    if status == STATUS_COMPLETED:
        marker = _success_marker_path(identity.run_id, base_dir)
        try:
            mfd = os.open(str(marker), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.close(mfd)
        except FileExistsError:  # pragma: no cover - marker already present
            pass
    return WrittenArtifact(path=final_path, sha256=sha, status=status, success_marker=marker)


def write_failure_artifact(
    *,
    status: str,
    command: str,
    identity: ResultArtifactIdentity,
    payload: Dict[str, object],
    claimed: bool,
    base_dir: Optional[str] = None,
) -> WrittenArtifact:
    """Durably capture a content-safe failure artifact (task §33/§34).

    ``status`` is POSTCLAIM_FAILURE (claimed=True) or PRECLAIM_FAILURE (claimed=False;
    provider_traffic 0). No completeness requirement. Never overwrites an existing final
    artifact. Best-effort: callers wrap this so a retention failure never masks the original
    failure classification.
    """
    if status not in _FAILURE_STATUSES:
        raise ResultRetentionError(
            f"write_failure_artifact accepts failure statuses, got {status!r}"
        )
    final_path = _final_path(identity.run_id, base_dir)
    if final_path.exists():
        raise ResultArtifactAlreadyExistsError(identity.run_id, final_path)

    safe_payload = dict(payload)
    if status == STATUS_PRECLAIM_FAILURE:
        # A preclaim failure never claimed the grant and never contacted a provider.
        safe_payload["provider_traffic"] = 0
    envelope = build_content_safe_artifact(
        status=status, command=command, identity=identity, payload=safe_payload, claimed=claimed
    )
    run_dir = _prepare_run_dir(identity.run_id, base_dir)
    sha = _atomic_write_and_verify(
        run_id=identity.run_id,
        run_dir=run_dir,
        final_path=final_path,
        envelope=envelope,
        expected_identity=identity,
        mandatory_completed_paths=(),
        require_completeness=False,
    )
    return WrittenArtifact(path=final_path, sha256=sha, status=status, success_marker=None)


# --------------------------------------------------------------------------------------
# Preclaim readiness guard (task §12/§13/§14) — real storage probe, provider-free.
# --------------------------------------------------------------------------------------
def check_result_retention_readiness(
    run_id: str, *, base_dir: Optional[str] = None
) -> RetentionReadinessReport:
    """Real storage probe (NOT a boolean-only pretend check): validate run_id → resolve root →
    run-id collision check → directory creation → temp create+write+flush+fsync → read-back →
    atomic create-if-absent (os.link) compatibility → cleanup. No provider I/O, no ledger, no
    secrets. Probes the EXACT ``os.link`` publication primitive the real finalize uses.
    """
    passed: List[str] = []
    try:
        # PN02D-RH-IR1-H1: a run_id that is not a safe single segment fails readiness closed
        # (before any claim), never a crash.
        validate_run_id(run_id)
        passed.append("run_id_valid")

        base = _resolve_base_dir(base_dir)
        passed.append("root_resolved")

        final_path = _final_path(run_id, base_dir)
        if final_path.exists() or _success_marker_path(run_id, base_dir).exists():
            return RetentionReadinessReport(
                status="FAIL",
                run_id=run_id,
                checks_passed=tuple(passed),
                failed_check="run_id_collision",
                error_type="ResultArtifactAlreadyExistsError",
            )
        passed.append("no_run_id_collision")

        base.mkdir(parents=True, exist_ok=True)
        run_dir = run_artifact_dir(run_id, base_dir=base_dir)
        run_dir.mkdir(parents=True, exist_ok=True)
        passed.append("directory_creatable")

        # Probe the EXACT durability path a real finalize uses: temp create → write → flush →
        # fsync → close → read back → atomic create-if-absent (os.link) → remove. Throwaway data.
        fd, tmp_name = tempfile.mkstemp(prefix="_probe.", suffix=_TMP_SUFFIX, dir=str(run_dir))
        tmp_path = Path(tmp_name)
        link_target = run_dir / f"_probe_final.{os.getpid()}{_TMP_SUFFIX}"
        try:
            with os.fdopen(fd, "wb") as fh:
                fh.write(b'{"probe": true}\n')
                fh.flush()
                os.fsync(fh.fileno())
            passed.append("temp_write_flush_fsync")
            _ = tmp_path.read_bytes()
            passed.append("read_back")
            os.link(tmp_path, link_target)
            passed.append("atomic_link_create_compatible")
        finally:
            for leftover in (tmp_path, link_target):
                try:
                    if leftover.exists():
                        leftover.unlink()
                except OSError:
                    pass
    except ResultRetentionError:
        return RetentionReadinessReport(
            status="FAIL",
            run_id=run_id,
            checks_passed=tuple(passed),
            failed_check="run_id_invalid",
            error_type="ResultRetentionError",
        )
    except OSError as exc:
        return RetentionReadinessReport(
            status="FAIL",
            run_id=run_id,
            checks_passed=tuple(passed),
            failed_check=passed[-1] if passed else "root_resolved",
            error_type=type(exc).__name__,
        )
    return RetentionReadinessReport(status="OK", run_id=run_id, checks_passed=tuple(passed))


def assert_result_retention_readiness(
    run_id: str, *, base_dir: Optional[str] = None
) -> RetentionReadinessReport:
    """Fail-closed wrapper: raise :class:`LiveResultRetentionReadinessError` unless OK.

    Wired into ``RealB1Driver.run`` AFTER the runtime import-readiness guard and BEFORE the
    one-shot ledger claim, so an unusable result store fails closed before the grant is burnt
    (claim count 0, provider calls 0, grant not consumed).
    """
    report = check_result_retention_readiness(run_id, base_dir=base_dir)
    if report.status != "OK":
        raise LiveResultRetentionReadinessError(report)
    return report


# --------------------------------------------------------------------------------------
# Crash / recovery inspection (task §38) — provider-free.
# --------------------------------------------------------------------------------------
def inspect_run_artifact_state(
    run_id: str, *, base_dir: Optional[str] = None
) -> RecoveryState:
    """Deterministic, provider-free inspection of the per-run directory state."""
    run_dir = run_artifact_dir(run_id, base_dir=base_dir)
    if not run_dir.exists():
        return RecoveryState(
            run_id=run_id,
            state=RECOVERY_ABSENT,
            final_present=False,
            success_marker_present=False,
            temp_present=False,
            dir_path=str(run_dir),
        )
    final_present = _final_path(run_id, base_dir).exists()
    marker_present = _success_marker_path(run_id, base_dir).exists()
    temp_present = any(p.name.endswith(_TMP_SUFFIX) for p in run_dir.iterdir())

    if final_present and marker_present:
        state = RECOVERY_FINAL_COMPLETE
    elif final_present:
        state = RECOVERY_FINAL_WITHOUT_SUCCESS
    elif temp_present:
        state = RECOVERY_TEMP_ONLY
    else:
        state = RECOVERY_EMPTY
    return RecoveryState(
        run_id=run_id,
        state=state,
        final_present=final_present,
        success_marker_present=marker_present,
        temp_present=temp_present,
        dir_path=str(run_dir),
    )


__all__ = [
    "RESULT_ARTIFACT_SCHEMA_VERSION",
    "STATUS_COMPLETED",
    "STATUS_POSTCLAIM_FAILURE",
    "STATUS_PRECLAIM_FAILURE",
    "RECOVERY_ABSENT",
    "RECOVERY_EMPTY",
    "RECOVERY_TEMP_ONLY",
    "RECOVERY_FINAL_WITHOUT_SUCCESS",
    "RECOVERY_FINAL_COMPLETE",
    "ALLOWED_TOP_LEVEL_PAYLOAD_KEYS",
    "ALLOWED_FAILURE_KEYS",
    "MANDATORY_B3_COMPLETED_PATHS",
    "MAX_SAFE_STRING_LEN",
    "ResultRetentionError",
    "LiveResultRetentionReadinessError",
    "ResultArtifactAlreadyExistsError",
    "ResultArtifactPersistenceError",
    "ResultArtifactIdentity",
    "RetentionReadinessReport",
    "WrittenArtifact",
    "ResultRetentionConfig",
    "RecoveryState",
    "default_result_artifact_dir",
    "validate_run_id",
    "run_artifact_dir",
    "ALLOWED_SCIENTIFIC_RESULT_KEYS",
    "ALLOWED_NESTED_KEYS",
    "build_content_safe_artifact",
    "verify_artifact",
    "read_result_artifact",
    "finalize_result_artifact",
    "write_failure_artifact",
    "check_result_retention_readiness",
    "assert_result_retention_readiness",
    "inspect_run_artifact_state",
]
