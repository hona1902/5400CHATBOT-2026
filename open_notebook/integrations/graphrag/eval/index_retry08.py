"""GraphRAG-08 full-run index-failure classification (EVALUATION-ONLY).

Nothing in production imports this. It classifies a single Source's GraphRAG
indexing failure as TRANSIENT (eligible for ONE bounded retry) or not, for the
authorized full-run harness. Fail-closed by design: anything not positively shown
to be transient is treated as NON-retryable.

Two failure surfaces:
  * SUBMIT-time — ``service.index_source`` raised. Classified purely on the typed
    GraphRAG exception: unavailable/timeout (transport), 5xx, and 409-during-
    reindex are transient; every other typed error (4xx request, auth/config,
    schema/protocol, validation) and any unknown exception are NON-retryable.
  * TRACK-time — the document reached LightRAG ``DocStatus.FAILED``. The production
    client surface exposes no cause, so this reads the sidecar's raw track-status
    error text through an eval-only direct HTTP call (mirroring the GD seam), matches
    it against a transient-marker allowlist, and returns ONLY a coarse category —
    the raw error text never escapes this module (content containment). If no error
    text is available or it does not clearly indicate a transient cause, the verdict
    is NON-retryable (fail closed, task §4).

This module performs NO retry itself and starts nothing; it only classifies.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Dict, FrozenSet, Optional, Tuple

from open_notebook.integrations.graphrag.models import (
    GraphRAGConflictError,
    GraphRAGRequestError,
    GraphRAGServerError,
    GraphRAGUnavailableError,
)

CATEGORY_TRANSIENT = "TRANSIENT"
CATEGORY_NON_RETRYABLE = "NON_RETRYABLE"
CATEGORY_UNKNOWN = "UNKNOWN"

# Markers that clearly indicate a transient provider/transport/upstream failure.
# Deliberately specific: 5xx only in an explicit HTTP/status context (never a bare
# 3-digit token), and phrases anchored enough that a deterministic failure whose
# text incidentally contains a number/word does not read as transient (fail-closed
# bias; review LOW-1).
_TRANSIENT_MARKERS = re.compile(
    r"(429|rate[ _-]?limit|too many requests|"
    r"time ?out|timed out|"
    r"temporarily unavailable|temporarily down|"
    r"connection (?:reset|aborted|refused)|"
    r"http[ /]?5\d\d|status(?: ?code)?[ :=]+5\d\d|"
    r"internal server error|bad gateway|gateway timeout|service unavailable|"
    r"overloaded|server is busy|please try again)",
    re.IGNORECASE,
)


def classify_submit_exception(exc: BaseException) -> bool:
    """True iff a submit-time exception is a CLEARLY transient transport failure.

    Only ``GraphRAGUnavailableError`` (timeout/connection), ``GraphRAGServerError``
    (5xx), and ``GraphRAGConflictError`` (409, transient during delete-then-insert
    reindex) are retryable. Everything else — 4xx request errors, auth/config,
    schema/protocol, validation, and any non-GraphRAG exception — is NON-retryable
    (fail closed).
    """
    return isinstance(
        exc, (GraphRAGUnavailableError, GraphRAGServerError, GraphRAGConflictError)
    )


def is_transient_reason(error_text: Optional[str]) -> bool:
    """True iff ``error_text`` positively matches a transient marker.

    Empty / absent / non-matching text -> False (fail closed): 'it failed once' is
    NOT sufficient evidence of transience (task §4).
    """
    if not error_text:
        return False
    return bool(_TRANSIENT_MARKERS.search(error_text))


# ----------------------------------------------------------------------------
# GraphRAG-08B content-safe failure diagnostics (OBSERVABILITY ONLY).
# These NEVER change the retry decision (that stays classify_submit_exception /
# is_transient_reason / classify_failed_track). They read the raw error text
# transiently, derive coarse content-free categories, and DISCARD the raw text.
# ----------------------------------------------------------------------------

# Coarse transient classes, mapped from the SAME frozen allowlist tokens. This is
# diagnostic labelling only; the overall transient DECISION remains the combined
# ``is_transient_reason`` regex (a test asserts any class match implies that
# decision is True, so the mapping cannot diverge from the frozen semantics).
_TRANSIENT_CLASS_PATTERNS: Dict[str, "re.Pattern[str]"] = {
    "RATE_LIMIT": re.compile(r"(429|rate[ _-]?limit|too many requests)", re.I),
    "TIMEOUT": re.compile(r"(time ?out|timed out|gateway timeout)", re.I),
    "TEMPORARILY_UNAVAILABLE": re.compile(
        r"(temporarily unavailable|temporarily down)", re.I
    ),
    "CONNECTION_RESET": re.compile(r"connection reset", re.I),
    "CONNECTION_ABORTED": re.compile(r"connection (?:aborted|refused)", re.I),
    "HTTP_5XX": re.compile(r"(http[ /]?5\d\d|status(?: ?code)?[ :=]+5\d\d)", re.I),
    "BAD_GATEWAY": re.compile(r"bad gateway", re.I),
    "SERVICE_UNAVAILABLE": re.compile(r"service unavailable", re.I),
    "INTERNAL_SERVER_ERROR": re.compile(r"internal server error", re.I),
    "OVERLOADED": re.compile(r"(overloaded|server is busy)", re.I),
    "TRY_AGAIN": re.compile(r"please try again", re.I),
}

# Coarse NON-transient classes (diagnostic hints only; never affect the decision).
_NON_TRANSIENT_CLASS_PATTERNS: Dict[str, "re.Pattern[str]"] = {
    "AUTH": re.compile(r"(unauthori[sz]ed|authentication|invalid api key|401|403)", re.I),
    "REQUEST_4XX": re.compile(r"(http[ /]?4\d\d|status(?: ?code)?[ :=]+4\d\d|400|422)", re.I),
    "CONTENT_PARSE": re.compile(r"(parse|malformed|could not decode|invalid json)", re.I),
    "SCHEMA": re.compile(r"(schema|unexpected field|missing (?:field|key))", re.I),
    "VALIDATION": re.compile(r"(validation|invalid (?:input|value|argument))", re.I),
}


class ReasonCode:
    TYPED_TRANSIENT_EXCEPTION = "TYPED_TRANSIENT_EXCEPTION"
    TYPED_NON_RETRYABLE_EXCEPTION = "TYPED_NON_RETRYABLE_EXCEPTION"
    UNKNOWN_EXCEPTION_FAIL_CLOSED = "UNKNOWN_EXCEPTION_FAIL_CLOSED"
    TRACK_TRANSIENT_ALLOWLIST_MATCH = "TRACK_TRANSIENT_ALLOWLIST_MATCH"
    TRACK_TEXT_PRESENT_NO_ALLOWLIST_MATCH = "TRACK_TEXT_PRESENT_NO_ALLOWLIST_MATCH"
    TRACK_TEXT_ABSENT = "TRACK_TEXT_ABSENT"
    TRACK_TEXT_UNREADABLE = "TRACK_TEXT_UNREADABLE"


_LENGTH_BUCKETS = (
    (0, "EMPTY"),
    (64, "1_64"),
    (128, "65_128"),
    (256, "129_256"),
    (512, "257_512"),
    (1024, "513_1024"),
)


def _length_bucket(n: int) -> str:
    if n <= 0:
        return "EMPTY"
    for upper, label in _LENGTH_BUCKETS[1:]:
        if n <= upper:
            return label
    return "GT_1024"


def transient_match_classes(error_text: Optional[str]) -> FrozenSet[str]:
    """Coarse transient classes matched (content-safe). Never returns raw text."""
    if not error_text:
        return frozenset()
    return frozenset(
        name for name, pat in _TRANSIENT_CLASS_PATTERNS.items() if pat.search(error_text)
    )


def non_transient_match_classes(error_text: Optional[str]) -> FrozenSet[str]:
    if not error_text:
        return frozenset()
    return frozenset(
        name for name, pat in _NON_TRANSIENT_CLASS_PATTERNS.items() if pat.search(error_text)
    )


@dataclass(frozen=True)
class FailureDiagnostic:
    """Content-free description of one index failure. No raw error text ever."""

    failure_surface: str  # "SUBMIT" | "TRACK"
    attempt_number: int
    classification: str  # CATEGORY_TRANSIENT | CATEGORY_NON_RETRYABLE | CATEGORY_UNKNOWN
    classification_reason_code: str
    retry_allowed: bool
    retry_consumed: bool
    error_text_present: bool
    error_text_length_bucket: str
    matched_transient_classes: Tuple[str, ...]
    matched_non_transient_classes: Tuple[str, ...]
    http_status_class: Optional[str]  # "4XX" | "5XX" | None
    exception_type: Optional[str]
    logical_source_id: Optional[str] = None
    canonical_source_id: Optional[str] = None

    def as_dict(self) -> Dict[str, object]:
        return {
            "failure_surface": self.failure_surface,
            "attempt_number": self.attempt_number,
            "classification": self.classification,
            "classification_reason_code": self.classification_reason_code,
            "retry_allowed": self.retry_allowed,
            "retry_consumed": self.retry_consumed,
            "error_text_present": self.error_text_present,
            "error_text_length_bucket": self.error_text_length_bucket,
            "matched_transient_classes": sorted(self.matched_transient_classes),
            "matched_non_transient_classes": sorted(self.matched_non_transient_classes),
            "http_status_class": self.http_status_class,
            "exception_type": self.exception_type,
            "logical_source_id": self.logical_source_id,
            "canonical_source_id": self.canonical_source_id,
        }


def diagnose_submit_exception(
    exc: BaseException,
    *,
    attempt_number: int,
    canonical_source_id: Optional[str] = None,
    logical_source_id: Optional[str] = None,
) -> FailureDiagnostic:
    """Content-safe diagnostic for a submit-time failure. Decision == classify_submit_exception."""
    retry_ok = classify_submit_exception(exc)
    if retry_ok:
        reason = ReasonCode.TYPED_TRANSIENT_EXCEPTION
        classification = CATEGORY_TRANSIENT
    elif isinstance(
        exc,
        (GraphRAGRequestError, GraphRAGUnavailableError, GraphRAGServerError,
         GraphRAGConflictError),
    ):
        reason = ReasonCode.TYPED_NON_RETRYABLE_EXCEPTION
        classification = CATEGORY_NON_RETRYABLE
    else:
        reason = ReasonCode.UNKNOWN_EXCEPTION_FAIL_CLOSED
        classification = CATEGORY_UNKNOWN
    http_class = None
    if isinstance(exc, GraphRAGServerError):
        http_class = "5XX"
    elif isinstance(exc, GraphRAGRequestError):
        http_class = "4XX"
    return FailureDiagnostic(
        failure_surface="SUBMIT",
        attempt_number=attempt_number,
        classification=classification,
        classification_reason_code=reason,
        retry_allowed=retry_ok,
        retry_consumed=attempt_number >= 2,
        error_text_present=False,
        error_text_length_bucket="EMPTY",
        matched_transient_classes=(),
        matched_non_transient_classes=(),
        http_status_class=http_class,
        exception_type=type(exc).__name__,
        logical_source_id=logical_source_id,
        canonical_source_id=canonical_source_id,
    )


async def diagnose_failed_track(
    config,
    track_id: str,
    *,
    attempt_number: int,
    canonical_source_id: Optional[str] = None,
    logical_source_id: Optional[str] = None,
    transport=None,
) -> FailureDiagnostic:
    """Content-safe diagnostic for a track-time DocStatus.FAILED. Decision ==
    classify_failed_track (TRANSIENT iff is_transient_reason on a present text)."""
    presence, text = await _fetch_failed_reason_ex(config, track_id, transport=transport)
    if presence == "PRESENT":
        transient = is_transient_reason(text)
        classification = CATEGORY_TRANSIENT if transient else CATEGORY_NON_RETRYABLE
        reason = (
            ReasonCode.TRACK_TRANSIENT_ALLOWLIST_MATCH
            if transient
            else ReasonCode.TRACK_TEXT_PRESENT_NO_ALLOWLIST_MATCH
        )
        length_bucket = _length_bucket(len(text or ""))
        t_classes = tuple(transient_match_classes(text))
        nt_classes = tuple(non_transient_match_classes(text))
        present = True
    elif presence == "ABSENT":
        classification, reason = CATEGORY_UNKNOWN, ReasonCode.TRACK_TEXT_ABSENT
        length_bucket, t_classes, nt_classes, present = "EMPTY", (), (), False
    else:  # UNREADABLE
        classification, reason = CATEGORY_UNKNOWN, ReasonCode.TRACK_TEXT_UNREADABLE
        length_bucket, t_classes, nt_classes, present = "EMPTY", (), (), False
    return FailureDiagnostic(
        failure_surface="TRACK",
        attempt_number=attempt_number,
        classification=classification,
        classification_reason_code=reason,
        retry_allowed=(classification == CATEGORY_TRANSIENT),
        retry_consumed=attempt_number >= 2,
        error_text_present=present,
        error_text_length_bucket=length_bucket,
        matched_transient_classes=t_classes,
        matched_non_transient_classes=nt_classes,
        http_status_class=None,
        exception_type=None,
        logical_source_id=logical_source_id,
        canonical_source_id=canonical_source_id,
    )


async def classify_failed_track(config, track_id: str) -> str:
    """Classify a TRACK-time ``DocStatus.FAILED`` as TRANSIENT / NON_RETRYABLE / UNKNOWN.

    Reads the sidecar's raw ``/documents/track_status`` error text via an eval-only
    HTTP call, classifies it, and DISCARDS the raw text (only a coarse category is
    returned). Returns UNKNOWN when the cause cannot be read at all (network/parse) —
    the caller treats UNKNOWN and NON_RETRYABLE identically (no retry).
    """
    # Decision is UNCHANGED: PRESENT-transient -> TRANSIENT; PRESENT-non-transient ->
    # NON_RETRYABLE; ABSENT or UNREADABLE -> UNKNOWN. (config None -> UNKNOWN.)
    presence, text = await _fetch_failed_reason_ex(config, track_id)
    if presence == "PRESENT":
        return CATEGORY_TRANSIENT if is_transient_reason(text) else CATEGORY_NON_RETRYABLE
    return CATEGORY_UNKNOWN


async def _fetch_failed_reason_ex(
    config, track_id: str, *, transport=None
) -> Tuple[str, Optional[str]]:
    """Eval-only read of the first FAILED document's error text.

    Returns (presence, text) where presence is 'PRESENT' (a non-empty error text was
    read), 'ABSENT' (the doc had no error field), or 'UNREADABLE' (config missing /
    network / HTTP / parse failure). The raw text is returned ONLY to the in-module
    classifier/diagnostic layer and is never propagated further.
    """
    if config is None:
        return "UNREADABLE", None

    import httpx

    headers = {"Content-Type": "application/json"}
    if getattr(config, "api_key", None):
        headers["X-API-Key"] = config.api_key
    try:
        async with httpx.AsyncClient(
            base_url=config.base_url,
            timeout=config.timeout,
            headers=headers,
            transport=transport,
        ) as client:
            resp = await client.get(f"/documents/track_status/{track_id}")
        if resp.status_code >= 400:
            return "UNREADABLE", None
        data = resp.json()
    except Exception:  # noqa: BLE001 - unreadable cause -> fail closed
        return "UNREADABLE", None
    docs = data.get("documents") if isinstance(data, dict) else None
    if not isinstance(docs, list):
        return "UNREADABLE", None
    saw_failed_doc = False
    for doc in docs:
        if not isinstance(doc, dict):
            continue
        if str(doc.get("status", "")).strip().lower() not in ("failed", "failure"):
            continue
        saw_failed_doc = True
        for key in ("error", "error_msg", "error_message", "message"):
            val = doc.get(key)
            if isinstance(val, str) and val.strip():
                return "PRESENT", val.strip()
    return ("ABSENT" if saw_failed_doc else "UNREADABLE"), None


__all__ = [
    "CATEGORY_TRANSIENT",
    "CATEGORY_NON_RETRYABLE",
    "CATEGORY_UNKNOWN",
    "classify_submit_exception",
    "is_transient_reason",
    "classify_failed_track",
    "transient_match_classes",
    "non_transient_match_classes",
    "FailureDiagnostic",
    "ReasonCode",
    "diagnose_submit_exception",
    "diagnose_failed_track",
]
