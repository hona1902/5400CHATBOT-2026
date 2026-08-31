"""GraphRAG-08C content-safe SIDECAR_START diagnostics (EVALUATION-ONLY).

Nothing in production imports this. Attempt #3 failed at SIDECAR_START (the sidecar
container started but never became healthy within the 120s wait) and the harness
recorded only the exception TYPE name — not enough to tell a slow-but-healthy start
from a crash, a closed port, or an HTTP error. This module turns the observable,
COARSE facts about a sidecar-start attempt into a content-free diagnostic + a single
reason code, mirroring the 08B ``FailureDiagnostic`` discipline.

Containment (task §7/§21): the diagnostic holds ONLY coarse booleans, enums, small
ints, and buckets. Raw container logs, the raw health-response body, and any
provider/config secret are NEVER read into a persisted field. The live gatherers use
targeted ``docker inspect`` field templates (never the full JSON, which carries env)
and read only a health call's success/status — never its body/detail.

This module changes NOTHING about the frozen 120s timeout, retry policy, or
concurrency; it only observes and classifies (task §9/§10).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Optional

# Coarse, content-free failure reason codes (task §8) plus a success sentinel.
SIDECAR_HEALTHY = "SIDECAR_HEALTHY"
SIDECAR_CONTAINER_NOT_CREATED = "SIDECAR_CONTAINER_NOT_CREATED"
SIDECAR_CONTAINER_EXITED = "SIDECAR_CONTAINER_EXITED"
SIDECAR_CONTAINER_RUNNING_NOT_HEALTHY = "SIDECAR_CONTAINER_RUNNING_NOT_HEALTHY"
SIDECAR_PORT_NOT_OPEN = "SIDECAR_PORT_NOT_OPEN"
SIDECAR_HEALTH_HTTP_UNREACHABLE = "SIDECAR_HEALTH_HTTP_UNREACHABLE"
SIDECAR_HEALTH_NON_SUCCESS = "SIDECAR_HEALTH_NON_SUCCESS"
SIDECAR_HEALTH_TIMEOUT = "SIDECAR_HEALTH_TIMEOUT"
SIDECAR_INSPECT_UNAVAILABLE = "SIDECAR_INSPECT_UNAVAILABLE"
SIDECAR_START_UNKNOWN = "SIDECAR_START_UNKNOWN"

# Docker's own coarse health enum (never provider text): starting/healthy/unhealthy,
# plus "none" when the image defines no HEALTHCHECK.
_DOCKER_HEALTH_STATES = frozenset({"starting", "healthy", "unhealthy", "none"})

_ELAPSED_BUCKETS = ((5, "0_5"), (30, "6_30"), (60, "31_60"), (120, "61_120"))


def _elapsed_bucket(elapsed_seconds: Optional[float]) -> str:
    if elapsed_seconds is None:
        return "UNKNOWN"
    if elapsed_seconds < 0:
        return "UNKNOWN"
    for upper, label in _ELAPSED_BUCKETS:
        if elapsed_seconds <= upper:
            return label
    return "GT_120"


def _status_class(status_code: Optional[int]) -> Optional[str]:
    if status_code is None:
        return None
    if 200 <= status_code < 300:
        return "2XX"
    if 400 <= status_code < 500:
        return "4XX"
    if 500 <= status_code < 600:
        return "5XX"
    return "OTHER"


@dataclass(frozen=True)
class SidecarObservation:
    """Raw COARSE observations of one sidecar-start attempt. No raw text ever.

    Every field is a small typed fact; ``None`` means "could not observe". This is the
    only input to classification, so nothing content-bearing can reach the verdict."""

    container_created: Optional[bool]
    container_running: Optional[bool]
    container_health_state: Optional[str]  # docker enum or None
    container_exit_code: Optional[int]
    container_restart_count: Optional[int]
    port_open: Optional[bool]
    health_http_reachable: Optional[bool]
    health_http_status_class: Optional[str]  # "2XX"|"4XX"|"5XX"|"OTHER"|None
    healthy: bool
    timeout_reached: bool


def classify_sidecar_start(obs: SidecarObservation) -> str:
    """Map a coarse observation to exactly one reason code (deterministic precedence).

    Precedence: success -> inspect availability -> container created -> running/exited
    -> port -> health reachability -> health status -> running-not-healthy vs timeout.
    Fail-closed to ``SIDECAR_START_UNKNOWN`` when evidence is insufficient (task §8:
    "do not infer more than observable evidence supports")."""
    if obs.healthy:
        return SIDECAR_HEALTHY
    if obs.container_created is None:
        return SIDECAR_INSPECT_UNAVAILABLE
    if obs.container_created is False:
        return SIDECAR_CONTAINER_NOT_CREATED
    # created:
    if obs.container_running is None:
        return SIDECAR_INSPECT_UNAVAILABLE
    if obs.container_running is False:
        return SIDECAR_CONTAINER_EXITED
    # running:
    if obs.port_open is False:
        return SIDECAR_PORT_NOT_OPEN
    if obs.health_http_reachable is False:
        return SIDECAR_HEALTH_HTTP_UNREACHABLE
    if obs.health_http_status_class in ("4XX", "5XX", "OTHER"):
        return SIDECAR_HEALTH_NON_SUCCESS
    # running, port open, health reachable, but not healthy:
    if obs.container_health_state in ("starting", "unhealthy"):
        return SIDECAR_CONTAINER_RUNNING_NOT_HEALTHY
    if obs.timeout_reached:
        return SIDECAR_HEALTH_TIMEOUT
    return SIDECAR_START_UNKNOWN


@dataclass(frozen=True)
class SidecarStartDiagnostic:
    """Content-free description of one sidecar-start attempt (task §7)."""

    health_wait_timeout_seconds: float
    elapsed_seconds: Optional[float]
    elapsed_bucket: str
    container_created: Optional[bool]
    container_running: Optional[bool]
    container_health_state: Optional[str]
    container_exit_code: Optional[int]
    container_restart_count: Optional[int]
    port_open: Optional[bool]
    health_http_reachable: Optional[bool]
    health_http_status_class: Optional[str]
    timeout_reached: bool
    healthy: bool
    failure_reason_code: str

    def as_dict(self) -> Dict[str, object]:
        return {
            "health_wait_timeout_seconds": self.health_wait_timeout_seconds,
            "elapsed_seconds": self.elapsed_seconds,
            "elapsed_bucket": self.elapsed_bucket,
            "container_created": self.container_created,
            "container_running": self.container_running,
            "container_health_state": self.container_health_state,
            "container_exit_code": self.container_exit_code,
            "container_restart_count": self.container_restart_count,
            "port_open": self.port_open,
            "health_http_reachable": self.health_http_reachable,
            "health_http_status_class": self.health_http_status_class,
            "timeout_reached": self.timeout_reached,
            "healthy": self.healthy,
            "failure_reason_code": self.failure_reason_code,
        }


def build_sidecar_diagnostic(
    obs: SidecarObservation,
    *,
    timeout_seconds: float,
    elapsed_seconds: Optional[float] = None,
) -> SidecarStartDiagnostic:
    """Assemble a content-free diagnostic from a coarse observation."""
    return SidecarStartDiagnostic(
        health_wait_timeout_seconds=timeout_seconds,
        elapsed_seconds=elapsed_seconds,
        elapsed_bucket=_elapsed_bucket(elapsed_seconds),
        container_created=obs.container_created,
        container_running=obs.container_running,
        container_health_state=obs.container_health_state,
        container_exit_code=obs.container_exit_code,
        container_restart_count=obs.container_restart_count,
        port_open=obs.port_open,
        health_http_reachable=obs.health_http_reachable,
        health_http_status_class=obs.health_http_status_class,
        timeout_reached=obs.timeout_reached,
        healthy=obs.healthy,
        failure_reason_code=classify_sidecar_start(obs),
    )


# ---- live coarse probes (used only during a real run; injectable for tests) ----

# Targeted inspect template: emits ONLY coarse fields, never logs or env (§21).
INSPECT_FORMAT = "{{.State.Running}}|{{.State.ExitCode}}|{{.State.Health.Status}}|{{.RestartCount}}"


def parse_inspect_line(line: Optional[str]) -> SidecarObservation:
    """Parse ``docker inspect -f INSPECT_FORMAT`` output into coarse container facts.

    ``line is None`` -> container not created / inspect failed (created=None so the
    classifier reports INSPECT_UNAVAILABLE unless a caller overrides). Only four
    positional tokens are read and coerced to typed coarse values; anything
    unparseable (including secret-laden noise) becomes ``None``, so raw text can never
    survive into the observation (§21)."""
    if line is None:
        return SidecarObservation(
            container_created=None,
            container_running=None,
            container_health_state=None,
            container_exit_code=None,
            container_restart_count=None,
            port_open=None,
            health_http_reachable=None,
            health_http_status_class=None,
            healthy=False,
            timeout_reached=False,
        )
    parts = line.strip().split("|")

    def _bool(tok: str) -> Optional[bool]:
        t = tok.strip().lower()
        if t == "true":
            return True
        if t == "false":
            return False
        return None

    def _int(tok: str) -> Optional[int]:
        t = tok.strip()
        try:
            return int(t)
        except (TypeError, ValueError):
            return None

    def _health(tok: str) -> Optional[str]:
        t = tok.strip().lower()
        # docker emits "<no value>" when no HEALTHCHECK is defined -> "none".
        if t in ("", "<no value>"):
            return "none"
        return t if t in _DOCKER_HEALTH_STATES else None

    running = _bool(parts[0]) if len(parts) > 0 else None
    exit_code = _int(parts[1]) if len(parts) > 1 else None
    health_state = _health(parts[2]) if len(parts) > 2 else None
    restart_count = _int(parts[3]) if len(parts) > 3 else None
    # inspect succeeded (line present) => the container object exists => created.
    return SidecarObservation(
        container_created=True,
        container_running=running,
        container_health_state=health_state,
        container_exit_code=exit_code,
        container_restart_count=restart_count,
        port_open=None,
        health_http_reachable=None,
        health_http_status_class=None,
        healthy=False,
        timeout_reached=False,
    )


def with_health(
    obs: SidecarObservation,
    *,
    port_open: Optional[bool],
    health_reachable: Optional[bool],
    health_status_code: Optional[int],
    healthy: bool,
    timeout_reached: bool,
) -> SidecarObservation:
    """Return a new observation augmented with coarse health/port facts (§21: only a
    status CODE is accepted, never a body, so the response text cannot leak)."""
    return SidecarObservation(
        container_created=obs.container_created,
        container_running=obs.container_running,
        container_health_state=obs.container_health_state,
        container_exit_code=obs.container_exit_code,
        container_restart_count=obs.container_restart_count,
        port_open=port_open,
        health_http_reachable=health_reachable,
        health_http_status_class=_status_class(health_status_code),
        healthy=healthy,
        timeout_reached=timeout_reached,
    )


__all__ = [
    "SIDECAR_HEALTHY",
    "SIDECAR_CONTAINER_NOT_CREATED",
    "SIDECAR_CONTAINER_EXITED",
    "SIDECAR_CONTAINER_RUNNING_NOT_HEALTHY",
    "SIDECAR_PORT_NOT_OPEN",
    "SIDECAR_HEALTH_HTTP_UNREACHABLE",
    "SIDECAR_HEALTH_NON_SUCCESS",
    "SIDECAR_HEALTH_TIMEOUT",
    "SIDECAR_INSPECT_UNAVAILABLE",
    "SIDECAR_START_UNKNOWN",
    "SidecarObservation",
    "SidecarStartDiagnostic",
    "classify_sidecar_start",
    "build_sidecar_diagnostic",
    "parse_inspect_line",
    "with_health",
    "INSPECT_FORMAT",
]
