"""PN02D-A provider-egress guard: socket-level detection of external traffic.

EVALUATION-ONLY. Nothing in production imports this. PN02D-A boots a REAL
LightRAG ``v1.5.6`` sidecar and must prove startup causes ZERO external provider
traffic (task §4/§5/§21/§27). Logs are NOT trusted as the sole signal
(task §5); this module observes the container's own kernel socket table
(``/proc/net/tcp`` + ``/proc/net/tcp6``, read via ``docker exec``) and counts any
socket with a *real remote peer that is not loopback* — i.e. an actual or
attempted outbound connection. Combined with the two prevention layers PN02D-A
uses (a per-runtime ``--internal`` Docker network with no route off-host, and
EMPTY provider bindings so there is nothing to call), a nonzero count here is the
fail-closed detection layer.

Content-safety (task §6/§26): this module ingests only the coarse socket-table
text and emits ONLY integer counts + coarse booleans. It never reads a request
body, an ``Authorization`` header, an env var value, or a provider payload. Remote
IP/port hex is used transiently to classify loopback-vs-external and is NOT
retained in any emitted field.

``/proc/net/tcp`` address encoding: each ``local_address`` / ``rem_address`` is
``IPHEX:PORTHEX`` where the IPv4 ``IPHEX`` is little-endian, so the FIRST octet is
the LAST hex byte. Loopback ``127.0.0.0/8`` therefore ends in ``7F`` (e.g.
``0100007F`` = 127.0.0.1, ``0B00007F`` = 127.0.0.11 Docker DNS). A LISTEN socket
has ``rem`` port ``0000`` and no peer; a socket with a nonzero remote port has an
actual peer. External ⟺ has a peer AND the remote IP is not loopback.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Sequence, Tuple

# TCP states (hex) that carry, or are establishing, a remote peer. LISTEN (0A) and
# CLOSE (07)/TIME_WAIT (06) with a zero remote are excluded by the zero-peer test;
# this set is informational only (classification keys off the remote peer, not the
# state, so an attempted SYN_SENT to an external host still counts).
TCP_LISTEN = "0A"

#: IPv6 loopback ``::1`` as it appears in /proc/net/tcp6 (last 32-bit word is the
#: little-endian 0x00000001). IPv6 any-address is all zeros. The IPv4-mapped prefix
#: (``::ffff:0:0``) marks an IPv4-in-IPv6 address whose trailing byte is the mapped
#: IPv4 first octet, so a mapped 127/8 loopback still ends in ``7F``.
_IPV6_LOOPBACK = "00000000000000000000000001000000"
_IPV6_ANY = "00000000000000000000000000000000"
_IPV6_V4MAPPED_PREFIX = "00000000000000000000FFFF"


class ProviderEgressDetected(RuntimeError):
    """An external (non-loopback) socket was observed during PN02D-A (must be 0)."""


@dataclass(frozen=True)
class SocketRow:
    """One coarse /proc/net socket fact. No body, header, or payload — addresses
    are hex used only for loopback classification, never emitted downstream."""

    local_ip_hex: str
    local_port_hex: str
    remote_ip_hex: str
    remote_port_hex: str
    state_hex: str

    @property
    def has_remote_peer(self) -> bool:
        """True when the remote port is nonzero (a LISTEN socket has ``:0000``)."""
        return self.remote_port_hex not in ("", "0000", "00000000")

    @property
    def remote_is_loopback(self) -> bool:
        return _is_loopback_hex(self.remote_ip_hex)

    @property
    def is_external_connection(self) -> bool:
        """A real peer whose remote IP is NOT loopback = external egress."""
        return self.has_remote_peer and not self.remote_is_loopback


def _is_loopback_hex(ip_hex: str) -> bool:
    """True if the little-endian /proc IP hex is loopback (127/8 or ::1 or any-0)."""
    h = ip_hex.strip().upper()
    if not h:
        return True
    if len(h) <= 8:
        # IPv4: all-zero (any/none) or first octet 127 (little-endian => ends 7F).
        if h == "00000000" or h == "0":
            return True
        return h.endswith("7F")
    # IPv6: any-address, ::1 loopback, or an IPv4-mapped 127/8 loopback. A
    # non-mapped external IPv6 that merely ends in ``7F`` must NOT be treated as
    # loopback (that would be a false negative in the egress detector).
    if h == _IPV6_ANY or h == _IPV6_LOOPBACK:
        return True
    if h.startswith(_IPV6_V4MAPPED_PREFIX):
        return h.endswith("7F")
    return False


def parse_proc_net_tcp(text: str) -> List[SocketRow]:
    """Parse ``/proc/net/tcp`` or ``/proc/net/tcp6`` text into coarse socket rows.

    Skips the header and any malformed line. Only the first four columns
    (``sl``, ``local_address``, ``rem_address``, ``st``) are read; everything else
    (uid, inode, retransmit counters, ...) is ignored so nothing content-bearing is
    kept. A concatenation of tcp + tcp6 is accepted (multiple header lines OK).
    """
    rows: List[SocketRow] = []
    for line in text.splitlines():
        parts = line.split()
        if len(parts) < 4:
            continue
        if not parts[0].rstrip(":").isdigit():
            # header ("sl local_address ...") or noise
            continue
        local = parts[1]
        remote = parts[2]
        state = parts[3].strip().upper()
        lip, _, lport = local.partition(":")
        rip, _, rport = remote.partition(":")
        if not lport or not rport:
            continue
        rows.append(
            SocketRow(
                local_ip_hex=lip.upper(),
                local_port_hex=lport.upper(),
                remote_ip_hex=rip.upper(),
                remote_port_hex=rport.upper(),
                state_hex=state,
            )
        )
    return rows


@dataclass(frozen=True)
class EgressObservation:
    """Content-safe egress observation of one sidecar at one instant (counts only)."""

    total_sockets: int
    listen_sockets: int
    peer_sockets: int
    loopback_peer_sockets: int
    external_peer_sockets: int

    @property
    def clean(self) -> bool:
        return self.external_peer_sockets == 0


def classify_egress(rows: Sequence[SocketRow]) -> EgressObservation:
    """Summarize socket rows into coarse counts. ``external_peer_sockets`` is the
    provider-egress signal — it must be 0 for a provider-free boot (task §4)."""
    listen = sum(1 for r in rows if r.state_hex == TCP_LISTEN)
    peer = [r for r in rows if r.has_remote_peer]
    loop = sum(1 for r in peer if r.remote_is_loopback)
    ext = sum(1 for r in peer if not r.remote_is_loopback)
    return EgressObservation(
        total_sockets=len(rows),
        listen_sockets=listen,
        peer_sockets=len(peer),
        loopback_peer_sockets=loop,
        external_peer_sockets=ext,
    )


def observe_from_text(*proc_net_texts: str) -> EgressObservation:
    """Parse one or more /proc/net/tcp{,6} blobs and classify their union."""
    rows: List[SocketRow] = []
    for t in proc_net_texts:
        rows.extend(parse_proc_net_tcp(t))
    return classify_egress(rows)


@dataclass
class ProviderEgressGuard:
    """Fail-closed accumulator of external-provider socket observations (task §5).

    ``external_provider_network_calls`` is the running max of external peer sockets
    ever seen across all samples. It must stay 0. ``assert_zero`` raises
    ``ProviderEgressDetected`` otherwise (fail-closed, task §27). A ``record_attempt``
    hook exists so any FUTURE seam that deliberately dialed a provider would trip the
    guard rather than pass silently."""

    external_provider_network_calls: int = 0
    samples: int = 0
    max_external_seen: int = 0

    def observe(self, obs: EgressObservation, *, seam: str = "boot") -> None:
        self.samples += 1
        if obs.external_peer_sockets > self.max_external_seen:
            self.max_external_seen = obs.external_peer_sockets
        # The reported call count is the worst-case external socket count seen.
        self.external_provider_network_calls = max(
            self.external_provider_network_calls, obs.external_peer_sockets
        )

    def record_attempt(self, seam: str) -> None:  # pragma: no cover - defensive
        self.external_provider_network_calls += 1
        raise ProviderEgressDetected(
            f"provider egress attempted via {seam!r} during PN02D-A "
            "(EXTERNAL_PROVIDER_NETWORK_CALLS must be 0)"
        )

    def assert_zero(self) -> None:
        if self.external_provider_network_calls != 0:
            raise ProviderEgressDetected(
                f"EXTERNAL_PROVIDER_NETWORK_CALLS="
                f"{self.external_provider_network_calls} != 0 "
                f"(external peer sockets seen across {self.samples} sample(s))"
            )


def split_ipv4_ipv6(proc_tcp: Optional[str], proc_tcp6: Optional[str]) -> Tuple[str, str]:
    """Normalize possibly-None probe outputs into two strings for observe_from_text."""
    return (proc_tcp or "", proc_tcp6 or "")


__all__ = [
    "TCP_LISTEN",
    "ProviderEgressDetected",
    "SocketRow",
    "parse_proc_net_tcp",
    "EgressObservation",
    "classify_egress",
    "observe_from_text",
    "ProviderEgressGuard",
    "split_ipv4_ipv6",
]
