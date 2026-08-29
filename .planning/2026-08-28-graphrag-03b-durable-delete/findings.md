# GraphRAG-03B — Findings (context recovery + source forensic)

**Session start:** 2026-08-28 · **Branch:** `feature/graphrag-lifecycle` · **HEAD:** `22e0b31` (== 03A checkpoint) · working tree clean.
**03A state:** COMPLETE / COMMITTED. Verified `git status` clean, HEAD matches checkpoint `22e0b31949e6468d2302a6d002bffcc00442dace`.

> All content below verified against current checkout, not memory (AGRIBANK §1).

## F1 — Migration system & the REAL next number (corrects the forensic)

- Migrations are **hard-coded** in `AsyncMigrationManager.__init__` (`open_notebook/database/async_migrate.py:98-219`) as two parallel Python lists: `up_migrations` (files `1.surrealql`..`23.surrealql`) and `down_migrations` (`1_down`..`23_down`). **Not auto-discovered** — adding a migration requires editing BOTH lists (backend AGENTS.md confirms).
- On-disk: **23 up + 23 down = 46 `.surrealql` files**. Highest number = **23**.
- **The next migration is `24` (files `24.surrealql` + `24_down.surrealql`).** The forensic's "count moves 46 → 47" conflated *file count* with *version number*: adding one migration adds **two files** (46 → **48**) and bumps `up_migrations` length 23 → 24 / version 23 → 24.
- Version tracking: `_sbl_migrations` table, `bump_version`/`lower_version`. `run_all()` runs `range(current_version, len(up_migrations))`. Migrations run automatically on API startup.
- `AsyncMigration.from_file` strips comment lines (`--`) and blank lines, joins with spaces → the whole file is executed as **one query string**. Down file symmetry is enforced by manager list length.

### Count-guard tests that WILL break by design (must update)
- `tests/test_graphrag_isolation.py:207 test_no_new_migration_added` — asserts `len(glob *.surrealql) == 46` AND `not any("graphrag" in name)`.
- `tests/test_graphrag_lifecycle.py:358 test_no_migration_added` — asserts `len(glob *.surrealql) == 46`.
- After migration 24: count → **48**. Keep **numeric** filename `24.surrealql` (no "graphrag" substring) so the name-based assertion can stay green; update the `== 46` → `== 48`. This is test #25 ("migration count changes exactly as intended"), NOT a 03A regression.
- Other manager-length tests use `>=` (`test_insight_timestamps.py:72` `>= 19`, etc.) and `up == down` — these stay green automatically.

## F2 — ATOMICITY: SurrealDB v2 events are in-transaction (decisive)

- Deployment pins **`surrealdb/surrealdb:v2`** (`docker-compose.yml`); client `surrealdb>=1.0.4`.
- **SurrealDB v2 `DEFINE EVENT` runs synchronously within the same transaction as the triggering statement.** If the event body fails/THROWs, the triggering statement (the `DELETE`) is **rolled back** — event and trigger share one atomic fate (ACID). The `ASYNC` option (event runs in a *separate* transaction, not rolled back with the trigger) exists **only in v3.0.0-beta+** and is not used here.
  - Sources: [Transactions](https://surrealdb.com/docs/surrealql/statements/begin), [DEFINE EVENT](https://surrealdb.com/docs/surrealql/statements/define/event), [Tour p.24](https://surrealdb.com/learn/tour/page-24).
- **Consequence:** a tombstone-writing event gets the *exact* atomicity guarantee the project already relies on for `source_delete` vector cleanup — no stronger claim. Tombstone exists **iff** the canonical source delete committed. Covers **every** delete path incl. raw SurrealQL (path #7), because the event is where all delete paths converge.
- **Honest limitation to document:** (a) guarantee is *local durable intent*, NOT remote sidecar removal (that's 03C HTTP drain); (b) it is *parity with existing vector cleanup*, not a novel stronger guarantee; (c) if a future deploy upgrades to SurrealDB v3 and someone marks the event `ASYNC`, atomicity weakens — version-coupling caveat.

## F3 — Existing delete mechanics

- `source_delete` event lives in **migration `1.surrealql:29-32`**: `DEFINE EVENT ... ON TABLE source WHEN ($after == NONE) THEN { delete source_embedding...; delete source_insight...; }`. **DO NOT modify** — separate GraphRAG event instead (isolation + rollback).
- `Source.delete()` (`domain/notebook.py:642-680`): unlinks file, then **explicit Python** `DELETE source_embedding/source_insight` (belt-and-suspenders with the event), then `super().delete()` → `ObjectModel.delete()` (`base.py:205-210`) → `repo_delete(self.id)`.
- Delete paths (forensic §4): API delete, create/retry rollback, `Notebook.delete(exclusive)`, `ObjectModel.delete`, **raw SurrealQL `DELETE source` (no Python — the bypass hole path #7)**. `Notebook.delete()` default = UNLINK only (source survives → must NOT delete graph doc).
- SurrealDB v2 supports **multiple events on one table** with distinct names; independent bodies (tombstone UPSERT vs embedding DELETE touch different tables, no ordering dependency).

## F4 — Reusable integration helpers (already built in 03A/02)

- `client.compute_doc_id(source_id)` (`client.py:50-69`): `"doc-" + md5(canonical_source_id)`. Locally computable, content-independent. **03C uses this** — so the tombstone need NOT store doc_id.
- `models.record_id_for(value, tables=...)` (`models.py:251`): validates + returns a **losslessly-built `RecordID` object** (numeric `source:123` vs string-numeric `source:⟨123⟩` stay distinct; never re-parses canonical string → no double-escape).
- `models.is_valid_record_id`, `service.validate_source_id` (`service.py:34`), `service.delete_document_for_source` (`service.py:185`), `service.index_source` (`service.py:165`). `_INDEXABLE_TABLES == {"source"}`.

## F5 — 03A interaction (must not regress)

- 03A queued payload = **`source_id` only**; worker reloads CURRENT Source; deleted source → `skipped_absent` (no resurrection). Double `confirm_current` around reindex delete.
- A tombstone does NOT change 03A: a pending INDEX after DELETE still reloads → absent → `skipped_absent`. Independent mechanisms. Verify 03A test suite still passes.

## F6 — Feature-flag reachability (the core decision input)

- DB event **cannot** read `OPEN_NOTEBOOK_GRAPHRAG_ENABLED` (Python env var). SurrealQL has no access to process env.
- **Option B** (tombstone only if source was GraphRAG-indexed) needs a *persistent index-state registry* that **does not exist** — 03A deliberately added none (no `content_hash`, no `indexed_at`). Building it = new table + write-on-every-index = out of 03B scope AND grows state.
- **Option A** (always write a tiny tombstone on any source delete): drain for a never-indexed source is a **no-op** — LightRAG delete-of-absent returns `deletion_started`/`not_found` → `GONE` → idempotent success (verified `client.delete_document`). Storage = one tiny row/deletion; 03C/03D resolve+remove. No content stored.
- **Option-C-with-DB-flag** (gate event on a mirrored DB flag row) is **actively wrong**: it re-creates failure-matrix **row 18** — flag off ⇒ deleted text's tombstone never written ⇒ sidecar copy lingers with no local evidence, violating §0.5 (deletion must not fail open). Deletion correctness must be **independent** of the enable flag.
- ⇒ **Option A is correct and matches approved architecture** (forensic §18.2, row 18, §0.5: "drain path is NOT gated by the indexing flag").

### The ONE invariant refinement to surface (not a conflict, but material)
- AGR-005 §14.1 / invariant §0.3 state "flag off ⇒ byte-for-byte baseline." Under Option A, once **migration 24 is applied**, even a flag-off source delete writes a tombstone row → delete path is no longer byte-for-byte baseline at the DB layer.
- The byte-for-byte guarantee **re-keys from "flag off" to "migration 24 not applied."** This is exactly the tradeoff the phase spec's *GRAPHRAG OFF BEHAVIOR* section pre-endorses ("DB lifecycle correctness is independent from runtime HTTP enablement"). Removability preserved: down migration 24 drops table+event ⇒ delete path returns byte-for-byte to baseline.

## F7 — Tombstone schema decision (minimum durable state)

- Table `graphrag_deletion` SCHEMAFULL. Fields (minimum):
  - `source_id` `TYPE record<source>` — canonical identity, **lossless** (`$before.id`). 03C derives `doc_id = compute_doc_id(str(source_id))`. Dangling record link is fine (SurrealDB permits; not an enforced FK without REFERENCE).
  - `requested_at` `TYPE datetime DEFAULT time::now()`.
  - `status` `TYPE string DEFAULT "pending"` — minimal lifecycle state (phase name = durable deletion *STATE*; 03C flips it). Justified as minimal; row-existence alone could encode pending, but a status column now avoids a 03C schema migration just to add it.
- **DEFERRED to 03C** (do NOT add now): `attempts`/`attempt_count`, `last_error`, `next_retry_at`, `resolved_at`, `doc_id` (derivable — task: don't duplicate).
- **Idempotency / identity:** event UPSERTs a tombstone whose **own record id is deterministically derived from the source identity** (via `type::thing`), so repeated delete intent for the same source → **one** row. Numeric vs string-numeric distinctness preserved because they are distinct source RecordIDs → distinct derived keys AND distinct `source_id` field values → distinct `doc_id` downstream.
- **Delete/recreate/reuse race:** UPSERT-by-source-identity is generation-agnostic — a reused id deleted again correctly re-arms the same-keyed tombstone (same doc_id, idempotent). **No generation/version field needed** (task: don't add if not needed). Open Notebook source id generation semantics still to confirm, but design is correct either way.
- **Confidentiality:** tombstone holds ONLY an opaque `source_id` + timestamp + status. NO full_text/title/url/path/notebook/asset/credentials. Tests assert absence.

## F9 — Recreate-same-RecordID check (MANDATORY gate) — NO BLOCKER, no generation field

Verified against source:
- **Create path = SurrealDB random id, no reuse.** `sources.py:497` builds `Source(...)` with no id → `ObjectModel.save()` (`base.py:162`, `id is None`) → `repo_create` (`repository.py:121`) does `data.pop("id", None)` then `connection.insert("source", data)` → SurrealDB auto-generates a random ~20-char id. Two creates never collide; a deleted id is not handed back.
- **No id-preserving recreation exists.** `repo_upsert` used only for singleton config (`base.py:333` RecordModel, `provider_config.py:437`), never `source`. `repo_insert` used only for `source_embedding` children (`embedding_commands.py:386`). No `UPSERT source` / `UPDATE source:<lit>` / `CREATE source:` anywhere. **No backup/restore/import/export data feature** (grep empty). `ObjectModel.save()` with a set `self.id` only runs on an already-loaded existing record (update), not a resurrection path.
- **Generation/version discriminator is useless here.** `doc_id = md5(source_id)` is identical for an original and a reused-id source ⇒ they map to the **same** LightRAG doc slot; nothing for a generation field to distinguish at the sidecar. The correct defense is **03C canonical-existence re-check at drain time** (forensic §14; 03A precedent): source exists again ⇒ drain skips delete. The identity-keyed tombstone is compatible with that.
- **Decision:** PROCEED. No generation field (task: don't add speculatively). 03C must re-check canonical existence before draining — noted in 03C contract.

## F8 — Open implementation-verification items (resolve during build, not blockers)

1. Exact SurrealQL for a deterministic tombstone id from `$before.id` inside an event (`type::thing("graphrag_deletion", <derived from $before.id>)` + `UPSERT`). Verify against a **live SurrealDB v2** (`make database`) — the id-part-from-record-id and UPSERT-in-event forms need a runtime check.
2. Raw-`DELETE source` integration test: does the test suite reach a live SurrealDB, or are all tests file/mock-based? If live-DB infra permits → real raw-DELETE test; else prove structurally via migration/event definition + ephemeral-DB application test, and **state the limitation** (do NOT claim raw-DELETE covered from a `Source.delete()` unit test alone).
3. `Notebook.delete()` default UNLINK must NOT write a tombstone (source not deleted → event does not fire; verify).
