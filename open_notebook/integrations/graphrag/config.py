"""Configuration for the experimental LightRAG GraphRAG integration.

Read at call time (not import time) so a redeploy that flips the flag takes
effect without code changes, and so importing this module never depends on the
sidecar being configured or reachable. See AGR-005 §21.3.

The flag defaults to OFF: with it unset, Open Notebook behaves exactly as it
does today and no client is ever instantiated.
"""

import math
import os
from dataclasses import dataclass

# LightRAG's own default port (lightrag/api/config.py). Only used to make the
# example in .env.example concrete; there is no implicit fallback URL - an
# unset base URL is a configuration error, not a guess at localhost.
DEFAULT_TIMEOUT_SECONDS = 30.0

# GraphRAG-03C deletion-drain scheduling defaults. All are bounded so a bad env
# value can never produce a tight loop, a zero retry delay, or an unbounded scan.
DEFAULT_DRAIN_INTERVAL_SECONDS = 300.0  # periodic wake-up cadence
MIN_DRAIN_INTERVAL_SECONDS = 30.0  # floor: never a sub-minute hot wake-up
DEFAULT_DRAIN_BATCH_SIZE = 50  # rows per due-set query
MAX_DRAIN_BATCH_SIZE = 200  # LightRAG paginated page_size ceiling / sane cap
DEFAULT_DRAIN_MAX_ROWS = 500  # hard cap on rows processed per drain command
MAX_DRAIN_MAX_ROWS = 5000  # absolute ceiling: a bad env can't force an unbounded scan
DEFAULT_DRAIN_RETRY_DELAY_SECONDS = 60  # per-row defer on non-convergence
MIN_DRAIN_RETRY_DELAY_SECONDS = 5  # floor: positive, never zero-delay retry

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


@dataclass(frozen=True)
class GraphRAGDrainConfig:
    """Bounded scheduling knobs for the GraphRAG-03C deletion drain.

    Separate from GraphRAGConfig because these tune the background lifecycle, not
    a per-request sidecar call. Every value is clamped to a safe range on load so
    a misconfigured env var can never create a hot loop, a zero-delay retry, or an
    unbounded scan.
    """

    interval_seconds: float  # periodic wake-up cadence (>= MIN_DRAIN_INTERVAL)
    batch_size: int  # rows per due-set query (1..MAX_DRAIN_BATCH_SIZE)
    max_rows: int  # hard cap on rows processed per drain command
    retry_delay_seconds: int  # per-row defer on non-convergence (>= MIN floor)


def _parse_positive(raw: str, default: float) -> float:
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError:
        return default
    # Reject non-finite (inf/nan, e.g. "1e309") BEFORE any int() conversion: an
    # infinite value would raise OverflowError at clamp time and could break the
    # whole drain config load. A bad value falls back to the safe default.
    if not math.isfinite(value) or value <= 0:
        return default
    return value


def load_drain_config() -> GraphRAGDrainConfig:
    """Load and CLAMP the deletion-drain scheduling knobs from the environment."""
    interval = _parse_positive(
        _env("OPEN_NOTEBOOK_GRAPHRAG_DRAIN_INTERVAL_SECONDS"),
        DEFAULT_DRAIN_INTERVAL_SECONDS,
    )
    batch = int(
        _parse_positive(
            _env("OPEN_NOTEBOOK_GRAPHRAG_DRAIN_BATCH_SIZE"),
            float(DEFAULT_DRAIN_BATCH_SIZE),
        )
    )
    max_rows = int(
        _parse_positive(
            _env("OPEN_NOTEBOOK_GRAPHRAG_DRAIN_MAX_ROWS"),
            float(DEFAULT_DRAIN_MAX_ROWS),
        )
    )
    retry_delay = int(
        _parse_positive(
            _env("OPEN_NOTEBOOK_GRAPHRAG_DRAIN_RETRY_DELAY_SECONDS"),
            float(DEFAULT_DRAIN_RETRY_DELAY_SECONDS),
        )
    )
    return GraphRAGDrainConfig(
        interval_seconds=max(MIN_DRAIN_INTERVAL_SECONDS, interval),
        batch_size=max(1, min(MAX_DRAIN_BATCH_SIZE, batch)),
        max_rows=max(1, min(MAX_DRAIN_MAX_ROWS, max_rows)),
        retry_delay_seconds=max(MIN_DRAIN_RETRY_DELAY_SECONDS, retry_delay),
    )
