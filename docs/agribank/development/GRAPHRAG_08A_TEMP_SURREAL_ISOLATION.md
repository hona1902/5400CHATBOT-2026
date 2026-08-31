# GraphRAG-08A — Dedicated Temporary Surreal Isolation for the Evaluation Harness

**Status:** IMPLEMENTATION-OF-ISOLATION (eval-only). No live micro-precheck, no provider
traffic, no sidecar, no full benchmark, no production adapter.
**Date:** 2026-08-31
**Builds on:** GraphRAG-08 offline harness (tag `graphrag-08-harness-approved`, commit
`356e8ae`). Fixture frozen at `a58a68535c345e18f0263904f818e4e2068a164056408665d8bb9233eceb143d`.

> 08A solves the ONLY blocker left after the offline harness checkpoint: the GraphRAG-08 live
> micro-precheck must create canonical Sources in a **dedicated temporary Surreal namespace/
> database**, never the normal application namespace. 08A implements and proves that isolation
> substrate. It does **not** run the precheck.

---

## 1. Forensic — why a process-local env override is the correct, narrow mechanism

`open_notebook/database/repository.py::db_connection` is an `@asynccontextmanager` that opens a
**fresh** `AsyncSurreal` per call, signs in with root creds, and calls
`db.use(get_database_namespace(), get_database_name())`. There is **no global singleton client
and no connection pooling** (confirmed by the backend rules: "each `repo_*` call opens/closes a
connection"). `get_database_namespace()` / `get_database_name()` read `SURREAL_NAMESPACE` /
`SURREAL_DATABASE` from the environment **at connect time** (default `open_notebook`/
`open_notebook`).

Consequence: overriding those two env vars for the duration of a context **redirects every
`repo_*` call, the migration bootstrap, and the in-process embed/index calls** to the temporary
namespace — with **nothing to rebind** (no cached client). Restoring the env restores normal
binding. Among the §11 options (DI settings / context-local / process-local env / injected
client), the codebase exposes no DI or injected-session seam, so **process-local env override
(Option C)** is the narrowest approach that works without production changes.

**Schema bootstrap** reuses the canonical `AsyncMigrationManager().run_migration_up()` (25
up-migrations, idempotent, env-bound). No migration is added or duplicated (count stays 50).

**In-process indexing (no worker dependency):** the eval runner performs all writes in-process
— `embed_source_command(...)` is **awaited directly** (not `submit_command` to the worker), and
`service.index_source` is a **direct HTTP call** to the sidecar. So the separate
`surreal-commands` worker process is **not on the precheck path**, and the process-local env
override fully covers the precheck's DB writes. (If any future path dispatched to the worker,
the worker — a separate process — would NOT inherit the override; that is called out as a STOP
condition, and it does not apply to the current precheck path.)

---

## 2. Implementation (`open_notebook/integrations/graphrag/eval/isolation08.py`, eval-only)

- **Temp names:** `namespace = graphrag_eval_<run_id>`, `database = graphrag_08_<run_id>`;
  `run_id` = 12 hex chars. Validated against a strict Surreal-safe identifier regex
  (`^[A-Za-z_][A-Za-z0-9_]{0,62}$`), bounded length, content-free (no user/query data).
- **Normal-DB hard guard (§9/§28/§47):** `assert_not_normal` fails closed if the target equals
  the normal `(namespace, database)`; asserted before any write/bootstrap.
- **`isolated_surreal_eval_runtime(run_id=None)`** async context manager:
  - enter: refuse nesting (`_ACTIVE` guard, §41) → compute temp names → `assert_not_normal` →
    capture prior env → override env → bootstrap canonical schema (verify version == 25) → yield
    `IsolationContext`.
  - exit (`finally`): drop the temp namespace (guarded, idempotent) → restore env → **verify
    restoration** (normal identity restored, else `IsolationRestoreError`). A cleanup failure is
    **surfaced, never swallowed** (`IsolationCleanupError`), because future readiness depends on
    trustworthy teardown (§45).
- **Owned cleanup (`cleanup_isolated`, §21/§22):** refuses unless the target matches the
  run-owned identity `graphrag_eval_<run_id>` / `graphrag_08_<run_id>`, is a valid temp
  identifier, and differs from the normal namespace. Then `REMOVE DATABASE IF EXISTS` +
  `REMOVE NAMESPACE IF EXISTS` (idempotent). Never a broad purge; unknown/foreign/malformed
  ownership → no drop.
- **Option-A live block (§28):** `require_active_isolation()` (called at the top of
  `runner08.create_and_index`) raises unless an isolated runtime is active AND the active env
  target differs from the normal identity. The earlier Option-B normal-DB path is thus blocked
  from the authorized live path.
- **Errors:** `IsolationConfigurationError`, `IsolationBootstrapError`, `IsolationOwnershipError`,
  `IsolationCleanupError`, `IsolationRestoreError`. No credentials in messages.
- **Logging:** run_id / temp namespace / temp database / state only — never DB password, token,
  or content.

The temporary namespace/database name is a content-free benchmark identifier and may be logged.

---

## 3. Proven behavior (real local SurrealDB, temporary namespace only)

A live experiment + a gated integration test (`tests/test_graphrag_08a_isolation.py`) exercise
the real mechanism against the running SurrealDB, touching **only** a temporary namespace:

- Bootstrap applies all 25 migrations into the temp namespace → schema version 25.
- A synthetic `source:` write lands in the **isolated** database.
- The **normal namespace is unchanged** (source count before == after).
- On exit the temp namespace is **dropped** (absent), and the env is **restored** to
  `open_notebook`/`open_notebook`.
- Double cleanup is a safe no-op; a bootstrap failure still restores the env and surfaces the
  error; the runner refuses to create Sources without an active isolated runtime.

No provider calls, no LightRAG sidecar, no HOLDOUT, no benchmark Sources in the normal DB.

---

## 4. Storage-boundary inventory (§37)

| Boundary | State | Mechanism |
|---|---|---|
| Canonical SurrealDB | **ISOLATED** | process-local namespace/database override |
| Vector storage (`source_embedding` + `fn::vector_search`) | **ISOLATED** | lives inside SurrealDB → covered by the namespace override |
| LightRAG storage/workspace | NOT_USED_IN_08A → future **SHARED_BUT_OWNED** | per-run unique source ids + per-id cleanup (as GraphRAG-04 proved); a dedicated per-run LightRAG workspace is an OPTIONAL future hardening (config-only), not required for safe isolation |
| Job queue / surreal-commands worker | **NOT_USED** by the precheck | runner indexes in-process (no `submit_command`) |
| Artifact directory `.artifacts/` | ISOLATED_CONTENT_FREE | per-run, content-free |
| Temporary Model records | NOT_USED_IN_08A | precheck concern (temp embedding model capture/restore) |

**Honest limitation:** 08A isolates the **Surreal** boundary (canonical + vector). LightRAG
storage is not namespace-isolated; the future precheck relies on unique per-run ids + per-id
cleanup (SHARED_BUT_OWNED), exactly as GraphRAG-04's live run did. A dedicated LightRAG workspace
per run would strengthen this but is deferred (no LightRAG change in 08A, §33/§34).

---

## 5. Independent review + readiness

An independent adversarial review found **no path that mutates or drops the normal namespace**,
and confirmed the production/storage boundaries. It found one **HIGH functional** defect (the
Option-A guard `require_active_isolation` read `normal_identity()` at call time, which — inside an
active context, with the env already overridden — returns the *temp* identity, so the guard
self-tripped and blocked every legitimate isolated run; fail-closed, no data risk) plus two LOW
hardenings. **All are fixed:** the guard now compares the live env target against the *captured*
normal/temp identities snapshotted at context enter (with a regression test that exercises it
inside an active context); the `finally` restores the env and clears active state *before* the
cleanup await so a cancellation during teardown cannot strand the override; and the enter guard
now rejects a shared-namespace overlap, mirroring the cleanup guard.

08A makes the isolation substrate available so a **future, separately-authorized** micro-precheck
can create ≤8 canonical Sources without touching the normal DB. It does **not** authorize the
precheck, and it keeps `GRAPH_RAG_08_FULL_EXECUTION_READY = NO`.

**Answer to the readiness question (§60):** *Can the later micro-precheck create ≤8 canonical
Sources without touching the normal DB?* — **YES for the isolation guarantee**: bare canonical
Source creation runs against the temp namespace and leaves the normal DB untouched (proven). One
**documented precheck prerequisite** remains, and it is by design out of 08A scope (§32): the
temp namespace is freshly migrated and empty, so before the embed/index steps the precheck must
seed a **temporary embedding Model** into the isolated namespace via the normal supported path
(and restore/delete it afterward, §48), and must handle **LightRAG per-run ownership + cleanup**
(§33). These are precheck steps, not isolation-safety gaps.

---

## 6. Boundaries honored

Eval/test scope only; production imports no eval code; `client.query()`, `vector_search`, Ask,
Chat, frontend, lifecycle, and migrations UNCHANGED; no new production API; no migration (count
50); no `.env` edit (process-local override, restored + verified); no LightRAG start/workspace
change; fixture hash unchanged; no provider traffic; sidecar not started; `GRAPHRAG_ENABLED`
stayed false. No commit/tag/push in this gate (independent review precedes checkpoint).
