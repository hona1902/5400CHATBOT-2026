"""GraphRAG-08E.5 frozen per-cell provider-binding CONTRACT (EVALUATION-ONLY).

Nothing in production imports this. It carries the EXACT frozen provider configuration a
diagnostic LightRAG cell container needs so its extraction/embedding go to the pinned
OpenRouter models — closing the Reauthorization #4 blocker (fresh cell containers started
with no LLM/embedding binding fell back to the image's default local Ollama).

Secret SAFETY is the whole point of the shape here: this structure holds ONLY content-safe
values — the public binding/model/host strings and the NAMES of the environment variables
that hold the secrets — NEVER a secret value. Secret values are resolved LATE, at the
container-launch boundary, and passed to Docker by environment INHERITANCE (never on argv),
so a provider key can never reach a repr, log, artifact, ``AttemptRecord``, or the process
command line (task §7/§8/§9/§10).

The values are FROZEN to the benchmark (task §5/§15): OpenRouter, LLM ``openai/gpt-4o-mini``,
embedding ``openai/text-embedding-3-small`` (dim 1536), against the OpenRouter OpenAI-
compatible endpoint. ``validate()`` rejects any drift; this is NOT a generic model-selection
feature. Verified against ``deploy/graphrag-poc/docker-compose.graphrag.yml`` for the exact
container variable names (``LLM_BINDING``/``LLM_MODEL``/``LLM_BINDING_HOST``/
``LLM_BINDING_API_KEY`` and the ``EMBEDDING_*`` equivalents) the pinned v1.5.6 image reads.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Tuple

# ---- frozen benchmark provider configuration (public, content-safe) --------

FROZEN_LLM_BINDING = "openai"  # OpenRouter is OpenAI-compatible
FROZEN_LLM_MODEL = "openai/gpt-4o-mini"
FROZEN_EMBEDDING_BINDING = "openai"
FROZEN_EMBEDDING_MODEL = "openai/text-embedding-3-small"
FROZEN_EMBEDDING_DIM = 1536
#: The OpenRouter OpenAI-compatible endpoint (public; NOT a credential).
FROZEN_OPENROUTER_HOST = "https://openrouter.ai/api/v1"
#: The environment variable NAME that holds the OpenRouter provider credential. This is
#: the EXTERNAL provider secret — SEPARATE from the sidecar auth key
#: (``GRAPHRAG_POC_API_KEY``); the two must not be conflated (task §6).
FROZEN_PROVIDER_SECRET_ENV = "OPENROUTER_API_KEY"

# Exact container env variable NAMES the pinned v1.5.6 image reads (compose-verified).
CONTAINER_LLM_BINDING = "LLM_BINDING"
CONTAINER_LLM_MODEL = "LLM_MODEL"
CONTAINER_LLM_HOST = "LLM_BINDING_HOST"
CONTAINER_LLM_SECRET = "LLM_BINDING_API_KEY"
CONTAINER_EMBEDDING_BINDING = "EMBEDDING_BINDING"
CONTAINER_EMBEDDING_MODEL = "EMBEDDING_MODEL"
CONTAINER_EMBEDDING_HOST = "EMBEDDING_BINDING_HOST"
CONTAINER_EMBEDDING_SECRET = "EMBEDDING_BINDING_API_KEY"


class ProviderBindingError(RuntimeError):
    """The provider binding drifted from the frozen benchmark config (fail closed)."""


@dataclass(frozen=True)
class DiagnosticProviderBinding08:
    """Content-safe frozen provider binding: public config + secret ENV NAMES only.

    Every field is safe to repr/log/persist — there is NO secret value here. The secret
    is referenced by the NAME of the env var that holds it (``*_secret_env``); the value is
    resolved only at container launch."""

    llm_binding: str
    llm_model: str
    llm_host: str
    llm_secret_env: str  # NAME of the env var holding the LLM provider key (not the value)
    embedding_binding: str
    embedding_model: str
    embedding_host: str
    embedding_secret_env: str  # NAME of the env var holding the embedding provider key
    #: The frozen benchmark embedding dimension (aligns with
    #: ``precheck08._EXPECTED_EMBED_DIM``). Recorded + validated as the frozen value, but
    #: INTENTIONALLY NOT injected as a container env var: the approved compose
    #: (``docker-compose.graphrag.yml``) also omits ``EMBEDDING_DIM``, the pinned image's
    #: ``openai`` embedding binding derives the dimension from the model/endpoint, and
    #: injecting a var compose does not set would break the §44 compose-parity guarantee.
    #: A live run's first cell embedding is where any real dimension mismatch would surface
    #: (as a uniform, operator-visible failure) — flagged for the reauthorization.
    embedding_dim: int

    def validate(self) -> None:
        """Fail closed unless this is EXACTLY the frozen benchmark binding (task §14/§15)."""
        if self.llm_binding != FROZEN_LLM_BINDING:
            raise ProviderBindingError(
                f"llm_binding {self.llm_binding!r} != frozen {FROZEN_LLM_BINDING!r}"
            )
        if self.llm_model != FROZEN_LLM_MODEL:
            raise ProviderBindingError(
                f"llm_model {self.llm_model!r} != frozen {FROZEN_LLM_MODEL!r}"
            )
        if not self.llm_host:
            raise ProviderBindingError("llm_host missing (no localhost/Ollama fallback)")
        if not self.llm_secret_env:
            raise ProviderBindingError("llm_secret_env (secret variable name) missing")
        if self.embedding_binding != FROZEN_EMBEDDING_BINDING:
            raise ProviderBindingError(
                f"embedding_binding {self.embedding_binding!r} != frozen "
                f"{FROZEN_EMBEDDING_BINDING!r}"
            )
        if self.embedding_model != FROZEN_EMBEDDING_MODEL:
            raise ProviderBindingError(
                f"embedding_model {self.embedding_model!r} != frozen {FROZEN_EMBEDDING_MODEL!r}"
            )
        if not self.embedding_host:
            raise ProviderBindingError("embedding_host missing (no localhost fallback)")
        if not self.embedding_secret_env:
            raise ProviderBindingError("embedding_secret_env (secret variable name) missing")
        if self.embedding_dim != FROZEN_EMBEDDING_DIM:
            raise ProviderBindingError(
                f"embedding_dim {self.embedding_dim} != frozen {FROZEN_EMBEDDING_DIM}"
            )

    def container_public_env(self) -> Dict[str, str]:
        """PUBLIC container env vars (binding/model/host) — safe to place on argv. NO key."""
        return {
            CONTAINER_LLM_BINDING: self.llm_binding,
            CONTAINER_LLM_MODEL: self.llm_model,
            CONTAINER_LLM_HOST: self.llm_host,
            CONTAINER_EMBEDDING_BINDING: self.embedding_binding,
            CONTAINER_EMBEDDING_MODEL: self.embedding_model,
            CONTAINER_EMBEDDING_HOST: self.embedding_host,
        }

    def container_secret_env_map(self) -> Dict[str, str]:
        """Map each SECRET container var NAME -> the source env NAME whose value it takes.

        The launch boundary resolves the value from the source env and hands it to Docker by
        inheritance (bare ``-e <NAME>``), so no secret value is ever on argv (task §10)."""
        return {
            CONTAINER_LLM_SECRET: self.llm_secret_env,
            CONTAINER_EMBEDDING_SECRET: self.embedding_secret_env,
        }

    def required_secret_envs(self) -> Tuple[str, ...]:
        """Distinct source env NAMES that must be present for a live launch (task §35/§61)."""
        return tuple(sorted({self.llm_secret_env, self.embedding_secret_env}))

    def as_public_dict(self) -> Dict[str, object]:
        """Content-safe view for docs/attestation/reporting — names + public values only."""
        return {
            "llm_binding": self.llm_binding,
            "llm_model": self.llm_model,
            "llm_host": self.llm_host,
            "llm_secret_env": self.llm_secret_env,
            "embedding_binding": self.embedding_binding,
            "embedding_model": self.embedding_model,
            "embedding_host": self.embedding_host,
            "embedding_secret_env": self.embedding_secret_env,
            "embedding_dim": self.embedding_dim,
        }


def frozen_provider_binding() -> DiagnosticProviderBinding08:
    """The single approved frozen benchmark binding (OpenRouter / gpt-4o-mini /
    text-embedding-3-small). Constructed + validated; carries NO secret value."""
    binding = DiagnosticProviderBinding08(
        llm_binding=FROZEN_LLM_BINDING,
        llm_model=FROZEN_LLM_MODEL,
        llm_host=FROZEN_OPENROUTER_HOST,
        llm_secret_env=FROZEN_PROVIDER_SECRET_ENV,
        embedding_binding=FROZEN_EMBEDDING_BINDING,
        embedding_model=FROZEN_EMBEDDING_MODEL,
        embedding_host=FROZEN_OPENROUTER_HOST,
        embedding_secret_env=FROZEN_PROVIDER_SECRET_ENV,
        embedding_dim=FROZEN_EMBEDDING_DIM,
    )
    binding.validate()
    return binding


__all__ = [
    "FROZEN_LLM_BINDING",
    "FROZEN_LLM_MODEL",
    "FROZEN_EMBEDDING_BINDING",
    "FROZEN_EMBEDDING_MODEL",
    "FROZEN_EMBEDDING_DIM",
    "FROZEN_OPENROUTER_HOST",
    "FROZEN_PROVIDER_SECRET_ENV",
    "CONTAINER_LLM_BINDING",
    "CONTAINER_LLM_MODEL",
    "CONTAINER_LLM_HOST",
    "CONTAINER_LLM_SECRET",
    "CONTAINER_EMBEDDING_BINDING",
    "CONTAINER_EMBEDDING_MODEL",
    "CONTAINER_EMBEDDING_HOST",
    "CONTAINER_EMBEDDING_SECRET",
    "ProviderBindingError",
    "DiagnosticProviderBinding08",
    "frozen_provider_binding",
]
