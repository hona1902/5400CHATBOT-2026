# GraphRAG-PN02D-B0C-B — Real Provider Adapter & Execution Wiring (Offline Implementation)

**Phase kind:** CODE IMPLEMENTATION. Implements the frozen B0C-A design against the
approved B0B Protocol/orchestration seams. **Executed with ZERO real provider traffic:**
no external provider calls, no OpenRouter requests, no real embeddings, no
provider-backed LightRAG boot, no real indexing / `/query/data` / vector-provider
execution / graph deletion, no `PN02ProviderRunAuthorization` for a live run, no B1/B2,
no production Ask/GraphRAG, no GraphRAG-09. All real adapters are exercised only through
mock HTTP transports, a fake DB, a fake embedding provider, and a mock runtime
controller.

**This phase is NOT checkpointed.** It stops at
`GRAPH_RAG_PN02DB0CB_IMPLEMENTATION_READY_FOR_CODEX_REVIEW`. A B0C-B checkpoint is
forbidden without a Codex PASS.

---

## 1. Baseline / design tag

Verified directly from git at phase start:

| Item | Expected | Observed | Match |
|---|---|---|---|
| Branch | `feature/graphrag-lifecycle` | `feature/graphrag-lifecycle` | ✅ |
| HEAD | `cb766883f6421e5c692317f286e2ff071bf82fe8` | same | ✅ |
| Design tag peel | `graphrag-pn02db0ca-real-provider-wiring-design-approved` → `cb76688…82fe8` | same | ✅ |
| Working tree | CLEAN | CLEAN | ✅ |

Authoritative design:
[`GRAPHRAG_PN02DB0CA_REAL_PROVIDER_ADAPTER_WIRING_DESIGN.md`](GRAPHRAG_PN02DB0CA_REAL_PROVIDER_ADAPTER_WIRING_DESIGN.md).
Frozen fixture `graphrag_pn02_eval_v1`, hash
`9ce7df742810424d9ef7b7b34962187d9061245f81471f5fb1d7f2608f6899a6` (3 notebooks / 21
canonical Sources / 24 memberships / 24 queries; K ∈ {3,5}) — unchanged.

---

## 2. Implemented modules (reuse vs new code)

All eval-only, under `open_notebook/integrations/graphrag/eval/`
(`PRODUCTION_IMPORTS_EVAL = NO`).

### New modules (9)

| Module | Responsibility | Key reuse |
|---|---|---|
| `authmintlivepn02d.py` | Real `PN02ProviderRunAuthorization` mint + the distinct unforgeable `LiveProviderRunAuthorization` + `OperatorRunGrant` + run_id/type hardening | `authlivepn02d.mint_provider_run_authorization`, `attestpn02d`, `budgetlivepn02d` |
| `runtimelivepn02d.py` | `RealPN02RuntimeManager`: two-boot lifecycle (provider-free preflight → teardown → provider-bound execution), attestation, real route table, owned cleanup | `cell_provisioner08` (`CellProcessSpec`/handles), `attestpn02d.attest_version`, `realsidecarpn02d`, `manifestpn02.workspace_id_for` |
| `provider binding` (in `driver_live_pn02d.materialize_live_provider_binding`) | Live-gated binding materialization (secret NAMES only) | `provbindpn02d.materialize_provider_binding`, `provider_binding08.frozen_provider_binding` |
| `corpuslivepn02d.py` | `RealPN02CorpusProvisioner` + `CorpusEmbeddingBudget` + `derive_corpus_workload` | injected seams; live-only composition reuses `isolation08`, `precheck08`, `Source`, `embed_source_command` |
| `indexadapterpn02d.py` | Real per-notebook index client + factory | `GraphRAGClient.index_document`/`track_status`, `index_retry08._fetch_failed_reason_ex` |
| `gdadapterpn02d.py` | `RealPN02GDBackend` + factory (`POST /query/data`, `only_need_context=True`) | same wire seam as `gd_seam.GDQueryClient` |
| `vectoradapterpn02d.py` | `RealPN02VectorBackend` (Approach B) + `ActiveEmbeddingModelAttestation` (L-4) + `build_repo_query_member_row_fetcher` | `utils/embedding.generate_embedding` (live), `repo_query` (live), `source_embedding` |
| `deleteadapterpn02d.py` | `RealPN02DeleteBackend` + factory | `GraphRAGClient.delete_document`, `docidpn02d.compute_derived_document_id` |
| `driver_live_pn02d.py` | Thin `RealB1Driver` + `LiveB1Seams` + `build_live_b1_driver_deps` + `plan_live_b1` | reuses `driverpn02d.B1OfflineDriver` unchanged |
| `cli_live_pn02d.py` | `execute-b1-live` verb (authorization-gated) + `dry-run-b1-live-plan` | separate module from `liveclipn02d` |

### Minimal additive edits to committed B0B code (6 files)

Purely additive guards / one backward-compatible parameter — no behaviour change to
existing paths (595-test regression green):

- `authlivepn02d.py`: `RunIdConsistencyError` + `assert_run_ids_consistent` helper (L-1).
- `indexlivepn02d.py`, `gdlivepn02d.py`, `vectorlivepn02d.py`, `removallivepn02d.py`:
  executor-init `require_provider_run_authorization` (L-2 `isinstance`) + per-op run_id
  cross-check (L-1).
- `driverpn02d.py`: driver/operation run_id == capability run_id assertion (L-1); a
  backward-compatible `route_table` override so a live run injects the runtime manager's
  REAL endpoints (default `None` → unchanged offline behaviour).

---

## 3. Two-boot lifecycle

`PROVIDER_FREE_PREFLIGHT_RUNTIME_IS_PROVIDER_BOUND_EXECUTION_RUNTIME = NO`;
`SAME_CONTAINER = NO`. `RealPN02RuntimeManager.boot_preflight()` boots provider-FREE
runtimes (empty `provider_binding`), attests version, and TEARS THEM DOWN;
`boot_execution()` boots FRESH provider-BOUND runtimes (frozen binding + published
loopback port), attests version/workspace/endpoint/storage/health, and only then exposes
the route table. `boot_execution` requires a completed + torn-down preflight (else
`TwoBootOrderError`) and rejects any execution container that reuses a preflight identity.
Tested with mocks: preflight created → attested → cleaned; provider authorization
required; execution runtimes A/B/C created + separately attested; operations enabled only
after execution attestation; container identities never reused.

## 4. Authorization path

`zero-provider prechecks → RealLightRAGPreflightAuthorization (Gate 0 ∧ Gate 1) →
OperatorRunGrant → LiveProviderRunAuthorization mint → provider-binding materialization →
IndexingAuthorization → QueryAuthorization (24/24) → operation (allowlist-gated)`.

- **Real mint path** (`mint_live_provider_run_authorization`) is DISTINCT from
  `build_simulation_provider_run_authorization`. It requires a genuine real-preflight
  capability + an `OperatorRunGrant` carrying run_id, fixture hash, B0C-B implementation
  checkpoint identity, future B1-R2 checkpoint identity, provider-config fingerprint,
  `synthetic_only=true`, workload caps, the operation allowlist, and an approved (non-
  `SIMULATION`/`OFFLINE`) git baseline. It mints the underlying `PN02ProviderRunAuthorization`
  (real git baseline) and wraps it in an unforgeable `LiveProviderRunAuthorization`.
- **A boolean is insufficient (task §12):** every live seam (runtime, binder, corpus
  provisioner, factories, entrypoint) calls `require_live_provider_run_authorization`, an
  `isinstance` guard that rejects a plain (simulation) `PN02ProviderRunAuthorization`, a
  dict look-alike, or `None`.

## 5. run_id (L-1) & capability-type (L-2) hardening

`RUN_ID_CROSSCHECK_IMPLEMENTED = YES` — each executor pins the run identity from its
provider-run capability and cross-checks every other capability at init AND before each
dispatch; the driver asserts `operation.run_id == capability.run_id`. Mismatch fails
closed before any backend/network call. `AUTH_CAPABILITY_TYPE_HARDENING_IMPLEMENTED = YES`
— each executor validates the provider-run capability TYPE (`isinstance`) at init, so a
look-alike object with matching fields cannot construct a real executor. The live seam
additionally requires the stronger `LiveProviderRunAuthorization` type.

## 6. Provider binding

`REAL_PROVIDER_BINDING_IMPLEMENTED = YES`. Frozen identity: OpenRouter, LLM
`openai/gpt-4o-mini`, embedding `openai/text-embedding-3-small`, dim 1536, secret env
NAME `OPENROUTER_API_KEY`, fingerprint `pbf_1811d0bfd5cfad2743ffaa69`. Materialization is
unreachable before a valid live capability; only secret NAMES are read; no secret VALUE is
stored, logged, serialized, put on argv, or placed in an exception/manifest.

## 7. Runtime manager

`REAL_RUNTIME_MANAGER_IMPLEMENTED = YES` — allocates per-notebook workspace id
(`workspace_id_for(record_id)`), port, storage, loopback endpoint; boots via an injected
process controller (`DockerCellProcessController` in a real run; a fake in tests); waits
readiness via an injected health prober; attests version from three provider-free signals
(`attest_version`); records endpoints into the route table; owned-only cleanup. No real
container is launched in B0C-B.

## 8. Corpus provisioner + workload/budget

`REAL_PN02_CORPUS_PROVISIONER_IMPLEMENTED = YES`,
`CORPUS_EMBEDDING_ACCOUNTING_IMPLEMENTED = YES`,
`CORPUS_VECTOR_EMBEDDING_WORKLOAD_SEPARATE_FROM_QUERY = YES`.

- **Derivation (mechanical, test-locked):** Approach B keeps ONE canonical corpus and
  scopes retrieval by membership, so each canonical Source is embedded **once per isolated
  corpus** — NOT once per workspace copy. Therefore
  `PLANNED_CORPUS_SOURCE_EMBEDDING_OPERATIONS = MAX_CORPUS_SOURCE_EMBEDDING_OPERATIONS =
  len(fixture.source_keys) = 21` (the canonical Source count, distinct from the 24
  memberships).
- **Budget:** a dedicated bounded `CorpusEmbeddingBudget` (cap 21), checked BEFORE each
  embedding, SEPARATE from the driver's 26 query-embedding cap. Exhaustion raises
  `CorpusBudgetExceeded` before any embed.
- Creates 21 Sources + 24 `reference` edges, then vectorizes each Source once. State is
  run/test-owned; `NORMAL_DB_MUTATIONS = 0` (fake DB in tests; isolated namespace in a
  real run, via `build_in_isolation_corpus_seams` which asserts active isolation).

## 9. Index backend + completion

`REAL_INDEX_BACKEND_IMPLEMENTED = YES`, `REAL_INDEX_COMPLETION_IMPLEMENTED = YES`. Wire:
`POST /documents/text` body exactly `{"text": canonical_text, "file_source": source_id}`,
`X-API-Key` only when configured. Completion is proven by
`GET /documents/track_status/{id}` aggregate `PROCESSED` (submit acceptance is never
completion). Retry/normalization/caps are the frozen executor's
(`MAX_INDEX_ATTEMPTS_PER_OPERATION = 2`, `MAX_GRAPH_INDEX_ATTEMPTS = 48`), classified by
`index_retry08.is_transient_reason`.

## 10. GD backend

`REAL_GD_BACKEND_IMPLEMENTED = YES`. Wire: `POST /query/data` body
`{"query": question, "mode": "hybrid", "only_need_context": True}`. Never `client.query()`
/ `/query`; no final-answer LLM. Returns STRONG-anchor `chunks[].file_path` +
`references[].file_path` as raw candidates; the executor's `normalize_graph(allowlist=
fixture.source_keys)` produces the UNORDERED set. `QUERY_DATA_EXPOSES_VALID_RANK/SCORE =
NO`.

## 11. Vector backend (Approach B — load-bearing)

`REAL_VECTOR_BACKEND_IMPLEMENTED = YES`, `GLOBAL_TOPK_THEN_POSTFILTER_PRESENT = NO`,
`VECTOR_K3_K5_ONE_RANKING = YES`, `ACTIVE_EMBEDDING_MODEL_ATTESTATION_IMPLEMENTED = YES`.

`rank_members` resolves the current-notebook member keys → isolated `source` record ids,
fetches ONLY those rows (`SELECT source, embedding FROM source_embedding WHERE source IN
$ids AND embedding != NONE AND array::len(embedding) = array::len($q)`), computes a genuine
cosine (`dot / (‖a‖‖b‖)`, matching `vector::similarity::cosine`), takes `max` per source,
and returns one ranked list. The backend never sees a non-member row, so a
global-top-K-then-post-filter is structurally impossible (an adversarial test with a
higher-similarity foreign row proves it is never fetched). `embed_query` reuses the
standalone `generate_embedding` and attests the active model/dim
(`openrouter`/`text-embedding-3-small`/1536) BEFORE any provider embedding (L-4);
provider/model/dimension mismatch fails before the embedding.

## 12. Delete backend + isolation

`REAL_DELETE_BACKEND_IMPLEMENTED = YES`,
`SHARED_SOURCE_DELETE_ISOLATION_IMPLEMENTED = YES`. Wire:
`DELETE /documents/delete_document` body `{"doc_ids": [compute_doc_id(source_id)]}`;
`deletion_started`/`not_found` → GONE → succeeded; `busy`/`not_allowed`/unexpected → not
succeeded. Cross-workspace refusal is enforced by the frozen removal executor
(`CrossWorkspaceDeleteRefused`). Tested: SH_AB deleted at NB_A's endpoint only → NB_A loses
it, NB_B keeps it. `STALE_GRAPH_EVIDENCE_ACCEPTED_AS_VALID = 0` (the ON post-validation
backstop makes a failed delete never restore membership).

## 13. Live entrypoint

`LIVE_B1_ENTRYPOINT_IMPLEMENTED = YES`, `LIVE_ENTRYPOINT_REQUIRES_REAL_AUTH = YES`.
`cli_live_pn02d.execute-b1-live` requires a run manifest (operator grant), run_id, frozen
fixture hash, approved git baseline, frozen provider-config fingerprint, and
`synthetic_only=true`. A missing/invalid field, a simulation authorization, a
run_id/fixture-hash/provider-fingerprint mismatch, or a dirty/unapproved git baseline is
REFUSED before any provider binding or runtime boot. In this build the verb additionally
stops at the governance gate (`PN02_PROVIDER_RUN_AUTHORIZED = NO`) and wires no live
seams, so it never boots or binds. The offline `liveclipn02d` still has neither
`execute-b1` nor `execute-b1-live` (invariant preserved). `dry-run-b1-live-plan` prints a
provider-zero content-safe plan (fixture hash, 3 routes, two-boot plan, 24 index, corpus
workload, 24+2 GD/vector, 26 query-embedding cap, 1 delete, provider/model identity,
concurrency 1/1/1) with no secret value.

## 14. Design/source discrepancies (flagged for Codex)

Two committed reuse targets validate provenance as Open Notebook **record ids** and reject
PN02 **fixture keys** (`A1`/`SH_AB`), so they cannot be used verbatim:

1. **Index** — `RealCellIndexClient` → `GraphRAGService.index_source` → `validate_source_id`
   rejects fixture keys. `indexadapterpn02d.RealPN02IndexClient` wraps the SAME wire seam
   (`GraphRAGClient.index_document` / `track_status`) WITHOUT that validation, with an
   equivalent fixture-key allowlist guard.
2. **GD** — `gd_seam.GDQueryClient` normalizes STRONG anchors through
   `normalize.canonical_source_id` (record-id world), dropping fixture keys.
   `gdadapterpn02d.RealPN02GDBackend` issues the SAME `/query/data` call and extracts the
   SAME STRONG anchors as raw candidates, letting the PN02 fixture-key normalizer filter.

The wire contracts, completion model, and secret handling are identical to the named
targets; only the inapplicable record-id validation is bypassed. This is a detail the
frozen design (§9/§11/§12) did not anticipate, resolved without redesigning the
methodology. **Codex should confirm this resolution.**

## 15. Test evidence

52 new B0C-B tests (`tests/test_graphrag_pn02db0cb_adapters.py`,
`tests/test_graphrag_pn02db0cb_live.py`, support in
`tests/graphrag_pn02db0cb_common.py`):

- `FULL_REAL_WIRING_MOCK_SIMULATION = PASS` (COMPLETE run through the frozen evaluator;
  24 index / 26 GD / 1 delete wire calls; 21 corpus embeddings; isolation evidenced).
- `TWO_BOOT_MOCK_TEST = PASS`; `HTTP_CONTRACT_TESTS = PASS`;
  `VECTOR_ADVERSARIAL_TEST = PASS`; `CORPUS_BUDGET_TEST = PASS`;
  `RUN_ID_NEGATIVE_TESTS = PASS`; `CAPABILITY_TYPE_NEGATIVE_TESTS = PASS`;
  `NETWORK_DENIAL_TEST = PASS` (transport-error → content-free `GDBackendError`).
- 24/24 gate: 24/24 → GD/vector permitted; 23/24 → `FAILED_BEFORE_QUERY`, GD/vector
  wire calls = 0.

Counters (real external/live execution, not mocked adapter calls):
`B0C_B_EXTERNAL_PROVIDER_NETWORK_CALLS = 0`, `B0C_B_REAL_PROVIDER_CALLS = 0`,
`B0C_B_PROVIDER_BOUND_LIGHTRAG_BOOT_COUNT = 0`, `B0C_B_REAL_INDEX_OPERATIONS = 0`,
`B0C_B_REAL_GD_QUERIES = 0`, `B0C_B_REAL_VECTOR_PROVIDER_QUERIES = 0`,
`B0C_B_REAL_DELETE_OPERATIONS = 0`, `NORMAL_DB_MUTATIONS_DURING_TESTS = 0`.

Suites: PN02 + graphrag_08 + B0C-B = **595 passed / 1 skipped**. `ruff` PASS.
`mypy` = 0 errors in new/changed modules (5 pre-existing `concurrency_diag08` baseline
findings only, file unchanged from baseline). `PRODUCTION_IMPORTS_EVAL = NO` (grep-
confirmed). `NEW_MIGRATIONS = 0`.

## 16. Network / provider-zero evidence

All real adapters are tested against `httpx.MockTransport` (index/GD/delete), a fake DB +
fake embedding provider (vector/corpus), and a fake process controller (runtime). No real
socket is opened, no container is started, and no secret value is read. The external
OpenRouter network is reachable only under a future operator-authorized B1 run, never in
B0C-B.

## 17. Known limitations

- The two design/source discrepancies (§14) are resolved by bypassing an inapplicable
  record-id validation; Codex must confirm.
- The runtime manager's real Docker composition and the corpus provisioner's real
  isolated-namespace composition (`build_in_isolation_corpus_seams`) are exercised only
  through injected mocks/fakes in B0C-B; their real execution happens only under a future
  authorized B1-R2 run.
- `execute-b1-live` deliberately wires no live seams in this build, so its authorized
  success path is not exercised (only its refusals are).

## 18. Governance (retained)

`CODEX_REVIEW_REQUIRED = YES`, `CODEX_INITIAL_REVIEW = NOT_RUN`,
`CODEX_RE_REVIEW = NOT_RUN`, `B0C_B_CHECKPOINT_ALLOWED = NO`,
`CLAUDE_INTERNAL_REVIEW = supplementary only`. `PN02D-B1-R2 = NOT_STARTED`,
`PN02_PROVIDER_RUN_AUTHORIZED = NO`, `PN02D-B2 = NOT_AUTHORIZED`,
`GRAPHRAG_PRODUCTION_INTEGRATION = NOT_APPROVED`,
`LIGHTRAG_ASK_INTEGRATION = NOT_APPROVED`, `GRAPH_RAG_09_JUSTIFIED = NO`, Boundary B
(`SYNTHETIC_ONLY = true`). Historical authorization
`pn02db1-daf6b760-7d68-4674-9222-ac9f962ef6c4` remains RETIRED and is not reused.

**STOP:** `GRAPH_RAG_PN02DB0CB_IMPLEMENTATION_READY_FOR_CODEX_REVIEW`.
