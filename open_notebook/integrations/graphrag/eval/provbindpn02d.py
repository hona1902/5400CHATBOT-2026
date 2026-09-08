"""Provider-binding materialization for the PN02 live driver (PN02D-B0B).

EVALUATION-ONLY. Nothing in production imports this (``PRODUCTION_IMPORTS_EVAL =
NO``). Implements the frozen binding layer (design §9/§10/§35, task §9/§10/§46/§47):
the driver injects the frozen OpenRouter binding into each PN02 sidecar boot, but
**provider-binding materialization is unreachable before a genuine
``PN02ProviderRunAuthorization``** (``PROVIDER_AUTHORIZATION_PRECEDES_BINDING =
YES``, design §R1.9). B0B implements the structures + the auth-gated materialization
and a content-safe boot descriptor; it boots NO container and reads NO secret VALUE
(``B0B_PROVIDER_TRAFFIC = 0``, design §R1.10).

Secret safety (design §R1.11/§32, task §46/§47): every artifact carries only public
config + secret-env NAMES; a secret VALUE is never stored, hashed, logged, or placed
on argv. The container secret vars are passed by env-inheritance at the (future,
authorized) launch boundary — modelled here as a name-only map. A required-secret
name-presence check produces a content-safe ``REQUIRED_RUNTIME_SECRET_MISSING=<name>``
and NEVER reads the value (design §9/§35).
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import AbstractSet, Dict, Optional, Tuple

from open_notebook.integrations.graphrag.eval.authlivepn02d import (
    PN02ProviderRunAuthorization,
    frozen_provider_config_id,
    require_provider_run_authorization,
)
from open_notebook.integrations.graphrag.eval.provider_binding08 import (
    DiagnosticProviderBinding08,
    frozen_provider_binding,
)


class ProviderBindingMaterializationError(RuntimeError):
    """A provider binding could not be materialized (fail-closed, design §9/§35)."""


def env_present_secret_names(names: AbstractSet[str]) -> Tuple[str, ...]:
    """Content-safe name-presence check over ``os.environ`` (NEVER reads a value).

    Returns the subset of ``names`` set to a non-empty string. Used as the DEFAULT
    real presence resolver; the offline simulation injects a simulated present-set
    instead so it never depends on ambient ``.env`` (design §R1.10).
    """
    return tuple(sorted(n for n in names if os.environ.get(n, "").strip()))


@dataclass(frozen=True)
class MaterializedProviderBinding:
    """Content-safe result of materializing the frozen binding (design §9). No secret."""

    provider_config_id: str
    llm_model: str
    embedding_model: str
    embedding_dim: int
    llm_host: str
    required_secret_envs: Tuple[str, ...]
    present_secret_envs: Tuple[str, ...]
    #: container var NAME -> public value (safe for argv). NEVER a secret.
    container_public_env: Dict[str, str]
    #: container secret var NAME -> source env NAME whose value it inherits (names only).
    container_secret_env_map: Dict[str, str]
    attested: bool
    failure_reasons: Tuple[str, ...]

    def as_public_dict(self) -> Dict[str, object]:
        return {
            "provider_config_id": self.provider_config_id,
            "llm_model": self.llm_model,
            "embedding_model": self.embedding_model,
            "embedding_dim": self.embedding_dim,
            "llm_host": self.llm_host,
            "required_secret_envs": list(self.required_secret_envs),
            "present_secret_envs": list(self.present_secret_envs),
            "container_public_env": dict(self.container_public_env),
            "container_secret_env_map": dict(self.container_secret_env_map),
            "attested": self.attested,
            "failure_reasons": list(self.failure_reasons),
        }


def materialize_provider_binding(
    provider_run_auth: Optional[PN02ProviderRunAuthorization],
    *,
    binding: Optional[DiagnosticProviderBinding08] = None,
    present_secret_envs: Optional[AbstractSet[str]] = None,
    require_secret_present: bool = True,
) -> MaterializedProviderBinding:
    """Materialize the frozen binding — REJECTS before authorization (design §10).

    ``present_secret_envs`` is the injected name-presence set (a SIMULATED presence
    in B0B; ``None`` falls back to the content-safe ``os.environ`` name check). No
    secret value is ever read. Fails closed on: missing provider-run auth, binding
    drift, provider-config mismatch, or (when ``require_secret_present``) a missing
    required secret NAME.
    """
    # §10: materialization is unreachable before a genuine provider-run auth.
    auth = require_provider_run_authorization(provider_run_auth)

    binding = binding or frozen_provider_binding()
    binding.validate()  # exactly the frozen benchmark binding, else fail closed

    provider_config_id = frozen_provider_config_id()
    reasons = []
    if provider_config_id != auth.approved_provider_config_id:
        reasons.append("provider_config_fingerprint_mismatch")

    required = binding.required_secret_envs()
    if present_secret_envs is None:
        present = env_present_secret_names(set(required))
    else:
        present = tuple(sorted(set(present_secret_envs) & set(required)))
    if require_secret_present:
        for name in required:
            if name not in present:
                reasons.append(f"REQUIRED_RUNTIME_SECRET_MISSING={name}")

    return MaterializedProviderBinding(
        provider_config_id=provider_config_id,
        llm_model=binding.llm_model,
        embedding_model=binding.embedding_model,
        embedding_dim=binding.embedding_dim,
        llm_host=binding.llm_host,
        required_secret_envs=tuple(required),
        present_secret_envs=tuple(present),
        container_public_env=binding.container_public_env(),
        container_secret_env_map=binding.container_secret_env_map(),
        attested=not reasons,
        failure_reasons=tuple(reasons),
    )


@dataclass(frozen=True)
class SidecarBootPlan:
    """Content-safe provider-bound sidecar boot descriptor (design §9). No secret.

    Public env vars carry values; secret env vars carry only the SOURCE env NAME the
    value would be inherited from (bare ``-e NAME`` at the future launch boundary).
    B0B never executes this — it is a plan for a later authorized B1.
    """

    notebook_id: str
    workspace_id: str
    endpoint: str
    public_env: Dict[str, str]
    inherit_secret_env: Dict[str, str]

    def as_public_dict(self) -> Dict[str, object]:
        return {
            "notebook_id": self.notebook_id,
            "workspace_id": self.workspace_id,
            "endpoint": self.endpoint,
            "public_env": dict(self.public_env),
            "inherit_secret_env": dict(self.inherit_secret_env),
        }


def plan_sidecar_boot(
    provider_run_auth: Optional[PN02ProviderRunAuthorization],
    *,
    notebook_id: str,
    workspace_id: str,
    endpoint: str,
    materialized: MaterializedProviderBinding,
) -> SidecarBootPlan:
    """Build a content-safe provider-bound boot plan — REJECTS before auth (design §10)."""
    require_provider_run_authorization(provider_run_auth)
    if not materialized.attested:
        raise ProviderBindingMaterializationError(
            "cannot plan a provider-bound sidecar boot from an unattested binding "
            f"({', '.join(materialized.failure_reasons) or 'unknown'})"
        )
    return SidecarBootPlan(
        notebook_id=notebook_id,
        workspace_id=workspace_id,
        endpoint=endpoint,
        public_env=dict(materialized.container_public_env),
        inherit_secret_env=dict(materialized.container_secret_env_map),
    )


__all__ = [
    "ProviderBindingMaterializationError",
    "env_present_secret_names",
    "MaterializedProviderBinding",
    "materialize_provider_binding",
    "SidecarBootPlan",
    "plan_sidecar_boot",
]
