# GraphRAG-PN02D-B0C-B — Codex Initial-Review Findings Remediation

**Phase kind:** REMEDIATION of the Codex initial review (decision A — FAIL, 2 HIGH / 4
MEDIUM / 0 LOW). Executed with ZERO real provider traffic; NOT checkpointed; Codex
re-review still required before any checkpoint.

Baseline: branch `feature/graphrag-lifecycle`, HEAD + design-tag peel
`cb766883f6421e5c692317f286e2ff071bf82fe8`; the B0C-B implementation is uncommitted
(the exact tree Codex reviewed). The historical B0C-A checkpoint document is unchanged.

Codex could not run pytest/ruff/mypy in its sandbox (Windows temp `Access is denied`);
its review was static. Claude re-ran the zero-provider verification in the normal dev
environment: **604 passed / 1 skipped** (595 prior + 9 new remediation tests), ruff
PASS, mypy 0 errors in new/changed modules (5 pre-existing `concurrency_diag08`
baseline findings only). `CLAUDE_INTERNAL_REVIEW` is supplementary and does not replace
Codex.

---

## B0CB-H1 — Preflight fixture binding — **REMEDIATED**

- **Status:** FIXED. The full fixture-identity chain is now enforced BEFORE minting:
  `real_preflight_auth.fixture_hash == observed_fixture_hash == operator_grant.fixture_hash
  == expected_frozen_fixture_hash`. Defense in depth at two layers.
- **Files:** `authmintlivepn02d.py` (`mint_live_provider_run_authorization` step 5 adds
  the `real_preflight_auth.fixture_hash == expected` check); `authlivepn02d.py`
  (`mint_provider_run_authorization` now binds `real_preflight_auth.fixture_hash ==
  fixture_hash` at the shared low-level boundary, so the simulation path is also bound —
  it already passes matching hashes).
- **Tests:** `test_mint_rejects_preflight_minted_for_wrong_fixture` (same run_id, wrong
  preflight fixture hash), `test_mint_rejects_wrong_observed_fixture_hash`,
  `test_mint_rejects_wrong_grant_fixture_hash`.
- **Design impact:** NONE. `PREFLIGHT_FIXTURE_BINDING_HARDENED = YES`.

## B0CB-H2 — Clean worktree / approved baseline — **REMEDIATED**

- **Status:** FIXED. A live authorization now requires a real `GitBaselineAttestation`
  of OBSERVED repository state (branch, HEAD commit, head tag, tag peel, and
  staged/unstaged/execution-affecting-untracked counts). `is_clean` is DERIVED from the
  counts, so a caller cannot assert cleanliness by hand — a bare boolean is rejected as
  the wrong type. The mint and the CLI compare the observed attestation against the
  operator grant's approved commit/tag/peel and require a clean tree, all BEFORE any
  binding/runtime/backend invocation. The approved baseline comes from the grant/manifest
  (never hard-coded); the CLI's `read_git_baseline()` obtains actual repo state via
  `git status --porcelain` + rev-parse/describe.
- **Files:** `authmintlivepn02d.py` (new `GitBaselineAttestation`,
  `attest_approved_clean_baseline`, `GitBaselineError`; the live mint takes a
  `git_baseline_attestation` and drops the loose observed-commit/tag strings);
  `cli_live_pn02d.py` (new `read_git_baseline()` real reader + `GitBaselineReader`;
  `validate_live_run_inputs` + `evaluate_execute_b1_live` now validate a clean, approved
  attestation); `driver_live_pn02d.py` (`RealB1Driver.run` takes a
  `git_baseline_attestation` and passes it to the mint).
- **Tests:** `test_mint_rejects_dirty_worktree`,
  `test_mint_rejects_baseline_mismatch_even_when_clean`,
  `test_mint_rejects_bare_boolean_baseline`,
  `test_cli_execute_b1_live_dirty_git_baseline_refused` (dirty attestation → refused),
  and the CLI baseline-mismatch/simulation refusals. Tests use an injected/fake
  attestation, so the intentionally-dirty remediation tree does not block them.
- **Design impact:** NONE. `CLEAN_TREE_ATTESTATION_IMPLEMENTED = YES`,
  `APPROVED_BASELINE_ATTESTATION_IMPLEMENTED = YES`, `LIVE_AUTH_DIRTY_TREE_BYPASS = NO`.

## B0CB-M1 — Provider-free preflight boot — **REMEDIATED**

- **Status:** FIXED. The preflight no longer boots through `CellProcessSpec` /
  `DockerCellProcessController` (which publishes a port and can inherit
  `LIGHTRAG_API_KEY`). It now builds the PN02D-A provider-free boot command via
  `realsidecarpn02d.make_spec` + `build_run_command` (`--internal` network, EMPTY
  provider bindings, NO published port, NO secret) and runs
  `assert_no_provider_binding_in_command` on the argv BEFORE launch (structural refusal
  of any provider binding/secret) plus an explicit no-`-p` guard. Preflight runs through
  a dedicated injected `preflight_runner` (default `RealProviderFreePreflightRunner`
  over the content-safe `DockerCLI`; a fake in tests). Provider-free preflight and
  provider-bound execution remain different lifecycles
  (`SAME_CONTAINER = NO`).
- **Files:** `runtimelivepn02d.py` (`PreflightRunnerLike`,
  `RealProviderFreePreflightRunner`, rewritten `boot_preflight`/`teardown_preflight`);
  `driver_live_pn02d.py` (`LiveB1Seams.preflight_runner`, injected into the manager).
- **Tests:** `test_two_boot_lifecycle_preflight_then_execution` asserts every preflight
  boot command passes `assert_no_provider_binding_in_command`, contains no `-p`, and
  carries no provider secret value; the full-sim asserts preflight runs via the runner,
  not the execution controller.
- **Design impact:** REFINEMENT (the preflight runner is a preflight-specific seam
  reusing the PN02D-A provider-free mechanism). `PROVIDER_FREE_PREFLIGHT_PATH_HARDENED =
  YES`, `PROVIDER_SECRET_PRESENT_DURING_PREFLIGHT = NO`.

## B0CB-M2 — Preflight success gate — **REMEDIATED**

- **Status:** FIXED. Added an explicit `_preflight_passed` flag set ONLY on the normal
  (non-exception) return path and ONLY when every gate attested — never from the
  `finally` teardown. `boot_execution` now requires
  `_preflight_passed AND _preflight_done AND _preflight_torn_down` (plus the live
  capability + attested binding).
- **Files:** `runtimelivepn02d.py` (`boot_preflight` sets `_preflight_passed`;
  `boot_execution` requires it).
- **Tests:** `test_failed_preflight_cannot_boot_execution` (preflight attestation fails →
  `preflight.passed is False` → `boot_execution` raises `TwoBootOrderError`).
- **Design impact:** NONE. `EXECUTION_REQUIRES_SUCCESSFUL_PREFLIGHT = YES`,
  `FAILED_PREFLIGHT_CAN_BOOT_EXECUTION = NO`.

## B0CB-M3 — Corpus cleanup ownership — **REMEDIATED**

- **Status:** FIXED. `RealB1Driver.run`'s outer `finally` now runs the FULL owned-only
  cleanup (`_cleanup` = runtime cleanup + corpus teardown), guarded idempotent with a
  `_cleaned` flag, so corpus teardown happens on ANY failure path — including a failure
  DURING corpus provisioning, before the dependency bundle / `cleanup_fn` was wired. The
  corpus teardown (the isolated-namespace drop, opened at seam-build time) is invoked
  whenever a teardown seam exists.
- **Files:** `driver_live_pn02d.py` (`_cleaned` guard; outer `finally` calls
  `_cleanup`).
- **Tests:** `test_partial_corpus_failure_triggers_owned_cleanup` (embedder raises
  mid-provisioning → run raises → corpus torn down + execution runtimes terminated, no
  residue).
- **Design impact:** NONE. `PARTIAL_CORPUS_FAILURE_CLEANUP = PASS`.

## B0CB-M4 — Index fixture allowlist mandatory — **REMEDIATED**

- **Status:** FIXED. `allowed_source_keys` is now a REQUIRED, non-empty argument for both
  `RealPN02IndexClient` and `build_real_index_client_factory`; construction fails closed
  (`RealIndexAdapterError`) without it. There is no guard-less real-construction mode.
  `submit` rejects any `file_source` not in the frozen fixture-key set (arbitrary
  values, ON record ids, paths, URLs, foreign/malformed) BEFORE any HTTP request. The
  live dependency builder passes the fixture Source-key universe.
- **Files:** `indexadapterpn02d.py` (`RealPN02IndexClient.__init__` + factory require a
  non-empty allowlist); `driver_live_pn02d.build_live_b1_driver_deps` already passes
  `fixture.source_keys`.
- **Tests:** `test_index_client_requires_mandatory_allowlist`,
  `test_index_factory_requires_mandatory_allowlist`,
  `test_index_fixture_key_guard_rejects_unknown_key` (arbitrary/record-id/path/URL
  `file_source` all rejected pre-wire; no HTTP issued).
- **Design impact:** REFINEMENT. `REAL_PN02_INDEX_FIXTURE_ALLOWLIST_MANDATORY = YES`,
  `ARBITRARY_FILE_SOURCE_REACHES_REAL_HTTP = NO`.

---

## Index-client deviation — design refinement (§9)

`INDEX_CLIENT_DEVIATION = REFINED_AND_EXPLICITLY_GUARDED`.

The approved B0C-A design named `RealCellIndexClient` as the reuse target. Source
forensic during B0C-B proved `RealCellIndexClient` → `GraphRAGService.index_source` →
`validate_source_id` requires Open Notebook **record ids** and rejects a PN02 fixture
Source key used as `file_source`, while the frozen PN02 evaluation requires fixture
Source keys in `file_source` (so GD provenance normalizes against
`fixture.source_keys`). The real PN02 implementation therefore uses a PN02-specific
adapter over the SAME real `POST /documents/text` wire contract (identical endpoint,
method, body `{"text", "file_source"}`, `X-API-Key` auth, timeout/error semantics, and
`track_status` completion), WITHOUT the inapplicable record-id validation. The refined
contract is acceptable because the mandatory fixture-key allowlist (B0CB-M4) and the
run/notebook/workspace/authorization guards enforce that only a frozen fixture Source
key can reach the transport. This is an explicit **design refinement**, not silent
implementation equivalence. The B0C-A checkpoint document is unchanged (history
preserved).

## GD-client deviation — unchanged (§10)

`GD_CLIENT_DEVIATION = ACCEPTABLE_IMPLEMENTATION_DETAIL`. Codex accepted it; the GD path
was not changed by this remediation (its properties — `only_need_context=true`, auth,
timeout/error handling, fixture-key provenance, foreign/malformed rejection via the
executor normalizer, notebook-membership backstop, vendor-schema containment, no
fabricated rank/score — remain PASS). No GD tests were added.

---

## Preserved Codex PASS areas

No PASS area was regressed. The remediation was minimal and targeted; run_id/capability
hardening, corpus provisioner + workload/budget separation, embedding-model attestation,
index completion polling, GD backend + provenance, the vector backend (member-restriction
before ranking, cosine equivalence, K3/K5 one ranking), delete backend + shared-Source
isolation + stale-evidence backstop, live-entrypoint governance, secret handling,
normal-DB safety, production/eval isolation, and evaluator reuse are unchanged and still
covered by their tests.

## Verification (Claude, zero-provider)

`TARGETED_TESTS` = `tests/test_graphrag_pn02db0cb_adapters.py` +
`tests/test_graphrag_pn02db0cb_live.py` → **61 passed**. `BROADER_TESTS` = PN02 +
graphrag_08 + B0C-B → **604 passed / 1 skipped** (`NEW_REGRESSIONS = 0`). `RUFF` = PASS.
`MYPY_NEW_CODE` = 0 errors (5 pre-existing `concurrency_diag08` baseline findings only).
Secret/network audit: `SECRET_VALUES_COMMITTED = NO`,
`EXTERNAL_PROVIDER_NETWORK_CALLS = 0`, `REAL_PROVIDER_BOUND_LIGHTRAG_BOOT_COUNT = 0`,
`REAL_INDEX/GD/VECTOR/DELETE_OPERATIONS = 0`, `NORMAL_DB_MUTATIONS = 0`,
`NEW_MIGRATIONS = 0`, `PRODUCTION_IMPORTS_EVAL = NO`.

## Governance (retained)

`CODEX_INITIAL_REVIEW = FAIL` (2 HIGH / 4 MEDIUM — historical, not overwritten).
`CODEX_RE_REVIEW = NOT_RUN`. `B0C_B_CHECKPOINT_ALLOWED = NO`. `PN02D_B1_R2 = NOT_STARTED`.
`PN02_PROVIDER_RUN_AUTHORIZED = NO`. `PN02D_B2_QA_AUTHORIZED = NO`.
`GRAPHRAG_PRODUCTION_INTEGRATION = NOT_APPROVED`. `LIGHTRAG_ASK_INTEGRATION =
NOT_APPROVED`. `GRAPH_RAG_09_JUSTIFIED = NO`.

**STOP:** `GRAPH_RAG_PN02DB0CB_REMEDIATION_READY_FOR_CODEX_REREVIEW`. No checkpoint; no
Codex re-review in this turn.

---

# Codex Re-Review Remediation Cycle #2

The Codex re-review (#1) of the cycle-1 remediation returned **decision A — FAIL, 2 HIGH
/ 0 MEDIUM / 0 LOW**: H1/M3/M4 PASS, but H2/M1/M2 FAIL with two NEW HIGH findings on the
**real/default** code paths (the cycle-1 tests exercised those paths only via fakes).
Codex's sandbox again could not run tests (Windows temp access denied) — its re-review
was static. This cycle targets ONLY those two findings; no other area was reopened, and
no B0C-A history was rewritten. `DESIGN IMPACT = NONE` (both findings; per Codex).

## B0CB-H2-R1 — Real tag identity (no branch-name fallback) — **REMEDIATED**

- **Root cause:** `cli_live_pn02d.read_git_baseline()` substituted `tag = branch` and
  `tag_peel = commit` when `git describe --tags --exact-match` returned no tag, so a
  clean but UNTAGGED HEAD could satisfy an approved-tag identity if the manifest approved
  the branch name as the tag.
- **Fix:** the branch-name/HEAD-SHA fallback is REMOVED — an untagged HEAD keeps
  `head_tag`/`tag_peel_commit` EMPTY. `read_git_baseline` now takes an injectable
  `git_runner` (real by default) so the REAL parser is unit-testable without a repo.
  `attest_approved_clean_baseline` additionally fails on `no_exact_tag_on_head`,
  `no_observed_tag_peel`, and `head_not_at_tag_peel` (actual HEAD must equal the peeled
  approved tag commit, §4).
- **Files:** `cli_live_pn02d.py` (`read_git_baseline`), `authmintlivepn02d.py`
  (`attest_approved_clean_baseline`).
- **Tests (real parser + real attest, injected git runner):** clean exact-tagged HEAD →
  PASS; clean untagged HEAD → FAIL; branch-name-as-tag-but-no-real-tag → FAIL
  (load-bearing); wrong exact tag → FAIL; tag peels to another commit → FAIL; HEAD ≠ tag
  peel → FAIL; HEAD correct but approved tag differs → FAIL; staged / unstaged /
  untracked-execution-affecting → FAIL. (`test_h2r1_*`.)
- **Result:** `B0CB_H2_R1_REMEDIATED = YES`, `REAL_EXACT_TAG_REQUIRED = YES`,
  `BRANCH_NAME_TAG_FALLBACK_PRESENT = NO`, `UNTAGGED_HEAD_CAN_AUTHORIZE = NO`,
  `APPROVED_BASELINE_ATTESTATION_IMPLEMENTED = YES`.

## B0CB-M1M2-R1 — Real preflight runner (fail-closed + 3 independent signals) — **REMEDIATED**

- **Root cause:** `RealProviderFreePreflightRunner.launch()` ignored
  `network_create_internal()` / `DockerCLI.run()` return values and returned a handle
  regardless, and `version_signals()` returned the image label three times — so a
  failed/nonexistent provider-free container could still set `_preflight_passed = True`
  and mint `RealLightRAGPreflightAuthorization`.
- **Fix (fail-closed):** `launch()` now raises `PreflightRunError` (returning NO handle,
  after cleaning any partial owned network/container) when `network_create_internal`
  returns False, `docker run` returns nonzero or raises, or `inspect_state` shows the
  container is not running. `version_signals()` observes THREE genuinely independent
  signals from the running container — `DockerCLI.health()` (runtime health-endpoint core
  version), `DockerCLI.runtime_version()` (in-container installed import version), and
  `DockerCLI.image_version_label()` (pinned image label) — each must be present or it
  fails closed; the manager's `attest_version` then requires all three to equal the
  frozen version. `boot_preflight` wraps each runtime so ANY failure fails the preflight
  closed (`_preflight_passed` stays False), and `boot_execution` already requires
  `_preflight_passed`. Both remain provider-free (no published port, no secret) and the
  runner is injectable (a fake `DockerCLI` transport drives the REAL runner logic in
  tests).
- **Files:** `runtimelivepn02d.py` (`PreflightRunError`, `PreflightRunnerLike`,
  `RealProviderFreePreflightRunner`, `boot_preflight`).
- **Tests (REAL runner + fake Docker transport, NOT a fake runner):** network-create
  failure → fail-closed; docker-run nonzero → fail-closed + owned network/container
  cleaned; docker-run exception → fail-closed; container not running → fail-closed;
  health unavailable / installed-version missing / image-label missing → fail-closed;
  three-signal independence (changing health core does not change the label); and at the
  manager level: image-label-correct-but-no-real-boot → preflight NOT passed → execution
  refused (Codex scenario 14); one-signal mismatch → preflight NOT passed; all three
  correct → preflight PASS (positive real-logic path). (`test_m1_*`, `test_m1m2_*`.)
- **Result:** `B0CB_M1M2_R1_REMEDIATED = YES`, `PROVIDER_FREE_PREFLIGHT_PATH_HARDENED =
  YES`, `NETWORK_CREATE_FAILURE_CAN_PASS_PREFLIGHT = NO`,
  `DOCKER_RUN_FAILURE_CAN_PASS_PREFLIGHT = NO`, `NONEXISTENT/EXITED_CONTAINER_CAN_PASS_
  PREFLIGHT = NO`, `REAL_RUNTIME_VERSION_SIGNAL_COUNT = 3`,
  `INDEPENDENT_VERSION_SIGNAL_COUNT = 3`, `THREE_SIGNAL_VERSION_ATTESTATION = PASS`,
  `IMAGE_LABEL_ONLY_CAN_PASS_PREFLIGHT = NO`,
  `FAILED_OR_NONEXISTENT_PREFLIGHT_CAN_MINT_AUTH = NO`,
  `FAILED_OR_NONEXISTENT_PREFLIGHT_CAN_BOOT_EXECUTION = NO`,
  `PROVIDER_SECRET_PRESENT_DURING_PREFLIGHT = NO`,
  `PREFLIGHT_FAILURE_RUNTIME_RESIDUE = 0`, `PREFLIGHT_FAILURE_NETWORK_RESIDUE = 0`.

## Preserved (not reopened)
H1, M3, M4 remain PASS; INDEX_CLIENT_DEVIATION = ACCEPTABLE_REFINED_IMPLEMENTATION,
GD_CLIENT_DEVIATION = ACCEPTABLE_IMPLEMENTATION_DETAIL (no GD change). No prior PASS area
regressed. B0C-A checkpoint document unchanged.

## Verification (Claude, zero-provider — supplementary, not a Codex substitute)
82 B0C-B tests pass (61 + 21 new cycle-2 tests); ruff PASS; mypy 0 errors in changed
modules (5 pre-existing `concurrency_diag08` baseline findings only). Governance retained:
`CODEX_INITIAL_REVIEW = FAIL`, `CODEX_REREVIEW_1 = FAIL (A, 2 HIGH)`, `CODEX_REREVIEW_2 =
NOT_RUN`, `B0C_B_CHECKPOINT_ALLOWED = NO`, `PN02_PROVIDER_RUN_AUTHORIZED = NO`.

**STOP:** `GRAPH_RAG_PN02DB0CB_REMEDIATION_CYCLE_2_READY_FOR_CODEX_REREVIEW`. No
checkpoint; no Codex re-review in this turn.

---

# Codex Re-Review #2 Remediation Cycle #3

Codex re-review #2 of the cycle-2 remediation returned **decision B — REMEDIATION
REQUIRED, 0 HIGH / 1 MEDIUM / 0 LOW**: both cycle-2 HIGH findings (exact-tag baseline,
real preflight fail-closed + 3 independent signals) were confirmed PASS, and one new
MEDIUM remained. This cycle targets ONLY that MEDIUM; no other area was reopened, and no
B0C-A history was rewritten. Codex's sandbox again could not run tests — re-review #2 was
static.

## B0CB-RR2-M1 — B1-R2 checkpoint identity carried but not validated — **REMEDIATED**

- **Root cause:** `OperatorRunGrant.b1_r2_checkpoint` was parsed and copied into
  `LiveProviderRunAuthorization` but never validated — `mint_live_provider_run_authorization`
  and `validate_live_run_inputs` checked run_id / fixture / git baseline / provider
  fingerprint / Boundary B / allowlist / caps, but not that the B1-R2 checkpoint was
  non-empty or matched an approved, independently-observed identity. The mint/validation
  API would have accepted an arbitrary or empty B1-R2 marker.
- **Trust model (Git-backed, not a self-asserted string):** a new
  `B1R2CheckpointAttestation` carries OBSERVED Git state for the operator-approved B1-R2
  tag — whether it resolves to an actual **tag object** (`refs/tags/<name>`, so a branch
  name or raw SHA does not qualify), the commit it **peels** to, and the current HEAD.
  `attest_b1_r2_checkpoint` fails closed unless: a genuine attestation object (not a
  string/dict), a non-sentinel approved identity, the observed tag matches the approved
  identity, the tag exists, and its peel equals the observed HEAD. `read_b1_r2_checkpoint`
  observes this from real Git (injectable `git_runner` for tests). The mint requires it
  BEFORE minting; the CLI validates it as defense-in-depth; a direct mint bypassing the
  CLI still fails.
- **Temporal correctness / no circular dependency:** `PN02D-B1-R2 = NOT_STARTED`, so NO
  approved B1-R2 tag exists yet — the real reader observes no such tag and the mint fails
  closed. No future tag name is invented or hardcoded (no prefix matching); the approved
  identity is an operator input independently verified against Git. B0C-B implements the
  MECHANISM; it stays unsatisfied until the future B1-R2 phase creates an approved tag.
  `_B1_R2_SENTINELS` explicitly rejects ``""``/`NOT_STARTED`/`NOT_AUTHORIZED`/`TBD`/
  `PENDING`/`FUTURE`/`UNKNOWN`/`NONE`/`SIMULATION`/`OFFLINE`/`DRYRUN`/`PLACEHOLDER`/
  `B1-R2-NOT-STARTED`.
- **Files:** `authmintlivepn02d.py` (`B1R2CheckpointAttestation`,
  `attest_b1_r2_checkpoint`, `B1R2CheckpointError`, mint gate + `b1_r2_checkpoint_attested`
  evidence), `cli_live_pn02d.py` (`read_b1_r2_checkpoint`, `B1R2CheckpointReader`, CLI
  validation), `driver_live_pn02d.py` (`RealB1Driver.run` threads the attestation into
  the mint).
- **Tests (mint-level, real attest, + CLI + current-repo governance):** positive
  future-simulation (synthetic approved tag observed at HEAD → mints); missing / empty /
  whitespace / sentinel (incl. `B1-R2-NOT-STARTED`) → refused; grant tag not observed in
  Git (current reality) → refused; tag not at HEAD → refused; observed-identity mismatch →
  refused; B0C-A tag substituted (peels elsewhere) → refused; dict look-alike and bare
  string attestation → refused; CLI with the REAL reader (no B1-R2 tag) → refused before
  binding; and a governance test proving the CURRENT repo cannot attest a B1-R2 checkpoint
  (`observed_tag_exists = False`). (`test_b1r2_*`, `test_cli_b1_r2_*`,
  `test_current_repo_cannot_attest_b1_r2_checkpoint`, `test_current_governance_flags_unchanged`.)
- **Result:** `B0CB_RR2_M1_REMEDIATED = YES`,
  `B1_R2_CHECKPOINT_IDENTITY_VALIDATION_IMPLEMENTED = YES`,
  `CLI_B1_R2_CHECKPOINT_VALIDATION = PASS`, `MINT_B1_R2_CHECKPOINT_VALIDATION = PASS`,
  `UNVERIFIED/MISSING/EMPTY/SENTINEL/WRONG_B1_R2_IDENTITY_CAN_MINT_AUTH = NO`,
  `UNTAGGED_B1_R2_STATE_CAN_MINT_AUTH = NO`, `B0CA_TAG_CAN_SUBSTITUTE_FOR_B1_R2 = NO`,
  `B1_R2_IDENTITY_SELF_ASSERTION_TRUSTED = NO`, `B1_R2_CHECKPOINT_AVAILABLE = NO`,
  `LIVE_PROVIDER_AUTHORIZATION_CURRENTLY_MINTABLE = NO`,
  `B1_R2_FUTURE_POSITIVE_SIMULATION = PASS`. DESIGN IMPACT: REFINEMENT.

## Preserved (not reopened)
All Codex re-review #2 PASS areas remain PASS (exact-tag, real preflight fail-closed + 3
signals, fixture binding, clean-tree, two-boot, corpus cleanup, index allowlist, vector
prefilter/cosine/K3K5, delete isolation, stale backstop, live-entrypoint-cannot-self-
authorize, secret/DB/prod isolation). INDEX_CLIENT_DEVIATION = ACCEPTABLE_REFINED_
IMPLEMENTATION; GD_CLIENT_DEVIATION = ACCEPTABLE_IMPLEMENTATION_DETAIL. B0C-A checkpoint
document unchanged; no B1-R2 approval implied.

## Verification (Claude, zero-provider — supplementary, not a Codex substitute)
102 B0C-B tests pass (82 + 20 new cycle-3 tests); ruff PASS; mypy 0 errors in changed
modules (5 pre-existing `concurrency_diag08` baseline findings only). Governance retained:
`CODEX_INITIAL_REVIEW = FAIL`, `CODEX_REREVIEW_1 = FAIL (A)`, `CODEX_REREVIEW_2 = FAIL (B,
1 MEDIUM)`, `CODEX_REREVIEW_3 = NOT_RUN`, `B0C_B_CHECKPOINT_ALLOWED = NO`,
`PN02D_B1_R2 = NOT_STARTED`, `PN02_PROVIDER_RUN_AUTHORIZED = NO`.

**STOP:** `GRAPH_RAG_PN02DB0CB_REMEDIATION_CYCLE_3_READY_FOR_CODEX_REREVIEW`. No
checkpoint; no Codex re-review in this turn.

---

# Codex Re-Review #3 Remediation Cycle #4

Codex re-review #3 of the cycle-3 remediation returned **decision A — FAIL, 1 HIGH / 0
MEDIUM / 0 LOW**: the cycle-2 fixes stayed PASS, but the cycle-3 B1-R2 fix was
INSUFFICIENT — the MEDIUM escalated to **HIGH B0CB-RR3-H1 (CONTRADICTION)**. This cycle
targets ONLY that finding; no other area was reopened and no prior-cycle history was
rewritten. Codex's sandbox again could not run tests — re-review #3 was static.

## B0CB-RR3-H1 — B1-R2 trust originated from caller-created data — **REMEDIATED**

- **Finding / severity:** `B0CB-RR3-H1`, **HIGH** (design impact: CONTRADICTION → the
  cycle-3 mechanism contradicted the NOT_STARTED fail-closed invariant).
- **Root cause:** trust originated from a **caller-created attestation object**.
  `B1R2CheckpointAttestation` was a public, self-constructable `@dataclass(frozen=True)`;
  `attest_b1_r2_checkpoint` merely checked fields on the caller's object (an
  `isinstance` on a public dataclass is not an unforgeability boundary); and
  `mint_live_provider_run_authorization` accepted that caller-supplied attestation AND a
  caller-supplied approved identity (`operator_grant.b1_r2_checkpoint`). So caller-
  controlled data crossed the trust boundary.
- **Real exploit condition (reproduced, then blocked):** the existing approved B0C-A tag
  `graphrag-pn02db0ca-real-provider-wiring-design-approved` is a REAL tag that peels to
  the current HEAD `cb766883…`. A caller could set the grant's B1-R2 identity to the
  B0C-A tag and hand the mint a TRUTHFUL self-constructed attestation (peel == head ==
  real HEAD) — minting a live authorization while `PN02D-B1-R2 = NOT_STARTED`. An
  adversarial probe confirmed this against the pre-fix code (`VULNERABLE`), and confirms
  the post-fix code blocks every attacker strategy (`ALL BLOCKED`). The cycle-3 test
  masked this by faking a WRONG B0C-A peel; the real peel is HEAD.
- **Design correction (trust model changed, not patched):** trust now originates from a
  **TRUSTED READER invoked INSIDE the mint boundary**, never from caller data.
  - The mint no longer accepts a B1-R2 attestation parameter at all. It accepts an
    (injected, real-by-default) `trusted_b1_r2_reader` and **independently invokes it**.
  - `TrustedB1R2Observation` is an unforgeable, `__slots__`, module-private-key-minted
    type (same pattern as `LiveProviderRunAuthorization`); `RealTrustedB1R2Reader` is
    the ONLY producer (a direct construction raises `PermissionError`). A self-
    constructed public look-alike can never cross the boundary
    (`SELF_CONSTRUCTED_PUBLIC_DATACLASS_CAN_AUTHORIZE = NO`).
  - The approved-EXPECTED B1-R2 identity is **governance-sourced**
    (`current_approved_b1_r2_checkpoint()` — `None` while NOT_STARTED), NOT the grant and
    NOT a caller default. Real callers (driver `run`, CLI) pass neither the reader nor an
    approved identity, so the mint uses its real-Git reader + governance `None` and
    **fails closed** regardless of any caller input. The reader/approved parameters exist
    ONLY as a documented test-injection seam (§6).
  - **Exact approved identity (§8):** the grant's B1-R2 identity must EXACTLY equal the
    approved-expected identity; the approved-expected identity must be DISTINCT from the
    implementation checkpoint AND from a small exact-string SAFETY denylist of known-
    non-B1-R2 checkpoints (the B0C-A design tag). No prefix/regex/`contains`/naming
    heuristic is used as proof of approval.
  - **Baseline binding (§10):** the trust-observed HEAD must equal the observed tag peel
    AND the approved Git baseline `head_commit`/`tag_peel_commit`.
- **Files:** `authmintlivepn02d.py` (removed `B1R2CheckpointAttestation` +
  `attest_b1_r2_checkpoint`; added `TrustedB1R2Observation`, `TrustedB1R2Reader`,
  `RealTrustedB1R2Reader`, `current_approved_b1_r2_checkpoint`, `verify_b1_r2_checkpoint`,
  `_GOVERNANCE_B1_R2`, `_KNOWN_NON_B1_R2_CHECKPOINTS`; mint owns the trusted read),
  `cli_live_pn02d.py` (removed `read_b1_r2_checkpoint`/`B1R2CheckpointReader`; CLI uses
  the trusted reader + governance as defense-in-depth, task §14),
  `driver_live_pn02d.py` (`RealB1Driver.run` no longer takes a caller attestation; forwards
  trust seams ONLY when injected).
- **Tests (real mint boundary, direct — bypassing the CLI):** twelve direct-mint
  adversarial cases (§12) incl. the REAL B0C-A-at-HEAD substitution (both governance-
  default and caller-injected-approved variants), arbitrary/future real tags on the
  default path, sentinels, reader-observed-wrong-tag, correct-tag-wrong-peel, correct-
  peel-wrong-baseline-HEAD, reader-observes-no-tag, untagged state, look-alike observation
  object, and valid-grant-but-governance-trust-source; the removed-attestation-kwarg
  `TypeError`; the unforgeable-observation `PermissionError`; exact-identity /
  implementation-checkpoint-distinct binding; the current-repo governance test (real
  reader + real verifier fail closed); and the REPLACEMENT future-positive test that
  exercises the REAL mint path with an injected TRUSTED reader over a scripted git
  boundary (NOT a hand-built attestation). (`test_b1r2_*`, `test_trusted_observation_*`,
  `test_cli_b1_r2_*`, `test_current_repo_cannot_verify_b1_r2_checkpoint`.)
- **Result:** `B0CB_RR3_H1_REMEDIATED = YES`,
  `TRUSTED_B1_R2_READER_IN_MINT_BOUNDARY = YES`,
  `CALLER_SUPPLIED_B1_R2_ATTESTATION_ACCEPTED = NO`,
  `SELF_CONSTRUCTED_PUBLIC_DATACLASS_CAN_AUTHORIZE = NO`,
  `DIRECT_MINT_B1_R2_BYPASS = NO`,
  `REAL_B1_R2_TAG_EXISTENCE_VERIFIED_AT_MINT = YES`,
  `EXACT_APPROVED_B1_R2_IDENTITY_REQUIRED = YES`,
  `B1_R2_BOUND_TO_APPROVED_GIT_BASELINE = YES`,
  `B0CA_TAG_CAN_SUBSTITUTE_FOR_B1_R2 = NO`,
  `ARBITRARY_REAL_TAG_AT_HEAD_CAN_AUTHORIZE = NO`,
  `B1_R2_IDENTITY_SELF_ASSERTION_TRUSTED = NO`,
  `ALL_LIVE_MINT_PATHS_REQUIRE_TRUSTED_B1_R2_READ = YES`,
  `B1_R2_CHECKPOINT_AVAILABLE = NO`,
  `LIVE_PROVIDER_AUTHORIZATION_CURRENTLY_MINTABLE = NO`,
  `B1_R2_FUTURE_POSITIVE_TEST_QUALITY = PASS`. DESIGN IMPACT: the B1-R2 trust MODEL
  changed (as Codex required); the surrounding B0C-A real-wiring design is unchanged.

## Preserved (not reopened)
All Codex re-review #2 PASS areas and the cycle-3 PASS areas remain PASS (fixture-hash
binding, exact B0C baseline tag attestation, clean-tree attestation, run_id binding,
provider fingerprint binding, provider-free preflight + three independent version signals
+ failed-preflight fail-closed, two-boot lifecycle, corpus cleanup, index fixture
allowlist, index completion, GD provenance, vector prefilter-before-ranking + K3/K5 one
ranking, delete isolation, stale-evidence backstop, live-entrypoint-cannot-self-authorize,
secret/normal-DB/production-eval isolation). INDEX_CLIENT_DEVIATION =
ACCEPTABLE_REFINED_IMPLEMENTATION; GD_CLIENT_DEVIATION = ACCEPTABLE_IMPLEMENTATION_DETAIL.
B0C-A checkpoint document unchanged; no B1-R2 approval implied.

## Verification (Claude, zero-provider — supplementary, not a Codex substitute)
114 B0C-B tests pass (adapters + live); PN02 + graphrag regression unchanged from the
prior baseline (`NEW_REGRESSIONS = 0`); ruff PASS; mypy 0 errors in changed modules (5
pre-existing `concurrency_diag08` baseline findings only). Adversarial probe: every
attacker strategy (default-governance-B0CA, inject-approved-B0CA, forge-observation,
lying-look-alike-reader) BLOCKED. Governance retained: `CODEX_INITIAL_REVIEW = FAIL`,
`CODEX_REREVIEW_1 = FAIL (A)`, `CODEX_REREVIEW_2 = FAIL (B, 1 MEDIUM)`,
`CODEX_REREVIEW_3 = FAIL (A, 1 HIGH)`, `CODEX_REREVIEW_4 = NOT_RUN`,
`B0C_B_CHECKPOINT_ALLOWED = NO`, `PN02D_B1_R2 = NOT_STARTED`,
`PN02_PROVIDER_RUN_AUTHORIZED = NO`.

**STOP:** `GRAPH_RAG_PN02DB0CB_REMEDIATION_CYCLE_4_READY_FOR_CODEX_REREVIEW`. No
checkpoint; no Codex re-review in this turn.

---

# Codex Re-Review #4 Remediation Cycle #5

Codex Independent Re-Review #4 of the cycle-4 remediation returned **decision A — FAIL, 1
HIGH / 0 MEDIUM / 0 LOW** (`B0CB-RR4-H1`, DESIGN IMPACT = CONTRADICTION). Codex confirmed
cycle-4 fixed most axes (no caller-supplied attestation object accepted; unforgeable
`TrustedB1R2Observation`; real-Git reader; exact identity; B0C-A denylist with a
real-condition test; baseline binding; governance `None`; no heuristic/hardcoded tag; all
preflight/data-path/regression PASS; PROD_IMPORTS/migrations/secret/DB PASS) — BUT the mint
STILL exposed two caller-controlled trust roots as public kwargs
(`trusted_b1_r2_reader`, `approved_expected_b1_r2_checkpoint`), and `RealB1Driver.run`
forwarded them. A direct caller could pass a matching approved string + a
`RealTrustedB1R2Reader(git_runner=…)` and mint without a genuine approved B1-R2 checkpoint.
This cycle targets ONLY that finding; Codex's sandbox again could not run tests (uv cache
os error 183) — its re-review was static.

## B0CB-RR4-H1 — Test-injection seam was production-callable — **REMEDIATED**

- **Finding / severity:** `B0CB-RR4-H1`, **HIGH** (DESIGN IMPACT = CONTRADICTION → the
  documented "test-only seam" was reachable by production/live-callable code, so a live
  authorization was still mintable while B1-R2 = NOT_STARTED).
- **Root cause:** the trust-root injection parameters — intended for tests — were part of
  the PUBLIC mint and driver signatures with no enforcement that only tests use them. Any
  caller could supply both trust roots and bypass governance `None`.
- **Design correction (both seams removed from the public/live API):**
  - `mint_live_provider_run_authorization` DROPPED `trusted_b1_r2_reader` and
    `approved_expected_b1_r2_checkpoint`. It now resolves BOTH trust roots INTERNALLY via
    a new `b1_r2_refusal_reasons(operator_grant, git_baseline)`, which reads the
    governance identity from `current_approved_b1_r2_checkpoint()` (module-level) and
    builds the reader from `_build_trusted_b1_r2_reader()` (module-level, default
    `RealTrustedB1R2Reader` over real Git). Neither is a parameter. While governance is
    `None` the mint fails closed regardless of any caller.
  - `RealB1Driver.run` DROPPED both parameters and forwards nothing to the mint.
  - CLI `validate_live_run_inputs` / `evaluate_execute_b1_live` DROPPED both parameters and
    call the same internal `b1_r2_refusal_reasons` for defense-in-depth — the CLI can
    neither override the approved identity nor the trusted reader.
  - `b1_r2_refusal_reasons` returns refusal reasons only; it CANNOT mint a capability. The
    pure validator `verify_b1_r2_checkpoint` is retained (it takes reader/identity but only
    returns reasons and is never fed caller data by any live mint path — used internally by
    the resolver and directly in unit tests).
  - **Testability without a public seam (§8/§14):** tests simulate a FUTURE approval by
    patching the two module-level functions (`current_approved_b1_r2_checkpoint`,
    `_build_trusted_b1_r2_reader`) at the module boundary — `common.approved_b1r2_governance`
    context manager — then invoke the SAME production mint signature live code uses. No
    public/live trust parameter and no `mint_for_test`/`unsafe` helper was introduced.
- **Files:** `authmintlivepn02d.py` (removed the two mint params + the `_GOVERNANCE_B1_R2`
  sentinel; added `_build_trusted_b1_r2_reader` + public `b1_r2_refusal_reasons`; mint
  gate 6b now calls the resolver), `driver_live_pn02d.py` (removed the two `run` params +
  the forwarding block + the `TrustedB1R2Reader` import), `cli_live_pn02d.py` (removed the
  two params from both functions; uses `b1_r2_refusal_reasons`), `tests/graphrag_pn02db0cb_common.py`
  (replaced `b1r2_sim_kwargs` with the `approved_b1r2_governance` patch context;
  `mint_test_live_auth` wraps the production mint in it).
- **Tests (production signature + module-internal patching + validator units):** mint and
  `RealB1Driver.run` signatures assert-free of the trust params (`inspect.signature`); the
  mint rejects the old `trusted_b1_r2_reader` / `approved_expected_b1_r2_checkpoint` /
  `b1_r2_checkpoint_attestation` keywords (TypeError); the CLI rejects the old keywords;
  direct mint with current governance (no patch) fails closed; B0C-A / arbitrary real tag /
  future-looking tag named in the grant fail closed on the default path; the
  caller-controlled-reader attack has no public entry (TypeError + default-path fail-closed);
  the future-positive path uses the PRODUCTION mint signature under the governance patch;
  a patched-future-governance run with a wrong-peel reader still fails; plus the validator
  units (denylist / wrong-tag / wrong-peel / wrong-baseline / no-tag / untagged / look-alike
  / grant-mismatch / impl-checkpoint / sentinel).
- **Result:** `B0CB_RR4_H1_REMEDIATED = YES`,
  `PUBLIC_MINT_APPROVED_IDENTITY_INJECTION = ABSENT`,
  `PUBLIC_MINT_TRUSTED_READER_INJECTION = ABSENT`,
  `REAL_B1_DRIVER_APPROVED_IDENTITY_INJECTION = ABSENT`,
  `REAL_B1_DRIVER_TRUSTED_READER_INJECTION = ABSENT`,
  `LIVE_CALLER_CAN_CONTROL_GIT_READER = NO`, `DIRECT_MINT_B1_R2_BYPASS = NO`,
  `CALLER_CONTROLLED_READER_ATTACK = BLOCKED`, `CURRENT_GOVERNANCE_NONE_FAILS_CLOSED = PASS`,
  `B0CA_TAG_CAN_SUBSTITUTE_FOR_B1_R2 = NO`, `OLD_TRUST_INJECTION_KEYWORDS_ACCEPTED = NO`,
  `REAL_B1_DRIVER_OLD_TRUST_INJECTION_KEYWORDS_ACCEPTED = NO`,
  `ALL_LIVE_MINT_PATHS_USE_INTERNAL_GOVERNANCE = YES`,
  `ALL_LIVE_MINT_PATHS_USE_INTERNAL_REAL_READER = YES`,
  `CLI_CAN_OVERRIDE_APPROVED_B1_R2_IDENTITY = NO`,
  `CLI_CAN_OVERRIDE_TRUSTED_B1_R2_READER = NO`,
  `B1_R2_FUTURE_POSITIVE_TEST_USES_PRODUCTION_SIGNATURE = YES`,
  `B1_R2_FUTURE_POSITIVE_TEST_QUALITY = PASS`,
  `LIVE_PROVIDER_AUTHORIZATION_CURRENTLY_MINTABLE = NO`. DESIGN IMPACT: the injection seams
  were removed from the public API (as Codex required); the B1-R2 trust logic and the
  surrounding B0C-A real-wiring design are unchanged.

## Preserved (not reopened)
All Codex re-review #4 PASS areas remain PASS (unforgeable observation, real-Git reader,
exact identity, B0C-A denylist with the real-condition test, baseline binding, governance
`None`, no heuristic/hardcoded tag, fixture/run_id/provider/baseline/clean-tree/allowlist/
budget/ordering authorization checks, provider-free preflight + three signals + fail-closed,
two-boot lifecycle, corpus cleanup, index allowlist, index completion, GD provenance, vector
prefilter-before-ranking + K3/K5 one ranking, delete isolation, stale-evidence backstop,
live-entrypoint-cannot-self-authorize, secret/normal-DB/production-eval isolation).
INDEX_CLIENT_DEVIATION = ACCEPTABLE_REFINED_IMPLEMENTATION; GD_CLIENT_DEVIATION =
ACCEPTABLE_IMPLEMENTATION_DETAIL. B0C-A checkpoint document unchanged; no B1-R2 approval implied.

## Verification (Claude, zero-provider — supplementary, not a Codex substitute)
116 B0C-B tests pass (adapters + live); PN02 + GraphRAG regression `NEW_REGRESSIONS = 0`;
ruff PASS; mypy 0 errors in changed modules (5 pre-existing `concurrency_diag08` findings
only). Adversarial probe: the injection parameters are absent from both the mint and
`RealB1Driver.run` signatures; the default (governance) path fails closed
(`B1R2CheckpointError`); passing the old keywords raises `TypeError`. Governance retained:
`CODEX_INITIAL_REVIEW = FAIL`, `CODEX_REREVIEW_1 = FAIL (A)`, `CODEX_REREVIEW_2 = FAIL (B)`,
`CODEX_REREVIEW_3 = FAIL (A)`, `CODEX_REREVIEW_4 = FAIL (A, 1 HIGH)`,
`CODEX_REREVIEW_5 = NOT_RUN`, `B0C_B_CHECKPOINT_ALLOWED = NO`, `PN02D_B1_R2 = NOT_STARTED`,
`PN02_PROVIDER_RUN_AUTHORIZED = NO`.

**STOP:** `GRAPH_RAG_PN02DB0CB_REMEDIATION_CYCLE_5_READY_FOR_CODEX_REREVIEW`. No
checkpoint; no Codex re-review in this turn.

---

# Codex Re-Review #5 (PASS-C) Minimal Cleanup Cycle #6

Codex Independent Re-Review #5 of the cycle-5 remediation returned the **FIRST passing
verdict — decision C — PASS_WITH_LOW_FINDINGS (0 HIGH / 0 MEDIUM / 1 LOW)**. B0CB-RR4-H1
is confirmed fixed; B0C-B is technically checkpoint-ready pending operator approval. This
cycle removes ONLY the remaining LOW test-precision gap. **No production logic, signature,
trust boundary, or authorization architecture was changed.** Codex's sandbox again could
not run tests (uv cache os error 183) — its re-review was static.

## B0CB-RR5-L1 — Driver legacy-kwarg TypeError not explicitly runtime-tested — **REMEDIATED (test-only)**

- **Finding / severity:** `B0CB-RR5-L1`, **LOW** (DESIGN IMPACT = NONE). Codex confirmed
  `RealB1Driver.run` has a closed keyword-only signature with no `**kwargs`
  (`driver_live_pn02d.py`), so stale callers passing a removed trust keyword already raise
  `TypeError` — and the signature is asserted in tests. The gap was purely that, unlike the
  mint and CLI paths, there was no EXPLICIT `pytest.raises(TypeError)` runtime negative test
  for the driver.
- **Fix (test-only):** added one parametrized runtime negative test —
  `test_real_b1_driver_run_rejects_legacy_trust_kwargs_at_runtime` — that calls the REAL
  `RealB1Driver.run` callable with the valid required arguments plus each removed legacy
  keyword (`trusted_b1_r2_reader`, `approved_expected_b1_r2_checkpoint`,
  `b1_r2_checkpoint_attestation`) and asserts `TypeError`. The error is raised at CALL
  BINDING, before any coroutine runs — so no mint, no provider call, no runtime boot, no
  backend/DB effect. The existing `inspect.signature` assertion is retained.
- **Files:** `tests/test_graphrag_pn02db0cb_adapters.py` (ONE new parametrized test). NO
  production source changed this cycle.
- **Result:** `B0CB_RR5_L1_REMEDIATED = YES`, `PRODUCTION_CODE_CHANGED_CYCLE_6 = NO`,
  `REAL_B1_DRIVER_EXPLICIT_OLD_KWARG_TYPEERROR_TEST = PASS`,
  `REAL_B1_DRIVER_TRUSTED_READER_OLD_KWARG_RUNTIME_REJECTION = PASS`,
  `REAL_B1_DRIVER_APPROVED_IDENTITY_OLD_KWARG_RUNTIME_REJECTION = PASS`.

## Preserved (not reopened)
Every Codex re-review #5 PASS property is unchanged (both trust-root injections ABSENT from
mint/driver/CLI, governance + real reader resolved internally, module-monkeypatch not a
live seam, helper cannot mint, no secondary mint path, current-governance fail-closed,
B0C-A/arbitrary-tag cannot authorize, caller-controlled-reader attack blocked, all
authorization/preflight/data-path regressions PASS, PROD_IMPORTS/migrations/secret/DB
clean). No trust-boundary or authorization behavior touched.

## Verification (Claude, zero-provider — supplementary, not a Codex substitute)
119 B0C-B tests pass (116 + 3 new parametrized cases); PN02 + GraphRAG regression
`NEW_REGRESSIONS = 0`; ruff PASS; mypy unchanged (no production edit). Governance retained:
`CODEX_INITIAL_REVIEW = FAIL`, `CODEX_REREVIEW_1 = FAIL (A)`, `CODEX_REREVIEW_2 = FAIL (B)`,
`CODEX_REREVIEW_3 = FAIL (A)`, `CODEX_REREVIEW_4 = FAIL (A, 1 HIGH)`,
`CODEX_REREVIEW_5 = PASS (C, 1 LOW)`, `CODEX_REREVIEW_6 = NOT_RUN`,
`B0C_B_CHECKPOINT_ALLOWED = NO`, `PN02D_B1_R2 = NOT_STARTED`,
`PN02_PROVIDER_RUN_AUTHORIZED = NO`.

**STOP:** `GRAPH_RAG_PN02DB0CB_REMEDIATION_CYCLE_6_READY_FOR_CODEX_REREVIEW`. No
checkpoint; no Codex re-review in this turn.
