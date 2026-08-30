# GraphRAG-04 — Synthetic Retrieval Evaluation / Quality Baseline

**Status: COMPLETE / APPROVED — signed off 2026-08-30.** Methodology, benchmark, and evaluator complete
and verified offline + live. The **live synthetic baseline was EXECUTED** (OpenRouter + LightRAG v1.5.6;
both probe gates PASS; results in §26.5–§33). Review gates passed: **Karpathy CLEAN**; **Codex A/B/C**
resolved with no unresolved actionable HIGH (see §38.1). Final decisions frozen:
**RRF_CANDIDATE_INTERFACE_READY = NO**, **HYBRID_VALUE_EVIDENCED = INCONCLUSIVE**.

**Static-analysis posture (accurate — NOT "full mypy clean"):** the GraphRAG-04 surface (the `eval/`
package + both 04 test files) is mypy-clean and introduces **zero** new mypy errors; full-project
`mypy .` still reports **91 errors, all in pre-existing untouched GraphRAG-03 test files**
(`03c/03d/03e/command_seam`). ruff clean.

**Approved checkpoint:** branch `feature/graphrag-lifecycle` · HEAD
`4f0e43afeb34efb928d68e87e90b6f45e931befe` · tag `graphrag-03e-approved` · migrations
frozen at **50** (no migration 26/27).

> **Boundary B (sidecar → LLM/embedding provider) is NOT approved for real internal
> data.** This phase uses synthetic/fictional/public content only.

---

## 1. Scope
A reproducible, source-level retrieval-quality baseline comparing the two retrieval
systems that exist **today**, measured — never modified:
- **VECTOR_BASELINE** — the existing Open Notebook vector search seam.
- **GRAPHRAG_BASELINE_CURRENT_HYBRID** — the currently-wired LightRAG v1.5.6 diagnostic
  hybrid query (`GraphRAGClient.query` / `GraphRAGService.query_strict`).

## 2. Non-goals
No HybridRetriever, RRF, weighted fusion, reranking, query routing/classification, Ask/
Chat/Source-Chat integration, frontend, citation changes, production retrieval routing,
public API, migration, or any GraphRAG-05+ work. No tuning of either system to improve
scores. This phase measures; it does not redesign.

## 3. Approved checkpoint
As above. Startup gate verified (branch/HEAD/tag/clean). No upstream pull/rebase/merge.

## 4. Data safety / synthetic-only rule
Only synthetic/fictional/public content is created, indexed, queried, or logged. No
Agribank/customer/employee/production data. Boundary B remains synthetic-only and is
NOT widened. Isolation is enforced structurally (§14, §23).

## 5. Benchmark v1 corpus
`tests/fixtures/graphrag_04_eval_v1/corpus.json` — **14** fictional canonical Sources
(`{key,title,text}`). A fictional research chain (Project Halcyon → Dr. Elena Voss →
Meridian Institute → Novak Foundation) and a shipping chain (Aurora Shipping → MV
Cormorant → Captain Riko Tan) cross at the **Aurora-7 catalyst** and the city
**Calderon**, with deliberate collisions (`Voss`, `Aurora`, `Halcyon`) and pure
distractors (S11–S13). Documents are plain prose — no query ids, labels, or hints.

## 6. Query taxonomy
`tests/fixtures/graphrag_04_eval_v1/queries.json` — **28** queries across 6 classes:
`direct` (5), `paraphrase` (5), `two_hop` (5), `three_hop` (4), `distractor` (5),
`negative` (4). Several relational queries carry **multiple** relevant sources.

## 7. DEV / HOLDOUT split
17 DEV / 11 HOLDOUT (≈61% / 39%); every class present in both splits; query-ids
disjoint. **HOLDOUT is frozen at 04 sign-off** and must not be tuned on by later phases.

## 8. Ground-truth methodology
Source-level relevance, **manually authored from the corpus before any retriever ran**,
independent of vector/graph output and of any generated answer. Negative queries have an
empty relevant set (genuine unanswerables). Validated at load time: every relevant key
exists; ids/keys unique; no empty text; class/split valid; negatives carry no labels;
answerables carry ≥1. (`dataset.py`; tests §43.)

## 9. Vector retrieval contract
`api/routers/search.py:22` → `domain/notebook.py:846 vector_search()` →
`fn::vector_search` (`migrations/9.surrealql:4`). Query embedding via
`generate_embedding()`. Results are **already source-level**: grouped by `id,parent_id,
title`, `math::max(similarity)`, `ORDER BY similarity DESC LIMIT $match_count`. Score =
cosine similarity (higher better); production floor `minimum_score=0.2`. Normalization key
= `parent_id` (= `source.id`). **Ranked, deterministic** ordering (ties unspecified — no
secondary sort key).

## 10. GraphRAG retrieval contract
`GraphRAGClient.query()` (`client.py:538`) → `POST /query` (LightRAG v1.5.6), exposed only
via the diagnostic `POST /api/search/graph`. It is the only wired retrieval path; Ask/Chat
are untouched. The client sends `{query, mode, include_references:true, top_k?}` and
**requires a generated `response`** — an LLM answer is an unavoidable overhead of the wired
path. Provenance returns as `references[]`; each `ReferenceItem.file_path` carries our
`source_id` (round-trip verified lossless in GraphRAG-02), mapped to `GraphReference.
source_id` with a structural `resolved` shape flag.

## 11. LightRAG query mode
`hybrid` (the wired default, `QueryMode.HYBRID`). Verified: `hybrid` = local (entity) +
global (relation) retrieval and **internally mixes graph traversal and embedding
similarity**. It is therefore named `GRAPHRAG_BASELINE_CURRENT_HYBRID` and explicitly **not
"graph-only"**. `only_need_context` exists in the v1.5.6 `QueryRequest` but is **not used**
and is **not added** in this phase (no client modification).

## 12. Ranking semantics
Verified against pinned `HKUDS/LightRAG@v1.5.6` `query_routes.py`: `ReferenceItem{
reference_id, file_path, content}` has **no score and no rank field**, and the `references`
list carries no relevance ordering. The client performs no re-sort. **GraphRAG references
are an unordered provenance set.** No rank is manufactured from JSON/response order (§50).

## 13. Provenance semantics
`file_path` → `source_id`, validated with the same structural RecordID helpers the
production boundary uses (`record_id_for` / `is_valid_record_id`). Valid `source:` ids are
candidates; `note:`/`source_insight:` are counted **foreign**; missing/invalid are
**malformed**. Foreign/malformed provenance is never a hit and is reported separately
(§24). `resolved` is shape-only (not existence/authorization — live validation is 05/06).

## 14. Source normalization
`eval/normalize.py`. VECTOR: rows → `parent_id` → canonical source id, deduped preserving
first/best rank → **ordered** list (five chunks from one source cannot take five slots).
GRAPH: references → `source_id` → canonical source id, deduped → **unordered set**
(`ordered=False`). Identity is lossless: `source:123` ≠ `source:⟨123⟩`; escaped ids
round-trip. Provenance accounting: total / valid_unique / duplicates / malformed / foreign.

## 15. Metric definitions
`eval/metrics.py`. VECTOR (ranked): `Hit@K`, `Recall@K`, `MRR` for K∈{1,3,5}. GRAPH (set):
`source_hit_rate`, `mean_source_recall`, provenance coverage. Complementarity per query at
budget K: both_hit / vector_only / graph_only / both_miss, and `ORACLE_UNION@K` = set union
of vector top-K and the graph set (an offline upper bound; **not** a produced hybrid).

## 16. Unsupported metrics
GraphRAG **MRR / nDCG are N/A** (unordered, no score) — reported literally as `N/A`, never
0. Every metric raises on an empty relevant set so a negative can never be silently scored
0 (§45.32). No graded relevance is invented for nDCG. No answer-quality metrics (ROUGE/
BLEU/EM/LLM-judge) — the generated answer is discarded, not scored (§56).

## 17. Multi-source relevance
HIT (≥1 relevant retrieved) is distinguished from RECALL (fraction of all relevant
retrieved), so a one-source hit on a two/three-hop query never implies full evidence (§19).

## 18. Complementarity metrics
Per candidate budget K: vector-only / graph-only / both-hit / both-miss counts; oracle-
union hit-rate and mean oracle-union recall. Budget asymmetry is explicit (§28): vector is
truncated to top-K (it has a rank); the graph set has no honest K so its full set is used.

## 19. Oracle-union definition
For a query at K: relevant source appears in `set(vector top-K) ∪ graph_set`. Set union
only — no fusion, no RRF. Diagnostic upper bound for whether a future hybrid could help.

## 20. Negative-query treatment
No precision metric (nearest-neighbour always returns something; no abstention contract).
Reported conservatively: candidate counts + any-returned rate per retriever = false-
confidence risk. Negatives are excluded from hit/recall denominators.

## 21. Error accounting
Every query carries an explicit per-retriever state: `evaluated`, `retriever_error`,
`timeout`, `invalid_provenance`, `unsupported_metric`, `skipped_with_reason`. Errors stay
in state-count accounting and are never dropped from denominators (`report.py`; test §45.31).

## 22. Reproducibility metadata
`runner.build_metadata()` records (no secrets): git commit, LightRAG version, query mode,
graph top_k, vector fetch + minimum_score, K budgets, corpus size, query/dev/holdout
counts, `db_total_sources` vs benchmark_sources, id namespace, namespace tag, timestamp.

## 23. Live indexing procedure
`runner.create_and_index()`: create each Source under a unique per-run id namespace
(`source:gr04e<run><NN>`) + tag `__graphrag04_eval_v1__`; **isolation proof** (every id to
be touched was created this run and carries the tag); vector-embed via the real
`embed_source` command; GraphRAG-index via the real 03A `service.index_source`; **bounded**
wait until every track is `PROCESSED` (acceptance ≠ completion) or `IndexNotReadyError`.

## 24. Live evaluation procedure
`runner.run()`: for each frozen query, real `vector_search` + real
`service.query_strict(hybrid)`; normalize both to canonical source ids; record per-query
states; the generated answer is discarded. No query text is rewritten per-retriever (§27).

## 25. Cleanup procedure
`runner.cleanup()`: for each created id only — eager sidecar delete
(`delete_document_for_source`, single doc), then delete embeddings + source row; verify no
tagged synthetic source WE created remains. Never a global purge; foreign sources untouched
(verified live, §38 tests). Tombstones written by the DB delete event drain normally.

## 26. Baseline results — **BLOCKED (two provider blockers; live run not executed)**
Live infrastructure probe (2026-08-30), including a single self-cleaning synthetic
Boundary-B probe (one fictional doc, indexed→polled→deleted via the approved lifecycle):

| Dependency | State |
|---|---|
| SurrealDB v2 | **UP** (reachable; only 1 pre-existing source — clean, low-noise) |
| LightRAG v1.5.6 sidecar (`ghcr.io/hkuds/lightrag:v1.5.6`) | **UP / healthy**; API-key auth on write routes (existing key works — never printed) |
| Sidecar LLM provider | `LLM_BINDING=openai`, **`LLM_MODEL=mock-llm`** @ `host.docker.internal:11500/v1` (mock, up) |
| Sidecar embedding provider | `EMBEDDING_BINDING=openai`, **`EMBEDDING_MODEL=mock-embed`** @ `:11500` (mock, up) |
| **Synthetic index through sidecar** | **FAILED** — accepted → `ANALYZING` → `DocStatus.FAILED`: the mock LLM cannot perform real entity/relation extraction. Query stage not reached. Doc cleaned up (`delete_state=gone`). |
| Open Notebook embedding model (VECTOR dependency) | **NOT CONFIGURED** — 0 model records, 0 credentials; `generate_embedding` raises "No embedding model configured" |
| Local embedding providers (Ollama :11434 / LM Studio :1234) | **not running** |

**Conclusion — the remaining blocker is NOT only the vector model. There are TWO:**
1. **GRAPH baseline** — the sidecar is bound to `mock-llm` / `mock-embed` (the 03E test harness).
   Real indexing FAILS, so no representative knowledge graph can be built and no valid GraphRAG
   retrieval baseline can be measured. Needs a **real synthetic-safe LLM + embedding provider**
   on the sidecar (Boundary B), which is a provider/credential decision the operator controls.
2. **VECTOR baseline** — no embedding model, no credentials, no local provider running.

Supported embedding providers (registry): `azure, cohere, google, mistral, ollama, omlx,
openai, openai_compatible, openrouter, ppq, vertex, voyage`. No suitable one is currently
configured or running, so configuration is **paused pending an operator provider choice**
(no credentials invented; nothing configured). **No baseline is fabricated (§65).**

### 26.1 OpenRouter provider verification (operator approved OpenRouter, synthetic-only)
The operator approved OpenRouter (synthetic-only) as a temporary provider. Verified BEFORE
any config change:
- `OPENROUTER_API_KEY` is **not present** locally.
- **OpenRouter serves NO embeddings.** Evidence: (1) `GET /api/v1/models` returns **396 models,
  zero embedding models** — `openai/text-embedding-3-small` is not offered; (2) OpenRouter docs
  expose only `/chat/completions` + `/generation`; (3) Open Notebook's own discovery
  **force-classifies every OpenRouter model as `language`** (`model_discovery.py:257`;
  `test_model_discovery.py:187`). The esperanto `OpenRouterEmbeddingModel` would POST to
  `https://openrouter.ai/api/v1/embeddings` optimistically, but nothing verifies that endpoint
  and the catalog proves no embedding model exists behind it.
- **Conclusion:** `text-embedding-3-small` via OpenRouter is impossible; this breaks BOTH the
  Open Notebook vector embedding AND the LightRAG embedding binding. OpenRouter **can** serve the
  LightRAG **LLM** (chat) side. Per operator instruction this was a STOP-and-report (no fallback
  provider chosen). Verified LightRAG v1.5.6 sidecar binding var names (from the running
  container, values redacted): `LLM_BINDING`, `LLM_BINDING_HOST`, `LLM_BINDING_API_KEY`,
  `LLM_MODEL`, `EMBEDDING_BINDING`, `EMBEDDING_BINDING_HOST`, `EMBEDDING_BINDING_API_KEY`,
  `EMBEDDING_MODEL` (no `EMBEDDING_DIM` set).

### 26.1.1 CORRECTION — OpenRouter embeddings DISPROVED as unavailable (live runtime evidence)
**Previous provider-discovery conclusion (retained, not deleted):** "OpenRouter serves no embeddings"
— based on the `/api/v1/models` catalog (396 models, 0 embedding), the docs, and Open Notebook's
discovery classifying all OpenRouter models as `language`.

**Live runtime result: DISPROVED.** Direct probes (operator-run, 2026-08-30):
- `POST https://openrouter.ai/api/v1/embeddings`, model `openai/text-embedding-3-small` →
  `OPENROUTER_EMBEDDING_PROBE = PASS`, embedding_count=1, **embedding_dimension=1536**.
- `POST` chat, model `openai/gpt-4o-mini` → `OPENROUTER_LLM_PROBE = PASS` (`GRAPHRAG04_LLM_OK`).
- LightRAG v1.5.6 sidecar authenticated `/health`: healthy; `llm_binding=openai`,
  `llm_model=openai/gpt-4o-mini`; `embedding_binding=openai`, `embedding_model=openai/text-embedding-3-small`
  (the "openai" binding is the OpenAI-compatible adapter; the configured host is OpenRouter).

**Reason the earlier conclusion was wrong:** OpenRouter's `/models` catalog does not enumerate embedding
models, but the `/embeddings` endpoint itself proxies `openai/text-embedding-3-small` successfully. The
discovery-layer signal (catalog + `language`-only classification) was necessary but NOT sufficient; the
live endpoint is authoritative. **The live runtime result takes precedence for GraphRAG-04 execution.**
Provider path for the live baseline is therefore **OpenRouter for everything** (embeddings + graph LLM),
observed embedding dimension **1536**.

### 26.2 Status (superseded): `GRAPH_RAG_04_EMBEDDING_PROVIDER_REQUIRED`
The live benchmark needs a real embedding provider (that actually serves `/embeddings`) for BOTH the
Open Notebook vector side and the LightRAG embedding binding. OpenRouter may back the GRAPH-side LLM
only. Resolved by operator decision → see §26.3.

### 26.3 Approved live configuration — Option B (OpenAI-direct), PENDING execution
Operator approved **Option B** (2026-08-30), synthetic/public GraphRAG-04 data ONLY (NOT internal data):
Open Notebook VECTOR embedding + LightRAG embedding = `text-embedding-3-small`; LightRAG LLM = `gpt-4o-mini`.
Operator is away; the key is configured **by the operator locally** (gitignored `.env` / sidecar container
env). **Nothing is configured until the operator returns.** Current status:
`GRAPH_RAG_04_OPENAI_CONFIGURATION_PENDING`.

**Runbook to execute on return (verified contracts, no invented names):**
1. **VECTOR (Open Notebook)** — `OPENAI_API_KEY` in gitignored `.env`. Create the embedding Model record
   (`provider=openai`, `name=text-embedding-3-small`, `type=embedding`) and set it as the default embedding
   model. Endpoint: esperanto `OpenAIEmbeddingModel` → `https://api.openai.com/v1/embeddings`. No container restart.
2. **Embedding dimension** — obtain the ACTUAL dimension from the provider response at probe time
   (`len(vector)`); do NOT assume. Set the LightRAG `EMBEDDING_DIM` to that verified value.
3. **LightRAG sidecar (v1.5.6, OpenAI bindings — verified var names)** — set in the container env:
   `EMBEDDING_BINDING=openai`, `EMBEDDING_BINDING_HOST=https://api.openai.com/v1`,
   `EMBEDDING_MODEL=text-embedding-3-small`, `EMBEDDING_BINDING_API_KEY=<OPENAI_API_KEY>`,
   `EMBEDDING_DIM=<verified>`, `LLM_BINDING=openai`, `LLM_BINDING_HOST=https://api.openai.com/v1`,
   `LLM_MODEL=gpt-4o-mini`, `LLM_BINDING_API_KEY=<OPENAI_API_KEY>`; keep existing `LIGHTRAG_API_KEY`.
   **Restart the sidecar** (its config is read at startup).
4. **GraphRAG client env for the run** — `OPEN_NOTEBOOK_GRAPHRAG_ENABLED=true`,
   `OPEN_NOTEBOOK_GRAPHRAG_BASE_URL=http://localhost:9621`, and the existing sidecar key
   (`OPEN_NOTEBOOK_GRAPHRAG_API_KEY`, read from the container at run time, never printed).
5. **Probe gate** — one isolated synthetic Source: `VECTOR_PROBE` (embed → `vector_search` → ranked
   canonical Source) and `GRAPHRAG_PROBE` (03A index → LightRAG `PROCESSED` → `client.query()` hybrid →
   usable provenance). Both must PASS; the generated answer is ignored.
6. **Full baseline** — only after both probes PASS: 14 sources / 28 queries / DEV 17 / HOLDOUT 11 →
   VECTOR_BASELINE + GRAPHRAG_BASELINE_CURRENT_HYBRID → source-level metrics + complementarity/oracle-union.
   `RRF_CANDIDATE_INTERFACE_READY` stays **NO** unless live runtime disproves the unordered-reference
   contract; `HYBRID_VALUE_EVIDENCED` decided from the live evidence. Do not fabricate graph rank.

## 26.5 Live baseline — EXECUTED 2026-08-30 (synthetic-only, OpenRouter + LightRAG v1.5.6)
Both gates passed: **`VECTOR_PROBE = PASS`** (real OpenRouter embedding, dim **1536**; probe source at
rank 1 through the normal path) and **`GRAPHRAG_PROBE = PASS`** (03A `graphrag_index_source` source_id-only
→ LightRAG PENDING→PROCESSING→PROCESSED via OpenRouter LLM+embed → `client.query()` hybrid → usable
canonical provenance, `ordered=False`). Full baseline: 14 sources / 28 queries (24 answerable + 4 negative);
DB held 15 sources during the run (14 benchmark + 1 pre-existing); cleanup verified clean (14 deleted,
0 remaining); environment restored (temp embedding model deleted, default reset to prior `None`).
Artifact: `.artifacts/graphrag-04/<run>/evaluation.json` (not committed).

### Aggregate (24 answerable)
| Metric | VECTOR_BASELINE | GRAPHRAG_BASELINE_CURRENT_HYBRID |
|---|---|---|
| Hit@1 / Hit@3 / Hit@5 | 0.333 / 0.625 / 0.875 | source_hit_rate **1.0** |
| Recall@1 / @3 / @5 | 0.215 / 0.535 / 0.826 | mean_source_recall **1.0** |
| MRR | 0.526 | **N/A** (unordered, no score) |
| Provenance | — | 265 candidates, **all valid** (0 malformed / 0 foreign / 0 dup); ≈**11.0 sources/query (~79% of the 14-source corpus)** |

### Complementarity + oracle union (24 answerable)
| K | both_hit | vector_only | graph_only | both_miss | oracle_union_hit |
|---|---|---|---|---|---|
| 1 | 8 | 0 | 16 | 0 | 1.0 |
| 3 | 15 | 0 | 9 | 0 | 1.0 |
| 5 | 21 | 0 | 3 | 0 | 1.0 |

## 27. Results by query class (VECTOR Hit@1/Hit@5/MRR · GRAPH hit/recall · graph candidates/query)
| Class (n) | VECTOR Hit@1 | Hit@5 | MRR | GRAPH hit / recall | graph cand/query |
|---|---|---|---|---|---|
| direct (5) | 0.20 | 1.00 | 0.48 | 1.0 / 1.0 | ~11.4 |
| paraphrase (5) | 0.20 | 1.00 | 0.42 | 1.0 / 1.0 | ~10.2 |
| two_hop (5) | 0.40 | 0.80 | 0.60 | 1.0 / 1.0 | ~11.0 |
| three_hop (4) | 0.75 | 0.75 | 0.79 | 1.0 / 1.0 | ~12.5 |
| distractor (5) | 0.20 | 0.80 | 0.38 | 1.0 / 1.0 | ~10.4 |
| negative (4) | N/A | N/A | N/A | N/A (no relevant by def.) | ~8.75 returned |

## 28. DEV results (14 answerable + 3 negative)
VECTOR Hit@1 0.286 / Hit@3 0.643 / Hit@5 0.857 / MRR 0.494. GRAPH hit 1.0 / recall 1.0 (152 valid
candidates). Complementarity k=5: both_hit 12, graph_only 2, vector_only 0, both_miss 0; oracle 1.0.

## 29. HOLDOUT results (10 answerable + 1 negative)
VECTOR Hit@1 0.40 / Hit@3 0.60 / Hit@5 0.90 / MRR 0.57. GRAPH hit 1.0 / recall 1.0 (113 valid
candidates). Complementarity k=5: both_hit 9, graph_only 1, vector_only 0, both_miss 0; oracle 1.0.

## 30. GraphRAG provenance quality
**Excellent mapping fidelity:** 265 references across 24 queries, **100% valid** canonical `source:` ids
(0 malformed, 0 foreign, 0 duplicate). The `file_path → source_id` round-trip works flawlessly live. BUT
the set is **broad and unranked**: ~11 of 14 sources returned per query, so the perfect hit/recall is a
low-precision artifact (precision ≈ 1/11 ≈ 0.09 for a single-relevant query), not discrimination.

## 31. Stability observations
One full baseline run (28 queries) plus two probe runs. Provenance mapping was clean and graph
hit/recall were saturated (1.0) across every answerable query and class — stable but trivially so given
the set breadth. Repeated-run variance under provider nondeterminism was **not** stress-tested (single
full run); flagged as a limitation. Vector ordering is deterministic given stable embeddings.

## INTERPRETATION (honest, non-forced)
- **GraphRAG's "perfect recall" is a small-corpus artifact, not retrieval quality.** Hybrid mode returns
  ~79% of a 14-source corpus per query — including for `distractor` and `negative` queries — as an
  UNORDERED set. It trivially contains the relevant source(s) but cannot indicate WHICH of the ~11
  returned sources matter. On a larger corpus this fraction (and the apparent recall) would fall.
- **Vector is a genuine ranked retriever** with real discrimination (Hit@5 0.875, MRR 0.53) but modest
  rank-1 precision on this densely-overlapping synthetic corpus (Hit@1 0.33; the 14 sources share
  entities/terms by design).
- **The "graph-only hits" are dominated by set breadth**, not precise complementary evidence, so they do
  not by themselves justify a precision-preserving hybrid.
- **Neither retriever abstains** on negative queries (graph ~8.75, vector ~5.25 candidates), a
  false-confidence risk if fed to an answer prompt.

## 32. RRF_CANDIDATE_INTERFACE_READY = **NO**
Decided by the forensic contract, independent of the live run: the GraphRAG side exposes
**no ranked/scored candidate list** (LightRAG v1.5.6 references are an unordered set with no
score/rank field). RRF requires ranked candidate lists on both sides; fabricating graph
rank is prohibited (§50). Vector *does* expose a meaningful ranked list, but a hybrid RRF
cannot be honestly built until a graph candidate-**ranking** contract exists. This does not
mean GraphRAG has no value — set-based recall/complementarity remain valid.

**Confirmed LIVE (2026-08-30):** the real LightRAG v1.5.6 hybrid response carried references with
`ordered=False` and no score field; the runtime provenance set is broad and unranked. No rank was
fabricated. RRF readiness stays **NO**.

## 33. HYBRID_VALUE_EVIDENCED = **INCONCLUSIVE**
The live baseline shows raw recall "complementarity" (graph_only_hit 16/9/3 at K=1/3/5; oracle union 1.0),
but this is an **artifact of GraphRAG returning ~79% of a 14-source corpus as an unordered set**, not
discriminative complementary retrieval. With no graph rank/score, no abstention, and a small corpus, the
evidence does **not** establish that a precision-preserving hybrid adds value — nor could an honest RRF be
built without a ranked graph candidate contract. **Not asserted YES.** A defensible YES/NO would require
(a) a larger corpus where graph recall is non-trivial, and (b) a ranked/scored graph candidate surface.

## 34. Known limitations
- Live baseline blocked on provider configuration (§26).
- Answer generation is unavoidable overhead of the wired path (cost/latency); the answer is
  not scored.
- Vector `minimum_score=0.2` floor is production behavior and may drop borderline candidates
  (recorded in metadata).
- Retrieval over a shared DB: non-benchmark sources (if present) are non-relevant noise;
  `db_total_sources` is recorded so signal-to-noise is visible.
- Only single hybrid mode measured (the wired default); other modes not benchmarked (avoids
  mode-tuning, §39).

## 35. Failure / race matrix
See §59 of the task; encoded in the runner (bounded readiness wait, per-query error states,
isolation proof, cleanup residue reporting) and tests. Key rows: index delay → bounded wait
→ `IndexNotReadyError`; query error/timeout → per-query `retriever_error`/`timeout` state,
stays in accounting; malformed/foreign provenance → counted, never a hit; duplicate chunks/
provenance → deduped; numeric/string-numeric/escaped ids → lossless; synthetic cleanup
partial → residue reported, not silently "clean"; foreign source present → never touched.

## 36. Exact files changed (all additive; no production/retrieval code modified)
- `tests/fixtures/graphrag_04_eval_v1/{corpus.json,queries.json,README.md}`
- `open_notebook/integrations/graphrag/eval/{__init__,dataset,normalize,metrics,report,runner}.py`
- `tests/test_graphrag_04_eval.py` · `tests/test_graphrag_04_eval_live.py`
- `docs/agribank/development/GRAPHRAG_04_EVALUATION.md` (this) · `CURRENT_PHASE.md` (status)

No edits to `vector_search`, graphrag client/service retrieval, Ask/Chat, routers, or any
migration.

## 37. Migration decision
**No migration.** 24/25 frozen; count stays **50** (no 26/27). A dev evaluation harness
needs no persistent schema.

## 38. Security review
Synthetic-only; Boundary B not widened. Report is content-free by construction (ids/metrics/
counts/non-secret metadata only — no `full_text`, no answer, no excerpts, no credentials;
tests assert this). Isolation: **all mutations (create/delete) are strictly scoped to the exact
created-id set**; foreign sources are never indexed or deleted; no global purge (verified against
the live DB: the pre-existing source was untouched, cleanup deleted exactly the 14 it made).
RecordID identity preserved losslessly. No secrets committed.

### 38.1 Review gate dispositions (Karpathy + Codex A/B/C, 2026-08-30)
- **Karpathy = PASS** (1 nit: an orphaned re-export in `runner.py` — fixed).
- **Codex C (architecture) = PASS** — 9/9 phase-boundary + 4/4 doc-integrity checks pass; migration
  count 50; no production file imports the eval package; no HybridRetriever/RRF/fusion/Ask/Chat.
- **Codex A (methodology) — resolved:** (MEDIUM) off-benchmark but structurally-valid `source:` ids
  could be counted as graph candidates → **fixed**: `normalize_graph_references` now takes a
  benchmark-id allowlist and counts off-allowlist source ids as `off_benchmark` (dropped, never a
  candidate); the runner passes its created-id set. Vector normalization stays global by design.
  (LOW) graph `source_hit_rate` naming → a `metric_note` now states it is broad-candidate coverage.
- **Codex B (security) — dispositions:** the "scope the retriever" HIGHs (vector_search / graph query
  are global) are **rejected as fixes**: measuring the real wired global seam is the methodology
  (§9/§27/§51 — scoping production retrieval is prohibited), and retrieval is **read-only** (it never
  mutates a Source). A non-benchmark candidate is simply non-relevant; graph off-benchmark provenance
  is now explicitly accounted (above) and was **0** in the live run. Applied fixes: cleanup now covers
  ambiguous-commit ids (ids recorded **before** the CREATE); `_assert_isolation` now **fails closed on
  benchmark-tagged residue** from a prior run. Accepted (LOW): `db_total_sources` is intended
  reproducibility metadata (a count, no content); provider error strings in `IndexNotReadyError` are
  exception-path only (never in the committed artifact) and are not credential-bearing.

### 38.2 Sidecar egress note (environment assumption)
GraphRAG queries hit the LightRAG sidecar's own derived index (Boundary B, synthetic-only). For a
clean baseline the sidecar should hold only benchmark docs; the live run confirmed this
(`foreign_total = 0`, `off_benchmark = 0`). The harness cannot police the sidecar's contents, but any
non-benchmark provenance is counted separately and never credited as a hit.

## 39. Acceptance criteria (status)
- [x] Methodology correct, reproducible, honestly named
- [x] Frozen benchmark (14 sources / 28 queries / 6 classes / DEV+HOLDOUT), validated
- [x] Evaluator: normalization, metrics, complementarity, error accounting, content-free report
- [x] Offline + live-DB isolation/cleanup tests green; ruff + mypy clean; no regression
- [x] **Live synthetic baseline executed** (real OpenRouter embeddings + real LightRAG v1.5.6 hybrid);
      both probe gates PASS; cleanup + environment restoration verified (§26.5)
- [x] Karpathy (CLEAN) + Codex A/B/C review gates (resolved; no unresolved actionable HIGH); full
      `mypy .` characterized (04 surface clean; 91 pre-existing 03-test errors, 0 new)
- [x] **Operator sign-off — 2026-08-30**

## 40. Recommendation for GraphRAG-05
`RRF_CANDIDATE_INTERFACE_READY = NO`: 05 cannot honestly implement RRF until a graph
candidate-**ranking** contract exists (LightRAG v1.5.6 provides none). If hybrid value is
later evidenced (oracle-union / graph-only hits from the live run), the next architecture
step is a graph retrieval surface that returns **ranked, scored** candidates (e.g. a
retrieval-only mode exposing chunk/entity scores), justified by 04 evidence — not a rank
fabricated over the current unordered provenance.
