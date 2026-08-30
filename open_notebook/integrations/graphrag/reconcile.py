"""GraphRAG-03D RECONCILE: defense-in-depth drift detection + safe repair.

Reconciliation compares canonical SurrealDB ``source`` records against the
LightRAG sidecar's derived documents, classifies drift, and applies ONLY safe
repairs by REUSING the existing lifecycle:

    owned orphan / should-be-absent  ->  arm a durable deletion tombstone (03B/03C)
    authoritatively-missing (flag ON) ->  enqueue a source_id-only 03A index

It is NOT a second delete engine and NOT a REBUILD. It never deletes remotely
itself (03C does, at its proven single-response ceiling) and never blindly
reindexes.

Source of truth: SurrealDB ``source`` is canonical; LightRAG is derived. The
desired derived state is determined by CURRENT canonical state:

    canonical absent                         -> derived doc should be ABSENT
    canonical live, empty/whitespace text    -> derived doc should be ABSENT
    canonical live, non-empty, indexing ON   -> derived doc should EXIST (current)
    canonical live, non-empty, indexing OFF  -> derived doc should be ABSENT

Hard forensic limit (pinned LightRAG v1.5.6): there is NO by-id lookup, NO
corpus-wide keyset cursor, offset paging caps at 200, and ``GET /documents`` is
an unbounded inconsistent dump — so absence CANNOT be authoritatively proven
above the single-response ceiling under concurrent mutation (see
GRAPHRAG_03D_RECONCILE.md §9). Therefore 03D:

  * detects orphans by POSITIVE observation (a doc that IS present + IS owned +
    whose source IS absent) — offset multi-page enumeration is fine for this
    because incomplete/racy enumeration only UNDER-detects (a re-run converges),
    it can never manufacture a false orphan;
  * does NOT resolve tombstones (that stays with 03C at the same ceiling);
  * classifies missing docs authoritatively ONLY from a single complete-snapshot
    page, else UNKNOWN/INCOMPLETE (never a bulk reindex — that would be REBUILD
    creep + false Boundary-B egress);
  * treats an owned, present, live+non-empty+flag-ON document as
    PRESENT_UNVERIFIED (no content_hash exists to prove freshness — content-drift
    convergence is deferred to 03E), never as stale.

Ownership is proven, never assumed: a remote document is Open-Notebook-owned ONLY
when its ``file_path`` parses losslessly as a canonical ``source`` RecordID AND
``compute_doc_id`` of that id equals the document's id. Anything else is FOREIGN
or UNKNOWN_OWNERSHIP and is reported only — never a destructive target.

No document content is read for egress, logged, or returned: reconcile reads
``full_text`` locally only to decide empty/non-empty (never transmits it), and
the typed summary carries only counts + capped sample identities.
"""

import asyncio
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional

from loguru import logger

from open_notebook.database.repository import repo_query
from open_notebook.integrations.graphrag import deletion
from open_notebook.integrations.graphrag.client import compute_doc_id
from open_notebook.integrations.graphrag.config import (
    GraphRAGReconcileConfig,
    load_reconcile_config,
)
from open_notebook.integrations.graphrag.drain import enqueue_drain_if_pending
from open_notebook.integrations.graphrag.models import (
    _INDEXABLE_TABLES,
    GraphRAGError,
    GraphRAGValidationError,
    RemoteDocument,
    RemoteDocumentsPage,
    record_id_for,
)
from open_notebook.integrations.graphrag.service import GraphRAGService

#: Name of the surreal-commands reconcile command (registered in commands/).
RECONCILE_COMMAND_NAME = "graphrag_reconcile"

#: The 03A index command reconcile enqueues (source_id ONLY) for missing repair.
INDEX_COMMAND_NAME = "graphrag_index_source"


class OwnershipClass(str, Enum):
    """Whether a remote document is provably Open-Notebook-owned.

    - OWNED: file_path is a lossless canonical ``source`` id AND
      ``compute_doc_id(file_path) == doc.id`` (both required).
    - FOREIGN: no usable ``file_path`` join key, or it is not a canonical
      ``source`` record id — the document was not created through our contract.
    - UNKNOWN_OWNERSHIP: ``file_path`` IS a valid ``source`` id but its
      ``compute_doc_id`` does NOT match the document's id — inconsistent
      provenance we must not act on destructively.
    """

    OWNED = "owned"
    FOREIGN = "foreign"
    UNKNOWN_OWNERSHIP = "unknown_ownership"


def classify_ownership(doc: RemoteDocument) -> tuple[OwnershipClass, Optional[str]]:
    """Classify a remote document's ownership; return (class, canonical source_id).

    STRONG contract, both conditions required for OWNED (task §5/§6):
      A. ``doc.file_path`` parses losslessly as a canonical ``source`` RecordID.
      B. ``compute_doc_id`` of that canonical id equals ``doc.doc_id``.

    A document whose id merely starts with "doc-" is NEVER owned on that basis.
    The canonical source_id is returned only for OWNED (None otherwise), so a
    non-owned document can never become a destructive target.
    """
    file_path = doc.file_path
    if not doc.doc_id or not file_path:
        return (OwnershipClass.FOREIGN, None)
    try:
        canonical = str(record_id_for(file_path, tables=_INDEXABLE_TABLES))
    except GraphRAGValidationError:
        # Not a canonical source record id -> not created through our contract.
        return (OwnershipClass.FOREIGN, None)
    if compute_doc_id(canonical) != doc.doc_id:
        # Valid source id but the id does not match our deterministic derivation:
        # inconsistent provenance. Report, never act.
        return (OwnershipClass.UNKNOWN_OWNERSHIP, None)
    return (OwnershipClass.OWNED, canonical)


@dataclass
class ReconcileSummary:
    """Typed aggregate result of one reconcile run (counts + capped samples only).

    Carries NO document content and NO raw sidecar payloads. Sample identities are
    record ids / doc ids (opaque), capped per class. ``incomplete_inventory`` is
    set whenever any bound was hit, any listing errored, or absence could not be
    authoritatively established — so a caller NEVER reads a truncated/racy run as
    "healthy / no drift".
    """

    mode: str  # "audit" | "repair"
    # Remote sweep (canonical <- remote): positive-observation orphan detection.
    remote_scanned: int = 0
    owned_present_unverified: int = 0
    owned_orphan: int = 0
    owned_should_be_absent: int = 0
    foreign: int = 0
    unknown_ownership: int = 0
    deletion_intents_armed: int = 0
    deletion_intents_already_pending: int = 0
    # Canonical sweep (remote <- canonical): authoritative missing detection.
    canonical_scanned: int = 0
    present_confirmed: int = 0
    missing_confirmed: int = 0
    index_repairs_enqueued: int = 0
    missing_inventory_incomplete: bool = False
    # Global.
    incomplete_inventory: bool = False
    errors: int = 0
    notes: str = ""
    samples: Dict[str, List[str]] = field(default_factory=dict)
    max_sample_ids: int = 20

    def add_sample(self, key: str, value: str) -> None:
        bucket = self.samples.setdefault(key, [])
        if value and len(bucket) < self.max_sample_ids:
            bucket.append(value)

    def __str__(self) -> str:  # compact, log-safe (no content, no raw ids beyond counts)
        return (
            f"mode={self.mode} remote_scanned={self.remote_scanned} "
            f"orphan={self.owned_orphan} should_be_absent={self.owned_should_be_absent} "
            f"present_unverified={self.owned_present_unverified} "
            f"foreign={self.foreign} unknown_ownership={self.unknown_ownership} "
            f"armed={self.deletion_intents_armed} "
            f"already_pending={self.deletion_intents_already_pending} "
            f"canonical_scanned={self.canonical_scanned} "
            f"missing={self.missing_confirmed} present={self.present_confirmed} "
            f"index_repairs={self.index_repairs_enqueued} "
            f"incomplete={self.incomplete_inventory} errors={self.errors}"
        )


def _is_complete_snapshot(page: RemoteDocumentsPage) -> bool:
    """True iff ONE response authoritatively enumerated the ENTIRE current document
    set — the only basis on which 03D may treat a doc's absence as authoritative.

    Fails closed on ANY pagination/count/readability disagreement (a self-
    contradictory response — which the pinned v1.5.6 never emits, but a buggy or
    version-changed sidecar could): not malformed, not still advertising another
    page (``has_next``), a single page (``total_pages <= 1``), the server count
    equals the number of readable ids, AND every returned row carried a usable id
    (no dropped/unreadable rows). Any mismatch → False → the caller marks the run
    incomplete and never classifies a missing/absent result from it.
    """
    present_ids = {d.doc_id for d in page.documents if d.doc_id}
    n = len(page.documents)
    # Exactly one page. total_pages==0 is accepted ONLY for a genuinely empty result
    # (some backends report 0 pages for 0 docs); any other value (0-with-docs, or a
    # negative page count) is self-contradictory metadata → not authoritative.
    single_page = page.total_pages == 1 or (page.total_pages == 0 and n == 0)
    return (
        not page.malformed
        and not page.has_next
        and single_page
        and len(present_ids) == n  # every returned row has a usable, unique id
        and page.total_count == n  # server count matches the rows actually returned
    )


async def _canonical_state(record_id: object) -> str:
    """Return CURRENT canonical state: "absent" | "empty" | "nonempty".

    Mirrors the drain's ``.strip()`` semantics so reconcile and 03C agree on what
    "empty" means. Only ``full_text`` is read, and ONLY to decide empty/non-empty
    — it is never transmitted, logged, or returned. One record at a time keeps
    memory bounded regardless of corpus size.
    """
    rows = await repo_query("SELECT full_text FROM $id", {"id": record_id})
    if not rows:
        return "absent"
    full_text = rows[0].get("full_text") or ""
    return "nonempty" if full_text.strip() else "empty"


async def _maybe_arm(
    source_id: str, summary: ReconcileSummary, *, repair: bool
) -> None:
    """In REPAIR mode, arm a durable deletion intent (no re-arm churn). AUDIT: no-op."""
    if not repair:
        return
    try:
        armed = await deletion.arm_orphan_deletion(source_id)
    except Exception as e:  # never abort the sweep for one row
        summary.errors += 1
        logger.warning(f"GraphRAG reconcile: arm failed: {type(e).__name__}")
        return
    if armed:
        summary.deletion_intents_armed += 1
    else:
        summary.deletion_intents_already_pending += 1


async def _classify_remote_doc(
    service: GraphRAGService,
    doc: RemoteDocument,
    summary: ReconcileSummary,
    *,
    repair: bool,
) -> None:
    """Classify ONE remote document and, in REPAIR mode, arm should-be-absent ones."""
    if not doc.doc_id:
        # An unreadable row (a dict with no usable id): we could not read this part
        # of the inventory, so mark the run incomplete rather than miscount it as a
        # foreign document. No action is possible without an id.
        summary.incomplete_inventory = True
        return
    kind, source_id = classify_ownership(doc)
    if kind is OwnershipClass.FOREIGN:
        summary.foreign += 1
        summary.add_sample("foreign", doc.doc_id)  # a doc-<md5> hash, never content
        return
    if kind is OwnershipClass.UNKNOWN_OWNERSHIP:
        summary.unknown_ownership += 1
        summary.add_sample("unknown_ownership", doc.doc_id)
        return

    # OWNED: the desired state depends on CURRENT canonical state.
    assert source_id is not None
    try:
        record_id = record_id_for(source_id, tables=_INDEXABLE_TABLES)
        state = await _canonical_state(record_id)
    except GraphRAGValidationError:
        # Defensive: OWNED implies a valid id, so this is unreachable in practice.
        summary.unknown_ownership += 1
        return
    except Exception as e:
        summary.errors += 1
        summary.incomplete_inventory = True
        logger.debug(f"GraphRAG reconcile: canonical load failed: {type(e).__name__}")
        return

    if state == "absent":
        summary.owned_orphan += 1
        summary.add_sample("owned_orphan", source_id)
        await _maybe_arm(source_id, summary, repair=repair)
    elif state == "empty":
        summary.owned_should_be_absent += 1
        summary.add_sample("should_be_absent", source_id)
        await _maybe_arm(source_id, summary, repair=repair)
    else:  # nonempty
        if service.config.enabled:
            # Live + non-empty + indexing ON: the doc SHOULD exist. We have no
            # content_hash to prove it is current, so this is PRESENT_UNVERIFIED,
            # never "stale". Content-drift convergence is 03E, not 03D.
            summary.owned_present_unverified += 1
        else:
            # Live + non-empty but indexing OFF: desired remote state is ABSENT
            # (reindexing would be a Boundary-B egress while the feature is off).
            summary.owned_should_be_absent += 1
            summary.add_sample("should_be_absent", source_id)
            await _maybe_arm(source_id, summary, repair=repair)


async def _sweep_remote(
    service: GraphRAGService,
    cfg: GraphRAGReconcileConfig,
    summary: ReconcileSummary,
    *,
    repair: bool,
) -> None:
    """Bounded, streaming remote sweep for owned orphans / should-be-absent docs.

    Offset pagination is SAFE here: this is positive-observation detection, so an
    offset shift under concurrent mutation can only make us MISS a doc this run (a
    later run converges) — it can never invent a false orphan. Bounded by
    ``max_records``; hitting the cap (or any listing error) marks the run
    INCOMPLETE so it is never read as "no drift".
    """
    # Bound the number of PAGES too, not just records: a misbehaving backend that
    # returns empty pages with has_next=True (incrementing total_pages) would never
    # advance remote_scanned and could loop forever otherwise.
    max_pages = max(1, (cfg.max_records // cfg.remote_page_size) + 1)
    page = 1
    while summary.remote_scanned < cfg.max_records:
        try:
            docpage = await service.list_remote_documents_detailed(
                page=page, page_size=cfg.remote_page_size
            )
        except GraphRAGError as e:
            summary.incomplete_inventory = True
            summary.errors += 1
            logger.warning(
                f"GraphRAG reconcile: remote listing failed on page {page}: "
                f"{type(e).__name__}"
            )
            return
        if docpage.malformed:
            # Non-dict rows were dropped: this page was only partially readable, so
            # the run can no longer claim it saw the whole inventory.
            summary.incomplete_inventory = True
        for doc in docpage.documents:
            if summary.remote_scanned >= cfg.max_records:
                summary.incomplete_inventory = True
                return
            summary.remote_scanned += 1
            await _classify_remote_doc(service, doc, summary, repair=repair)
        # Continue while EITHER has_next OR the authoritative page count says more
        # pages exist. Relying on has_next alone lets an omitted/false has_next (with
        # total_pages > 1) stop the sweep after page 1 while the run still reports
        # complete — orphans on later pages would be silently missed. total_pages is
        # the fail-closed signal here (still bounded by the page cap below).
        if not docpage.has_next and page >= max(docpage.total_pages, 1):
            # Natural end. A sweep may claim authoritative completeness ONLY when the
            # ENTIRE corpus was a single internally-consistent page: offset
            # pagination cannot prove full coverage across pages (the forensic
            # ceiling), and a one-page result with contradictory metadata
            # (count/total_pages/has_next/unreadable rows) is not trustworthy. In
            # every other case fail closed — the orphans found are still valid
            # (positive observation), but "no more drift" is not provable here.
            if not (page == 1 and _is_complete_snapshot(docpage)):
                summary.incomplete_inventory = True
            return
        if page >= max_pages:
            # Page bound reached (guards empty-page loops and any pagination that
            # would outrun max_records). Never read as "no drift".
            summary.incomplete_inventory = True
            return
        page += 1
    # Fell out because remote_scanned reached max_records with more pages possible.
    summary.incomplete_inventory = True


async def _enqueue_index(source_id: str, summary: ReconcileSummary) -> None:
    """Enqueue a source_id-ONLY 03A index (the worker reloads CURRENT source)."""
    from surreal_commands import submit_command

    try:
        # submit_command uses a blocking DB client; keep it off the event loop.
        await asyncio.to_thread(
            submit_command, "open_notebook", INDEX_COMMAND_NAME, {"source_id": source_id}
        )
        summary.index_repairs_enqueued += 1
    except Exception as e:
        summary.errors += 1
        logger.warning(
            f"GraphRAG reconcile: index enqueue failed: {type(e).__name__}"
        )


async def _sweep_canonical_missing(
    service: GraphRAGService,
    cfg: GraphRAGReconcileConfig,
    summary: ReconcileSummary,
    *,
    repair: bool,
) -> None:
    """Authoritative missing-doc detection (canonical -> remote), flag-ON only.

    Missing detection requires proving a specific doc is ABSENT, which is only
    possible from a single COMPLETE snapshot (total_pages <= 1 AND the id count
    matches total_count — the exact 03C proof). If the corpus needs more than one
    page, NO absence can be authoritatively established, so this whole phase is
    marked INCOMPLETE and skipped rather than probing every source in vain or
    guessing (task §9/§25 — never bulk-reindex on uncertain absence). When the
    snapshot IS complete, membership in its id set is authoritative, so a live
    non-empty source whose ``compute_doc_id`` is not present is genuinely missing;
    in REPAIR mode it is repaired by a source_id-only 03A enqueue (never full_text;
    03A reloads current state and no-ops if the source changed).
    """
    try:
        snapshot = await service.list_remote_documents_detailed(
            page=1, page_size=cfg.remote_page_size
        )
    except GraphRAGError as e:
        # A snapshot failure prevented missing detection: report it truthfully as an
        # error (not just incomplete), mirroring the remote-sweep listing-error path.
        summary.errors += 1
        summary.incomplete_inventory = True
        summary.missing_inventory_incomplete = True
        logger.warning(
            f"GraphRAG reconcile: missing-snapshot listing failed: {type(e).__name__}"
        )
        return

    if not _is_complete_snapshot(snapshot):
        # Not an authoritatively complete single response (above the ceiling, a
        # count/id mismatch, a partially-readable/malformed page, or one that still
        # advertises another page): cannot prove any absence. Do NOT classify
        # anything missing; do NOT reindex.
        summary.missing_inventory_incomplete = True
        summary.incomplete_inventory = True
        return
    present_ids = {d.doc_id for d in snapshot.documents if d.doc_id}

    # Keyset cursor MUST be a RecordID, not the stringified id repo_query returns:
    # on SurrealDB v2.6.5 a string bound as $last makes `id > $last` compare wrong
    # (it does not exclude the boundary), and `SELECT full_text FROM $id` with a
    # string $id returns nothing (a live source would read as absent). Rebuilding
    # each id with record_id_for fixes both (verified live).
    last_id = None  # RecordID | None
    while summary.canonical_scanned < cfg.max_records:
        limit = min(cfg.canonical_batch_size, cfg.max_records - summary.canonical_scanned)
        try:
            if last_id is None:
                raw_rows = await repo_query(
                    "SELECT VALUE id FROM source ORDER BY id ASC LIMIT $n",
                    {"n": limit},
                )
            else:
                raw_rows = await repo_query(
                    "SELECT VALUE id FROM source WHERE id > $last "
                    "ORDER BY id ASC LIMIT $n",
                    {"last": last_id, "n": limit},
                )
        except Exception as e:
            summary.errors += 1
            summary.incomplete_inventory = True
            logger.warning(
                f"GraphRAG reconcile: canonical enumeration failed: {type(e).__name__}"
            )
            return
        if not raw_rows:
            return
        for raw in raw_rows:
            summary.canonical_scanned += 1
            try:
                rid = record_id_for(str(raw), tables=_INDEXABLE_TABLES)
            except GraphRAGValidationError:
                # A non-canonical id in the source table (shouldn't happen): cannot
                # bind it as a record safely, so mark incomplete and skip it.
                summary.errors += 1
                summary.incomplete_inventory = True
                continue
            last_id = rid
            try:
                state = await _canonical_state(rid)
            except Exception:
                # Could not determine canonical state: do NOT silently skip into a
                # clean/no-drift result — mark the run incomplete.
                summary.errors += 1
                summary.incomplete_inventory = True
                continue
            if state != "nonempty":
                # empty/absent sources are NOT "missing": their desired remote state
                # is ABSENT (handled by the remote sweep / 03C), not indexed.
                continue
            source_id = str(rid)
            doc_id = compute_doc_id(source_id)
            if doc_id in present_ids:
                summary.present_confirmed += 1
                continue
            # Missing per the authoritative snapshot.
            summary.missing_confirmed += 1
            summary.add_sample("missing", source_id)
            if repair:
                await _repair_missing(service, source_id, summary, cfg)
        if len(raw_rows) < limit:
            return  # canonical corpus exhausted
    # Hit max_records with more canonical sources possibly unexamined.
    summary.incomplete_inventory = True


async def _repair_missing(
    service: GraphRAGService,
    source_id: str,
    summary: ReconcileSummary,
    cfg: GraphRAGReconcileConfig,
) -> None:
    """Re-confirm absence FRESH at decision time, then enqueue a source_id-only 03A
    index (worker reloads CURRENT source).

    The complete snapshot that flagged this candidate was taken earlier in the run;
    a concurrent index could have added the document since. This takes a FRESH
    single-response snapshot and applies the SAME fail-closed completeness rule
    (``_is_complete_snapshot`` — carries has_next, unlike the 03C absence probe):
    a reindex is enqueued ONLY when that fresh snapshot authoritatively enumerated
    the whole set AND still does not contain this doc. A raced-in document (now
    present) is not reindexed, and any pagination/metadata disagreement marks the
    run incomplete instead of triggering a blind reindex.
    """
    doc_id = compute_doc_id(source_id)
    try:
        snapshot = await service.list_remote_documents_detailed(
            page=1, page_size=cfg.remote_page_size
        )
    except GraphRAGError:
        # Could not re-confirm -> do not reindex blindly; keep the run honest and
        # report the failure truthfully (consistent with the other listing paths).
        summary.errors += 1
        summary.incomplete_inventory = True
        return
    if not _is_complete_snapshot(snapshot):
        summary.incomplete_inventory = True
        return
    if doc_id in {d.doc_id for d in snapshot.documents if d.doc_id}:
        return  # raced in since the Phase-B snapshot -> present -> not missing
    await _enqueue_index(source_id, summary)


async def reconcile(
    service: GraphRAGService | None = None,
    *,
    repair: bool = False,
    reconcile_config: GraphRAGReconcileConfig | None = None,
) -> ReconcileSummary:
    """Run one bounded reconcile pass. AUDIT (default) detects only; REPAIR arms.

    AUDIT never mutates anything (no arm, no enqueue). REPAIR arms durable deletion
    intents for owned orphans / should-be-absent docs (drained by 03C) and, when
    indexing is ON and absence is authoritative, enqueues source_id-only 03A index
    repairs. It NEVER deletes remotely itself and NEVER resolves a tombstone.

    Gated on sidecar CONFIG (base_url) only — the confidentiality half runs even
    with indexing OFF. With no base_url the run is INCOMPLETE/unavailable, never
    "no drift" (task §27).
    """
    service = service or GraphRAGService()
    cfg = reconcile_config or load_reconcile_config()
    summary = ReconcileSummary(mode="repair" if repair else "audit")
    summary.max_sample_ids = cfg.max_sample_ids

    if not service.config.base_url:
        summary.incomplete_inventory = True
        summary.notes = "sidecar not configured (no base_url)"
        logger.info(f"GraphRAG reconcile: {summary}")
        return summary

    await _sweep_remote(service, cfg, summary, repair=repair)

    # Missing detection/repair only when indexing is ON: with the flag OFF the
    # desired state of a missing doc is ABSENT, so "missing" is not a drift to fix.
    if service.config.enabled:
        await _sweep_canonical_missing(service, cfg, summary, repair=repair)

    # If we armed any deletion intents, wake the 03C drain so convergence starts
    # promptly (dedup + idempotent; correctness never depends on this).
    if repair and summary.deletion_intents_armed > 0:
        try:
            await enqueue_drain_if_pending()
        except Exception as e:
            logger.debug(
                f"GraphRAG reconcile: drain wake-up skipped: {type(e).__name__}"
            )

    logger.info(f"GraphRAG reconcile complete: {summary}")
    return summary
