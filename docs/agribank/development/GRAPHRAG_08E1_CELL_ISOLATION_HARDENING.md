# GraphRAG-08E.1 — Diagnostic Experimental-Cell Isolation Hardening

**Status:** OFFLINE DESIGN + IMPLEMENTATION + TEST COMPLETE — awaiting independent-review
sign-off + checkpoint. **No provider traffic, no live diagnostic, no sidecar start, no DB
mutation, no attempt #6, no DEV/HOLDOUT, no frozen-parameter change.**

This phase fixes the experimental-cell isolation defect discovered at the GraphRAG-08E
bounded-live-diagnostic preflight. It does not run the diagnostic and does not confirm any
root cause.

## 1. Blocked live-diagnostic record (preserved honestly)

```
LIVE_DIAGNOSTIC_AUTHORIZATION_ATTEMPT_1 = BLOCKED_BEFORE_PROVIDER
BLOCKER                                 = EXPERIMENTAL_CELL_ISOLATION_UNDEFINED
FAILURE_PHASE                           = PRE-PROVIDER / PRE-LIVE-EXECUTION
PROVIDER_TRAFFIC                        = NO
SIDECAR_STARTED                         = NO
DB_MUTATION                             = NO
LIVE_DIAGNOSTIC_EXECUTED                = NO
DIAGNOSTIC_EVIDENCE_PRODUCED            = NO
DEV / HOLDOUT / ATTEMPT_6               = 0 / 0 / NOT_RUN
```

The committed 08E `run_sweep` delegated indexing to an injected function and did not itself
define/reset LightRAG state, graph storage, or the LLM response cache between concurrency
levels or repetitions. Because the frozen diagnostic plan re-indexes the **same** synthetic
Source content across levels/reps, a later cell could reuse a prior cell's cached extraction,
biasing failure-rate-vs-concurrency. This blocked authorization produced **zero** diagnostic
evidence and is **not** a failed H1/H2/H3 experiment.

## 2. Pinned LightRAG v1.5.6 storage forensic (verified from source, commit b33c6b0)

- **Workspace scopes every on-disk store by subdirectory.** `working_dir/[workspace/]kv_store_<ns>.json`
  (`kg/json_kv_impl.py:141-151`) — this backs the LLM response cache
  (`kv_store_llm_response_cache.json`); `working_dir/[workspace/]graph_<ns>.graphml`
  (`kg/networkx_impl.py:33`); FAISS/vector stores likewise (`kg/faiss_impl.py:217-219`).
- **In-memory shared storage is keyed by `(namespace, workspace)`** via `get_final_namespace`
  (`kg/shared_storage.py:208`) — distinct workspaces do not share in-process data.
- **The HTTP server's workspace is FIXED at startup** (`--workspace` "Default workspace for
  all storage", `api/config.py:474-477`); insert endpoints (`insert_text`/`insert_texts`/
  `pipeline_index_texts`) accept **no** per-request workspace. So per-cell isolation on a
  single running server is impossible — a fresh server process (unique WORKSPACE) is required
  per cell.

Results:
```
LLM_CACHE_SCOPE     = working_dir/<workspace>/kv_store_llm_response_cache.json  (workspace-scoped)
GRAPH_STORAGE_SCOPE = working_dir/<workspace>/graph_*.graphml + vdb_*.json      (workspace-scoped)
IN_MEMORY_SCOPE     = keyed by (namespace, workspace)                           (workspace-scoped)
SERVER_WORKSPACE    = fixed at startup, not per-request                         (⇒ fresh process/cell)
```

## 3. Selected isolation strategy

**OPTION B + per-cell workspace:** every experimental cell runs against a **fresh LightRAG
sidecar process** configured with a **unique per-cell WORKSPACE**. Fresh process ⇒ no
in-memory cache carryover; unique workspace subdirectory ⇒ no on-disk carryover of the LLM
cache, graph, KV, vector, or doc-status. This is strictly stronger than workspace-only or
disk-reset-only isolation (a running server keeps the cache in memory, so disk reset alone is
insufficient — Option C rejected). Same Source content is preserved; only storage/cache
independence changes (no content perturbation — task §5/§26).

## 4. Implementation (offline, eval-only)

New `open_notebook/integrations/graphrag/eval/cell_isolation08.py`:
- `CellIdentity(run_id, concurrency, repetition)` → `cell_id = <run>_c<L>_r<R>`, unique
  LightRAG-valid `workspace = gr08e_<cell_id>` (sanitised to alnum+underscore, mirroring
  `api/config.py:912`).
- `cell_storage_dir` / `cell_storage_paths` / `cross_cell_storage_isolated` — mirror LightRAG's
  `working_dir/[workspace/]` layout and **prove** two cells share no storage path (esp. the LLM
  cache) → `CROSS_CELL_LLM_CACHE_REUSE_POSSIBLE = NO`.
- `CellRegistry` — defense-in-depth uniqueness guard; a duplicate cell identity fails closed
  **before** any provisioning (task §22).
- `CellProvisioner` protocol + `CellProvision`/`CellDisposal` — injected; the LIVE provisioner
  restarts the sidecar with the cell's WORKSPACE and disposes ONLY that workspace subdir. This
  module starts no sidecar and calls no provider; offline tests inject a mock.
- `diagnostic_cell08(...)` async context manager — ENTER: register unique identity → provision
  fresh state → **validity gate** (isolation + cache-fresh + owned; mismatch/unfresh/unowned →
  `DiagnosticCellIsolationFailure`, no mid-run repair, task §19). EXIT: dispose only the
  cell-owned workspace; unproven ownership → `CellOwnershipError` (fail closed).

`concurrency_diag08.run_sweep` now **requires** cell isolation (task §21): each (level,
repetition) is wrapped in `diagnostic_cell08`; the injected indexer (`index_cell_fn`) is only
ever called **inside** an entered, validated cell and cannot bypass isolation. `authorized_live`
+ active Option-A isolation guards retained; the bounded budget guard retained.

## 5. Tests

- New `tests/test_graphrag_08e1_cell_isolation.py` — cross-cell cache/graph isolation proof,
  repeated-Source independence (S001 across all levels/reps), all-8-cell uniqueness, cleanup,
  failed-cell fail-stop (cleanup runs, second cell never entered), invalid-isolation fail-closed
  (fresh=False / workspace-mismatch / unowned), scientific-validity invariants, workspace
  sanitisation, storage-layout match.
- Updated 08E `run_sweep` tests to the cell-isolated signature.
- 08E + 08E.1 suite green; GraphRAG regression (flag off) **577 pass / 8 skip / 0 fail**; ruff +
  targeted mypy clean; fixture hash unchanged; no production import of eval.

## 6. State flags

```
GRAPH_RAG_08E1_CELL_ISOLATION_HARDENING       = COMPLETE
ISOLATION_STRATEGY_SELECTED                    = OPTION_B_FRESH_SIDECAR_PER_CELL + PER_CELL_WORKSPACE
CELL_ISOLATION_DESIGN_READY                    = YES
CELL_ISOLATION_CONTRACT_DEFINED                = YES
CELL_PROVISIONER_IMPLEMENTED                    = NO   (design/contract only — the live sidecar-restart-per-cell provisioner is a separate offline build/review gate: GraphRAG-08E.2)
GRAPH_RAG_08E_READY_FOR_LIVE_DIAGNOSTIC_REAUTHORIZATION = NO
LIVE_DIAGNOSTIC_BLOCKER                         = LIVE_PER_CELL_PROVISIONER_NOT_IMPLEMENTED
CROSS_CELL_LLM_CACHE_REUSE_POSSIBLE            = NO
CROSS_CELL_GRAPH_STATE_REUSE_POSSIBLE          = NO
REPETITION_INDEPENDENCE                        = PASS
LEVEL_INDEPENDENCE                             = PASS
SAME_SOURCE_CONTENT_PRESERVED                  = YES
CELL_OWNERSHIP_FAIL_CLOSED                     = YES
CELL_CLEANUP_PROVEN                            = YES (offline contract; live disposal by injected provisioner)
EXPERIMENT_PLAN_CHANGED                        = NO  (levels [1,2,4,8], reps 2, 30 submissions, cap 64)
PROVIDER_BUDGET_CHANGED                        = NO
RETRY_POLICY_CHANGED / ALLOWLIST_CHANGED       = NO
FULL_RUN_CONCURRENCY_CHANGED                   = NO
LIGHTRAG_VERSION                               = v1.5.6 (unchanged)
LIVE_DIAGNOSTIC_EXECUTED                       = NO
```

## 7. Future live sequence (designed, not executed)

Embedding is prepared via the existing in-process path (`commands.embedding_commands`) inside
the Option-A isolated Surreal namespace; the varying treatment is Graph-indexing concurrency.
Per cell: the live provisioner starts a fresh sidecar (unique WORKSPACE) → the injected
`index_cell_fn` creates run-owned canonical ids, submits the cell's Sources at the cell's
concurrency, polls terminal status, and characterizes any failure via the frozen 08B path
(one ephemeral raw read, discarded) → the cell's workspace is disposed. Any cell that fails
the validity gate STOPS the sweep (`DiagnosticCellIsolationFailure`); no contaminated cell is
recorded as evidence.

**The live per-cell provisioner is NOT implemented in 08E.1** (design/contract/offline tests
only). Implementing it — a fresh LightRAG sidecar process per cell with a unique WORKSPACE,
plus owned-workspace disposal — is a **separate offline build + review gate (GraphRAG-08E.2)**.
`GRAPH_RAG_08E_READY_FOR_LIVE_DIAGNOSTIC_REAUTHORIZATION = NO`;
`LIVE_DIAGNOSTIC_BLOCKER = LIVE_PER_CELL_PROVISIONER_NOT_IMPLEMENTED`. The independent-review
residuals **LOW-2** (`provision` must be atomic/self-cleaning) and **LOW-3** (the provisioner
must VERIFY actual workspace-directory freshness, not merely trust the `fresh_extraction_state`
flag) are **HARD REQUIREMENTS for 08E.2** and must not be deferred into the live run.
