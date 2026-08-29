# GraphRAG-03B — Durable Deletion State + Database Delete Event

**Status: GraphRAG-03B APPROVED / COMPLETE** — signed off 2026-08-28 (Option A + migration 24, separate event, minimal state + `arm_id` fence). Karpathy CLEAN · Codex APPROVE · no unresolved actionable HIGH.
**Branch:** `feature/graphrag-lifecycle` · **Baseline:** `22e0b31` (03A checkpoint) · **SurrealDB runtime:** `2.6.5` · **LightRAG pinned:** `v1.5.6` (not contacted in this phase).
**Egress:** synthetic / public / anonymized only. Boundary B remains **NOT approved**. This phase makes **no** network calls at all.

Forensic basis: [`../architecture/GRAPHRAG_LIFECYCLE_FORENSIC.md`](../architecture/GRAPHRAG_LIFECYCLE_FORENSIC.md) §5, §10, §17, §18. Decision authority: [`GRAPHRAG_DECISION.md`](GRAPHRAG_DECISION.md) (AGR-005) §9, §10. Precedes: 03-C (HTTP drain), 03-D (RECONCILE), 03-E (REBUILD).

---

## 1. Scope

Implements **only** the durable local *evidence* half of deletion:

- Migration **24**: a `graphrag_deletion` tombstone table + a separate `graphrag_source_delete` SurrealDB event that writes a tombstone whenever a canonical `source` record is deleted — through **any** path, including a raw SurrealQL `DELETE source` that runs no Python.
- A read-only integration helper (`deletion.py`) that enumerates pending tombstones — the 03-C enumeration primitive.
- Property-oriented tests + this document.

**Explicitly NOT in this phase** (deferred): HTTP deletion draining, retry/backoff, `LightRAGClient.delete_document` invocation on the lifecycle path, `Source.delete()` best-effort HTTP call, RECONCILE, REBUILD, HybridRetriever/RRF/reranking, Ask/Chat/frontend changes, any real internal data, any Boundary-B behavior.

## 2. Source-of-truth invariant

Open Notebook / SurrealDB `source` is the sole source of truth. LightRAG is derived, rebuildable, removable. The tombstone is **lifecycle metadata**, not canonical data and not a copy of the document.

**Central asymmetry (AGR-005 §9):** INDEXING MAY FAIL OPEN (a missing graph doc is availability, recoverable by REBUILD). **DELETION MAY NOT DISAPPEAR SILENTLY** (a missed delete retains a copy of canonical text in the sidecar — confidentiality). This phase guarantees the *intent to delete* is durable; 03-C guarantees it eventually *converges*.

## 3. Schema

Migration 24 (`open_notebook/database/migrations/24.surrealql`):

```surql
DEFINE TABLE IF NOT EXISTS graphrag_deletion SCHEMAFULL;
DEFINE FIELD IF NOT EXISTS source_id     ON TABLE graphrag_deletion TYPE record<source>;
DEFINE FIELD IF NOT EXISTS requested_at  ON TABLE graphrag_deletion TYPE datetime DEFAULT time::now();
DEFINE FIELD IF NOT EXISTS status        ON TABLE graphrag_deletion TYPE string   DEFAULT "pending";
DEFINE FIELD IF NOT EXISTS arm_id        ON TABLE graphrag_deletion TYPE uuid;
```

Four fields, none content-bearing:
- `source_id` — the canonical Open Notebook record id, stored losslessly as a `record<source>` link (a dangling link after the source is deleted is fine; SurrealDB does not enforce it as a FK). 03-C derives `doc_id = "doc-" + md5(source_id)` via `GraphRAGClient.compute_doc_id`, so **doc_id is NOT stored** — no duplicated, drift-prone copy.
- `requested_at` — when the deletion was recorded.
- `status` — lifecycle marker, `"pending"` until 03-C resolves it.
- `arm_id` — a fresh per-arm fence token (`rand::uuid()`), minted by the event on **every** arm/re-arm. It is the compare-and-set token for 03-C's tombstone resolution (§17.1.1); it closes the ABA race that a non-unique `requested_at` could not. Opaque random value — carries no source content. **Verified on pinned SurrealDB v2.6.5:** `rand::uuid()` is server-side (no Python/HTTP), yields a distinct time-ordered UUIDv7 on every event execution, and changes on re-arm even when `requested_at` is forced identical.

**Deferred to 03-C** (deliberately absent): `attempts`/`attempt_count`, `last_error`, `next_retry_at`, `resolved_at`, `doc_id`. Those are retry/drain concerns; adding them now would be speculative state.

## 4. Tombstone identity

The event UPSERTs `type::thing("graphrag_deletion", $before.id)` — the tombstone's own record id is deterministically derived from the deleted source's record id. Consequences:

- **Idempotent:** repeated delete intent for the same source collapses to **one** row (UPSERT, not CREATE).
- **Lossless identity:** numeric `source:123` and string-numeric `source:⟨123⟩` are distinct SurrealDB records, so they key distinct tombstones (`graphrag_deletion:123` vs `graphrag_deletion:⟨123⟩`) and store distinct `source_id` links — and therefore distinct `doc_id`s downstream. A lossy key would merge two documents and orphan one in the sidecar.

Verified empirically against live SurrealDB v2 and by `test_numeric_and_string_numeric_ids_stay_distinct` / `test_escaped_record_id_round_trips`.

## 5. Database event

A **separate** event, isolated from the existing vector-cleanup event:

```surql
DEFINE EVENT IF NOT EXISTS graphrag_source_delete ON TABLE source WHEN $after == NONE THEN {
    UPSERT type::thing("graphrag_deletion", $before.id) SET
        source_id = $before.id, requested_at = time::now(), status = "pending";
};
```

The existing `source_delete` event (migration 1: cascades `source_embedding` + `source_insight` deletes) is **untouched**. SurrealDB v2 permits multiple events on one table; the two bodies touch disjoint tables with no ordering dependency, and both fire in the same delete transaction. Rationale (forensic §16, ranked): clean ownership, isolated rollback (down migration removes only the GraphRAG objects), no risk to vector cleanup.

## 6. Atomicity argument (and its exact limit)

The deployment pins `surrealdb/surrealdb:v2`. **In SurrealDB v2, `DEFINE EVENT` executes synchronously, inside the same transaction as the triggering statement**: if the event body fails, the triggering `DELETE` is rolled back; event and trigger share one atomic fate. (Out-of-transaction `ASYNC` events exist only in SurrealDB v3.0-beta+ and are not used.) Therefore:

> **The tombstone exists if and only if the canonical source delete committed** — for every delete path, including a raw SurrealQL `DELETE source` that converges at the same event. This is the exact guarantee the existing `source_delete` vector cleanup already relies on. It closes the "app crashes after `DELETE source` commits but before enqueue" hole, because the write is done *by the database event*, not by application code after the commit.

**Honest limits (not overclaimed):**
- This is **parity** with the existing vector-cleanup event, not a novel stronger guarantee.
- It guarantees durable **local intent only**, not remote sidecar removal. Turning intent into an actual LightRAG deletion is 03-C.
- **Version coupling:** if a future deployment upgrades to SurrealDB v3 and marks this event `ASYNC`, atomicity weakens (the event would run in a separate transaction). The event is defined *without* `ASYNC`; keep it that way.

## 7. Feature-flag decision (Option A)

A SurrealDB event **cannot read the Python env var** `OPEN_NOTEBOOK_GRAPHRAG_ENABLED`. Options evaluated:

| Option | Verdict |
|---|---|
| **A — always write a tiny tombstone on any source delete** | **CHOSEN.** Correct, tiny, content-free. A never-indexed source produces a harmless no-op drain in 03-C (LightRAG delete-of-absent → `deletion_started`/`not_found` → GONE → idempotent success). |
| B — only if the source was GraphRAG-indexed | Rejected: needs a persistent index-state registry that does not exist (03-A added none); building it grows state and re-opens the row-18 trap. |
| C — gate the event on a mirrored DB flag row | Rejected: **actively wrong.** Flag off ⇒ deleted text's tombstone never written ⇒ sidecar copy lingers with no local evidence — the exact failure-matrix **row 18** confidentiality trap (§0.5: deletion must not fail open). |

**Deletion correctness is independent of the runtime enable flag** (forensic §18.2, row 18). With the flag OFF: no HTTP, no LightRAG call, no provider/model egress — but the DB event **still** writes the tiny local tombstone. 03-C later decides whether to drain while the feature is disabled.

**Invariant refinement (approved 2026-08-28):** AGR-005 §14.1's "flag off ⇒ byte-for-byte baseline" is re-keyed to **"migration 24 not applied ⇒ byte-for-byte baseline."** Once migration 24 is applied, a flag-off source delete additionally writes a tombstone row. Removability is preserved: the down migration restores byte-for-byte baseline delete behavior. Tombstone creation has **no outbound side effects** (a pure SurrealQL UPSERT), so flag-off tombstone creation is safe.

## 8. Idempotency

- **Duplicate delete event / repeated delete intent:** UPSERT keyed on source identity → one effective pending tombstone (`test_repeated_delete_is_idempotent_one_tombstone`).
- **Delete/recreate/redelete of the same id:** UPSERT refreshes the single row (re-arms `status="pending"`, refreshes `requested_at`) — correct, since a genuine re-deletion needs draining again.
- **03-C drain (out of scope here):** must treat absent doc / `not_allowed` as success and `busy` as retry (already in `GraphRAGClient.delete_document`).

## 9. RecordID handling

Identity is preserved end-to-end without lossy normalization:
- The event stores `$before.id` verbatim; the tombstone key is `type::thing(..., $before.id)`.
- Lookups/derivation use the lossless builder `record_id_for()` (`models.py`), never `RecordID.parse()` (which double-escapes escaped ids — the 03-A/02 lesson).
- Numeric vs string-numeric vs escaped ids all round-trip distinctly (verified on live DB).

## 10. GraphRAG-03A race interaction

03-A queued INDEX/REINDEX jobs carry `source_id` only and reload the CURRENT canonical Source at execution; a deleted source → `skipped_absent` (no resurrection). 03-B changes none of this — the tombstone is an independent record. Races:
- INDEX/REINDEX pending → source DELETE: the tombstone is written on delete; the pending index job later reloads → absent → `skipped_absent`. Both are consistent (no doc resurrected, deletion intent recorded).
- source DELETE → old INDEX executes: same — reload-current sees absent → no-op.
03-A regression suite (219 tests) passes unchanged.

## 11. No-egress guarantee

This phase performs **zero** network I/O. The event is pure SurrealQL. The helper `deletion.py` imports no `httpx`, no LightRAG, and does not import or instantiate `GraphRAGClient`; it uses only `repo_query` (SELECT). `Source.delete()` gains no GraphRAG reference. Asserted by `TestDeletionHelperIsReadOnlyAndNoHttp` and the existing isolation guards.

## 12. Migration forward

`24.surrealql` registered in `AsyncMigrationManager.up_migrations[23]` (migrations are hard-coded, not discovered). On API startup `run_migration_up` applies it (version 23 → 24). `DEFINE ... IF NOT EXISTS` makes it idempotent. Verified against live SurrealDB v2 using the exact flattened form the manager runs (`AsyncMigration.from_file`): table + fields + event created; raw `DELETE` fires the event; `UPDATE` does not.

## 13. Migration rollback

`24_down.surrealql` registered in `down_migrations[23]`:
```surql
REMOVE EVENT IF EXISTS graphrag_source_delete ON TABLE source;
REMOVE TABLE IF EXISTS graphrag_deletion;
```
Removes **only** the GraphRAG event + tombstone table. The `source` table, the existing `source_delete` event, and all unrelated schema survive (verified live: `test_down_migration_removes_only_graphrag_objects`). Behavior if run while tombstones are pending: dropping the table drops the pending rows — removing the GraphRAG feature removes its local lifecycle metadata cleanly. It does **not** purge any copy already held by a remote LightRAG sidecar; remote purge is a 03-C/03-D operational concern, not a schema rollback.

## 14. Failure / race matrix

| # | Scenario | Canonical state | Tombstone state | Eventual state | Recovery | 03-B solves? |
|---|---|---|---|---|---|---|
| 1 | Source delete commits → app crashes | deleted | **written by event, in the delete txn** | drain later | 03-C drain | **Yes (evidence)** |
| 2 | `source_delete`/graphrag event fires twice | deleted | one row (UPSERT) | one delete | idempotent | **Yes** |
| 3 | Duplicate tombstone insert/update | deleted | one row | — | UPSERT | **Yes** |
| 4 | GraphRAG flag OFF | deleted | **written** (flag-independent) | drain when 03-C decides | 03-C | **Yes (evidence)** |
| 5 | LightRAG down | deleted | written | retained until drained | 03-C retry | Evidence only |
| 6 | Worker down | deleted | written (DB event, no worker) | drain on worker start | 03-C | **Yes (evidence)** |
| 7 | Pending 03-A INDEX → source delete | deleted | written | index no-ops (`skipped_absent`) | reload-current | **Yes** |
| 8 | Source delete → old INDEX executes | deleted | written | index no-ops | reload-current | **Yes** |
| 9 | numeric RecordID | deleted | distinct row | — | lossless key | **Yes** |
| 10 | string-numeric RecordID | deleted | distinct row | — | lossless key | **Yes** |
| 11 | escaped RecordID | deleted | round-trips | — | `record_id_for` | **Yes** |
| 12 | non-source / malformed identity | n/a | **none** (event scoped ON source; field is record<source>) | — | scope | **Yes** |
| 13 | migration forward | — | table+event live | — | startup | **Yes** |
| 14 | migration forward partially fails | — | event body failure rolls back the delete txn (v2) | retry | atomic | **Yes** (atomicity) |
| 15 | down migration | — | table+event removed | baseline delete | rollback | **Yes** |
| 16 | GraphRAG permanently removed | — | down migration clears local metadata | baseline | rollback | **Yes (local)**; remote = 03-D |
| 17 | tombstones exist while GraphRAG disabled | deleted | written | drain when enabled/decided | 03-C | **Yes (evidence)** |
| 18 | source deleted, never GraphRAG-indexed | deleted | written | drain = no-op (delete-of-absent) | 03-C idempotent | **Yes** |
| 19 | source deleted after being indexed | deleted | written | doc removed | 03-C drain | Evidence only |
| 20 | raw DB delete bypassing Python | deleted | **written by event** | drain later | 03-C | **Yes (evidence)** |
| — | delete/recreate reused id (theoretical) | live again | UPSERT re-arms | 03-C re-checks existence → skips if live | canonical re-check | Design-compatible; 03-C |

Rows marked "Evidence only" mean 03-B durably records the intent; actual sidecar removal is 03-C.

## 15. Test properties

`tests/test_graphrag_deletion.py` — 24 tests (8 structural, always run; 16 live-DB, skipped if SurrealDB unreachable). Each asserts a property, not the implementation:

Structural: tombstone schema is minimal/content-free; separate event doesn't touch `source_delete`/`source_embedding`/`source_insight`; UPSERT keyed on source identity; down removes only GraphRAG objects; manager registration (24 up + 24 down); flattened SQL is valid; helper does no HTTP/writes; no 03-C draining command registered; `Source.delete()` has no GraphRAG HTTP.

Live-DB: raw `DELETE source` → tombstone (path #7); `Source.delete()` → tombstone; `repo_delete` → tombstone; no document content on the row; idempotent repeated delete → one row; numeric vs string-numeric distinct; escaped id round-trips; `UPDATE` → no tombstone; reference-edge unlink → no tombstone; non-source delete → no tombstone; existing `source_embedding` cleanup still fires alongside the tombstone; flag-off still writes tombstone; `list_pending_deletions` enumerates it; down migration removes only GraphRAG objects.

Two count guards updated 46 → 48 (`test_graphrag_isolation.py`, `test_graphrag_lifecycle.py`) — the intended, approved schema delta.

## 16. Exact files changed

**New:**
- `open_notebook/database/migrations/24.surrealql`, `24_down.surrealql`
- `open_notebook/integrations/graphrag/deletion.py`
- `tests/test_graphrag_deletion.py`
- `docs/agribank/development/GRAPHRAG_03B_DURABLE_DELETE.md` (this file)

**Edited:**
- `open_notebook/database/async_migrate.py` — register migration 24 up + down
- `tests/test_graphrag_isolation.py` — count guard 46 → 48 (+ assert migration 24 present)
- `tests/test_graphrag_lifecycle.py` — count guard 46 → 48
- `docs/agribank/development/CURRENT_PHASE.md` — slice status

**NOT touched:** `Source.save()`, `save_source`, `vectorize()`, `vector_search()`, `fn::vector_search`, `Source.delete()`, migration 1's `source_delete` event, Ask, Chat, frontend, `commands/*` (no draining command added).

## 17. Deferred GraphRAG-03C work — and the MANDATORY drain precondition

03-C must be able to, using the state this phase creates:
1. **Enumerate** pending tombstones — `deletion.list_pending_deletions()` provides this (returns all pending rows, oldest first). **Bounded/paginated enumeration is NOT part of the 03-B helper**; 03-C may add batching when the draining worker needs it.
2. **Derive** the LightRAG doc identity — `compute_doc_id(tombstone.source_id)`; no lookup needed.
3. **Validate CURRENT canonical state before acting** — the hard precondition below.
4. **Idempotent remote delete** — absent/`not_allowed` = success, `busy` = retry (already in `GraphRAGClient.delete_document`).
5. **Resolve safely / retry / survive worker crash** — 03-C decides whether to flip `status` or delete the row, and adds any retry fields (`attempts`, `last_error`, `next_retry_at`, `resolved_at`) in its own migration if proven necessary.
6. **Drain independent of the enable flag** — deletion must converge even with GraphRAG OFF (row 18).

03-B implements **none** of the above worker logic.

### 17.1 HARD PRECONDITION for GraphRAG-03C — live-source convergence (resolves the source-id-resurrection HIGH)

> **A 03-C drain MUST validate current canonical state before touching the sidecar, and MUST NOT blindly delete, nor silently skip forever, nor resolve a tombstone without repairing derived state.** This precondition is the accepted architectural resolution of the source-id-resurrection HIGH (see §21). 03-C may NOT ship without it.

What a tombstone means: **"the derived state associated with the *deleted generation* of this `source_id` must not survive"** — NOT "the sidecar slot for this `source_id` must be absent forever." Because LightRAG identity is deliberately `source_id`-based (`doc_id = compute_doc_id(source_id)`, one slot per `source_id`), the correct eventual state is one slot reflecting the CURRENT canonical source, or no slot at all — never a distinct slot per historical generation.

For each pending tombstone, 03-C loads the CURRENT canonical `Source` by the losslessly-built RecordID and branches:

```
tombstone(source_id)
  └─ load CURRENT Source from SurrealDB (record_id_for → SELECT * FROM $id)

  ├─ SOURCE ABSENT:
  │     converge-to-absent: idempotent LightRAG DELETE of compute_doc_id(source_id)
  │     resolve tombstone ONLY after CONFIRMED ABSENCE (see 17.1.1)
  │
  └─ SOURCE LIVE (same canonical source_id):
        DO NOT blindly delete the shared doc_id  (would erase the live source's doc)
        DO NOT simply skip forever               (would leave old-generation content)
        │
        ├─ current full_text is EMPTY / whitespace:
        │     converge-to-ABSENT: DELETE compute_doc_id(source_id)
        │     (03-A's `skipped_no_content` does NOT delete — see 17.1.2 — so 03-C
        │      MUST issue the delete itself here; a reindex is wrong for empty text)
        │     resolve tombstone ONLY after CONFIRMED ABSENCE
        │
        └─ current full_text is NON-empty:
              converge-to-CURRENT via 03-A (re)index semantics:
                - reload CURRENT Source
                - delete-then-insert the CURRENT full_text into the shared doc_id slot
                - double `confirm_current` guards stale/deleted egress
              resolve tombstone ONLY after CONFIRMED CURRENT INSERT

  └─ convergence/delete NOT confirmed → tombstone STAYS pending (re-drained later)
```

**Desired invariant, generation-agnostic:**
- Canonical Source **absent** ⇒ eventually **no** derived document.
- Canonical Source **present, empty text** ⇒ eventually **no** derived document.
- Canonical Source **present, non-empty text** ⇒ eventually the derived document equals the **CURRENT** canonical Source.
- Old deleted-generation content is **never** the eventual state.

#### 17.1.1 Resolution criterion — CONFIRMED ABSENCE, never acceptance alone (closes review HIGH-2)

A durable deletion tombstone may be marked resolved **only when the required derived state is proven**, never on an async acknowledgement:

- **`deletion_started` is acceptance, not proof.** LightRAG's delete endpoint returns `deletion_started` immediately while the background delete runs; the 03-A client maps that to `DeleteState.GONE` (`client.py:326`), which is correct for the *reindex-internal* remove-before-insert (a following insert + HTTP-409 handling covers it) but is **NOT** sufficient to resolve a durable tombstone. If 03-C resolved on `deletion_started` and the sidecar then lost the background delete, content would remain with no pending intent.
- **03-C MUST confirm absence** before resolving a delete/empty tombstone — e.g. poll document status / paginated listing until `compute_doc_id(source_id)` is absent, or defer resolution to a RECONCILE pass that proves absence. Until absence is proven, the tombstone **stays pending**.
- For the non-empty convergence branch, resolve only after the CURRENT insert is confirmed (not on the pre-insert delete's `deletion_started`).
- This is a **03-C-specific** resolution protocol; it does **not** change the 03-A reindex-internal delete semantics, which remain correct for their own purpose.

**Fenced (compare-and-set) resolution — closes the ABA re-arm race.** A drain reads a tombstone snapshot, spends time converging, then resolves. Between read and resolve the SAME row can be re-armed: the source is deleted again and migration 24's event UPSERTs the row back to `pending` with a **new `arm_id`**. If 03-C resolved by `source_id/status` alone, it would clear that **newer** delete intent while the sidecar now holds the just-deleted recreated content — a confidentiality failure. Therefore 03-C MUST resolve conditionally on the exact `arm_id` snapshot it processed:

- Capture `arm_id` when the tombstone is dequeued. Resolve (flip `status` or delete the row) **only** `WHERE id = $id AND status = "pending" AND arm_id = <uuid>$observed_arm_id`.
- If **zero rows** are affected, the row was re-armed (new `arm_id`) or already resolved → do **not** treat as done; **re-drive** the drain against current state.
- `arm_id` (not `requested_at`) is the fence, because `time::now()` is not guaranteed unique per re-arm; `arm_id` is a fresh `rand::uuid()` on every arm.
- For live-source branches, also re-check current canonical state immediately before the resolve write (the pre-delete re-confirm below), so a delete-again that lands during convergence is not missed.

**CAS fence token = `arm_id` (review HIGH-4, RESOLVED via Option A).** The compare-and-set is airtight only if every re-arm produces a **distinct** fence value. `requested_at = time::now()` is NOT guaranteed unique/monotonic per re-arm, so it must **not** be the fence token. Migration 24 therefore carries a dedicated `arm_id` (a `rand::uuid()` minted by the event on every arm/re-arm). 03-C captures `arm_id` in the dequeued snapshot and resolves with `WHERE id = $id AND status = "pending" AND arm_id = <uuid>$observed_arm_id`; a re-arm changes `arm_id`, so a stale drain's CAS affects **zero** rows and cannot clear the newer intent. (Option A, approved 2026-08-28; Option B — `requested_at` + RECONCILE-only — was rejected in favour of a standalone-airtight fence.)

**Verified on pinned SurrealDB v2.6.5 (live runtime):** `rand::uuid()` is supported and server-side (no Python/HTTP); three re-arms of one reused id produced three distinct `arm_id`s while the row stayed single; with `requested_at` forced identical across two arms, `arm_id` still changed; a CAS on the stale `arm_id` affected 0 rows and on the current `arm_id` affected 1 row. RECONCILE (03-D) remains defense-in-depth only, **not** the primary correctness mechanism for this race. Neither generation-aware `doc_id` nor repository-wide source-id immutability is required.

#### 17.1.2 Live-empty convergence — 03-C must delete, not reuse 03-A skip (closes review HIGH-1)

03-A's `graphrag_index_source` returns `skipped_no_content` for a live Source whose current `full_text` is empty/whitespace and **deliberately does not delete** the prior sidecar document (`graphrag_commands.py:169-183`; documented 03-A limitation — removing a doc because content became empty is a *deletion* semantic 03-A leaves to 03-B/03-C). Therefore 03-C **cannot** discharge a tombstone by simply invoking 03-A's index path for the live-empty case: doing so would leave the deleted generation's content in the shared slot. 03-C MUST instead treat live-empty as **converge-to-absent** (issue the delete, confirm absence). Only the live-**non-empty** case reuses 03-A's delete-then-insert. `skipped_no_content` (and `superseded`) do **not** count as convergence for tombstone resolution.

**Why this removes the need for generation-aware `doc_id` / repo-wide id-immutability** (verified against the real 03-A code, `lifecycle.py` + `commands/graphrag_commands.py`; corroborated by the independent re-review, which explicitly found neither required): the sidecar slot is `source_id`-keyed by design, so convergence-to-current (or to-absent) makes the eventual state depend only on CURRENT canonical state, never on how many generations shared the id. Delete-then-insert **overwrites** the slot with current text; converge-to-absent empties it. A generation discriminator would only matter to *preserve/distinguish* historical generations — which the confidentiality invariant forbids. 03-C reuses 03-A's `index_source` for the live-non-empty branch and an explicit confirmed delete for the absent/empty branches; **not implemented now.**

**Residual (availability only, never confidentiality):** an irreducible TOCTOU between 03-C's existence check and its destructive action (e.g. a recreate landing exactly between check and delete) can leave a live source momentarily without a doc — closed by RECONCILE (03-D) and by transient-retry, exactly as 03-A's own sub-step residual is. 03-C's DELETE branch SHOULD add its own pre-delete re-confirm to shrink this window (03-A's `confirm_current` only guards the index branch).

### 17.2 Source-id reuse reachability (why the live-source branch is required defensively)

- **Application-supported creation does NOT reuse deleted ids.** `Source()` is constructed with no id (`api/routers/sources.py:497`) → `ObjectModel.save()` (`id is None`) → `repo_create` which `data.pop("id")` → SurrealDB random id (`repository.py:124`). No supported API/domain path supplies a previously-deleted source id.
- **But reuse is reachable in principle.** `ObjectModel.save()` on an id-bearing model routes through `repo_update` (`base.py:162,172`), and `repo_update`/`repo_upsert` materialize a supplied RecordID via `UPDATE $target MERGE` (`repository.py:181-200`). A raw restore / manual DB write / future code path could therefore re-materialize a deleted `source_id`.
- Therefore the live-source convergence branch is **required defensively** even though normal operation never triggers it.

## 18. Known limitations

- **Local intent only.** A tombstone proves a delete is *owed*; it does not by itself remove the sidecar copy. Convergence is 03-C; orphan discovery for pre-tombstone history is 03-D RECONCILE (which does not depend on any local row, since `doc_id` is derivable).
- **Byte-for-byte baseline re-keyed** to "migration 24 not applied" (see §7). A deployment that applies migration 24 but never uses GraphRAG accumulates tiny content-free tombstones on source deletes; the down migration clears them.
- **SurrealDB v3 `ASYNC` caveat** (§6): keep the event synchronous.
- **No test-DB isolation.** Live-DB tests run against the configured SurrealDB (dev), create uniquely-named synthetic records, and clean up; they skip when the DB is unreachable, in which case raw-DELETE coverage is proven structurally only.
- **Live-DB event tests are a MANDATORY 03-B release/CI acceptance gate** (accepted review finding). The 8 structural tests alone are **not** sufficient evidence for raw-`DELETE`/event behavior — a broken event body would still pass them. The 16 live-DB tests in `tests/test_graphrag_deletion.py` (raw-DELETE tombstone creation, idempotency, content-absence, identity distinctness, vector-cleanup coexistence, down-migration) MUST be executed against a live SurrealDB v2 and pass before 03-B is accepted; a CI run that skips them due to an absent DB does **not** satisfy the acceptance criteria.

## 19. Security implications

- **Confidentiality-positive:** converts the raw-SurrealQL delete bypass (forensic path #7) from "invisible" to "durably recorded," closing the gap where a deletion could be lost silently and leave a copy of internal text in the sidecar indefinitely.
- **No new egress / no new surface:** zero network I/O; no new port, endpoint, credential, or provider client. Tombstone stores an opaque record id + timestamp + status — no document text, title, URL, path, notebook metadata, asset, or credentials (asserted by `test_tombstone_carries_no_document_content`).
- **Removable:** down migration returns the delete path to baseline; dropping `open_notebook/integrations/graphrag/` + migration 24 + the flag leaves no orphaned schema.
- **No SSRF/LFI/auth/upload-limit change.**

## 20. Acceptance criteria

| # | Criterion | Status |
|---|---|---|
| 1 | Every delete path (incl. raw `DELETE source`) records durable intent atomically with the canonical delete | ✅ live-DB verified |
| 2 | Tombstone survives flag OFF, worker absent, LightRAG absent | ✅ (DB event, no deps) |
| 3 | Tombstone carries no document content/title/url/path/model-dump | ✅ asserted |
| 4 | Idempotent: repeated delete → one effective pending tombstone | ✅ |
| 5 | numeric vs string-numeric vs escaped ids stay distinct/lossless | ✅ |
| 6 | Non-source / malformed identity cannot create an unsafe tombstone | ✅ |
| 7 | Existing `source_embedding` cleanup unregressed; both events coexist | ✅ |
| 8 | Notebook unlink does NOT create a tombstone | ✅ |
| 9 | No outbound HTTP in this phase | ✅ structural |
| 10 | Migration forward applies; count 46 → 48 exactly as intended | ✅ |
| 11 | Down migration removes only GraphRAG event/table | ✅ live-DB verified |
| 12 | 03-A + 02 regression green; no Ask/Chat/vector-RAG change | ✅ 219 pass |
| 13 | Backend baseline: full suite green except documented pre-existing 5 | ✅ 897 pass / 5 pre-existing |
| 14 | ruff + mypy clean on changed source | ✅ |
| 15 | `arm_id` fence: fresh per re-arm, survives `requested_at` collision, stale-CAS→0/current-CAS→1 | ✅ live-DB verified (v2.6.5) |
| 16 | Karpathy diff + independent Codex review, no unresolved HIGH | ✅ Karpathy CLEAN · Codex APPROVE |

**COMPLETE — all criteria met; signed off 2026-08-28.** Remaining LightRAG remote deletion, draining, retry, RECONCILE, and REBUILD are GraphRAG-03C/03D/03E and are not part of this phase.

## 21. Independent review & resolution of the source-id-resurrection HIGH

Two independent adversarial reviews (a broad pass and a focused database pass) plus a focused security/minimal-state pass converged on a single HIGH: a tombstone keyed only by `source_id` is generation-ambiguous **if a `source_id` is resurrected before 03-C drains**. All other focus areas (raw-DELETE coverage, v2 atomicity, flag-independence, idempotent identity, numeric/string-numeric distinctness, migration forward/back, untouched `source_delete`, content minimization, no-egress, LightRAG-absent, 03-B-independent intent) passed.

**Classification: ACCEPT — RESOLVED ARCHITECTURALLY, implementation mandatory in GraphRAG-03C.** It is not an unresolved **03-B** HIGH, because 03-B only records durable intent and performs no remote destructive action; the hazard exists solely at 03-C action time. The accepted resolution is the **live-source convergence** contract in §17.1: on drain, absent source ⇒ confirmed delete; live same-id source ⇒ converge the shared `doc_id` slot to CURRENT canonical (to-absent if empty, to-current if non-empty); resolve the tombstone only after the required derived state is **proven**. This makes the eventual state generation-agnostic **without** generation-aware `doc_id` or repository-wide id-immutability — verified against the real 03-A code (§17.1) and a 10-scenario race analysis in which confidentiality is preserved in every case and only availability can briefly degrade (closed by RECONCILE/retry).

**Re-review of the convergence contract.** A focused independent re-review asked whether convergence closes the HIGH without generation-aware `doc_id` or repo immutability. It answered: **neither is required** — but it found the first draft of §17.1 under-specified in two ways, both now fixed (docs only, still no generation/immutability, no 03-A code change):
- **HIGH-1 (live-empty):** delegating to 03-A's index path for a live source with empty current text would hit `skipped_no_content` and leave the old content. **Fixed** in §17.1.2 — 03-C must converge live-empty **to absent** (explicit confirmed delete), and `skipped_no_content`/`superseded` do not count as convergence.
- **HIGH-2 (confirmation):** resolving on LightRAG's async `deletion_started` (mapped to `GONE` for 03-A's reindex-internal delete) is not proof of absence. **Fixed** in §17.1.1 — 03-C resolves a tombstone only on **confirmed absence / confirmed current insert**, never on acceptance alone; else it stays pending.

A second re-review round confirmed HIGH-1/HIGH-2 closed, then found one more 03-C concurrency requirement:
- **HIGH-3 (ABA re-arm on resolution):** a drain could resolve a tombstone that was re-armed (deleted-again) mid-convergence, clearing the newer intent and leaving just-deleted content. **Fixed** in §17.1.1 — 03-C resolution is a **compare-and-set on a per-arm fence token** (resolve only on the exact processed snapshot; re-drive if zero rows affected).

A third re-review round raised **HIGH-4:** using `requested_at = time::now()` as the CAS fence is unsafe — SurrealDB does not guarantee it strictly unique per re-arm, so a same-timestamp ABA could clear a newer delete intent. **RESOLVED in 03-B itself (Option A, approved 2026-08-28):** migration 24 adds a dedicated **`arm_id`** field (`rand::uuid()` minted by the event on every arm/re-arm), and 03-C CAS is on `arm_id`, not `requested_at`. **Verified on pinned SurrealDB v2.6.5 (live):** `rand::uuid()` is server-side, distinct per re-arm, and changes even when `requested_at` is forced identical; stale-`arm_id` CAS → 0 rows, current-`arm_id` CAS → 1 row (`tests/test_graphrag_deletion.py::test_arm_id_is_the_fence_not_requested_at`). RECONCILE (03-D) stays defense-in-depth only, not the primary mechanism.

HIGH-1/2/3 are 03-C-contract preconditions (§17.1); **HIGH-4 is closed inside 03-B** by the `arm_id` schema+event. None reintroduces a generation-aware `doc_id` or repo-wide id-immutability; the tombstone stays minimal (`source_id + requested_at + status + arm_id`).

**LOW findings:** (a) `IF NOT EXISTS` could preserve a stale hand-applied event body — **rejected**, consistent with every existing migration's convention and not reachable in the version-tracked run-once flow; no code change. (b) live-DB tests skip when the DB is absent — **accepted**, now a mandatory acceptance gate (§18).
