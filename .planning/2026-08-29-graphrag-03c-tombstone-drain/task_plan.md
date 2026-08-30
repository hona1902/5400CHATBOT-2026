# Task Plan — GraphRAG-03C: Tombstone Drain / Retry / Idempotent Remote Delete

## Goal
Implement durable GraphRAG deletion tombstone draining: enumerate pending tombstones,
re-check CURRENT canonical Source, perform idempotent LightRAG remote convergence
(delete-when-absent / converge-when-live / delete-when-empty), confirm remote absence,
and resolve tombstones via arm_id compare-and-set. First phase permitted to perform
remote LightRAG deletion. Crash-safe, re-drivable, bounded, minimal observability.

**Session type:** NEW — no conversational memory. State recovered from repo + Git + docs.
**Branch:** feature/graphrag-lifecycle
**Baseline commit:** 66560d53521f56fa01626b2a4e000d9624091bdd (tag graphrag-03b-approved)
**Highest migration:** 24 (FROZEN). Next available: 25.

## Guardrails (from spec)
- SoT: SurrealDB Source is canonical; LightRAG derived; graphrag_deletion tombstones = durable delete intent.
- Never blindly delete tombstone target — always re-check CURRENT Source first.
- HTTP accept != remote absence. Resolve only on confirmed absence / confirmed convergence.
- arm_id CAS is the ONLY sync fence (not requested_at, not source_id alone, not row id).
- Migration 24 FROZEN — schema changes only via new migration 25 (decision-gated).
- No Boundary-B provider egress in absent-source branch. No content in logs.
- HARD STOP before coding if: global scheduler/daemon needed, migration 25 non-minimal,
  03A identity change, migration 24 change, Boundary-B call, new public API, generation-aware
  doc_id, repo-wide Source-ID immutability.
- NO COMMIT. Stop after implementation + review + sign-off report.

## Phases

### Phase 0 — Context Recovery — Status: complete
- git state verified: branch/HEAD/clean/tag all correct.
- Migration 24 is highest; 25 available.

### Phase 1 — Forensic Analysis (read-only) — Status: complete
Read all docs + full graphrag implementation + migration 24 + enqueue seam. 3 forensic agents
(LightRAG absence contract, drain trigger/scheduler, queue crash + repo/CAS/pagination) complete.
All findings in findings.md. Key: absence via POST /documents/paginated (new client method needed);
NO scheduler exists (drain trigger = §16 gate); CAS idiom already live-tested in 03B; Docker + SurrealDB
both reachable for Phase 4 live gates.

### Phase 2 — Design & Decision Gate — Status: in_progress
APPROVED by user (2026-08-29):
- D1 Drain trigger: APPROVED — GraphRAG-specific cancellable periodic wake-up in FastAPI lifespan
  (worker can't cleanly host); startup kick + periodic pending-tombstone discovery + optional
  best-effort Source.delete wakeup; deterministic shutdown cancel; dup-safe via idempotency+arm_id CAS;
  no generic scheduler, no new daemon; raw DELETE after startup found by periodic tick.
- D2 Migration 25: RE-OPENED by user's fairness challenge. Rigorous analysis: OFFSET over a shrinking
  set skips rows; bounded front-start traversal starves later rows if ≥cap persistent front-failures;
  keyset fixes offset-skip but not cross-tick global fairness. ⇒ migration 25 IS NECESSARY. Minimal =
  ONE field `next_attempt_at: datetime` (defer failing rows out of the DUE set). LIVE-VERIFIED on v2.6.5
  (DUE filter, arm-fenced defer/delete-CAS, re-arm safety, event OVERWRITE, field add+backfill+remove).
  NEEDS USER APPROVAL of migration 25 (§21 decision gate).
- D3 Resolution: APPROVED — DELETE tombstone row via arm_id CAS after CONFIRMED convergence.
- D4 Boundary B: APPROVED — flag OFF → converge slot to ABSENT (no provider egress); flag ON → 03A index.
- D5 Absence confirmation: NOT APPROVED yet. Required LIVE forensic vs pinned LightRAG v1.5.6 runtime
  before any coding (see Phase 2b). Must define FOUND / ABSENT_CONFIRMED / UNKNOWN; only ABSENT_CONFIRMED
  permits CAS resolve; any uncertainty → UNKNOWN → stay pending. If strict absence unprovable → STOP.

### Phase 2b — LIVE absence-probe forensic (BLOCKS coding) — Status: complete → AWAITING D5 APPROVAL
Run pinned ghcr.io/hkuds/lightrag:v1.5.6 (Docker available). Verify LIVE:
A. exact /documents/paginated request+response (openapi.json) — total_count/total_pages/has_next/
   sort_field(id?)/deterministic sort.
B. whether full-page scan can PROVE absence; offset-pagination race (target crossing page boundary).
C. stronger existing mechanisms (exact lookup / status-by-id / track / delete-until-not-found / id filter).
D. LIVE text-ingest via /documents/text (file_source) → paginated lists it (nullable file_path ok?).
E. LIVE delete-of-already-absent behavior on v1.5.6.
F. chosen FOUND/ABSENT_CONFIRMED/UNKNOWN contract + why false-absence cannot occur.
G. fair bounded traversal algorithm w/o migration 25.
Synthetic data only. Nothing committed.

### Phase 3 — Implementation — Status: complete (code + tests written, all local gates green)
Finalized design (all approved):
- models.py: + AbsenceState enum (FOUND/ABSENT_CONFIRMED/UNKNOWN); + DocumentsPage normalized type.
- client.py: + `list_documents_page(page,page_size,sort_field,sort_direction) -> DocumentsPage`
  (POST /documents/paginated; normalize ids/total_count/total_pages); + `confirm_document_absent(doc_id)
  -> AbsenceState` (page_size=200, sort id asc; FOUND / ABSENT_CONFIRMED iff total_pages<=1 ∧
  total_count==len ∧ id absent / else UNKNOWN; any HTTP/parse/timeout error → UNKNOWN).
- service.py: + `confirm_source_document_absent(source_id) -> AbsenceState` (validate id→doc_id→client).
- MIGRATION 25 (APPROVED): 25.surrealql (DEFINE FIELD next_attempt_at + backfill + EVENT OVERWRITE) +
  25_down (EVENT OVERWRITE exact-24 + REMOVE FIELD) + register in AsyncMigrationManager up/down.
- deletion.py: DeletionTombstone += next_attempt_at; + `list_due_deletions(limit)` (SELECT * WHERE
  status='pending' AND next_attempt_at<=time::now() ORDER BY next_attempt_at ASC, id ASC LIMIT $cap);
  + `resolve_tombstone_cas(source_id, arm_id)->bool` (DELETE ... WHERE status='pending' AND
  arm_id=<uuid>$a RETURN BEFORE, len==1); + `defer_tombstone_cas(source_id, arm_id, delay)->bool`
  (UPDATE ... SET next_attempt_at=time::now()+delay WHERE status='pending' AND arm_id=<uuid>$a).
- D5 FINAL: NO GET/documents fallback; NO multi-request offset proof; >200-doc single-page ceiling →
  UNKNOWN (documented explicitly, never silent success).
- drain.py (NEW, integration pkg): DrainOutcome enum; `converge_tombstone(service, tombstone)` state
  machine (load canonical via record_id_for → absent/empty→delete+confirm-absent; live-nonempty→
  flag ON reuse 03A lifecycle.index_source (INDEXED→resolve) / flag OFF→delete+confirm-absent);
  `drain_pending_deletions(service,*,limit,max_rows)` paged, finite per-row, returns DrainSummary;
  `graphrag_drain_wakeup_loop(interval)` + `enqueue_drain_if_pending()` (cheap pending LIMIT 1 +
  no-duplicate-drain guard + submit_command). Never raise per-row.
- commands/graphrag_commands.py: + `graphrag_drain_deletions_command` (worker; calls drain_pending_
  deletions; NO retry that aborts batch — finite per-row; returns summary). Register in commands/__init__.
- api/main.py lifespan: + `_maybe_start_graphrag_drain_wakeup()` (create_task after migrations) +
  deterministic cancel after yield. Lazy import (removable).
- config.py: + OPEN_NOTEBOOK_GRAPHRAG_DRAIN_INTERVAL_SECONDS (def 300, floor 30), batch/max envs.
- domain Source.delete: + best-effort submit_command wake-up (optional, never raises). [verify seam]
- NO migration. Migration 24 frozen.
Sub-steps (TDD, property-oriented §33): (a) client paginated+absence, (b) deletion CAS+paged,
(c) drain state machine, (d) command, (e) wakeup loop, (f) lifespan wiring, (g) Source.delete wakeup.

### Phase 4 — Tests + verification gates — Status: complete
03C 32 pass (incl 2 live-LightRAG); full graphrag 278 pass; full backend 926 pass / 5 pre-existing
baseline / 2 skipped; ruff+mypy clean; migration 25 up/down/up verified live.

### Phase 5 — Docs — Status: complete
GRAPHRAG_03C_TOMBSTONE_DRAIN.md (30 sections) written + CURRENT_PHASE updated (IN PROGRESS, not COMPLETE).

### Phase 6 — Reviews — Status: complete
Karpathy diff: clean (1 nit kept). Codex A (5 passes): 4 HIGH fixed + >200 ceiling accepted (D5).
Codex B (4 passes): 2 HIGH + 4 MEDIUM fixed → final verdict APPROVE. No unresolved actionable HIGH.
Full re-verify after fixes: GraphRAG 288 pass; full backend 936 pass / 2 skipped / 5 pre-existing
baseline; ruff+mypy clean.

## Next Step
✅ **GraphRAG-03C COMPLETE / APPROVED — signed off 2026-08-30.** Karpathy CLEAN · Codex A APPROVE ·
Codex B APPROVE · no unresolved actionable HIGH. All phases complete. Migration 25 added (24 frozen);
42 03C tests + 288 GraphRAG regression + 936 backend (2 skipped / 5 pre-existing baseline); ruff+mypy
clean. Finalizing docs + committing the exact 03C changeset. **GraphRAG-03D NOT started.**

## Decisions Made
| # | Decision | Rationale |
|---|----------|-----------|
| 1 | Forensic-first, plan-gate before any code edit | AGRIBANK.md §2 + spec §39 |

## Errors Encountered
| Error | Attempt | Resolution |
|-------|---------|------------|
| (none yet) | | |
