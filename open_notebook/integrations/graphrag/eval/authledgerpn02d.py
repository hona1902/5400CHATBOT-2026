"""PN02D-B3G/B3H — durable, code-enforced ONE-SHOT live-authorization consumption ledger.

EVALUATION-ONLY. Nothing in production imports this. It closes the shared-framework HIGH
finding PN02DB3D-ER1-H1: the shared live-provider execution framework (B1 / B2 / B3) could
mint + use a live capability repeatedly from one otherwise-valid operator grant because there
was no durable consumption state. This module is the authoritative durable ledger that makes

    ONE EXPLICIT OPERATOR GRANT (auth profile + run_id) -> AT MOST ONE CANONICAL REAL ATTEMPT

code-enforced. A genuinely distinct second authorized attempt requires a NEW run_id (an
explicit governance invariant, not an informal convention — B3H §2/§40).

Identity model (B3H remediation of PN02DB3G-OR1-M1):
  * The authoritative one-shot CLAIM identity is exactly ``auth-profile/execution-kind + run_id``.
    The digest (SHA-256, canonical length-framed, domain ``OPEN_NOTEBOOK_LIVE_AUTH_ONESHOT_V2``)
    is computed over ONLY those two fields. Changing the execution envelope (checkpoint, git
    commit, fixture hash, provider fingerprint) NEVER yields a second claimable key for the same
    profile+run_id — those fields are validated by the authorization profile/mint and are
    persisted here only as content-safe AUDIT metadata.

Durability / integrity model (B3H remediation of PN02DB3G-OR1-H1/H2/H3):
  * H1 — the authoritative production path is NOT selected from any public runtime env var. It
    resolves deterministically to a per-user OS state location OUTSIDE the app data folder. Tests
    isolate by monkeypatching the internal ``default_ledger_path`` resolver — never a public env
    var, CLI flag, or public run-function parameter.
  * H2 — schema is created ONLY for a path that did not exist before open. An EXISTING ledger is
    validated exactly (both tables, single meta row, supported version, required columns) and any
    deviation FAILS CLOSED with no mutation / no auto-repair / no recreate.
  * H3 — the ledger lives at ``~/.open-notebook/security/`` (outside ``./data`` and
    ``surreal_data/``), so the documented ``tar data/ surreal_data/`` backup/restore cannot roll
    consumed grants backward.
  * Backend = stdlib ``sqlite3`` (WAL, synchronous=FULL, busy_timeout). Atomic claim =
    ``BEGIN IMMEDIATE`` + ``INSERT`` on the ``PRIMARY KEY`` digest; a duplicate is ALREADY_CONSUMED.
  * Fail-closed everywhere: unavailable / corrupt / partial-schema / future-schema / lock
    contention REFUSE execution. Post-claim failure or crash leaves the grant consumed. No
    normal-runtime unconsume/reset/delete API; normal cleanup never removes a record.
"""

from __future__ import annotations

import hashlib
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Optional

#: Ledger schema version. Bump only with an explicit forward migration; a ledger whose stored
#: version is NEWER than this fails closed (never silently downgraded/accepted).
LEDGER_SCHEMA_VERSION = 2

#: Domain separator for the one-shot CLAIM identity digest. V2 (B3H): the digest is keyed on
#: ``auth-profile/execution-kind + run_id`` ONLY (not the execution envelope).
_DIGEST_DOMAIN = "OPEN_NOTEBOOK_LIVE_AUTH_ONESHOT_V2"

#: SQLite busy timeout (seconds). On expiry the claim FAILS CLOSED (a lock timeout is never
#: interpreted as "grant unused").
_BUSY_TIMEOUT_S = 5.0

#: Content-safe columns that MUST exist on an existing ledger's consumed_grants table.
_REQUIRED_CONSUMED_COLUMNS = frozenset(
    {
        "grant_digest",
        "profile",
        "run_id",
        "execution_kind",
        "checkpoint_commit",
        "checkpoint_tag",
        "fixture_hash",
        "provider_config_fingerprint",
        "consumed_at",
        "schema_version",
    }
)


class OneShotLedgerError(RuntimeError):
    """Base class for all one-shot ledger failures (content-free, fail-closed)."""


class GrantAlreadyConsumedError(OneShotLedgerError):
    """The operator grant (profile + run_id) was already consumed by a prior canonical real
    execution attempt. Raised at the execution boundary to REFUSE a replay (PN02DB3D-ER1-H1)."""


class LedgerUnavailableError(OneShotLedgerError):
    """The authoritative ledger / its directory could not be opened or a safe claim could not be
    obtained (open failure, directory failure, lock contention/timeout). Fail closed."""


class LedgerSchemaError(OneShotLedgerError):
    """The ledger schema is missing/partial on an EXISTING ledger, unsupported, or NEWER than
    this runtime understands. Never auto-repaired/downgraded/recreated; fail closed."""


class LedgerCorruptError(OneShotLedgerError):
    """SQLite reported a malformed/corrupt database. Never auto-recreated; fail closed."""


class ClaimResult(str, Enum):
    """Outcome of an atomic claim attempt."""

    CLAIMED = "CLAIMED"
    ALREADY_CONSUMED = "ALREADY_CONSUMED"


@dataclass(frozen=True)
class GrantIdentity:
    """The operator-grant/attempt identity + content-safe audit envelope.

    The CLAIM digest is derived from ``execution_kind`` + ``run_id`` ONLY (B3H §5). The remaining
    fields are non-secret audit metadata (see ``OperatorRunGrant`` — "every field is a non-secret
    id / count / flag"); they are persisted for forensic audit but never create a second claim
    identity for the same profile+run_id. NO provider key / password / token is ever included."""

    execution_kind: str
    run_id: str
    b1_r2_checkpoint: str
    approved_git_commit: str
    fixture_hash: str
    provider_config_fingerprint: str


# --------------------------------------------------------------------------- #
# Authoritative production path (H1/H3): per-user OS state, NOT public-overridable,
# NOT under the app ./data folder.
# --------------------------------------------------------------------------- #

#: The per-user security-state directory holding the authoritative one-shot ledger. Deterministic
#: and portable; OUTSIDE the app ``./data`` folder and the documented ``tar data/ surreal_data/``
#: backup set, so an ordinary data restore cannot roll consumed grants backward (H3).
_SECURITY_STATE_DIRNAME = ".open-notebook"
_SECURITY_STATE_SUBDIR = "security"
_LEDGER_FILENAME = "live_auth_consumption.sqlite"


def _production_ledger_path() -> str:
    """The AUTHORITATIVE production ledger path. Deterministic, per-user, dependency-free, and
    OUTSIDE ``./data`` — resolved from ``Path.home()`` with NO environment override (H1). This is
    the real resolver a dedicated test exercises directly; the ordinary test isolation fixture
    monkeypatches ``default_ledger_path`` (below), never this function or a public env var."""
    return str(
        Path.home() / _SECURITY_STATE_DIRNAME / _SECURITY_STATE_SUBDIR / _LEDGER_FILENAME
    )


def default_ledger_path() -> str:
    """The authoritative ledger path used by ``claim_grant`` / ``enforce_one_shot`` when no
    explicit (internal-injection) path is given. Returns the production resolver; there is NO
    public env/CLI/config override (PN02DB3G-OR1-H1 closed). Tests isolate by monkeypatching
    THIS function to a temp path (an internal test seam), leaving ``_production_ledger_path``
    intact for a direct production-path assertion."""
    return _production_ledger_path()


def compute_grant_digest(identity: GrantIdentity) -> str:
    """Stable SHA-256 over a canonical, length-framed, domain-separated encoding of the one-shot
    CLAIM identity = (execution_kind, run_id) ONLY (B3H §5). Length-framing (``name:len:value``)
    is unambiguous. Deterministic across processes; depends on NO secret/randomness/timestamp and
    NO execution-envelope field — so an envelope change cannot mint a second claim key."""
    fields = (
        ("kind", identity.execution_kind),
        ("run", identity.run_id),
    )
    parts = []
    for name, value in fields:
        v = "" if value is None else str(value)
        parts.append(f"{name}:{len(v)}:{v}")
    canonical = f"{_DIGEST_DOMAIN}|" + "|".join(parts)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def grant_identity_from(operator_grant: object, *, execution_kind: str) -> GrantIdentity:
    """Build the ``GrantIdentity`` from an ``OperatorRunGrant`` + the profile execution kind.

    ``execution_kind`` + ``run_id`` form the claim identity; the remaining fields are audit-only.
    The existing run-id binding (``assert_run_ids_consistent`` in the mint) guarantees a grant for
    run A cannot drive run B, and B3H governance requires a NEW run_id for any new attempt."""
    return GrantIdentity(
        execution_kind=str(execution_kind),
        run_id=str(getattr(operator_grant, "run_id", "")),
        b1_r2_checkpoint=str(getattr(operator_grant, "b1_r2_checkpoint", "")),
        approved_git_commit=str(getattr(operator_grant, "approved_git_commit", "")),
        fixture_hash=str(getattr(operator_grant, "fixture_hash", "")),
        provider_config_fingerprint=str(
            getattr(operator_grant, "provider_config_fingerprint", "")
        ),
    )


# --------------------------------------------------------------------------- #
# Ledger open + strict schema (H2)
# --------------------------------------------------------------------------- #


def _configure(conn: sqlite3.Connection) -> None:
    """WAL + FULL durability + busy timeout. A malformed file raises here -> corrupt (fail closed)."""
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=FULL")
        conn.execute(f"PRAGMA busy_timeout={int(_BUSY_TIMEOUT_S * 1000)}")
    except sqlite3.DatabaseError as exc:
        conn.close()
        raise LedgerCorruptError(
            f"one-shot ledger is malformed/corrupt ({type(exc).__name__})"
        ) from exc


def _table_names(conn: sqlite3.Connection) -> set:
    try:
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    except sqlite3.DatabaseError as exc:
        raise LedgerCorruptError(
            f"one-shot ledger master table unreadable ({type(exc).__name__})"
        ) from exc
    return {r[0] for r in rows}


def _initialize_fresh_schema(conn: sqlite3.Connection) -> None:
    """Create the v2 schema on a TRULY FRESH (previously nonexistent) ledger, transactionally.
    IF NOT EXISTS + INSERT OR IGNORE make a rare concurrent first-init safe; this path is NEVER
    reached for an already-existing ledger (that goes to strict validation)."""
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("CREATE TABLE IF NOT EXISTS meta (schema_version INTEGER NOT NULL)")
        conn.execute(
            "CREATE TABLE IF NOT EXISTS consumed_grants ("
            " grant_digest TEXT PRIMARY KEY,"
            " profile TEXT NOT NULL,"
            " run_id TEXT NOT NULL,"
            " execution_kind TEXT NOT NULL,"
            " checkpoint_commit TEXT NOT NULL,"
            " checkpoint_tag TEXT NOT NULL,"
            " fixture_hash TEXT NOT NULL,"
            " provider_config_fingerprint TEXT NOT NULL,"
            " consumed_at TEXT NOT NULL,"
            " schema_version INTEGER NOT NULL"
            ")"
        )
        if not conn.execute("SELECT 1 FROM meta LIMIT 1").fetchone():
            conn.execute(
                "INSERT OR IGNORE INTO meta(schema_version) VALUES(?)",
                (LEDGER_SCHEMA_VERSION,),
            )
        conn.execute("COMMIT")
    except sqlite3.DatabaseError as exc:
        _rollback_quietly(conn)
        raise LedgerCorruptError(
            f"one-shot ledger fresh init failed ({type(exc).__name__})"
        ) from exc


def _validate_existing_schema(conn: sqlite3.Connection) -> None:
    """Validate an EXISTING ledger EXACTLY (H2). Any deviation FAILS CLOSED with NO mutation:
    missing meta / consumed_grants table, wrong meta cardinality, missing required column, or an
    unsupported/future schema version. Never creates, drops, repairs, or recreates anything."""
    tables = _table_names(conn)
    if "meta" not in tables:
        raise LedgerSchemaError(
            "existing one-shot ledger is missing the meta table (fail-closed; no auto-repair)"
        )
    if "consumed_grants" not in tables:
        raise LedgerSchemaError(
            "existing one-shot ledger is missing the consumed_grants table "
            "(fail-closed; no auto-recreate)"
        )
    try:
        meta_rows = conn.execute("SELECT schema_version FROM meta").fetchall()
        col_rows = conn.execute("PRAGMA table_info(consumed_grants)").fetchall()
    except sqlite3.DatabaseError as exc:
        raise LedgerCorruptError(
            f"existing one-shot ledger schema unreadable ({type(exc).__name__})"
        ) from exc
    if len(meta_rows) != 1:
        raise LedgerSchemaError(
            f"existing one-shot ledger meta has {len(meta_rows)} rows (expected exactly 1)"
        )
    columns = {r[1] for r in col_rows}
    missing = _REQUIRED_CONSUMED_COLUMNS - columns
    if missing:
        raise LedgerSchemaError(
            "existing one-shot ledger consumed_grants is missing required columns "
            "(fail-closed; no auto-repair)"
        )
    stored = int(meta_rows[0][0])
    if stored > LEDGER_SCHEMA_VERSION:
        raise LedgerSchemaError(
            f"one-shot ledger schema_version {stored} is newer than supported "
            f"{LEDGER_SCHEMA_VERSION} (fail-closed; no auto-downgrade)"
        )
    if stored < LEDGER_SCHEMA_VERSION:
        raise LedgerSchemaError(
            f"one-shot ledger schema_version {stored} requires a migration to "
            f"{LEDGER_SCHEMA_VERSION} that is not available (fail-closed)"
        )


def _open_ledger(ledger_path: str) -> sqlite3.Connection:
    """Open the authoritative ledger with strict fresh-vs-existing handling (H2), fail-closed.

    Freshness is decided from the FILE's existence BEFORE sqlite may create it. A truly fresh path
    gets a transactional schema init; any pre-existing file (including a zero-byte or partially
    initialized one) is validated exactly and fails closed on any deviation — never recreated."""
    p = Path(ledger_path)
    existed_before = p.exists()
    if existed_before and p.is_file() and p.stat().st_size == 0:
        # A pre-existing zero-byte file is NOT a fresh DB (e.g. a crashed/interrupted init) — H2/§25.
        raise LedgerSchemaError(
            "existing one-shot ledger file is zero-byte (fail-closed; not treated as fresh)"
        )
    if not existed_before:
        # Create the per-user security-state directory for a first-ever ledger; fail closed if
        # the directory cannot be created (§16).
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise LedgerUnavailableError(
                f"one-shot ledger directory could not be created ({type(exc).__name__})"
            ) from exc
    try:
        conn = sqlite3.connect(ledger_path, timeout=_BUSY_TIMEOUT_S, isolation_level=None)
    except sqlite3.Error as exc:
        raise LedgerUnavailableError(
            f"one-shot ledger could not be opened ({type(exc).__name__})"
        ) from exc
    _configure(conn)
    try:
        if existed_before:
            _validate_existing_schema(conn)  # fail-closed, no mutation
        else:
            _initialize_fresh_schema(conn)
    except OneShotLedgerError:
        conn.close()
        raise
    return conn


def _rollback_quietly(conn: sqlite3.Connection) -> None:
    try:
        conn.execute("ROLLBACK")
    except sqlite3.Error:
        pass


def claim_grant(
    identity: GrantIdentity,
    *,
    ledger_path: Optional[str] = None,
) -> ClaimResult:
    """Atomically claim (consume) the grant's (profile + run_id) identity. Returns CLAIMED or
    ALREADY_CONSUMED. Single atomic ``BEGIN IMMEDIATE`` + ``INSERT`` on the PRIMARY KEY digest; a
    duplicate is ALREADY_CONSUMED. Any open/lock/corruption/schema failure is raised (fail closed;
    an ambiguous transaction outcome never authorizes execution). Persists only content-safe audit
    metadata (digest / profile / run / kind / checkpoint / fixture / provider-fp / timestamp /
    version) — NO secret, prompt, answer, or source content."""
    path = ledger_path if ledger_path is not None else default_ledger_path()
    digest = compute_grant_digest(identity)
    conn = _open_ledger(path)
    try:
        try:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                "INSERT INTO consumed_grants("
                " grant_digest, profile, run_id, execution_kind, checkpoint_commit,"
                " checkpoint_tag, fixture_hash, provider_config_fingerprint, consumed_at,"
                " schema_version"
                ") VALUES(?,?,?,?,?,?,?,?,?,?)",
                (
                    digest,
                    identity.execution_kind,
                    identity.run_id,
                    identity.execution_kind,
                    identity.approved_git_commit,
                    identity.b1_r2_checkpoint,
                    identity.fixture_hash,
                    identity.provider_config_fingerprint,
                    datetime.now(timezone.utc).isoformat(),
                    LEDGER_SCHEMA_VERSION,
                ),
            )
            conn.execute("COMMIT")
            return ClaimResult.CLAIMED
        except sqlite3.IntegrityError:
            _rollback_quietly(conn)
            return ClaimResult.ALREADY_CONSUMED
        except sqlite3.OperationalError as exc:
            _rollback_quietly(conn)
            raise LedgerUnavailableError(
                f"one-shot ledger claim could not acquire a safe lock ({type(exc).__name__})"
            ) from exc
        except sqlite3.DatabaseError as exc:
            _rollback_quietly(conn)
            raise LedgerCorruptError(
                f"one-shot ledger claim failed on a malformed database ({type(exc).__name__})"
            ) from exc
    finally:
        conn.close()


def is_consumed(identity: GrantIdentity, *, ledger_path: Optional[str] = None) -> bool:
    """Content-safe read: whether this grant identity's digest is already recorded consumed.
    Fail-closed on open/corruption/schema failure (raises rather than reporting False)."""
    path = ledger_path if ledger_path is not None else default_ledger_path()
    digest = compute_grant_digest(identity)
    conn = _open_ledger(path)
    try:
        try:
            row = conn.execute(
                "SELECT 1 FROM consumed_grants WHERE grant_digest=? LIMIT 1", (digest,)
            ).fetchone()
        except sqlite3.DatabaseError as exc:
            raise LedgerCorruptError(
                f"one-shot ledger read failed on a malformed database ({type(exc).__name__})"
            ) from exc
        return row is not None
    finally:
        conn.close()


def enforce_one_shot(
    *,
    operator_grant: object,
    execution_kind: str,
    ledger_path: Optional[str] = None,
) -> None:
    """Consume the grant at the canonical real-execution boundary, or REFUSE a replay.

    Called by ``RealB1Driver.run`` AFTER mint validation and BEFORE the first provider-bound
    action. First attempt for a (profile, run_id) is CLAIMED and execution proceeds; a second
    attempt (same profile+run_id) raises ``GrantAlreadyConsumedError`` regardless of any audit-
    envelope variation. A genuinely distinct new attempt requires a NEW run_id (B3H governance).
    Any ledger open/lock/corruption/schema failure propagates (fail closed)."""
    identity = grant_identity_from(operator_grant, execution_kind=execution_kind)
    result = claim_grant(identity, ledger_path=ledger_path)
    if result is ClaimResult.ALREADY_CONSUMED:
        raise GrantAlreadyConsumedError(
            "operator grant already consumed by a prior canonical real execution attempt "
            "(one grant = auth-profile + run_id -> one real attempt; a new attempt needs a new "
            "run_id; PN02DB3D-ER1-H1)"
        )


__all__ = [
    "LEDGER_SCHEMA_VERSION",
    "OneShotLedgerError",
    "GrantAlreadyConsumedError",
    "LedgerUnavailableError",
    "LedgerSchemaError",
    "LedgerCorruptError",
    "ClaimResult",
    "GrantIdentity",
    "default_ledger_path",
    "compute_grant_digest",
    "grant_identity_from",
    "claim_grant",
    "is_consumed",
    "enforce_one_shot",
]
