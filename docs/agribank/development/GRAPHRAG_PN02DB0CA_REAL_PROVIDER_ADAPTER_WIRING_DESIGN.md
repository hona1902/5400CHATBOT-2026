# GraphRAG-PN02D-B0C-A — Real Provider Adapter & Execution Wiring Design

**Phase kind:** FORENSIC / DESIGN-ONLY. No code, no provider traffic, no real LightRAG
boot, no indexing, no embeddings, no `/query/data`, no vector live query, no graph
delete, no `PN02ProviderRunAuthorization` mint, no B1 execution, no B2, no Ask
integration, no GraphRAG-09.

This document designs the **exact** real provider-backed wiring that is missing from the
approved B0B offline driver, so a future **B0C-B** implementation can connect the
existing PN02 orchestrator to real runtimes/indexing/`/query/data`/vector/delete
**without** changing the frozen PN02 scientific methodology.

---

## 1. Baseline / governance

Verified directly from git at phase start:

| Item | Expected | Observed | Match |
|---|---|---|---|
| Branch | `feature/graphrag-lifecycle` | `feature/graphrag-lifecycle` | ✅ |
| HEAD | `0fed5ea2fecd9fb10a684d0780b9cf39ff15472a` | `0fed5ea2…472a` | ✅ |
| Blocker tag peel | `graphrag-pn02db1-real-execution-path-blocked` → `0fed5ea…472a` | `0fed5ea…472a` | ✅ |
| Working tree | CLEAN | CLEAN | ✅ |

Governance carried forward unchanged:

- PN02D-A = **APPROVED**
- PN02D-B0A = **APPROVED**
- PN02D-B0B = **APPROVED**
- PN02D-B1-R = **APPROVED**
- PN02D-B1 attempt #2 = **BLOCKED PRE-PROVIDER — REAL EXECUTION PATH ABSENT**
- PN02D-B0C-A = **REAL WIRING DESIGN / CANDIDATE REVIEW** (this phase)
- PN02D-B0C-B = **NOT_STARTED**
- PN02D-B1-R2 = **NOT_STARTED**
- `PN02_PROVIDER_RUN = NOT_AUTHORIZED`
- PN02D-B2 = **NOT_AUTHORIZED**
- `GRAPHRAG_PRODUCTION_INTEGRATION = NOT_APPROVED`
- `LIGHTRAG_ASK_INTEGRATION = NOT_APPROVED`
- `GRAPH_RAG_09_JUSTIFIED = NO`

**Historical authorization retired.** `OLD_AUTHORIZATION_RUN_ID =
pn02db1-daf6b760-7d68-4674-9222-ac9f962ef6c4`; `AUTHORIZATION_CONSUMED = NO`;
`AUTHORIZATION_REUSABLE = NO`; `AUTHORIZATION_STATUS =
RETIRED_AFTER_BLOCKED_EXECUTION_ATTEMPT`. It is not revived or reused. Any future live
B1 run requires: **new implementation checkpoint → new B1-R2 → new operator authorization
→ new run_id**.

**Frozen fixture (unchanged):** `PN02_FIXTURE_NAME = graphrag_pn02_eval_v1`;
`PN02_FIXTURE_HASH = 9ce7df742810424d9ef7b7b34962187d9061245f81471f5fb1d7f2608f6899a6`;
3 notebooks / 21 canonical Sources / 24 workspace memberships / 24 queries; vector K ∈
{3,5}.

---

## 2. Attempt #2 blocker (what B0C solves)

Attempt #2 passed every zero-provider precheck (baseline, fixture hash, provider-config
fingerprint `pbf_1811d0bfd5cfad2743ffaa69`) and then **STOPPED before any provider
traffic** because the committed B0B harness has no real provider-backed B1 execution path.
Re-verified from source this phase:

| Real seam | State (source-verified) |
|---|---|
| `RealCellIndexClient` | **PRESENT** (`live_indexer08.py:348`) but **not wired into PN02**: satisfies the `CellIndexClient` Protocol PN02 already imports, yet the PN02 `IndexClientFactory` is `NotebookRuntimeRoute → CellIndexClient` while the real client factory is `CellEndpoint → CellIndexClient`, and PN02 routes carry `eval-null://` placeholder endpoints (`routelivepn02d.py:45-47`), not a real base_url. |
| `RealGDBackend` (PN02 `GDQueryBackend`) | **ABSENT** — only `fakeslivepn02d` implements it; `gd_seam.GDQueryClient` exists but is not adapted to the PN02 backend Protocol. |
| `RealVectorBackend` (PN02 `VectorBackend`) | **ABSENT** — only fakes implement it. |
| `RealDeleteBackend` (PN02 `DeleteBackend`) | **ABSENT** — only fakes implement it. |
| `execute-b1` live entrypoint | **ABSENT** — `liveclipn02d` deliberately exposes only `validate-live-driver` / `dry-run-b1-plan` / `run-offline-b1-simulation` (`liveclipn02d.py:8`); a test pins the absence (`test_graphrag_pn02db0b_cli.py:20`). |
| Real `PN02ProviderRunAuthorization` mint-and-drive path | **ABSENT** — only `build_simulation_provider_run_authorization` (`driverpn02d.py:502`) mints from **simulated** gate booleans; the sole `B1DriverDeps` constructor is `run_offline_b1_simulation` using `build_clean_fake_topology`. |

`grep` across the repo confirms **no** `RealGDBackend` / `RealVectorBackend` /
`RealDeleteBackend` / `RealPN02RuntimeManager` / `RealPN02ProviderBinder` class exists
anywhere (only doc references). `RealCellIndexClient` is the single real adapter that
exists.

---

## 3. Existing B0B architecture (reused unchanged)

The B0B driver (`driverpn02d.B1OfflineDriver`) is already the full scientific
orchestrator: every provider/DB/Docker seam is an **injected Protocol**, and the driver
reimplements **no** scientific logic — it emits `schemaspn02` records into the frozen
`evaluatepn02.run_offline_evaluation` (Stage-1 → R0-R4 → M0-M3). B0C-B changes **nothing**
in this orchestrator or the evaluator; it supplies **real** implementations of the same
injected Protocols plus the resource ownership around them.

Injected Protocol surface (the contract B0C-B must satisfy):

| Protocol | Method(s) | Defined in | Fake (B0B) | Factory type |
|---|---|---|---|---|
| `CellIndexClient` | `submit(source_id, canonical_text) → IndexSubmitResult`; `status(track_id) → IndexStatusResult` | `live_indexer08.py:166` | `FakeCellIndexClient` | `IndexClientFactory = NotebookRuntimeRoute → CellIndexClient` |
| `GDQueryBackend` | `query_evidence(question, benchmark_ids) → GDBackendResult(candidate_source_ids, latency_ms)` | `gdlivepn02d.py:65` | `FakeGDBackend` | `GDBackendFactory = NotebookRuntimeRoute → GDQueryBackend` |
| `VectorBackend` | `embed_query(question) → tuple[float,…]`; `rank_members(query_embedding, candidate_source_ids) → Sequence[str]` | `vectorlivepn02d.py:62` | `FakeVectorBackend` | `VectorBackendFactory = NotebookRuntimeRoute → VectorBackend` |
| `DeleteBackend` | `delete_document(derived_document_id) → DeleteResult(succeeded)` | `removallivepn02d.py:73` | `FakeDeleteBackend` | `DeleteBackendFactory = NotebookRuntimeRoute → DeleteBackend` |

Authorization/budget/routing/identity layers are already real and reused verbatim:
`authlivepn02d` (unforgeable `_AUTH_KEY`-sentinel capability chain), `budgetlivepn02d`
(stateful pre-op guard, caps 24/48/1/26/26/26 with B1 final-answer/client-query/judge = 0),
`routelivepn02d` (`PN02Router`, wrong-endpoint / wrong-workspace / non-member rejection),
`docidpn02d` (`(workspace_id, canonical_source_id)` key, `doc-`+md5 derived id),
`outcomespn02d` (technical-vs-scientific taxonomy), `manifestpn02` (attestation).

**Minimal-change principle:** B0C-B adds new concrete adapter modules and a thin live
driver/CLI. The only touch to existing B0B code is two hardening additions (run_id
cross-check, executor-init capability `isinstance`) called out in §8, which are additive
guards, not behavioural changes.

---

## 4. Real-seam forensic matrix

Every reusable seam, source-verified this phase:

| # | Seam | File · symbol | Input | Output | Auth / network | Reuse verdict |
|---|---|---|---|---|---|---|
| A | B0B orchestrator | `driverpn02d.B1OfflineDriver` | fixture + `B1DriverDeps` + `PN02ProviderRunAuthorization` | `B1RunOutcome` (→ evaluator) | injected; no I/O of its own | **Reuse unchanged** |
| B | Injected Protocols | §3 table | — | — | — | **Reuse unchanged** (new concrete impls) |
| C | Capability chain | `authlivepn02d` (`mint_provider_run_authorization`, `mint_indexing_authorization`, `mint_query_authorization`) | real-preflight cap + fixture hash + caps + run_id | unforgeable capabilities | fail-closed `isinstance`; no I/O | **Reuse; feed by real preflight** |
| D | Routing / attestation | `routelivepn02d.PN02Router`, `attest_route`; `manifestpn02` | notebook_id, observed identity | route / `WorkspaceAttestation` | offline | **Reuse; real endpoints & observations** |
| E | Workload guard | `budgetlivepn02d.StatefulBudgetGuard` | op class | reserve/raise | offline | **Reuse unchanged** |
| F | Result schemas / normalizer | `schemaspn02`, `normalizepn02`, `outcomespn02d` | candidate ids | `*EvidenceResult`, `TechnicalOutcome` | offline | **Reuse unchanged** |
| G | Real LightRAG preflight | `preflightpn02d.run_preflight_da` (Gate 0 single + Gate 1 three) → `attestpn02d.mint_real_preflight_authorization` | Docker + storage base dir | `RealLightRAGPreflightAuthorization` | provider-**free** boot (`--internal`, empty bindings) | **Reuse for preflight stage** |
| H | Real index client | `live_indexer08.RealCellIndexClient` (`:348`) wrapping `GraphRAGService` | `source_id`, `canonical_text` | `IndexSubmitResult` / `IndexStatusResult` | httpx to a **base_url**; `X-API-Key` | **Reuse; needs route→endpoint(base_url) factory** |
| I | GraphRAG-04/08 index/query primitives | `service.py` `index_source` / `track_status` / `delete_document_for_source`; `client.py` | scalars | `IndexAck` / `IndexStatus` / `DeleteOutcome` | httpx | **Reuse via H + delete adapter** |
| J | GraphRAG-08 provider binding + published-port boot | `cell_provisioner08.DockerCellProcessController.start` (`:554`) | `CellProcessSpec` (+ `provider_binding`) | running container + loopback `base_url` | **provider-BOUND**; `-p 127.0.0.1:port`, secret env by inheritance | **Reuse as the execution runtime manager** |
| K | 08E provider wiring | `provider_binding08.frozen_provider_binding` / `DiagnosticProviderBinding08`; `provbindpn02d.materialize_provider_binding` | auth + secret-name presence | content-safe boot descriptor | secret NAME only | **Reuse unchanged** |
| L | LightRAG v1.5.6 HTTP seams | `client.py` `index_document` (`POST /documents/text`), `track_status` (`GET /documents/track_status/{id}`), `delete_document` (`DELETE /documents/delete_document`) | see §9/§14 | see §9/§10/§14 | httpx | **Reuse unchanged** |
| M | ON vector embedding/query | `notebook.vector_search` (`:846`), `fn::vector_search` (`9.surrealql:2`), `utils/embedding.generate_embedding` (`:204`), `source_embedding` (`1.surrealql:16`) | query string / member ids | ranked `{id, similarity}` | embedding call to OpenRouter | **`fn::vector_search` NOT reusable (global top-K); use eval-side Approach B (§13)** |
| N | ON notebook↔source membership | `reference` relation (`1.surrealql:54` `TYPE RELATION FROM source TO notebook`), `notebook.get_sources` (`:30`) | notebook_id | member Source ids | DB read | **Reuse — `SELECT VALUE in FROM reference WHERE out=$nb`** |
| O | Graph delete lifecycle | `service.delete_document_for_source` → `client.delete_document(compute_doc_id(source_id))`; `compute_doc_id` (`client.py:61`) | source_id | `DeleteOutcome` | httpx | **Reuse — per-notebook base_url** |

---

## 5. Runtime lifecycle (provider-free preflight vs provider-bound execution)

**Source reality resolves the §35 subtlety definitively:**

- PN02D-A (`realsidecarpn02d.build_run_command`) boots each sidecar with **empty** provider
  bindings on a run-owned `--internal` Docker network with **no published port** — reachable
  only via `docker exec`; `assert_no_provider_binding_in_command` (`:215`) **structurally
  refuses** any boot command carrying a non-empty provider binding.
- Container environment is **fixed at `docker run`** — it cannot be reconfigured after
  startup, and a `--internal`/no-port container cannot serve the HTTP index/`/query/data`
  the real adapters require.
- GraphRAG-08's `DockerCellProcessController.start` (`cell_provisioner08.py:554`) boots a
  **provider-BOUND** container: `-p 127.0.0.1:port:9621` (published loopback), public
  bindings on argv, **secret** provider key resolved late and injected by env inheritance
  (`-e NAME`, value in subenv, never on argv), base_url `http://127.0.0.1:port`
  (`:1534`).

**Conclusion — `PROVIDER_FREE_AND_BOUND_RUNTIMES_ARE_SAME_CONTAINER = NO`.** The two are
**separate lifecycle stages**. There is **no** contradiction requiring auth-before-preflight
and **no** weakening of fail-closed behaviour: the provider-free preflight runs first (Gate
0 + Gate 1) and produces the `RealLightRAGPreflightAuthorization`; only then are the
provider-bound execution runtimes booted. This is a *refinement of detail* over the frozen
B0A conceptual chain (which spoke of one boot), not a change to the capability order — see
§7. **B1-R2 must acknowledge the two-boot sequence** (design finding **M-2**, non-blocking).

### Recommended frozen lifecycle (§36)

```
zero-provider static prechecks            (fixture hash, baseline, provider-config fingerprint, secret NAME presence)
  ↓
operator one-run grant                    (out-of-band; binds run_id + fixture hash + B0C-B checkpoint + provider fingerprint)
  ↓
[STAGE P] provider-FREE preflight         (PN02D-A Gate 0 single + Gate 1 three; --internal, empty bindings; ZERO egress)  → mint RealLightRAGPreflightAuthorization → TEAR DOWN preflight runtimes
  ↓
mint PN02ProviderRunAuthorization         (from the real preflight cap; fixture-hash + provider-fingerprint + caps + run_id pinned)
  ↓
materialize provider binding              (auth-gated; secret NAME presence only)
  ↓
[STAGE X] open Option-A isolated Surreal namespace + seed temp embedding Model (text-embedding-3-small, dim 1536) + create 21 canonical Sources + 24 reference edges + vectorize (source_embedding)   ← corpus provisioning (finding M-1)
  ↓
[STAGE X] boot 3 provider-BOUND LightRAG runtimes (cell_provisioner08 pattern; published loopback port; frozen binding injected)
  ↓
health / version / workspace / storage attestation (three provider-free signals + published-port /health) → mint IndexingAuthorization
  ↓
index 24 memberships  →  24/24 completeness gate  →  mint QueryAuthorization
  ↓
24 GD + 24 vector baseline  →  membership removal (1 delete + 2 GD + 2 vector re-probes)
  ↓
normalize → frozen PN02B evaluator → content-safe report
  ↓
owned-only cleanup (LightRAG per-id delete, containers/ports/networks, temp namespace drop, env restore)
```

### Runtime manager responsibilities (`RealPN02RuntimeManager`)

Allocate run-owned network/port/storage per notebook; assign 3 workspace ids
(`manifestpn02.workspace_id_for(record_id)`, already frozen); assign 3 loopback endpoints;
inject provider binding **after** authorization; boot provider-bound containers
(`DockerCellProcessController`); wait real readiness (`/health` over the published port);
attest version (three provider-free signals, `attestpn02d.attest_version`) + workspace +
storage (`attest_real_runtime`); record endpoint/storage ownership into the route table;
own-only cleanup on partial startup. Reuses PN02D-A `attest_version` / `attest_real_runtime`
and GraphRAG-08 `DockerCellProcessController` — net-new work is the **assembly**, not new
provider logic.

---

## 6. Provider binding

Frozen safe identity (source-verified `provider_binding08.py`): provider **OpenRouter**;
LLM `openai/gpt-4o-mini`; embedding `openai/text-embedding-3-small`; embedding dim **1536**;
host `https://openrouter.ai/api/v1`; secret env **NAME** `OPENROUTER_API_KEY`;
secret-free fingerprint **`pbf_1811d0bfd5cfad2743ffaa69`**.

Binding flow (unchanged from B0B; already correct):

```
PN02ProviderRunAuthorization  →  secret-NAME presence check (env_present_secret_names; NEVER reads a value)
  →  frozen_provider_binding().validate()  →  provider-config fingerprint match
  →  container_public_env() on argv  +  container_secret_env_map() by env inheritance  →  provider-bound runtime boot
```

`PROVIDER_AUTHORIZATION_PRECEDES_BINDING = YES` (`provbindpn02d.materialize_provider_binding`
calls `require_provider_run_authorization` first). No secret value is stored in manifest,
docs, argv, log, exception, or artifact.

---

## 7. Authorization order & real mint path

Frozen order (fail-closed, each step unreachable before the prior):

```
zero-provider prechecks
  → RealLightRAGPreflightAuthorization   (minted ONLY on Gate 0 ∧ Gate 1 PASS, provider-free)
  → operator one-run grant               (out-of-band; binds run identity)
  → PN02ProviderRunAuthorization mint     (require_real_preflight_authorization first)
  → provider-binding materialization      (require_provider_run_authorization first)
  → provider-bound runtime boot
  → IndexingAuthorization                 (after binding attestation PASS)
  → QueryAuthorization                    (only at 24/24 index completeness)
  → operation                             (allowlist-gated)
```

**No contradiction.** The preflight that mints `RealLightRAGPreflightAuthorization` is
provider-**free** (§5 Stage P), so it precedes the provider-run mint without any
provider-bound boot happening first. The provider-bound boot (§5 Stage X) happens **after**
the mint. The capability sequence is therefore valid and needs no weakening.

### Real mint path (replaces the B0B simulation factory)

B0B's `build_simulation_provider_run_authorization` (`driverpn02d.py:502`) mints from
**simulated** gate booleans. B0C-B adds a **real** mint that requires all of:

- an **operator-approved `run_id`** (immutable CLI input, not defaulted);
- the frozen **fixture hash** (`9ce7df74…6899a6`), re-verified live at mint;
- the approved **B0C-B implementation checkpoint** (commit + tag) as `git_baseline_commit`/`git_baseline_tag`;
- the **B1-R2 checkpoint** identity (recorded in the run manifest);
- the frozen **provider fingerprint** `pbf_1811d0bfd5cfad2743ffaa69`;
- `synthetic_only = True`, `real_internal_data_allowed = False` (Boundary B);
- the frozen **workload caps** (24/48/1/26/26/26; final-answer/client-query/judge = 0);
- the operation **allowlist** `{GRAPH_INDEX, INDEX_REQUIRED_EMBEDDING, GD_QUERY_DATA, VECTOR_QUERY_EMBEDDING, VECTOR_NOTEBOOK_QUERY, GRAPH_DELETE}`;
- a genuine `RealLightRAGPreflightAuthorization` from a **real** Gate 0 ∧ Gate 1 preflight (not simulated booleans);
- a clean/approved git baseline.

`mint_provider_run_authorization` already enforces the real-preflight capability, fixture
hash, Boundary B, and provider fingerprint; the only net-new work is driving it from a
**real** preflight and an operator `run_id`, and refusing any default/simulated input.
Direct construction remains impossible (`_AUTH_KEY` sentinel).

---

## 8. run_id / capability-type hardening

Two B1-R LOW findings become **hard B0C-B requirements**:

- **run_id cross-check (LOW-2 → finding L-1).** Every capability already carries `run_id`.
  B0C-B adds a per-operation assertion:
  `operation.run_id == provider_run_auth.run_id == indexing/query_auth.run_id ==
  driver.run_id == manifest.run_id`. A mismatch fails closed **before dispatch** (not only
  at mint time). Acceptance flag: `RUN_ID_CROSSCHECK_IMPLEMENTED = YES`.
- **capability-type hardening (LOW-1 → finding L-2).** Executors currently store
  `provider_run_auth` without validating it in `__init__` (they validate `query_auth` via
  `require_query_authorization`). B0C-B adds `require_provider_run_authorization(...)` (an
  `isinstance` guard) in each executor's `__init__`, so a real executor cannot be
  constructed with an unrelated object that merely has similar fields. `isinstance` against
  the `__slots__`, `_AUTH_KEY`-minted capability classes is the repository-consistent
  unforgeable pattern (a look-alike cannot be minted without the module-private key, so
  `isinstance` is sufficient and appropriate here). Acceptance flag:
  `AUTH_CAPABILITY_TYPE_HARDENING_IMPLEMENTED = YES`.

---

## 9. Real index adapter (`RealPN02IndexBackend`)

The PN02 index executor (`indexlivepn02d.MembershipIndexExecutor`) already imports the
`CellIndexClient` Protocol + `IndexSubmitResult`/`IndexStatusResult` from `live_indexer08`,
and `RealCellIndexClient` (`:348`) already implements them against a real `GraphRAGService`.
**The only net-new code is a `NotebookRuntimeRoute → CellIndexClient` factory that carries
the notebook's real base_url + api_key.**

Verified real wire behaviour (`client.index_document`, `client.py:248-281`):

- **endpoint / method:** `POST {base_url}/documents/text`;
- **request JSON:** exactly `{"text": canonical_text, "file_source": source_id}` (allowlisted; no title/metadata/notebook_ids cross the wire);
- **auth:** `X-API-Key` header, added only when configured, never logged;
- **`source_id` → `file_source`:** ON always passes the canonical Source id as `file_source`;
- **synthetic text:** `canonical_text` = the fixture Source text (Boundary B: fixture-owned only, §17 of task);
- **derived document id:** obtained deterministically as `compute_doc_id(source_id) = "doc-"+md5(source_id)` — content-independent (verified `client.py:61-80`), identical across workspaces (safe only under workspace isolation);
- **operation_id tracking:** the run-owned `DerivedDocMappingStore` row (`(workspace_id, canonical_source_id)`) carries `index_operation_id` + `index_status`.

The factory builds `CellEndpoint(base_url=route-real-endpoint, …)` from the runtime
manager's booted container and returns `RealCellIndexClient(endpoint, api_key=…)`. Because
`RealCellIndexClient` binds `GraphRAGConfig(enabled=True, base_url=endpoint.base_url)`, each
notebook's index client talks **only** to that notebook's own sidecar.

---

## 10. Index completion & retry

**Completion is proven by the track surface, never by submit acceptance** (already enforced
by `MembershipIndexExecutor._one_attempt`). Verified `client.track_status`
(`client.py:494-536`, `models.py:287-297`):

- **endpoint:** `GET {base_url}/documents/track_status/{track_id}`;
- per-document upstream status normalizes via `_TERMINAL_UPSTREAM_STATES`:
  `"processed" → PROCESSED`, `"failed" → FAILED`, **everything else → IN_PROGRESS**
  (collapses LightRAG's pending/parsing/analyzing/processing/preprocessed);
- **aggregate:** empty docs → IN_PROGRESS; any IN_PROGRESS → IN_PROGRESS; else any FAILED →
  FAILED; else → PROCESSED;
- **COMPLETE ⇔ aggregate `IndexState.PROCESSED`** (the doc reached `"processed"` and no
  sibling is still in progress). `RealCellIndexClient.status` maps
  `PROCESSED→"PROCESSED"`, `FAILED→"FAILED"` (reading the FAILED reason ephemerally via the
  frozen `_fetch_failed_reason_ex`, consumed only to classify), else `"IN_PROGRESS"`; the
  executor's own poll loop adds a bounded `"TIMEOUT"`.

**Retry classification (frozen, unchanged):** `MAX_INDEX_ATTEMPTS_PER_OPERATION = 2`,
`MAX_GRAPH_INDEX_ATTEMPTS = 48`. Retry is decided by the frozen decision-twin
`index_retry08.is_transient_reason` on the **ephemeral** error text: transient →
retry (within cap); non-transient/`FAILED` deterministic → no retry; poll `TIMEOUT` →
transient (retry if budget allows); running out of polls → transient. **No retry is ever
triggered by a retrieval result** (retrieval quality is the evaluator's, not the driver's).
Polling is bounded (`max_polls`); there is no unbounded loop. `REINDEX = delete-then-insert`
semantics are honoured by the frozen retry path (documented in `indexlivepn02d.py:216`).

---

## 11. Real GD adapter (`RealPN02GDBackend`)

A thin adapter wrapping `gd_seam.GDQueryClient` to the PN02 `GDQueryBackend` Protocol.
Verified `gd_seam.GDQueryClient.query_evidence` (`gd_seam.py:139-220`):

- **endpoint / method:** `POST {base_url}/query/data`;
- **request JSON:** `{"query": question, "mode": "hybrid", "only_need_context": True}` (+
  optional `top_k`); `only_need_context=True` is sent explicitly and also forced upstream,
  so **no final-answer LLM** runs; **never** `client.query()` / `/query`;
- **auth:** `X-API-Key` header when configured;
- **timeout:** from `GraphRAGConfig.timeout`;
- **response:** `data.{entities, relationships, chunks, references}` — raw vendor schema
  lives only as a transient local, projected then dropped, never returned/logged/persisted.

The adapter constructs a **per-notebook** `GraphRAGConfig(base_url=<notebook sidecar>,
api_key=…)` (from the runtime manager) and returns
`GDBackendResult(candidate_source_ids=list(GDEvidence.source_ids),
latency_ms=GDEvidence.latency_ms)`. `GDQueryClient` is fakeable via an injected httpx
transport, so B0C-B tests exercise it against a mock `/query/data` with **zero** provider
traffic.

---

## 12. GD result normalization

Frozen provenance projection (already implemented in `gd_seam` + `normalizepn02`):

```
raw /query/data response
  → STRONG anchors only: chunks[].file_path + references[].file_path (canonical source_id, lossless)   (entity/relation provenance = PARTIAL → counts only, never a candidate)
  → benchmark_ids allowlist restriction (off-benchmark ids counted + dropped)
  → PN02 normalize_graph(..., allowlist=fixture.source_keys)   (foreign/malformed dropped + counted → feeds Stage-1 exact =0 gate)
  → GDEvidenceResult.evidence : UNORDERED NormalizedEvidencePN02 (schema forbids `ordered=True`)
```

`QUERY_DATA_EXPOSES_VALID_RANK = NO`, `QUERY_DATA_EXPOSES_VALID_SCORE = NO` (retained; the
round-robin interleave order is discarded, no score is read or invented). Current-notebook
membership validation is applied by the evaluator/removal layer, not by fabricating a rank.

**Provider cost accounting (§22).** A logical GD query is one `/query/data` call, but
LightRAG v1.5.6 internally may run the **keyword-extraction LLM** and query **embeddings**
(final-answer LLM is suppressed by `only_need_context=True`). The design reports the
**logical** GD-query count (cap 26) and does **not** claim one-to-one provider-call
accounting for LightRAG-internal work.

---

## 13. Exact real vector adapter (`RealPN02VectorBackend`) — the load-bearing seam

**Hard semantics (frozen):**
`PN02_VECTOR_BASELINE_CANDIDATE_UNIVERSE = CURRENT_NOTEBOOK_MEMBERS_ONLY`;
`GLOBAL_TOPK_THEN_POSTFILTER_ALLOWED = NO`; candidate restriction happens **before** ranking;
one embedding, K=3/K=5 are slices of one ranking.

**Vector-source forensic (verified):**

- `source_embedding` (`1.surrealql:16-20`): `source record<source>`, `order int`,
  `content string`, `embedding array<float>`; **one Source → many chunk rows**; **no stored
  dimension/model metadata** (dimension guarded at query time by
  `array::len(embedding)=array::len($query)`); cascade-deleted with the Source.
- Live `fn::vector_search` (`9.surrealql:2-66`): cosine
  `vector::similarity::cosine(embedding, $query)`, **`ORDER BY similarity DESC LIMIT
  $match_count` — GLOBAL top-K, no source-ID filter**, brute-force (no HNSW/`<|k|>` index).
  Returns `{id, parent_id, title, similarity, matches}` with a **genuine cosine**
  similarity (`math::max(similarity)` grouped per source).
- Member resolution: `SELECT VALUE in FROM reference WHERE out = $notebook_id`
  (`reference TYPE RELATION FROM source TO notebook`, `notebook.get_sources` `:36`).

**Approach A (frozen preferred) is NOT expressible without a production migration.** The
live `fn::vector_search` has no `$source_ids` parameter; adding one
(`WHERE source IN $source_ids AND … ORDER BY similarity DESC LIMIT k`) is *technically*
straightforward (brute-force cosine composes cleanly with a member prefilter) but requires a
**new migration** (count 50 → 51) — against PN02's eval-only, `PRODUCTION_IMPORTS_EVAL=NO`,
no-migration posture.

**Decision: implement frozen Approach B (exact equivalent), eval-side, no migration.**
`VectorBackend` already splits `embed_query` + `rank_members(query_embedding,
candidate_source_ids)`, and `rank_members` is exactly where the member restriction lives:

```
rank_members(query_embedding, candidate_source_ids):        # candidate_source_ids = CURRENT members, resolved BEFORE ranking
  rows = repo_query(
    "SELECT source, embedding FROM source_embedding
       WHERE source IN $ids AND embedding != NONE
         AND array::len(embedding) = array::len($q)",
    {"ids": [record ids of candidate_source_ids], "q": query_embedding})   # member-scoped fetch, NO ranking, NO global scan
  per_source_max = { source : max(cosine(chunk.embedding, query_embedding)) for chunk rows }   # eval-side cosine, mirrors math::max group-by
  return [source for source, _ in sorted(per_source_max.items(), key=score desc)]
```

- **Metric:** cosine, computed to match `vector::similarity::cosine` exactly (dot / (‖a‖‖b‖),
  IEEE-754 float64; a unit test pins parity against a direct `fn::vector_search` call over
  the same isolated rows on a member-only corpus).
- **Aggregation:** `max` per source, mirroring the DB's `math::max(similarity) GROUP BY id`.
- **Restriction before ranking:** the `WHERE source IN $ids` clause prunes non-members
  before any ordering; the executor's defense-in-depth (`vectorlivepn02d.py:179`) already
  drops any id outside the candidate universe. **A global-top-K-then-post-filter design is
  structurally impossible** — the backend never fetches a non-member row.
- **Removal re-probe:** the caller passes the **post-removal** member snapshot, so a removed
  Source is excluded from the candidate universe *before* ranking (not filtered post-hoc).
- **Equivalence to baseline:** same cosine over the same stored `source_embedding` chunks as
  `fn::vector_search`, only restricted to members — the exact notebook-local equivalent of
  the production similarity.

**Query-embedding path (§27).** `embed_query(question)` reuses the standalone
`utils/embedding.generate_embedding(question)` (`:204`) → `model_manager.get_embedding_model()`
→ the DB-configured `default_embedding_model`. **It can be invoked without the production
Ask flow** (a plain async function). Pinning: the run seeds a temp default embedding Model
`openai/text-embedding-3-small` (dim 1536) into the **Option-A isolated namespace** (reuse
`precheck08` model-seeding), so `generate_embedding` resolves to that model; B0C-B **asserts
the active default embedding model == text-embedding-3-small / dim 1536 before any query
embedding** (finding **L-4**). Query embeddings are the `VECTOR_QUERY_EMBEDDING` provider op,
counted one per vector query (cap 26). Provider authorization is enforced by the operation
allowlist + `QueryAuthorization`.

**Corpus prerequisite (finding M-1).** `rank_members` needs `source_embedding` rows to
exist. These are **not** produced by LightRAG graph indexing; they come from ON
`Source.vectorize()` / `embed_source_command`. B0C-B therefore provisions the corpus in the
isolated namespace before the vector phase — create 21 canonical Sources + 24 `reference`
edges, then vectorize (populate `source_embedding`). This is a **separate, bounded**
embedding workload (21 Sources) **outside** the 26 query-embedding cap; B1-R2 must
acknowledge it (finding **L-3**). Reuse: `isolation08` (temp namespace + normal-DB hard
guard), `precheck08` (model seed), `Source` domain + `embedding_commands` (create +
vectorize, in-process, no worker).

**Vector result:** `RealPN02VectorBackend` emits (via the executor →
`VectorEvidenceResult`): `query_id`, `notebook_id`, ranked canonical `source_ids`
(RANKED/`ordered=True`), genuine cosine values available in the trace, technical status,
latency, candidate-universe size. K3/K5 are slices of the one ranking.

---

## 14. Real delete adapter (`RealPN02DeleteBackend`)

A thin adapter wrapping the verified ON/LightRAG delete path to the PN02 `DeleteBackend`
Protocol. Verified `client.delete_document` (`client.py:283-353`):

- **endpoint / method:** `DELETE {base_url}/documents/delete_document`;
- **request JSON:** `{"doc_ids": [doc_id]}` where `doc_id = compute_doc_id(source_id)`;
- **success:** upstream `status == "deletion_started"` → `DeleteState.GONE`; `not_found` →
  GONE (idempotent); `busy` → `BUSY` (retryable); `not_allowed` → `REFUSED`; unexpected
  status → `GraphRAGProtocolError` (never assumed success);
- **completion:** **ASYNC** — the sidecar runs the delete in the background and returns
  immediately; `deletion_started` is acceptance, not proven completion.

The adapter maps `DeleteOutcome.state` to `DeleteResult(succeeded = state is GONE)`.
`derived_document_id` on the Protocol equals `compute_doc_id(canonical_source_id)`; the
adapter builds a per-notebook client bound to the removed notebook's base_url.

**Workspace safety (§29-§31, already frozen in `removallivepn02d`):** the delete target is
`(workspace_A, SH_AB, derived_doc_id)`; `MembershipRemovalExecutor.delete_membership`
refuses dispatch to any route whose `workspace_id != target.workspace_id`
(`CrossWorkspaceDeleteRefused`), even though the vendor `derived_document_id` is identical in
NB_B — so **A loses SH_AB, B keeps it**. Pre-dispatch checks: `notebook_id`, `workspace_id`,
`run_id` (per §8), derived-document mapping present.

**Delete-failure backstop (§31).** Even if the async delete does not converge and A's stale
store still returns SH_AB, the ON canonical-membership `post_validate`
(`membershippn02.evaluate_removal`) drops it →
`STALE_GRAPH_EVIDENCE_ACCEPTED_AS_VALID = 0` regardless of delete success. A real adapter
failure never restores membership.

---

## 15. Live orchestrator & `execute-b1` entrypoint

**Reuse the SAME orchestrator (Option A), no separate scientific path.** The future real
run must reuse `driverpn02d.B1OfflineDriver` with **real** `B1DriverDeps` — not a second
scientific implementation. The only wrapper is a thin **`RealB1Driver`** (`driver_live_pn02`)
that:

1. runs the provider-free preflight (Stage P) and mints `RealLightRAGPreflightAuthorization`;
2. mints the real `PN02ProviderRunAuthorization` (§7) from an operator `run_id`;
3. owns resources: opens the Option-A isolated namespace, seeds the temp Model, provisions
   the corpus, boots the 3 provider-bound runtimes (Stage X), builds the real route table
   (real endpoints) + real backend factories;
4. invokes the **existing** `B1OfflineDriver.run(...)`; and
5. runs owned-only cleanup in `finally`.

The scientific logic stays entirely inside the reused orchestrator + `evaluatepn02`.

**`execute-b1-live` CLI verb (net-new, in a dedicated live CLI module).** It **MUST**
require an explicit immutable operator authorization input (`--run-id` + an authorization
token/manifest path) and **MUST** refuse to run when: `PN02_PROVIDER_RUN_AUTHORIZED` is not
set for this run, a simulation authorization is presented, the run manifest is missing, or
the git baseline is dirty/unapproved. It follows repository CLI conventions
(`argparse` sub-command, like `liveclipn02d`), but lives in a **separate** module so the
offline CLI keeps its no-provider guarantee and its `test_cli_has_no_execute_b1_verb`
invariant.

---

## 16. Cleanup

Owned-only, idempotent, `try/finally` (reusing `RealTopology.cleanup` +
`isolation08` guarded cleanup patterns): stop + `docker rm -f` every run-owned container;
remove every run-owned network + published port; LightRAG per-derived-id deletes for owned
documents; drop the run-owned temp Surreal namespace (`REMOVE NAMESPACE IF EXISTS`, hard
`assert_not_normal` guard); restore + verify env (temp default Model removed with the
namespace); delete run-owned storage roots. Cleanup touches **only** run-owned identities;
the normal DB is never mutated (`NORMAL_DB_MUTATIONS = 0`). Partial-startup failures trigger
the same owned-only cleanup.

---

## 17. Secrets & network boundaries

**Secrets.** `OPENROUTER_API_KEY` (and `LIGHTRAG_API_KEY`) are resolved **late** at the
container-launch boundary and injected by env inheritance (`-e NAME`, value in subenv), never
on argv, never in a manifest/log/exception/artifact/git/test snapshot. Only secret **NAMES**
appear anywhere. The LightRAG `X-API-Key` header is added only when configured and never
logged. Content-safe artifacts carry public config + names only.

**Network.** B0C-B itself performs **`EXTERNAL_PROVIDER_NETWORK_CALLS = 0`** and
**`REAL_PROVIDER_CALLS = 0`**: all real adapters are tested against **mock HTTP** (injected
httpx transport for GD; mock `GraphRAGService`/track reader for index/delete) and **fake DB +
fake embedding provider** for vector. Local isolated test servers are allowed only if clearly
recorded and owned. The external OpenRouter network is reached only under a future
operator-authorized B1 run, never in B0C-B.

---

## 18. B0C-B module plan (proposed; no files created now)

Eval-only, under `open_notebook/integrations/graphrag/eval/` (repository convention;
`PRODUCTION_IMPORTS_EVAL = NO`):

| Module | Responsibility | Reused source | Network in B0C-B tests |
|---|---|---|---|
| `runtimelivepn02d.py` | `RealPN02RuntimeManager`: allocate port/net/storage, boot 3 provider-bound runtimes, attest, build real route table, owned cleanup | `cell_provisioner08`, `realsidecarpn02d` (attest), `attestpn02d`, `provbindpn02d` | command-generation only (no start) |
| `corpuslivepn02d.py` | `RealPN02CorpusProvisioner`: Option-A namespace, seed temp Model, create 21 Sources + 24 reference edges, vectorize | `isolation08`, `precheck08`, `Source`, `embedding_commands` | fake DB + fake embed |
| `indexadapterpn02d.py` | `NotebookRuntimeRoute → RealCellIndexClient` factory (real base_url) | `live_indexer08.RealCellIndexClient`, `service`, `client` | mock HTTP |
| `gdadapterpn02d.py` | `RealPN02GDBackend` wrapping `gd_seam.GDQueryClient` per-notebook | `gd_seam`, `normalizepn02` | mock `/query/data` transport |
| `vectoradapterpn02d.py` | `RealPN02VectorBackend` (Approach B: member-scoped fetch + eval cosine; `generate_embedding`) | `utils/embedding`, `repo_query`, `source_embedding` | fake DB + fake embed |
| `deleteadapterpn02d.py` | `RealPN02DeleteBackend` wrapping `client.delete_document(compute_doc_id())` | `service`, `client` | mock DELETE transport |
| `authmintlivepn02d.py` | real `PN02ProviderRunAuthorization` mint from a real preflight + operator run_id; run_id cross-check helpers | `authlivepn02d`, `attestpn02d`, `preflightpn02d` | none |
| `driver_live_pn02d.py` | `RealB1Driver` thin wrapper: preflight → mint → own resources → reuse `B1OfflineDriver` → cleanup | `driverpn02d` (unchanged) | none (assembled from the above) |
| `cli_live_pn02d.py` | `execute-b1-live` verb (authorization-gated; refuses simulation/dirty/missing-manifest) | `driver_live_pn02d` | none |
| `test_*` | mock/contract tests (§19) | — | mock only |

Existing B0B modules edited minimally (§8): add executor-init `isinstance` guard + per-op
run_id cross-check (additive guards).

---

## 19. Mock / contract test strategy (zero provider traffic)

- **RealIndexBackend** against a **mock `GraphRAGService`/track reader**: submit accepted →
  track PROCESSED → COMPLETED; track FAILED (transient text) → bounded retry; FAILED
  (non-transient) → no retry; poll TIMEOUT → transient.
- **RealGDBackend** against a **mock `/query/data`** (injected httpx transport): STRONG-anchor
  projection, allowlist restriction, unordered set, `only_need_context` asserted in body,
  content-free error mapping.
- **RealDeleteBackend** against a **mock DELETE** endpoint: `deletion_started`/`not_found` →
  succeeded; `busy`/`not_allowed`/unexpected → not succeeded; cross-workspace refusal.
- **RealVectorBackend** against a **fake DB** (member `source_embedding` rows) + **fake
  embedding provider**: member-only fetch, eval cosine parity, restriction-before-ranking,
  removal snapshot, no non-member leakage.
- **Provider binder** with **dummy env NAMES** (no real key), secret-name presence only.
- **Runtime manager** boot-command generation (published port, injected binding, secret by
  inheritance) — string/argv assertions, no container start.
- **Authorization / run_id negative tests**: simulation auth rejected by `execute-b1-live`;
  run_id mismatch fails closed; missing preflight fails closed; wrong provider fingerprint
  fails closed.
- **Cleanup tests**: owned-only, idempotent, normal-DB untouched, temp namespace dropped.

**Contract tests (§41).** Each real adapter (mock-backed) must pass the **same abstract
behaviour suite** as its B0B fake (`FakeCellIndexClient` / `FakeGDBackend` /
`FakeVectorBackend` / `FakeDeleteBackend`) — e.g. `FakeIndexBackend` and
`RealIndexBackend(mock HTTP)` satisfy one shared `CellIndexClient` contract test — so the
offline and live paths cannot drift.

Invariants: `EXTERNAL_PROVIDER_NETWORK_CALLS_DURING_B0C_B = 0`,
`REAL_PROVIDER_CALLS = 0`, `NORMAL_DB_MUTATIONS_DURING_TESTS = 0`,
`PRODUCTION_IMPORTS_EVAL = NO`.

---

## 20. Mandatory Codex review process (B0C-B code phase)

B0C-A is docs/design only → **no Codex code review required here**. For **B0C-B** (and every
later code phase) the frozen governance is:

```
CLAUDE_CODE_IMPLEMENTATION
  → STOP
  → CODEX_INDEPENDENT_CODE_REVIEW
  → CLAUDE_REMEDIATION_OF_CODEX_FINDINGS
  → CODEX_RE_REVIEW
  → (only if CODEX_PASS)
  → OPERATOR_CHECKPOINT_APPROVAL
```

Claude's internal agent review is **supplementary only** and does not replace Codex. **A
B0C-B checkpoint is forbidden without Codex PASS.** Codex must explicitly inspect: real
runtime manager, provider binding, authorization mint path, run_id binding, RealIndexBackend,
RealGDBackend, RealVectorBackend, RealDeleteBackend, live entrypoint, budget enforcement,
workspace routing, shared-Source deletion, secret handling, network boundaries, cleanup, and
scientific-evaluator reuse.

---

## 21. B1-R2 prerequisites (future)

A future **B1-R2** (zero provider traffic) must verify: live entrypoint exists; the four real
adapters + runtime manager + corpus provisioner + real mint path exist; all mock/contract
integration tests pass; **Codex review PASS**; a B0C-B implementation checkpoint (commit +
tag); clean tree; fixture hash `9ce7df74…6899a6`; provider-config fingerprint
`pbf_1811d0bfd5cfad2743ffaa69`; the **two-boot** runtime lifecycle (finding M-2) and the
corpus-vectorization workload (findings M-1/L-3) explicitly acknowledged; workload caps
24/48/1/26/26/26 with final-answer/client-query/judge = 0; concurrency 1 (index/GD/vector);
synthetic-only; cleanup readiness. Only then may a new one-run authorization be requested
(new run_id). B0C-B passing tests + Codex does **not** set `PN02_PROVIDER_RUN_AUTHORIZED`.

---

## 22. Contract matrix (Protocol → future concrete real implementation)

| B0B Protocol / interface | Method(s) | Future real impl | Feasibility |
|---|---|---|---|
| `CellIndexClient` (index backend) | `submit`, `status` | `RealCellIndexClient` via `route→CellEndpoint` factory | **Mapped** (`live_indexer08:348`, `service.index_source`, `POST /documents/text`) |
| index completion | `status().state == PROCESSED` | `track_status` aggregate PROCESSED | **Mapped** (`client.py:494`) |
| `GDQueryBackend` (GD backend) | `query_evidence` | `RealPN02GDBackend` over `gd_seam.GDQueryClient` | **Mapped** (`POST /query/data`, `only_need_context`) |
| `VectorBackend` (vector backend) | `embed_query`, `rank_members` | `RealPN02VectorBackend` (Approach B, eval-side cosine over member `source_embedding`) | **Mapped** (no migration) |
| query embedding | `embed_query` | `generate_embedding` pinned to seeded temp Model | **Mapped** (`embedding.py:204`) |
| `DeleteBackend` (delete backend) | `delete_document` | `RealPN02DeleteBackend` over `client.delete_document(compute_doc_id())` | **Mapped** (`DELETE /documents/delete_document`) |
| runtime / router | boot + attest + route | `RealPN02RuntimeManager` (cell_provisioner08 + attestpn02d) | **Mapped** |
| provider binding | materialize + inject | `provbindpn02d` + `cell_provisioner08` injection | **Mapped** |
| authorization minting | real mint | `authmintlivepn02d` from real Gate 0/1 preflight + run_id | **Mapped** |
| corpus provisioning (new) | Source create + vectorize | `RealPN02CorpusProvisioner` (isolation08 + precheck08 + embedding_commands) | **Mapped** |
| cleanup | owned-only teardown | `RealTopology.cleanup` + `isolation08` guarded drop | **Mapped** |

**No Protocol is left without a concrete, technically-feasible mapping — there is no
blocker.**

---

## 23. No scientific-methodology drift

Retained exactly: 24 memberships · 24 baseline GD · 24 baseline vector · 2 removal GD · 2
removal vector · 1 graph delete · K = 3/5 · candidate universe notebook-local · R0-R4 ·
Q0-Q3 · M0-M3 · Stage-1 safety gates · **no B2**. B0C is execution plumbing, not a new
experiment. The evaluator (`evaluatepn02`) and result schemas (`schemaspn02`) are unchanged.

---

## 24. Independent design review (§55)

| # | Question | Finding | Verdict |
|---|---|---|---|
| A | Every B0B Protocol maps to a real impl? | §22 contract matrix — all mapped | PASS |
| B | Is `RealCellIndexClient` actually reusable? | Yes; only a `route→endpoint(base_url)` factory is net-new | PASS |
| C | Index completion source-verified? | Yes (`track_status` aggregate PROCESSED, `client.py:494`) | PASS |
| D | `/query/data` request/response verified? | Yes (`gd_seam` + `client`; `only_need_context`, STRONG-anchor projection) | PASS |
| E | Provenance maps safely to canonical source ids? | Yes (chunk/reference `file_path` = source_id, allowlist-restricted) | PASS |
| F | Notebook-local vector exact, not approximate? | Yes — Approach B, member-scoped fetch before ranking, exact cosine | PASS |
| G | Delete guaranteed workspace-local? | Yes (`CrossWorkspaceDeleteRefused` + endpoint-scoped route) | PASS |
| H | Provider-bound runtime lifecycle technically valid? | Yes — two-boot; provider-bound = cell_provisioner08 published-port | PASS (finding M-2) |
| I | Can auth mint before required preconditions? | No — real preflight → mint → binding → boot; provider-free preflight precedes mint | PASS |
| J | Can a run_id mismatch pass? | Closed by finding L-1 (per-op cross-check) | PASS (requirement) |
| K | Can a real backend be used without a capability? | Closed by finding L-2 (executor-init `isinstance`) | PASS (requirement) |
| L | Can B0C-B tests contact a provider? | No — mock HTTP + fake DB/embed only | PASS |
| M | Can secrets leak? | No — env-inheritance, names only, redaction | PASS |
| N | Does the live entrypoint bypass the workload guard? | No — reuses `StatefulBudgetGuard` via the reused orchestrator | PASS |
| O | Can scientific logic diverge from PN02B? | No — same orchestrator + `evaluatepn02`; thin wrapper only | PASS |
| P | Is cleanup run-owned only? | Yes — owned identities, normal DB untouched | PASS |
| Q | Is Codex review mandatory before B0C-B checkpoint? | Yes (§20) | PASS |

**Findings:** HIGH = 0; MEDIUM = 2 (**M-1** corpus-provisioning prerequisite — resolved as a
required B0C-B component via reuse; **M-2** two-boot lifecycle — resolved, B1-R2 to
acknowledge); LOW = 4 (**L-1** run_id cross-check, **L-2** executor-init `isinstance`, **L-3**
corpus-vectorization workload outside the 26 query-embedding cap, **L-4** attest active
embedding model/dim before query embeddings — all resolved as explicit B0C-B requirements /
acknowledgements). No unresolved HIGH/MEDIUM design finding.

`INDEPENDENT_DESIGN_REVIEW = PASS`.

---

## 25. Decision

**`B0C_A_DECISION = D — REAL_WIRING_DESIGN_FROZEN_AND_B0C_B_IMPLEMENTATION_JUSTIFIED`.**

Justification: all real adapter seams are mapped to concrete, source-verified,
technically-feasible implementations with exact APIs identified (index `/documents/text` +
track, GD `/query/data`, delete `/documents/delete_document`, vector Approach B, membership
`reference`, provider binding, real preflight/mint); the runtime lifecycle is frozen
(two-boot, source-verified); the authorization/mint flow, run_id binding, provider binding,
index completion, GD request/response mapping, exact vector path, delete path, CLI path, test
strategy, and Codex review gate are all frozen; no seam is unmappable and no vendor/source
constraint blocks the design (the one wrinkle — no DB member prefilter — is resolved eval-side
without a migration). Implementation is justified as the natural successor to the approved
B0A→B0B→B1-R chain whose only remaining gap is the absent real wiring.

**Retained unchanged:** `PN02_PROVIDER_RUN_AUTHORIZED = NO`; `PN02D_B1_R2 = NOT_STARTED`;
`PN02D_B2_QA_AUTHORIZED = NO`; `GRAPHRAG_PRODUCTION_INTEGRATION = NOT_APPROVED`;
`LIGHTRAG_ASK_INTEGRATION = NOT_APPROVED`; `GRAPH_RAG_09_JUSTIFIED = NO`; Boundary B
(`SYNTHETIC_ONLY = true`).

**Checkpoint (operator-approved, DESIGN ONLY):** this document is frozen at the annotated
tag `graphrag-pn02db0ca-real-provider-wiring-design-approved`. The tag means the concrete
real provider/runtime/index/GD/vector/delete/live-entrypoint architecture is
source-forensically mapped and B0C-B **offline** implementation is justified. It does **not**
mean real adapters are implemented, provider traffic is authorized, or B1/B2/production
integration is approved. B0C-B remains a separate phase gated on the mandatory Codex review
process (§20), and a future B1-R2 + a new operator authorization + a new run_id are required
before any provider-backed run.
