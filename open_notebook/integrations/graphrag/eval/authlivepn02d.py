"""PN02 live-driver authorization chain + provider-operation allowlist (PN02D-B0B).

EVALUATION-ONLY. Nothing in production imports this (``PRODUCTION_IMPORTS_EVAL =
NO``). Implements the frozen fail-closed capability chain (design §6/§7/§8):

    RealLightRAGPreflightAuthorization   (PN02D-A; minted on Gate0∧Gate1 PASS)
        -> PN02ProviderRunAuthorization  (operator grant; design §7)
        -> provider-binding materialization  (unreachable before the above; §10)
        -> IndexingAuthorization         (minted after binding attestation PASS)
        -> QueryAuthorization            (minted only at 24/24 completeness; §24)
        -> provider-backed operation     (allowlist §8)

Each capability follows the proven ``stage1pn02.Stage1Authorization`` /
``attestpn02d.RealLightRAGPreflightAuthorization`` pattern: a module-private
``_AUTH_KEY`` sentinel, ``__slots__``, a ctor that raises ``PermissionError`` on any
other key, minted only inside a verifier here, and a ``require_*`` that raises a
typed error otherwise. Capabilities carry ``fixture_hash`` + ``run_id`` so they
cannot be replayed across runs/fixtures.

**B0B never grants a real provider run.** ``PN02_PROVIDER_RUN_AUTHORIZED = False``
is the governance posture; the offline simulation drives everything through FAKE
backends, so even a (test-minted) authorization produces zero provider traffic.
The only mint path for the preflight capability is the genuine PN02D-A
``mint_real_preflight_authorization`` (which itself requires both real gates); B0B
tests exercise the chain by passing simulated gate booleans to THAT frozen mint —
there is no separate backdoor here.
"""

from __future__ import annotations

from enum import Enum
from typing import Dict, FrozenSet, Mapping, Optional

from open_notebook.integrations.graphrag.eval.attestpn02d import (
    RealLightRAGPreflightAuthorization,
    require_real_preflight_authorization,
)
from open_notebook.integrations.graphrag.eval.manifestpn02 import (
    provider_binding_fingerprint,
)
from open_notebook.integrations.graphrag.eval.provider_binding08 import (
    frozen_provider_binding,
)

#: Governance posture — never flipped by B0B (design §7/§45, task §7).
PN02_PROVIDER_RUN_AUTHORIZED = False


# --------------------------------------------------------------------------- #
# Operation allowlist (design §8) — structural rejection
# --------------------------------------------------------------------------- #

class ProviderOperationClass(str, Enum):
    """Every provider-backed operation class the driver knows about (design §8)."""

    # Allowed in B1.
    GRAPH_INDEX = "GRAPH_INDEX"
    INDEX_REQUIRED_EMBEDDING = "INDEX_REQUIRED_EMBEDDING"
    GD_QUERY_DATA = "GD_QUERY_DATA"
    VECTOR_QUERY_EMBEDDING = "VECTOR_QUERY_EMBEDDING"
    VECTOR_NOTEBOOK_QUERY = "VECTOR_NOTEBOOK_QUERY"
    GRAPH_DELETE = "GRAPH_DELETE"
    # Forbidden in B1 (rejected structurally).
    CLIENT_QUERY = "CLIENT_QUERY"
    LIGHTRAG_FINAL_ANSWER = "LIGHTRAG_FINAL_ANSWER"
    QA_V = "QA_V"
    QA_GD = "QA_GD"
    QA_V_GD = "QA_V_GD"
    JUDGE_MODEL = "JUDGE_MODEL"
    PRODUCTION_ASK = "PRODUCTION_ASK"


B1_ALLOWED_OPERATION_CLASSES: FrozenSet[ProviderOperationClass] = frozenset(
    {
        ProviderOperationClass.GRAPH_INDEX,
        ProviderOperationClass.INDEX_REQUIRED_EMBEDDING,
        ProviderOperationClass.GD_QUERY_DATA,
        ProviderOperationClass.VECTOR_QUERY_EMBEDDING,
        ProviderOperationClass.VECTOR_NOTEBOOK_QUERY,
        ProviderOperationClass.GRAPH_DELETE,
    }
)

B1_FORBIDDEN_OPERATION_CLASSES: FrozenSet[ProviderOperationClass] = frozenset(
    c for c in ProviderOperationClass if c not in B1_ALLOWED_OPERATION_CLASSES
)


# --------------------------------------------------------------------------- #
# Typed errors (fail-closed)
# --------------------------------------------------------------------------- #

class ProviderRunNotAuthorized(RuntimeError):
    """A provider-backed op/binding was reached without a PN02ProviderRunAuthorization."""


class ForbiddenOperationClass(RuntimeError):
    """An operation class outside the B1 allowlist was attempted (design §8)."""


class IndexingAuthorizationMissing(RuntimeError):
    """An index op was reached without a valid IndexingAuthorization (design §6)."""


class QueryAuthorizationMissing(RuntimeError):
    """A GD/vector query was reached without a valid QueryAuthorization (design §15)."""


class ProviderRunAuthorizationError(ValueError):
    """The inputs to mint a PN02ProviderRunAuthorization were invalid (fail-closed)."""


class RunIdConsistencyError(RuntimeError):
    """A per-operation run_id did not match every capability's run_id (design §8/L-1).

    The B0C-A run_id cross-check (finding L-1) makes this a hard fail-closed guard:
    ``operation.run_id == provider_run_auth.run_id == indexing/query_auth.run_id ==
    driver.run_id == manifest.run_id``. A mismatch is refused BEFORE any backend or
    network dispatch, not merely at mint time — so a capability minted for one run can
    never drive an operation labelled with a different run_id.
    """


def assert_run_ids_consistent(run_id: str, *auths: object) -> None:
    """Fail closed unless ``run_id`` equals every supplied capability's ``run_id`` (L-1).

    ``None`` capabilities are skipped (an optional stage may be absent); every present
    capability MUST carry an identical ``run_id`` attribute. Raises
    ``RunIdConsistencyError`` on the first mismatch (or an empty run_id).
    """
    if not run_id:
        raise RunIdConsistencyError("run_id must be non-empty (design §8/L-1)")
    for auth in auths:
        if auth is None:
            continue
        observed = getattr(auth, "run_id", None)
        if observed != run_id:
            raise RunIdConsistencyError(
                f"run_id cross-check failed: capability run_id {observed!r} != "
                f"operation run_id {run_id!r} (fail-closed, design §8/L-1)"
            )


# --------------------------------------------------------------------------- #
# Frozen provider-config identity (secret-free, design §7/§R1.11)
# --------------------------------------------------------------------------- #

def frozen_provider_config_id() -> str:
    """Content-safe fingerprint of the frozen provider config (NO secret value).

    Uses only public config identity — provider/model names, embedding dim, host,
    and the secret env-var NAME (never its value) — per design §R1.11.
    """
    b = frozen_provider_binding()
    identity = (
        f"{b.llm_binding}|{b.llm_model}|{b.embedding_binding}|{b.embedding_model}|"
        f"{b.embedding_dim}|{b.llm_host}|{b.llm_secret_env}"
    )
    return provider_binding_fingerprint(identity)


# --------------------------------------------------------------------------- #
# PN02ProviderRunAuthorization (design §7) — unforgeable, operator-granted
# --------------------------------------------------------------------------- #

_AUTH_KEY = object()


class PN02ProviderRunAuthorization:
    """Capability required before ANY provider-backed op (design §6/§7).

    Encodes non-secret run identity: fixture hash, approved git baseline, synthetic-
    only state, provider-config fingerprint, workload caps, allowed operation
    classes, and run ownership. It cannot be constructed directly (a manual attempt
    raises ``PermissionError``); the ONLY mint path is
    ``mint_provider_run_authorization`` after a genuine
    ``RealLightRAGPreflightAuthorization`` (design §7). No secret value ever appears
    on the object.
    """

    __slots__ = (
        "fixture_hash",
        "git_baseline_commit",
        "git_baseline_tag",
        "synthetic_only",
        "real_internal_data_allowed",
        "approved_provider_config_id",
        "workload_caps",
        "allowed_operation_classes",
        "run_id",
    )

    def __init__(
        self,
        key: object,
        *,
        fixture_hash: str,
        git_baseline_commit: str,
        git_baseline_tag: str,
        synthetic_only: bool,
        real_internal_data_allowed: bool,
        approved_provider_config_id: str,
        workload_caps: Mapping[str, int],
        allowed_operation_classes: FrozenSet[ProviderOperationClass],
        run_id: str,
    ) -> None:
        if key is not _AUTH_KEY:
            raise PermissionError(
                "PN02ProviderRunAuthorization is minted only by "
                "mint_provider_run_authorization after RealLightRAGPreflight"
                "Authorization; it cannot be constructed directly (fail-closed, §7)"
            )
        self.fixture_hash = fixture_hash
        self.git_baseline_commit = git_baseline_commit
        self.git_baseline_tag = git_baseline_tag
        self.synthetic_only = synthetic_only
        self.real_internal_data_allowed = real_internal_data_allowed
        self.approved_provider_config_id = approved_provider_config_id
        self.workload_caps = dict(workload_caps)
        self.allowed_operation_classes = frozenset(allowed_operation_classes)
        self.run_id = run_id

    def as_public_dict(self) -> Dict[str, object]:
        """Content-safe view for artifacts (no secret; names/ids/flags only)."""
        return {
            "fixture_hash": self.fixture_hash,
            "git_baseline_commit": self.git_baseline_commit,
            "git_baseline_tag": self.git_baseline_tag,
            "synthetic_only": self.synthetic_only,
            "real_internal_data_allowed": self.real_internal_data_allowed,
            "approved_provider_config_id": self.approved_provider_config_id,
            "allowed_operation_classes": sorted(
                c.value for c in self.allowed_operation_classes
            ),
            "run_id": self.run_id,
        }


def mint_provider_run_authorization(
    *,
    real_preflight_auth: Optional[RealLightRAGPreflightAuthorization],
    fixture_hash: str,
    expected_fixture_hash: str,
    git_baseline_commit: str,
    git_baseline_tag: str,
    run_id: str,
    workload_caps: Mapping[str, int],
    synthetic_only: bool = True,
    real_internal_data_allowed: bool = False,
    approved_provider_config_id: Optional[str] = None,
) -> PN02ProviderRunAuthorization:
    """Mint the provider-run capability — the ONLY construction path (design §7).

    Fails closed on: a missing/forged real-preflight capability, a fixture-hash
    mismatch, a Boundary-B violation, or a provider-config-fingerprint mismatch.
    This does NOT itself contact a provider. In B0B the ``real_preflight_auth`` is
    obtained from the frozen PN02D-A ``mint_real_preflight_authorization`` with
    simulated gate booleans (a controlled test factory, design §45); no real
    provider run is ever authorized.
    """
    # 1) genuine real-preflight capability required first (design §6).
    require_real_preflight_authorization(real_preflight_auth)
    # 1b) B0CB-H1 hardening: the preflight capability MUST have been minted for the
    #     SAME fixture we are authorizing — otherwise a preflight cap for another
    #     fixture (same run_id) could authorize this run. Bind the fixture identity.
    if real_preflight_auth.fixture_hash != fixture_hash:  # type: ignore[union-attr]
        raise ProviderRunAuthorizationError(
            "real-preflight capability fixture_hash does not match the fixture being "
            "authorized (fail-closed, B0CB-H1)"
        )
    # 2) fixture-hash hard gate (design §37).
    if fixture_hash != expected_fixture_hash:
        raise ProviderRunAuthorizationError(
            f"fixture_hash {fixture_hash!r} != frozen {expected_fixture_hash!r}"
        )
    # 3) Boundary B (design §36).
    if real_internal_data_allowed:
        raise ProviderRunAuthorizationError("real_internal_data_allowed must be False")
    if not synthetic_only:
        raise ProviderRunAuthorizationError("synthetic_only must be True")
    # 4) provider-config identity (design §7/§R1.11).
    expected_cfg = frozen_provider_config_id()
    cfg = approved_provider_config_id or expected_cfg
    if cfg != expected_cfg:
        raise ProviderRunAuthorizationError(
            "approved_provider_config_id does not match the frozen provider config"
        )
    if not run_id:
        raise ProviderRunAuthorizationError("run_id must be non-empty")
    return PN02ProviderRunAuthorization(
        _AUTH_KEY,
        fixture_hash=fixture_hash,
        git_baseline_commit=git_baseline_commit,
        git_baseline_tag=git_baseline_tag,
        synthetic_only=synthetic_only,
        real_internal_data_allowed=real_internal_data_allowed,
        approved_provider_config_id=cfg,
        workload_caps=workload_caps,
        allowed_operation_classes=B1_ALLOWED_OPERATION_CLASSES,
        run_id=run_id,
    )


def require_provider_run_authorization(
    auth: Optional[PN02ProviderRunAuthorization],
) -> PN02ProviderRunAuthorization:
    """Guard every provider-backed seam (binding, index, GD, vector, delete).

    Raises ``ProviderRunNotAuthorized`` unless a genuine capability is presented.
    """
    if not isinstance(auth, PN02ProviderRunAuthorization):
        raise ProviderRunNotAuthorized(
            "provider-backed operation blocked: no valid PN02ProviderRunAuthorization "
            "(fail-closed, design §6/§7)"
        )
    return auth


def require_operation_allowed(
    auth: PN02ProviderRunAuthorization, op_class: ProviderOperationClass
) -> None:
    """Reject any operation class not on the B1 allowlist (design §8), structurally."""
    if op_class in B1_FORBIDDEN_OPERATION_CLASSES:
        raise ForbiddenOperationClass(
            f"operation class {op_class.value} is forbidden in B1 (design §8)"
        )
    if op_class not in auth.allowed_operation_classes:
        raise ForbiddenOperationClass(
            f"operation class {op_class.value} not in the authorized set (design §8)"
        )


# --------------------------------------------------------------------------- #
# IndexingAuthorization (design §6) — minted after provider-binding attestation
# --------------------------------------------------------------------------- #

class IndexingAuthorization:
    """Capability proving provider binding materialized + attested (design §6).

    Minted only by ``mint_indexing_authorization`` after a genuine
    PN02ProviderRunAuthorization AND a PASS binding attestation. Guards every index
    op. Not directly constructible.
    """

    __slots__ = ("fixture_hash", "run_id")

    def __init__(self, key: object, *, fixture_hash: str, run_id: str) -> None:
        if key is not _AUTH_KEY:
            raise PermissionError(
                "IndexingAuthorization is minted only after provider-binding "
                "attestation; it cannot be constructed directly (fail-closed, §6)"
            )
        self.fixture_hash = fixture_hash
        self.run_id = run_id


def mint_indexing_authorization(
    provider_run_auth: Optional[PN02ProviderRunAuthorization],
    *,
    binding_attested: bool,
    run_id: str,
) -> Optional[IndexingAuthorization]:
    """Mint iff a genuine provider-run auth exists AND binding attestation PASSED."""
    auth = require_provider_run_authorization(provider_run_auth)
    if not binding_attested:
        return None
    return IndexingAuthorization(_AUTH_KEY, fixture_hash=auth.fixture_hash, run_id=run_id)


def require_indexing_authorization(
    auth: Optional[IndexingAuthorization],
) -> IndexingAuthorization:
    if not isinstance(auth, IndexingAuthorization):
        raise IndexingAuthorizationMissing(
            "indexing blocked: no valid IndexingAuthorization (provider binding not "
            "materialized/attested) — fail-closed (design §6)"
        )
    return auth


# --------------------------------------------------------------------------- #
# QueryAuthorization (design §15/§24) — minted only at 24/24 completeness
# --------------------------------------------------------------------------- #

class QueryAuthorization:
    """Capability proving the 24/24 index-completeness gate PASSED (design §15).

    Minted only by ``mint_query_authorization`` when every planned workspace
    membership indexed successfully. Guards every GD and vector query. Not directly
    constructible.
    """

    __slots__ = ("fixture_hash", "run_id", "indexed_memberships")

    def __init__(
        self, key: object, *, fixture_hash: str, run_id: str, indexed_memberships: int
    ) -> None:
        if key is not _AUTH_KEY:
            raise PermissionError(
                "QueryAuthorization is minted only at 24/24 index completeness; it "
                "cannot be constructed directly (fail-closed, §15)"
            )
        self.fixture_hash = fixture_hash
        self.run_id = run_id
        self.indexed_memberships = indexed_memberships


def mint_query_authorization(
    indexing_auth: Optional[IndexingAuthorization],
    *,
    indexed_memberships: int,
    planned_memberships: int,
    run_id: str,
) -> Optional[QueryAuthorization]:
    """Mint iff a valid IndexingAuthorization exists AND indexed == planned (24/24).

    23/24 (or any shortfall) returns None — no GD/vector authorization (design §24).
    """
    auth = require_indexing_authorization(indexing_auth)
    if planned_memberships <= 0 or indexed_memberships != planned_memberships:
        return None
    return QueryAuthorization(
        _AUTH_KEY,
        fixture_hash=auth.fixture_hash,
        run_id=run_id,
        indexed_memberships=indexed_memberships,
    )


def require_query_authorization(
    auth: Optional[QueryAuthorization],
) -> QueryAuthorization:
    if not isinstance(auth, QueryAuthorization):
        raise QueryAuthorizationMissing(
            "query blocked: no valid QueryAuthorization (index completeness < 24/24) "
            "— fail-closed (design §15/§24)"
        )
    return auth


__all__ = [
    "PN02_PROVIDER_RUN_AUTHORIZED",
    "ProviderOperationClass",
    "B1_ALLOWED_OPERATION_CLASSES",
    "B1_FORBIDDEN_OPERATION_CLASSES",
    "ProviderRunNotAuthorized",
    "ForbiddenOperationClass",
    "IndexingAuthorizationMissing",
    "QueryAuthorizationMissing",
    "ProviderRunAuthorizationError",
    "RunIdConsistencyError",
    "assert_run_ids_consistent",
    "frozen_provider_config_id",
    "PN02ProviderRunAuthorization",
    "mint_provider_run_authorization",
    "require_provider_run_authorization",
    "require_operation_allowed",
    "IndexingAuthorization",
    "mint_indexing_authorization",
    "require_indexing_authorization",
    "QueryAuthorization",
    "mint_query_authorization",
    "require_query_authorization",
]
