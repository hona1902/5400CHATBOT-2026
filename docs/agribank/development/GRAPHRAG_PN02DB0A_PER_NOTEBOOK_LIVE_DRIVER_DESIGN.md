# GraphRAG-PN02D-B0A — Per-Notebook Provider Live-Driver Design

**Status: `DESIGN / FORENSIC COMPLETE` + `REFINED (B0A-R1)` — design frozen; offline implementation (PN02D-B0B) justified. NO CODE, ZERO provider traffic, no real LightRAG boot, no indexing, no embeddings, no `/query/data`, no `client.query`, no vector calls, no final answers, no Ask integration, no GraphRAG-09. NOT checkpointed — operator review required.**

**Refinement B0A-R1 (pre-checkpoint):** three load-bearing ambiguities resolved in **§R1** —
(1) canonical-Source ↔ workspace-local document identity (endpoint is not identity; verified
vendor doc_id semantics); (2) exact notebook-local vector baseline (candidate universe =
current members, pre-ranking; no global-topK-then-postfilter); (3) provider-binding /
authorization ordering (authorization precedes binding materialization).

This document designs the *missing* provider-backed PN02 execution driver whose absence
fail-closed-blocked PN02D-B1 (`B1_BLOCKER = MISSING_APPROVED_LIVE_DRIVER`). It is a
**design-only** gate: it specifies exactly what B0B may build offline (with zero provider
traffic) and what a later re-authorized B1 would run. It builds nothing.

---

## 0. Authoritative baseline (verified from git)

| Item | Expected | Observed | Result |
|---|---|---|---|
| Branch | `feature/graphrag-lifecycle` | `feature/graphrag-lifecycle` | ✅ |
| HEAD | `162b3d1474d21b566e636f1fe0202e3ae085c8b0` | `162b3d1474d21b566e636f1fe0202e3ae085c8b0` | ✅ |
| Tag | `graphrag-pn02db1-live-driver-blocked` | present, annotated | ✅ |
| Tag peel | `162b3d1474d21b566e636f1fe0202e3ae085c8b0` | `162b3d1474d21b566e636f1fe0202e3ae085c8b0` | ✅ |
| Working tree | CLEAN | CLEAN | ✅ |

Retained governance (unchanged by this design gate):

```
GraphRAG-08                        = CLOSED / APPROVED
PN01                               = APPROVED
PN02A                              = APPROVED
PN02B                              = APPROVED
PN02C                              = APPROVED
PN02D-A                            = APPROVED
PN02D-B1                           = BLOCKED — LIVE DRIVER ABSENT
PN02D-B0A                          = DESIGN (this document)
PN02D-B0B                          = NOT_STARTED
PN02D-B2                           = NOT_AUTHORIZED
PN02_PROVIDER_RUN_AUTHORIZED       = NO
GRAPHRAG_PRODUCTION_INTEGRATION    = NOT_APPROVED
LIGHTRAG_ASK_INTEGRATION           = NOT_APPROVED
GRAPH_RAG_09_JUSTIFIED             = NO
```

## 1. Authoritative fixture (retained, not redesigned)

```
PN02_FIXTURE_NAME            = graphrag_pn02_eval_v1
PN02_FIXTURE_HASH            = 9ce7df742810424d9ef7b7b34962187d9061245f81471f5fb1d7f2608f6899a6
NOTEBOOK_COUNT               = 3        (NB_A / NB_B / NB_C)
CANONICAL_SOURCE_COUNT       = 21       (18 unique A1-6/B1-6/C1-6 + 3 shared SH_AB/SH_AC/SH_BC)
WORKSPACE_MEMBERSHIP_COUNT   = 24       (18 unique + 6 shared edges)
QUERY_COUNT                  = 24       (8 classes × 3 notebooks)
VECTOR_K_VALUES              = {3, 5}
```

The fixture is frozen in `tests/fixtures/graphrag_pn02_eval_v1/{corpus,queries,freeze}.json`
and enforced by `datasetpn02.py` (`FROZEN_*` constants `datasetpn02.py:36-46`;
`_validate_frozen_counts` `:437-500`). This design does not touch the frozen experiment.

## R1. Design refinement (B0A-R1, pre-checkpoint — docs/semantics only)

The B0A architecture is accepted in principle; this refinement removes three load-bearing
ambiguities before checkpoint. It changes **no** experiment design, adds **no** code, and
authorizes **no** traffic. Retained (provisionally): `B0A_DECISION=C`,
`PN02D_B0B_IMPLEMENTATION_JUSTIFIED=YES`, `PN02_PROVIDER_RUN_AUTHORIZED=NO`,
`PN02D_B1_REAUTHORIZATION=NOT_YET`, `PN02D_B2_QA_AUTHORIZED=NO`. Not checkpointed.

### R1.1 — Three separate identities
`CANONICAL_SOURCE_ID` (ON-owned, cross-notebook stable) · `WORKSPACE_ID` (notebook-local
LightRAG namespace/runtime) · `DERIVED_LIGHTRAG_DOCUMENT_ID` (workspace-local indexed
representation). Never conflated. See §13.

### R1.2 — Endpoint is not identity
`ENDPOINT_IDENTITY_IS_DOCUMENT_IDENTITY = NO`. A local endpoint/port is **runtime routing
state** that may change after restart / reprovision / new run id. A derived document must not
become unlocatable because the endpoint changed. "Reached through that endpoint" means: the
vendor document id is resolved/used *inside the workspace reached through* the attested
endpoint — the endpoint/port is **not** part of the persistent document identity.

### R1.3 — Vendor doc_id semantics (verified, LightRAG v1.5.6 — not guessed)
- **Input hashed:** the `file_source` field. ON always supplies `file_source =
  canonical_source_id` (POST `/documents/text` body `{text, file_source: source_id}`,
  `client.py:268`). Title / content-hash / notebook ids are **not** sent (`client.py:256-261`).
- **Formula:** `doc_id = "doc-" + md5(file_source)` (`client.py:80`; LightRAG
  `pipeline.py:936-946` → `compute_mdhash_id` → single-arg md5, per `client.py:64-71`).
- **Deterministic:** YES.
- **Same content across two workspaces → same vendor doc_id?** YES — but because both use the
  same `file_source` (= same `canonical_source_id`), **not** because of content. Content is
  irrelevant to the id.
- **Does a content change alter the vendor doc_id?** **NO** — the id is content-independent
  (`client.py:66,74`). This is what lets an idempotent delete-then-insert target the right
  document.

### R1.4 — Workspace-local logical key
Authoritative lookup key = **`(workspace_id, canonical_source_id)`**. For shared `SH_AB`:
`(workspace_A, SH_AB) → derived doc A` and `(workspace_B, SH_AB) → derived doc B`. They may
share a vendor doc_id (same `file_source`); that is safe **only** because they live in
isolated workspaces. The PN driver's logical identity is always the pair, never the vendor id
alone and never the endpoint.

### R1.5 — Document-id mapping record (derived eval state, content-safe)
B0B maintains a run-owned mapping (NOT canonical ON state, no secrets):

```
DocMappingRecordPN02:
  canonical_source_id
  workspace_id
  derived_document_id            # vendor doc_id = "doc-"+md5(source_id)
  content_identity               # optional content hash, only if a future update test needs it
  index_operation_id
  index_status                   # submit/track terminal state
```

### R1.6 — Delete uses workspace + document
`delete_target = attested workspace/runtime + derived document id for that membership`. A
delete for `(workspace_A, SH_AB)` must not affect `(workspace_B, SH_AB)` even when the vendor
doc_ids are identical: **endpoint routing selects the isolated workspace; document identity
selects the derived document within it** (§29). A delete against a shared/global base_url is a
structural refusal (`FAILED_MEMBERSHIP_REMOVAL`).

### R1.7 — Update limitation (scope unchanged)
`SOURCE_UPDATE_TEST_INCLUDED = NO` — B1 tests membership removal, not content update; B0A scope
is not expanded. Note for a *future* phase: because the vendor doc_id is `file_source`-derived
and **content-independent** (R1.3), a content change keeps the same `(workspace_id,
canonical_source_id)` key and the same vendor doc_id, so the mapping stays resolvable; the
update mechanism would be **delete-then-insert** (a re-POST of the same `file_source` is a 409
duplicate reject, `client.py:186-195`; `lifecycle.py:76-222`), not a doc_id change.

### R1.8 — Vector: exact notebook-local semantics
`PN02_VECTOR_BASELINE_CANDIDATE_UNIVERSE = CURRENT_SOURCES_OF_QUERIED_NOTEBOOK_ONLY`;
`GLOBAL_TOPK_THEN_POSTFILTER_ALLOWED = NO`; `VECTOR_EXACT_IMPLEMENTATION_APPROACH = A`
(member-ID pre-ranking candidate filter over `source_embedding`, genuine DB cosine, no
truncating inner LIMIT; approach B = member-embedding-subset scoring is an exact equivalent).
`VECTOR_K3_K5_ONE_RANKING = YES`. Caps unchanged: `MAX_VECTOR_QUERY_OPERATIONS = 26`,
`MAX_QUERY_EMBEDDING_OPERATIONS = 26`. Full detail §21; removal §28.

### R1.9 — Authorization ordering
`RealLightRAGPreflightAuthorization → PN02ProviderRunAuthorization → provider-binding
materialization → index/query operation authorization → provider-backed operation` (§6).
`PROVIDER_AUTHORIZATION_PRECEDES_BINDING = YES`.
`PROVIDER_BOUND_EXECUTION_WITHOUT_CAPABILITY_POSSIBLE = NO` (capability object required, not a
boolean; §18).

### R1.10 — B0B provider binding stays inert
`B0B_PROVIDER_TRAFFIC = 0`. B0B may implement binding data structures, safe config
fingerprints, env-var-name contracts, secret-presence interfaces, a fake provider binding,
command construction, and authorization checks — but must **not** read/use a provider secret
for a live request, test credentials against a provider, boot a provider-bound LightRAG, or
send any embedding/LLM call (§17/§39).

### R1.11 — Provider config fingerprint excludes secret material
The committed fingerprint uses only non-secret identity: provider class/name, LLM model name,
embedding model name, embedding dimension, config version. **No** API-key value, **no**
Authorization value; secret **values** are never hashed into a committed artifact. The secret
env-var **name** (`OPENROUTER_API_KEY`) may appear as a name only (§9/§19/§32).

### R1.12 — Refinement independent review (§21 A–I)

| # | Challenge | Resolution |
|---|---|---|
| A | Does derived document identity depend on ephemeral endpoint/port? | No — identity is `(workspace_id, canonical_source_id)`; endpoint is routing only (R1.2/§13). |
| B | Can SH_AB deletion in A accidentally target B? | No — delete routed to A's attested endpoint; vendor id used only inside A's workspace (R1.6/§29). |
| C | Can a Source content update make the mapping unresolvable? | No — vendor id is content-independent (R1.3); `(workspace_id, canonical_source_id)` is stable; update = delete-then-insert (R1.7). |
| D | Does the vector baseline truly rank only current notebook Sources? | Yes — candidate universe = current members, restricted before ranking (R1.8/§21). |
| E | Could global top-K + post-filter sneak back in? | No — explicitly forbidden; the exclusion is in the candidate universe, not post-hoc (R1.8/§21/§28). |
| F | Are K=3/K=5 produced from one notebook-local ranking? | Yes — one embedding, one ranked member list, K are slices (R1.8/§21). |
| G | Can provider configuration become live before operator authorization? | No — binding materialization is unreachable before `PN02ProviderRunAuthorization` (R1.9/§6). |
| H | Could B0B contact a provider accidentally? | No — `B0B_PROVIDER_TRAFFIC=0`; injected seams + fakes + autouse socket guard (R1.10/§39/§41). |
| I | Does the design still keep scientific rules only in the PN02B evaluator? | Yes — driver emits records only; `evaluatepn02` owns all R/Q/M/Stage-1/grading (§42). |

**Refinement review: HIGH = 0, MEDIUM = 0, LOW = 0** unresolved.

### R1.13 — Refinement final report

```
GRAPH_RAG_PN02DB0A_DESIGN_REFINEMENT          = COMPLETE
CANONICAL_SOURCE_IDENTITY_DEFINED             = YES
WORKSPACE_LOCAL_DOCUMENT_IDENTITY_DEFINED     = YES
ENDPOINT_IDENTITY_IS_DOCUMENT_IDENTITY        = NO
WORKSPACE_SOURCE_LOGICAL_KEY                  = (workspace_id, canonical_source_id)
VENDOR_DOC_ID_SEMANTICS_VERIFIED              = YES   (doc-+md5(file_source); content-independent)
SHARED_SOURCE_DELETE_ISOLATION_DEFINED        = YES
VECTOR_CANDIDATE_UNIVERSE                      = CURRENT_NOTEBOOK_MEMBERS_ONLY
GLOBAL_TOPK_THEN_POSTFILTER_ALLOWED           = NO
VECTOR_EXACT_IMPLEMENTATION_APPROACH          = A (member-ID pre-ranking filter; B = exact equivalent)
VECTOR_K3_K5_ONE_RANKING                       = YES
MAX_VECTOR_QUERY_OPERATIONS                     = 26
MAX_QUERY_EMBEDDING_OPERATIONS                  = 26
PROVIDER_AUTHORIZATION_PRECEDES_BINDING        = YES
B0B_PROVIDER_TRAFFIC                            = 0
PROVIDER_BOUND_EXECUTION_WITHOUT_CAPABILITY     = NO
SOURCE_UPDATE_TEST_INCLUDED                     = NO
B0A_DECISION                                    = C
PN02D_B0B_IMPLEMENTATION_JUSTIFIED              = YES
PN02_PROVIDER_RUN_AUTHORIZED                    = NO
PN02D_B1_REAUTHORIZATION                        = NOT_YET
```

## 2. Purpose

Design (only) the provider-backed PN02 driver that could later perform B1 safely. The
driver must eventually: boot **three** notebook-specific real LightRAG runtimes/workspaces;
perform **24** membership-derived graph index operations; make attestation-gated
`/query/data` calls; run a notebook-scoped vector baseline; validate membership removal;
enforce the workload cap; normalize results into the already-approved PN02B evaluator
schemas; and clean up fail-closed. **B0A implements none of these.**

## 3. Forensic — the current seams (source-verified)

All modules below are under `open_notebook/integrations/graphrag/` (eval subpackage where
noted). Every `*pn02*` / `*08` / `eval/*` module is EVALUATION-ONLY
(`PRODUCTION_IMPORTS_EVAL = NO`; dependency direction eval → production only).

### 3.1 The gate the driver chains from (present)

| Seam | Location | What it is |
|---|---|---|
| `RealLightRAGPreflightAuthorization` | `eval/attestpn02d.py:188-210` | Unforgeable capability (`__slots__=(fixture_hash, run_id, runtime_count)`; ctor rejects any key that is not the module-private `_AUTH_KEY` `:185,203-207`). Certifies Gate 0 + Gate 1 real-preflight PASS. Authorizes **no** provider run by itself. |
| mint | `eval/attestpn02d.py:213-229` | `mint_real_preflight_authorization(...)` returns the capability iff `gate0_passed and gate1_passed`, else `None`. Bound to `EXPECTED_FIXTURE_HASH` (`preflightpn02d.py:78-80`). |
| require (fail-closed) | `eval/attestpn02d.py:232-256` | `require_real_preflight_authorization` raises `IndexingGateBlocked` unless a genuine capability is presented; `assert_indexing_gated` is the structural proof helper (indexes nothing). |

### 3.2 The inert seams the driver must replace (present)

`eval/live_seam_pn02.py` (frozen flags `PN02_LIVE_AUTHORIZED=False`,
`STRUCTURED_EVIDENCE_IMPLEMENTATION_READY=False`, `PRODUCTION_IMPORTS_EVAL=False`) defines
the **Protocol** interface contract plus deliberately non-invokable stubs:

| Protocol | Signature | Inert stub |
|---|---|---|
| `MembershipIndexerSeam` | `index_membership(workspace_id, source_key, text) -> None` (`:35-41`) | `InertMembershipIndexer` raises `LiveExecutionNotAuthorized` (`:85-87`) |
| `GDQuerySeam` | `async query_evidence(attestation: WorkspaceAttestation, question, *, benchmark_ids=None) -> GDEvidenceResult` (`:44-54`) | `InertGDQuerySeam` (`:90-98`) |
| `VectorQuerySeam` | `async query(notebook_id, question, *, k) -> VectorEvidenceResult` (`:57-63`) | `InertVectorQuerySeam` (`:101-104`) |
| `FinalAnswerSeam` | `async answer(notebook_id, question, evidence_source_ids) -> QAAnswerResult` (`:66-72`) | `InertFinalAnswerSeam` (`:108-112`) — **B2 only, out of B1 scope** |

The B1 driver implements the first three Protocols with real backends; `FinalAnswerSeam`
stays inert (B2 is a separate authorization path, §43).

### 3.3 PN02B evaluator (present — the scientific owner; driver must NOT duplicate)

| Seam | Location | Role |
|---|---|---|
| Result schemas | `eval/schemaspn02.py` | `VectorEvidenceResult` (ordered, `:68-86`), `GDEvidenceResult` (**unordered set**, `:89-108`), `RemovalProbeResult` (`:143-158`), `QAAnswerResult` (`:115-136`), `TechnicalOutcome` (`:29-39`), `ScienceVerdict` (`:42-48`). The records the driver emits. |
| Normalization | `eval/normalizepn02.py` | `normalize_vector` (ordered, `:106-115`), `normalize_graph` (unordered, `:118-126`) → `NormalizedEvidencePN02` + `ProvenancePN02`. Canonical Source id = membership in `fx.source_keys` allowlist (`:87`). |
| Workload ledger | `eval/workloadpn02.py` | Caps (`:22-46`); `WorkloadLedger.check_cap(name, observed, cap)` (`:132-139`) is a **stateless pre-op guard** (`observed > cap`) — there is **no** consume/charge/reserve accumulator. |
| Stage-1 gate | `eval/stage1pn02.py` | Unforgeable `Stage1Authorization` (`_AUTH_KEY :37`, ctor `:40-55`), minted only on PASS in `evaluate_stage1` (`:170-188`), enforced by `require_stage2_authorization` (`:191-202`). Exact `=0` isolation gates. |
| Decision engines | `eval/decisionspn02.py` | R0-R4 `retrieval_decision` (`:49-82`), Q0-Q3 `qa_decision` (`:135-170`), M0-M3 `multihop_decision` (`:184-200`) — mechanical first-match tables. |
| Metrics / grading | `eval/metricspn02.py` | Set/count formulas + deterministic QA grader. No classifier, no fabricated rank. |
| Orchestrator (offline) | `eval/evaluatepn02.py` | `run_offline_evaluation(...)` (`:146-210`): Stage-1 gate → (guarded) Stage-2. Consumes deterministic records; no retriever/provider. |
| Membership removal | `eval/membershippn02.py` | `post_validate(returned, current_members) = returned ∩ current_members` (`:35-43`); `evaluate_removal → RemovalReport` (`:65-118`); reprobe probes (`:121-152`). |
| Manifest / routing / attestation / Boundary-B | `eval/manifestpn02.py` | `workspace_id_for = "nb_"+sha256(record_id)[:16]` (`:82-87`), `routing_manifest` (`:103-120`), `WorkspaceAttestation` + `attest_workspace` + `require_attested_before_query` (`:127-200`), `RunManifest`+`build_run_manifest` (`:241-302`), `validate_boundary_b` (`:214-227`). |
| OFFLINE CLI | `eval/offlinepn02.py` | `validate` / `hash` / `evaluate-results` — **deliberately no `run-benchmark` verb**, no HTTP client. |

### 3.4 PN02C/PN02D-A live topology (present — reusable)

| Seam | Location | Role |
|---|---|---|
| Real sidecar (provider-free) | `eval/realsidecarpn02d.py` | Real `ghcr.io/hkuds/lightrag:v1.5.6` Docker boot; `EMPTY_BINDING_ENV` forces provider-free (`:80-89`); `--internal` network; `RealTopology.cleanup` (`:563-600`); `DockerCLI` injectable (`:335-461`). **Boots with NO provider binding** — the B1 gap (§9). |
| Version attestation | `eval/attestpn02d.py:70-177` | 3-signal version (`/health core_version` + `lightrag.__version__` + image label), `attest_real_runtime` (workspace/endpoint/storage/egress). |
| Routing (fail-closed) | `eval/routingpn02c.py:209-265` | `route_query` (rejects wrong endpoint/workspace), `authorize_future_gd` (attestation + notebook-match + route). `cross_workspace_storage_aliasing` (`:279-305`). |
| Provider-config attestation (name-only) | `eval/routingpn02c.py:38-118` | `EXPECTED_LLM_MODEL="openai/gpt-4o-mini"`, `EXPECTED_EMBEDDING_MODEL="openai/text-embedding-3-small"`, `EXPECTED_EMBEDDING_DIM=1536`; `provider_binding_configured("OPENROUTER_API_KEY")` = name presence only. |
| Egress guard | `eval/egressguardpn02d.py` | Socket-level `/proc/net/tcp{,6}` external-peer detector; `ProviderEgressGuard.assert_zero()` — used to prove **provider-free boot**, not indexing. |
| Preflight orchestrator | `eval/preflightpn02d.py` | Gate 0 + Gate 1 + mint. Decision `C = ...PROVIDER_RUN_AUTHORIZATION_GATE_JUSTIFIED`. |

### 3.5 Historical GraphRAG-08 live primitives (present — partially reusable)

| Seam | Location | Reusable for B1? |
|---|---|---|
| Provider binding | `eval/provider_binding08.py` | **YES (as-is).** `frozen_provider_binding()` (`:155-170`): openai/gpt-4o-mini + text-embedding-3-small dim 1536, host `https://openrouter.ai/api/v1`, secret env NAME `OPENROUTER_API_KEY`. Public on argv; secret by env-inheritance (bare `-e NAME`). |
| Index submit/poll | `eval/live_indexer08.py` | **Primitive reusable.** `CellIndexClient` Protocol `submit`/`status` (`:166-173`); `RealCellIndexClient` wraps `GraphRAGService(base_url=cell endpoint)` → `index_source` (`:348-390`); ownership-bound endpoint resolution (`:103-139`); frozen retry `MAX_INDEX_ATTEMPTS_PER_SOURCE=2`. |
| GD `/query/data` client | `eval/gd_seam.py` | **Primitive reusable (per-endpoint).** `GDQueryClient.query_evidence` body `{query, mode, only_need_context:True, top_k?}` (`:158-164`); parses `data.{entities,relationships,chunks,references}`; STRONG anchors = `chunks[].file_path` + `references[].file_path`; **post-filter only** (`benchmark_ids`→allowlist `:188`); `final_answer_generation=False`, never `client.query()`. |
| Cell provisioner (provider-bound) | `eval/cell_provisioner08.py` | **Pattern reusable.** `DockerCellProcessController.start` (`:554-599`) injects the frozen binding (public argv + secret inheritance); `_attest_provider_binding` (`:1286-1337`); atomic provision + `try/finally` teardown (`:1418-1604`). |
| Runtime attestor | `eval/cell_provisioner08.py` (`DockerRuntimeAttestor`) | Reusable for provider-binding runtime attestation. |
| doc_id + delete | `client.py:61-80,283-353`; `lifecycle.py:76-222`; `deletion.py` | **doc_id = `"doc-"+md5(source_id)`** (workspace-agnostic); `delete_document(doc_id)` → `DELETE /documents/delete_document {doc_ids:[doc_id]}`; lifecycle = delete-then-insert. |

### 3.6 Open Notebook production vector seam (present — global, needs eval-only scoping)

| Seam | Location | Fact |
|---|---|---|
| `vector_search` | `domain/notebook.py:846` | Embeds query once (`generate_embedding :859`) then `fn::vector_search`. |
| `fn::vector_search` | `database/migrations/9.surrealql:4-64` | **GLOBAL** — no notebook parameter, no membership join; brute-force cosine (no ANN); `K` baked into `LIMIT $match_count`. Genuine cosine scores. |
| Membership | `database/migrations/1.surrealql:54-60` | `reference` = RELATION `FROM source TO notebook`; `artifact` = `FROM note TO notebook`. Traversal `SELECT VALUE in FROM reference WHERE out=$notebook_id`. |
| Embedding provisioning | `ai/models.py:173-328`, `utils/embedding.py:204` | `model_manager.get_embedding_model()` → esperanto `AIFactory.create_embedding`. Source embeddings **stored/reusable**; only the query embedding is per-call. |

**Consequence:** a notebook-scoped vector executor (pre-filter by `reference` edge) does not
exist in the repo — it is **net-new eval-only** work (§21).

## 4. Why existing runners cannot be reused (source-verified)

| Runner | Location | Disqualifier |
|---|---|---|
| GraphRAG-04 `runner.py` | `eval/runner.py` | Single global sidecar/corpus; targets `created_ids`; discards LightRAG answer; **04 result schema** (`report.QueryEvaluation`), not PN02B. No notebook routing. |
| GraphRAG-08 `runner08.py` | `eval/runner08.py:221` | ONE global `GraphRAGService`/base_url (`:445,622`); V/GQ/**GQ uses `service.query_strict`** — the forbidden hybrid-query path; single default workspace; **08 three-system schema** (`build_metadata :691-717`), not PN02B. |
| `precheck08.py` | `eval/precheck08.py` | ONE compose sidecar, single default workspace/global corpus; `run_type` MICRO/FULL_BENCHMARK schema, not PN02B. |
| 08E diagnostics (`live_indexer08`, `live_orchestrator08`, `cell_provisioner08`, burst) | `eval/*08.py` | Per-cell containers ARE created — but each cell is a throwaway `(concurrency, repetition)` diagnostic; deny-by-default (`authorized_live`), indexes the **08** frozen 75-source benchmark, generates no answers, workspaces are `gr08e_*` cell ids (not durable per-notebook), emits `AttemptRecord`/sweep artifacts, not PN02 per-notebook records. |

Common structural gaps that force a **new** driver:
1. **Single-workspace / single global base_url** everywhere in production + historical runners
   (LightRAG server workspace is fixed at process startup, `cell_isolation08.py:20-24`); no
   `notebook_id → workspace/endpoint` routing exists in any runner.
2. **Global corpus, no notebook partitioning**; GD can only **post-filter** by allowlist,
   never pre-scope retrieval to a notebook (`gd_seam.py:188`).
3. **No final-answer path** in any runner (all discard the answer) — irrelevant for B1
   (which excludes QA) but confirms none is a full driver.
4. **Wrong result schema** — 04/08 schemas ≠ PN02B (`reportpn02.py`).
5. **doc_id is workspace-agnostic** — per-notebook delete isolation requires routing DELETE
   to the correct per-workspace sidecar; production lifecycle/deletion has ONE base_url.

The correct strategy is therefore **NOT to reuse a runner** but to **compose the reviewed
low-level primitives** (real-sidecar boot, provider binding, index submit/poll, GD client,
attestation, routing, egress guard, workload ledger, PN02B schemas/evaluator) behind a new
**3-notebook router + orchestrator + a small number of net-new eval-only layers**.

## 5. Driver layering (frozen — one responsibility per layer)

The driver is a set of small eval-only layers, **not** a monolith. Layers map to §5 A-J:

| # | Layer | Responsibility | Reuses | Net-new? |
|---|---|---|---|---|
| A | **Runtime/workspace router** | `notebook_id → (runtime, workspace, endpoint, storage)`; reject any op whose route ≠ manifest. | `routing_manifest`, `route_query`, `authorize_future_gd`, `RealTopology`. | thin |
| B | **Provider-binding injector** | Inject the frozen OpenRouter binding into each PN02 sidecar boot (secret-safe). | `provider_binding08.frozen_provider_binding`, `cell_provisioner08.DockerCellProcessController.start` pattern. | wiring |
| C | **Membership index executor** | One `(source_key, notebook_id)` edge → one index op against THAT notebook's sidecar; submit→poll→terminal; bounded retry. | `live_indexer08` `CellIndexClient`/`RealCellIndexClient`, retry twin. | thin |
| D | **GD `/query/data` client** | Per-notebook, attestation-gated evidence query; unordered set; no answer/`client.query`. | `gd_seam.GDQueryClient` (one per endpoint) + `require_attested_before_query`. | thin |
| E | **Notebook-local vector executor** | Member-scoped ranked retrieval; single query embedding → slice K=3/K=5. | ON `reference` edge + esperanto embedding; **eval-only scoped query**. | **YES** |
| F | **Membership-removal executor** | Canonical edge removal + per-workspace derived-doc delete routed to NB_A only. | `client.compute_doc_id`, `client.delete_document`, `membershippn02.post_validate`. | thin |
| G | **Workload ledger / budget guard** | Stateful pre-op counter wrapping `check_cap`; refuses over-cap before executing. | `workloadpn02` caps + `check_cap`. | **YES (stateful wrapper)** |
| H | **Result normalization** | Vendor payload → `NormalizedEvidencePN02` → `schemaspn02` records (canonical ids only). | `normalize_vector`/`normalize_graph` + `schemaspn02`. | thin |
| I | **B1 orchestrator** | boot 3 → provider-bind → attest → index 24 → 24/24 completeness gate → GD → vector → removal → normalize → hand to `evaluatepn02` → cleanup. | all above + `evaluatepn02.run_offline_evaluation`. | **YES (assembly)** |
| J | **Cleanup / failure manager** | Owned-only teardown on every exit path (`try/finally`). | `RealTopology.cleanup`, `isolation08`. | thin |

**Net-new eval-only surface** is therefore small and bounded: the notebook-scoped vector
executor (E), the stateful budget guard (G), the 3-notebook orchestrator (I), the new
provider-run authorization capability (§6/§7), and the fake backends for offline testing
(§41). Everything else is composition of reviewed primitives.

## 6. Authorization chain (frozen — fail-closed capabilities)

The live driver stays structurally gated. A boolean flag never suffices; each stage requires
an **unforgeable capability** minted only by the prior stage:

```
RealLightRAGPreflightAuthorization        (PN02D-A; minted on Gate0∧Gate1 PASS)
        │  require_real_preflight_authorization(...)  → else IndexingGateBlocked
        ▼
PN02ProviderRunAuthorization              (NEW; explicit operator grant, §7)
        │  require_provider_run_authorization(...)    → else ProviderRunNotAuthorized
        ▼
provider-binding MATERIALIZATION           (frozen binding injected into the 3 sidecars +
        │                                    runtime binding attestation for A,B,C; §9)
        │  NOT reachable before ProviderRunAuthorization exists (§R1.16)
        ▼
IndexingAuthorization                      (minted only after binding attestation PASS)
        │  require_indexing_authorization(...)        → else IndexingGateBlocked
        ▼
QueryAuthorization                         (minted only after INDEXED_WORKSPACE_MEMBERSHIPS
                                            == 24/24 completeness gate, §15)
        ▼
provider-backed operation                  (index / GD / vector-embedding — allowlist §8)
```

The ordering is strict (§R1.16): **provider-binding materialization is unreachable until a
genuine `PN02ProviderRunAuthorization` capability exists.** A boolean (`PN02_LIVE_AUTHORIZED`)
never suffices — each provider-backed method requires the *capability object* (§18/§R1.18).

Each capability follows the proven `stage1pn02.Stage1Authorization` /
`attestpn02d.RealLightRAGPreflightAuthorization` pattern: a module-private `_AUTH_KEY`
sentinel, `__slots__`, ctor `PermissionError` on any other key, minted only inside a
verifier function, `require_*` raising a typed error otherwise. Capabilities carry the
`fixture_hash` and `run_id` so they cannot be replayed across runs/fixtures.

The offline evaluation stage keeps reusing `Stage1Authorization` (unchanged): the driver
produces records → `evaluatepn02.run_offline_evaluation` → Stage-1 gate → Stage-2. B1 runs
only Stage-1-relevant retrieval/GD/vector/removal, not QA.

## 7. `PN02ProviderRunAuthorization` (designed, NOT granted)

A separate operator-granted capability required before any provider-backed op. Designed here,
**not constructed/granted**. It encodes (content-safe, no secret values):

```
PN02ProviderRunAuthorization:
  fixture_hash                 = "9ce7df74…6899a6"          # must equal frozen (§37)
  git_baseline_commit          = <approved B0B impl commit>  # §38
  git_baseline_tag             = <approved B0B tag>
  synthetic_only               = True                        # Boundary B
  real_internal_data_allowed   = False
  approved_provider_config_id  = provider_binding_fingerprint(
                                   "openai/gpt-4o-mini|openai/text-embedding-3-small|1536|
                                    https://openrouter.ai/api/v1|OPENROUTER_API_KEY")
  workload_caps                = frozen_ledger() snapshot     # §26
  allowed_operation_classes    = {GRAPH_INDEX, INDEX_EMBEDDING, GD_QUERY_DATA,
                                  VECTOR_QUERY, VECTOR_QUERY_EMBEDDING, GRAPH_DELETE}
  run_ownership                = run_id
```

Minted only by an explicit operator-run verifier that checks the fixture hash, git baseline,
Boundary B, provider-config identity, and RealLightRAGPreflightAuthorization presence. It is
NOT minted automatically after B0B. No secret value ever appears in the object or any artifact.

## 8. Operation allowlist (frozen — structural rejection)

The driver validates every provider-backed op against `allowed_operation_classes` **before**
dispatch; any class not on the list raises `ForbiddenOperationClass` structurally.

**Allowed in B1:** `GRAPH_INDEX` (24), `INDEX_EMBEDDING` (embedding required by indexing),
`GD_QUERY_DATA`, `VECTOR_QUERY`, `VECTOR_QUERY_EMBEDDING`, `GRAPH_DELETE` (1).

**Forbidden in B1 (rejected structurally):** `CLIENT_QUERY` (`client.query()` / `/query`),
`LIGHTRAG_FINAL_ANSWER`, `QA_V`, `QA_GD`, `QA_VGD`, `JUDGE_MODEL`, `UNBOUNDED_RETRY`,
`PRODUCTION_ASK`. The GD client is `gd_seam.GDQueryClient` (`final_answer_generation=False`
by construction), so `/query` and final-answer are unreachable; the allowlist is a second,
explicit barrier.

## 9. Provider binding (frozen — secret-safe)

Each PN02 sidecar receives the **frozen** binding via `provider_binding08.frozen_provider_binding()`:

```
LLM binding      = openai        LLM model       = openai/gpt-4o-mini
EMBEDDING binding= openai        EMBEDDING model = openai/text-embedding-3-small   (dim 1536)
host             = https://openrouter.ai/api/v1
secret env NAME  = OPENROUTER_API_KEY      (value NEVER in file/argv/log/artifact)
```

Injection boundary (reusing the `cell_provisioner08.DockerCellProcessController.start`
pattern): **public** binding values on argv (`-e NAME=VALUE`); **secret** values by
env-inheritance (bare `-e NAME` + `subprocess.run(env=…)`), so no key reaches
argv/repr/logs/records/artifacts. A safe fingerprint (`provider_binding_fingerprint`,
`manifestpn02.py:146-152`) records the config identity.

**The B1 gap this closes:** PN02D-A's `realsidecarpn02d` deliberately boots **provider-free**
(`EMPTY_BINDING_ENV :80-89`) for preflight. The B1 driver needs a **provider-bound** PN02
sidecar boot = `realsidecarpn02d` boot topology **+** `provider_binding08` injection +
runtime binding attestation (`_attest_provider_binding` pattern) for all three runtimes. This
is a bounded wiring layer (B), not a new mechanism.

Secret-presence check: fail BEFORE any provider op if `OPENROUTER_API_KEY` (or the sidecar
`GRAPHRAG_POC_API_KEY`) is absent — content-safe `REQUIRED_RUNTIME_SECRET_MISSING=<name>`
(the frozen 08E.5 behavior). No provider call is made merely to test credentials (§35).

## 10. One notebook = one runtime (frozen topology)

```
NB_A → runtime A → workspace A (nb_2801282a…) → storage A → endpoint A
NB_B → runtime B → workspace B (nb_ca49abb9…) → storage B → endpoint B
NB_C → runtime C → workspace C (nb_45f0f09a…) → storage C → endpoint C
```

Workspace ids are `workspace_id_for(notebook.record_id) = "nb_"+sha256(record_id)[:16]`
(`manifestpn02.py:82-87`) from `notebook:gr_pn02_{a,b,c}` — **never** the display theme
(Helix Robotics / Marisol Seaport / Verdant Agritech). Every op routes through the canonical
`notebook_id`.

## 11. Routing invariant (frozen — validate before send)

Required mapping, validated **before** any provider-backed work via `route_query`
(`routingpn02c.py:209-236`) + `authorize_future_gd`:

```
notebook_id → attested runtime → attested workspace → attested endpoint → attested storage
```

An op is rejected (`WrongEndpointRoutingError` / `WrongWorkspaceRoutingError` /
`RoutingViolation` / `UnattestedWorkspaceError`) if any component differs from the run
manifest. No post-hoc route validation. `cross_workspace_storage_aliasing` must report 0
before indexing.

## 12. Indexing interface (frozen)

Per-membership indexing (reusing `live_indexer08` `CellIndexClient` submit/poll):

**Input (conceptual):** canonical `source_key`, `notebook_id`, `WorkspaceAttestation`,
synthetic Source text (from the frozen fixture only), run context.

**Output (`IndexOperationRecord`, content-safe):** `operation_id`, `source_key`,
`notebook_id`, `workspace_id`, `technical_status` (submit/track state), `attempt_count`,
`elapsed_ms`, coarse provider-call observations where available (§27), derived LightRAG
`doc_id` if needed for delete, safe error category (§33). **No raw provider payload, no
secret, no raw error text** (raw error consumed transiently only to classify).

## 13. Canonical Source vs derived document identity (frozen — refined in R1)

Three identities are kept strictly separate (see **R1** for the load-bearing refinement):

- **`CANONICAL_SOURCE_ID`** — owned by Open Notebook, stable across notebooks (the fixture key
  `A1`, `SH_AB`; at runtime the `source:` record id). The PN02B truth id.
- **`WORKSPACE_ID`** — identifies the notebook-local LightRAG graph namespace/runtime
  (`"nb_"+sha256(record_id)[:16]`, `manifestpn02.py:82-87`).
- **`DERIVED_LIGHTRAG_DOCUMENT_ID`** — the vendor id of the workspace-local indexed
  representation of a canonical Source.

**Authoritative logical lookup key = `(workspace_id, canonical_source_id)`** — this pair
identifies the intended derived document instance. The endpoint/port is **routing state, not
identity** (`ENDPOINT_IDENTITY_IS_DOCUMENT_IDENTITY = NO`, §R1.2). The vendor
`DERIVED_LIGHTRAG_DOCUMENT_ID` is resolved/used *inside* the workspace reached through the
attested endpoint; it is not made unlocatable by an endpoint/port change (restart /
reprovision / new run id).

Vendor derivation (verified, §R1.3): `doc_id = "doc-"+md5(file_source)` and ON always sets
`file_source = canonical source_id` (`client.py:66,74,80`; POST body `{text, file_source:
source_id}` `:268`). So the vendor id is **deterministic and content-independent**, and the
*same* canonical Source yields the *same* vendor doc_id in every workspace — collision is
avoided **only** because the two live in isolated workspaces reached through distinct attested
endpoints, never because the id differs. Every index/delete op is addressed to the notebook's
own attested sidecar; the driver must never issue a delete against a shared/global base_url.

## 14. Shared-Source indexing (frozen terminology)

The 6 shared membership edges each produce a **distinct derived document instance**:

```
SH_AB → indexed into A and B   (2 derived docs, same doc_id, endpoints A & B)
SH_AC → indexed into A and C
SH_BC → indexed into B and C
```

So one canonical Source (SH_AB) yields two derived graph documents. Terminology is frozen:
*canonical Source* (1) vs *workspace membership* (2 for a shared Source) vs *derived graph
document* (2, one per workspace). The two derived documents carry the **same vendor doc_id**
— not because of content, but because both are inserted with the same `file_source`
(= `canonical_source_id`) and the vendor id is content-independent (§R1.3). Their logical
keys `(workspace_A, SH_AB)` and `(workspace_B, SH_AB)` differ, and they live in isolated
workspaces, so the identical vendor id is safe.

## 15. Index-completeness gate (frozen — hard)

Before any GD query:

```
INDEXED_WORKSPACE_MEMBERSHIPS == 24 / 24     → mint QueryAuthorization
INDEXED_WORKSPACE_MEMBERSHIPS  < 24 / 24     → FAILED_INDEX_COMPLETION, block all queries
```

23/24 blocks querying (no degraded scientific run). The `QueryAuthorization` capability is
minted only at 24/24; the GD/vector executors require it.

## 16. Index retries (frozen)

```
MAX_INDEX_ATTEMPTS_PER_OPERATION = 2
MAX_GRAPH_INDEX_ATTEMPTS         = 48   (24 × 2)
```

Retry **only** technical/transient failures per the frozen classifier
(`index_retry08.characterize_failure` → `is_transient_reason`, reused unchanged). Never retry
to improve retrieval quality. A non-transient failure fails closed (does not consume the
whole 48 budget silently — see §26).

## 17. Index status polling (frozen)

Completion is observed via the LightRAG track-status surface (reusing
`live_indexer08.RealCellIndexClient.status` → `GraphRAGService.track_status`), states
`PROCESSED | FAILED | IN_PROGRESS | TIMEOUT`. Never infer completion from HTTP-submit
acceptance alone. Lifecycle: `submit → poll(interval) → PROCESSED (success) | FAILED
(permanent, classify) | TIMEOUT (deadline) → bounded retry (§16)`. Reindex = delete-then-insert
(`lifecycle.py:76-222`; re-POST of an identical file is a filename-duplicate reject in v1.5.6).

## 18. GD client (frozen — eval-only, per notebook)

A dedicated eval-only GD client per notebook endpoint, reusing `gd_seam.GDQueryClient`:

```
POST /query/data   body = {"query": question, "mode": <mode>, "only_need_context": true[, "top_k": K?]}
```

- `only_need_context=true` suppresses the final-answer LLM (`gd_seam.py:161`).
- **Never** `client.query()` / `/query` / final-answer (`final_answer_generation=False`).
- Per PN02A §18, `top_k` unset (observe natural breadth); GD result is an **unordered set**,
  no rank/score (`GDEvidenceResult` enforces `ordered=False`).
- Each GD op requires `require_attested_before_query(attestation)` + `authorize_future_gd`
  (notebook-match + route) + `QueryAuthorization`.

## 19. GD result normalization (frozen — boundary confined)

```
vendor /query/data payload (data.{entities,relationships,chunks,references})
   ↓  eval-only adapter (gd_seam projection: STRONG anchors chunks[].file_path + references[].file_path)
   ↓  normalize_graph(raw, allowlist=fx.source_keys)  → NormalizedEvidencePN02 (ordered=False)
   ↓  GDEvidenceResult(query_id, notebook_id, evidence, latency_ms, outcome)
   ↓  canonical fixture Source ids only
```

Vendor schema stays inside the GD adapter; the raw payload is dropped after projection. No
vendor field reaches PN02 decision code (`decisionspn02`/`metricspn02` see only canonical ids).

## 20. Provenance validation (frozen — fail-closed)

Before a GD Source is accepted as evidence:

1. canonical `source_key` must resolve in `fx.source_keys` (else `foreign`/`malformed`,
   counted in `ProvenancePN02`, feeds Stage-1 exact-`=0` gate);
2. the Source must be a current **member** of the notebook (leakage is a membership judgement
   in the metrics layer; non-member-but-valid ids are kept raw and judged there);
3. `post_validate(returned, current_members)` (`membershippn02.py:35-43`) is applied for
   citation/removal acceptance.

Foreign / malformed / unknown / removed-membership Sources are handled fail-closed:
non-canonical → dropped + counted; removed-membership → rejected by `post_validate` even if
the derived store still returns it (§30).

## 21. Vector baseline (frozen — net-new eval-only executor, refined in R1)

**Hard semantic requirement (§R1.4):**
`PN02_VECTOR_BASELINE_CANDIDATE_UNIVERSE = CURRENT_SOURCES_OF_QUERIED_NOTEBOOK_ONLY`. Candidate
restriction happens **BEFORE** final top-K selection.
`GLOBAL_TOPK_THEN_POSTFILTER_ALLOWED = NO` — the forbidden pattern (global top-K → post-filter
to notebook → call it "notebook-local") is explicitly rejected; it is not an approximation the
PN02 baseline may use.

**Forensic basis (not a PN01 rewrite):** Open Notebook *has* canonical notebook→Source
membership (`reference` edge, `1.surrealql:54-56`), but the shipped low-level
`fn::vector_search` (`9.surrealql:4-64`) does **not** perform notebook-scoped pre-filtered
ranking — it scans globally and bakes K into `LIMIT`. So notebook-scoped pre-filtering is a
capability the schema *supports* but the current function does *not* implement; it is net-new
eval-only work. Production `fn::vector_search` stays **untouched**.

**Frozen exact approach for B0B (`VECTOR_EXACT_IMPLEMENTATION_APPROACH = A`):** an eval-only
notebook-local executor that restricts candidates to the notebook's **current member Source
rows before ranking** — conceptually
`SELECT …, vector::similarity::cosine(embedding, $q) AS sim FROM source_embedding
WHERE source IN $member_ids AND array::len(embedding)=array::len($q) ORDER BY sim DESC`
with **no inner LIMIT that would truncate a global set** — where `$member_ids` is resolved
from `SELECT VALUE in FROM reference WHERE out=$notebook_id`. This yields genuine DB cosine
scores over exactly the member subset. Approach **B** (resolve the member Source/embedding
records first, then score similarity only over that exact subset in the same embedding space)
is a documented, mathematically-equivalent substitute B0B may adopt if the filtered-DB form
proves impractical; both are exact (no approximation). B0B freezes the concrete SurrealQL /
Python form.

**One ranking, both K:** one execution produces a single ranked member list; **K=3 and K=5 are
slices of that one list** (PN02A §17) — the query is embedded once (reuse the `List[float]`
vector), so no duplicate query-embedding op serves the two K (§13/§22 caps).

## 22. Vector provider boundary (frozen — accounted)

The query embedding is a real provider call via `generate_embedding` →
`model_manager.get_embedding_model()` → esperanto `AIFactory.create_embedding` (frozen
`openai/text-embedding-3-small`, dim 1536). Source/chunk embeddings are **stored and reused**
(`source_embedding` table) — only the *query* embedding is per-call. Caps:

```
MAX_VECTOR_QUERY_OPERATIONS   = 26     (24 baseline + 2 removal)
MAX_QUERY_EMBEDDING_OPERATIONS= 26     (one query embedding per vector query, K=3/K=5 share it)
```

The 24 canonical Source embeddings (`MAX_CANONICAL_SOURCE_EMBEDDINGS=21` for canonical, but
the driver must embed member copies for the scoped store) are produced during index prep
inside Option-A isolation; the driver accounts them separately from query embeddings.

## 23. Vector result normalization (frozen)

Output `VectorEvidenceResult`: `query_id`, `notebook_id`, ranked `NormalizedEvidencePN02`
(`ordered=True` via `normalize_vector`), `latency_ms`, `outcome`. Genuine cosine scores are
carried only if the ON seam supplies them; **scores are never modified or fabricated**. The
PN02B schema deliberately carries no score field on the ranked list (rank position is the
signal); provenance stats accompany it.

## 24. Query orchestration (frozen — deterministic order)

B1 executes exactly:

```
24 baseline queries        (PN02Q01…PN02Q24, GD + vector each)
+ 2 GD removal probes       (Q04 NB_A, Q12 NB_B — after removal)
+ 2 vector removal probes   (Q04 NB_A, Q12 NB_B — after removal)
```

No query additions, no tuning. Iteration order is **deterministic**: notebooks A→B→C; within
a notebook, queries in fixture `query_id` order; GD then vector per query. Removal probes run
after the removal transition (§28), in the frozen order NB_A then NB_B.

## 25. Concurrency (frozen — conservative, observable)

Given the historical provider-burst findings (08E), the driver does **NOT** submit all ops
concurrently. Separate, conservative initial limits (all observable in the run manifest):

```
INDEX_CONCURRENCY   = 1   (sequential submit+poll per membership initially; raise only on evidence)
GD_QUERY_CONCURRENCY= 1
VECTOR_CONCURRENCY  = 1
```

Provider pressure (per-op latency, attempt counts) is captured so a future analysis can
justify raising a limit — never defaulted higher. Cross-notebook parallelism is likewise
off initially (one notebook indexed/queried at a time).

## 26. Workload-ledger enforcement (frozen — stateful, pre-op)

The driver adds a **stateful budget guard** (layer G) that increments a per-class counter and
calls `workloadpn02.check_cap(name, projected, cap)` **before** dispatching each op — no op
executes first and is counted afterward. Frozen caps for B1:

```
PLANNED_GRAPH_INDEX_OPERATIONS = 24     MAX_GRAPH_INDEX_ATTEMPTS = 48
GRAPH_DELETE_OPERATIONS        = 1
MAX_GD_QUERIES                 = 26     (24 + 2 removal)
MAX_VECTOR_QUERY_OPERATIONS    = 26     MAX_QUERY_EMBEDDING_OPERATIONS = 26
MAX_FINAL_ANSWER_CALLS (B1)    = 0      ← B1-specific; the ledger's 72 is the Stage-2 total (§43)
CLIENT_QUERY_CALLS             = 0      JUDGE_MODEL_CALLS = 0
```

Note the deliberate B1 override: `workloadpn02.MAX_FINAL_ANSWER_CALLS = 72` is the *Stage-2*
budget; **B1 enforces a final-answer cap of 0** (B1 excludes QA). The guard exposes a
`B1FinalAnswerForbidden` cap of 0 distinct from the ledger's Stage-2 total.

## 27. Provider-request accounting (frozen — logical vs provider)

The driver separates **PN logical operations** (24 index ops) from **underlying provider API
calls** (LightRAG may make many LLM/embedding requests per one graph index op). It records
the strongest observable provider accounting available without unsupported internals — e.g.
`PROVIDER_TRAFFIC=BOUNDED_INDEX_DIAGNOSTIC` plus any observable per-op counts (as 08E7C did),
and marks provider-internal counts `NOT_OBSERVABLE` when the sidecar does not expose them. It
**never** reports "24 graph indexes = 24 provider calls."

## 28. Membership removal (frozen — ordering + validation)

Frozen transition (`membership_removal_scenario`, `datasetpn02.py:705-744`): SH_AB begins in
A+B; remove A membership; retain B.

```
1. canonical edge removal        remove (SH_AB, NB_A) from ON membership   (edge-only)
2. derived graph delete           delete doc_id(SH_AB) at NB_A's sidecar ONLY (§29)
3. 2 GD removal probes            Q04 (NB_A) must NOT return SH_AB; Q12 (NB_B) must still return SH_AB
4. 2 vector removal probes        same expectation, scoped executor
```

Ordering: canonical removal first (so `post_validate` invalidates SH_AB for A immediately,
independent of the graph delete), then the graph delete, then the 4 probes. Validated by
`evaluate_removal → RemovalReport` (`membershippn02.py:65-118`).

**Vector removal semantics (§R1.15):** for the two vector removal probes the candidate universe
is recomputed from the *post-removal* membership snapshot **before ranking** — NB_A's
`$member_ids` no longer contains SH_AB, so SH_AB is excluded from A's candidate set before
top-K; NB_B's `$member_ids` still contains it, so it remains eligible in B. Post-hoc
citation-only filtering is **not** sufficient for the vector baseline (that would violate §R1.4);
the exclusion must be in the candidate universe. (The GD probes rely additionally on the
`post_validate` backstop of §30, since the derived graph store may lag the delete.)

## 29. Delete identity (frozen — no cross-workspace deletion)

The A-copy of SH_AB is deleted by addressing `client.delete_document(compute_doc_id("SH_AB"))`
(→ `DELETE /documents/delete_document {doc_ids:[doc_id]}`) at **NB_A's sidecar base_url
only**. Because the doc_id is identical in workspace B, correctness depends entirely on the
endpoint: the B copy is never touched. The removal executor asserts the target endpoint ==
route(NB_A).endpoint before issuing the delete; a delete against any other/global base_url is
a `FAILED_MEMBERSHIP_REMOVAL` structural refusal.

## 30. Delete-failure safety (frozen — defense in depth)

Even if the LightRAG delete fails, the canonical ON membership removal (step 1) already
invalidates SH_AB for A. `post_validate(returned, current_members_of_A)` drops SH_AB from any
GD/vector evidence returned by A's still-stale store, so `STALE_GRAPH_EVIDENCE_ACCEPTED_AS_VALID
= 0` holds regardless of delete success (`RemovalProbeResult.graph_delete_succeeded` records
the delete outcome; `evaluate_removal` proves the postconditions still hold). Stale evidence
can never count as accepted evidence/citation.

## 31. Result capture (frozen — machine-readable, content-safe)

Artifacts (all content-free — ids/counts/flags/metrics only):

```
run manifest                 (build_run_manifest — routing, caps, boundary_b, fixture_hash, git)
index-operation results      (IndexOperationRecord per membership)
index completion report      (24/24 gate outcome)
GD raw→normalized boundary   (GDEvidenceResult per query; vendor schema NOT persisted)
vector results               (VectorEvidenceResult per query)
stage-1 metrics              (from evaluatepn02)
retrieval metrics            (R0-R4 inputs/outputs)
multi-hop metrics            (M0-M3)
membership-removal report    (RemovalReport)
decision report              (build_report — reportpn02)
cleanup report               (RealCleanupReport: processes/storage/networks remaining)
```

Raw provider responses are not persisted.

## 32. Secret-safe artifacts (frozen)

No artifact may contain API keys, Authorization headers, `.env` contents, provider secret
fields, or raw runtime environment. Secrets appear only as env-var **names** and safe
fingerprints (`provider_binding_fingerprint`). The frozen 08E redaction discipline applies:
`redact_command`, `PRESENT` sentinels, forbidden-token scan over every artifact.

## 33. Error taxonomy (frozen — technical, separate from science)

Driver-internal technical outcomes (mapped down to `schemaspn02.TechnicalOutcome` at the
evaluator boundary, since the evaluator consumes that enum):

```
FAILED_PRECHECK                → (blocks run)
FAILED_PROVIDER_AUTHORIZATION  → (blocks run)
FAILED_PROVIDER_BINDING        → FAILED_BEFORE_INDEX
FAILED_RUNTIME_ATTESTATION     → FAILED_WORKSPACE_ATTESTATION
FAILED_INDEX_SUBMIT            → FAILED_INDEXING
FAILED_INDEX_COMPLETION        → FAILED_INDEXING
FAILED_INDEX_CAP               → FAILED_INDEXING
FAILED_BEFORE_QUERY            → FAILED_BEFORE_INDEX / FAILED_INDEXING
FAILED_GD_QUERY                → FAILED_GD_QUERY
FAILED_VECTOR_QUERY            → FAILED_VECTOR_QUERY
FAILED_MEMBERSHIP_REMOVAL      → (removal report failure)
FAILED_STAGE1_ISOLATION        → FAILED_STAGE1_ISOLATION
FAILED_CLEANUP                 → (post-run)
COMPLETED                      → COMPLETED
```

A **technical failure is never encoded as a scientific NO** — it leaves the affected value
`NOT_EVALUATED`/`INCONCLUSIVE` per the frozen decision semantics (`schemaspn02` §8/§56/§57).
(B0B may add the richer driver enum with an explicit lossless mapping test to the existing
`TechnicalOutcome`; it must not silently repurpose an existing member.)

## 34. Cleanup model (frozen — every exit path)

`try/finally` (or async CM) ownership pattern for every exit. Owned resources cleaned:
LightRAG containers, run-owned networks, workspace storage roots, temporary isolated Surreal
namespace/state, and non-retained artifacts. Reuses `RealTopology.cleanup`
(`realsidecarpn02d.py:563-600`) + `isolation08` context-manager `finally`-drop. **Unrelated
Docker/ON/SurrealDB services are never touched** (verified before/after container+network
listings, as PN02D-A did). `OWNED_PROCESSES_REMAINING / RUNTIME_STORAGE_RESIDUE / networks =
0` required on exit.

## 35. Provider-config failure (frozen — fail before indexing)

If provider configuration/secret is missing or invalid, the driver fails **before** indexing:
(1) provider-binding attestation on the owned containers (bound to frozen models, both secrets
present) via the `_attest_provider_binding` pattern; (2) a required-secret name-presence check
(`REQUIRED_RUNTIME_SECRET_MISSING=<name>`). No provider call is made merely to test
credentials unless a later phase explicitly authorizes such a probe.

## 36. Data-class guard (frozen)

```
SYNTHETIC_ONLY = true      REAL_INTERNAL_DATA_ALLOWED = false
```

`validate_boundary_b` (`manifestpn02.py:214-227`) runs before any provider traffic. Only the
frozen PN02 fixture is accepted — no arbitrary file-path ingestion. The run manifest declares
`DatasetClass.SYNTHETIC_FIXTURE`.

## 37. Fixture-hash hard gate (frozen)

Before any provider traffic, `verify_fixture_hash` (`datasetpn02.py:833-848`) must equal
`9ce7df74…6899a6`. On mismatch the driver refuses structurally (`FAILED_PRECHECK`); the
`PN02ProviderRunAuthorization` also binds the hash so a drifted fixture cannot reach provider
code.

## 38. Git-baseline hard gate (frozen)

The B1 run records + verifies its approved implementation checkpoint (expected commit/tag).
A provider-backed run against a dirty or unexpected tree is refused unless the operator
explicitly authorizes it; the run manifest carries `git_commit`. Dirty-tree handling:
`FAILED_PRECHECK` by default.

## 39. B0B offline implementation boundary (frozen)

PN02D-B0B may implement, with `PROVIDER_TRAFFIC = 0` and `LIVE_LIGHTRAG_PROVIDER_CALLS = 0`:

- all driver layers A-J (§5) as eval-only modules;
- provider interfaces (index/GD/vector Protocols → concrete classes bound to injected clients);
- **fake/mocked** LightRAG endpoints, mock index completion, mock GD responses, mock vector
  responses (§41);
- authorization enforcement (all capabilities), operation allowlist, stateful workload guard;
- artifact serialization; cleanup tests.

B0B **must NOT** execute live provider operations, boot a real sidecar, or open a provider
socket. Every provider/DB/Docker seam is **injected** (as `live_orchestrator08.OrchestratorDeps`
already demonstrates), so importing/testing the driver starts nothing.

## 40. B0B test plan (frozen — falsifiable)

Each test is a concrete pass/fail assertion against the offline driver + fakes (§41):

1. **Missing provider-run authorization** → any provider-backed op refused (`ProviderRunNotAuthorized`); 0 fake calls.
2. **Wrong notebook route** (op for NB_A dispatched at NB_B endpoint) → `WrongEndpointRoutingError`.
3. **Wrong workspace** (attestation workspace ≠ route) → `WrongWorkspaceRoutingError` / attestation reject.
4. **Fixture-hash mismatch** → `FAILED_PRECHECK`, no provider code reached.
5. **Dirty/incorrect git baseline** (if enforced) → refused.
6. **Unauthorized Source membership** (index a Source into a workspace it does not belong to) → refused.
7. **Shared-Source duplicate indexing** (SH_AB into A and B) → two distinct derived-doc records, same doc_id, different endpoints.
8. **24/24 completeness gate** passes → `QueryAuthorization` minted.
9. **23/24 blocks query** → `FAILED_INDEX_COMPLETION`; GD/vector never dispatched.
10. **Index retry cap** — a transient fake failure retries ≤2; a 49th attempt is refused (`FAILED_INDEX_CAP`).
11. **GD cap** — 27th GD op refused before dispatch.
12. **Vector cap** — 27th vector op refused; query-embedding cap likewise.
13. **`client.query` forbidden** — the allowlist rejects `CLIENT_QUERY`; the GD client has no `/query` path.
14. **Final-answer forbidden** — B1 final-answer cap 0; `FinalAnswerSeam` stays inert.
15. **Provider-operation allowlist** — any forbidden class → `ForbiddenOperationClass`.
16. **Malformed provenance** — malformed ids counted, dropped, feed Stage-1 exact-0 gate.
17. **Foreign Source** — non-fixture id counted `foreign`, dropped.
18. **Membership removal** — SH_AB vanishes from A probes, remains in B probes.
19. **Delete-failure stale-evidence rejection** — fake delete fails; `post_validate` still drops SH_AB from A; `STALE_GRAPH_EVIDENCE_ACCEPTED_AS_VALID=0`.
20. **Cleanup after every technical failure** — inject failure at each phase; assert owned resources torn down, 0 residue.
21. **Secret-safe artifacts** — forbidden-token scan over every serialized artifact is clean; env values never appear.

Additional invariants: no-production-import test (`PRODUCTION_IMPORTS_EVAL=NO`); autouse
httpx/socket guard so no test can reach a network; the driver's scientific outputs equal the
existing PN02B evaluator outputs on identical inputs (§42).

## 41. Mock live driver (frozen — the B0B backend contract)

A `FakeProvider` / `FakeLightRAG` / `FakeVector` backend implementing the same injected
Protocols (`CellIndexClient`, GD client, vector executor, delete client) lets B0B exercise the
**entire B1 orchestrator offline**. It can be fed: index successes/failures (per source,
transient vs permanent); GD Source sets (valid/foreign/malformed/member/non-member);
vector ranked results; delete success/failure; arbitrary technical errors — and must yield
the exact `schemaspn02` records the real backend would, so the same records flow into
`evaluatepn02.run_offline_evaluation` and produce the exact PN02B decisions. (This mirrors the
proven `live_orchestrator08` injected-deps design.)

## 42. No duplicate scientific logic (frozen)

The driver must **not** reimplement R0-R4, Q0-Q3, M0-M3, Stage-1 formulas, grading, or ground
truth — those live only in `metricspn02` / `decisionspn02` / `stage1pn02` / `datasetpn02`.
Flow:

```
live driver → normalized schemaspn02 records → evaluatepn02.run_offline_evaluation → decisions
```

One scientific-rule implementation, already approved in PN02B.

## 43. Final-answer separation (frozen)

The B1 driver contains **no** answer-generation (`FinalAnswerSeam` stays inert; final-answer
cap = 0). B2 (QA arms QA-V / QA-GD / QA-V+GD, Stage-2, the ledger's 72 final-answer budget) is
a **separate** authorization path and a separate driver capability — never bundled into the B1
orchestrator.

## 44. Production separation (frozen)

All B0/B1 live wiring stays under the eval/research namespace. `PRODUCTION_IMPORTS_EVAL = NO`;
no production `Ask`/GraphRAG imports; no production dependency on the driver. Verified by the
no-production-import test.

## 45. Driver module plan (proposed — NOT created)

Repository-consistent eval-only names under `open_notebook/integrations/graphrag/eval/`
(mirroring `attestpn02d.py` / `realsidecarpn02d.py` / `preflightpn02d.py`):

```
authlivepn02d.py       # PN02ProviderRunAuthorization + IndexingAuthorization + QueryAuthorization + operation allowlist
provbindpn02d.py       # provider-bound PN02 sidecar boot (realsidecarpn02d boot + provider_binding08 injection + binding attestation)
indexlivepn02d.py      # membership index executor (wraps live_indexer08 CellIndexClient, per-notebook)
gdlivepn02d.py         # per-notebook attestation-gated GD /query/data client (wraps gd_seam.GDQueryClient)
vectorlivepn02d.py     # notebook-scoped eval-only vector executor (reference-edge pre-filter)
removallivepn02d.py    # membership-removal + endpoint-scoped derived-doc delete
budgetlivepn02d.py     # stateful workload guard (wraps workloadpn02.check_cap)
driverpn02d.py         # the B1 orchestrator (layers A-J assembly; injected deps)
fakeslivepn02d.py      # FakeProvider/FakeLightRAG/FakeVector backends (test only)
liveclipn02d.py        # CLI entrypoints (validate-live-config / dry-run-plan / offline-driver-test / execute-b1)
```

Cleanup/normalization reuse `RealTopology.cleanup` + `normalizepn02`/`schemaspn02` directly
(no new module). Names are indicative; B0B may adjust for consistency. **None created now.**

## 46. CLI / entrypoint (frozen — separate commands)

Distinct commands (not overloading `offlinepn02.py`, which stays provider-free):

```
validate-live-config   # verify fixture hash, git baseline, provider-config identity, secret presence (no provider call)
dry-run-plan           # print the full plan, provider-free (§47)
offline-driver-test    # run the driver end-to-end against fakes (B0B; no provider)
execute-b1             # THE provider-backed run — impossible to invoke without PN02ProviderRunAuthorization
```

`execute-b1` structurally requires a genuine `PN02ProviderRunAuthorization` (operator-granted)
before it can dispatch any op; there is no flag-only path.

## 47. Dry-run (frozen — provider-free)

`dry-run-plan` (B0B, zero provider calls) prints:

```
24 index plan (per membership, with target workspace/endpoint)
workspace routing (NB_A/B/C → nb_… → endpoint/storage)
shared-source routing (SH_AB→{A,B}, SH_AC→{A,C}, SH_BC→{B,C})
24 + 2 GD plan
24 + 2 vector plan
caps (§26)
provider models (openai/gpt-4o-mini, text-embedding-3-small, dim 1536) by name
fixture hash
```

No provider call, no sidecar boot, no socket.

## 48. B1 re-authorization requirements (frozen)

After a B0B checkpoint, a **new** B1 execution authorization must verify:

1. B0B implementation checkpoint/tag present + tree clean;
2. fixture hash == frozen;
3. B0B tests pass;
4. exact notebook-local vector semantics (candidate universe = current members, pre-ranking; §21/§R1.8);
5. canonical ↔ workspace-derived document mapping intact (`(workspace_id, canonical_source_id)`; §13/§R1.5);
6. provider authorization chain intact (authorization precedes binding materialization; §6/§R1.9);
7. provider config + required secret safely present (name-only check);
8. `RealLightRAGPreflightAuthorization` path intact (PN02D-A gates re-runnable);
9. driver dry-run exact (§47);
10. workload caps exact (§26);
11. Boundary B (`SYNTHETIC_ONLY=true`);
12. cleanup readiness.

**No automatic provider run after B0B** — a separate operator prompt is required.

## 49. Independent review (design findings resolved)

| # | Challenge | Resolution |
|---|---|---|
| A | Can provider work execute without explicit provider-run authorization? | No — `execute-b1` requires a genuine `PN02ProviderRunAuthorization` (unforgeable, operator-minted, §7); no flag-only path (§46). |
| B | Can one notebook route to another workspace? | No — `route_query`/`authorize_future_gd` reject wrong endpoint/workspace before send (§11); attestation binds `notebook_id`. |
| C | Can a Source be indexed into an unauthorized workspace? | No — index executor routes by canonical `notebook_id` through the attested route; membership checked against the fixture edge matrix (test §40.6). |
| D | Can a fixture-hash mismatch reach provider code? | No — hard gate (§37) + hash bound into the authorization capability. |
| E | Can `client.query` be invoked? | No — GD client has no `/query` path (`final_answer_generation=False`); allowlist rejects `CLIENT_QUERY` (§8). |
| F | Can final-answer generation be invoked? | No — B1 final-answer cap 0; `FinalAnswerSeam` inert; B2 is a separate path (§43). |
| G | Can caps be exceeded through retry paths? | No — retries count toward `MAX_GRAPH_INDEX_ATTEMPTS=48`; the stateful guard checks the projected attempt count pre-op (§16/§26; test §40.10). |
| H | Can a shared-Source identity cause cross-workspace deletion? | No — delete is addressed to NB_A's endpoint only; identical doc_id in B is physically separate (§29; test §40.7/§40.19). |
| I | Can stale evidence survive membership removal as accepted evidence? | No — canonical removal + `post_validate` drop it even on delete failure; `STALE_…=0` (§30; test §40.19). |
| J | Can provider secrets appear in artifacts? | No — env-inheritance injection, name-only records, forbidden-token scan (§9/§32; test §40.21). |
| K | Can driver scientific logic diverge from PN02B? | No — driver emits records only; `evaluatepn02` owns all decisions; equality test (§42; test §40 invariant). |
| L | Can cleanup miss a partial-startup/index failure? | No — `try/finally` owned-only teardown on every exit; per-phase failure injection test (§34; test §40.20). |
| M | Can real/internal data be supplied? | No — Boundary-B guard + fixture-only ingestion; no arbitrary file paths (§36). |
| N | Can B0B accidentally contact a provider? | No — all seams injected; fakes only; autouse socket/httpx guard; no real boot in B0B (§39/§41). |
| O | Can B1 execute from a dirty/unapproved implementation? | No — git-baseline gate + capability binds the baseline (§38/§48). |

**HIGH = 0, MEDIUM = 0, LOW = 0** design findings unresolved. (All challenges resolved by
existing reviewed mechanisms + the small net-new layers; no design defect blocks B0B.)

## 50. B0A decision

```
B0A_DECISION      = C
B0A_DECISION_NAME = LIVE_DRIVER_DESIGN_FROZEN_AND_OFFLINE_IMPLEMENTATION_JUSTIFIED
```

C criteria are met: exact seams identified (§3, source-verified); authorization model frozen
(§6/§7); provider boundary frozen (§9); index/GD/vector interfaces frozen (§12/§18/§21);
workload enforcement frozen (§26); result normalization frozen (§19/§23/§31); cleanup frozen
(§34); B0B test plan falsifiable (§40). The missing driver is a bounded assembly of
reviewed primitives plus a small, well-scoped set of net-new eval-only layers (vector-scoped
executor, stateful budget guard, provider-run capability, 3-notebook orchestrator, fakes) —
all implementable offline with zero provider traffic.

## 51. Consequent flags

```
PN02D_B0B_IMPLEMENTATION_JUSTIFIED = YES
PN02_PROVIDER_RUN_AUTHORIZED       = NO
PN02D_B1_REAUTHORIZATION           = NOT_YET
```

Next phase: **GraphRAG-PN02D-B0B — Offline Per-Notebook Live Driver Implementation** (ZERO
provider traffic).

## 52. Retained frozen

```
GraphRAG-08                     = CLOSED / APPROVED
GraphRAG-09                     = NOT_JUSTIFIED
GRAPHRAG_PRODUCTION_INTEGRATION = NOT_APPROVED
LIGHTRAG_ASK_INTEGRATION        = NOT_APPROVED
PRODUCTION_IMPORTS_EVAL         = NO
Boundary B                      = SYNTHETIC_ONLY=true, REAL_INTERNAL_DATA_ALLOWED=false
```

No code, no migration (count 50), zero provider traffic, sidecar not started,
`GRAPHRAG_ENABLED` untouched, no `.env` edit, fixture unchanged, NOT checkpointed.
