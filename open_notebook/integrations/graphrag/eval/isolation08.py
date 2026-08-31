"""GraphRAG-08A — dedicated temporary Surreal namespace/database isolation.

EVALUATION-ONLY. Nothing in production imports this (eval -> production only).

Purpose: let a FUTURE GraphRAG-08 micro-precheck create canonical Sources in a
DEDICATED TEMPORARY Surreal namespace/database instead of the normal application
namespace, so the normal DB is never mutated by the benchmark (the only blocker
left after the offline harness checkpoint).

Why a process-local env override is the right (and narrow) mechanism:
``open_notebook/database/repository.py::db_connection`` opens a FRESH AsyncSurreal
per call and reads ``SURREAL_NAMESPACE`` / ``SURREAL_DATABASE`` from the env at
connect time — there is NO global singleton client and NO pooling. So overriding
those two env vars for the duration of an isolated context redirects every
``repo_*`` call, the migration bootstrap, and the in-process embed/index calls to
the temporary namespace, with nothing to rebind; restoring the env restores the
normal binding. (Schema is bootstrapped through the canonical
``AsyncMigrationManager`` — no migration is added or duplicated.)

Safety invariants (fail closed):
  * The temporary (namespace, database) MUST differ from the normal configured
    identity — asserted before any write/bootstrap (§9/§28/§47).
  * Cleanup drops ONLY a run-owned temporary namespace whose identity matches the
    manifest; it NEVER drops the normal namespace and never issues a broad purge
    (§21/§22). Unknown/foreign/malformed ownership -> no drop.
  * The env override is captured and restored in ``finally``, and restoration is
    explicitly verified (§31).
  * Nesting is refused (one isolated context per process at a time) so two
    contexts cannot clobber the same env (§41).

Out of scope (documented, NOT done here): provider/model records, LightRAG
sidecar/workspace, and running the micro-precheck itself.
"""

from __future__ import annotations

import os
import re
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import AsyncIterator, Optional, Tuple

from loguru import logger

_NS_PREFIX = "graphrag_eval_"
_DB_PREFIX = "graphrag_08_"
# Surreal identifier we are willing to emit unescaped: letters, digits, underscore,
# not starting with a digit, bounded length.
_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,62}$")

# Process-level reentrancy guard: the env override is global to the process, so
# only one isolated context may be active at a time. When active, we also record
# the CAPTURED normal identity (snapshotted BEFORE the override) and the active
# temp identity, so guards compare against the true normal identity rather than a
# fresh env read (which, during an active context, is already the temp identity).
_ACTIVE = False
_CAPTURED_NORMAL: Optional[Tuple[str, str]] = None
_ACTIVE_TEMP: Optional[Tuple[str, str]] = None


class IsolationConfigurationError(RuntimeError):
    """The isolation request is invalid (bad names, nesting, missing config)."""


class IsolationBootstrapError(RuntimeError):
    """Schema bootstrap into the temporary namespace failed."""


class IsolationOwnershipError(RuntimeError):
    """A safety guard (target == normal, or unverifiable ownership) tripped."""


class IsolationCleanupError(RuntimeError):
    """The temporary namespace could not be dropped / proven absent."""


class IsolationRestoreError(RuntimeError):
    """The normal runtime configuration could not be restored/verified."""


def make_run_id() -> str:
    """Content-free, collision-resistant run id (12 hex chars)."""
    return uuid.uuid4().hex[:12]


def _valid_identifier(name: str) -> bool:
    return bool(_IDENT_RE.match(name))


def temp_names(run_id: str) -> Tuple[str, str]:
    """Temporary (namespace, database) for a run id. Raises on an invalid id."""
    if not re.match(r"^[A-Za-z0-9]{4,32}$", run_id):
        raise IsolationConfigurationError(f"invalid run_id {run_id!r}")
    namespace = f"{_NS_PREFIX}{run_id}"
    database = f"{_DB_PREFIX}{run_id}"
    if not (_valid_identifier(namespace) and _valid_identifier(database)):
        raise IsolationConfigurationError("generated identifier is not Surreal-safe")
    return namespace, database


def normal_identity() -> Tuple[str, str]:
    """The currently-configured NORMAL (namespace, database) — non-secret."""
    from open_notebook.database.repository import (
        get_database_name,
        get_database_namespace,
    )

    return get_database_namespace(), get_database_name()


def assert_not_normal(namespace: str, database: str) -> None:
    """Fail closed if the target overlaps the normal configured namespace/database.

    Rejects both an exact identity match AND a shared-namespace overlap (same
    namespace, different database), so a temp database can never be bootstrapped
    *inside* the normal namespace — mirroring the cleanup guard (§21/§22).
    """
    norm_ns, norm_db = normal_identity()
    if (namespace, database) == (norm_ns, norm_db):
        raise IsolationOwnershipError(
            "refusing to use the NORMAL namespace/database as an isolation target"
        )
    if namespace == norm_ns:
        raise IsolationOwnershipError(
            "isolation namespace overlaps the NORMAL namespace"
        )


@dataclass(frozen=True)
class IsolationContext:
    run_id: str
    namespace: str
    database: str
    prior_namespace: Optional[str]
    prior_database: Optional[str]
    normal_namespace: str
    normal_database: str


def _set_env(namespace: str, database: str) -> None:
    os.environ["SURREAL_NAMESPACE"] = namespace
    os.environ["SURREAL_DATABASE"] = database


def _restore_env(prior_namespace: Optional[str], prior_database: Optional[str]) -> None:
    for key, prior in (
        ("SURREAL_NAMESPACE", prior_namespace),
        ("SURREAL_DATABASE", prior_database),
    ):
        if prior is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = prior


async def _bootstrap_schema() -> int:
    """Run the canonical migration path against the CURRENT env target.

    Returns the resulting schema version. Assumes the env override is already
    active (so db_connection resolves to the temporary namespace).
    """
    from open_notebook.database.async_migrate import AsyncMigrationManager

    manager = AsyncMigrationManager()
    await manager.run_migration_up()
    version = await manager.get_current_version()
    expected = len(manager.up_migrations)
    if version != expected:
        raise IsolationBootstrapError(
            f"schema bootstrap version {version} != expected {expected}"
        )
    return version


async def _drop_namespace(namespace: str, database: str) -> None:
    """Drop the temporary namespace (removes its databases). Idempotent."""
    import os as _os

    from surrealdb import AsyncSurreal  # type: ignore

    from open_notebook.database.repository import (
        get_database_password,
        get_database_url,
    )

    db = AsyncSurreal(get_database_url())
    await db.signin(
        {"username": _os.environ.get("SURREAL_USER"), "password": get_database_password()}
    )
    try:
        # USE the target so REMOVE DATABASE resolves within it, then remove the
        # whole namespace (idempotent). We connect with EXPLICIT names, never via
        # the env, so teardown is independent of the override state.
        await db.use(namespace, database)
        await db.query(f"REMOVE DATABASE IF EXISTS {database};")
        await db.query(f"REMOVE NAMESPACE IF EXISTS {namespace};")
    finally:
        await db.close()


async def namespace_databases(namespace: str) -> list[str]:
    """List database names under a namespace (via INFO FOR NS). Empty if absent."""
    import os as _os

    from surrealdb import AsyncSurreal  # type: ignore

    from open_notebook.database.repository import (
        get_database_password,
        get_database_url,
    )

    db = AsyncSurreal(get_database_url())
    await db.signin(
        {"username": _os.environ.get("SURREAL_USER"), "password": get_database_password()}
    )
    try:
        await db.use(namespace, "probe")
        info = await db.query(f"INFO FOR NS {namespace};")
    except Exception:  # noqa: BLE001 - absent namespace / probe failure = empty
        return []
    finally:
        await db.close()
    # Surreal returns {"databases": {name: DEFINE...}} shape; be defensive.
    dbs = None
    if isinstance(info, dict):
        dbs = info.get("databases")
    if isinstance(dbs, dict):
        return sorted(dbs.keys())
    return []


async def cleanup_isolated(
    namespace: str,
    database: str,
    *,
    run_id: str,
    normal_namespace: str,
    normal_database: str,
) -> None:
    """Drop a run-owned temporary namespace with fail-closed guards (§21/§22).

    Never drops the normal namespace; refuses on unverifiable ownership.
    """
    if not namespace or not database or not run_id:
        raise IsolationOwnershipError("cleanup requires run_id + temp namespace/database")
    if not (_valid_identifier(namespace) and _valid_identifier(database)):
        raise IsolationOwnershipError("cleanup target is not a valid temp identifier")
    if namespace != f"{_NS_PREFIX}{run_id}" or database != f"{_DB_PREFIX}{run_id}":
        raise IsolationOwnershipError(
            "cleanup target does not match the run-owned temp identity"
        )
    if (namespace, database) == (normal_namespace, normal_database):
        raise IsolationOwnershipError("refusing to drop the NORMAL namespace/database")
    if namespace == normal_namespace:
        raise IsolationOwnershipError("temp namespace collides with the normal namespace")
    try:
        await _drop_namespace(namespace, database)
    except Exception as exc:  # noqa: BLE001
        raise IsolationCleanupError(
            f"failed to drop temp namespace {namespace}: {type(exc).__name__}"
        ) from exc


@asynccontextmanager
async def isolated_surreal_eval_runtime(
    run_id: Optional[str] = None,
    *,
    bootstrap: bool = True,
) -> AsyncIterator[IsolationContext]:
    """Bind the eval runtime to a dedicated temporary Surreal namespace/database.

    On enter: assert target != normal, override env, bootstrap the canonical
    schema, yield the context. On exit: drop the temp namespace (guarded), restore
    the env, and VERIFY restoration. Refuses to nest.

    This context does NOT create Sources, call providers, or start LightRAG — it is
    the isolation substrate a future micro-precheck runs inside.
    """
    global _ACTIVE, _CAPTURED_NORMAL, _ACTIVE_TEMP
    if _ACTIVE:
        raise IsolationConfigurationError(
            "an isolated Surreal eval runtime is already active (no nesting)"
        )

    run_id = run_id or make_run_id()
    namespace, database = temp_names(run_id)
    # Snapshot the normal identity BEFORE any override; every later guard compares
    # against this snapshot, never a fresh env read (which would be the temp id).
    normal_ns, normal_db = normal_identity()
    assert_not_normal(namespace, database)

    prior_ns = os.environ.get("SURREAL_NAMESPACE")
    prior_db = os.environ.get("SURREAL_DATABASE")
    ctx = IsolationContext(
        run_id=run_id,
        namespace=namespace,
        database=database,
        prior_namespace=prior_ns,
        prior_database=prior_db,
        normal_namespace=normal_ns,
        normal_database=normal_db,
    )

    _ACTIVE = True
    _CAPTURED_NORMAL = (normal_ns, normal_db)
    _ACTIVE_TEMP = (namespace, database)
    _set_env(namespace, database)
    logger.debug(
        f"[gr08a] isolation enter run={run_id} ns={namespace} db={database}"
    )
    try:
        if bootstrap:
            try:
                await _bootstrap_schema()
            except IsolationBootstrapError:
                raise
            except Exception as exc:  # noqa: BLE001
                raise IsolationBootstrapError(
                    f"schema bootstrap failed: {type(exc).__name__}"
                ) from exc
        yield ctx
    finally:
        # Restore env + clear active state FIRST, so even a cancellation/interrupt
        # delivered during the cleanup await below cannot leave the process bound to
        # the temp namespace or leave _ACTIVE stuck True (§31/§45).
        _restore_env(prior_ns, prior_db)
        _ACTIVE = False
        _CAPTURED_NORMAL = None
        _ACTIVE_TEMP = None
        restored_ns, restored_db = normal_identity()
        restore_ok = (restored_ns, restored_db) == (normal_ns, normal_db)

        # Drop the temp namespace with EXPLICIT names (independent of env state).
        cleanup_error: Optional[Exception] = None
        try:
            await cleanup_isolated(
                namespace,
                database,
                run_id=run_id,
                normal_namespace=normal_ns,
                normal_database=normal_db,
            )
        except Exception as exc:  # noqa: BLE001 - surface after state is restored
            cleanup_error = exc
            logger.error(f"[gr08a] isolation cleanup failed: {type(exc).__name__}")

        if not restore_ok:
            raise IsolationRestoreError(
                "normal namespace/database not restored after isolated context"
            )
        if cleanup_error is not None:
            # Cleanup failure must NOT be swallowed — future readiness depends on
            # trustworthy teardown (§45).
            raise IsolationCleanupError(
                f"isolation cleanup failed: {type(cleanup_error).__name__}"
            ) from cleanup_error


def is_active() -> bool:
    """Whether an isolated eval runtime is currently active in this process."""
    return _ACTIVE


def require_active_isolation() -> None:
    """Guard for the live GraphRAG-08 path: Option-A isolation is REQUIRED (§28).

    The live runner must call this before creating any canonical Source, so a
    caller can never run the benchmark against the normal namespace (Option B).
    """
    if not _ACTIVE or _ACTIVE_TEMP is None or _CAPTURED_NORMAL is None:
        raise IsolationOwnershipError(
            "GraphRAG-08 live execution requires an active Option-A isolated Surreal "
            "runtime (isolated_surreal_eval_runtime); the normal-DB path is blocked"
        )
    # Defense in depth: the LIVE env target must be exactly the temp identity this
    # context set, and must differ from the CAPTURED normal identity (snapshotted
    # before the override). We compare against the captured snapshot, NOT a fresh
    # normal_identity() read — during an active context that read returns the temp
    # identity, so comparing against it would be meaningless (and self-tripping).
    active = (os.environ.get("SURREAL_NAMESPACE"), os.environ.get("SURREAL_DATABASE"))
    if active != _ACTIVE_TEMP:
        raise IsolationOwnershipError(
            "active Surreal target drifted from the isolated temp identity"
        )
    if active == _CAPTURED_NORMAL:
        raise IsolationOwnershipError(
            "active Surreal target equals the captured normal identity"
        )


__all__ = [
    "IsolationConfigurationError",
    "IsolationBootstrapError",
    "IsolationOwnershipError",
    "IsolationCleanupError",
    "IsolationRestoreError",
    "IsolationContext",
    "make_run_id",
    "temp_names",
    "normal_identity",
    "assert_not_normal",
    "namespace_databases",
    "cleanup_isolated",
    "isolated_surreal_eval_runtime",
    "is_active",
    "require_active_isolation",
]
