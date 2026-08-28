# Progress — GraphRAG-03A INDEX/REINDEX

**2026-08-28** — Implementation complete, pending verification review + sign-off.

## Done
- Branch `feature/graphrag-lifecycle` from `bc5b413`; forensic committed `6cd8333`.
- Added `DeleteState`/`DeleteOutcome`/`GraphRAGConflictError` (models), `compute_doc_id` + `delete_document` + 409 handling (client), `index_source` + `delete_document_for_source` (service).
- New `lifecycle.py` (delete-then-insert orchestration, transient/permanent classification).
- New `commands/graphrag_commands.py` (`graphrag_index_source`, source_id-only payload, reload-current, flag gate, retry config); registered in `commands/__init__.py`.
- Fail-open `_maybe_enqueue_graphrag_index` seam in `graphs/source.py::save_source` (flag-gated first; never raises).
- Tests: `test_graphrag_lifecycle.py` (13) + `test_graphrag_command_seam.py` (9).
- Updated GraphRAG-02 guard tests to the approved 03-A surface (409 conflict type; approved referrer/command sets; source.py excluded from prohibited list, replaced with a minimal-seam guard).
- Docs: `GRAPHRAG_03A_INDEXING.md`; forensic status + §21; `CURRENT_PHASE.md`.

## Verification (recorded)
- GraphRAG suite: **210 passed** (`test_graphrag_*`).
- Full backend: **860 passed, 5 failed** — the 5 are the documented pre-existing baseline failures (4 Windows path artifacts + 1 proxy case-merge), unrelated to GraphRAG (GRAPHRAG_POC.md § Known Baseline Test Failures). Regression check: my seam initially broke 4 title tests via a MagicMock attr access; fixed by moving the flag gate first (flag OFF ⇒ zero side effect) — back to exactly 5.
- ruff: clean on all changed files.
- mypy: **Success, no issues** (9 files incl. `graphs/source.py`).
- Migration count: **46** (unchanged).

## Review round 1
- Karpathy: F1 (validation classification mismatch) fixed; F2 (docstring) doc-fix; F3 (isinstance) fixed. Re-trace clean.
- Codex (2 HIGH + 2 MEDIUM), verified vs source:
  - HIGH-2 `Source.get` masks transient DB error as absent → **fixed** (direct existence query re-raises on DB error). Test added.
  - HIGH-1 async delete/insert race → verified upstream 409 interlock (insert 409 while destructive_busy held) → our 409=TRANSIENT converges; downgraded, documented (no loss).
  - MEDIUM-2 enabled-but-unconfigured terminal skip → **fixed** (gate on config.enabled + base_url raise). Test added.
  - MEDIUM-1 empty-text stale doc → **documented** as deferred deletion semantic (03B/03C).
- Re-verified: 213 GraphRAG pass; 863 backend pass / 5 pre-existing baseline fail; ruff + mypy clean.

## Review round 2 (Codex pass 2)
Codex pass 2 (2 HIGH), verified vs source:
- HIGH-3 escaped record id double-escaped by `ensure_record_id`/`RecordID.parse` (`source:⟨123⟩` → `source:⟨⟨123\⟩⟩`) → live escaped-id source missed and terminally skipped. **Confirmed & fixed:** added `record_id_for()` (lossless RecordID object); command queries + builds Source via it, no re-parse. Test `test_escaped_record_id_is_queried_losslessly`.
- HIGH-4 409/crash "converge without loss" overclaim → **doc correction:** documented as a bounded availability gap (crash/ack-lost/exhausted-retry) recovered by REBUILD/RECONCILE (03-D/03-E) + durable re-drive (03-B/03-C); INDEX is fail-open/rebuildable; not confidentiality. No code change (durable re-drive is out of 03-A scope).
- Re-verified: 214 GraphRAG pass; 864 backend pass / 5 pre-existing baseline fail; ruff + mypy clean.

## Review round 3 (Codex pass 3)
- HIGH-3 verified fixed; HIGH-4 accepted as scoped.
- NEW HIGH — TOCTOU: stale text egress after in-flight delete/redaction. **Mitigated:** `confirm_current` callback re-checks canonical state immediately before egress; command returns `superseded` (no egress) on change/vanish, TRANSIENT on confirm error. Irreducible sub-ms residual + ack-lost-then-deleted deferred to durable DELETE/RECONCILE (03-B/03-D) + query-time validation. Tests added (3).
- Re-verified: 217 GraphRAG pass; 867 backend pass / 5 pre-existing baseline fail; ruff + mypy clean.

## Review round 4 (Codex pass 4)
- HIGH-2/HIGH-3 verified resolved.
- NEW HIGH — superseded-after-delete could erase a newer job's document (confirm ran after delete). **Fixed by ordering:** `confirm_current` now runs BEFORE any destructive action; a superseded job does neither delete nor insert. Regression test `test_superseded_does_not_delete_a_newer_jobs_document`.
- Re-verified: 218 GraphRAG pass; 868 backend pass / 5 pre-existing baseline fail; ruff + mypy clean.

## Review round 5 (Codex pass 5)
- Pass-4 ordering confirmed to fix the erase-race. NEW HIGH — stale egress during the delete round-trip (single pre-delete confirm insufficient). **Fixed:** `confirm_current` checked TWICE (before delete + after delete/before POST). Test `test_second_confirm_after_delete_prevents_stale_egress`.
- Re-verified: 219 GraphRAG pass; 869 backend pass / 5 pre-existing baseline fail; ruff + mypy clean.

## Review round 6 (Codex pass 6) — FINAL
Two HIGH raised, both verified vs pinned source:
- Claimed `deletion_started`→insert erase race: **REFUTED by source.** destructive_busy held synchronously through the whole background delete; insert returns 409 while held → our TRANSIENT→retry; insert only succeeds post-delete. No race. Locked by `test_insert_409_is_transient_not_permanent`; reasoning documented in `client.delete_document`.
- Crash-after-delete/no-re-drive: **real, accepted, out of 03-A scope** (durability gap = 03-B/03-C/03-D). INDEX is fail-open/rebuildable. Documented.
- No other new/unresolved HIGH on any axis.
- Final verify: 219 GraphRAG pass; 869 backend pass / 5 pre-existing baseline fail; ruff + mypy clean.

## Outcome
All actionable Karpathy + Codex findings resolved or verified-non-issues; two residuals explicitly deferred to later slices with tests + docs. Recommendation: **READY_FOR_GRAPH_RAG_03A_SIGNOFF**.

## Sign-off
**GraphRAG-03A APPROVED / COMPLETE — 2026-08-28.** Docs finalized (`GRAPHRAG_03A_INDEXING.md`, `CURRENT_PHASE.md`, forensic §21). Plan closed. Nothing committed automatically. **03-B NOT started** — requires separate go-ahead.
