# GraphRAG-PN02D-B1-EW3 — Isolation Runtime ID Compatibility

**Status:** `EW3_REMEDIATION_3_COMPLETE_READY_FOR_CODEX_REREVIEW_4` — offline defect
remediation. Codex EW3 review #1 = **B** → rem #1 → re-review #2 = **C** → rem #2 → re-review #3 =
**C (pass with low findings)**: the MEDIUM (M1) is CLOSED; the residual LOW wording finding recurred
twice more in files/phrasings earlier sweeps missed, and remediation #3 has now closed it via a
full changed-set semantic sweep (see §9–§11). No provider traffic, no mint, no Docker boot, no Git
checkpoint. Worktree intentionally DIRTY; HEAD remains the EW2 checkpoint
`707c8782a2ea34e59700ae4cd4d6d1ea403dd2c0`.

## 1. Why this phase exists

The **third** operator-authorized PN02D-B1 real-provider execution (the first AFTER the EW2
checkpoint) was correctly **BLOCKED before any provider traffic** by a new latent code defect.
All Boot-1 provider-free gates passed (checkpoint trust at HEAD 707c8782 / tag EW2 / peel
707c8782 / clean; fixture hash match; provider fingerprint match; caps; `validate_live_run_inputs
= []`; `OPENROUTER_API_KEY` present; Docker + SurrealDB + LightRAG image ready), then the real
runner raised `IsolationConfigurationError` at isolation entry — **before** the seed, model
attestor, in-process mint, provider binding, or Docker boot (0 provider traffic, no mint, no
authorization).

### Root cause (reproduced from source, provider-free)

`realseamspn02d._run_live_b1_execution_composed` entered `async with
isolation_factory(operator_grant.run_id)`, passing the **frozen scientific B1 run id**
`pn02db1-fe3efb27-e720-48c6-b97e-2c0e6a60969d` (hyphens, 44 chars) verbatim. `isolation08.temp_names`
requires `^[A-Za-z0-9]{4,32}$` and raised `IsolationConfigurationError("invalid run_id …")`.

This is the third "next latent defect surfaced once the prior blocker was fixed": exec#1 = no
real seams → EW1; exec#2 = no isolated-namespace model seed → EW2; **exec#3 = the frozen run id
is incompatible with the isolation runtime's namespace-id validation → EW3**. The EW2 fix let
execution proceed past the model-seed stage into isolation entry, revealing this.

## 2. Design — separate the scientific id from the isolation-runtime id

The scientific/operator-visible run id is **FROZEN** and must not change (envelope frozen). EW3
introduces a distinct concept:

- **`scientific_run_id`** = `pn02db1-fe3efb27-e720-48c6-b97e-2c0e6a60969d` — identifies the
  operator grant, the ephemeral live authorization, and every scientific result. Unchanged.
- **`isolation_runtime_id`** — a Surreal-safe identifier used ONLY to name the temporary
  isolated namespace/database, derived deterministically from the scientific run id.

## 3. Derivation — `realseamspn02d._derive_isolation_runtime_id`

```
domain     = "pn02d-isolation-v1"        # versioned, auditable
digest     = sha256(f"{domain}:{run_id}").hexdigest()
runtime_id = "pn02d" + digest[:27]        # 5 + 27 = 32 chars, ^[A-Za-z0-9]{4,32}$
```

For the frozen B1 run id the derived isolation id is exactly
**`pn02d369c1c2b73339d9362faf1a9fa7`**.

**Why a domain-separated SHA-256 (not a truncation-only transform).** `run_id.replace("-","")[:32]`
could collide: two distinct scientific ids sharing a 32-char prefix would name the same temp
namespace. The digest maps distinct scientific ids to distinct isolation ids and cannot be
reproduced by prefix manipulation. The domain separator versions the mapping so it can never
silently collide with another hashing use. The derivation uses **only the non-secret run id** —
no secret material (`OPENROUTER_API_KEY` / `GRAPHRAG_POC_API_KEY`), no randomness, no timestamp,
no machine id — so it is fully deterministic.

## 4. Application — the isolation boundary only

`_run_live_b1_execution_composed` now derives the isolation id and passes it ONLY to the
isolation factory; everything scientific keeps the frozen `operator_grant.run_id`:

```
isolation_runtime_id = _derive_isolation_runtime_id(operator_grant.run_id)
async with isolation_factory(isolation_runtime_id):        # DERIVED id (temp namespace naming)
    async with seed_factory():
        seams = seams_builder(fx, run_id=operator_grant.run_id, env=env, **extra)  # SCIENTIFIC
        driver = RealB1Driver(fx, seams)
        return await driver.run(operator_grant=operator_grant, ...)                # SCIENTIFIC
```

The operator grant is not mutated or cloned; the eventual live authorization the driver mints
still binds the scientific run id; the isolation context creates AND tears down the temp
namespace/database with the same derived id (identity match by construction). `isolation08.temp_names`
is **unchanged** — its generic contract is not weakened to accommodate one identifier.

## 5. Governance — successor checkpoint EW3

The EW3 production change moves HEAD past the EW2 commit `707c8782`, so the EW2 tag no longer
peels to the authorized HEAD and the EW2 Git gate is superseded (the B1-R2 → PF1 → EW1 → EW2 →
EW3 precedent). Governance is repointed:

- **NEW** `EXPECTED_EW3_CHECKPOINT_TAG = "graphrag-pn02db1ew3-isolation-id-compat-approved"` is
  the frozen approved identity (`_APPROVED_B1_R2_CHECKPOINT` / `B1_R2_EXPECTED_CHECKPOINT_TAG`).
  Its annotated tag does **not** exist yet.
- EW2, EW1, PF1 and B1-R2 are retained **HISTORICAL** identities that can never substitute.
- The EW3 tag is absent in real Git → the trusted reader observes it absent → the live mint
  **fails closed** (`b1_r2_tag_not_observed_in_git`). Not tested by minting.

The EW3 checkpoint tests are **lifecycle-aware from day one** (learning the EW2 checkpoint
lesson): the real-Git test supports State A (tag absent → fail-closed) and State B (exact tag at
HEAD → Git gate satisfiable), and State B proves checkpoint IDENTITY only — it never sets
provider authorization.

## 6. Verification (offline, provider-free)

- **New EW3 tests** (`tests/test_graphrag_pn02db1ew3.py`, 15): exact frozen-id derivation +
  32-char length + explicit-formula equality; canonical `temp_names` acceptance; determinism;
  collision resistance (three near-identical run ids → three distinct ids); not-truncation-only;
  domain separation + no-secret/no-randomness; the production composition boundary spy (isolation
  receives the derived id exactly once, seams builder + operator grant keep the scientific id);
  the original isolation-entry blocker removed (frozen id rejected, derived id accepted); EW3
  governance current-is-EW3 + EW1/PF1/B1-R2/EW2 cannot substitute; and the EW3 checkpoint
  lifecycle (real-Git State-A/State-B, synthetic State-A fail-closed, synthetic State-B
  satisfiable-but-no-provider-auth, wrong-peel, dirty-tree).
- **EW2 governance tests reframed**: EW2 is now HISTORICAL (its tag exists at 707c8782,
  immutable, cannot substitute for EW3); the current-successor lifecycle coverage moved to the
  EW3 file. The `current == EW2` assertions in the B1-R2 / EW1 / B0C-B governance test files were
  retargeted to `EXPECTED_EW3_CHECKPOINT_TAG` (the same retarget the EW2 phase did EW1→EW2).
- **Regression (after remediation #1):** 1206 passed / 9 skipped / 0 failed
  (`NEW_REGRESSIONS = 0`; net +3 vs the pre-remediation EW3 baseline of 1203 — the three new
  manifest-lifecycle tests in `test_graphrag_pn02db1r2.py`); ruff clean; mypy 0 new errors in
  changed modules (the 5 pre-existing `concurrency_diag08.py` `object`-attr errors are the
  untouched baseline). Scientific envelope unchanged (frozen run id / fixture / hash / fingerprint
  / models / budgets / K / concurrency); zero provider traffic; no secret read; no mint; HEAD
  still `707c8782` (uncommitted).

## 7. Scope boundary

EW3 proves the isolation-runtime-id naming defect is removed **offline**. It does NOT claim a B1
scientific pass or provider-execution success: `B1_REAL_PROVIDER_EXECUTION = NOT_RUN`. EW2
history is unchanged (EW2 remains `CLOSED_APPROVED` at its own commit/tag); EW3 is a successor to
EW2, not a modification of it.

## 8. Next steps (NOT this turn)

Mandatory independent **Codex EW3 re-review #2** (target D_PASS_CLEAN), then a separate
operator-approved EW3 checkpoint (create the annotated tag peeling to the EW3 HEAD, lifecycle-tests
already tag-aware, BACKUP-only), then a fresh operator authorization to retry PN02D-B1 real
provider execution.

## 9. Codex EW3 review #1 (= B) and remediation #1

**Codex EW3 review #1** (independent, read-only, single task) returned
`CODEX_B1EW3_REVIEW_1_REMEDIATION_REQUIRED` (**B**) — HIGH = 0, MEDIUM = 1, LOW = 1. The core EW3
isolation-id design was independently validated and is **frozen** (scientific run id unchanged;
derived id independently recomputed to `pn02d369c1c2b73339d9362faf1a9fa7`; isolation boundary
correct; no double derivation; `temp_names` not weakened; substitution/wrong-peel gates pass; EW2
immutable; envelope unchanged; original blocker removed provider-free). Two non-core findings were
raised and are now **closed by remediation #1** (this turn — offline, provider-free, no checkpoint):

- **B1EW3-R1-M1 (MEDIUM) — CLOSED.** The `b1_r2_authorization_manifest()` field
  `b1_r2_tag_exists_in_git_now` was hardcoded `False` (and `test_graphrag_pn02db1r2.py` asserted it
  `is False` unconditionally) — the same lifecycle-fragility class that blocked EW2 checkpoint
  attempt #1: once the EW3 annotated tag is minted the field would misreport Git reality and the
  committed test would flip red. **Fix:** the manifest now DERIVES the field from the canonical
  trusted Git reader (`RealTrustedB1R2Reader`) — the SAME authority `b1_r2_preflight` uses — via an
  injectable `git_runner` boundary used ONLY for this manifest's own observation (never a
  mint/trust-root parameter). State A (tag absent) → `False`; State B (exact tag present) → `True`.
  Existence is an OBSERVATION, never authorization: the manifest's `live_provider_authorization_minted`
  / `pn02_provider_run_authorized` stay `False`, and there is no caller-supplied boolean trust root.
  `test_manifest_frozen_and_content_safe` is now lifecycle-aware, and three new tests cover
  State-A/State-B derivation, State-B-does-not-auto-authorize, and the not-a-caller-boolean-trust-root
  invariant (mirrors the B1-R2 / EW1 / EW2 checkpoint-lifecycle precedent).
- **B1EW3-R1-L1 (LOW) — CLOSED.** Governance comments/docstrings still described the current
  approved identity as the "EW2 successor" after governance moved to EW3. Current-state wording was
  updated to "EW3 successor" across `authmintlivepn02d.py`, `authb1r2pn02d.py`, `cli_live_pn02d.py`,
  `driver_live_pn02d.py`, and two comments in `test_graphrag_pn02db1ew1.py`; the manifest parenthetical
  in the B1-R2 preflight doc was corrected to reflect the now-live observation. Genuinely HISTORICAL
  wording (e.g. the EW2 phase document's record of the EW1→EW2 correction) was preserved.

Review #1's verdict (B) and finding IDs are recorded as-is; this remediation does not re-present it
as a pass. Checkpoint remains disallowed until a separate operator authorization; a clean Codex
re-review #2 (target D) is the prerequisite.

## 10. Codex EW3 re-review #2 (= C) and remediation #2

**Codex EW3 re-review #2** (independent, read-only, single task; `CODEX_TEST_EXECUTION =
BLOCKED_BY_CODEX_ENVIRONMENT` — uv/python unavailable in its sandbox, so static + read-only Git
review) returned `CODEX_B1EW3_REREVIEW_2_PASS_WITH_LOW_FINDINGS` (**C**) — HIGH = 0, MEDIUM = 0,
LOW = 1. **B1EW3-R1-M1 = CLOSED** (manifest field derived via `RealTrustedB1R2Reader`; `git_runner`
is manifest/preflight DIAGNOSTIC-ONLY and cannot enter preflight/mint/checkpoint trust; State-A/B
tests pass; wrong-peel fails closed). All core EW3 isolation-id / trust-root / substitution /
EW2-immutability / envelope invariants re-verified clean.

The one residual finding was **B1EW3-RR2-L1 (LOW, wording-only, DESIGN IMPACT NONE)** — remediation
#1's `"EW2 successor"` grep was too narrow and left current-state governance wording still naming
EW2 as the current approved/checkpoint identity. **Remediation #2 (this turn — comment/docstring/
test-name only, `EXECUTABLE_LOGIC_CHANGED = NO`)** performed a semantic sweep (not a single phrase)
and closed every current-state site — including three the review had not enumerated:

- `authmintlivepn02d.py`: `:199` "now the PN02D-B1-EW2 successor tag" → EW3; `:246` and `:255`
  (PF1 and EW1 constant comments) "the current approved identity is now `EXPECTED_EW2_CHECKPOINT_TAG`"
  → `EXPECTED_EW3_CHECKPOINT_TAG`.
- `authb1r2pn02d.py`: `:7` module docstring "current governance-approved checkpoint tag — the
  PN02D-B1-EW2 successor" → EW3; `:42` "peeling to the approved EW2 HEAD" (factually wrong — the EW3
  tag peels to the EW3 HEAD) → "approved EW3 HEAD".
- `cli_live_pn02d.py`: `:304` "the trust-observed EW2 checkpoint" → EW3.
- `tests/test_graphrag_pn02db1r2.py`: the `test_expected_b1_r2_tag_frozen_in_governance` and
  `test_current_approved_checkpoint_git_state_is_valid` comment blocks reframed EW2→EW3 (current
  approved identity is EW3; EW2 now historical).
- `tests/test_graphrag_pn02db1ew1.py`: renamed `test_governance_current_is_ew2_…` →
  `test_governance_current_is_ew3_with_ew2_ew1_pf1_b1r2_historical` and `test_exact_ew2_tag_…` →
  `test_exact_ew3_tag_with_peel_is_the_only_accepted_identity` (names only; bodies/assertions
  unchanged).

Genuinely HISTORICAL references (the `EXPECTED_EW2_CHECKPOINT_TAG` constant + export, "superseded
historical EW2 checkpoint" descriptions, `B1EW2-*` finding-ID provenance citations, and the EW2-era
record in the EW2 phase doc) were preserved. Post-remediation semantic sweep:
`STALE_EW2_CURRENT_STATE_REFERENCES = 0`, `UNCLASSIFIED_EW2_REFERENCES = 0`,
`HISTORICAL_EW2_REFERENCES_PRESERVED = YES`. Re-review #2's verdict (C) and the finding are recorded
as-is; the MEDIUM stays CLOSED. Checkpoint remains disallowed until a separate operator
authorization; a clean Codex re-review #3 (target D) is the prerequisite.

## 11. Codex EW3 re-review #3 (= C) and remediation #3

**Codex EW3 re-review #3** (independent, read-only, single task; `CODEX_TEST_EXECUTION =
PARTIAL_BLOCKED_BY_CODEX_ENVIRONMENT`) returned `CODEX_B1EW3_REREVIEW_3_PASS_WITH_LOW_FINDINGS`
(**C**) — HIGH = 0, MEDIUM = 0, LOW = 1. **B1EW3-R1-M1 stays CLOSED** and every core isolation-id /
git_runner-diagnostic-only / boundary / substitution / trust-root / EW2-immutability / envelope
invariant re-verified clean. The residual **B1EW3-RR3-L1 (LOW, comment-only, DESIGN IMPACT NONE)**
was two MORE current-state stale EW2 comments — this time in the B0C-B test files, which the
remediation-#2 sweep had not covered (`tests/test_graphrag_pn02db0cb_adapters.py:825-826` and
`tests/test_graphrag_pn02db0cb_live.py:843`, both describing current governance as frozen to the
"successor EW2 tag" while their adjacent assertions compare `EXPECTED_EW3_CHECKPOINT_TAG`).

This was the third recurrence of the same stale-wording class in a place the prior remediation had
not reached (rem #1's grep phrase was too narrow; rem #2's file-set was too narrow). **Remediation
#3 (this turn — comment-only, `EXECUTABLE_LOGIC_CHANGED = NO`)** therefore derived its sweep scope
DIRECTLY from Git — the full EW3 changed-set (`git diff --name-only HEAD` = 12 tracked +
`git ls-files --others --exclude-standard` = 2 untracked = **14 files**) — and classified EVERY
`EW2` occurrence in all 14 (CURRENT_STATE_STALE / HISTORICAL_VALID / IDENTIFIER_PROVENANCE /
FINDING_ID_PROVENANCE / CONSTANT_FOR_HISTORICAL_NEGATIVE_TEST). Only the two B0C-B test comments
classified CURRENT_STATE_STALE; both were fixed (EW2→EW3, EW2 recast as the superseded historical
checkpoint). All other EW2 hits — the `EXPECTED_EW2_CHECKPOINT_TAG` constant/export, `B1EW2-*`
finding IDs, `PN02D-B1-EW2 §…` phase-provenance citations, "supersedes the historical EW2
checkpoint" descriptions, the EW3-doc finding-descriptions, CURRENT_PHASE review-history, and the
EW2 phase's own test file — are legitimately historical and were preserved. Post-sweep:
`STALE_EW2_CURRENT_STATE_REFERENCES = 0`, `UNCLASSIFIED_EW2_REFERENCES = 0`,
`FALSE_POSITIVE_HISTORICAL_REWRITES = 0`. Re-review #3's verdict (C) and the finding are recorded
as-is; the MEDIUM stays CLOSED. Checkpoint remains disallowed until a separate operator
authorization; a clean Codex re-review #4 (target D) is the prerequisite.
