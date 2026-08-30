"""Durable GraphRAG deletion tombstones: enumeration + arm-fenced state (03B/03C).

A tombstone is durable LOCAL evidence that a canonical Source was deleted and
that its derived LightRAG document therefore needs to be removed. The rows are
written by a SurrealDB event (migration 24: ``graphrag_source_delete``, re-armed
with a ``next_attempt_at`` schedule in migration 25) **atomically with the
canonical Source delete**, on every delete path including a raw SurrealQL
``DELETE source`` that runs no Python. They are NOT created by this module and
NOT by any Python domain hook.

This module owns the tombstone's DATABASE surface and nothing else:
  - **Read** (03B): ``list_pending_deletions`` and the bounded, fair
    ``list_due_deletions`` / ``has_due_deletions`` enumeration the 03C worker
    drains.
  - **Resolve / defer** (03C): ``resolve_tombstone_cas`` (DELETE) and
    ``defer_tombstone_cas`` (reschedule) are compare-and-set writes fenced on the
    exact ``arm_id`` snapshot the drain processed.

It performs **no HTTP** and imports no LightRAG client/service code — turning a
tombstone into an actual LightRAG deletion (deriving the doc_id, calling the
sidecar, confirming absence) lives in ``drain.py`` / ``service`` / ``client``.
Keeping the remote lifecycle out of here preserves the 03B guarantee that the
deletion intent is *durable*, independent of the feature flag and of
sidecar/worker availability; 03C guarantees it eventually *converges*. The only
import from the integration is ``models.record_id_for`` (pure RecordID types, no
HTTP), needed to bind the source identity losslessly for the CAS predicates.

The tombstone carries only the canonical source identity, a timestamp, a
lifecycle status, an opaque ``arm_id`` fence, and a ``next_attempt_at`` schedule.
It never carries document text, title, URL, file path, notebook metadata, or
credentials (enforced by the SCHEMAFULL field set and asserted by tests).
"""

from dataclasses import dataclass
from datetime import datetime
from typing import List, Optional

from open_notebook.database.repository import repo_query
from open_notebook.integrations.graphrag.models import _INDEXABLE_TABLES, record_id_for

#: SurrealDB table that holds durable deletion tombstones (migration 24).
DELETION_TABLE = "graphrag_deletion"

#: Lifecycle status of a tombstone that has not yet been drained by 03C.
STATUS_PENDING = "pending"


@dataclass(frozen=True)
class DeletionTombstone:
    """One durable deletion intent for a deleted canonical Source.

    ``source_id`` is the canonical Open Notebook record id, preserved losslessly
    (numeric ``source:123`` and string-numeric ``source:⟨123⟩`` stay distinct).
    GraphRAG-03C derives the LightRAG ``doc_id`` from it via
    ``GraphRAGClient.compute_doc_id(source_id)`` — the id is intentionally NOT
    stored here, to avoid a duplicated, drift-prone copy.

    ``arm_id`` is the per-arm fence token (a fresh ``rand::uuid()`` written by the
    delete event on every arm/re-arm). GraphRAG-03C captures it in the dequeued
    snapshot and resolves the tombstone with a compare-and-set on it: if the row
    was re-armed (deleted again) between dequeue and resolve, ``arm_id`` changed,
    the CAS matches zero rows, and the newer deletion intent is not cleared. It
    carries no source content — it is an opaque random token.
    """

    source_id: str
    status: str
    arm_id: str
    requested_at: Optional[datetime] = None
    #: GraphRAG-03C fair-drain schedule (migration 25). A tombstone is eligible
    #: when ``next_attempt_at <= now``; a non-converged drain defers it into the
    #: future so it leaves the current due set and later tombstones progress.
    next_attempt_at: Optional[datetime] = None


def _to_tombstone(row: dict) -> DeletionTombstone:
    source_id = row.get("source_id")
    arm_id = row.get("arm_id")
    return DeletionTombstone(
        # repo_query stringifies RecordID values; keep the canonical string.
        source_id=str(source_id) if source_id is not None else "",
        status=str(row.get("status") or ""),
        # arm_id comes back as a uuid value; carry its canonical string form so a
        # 03-C CAS can bind it (as <uuid>$arm_id) without re-deriving it.
        arm_id=str(arm_id) if arm_id is not None else "",
        requested_at=row.get("requested_at"),
        next_attempt_at=row.get("next_attempt_at"),
    )


async def list_pending_deletions() -> List[DeletionTombstone]:
    """Enumerate tombstones still awaiting deletion, oldest first.

    The 03C enumeration primitive. Read-only: it never mutates a tombstone and
    never contacts the sidecar. Ordering by ``requested_at`` lets a future drain
    process the oldest deletion intents first.

    Returns every pending row. Bounded/paginated enumeration is deliberately not
    provided here: batching is a draining concern, so GraphRAG-03C may add it
    when the worker is implemented.
    """
    rows = await repo_query(
        f"SELECT * FROM {DELETION_TABLE} WHERE status = $status ORDER BY requested_at ASC",
        {"status": STATUS_PENDING},
    )
    return [_to_tombstone(row) for row in rows]


async def list_due_deletions(limit: int) -> List[DeletionTombstone]:
    """Return a BOUNDED batch of tombstones that are due for a drain attempt.

    "Due" = ``status = 'pending' AND next_attempt_at <= time::now()`` (migration
    25). Ordered by ``next_attempt_at, id`` for a stable, testable order, capped at
    ``limit``. Deliberately NOT offset-paginated: the drain resolves rows by
    DELETE and defers non-converged rows into the future, so both leave the due
    set — the next call returns the *next* due rows without any START/OFFSET that a
    mutating set would make skip work. A deferred (failing) row drops out of the
    due set until its delay elapses, so a persistently-failing row cannot starve
    later tombstones. ``SELECT *`` (not a projection) because v2.6.5 requires the
    ORDER BY field to appear in the selection.
    """
    rows = await repo_query(
        f"SELECT * FROM {DELETION_TABLE} "
        f"WHERE status = $status AND next_attempt_at <= time::now() "
        f"ORDER BY next_attempt_at ASC, id ASC LIMIT $limit",
        {"status": STATUS_PENDING, "limit": max(1, int(limit))},
    )
    return [_to_tombstone(row) for row in rows]


async def has_due_deletions() -> bool:
    """Cheap check: is at least one tombstone currently due for draining?

    Used by the periodic wake-up to decide whether to enqueue a drain. It is an
    OPTIMISATION only, never a lock: two replicas may both see work and both
    enqueue, which is safe because remote operations are idempotent and
    resolution/deferral are arm_id compare-and-set.
    """
    rows = await repo_query(
        f"SELECT id FROM {DELETION_TABLE} "
        f"WHERE status = $status AND next_attempt_at <= time::now() LIMIT 1",
        {"status": STATUS_PENDING},
    )
    return len(rows) > 0


async def resolve_tombstone_cas(source_id: str, arm_id: str) -> bool:
    """Resolve a tombstone by DELETE, fenced on the exact ``arm_id`` snapshot.

    Returns True iff exactly one row was deleted. Zero rows means the snapshot is
    stale — the source was deleted again (a re-arm minted a new ``arm_id``) or the
    row was already resolved — so the caller must NOT treat it as done and must
    re-drive against current state. ``arm_id`` (not ``requested_at`` or
    ``next_attempt_at``) is the authoritative fence.

    The source RecordID is rebuilt losslessly (``record_id_for``), never via
    ``RecordID.parse``, so numeric vs string-numeric vs escaped identities bind
    the correct single deterministic tombstone.
    """
    sid = record_id_for(source_id, tables=_INDEXABLE_TABLES)
    rows = await repo_query(
        f"DELETE {DELETION_TABLE} "
        f"WHERE source_id = $sid AND status = $status AND arm_id = <uuid>$arm "
        f"RETURN BEFORE",
        {"sid": sid, "status": STATUS_PENDING, "arm": arm_id},
    )
    return len(rows) == 1


async def resolve_current_tombstone_cas(
    source_id: str, arm_id: str, expected_text: str
) -> bool:
    """Resolve a LIVE-source tombstone ATOMICALLY with a canonical-text condition.

    For the live-non-empty convergence branch: after the current text is indexed,
    the tombstone must be resolved only if the canonical source STILL holds exactly
    the text that was shipped. A plain read-then-CAS has an irreducible TOCTOU (a
    redaction/delete landing between the read and the resolve could bless stale
    sidecar text). This folds the canonical condition INTO the CAS predicate via
    the record-link dereference ``source_id.full_text``, so the check and the
    delete are one atomic SurrealDB statement (verified live on v2.6.5): a
    redacted/emptied source (``full_text`` changed) or a deleted source (dangling
    link → NONE) matches zero rows and the tombstone is left pending for the next
    attempt (which then takes the empty/absent delete branch).

    Returns True iff exactly one row was deleted (arm still current AND canonical
    text unchanged). Zero rows = re-armed, already resolved, or canonical changed —
    the caller must NOT treat it as done.
    """
    sid = record_id_for(source_id, tables=_INDEXABLE_TABLES)
    rows = await repo_query(
        f"DELETE {DELETION_TABLE} "
        f"WHERE source_id = $sid AND status = $status AND arm_id = <uuid>$arm "
        f"AND source_id.full_text = $expected "
        f"RETURN BEFORE",
        {
            "sid": sid,
            "status": STATUS_PENDING,
            "arm": arm_id,
            "expected": expected_text,
        },
    )
    return len(rows) == 1


async def defer_tombstone_cas(arm_id: str, delay_seconds: int) -> bool:
    """Push a non-converged tombstone's ``next_attempt_at`` into the future.

    Fenced on ``arm_id`` ALONE — deliberately NOT on source_id. ``arm_id`` is a
    fresh, unique ``rand::uuid()`` per arm, so it identifies the exact row without
    re-parsing the source identity. That matters for robustness: a malformed
    source_id (near-impossible under the ``record<source>`` schema, but reachable
    via a raw DB write) must still be movable OUT of the due set so it cannot
    monopolize a bounded batch; binding source_id here would raise on such a row
    and, worse, leave it perpetually due. If the row was re-armed while the drain
    worked, ``arm_id`` changed, the CAS matches zero rows, and the freshly re-armed
    tombstone (immediately due) is left intact. Returns True iff a row was moved.

    ``delay_seconds`` is floored to a positive value (a zero/negative delay would
    keep the row in the due set and hot-loop); the caller passes a bounded,
    configured retry delay.
    """
    rows = await repo_query(
        f"UPDATE {DELETION_TABLE} "
        f"SET next_attempt_at = time::now() + type::duration($delay) "
        f"WHERE status = $status AND arm_id = <uuid>$arm "
        f"RETURN BEFORE",
        {
            "status": STATUS_PENDING,
            "arm": arm_id,
            "delay": f"{max(1, int(delay_seconds))}s",
        },
    )
    return len(rows) == 1
