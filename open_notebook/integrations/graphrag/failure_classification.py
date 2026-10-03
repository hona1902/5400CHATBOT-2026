"""Domain-first GraphRAG failure classification (GraphRAG-09C).

The shared provider classifier (``open_notebook.utils.provider_errors.classify_provider_error``)
is correct for its intended domain: a genuinely provider-bound exception. Its honest fallback
for an opaque provider-bound exception is ``unknown_provider_error``. The 09C defect was a
CALL-SITE one: the eval/live harness routed *mixed-domain* top-level exceptions (SurrealDB
auth/bootstrap, GraphRAG sidecar/config) through that provider classifier, so non-provider
failures were mislabeled ``unknown_provider_error``.

This module adds a thin, content-safe, DOMAIN-FIRST dispatcher. It classifies by failure
domain BEFORE any provider heuristic:

  1. GraphRAG typed exceptions (``GraphRAGError`` subclasses) -> sidecar / validation /
     feature-disabled tokens.
  2. Local-dependency failures (SurrealDB, ``DatabaseOperationError``, ``AuthenticationError``)
     -> ``local_dependency_error``.
  3. Genuinely provider-bound exceptions (an attached provider diagnostic, an httpx error, or
     a structured HTTP status) -> delegated to ``classify_provider_error`` (unchanged), domain
     ``provider``.
  4. Otherwise -> domain-neutral ``unknown_error`` (NEVER ``unknown_provider_error``).

It is safe by construction: it reads only the exception TYPE and already-safe structured
attributes, and delegates every provider detail to the existing safe provider classifier. It
never reads or emits ``str(exc)``, a response body, headers, or a secret. It changes no runtime
retry policy; retryability here is diagnostic metadata only.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from open_notebook.exceptions import AuthenticationError, DatabaseOperationError
from open_notebook.integrations.graphrag.models import (
    GraphRAGConfigurationError,
    GraphRAGConflictError,
    GraphRAGDisabledError,
    GraphRAGError,
    GraphRAGProtocolError,
    GraphRAGRequestError,
    GraphRAGServerError,
    GraphRAGUnavailableError,
    GraphRAGValidationError,
)
from open_notebook.utils.provider_errors import (
    extract_attached_diagnostic,
    safe_provider_error_fields,
)

# --- Fixed, allowlisted failure domains -------------------------------------------- #
DOMAIN_PROVIDER = "provider"
DOMAIN_SIDECAR = "sidecar"
DOMAIN_LOCAL_DEPENDENCY = "local_dependency"
DOMAIN_VALIDATION = "validation"
DOMAIN_FEATURE_DISABLED = "feature_disabled"
DOMAIN_UNKNOWN = "unknown"

# --- Fixed, allowlisted classification tokens -------------------------------------- #
FEATURE_DISABLED = "feature_disabled"
SIDECAR_CONFIGURATION_ERROR = "sidecar_configuration_error"
SIDECAR_NETWORK_ERROR = "sidecar_network_error"
SIDECAR_SERVICE_ERROR = "sidecar_service_error"
SIDECAR_PROTOCOL_ERROR = "sidecar_protocol_error"
SIDECAR_REQUEST_ERROR = "sidecar_request_error"
SIDECAR_CONFLICT_TRANSIENT = "sidecar_conflict_transient"
SIDECAR_ERROR = "sidecar_error"
VALIDATION_ERROR = "validation_error"
LOCAL_DEPENDENCY_ERROR = "local_dependency_error"
PROVIDER_ERROR = "provider_error"
UNKNOWN_ERROR = "unknown_error"

# --- Retryability (OBSERVED metadata for reporting; NEVER changes runtime retry) --- #
RETRYABLE = "retryable"
NON_RETRYABLE = "non_retryable"
CONDITIONALLY_RETRYABLE = "conditionally_retryable"
RETRYABILITY_UNKNOWN = "unknown"

# GraphRAG typed exception -> (domain, token, retryability). Entries are concrete sibling
# subclasses of GraphRAGError (models.py); the first isinstance match wins, with a generic
# GraphRAGError catch-all last so an unknown future subclass still classifies as sidecar.
_GRAPHRAG_MAP: tuple[tuple[type, str, str, str], ...] = (
    (GraphRAGDisabledError, DOMAIN_FEATURE_DISABLED, FEATURE_DISABLED, NON_RETRYABLE),
    (GraphRAGValidationError, DOMAIN_VALIDATION, VALIDATION_ERROR, NON_RETRYABLE),
    (GraphRAGConfigurationError, DOMAIN_SIDECAR, SIDECAR_CONFIGURATION_ERROR, NON_RETRYABLE),
    (GraphRAGUnavailableError, DOMAIN_SIDECAR, SIDECAR_NETWORK_ERROR, CONDITIONALLY_RETRYABLE),
    (GraphRAGServerError, DOMAIN_SIDECAR, SIDECAR_SERVICE_ERROR, RETRYABLE),
    (GraphRAGProtocolError, DOMAIN_SIDECAR, SIDECAR_PROTOCOL_ERROR, NON_RETRYABLE),
    (GraphRAGConflictError, DOMAIN_SIDECAR, SIDECAR_CONFLICT_TRANSIENT, RETRYABLE),
    (GraphRAGRequestError, DOMAIN_SIDECAR, SIDECAR_REQUEST_ERROR, NON_RETRYABLE),
    (GraphRAGError, DOMAIN_SIDECAR, SIDECAR_ERROR, RETRYABILITY_UNKNOWN),
)


@dataclass(frozen=True)
class GraphRAGFailureClassification:
    """An immutable, content-safe, domain-first failure classification.

    ``classification`` is a fixed-vocabulary token; ``provider_error`` is the existing
    allowlisted, secret-free provider diagnostic dict, populated ONLY for the provider domain.
    There is no field carrying raw exception/response text.
    """

    failure_domain: str
    classification: str
    retryability: str
    provider_error: Optional[dict] = None

    @property
    def is_provider(self) -> bool:
        return self.failure_domain == DOMAIN_PROVIDER

    def as_public_dict(self) -> dict:
        out: dict[str, object] = {
            "failure_domain": self.failure_domain,
            "classification": self.classification,
            "retryability": self.retryability,
        }
        if self.provider_error is not None:
            out["provider_error"] = self.provider_error
        return out


def _is_local_dependency(exc: BaseException) -> bool:
    """True for a local runtime/data-dependency failure (never a provider failure).

    Covers Open Notebook's own DB/auth typed errors and any exception raised by the
    ``surrealdb`` client package (matched by module, like the httpx check in provider_errors).
    """
    if isinstance(exc, (DatabaseOperationError, AuthenticationError)):
        return True
    module = (type(exc).__module__ or "").split(".", 1)[0]
    return module == "surrealdb"


def is_non_provider_domain(exc: BaseException) -> bool:
    """True only for a CLEARLY non-provider typed failure.

    Used by a provider-bound call site (e.g. the final-answer seam) to decide that a typed
    GraphRAG or local-dependency failure must propagate with its own domain rather than be
    forced through provider vocabulary. An opaque/ambiguous exception returns False, so a
    genuine provider boundary still classifies it as a provider failure.
    """
    return isinstance(exc, GraphRAGError) or _is_local_dependency(exc)


def _has_structured_status_or_httpx(exc: BaseException) -> bool:
    """Whether a SINGLE exception carries an httpx type or a structured HTTP status."""
    if (type(exc).__module__ or "").split(".", 1)[0] == "httpx":
        return True
    resp = getattr(exc, "response", None)
    if isinstance(getattr(resp, "status_code", None), int):
        return True
    for attr in ("status_code", "status", "http_status"):
        v = getattr(exc, attr, None)
        if isinstance(v, int) and not isinstance(v, bool):
            return True
    return False


def _provider_error_if_bound(
    exc: BaseException, operation: str, provider_name: Optional[str]
) -> Optional[dict]:
    """Return a safe provider-error dict iff ``exc`` is genuinely provider-bound, else None.

    A bare status code is not trusted across boundaries (§status-code-origin): by the time this
    runs, GraphRAG-typed and local-dependency exceptions have already been handled, so an
    attached provider diagnostic, an httpx error, or a structured HTTP status (on the exception
    or anywhere in its cause chain) indicates the external provider boundary. When provider-bound,
    delegation to ``safe_provider_error_fields`` preserves the EW5 contract exactly: it prefers a
    diagnostic attached at the provider boundary (operation-accurate) and otherwise classifies the
    most informative exception in the chain.
    """
    cur: Optional[BaseException] = exc
    seen: set[int] = set()
    depth = 0
    provider_bound = extract_attached_diagnostic(exc) is not None
    while not provider_bound and cur is not None and depth < 8 and id(cur) not in seen:
        seen.add(id(cur))
        if _has_structured_status_or_httpx(cur):
            provider_bound = True
            break
        cur = cur.__cause__ or cur.__context__
        depth += 1
    if not provider_bound:
        return None
    return safe_provider_error_fields(
        exc, operation=operation, provider_name=provider_name
    )


def classify_graphrag_failure(
    exc: BaseException,
    *,
    operation: str,
    provider_name: Optional[str] = "openrouter",
) -> GraphRAGFailureClassification:
    """Classify a caught exception by failure DOMAIN, content-safe and provider-last.

    Order: GraphRAG typed error -> local dependency -> provider-bound (delegated to the
    unchanged ``classify_provider_error``) -> domain-neutral ``unknown_error``.
    """
    for exc_type, domain, token, retry in _GRAPHRAG_MAP:
        if isinstance(exc, exc_type):
            return GraphRAGFailureClassification(
                failure_domain=domain, classification=token, retryability=retry
            )

    if _is_local_dependency(exc):
        return GraphRAGFailureClassification(
            failure_domain=DOMAIN_LOCAL_DEPENDENCY,
            classification=LOCAL_DEPENDENCY_ERROR,
            retryability=NON_RETRYABLE,
        )

    provider_error = _provider_error_if_bound(exc, operation, provider_name)
    if provider_error is not None:
        return GraphRAGFailureClassification(
            failure_domain=DOMAIN_PROVIDER,
            classification=PROVIDER_ERROR,
            retryability=str(provider_error.get("retryability") or RETRYABILITY_UNKNOWN),
            provider_error=provider_error,
        )

    # No domain signal at all: honest, domain-neutral fallback. NOT unknown_provider_error.
    return GraphRAGFailureClassification(
        failure_domain=DOMAIN_UNKNOWN,
        classification=UNKNOWN_ERROR,
        retryability=RETRYABILITY_UNKNOWN,
    )


__all__ = [
    "DOMAIN_PROVIDER",
    "DOMAIN_SIDECAR",
    "DOMAIN_LOCAL_DEPENDENCY",
    "DOMAIN_VALIDATION",
    "DOMAIN_FEATURE_DISABLED",
    "DOMAIN_UNKNOWN",
    "FEATURE_DISABLED",
    "SIDECAR_CONFIGURATION_ERROR",
    "SIDECAR_NETWORK_ERROR",
    "SIDECAR_SERVICE_ERROR",
    "SIDECAR_PROTOCOL_ERROR",
    "SIDECAR_REQUEST_ERROR",
    "SIDECAR_CONFLICT_TRANSIENT",
    "SIDECAR_ERROR",
    "VALIDATION_ERROR",
    "LOCAL_DEPENDENCY_ERROR",
    "PROVIDER_ERROR",
    "UNKNOWN_ERROR",
    "RETRYABLE",
    "NON_RETRYABLE",
    "CONDITIONALLY_RETRYABLE",
    "RETRYABILITY_UNKNOWN",
    "GraphRAGFailureClassification",
    "is_non_provider_domain",
    "classify_graphrag_failure",
]
