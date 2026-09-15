# GraphRAG-PN02D-B1-EW4 — Boot-2 Execution Readiness Parity

**Status:** `EW4_REMEDIATION_2_COMPLETE_READY_FOR_CODEX_REREVIEW_3` — offline / provider-free.
**Checkpoint attempt #1 was BLOCKED at post-tag State B (lifecycle-fragile test); tag deleted
per §37, commit `75fa5e0` retained (unapproved ancestor). No push. No provider secret read.
EXEC #5 NOT authorized.**

## 0b. Checkpoint attempt #1 (BLOCKED) + Remediation #2

The operator-authorized EW4 successor-checkpoint attempt reached post-tag State-B validation and
was **correctly BLOCKED**: `tests/test_graphrag_pn02db1ew4.py::test_ew4_tag_does_not_exist_in_real_git_yet`
hard-asserted `observed_tag_exists is False`, a **permanent real-Git tag-absence assumption** that is
legitimately true in State A (pre-checkpoint) but flips to failing the instant the exact checkpoint
tag is created. Classification: **checkpoint-lifecycle fragility** — not a production/runtime/trust
defect. The commit `75fa5e06d1c91e8bf5482a037c77e6bf6762265d` was created; the annotated tag was
created (object `d2755b9…`, peel `75fa5e0`) then, per checkpoint §37, **deleted locally
(unpublished)**; nothing was pushed; the commit was retained as an unapproved checkpoint-attempt
ancestor. Historical tags stayed immutable; zero provider activity.

**Remediation #2 (this turn, tests + docs only — no production change):** the fragile test was
rewritten to be lifecycle-aware and renamed `test_ew4_successor_tag_git_state_is_lifecycle_valid`
(State A: tag absent → empty peel; State B: exact tag present, 40-hex peel == authorized HEAD),
mirroring the EW3 successor-tag lifecycle test. A full EW4-test-file sweep + a cross-test sweep
(r2/ew1/ew2/ew3/ew4) confirmed **no remaining permanent real-Git EW4-tag-absence assumption**
(synthetic State-A absence negatives, e.g. `test_ew4_state_a_mint_fails_closed_when_tag_absent`
using `TEST_B1R2_TAG`, are allowed and retained). A **temporary local real-Git tag** was created at
HEAD, the lifecycle tests passed in State B (86 passed), and the temp tag was deleted — directly
proving the fix against the exact scenario that blocked attempt #1. The already-reviewed Boot-2
readiness production code (M1/M2 fixes) is **unchanged**. The final EW4 checkpoint tag must peel to
a **future** remediation-successor HEAD, not `75fa5e0`.

## 0. Codex Review #1 + Remediation #1

**Codex EW4 Review #1 = `B_REMEDIATION_REQUIRED`** (0 HIGH · 2 MEDIUM · 0 LOW):
- **B1EW4-R1-M1 (MEDIUM)** — readiness hard-deadline off-by-one: `_wait_execution_runtime_ready`
  checked the deadline only after a not-healthy probe, so a cell reporting healthy at/after the
  120s bound could be accepted instead of failing closed.
- **B1EW4-R1-M2 (MEDIUM)** — the exact deadline-boundary / late-ready case was untested.

**Remediation #1 (this section):** the hard monotonic deadline is now checked at the **top of the
poll loop, before the probe**, so it is load-bearing on the success path — a health observation is
accepted only while `readiness_now() < deadline`; at `now >= deadline` the wait fails closed
(`HEALTHY_AT_TIME_EQ_DEADLINE = REJECTED`, `HEALTHY_AT_TIME_GT_DEADLINE = REJECTED`,
`HEALTHY_AT_TIME_LT_DEADLINE = ACCEPTABLE`). No policy/timeout/poll value changed; no fixed sleep;
monotonic default clock unchanged; seams still internal-only. Added 3 deterministic boundary tests
(healthy exactly at deadline → rejected; healthy after deadline → rejected; healthy just before
deadline → accepted) using a scripted clock (no real sleep). `B1EW4-R1-M1_REMEDIATED = YES`,
`B1EW4-R1-M2_REMEDIATED = YES` (final closure pending independent Codex re-review #2). This section
is remediation, **not** a PASS_CLEAN claim.

## 1. Context — the EXEC #4 blocker and the EF1 root cause

The fourth operator-authorized PN02D-B1 real-provider execution (**EXEC #4**) was **BLOCKED /
INCOMPLETE** with **zero provider traffic** at Boot-2 execution-runtime attestation:

```
RuntimeLifecycleError: route table is unavailable until every execution runtime is attested
```

The EW4 phase is the remediation of the root cause established by the **EF1 forensic**
(`PRIMARY_ROOT_CAUSE_CLASSIFICATION = G_ATTESTATION_STATE_MACHINE_DEFECT`):

- `RealPN02RuntimeManager.boot_execution` started each LightRAG execution container and
  **immediately** probed `/health` with **no readiness wait**.
- `DockerCellProcessController.start` returns as soon as `docker run -d` returns (container
  merely *running*); `LightRagCellHealthProber.probe` is a **single** GET (no retry).
- A cold LightRAG v1.5.6 cell is RUNNING ~0.34s after start but only serves `/health` ~7s
  later (EF1 provider-free reproduction), so all 3 cells were observed unhealthy → `attested =
  False` → `route_table()` raised.
- Boot 1 (`boot_preflight`) already waited (the PF1 `wait_ready` fix); **PF1 was never mirrored
  into the provider-bound Boot 2.**

This is **not** a too-short timeout — Boot 2 had **no readiness poll at all**.

## 2. The fix — add the missing readiness transition (not a timeout retune, not a sleep)

`open_notebook/integrations/graphrag/eval/runtimelivepn02d.py`:

- New `RealPN02RuntimeManager._wait_execution_runtime_ready(base_url, host, port)`: a bounded,
  fail-closed, **poll-based** readiness wait over the **same injected `health_prober` seam**
  Boot 2 already uses, inserted **between** `process_controller.start(spec)` and the canonical
  attestation probe — parity with `boot_preflight`.
- Readiness POLICY **reuses** the PF1 Boot-1 values: `EXECUTION_READINESS_TIMEOUT_S =
  PREFLIGHT_READINESS_TIMEOUT_S = 120.0`, `EXECUTION_READINESS_POLL_S =
  PREFLIGHT_READINESS_POLL_S = 2.0` (`BOOT2_READINESS_POLICY_MATCHES_BOOT1 = YES`;
  `READINESS_TIMEOUT_VALUE_CHANGED = NO`).
- **No fixed `asyncio.sleep(N)`** as the mechanism (`FIXED_SLEEP_ADDED = NO`); `readiness_now`
  / `readiness_sleep` are injectable so the race is unit-testable deterministically.
- Readiness success does **not** attest a cell: the canonical health/version/workspace/endpoint/
  storage attestation still runs afterwards and stays **load-bearing**
  (`READINESS_SUCCESS_AUTO_ATTESTS = NO`; version + final-health checks unchanged).
- `route_table()` guard **unchanged** — still requires **all 3** cells attested; no partial
  routing (`ROUTE_TABLE_GUARD_CHANGED = NO`).
- A readiness timeout **fails closed** and propagates; already-started cells are recorded in
  `_execution_handles` before the wait, so the existing owned-only cleanup tears them down
  (`PARTIAL_BOOT_FAILURE_CLEANUP = PASS`).

Boot 1 semantics, EW3 isolation-id design, EW2 model-seed lifecycle, and the live
authorization / checkpoint-trust semantics are **unchanged**.

## 3. Successor checkpoint & governance repoint (EW3 → EW4)

Because production runtime code changes after EW3, EW3 becomes historical for provider
execution and a **successor checkpoint is required**:

- New `authmintlivepn02d.EXPECTED_EW4_CHECKPOINT_TAG =
  "graphrag-pn02db1ew4-boot2-readiness-approved"`.
- `_APPROVED_B1_R2_CHECKPOINT` repointed EW3 → EW4; `current_approved_b1_r2_checkpoint()` now
  returns the EW4 tag. `authb1r2pn02d.B1_R2_EXPECTED_CHECKPOINT_TAG` re-exports EW4.
- EW3 joins EW2/EW1/PF1/B1-R2 as a **retained historical** identity (its constant/string
  unchanged, immutable). EW3/EW2/EW1/PF1/B1-R2 **cannot substitute** for EW4.
- **Lifecycle-aware from day one:** the EW4 annotated tag does **not** exist yet, so the trusted
  Git reader observes its absence and the live mint **fails closed**
  (`b1_r2_tag_not_observed_in_git`). State B (exact EW4 tag at the authorized HEAD) makes only
  the **Git prerequisite** satisfiable — it does **not** auto-authorize a provider run
  (`EW4_STATE_B_AUTO_AUTHORIZES_PROVIDER_RUN = NO`). No permanent tag-absence assertion.

## 4. Tests

New `tests/test_graphrag_pn02db1ew4.py` (15 tests): policy-matches-Boot-1; cold-start race
(unhealthy→healthy→attests); 3-cell eventual readiness; one-cell timeout → fail closed / no
partial route table; version-mismatch-after-readiness → not attested; health-regression-after-
readiness → not attested (final probe load-bearing); mid-boot failure cleans up started cells;
readiness contacts only loopback; healthy-immediately non-regression; and EW4 governance
lifecycle (current == EW4; historical cannot substitute; State A fail-closed; State B Git-gate
satisfiable but no provider auth; checkpoint identity separate from operator grant; EW4 tag
absent in real Git).

Stale **current-state** `EXPECTED_EW3` references (the "current approved successor" assertions)
were swept EW3 → EW4 across `test_graphrag_pn02db0cb_adapters.py`,
`test_graphrag_pn02db0cb_live.py`, `test_graphrag_pn02db1ew1.py`, `test_graphrag_pn02db1ew2.py`,
`test_graphrag_pn02db1ew3.py`, `test_graphrag_pn02db1r2.py`; EW3 is retained in the historical
distinctness sets. Historical/phase references to EW3 (e.g. the isolation-id derivation in
`realseamspn02d`) were preserved.

## 5. Verification evidence

- **Targeted + affected suites:** all green (b0cb adapters/live, ew1, ew2, ew3, ew4, r2, pf1).
- **Broader GraphRAG/PN02 suite:** `1224 passed, 9 skipped, 0 failed` (pre-EW4 baseline
  `1206 / 9 / 0`; delta **+18** = 15 EW4 tests + 3 M2 deadline-boundary tests from remediation #1;
  `NEW_REGRESSIONS = 0`). `test_graphrag_pn02db1ew4.py` = **18** tests.
- **`ruff`** clean on changed modules; **`mypy`** clean (no new errors) on the changed
  production modules.
- The 5 full-suite failures (`test_podcast_*`, `test_proxy`) are pre-existing, Windows-specific,
  in files **unmodified** by EW4 and importing no GraphRAG/PN02 code — not EW4 regressions.
- **Provider-free real validation:** the production `_wait_execution_runtime_ready` + the real
  `LightRagCellHealthProber` were exercised against real cold LightRAG v1.5.6 sidecars,
  provider-free (no `.env`, no `OPENROUTER_API_KEY`, `provider_binding=None`): the immediate
  probe was not-ready and the bounded readiness wait caught the cold start (each cell became
  healthy), then cleanup to zero residue. (The full provider-*bound* `boot_execution` cannot run
  provider-free — its exec cells require the secret VALUE at container start — so this exercises
  the exact new readiness code path, which is what EXEC #4 lacked.)

## 6. Security & scope

`OPENROUTER_API_KEY_READ = NO`, `REAL_PROVIDER_SECRET_USED = NO`,
`EXTERNAL_PROVIDER_NETWORK_CALLS = 0`, `LIVE_PROVIDER_AUTHORIZATION_MINTED = NO`,
`B1_REAL_PROVIDER_EXECUTION = NOT_RUN`. Frozen scientific envelope unchanged (run id, fixture,
provider, models, dimension, K, budgets, concurrency, leakage). No git mutation.
`EXEC5_READY_FOR_AUTHORIZATION = NO` (requires this remediation + independent Codex review +
an operator-approved EW4 successor checkpoint first). B2 / production / Ask / GraphRAG-09 remain
NOT approved.

## 7. Next

Codex EW4 review chain: review #1 `B_REMEDIATION_REQUIRED` → remediation #1 (closed M1/M2) →
re-review #2 `D_PASS_CLEAN`. The checkpoint attempt then BLOCKED on the lifecycle-fragile test
(§0b); remediation #2 (§0b) fixed it (tests/docs only). Next: mandatory **independent Codex EW4
re-review #3** (target `D_PASS_CLEAN`; focus: lifecycle-aware real-Git State A/B, no permanent
real-tag-absence assumption, retained `75fa5e0` history, future tag targets the successor
remediation HEAD, production readiness code untouched) → operator-approved EW4 checkpoint (a NEW
remediation-successor commit + `graphrag-pn02db1ew4-boot2-readiness-approved` at that successor HEAD,
backup-only) → fresh operator authorization for **EXEC #5**.
