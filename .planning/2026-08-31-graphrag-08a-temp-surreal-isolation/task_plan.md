# Task Plan — GraphRAG-08A Dedicated Temporary Surreal Isolation

## Goal
Implement Option-A isolation for the GraphRAG-08 eval harness: a dedicated temporary Surreal
namespace/database per run, bootstrapped with the EXISTING canonical schema, with a hard
normal-DB guard, owned cleanup, and runtime restoration — so a FUTURE micro-precheck can create
≤8 canonical Sources without touching the normal DB. **No live precheck, no provider calls, no
sidecar, no full benchmark, no production adapter, no migration, no fixture edit, no commit.**

Outcome flag: `GRAPH_RAG_08A_READY_FOR_MICRO_PRECHECK = YES/NO`. (Still keeps
`GRAPH_RAG_08_FULL_EXECUTION_READY = NO`.)

## Hard boundaries (task §3)
- Eval/test scope only; production must NOT import eval; eval → production only.
- No change to client.query()/vector_search/Ask/Chat/frontend/lifecycle/migrations/provider config.
- No migration 26; migrations unchanged (25 up / 50 files). No fixture edit (hash a58a6853…143d).
- No .env edit; process-local env override, restored in finally. No LightRAG start/workspace change.
- No stage/commit/tag/push.

## Forensic result (Phase 1 — done)
- `open_notebook/database/repository.py::db_connection()` (@asynccontextmanager) opens a FRESH
  `AsyncSurreal` per call, signs in (root creds), `db.use(get_database_namespace(),
  get_database_name())`. **No global singleton client, no pooling** (backend AGENTS.md confirms).
  ns/db from env `SURREAL_NAMESPACE`/`SURREAL_DATABASE` (default open_notebook/open_notebook),
  read at each connection. ⇒ a **process-local env override redirects ALL repo_* + migrations**
  to the isolated namespace; nothing to rebind; restore = reset env.
- `AsyncMigrationManager().run_migration_up()` (async_migrate.py:255) self-contained, env-bound,
  idempotent (25 up-migrations; needs_migration = current<25). `get_current_version()` = schema
  parity indicator. ⇒ bootstrap isolated DB with canonical path, no migration duplication.
- **In-process indexing (no worker dependency for precheck):** runner08 calls
  `embed_source_command(...)` awaited directly (not submit_command) and `service.index_source`
  → `client.index_document` = direct HTTP to sidecar. ⇒ WORKER_ISOLATION not required for the
  precheck path (env override covers the in-process writes).
- **Vector storage lives in SurrealDB** (`source_embedding` table; fn::vector_search) ⇒ isolated
  by the namespace override. **LightRAG storage** = separate sidecar volume; isolated per-run via
  unique source ids (gr08e prefix) + per-id cleanup = SHARED_BUT_OWNED (dedicated workspace = a
  future optional hardening, config-only, not required — do NOT implement now, §33/§34).

## Next Step
Phase 6 — final git audit (read-only) + report. No commit/tag/push (§76). Review complete
(HIGH+2 LOW fixed); CURRENT_PHASE + GRAPHRAG_08A doc updated.

## Phases
### Phase 1 — Forensic (connection/migration/worker/storage) — complete
### Phase 2 — Implement isolation08 + runner08 Option-A guard — complete
### Phase 3 — Tests (unit + gated live-Surreal integration) — complete (11 pass: 7 unit + 4 live)
### Phase 4 — Regression — complete (455 graphrag pass/8 skip/0 fail; ruff+mypy clean; migs 50;
             fixture hash unchanged; no prod import of isolation08)
### Phase 5 — Independent review — complete. No normal-DB mutation path. 1 HIGH (fail-closed
             guard self-trip) + 2 LOW all FIXED + regression tests added. Post-fix 13 08A tests
             + 46 GR08/08A pass; mypy clean; fixture hash unchanged.
### Phase 6 — Docs + final audit + report (NO commit) — in_progress
  - GRAPHRAG_08A doc + CURRENT_PHASE row done; final git audit next.

## Decisions Made
| # | Decision | Rationale |
|---|----------|-----------|
| 1 | Process-local env override (Option C of §11) | Only viable narrow approach: no DI, env read per connection, no singleton |
| 2 | Reuse AsyncMigrationManager for bootstrap | Canonical path; no migration duplication (§14) |
| 3 | Worker isolation NOT required for precheck | Runner does all indexing in-process (§38) |
| 4 | LightRAG storage = SHARED_BUT_OWNED (unique ids+cleanup), workspace isolation deferred | §33/§34 no LightRAG change now |

## Errors Encountered
| Error | Attempt | Resolution |
|-------|---------|------------|
| (none yet) | | |
