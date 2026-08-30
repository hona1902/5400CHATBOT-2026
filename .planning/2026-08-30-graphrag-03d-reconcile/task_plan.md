# GraphRAG-03D — RECONCILE / DRIFT DETECTION / SAFE REPAIR

**Goal:** Add defense-in-depth reconciliation that compares canonical SurrealDB
`source` records against LightRAG derived documents, classifies drift, and
applies ONLY safe repairs by reusing the existing 03A indexing + 03B/03C durable
deletion lifecycle. RECONCILE, not REBUILD. It must NEVER regress 03A/03B/03C
semantics, never delete a document whose Open-Notebook ownership is unproven, and
never falsely resolve a tombstone from an incomplete/racy inventory.

**Branch:** `feature/graphrag-lifecycle` · **Baseline:** `c830e5a`
(tag `graphrag-03c-approved`) · **LightRAG pinned:** `v1.5.6` ·
**SurrealDB:** `v2.6.5`

**Egress:** synthetic / public / anonymized only. Boundary B NOT approved for
internal data.

## Current status: ✅ APPROVED / COMPLETE — signed off 2026-08-30. Committing (no push). 03E NOT started.
Karpathy CLEAN · Codex A APPROVE · Codex B (1 HIGH + 3 MED + 1 LOW) resolved/documented ·
Codex C blocked by safety classifier (bypass REFUSED; covered by self-audit + tests + B overlap).
Live-only keyset bug (string vs RecordID cursor) caught by 03D's own live test + fixed.
Full backend 975 pass / 6 skip / 5 pre-existing baseline unchanged. ruff+mypy clean.
No unresolved actionable HIGH. >200 absence ceiling remains a documented blocker (not solved).

## Forensic verdict (Phase 1 — GATE)
v1.5.6 CANNOT prove scalable authoritative absence (no by-id lookup, no corpus
cursor, offset paging ≤200, GET /documents unbounded/inconsistent, no content_hash).
=> task §9 HARD STOP triggered. 03D must NOT claim the >200 ceiling is solved and
must NOT resolve tombstones from multi-page absence.

## Next Step
Await the 3 Codex adversarial reviews (A ownership/deletion, B inventory/absence,
C repair/boundaries); verify any findings against source; resolve actionable HIGH.
Then present the final report + READY/NOT_READY verdict. Do NOT commit; do NOT start 03E.

## Status snapshot (2026-08-30)
- Phases 0-4: complete (context, forensic gate, ownership, design, APPROVAL).
- Phase 5 IMPLEMENTATION: complete (reconcile.py + config/models/client/deletion/service
  + graphrag_reconcile command + registration).
- Phase 6 TESTS: complete (36 tests: mock/structural + live SurrealDB + live LightRAG).
- Phase 7 VERIFICATION: complete — ruff clean; mypy exit 0 (source clean; test arg-type
  notes match 03C convention); GraphRAG regression 323 pass/1 skip; full backend 968 pass/
  6 skip/5 pre-existing baseline failures unchanged.
- Phase 8 REVIEWS: Karpathy diff CLEAN (0 critical/warning, 2 env nits); Codex A/B/C RUNNING.
- Phase 9 DOCS: GRAPHRAG_03D_RECONCILE.md written; CURRENT_PHASE.md updated (03-D
  IMPLEMENTED — awaiting sign-off, not COMPLETE).

## Approved-safe 03D scope (given the hard stop)
1. Bounded STREAMING remote sweep (offset-paged): classify each remote doc by the
   STRONG ownership contract; arm/re-arm durable tombstone for owned orphans /
   owned should-be-absent (live-empty, or live-nonempty+flag-OFF). Positive
   detection => no false orphans even if enumeration is incomplete. FOREIGN /
   UNKNOWN_OWNERSHIP / PRESENT_UNVERIFIED => report only.
2. Orphan-arming helper: DB-generated arm_id (rand::uuid), only when no suitable
   pending tombstone exists (no re-arm churn, task §24). Reuses existing schema —
   NO migration 26.
3. 03D does NOT resolve tombstones and does NOT run a 2nd delete engine — it wakes
   03C (enqueue_drain_if_pending) which converges/resolves at the existing ceiling.
4. Missing-doc detection: ceiling-limited to authoritative single-page absence;
   REPAIR+flag-ON => source_id-only 03A enqueue; above ceiling => INCOMPLETE/UNKNOWN,
   never bulk reindex. (Scope choice for user: include conservatively vs report-only.)
5. AUDIT default; REPAIR opt-in. Trigger = `graphrag_reconcile` surreal-commands
   command (no scheduler, no new API). Typed ReconcileSummary (counts + capped
   samples, no content).
6. Documented limitation: >200 absence-resolution ceiling NOT solved; needs upstream
   LightRAG by-id/keyset capability. NO false resolution introduced.

---

## Phases

### Phase 0 — Startup / context recovery — Status: complete
- [x] git status/branch/log/tag verified (HEAD=c830e5a, tag present, clean)
- [x] Read AGENTS.md / AGRIBANK.md / backend AGENTS.md
- [x] Read GRAPHRAG_DECISION, 03A, 03B, 03C docs + CURRENT_PHASE
- [x] Read implementation: client/models/deletion/drain/service/config/lifecycle/commands + migrations 24/25
- [x] Read remaining: lifecycle forensic §12 RECONCILE, source.py + notebook.py seams, api/main.py lifespan, graphrag router, 03C test harness
- [ ] (POC doc §2/§4 optional — ownership contract confirmed from code + forensic)

### Phase 1 — FORENSIC GATE: LightRAG v1.5.6 inventory API — Status: complete
- [x] Enumerated endpoints (OpenAPI contract): paginated/GET documents/status_counts/
      track_status/source_conflicts/query/delete — see findings.md
- [x] Decided: absence CANNOT be authoritatively proven above the ceiling (NO by-id,
      NO corpus cursor, offset-only ≤200) — task §9 HARD STOP triggered
- [x] Documented as blocker; UNKNOWN->pending preserved; 03D adds NO tombstone resolution
- [~] Live empirical probe BLOCKED by LIGHTRAG_API_KEY 403 gate; contract is authoritative;
      live evidence deferred to test phase once key is provided

### Phase 2 — Ownership contract forensic — Status: complete (from contract + 03C live)
- [x] Strong ownership contract confirmed: DocStatusResponse carries id + file_path(=source_id);
      03C already live-verified doc.id == compute_doc_id(source_id) & file_path==source_id.
      Owned iff is_valid_record_id(file_path,{source}) AND compute_doc_id(file_path)==doc.id.
- [x] FOREIGN / UNKNOWN_OWNERSHIP => report only, never destructive. (Re-verify live in tests.)

### Phase 3 — Design (plan artifact) — Status: pending
- [ ] Inventory completeness semantics (bounded, memory-capped, INCOMPLETE not false-healthy)
- [ ] Canonical-side batching (fields = id + empty/non-empty/indexable signal only)
- [ ] Classification matrix (typed outcomes)
- [ ] Audit (default) vs Repair mode
- [ ] Orphan repair -> arm/re-arm durable tombstone (reuse 03B/03C; no 2nd delete engine; no needless re-arm churn)
- [ ] Missing repair -> reuse 03A (source_id-only enqueue; flag-gated)
- [ ] Existing-tombstone resolution via arm_id CAS ONLY on authoritative absence
- [ ] Migration-26 decision (prefer NO migration)
- [ ] Trigger = operator/internal command (no new scheduler)
- [ ] Exact files to change

### Phase 4 — APPROVAL GATE (task §37) — Status: pending
- [ ] Present forensic conclusions + plan; STOP for user go-ahead before coding

### Phase 5 — Implementation — Status: pending (BLOCKED on Phase 4)
### Phase 6 — Tests (property-oriented + live LightRAG + live SurrealDB) — Status: pending
### Phase 7 — Verification (03D + regression + ruff + mypy) — Status: pending
### Phase 8 — Reviews (Karpathy diff, Codex A/B/C) — Status: pending
### Phase 9 — Docs (GRAPHRAG_03D_RECONCILE.md + CURRENT_PHASE) + final report — Status: pending

---

## Hard constraints (DO NOT violate)
- No REBUILD / no bulk reindex-every-source.
- No modification of migrations 24 or 25. Prefer NO migration 26.
- No new scheduler/daemon/cron/APScheduler/Celery/FastAPI periodic loop.
- No second independent remote delete/poll/resolve engine — reuse 03C.
- No delete/tombstone of FOREIGN/UNKNOWN documents.
- No false ABSENT_CONFIRMED; UNKNOWN must never be downgraded to ABSENT.
- No canonical Source text egress for flag-OFF / delete repairs (Boundary A only).
- No content or credentials in results/logs; capped sample ids only.
- Bounded memory + bounded inventory; INCOMPLETE, never false-healthy.
- Do not commit / do not push / do not start 03E.

## Decisions Made (2026-08-30, user-approved)
- **APPROVED scoped 03D** (orphan detection + durable-intent arming reusing 03B/03C;
  FOREIGN/UNKNOWN report-only; NO tombstone resolution by 03D; NO migration; NO scheduler).
- **Missing-repair = CONSERVATIVE**: only on authoritative single-page absence (≤200 corpus)
  AND REPAIR mode AND flag ON → source_id-only 03A enqueue; above ceiling → UNKNOWN/INCOMPLETE.
- **Live-test key**: read from compose/.env at test time via OPEN_NOTEBOOK_GRAPHRAG_API_KEY
  (compose maps shell GRAPHRAG_POC_API_KEY → container LIGHTRAG_API_KEY). Never commit/echo.
  Live-LightRAG tests skip if env unset (existing convention) — provide run command to user.
- **>200 absence ceiling NOT solved** — documented blocker (needs upstream by-id/keyset).

## Design (approved-safe, to implement)
- New `reconcile.py`: `owned_source_id_for(doc_id, file_path)` (STRONG contract: file_path
  lossless `source` RecordID AND compute_doc_id==doc_id); `_canonical_state(record_id)`→
  absent/empty/nonempty (mirror drain `.strip()`); `reconcile(service, repair, cfg)` engine:
  Phase A remote sweep (streaming, bounded) classify+ (repair) arm; Phase B canonical missing
  detection (flag-ON only, ceiling-limited, repair→03A enqueue). Typed `ReconcileSummary`.
- `deletion.py`: `arm_orphan_deletion(source_id)` UPSERT type::thing(DELETION_TABLE,$sid) with
  DB-generated arm_id=rand::uuid(); only when no pending row exists (no churn). Reuses schema —
  NO migration. `pending_deletion_exists(source_id)`.
- `client.py`: `RemoteDocument`(doc_id,file_path,status) + `RemoteDocumentsPage`;
  `list_documents_detailed(...)`; refactor `list_documents_page` to project from it (03C
  absence contract UNCHANGED).
- `service.py`: `list_remote_documents_detailed(page,page_size)` via `_require_client_for_deletion`
  (base_url gate, flag-independent).
- `config.py`: `GraphRAGReconcileConfig` + `load_reconcile_config` (clamped: page_size 10..200,
  canonical_batch, max_records, max_sample_ids).
- `commands/graphrag_commands.py`: `graphrag_reconcile` (input repair:bool; max_attempts=1) +
  register in `commands/__init__.py`.
- Trigger: operator submits the command (worker runs it). base_url unset → INCOMPLETE outcome,
  never "no drift".
- Guard tests: add graphrag_reconcile to command-set guards; migration count stays 50.
