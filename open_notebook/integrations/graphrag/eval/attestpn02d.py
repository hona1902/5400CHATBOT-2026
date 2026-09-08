"""PN02D-A real-runtime attestation + the hard no-index authorization capability.

EVALUATION-ONLY. Nothing in production imports this. This module attests a REAL
booted LightRAG ``v1.5.6`` runtime (task §9-§11/§16-§18) and mints the structural
capability that any FUTURE live indexing seam must present (task §34/§35). It does
NOT index, query, embed, or call any provider.

Version attestation (task §9) is RUNTIME-OBSERVED, not merely config-pinned: it
requires THREE independent provider-free signals to agree on ``1.5.6`` — the
running server's ``/health`` ``core_version``, the installed package
``lightrag.__version__`` inside the running container, and the image's
``org.opencontainers.image.version`` label. ``/query/data`` is never used as a
probe.

Routing (task §19) reuses the PN02C contract verbatim — ``routingpn02c.route_query``
/ ``authorize_future_gd`` / ``cross_workspace_storage_aliasing`` and
``manifestpn02.require_attested_before_query`` — because ``RealRuntimeIdentity``
carries the same attribute surface as the PN02C ``RuntimeIdentity``. No route proof
calls ``/query/data``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Tuple

from open_notebook.integrations.graphrag.eval.manifestpn02 import (
    LIGHTRAG_EVAL_VERSION,
    WorkspaceAttestation,
    canonical_version,
)
from open_notebook.integrations.graphrag.eval.realsidecarpn02d import (
    EXPECTED_IMAGE_VERSION_LABEL,
    RealRuntimeIdentity,
)
from open_notebook.integrations.graphrag.eval.routingpn02c import (
    runtime_config_fingerprint,
)

_EXPECTED_CANON = canonical_version(LIGHTRAG_EVAL_VERSION)  # "1.5.6"


class RealAttestationError(RuntimeError):
    """A real runtime failed attestation (version/workspace/storage/endpoint)."""


class IndexingGateBlocked(RuntimeError):
    """A future indexing seam was reached without a valid real-preflight auth."""


# --------------------------------------------------------------------------- #
# Version attestation (task §9) — three independent provider-free signals
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class RealVersionAttestation:
    health_core_version: str
    import_version: str
    image_label_version: str
    expected: str
    attested: bool
    failure_reasons: Tuple[str, ...]

    @property
    def observed_version(self) -> str:
        """The canonical observed version when attested (else 'UNVERIFIED')."""
        return _EXPECTED_CANON if self.attested else "UNVERIFIED"


def attest_version(
    *,
    health_core_version: str,
    import_version: str,
    image_label_version: str,
    expected_label: str = EXPECTED_IMAGE_VERSION_LABEL,
) -> RealVersionAttestation:
    """Attest the RUNTIME version from three independent provider-free signals.

    PASS iff all three canonicalize to the pinned version and none is empty/errored.
    Tolerates the historical ``v``-prefix (``1.5.6`` and ``v1.5.6`` both canonicalize
    to ``1.5.6``)."""
    reasons = []
    hc = canonical_version(health_core_version or "")
    iv = canonical_version(import_version or "")
    il = canonical_version(image_label_version or "")
    expected_canon = canonical_version(expected_label)
    if not health_core_version or health_core_version.upper().startswith("ERR") or hc != expected_canon:
        reasons.append(f"health_core_version={health_core_version!r}!={expected_canon}")
    if not import_version or import_version.upper() in ("", "NONE") or iv != expected_canon:
        reasons.append(f"import_version={import_version!r}!={expected_canon}")
    if not image_label_version or il != expected_canon:
        reasons.append(f"image_label_version={image_label_version!r}!={expected_canon}")
    return RealVersionAttestation(
        health_core_version=health_core_version,
        import_version=import_version,
        image_label_version=image_label_version,
        expected=expected_canon,
        attested=not reasons,
        failure_reasons=tuple(reasons),
    )


# --------------------------------------------------------------------------- #
# Full runtime attestation (task §10/§11/§16-§18/§21)
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class RealRuntimeAttestation:
    """Attestation of one real runtime. Wraps the shared ``WorkspaceAttestation``
    plus the version + egress evidence unique to a real boot. No secrets."""

    workspace: WorkspaceAttestation
    version: RealVersionAttestation
    observed_workspace_id: str
    observed_endpoint: str
    observed_storage_identity: str
    external_egress_sockets: int
    provider_execution_authorized: bool
    attested: bool
    failure_reasons: Tuple[str, ...]


def attest_real_runtime(
    identity: RealRuntimeIdentity,
    *,
    version: RealVersionAttestation,
    observed_workspace_id: str,
    observed_endpoint: str,
    observed_storage_identity: str,
    external_egress_sockets: int,
    observed_synthetic_only: bool = True,
    provider_execution_authorized: bool = False,
) -> RealRuntimeAttestation:
    """Attest a real runtime: version PASS + workspace/endpoint/storage identity +
    synthetic-only + ZERO external egress + provider-run NOT authorized (task §11).

    ``attested`` is True only when EVERY condition holds — a single failure (wrong
    workspace, wrong storage, version mismatch, any egress) fails the gate."""
    reasons = []
    if not version.attested:
        reasons.append("version_not_attested")
    if observed_workspace_id != identity.workspace_id:
        reasons.append("workspace_id_mismatch")
    if observed_endpoint != identity.endpoint:
        reasons.append("endpoint_ownership_mismatch")
    if observed_storage_identity != identity.storage_identity:
        reasons.append("storage_identity_mismatch")
    if not observed_synthetic_only:
        reasons.append("synthetic_only_false")
    if external_egress_sockets != 0:
        reasons.append(f"external_egress_sockets={external_egress_sockets}")
    if provider_execution_authorized:
        reasons.append("provider_execution_authorized_true")

    workspace_att = WorkspaceAttestation(
        notebook_id=identity.notebook_id,
        workspace_id=identity.workspace_id,
        owned_endpoint_identity=identity.endpoint,
        expected_storage_identity=identity.storage_identity,
        lightrag_version=version.observed_version,
        # RealRuntimeIdentity is intentionally attribute-compatible with the PN02C
        # RuntimeIdentity (task §19 reuse); the fingerprint reads only shared attrs.
        provider_binding_fingerprint=runtime_config_fingerprint(identity),  # type: ignore[arg-type]
        attested=not reasons,
        failure_reasons=tuple(reasons),
    )
    return RealRuntimeAttestation(
        workspace=workspace_att,
        version=version,
        observed_workspace_id=observed_workspace_id,
        observed_endpoint=observed_endpoint,
        observed_storage_identity=observed_storage_identity,
        external_egress_sockets=external_egress_sockets,
        provider_execution_authorized=provider_execution_authorized,
        attested=not reasons,
        failure_reasons=tuple(reasons),
    )


# --------------------------------------------------------------------------- #
# HARD no-index authorization capability (task §34/§35) — mirrors Stage1Authorization
# --------------------------------------------------------------------------- #

# Private, unforgeable minting key — only this module holds it.
_AUTH_KEY = object()


class RealLightRAGPreflightAuthorization:
    """Capability proving BOTH real preflight gates PASSED (task §34).

    Minted ONLY by ``mint_real_preflight_authorization`` after Gate 0 (single-sidecar
    provider-free boot) AND Gate 1 (three-runtime provider-free topology) both pass.
    It cannot be constructed directly (a manual attempt raises), so any future live
    indexing seam guarded by ``require_real_preflight_authorization`` is unreachable
    without both gates (fail-closed). It authorizes NO provider run by itself
    (task §35): a separate operator decision is still required."""

    __slots__ = ("fixture_hash", "run_id", "runtime_count")

    def __init__(
        self, key: object, *, fixture_hash: str, run_id: str, runtime_count: int
    ) -> None:
        if key is not _AUTH_KEY:
            raise PermissionError(
                "RealLightRAGPreflightAuthorization is minted only after Gate 0 AND "
                "Gate 1 PASS; it cannot be constructed directly (fail-closed, §34)"
            )
        self.fixture_hash = fixture_hash
        self.run_id = run_id
        self.runtime_count = runtime_count


def mint_real_preflight_authorization(
    *,
    gate0_passed: bool,
    gate1_passed: bool,
    fixture_hash: str,
    run_id: str,
    runtime_count: int,
) -> Optional[RealLightRAGPreflightAuthorization]:
    """Mint the capability iff BOTH gates passed; else None (task §34)."""
    if gate0_passed and gate1_passed:
        return RealLightRAGPreflightAuthorization(
            _AUTH_KEY,
            fixture_hash=fixture_hash,
            run_id=run_id,
            runtime_count=runtime_count,
        )
    return None


def require_real_preflight_authorization(
    auth: Optional[RealLightRAGPreflightAuthorization],
) -> RealLightRAGPreflightAuthorization:
    """Guard a FUTURE indexing seam. Raises without a valid real-preflight auth.

    This is the enforcement seam making it impossible to reach live indexing through
    the normal PN02 path without both real gates (task §34). PN02D-A implements NO
    indexing — this only proves the seam is gated."""
    if not isinstance(auth, RealLightRAGPreflightAuthorization):
        raise IndexingGateBlocked(
            "future live indexing is blocked: no valid RealLightRAGPreflight"
            "Authorization (Gate 0 and/or Gate 1 did not PASS). Fail-closed (§34)."
        )
    return auth


def assert_indexing_gated(
    auth: Optional[RealLightRAGPreflightAuthorization],
) -> None:
    """Structural proof that indexing is gated: validates the capability and returns.

    Deliberately performs NO indexing (task §34): PN02D-A must not implement or reach
    any real document-insert/query seam. It exists so a test can prove the gate holds
    and denies without a valid authorization."""
    require_real_preflight_authorization(auth)


__all__ = [
    "RealAttestationError",
    "IndexingGateBlocked",
    "RealVersionAttestation",
    "attest_version",
    "RealRuntimeAttestation",
    "attest_real_runtime",
    "RealLightRAGPreflightAuthorization",
    "mint_real_preflight_authorization",
    "require_real_preflight_authorization",
    "assert_indexing_gated",
]
