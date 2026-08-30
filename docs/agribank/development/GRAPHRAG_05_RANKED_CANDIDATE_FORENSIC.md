# GraphRAG-05 — Ranked Graph Candidate Surface (FORENSIC / DESIGN GATE)

**Status: FORENSIC/DESIGN-ONLY — no implementation.** This is a design gate, not an
implementation phase. No production code, no retrieval code, no RRF/fusion/reranker, no
tests, no migrations, no API/frontend, no provider traffic, no DB or LightRAG-storage
mutation. `OPEN_NOTEBOOK_GRAPHRAG_ENABLED` remained **false**; the sidecar was **not**
started (every contract question was answerable statically from pinned source).

**Frozen input (GraphRAG-04, approved 2026-08-30):** commit
`cb86a06aebe45fe0c7bfdaf5f075def8709a7694`, tag `graphrag-04-approved`.
`RRF_CANDIDATE_INTERFACE_READY = NO`; `HYBRID_VALUE_EVIDENCED = INCONCLUSIVE`. Those
conclusions are authoritative and are **not** reopened or weakened here.

**Pinned source of truth.** All LightRAG findings below are read from the pinned tag, not
docs and not `main`:

```
HKUDS/LightRAG  tag v1.5.6  commit b33c6b0812cddf39206e48a9810112e51f025274
lightrag/_version.py:  __version__ = "1.5.6"   __api_version__ = "0328"
```

The source was obtained by a read-only `git clone --depth 1 --branch v1.5.6` into an
out-of-repo scratchpad (public open-source code; not a provider call, not internal-data
egress, not committed). Where docs and code disagree, **code wins**. Every finding cites
`file:line` in the pinned tree.

---

## 0. GraphRAG-05 question

> Can pinned LightRAG v1.5.6 expose enough **real retrieval evidence** to construct a
> defensible **ranked/scored canonical Source candidate** interface?

Direction is evidence → contract → (future) evaluation → (only then) possibly fusion. The
target is **not** vector+graph fusion; it is
`LightRAG → meaningful retrieval evidence → canonical Source candidates → defensible
score/rank semantics`.

**Answer (headline): NO.** Pinned v1.5.6 computes genuine query-relevance scores
internally (entity/relation/chunk embedding cosine, and cross-encoder `rerank_score`) but
**drops every one of them before any HTTP surface**. The only numerics that survive to the
API are **structural** (relationship `weight`, node degree) and **frequency-derived**
(`reference_id` citation index). No defensible ranked Source surface exists on pinned
v1.5.6 without modifying LightRAG. Details, options, and a design-only candidate contract
follow.

---

## 1. Current query path (as wired today) — exact call graph

Open Notebook wires exactly one retrieval call: `GraphRAGClient.query()`
(`open_notebook/integrations/graphrag/client.py:538`) → `POST /query`, body
`{query, mode:"hybrid", include_references:true, top_k?}` (client.py:553-559). It is exposed
only through the diagnostic `POST /api/search/graph`; Ask/Chat are untouched. The client
maps each returned `references[].file_path` → `source_id` and returns an **unordered set**
(`GraphReference`, `ordered=False`) — verified live in GraphRAG-04.

Pinned v1.5.6 path for that call:

```
POST /query                                      query_routes.py:447  query_text()
  └─ rag.aquery(query, param)                    lightrag.py:3643     (param.mode="hybrid")
       └─ kg_query(...)                          operate.py:4180      (local/global/hybrid/mix)
            └─ _build_query_context(...)         operate.py:5440      4-stage pipeline
                 ├─ Stage 1  _perform_kg_search  operate.py:4717
                 │    ├─ _get_node_data          operate.py:5563
                 │    │    entities_vdb.query()   operate.py:5574  ← cosine sim COMPUTED (order)
                 │    │    keeps entity_name + rank(=node degree); DROPS cosine  :5595-5607,5618
                 │    ├─ _get_edge_data          operate.py:5838
                 │    │    relationships_vdb.query() :5849 ← cosine sim COMPUTED (order)
                 │    │    edges sorted by (rank,weight):5672-5673 / "vector order":5882
                 │    │    keeps weight(structural); DROPS cosine
                 │    └─ _get_vector_context      operate.py:4660  (mix/naive vector chunks)
                 │         chunks_vdb.query()      operate.py:4690  ← cosine distance COMPUTED
                 │         keeps content/file_path/chunk_id; DROPS distance :4700-4709
                 ├─ Stage 2  _apply_token_truncation      operate.py:4938  (token-budget cut)
                 ├─ Stage 3  _merge_all_chunks            operate.py:5153
                 │    ROUND-ROBIN interleave of vector/entity/relation chunk lists :5199-5246
                 │    merged dicts carry content/file_path/chunk_id — NO score
                 └─ Stage 4  _build_context_str           operate.py:5261
                      ├─ process_chunks_unified           utils.py:5601
                      │    ├─ apply_rerank_if_enabled      utils.py:5470
                      │    │    IF rerank_model_func set: doc["rerank_score"]=score :5543
                      │    │    ELSE warn + return unranked                      :5494-5498
                      │    ├─ filter by min_rerank_score (default 0.5)           :5643-5657
                      │    └─ chunk_top_k cut + token truncation
                      ├─ generate_reference_list_from_chunks  utils.py:6200
                      │    reference_id assigned by CHUNK-OCCURRENCE FREQUENCY   :6238-6245
                      └─ convert_to_user_format               utils.py:6076
                           rebuilds entities/relations/chunks/references with a
                           FIXED no-score allowlist — rerank_score/distance/sim  :6099-6172
  └─ handler extracts data.references                query_routes.py:537
  └─ QueryResponse{response, references:[ReferenceItem{reference_id,file_path,content?}]}
                                                     query_routes.py:236-244, 568-571
```

**Interleave is not limited to chunks (verified `_perform_kg_search`).** In the wired
`hybrid` mode the two entity lists (`local_entities` from `_get_node_data`, `global_entities`
from `_get_edge_data`) are themselves **round-robin merged** (operate.py:4869-4888), as are the
two relation lists (operate.py:4890-4923). `vector_chunks` is populated **only in `mix`** mode
(operate.py:4848), so in `hybrid` the chunk merge draws from the entity/relation chunk lists,
still round-robin (operate.py:5199-5246). So no `data` array — entities, relationships, or
chunks — carries a single comparable relevance ordering in the wired mode.

**Where score/rank is created, transformed, and lost:**

| Stage | Score created | What happens to it |
|---|---|---|
| `entities_vdb.query` (5574) | entity embedding **cosine similarity** (genuine query relevance) | used to **order** results; the value is **dropped** at `_get_node_data` (only `entity_name` + `rank`=degree kept) |
| `relationships_vdb.query` (5849) | relation embedding **cosine similarity** | used to **order** (global); value dropped; `weight`(structural) kept |
| `chunks_vdb.query` (4690) | chunk embedding **cosine distance** | dropped at `_get_vector_context` (only content/ids kept) |
| `_merge_all_chunks` (5153) | — | **round-robin interleave** destroys any single global ordering; dicts scoreless |
| `apply_rerank_if_enabled` (5470) | cross-encoder **`rerank_score`** (genuine relevance) + `min_rerank_score` cutoff | only if a rerank model is configured; **dropped by `convert_to_user_format`** |
| `generate_reference_list_from_chunks` (6200) | `reference_id` = **frequency rank** of file occurrence | survives to HTTP, but it is frequency, not relevance |
| `convert_to_user_format` (6076) | — | emits a fixed allowlist; **no score field on entity/chunk/reference**; only relationship `weight` survives |

**Net:** the genuine query-relevance signals (embedding cosine at three VDBs; cross-encoder
`rerank_score`) are all **computed and then discarded** before the response is serialized.
`ReferenceItem` (query_routes.py:236-244) has exactly `{reference_id, file_path, content?}`
— **no score, no rank** — confirming the GraphRAG-04 finding against the same pinned tag.

---

## 2. All possible scored surfaces (Forensic Target B) — inventory + classification

Score-kind classes (task §7): **A** QUERY_RELEVANCE_SCORE · **B** QUERY_RELEVANCE_ORDER_ONLY
· **C** STRUCTURAL_SIGNAL · **D** FREQUENCY_SIGNAL · **E** UNSPECIFIED/UNSAFE. Only A (or a
rigorously justified B) is usable as a direct ranking signal.

| # | Surface | Signal | Query-dep? | Numeric? | Meaning | Sorted by it? | Provenance→Source | Graph-native? | Safe for rank? | API-visible? | Class | Risk |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | `entities_vdb.query` distance (5574) | entity embedding cosine | Yes | Yes | query↔entity(name+desc) similarity | order only | entity→chunks→file_path→source (PARTIAL, multi-source) | No (embedding) | Would be, **but LOST** | **No** (dropped 5595-5607) | A→lost | reaching it needs LightRAG patch |
| 2 | entity `rank` (5601) | node **degree** | No | Yes | graph connectedness | edge sort key | n/a | Yes | **No** (structural≠relevance) | No (not in /query/data entity fields) | C | degree↦relevance fallacy |
| 3 | relation `weight` (utils 6142) | **edge strength** (extraction-time) | No | Yes | relationship strength (default 1.0) | edge sort key (5673) | relation→chunks→source (PARTIAL) | Yes | **No** (structural≠relevance) | **Yes** (/query/data only) | C | looks like a score; is not |
| 4 | `relationships_vdb.query` distance (5849) | relation embedding cosine | Yes | Yes | query↔relation similarity | order only | as #3 | No | Would be, **but LOST** | **No** | A→lost | as #1 |
| 5 | `chunks_vdb.query` distance (4690) | chunk embedding cosine | Yes | Yes | query↔chunk similarity | order only | chunk.file_path→source (STRONG) | No | Would be, **but LOST** | **No** (dropped 4700-4709) | A→lost | as #1 |
| 6 | `rerank_score` (utils 5543) | **cross-encoder relevance** | Yes | Yes | calibrated query↔chunk relevance | yes (provider order) + `min_rerank_score` cutoff | chunk.file_path→source (STRONG) | No | **Yes in principle** — but LOST + needs model | **No** (dropped by convert_to_user_format) | A→lost | best signal; unreachable |
| 7 | `reference_id` (utils 6238-6245) | file **occurrence frequency** rank | Yes (which chunks retrieved) | string index | citation index by #chunks/file | implicit | file_path→source (STRONG) | No | **No** (frequency≠relevance) | **Yes** (/query, /query/data) | D | mistaking freq-id for rank |
| 8 | `chunk_occurrence_count` WEIGHT method (5748-5751,6039-6042) | frequency | partial | Yes | # entities/relations pointing to a chunk | fallback sort | chunk→source (STRONG) | Yes-ish (KG co-occurrence) | **No** (frequency, KG-fanout biased) | No (internal fallback only) | D | length/degree bias |
| 9 | array **order** of `references[]` (/query) | order artifact | — | No | reference_id (frequency) order | — | file_path→source | No | **No** (§50: order≠rank) | Yes | B-unsafe | client already treats unordered |
| 10 | array **order** of `chunks[]`/`entities[]`/`relationships[]` (/query/data) | order artifact | Yes | No | retrieval order | — | as above | No | **No** in hybrid/mix (round-robin interleave, 5199-5246); score gone even in pure modes | Yes | B-unsafe | round-robin destroys global order |
| 11 | `metadata.processing_info` counts (lightrag 3770-3777) | counts | — | Yes | totals before/after truncation | — | n/a | — | No (not per-candidate) | Yes (/query/data) | E | not a candidate signal |
| 12 | `cosine_better_than_threshold` (base.py:229, default 0.2) | absolute VDB floor | Yes | Yes | drop candidates below cosine 0.2 | filter | n/a | No | filter only; value not surfaced | No | A-derived filter | silent; not exposed |

**Reading of the table.** The only genuine **A** (query-relevance) signals are #1/#4/#5/#6 —
all embedding-cosine or cross-encoder, i.e. **VECTOR-equivalent**, and **all four are dropped
before HTTP**. Every numeric that *does* reach the API is **C (structural: weight, degree)**
or **D (frequency: reference_id, occurrence)**. Array order (#9/#10) is **B but unsafe**:
in the wired `hybrid` mode the final chunk/entity/relation order is a **round-robin
interleave** of independently-ordered sublists, so it is not a single comparable relevance
ranking (task §50: position must not become rank).

---

## 3. Structured query endpoint (Forensic Target C) — `/query/data`

Pinned v1.5.6 **does** expose a structured endpoint that GraphRAG-04 did not measure:

```
POST /query/data      query_routes.py:1309  query_data()
  param.to_query_params(False)               query_routes.py:1413
  rag.aquery_data(query, param)              lightrag.py:3701
      sets only_need_context=True            lightrag.py:3817   (no LLM generation)
      kg_query / naive_query → raw_data = convert_to_user_format(...)
  → QueryDataResponse{status,message,data,metadata}   query_routes.py:261-269
```

**Response schema (documented at lightrag.py:3717-3778, produced by `convert_to_user_format`
utils.py:6076-6197):**

- `data.entities[]` = `{entity_name, entity_type, description, source_id, file_path,
  created_at, reference_id}` — **no score.**
- `data.relationships[]` = `{src_id, tgt_id, description, keywords, weight, source_id,
  file_path, created_at, reference_id}` — `weight` is **structural** edge strength
  (default 1.0), **not** query relevance.
- `data.chunks[]` = `{content, file_path, chunk_id, reference_id}` — **no score.**
- `data.references[]` = `{reference_id, file_path}` — `reference_id` is the **frequency**
  index.
- `metadata.processing_info` = counts only.

Determinations required by task §8:
- **Scores exist?** No query-relevance score on any field; only structural `weight`.
- **Post-ranking or raw metadata?** Post-*ordering*, but the ordering value is gone; fields
  are raw KG/chunk metadata.
- **Provenance retained?** Yes — every entity/relation/chunk carries `file_path`
  (+`source_id`), the same lossless join key GraphRAG-04 verified 100% valid live.
- **One call without answer generation?** Yes (`only_need_context=True`, no LLM) — cheaper
  and cleaner than the wired `/query` path, which forces an LLM answer.
- **Would using it change 04 semantics?** It changes the *shape* (structured vs
  references-only) and removes the forced LLM answer, but the underlying retrieval is the
  same `kg_query`; it exposes **more provenance** (entities/relations/chunks, not just the
  frequency-ranked reference list) but **no additional score**.
- **Would using it change production behavior?** No — it is an additional read-only endpoint;
  Open Notebook does not call it today.

**Conclusion:** `/query/data` is a materially better **evidence/provenance** surface (Option
A/B substrate) but it is **not** a scored surface. It does not create a query-relevance rank.

---

## 4. Source provenance (Forensic Target D)

Required chains, verified against pinned code and GraphRAG-02/04 live evidence:

```
chunk    → chunk.file_path            → canonical source_id      [STRONG, direct, lossless]
entity   → entity.source_id/file_path → chunk(s) → file_path(s)  → source_id  [PARTIAL]
relation → relation.source_id         → chunk(s) → file_path(s)  → source_id  [PARTIAL]
```

- **Chunk provenance is STRONG.** `chunk.file_path` carries the `source_id` Open Notebook
  passed as `file_source`; GraphRAG-04 measured 265 live references at **100% valid** canonical
  ids (0 malformed / 0 foreign / 0 duplicate). The `file_path → source_id` mapping uses the
  same structural RecordID helpers as the outbound boundary (`is_valid_record_id`,
  `record_id_for`), so identity is lossless (numeric ≠ string-numeric; escaped ids
  round-trip).
- **Entity/relation provenance is PARTIAL.** An entity/relation is derived from *one or more*
  chunks and can therefore map to **multiple** sources, or to `"unknown_source"`
  (`convert_to_user_format` default, utils.py:6105/6117/6144). A single entity does not
  cleanly own one Source.
- **Foreign/ambiguous provenance stays report-only / invalid** (GraphRAG-03D STRONG-ownership
  rule remains authoritative). Never `entity name → guess source`, never
  `LLM citation text → guess source`.

Provenance is **not** the blocker for a ranked surface; the missing scores are.

---

## 5. Source-level aggregation forensic (task §10)

Because no per-candidate query-relevance score reaches the API, source-level aggregation
would have to run on the only exposed numerics — **structural `weight`/degree** or
**frequency (`reference_id`/occurrence)** — or on a score that does not yet exist. Every
candidate family carries a documented bias:

| Aggregation family | Definition | Fatal bias(es) on exposed v1.5.6 signals |
|---|---|---|
| MAX chunk relevance / source | max over chunks | **no exposed chunk relevance** to take a max of |
| TOP-N chunk score sum | sum top-N | same — nothing to sum |
| SUM w/ dup controls | Σ evidence | source-**length** & chunk-**count** bias (long docs win) |
| entity-evidence agg | Σ/│entities│ per source | entity-**density** bias; multi-source entities double-count |
| relation-evidence agg | Σ weight per source | **degree/fan-out** bias; `weight` is structural, not relevance |
| combined e+r+c | weighted blend | requires arbitrary inter-signal weights → tuning before meaning |

Frequency-based source scoring (count of retrieved chunks per source, i.e. what
`reference_id` already encodes) is biased by **document length and chunk count**, is **not**
query relevance, and reproduces the GraphRAG-04 low-precision, broad-set behavior.

`SOURCE_LEVEL_AGGREGATION_DEFENSIBLE = REQUIRES_EXPERIMENT` (explicitly **not YES**, and not
yet downgraded to NO). Two distinct experiments are conflated by a bare "aggregate the
evidence": (a) aggregating a **real per-candidate relevance score** is only meaningful **after**
such a score is surfaced (Option D) — until then there is nothing relevance-bearing to
aggregate; (b) aggregating the **exposed** structural/frequency signals is *computable today*
but is **expected to be biased** (table above) and is **not itself a relevance signal**, so any
value it has is an open empirical question, never an approved formula. Either way the verdict is
"unresolved pending evidence on a larger corpus," which is REQUIRES_EXPERIMENT — **not** a
green light to build an aggregation now. No formula is proposed or endorsed here (task §10, §16).

---

## 6. Multi-hop / graph-native value (task §11)

Classify the candidate *signals*, not the wish:

- The genuine relevance signals available anywhere in the pipeline (#1/#4/#5/#6) are
  **embedding cosine + cross-encoder rerank → VECTOR_EQUIVALENT**. A ranked surface built on
  them would rank by vector similarity, providing **no graph-specific signal**.
- The graph-native numerics (`weight`, node degree, KG co-occurrence count) are
  **STRUCTURAL/FREQUENCY**, not query relevance. Ranking by them is **GRAPH_AUGMENTED at best,
  but not query-relevance** — high degree ≠ high relevance.
- The multi-hop *evidence recovery* value of GraphRAG is real (GraphRAG-04 oracle-union), but
  it manifests as **set membership** (a related source appears in the candidate set), **not**
  as a score. Reducing GraphRAG to exposed cosine order would be **VECTOR_EQUIVALENT** and
  would discard the graph value.

`GRAPH_NATIVE_RANKING_SIGNAL = NO`: no exposed signal is simultaneously **query-relevant** and
**graph-native**.

---

## 7. Negative / abstention (task §12)

Two real cutoff mechanisms exist in pinned v1.5.6:

- `cosine_better_than_threshold` (base.py:229, default **0.2**) — an absolute VDB floor
  applied inside each `*_vdb.query`. Genuine, but the score is **not surfaced** and the floor
  is silent.
- `min_rerank_score` (utils.py:5644, default **0.5**) — an absolute cutoff that can **empty**
  the chunk set (utils.py:5664-5665). Genuine calibrated abstention — **but** it fires only
  when a rerank model is configured; with none configured the code defaults each chunk to
  `1.0` (utils.py:5652), so **nothing is filtered**, and either way the score is dropped
  before HTTP.

Open Notebook's sidecar (GraphRAG-04 §26.1 verified bindings) configures **LLM + embedding
only — no rerank binding**. So as deployed there is **no abstention** (matching GraphRAG-04:
graph returned ~8.75 candidates even for negative queries).

`ABSTENTION_SIGNAL_AVAILABLE = UNCLEAR`: a real calibrated cutoff exists in the engine, but it
is **not exposed on any HTTP response**, is **off** in the deployed configuration, and cannot
be read as a per-candidate confidence without modifying LightRAG. Do **not** invent a
threshold in this phase.

---

## 8. Proposed GraphCandidate contract (DESIGN-ONLY — not implemented)

Given the evidence, the **smallest defensible** contract is an **UNRANKED evidence candidate**
— it deliberately has **no score field**, because no honest query-relevance score exists on
pinned v1.5.6, and inventing one is prohibited (task §16, §50).

```text
GraphSourceCandidate            # design sketch; NOT code
  source_id: str                # REQUIRED. canonical Open Notebook id, from chunk.file_path
                                #   (lossless via record_id_for). Source of truth: LightRAG
                                #   /query/data provenance. Invariant: is_valid_record_id.
  evidence_types: frozenset[EvidenceType]   # REQUIRED. subset of {CHUNK, ENTITY, RELATION}
                                #   that pointed at this source this query. Semantics: which
                                #   retrieval branches surfaced it. NOT a score.
  supporting_chunk_count: int   # OPTIONAL. # distinct retrieved chunks whose file_path == this
                                #   source. Source of truth: data.chunks[]. Semantics:
                                #   FREQUENCY signal (D), explicitly NOT relevance. >= 0.
  provenance_quality: ProvenanceQuality     # REQUIRED. VALID | FOREIGN | MALFORMED
                                #   (GraphRAG-03D ownership). FOREIGN/MALFORMED never citable.
  # NO `score` / `rank` field. Absent by design. See §9.
```

`EvidenceType = {CHUNK, ENTITY, RELATION}`; `ProvenanceQuality = {VALID, FOREIGN, MALFORMED}`.

Serialization: all fields JSON-native; `evidence_types` as a sorted list; `source_id` as the
canonical string (escaped form preserved). The candidate is an **unordered set element**,
consistent with GraphRAG-04 `ordered=False`.

If — and only if — a future Option-D change surfaces a real per-candidate relevance score,
extend the contract with `relevance_score: float` **and** `score_kind: enum{RERANK_CE,
VECTOR_COSINE}` so the score's provenance is never ambiguous. Until then those fields are
**omitted**, not defaulted to 0/None (a 0 would read as "irrelevant", a fabrication).

---

## 9. Critical rank contract (task §14)

> **NO DEFENSIBLE RANKED SOURCE SURFACE EXISTS IN PINNED v1.5.6.**

There is no verified rule that makes candidate A rank above candidate B:
- "whatever order LightRAG returned" → round-robin interleave (operate.py:5199-5246); not a
  ranking. **Rejected.**
- "higher edge degree/weight" → structural, not query relevance (`rank`, `weight`).
  **Rejected.**
- "sum the signals" → arbitrary inter-signal weights; length/density/degree biased (§5).
  **Rejected.**
- "reference_id order" → frequency of chunk occurrence, not relevance (utils.py:6238-6245).
  **Rejected.**

This is an accepted forensic outcome, not a gap to design around (task §14, §23).

---

## 10. Architecture options

| Option | Correctness | Ranking semantics | Graph value | Provenance | Abstention | LightRAG-internal coupling | Maintenance risk | Complexity | Future-RRF suitability |
|---|---|---|---|---|---|---|---|---|---|
| **A** Existing scored surface, normalized | **Not viable** — no query-relevance score on any HTTP surface | none | n/a | STRONG (chunks) | none | low | n/a | n/a | none |
| **B** Derive source score from lower-level evidence | Weak — only structural/frequency exposed | frequency/structural, **not** relevance | GRAPH_AUGMENTED but biased | STRONG (chunks) / PARTIAL (kg) | none | low–med | med (bias tuning) | med | poor (not honest relevance) |
| **C** No ranked interface; keep GraphRAG as **unranked evidence/context** (`GraphSourceCandidate`, §8) | **Correct & honest** | none by design | set-membership (multi-hop recall) | STRONG | none | **low** (uses /query/data provenance only) | **low** | low | none yet — but no fabrication |
| **D** Upstream/config change to expose a real score | Correct **if** upstream surfaces the score | genuine (rerank_ce / vector_cosine) | VECTOR-equivalent unless a KG-relevance score is added upstream | STRONG | rerank cutoff becomes usable | **high** (patch `_get_vector_context`/`_get_node_data`/`convert_to_user_format` or add endpoint) + config a rerank model | high (fork drift) | high | the only path to honest ranked candidates |

**Preferred: OPTION C**, now. It is the only option that is correct and honest under pinned
v1.5.6: expose GraphRAG as an **unranked, provenance-strong evidence set** (ideally sourced
from `/query/data`, which is cheaper — no forced LLM answer — and richer than the wired
`/query` references), with **no score and no rank**. Option D is documented as the *only*
route to a real ranked surface and is **explicitly out of scope** (it would modify pinned
LightRAG behavior and/or require a version/upstream change — both prohibited this phase; task
§2). Do not implement D.

---

## 11. Future implementation scope (when/if pursued — NOT now)

Under Option C: an additive, read-only `GraphSourceCandidate` adapter over `/query/data`
provenance (unordered set, no score), behind the existing flag, never wired into Ask/Chat/
citations. No RRF, no fusion, no rank. Under Option D (separate approved decision only):
either configure a rerank model on the sidecar **and** contribute/adopt an upstream change
that surfaces `rerank_score` (or the VDB cosine) through the response — then re-run the
evaluation before any fusion.

---

## 12. Future evaluation design (DESIGN-ONLY — do not run; task §17)

Reuse the GraphRAG-04 harness discipline (source-level ground truth authored before any
retriever runs; DEV/HOLDOUT frozen; content-free reporting; strict created-id isolation;
metrics raise on empty relevant sets so negatives are never silently scored 0).

- **Corpus requirement (not contents):** materially **larger** than 04 — target **≥60–100**
  canonical sources so that a broad candidate set (≈top-k) covers only a **small fraction** of
  the corpus and can no longer trivially produce perfect recall. Keep the 6 query classes
  (direct/paraphrase/two_hop/three_hop/distractor/negative), several multi-relevant queries,
  deliberate entity collisions, and pure distractors. Define **requirements**, not fixtures, at
  this gate; do not author corpus/queries here (avoids tuning).
- **Metrics:** Hit@K, Recall@K, precision@K (now meaningful on a larger corpus),
  candidate-set size, negative-query candidate count, abstention behavior; **MRR/nDCG only if
  a legitimate rank exists** (Option D) — otherwise `N/A`, never 0. Complementarity:
  vector-only / graph-only / both-hit / both-miss and ORACLE_UNION@K (set union, not fusion).
  Report per class and per DEV/HOLDOUT.
- **Purpose:** decide, on real evidence, whether a ranked graph candidate surface (post
  Option D) adds precision-preserving value **before** any RRF is considered.

---

## 13. Adversarial design review (task §24)

- **Review A — score semantics.** Claim: relationship `weight`, node `rank`, `reference_id`
  are relevance scores. **Disproved from source:** `weight` is extraction-time edge strength
  (default 1.0, utils.py:6142); `rank` is node degree (operate.py:5601); `reference_id` is
  chunk-occurrence frequency (utils.py:6238-6245); array order in hybrid is a round-robin
  artifact (operate.py:5199-5246). None is query relevance. The only relevance signals
  (cosine/rerank) are dropped before HTTP. **Held.**
- **Review B — aggregation bias.** Any source score derived from exposed signals is biased by
  source length, chunk count, entity density, degree, and multi-hop fan-out (§5). **Held —**
  hence REQUIRES_EXPERIMENT, and only after a real score exists.
- **Review C — phase boundaries.** No HybridRetriever/RRF/fusion/reranker/Ask/Chat/frontend/
  retriever/benchmark code was written. No production/test/migration/API change. Documentation
  and planning only. **Held.**
- **Review D — security/governance.** Zero provider/LLM/embedding traffic. No internal-data
  egress. No `.env`/sidecar/credential change. No SurrealDB or LightRAG-storage mutation. No
  Source create/delete. `OPEN_NOTEBOOK_GRAPHRAG_ENABLED` stayed false; sidecar not started.
  Pinned LightRAG source read via read-only clone in an out-of-repo scratchpad (public code,
  not committed). No source content copied into this doc. **Held.**

---

## 14. Required decisions

```
LIGHTRAG_DIRECT_SCORED_SOURCE_SURFACE   = NO
LOWER_LEVEL_QUERY_SCORE_AVAILABLE       = PARTIAL   (computed internally: entity/relation/chunk
                                                     cosine + cross-encoder rerank_score; NOT
                                                     exposed on any HTTP surface; reaching it
                                                     requires modifying LightRAG)
SOURCE_PROVENANCE_FOR_SCORED_EVIDENCE   = STRONG    (chunk.file_path→source_id lossless,
                                                     100% valid live; entity/relation
                                                     aggregated provenance is PARTIAL)
SOURCE_LEVEL_AGGREGATION_DEFENSIBLE     = REQUIRES_EXPERIMENT  (and only after a real
                                                     per-candidate score exists; not on
                                                     structural/frequency signals)
GRAPH_NATIVE_RANKING_SIGNAL             = NO        (relevance signals are VECTOR-equivalent;
                                                     graph-native numerics are structural)
ABSTENTION_SIGNAL_AVAILABLE             = UNCLEAR   (min_rerank_score / cosine floor exist in
                                                     the engine but are not exposed and are off
                                                     in the deployed config)
GRAPH_CANDIDATE_CONTRACT_DESIGNABLE     = YES       (an UNRANKED evidence contract, §8;
                                                     a RANKED contract is NOT designable on
                                                     exposed v1.5.6 signals)
GRAPH_CANDIDATE_IMPLEMENTATION_READY    = NO
RRF_CANDIDATE_INTERFACE_READY           = NO        (mandated; unchanged from GraphRAG-04)
```

---

## 15. Final report

```
GRAPH_RAG_05_FORENSIC = COMPLETE
FILES_CHANGED =
  docs/agribank/development/GRAPHRAG_05_RANKED_CANDIDATE_FORENSIC.md   (new)
  docs/agribank/development/CURRENT_PHASE.md                           (GraphRAG-05 row)
  .planning/2026-08-30-graphrag-05-forensic-design/{task_plan,findings,progress}.md (new)
  .planning/.active_plan                                              (slug updated)
PRODUCTION_CODE_CHANGED   = NO
TEST_CODE_CHANGED         = NO
MIGRATION_CHANGED         = NO
PROVIDER_TRAFFIC          = NO
DATABASE_MUTATION         = NO
LIGHTRAG_STORAGE_MUTATION = NO
```

1. **Call graph:** §1 (pinned v1.5.6, commit b33c6b0).
2. **Score/rank surfaces:** §2 table (12 surfaces).
3. **Where score is lost:** §1 loss table — every cosine/rerank score dropped before HTTP.
4. **Source provenance quality:** STRONG for chunks (lossless, 100% live-valid); PARTIAL for
   entity/relation aggregates (§4).
5. **Direct scored Source surface exists?** No (§2, §3).
6. **Lower-level scores defensibly aggregatable?** Not on exposed signals; REQUIRES_EXPERIMENT
   only after a real score exists (§5).
7. **Graph-native vs vector-equivalent:** relevance signals are VECTOR-equivalent; graph
   numerics are structural (§6).
8. **Abstention:** UNCLEAR — real engine cutoffs exist but are unexposed and off as deployed
   (§7).
9. **Recommended contract:** unranked `GraphSourceCandidate`, no score field (§8).
10. **Options A/B/C/D:** §10.
11. **Preferred:** Option C (unranked evidence/context via `/query/data`); Option D is the only
    route to a real rank and is out of scope (§10).
12. **Future implementation scope:** §11.
13. **Future evaluation design:** larger corpus (≥60–100 sources), §12.
14. **Unresolved risks:** (a) any future ranked surface needs an Option-D LightRAG change =
    fork-drift/maintenance risk; (b) rerank abstention needs a configured rerank model on the
    sidecar (Boundary-B provider decision); (c) evaluation of hybrid value is still pending a
    larger corpus (GraphRAG-04 `HYBRID_VALUE_EVIDENCED = INCONCLUSIVE`).

**GRAPH_RAG_05_FORENSIC_DESIGN_GATE_COMPLETE** — no commit, no push, no tag; no GraphRAG-05
implementation; no GraphRAG-06.
