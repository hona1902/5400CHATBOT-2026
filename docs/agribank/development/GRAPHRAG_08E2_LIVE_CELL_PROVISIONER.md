# GraphRAG-08E.2 — Live Diagnostic-Cell Provisioner

**Status:** OFFLINE IMPLEMENTATION + TEST + INDEPENDENT REVIEW — **execution plumbing only.**
**No provider traffic, no live diagnostic, no sidecar start in the suite, no DB mutation, no
attempt #6, no DEV/HOLDOUT, no frozen-parameter change.**

```
LIVE_DIAGNOSTIC_EXECUTED       = NO
PROVIDER_TRAFFIC               = NO
FULL_RUN_ATTEMPT_6_EXECUTED    = NO
DEV_EXECUTED / HOLDOUT_EXECUTED = 0 / 0
NORMAL_DB_MUTATION             = NO
```

This phase implements the missing per-cell LightRAG **provisioner** that GraphRAG-08E.1
declared as its live blocker (`CELL_PROVISIONER_IMPLEMENTED = NO`,
`LIVE_DIAGNOSTIC_BLOCKER = LIVE_PER_CELL_PROVISIONER_NOT_IMPLEMENTED`). It does **not** run
the bounded live concurrency diagnostic and does **not** confirm any root cause. Success here
does **not** authorize a live run — that remains a separate operator prompt.

## 1. What was implemented

New eval-only module `open_notebook/integrations/graphrag/eval/cell_provisioner08.py` that
realizes the frozen 08E.1 cell-isolation contract (`cell_isolation08.CellProvisioner`:
`provision`/`dispose`) with a real process + storage **lifecycle**, plus a richer
`ProvisionedCell` handle via the `provision_diagnostic_cell(...)` async context manager.

Strategy (frozen at 08E.1, not reopened): **OPTION B** — one FRESH pinned LightRAG process
per cell + one UNIQUE per-cell `WORKSPACE` + one run-owned storage boundary. Fresh process ⇒
no in-memory cache carryover; unique workspace subdirectory ⇒ no on-disk carryover of the LLM
response cache, graph, KV, vector, or doc-status state.

### Storage model (matches the 08E.1 contract exactly)

- **Run-owned working dir** = `<eval_root>/<run_id>/` — the LightRAG working dir shared by
  the run's cells; created once, owned by `run_id`. **Never deleted** by cell teardown.
- **Cell-owned workspace** = `<working_dir>/<workspace>/` (`cell_storage_dir(working_dir,
  workspace)`) — the **only** path the provisioner creates or destroys for a cell. This is
  the disjoint per-cell boundary proven by `cell_isolation08.cross_cell_storage_isolated`.

The live Docker realization (never exercised offline) mounts `working_dir` at
`/app/data/rag_storage` and sets `-e WORKSPACE=<workspace>` per fresh container, with a unique
loopback port per cell. A fresh container per cell = a fresh process per cell; `docker rm -f`
removes the container's whole in-container process tree.

### Atomic provision state machine (task §27)

`PLANNED → STORAGE_RESERVED → PROCESS_STARTING → PROCESS_STARTED → HEALTH_VERIFIED →
VERSION_VERIFIED → WORKSPACE_VERIFIED → PROVISIONED`, and for teardown `TEARDOWN_STARTED →
PROCESS_STOPPED → STORAGE_DISPOSED → CLEANED`. Failure states: `FRESHNESS_FAILED`,
`START_FAILED`, `HEALTH_FAILED`, `VERSION_FAILED`, `WORKSPACE_VERIFY_FAILED`,
`OWNERSHIP_FAILED`, `CLEANUP_FAILED`.

A cell handle is returned to future diagnostic code **only after** fresh storage verified,
process running, health verified, version pinned, and workspace bound (task §28). A bare
HTTP-200 `/health` is explicitly **not** enough (task §29).

## 2. The two 08E.1 review HARD requirements, discharged

- **LOW-2 (atomic / self-cleaning).** `provision` is atomic: any failure **after** the
  storage reservation triggers `_atomic_rollback`, which stops the process tree and disposes
  the reserved workspace, positively verifies both, and re-raises. If teardown cannot be
  verified it raises `CellCleanupError` — a `PROVISIONED` result is **never** reported over
  uncertain residue. This matters because a raise inside `diagnostic_cell08.__aenter__` means
  the outer `__aexit__` never runs, so this layer must clean up after itself.
- **LOW-3 (physical freshness).** `fresh_extraction_state` is set only after
  `scan_workspace_freshness` **physically** inspects the cell workspace on disk. A
  pre-existing workspace fails **closed** (never silently deleted/reused). The directory-level
  emptiness guarantee is primary; the `_LIGHTRAG_STORE_FILES` scan is defense-in-depth —
  correctness does not depend on that list being complete, because any entry already makes a
  pre-existing directory non-empty ⇒ non-fresh.

## 3. Safety properties (all fail-closed)

| Property | Mechanism |
|---|---|
| **Path safety** (§24) | `assert_cell_paths_safe` rejects non-content-free `run_id`/`workspace` segments (`..`, separators, drive, UNC) and proves the cell path sits directly under the run root under the eval root. |
| **Symlink/junction** (§25) | `is_within_root` resolves realpath + case-normalises; `_dispose_storage` refuses a symlink target and refuses nested reparse points that escape the owned root. |
| **Ownership** (§23) | `dispose` proves `provision.workspace`/`storage_dir` match the cell's own identity + owned path before any destructive action; otherwise `owned=False`, no deletion. |
| **Process ownership** (§13) | Only the exact owned container/pid is targeted. `ProcessTreeController` does Windows `taskkill /T` then `/F /T` (tree kill; a bare parent kill orphans children), POSIX `killpg` then `SIGKILL`. |
| **Port ownership** (§14/§48) | An intended port already occupied by an unrelated process fails **closed** — a foreign process is **never** killed for a port; after teardown the port is verified released. |
| **Cleanup verification** (§21) | Teardown is `ok` only on positive verification: process not alive, port released, storage absent. |
| **Idempotent cleanup** (§22/§50) | A second dispose of an already-cleaned cell is a safe no-op, never a broader delete. |
| **Content-safety** (§63) | Handles/logs/exceptions carry only ids, level, repetition, workspace, port, state, timings, version — never a credential, provider secret, Source/query text, or raw provider error. The Docker API key is read from env at start and never stored on the handle/spec/log. |

## 4. Integration with the diagnostic harness (offline only)

`concurrency_diag08.run_sweep` gained a minimal fail-closed guard: it now requires a live cell
provisioner implementing `provision`/`dispose` **before** the loop, so an `authorized_live=True`
call with no provisioner fails closed and can never reach a bare injected indexer (task §35/§58).
The injected indexer still runs **only inside** an entered, `cell_isolation08`-validated cell.
Provisioning readiness never implies live authorization: `authorized_live` must still be
explicitly `True`, and active Option-A isolation is still required for the normal-DB path
(task §36/§59). `run_sweep` is **not** executed live in this phase.

The dependency direction is preserved: `cell_isolation08` defines/validates the contract →
`cell_provisioner08` realizes it with process/storage lifecycle → `concurrency_diag08` is the
future consumer.

## 5. What this phase did NOT do

No Source creation, embedding, GraphRAG indexing, or provider call. No change to the retry
policy, allowlist, classifier, retry timing, diagnostic plan, budget caps, source contents,
full-run concurrency, provider/model, or pinned LightRAG version. No production code change, no
migration, no `.env` edit, no fixture change
(`a58a68535c345e18f0263904f818e4e2068a164056408665d8bb9233eceb143d` unchanged). No tracked
runtime artifact. Production imports no eval code.

## 5a. Independent review history (preserved honestly)

An independent adversarial review ran against the implementation. Outcome: **PASS — 0 HIGH,
0 unresolved MEDIUM.** Findings and their disposition:

- **M1 (MEDIUM) — FIXED before checkpoint.** Atomic rollback was initially limited to
  `CellProvisionError` paths, so a non-`CellProvisionError` raised after storage reservation
  (a raising injected prober/allocator, an `OSError` from `makedirs`, or an
  `asyncio.CancelledError`) could strand a started process + reserved workspace. Fixed by
  broadening the rollback to `except BaseException`, wrapping the health probe (a mid-startup
  probe error is treated as not-ready), and normalizing a `makedirs` TOCTOU to
  `CellFreshnessError`. Pinned by new tests: a raising prober and a raising
  non-`CellProvisionError` allocator both still self-clean (workspace absent, process torn
  down, cell absent from `_active`).
- **L1 — FIXED.** The startup wait now honors the configured timeout (small positive floor
  only for a non-positive value), always probes at least once, and reports the actual timeout.
- **L3 — FIXED + TESTED.** `_teardown` is now exception-safe: an injected `terminate`/delete
  that raises becomes a verifiable cleanup **failure** (`disposed=False`), never a raw escape.
- **L4 — documented residual LOW.** `ProcessTreeController.terminate_tree` targets a bare pid
  (pid-reuse window). It is a standalone utility, is **not** wired into any controller or any
  recovery path, and the live path is `DockerCellProcessController` (targets the exact owned
  container name — no pid reuse). Any future delayed/recovery cleanup must revalidate ownership
  or fail closed.
- **L2 — intentional fail-closed.** With `require_runtime_workspace=True` (default), the
  default-composed live prober cannot confirm the bound workspace from v1.5.6 `/health`, so the
  live path **blocks readiness** until a workspace-reporting prober is supplied. This is the
  correct §16 behavior for an unauthorized future live path, not a bug.

## 6. Real-local-sidecar provision test

`REAL_LOCAL_SIDECAR_PROVISION_TEST = SKIPPED_SAFETY`. The offline proof is strong (source-level
+ filesystem + fake-process/health lifecycle coverage, including a real local child-process
**tree** kill for §49). A real pinned LightRAG v1.5.6 container was **not** started: this phase
is offline and the safety preconditions in task §32/§75 (guaranteed zero indexing/embedding/
LLM/provider egress with monitored traffic and no credential exposure) were not established, so
per the rule the sidecar was not started. `SKIPPED_SAFETY` is not a failure.

## 7. Readiness

```
CELL_PROVISIONER_IMPLEMENTED                           = YES
ATOMIC_PROVISIONING                                    = PASS
PHYSICAL_WORKSPACE_FRESHNESS                           = PASS
FRESH_PROCESS_PER_CELL                                 = PASS
UNIQUE_WORKSPACE_PER_CELL                              = PASS
PROCESS_OWNERSHIP / PORT_OWNERSHIP                     = PASS / PASS
WORKSPACE_RUNTIME_VERIFICATION / STORAGE_ROOT_VERIF.   = PASS / PASS
OWNED_CLEANUP / CLEANUP_VERIFICATION / CLEANUP_IDEMPOTENT = PASS / PASS / PASS
PATH_SAFETY / SYMLINK_JUNCTION_SAFETY                  = PASS / PLATFORM_GUARDED
CROSS_CELL_LLM_CACHE_REUSE_POSSIBLE                    = NO
CROSS_CELL_GRAPH_STATE_REUSE_POSSIBLE                  = NO

GRAPH_RAG_08E_READY_FOR_LIVE_DIAGNOSTIC_REAUTHORIZATION = YES (eligibility only)

LIVE_DIAGNOSTIC_AUTHORIZED = NO   ·   LIVE_DIAGNOSTIC_EXECUTED = NO
ROOT_CAUSE_CONFIRMED = NO   ·   H1 = UNCONFIRMED · H2 = UNCONFIRMED · H3 = UNCONFIRMED
FULL_EXECUTION_AUTHORIZED = NO   ·   VALUE_EVIDENCE_READY = NO
```

Even with reauthorization eligibility `YES`, a **separate operator prompt** is required to
authorize the live diagnostic; provisioning readiness alone authorizes nothing (task §76/§77).
