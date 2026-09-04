# GraphRAG-08E.4 — Live Diagnostic Execution Wiring

**Status:** OFFLINE IMPLEMENTATION + TESTS + INDEPENDENT REVIEW — execution wiring only.
**No provider traffic, no provider-backed indexing, no sidecar start, no bounded sweep, no
attempt #6, no DEV/HOLDOUT, no V/GQ/GD, no frozen-parameter change.**

This phase implements the final missing execution wiring the GraphRAG-08E bounded live
concurrency diagnostic needs. It does **not** run the diagnostic and does **not** authorize
Stage B — a fresh operator reauthorization is still required.

## 1. Preserved Reauthorization #3 record (honest)

```
GRAPH_RAG_08E_LIVE_DIAGNOSTIC_REAUTHORIZATION_3 = BLOCKED_PRE_PROVIDER
BLOCKER = LIVE_EXECUTION_WIRING_NOT_IMPLEMENTED
PROVIDER_TRAFFIC = NO   CELLS_COMPLETED = 0   ACTUAL_TOTAL_SUBMISSIONS = 0
OPTION_A_SURREAL_ISOLATION = NOT_ENTERED   SIDECAR_STARTED = NO   NORMAL_DB_MUTATION = NO
DEV_EXECUTED = 0   HOLDOUT_EXECUTED = 0   ATTEMPT_6 = NOT_RUN
H1/H2/H3 = UNCONFIRMED   ROOT_CAUSE_CONFIRMED = NO
```

Reauthorization #3 produced **zero** H1/H2/H3 evidence — it is not a diagnostic repetition.
Verified against current source: `run_sweep` requires an injected `index_cell_fn` that only
fake/test code supplied; no live orchestrator existed; the `DiagnosticCell` passed to the
indexer carried no `base_url`/`port`. Those gaps are what this phase fills.

## 2. New architecture (eval-only)

Two new eval-only modules; production imports neither.

- `live_indexer08.py` — `LiveCellIndexer08`: indexes ONE valid provisioned cell's frozen
  Source subset against THAT cell's own sidecar and returns content-free `AttemptRecord`s.
  It never provisions, never sweeps, never interprets H1/H2/H3. Endpoint reached ONLY via
  the provisioner's ownership-bound accessor (below); the LightRAG client is INJECTED (a
  fake drives every test; `RealCellIndexClient` wraps the pinned GraphRAG service bound to
  the cell's own `base_url` — never a global/default URL).
- `live_orchestrator08.py` — `LiveDiagnosticOrchestrator08`: assembles preflight → Option-A
  isolation → temp embedding Model → frozen Source prep + canonical embedding → per-cell
  provisioner + injected `DockerRuntimeAttestor` → `run_sweep(...)` with the per-cell indexer
  → content-free artifact → global cleanup. Every provider/DB/Docker seam is INJECTED
  (`OrchestratorDeps`); `default_live_deps(...)` wires the real seams for a future run.

The one change to `cell_provisioner08.py` is a **read-only** `active_provisioned_cell(identity)
-> ProvisionedCell` accessor — the ownership-bound bridge that hands the indexer the exact
cell's `base_url`/`port`/`workspace`/`storage_dir`/`process_identifier`. It starts nothing,
indexes nothing, and calls no provider (the §22 provisioner-vs-indexer boundary is preserved).

## 3. Endpoint ownership (Defect D from Reauth #3)

`resolve_cell_endpoint(provisioner, cell)` fails closed unless the provisioner's
`active_provisioned_cell` agrees, on every field, with the entered+validated `DiagnosticCell`
(same `run_id`, `cell_id`, `workspace`, owned `storage_dir`), the host is loopback, and the
port is valid. A bare/stale/foreign/non-loopback/wrong-port endpoint is rejected — the indexer
never reads `OPEN_NOTEBOOK_GRAPHRAG_BASE_URL`/`load_config`, and each cell's client is bound to
its own endpoint (per-cell propagation test pins A↔PORT_A, B↔PORT_B).

## 4. Authorization boundary (deny by default)

Provider work requires **all** gates, checked in order (task §14/§19/§20/§23):

```
preflight (frozen fixture + bounded plan)
  -> DockerRuntimeAttestor REQUIRED (missing -> fail closed)
    -> authorized_live == True (deny by default; else stop BEFORE isolation/provider)
      -> Option-A isolation entered (all DB/provider work inside it)
        -> temp Model -> Sources+embedding
          -> run_sweep(authorized_live=True, require_isolation=True) + valid provisioned cell
```

`run_sweep` re-checks `authorized_live` AND provisions/validates each cell via
`diagnostic_cell08` before the injected indexer runs (double gate). `authorized_live=False`, a
missing attestor, an invalid cell, or an un-entered isolation each yield **zero** provider/index
calls.

## 5. Frozen reuse (no drift)

- Retry decision = the frozen `index_retry08.is_transient_reason` via
  `concurrency_diag08.characterize_failure` (a test asserts zero divergence). Per-Source cap =
  2 (`MAX_INDEX_ATTEMPTS_PER_SOURCE`, pinned == the runner default); no hidden third attempt.
- Failure family = the frozen 08E taxonomy. Retry classification and family stay separate.
- Plan/budget = the frozen `default_plan` (levels [1,2,4,8], 2 reps, 30 expected submissions),
  hard cap 64. Worst legal execution = 30×2 = 60 ≤ 64; `submission_count` is guarded and
  `run_sweep`'s per-cell/total record guards remain. An over-cap plan fails `validate_plan`
  before any provider work.
- `AttemptRecord` is the existing content-free schema; a raw provider/LightRAG error string is
  used only transiently to compute the characterization and is NEVER stored, logged, or
  returned (a secret-injection test pins it). Cancellation propagates and is never classified
  as a provider failure. An incomplete sweep leaves H1/H2/H3 INCONCLUSIVE.

## 5a. Independent review (preserved honestly)

The independent adversarial review found the fail-closed safety envelope solid and
well-tested (authorization double-gate, isolation-first ordering, endpoint ownership,
content safety, budget, no-live-execution — all verified clean), but flagged two real
**execution-validity** defects in the untested real live-path glue, both **fixed** here:

- **H1 (HIGH) — FIXED.** `RealCellIndexClient.status` read `getattr(st, "detail", None)`
  on an `IndexStatus` that has no error-text field, so every real FAILED became
  `characterize_failure(None)` → `TRACK_TEXT_ABSENT` → family `UNKNOWN_SAFE`,
  retryable=False. A real run would have completed mechanically while suppressing the
  frozen transient retry and collapsing every family — leaving H1/H2/H3 INCONCLUSIVE
  regardless of the true cause (a diagnostically void sweep). Fixed by reading the FAILED
  doc's raw error text with the frozen, content-contained `index_retry08._fetch_failed_reason_ex`
  (the same `GET /documents/track_status` reader `runner08` uses via `diagnose_failed_track`)
  and feeding it transiently to `characterize_failure`. Pinned by a new test.
- **M1 (MEDIUM) — FIXED.** The container's `LIGHTRAG_API_KEY` comes from
  `GRAPHRAG_POC_API_KEY`, but `default_live_deps` defaulted the client/prober key to
  `None`, so a key-protected sidecar would 401 every index submit. Fixed by defaulting the
  client/prober key from the same `GRAPHRAG_POC_API_KEY` env. Pinned by a new test.
- **L1 (LOW) — documented.** The submit-exception retry uses the frozen 08E decision-twin
  (`is_transient_reason` via `characterize_failure`) uniformly across submit + track,
  rather than `runner08`'s typed submit classifier. This is the intentional
  single-classifier 08E design (the diagnostic's failures of interest are track-surface);
  the code comment now states this precisely.
- **L2 (LOW) — FIXED.** The production-boundary test's grep now also covers the non-eval
  `open_notebook/integrations/graphrag/` modules (excluding the eval package).

## 6. Scope

Changes confined to eval wiring + tests + docs. No change to Source lifecycle, embedding
semantics (the canonical `embed_source_command` path is reused, not reimplemented), the LightRAG
client HTTP contract, retry policy/allowlist, full-run concurrency, provider/model, the LightRAG
pin, or the fixture (`a58a68535c345e18f0263904f818e4e2068a164056408665d8bb9233eceb143d`
unchanged). No migration, no `.env`, no runtime artifact. Production imports no eval code. The
orchestrator/indexer never import V/GQ/GD, `run_full_benchmark`, or the full-run runner (attempt
#6 is unreachable from here).

## 7. Posture

```
LIVE_DIAGNOSTIC_AUTHORIZED = NO   LIVE_DIAGNOSTIC_EXECUTED = NO   ATTEMPT_6 = NOT_RUN
PROVIDER_TRAFFIC = NO   PROVIDER_BACKED_INDEXING = NO   DEV/HOLDOUT = 0/0
ROOT_CAUSE_CONFIRMED = NO   H1/H2/H3 = UNCONFIRMED
FULL_EXECUTION_AUTHORIZED = NO   VALUE_EVIDENCE_READY = NO
```

After this phase the execution path is COMPLETE (a future authorized call needs no new glue),
but running the bounded diagnostic (Stage B) still requires a **separate explicit operator
reauthorization**, and that live run must inject the real `DockerRuntimeAttestor` +
`default_live_deps`.
