# Task Plan — GraphRAG-03B: Durable Deletion State + DB Delete Event

## Goal
Implement ONLY the **durable deletion tombstone** + **SurrealDB delete event** so that whenever a canonical Source deletion commits (via ANY path, including raw SurrealQL), a durable local record proving the derived GraphRAG document must be deleted exists — surviving app/worker/LightRAG absence and the GraphRAG flag being OFF. **No** HTTP draining, retry, reconcile, or rebuild (those are 03C/03D/03E).

Core invariant: **INDEXING MAY FAIL OPEN. DELETION MAY NOT DISAPPEAR SILENTLY.**

## Scope
- Migration **24** (`24.surrealql` + `24_down.surrealql`) + `AsyncMigrationManager` list edits.
- `graphrag_deletion` tombstone table + separate `graphrag_source_delete` DB event (in-transaction, atomic with canonical delete).
- Integration-owned tombstone read/query helpers (for tests/03C), NO worker/HTTP.
- Property-oriented tests + docs.

## Design decisions (verified — see findings.md)
- **Feature flag:** Option A — always write a tiny tombstone via DB event (event can't read the Python flag; Option B needs nonexistent index-state schema; DB-flag gating recreates row-18 confidentiality trap). Refines "byte-for-byte baseline" from *flag-off* → *migration-24-not-applied*. **NEEDS user nod (invariant change + migration = AGRIBANK §5/§6).**
- **DB event:** separate `graphrag_source_delete` event, existing `source_delete` UNTOUCHED (isolation, clean rollback).
- **Atomicity:** SurrealDB v2 events are in-transaction ⇒ tombstone exists iff canonical delete committed; parity with existing vector cleanup. Limitation stated (local intent only; v3-ASYNC caveat).
- **Schema:** `source_id` (record<source>, lossless), `requested_at`, `status="pending"`. doc_id NOT stored (derivable). attempts/last_error/next_retry_at/resolved_at DEFERRED to 03C.
- **Idempotency:** UPSERT keyed on deterministic tombstone id derived from source identity ⇒ one row per source; numeric vs string-numeric stay distinct.

## Next Step
✅ **GraphRAG-03B COMPLETE / APPROVED (signed off 2026-08-28).** All phases done, all gates green, HIGH-4 closed via `arm_id`. Finalizing docs + committing the exact 03B changeset. **03-C NOT started.**

## Phases
### Phase 0 — Context recovery + design + approval — Status: complete
- [x] git/branch/checkpoint verified (03A COMPLETE, tree clean)
- [x] Read 8 required docs + AGENTS/AGRIBANK
- [x] Inspect migrations, `source_delete` event, `Source.delete`, integration helpers
- [x] Verify SurrealDB v2 event atomicity (in-transaction)
- [x] Resolve real next migration number (24, not 47)
- [x] Decide feature-flag (Option A) / event (separate) / schema
- [x] Write findings.md, task_plan.md, progress.md
- [x] Present concise plan + get user go-ahead (Option A + migration 24 approved)

### Phase 1 — Migration 24 (forward + down) — Status: complete
- [x] `24.surrealql`: `graphrag_deletion` table + fields + `graphrag_source_delete` event (UPSERT, in-txn)
- [x] `24_down.surrealql`: REMOVE event + REMOVE table ONLY (source table + existing source_delete untouched)
- [x] Register both in `AsyncMigrationManager` up/down lists
- [x] Verified against LIVE SurrealDB v2: UP applies (flattened multi-stmt); raw DELETE→1 tombstone; fields={id,requested_at,source_id,status} no content; idempotent; UPDATE→0; DOWN removes table+event; existing source_delete untouched. Recreate-same-id check: no blocker, no generation field (F9).

### Phase 2 — Integration helpers (no HTTP) — Status: complete
- [x] `deletion.py`: read-only `DeletionTombstone` + `list_pending_deletions` (no worker, no LightRAG import, no HTTP)

### Phase 3 — Tests (property-oriented) — Status: complete — 24 pass (8 structural + 16 live-DB); ruff+mypy clean; full backend 893 pass / 5 pre-existing baseline fails
- [ ] Update count guards 46 → 48 (`test_no_migration_added`, `test_no_new_migration_added`); keep name assertion
- [ ] New: migration 24 applies; table+event defined
- [ ] Delete creates tombstone: `Source.delete()` path, repository path, raw `DELETE source` (if live-DB infra permits, else structural)
- [ ] Idempotency: repeated delete → one tombstone; duplicate event idempotent
- [ ] Content: no full_text/title/url/path/model-dump; only source_id+requested_at+status
- [ ] Identity: numeric vs string-numeric distinct; escaped round-trip; invalid id can't create unsafe tombstone
- [ ] `Notebook.delete()` default UNLINK → NO tombstone; existing source_embedding cleanup still correct
- [ ] No outbound HTTP; flag-OFF still writes tombstone; canonical delete succeeds w/o worker/LightRAG
- [ ] 03A + 02 regression green

### Phase 4 — Docs — Status: complete
- [x] `docs/agribank/development/GRAPHRAG_03B_DURABLE_DELETE.md` (20 sections)
- [x] Update `CURRENT_PHASE.md`

### Phase 5 — Verification & review — Status: in_progress
- [x] pytest: 03B 24 pass · 03A+02 219 pass · full backend 893 pass / 5 pre-existing baseline
- [x] migration up/down live-verified; ruff clean; mypy clean
- [x] Karpathy diff: CLEAN (nit removed; Option-A arm_id delta all traces)
- [x] Codex (foreground, focused): A(1 HIGH+2 LOW reconciled) · B(APPROVE) · C/D/E convergence(HIGH-1/2/3 → 03C-contract docs) · **F arm_id: APPROVE, no findings**
- [x] HIGH-4 closed in 03-B via `arm_id` (`rand::uuid()`), live-verified on SurrealDB v2.6.5
- [x] Final gates: 03B **28** · 03A/02 **219** · full backend **897/5-baseline** · ruff/mypy clean · migration fwd/back verified
- [x] Verdict: **READY_FOR_GRAPH_RAG_03B_SIGNOFF**. No commit. No 03C.

## Decisions Made
- 2026-08-28: Next migration = **24** (verified; forensic's 47 was a file-count/version confusion).
- 2026-08-28: SurrealDB **v2.6.5** ⇒ events in-transaction ⇒ atomic tombstone; documented as parity not novel.
- 2026-08-28: Feature-flag **Option A**; DB event **separate**; schema minimal.
- 2026-08-28: HIGH-4 fence = **Option A `arm_id` (`rand::uuid()`)**, NOT requested_at, NOT RECONCILE-only; verified live. Generation-aware doc_id / repo immutability confirmed unnecessary.

## Errors Encountered
| Error | Attempt | Resolution |
|-------|---------|------------|
| (none yet) | | |
