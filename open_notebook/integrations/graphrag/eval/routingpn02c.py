"""PN02C routing + attestation gate over REAL owned runtimes (task §12-§16/§32-§34).

EVALUATION-ONLY. Nothing in production imports this. Where PN02B attests against
non-openable ``eval-null://`` placeholders, PN02C attests against the REAL owned
endpoints/storage produced by ``provisionpn02c`` and proves the router is
fail-closed:

  * a correct notebook->workspace->endpoint route is accepted,
  * a wrong endpoint or wrong workspace route is REJECTED (task §14),
  * an unattested route can never reach a future GD authorization (task §15),
  * no two runtimes alias one storage root (task §16).

Provider configuration is attested by NAME/shape only — NO provider call is ever
made (task §33/§34). Reuses the PN02B ``WorkspaceAttestation`` /
``require_attested_before_query`` / ``provider_binding_fingerprint`` primitives so
there is a single attestation vocabulary across PN02B and PN02C.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Dict, Optional, Tuple

from open_notebook.integrations.graphrag.eval.manifestpn02 import (
    LIGHTRAG_EVAL_VERSION,
    WorkspaceAttestation,
    canonical_version,
    provider_binding_fingerprint,
    require_attested_before_query,
)
from open_notebook.integrations.graphrag.eval.provisionpn02c import (
    ProvisionedTopology,
    RuntimeIdentity,
)

# Frozen provider CONFIGURATION (task §33) — names/shape only, NEVER invoked.
EXPECTED_LLM_MODEL = "openai/gpt-4o-mini"
EXPECTED_EMBEDDING_MODEL = "openai/text-embedding-3-small"
EXPECTED_EMBEDDING_DIM = 1536
#: The env var NAME (never its value) that would carry the future provider key.
PROVIDER_KEY_ENV_NAME = "OPENROUTER_API_KEY"


class RoutingViolation(RuntimeError):
    """A notebook was routed to a runtime/workspace it does not own (task §14)."""


class WrongEndpointRoutingError(RoutingViolation):
    """A notebook was routed to another notebook's endpoint (task §14)."""


class WrongWorkspaceRoutingError(RoutingViolation):
    """A notebook was routed to another notebook's workspace (task §14)."""


class ProviderConfigError(ValueError):
    """The frozen provider CONFIGURATION does not match expectations (task §33)."""


# --------------------------------------------------------------------------- #
# Provider configuration attestation (task §33/§34) — NO calls
# --------------------------------------------------------------------------- #

def attest_provider_config(
    llm_model: str = EXPECTED_LLM_MODEL,
    embedding_model: str = EXPECTED_EMBEDDING_MODEL,
    embedding_dim: int = EXPECTED_EMBEDDING_DIM,
) -> None:
    """Verify the configured model NAMES + embedding dim (task §33). No provider call."""
    if llm_model != EXPECTED_LLM_MODEL:
        raise ProviderConfigError(f"LLM model {llm_model!r} != {EXPECTED_LLM_MODEL!r}")
    if embedding_model != EXPECTED_EMBEDDING_MODEL:
        raise ProviderConfigError(
            f"embedding model {embedding_model!r} != {EXPECTED_EMBEDDING_MODEL!r}"
        )
    if embedding_dim != EXPECTED_EMBEDDING_DIM:
        raise ProviderConfigError(
            f"embedding dim {embedding_dim} != {EXPECTED_EMBEDDING_DIM}"
        )


def provider_binding_configured(env_name: str = PROVIDER_KEY_ENV_NAME) -> bool:
    """True iff the provider key env var NAME is present (task §34).

    NEVER returns, logs, or hashes the value — only whether the name is set to a
    non-empty string. The absence of the key is NOT a PN02C failure (preflight is
    provider-free); it is reported as PROVIDER_BINDING_CONFIGURED=NO.
    """
    return bool(os.environ.get(env_name, "").strip())


def safe_provider_config_identity() -> str:
    """A secret-free config identity string for the binding fingerprint (task §32)."""
    return (
        f"lightrag={LIGHTRAG_EVAL_VERSION};"
        f"llm={EXPECTED_LLM_MODEL};"
        f"emb={EXPECTED_EMBEDDING_MODEL};"
        f"dim={EXPECTED_EMBEDDING_DIM};"
        f"key_env={PROVIDER_KEY_ENV_NAME};"
        "synthetic=true"
    )


# --------------------------------------------------------------------------- #
# Config fingerprint (task §32)
# --------------------------------------------------------------------------- #

def runtime_config_fingerprint(identity: RuntimeIdentity) -> str:
    """Content-safe per-runtime configuration fingerprint (task §32). No secrets."""
    safe_identity = (
        f"ws={identity.workspace_id};"
        f"store={identity.storage_identity};"
        f"endpoint={identity.endpoint};"
        f"version={identity.lightrag_version_config};"
        f"{safe_provider_config_identity()}"
    )
    return provider_binding_fingerprint(safe_identity)


# --------------------------------------------------------------------------- #
# Attestation over a REAL owned runtime (task §12/§13)
# --------------------------------------------------------------------------- #

def attest_runtime(
    identity: RuntimeIdentity,
    *,
    observed_workspace_id: str,
    observed_endpoint: str,
    observed_storage_identity: str,
    observed_lightrag_version: str,
    observed_synthetic_only: bool = True,
    provider_execution_authorized: bool = False,
) -> WorkspaceAttestation:
    """Attest one runtime by comparing observations to its frozen identity (§12).

    ``attested`` is True only when EVERY field matches AND synthetic-only holds AND
    provider-backed execution is NOT authorized (preflight must remain live-unsafe).
    """
    reasons = []
    if observed_workspace_id != identity.workspace_id:
        reasons.append("workspace_id_mismatch")
    if observed_endpoint != identity.endpoint:
        reasons.append("endpoint_ownership_mismatch")
    if observed_storage_identity != identity.storage_identity:
        reasons.append("storage_identity_mismatch")
    if canonical_version(observed_lightrag_version) != canonical_version(
        LIGHTRAG_EVAL_VERSION
    ):
        reasons.append("lightrag_version_mismatch")
    if not observed_synthetic_only:
        reasons.append("synthetic_only_false")
    if provider_execution_authorized:
        reasons.append("provider_execution_authorized_true")
    return WorkspaceAttestation(
        notebook_id=identity.notebook_id,
        workspace_id=identity.workspace_id,
        owned_endpoint_identity=identity.endpoint,
        expected_storage_identity=identity.storage_identity,
        lightrag_version=observed_lightrag_version,
        provider_binding_fingerprint=runtime_config_fingerprint(identity),
        attested=not reasons,
        failure_reasons=tuple(reasons),
    )


def attest_from_runtime(
    topology: ProvisionedTopology, notebook_id: str
) -> WorkspaceAttestation:
    """Attest a running runtime by reading its provider-free loopback /identity."""
    identity = topology.identities[notebook_id]
    runtime = topology.runtimes.get(notebook_id)
    if runtime is not None:
        observed = runtime.fetch_identity()
        return attest_runtime(
            identity,
            observed_workspace_id=str(observed.get("workspace_id")),
            observed_endpoint=runtime.endpoint,
            observed_storage_identity=str(observed.get("storage_identity")),
            observed_lightrag_version=str(observed.get("lightrag_version_config")),
            observed_synthetic_only=bool(observed.get("synthetic_only")),
        )
    # start=False structural mode: attest against the frozen identity directly.
    return attest_runtime(
        identity,
        observed_workspace_id=identity.workspace_id,
        observed_endpoint=identity.endpoint,
        observed_storage_identity=identity.storage_identity,
        observed_lightrag_version=identity.lightrag_version_config,
        observed_synthetic_only=identity.synthetic_only,
    )


def attest_all(
    topology: ProvisionedTopology,
) -> Tuple[Dict[str, WorkspaceAttestation], bool]:
    """Attest every runtime; returns (per-notebook attestations, all_attested)."""
    out: Dict[str, WorkspaceAttestation] = {}
    for nb in topology.notebook_ids:
        out[nb] = attest_from_runtime(topology, nb)
    all_ok = all(a.attested for a in out.values())
    return out, all_ok


# --------------------------------------------------------------------------- #
# Router — wrong-route rejection (task §14) — NO graph query
# --------------------------------------------------------------------------- #

def route_query(
    topology: ProvisionedTopology,
    notebook_id: str,
    *,
    target_endpoint: str,
    target_workspace_id: str,
) -> RuntimeIdentity:
    """Return the owned runtime for a route, or REJECT a mismatch (task §14).

    This is an infrastructure-level ownership check; it performs NO retrieval and
    reads NO graph data. A notebook may only be routed to the endpoint AND
    workspace it owns.
    """
    if notebook_id not in topology.identities:
        raise RoutingViolation(f"unknown notebook {notebook_id!r}")
    identity = topology.identities[notebook_id]
    if target_endpoint != identity.endpoint:
        owner = topology.owner_of_endpoint(target_endpoint)
        raise WrongEndpointRoutingError(
            f"{notebook_id} routed to endpoint owned by {owner or 'unknown'} "
            f"(expected its own {identity.endpoint})"
        )
    if target_workspace_id != identity.workspace_id:
        raise WrongWorkspaceRoutingError(
            f"{notebook_id} routed to workspace {target_workspace_id!r} "
            f"(expected {identity.workspace_id!r})"
        )
    return identity


def authorize_future_gd(
    topology: ProvisionedTopology,
    notebook_id: str,
    attestation: WorkspaceAttestation,
    *,
    target_endpoint: Optional[str] = None,
    target_workspace_id: Optional[str] = None,
) -> RuntimeIdentity:
    """Contract a FUTURE GD query must pass — REJECTS unattested/mis-routed (§15).

    Does NOT call ``/query/data`` or any provider seam: it only proves the
    authorization *gate* refuses an unattested or wrongly-routed request. The
    actual GD seam remains the inert, non-invocable PN02B stub.
    """
    identity = topology.identities[notebook_id]
    # Fail-closed on attestation first (task §15).
    require_attested_before_query(attestation)
    if attestation.notebook_id != notebook_id:
        raise RoutingViolation(
            f"attestation is for {attestation.notebook_id!r}, not {notebook_id!r}"
        )
    return route_query(
        topology,
        notebook_id,
        target_endpoint=target_endpoint or identity.endpoint,
        target_workspace_id=target_workspace_id or identity.workspace_id,
    )


# --------------------------------------------------------------------------- #
# Cross-workspace storage aliasing (task §16)
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class StorageAliasingReport:
    aliasing_count: int
    distinct_storage_identities: int
    distinct_storage_roots: int


def cross_workspace_storage_aliasing(
    topology: ProvisionedTopology,
) -> StorageAliasingReport:
    """Count structural storage aliases across runtimes — must be 0 (task §16).

    An alias is any pair of distinct notebooks whose storage identity OR real
    storage root path coincides or nests. Runtime A cannot be represented as
    owning storage B without producing a nonzero count here.
    """
    ids = topology.identities
    idents = list(ids.values())
    aliases = 0
    for i in range(len(idents)):
        for j in range(i + 1, len(idents)):
            a, b = idents[i], idents[j]
            if a.storage_identity == b.storage_identity:
                aliases += 1
                continue
            ra = os.path.realpath(a.storage_root).rstrip("/\\") + os.sep
            rb = os.path.realpath(b.storage_root).rstrip("/\\") + os.sep
            if ra == rb or ra.startswith(rb) or rb.startswith(ra):
                aliases += 1
    return StorageAliasingReport(
        aliasing_count=aliases,
        distinct_storage_identities=len({i.storage_identity for i in idents}),
        distinct_storage_roots=len({os.path.realpath(i.storage_root) for i in idents}),
    )


__all__ = [
    "EXPECTED_LLM_MODEL",
    "EXPECTED_EMBEDDING_MODEL",
    "EXPECTED_EMBEDDING_DIM",
    "PROVIDER_KEY_ENV_NAME",
    "RoutingViolation",
    "WrongEndpointRoutingError",
    "WrongWorkspaceRoutingError",
    "ProviderConfigError",
    "attest_provider_config",
    "provider_binding_configured",
    "safe_provider_config_identity",
    "runtime_config_fingerprint",
    "attest_runtime",
    "attest_from_runtime",
    "attest_all",
    "route_query",
    "authorize_future_gd",
    "StorageAliasingReport",
    "cross_workspace_storage_aliasing",
]
