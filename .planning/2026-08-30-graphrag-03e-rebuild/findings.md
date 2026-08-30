# GraphRAG-03E — Findings (forensic, verified against source @ 94b8885)

## Context recovery (verified)
- branch feature/graphrag-lifecycle; HEAD 94b8885…; tag graphrag-03d-approved @ HEAD;
  backup/feature/graphrag-lifecycle @ HEAD; working tree clean. 03D approved, 03E not started.
- Migrations: 25 up + 25 down = **50 files**. No migration 26. 24/25 present.
- graphrag integration modules: client, config, deletion, drain, lifecycle, models,
  reconcile, service. No rebuild.py yet.

## 03A index command (reuse target) — commands/graphrag_commands.py
- `@command("graphrag_index_source", app="open_notebook", retry=…5 attempts…)`.
- Input `GraphRAGIndexInput{source_id: str}` — **source_id ONLY, no full_text** (structural).
- Output outcomes: indexed · superseded · skipped_disabled · skipped_absent ·
  skipped_no_content · permanent_failure.
- Flag gate first (config.enabled); base_url unset while enabled → raise (retryable).
- Loads CURRENT source by lossless `record_id_for(...)` RecordID; `SELECT * FROM $id`;
  empty rows → skipped_absent; empty/whitespace full_text → skipped_no_content (does
  NOT delete old doc — that's why empty-source cleanup is 03C/03D's job).
- `_still_current()` double-TOCTOU confirm → superseded (sends nothing).

## Reusable primitives (from reconcile.py / service.py / deletion.py)
- **Keyset canonical enumeration** (03D `_sweep_canonical_missing`): `SELECT VALUE id
  FROM source ORDER BY id ASC LIMIT $n` then `WHERE id > $last ORDER BY id ASC LIMIT $n`.
  Cursor MUST be a `RecordID` rebuilt via `record_id_for(str(raw), tables=_INDEXABLE_TABLES)`
  — a bound STRING makes `id > $last` non-strict on SurrealDB v2.6.5 AND
  `SELECT full_text FROM $id` returns nothing (live source misread absent). Live-guarded
  by test_live_canonical_keyset_recordid_cursor_is_strict.
- **`_canonical_state(record_id)`** → "absent"|"empty"|"nonempty" via `SELECT full_text
  FROM $id`; reads full_text ONLY to decide empty/non-empty; never transmits/logs/returns
  it; one row at a time (bounded memory).
- **Enqueue seam**: `submit_command("open_notebook","graphrag_index_source",{"source_id":sid})`
  wrapped in `asyncio.to_thread` (blocking DB client).
- **`service.health()`** → HealthResult, never raises; content-free preflight.
- **`ReconcileSummary`** typed-result pattern: counts + `samples: Dict[str,List[str]]`
  capped by `max_sample_ids`, `add_sample`, log-safe `__str__` (no content). Mirror this.
- **`deletion.arm_orphan_deletion(source_id)`** → bool (True armed / False already pending);
  DB-generated arm_id; anti-churn via `pending_deletion_exists`; collapses to the SAME
  tombstone key the migration-24/25 delete event uses. (03E Decision A: do NOT call this;
  leave empty-source cleanup to 03D REPAIR + 03C.)

## Config pattern — config.py
- Per-lifecycle frozen dataclasses with clamped `load_*` (drain, reconcile). Reconcile:
  remote_page_size 10..200, canonical_batch_size 1..500, max_records 1..50000,
  max_sample_ids 1..100. `_parse_positive` rejects non-finite. → add GraphRAGRebuildConfig
  the same way (OPEN_NOTEBOOK_GRAPHRAG_REBUILD_*).

## surreal_commands durability (forensic)
- Durable `command` row; boot re-scans status='new' only; crash mid-run (status='running',
  no lease/heartbeat) stuck forever; exhausted retry 'failed' never re-driven; router has
  submit/status/list/cancel — no re-drive. No scheduler in project.
- ⇒ 03E uses `max_attempts=1` (like drain/reconcile). Completion honesty: enqueue ≠ done.
  Dedup (`new`-only) is optimization, not a lock. 03A is idempotent/convergent → duplicate
  enqueue harmless.

## Boundary B (hard)
- Boundary A (ON→sidecar) approved synthetic-only. Boundary B (sidecar→LLM/embedding)
  NOT APPROVED for real internal data, every phase. REBUILD re-sends full text → sidecar
  forwards to LLM for extraction → **Boundary-B-scale egress**. CURRENT_PHASE.md:90: 03E
  "requires its own written go-ahead"; synthetic-only until Boundary B approved.
- ⇒ EXECUTE is operator-triggered only; never auto/startup/periodic; tests synthetic-only;
  never run EXECUTE against real internal Sources.

## Completion semantics (acceptance ≠ proof)
- Docs draw sharp line: deletion_started/ack.accepted = queued, not done; tombstone
  resolved only on CONFIRMED absence; 03D "enqueues" repairs, never resolves. No content_hash
  → cannot verify remote content equality at scale (>200 ceiling persists).
- ⇒ 03E terminology: REBUILD_DISPATCH_COMPLETE (enqueued) vs DISPATCH_INCOMPLETE /
  DISPATCH_PARTIAL. Per-source: enqueued, never "reindexed/verified". Never
  "remote content verified".

## CURRENT_PHASE.md
- 03A/03B/03C/03D COMPLETE/APPROVED; migration count 50; 24/25 frozen. 03E ⬜ Not started;
  "requires its own written go-ahead"; 03A–03D correctness does not depend on 03E.

## 03D vs 03E delta (why 03E exists and how it differs)
- 03D `_sweep_canonical_missing` enqueues 03A ONLY for sources AUTHORITATIVELY missing from
  a complete (≤200) remote snapshot; requires the snapshot; skips PRESENT_UNVERIFIED.
- 03E re-drives 03A for ALL current live NON-EMPTY sources regardless of remote presence
  (forces convergence of PRESENT_UNVERIFIED). No remote snapshot needed for dispatch. Empty
  sources → desired ABSENT → reported (Decision A: 03D/03C clean up).
