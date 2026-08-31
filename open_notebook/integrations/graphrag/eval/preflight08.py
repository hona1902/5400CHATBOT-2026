"""GraphRAG-08C normal-DB preflight safety (EVALUATION-ONLY).

Nothing in production imports this. It restores a TRUSTWORTHY normal-application-DB
baseline invariant for the authorized full-run harness, fixing the attempt-#3
anomaly where an unreadable baseline was silently recorded as ``null`` and then
``null == null`` was mistaken for "unchanged".

Two guarantees (task §2/§3/§5/§6):
  * A full benchmark must NOT begin unless the normal DB baseline can be read as
    CONCRETE values: identity, a concrete source count, and the model baseline that
    the seed/restore path already depends on. If any is unreadable -> FAIL CLOSED
    (``require_readable_baseline`` raises) BEFORE any sidecar/provider action.
  * "Unchanged" is proven only from two VALID concrete observations. ``null == null``
    (or any non-concrete observation) yields ``NOT_PROVEN`` — never ``YES``.

This module never mutates the DB and never touches production repository semantics;
it reads through the normal supported access path (``repo_query`` / ``DefaultModels``).
It records only content-free fields (namespace/database names are non-secret; the
default embedding model is recorded as a presence bool, never its id/value).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Optional

# NORMAL_DB_UNCHANGED verdicts (task §3).
UNCHANGED_YES = "YES"
UNCHANGED_NOT_PROVEN = "NOT_PROVEN"


class NormalDbReasonCode:
    READABLE = "NORMAL_DB_BASELINE_READABLE"
    IDENTITY_UNREADABLE = "NORMAL_DB_IDENTITY_UNREADABLE"
    COUNT_UNREADABLE = "NORMAL_DB_SOURCE_COUNT_UNREADABLE"
    MODEL_BASELINE_UNREADABLE = "NORMAL_DB_MODEL_BASELINE_UNREADABLE"


class NormalDbBaselineError(RuntimeError):
    """The normal-DB baseline could not be read as concrete values (fail closed)."""


@dataclass(frozen=True)
class NormalDbBaseline:
    """Content-free normal-DB baseline observation.

    ``source_count`` is a CONCRETE int only when ``count_readable`` is True; it is
    ``None`` (not zero) when the read failed, so a failed read can never be mistaken
    for an empty-but-valid corpus. Names are non-secret; the default embedding model
    is recorded as presence only, never its id.
    """

    identity_readable: bool
    count_readable: bool
    model_baseline_readable: bool
    namespace: Optional[str]
    database: Optional[str]
    source_count: Optional[int]
    default_embedding_model_present: Optional[bool]
    reason_code: str

    @property
    def readable(self) -> bool:
        return (
            self.identity_readable
            and self.count_readable
            and self.model_baseline_readable
        )

    def as_dict(self) -> Dict[str, object]:
        return {
            "identity_readable": self.identity_readable,
            "count_readable": self.count_readable,
            "model_baseline_readable": self.model_baseline_readable,
            "readable": self.readable,
            "namespace": self.namespace,
            "database": self.database,
            "source_count": self.source_count,
            "default_embedding_model_present": self.default_embedding_model_present,
            "reason_code": self.reason_code,
        }


def _reason_for(
    identity_readable: bool, count_readable: bool, model_readable: bool
) -> str:
    if not identity_readable:
        return NormalDbReasonCode.IDENTITY_UNREADABLE
    if not count_readable:
        return NormalDbReasonCode.COUNT_UNREADABLE
    if not model_readable:
        return NormalDbReasonCode.MODEL_BASELINE_UNREADABLE
    return NormalDbReasonCode.READABLE


async def _read_source_count() -> int:
    """Concrete normal-DB source count via the supported path. Raises on failure."""
    from open_notebook.database.repository import repo_query

    rows = await repo_query("SELECT VALUE id FROM source")
    if not isinstance(rows, list):
        raise NormalDbBaselineError("source count query did not return a list")
    return len(rows)


async def read_normal_db_baseline() -> NormalDbBaseline:
    """Read the normal-DB baseline as CONCRETE values (task §2), fail-closed per part.

    Never raises: an unreadable part is recorded as its flag=False with a concrete
    reason code, so the caller can decide (``require_readable_baseline``) rather than
    the read silently collapsing to ``null``. Must be called with the NORMAL env
    binding active (i.e. outside the isolation context)."""
    from open_notebook.integrations.graphrag.eval.isolation08 import normal_identity

    # -- identity (non-secret names) --
    namespace: Optional[str] = None
    database: Optional[str] = None
    identity_readable = False
    try:
        namespace, database = normal_identity()
        identity_readable = bool(namespace) and bool(database)
    except Exception:  # noqa: BLE001 - unreadable identity -> fail closed
        identity_readable = False

    # -- concrete source count --
    source_count: Optional[int] = None
    count_readable = False
    try:
        source_count = await _read_source_count()
        count_readable = isinstance(source_count, int)
    except Exception:  # noqa: BLE001 - unreadable count -> fail closed (stays None)
        source_count = None
        count_readable = False

    # -- model baseline (presence only; part of seed/restore safety) --
    default_present: Optional[bool] = None
    model_readable = False
    try:
        from open_notebook.ai.models import DefaultModels

        defaults = await DefaultModels.get_instance()
        default_present = bool(getattr(defaults, "default_embedding_model", None))
        model_readable = True
    except Exception:  # noqa: BLE001 - unreadable model baseline -> fail closed
        default_present = None
        model_readable = False

    return NormalDbBaseline(
        identity_readable=identity_readable,
        count_readable=count_readable,
        model_baseline_readable=model_readable,
        namespace=namespace,
        database=database,
        source_count=source_count,
        default_embedding_model_present=default_present,
        reason_code=_reason_for(identity_readable, count_readable, model_readable),
    )


def require_readable_baseline(baseline: Optional[NormalDbBaseline]) -> None:
    """Hard gate (task §2/§5/§16): raise unless the baseline is fully readable.

    Called BEFORE sidecar startup so a full run can never begin against an
    unreadable normal DB."""
    if baseline is None or not baseline.readable:
        reason = baseline.reason_code if baseline is not None else "NORMAL_DB_NO_OBSERVATION"
        raise NormalDbBaselineError(
            f"normal DB baseline not readable ({reason}); refusing to start full run"
        )


def compare_normal_db(
    before: Optional[NormalDbBaseline], after: Optional[NormalDbBaseline]
) -> str:
    """NORMAL_DB_UNCHANGED verdict (task §3/§6/§18).

    Returns ``YES`` ONLY when BOTH observations are valid concrete observations and
    satisfy the approved comparison (same identity, same concrete source count, same
    model-baseline presence). Any missing/unreadable/non-concrete observation —
    including ``before is None and after is None`` — yields ``NOT_PROVEN``. Null
    equality is explicitly rejected."""
    if before is None or after is None:
        return UNCHANGED_NOT_PROVEN
    if not before.readable or not after.readable:
        return UNCHANGED_NOT_PROVEN
    # Reject null-equality: both counts must be CONCRETE ints (never None==None).
    if type(before.source_count) is not int or type(after.source_count) is not int:
        return UNCHANGED_NOT_PROVEN
    if before.source_count != after.source_count:
        return UNCHANGED_NOT_PROVEN
    if (before.namespace, before.database) != (after.namespace, after.database):
        return UNCHANGED_NOT_PROVEN
    if before.default_embedding_model_present != after.default_embedding_model_present:
        return UNCHANGED_NOT_PROVEN
    return UNCHANGED_YES


__all__ = [
    "UNCHANGED_YES",
    "UNCHANGED_NOT_PROVEN",
    "NormalDbReasonCode",
    "NormalDbBaselineError",
    "NormalDbBaseline",
    "read_normal_db_baseline",
    "require_readable_baseline",
    "compare_normal_db",
]
