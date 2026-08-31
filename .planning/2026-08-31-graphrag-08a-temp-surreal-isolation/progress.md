# Progress — GraphRAG-08A Temp Surreal Isolation

## Session 2026-08-31
- Phase 1 forensic: db_connection = fresh AsyncSurreal per call, env-read ns/db, no singleton →
  process-local env override is the narrow isolation mechanism. AsyncMigrationManager reused for
  bootstrap (25 up, idempotent). Indexing in-process (embed_source_command awaited directly;
  service.index_source = direct HTTP) → no worker dependency. Vector storage in SurrealDB
  (source_embedding) → isolated by namespace.
- Phase 2 impl: eval/isolation08.py (temp names, assert_not_normal, context manager w/ bootstrap
  + guarded owned cleanup + env restore+verify + reentrancy guard + 5 error types + Option-A
  require_active_isolation). runner08.create_and_index calls require_active_isolation() (Option-B
  hard block).
- Live experiment (real SurrealDB, temp ns only): bootstrap→v25, isolated write, normal DB
  unchanged (1→1), temp dropped, env restored. RESULT_OK.
- Phase 3 tests: 11 pass (7 unit + 4 live-gated). Phase 4: graphrag regression flag-off 455
  pass/8 skip/0 fail; ruff+mypy clean; migrations 50 unchanged; fixture hash a58a6853…143d
  unchanged; no production import of isolation08.
- Phase 5: independent adversarial review dispatched.
- Docs: GRAPHRAG_08A_TEMP_SURREAL_ISOLATION.md written.
- Boundaries: no provider traffic; sidecar not started; GRAPHRAG_ENABLED false; no .env edit
  (process-local override, restored+verified); no migration; no production code change; no
  fixture edit. No commit/tag/push (review precedes checkpoint).

- Phase 5 review complete: no normal-DB mutation path; 1 HIGH (fail-closed guard self-trip inside
  active context) + 2 LOW all FIXED (captured normal/temp identity guard; finally restores before
  cleanup await; enter guard rejects shared-namespace overlap). Added regression tests. 13 08A
  tests + 46 GR08/08A pass; mypy Success; fixture hash unchanged.
- Phase 6: GRAPHRAG_08A doc + CURRENT_PHASE row updated. Final git audit next. No commit (review
  precedes checkpoint).
