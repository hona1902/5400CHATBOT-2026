# Task Plan — GraphRAG-08 AUTHORIZED Live Micro-Precheck

## Goal
Run ONE bounded provider-backed micro-precheck to prove EXECUTION CORRECTNESS of the frozen
benchmark end-to-end inside Option-A isolation: fixture → isolated canonical runtime → temp
embedding Model → V/GQ/GD on ≤6 DEV queries over ≤8 Sources → metrics/artifact → cleanup →
full restoration. NOT a value run. HOLDOUT=0.

Outcome: `GRAPH_RAG_08_MICRO_PRECHECK_PASS = YES/NO`, and if YES then
`GRAPH_RAG_08_FULL_EXECUTION_READY = YES` (still does NOT authorize the full run).

## HARD CAPS (task §3)
- ≤8 synthetic Sources; ≤6 DEV queries; HOLDOUT=0. Never exceeded for any reason.

## Absolute boundaries
- No full 75×60 run; no HOLDOUT; no production adapter/query API; no RRF; no ranked candidates.
- No .env edit; process-local GRAPHRAG enable + SURREAL ns/db override, restored after.
- No migration; no fixture edit (hash a58a6853…143d BEFORE==AFTER). No stage/commit/tag/push.
- Provider traffic ONLY for the bounded precheck; no tuning/retries-to-pass.
- Pinned LightRAG v1.5.6 (image present, 4.92GB); OpenRouter text-embedding-3-small(1536)+gpt-4o-mini.

## Static gate (done)
Fixture hash MATCH; tree clean; Option-A + Option-B guards present; 46 tests pass; ruff clean.

## Infra (verified)
- Pinned image ghcr.io/hkuds/lightrag:v1.5.6 present. Sidecar env: LLM gpt-4o-mini, embedding
  text-embedding-3-small, both OpenRouter. ON base_url 127.0.0.1:9621. Sidecar CURRENTLY STOPPED.
- SurrealDB up (8000). 08A isolation proven.

## Plan
### Phase 1 — Static gate — complete
### Phase 2 — Map temp-Model seeding path (agent) — in_progress
### Phase 3 — Build eval-only precheck orchestrator (precheck08.py): enter isolation → seed temp
             embedding Model + set default (isolated DB) → clear singleton caches → start sidecar
             + process-local GRAPHRAG enable → health check → runner08 create_and_index (≤8) →
             run V/GQ/GD (≤6 DEV) → build content-free artifact → CLEANUP (LightRAG per-id delete,
             temp Model delete, drop temp namespace via 08A, restore default, stop sidecar,
             restore GRAPHRAG flag) → exit isolation. try/finally cleanup on PASS or FAIL.
### Phase 4 — Execute the bounded live precheck (state machine PLANNED→ISOLATION_CREATE→BOOTSTRAP
             →READY→MODEL_SEED→SIDECAR→INDEX→QUERY→ANALYZE→CLEANUP→COMPLETE).
### Phase 5 — Post-cleanup: normal DB postcheck, residue checks, fixture hash postcheck, offline
             regression (08/08A/graphrag flag-off), git audit. NO commit.
### Phase 6 — Final report (§64-§75). STOP.

## Cleanup design (mandatory, PASS or FAIL)
1. per-id LightRAG delete (service.delete_document_for_source for each created_id).
2. drop temp Surreal namespace (08A cleanup) → removes temp sources + temp Model atomically.
3. restore prior default embedding model (if any) — captured before seeding.
4. restore process-local env (SURREAL ns/db via 08A finally; GRAPHRAG_ENABLED).
5. docker compose down (stop sidecar). verify SIDECAR_RUNNING=NO.
6. normal DB postcheck (identity + source count unchanged); residue checks.

## Decisions
| # | Decision | Rationale |
|---|----------|-----------|
| 1 | Temp namespace drop handles all Surreal residue (sources+model) atomically | 08A REMOVE NAMESPACE; simplest safe cleanup |
| 2 | LightRAG per-id delete BEFORE namespace drop | doc ownership by canonical source_id (compute_doc_id) |
| 3 | Eval-only precheck08.py orchestrator (§11 allows) | keeps live orchestration out of production |
