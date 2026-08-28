"""Configuration for the experimental LightRAG GraphRAG integration.

Read at call time (not import time) so a redeploy that flips the flag takes
effect without code changes, and so importing this module never depends on the
sidecar being configured or reachable. See AGR-005 §21.3.

The flag defaults to OFF: with it unset, Open Notebook behaves exactly as it
does today and no client is ever instantiated.
"""

import os
from dataclasses import dataclass

# LightRAG's own default port (lightrag/api/config.py). Only used to make the
# example in .env.example concrete; there is no implicit fallback URL - an
# unset base URL is a configuration error, not a guess at localhost.
DEFAULT_TIMEOUT_SECONDS = 30.0

# Pinned upstream revision this integration was written against. Recorded here
# so a sidecar upgrade that changes the HTTP contract is a deliberate, visible
# decision rather than a silent runtime surprise.
VERIFIED_LIGHTRAG_VERSION = "v1.5.6"


def _env(name: str) -> str:
    return os.environ.get(name, "").strip()


@dataclass(frozen=True)
class GraphRAGConfig:
    """Resolved GraphRAG integration settings."""

    enabled: bool
    base_url: str
    timeout: float
    api_key: str | None

    @property
    def configured(self) -> bool:
        """True when the integration is both enabled and has a target URL."""
        return self.enabled and bool(self.base_url)


def _parse_timeout(raw: str) -> float:
    """Parse the timeout, falling back to the default on anything unusable.

    A malformed timeout must not raise at import/config time: that would turn a
    typo in the environment into a hard failure of an optional, disabled-by-
    default feature.
    """
    if not raw:
        return DEFAULT_TIMEOUT_SECONDS
    try:
        value = float(raw)
    except ValueError:
        return DEFAULT_TIMEOUT_SECONDS
    if value <= 0:
        return DEFAULT_TIMEOUT_SECONDS
    return value


def load_config() -> GraphRAGConfig:
    """Load GraphRAG settings from the environment.

    Called per-request rather than cached at import so tests and redeploys see
    current values.
    """
    return GraphRAGConfig(
        enabled=_env("OPEN_NOTEBOOK_GRAPHRAG_ENABLED").lower() in {"1", "true", "yes"},
        # rstrip("/") mirrors the OPENAI_COMPATIBLE_BASE_URL handling in
        # open_notebook/ai/models.py so callers can set either form.
        base_url=_env("OPEN_NOTEBOOK_GRAPHRAG_BASE_URL").rstrip("/"),
        timeout=_parse_timeout(_env("OPEN_NOTEBOOK_GRAPHRAG_TIMEOUT")),
        # Optional: LightRAG only enforces X-API-Key when it is configured to.
        # Never logged, never echoed back in any response.
        api_key=_env("OPEN_NOTEBOOK_GRAPHRAG_API_KEY") or None,
    )
