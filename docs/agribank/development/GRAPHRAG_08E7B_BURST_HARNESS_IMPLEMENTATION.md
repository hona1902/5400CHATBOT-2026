# GraphRAG-08E.7B — Burst-Reproduction Harness Implementation

**Status:** OFFLINE IMPLEMENTATION + TESTS + INDEPENDENT REVIEW. **No provider traffic, no
OpenRouter, no provider-backed embedding/indexing, no real burst rung executed, no attempt #6,
no DEV/HOLDOUT, no V/GQ/GD, no mitigation, no production adapter, no GraphRAG-09.** No frozen
parameter, retry policy, allowlist, Source content, or fixture changed. Not checkpointed
(operator review first).

Implements the frozen **GraphRAG-08E.7A** screening design
([GRAPHRAG_08E7A_BURST_REPRODUCTION_DESIGN.md](GRAPHRAG_08E7A_BURST_REPRODUCTION_DESIGN.md)).
Checkpoint: HEAD `70ae3f091cbf798c2fd420eacd5b03d70fe6a2d2`, tag
`graphrag-08e7a-burst-design-approved`, fixture
`a58a68535c345e18f0263904f818e4e2068a164056408665d8bb9233eceb143d` (75/60/30/30/12, unchanged).

## 1. Modules (eval-only; production imports none)

- **`open_notebook/integrations/graphrag/eval/burst_plan08.py`** — pure data/predicates (starts
  nothing): frozen `BurstPlan` + `validate_burst_plan` (fail-closed to the exact ladder),
  `default_burst_plan`, `select_burst_prefix` (nested S001-first prefixes), `budget_precheck`,
  `BurstBudgetGuard` (two distinct hard caps), the reproduction predicates
  (`is_classifier_signature` / `is_strict_s001_event` / `historical_attempt_number_match` /
  `is_novel_valid_failure`), and the content-safe result types (`BurstRungResult`,
  `BurstExperimentResult`, `classify_rung`).
- **`open_notebook/integrations/graphrag/eval/burst_runner08.py`** — execution:
  `BurstWaveIndexer08` (SUBMIT_ALL_THEN_POLL wave), `BurstLadderRunner08` (fresh sidecar per
  rung, drain, ladder stop), `BurstReproductionOrchestrator08` (Option-A + authorization gating,
  reusing `OrchestratorDeps`), and `default_burst_deps` (= `default_live_deps`). Every seam is
  INJECTED; importing/constructing performs no provider/network/DB/container work.

## 2. SUBMIT_ALL_THEN_POLL — the critical separation

`BurstWaveIndexer08` splits the two phases (never gather-per-source):

- **Phase A `submit_wave(sources)`** submits every Source once, in the historical order, and
  **does not poll**. A submit that raises or returns `accepted=False`/no track_id means the
  Source never entered the indexing contract → the full initial wave is **not** established
  (task §14/§15 case B). Otherwise the Source entered the contract (case A).
- Only when all C initial submissions entered the contract is
  **`FULL_INITIAL_WAVE_ESTABLISHED=YES`**; **Phase B `drain_wave`** then polls all established
  submissions to bounded terminal outcomes and applies the frozen bounded retry.

The committed gather-per-source `LiveCellIndexer08.index_cell` is **not** reused for the wave; a
test (`test_submit_all_before_any_poll`) asserts the first C events are submits with zero polls,
and `test_no_gather_regression_early_failure_does_not_shrink_burst` proves an immediate S001
failure cannot shrink the entered burst.

## 3. Full-initial-wave validity & the valid-vs-harness boundary

A rung counts as treatment C only if `FULL_INITIAL_WAVE_ESTABLISHED` (task §13/§18);
`PARTIAL_WAVE_COUNTS_AS_VALID_TREATMENT=NO`. A partial wave yields `TREATMENT_VALID=NO`, no
reproduction classification, and a **runtime stop** that never sets `FIRST_FAILURE_RUNG`
(`test_partial_wave_*`). A Source **accepted then later `DocStatus.FAILED`** is a **valid Source
failure** (experimental evidence) — `test_accepted_then_failed_is_experimental_evidence`.
Provisioning/attestation/endpoint/cleanup/budget/cancellation failures are runtime/harness stops,
not experimental evidence (`test_harness_invalidity_not_experimental_failure`,
`test_cleanup_failure_stops_ladder`, `test_cancellation_propagates`).

## 4. Current-rung drain + ladder stop

`drain_wave` iterates **all** established Sources to terminal; an early failure never cancels the
remaining already-submitted Sources (task §19) — `test_drain_current_rung_no_cancel_on_early_failure`
confirms all C were polled. After a valid rung drains **and is classified**, if it contains any
legitimate Source failure the **ladder stops at the rung boundary** — the next rung is never
provisioned (`test_next_rung_not_entered_after_valid_failure`: `provision_levels == [8]`). No
auto rerun, no auto confirmation, no auto next rung (task §31/§32).

## 5. Distinct budgets (203 planned / 406 attempts / 2 per Source)

`BurstBudgetGuard` keeps **two independent** counters (never one ambiguous "submission_count",
task §27): `planned_source_workload_count` (reserved C at each rung entry, hard cap **203**) and
`actual_index_attempt_count` (every initial AND retry attempt, hard cap **406**), plus a
per-treatment per-Source cap of **2**. A retry counts toward 406 but **not** toward 203. Tests:
`test_workload_budget_204_rejected`, `test_attempt_budget_407_rejected`,
`test_per_source_third_attempt_rejected`, and a clean ladder consumes exactly 203 workload / 203
attempts (`test_clean_ladder_through_75`).

## 6. Retry (frozen)

`MAX_INDEX_ATTEMPTS_PER_SOURCE=2`; the retry decision is the FROZEN
`index_retry08.is_transient_reason` via `concurrency_diag08.characterize_failure` — **unchanged**.
Retries are never pre-submitted; they occur only in the drain phase, after the full wave is
established (task §22), so they cannot alter the initial-burst fidelity
(`test_retry_only_after_full_wave`). Retry-eligible Sources are drained in deterministic
historical order (§23).

## 7. Split reproduction classification (non-exclusive)

Two separate flags (a signature match never implies the S001 event, and never implies a root
cause):

- `CLASSIFIER_SIGNATURE_REPRODUCED` — terminal FAILED with `retry_reason_code ==
  TRACK_TEXT_PRESENT_NO_ALLOWLIST_MATCH` + present-text + non-retryable (task §34);
- `S001_HISTORICAL_EVENT_REPRODUCED_STRICT` — that signature AND `logical_source_id == S001`
  (task §35); `HISTORICAL_ATTEMPT_NUMBER_MATCH = (attempt_number == 1)` recorded **separately**;
- `NOVEL_VALID_FAILURE` — any non-SUCCESS terminal that is not a signature match (retryable-
  exhausted / TIMEOUT / text-absent).

A same-signature failure on S037 → signature YES, strict-S001 NO, novel NO
(`test_strict_s001_vs_other_source_same_signature`). A rung may carry more than one of
B/C/D (non-exclusive, `test_multiple_failure_classes_in_one_rung_non_exclusive`).
`BurstExperimentResult.as_dict()` always emits `root_cause_confirmed=False`.

## 8. Outcome interpretation (frozen)

`FIRST_FAILURE_RUNG` = the first fully-established, drained, VALID rung with ≥1 legitimate Source
failure. A clean ladder → `first_failure_rung=None`, `clean_through_level=75`, all reproduction
flags NO (`test_clean_ladder_through_75`). A harness/runtime stop never sets `FIRST_FAILURE_RUNG`.
Even a positive reproduction flag keeps `ROOT_CAUSE_CONFIRMED=NO` — 08E.7 is reproduction
screening, not axis isolation (task §22/§89); no code or result text claims otherwise.

## 9. Reuse (no parallel live stack) & execution completeness (§83)

The orchestrator reuses the **same** `live_orchestrator08.OrchestratorDeps` /
`default_live_deps` wiring as the 08E.4/08E.5 live diagnostic: Option-A isolation, temp model,
Source prep + canonical embedding, frozen provider binding (`provider_binding08`),
`DockerRuntimeAttestor` + cell provisioner (`cell_provisioner08`), ownership-bound endpoint,
fresh per-rung sidecar (`cell_isolation08.diagnostic_cell08`), content-free `AttemptRecord` +
`characterize_failure`, and cleanup. A future authorized run is:

```python
BurstReproductionOrchestrator08(load_benchmark08(), default_burst_deps(eval_root=…)).run(
    working_dir=…, authorized_live=True)
```

with **no new code** — only operator authorization + runtime secrets. Deny-by-default: the run
fail-closes (before any provider/isolation) on `authorized_live=False`, a missing attestor, a
missing/invalid binding, or a missing provider secret (`REQUIRED_RUNTIME_SECRET_MISSING=<name>`).
`FUTURE_LIVE_RUN_REQUIRES_NEW_CODE = NO`.

## 10. Safety

- **Raw-error containment:** a raw failure string is consumed only transiently by
  `characterize_failure`; it is never stored on an `AttemptRecord`/result/artifact/log
  (`test_raw_error_never_persisted` injects `TEST_SECRET_08E7B_RAW_FAILURE` and asserts absence).
- **Source-content / secret safety:** results carry logical Source IDs + safe reason codes only;
  no Source text, chunk/entity/relation text, or provider key (`test_result_dict_has_no_source_text`).
- **Isolation:** fresh process/workspace/storage per rung; one sidecar shared only within a rung;
  Option-A normal-DB boundary preserved (all DB/provider seams injected; offline tests mutate
  nothing).
- **08E unchanged:** `concurrency_diag08` caps `ALLOWED_LEVELS=(1,2,4,8)`/`MAX_SOURCES_PER_LEVEL=8`/
  `MAX_TOTAL_SUBMISSIONS=64` are untouched; the burst caps live in `burst_plan08`.

## 11. Tests & verification

36 new 08E.7B tests (plan/prefix/budget exactness; submit-all-then-poll; no-gather regression;
partial-wave; accepted-then-failed; drain; ladder stop; clean-through-75; classification
predicates; multi-class rung; harness-vs-experimental; cleanup-stop; cancellation; content/secret
safety; orchestrator deny/attestor/secret gating; full offline Option-A-order flow; production
import boundary). Regression: 08E.7B+08E.5+08E.4+08E.3+08E.2+08E.1+08E **187 pass / 1 skip**
(the pre-existing Windows symlink skip); GraphRAG regression (flag off) **709 pass / 9 skip / 0
fail**; ruff clean; targeted mypy clean on both new files (the 5 pre-existing `concurrency_diag08`
`object`-attr errors are untouched, NOT whole-repo clean). No provider traffic; no container
start; no DB mutation; fixture hash unchanged.

## 12. Posture

```
BURST_PLAN_IMPLEMENTED = YES        SUBMIT_ALL_THEN_POLL_IMPLEMENTED = YES
GATHER_PER_SOURCE_USED_FOR_BURST = NO
FULL_INITIAL_WAVE_GUARD_IMPLEMENTED = YES   CURRENT_RUNG_DRAIN_IMPLEMENTED = YES
STOP_LADDER_AT_RUNG_BOUNDARY_IMPLEMENTED = YES   NESTED_SOURCE_PREFIX_IMPLEMENTED = YES
RUNTIME_203_WORKLOAD_GUARD_IMPLEMENTED = YES     RUNTIME_406_ATTEMPT_GUARD_IMPLEMENTED = YES
PER_SOURCE_ATTEMPT_CAP = 2
CLASSIFIER_SIGNATURE_PREDICATE_IMPLEMENTED = YES  STRICT_S001_EVENT_PREDICATE_IMPLEMENTED = YES
HISTORICAL_ATTEMPT_NUMBER_MATCH_IMPLEMENTED = YES NOVEL_VALID_FAILURE_PREDICATE_IMPLEMENTED = YES
NON_EXCLUSIVE_RUNG_CLASSIFICATION = YES
HARNESS_FAILURE_EXCLUDED_FROM_EXPERIMENTAL_FAILURE = YES
08E7_IMPLEMENTATION_REQUIRES_NEW_CODE = (satisfied by this phase)
FUTURE_LIVE_RUN_REQUIRES_NEW_CODE = NO
08E7_LIVE_AUTHORIZED = NO   FULL_ATTEMPT_6_AUTHORIZED = NO   08F_JUSTIFIED = NO
ROOT_CAUSE_CONFIRMED = NO   H1/H2/H3 = INCONCLUSIVE
HISTORICAL_SIGNATURE_REPRODUCED = NOT_RUN   S001_HISTORICAL_EVENT_REPRODUCED_STRICT = NOT_RUN
VALUE_EVIDENCE_READY = NO
PROVIDER_TRAFFIC = NO   REAL_BURST_RUNGS_EXECUTED = 0
```

Running the live screening sweep requires a SEPARATE explicit operator reauthorization (the
08E.7 live gate), with `OPENROUTER_API_KEY` (and `GRAPHRAG_POC_API_KEY` if the sidecar enforces
auth) set — synthetic/public-data-approved credentials only.
