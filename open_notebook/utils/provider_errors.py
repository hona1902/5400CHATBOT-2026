"""Sanitized, safe-by-construction provider-error diagnostics (PN02D-B1-EW5).

GraphRAG-PN02D-B1 EXEC #6 failed at the first live corpus embedding with only
``error_type = RuntimeError`` reaching the content-safe CLI payload — too lossy to tell
a credential/auth failure (A) from an endpoint/capability failure (B). EF2 confirmed the
local embedding path is sound and the failure is the live provider call.

This module produces a NARROW, allowlisted diagnostic from a provider-bound exception so
a later authorized run can distinguish provider failure FAMILIES without ever exposing a
secret. It is **safe by construction**: it extracts ONLY known-safe structured attributes
(exception type, a structured HTTP status attribute, a strictly-shaped error code) and
maps them to a fixed vocabulary. It NEVER serializes ``str(exc)``/``repr(exc)``, a
response body, request headers, an Authorization/Bearer value, or an API key. A regex
secret-pattern check exists only as defense-in-depth on the one free-ish field
(error code), never as the primary boundary.

Scope note (EW5): HTTP status is taken from STRUCTURED attributes only (never parsed from
exception text). The pinned esperanto ``OpenRouterEmbeddingModel`` would flatten an HTTP
error into a bare ``RuntimeError`` with no status attribute; the repository-owned safe
OpenRouter boundary (``open_notebook.ai.safe_openrouter_embedding.SafeOpenRouterEmbeddingModel``,
wired via ``ModelManager.get_model``) fixes that by raising ``ProviderEmbeddingHTTPError`` —
which carries a structured ``status_code`` — BEFORE that flattening, so the canonical B1
embedding path yields the real HTTP status (401 vs 404 distinct). A truly opaque exception
with no structured status still classifies as ``unknown_provider_error`` /
``provider_http_status = None`` — honest, not guessed.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

# --- Fixed, safe provider-error class vocabulary (no dynamic content) --------------- #
AUTHENTICATION_ERROR = "authentication_error"
AUTHORIZATION_ERROR = "authorization_error"
BAD_REQUEST = "bad_request"
NOT_FOUND = "not_found"
RATE_LIMIT = "rate_limit"
TIMEOUT = "timeout"
NETWORK_ERROR = "network_error"
SERVER_ERROR = "server_error"
PROVIDER_RESPONSE_ERROR = "provider_response_error"
UNKNOWN_PROVIDER_ERROR = "unknown_provider_error"

# --- Retryability (OBSERVED classification for reporting; NEVER changes runtime retry) - #
RETRYABLE = "retryable"
NON_RETRYABLE = "non_retryable"
RETRYABILITY_UNKNOWN = "unknown"

# --- Request-reached tri-state ------------------------------------------------------ #
REACHED_YES = "yes"
REACHED_NO = "no"
REACHED_UNKNOWN = "unknown"

#: Attribute name under which a diagnostic is attached to a raised exception.
DIAGNOSTIC_ATTR = "_provider_error_diagnostic"

#: A safe provider error-code shape: a short ASCII token (letters/digits/._-), bounded
#: length, NO whitespace/URL/credential material. Anything else is dropped to None.
_SAFE_CODE_RE = re.compile(r"^[A-Za-z0-9._:-]{1,64}$")

#: Defense-in-depth ONLY (never the primary safety boundary, §19): reject a candidate
#: error code that looks like it carries actual secret/credential MATERIAL. Deliberately
#: NARROW so a legitimate provider error code (e.g. ``invalid_api_key`` — a description, not
#: a credential) is retained (§28), while real tokens are dropped.
_SECRET_PATTERNS = (
    re.compile(r"sk-or-", re.IGNORECASE),  # OpenRouter key prefix
    re.compile(r"sk-[A-Za-z0-9]{6,}", re.IGNORECASE),  # OpenAI-style key token
    re.compile(r"bearer\b", re.IGNORECASE),  # auth scheme keyword
    re.compile(r"[A-Za-z0-9_-]{40,}"),  # long credential-like token
)


def _looks_like_secret(value: str) -> bool:
    return any(p.search(value) for p in _SECRET_PATTERNS)


def _safe_error_code(value: object) -> Optional[str]:
    """Return a code only if it is a strictly-shaped, non-secret ASCII token, else None."""
    if not isinstance(value, str):
        return None
    v = value.strip()
    if not v or not _SAFE_CODE_RE.match(v) or _looks_like_secret(v):
        return None
    return v


def sanitize_provider_error_code(value: object) -> Optional[str]:
    """Public wrapper: return a provider error code only if it is a safe, bounded token.

    Used by a repository-owned provider boundary to pass a STRUCTURED provider error code
    (e.g. ``invalid_api_key``) through the same allowlist the diagnostic uses — dropping any
    value with spaces/URL/credential material to None. Never accepts a raw message.
    """
    return _safe_error_code(value)


class ProviderEmbeddingHTTPError(RuntimeError):
    """A provider embedding HTTP error carrying STRUCTURED status (PN02D-B1-EW5 remediation #1).

    Raised by a repository-owned provider embedding boundary at the point the HTTP response
    is still available, BEFORE a third-party client would flatten it into a bare RuntimeError
    (losing the status). It exposes a structured integer ``status_code`` (and an optional
    already-sanitized ``code``) so ``classify_provider_error`` can distinguish auth (401/403)
    from endpoint/capability (400/404) etc. from a STRUCTURED attribute — never by parsing a
    message. The message is deliberately generic and secret-free (never the provider body).
    """

    def __init__(self, *, status_code: int, code: Optional[str] = None) -> None:
        # Generic, secret-free message; the provider body/message is NEVER included.
        super().__init__(f"provider embedding HTTP error (status {status_code})")
        self.status_code = int(status_code)
        # ``code`` must already be sanitized by the caller (safe token or None).
        self.code = code


@dataclass(frozen=True)
class ProviderErrorDiagnostic:
    """An immutable, allowlisted, secret-free provider-error diagnostic.

    Every field is a fixed-vocabulary string, a bounded integer, a strictly-shaped token,
    or None. There is deliberately NO field carrying raw exception/response text.
    """

    operation: str
    provider_name: Optional[str]
    provider_error_class: str
    provider_http_status: Optional[int]
    provider_error_code: Optional[str]
    retryability: str
    provider_request_reached: str

    def as_public_dict(self) -> dict:
        return {
            "operation": self.operation,
            "provider_name": self.provider_name,
            "provider_error_class": self.provider_error_class,
            "provider_http_status": self.provider_http_status,
            "provider_error_code": self.provider_error_code,
            "retryability": self.retryability,
            "provider_request_reached": self.provider_request_reached,
        }


def _class_from_status(status: int) -> str:
    if status == 401:
        return AUTHENTICATION_ERROR
    if status == 403:
        return AUTHORIZATION_ERROR
    if status == 404:
        return NOT_FOUND
    if status == 429:
        return RATE_LIMIT
    if 400 <= status < 500:
        return BAD_REQUEST
    if 500 <= status < 600:
        return SERVER_ERROR
    return PROVIDER_RESPONSE_ERROR


def _retryability_for(error_class: str) -> str:
    if error_class in (TIMEOUT, NETWORK_ERROR, RATE_LIMIT, SERVER_ERROR):
        return RETRYABLE
    if error_class in (
        AUTHENTICATION_ERROR,
        AUTHORIZATION_ERROR,
        NOT_FOUND,
        BAD_REQUEST,
    ):
        return NON_RETRYABLE
    return RETRYABILITY_UNKNOWN


def _structured_http_status(exc: BaseException) -> Optional[int]:
    """Extract an HTTP status ONLY from a structured attribute (never from text, §10).

    Checks ``exc.response.status_code`` (httpx-style) and a small allowlist of direct
    integer status attributes. A bool is rejected (bool is an int subclass).
    """
    resp = getattr(exc, "response", None)
    sc = getattr(resp, "status_code", None)
    if isinstance(sc, int) and not isinstance(sc, bool) and 100 <= sc <= 599:
        return sc
    for attr in ("status_code", "status", "http_status"):
        v = getattr(exc, attr, None)
        if isinstance(v, int) and not isinstance(v, bool) and 100 <= v <= 599:
            return v
    return None


def _exc_type_info(exc: BaseException) -> tuple[str, str]:
    t = type(exc)
    return (getattr(t, "__module__", "") or "", getattr(t, "__name__", "") or "")


def _is_httpx(exc: BaseException) -> bool:
    module, _ = _exc_type_info(exc)
    return module.split(".", 1)[0] == "httpx"


def classify_provider_error(
    exc: BaseException,
    *,
    operation: str,
    provider_name: Optional[str] = "openrouter",
) -> ProviderErrorDiagnostic:
    """Classify a provider-bound exception into a safe, allowlisted diagnostic.

    Safe by construction: reads only exception type + structured attributes; emits only
    the fixed vocabulary above. Never reads or emits the exception message/body.
    """
    status = _structured_http_status(exc)
    code = _safe_error_code(getattr(exc, "code", None))
    module, type_name = _exc_type_info(exc)

    if status is not None:
        error_class = _class_from_status(status)
        reached = REACHED_YES  # a status means a response was received
    elif _is_httpx(exc) and "Timeout" in type_name:
        error_class, reached = TIMEOUT, REACHED_YES  # request dispatched, no response
    elif _is_httpx(exc) and ("Connect" in type_name or "Proxy" in type_name):
        error_class, reached = NETWORK_ERROR, REACHED_NO  # never connected
    elif _is_httpx(exc):
        error_class, reached = NETWORK_ERROR, REACHED_UNKNOWN  # other transport error
    else:
        # Opaque provider exception (e.g. a provider adapter that flattened an HTTP error
        # into a bare RuntimeError). No structured signal: classify UNKNOWN and do NOT
        # guess a status or delivery state from the message (§10/§13).
        error_class, reached = UNKNOWN_PROVIDER_ERROR, REACHED_UNKNOWN

    return ProviderErrorDiagnostic(
        operation=operation,
        provider_name=provider_name,
        provider_error_class=error_class,
        provider_http_status=status,
        provider_error_code=code,
        retryability=_retryability_for(error_class),
        provider_request_reached=reached,
    )


def attach_provider_diagnostic(
    err: BaseException, diagnostic: ProviderErrorDiagnostic
) -> BaseException:
    """Attach a diagnostic to an exception (inert; read later by the content-safe CLI).

    Adds no message text and changes no control flow — a caller that never reads the
    attribute is unaffected.
    """
    try:
        setattr(err, DIAGNOSTIC_ATTR, diagnostic)
    except Exception:  # noqa: BLE001 - never let observability wiring break error flow
        pass
    return err


def extract_attached_diagnostic(
    exc: BaseException, *, max_depth: int = 8
) -> Optional[ProviderErrorDiagnostic]:
    """Walk exc + its __cause__/__context__ chain for an attached diagnostic (bounded)."""
    seen: set[int] = set()
    cur: Optional[BaseException] = exc
    depth = 0
    while cur is not None and depth < max_depth and id(cur) not in seen:
        seen.add(id(cur))
        diag = getattr(cur, DIAGNOSTIC_ATTR, None)
        if isinstance(diag, ProviderErrorDiagnostic):
            return diag
        cur = cur.__cause__ or cur.__context__
        depth += 1
    return None


def safe_provider_error_fields(
    exc: BaseException, *, operation: str, provider_name: Optional[str] = "openrouter"
) -> dict:
    """Best safe diagnostic dict for a caught exception.

    Prefers a diagnostic attached at the failure source (which knows the true operation);
    otherwise classifies the most informative exception in the cause chain. Always returns
    an allowlisted, secret-free dict.
    """
    attached = extract_attached_diagnostic(exc)
    if attached is not None:
        return attached.as_public_dict()
    # No attached diagnostic: classify the exception in the chain that carries a structured
    # status if any (prefer the most specific), else the top exception.
    best: Optional[BaseException] = None
    cur: Optional[BaseException] = exc
    seen: set[int] = set()
    depth = 0
    while cur is not None and depth < 8 and id(cur) not in seen:
        seen.add(id(cur))
        if _structured_http_status(cur) is not None:
            best = cur
            break
        if best is None:
            best = cur
        cur = cur.__cause__ or cur.__context__
        depth += 1
    target = best if best is not None else exc
    return classify_provider_error(
        target, operation=operation, provider_name=provider_name
    ).as_public_dict()


__all__ = [
    "AUTHENTICATION_ERROR",
    "AUTHORIZATION_ERROR",
    "BAD_REQUEST",
    "NOT_FOUND",
    "RATE_LIMIT",
    "TIMEOUT",
    "NETWORK_ERROR",
    "SERVER_ERROR",
    "PROVIDER_RESPONSE_ERROR",
    "UNKNOWN_PROVIDER_ERROR",
    "RETRYABLE",
    "NON_RETRYABLE",
    "RETRYABILITY_UNKNOWN",
    "REACHED_YES",
    "REACHED_NO",
    "REACHED_UNKNOWN",
    "DIAGNOSTIC_ATTR",
    "ProviderEmbeddingHTTPError",
    "ProviderErrorDiagnostic",
    "sanitize_provider_error_code",
    "classify_provider_error",
    "attach_provider_diagnostic",
    "extract_attached_diagnostic",
    "safe_provider_error_fields",
]
