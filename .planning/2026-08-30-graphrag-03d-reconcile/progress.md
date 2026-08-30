# GraphRAG-03D — Progress Log

## Session 1 — 2026-08-30

### Done
- Startup/context recovery: git verified (branch/HEAD/tag/clean). All startup gates pass.
- Created 03D plan dir + set active plan.
- Read governance (AGENTS/AGRIBANK/backend AGENTS) + CURRENT_PHASE.
- Read GraphRAG docs: DECISION (AGR-005), 03A, 03B, 03C.
- Read implementation: client.py, models.py, deletion.py, drain.py, service.py,
  config.py, lifecycle.py, commands/graphrag_commands.py, migrations 24 + 25.
- Wrote task_plan.md + findings.md.

- Read remaining seams (source.py, notebook.py Source.delete, api/main.py lifespan,
  graphrag router, 03C test harness) + lifecycle forensic §12 RECONCILE.
- Dispatched + received LightRAG v1.5.6 inventory forensic (subagent).

### Forensic verdict (GATE)
- v1.5.6 CANNOT prove scalable authoritative absence (no by-id lookup, no corpus
  cursor, offset paging ≤200, GET /documents unbounded/inconsistent, no content_hash).
  => task §9 HARD STOP. 03D adds NO tombstone resolution; orphan detection (positive)
  is safe and is 03D's core value. Details in findings.md.
- LIVE-TEST BLOCKER: this container gates doc endpoints behind LIGHTRAG_API_KEY (403);
  need the key/env var for live-LightRAG tests (§33).

### Next (BLOCKED on user approval — task §37)
- Present forensic + scoped plan; get go-ahead before any source edit.
- Get LightRAG API key for live tests + decide missing-repair scope.

## Session 1 (cont.) — APPROVED + IMPLEMENTED

### User approval (mid-turn)
- Scoped 03D APPROVED with exact constraints; conservative missing-repair APPROVED;
  live-test key from local config (never echo/commit).

### Implemented
- config.py: GraphRAGReconcileConfig + load_reconcile_config (clamped bounds).
- models.py: RemoteDocument + RemoteDocumentsPage.
- client.py: list_documents_detailed (provenance) + list_documents_page refactored to
  project from it (03C absence contract byte-identical).
- deletion.py: pending_deletion_exists + arm_orphan_deletion (DB-generated arm_id, no churn).
- service.py: list_remote_documents_detailed (base_url gate, flag-independent).
- reconcile.py (NEW): classify_ownership + streaming remote sweep + authoritative
  missing snapshot sweep + reconcile() engine + ReconcileSummary.
- commands/graphrag_commands.py: graphrag_reconcile command (repair:bool, max_attempts=1)
  + register in commands/__init__.py.
- tests/test_graphrag_03d_reconcile.py (NEW): 36 tests (mock/structural + live-DB + live-LightRAG).
- Guard updates: test_graphrag_isolation.py (command-set guard), test_graphrag_deletion.py
  (drain+reconcile registered, rebuild not). Migration count UNCHANGED (50) — NO migration 26.

### Live-test auth (secure)
- .env confirmed gitignored. Key extracted from running container config into a scoped
  env var via command substitution; length only (30) ever printed, value never echoed/committed.

### Verification (evidence)
- ruff check . -> All checks passed.
- 03D suite (live env): 35 passed + 1 e2e (after poll widen) = all live tests green.
- GraphRAG regression (02/03A/03B/03C/03D, live env): 323 passed, 1 skipped.
- Full backend (no live env): 968 passed, 6 skipped, 5 FAILED = the documented pre-existing
  baseline EXACTLY (4 Windows podcast-path artifacts + 1 proxy case-merge). No regressions.
  Baseline 936->968 passed (+32 new 03D), 5 pre-existing unchanged.
- mypy: running (changed-files mypy already exited 0).

### Next
- Confirm mypy clean. Write GRAPHRAG_03D_RECONCILE.md + update CURRENT_PHASE.
- Karpathy diff; Codex A/B/C adversarial reviews. Do NOT commit.

## Session 1 (cont.) — REVIEW FINDINGS + FIXES

### Karpathy: CLEAN (0 critical/warning; 2 env nits).
### Codex A (ownership/deletion): NO findings — verified ownership gate, arm idempotency,
  arm_id DB-gen, type::thing key collapse. Corroborates self-audit.
### Codex C (repair/boundaries): BLOCKED by auto-mode classifier. Hand-back recommended
  bypassing via a permission allow-rule / retry — REFUSED (AGRIBANK External Tooling Safety).
  Covered by my own adversarial self-audit + Codex B's overlapping vectors.
### Codex B (inventory/absence/scale): 1 HIGH + 3 MED + 1 LOW.
### My own live test caught a real bug Codex/mocks missed.

**Root cause (my live diagnostic):** `repo_query("SELECT VALUE id FROM source")` returns
STRINGIFIED ids. Binding a STRING as `$last` in `WHERE id > $last` breaks SurrealDB record
comparison (non-strict / no filter); binding a proper RecordID (record_id_for) works
strictly (DIAG_KEYSET: boundary_excluded=True). Same hazard: `SELECT full_text FROM $id`
with a string $id returns nothing → a live source misread as absent. Phase B must convert
each enumerated id to a RecordID before use.

### Fix list (actionable):
1. [Codex B #1 HIGH] stale complete-snapshot race → false missing/redundant reindex:
   REPAIR does a FRESH `confirm_source_document_absent` per candidate before enqueue
   (authoritative at decision time; AUDIT counts from snapshot).
2. [Codex B #2 MED] Phase B `_canonical_state` error → set incomplete_inventory (was silent).
3. [Codex B #3 MED] malformed/dropped remote rows → mark incomplete (client surfaces
   `malformed`; empty doc_id → incomplete).
4. [Codex B #4 MED, real] `_sweep_remote` infinite empty-page loop (has_next=True, no docs)
   → add a page bound; incomplete on exceed.
5. [my live bug] Phase B: convert enumerated ids to RecordID (record_id_for) for both the
   `id > $last` cursor and `SELECT full_text FROM $id`.
6. [Codex B #5 LOW] offset dup counting → documented (idempotent/safe), no code change.

### Fixes applied + re-verified
- client.py: RemoteDocumentsPage.malformed (non-dict rows dropped).
- reconcile.py: _sweep_remote page bound + malformed/empty-doc-id → incomplete;
  _sweep_canonical_missing rebuilds ids via record_id_for (RecordID cursor + FROM $id),
  canonical-state error → incomplete; new _repair_missing does FRESH per-candidate
  confirm before enqueue (FOUND→skip, UNKNOWN→incomplete, ABSENT_CONFIRMED→enqueue).
- models.py: malformed field. Guard test loosened to allow read-only confirm (not delete/resolve).
- Tests: +7 (race-mitigation, canonical-state-error, malformed page, unreadable row,
  empty-page-loop bound, keyset RecordID-cursor live). test file 43 tests.

### Final verification (evidence)
- ruff check . -> All checks passed.
- mypy changed source -> Success (7 files); full mypy exit 0 earlier.
- 03D suite (live env): 42 passed / 1 skipped (flaky live-doc registration).
- GraphRAG regression (live env): 330 passed / 1 skipped.
- Full backend (no live env): 975 passed / 6 skipped / 5 FAILED = pre-existing baseline
  EXACTLY (4 podcast-path + 1 proxy). No regressions.

### Reviews
- Karpathy: CLEAN. Codex A: APPROVE (no findings). Codex B: HIGH+3MED resolved, LOW documented.
- Codex C: BLOCKED by auto-mode classifier; bypass REFUSED (AGRIBANK tooling safety);
  covered by self-audit + B overlap + property tests. No unresolved actionable HIGH.

### Codex C — RE-RUN via supported path (user-directed, no bypass)
- Path: `codex review --uncommitted` (Codex CLI 0.150.1, logged in via ChatGPT), FOREGROUND,
  read-only. NOT the blocked companion script. No permission/tooling/config changes.
- Result: exit 0. 2 findings, NO HIGH:
  - [P2 MEDIUM] client.py has_next coercion + _sweep_remote early-stop: if sidecar returns/omits
    has_next=False while total_pages>1, sweep stops after page 1 WITHOUT incomplete_inventory
    → orphan under-detection + false-complete. Defensive (pinned v1.5.6 sets has_next=page<total_pages,
    so not triggered there). CLASSIFY: ACCEPT. Fix: also continue while page < total_pages (fail-closed).
  - [P3 LOW] reconcile.py _sweep_canonical_missing snapshot-failure except sets incomplete but not
    errors++ → errors under-counts (not false-healthy). CLASSIFY: ACCEPT. Fix: summary.errors += 1.
- Both small, verified against source. Code is FROZEN by user → NOT auto-fixed; awaiting approval
  to lift freeze and apply, then re-verify.

### Consolidated: Karpathy CLEAN · Codex A APPROVE · Codex B resolved · Codex C 1 MED + 1 LOW (accepted).
No unresolved actionable HIGH. Awaiting user decision on the 2 MEDIUM/LOW fixes before final READY.
Do NOT commit. Do NOT start 03E.

## Session 1 (cont.) — Codex C via supported CLI + P2/P3 fixes (user-approved freeze lift)
- Codex C RE-RUN via `codex review --uncommitted` (Codex CLI 0.150.1, read-only, foreground,
  no bypass/permission change). Then 5 focused `codex review` re-reviews on P2/P3.
- P3 (error accounting): fixed — all 3 listing-failure paths (remote sweep, Phase-B snapshot,
  repair snapshot) increment summary.errors.
- P2 (pagination completeness): iteratively hardened to fully fail-closed. Unified
  `_is_complete_snapshot(page)` oracle (not malformed, not has_next, single consistent page
  [total_pages==1 or 0-iff-empty], total_count==readable-ids, all rows have usable ids;
  client strips ids so blanks are unreadable). Gates remote sweep (complete only for a
  single consistent page; multi-page/contradiction => incomplete), Phase-B missing snapshot,
  and the fresh repair snapshot. Missing-repair no longer uses 03C confirm_document_absent.
- Final Codex C re-review: P2/P3 FULLY CLOSED, no new actionable HIGH/MEDIUM.
- Tests: 03D file now 51 tests (added: has_next-conflict, snapshot-failure error accounting,
  _is_complete_snapshot unit, total_count>scanned, repair-snapshot-failure, completeness
  semantics single/under/multi, blank-id-unreadable, race skip/incomplete).

### Final verification
- ruff check . -> clean. mypy . -> exit 0 (only 2 pre-existing arg-type notes in the 03A
  test test_graphrag_command_seam.py, not touched by 03D; changed source clean).
- 03D suite (live env): 50 passed / 1 skip. GraphRAG regression (live): 338 passed / 1 skip.
- Full backend: 983 passed / 6 skip / 5 FAILED = pre-existing baseline EXACTLY. No regressions.
- Karpathy final diff: CLEAN, no scope creep (fixes confined to reconcile.py/client.py + tests).

### Verdict: READY_FOR_GRAPH_RAG_03D_SIGNOFF. Nothing committed. 03E not started.

### Errors Encountered
| Error | Attempt | Resolution |
|-------|---------|------------|
| Live doc-endpoint probe 403 (LIGHTRAG_API_KEY) | 1 | Fell back to public OpenAPI contract (authoritative for schema); key needed for live tests |

### Errors Encountered
| Error | Attempt | Resolution |
|-------|---------|------------|
| (none yet) | | |
