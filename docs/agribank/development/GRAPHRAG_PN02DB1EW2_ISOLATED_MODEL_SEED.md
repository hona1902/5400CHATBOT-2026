# GraphRAG-PN02D-B1-EW2 — Isolated Embedding-Model Seed for Real B1 Execution

**Status:** `IMPLEMENTED_READY_FOR_CODEX_REVIEW` — offline code remediation. No provider
traffic, no mint, no Docker boot, no Git checkpoint. Worktree intentionally DIRTY; HEAD
remains `1b8ca5b6420e2fa6240cfa97aba2fcbfb222c29e`.

## 1. Why this phase exists

The first operator-authorized PN02D-B1 real-provider execution was correctly **BLOCKED
before any provider traffic** by source verification: the committed EW1 real-seams
execution path enters a fresh isolated Surreal namespace but never seeds the embedding
`Model` / `DefaultModels.default_embedding_model` that the normal embedding stack requires.

The committed real path would therefore deterministically fail at:

```
run_live_b1_execution
  → isolated_surreal_eval_runtime(run_id)     # fresh temp namespace: schema only, NO models
  → RealB1Driver.run
  → RealPN02CorpusProvisioner.provision
  → _embed_source (build_in_isolation_corpus_seams)
  → commands.embedding_commands.embed_source_command
  → open_notebook.utils.embedding.generate_embeddings
  → model_manager.get_embedding_model()  →  None
  → ValueError("No embedding model configured. Please configure one in the Models section.")
```

The real frozen-model attestor (`realseamspn02d.build_real_model_attestor`) reads the same
empty isolated state and would also fail the frozen match.

### Defect reproduced offline (provider-free)

Against a fresh `isolated_surreal_eval_runtime()` (temp namespace `graphrag_eval_*`, normal
DB untouched), before any seed:

- `DefaultModels.default_embedding_model` == `None`
- `model_manager.get_embedding_model()` == `None`
- `generate_embeddings([...])` raises `ValueError: No embedding model configured …`
  (raised **before** any provider HTTP — `get_embedding_model()` returns `None` first)

`EW2_DEFECT_REPRODUCED = YES`.

## 2. Full model-resolution forensic

| Required state | Schema/field | Consumer | Owner |
|---|---|---|---|
| A. Corpus source embedding | a `model` row (`type=embedding`, `name`, `provider`) + `DefaultModels.default_embedding_model` bound to its id | `embed_source_command` → `generate_embeddings` → `model_manager.get_embedding_model()` | isolated namespace (per-run) |
| B. Query embedding | same as A | `build_real_query_embed_fn` → `generate_embedding` → same manager | same seeded model |
| C. Frozen-model attestation | same `model` row read back | `build_real_model_attestor` → `DefaultModels` → `Model.get` → provider+name | same seeded model |

A **single** seed (frozen `Model` + bound default) satisfies A, B, and C. There is no env
fallback for the model id: `model_manager.get_embedding_model()` reads only the DB
`DefaultModels.default_embedding_model`; `isolated_surreal_eval_runtime` overrides
`SURREAL_NAMESPACE`/`SURREAL_DATABASE` process-wide to the temp namespace (bootstraps schema
only — "does NOT create Sources, call providers…"), so every read resolves against the empty
temp namespace.

## 3. GraphRAG-08 seed forensic & reuse decision

> **Historical forensic — describes the pre-EW2 GraphRAG-08 baseline** that motivated extracting
> the shared helper. The `precheck08.seed_temp_embedding_model` / `restore_default_and_delete_model`
> wrappers named below were **removed in Cycle #4**; both paths now enter the shared
> `seeded_frozen_embedding_model` context manager. For the current architecture see §4/§15.

`precheck08.seed_temp_embedding_model` (wired via `live_orchestrator08`) already seeds a temp
embedding model INSIDE the GraphRAG-08 isolation: it creates
`Model(name="openai/text-embedding-3-small", provider="openrouter", type="embedding",
credential=None)`, binds `DefaultModels.default_embedding_model`, and returns
`(model_id, prior)`; `restore_default_and_delete_model` restores + deletes. **The seed itself
makes zero provider calls** (the `_embedding_dim_probe` that does call the provider is a
separate orchestrator step, not part of the seed).

`PRECHECK08_SEED_REUSABLE_AS_IS = NO` — semantically correct but (a) it hardcodes the identity
literals instead of deriving from the frozen constants, (b) it has no conflict/idempotency
policy, and (c) it lives in a GraphRAG-08 module. Decision: **extract one shared provider-free
helper** and have BOTH paths use it (`MODEL_SEED_SCHEMA_SINGLE_SOURCE_OF_TRUTH = YES`).

## 4. Implementation

### New shared helper — `open_notebook/integrations/graphrag/eval/isolated_model_seed.py`

Evaluation-only, provider-free. Single source of truth for the frozen embedding `Model`
written into an active isolated namespace. Identity derived from the same constants the
runtime binding and attestor validate against:
`FROZEN_EMBEDDING_MODEL` (`provider_binding08`) + `FROZEN_EMBEDDING_PROVIDER`
(`vectoradapterpn02d`) + `type="embedding"`.

> **Current shape (final, after Cycle #6).** This subsection describes the CURRENT executable
> source. Cleanup authority is collapsed into the private context-manager lifecycle: there is
> **no** exported cleanup function, **no** module-level teardown primitive, **no** ownership
> handle/token/dataclass, **no** module-reachable construction key, and **no** caller-controlled
> isolation-bypass parameter — nothing a caller could supply, forge, retarget, or use to skip
> the guard. The intermediate designs that got here (a forgeable handle in Cycle #4, a
> module-level `_teardown_owned_seed` in Cycle #4, a `require_isolation` switch through Cycle #5)
> are recorded as **historical/superseded** in §12–§14; do not treat them as current.

- `seeded_frozen_embedding_model()` — the **only** public entrypoint, taking **no parameters**
  (no `require_isolation` / bypass switch of any kind): an async context manager that yields the
  resolved frozen model id (an IDENTIFIER for legitimate use / reporting, **not** cleanup
  authority) and owns the entire teardown itself. All cleanup state (created model id, prior
  default, whether the default was changed, and the isolation identity) is held as **private
  locals of the context manager** and can never be supplied, forged, or retargeted by a caller.
  It calls `isolation08.require_active_isolation()` **UNCONDITIONALLY as its first statement,
  before any `DefaultModels`/`Model` access**, so the create/bind boundary can never seed/mutate
  the normal application namespace (a legacy `require_isolation=` kwarg is rejected with
  `TypeError` at call binding, before any DB access). It then applies the **conflict policy
  (§15)**: default unset → CREATE + bind; already the EXACT frozen identity → REUSE (idempotent,
  no create/no teardown); a DIFFERENT model bound → `ConflictingEmbeddingModelError`
  (fail-closed). No embedding request, no network, no credential stored (env-key fallback at
  use time).
- `_cleanup_owned_seed()` — a **nested zero-argument closure defined inside**
  `seeded_frozen_embedding_model` (NOT a module-level function; nothing importable to call). The
  CM's `finally` invokes it only for a model this scope created; it reads the owned model id,
  prior default and creation-time isolation identity purely from the enclosing invocation's
  **lexical locals**. Before any mutation it re-verifies active isolation, the isolation identity
  (no cross-namespace teardown), that the current default is still the owned model, and that the
  target still matches the frozen identity — otherwise it RAISES. It restores the prior default
  FIRST, then deletes the owned model (a delete never runs after a failed restore). A reused
  pre-existing exact-match model is left intact.

### Wiring — `realseamspn02d.run_live_b1_execution`

The private composition helper takes a `model_seed` context-manager factory (default = the
real shared seed, entered via `_default_model_seed` → `seeded_frozen_embedding_model`).
Ordering (§9/§23):

```
isolation entered  <  frozen embedding model seeded  <  real seams built  <  RealB1Driver.run
```

The seed lives INSIDE the isolation (temp namespace only) and OUTSIDE the seams build, so the
corpus embedder, query embedder, and model attestor all resolve the seeded default. A
controlled offline test injects a no-op isolation + no-op model seed to exercise the
composition with zero provider traffic and no DB.

`MODEL_SEED_OWNER = run_live_b1_execution` (owns the isolated-namespace lifecycle).

### Single source of truth — GraphRAG-08 (`precheck08`, orchestrators)

**Current shape (final, after Cycle #6).** `precheck08` no longer defines its own
`seed_temp_embedding_model` / `restore_default_and_delete_model` wrappers (removed in Cycle #4);
it **imports and enters the same shared `seeded_frozen_embedding_model` context manager** via an
`AsyncExitStack`, so the seed lifecycle (and its cleanup authority) is the single source of
truth and cannot diverge. The enter is immediately followed by a `try` whose `finally` closes
the stack — there is no enter→cleanup gap (RR4-L1, closed in Cycle #5). The GraphRAG-08
orchestrators (`live_orchestrator08`, `burst_runner08`) enter the CM through a single
`OrchestratorDeps.model_seed_cm` factory (there is **no** `model_seeder`/`model_restorer` raw
`(model_id, prior)` state pair, and no raw cleanup-state threading). None of these call sites
passes any isolation-bypass argument.

The normal embedding stack (`embed_source_command → generate_embeddings → model_manager`) is
UNCHANGED — the fix seeds the required DB state, it does not bypass the manager.

## 5. Verification (offline, provider-free)

> **Historical — initial-cycle (Cycle #0) verification snapshot.** The test counts and
> regression totals below are the numbers from the FIRST implementation pass and are superseded
> by the per-cycle verification in §9–§15 (current EW2 suite = 36 tests; latest regression =
> 1188 passed / 9 skipped / 0 failed). Retained for audit history.

- **New EW2 tests** (`tests/test_graphrag_pn02db1ew2.py`, 13): frozen identity single-source;
  conflict policy (create / exact-match reuse / fail-closed mismatch); active-isolation guard;
  provider-free create; `precheck08` delegation; run_live ordering; real default seed identity;
  governance current-is-EW2 + EW2-tag-absent fail-closed; and one **live-gated** end-to-end
  test proving the defect reproduces before the seed and is removed after it (resolution +
  real attestor `matches_frozen` dim 1536 + corpus provider-boundary sentinel + teardown), with
  a DUMMY non-secret key and an autouse socket sentinel (no external network).
- **Provider network sentinel** (§24): any non-loopback connect fails the test.
- **EW1 composition tests** updated to inject the no-op model seed.
- **Regression:** full graphrag suite `1165 passed / 9 skipped / 0 failed` (baseline
  `1152/9/0` + 13 new EW2 tests; the 4 governance-current test files had their `current ==
  EW1` assertions retargeted to `EW2`, no count change); ruff clean; mypy clean on changed
  production modules (the 5 pre-existing `concurrency_diag08.py` `object`-attr errors are
  untouched — none in changed modules). Fixture hash / run_id / provider fingerprint / caps /
  concurrency unchanged.

## 6. Checkpoint continuity (§31–§34)

`CURRENT_CHECKPOINT_MUST_PEEL_TO_CURRENT_HEAD = YES` — the mint requires the approved tag to
peel to HEAD (`authmintlivepn02d` baseline attestation: `head == approved_commit == tag_peel`).
The EW2 production change moves HEAD past `1b8ca5b`, so the EW1 tag no longer peels to the
authorized HEAD and a **successor checkpoint is required**.

Following the B1-R2 → PF1 → EW1 precedent, governance is repointed to the successor:

- **NEW** `EXPECTED_EW2_CHECKPOINT_TAG = "graphrag-pn02db1ew2-isolated-model-seed-approved"`
  is the frozen approved identity (`_APPROVED_B1_R2_CHECKPOINT` /
  `B1_R2_EXPECTED_CHECKPOINT_TAG`). Its annotated tag does **not** exist yet.
- EW1, PF1, B1-R2 remain **HISTORICAL** identities that can never substitute.
- The EW2 tag is absent in real Git → the trusted reader observes it absent → the live mint
  **fails closed** (`b1_r2_tag_not_observed_in_git`). `LIVE_PROVIDER_AUTHORIZATION_CURRENTLY_
  MINTABLE = NO` (also enforced by the dirty tree). Not tested by minting.

Historical tags remain immutable: EW1 → `1b8ca5b`, PF1 → `082dc95`, B1-R2 → `611532c`.
`SUCCESSOR_CHECKPOINT_TAG_CURRENTLY_EXISTS = NO`.

## 7. Adversarial self-review (§36)

1. Fresh isolated namespace obtains the exact frozen model before the driver needs it? **YES.**
2. `model_manager.get_embedding_model()` resolves it? **YES** (live-proven).
3. Real attestor reads the SAME state? **YES** (`matches_frozen`, dim 1536, live-proven).
4. Name/dimension mismatch silently accepted? **NO** (conflict policy fail-closed; attestor
   reports dim 0 on a non-frozen name).
5. Seed state leaks into normal DB / process state after teardown? **NO** (isolation guard +
   restore/delete; namespace drop removes the row).
6. Bypassed the normal embedding stack? **NO** (delegates to `model_manager`).
7. Any provider call? **NO** (`EXTERNAL_PROVIDER_NETWORK_CALLS = 0`).
8. B1 science / run envelope changed? **NO** (run_id / fixture / caps / concurrency frozen).
9. Production-code commit requires successor EW2 checkpoint? **YES.**

## 8. Next steps (NOT this turn)

Mandatory independent **Codex** review of the final diff, then a separate operator-approved
EW2 checkpoint (create the annotated tag peeling to the EW2 HEAD, BACKUP-only), then a fresh
operator authorization to retry PN02D-B1 real provider execution.

## 9. Codex Independent Review #1 + Remediation Cycle #1

**Codex Independent Review #1 = `A_FAIL_HIGH_FINDINGS`** (2 HIGH, 0 MEDIUM, 1 LOW) — this
verdict is permanent history; it is not rewritten as passing after remediation.

| Finding | Severity | Status |
|---|---|---|
| B1EW2-R1-H1 — exported low-level seed writer not isolation-guarded | HIGH | OPEN → REMEDIATED |
| B1EW2-R1-H2 — public live composition exposes `model_attestor` trust-root override | HIGH | OPEN → REMEDIATED |
| B1EW2-R1-L1 — stale "EW1 successor" wording in comments/docstrings | LOW | OPEN → REMEDIATED |

### H1 — guard the low-level writer at the mutation boundary
`isolated_model_seed.create_frozen_embedding_model_and_bind` now calls the canonical
`isolation08.require_active_isolation()` **FIRST — before any `DefaultModels` read, `Model`
construction/save, or default binding**. A caller outside `isolated_surreal_eval_runtime`
fails closed with **zero Model rows created and DefaultModels untouched**. The
`seeded_frozen_embedding_model` context manager retains its own guard (intentional
defense-in-depth). `precheck08.seed_temp_embedding_model` still delegates to the writer and
its GraphRAG-08 call site enters `isolated_surreal_eval_runtime` first, so it is unaffected.
New negative tests: `test_low_level_writer_outside_isolation_fails_closed` (asserts the raise
+ no Model/DefaultModels touched) and `test_precheck08_seed_outside_isolation_fails_closed`.

### H2 — remove the trust-root override from the public live surface
- **`build_real_b1_live_seams`**: the `model_attestor` parameter is **removed**. The builder
  always constructs the genuine `build_real_model_attestor` internally; a legacy
  `model_attestor=` kwarg now raises `TypeError` (`test_builder_rejects_model_attestor_override`).
  Tests that need a controlled attestor patch the module-level `build_real_model_attestor`.
- **`run_live_b1_execution`** (the ONLY live-callable entrypoint, used by `execute-b1-live`):
  reduced to the production signature `{operator_grant, git_baseline_attestation,
  observed_fixture_hash, env}`. It exposes **no** `model_attestor`, `model_seed`, `isolation`,
  `seams_builder`, or `builder_kwargs`. It delegates to a new **private, non-live**
  `_run_live_b1_execution_composed` that carries the composition seams for controlled offline
  tests. The production CLI never calls the private helper.
  `PUBLIC_LIVE_TRUST_ROOT_OVERRIDES = 0`; `MODEL_ATTESTOR_OVERRIDE_VIA_KWARGS = IMPOSSIBLE`.
  New tests: `test_public_run_live_has_no_composition_or_trust_root_params` and a parametrized
  `test_public_run_live_rejects_injection_kwargs` (model_attestor / model_seed / isolation /
  seams_builder / builder_kwargs all → `TypeError` at call binding, before isolation/mint/boot).

**builder_kwargs forensic (§18).** The private helper still forwards `builder_kwargs` to the
builder for offline tests. Every remaining override is an **IO / DB / testability boundary**,
not a trust root: Docker/process/health/version/port/storage seams, HTTP transports,
`corpus_seams`/`repo_query`/`member_row_fetcher`, `notebook_record_ids`, `lightrag_api_key`
(local sidecar auth, not the provider secret), `present_secret_envs`, `corpus_teardown`, and
`query_embed_fn`. **`query_embed_fn` (§20):** caller injection cannot bypass a security control
— the (now non-injectable) frozen-model attestor runs BEFORE it and the 1536-dim check runs on
its result, and query-embedding budget is metered by the orchestrator, not the fn; retained as
a private-helper-only IO seam. **HTTP transports (§21):** protocol validation, operation
allowlist, budget, and workspace scoping stay in the adapters/orchestrator; retained as
private-helper-only IO seams. No checkpoint / trusted-Git-reader / provider-fingerprint /
fixture / authorization authority is caller-overridable on any public surface.

### L1 — wording
Corrected the stale "EW1 successor" phrasing to "EW2 successor" across the live control-plane
docstrings/comments (`authmintlivepn02d.py`, `authb1r2pn02d.py`, `cli_live_pn02d.py`,
`driver_live_pn02d.py`). Executable behavior unchanged; EW1 remains the immutable historical
checkpoint, EW2 the current expected successor.

### Verification
Full graphrag regression **1174 passed / 9 skipped / 0 failed** (1165 + 9 new H1/H2 tests;
`NEW_REGRESSIONS = 0`); ruff clean; mypy 0 new errors (the 5 pre-existing
`concurrency_diag08.py` errors are untouched). B1 run envelope unchanged; governance still
EW2 and fail-closed (EW2 tag absent); historical tags immutable. `EXTERNAL_PROVIDER_NETWORK_
CALLS = 0`; no real secret used; no mint; HEAD still `1b8ca5b` (uncommitted).
`GRAPH_RAG_PN02DB1EW2 = REMEDIATION_1_COMPLETE_READY_FOR_CODEX_REREVIEW`. A mandatory Codex
re-review is the next turn; the EW2 checkpoint remains **not allowed** until it passes.

## 10. Codex Re-Review #1 + Remediation Cycle #2

**Codex Re-Review #1 = `A_FAIL_HIGH_FINDINGS`** (2 new HIGH, 0 MEDIUM, 1 residual LOW) — the
original H1/H2 were closed for the surfaces fixed in Cycle #1, but the re-review found two
adjacent HIGH surfaces and L1 was still open. This verdict is permanent history.

| Finding | Severity | Status |
|---|---|---|
| B1EW2-RR1-H1 — cleanup writer `restore_default_and_delete` not isolation-guarded | HIGH | OPEN → REMEDIATED |
| B1EW2-RR1-H2 — public `evaluate_execute_b1_live` exposes `git_baseline_reader`/`fixture_hash_reader`/`live_runner` trust-root overrides | HIGH | OPEN → REMEDIATED |
| B1EW2-R1-L1 — residual stale "EW1 successor" wording | LOW | OPEN → REMEDIATED |

### RR1-H1 — guard the cleanup writer (symmetric with the create writer)
`isolated_model_seed.restore_default_and_delete` now calls `require_active_isolation()`
**FIRST — before any `DefaultModels` read/write or `Model` lookup/delete** — placed OUTSIDE
the best-effort try/except so a misuse outside isolation fails closed loudly; the legitimate
seed-teardown path runs inside the still-active isolation context (the seed CM's `finally`
executes while the isolation context is still open), so it passes. Ownership stays load-bearing
at the `seeded_frozen_embedding_model` context manager, which invokes cleanup ONLY for a model
it created (`if created:`), never on exact-match reuse (`CLEANUP_ONLY_MUTATES_SEED_OWNED_STATE
= YES`). New tests: `test_cleanup_writer_outside_isolation_fails_closed` (raises + `DefaultModels`
and `Model` spies asserted never called) and `test_cm_runs_cleanup_on_exception_for_created_seed`.

### RR1-H2 — split the public CLI evaluator
`cli_live_pn02d.evaluate_execute_b1_live` is reduced to the production signature
`{manifest_path, explicit_authorize, env}` — it exposes **no** `git_baseline_reader`,
`fixture_hash_reader`, or `live_runner` override and resolves all three INTERNALLY (canonical
`read_git_baseline`, `verify_fixture_hash`, `_default_live_runner`). It delegates to a new
**private, non-live** `_evaluate_execute_b1_live_composed` that carries those injectable
dependencies for controlled offline tests. `cmd_execute_b1_live` (the production CLI verb) calls
the public evaluator unchanged. `PUBLIC_CLI_EVALUATOR_TRUST_ROOT_OVERRIDES = 0`;
`CLI_TRUST_ROOT_OVERRIDE_VIA_KWARGS = IMPOSSIBLE` (no `**kwargs` on either function). New tests:
`test_public_cli_evaluator_has_no_trust_root_overrides` and a parametrized
`test_public_cli_evaluator_rejects_reader_and_runner_overrides` (git_baseline_reader /
fixture_hash_reader / live_runner → `TypeError` at call binding, before git validation /
isolation / mint / boot). All prior tests that injected these seams now call the private helper.

**Combined with Cycle #1**, the full public live trust-root matrix is now zero caller-overridable:
model attestor, model seed, checkpoint identity, trusted Git reader, git runner, fixture reader,
live runner, provider fingerprint, authorization authority — all resolved internally
(`PUBLIC_LIVE_TRUST_ROOT_OVERRIDES = 0`). Retained private-helper-only IO seams (query_embed_fn,
HTTP transports, corpus seams) are unchanged and remain non-trust-root.

### L1 — wording (fully closed)
Both residual spots fixed: `authmintlivepn02d.py` PF1 comment now says the current identity is
`EXPECTED_EW2_CHECKPOINT_TAG`; the `cli_live_pn02d` evaluator docstring (rewritten in the H2
split) references the "trust-observed EW2 checkpoint". A full scan of the four control-plane
files shows `RESIDUAL_STALE_EW1_SUCCESSOR_WORDING = NONE`.

### Verification
Full graphrag regression **1180 passed / 9 skipped / 0 failed** (1174 + 6 new RR1 tests;
`NEW_REGRESSIONS = 0`); ruff
clean; mypy 0 new errors (the 5 pre-existing `concurrency_diag08.py` errors untouched). Original
EW2 blocker still removed (live end-to-end); B1 run envelope unchanged; governance still EW2 and
fail-closed (EW2 tag absent); historical tags immutable. `EXTERNAL_PROVIDER_NETWORK_CALLS = 0`;
no real secret; no mint; HEAD still `1b8ca5b` (uncommitted). Review history retained:
`CODEX_B1EW2_REVIEW_1 = A_FAIL`, `CODEX_B1EW2_REREVIEW_1 = A_FAIL`.
`GRAPH_RAG_PN02DB1EW2 = REMEDIATION_2_COMPLETE_READY_FOR_CODEX_REREVIEW_2`; the EW2 checkpoint
remains **not allowed** until Codex Re-Review #2 passes.

## 11. Codex Re-Review #2 + Remediation Cycle #3

**Codex Re-Review #2 = `A_FAIL_HIGH_FINDINGS`** (1 new HIGH). RR1-H1's isolation guard, RR1-H2,
and L1 were all confirmed CLOSED, but the cleanup writer's **ownership** (which Cycle #2 had
left as "isolation-bounded but not ownership-bounded") was escalated to a new HIGH. Permanent
history retained: Review #1 = A, Re-Review #1 = A, Re-Review #2 = A.

| Finding | Severity | Status |
|---|---|---|
| B1EW2-RR2-H1 — exported cleanup writer accepts an arbitrary caller-supplied model id + prior default; enforces isolation but not ownership | HIGH | OPEN → REMEDIATED |

### RR2-H1 — make cleanup ISOLATION-bound + OWNERSHIP-bound + STATE-bound
The seed lifecycle is now **capability-bound**:

- `create_frozen_embedding_model_and_bind()` returns an **unforgeable** `SeededEmbeddingModelHandle`
  (constructed only via a module-private key; a direct construction raises `PermissionError`)
  instead of a raw `(model_id, prior)` tuple. The handle carries the owned model id, the prior
  default to restore, and the **isolation identity** (namespace/database) it was created under.
- The raw-id cleanup writer `restore_default_and_delete(model_id, prior)` is **removed** (a raw
  model id is an identifier, not authority). The only cleanup entrypoint is
  `restore_seeded_embedding_model(handle)`, which fails closed — in order, before any mutation —
  on: (1) non-handle argument, (2) a spent handle (single-use, no replay), (3) inactive
  isolation, (4) a handle whose isolation identity ≠ the active one (no cross-namespace reuse),
  (5) the current default no longer being the owned seeded model, and (6) the target model no
  longer matching the frozen identity. Only then does it restore the prior default (first) and
  delete the owned model. Ownership/security violations RAISE (never swallowed); only the
  post-verification DB writes are best-effort.
- The `seeded_frozen_embedding_model` context manager tears down via the handle (created path
  only); an exact-match reuse holds no handle and is left intact.
- `precheck08` (`seed_temp_embedding_model` → returns the handle; `restore_default_and_delete_model`
  → takes the handle) and the GraphRAG-08 orchestrators (`live_orchestrator08`, `burst_runner08`
  — `OrchestratorDeps.model_seeder`/`model_restorer`) thread the handle through instead of the
  `(model_id, prior)` tuple. `MODEL_SEED_CLEANUP_SINGLE_SOURCE_OF_TRUTH = YES`.

Result: `PUBLIC_ARBITRARY_CLEANUP_WRITER = ABSENT`, `RAW_MODEL_ID_CONFERS_CLEANUP_AUTHORITY = NO`,
`CALLER_CAN_FORGE_CLEANUP_OWNERSHIP = NO`, `CLEANUP_CANNOT_DELETE_UNRELATED_ISOLATED_MODEL = YES`,
`OWNERSHIP_ENFORCED_BY_API = YES`.

### Tests
New/updated ownership suite (`tests/test_graphrag_pn02db1ew2.py`): handle not forgeable;
no public raw-id writer + non-handle rejected; restore outside isolation fails closed (no DB
access); normal success (owned deleted + prior restored); current-default-changed fail-closed
(no delete); mutated-target-identity fail-closed (no delete); cross-namespace fail-closed;
single-use replay rejected; CM runs ownership-bound cleanup on exception; preexisting exact-match
reuse not deleted. GraphRAG-08 orchestrator test fakes (`08e4`, `08e7b`, `08d`) updated to the
handle contract.

### Verification
Full graphrag regression **1187 passed / 9 skipped / 0 failed** (`NEW_REGRESSIONS = 0`); ruff
clean; mypy 0 new errors (5 pre-existing `concurrency_diag08.py` untouched). Prior closures
retained; original EW2 blocker still removed (live end-to-end); governance still EW2 fail-closed
(EW2 tag absent); historical tags immutable; B1 run envelope unchanged; zero provider activity;
HEAD still `1b8ca5b` (uncommitted).
`GRAPH_RAG_PN02DB1EW2 = REMEDIATION_3_COMPLETE_READY_FOR_CODEX_REREVIEW_3`; the EW2 checkpoint
remains **not allowed** until Codex Re-Review #3 passes.

## 12. Codex Re-Review #3 + Remediation Cycle #4

**Codex Re-Review #3 = `A_FAIL`** on the same ownership/trust-boundary class: an exported
structured capability (`SeededEmbeddingModelHandle`) constructed via a module-private key is
still, in Python, forgeable/retargetable by reflection (the construction key and the dataclass
fields are reachable in-process). Permanent history retained: Review #1 = A, Re-Review #1 = A,
Re-Review #2 = A, Re-Review #3 = A.

| Finding | Severity | Status |
|---|---|---|
| B1EW2-RR3-H1 — exported cleanup-authority capability object is not unforgeable in Python (construction key + fields reflection-reachable) | HIGH | OPEN → REMEDIATED |

### Operator design decision (Cycle #4)
**Stop hardening a Python capability object to be cryptographically/reflection-proof** (an
unwinnable goal in-process). **Instead, COLLAPSE CLEANUP AUTHORITY INTO THE PRIVATE
LIFECYCLE**: expose no public/exported structured ownership object whose fields can be forged
or retargeted.

### RR3-H1 — private-lifecycle collapse
- `SeededEmbeddingModelHandle`, the `_SEED_OWNERSHIP_KEY` construction key, the public
  `create_frozen_embedding_model_and_bind`, and the public `restore_seeded_embedding_model` are
  **removed** (`__all__` no longer exports any create/restore authority).
- The **only** public surface is the `seeded_frozen_embedding_model` async context manager. It
  yields the frozen model id as an identifier and holds all cleanup state (created id, prior
  default, whether it created, and the `(namespace, database)` isolation identity) as **private
  locals**. On `__aexit__` (created path only) it calls the **private** `_teardown_owned_seed`
  with that private state — there is no cleanup surface a caller can reach, forge, or retarget.
  `_teardown_owned_seed` re-verifies active isolation, the isolation identity (no cross-namespace
  teardown), that the current default is still the owned model, and the frozen identity BEFORE
  any mutation (else RAISES), then restores the prior default FIRST and deletes (never deletes
  after a failed restore). An exact-match reuse creates nothing and is left intact.
- Legacy GraphRAG-08 orchestration now enters the CM directly: `OrchestratorDeps` carries a
  single `model_seed_cm: Callable[[], AbstractAsyncContextManager[object]]` (replacing the
  `model_seeder`/`model_restorer` pair); `live_orchestrator08` and `burst_runner08` enter it via
  an `AsyncExitStack` and `aclose()` it in `finally`. `precheck08` imports and enters the SAME
  CM via an `AsyncExitStack` (its own `seed_temp_embedding_model` / `restore_default_and_delete_model`
  wrappers are removed) — `MODEL_SEED_CLEANUP_SINGLE_SOURCE_OF_TRUTH = YES`.

Result: `CAPABILITY_FORGERY_SURFACE = ABSENT`, `PUBLIC_CLEANUP_AUTHORITY_OBJECT = NONE`,
`RAW_MODEL_ID_CONFERS_CLEANUP_AUTHORITY = NO`, `CALLER_CAN_FORGE_OR_RETARGET_CLEANUP = NO`
(nothing to forge), `CLEANUP_CANNOT_DELETE_UNRELATED_ISOLATED_MODEL = YES`,
`CLEANUP_AUTHORITY_OWNER = private context-manager lifecycle`.

### Tests
The EW2 suite (`tests/test_graphrag_pn02db1ew2.py`, 33) drops the handle-forgery tests
(object no longer exists) and instead proves **`test_capability_forgery_surface_absent`**
(no `SeededEmbeddingModelHandle` / `create_frozen_embedding_model_and_bind` /
`restore_seeded_embedding_model` / `restore_default_and_delete` / `_SEED_OWNERSHIP_KEY` /
`SeedOwnershipError`; `__all__` exports no create/restore authority). It exercises the CM
conflict policy (create / exact-match reuse no-teardown / fail-closed mismatch), the CM
isolation guard (raises before any DB access), and drives the private `_teardown_owned_seed`
directly for: normal restore-before-delete success, current-default-changed fail-closed (no
delete), mutated-identity fail-closed (no delete), cross-namespace fail-closed (before DB),
outside-isolation fail-closed (before DB), **restore-failure → no delete**, and **delete-failure
→ prior already restored**; plus the CM running teardown from private state on an in-scope
exception. GraphRAG-08 orchestrator/launcher fakes (`08e4`, `08e5`, `08e7b`, `08d`) updated to
the `model_seed_cm` context-manager contract; `test_precheck08_uses_shared_seed_context_manager`
proves the single-source delegation.

### Verification
Full graphrag regression **1185 passed / 9 skipped / 0 failed** (`NEW_REGRESSIONS = 0`; net
−2 vs Cycle #3's 1187 because the forgeable-handle suite is replaced by the smaller
private-teardown suite per the operator decision — no failures); ruff clean on all changed
files; mypy 0 new errors in changed modules (the 5 pre-existing `concurrency_diag08.py`
`object`-attr errors are untouched baseline). Prior closures (H1/H2, RR1-H1/H2, RR2-H1
ownership) retained; original EW2 blocker still removed (live end-to-end passes); governance
still EW2 and fail-closed (EW2 tag absent); historical tags immutable (EW1 `1b8ca5b`, PF1
`082dc95`, B1-R2 `611532c`); B1 run envelope unchanged; `EXTERNAL_PROVIDER_NETWORK_CALLS = 0`;
no real secret read/logged; no mint; HEAD still `1b8ca5b` (uncommitted). Review history
retained: `CODEX_B1EW2_REVIEW_1 = A_FAIL`, `CODEX_B1EW2_REREVIEW_1 = A_FAIL`,
`CODEX_B1EW2_REREVIEW_2 = A_FAIL`, `CODEX_B1EW2_REREVIEW_3 = A_FAIL`.
`GRAPH_RAG_PN02DB1EW2 = REMEDIATION_4_COMPLETE_READY_FOR_CODEX_REREVIEW_4`; the EW2 checkpoint
remains **not allowed** until Codex Re-Review #4 passes. Codex is **not** invoked this turn.

## 13. Codex Re-Review #4 + Remediation Cycle #5

**Codex Re-Review #4 = `B_REMEDIATION_REQUIRED`** (HIGH = 0, MEDIUM = 1, LOW = 1) — the **first
non-`A` verdict** in the chain. The recurring HIGH cleanup-ownership/trust-boundary class
(RR3-H1) is closed: the forgeable capability object is gone (`OLD_CAPABILITY_PRODUCTION_
REFERENCES = 0`, `CAPABILITY_FORGERY_SURFACE = ABSENT`, `EQUIVALENT_DESTRUCTIVE_API_REINTRODUCED
= NO`). Permanent history retained: Review #1 = A, Re-Review #1 = A, Re-Review #2 = A,
Re-Review #3 = A, **Re-Review #4 = B**.

| Finding | Severity | Status |
|---|---|---|
| B1EW2-RR4-M1 — cleanup authority not truly lexical: module-level `_teardown_owned_seed(...)` remains directly callable and accepts reconstructable cleanup state (blast radius limited to the same active isolation) | MEDIUM | OPEN → REMEDIATED |
| B1EW2-RR4-L1 — `precheck08` enters the seed CM before the cleanup-owning `try/finally`, leaving an exception window where `aclose()` may be skipped | LOW | OPEN → REMEDIATED |

### RR4-M1 — collapse teardown into a lexical closure (no module-level primitive)
The module-level `_teardown_owned_seed(created_model_id, prior_default, owner_namespace,
owner_database)` function is **removed entirely**. The destructive teardown is now a
**zero-argument closure defined inside** `seeded_frozen_embedding_model` (`_cleanup_owned_seed`)
that reads the owned model id, prior default, `created` flag, and creation-time
`(namespace, database)` identity from that invocation's **lexical locals** — it takes no
parameters, is never returned/yielded/exported/registered/attached, and the CM's `__aexit__`
invokes it on the created path only. Because there is nothing importable to call and the closure
accepts no arguments, an in-process caller cannot supply, forge, retarget, or reconstruct cleanup
authority even from observable DB state/constants. All existing gates are preserved verbatim
inside the closure (active isolation FIRST → creation-identity binding → current-default owned →
frozen identity → restore-before-delete). Results: `MODULE_LEVEL_DESTRUCTIVE_SEED_TEARDOWN =
ABSENT`, `PRIVATE_TEARDOWN_SYMBOL_PRESENT = NO`, `PRIVATE_TEARDOWN_DIRECTLY_CALLABLE = NO`,
`CLEANUP_AUTHORITY_TRULY_LEXICAL = YES`, `LEXICAL_CLEANUP_CLOSURE_ARGUMENT_COUNT = 0`,
`LEXICAL_CLEANUP_CLOSURE_EXPORTED = NO`, `CALLER_CAN_SYNTHESIZE_DESTRUCTIVE_CLEANUP_STATE = NO`,
`RAW_MODEL_ID_CONFERS_CLEANUP_AUTHORITY = NO`, `RAW_PRIOR_DEFAULT_CONFERS_REBIND_AUTHORITY = NO`,
`YIELDED_MODEL_ID_CONFERS_CLEANUP_AUTHORITY = NO`.

### RR4-L1 — close the precheck08 enter→cleanup gap
Both `precheck08` seed sites (`run_micro_precheck`, `run_full_benchmark`) now open a
`try:` **immediately** after `await seed_stack.enter_async_context(seeded_frozen_embedding_model())`,
whose **outer `finally`** performs the single `seed_stack.aclose()`. The dim probe / service /
runner construction that previously sat in the un-`try`'d gap are inside that outer `try`, so an
exception there can no longer skip `aclose()`. The runner-cleanup `finally` (which needs
`runner` to exist) stays nested where `runner` is defined; the seed `aclose()` runs exactly once
(no double close). `PRECHECK_SEED_STACK_CLEANUP_GUARANTEED_AFTER_ENTRY = YES`,
`PRECHECK_ENTER_TO_CLEANUP_GAP = ABSENT`, `PRECHECK_DOUBLE_STACK_CLOSE = NO`.

### Tests
The EW2 suite (`tests/test_graphrag_pn02db1ew2.py`, 34 — 32 offline + 2 live-gated) now drives
**every** cleanup scenario THROUGH the public CM against a coherent in-memory model DB
(`_fake_model_db`): normal restore-before-delete, changed-default fail-closed (new default
preserved, no delete), mutated-identity fail-closed, cross-namespace fail-closed, **restore
failure → no delete**, **delete failure → prior already restored**, and CM-exception teardown —
`TESTS_CALL_MODULE_LEVEL_TEARDOWN = NO`. `test_capability_forgery_surface_absent` additionally
asserts `_teardown_owned_seed` is absent and that no module-level cleanup coroutine remains. For
RR4-L1: a deterministic **AST** test proves every precheck08 seed enter is immediately followed
by a `try` whose `finally` closes `seed_stack`, plus a **live-gated functional** test that raises
in the former gap (the dim probe) and asserts the real seed CM teardown still ran
(`temp_model_cleanup_ok`).

### Verification
Full graphrag regression **1186 passed / 9 skipped / 0 failed** (`NEW_REGRESSIONS = 0`; net +1
vs Cycle #4's 1185 — one direct-teardown test removed, two RR4-L1 tests added); ruff clean on all
changed files; mypy 0 new errors in changed modules (the 5 pre-existing `concurrency_diag08.py`
`object`-attr errors are the untouched baseline). Prior closures (H1/H2, RR1-H1/H2, RR2-H1,
RR3-H1) retained; original EW2 blocker still removed (live end-to-end passes); governance still
EW2 and fail-closed (EW2 tag absent); historical tags immutable (EW1 `1b8ca5b`, PF1 `082dc95`,
B1-R2 `611532c`); B1 run envelope unchanged; `EXTERNAL_PROVIDER_NETWORK_CALLS = 0`; no real
secret read/logged; no mint; HEAD still `1b8ca5b` (uncommitted). Review history retained:
`CODEX_B1EW2_REVIEW_1 = A_FAIL`, `CODEX_B1EW2_REREVIEW_1 = A_FAIL`, `CODEX_B1EW2_REREVIEW_2 =
A_FAIL`, `CODEX_B1EW2_REREVIEW_3 = A_FAIL`, `CODEX_B1EW2_REREVIEW_4 = B_REMEDIATION_REQUIRED`.
`GRAPH_RAG_PN02DB1EW2 = REMEDIATION_5_COMPLETE_READY_FOR_CODEX_REREVIEW_5`; the EW2 checkpoint
remains **not allowed** until Codex Re-Review #5 passes. Codex is **not** invoked this turn.

## 14. Codex Re-Review #5 + Remediation Cycle #6

**Codex Re-Review #5 = `A_FAIL_HIGH_FINDINGS`** (HIGH = 1, MEDIUM = 0, LOW = 0). Both Cycle #5
findings are **CLOSED** (RR4-M1 lexical cleanup; RR4-L1 precheck ExitStack gap), but Codex
found the **create-path twin** of the isolation-guard issue. Permanent history retained:
Review #1 = A, Re-Review #1 = A, Re-Review #2 = A, Re-Review #3 = A, Re-Review #4 = B,
**Re-Review #5 = A**.

| Finding | Severity | Status |
|---|---|---|
| B1EW2-RR5-H1 — public create/bind isolation-guard bypass: `seeded_frozen_embedding_model(require_isolation=False)` reaches DefaultModels/Model.save/default-bind outside active isolation (reopened the R1-H1 class) | HIGH | OPEN → REMEDIATED |

### RR5-H1 — remove the caller-controlled isolation bypass
The `require_isolation` parameter is **removed entirely** from `seeded_frozen_embedding_model`
(no default-True, no rename, no private alias, no `**kwargs`, no options object). The context
manager now takes **no parameters** and calls `isolation08.require_active_isolation()`
**UNCONDITIONALLY** as its first statement, before any `DefaultModels`/`Model` access — so the
create/bind boundary can never touch the normal application namespace, and a legacy
`require_isolation=False` call is rejected at call binding (`TypeError`) before any DB access.
Results: `PUBLIC_REQUIRE_ISOLATION_PARAMETER = ABSENT`, `CALLER_CONTROLLED_ISOLATION_BYPASS =
ABSENT`, `REQUIRE_ISOLATION_VIA_KWARGS = IMPOSSIBLE`, `CREATE_WRITER_REQUIRES_ACTIVE_ISOLATION =
YES`, `CREATE_GUARD_PRECEDES_DB_ACCESS = YES`.

**Scope note.** The finding and this remediation are scoped to the seed create/bind boundary
(`seeded_frozen_embedding_model`). A separate `require_isolation` parameter exists on
`concurrency_diag08.run_sweep` (the 08E concurrency-diagnostic sweep, a different subsystem and
pre-existing baseline, never the model-seed mutation boundary); it is **out of RR5-H1 scope**
and is intentionally left untouched (no opportunistic refactor of unrelated baseline).
`PRODUCTION_ISOLATION_DISABLE_SWITCHES` on the seed create/bind boundary = **0**.

### Tests
The seed is now called with **no arguments** everywhere (production and tests); unit tests that
must run the body without a live isolated runtime patch `require_active_isolation` in
`isolated_model_seed` (via `_fake_model_db`) rather than disabling production safety
(`TESTS_USE_PRODUCTION_ISOLATION_BYPASS = NO`, `TEST_PATCHES_REAL_GUARD_BOUNDARY = YES`). Two new
regressions: `test_seed_signature_has_no_isolation_bypass_parameter` (signature takes no params;
none of require_isolation/skip_isolation/allow_unisolated/bypass_guard/unsafe/test_mode present)
and `test_seed_rejects_legacy_require_isolation_kwarg_before_any_db_access` (`require_isolation=
False` → `TypeError`, with Model/DefaultModels spies proving zero DB access before rejection).
The existing outside-isolation and guard-before-DB tests now call the no-arg CM.

### Verification
Full graphrag regression **1188 passed / 9 skipped / 0 failed** (`NEW_REGRESSIONS = 0`; net +2
vs Cycle #5's 1186 — the two new RR5-H1 regression tests); ruff clean on changed
files; mypy 0 new errors in the changed module (the 5 pre-existing `concurrency_diag08.py`
`object`-attr errors are the untouched baseline). Cycle #5 closures retained (cleanup lexical,
no module-level teardown, precheck gap absent, restore-before-delete, failure semantics); prior
closures (H1/H2, RR1-H1/H2, RR2-H1, RR3-H1, RR4-M1, RR4-L1) retained; original EW2 blocker still
removed (live end-to-end passes); governance still EW2 and fail-closed (EW2 tag absent);
historical tags immutable (EW1 `1b8ca5b`, PF1 `082dc95`, B1-R2 `611532c`); B1 run envelope
unchanged; `EXTERNAL_PROVIDER_NETWORK_CALLS = 0`; no real secret read/logged; no mint; HEAD still
`1b8ca5b` (uncommitted). Review history retained: `CODEX_B1EW2_REVIEW_1 = A_FAIL`,
`CODEX_B1EW2_REREVIEW_1..3 = A_FAIL`, `CODEX_B1EW2_REREVIEW_4 = B_REMEDIATION_REQUIRED`,
`CODEX_B1EW2_REREVIEW_5 = A_FAIL`.
`GRAPH_RAG_PN02DB1EW2 = REMEDIATION_6_COMPLETE_READY_FOR_CODEX_REREVIEW_6`; the EW2 checkpoint
remains **not allowed** until Codex Re-Review #6 passes. Codex is **not** invoked this turn.

## 15. Codex Re-Review #6 + Remediation Cycle #7 (documentation-only)

**Codex Re-Review #6 = `C_PASS_WITH_LOW_FINDINGS`** (HIGH = 0, MEDIUM = 0, LOW = 1) — the **first
PASS** in the EW2 chain. Codex confirmed the executable state is clean: the create/bind isolation
guard is unconditional (no `require_isolation` / bypass parameter, no `**kwargs`, no factory
bypass; `require_active_isolation()` precedes any DB access with zero pre-guard side-effecting
calls); the cleanup lifecycle is lexical, isolation- + ownership- + state-bound, restore-before-
delete; there is no public cleanup capability/token/raw-id writer and no module-level teardown;
no live trust-root overrides; provider traffic = 0; EW2 tag absent; historical tags immutable.
**B1EW2-RR5-H1 and B1EW2-R1-H1 are CLOSED**, and every prior finding remains CLOSED. Permanent
history retained: Review #1 = A, Re-Review #1 = A, Re-Review #2 = A, Re-Review #3 = A,
Re-Review #4 = B, Re-Review #5 = A, **Re-Review #6 = C**.

| Finding | Severity | Status |
|---|---|---|
| B1EW2-RR6-L1 — architecture doc §4 still showed the pre-Cycle-6 `seeded_frozen_embedding_model(require_isolation=True)` signature and a module-level `_teardown_owned_seed(...)` primitive (documentation-only; **no executable bypass**) | LOW | OPEN → REMEDIATED |

### Remediation Cycle #7 — DOCUMENTATION ONLY
`B1EW2_RR6_L1_REMEDIATED = YES`. §4's "current architecture" description was updated to match the
final Cycle-6 source: `seeded_frozen_embedding_model()` takes **no parameters** and calls
`require_active_isolation()` unconditionally before any DB access; teardown is the nested
zero-argument lexical closure `_cleanup_owned_seed()` (no module-level primitive); the GraphRAG-08
`precheck08` subsection now describes entering the shared CM via `AsyncExitStack` (its old
`seed_temp_embedding_model` / `restore_default_and_delete_model` wrappers were removed in Cycle #4)
with no enter→cleanup gap and no raw `(model_id, prior)` threading. The intermediate designs
(forgeable handle, module-level teardown, `require_isolation` switch) are explicitly marked
**historical/superseded** and preserved in §12–§14 — no audit history was erased.

`PRODUCTION_CODE_CHANGED_IN_CYCLE7 = NO`, `TEST_CODE_CHANGED_IN_CYCLE7 = NO`,
`DOCUMENTATION_ONLY_CHANGE = YES`, `STALE_CURRENT_ARCHITECTURE_REFERENCES = 0`. Governance
unchanged: EW2 tag absent → live mint fail-closed; historical tags immutable (EW1 `1b8ca5b`,
PF1 `082dc95`, B1-R2 `611532c`); B1 run envelope unchanged; `EXTERNAL_PROVIDER_NETWORK_CALLS = 0`;
no real secret; no mint; HEAD still `1b8ca5b` (uncommitted). Review history retained through
`CODEX_B1EW2_REREVIEW_6 = C_PASS_WITH_LOW_FINDINGS`.
`GRAPH_RAG_PN02DB1EW2 = DOC_REMEDIATION_7_COMPLETE_READY_FOR_CODEX_REREVIEW_7`. Because
Re-Review #6 was a PASS, `EW2_CHECKPOINT_REATTEMPT_READY = YES`, but the EW2 checkpoint remains
**not allowed** until a separate explicit operator checkpoint authorization. Codex is **not**
invoked this turn.
