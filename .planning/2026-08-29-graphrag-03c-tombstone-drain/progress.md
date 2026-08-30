# Progress Log — GraphRAG-03C

## Session 1 — 2026-08-29
- Phase 0 context recovery: verified branch, HEAD=66560d5, clean tree, tag graphrag-03b-approved.
- Confirmed highest migration = 24.
- Created planning files (task_plan / findings / progress).
- Starting Phase 1 forensic reads.
- Read deletion.py, client.py, service.py, lifecycle.py, models.py, config.py, graphrag_commands.py,
  migration 24 + down, source.py enqueue seam, commands/__init__.py, 03B doc, 03A doc, CURRENT_PHASE,
  LIFECYCLE_FORENSIC (§8 queue crash + drain trigger).
- Agent A (LightRAG absence contract) DONE: no local source; pinned Docker v1.5.6; absence via
  POST /documents/paginated (not in client yet); no live-sidecar test harness (all mocked).
- Agent B (drain trigger) DONE: lifespan startup hook + worker boot-drain exist; NO scheduler;
  crash-safe drain assemblable without new scheduler; wall-clock timer would need approval.
- Docker AVAILABLE (28.5.1) → live LightRAG sign-off gate feasible (needs providers for full processing).
- Waiting on Agent C (queue crash + repo_query/CAS/pagination house-style).
- Presented §16/§39 gate. User APPROVED D1 (periodic wake-up in FastAPI lifespan), D2 (no migration 25,
  subject to fairness), D3 (DELETE-row CAS), D4 (flag-off→converge-to-absent). D5 (absence probe) NOT
  approved without live forensic.
- Phase 2b LIVE forensic DONE against pinned ghcr.io/hkuds/lightrag:v1.5.6 + local synthetic mock provider:
  * proved deletion_started≠absence (delete FAILED w/ broken embedding → doc remained; SUCCEEDED w/ mock
    → absent in 3s). page_size bound [10,200]. GET/documents single-snapshot completeness (N==all).
    sort-by-id deterministic. No by-doc-id probe.
  * Chose FOUND/ABSENT_CONFIRMED/UNKNOWN contract: ABSENT_CONFIRMED only via COMPLETE single-request
    enumeration (total_pages<=1 ∧ total_count==len). Multi-page → UNKNOWN (excludes offset-shift race).
  * Fair traversal w/o migration 25: paged pending, finite per-row handling (no batch abort), DELETE-CAS
    resolve, interval-gated. No starvation/hot loop.
- Sidecar + mock still running for Phase 4 live tests. Working tree clean (only planning scratch untracked).
- Delivered A–G; D5 approved (single-page complete-snapshot; NO GET/documents fallback; NO multi-request
  offset proof; >200 ceiling→UNKNOWN documented).
- Fairness challenge → migration 25 proven NECESSARY. User APPROVED minimal migration 25 (next_attempt_at
  only; NO attempt_count/last_error/resolved_at/cursor). Live-verified all fair-traversal SurrealQL.
- IMPLEMENTATION STARTED:
  * migration runner: from_file strips comments, joins statements → ONE query() in ORDER; runs at API
    startup BEFORE yield (pre-traffic). Safe order: FIELD→backfill→EVENT.
  * WROTE 25.surrealql + 25_down.surrealql + registered in async_migrate up/down.
  * LIVE-VERIFIED 25 up/down/up: field add, BACKFILL materializes pre-25 rows, event stamps
    next_attempt_at on re-arm, arm_id kept; DOWN removes field + restores EXACT 24 event body
    (structurally identical modulo OVERWRITE vs IF NOT EXISTS); up/down/up idempotent. DB in 25-up state.
- IMPLEMENTED ALL 03C code:
  * models: AbsenceState, DocumentsPage.
  * client: list_documents_page + confirm_document_absent (single-page complete-snapshot; UNKNOWN on
    any uncertainty). ABSENCE_PROBE_PAGE_SIZE=200.
  * service: confirm_source_document_absent + _require_client_for_deletion (base_url only → deletion
    flag-INDEPENDENT). delete/confirm switched to deletion client.
  * deletion: DeletionTombstone.next_attempt_at; list_due_deletions; has_due_deletions;
    resolve_tombstone_cas (DELETE arm-fenced); defer_tombstone_cas (UPDATE arm-fenced). Live-verified.
  * config: load_drain_config (clamped interval/batch/max_rows/retry_delay).
  * drain.py: DrainOutcome, DrainSummary, converge_tombstone state machine, _converge_to_absent/current,
    drain_pending_deletions (bounded fair loop, finite per-row), enqueue_drain_if_pending (+dedup guard,
    optimization only), graphrag_drain_wakeup_loop (cancellable).
  * commands: graphrag_drain_deletions_command (max_attempts=1, no batch-abort). Registered.
  * api/main.py lifespan: start after migrations + deterministic cancel after yield.
  * Source.delete: best-effort _maybe_wake_graphrag_deletion_drain (optimization, never raises).
- Import smoke OK; ruff clean; mypy clean (6 files).
- Updated 03B/02 guard tests for 03C surface (migration count 48→50; approved referrers +api/main.py
  +notebook.py; ALLOWED_TOMBSTONE_FIELDS +next_attempt_at; disabled test → indexing; drain-command
  registered; deletion helper no-HTTP). ALL 246 existing GraphRAG tests PASS.
- WROTE tests/test_graphrag_03c_drain.py: absence-probe contract, converge state machine, bounded/fair
  drain loop, wake-up, + live-DB fairness (the 14 properties) + live-LightRAG synthetic.
- VERIFICATION GATES (all green):
  * 03C suite: 32 passed (incl 2 live-LightRAG against real pinned v1.5.6 + mock provider: delete-of-
    absent AND full index→delete→CONFIRMED-absent round trip).
  * full GraphRAG regression (02/03A/03B/03C): 278 passed.
  * live SurrealDB: fairness/CAS/backfill/migration-up-down tests ran & passed.
  * migration 25 up/down/up: verified (test + manual).
  * full backend: 926 passed / 2 skipped (live-LightRAG env) / 5 FAILED = EXACT documented pre-existing
    baseline (4 Windows podcast-path + 1 proxy case-merge). ZERO new regressions (03B was 897/5-same).
  * ruff clean; mypy clean (11 files).
- Docs written (GRAPHRAG_03C_TOMBSTONE_DRAIN.md 30 sections + CURRENT_PHASE updated, NOT marked complete).
- Karpathy diff: clean (1 nit — optional dedup guard, justified/kept). All hunks trace to 03C.
- Codex A (delete/CAS/concurrency) — 2 HIGH + 1 MEDIUM, VERIFIED against source:
  * HIGH-2 (stale-read blind delete of recreated current doc in absent branch) — CONFIRMED real bug I
    missed (03B §17.1.2 required pre-delete re-check). FIXED: _source_became_live_current re-check before
    destructive delete → defer+re-drive if source became live+current. +test.
  * HIGH-1 (resolve live-nonempty on INDEXED acceptance) — CONFIRMED actionable. FIXED: resolve only
    after current doc CONFIRMED PRESENT (bounded poll after INDEXED); failed async insert → stay pending.
    +test.
  * MEDIUM (>200 ceiling keeps tombstone pending) — ACCEPTED/documented (user-approved D5; delete still
    issued; 03D scope). +test asserting UNKNOWN→stay-pending.
  Fixes: drain.py; +4 tests. 35 03C pass (mock+live). ruff+mypy clean. Doc §7/§8 updated.
- Codex A iterated 5 passes (adversarial). FIXED all actionable code bugs:
  * HIGH-2 stale-read blind delete → pre-delete canonical re-check (_source_became_live_current).
  * HIGH-1 resolve-on-INDEXED → resolve only with confirmed current state.
  * redaction-TOCTOU (read-then-CAS not atomic) → resolve_current_tombstone_cas folds
    source_id.full_text=$expected INTO the CAS (single atomic statement, live-verified).
  * stale-running dedup → _drain_command_already_queued checks ONLY status='new' (never 'running';
    a crashed running can't suppress re-drive). +live test.
  * Codex confirmed each fix closes its hole.
- Codex A remaining finding = >200 single-page ceiling (raised 3x). DISPOSITION: user-approved D5
  decision (no GET/documents fallback, >ceiling→UNKNOWN→stay pending, documented; scalable proof = 03D).
  VERIFIED NOT a confidentiality hole: absent branch re-issues idempotent delete EVERY attempt →
  content removal NOT ceiling-limited; only bookkeeping resolution deferred to 03D. Per AGRIBANK §5/§11
  user decision + current source outrank reviewer recommendation. Documented §28. Test covers UNKNOWN→
  delete-driven+stay-pending. NOT an unresolved actionable confidentiality HIGH.
- 37 03C tests pass; ruff+mypy clean.
- Codex B (retry/scheduling/security) 4 passes: FIXED 2 HIGH + 4 MEDIUM:
  * malformed-tombstone batch-abort → wrapped defer in try/except.
  * malformed-monopolization → defer_tombstone_cas fences on arm_id ALONE (no source_id reparse);
    live-verified. Arm-uniqueness reliance documented (corrupt-data residual, low severity).
  * non-finite config (1e309→inf→int crash) → _parse_positive rejects non-finite; max_rows clamped ≤5000.
  * invalid-identity log leak → sanitized message, no value (log-capture test).
  * bulk-delete queue flood → Source.delete wake-up routes through dedup enqueue_drain_if_pending.
  Codex B FINAL VERDICT: APPROVE (no unresolved actionable HIGH).
- FINAL VERIFICATION (post-all-fixes): 03C 42 pass; GraphRAG regression 288 pass; full backend 936 pass
  / 2 skipped (live-LightRAG env) / 5 FAILED = exact pre-existing baseline (podcast-path x4 + proxy x1),
  ZERO new regressions; ruff clean (repo); mypy clean. Migration count 50.
- Working tree: uncommitted (HEAD 66560d5 unchanged, NOTHING committed). New files untracked. Planning
  scratch (findings/progress/task_plan.md) untracked — removable.
- PHASE COMPLETE (implementation + all gates + reviews). Sign-off report delivered. NOT committed;
  03D NOT started. Docker sidecar + mock provider left running for optional re-verification.
