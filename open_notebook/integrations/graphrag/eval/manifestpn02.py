"""PN02 run manifest, workspace routing, attestation, and Boundary-B guard.

EVALUATION-ONLY. Nothing in production imports this. Defines the deterministic,
content-safe manifests and the no-unattested-query / Boundary-B contracts a FUTURE
live runner must satisfy (task §12/§39-§45; PN02A §12). PN02B only validates them
OFFLINE — it opens no endpoint, starts no sidecar, and sends no request.

Identity rules (PN02A §12 / task §6, §40): a notebook's ``workspace_id`` is
``"nb_" + sha256(notebook_record_id)[:16]`` — opaque, deterministic, derived from
the canonical record id, NEVER from a display title. Endpoints are non-openable
placeholders. No secret VALUES ever appear here (secret variable NAMES may appear
in a config-contract, task §45).
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, Mapping, Optional, Sequence, Tuple

from open_notebook.integrations.graphrag.eval.datasetpn02 import FixturePN02

# Pinned evaluation LightRAG version (PN02A §12/§43). The canonical form tolerates
# the historical 'v'-prefix mismatch (GraphRAG-08E reauth #2 lesson): '1.5.6' and
# 'v1.5.6' both canonicalize to '1.5.6'.
LIGHTRAG_EVAL_VERSION = "v1.5.6"
_LIGHTRAG_EVAL_VERSION_CANON = "1.5.6"

WORKSPACE_ID_PREFIX = "nb_"
WORKSPACE_ID_HASH_LEN = 16
#: Deliberately non-openable scheme so a placeholder endpoint can never be dialed.
ENDPOINT_PLACEHOLDER_SCHEME = "eval-null"


class ManifestError(ValueError):
    """A PN02 manifest/attestation/contract is invalid."""


class BoundaryBViolation(RuntimeError):
    """A run manifest declared a non-approved (e.g. real internal) dataset class."""


class UnattestedWorkspaceError(RuntimeError):
    """A GD query was attempted against an unattested workspace (fail-closed)."""


class LightRAGVersionError(RuntimeError):
    """The observed LightRAG version does not match the pinned evaluation version."""


class DatasetClass(str, Enum):
    """Boundary-B dataset classification (task §44)."""

    SYNTHETIC_FIXTURE = "SYNTHETIC_FIXTURE"
    PUBLIC = "PUBLIC"
    ANONYMIZED = "ANONYMIZED"
    REAL_INTERNAL = "REAL_INTERNAL"


APPROVED_DATASET_CLASSES = frozenset(
    {DatasetClass.SYNTHETIC_FIXTURE, DatasetClass.PUBLIC, DatasetClass.ANONYMIZED}
)


def canonical_version(version: str) -> str:
    return version.strip().lstrip("vV").strip()


def validate_lightrag_version(observed: str) -> None:
    """Raise ``LightRAGVersionError`` unless ``observed`` matches the pinned version."""
    if canonical_version(observed) != _LIGHTRAG_EVAL_VERSION_CANON:
        raise LightRAGVersionError(
            f"LightRAG version {observed!r} != pinned {LIGHTRAG_EVAL_VERSION!r}"
        )


# --------------------------------------------------------------------------- #
# Workspace routing (task §40 / PN02A §12)
# --------------------------------------------------------------------------- #

def workspace_id_for(notebook_record_id: str) -> str:
    """Opaque deterministic workspace id from the canonical record id (PN01 §5)."""
    if not notebook_record_id:
        raise ManifestError("notebook_record_id must be non-empty")
    digest = hashlib.sha256(notebook_record_id.encode("utf-8")).hexdigest()
    return WORKSPACE_ID_PREFIX + digest[:WORKSPACE_ID_HASH_LEN]


def endpoint_placeholder_for(workspace_id: str) -> str:
    """A NON-openable placeholder endpoint identity (never dialed by PN02B)."""
    return f"{ENDPOINT_PLACEHOLDER_SCHEME}://workspace/{workspace_id}"


@dataclass(frozen=True)
class WorkspaceRoute:
    notebook_id: str
    notebook_record_id: str
    workspace_id: str
    endpoint_placeholder: str


def routing_manifest(fx: FixturePN02) -> Dict[str, WorkspaceRoute]:
    """Deterministic notebook -> workspace -> endpoint-placeholder map (task §40)."""
    routes: Dict[str, WorkspaceRoute] = {}
    seen_ws: set[str] = set()
    for nb in fx.notebooks:
        ws = workspace_id_for(nb.record_id)
        if ws in seen_ws:
            raise ManifestError(
                f"workspace id collision for {nb.notebook_id} ({ws})"
            )
        seen_ws.add(ws)
        routes[nb.notebook_id] = WorkspaceRoute(
            notebook_id=nb.notebook_id,
            notebook_record_id=nb.record_id,
            workspace_id=ws,
            endpoint_placeholder=endpoint_placeholder_for(ws),
        )
    return routes


# --------------------------------------------------------------------------- #
# Attestation contract (task §41/§42 / PN02A §12)
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class WorkspaceAttestation:
    """Per-notebook workspace attestation (task §41). NO secret values.

    ``provider_binding_fingerprint`` is a hash of a SAFE configuration identity
    (model ids / config shape, never a key); ``attested`` is True only when every
    check passed and ``failure_reasons`` is empty.
    """

    notebook_id: str
    workspace_id: str
    owned_endpoint_identity: str
    expected_storage_identity: str
    lightrag_version: str
    provider_binding_fingerprint: str
    attested: bool
    failure_reasons: Tuple[str, ...] = field(default_factory=tuple)


def provider_binding_fingerprint(safe_config_identity: str) -> str:
    """Hash a SECRET-FREE configuration identity into a stable fingerprint.

    ``safe_config_identity`` must contain only non-secret config (e.g. model ids);
    callers must never pass an API key value (task §45).
    """
    return "pbf_" + hashlib.sha256(safe_config_identity.encode("utf-8")).hexdigest()[:24]


def attest_workspace(
    route: WorkspaceRoute,
    *,
    observed_workspace_id: str,
    observed_endpoint_identity: str,
    observed_storage_identity: str,
    observed_lightrag_version: str,
    expected_storage_identity: str,
    safe_provider_config_identity: str,
) -> WorkspaceAttestation:
    """Evidence-based attestation (task §41). ``attested`` iff all checks pass.

    Never starts or dials the workspace — it compares supplied observations to the
    frozen expectations (an OFFLINE simulation of the live attestation).
    """
    reasons = []
    if observed_workspace_id != route.workspace_id:
        reasons.append("workspace_id_mismatch")
    if observed_endpoint_identity != route.endpoint_placeholder:
        reasons.append("endpoint_ownership_mismatch")
    if observed_storage_identity != expected_storage_identity:
        reasons.append("storage_identity_mismatch")
    if canonical_version(observed_lightrag_version) != _LIGHTRAG_EVAL_VERSION_CANON:
        reasons.append("lightrag_version_mismatch")
    return WorkspaceAttestation(
        notebook_id=route.notebook_id,
        workspace_id=route.workspace_id,
        owned_endpoint_identity=route.endpoint_placeholder,
        expected_storage_identity=expected_storage_identity,
        lightrag_version=observed_lightrag_version,
        provider_binding_fingerprint=provider_binding_fingerprint(
            safe_provider_config_identity
        ),
        attested=not reasons,
        failure_reasons=tuple(reasons),
    )


def require_attested_before_query(attestation: WorkspaceAttestation) -> None:
    """No GD query against an unattested workspace (task §42, fail-closed)."""
    if not attestation.attested:
        raise UnattestedWorkspaceError(
            f"workspace {attestation.workspace_id} is not attested "
            f"({', '.join(attestation.failure_reasons) or 'unknown'}); "
            "GD query refused (task §42)"
        )


# --------------------------------------------------------------------------- #
# Boundary-B guard (task §44)
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class BoundaryBDeclaration:
    dataset_class: DatasetClass
    real_internal_data_allowed: bool
    synthetic_only: bool


def validate_boundary_b(decl: BoundaryBDeclaration) -> None:
    """Refuse a run whose dataset class is not an approved synthetic/public class.

    A future live runner MUST call this before any provider traffic (task §44).
    """
    if decl.real_internal_data_allowed:
        raise BoundaryBViolation("REAL_INTERNAL_DATA_ALLOWED must be false (Boundary B)")
    if not decl.synthetic_only:
        raise BoundaryBViolation("SYNTHETIC_ONLY must be true (Boundary B)")
    if decl.dataset_class not in APPROVED_DATASET_CLASSES:
        raise BoundaryBViolation(
            f"dataset_class {decl.dataset_class.value} is not an approved "
            "synthetic/public/anonymized class (Boundary B)"
        )


DEFAULT_BOUNDARY_B = BoundaryBDeclaration(
    dataset_class=DatasetClass.SYNTHETIC_FIXTURE,
    real_internal_data_allowed=False,
    synthetic_only=True,
)


# --------------------------------------------------------------------------- #
# Run manifest (task §39)
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class RunManifest:
    """Content-safe run manifest (task §39). Ids/counts/flags only — no content."""

    run_id: str
    fixture_name: str
    fixture_hash: str
    git_commit: str
    lightrag_version_expected: str
    routing: Mapping[str, WorkspaceRoute]
    systems_enabled: Tuple[str, ...]
    vector_k_values: Tuple[int, ...]
    query_count: int
    budget_caps: Mapping[str, int]
    boundary_b: BoundaryBDeclaration
    stage_status: str
    created_at: Optional[str] = None

    def validate(self) -> None:
        if not self.fixture_hash:
            raise ManifestError("run manifest must carry a fixture_hash")
        validate_lightrag_version(self.lightrag_version_expected)
        validate_boundary_b(self.boundary_b)
        if self.query_count <= 0:
            raise ManifestError("query_count must be > 0")
        if not self.routing:
            raise ManifestError("run manifest must carry a routing map")


def build_run_manifest(
    fx: FixturePN02,
    *,
    run_id: str,
    fixture_hash: str,
    git_commit: str,
    systems_enabled: Sequence[str] = ("V", "GD"),
    vector_k_values: Sequence[int] = (3, 5),
    budget_caps: Optional[Mapping[str, int]] = None,
    boundary_b: BoundaryBDeclaration = DEFAULT_BOUNDARY_B,
    stage_status: str = "PLANNED",
    created_at: Optional[str] = None,
) -> RunManifest:
    """Construct and validate a content-safe run manifest (task §39)."""
    from open_notebook.integrations.graphrag.eval.workloadpn02 import as_dict

    manifest = RunManifest(
        run_id=run_id,
        fixture_name=fx.fixture_version,
        fixture_hash=fixture_hash,
        git_commit=git_commit,
        lightrag_version_expected=LIGHTRAG_EVAL_VERSION,
        routing=routing_manifest(fx),
        systems_enabled=tuple(systems_enabled),
        vector_k_values=tuple(vector_k_values),
        query_count=len(fx.queries),
        budget_caps=dict(budget_caps) if budget_caps is not None else as_dict(),
        boundary_b=boundary_b,
        stage_status=stage_status,
        created_at=created_at,
    )
    manifest.validate()
    return manifest


__all__ = [
    "LIGHTRAG_EVAL_VERSION",
    "WORKSPACE_ID_PREFIX",
    "ENDPOINT_PLACEHOLDER_SCHEME",
    "ManifestError",
    "BoundaryBViolation",
    "UnattestedWorkspaceError",
    "LightRAGVersionError",
    "DatasetClass",
    "APPROVED_DATASET_CLASSES",
    "canonical_version",
    "validate_lightrag_version",
    "workspace_id_for",
    "endpoint_placeholder_for",
    "WorkspaceRoute",
    "routing_manifest",
    "WorkspaceAttestation",
    "provider_binding_fingerprint",
    "attest_workspace",
    "require_attested_before_query",
    "BoundaryBDeclaration",
    "validate_boundary_b",
    "DEFAULT_BOUNDARY_B",
    "RunManifest",
    "build_run_manifest",
]
