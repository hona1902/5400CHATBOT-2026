# GraphRAG-PN02D-B1-EW1 — Real B1 Execution Seams Wiring

**Phase kind:** OFFLINE implementation. ZERO provider traffic, ZERO real Docker boot, ZERO
normal-DB mutation. NOT checkpointed in this turn; the mandatory Codex independent review is
a separate operator-gated gate that must run before any EW1 checkpoint.

## Discovered missing composition (the blocker this phase resolves)

The PN02D-B1 real provider execution attempt correctly stopped before Docker Boot 1 / mint /
provider-bound boot because the committed application had **no production real-execution
composition path**. `RealB1Driver.run` (the orchestrator) and `LiveB1Seams` (the execution
boundary) both existed, but the only `LiveB1Seams` constructions were **test fakes**
(`tests/graphrag_pn02db0cb_common.build_live_seams`), and the `execute-b1-live` CLI
intentionally returned `REASON_NO_LIVE_SEAMS` even when governance was otherwise satisfied.
`corpuslivepn02d.build_in_isolation_corpus_seams` supplied only 3 of the required seams and
was documented live-only/future.

## Complete `LiveB1Seams` inventory (forensic — 19 fields, 12 required)

`LIVE_B1_SEAMS_FIELD_COUNT = 19` (12 with no default, 7 optional). Each field → the real
production implementation composed by `build_real_b1_live_seams`:

| Field | Real implementation | Kind |
|---|---|---|
| `process_controller` | `cell_provisioner08.DockerCellProcessController` (wrapped by `RecordingProcessController` to capture container names) | reuse + thin wrapper |
| `health_prober` | `HealthProberAdapter(cell_provisioner08.LightRagCellHealthProber)` (maps `version→core_version`, `reported_workspace→workspace`) | reuse + adapter |
| `version_signal_reader` | composed from `DockerCLI.runtime_version(name)` + `image_version_label(image)`, exec-container name resolved from the recording controller | compose |
| `port_allocator` | `cell_provisioner08.EphemeralPortAllocator().allocate(host)` | reuse |
| `storage_dir_allocator` | `realsidecarpn02d.allocate_real_storage_root(base, cell_id)` | reuse |
| `source_creator` / `reference_linker` / `source_embedder` | `corpuslivepn02d.build_in_isolation_corpus_seams(run_id)` (real `Source` create / `RELATE reference` / in-process `embed_source_command`) | reuse |
| `notebook_record_ids` | `{nb.notebook_id: nb.record_id for nb in fx.notebooks}` — PN02-scope-bound; no `notebook` records needed for `RELATE`/membership | compose |
| `query_embed_fn` | wrapper over `utils.embedding.generate_embedding` (routes through the DefaultModels/ModelManager provisioning abstraction — no ad-hoc client) | compose |
| `member_row_fetcher` | `vectoradapterpn02d.build_repo_query_member_row_fetcher(repo_query)` (member-scoped `WHERE source IN $ids` — never a global scan) | reuse |
| `model_attestor` | real reader: `DefaultModels`→`Model.provider/.name` + frozen dim (0 on mismatch — not always-true) | build (real) |
| `preflight_runner` | `runtimelivepn02d.RealProviderFreePreflightRunner(docker)` | reuse |
| `index_transport` / `gd_transport` / `delete_transport` | `None` → the REAL httpx transport inside the adapters (documented real path, not a placeholder) | real-by-design |
| `lightrag_api_key` | LOCAL sidecar auth from `GRAPHRAG_POC_API_KEY` (distinct from the provider credential) | compose |
| `present_secret_envs` | `provbindpn02d.env_present_secret_names({OPENROUTER_API_KEY})` (NAME-only) | compose |
| `corpus_teardown` | `None` — the `isolated_surreal_eval_runtime` context manager owns namespace teardown | real-by-design |

`B1EW1_BLOCKED_MISSING_REAL_SEAM` = NONE — every seam is an approved real component or is
composed from one; no seam required faking.

## Canonical builder + real execution entrypoint

- `open_notebook/integrations/graphrag/eval/realseamspn02d.py`
  - `build_real_b1_live_seams(fx, *, run_id, …)` — the ONE canonical composition. Every
    external edge (Docker/HTTP/DB/provider/isolation) is injectable with a REAL default; a
    controlled offline test replaces ONLY those edges. Composition only — no provider client,
    no Docker orchestration, no scientific logic, no evaluation policy (that stays in
    `RealB1Driver`). `CANONICAL_REAL_SEAMS_BUILDER = ONE`.
  - `run_live_b1_execution(...)` — the real in-process runner. Owns the isolated-namespace
    lifecycle (`isolated_surreal_eval_runtime`, real by default, injectable), builds the real
    seams inside that isolation, and hands them to `RealB1Driver.run`, which OWNS the security
    ordering (provider-free preflight → in-process mint → provider-bound Boot 2 → execute →
    cleanup). `DRIVER_ORCHESTRATION_BYPASSED = NO`. The `LiveProviderRunAuthorization` stays
    IN-MEMORY-EPHEMERAL; nothing is persisted (`AUTHORIZATION_PERSISTENCE_ADDED = NO`,
    `NEW_MIGRATIONS = 0`).
- `open_notebook/integrations/graphrag/eval/cli_live_pn02d.py` — `execute-b1-live` is now the
  canonical real execution entrypoint. All fail-closed gates are preserved (manifest, fixture,
  clean+approved baseline, trust-observed EW1 checkpoint, provider fingerprint, caps/allowlist,
  governance authorization) and a NEW provider-secret-presence gate refuses with
  `provider_secret_missing` BEFORE any Docker boot. Only when every gate passes does it invoke
  the driver-owned execution. `PUBLIC_TRUST_ROOT_INJECTION = ABSENT` (no reader / approved-
  identity parameter on the mint or CLI). `EXECUTE_B1_LIVE_NO_LONGER_HARD_BLOCKED_BY_NO_LIVE_
  SEAMS = YES`.

## Secret model (§4/§18/§36)

- `PROVIDER_SECRET_SOURCE = OPENROUTER_API_KEY` (provider credential): resolved by NAME only;
  the VALUE is inherited late by `DockerCellProcessController` at the sidecar launch boundary,
  never read/printed/logged/serialized/committed here. `PROVIDER_SECRET_CLI_ARGUMENT = ABSENT`.
- `LIGHTRAG_LOCAL_AUTH_SOURCE = GRAPHRAG_POC_API_KEY` (LOCAL sidecar auth): value read into
  memory only to send as a loopback `Authorization` header; never surfaced in payloads/reports.
- `SECRETS_CONFLATED = NO`. `SECRET_SAFE_LOGGING = PASS`.

## Fail-closed missing-secret behaviour

`MISSING_PROVIDER_SECRET_FAILS_CLOSED = PASS`. The CLI refuses at the provider-secret gate
(`provider_secret_missing`) before any runtime boot; `run_live_b1_execution` additionally
fails closed inside the driver via `materialize_provider_binding`
(`REQUIRED_RUNTIME_SECRET_MISSING`). No secret is required to import modules or compose seams.

## Checkpoint-continuity result (§38/§39)

`CURRENT_CHECKPOINT_MUST_PEEL_TO_CURRENT_HEAD = YES` — verified from source:
`attest_approved_clean_baseline` requires `head == approved_commit == tag_peel`, and
`verify_b1_r2_checkpoint` requires `observed_tag_peel == observed_head ==
git_baseline.head_commit`. Therefore `NEW_EW1_COMMIT_INVALIDATES_PF1_CURRENT_HEAD_GATE = YES`:
committing EW1 moves HEAD off `082dc95`, the PF1 tag no longer peels to the authorized HEAD,
and the PF1 Git gate is superseded (`b1_r2_tag_not_at_authorized_head`) — exactly as PF1
superseded B1-R2.

`SUCCESSOR_CHECKPOINT_REQUIRED = YES`,
`SUCCESSOR_CHECKPOINT_TAG = graphrag-pn02db1ew1-real-execution-wiring-approved`,
`SUCCESSOR_CHECKPOINT_TAG_CURRENTLY_EXISTS = NO`,
`LIVE_PROVIDER_AUTHORIZATION_CURRENTLY_MINTABLE = NO`.

Governance repoint (smallest architecture-consistent change; the existing checkpoint-authority
model, no second framework):

- `authmintlivepn02d.EXPECTED_EW1_CHECKPOINT_TAG` is the new successor constant;
  `_APPROVED_B1_R2_CHECKPOINT` / `current_approved_b1_r2_checkpoint()` are repointed to it.
- `EXPECTED_PF1_CHECKPOINT_TAG` is RETAINED as a distinct HISTORICAL constant (peels to
  `082dc95`); `EXPECTED_B1_R2_CHECKPOINT_TAG` remains historical (peels to `611532c`). Neither
  is moved (`HISTORICAL_PF1_TAG_IMMUTABLE = YES`, `HISTORICAL_B1_R2_TAG_IMMUTABLE = YES`).
- `authb1r2pn02d.B1_R2_EXPECTED_CHECKPOINT_TAG` re-exports the new governance identity (EW1).
- The EW1 annotated tag does not exist yet, so the trusted reader observes real Git, finds no
  such tag, and the mint FAILS CLOSED (`b1_r2_tag_not_observed_in_git`). Exact-identity +
  trusted-reader binding remains the primary authority (not a denylist — §42); PF1, B1-R2, and
  arbitrary tags can never substitute (proved by tests).

## Zero-provider test evidence

- `LIVE_SEAMS_COMPLETENESS_TEST = PASS` (dynamic over all `LiveB1Seams` fields).
- `NO_TEST_OR_PLACEHOLDER_SEAMS = PASS`; `PRODUCTION_IMPORTS_TEST_MODULES = NO`.
- `REAL_EXECUTION_CLI_REACHABLE = PASS` (CLI → real builder → `RealB1Driver.run`, COMPLETE).
- `PRODUCTION_COMPOSITION_ORCHESTRATION_TEST = PASS` (real builder + fake external edges +
  no-op isolation; 24 index submits / 26 GD / 1 delete / 21 corpus embeds; two-boot exercised).
- `CLI_MISSING_SECRET_FAIL_CLOSED = PASS` (no runner invoked, no boot).
- `CLI_WRONG_CHECKPOINT_FAIL_CLOSED = PASS`; successor-governance regression (PF1/B1-R2/
  arbitrary cannot substitute; missing EW1 tag fails closed; exact EW1 + peel accepted).
- `OFFLINE_PROVIDER_NETWORK_SENTINEL = PASS` (real provider embedding / real httpx egress
  raise). `SECRET_SAFE_LOGGING = PASS`.
- `EXTERNAL_PROVIDER_NETWORK_CALLS = 0`, `REAL_PROVIDER_EMBEDDINGS = 0`, `REAL_INDEX_OPERATIONS
  = 0`, `REAL_GD_QUERIES = 0`, `REAL_VECTOR_PROVIDER_QUERIES = 0`, `PROVIDER_BOUND_LIGHTRAG_
  BOOT_COUNT = 0`, `NORMAL_DB_MUTATIONS = 0`, `FINAL_ANSWER_CALLS = 0`.

## Frozen B1 envelope (unchanged)

run_id `pn02db1-fe3efb27-e720-48c6-b97e-2c0e6a60969d`; fixture `graphrag_pn02_eval_v1`
(`9ce7df74…6899a6`); provider fingerprint `pbf_1811d0bfd5cfad2743ffaa69`; budgets 21 corpus
embeddings / 26 query embeddings / 24 index planned / 48 max attempts / 26 GD / 0 final
answers; concurrency 1/1/1; synthetic-only.

## Files

- `open_notebook/integrations/graphrag/eval/realseamspn02d.py` — NEW canonical builder + runner.
- `open_notebook/integrations/graphrag/eval/cli_live_pn02d.py` — provider-secret gate + real
  authorized execution branch (canonical entrypoint).
- `open_notebook/integrations/graphrag/eval/authmintlivepn02d.py` — EW1 successor governance
  (repoint approved identity; PF1/B1-R2 retained historical).
- `open_notebook/integrations/graphrag/eval/authb1r2pn02d.py` — re-export EW1 identity.
- `tests/test_graphrag_pn02db1ew1.py` — NEW EW1 suite (completeness / no-placeholder / CLI
  reachable / missing-secret / wrong-checkpoint / orchestration / provider-net sentinel /
  secret-safe / successor governance).
- `tests/test_graphrag_pn02db0cb_adapters.py`, `tests/test_graphrag_pn02db0cb_live.py`,
  `tests/test_graphrag_pn02db1r2.py` — historical-vs-successor identity updates.

## Codex Independent Review #1 → LOW-finding remediation cycle #1

Codex Independent Review #1 = **decision C — PASS_WITH_LOW_FINDINGS (0 HIGH / 0 MEDIUM / 1
LOW)**; every seams-contract / no-test-import / no-placeholder / budget / notebook-scope /
trust-root-injection / successor-governance / envelope / production-data-reachability area
passed (Codex static review; its sandbox could not run pytest —
`CODEX_TEST_EXECUTION = BLOCKED_BY_CODEX_ENVIRONMENT`).

- **B1EW1-R1-L1 — LOW (DESIGN=NONE) — REMEDIATED.** Stale checkpoint-temporal wording: two
  docstrings still described the pre-EW1 model — `authb1r2pn02d.py` said "Only a FUTURE
  operator-approved **PF1** checkpoint … (peeling to the approved PF1 HEAD)", and
  `authmintlivepn02d.py`'s mint docstring said "While governance returns **None** (PN02D-B1-R2
  NOT_STARTED) this fails closed". After the EW1 repoint, governance returns the EW1 successor
  tag (non-None) and the mint fails closed because that annotated tag is **ABSENT** from real
  Git. Fix: the same stale statement was corrected wherever it appeared in the B1 control-plane
  docstrings/comments — `authmintlivepn02d.py` (8 sites), `authb1r2pn02d.py`, `cli_live_pn02d.py`,
  and `driver_live_pn02d.py` (2 sites) — to the accurate "governance returns the EW1 successor
  tag; while that tag is ABSENT from Git the trusted reader observes its absence and the mint
  fails closed" model, preserving the distinction that a satisfiable Git prerequisite is NOT a
  minted authorization and NOT provider execution. **Docstrings/comments only —
  `EXECUTABLE_LOGIC_CHANGED = NO`** (the executable `_B1_R2_SENTINELS` tuple and all logic
  untouched; ruff + mypy clean; targeted 197 pass unchanged; governance still returns EW1 and
  fails closed — `LIVE_PROVIDER_AUTHORIZATION_CURRENTLY_MINTABLE = NO`). No Git ref touched:
  HEAD `082dc95`, EW1 tag still ABSENT, PF1 (`082dc95`) and B1-R2 (`611532c`) immutable.

`CODEX_B1EW1_REVIEW_1 = C_PASS_WITH_LOW_FINDINGS` (retained — Review #1 is not rewritten as
clean); `B1EW1_R1_L1_REMEDIATED = YES`; `CODEX_B1EW1_REREVIEW_1 = NOT_RUN`;
`B1EW1_CHECKPOINT_ALLOWED = NO`.

**STOP:** `GRAPH_RAG_PN02DB1EW1_LOW_REMEDIATION_READY_FOR_CODEX_REREVIEW`. No Codex; no
checkpoint; no tag; no provider run.

## Codex Re-Review #1 → #2 (baseline artifact) → checkpoint attempt #1 (BLOCKED) → lifecycle remediation #1

- `CODEX_B1EW1_REREVIEW_1 = B_REMEDIATION_REQUIRED` (0 HIGH / 1 MEDIUM / 0 LOW). The MEDIUM
  `B1EW1-RR1-M1` was a REVIEW-BASELINE artifact: Codex diffed the working tree against HEAD
  `082dc95`, which predates the entire uncommitted EW1 implementation, so its "74 executable
  lines" were the prior-turn EW1 code already approved as C — not the docs-only LOW cycle.
- `CODEX_B1EW1_REREVIEW_2 = D_PASS_CLEAN` (isolated pre-LOW baseline; 12 reverse transforms, 0
  ambiguous, 0 executable hunks across all four files, all strip-docstring ASTs equal, token
  streams equal, `_B1_R2_SENTINELS` unchanged). `B1EW1-RR1-M1 = CLOSED_AS_BASELINE_ARTIFACT`,
  `REPOSITORY_CODE_REMEDIATION_REQUIRED_FOR_RR1_M1 = NO`. Re-run of Re-Review #2 returned D
  again (determinism). Review history retained: Review #1 = C, Re-Review #1 = B, Re-Review #2 = D.

- **EW1 checkpoint attempt #1 = `BLOCKED_POSTTAG_VERIFICATION` (NOT published).** All
  pre-commit gates passed; a single commit and a single **LOCAL-ONLY** annotated tag were
  created:
  - candidate commit `9aa3219611055b7fc4b3c84ce75b97ed5c1b090b`
  - candidate tag `graphrag-pn02db1ew1-real-execution-wiring-approved` (LOCAL ONLY — **not
    approved, not closed, not pushed**; backup branch still `082dc95`, backup EW1 tag ABSENT).
  The real post-tag Git gate PASSED, but the mandatory post-tag test run turned ONE test red:
  `tests/test_graphrag_pn02db1ew1.py::test_missing_ew1_tag_fails_closed_in_real_git` was
  **checkpoint-lifecycle-fragile** — it asserted the real EW1 tag is permanently absent
  (`b1_r2_tag_not_observed_in_git`), but once the real tag existed at HEAD the trusted reader
  observed it present and the fail-closed came from baseline binding instead. Per the
  checkpoint protocol, nothing was pushed; the local commit/tag were left intact for forensic
  traceability. `PROVIDER_TRAFFIC = 0`.

- **Checkpoint-lifecycle test remediation #1 (this doc's cycle, tests only —
  `PRODUCTION_AUTHORIZATION_LOGIC_CHANGED = NO`).** The fragile test was split into two
  intents (the same State-A/State-B pattern B1-R2 used before its checkpoint):
  - `test_synthetic_absent_checkpoint_fails_closed_in_real_git` — the PERMANENT absent-tag
    negative, anchored on the SYNTHETIC never-real identity `C.TEST_B1R2_TAG` so it fails
    closed with `b1_r2_tag_not_observed_in_git` regardless of whether the real EW1/PF1/B1-R2
    tags exist.
  - `test_real_ew1_tag_git_gate_is_lifecycle_aware` — observes the REAL EW1 tag and branches:
    State A (tag absent) asserts the absent-tag reason; State B (tag present at HEAD) asserts
    the absent-tag reason is NOT produced, the exact tag is at the authorized HEAD, and the
    fail-closed comes from baseline binding (derived from current `verify_b1_r2_checkpoint`
    semantics, not an invented reason).
  No Git ref/history was mutated: HEAD remains `9aa3219`, the local candidate EW1 tag remains
  at `9aa3219`, PF1/B1-R2 immutable. EW1 tests 21 pass (was 20; 1 split into 2); affected
  PN02D 198 pass; provider-free Docker 8 pass / 0 residue — all with the real EW1 tag PRESENT.
  A dedicated Codex review of this test fix is required before any checkpoint retry.

## Governance retained

`LIVE_PROVIDER_AUTHORIZATION_MINTED = NO`, `PN02_PROVIDER_RUN_AUTHORIZED = NO`,
`B1_REAL_PROVIDER_EXECUTION = NOT_RUN`, `PN02D_B2_QA_AUTHORIZED = NO`,
`GRAPHRAG_PRODUCTION_INTEGRATION = NOT_APPROVED`, `LIGHTRAG_ASK_INTEGRATION = NOT_APPROVED`,
`GRAPH_RAG_09_JUSTIFIED = NO`. `CODEX_B1EW1_REVIEW = NOT_RUN`, `B1EW1_CHECKPOINT_ALLOWED = NO`.

**STOP:** `GRAPH_RAG_PN02DB1EW1_IMPLEMENTATION_READY_FOR_CODEX_REVIEW`. No Codex; no
checkpoint; no tag; no provider run; no B2.
