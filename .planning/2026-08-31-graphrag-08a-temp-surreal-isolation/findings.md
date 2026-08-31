# Findings — GraphRAG-08A Temp Surreal Isolation

## Live isolation experiment (real local SurrealDB, temp namespace only) — PROVEN
Ran isolated_surreal_eval_runtime end-to-end against the running SurrealDB:
- Enter → env SURREAL_NAMESPACE/DATABASE overridden to graphrag_eval_<id>/graphrag_08_<id>.
- Bootstrap ran all 25 migrations into temp ns → schema version 25 (== len up_migrations).
- Wrote synthetic `source:gr08a_probe` into ISOLATED db → isolated source count 1.
- Exit → env restored to open_notebook/open_notebook.
- Normal source count BEFORE=1, AFTER=1 → NORMAL DB UNCHANGED.
- Temp namespace databases after cleanup = [] → temp dropped (absent).
- RESULT_OK True.

## Storage-boundary inventory (§37)
| Boundary | State | Isolation mechanism |
|---|---|---|
| Canonical SurrealDB (source, note, …) | ISOLATED | process-local SURREAL_NAMESPACE/DATABASE override (no singleton; env read per db_connection) |
| Vector storage (source_embedding table, fn::vector_search) | ISOLATED | lives INSIDE SurrealDB → covered by the namespace override |
| LightRAG storage/workspace (sidecar volume) | NOT_USED_IN_08A / future SHARED_BUT_OWNED | per-run unique source ids (gr08e prefix) + per-id cleanup (delete_document_for_source); dedicated workspace = optional future hardening (config-only), not required |
| Job queue / surreal-commands worker | NOT_USED_by_precheck | runner indexes IN-PROCESS: embed_source_command awaited directly (not submit_command); service.index_source = direct HTTP to sidecar |
| Artifact directory (.artifacts/) | ISOLATED_CONTENT_FREE | per-run, content-free |
| Temp Model records | NOT_USED_IN_08A | precheck concern (temp embedding model capture/restore) |

## Test results
- 11 GraphRAG-08A tests PASS (7 unit + 4 live against real SurrealDB): temp-name validity/
  uniqueness, normal-DB guard, Option-A live block, cleanup ownership guards (normal/mismatch/
  invalid/empty), reentrancy refusal, env restore helper, full isolation cycle (normal DB
  untouched), cleanup idempotency, bootstrap-failure env-restore, runner Option-A guard block.
- GraphRAG regression (flag off) 455 pass / 8 skip / 0 fail. Ruff + mypy clean. Migrations
  unchanged (50). Fixture hash unchanged (a58a6853…143d). No production import of isolation08.

## Independent review — outcome (fill on return)
(pending agent aa51… )

## Independent review — outcome + resolution
Fresh-agent adversarial review. Verdict: NO path mutates/drops the normal namespace. One HIGH
FUNCTIONAL defect (fail-closed) + two LOW hardenings — all FIXED:
- **HIGH (fixed):** `require_active_isolation()`'s defense-in-depth clause read `normal_identity()`
  at call time, which during an active context returns the TEMP identity → guard always tripped →
  Option-A live path could never run (fail-closed, no data risk). Fix: capture the true normal
  identity + active temp identity in module state at enter; the guard now asserts active env ==
  captured temp identity AND != captured normal identity. New regression test
  `test_require_active_isolation_passes_inside_context` (the coverage the review said was missing).
- **LOW (fixed):** BaseException (CancelledError/KeyboardInterrupt) during the cleanup await could
  leave env overridden + _ACTIVE stuck. Fix: reordered `finally` — restore env + clear active
  state BEFORE the cleanup await (cleanup uses explicit names, env-independent).
- **LOW (fixed):** enter guard now rejects shared-namespace overlap (namespace==normal_namespace),
  mirroring the cleanup guard. New test `test_assert_not_normal_rejects_shared_namespace`.
Clean dimensions confirmed by review: isolation safety (no normal-DB mutation/drop; identifier
injection impossible), production boundary (no prod import; no API change), storage boundaries
(in-process embed/index verified; worker NOT on precheck path; vector in SurrealDB; LightRAG
honestly SHARED_BUT_OWNED), concurrency (check-and-set has no await between read/write; adequate
for single-loop eval).
Readiness gap E: even with the HIGH fix, full create_and_index needs a temp embedding Model in
the isolated namespace (embed_source_command → model_manager.get_embedding_model()). This is BY
DESIGN out of 08A scope (§32: model seeding belongs to the precheck). Raw isolated Source CREATE
is isolated + normal-DB-safe (proven). ⇒ isolation substrate READY; precheck must seed a temp
Model (via the normal path) inside the isolated runtime before embedding, and handle LightRAG
per-run ownership+cleanup (§32/§48/§33). Post-fix: 13 08A tests pass; 46 GR08+08A pass; mypy
Success; fixture hash unchanged.
