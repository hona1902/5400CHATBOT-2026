"""Read-only access to durable GraphRAG deletion tombstones (GraphRAG-03B).

A tombstone is durable LOCAL evidence that a canonical Source was deleted and
that its derived LightRAG document therefore needs to be removed. The rows are
written by a SurrealDB event (migration 24: ``graphrag_source_delete``)
**atomically with the canonical Source delete**, on every delete path including a
raw SurrealQL ``DELETE source`` that runs no Python. They are NOT written by this
module and NOT by any Python domain hook.

This module only READS tombstones. It performs no HTTP, imports no LightRAG code,
and does no draining, retrying, or resolving. Turning a tombstone into an actual
LightRAG deletion — deriving ``doc_id = compute_doc_id(source_id)``, calling the
sidecar, retrying, and marking the row resolved — is GraphRAG-03C. Keeping that
worker out of this phase is deliberate: 03B guarantees the deletion intent is
*durable*, independent of the GraphRAG feature flag and of sidecar/worker
availability; 03C guarantees it eventually *converges*.

The tombstone carries only the canonical source identity, a timestamp, and a
lifecycle status. It never carries document text, title, URL, file path,
notebook metadata, or credentials (enforced by the migration-24 SCHEMAFULL
field set and asserted by tests).
"""

from dataclasses import dataclass
from datetime import datetime
from typing import List, Optional

from open_notebook.database.repository import repo_query

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
