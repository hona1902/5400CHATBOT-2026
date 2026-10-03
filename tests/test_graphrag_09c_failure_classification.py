"""GraphRAG-09C domain-first failure classification regression tests (provider-free).

Proves the fix for the ``unknown_provider_error`` masking defect: non-provider failures
(GraphRAG sidecar/config, SurrealDB/local dependency) are classified by their own domain and
NEVER mislabeled ``unknown_provider_error``, while the existing provider classifier contract
(including its honest ``unknown_provider_error`` fallback for a genuinely provider-bound opaque
exception) is preserved unchanged.

All failures are simulated. No provider, sidecar, SurrealDB, or network access occurs. No real
secrets are used — only inert synthetic sentinels.
"""

import json

from open_notebook.exceptions import AuthenticationError, DatabaseOperationError
from open_notebook.integrations.graphrag import failure_classification as fc
from open_notebook.integrations.graphrag.models import (
    GraphRAGConfigurationError,
    GraphRAGConflictError,
    GraphRAGDisabledError,
    GraphRAGProtocolError,
    GraphRAGRequestError,
    GraphRAGServerError,
    GraphRAGUnavailableError,
    GraphRAGValidationError,
)
from open_notebook.utils import provider_errors as pe


class _ProviderHTTPError(Exception):
    """A generic provider-bound exception carrying a STRUCTURED HTTP status (no GraphRAG /
    local-dependency type), like a provider adapter raising with a ``status_code`` attribute."""

    def __init__(self, status: int, msg: str = "boom", code=None) -> None:
        super().__init__(msg)
        self.status_code = status
        if code is not None:
            self.code = code


def _surreal_module_error() -> Exception:
    """An exception whose defining module is ``surrealdb`` (without importing the package)."""
    cls = type("FakeSurrealError", (Exception,), {})
    cls.__module__ = "surrealdb"
    return cls("connection refused")


# --------------------------------------------------------------- provider domain (delegated)


def test_provider_401_classification():
    r = fc.classify_graphrag_failure(_ProviderHTTPError(401), operation="x")
    assert r.failure_domain == fc.DOMAIN_PROVIDER
    assert r.classification == fc.PROVIDER_ERROR
    assert r.provider_error is not None
    assert r.provider_error["provider_error_class"] == pe.AUTHENTICATION_ERROR


def test_provider_429_classification():
    r = fc.classify_graphrag_failure(_ProviderHTTPError(429), operation="x")
    assert r.failure_domain == fc.DOMAIN_PROVIDER
    assert r.provider_error["provider_error_class"] == pe.RATE_LIMIT
    assert r.retryability == pe.RETRYABLE


def test_provider_5xx_classification():
    r = fc.classify_graphrag_failure(_ProviderHTTPError(503), operation="x")
    assert r.failure_domain == fc.DOMAIN_PROVIDER
    assert r.provider_error["provider_error_class"] == pe.SERVER_ERROR


# --------------------------------------------------------------- sidecar domain (typed)


def test_sidecar_config_masking_regression():
    """Historical masking class B: a sidecar 401/403/version/config mismatch surfaces as
    GraphRAGConfigurationError and must be sidecar-domain, never unknown_provider_error."""
    r = fc.classify_graphrag_failure(
        GraphRAGConfigurationError("sidecar rejected credentials 401"), operation="index"
    )
    assert r.failure_domain == fc.DOMAIN_SIDECAR
    assert r.classification == fc.SIDECAR_CONFIGURATION_ERROR
    assert r.classification != pe.UNKNOWN_PROVIDER_ERROR
    assert r.provider_error is None
    assert "provider_error" not in r.as_public_dict()


def test_sidecar_protocol_classification():
    r = fc.classify_graphrag_failure(GraphRAGProtocolError("bad json"), operation="query")
    assert r.failure_domain == fc.DOMAIN_SIDECAR
    assert r.classification == fc.SIDECAR_PROTOCOL_ERROR


def test_sidecar_network_classification():
    r = fc.classify_graphrag_failure(
        GraphRAGUnavailableError("connection refused"), operation="query"
    )
    assert r.failure_domain == fc.DOMAIN_SIDECAR
    assert r.classification == fc.SIDECAR_NETWORK_ERROR
    assert r.retryability == fc.CONDITIONALLY_RETRYABLE


def test_sidecar_server_conflict_request_tokens():
    assert (
        fc.classify_graphrag_failure(GraphRAGServerError("5xx"), operation="x").classification
        == fc.SIDECAR_SERVICE_ERROR
    )
    assert (
        fc.classify_graphrag_failure(GraphRAGConflictError("409"), operation="x").classification
        == fc.SIDECAR_CONFLICT_TRANSIENT
    )
    assert (
        fc.classify_graphrag_failure(GraphRAGRequestError("422"), operation="x").classification
        == fc.SIDECAR_REQUEST_ERROR
    )


# --------------------------------------------------------------- feature-disabled / validation


def test_feature_disabled_classification():
    r = fc.classify_graphrag_failure(GraphRAGDisabledError("off"), operation="query")
    assert r.failure_domain == fc.DOMAIN_FEATURE_DISABLED
    assert r.classification == fc.FEATURE_DISABLED
    assert r.provider_error is None


def test_validation_classification():
    r = fc.classify_graphrag_failure(
        GraphRAGValidationError("invalid source_id"), operation="index"
    )
    assert r.failure_domain == fc.DOMAIN_VALIDATION
    assert r.classification == fc.VALIDATION_ERROR


# --------------------------------------------------------------- local dependency (SurrealDB)


def test_surreal_auth_masking_regression():
    """Historical masking class A: SurrealDB auth/bootstrap/runtime failures must be
    local-dependency, never provider, never unknown_provider_error."""
    for exc in (
        _surreal_module_error(),
        DatabaseOperationError("surreal namespace bootstrap failed"),
        AuthenticationError("surreal auth failed"),
    ):
        r = fc.classify_graphrag_failure(exc, operation="live_provider_execution")
        assert r.failure_domain == fc.DOMAIN_LOCAL_DEPENDENCY, type(exc)
        assert r.classification == fc.LOCAL_DEPENDENCY_ERROR
        assert r.classification != pe.UNKNOWN_PROVIDER_ERROR
        assert r.provider_error is None


# --------------------------------------------------------------- unknown fallbacks


def test_unknown_mixed_domain_fallback_is_domain_neutral():
    """An opaque non-provider exception with no domain signal -> domain-neutral unknown_error,
    NOT unknown_provider_error."""
    r = fc.classify_graphrag_failure(RuntimeError("opaque"), operation="x")
    assert r.failure_domain == fc.DOMAIN_UNKNOWN
    assert r.classification == fc.UNKNOWN_ERROR
    assert r.classification != pe.UNKNOWN_PROVIDER_ERROR
    assert r.provider_error is None


def test_provider_unknown_fallback_preserved():
    """The provider classifier contract is UNCHANGED: a genuinely provider-bound opaque
    exception still classifies as unknown_provider_error at the provider layer."""
    diag = pe.classify_provider_error(RuntimeError("opaque"), operation="embedding")
    assert diag.provider_error_class == pe.UNKNOWN_PROVIDER_ERROR


# --------------------------------------------------------------- status-code origin context


def test_status_code_origin_context_regression():
    """Bare status code is not sufficient across boundaries: a provider 401 is provider auth,
    while a sidecar 401 represented as GraphRAGConfigurationError is sidecar configuration."""
    provider = fc.classify_graphrag_failure(_ProviderHTTPError(401), operation="x")
    sidecar = fc.classify_graphrag_failure(
        GraphRAGConfigurationError("sidecar 401 key mismatch"), operation="x"
    )
    assert provider.failure_domain == fc.DOMAIN_PROVIDER
    assert provider.provider_error["provider_error_class"] == pe.AUTHENTICATION_ERROR
    assert sidecar.failure_domain == fc.DOMAIN_SIDECAR
    assert sidecar.classification == fc.SIDECAR_CONFIGURATION_ERROR


# --------------------------------------------------------------- content safety


def test_secret_sentinel_never_appears():
    """A secret-bearing message/code must never surface in the classification output."""
    sentinel = "sk-or-SENTINELSECRETVALUE0123456789"
    exc = _ProviderHTTPError(401, msg=f"Authorization: Bearer {sentinel}", code=sentinel)
    r = fc.classify_graphrag_failure(exc, operation="x")
    serialized = json.dumps(r.as_public_dict())
    assert sentinel not in serialized
    assert "Bearer" not in serialized
    # the secret-looking code is dropped by the provider classifier's allowlist
    assert r.provider_error["provider_error_code"] is None


def test_raw_exception_detail_not_leaked():
    exc = RuntimeError("super secret internal detail THAT_SHOULD_NOT_LEAK")
    r = fc.classify_graphrag_failure(exc, operation="x")
    assert "THAT_SHOULD_NOT_LEAK" not in json.dumps(r.as_public_dict())


# --------------------------------------------------------------- is_non_provider_domain guard


def test_is_non_provider_domain_guard():
    assert fc.is_non_provider_domain(GraphRAGConfigurationError("x")) is True
    assert fc.is_non_provider_domain(GraphRAGDisabledError("x")) is True
    assert fc.is_non_provider_domain(DatabaseOperationError("x")) is True
    assert fc.is_non_provider_domain(_surreal_module_error()) is True
    # provider / opaque are NOT diverted at a provider boundary (seam keeps wrapping them)
    assert fc.is_non_provider_domain(_ProviderHTTPError(401)) is False
    assert fc.is_non_provider_domain(RuntimeError("opaque")) is False
