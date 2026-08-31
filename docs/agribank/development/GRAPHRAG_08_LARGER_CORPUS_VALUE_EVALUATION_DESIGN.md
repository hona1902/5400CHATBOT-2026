# GraphRAG-08 — Larger-Corpus Structured Evidence Value Evaluation (DESIGN GATE)

**Status:** DESIGN / BENCHMARK-DESIGN GATE ONLY — no execution, no implementation.
**Date:** 2026-08-31
**Phase kind:** forensic / benchmark-design gate (mirrors GraphRAG-05/06/07 gate discipline).
**Author path:** `docs/agribank/development/` (internal fork; see `AGRIBANK.md`).

> This document DESIGNS the means to answer a value question. It does **not** answer it, does
> **not** run a benchmark, does **not** implement the GraphRAG-07 Structured Evidence Adapter,
> and does **not** touch production retrieval. Every "will / must / runs" below describes a
> *future* operator-approved execution phase, never an action taken in this gate.

---

## 0. Frozen checkpoints & inputs

| Item | Value |
|---|---|
| GraphRAG-04 approved | `cb86a06` — tag `graphrag-04-approved` |
| GraphRAG-05 forensic approved | `833ec59` — tag `graphrag-05-forensic-approved` |
| GraphRAG-06 forensic approved | `d7e6a5b` — tag `graphrag-06-forensic-approved` |
| GraphRAG-07 contract approved | `337456d` — tag `graphrag-07-contract-approved` |
| LightRAG pinned | HKUDS v1.5.6 commit `b33c6b0` (`__api_version__ 0328`) |

**Authoritative frozen decisions carried in (unchanged by this gate):**

- GraphRAG-04: `HYBRID_VALUE_EVIDENCED = INCONCLUSIVE`; `RRF_CANDIDATE_INTERFACE_READY = NO`.
- GraphRAG-05: `LIGHTRAG_DIRECT_SCORED_SOURCE_SURFACE = NO`; `LOWER_LEVEL_QUERY_SCORE_AVAILABLE
  = PARTIAL`; `SOURCE_LEVEL_AGGREGATION_DEFENSIBLE = REQUIRES_EXPERIMENT`;
  `GRAPH_NATIVE_RANKING_SIGNAL = NO`; `GRAPH_CANDIDATE_IMPLEMENTATION_READY = NO`.
- GraphRAG-06: `QUERY_DATA_AVAILABLE = YES`; `QUERY_DATA_AVOIDS_FINAL_ANSWER_GENERATION = YES`;
  `QUERY_DATA_OTHER_LLM_CALLS_REMAIN = YES`; `RETRIEVAL_SEMANTICS_PARITY = YES`;
  `QUERY_DATA_PROVENANCE_QUALITY = STRONG(chunk/reference)/PARTIAL(entity/relation)`;
  `QUERY_DATA_EXPOSES_VALID_RANK = NO`; `QUERY_DATA_EXPOSES_VALID_SCORE = NO`;
  `PREFERRED_ARCHITECTURE = B`.
- GraphRAG-07: `CONTRACT_AND_SAFETY_READY = YES`; `VALUE_EVIDENCE_READY = NO`;
  `STRUCTURED_EVIDENCE_IMPLEMENTATION_READY = NO`; `STALE_SOURCE_CONSUMER_LOOKUP_REQUIRED = YES`;
  RAW vendor schema must remain contained; no fake rank; no fake score.

---

## 1. Core question & target decision

**Core question:** Does structured GraphRAG evidence provide enough *real* retrieval /
provenance / runtime value on a sufficiently larger corpus to justify implementing the
production Structured Evidence Adapter frozen in GraphRAG-07?

The benchmark this document designs must produce, on a **frozen HOLDOUT**:

```
GRAPH_RETRIEVAL_VALUE_EVIDENCED               = YES / NO / INCONCLUSIVE
STRUCTURED_QUERY_DATA_RUNTIME_VALUE_EVIDENCED = YES / NO / INCONCLUSIVE
STRUCTURED_EVIDENCE_VALUE_EVIDENCED           = YES / NO / INCONCLUSIVE   (the top-line)
```

`STRUCTURED_EVIDENCE_VALUE_EVIDENCED` is the phase's real target. Only if it becomes `YES`
may a future implementation phase be *considered* (GraphRAG-07 already froze contract/safety).

### 1.1 Why GraphRAG-04 was not enough (the failure mode to defeat)

GraphRAG-04 (14 Sources, 28 queries) measured GraphRAG hybrid `source_hit_rate = 1.0` and
`mean_source_recall = 1.0`, but did so by returning **265 candidates / 24 answerable queries
≈ 11.0 Sources/query ≈ 79% of the 14-Source corpus** as an unordered set — for `distractor`
and `negative` queries too. Single-relevant precision ≈ `1/11 ≈ 0.09`. This is **broad
coverage, not precision**; the small corpus made near-full-corpus output look artificially
strong. `HYBRID_VALUE_EVIDENCED = INCONCLUSIVE` followed directly.

**GraphRAG-08's single most important design obligation:** a candidate surface that returns
most of the corpus MUST be *penalized* by the evaluation, never rewarded. Every metric,
threshold, and decision rule below is built around that.

---

## 2. Absolute phase boundaries (what this gate does NOT do)

Forbidden in this gate (design only): production Python; the Structured Evidence Adapter;
`client.query_data()`; `/query/data` production integration; any change to `client.query()`,
`HybridRetriever`, RRF, weighted fusion, reranker, cross-encoder; new source-level scoring;
fake ranking; changing LightRAG query mode/version; changing `vector_search()`; Source or
indexing lifecycle changes; migrations; new DB tables; frontend; Ask; Chat; citation
implementation; provider/model tuning; benchmark-score tuning; prompt tuning; real / internal
/ Agribank / customer data. **No benchmark run. No GraphRAG enable. No sidecar start. No
provider traffic.** `OPEN_NOTEBOOK_GRAPHRAG_ENABLED` stays `false`; no `.env` change.

---

## 3. No-adapter requirement & how evidence is obtained without it

The value evaluation **must not** require implementing the GraphRAG-07 production adapter to
decide whether that adapter is worth building. Verified wired seams (findings §D):

| System | Wired today? | Access path | New code needed |
|---|---|---|---|
| **V** `VECTOR_BASELINE` | YES | `open_notebook.domain.notebook.vector_search()` → rows with `parent_id` = canonical `source.id`; dedup via `eval/normalize.normalize_vector_results` (ranked, `ordered=True`) | none |
| **GQ** `CURRENT_LIGHTRAG_QUERY_EVIDENCE` | YES | `GraphRAGService.query_strict()` → `GraphQueryResult.references[].source_id`; already driven by `GraphRAGEvalRunner._graph()`; answer discarded | none |
| **GD** `STRUCTURED_QUERY_DATA_EVIDENCE` | **NO** | LightRAG sidecar `POST /query/data` (`aquery_data`) exists, but **no Open Notebook Python calls it** (grep = 0 hits in `open_notebook/`, `api/`, `commands/`); no FastAPI `/query/data` route | **eval-only** client method (design §7.3) |

**Consequence:** GQ needs zero new code (the existing `GraphRAGEvalRunner` already scores it).
GD needs a *tiny evaluation-only* seam that calls the sidecar's `/query/data` directly and
projects it to canonical Source ids **using the same GraphRAG-07 provenance discipline**, but
living under `open_notebook/integrations/graphrag/eval/` — clearly separated from production,
never importing into Ask/Chat/API. **This gate designs that seam; it does not implement it and
does not implement the GraphRAG-07 adapter.** (`normalize_graph_references(..., benchmark_ids)`
already treats a benchmark allowlist as a first-class concept, so GD reuses it.)

See §7 (systems) for whether GD is worth building for the benchmark at all.

---

## 4. Evidence access as an unordered set (no fake rank)

GraphRAG-05/06 froze `QUERY_DATA_EXPOSES_VALID_RANK = NO` and `..._VALID_SCORE = NO`, and
GraphRAG-05 established that LightRAG drops every internal relevance score before any HTTP
surface and returns a round-robin interleave (not a ranking). Therefore **graph evidence (GQ
and GD) is evaluated as an UNORDERED SET.** For graph systems this benchmark does **not**
compute MRR, nDCG, or Hit@1/3/5-by-array-order. No graph position is ever treated as rank.
Vector remains a genuine ranked system and keeps ranked metrics (§9, §16).

This is a hard invariant, not a limitation to design around (Review C, §22-C).

---

## 5. Larger corpus — size analysis & recommendation

The corpus must be large enough that a broad top-k graph candidate set covers only a *small
fraction* of the corpus, so "return most of the corpus" can no longer buy perfect recall.
GraphRAG-05 §12 already recommended **≥60–100 canonical Sources** for exactly this reason.

### 5.1 Candidate-size analysis

Anchor: GraphRAG-04's graph surface returned ≈11 Sources/query *regardless of corpus size*
(it is a broad, roughly fixed-breadth set, not a fixed fraction). Holding that breadth roughly
constant, the corpus-fraction it implies is:

| Corpus size | Implied graph candidate_fraction if breadth stays ~11 | Distractor room | Indexing cost (LLM entity-extraction ∝ chunks) | Cross-source graph density | Verdict |
|---|---|---|---|---|---|
| 40 | ~0.28 | thin; hard to isolate hubs | low | shallow | Too small — 04 artifact only partly defeated |
| 50 | ~0.22 | moderate | low-moderate | moderate | Borderline |
| **75** | **~0.15** | **good: multiple clusters + hubs + pure distractors** | **moderate (preflight-bounded)** | **rich enough for genuine 2/3-hop bridges** | **RECOMMENDED** |
| 100 | ~0.11 | very good | high | rich | Strong but costlier; use as *stretch*/HOLDOUT-scale only if budget approved |
| 150+ | ~0.07 | excellent | high → CI-hostile | very rich | Cost/cleanup risk outweighs marginal design benefit at this stage |

Note: if graph breadth *scales* with corpus (plausible — more entities, more expansion), the
fraction falls faster still, which only strengthens the case that a mid-size corpus already
exposes the behavior. The design does not depend on which way breadth moves, because the
metrics report the *observed* fraction distribution either way (§11, §26).

### 5.2 Recommendation — **BENCHMARK_CORPUS_SIZE = 75 canonical Sources**

Rationale: 75 puts the GraphRAG-04-style ~11-Source breadth at ≈15% of corpus (vs ~79% at
n=14) — a 5× tightening that makes candidate_fraction discriminating; it leaves room for the
required structural design (≥3 relation clusters, ≥2 shared hubs, cross-cluster bridges,
high-degree distractors, and a pure-distractor block) that a 40–50 corpus cannot hold cleanly;
its indexing cost stays bounded by the two-stage preflight (§33-34); and cleanup of 75 tagged
Sources is tractable and fully enumerated (§46). **Stretch option:** if the operator later
approves a larger budget, the *same frozen fixture design* scales to 100 without methodology
change — 75 is the floor that must be met, not a ceiling.

This gate **freezes the target at 75** and does not create the corpus.

---

## 6. Broad-candidate penalty — the anti-gaming core

The benchmark must make "return almost every Source → perfect recall" a *losing* strategy.
Designed metrics (exact formulas in §11-12, §60, §64-65):

- `candidate_count` — size of the returned benchmark Source set per query.
- `candidate_fraction = returned_benchmark_candidate_count / benchmark_corpus_size` — the
  direct exposure of "11 of 14"-style behavior (§65). **Denominator = benchmark corpus size
  (75), not DB size**, because off-benchmark canonical Sources are not benchmark candidates
  (§28-29). This is the *right* denominator: it measures "what fraction of the answer space did
  the system flag," which is exactly the coverage-vs-precision signal.
- `set_precision` (unordered) — `|GT ∩ R| / |R|` (§12).
- `candidate_inflation = returned_candidate_count / max(1, required_source_count)` (§64) —
  reported but interpreted cautiously: it is meaningful for answerable multi-source queries and
  *misleading for negatives* (GT is empty), so for negatives we use `candidate_count` directly.
- Negative-query `candidate_count` / `candidate_fraction` (§17, §27).

**Interpretation rule frozen (§25, §66):** high recall + low set_precision + high
candidate_fraction = **broad coverage, NOT a retrieval-quality victory.** A graph-only recovery
is credited only *together with* its candidate_fraction / set_precision (§25). No value verdict
may be `YES` when gains come only from very broad candidate sets (§39, §86, Review G).

---

## 7. Primary comparison — V / GQ / GD, and whether GD is needed

### 7.1 Systems

- **V — `VECTOR_BASELINE`** (ranked): production `vector_search()`, `minimum_score=0.2`,
  chunk→Source dedup keeping best rank. Genuine ranked retriever; keeps ranked metrics.
- **GQ — `CURRENT_LIGHTRAG_QUERY_EVIDENCE`** (unordered set): current `client.query()`
  references, exactly as GraphRAG-04 measured. Preserves baseline continuity with 04.
- **GD — `STRUCTURED_QUERY_DATA_EVIDENCE`** (unordered set): `/query/data` references/chunks
  projected to canonical Sources under the GraphRAG-07 provenance rules (STRONG-only admitted).

### 7.2 Are GQ and GD both needed?

| Question | Finding | Design answer |
|---|---|---|
| Does GQ add baseline continuity from 04? | GQ *is* the 04 surface | **Keep GQ** — it anchors comparability and is zero-cost to wire |
| Does GD better measure the future architecture? | The GraphRAG-07 adapter consumes `/query/data`; GD is its retrieval-equivalent evidence surface | **Keep GD** — it measures the *thing we might build* |
| Are GQ and GD retrieval-equivalent? | GraphRAG-06 froze `RETRIEVAL_SEMANTICS_PARITY = YES` (identical `kg_query`; only `include_references` differs) | Expected identical Source sets; **measuring both validates parity empirically and detects any formatting/provenance drift** |
| Would comparing both detect provenance/formatting differences? | GD exposes chunks[]+references[] (STRONG) plus entity/relation (PARTIAL corroboration); GQ exposes references only | **Yes** — GD provenance richness (PARTIAL corroboration adds `EvidenceType`s) is only observable on GD |
| Is provider cost materially different? | GQ triggers the sidecar's final-answer LLM (discarded); GD sets `only_need_context=True` and skips it. Both retain keyword-LLM + embeddings | **Yes, and this is the whole point of `STRUCTURED_QUERY_DATA_RUNTIME_VALUE`** — §35-36 |

**Decision: run all three (V, GQ, GD).** GQ↔GD Source-set equality is a *predicted* result
(parity) that the benchmark should confirm, not assume; and the GQ-vs-GD cost/latency/failure
delta is the direct evidence for `STRUCTURED_QUERY_DATA_RUNTIME_VALUE_EVIDENCED`. If a future
budget constraint forces a cut, GD is retained over GQ for HOLDOUT (it measures the future
architecture) and GQ kept on DEV for continuity — but the default is all three.

### 7.3 GD access seam (eval-only, design not implementation)

Design of the future benchmark-only seam (do **not** implement in this gate):

- Location: `open_notebook/integrations/graphrag/eval/` (e.g. an eval-only client extension),
  NOT `client.py`'s production surface, NOT `service.py`, NOT any API router.
- Behavior: POST sidecar `/query/data`; receive `QueryDataResponse`; project **only STRONG
  anchors** (`chunks[].file_path`, `references[].file_path`) to canonical Source ids via the
  existing `record_id_for` / `_PROVENANCE_TABLES` helpers; count PARTIAL (entity/relation) as
  corroborating `EvidenceType`s but never as Source-creating; DROP_AND_REPORT
  FOREIGN/INVALID/UNKNOWN/DUPLICATE. Restrict to `benchmark_ids` allowlist; valid non-benchmark
  ids → `off_benchmark` (dropped, reported).
- Containment: raw LightRAG dicts stay inside the eval module — never logged, persisted, or
  returned raw; `RAW_CHUNK_TEXT = NEVER` even transiently past projection. This mirrors the
  GraphRAG-07 containment boundary so the benchmark can never leak vendor schema or source text.
- Clearly labeled **evaluation-only / temporary**; not a production adapter, not
  `query_evidence()`. Its existence does not flip `STRUCTURED_EVIDENCE_IMPLEMENTATION_READY`.

---

## 8. Query classes

Every class answers a distinct retrieval question. Vector-solvability vs graph-nativeness is a
per-query *design label* (`GRAPH_NATIVE` / `GRAPH_AUGMENTED` / `VECTOR_SOLVABLE` / `NEGATIVE`),
authored with the ground truth and frozen (§15).

| # | Class | Purpose (distinct question) | Answerable | GT cardinality | Expected vector challenge | Expected graph challenge | Primary metric | Failure mode exposed |
|---|---|---|---|---|---|---|---|---|
| 1 | DIRECT_LEXICAL | Is the plainly-stated fact found? | Y | 1 | low | low | Vector Hit@1; graph set_precision | trivial baseline sanity |
| 2 | SEMANTIC_PARAPHRASE | Reworded, no lexical overlap | Y | 1 | moderate | low | Vector Hit@3/Recall | vector lexical brittleness |
| 3 | TWO_HOP | A—rel—B; needs the relation | Y | 2 (required) | high (one chunk rarely holds both) | moderate | FULL_SOURCE_SET_RECOVERED; graph_only_full | vector single-chunk ceiling |
| 4 | THREE_HOP / CROSS_SOURCE | A→B→C across Sources | Y | 3 (required) | very high | moderate-high | FULL_SOURCE_SET_RECOVERED | multi-hop recall gap |
| 5 | ENTITY_COLLISION | Same entity name, different context | Y | 1 (the right one) | high (name matches wrong Source) | high (graph may expand to collider) | set_precision; candidate_fraction | graph hub/expansion confusion |
| 6 | RELATIONSHIP_COLLISION | Same relation verb, different pair | Y | 1-2 | high | high | set_precision | relation over-expansion |
| 7 | DISTRACTOR_TERM_COLLISION | Shared vocabulary, different GT | Y | 1-2 | high | moderate-high | set_precision; candidate_fraction | vector semantic confusion |
| 8 | NEGATIVE_UNANSWERABLE | No supported answer in corpus | N | 0 (empty) | abstention (none) | abstention (none) | abstained rate; candidate_fraction | false confidence |
| 9 | PARTIAL_EVIDENCE | One required fact present, full relation absent | N (or partial — frozen per query) | 0 or partial | over-return | **graph over-expands plausible relation** | candidate_count; abstained | plausible-hallucination breadth |
| 10 | BROAD_ENTITY_NAME_COLLISION | High-degree hub name in query | Y (small GT) | 1 | moderate | **very high (hub explosion)** | candidate_fraction; hub-candidate breadth | hub-bias candidate inflation |

**Class counts (target, frozen at §5's n=75):** the corpus and query set must keep every class
represented in BOTH splits (§18). Query-count recommendation in §5.3 below.

### 5.3 (query count) — **BENCHMARK_QUERY_COUNT = 60** (referenced from §14/§18)

GraphRAG-04 used 28 queries over 6 classes. GraphRAG-08 adds 4 classes and a larger corpus,
and must support per-class *and* per-split analysis. **60 queries** gives ~6 per class with a
DEV/HOLDOUT split that still leaves ≥2 per class per split for the 10 classes. Distribution
frozen in §14 table and §73. This is a floor; the fixture may carry a few more for balance but
not fewer per class per split.

---

## 9. Vector remains ranked

For `VECTOR_BASELINE` retain ranked metrics after chunk→Source dedup (first/best rank kept):
`Hit@K`, `Recall@K`, `MRR`, optionally `Precision@K`, for K ∈ {1,3,5,10} (K=10 justified now
that corpus ≥75; see §61-62 for multi-source MRR handling). Vector is deliberately **not**
allowlist-filtered — off-benchmark canonical Sources in the real DB are legitimate ranked
competitors (§28-29), which makes the vector metric *harder*, not gamed.

---

## 10. Metrics — the full contract

Exact formulas in §11 (breadth), §12 (graph set), §60 (canonical), §61 (vector). Summary of
which metric applies to which system and whether it is valid for negatives:

See the **Metric table (§75)** for the full matrix. Key rules:
- Ranked metrics: **vector only**. Never on GQ/GD.
- Graph set metrics: GQ/GD only; unordered.
- Every ranked/set recall metric is **undefined on an empty GT** and must raise rather than
  score 0 (inherited from GraphRAG-04 `metrics.py` `_require_relevant`). Negatives use the
  dedicated negative metrics (§17, §27) instead.

---

## 11. Candidate-breadth metrics (unordered systems)

Per answerable query and per class, report the **distribution** (not just a mean), because a
single average hides the broad-set pathology:

- `candidate_count` per query.
- `candidate_fraction = candidate_count / 75`.
- Distribution reporting per class and per system: **median, p75, p90, p95, max, mean** of
  `candidate_count` and of `candidate_fraction` (§26).
- `candidate_inflation = candidate_count / max(1, |required_source_ids|)` (answerable only).

## 12. Required unordered graph set metrics (exact)

For a graph system returning benchmark Source set `R`, with required GT set `GT`:

```
set_precision = |GT ∩ R| / |R|                      (defined only when |R| > 0)
set_recall    = |GT ∩ R| / |GT|                     (defined only when |GT| > 0; negatives excluded)
set_f1        = 2·P·R / (P + R)                       (defined only when P+R > 0 and |GT|>0)
candidate_count            = |R|
candidate_fraction         = |R| / 75
false_positive_count       = |R \ GT|
provenance_valid_rate      = valid_unique / total_evidence_items
provenance_foreign_rate    = foreign / total_evidence_items
provenance_malformed_rate  = malformed / total_evidence_items
```

For negatives (`GT = ∅`): `set_recall` / `set_precision` / `set_f1` are **not defined** (never
reported as 0). Report instead: `abstained = (|R| == 0)`, `candidate_count`,
`candidate_fraction`, `false_positive_count = |R|` (§17, §27).

`NEGATIVE_QUERY_CANDIDATE_COUNT` / `NEGATIVE_QUERY_CANDIDATE_FRACTION` are the negative-set
aggregates of the above.

---

## 13. Multi-source ground truth

GraphRAG value is most plausibly a *multi-source* effect (two/three-hop, cross-source). GT must
distinguish **REQUIRED** from **OPTIONAL supporting** Sources (§21):

- `required_source_ids` — the Source set genuinely needed to answer. Recall is computed against
  this set only.
- `optional_support_source_ids` — corroborating Sources that may legitimately appear but whose
  absence is not a miss; **never** counted against precision as a false positive, and never
  required for recall. Used sparingly and only with a written rationale.

GT cardinalities supported: single-source, two-source, three-source, cross-source, multi-hop.

`FULL_SOURCE_SET_RECOVERED = (required_source_ids ⊆ R)`; `PARTIAL_SOURCE_SET_RECOVERED =
(required_source_ids ∩ R ≠ ∅) ∧ ¬FULL` (§62). Multi-source success is measured as FULL
recovery, kept separate from "any hit."

---

## 14. Query-class distribution (frozen target)

| Class | Answerable | GT cardinality | Design label | Target count (of 60) | DEV | HOLDOUT |
|---|---|---|---|---|---|---|
| 1 DIRECT_LEXICAL | Y | 1 | VECTOR_SOLVABLE | 6 | 3 | 3 |
| 2 SEMANTIC_PARAPHRASE | Y | 1 | VECTOR_SOLVABLE | 6 | 3 | 3 |
| 3 TWO_HOP | Y | 2 | GRAPH_NATIVE | 6 | 3 | 3 |
| 4 THREE_HOP/CROSS_SOURCE | Y | 3 | GRAPH_NATIVE | 6 | 3 | 3 |
| 5 ENTITY_COLLISION | Y | 1 | GRAPH_AUGMENTED | 6 | 3 | 3 |
| 6 RELATIONSHIP_COLLISION | Y | 1-2 | GRAPH_AUGMENTED | 6 | 3 | 3 |
| 7 DISTRACTOR_TERM_COLLISION | Y | 1-2 | VECTOR_SOLVABLE (hard) | 6 | 3 | 3 |
| 8 NEGATIVE_UNANSWERABLE | N | 0 | NEGATIVE | 6 | 3 | 3 |
| 9 PARTIAL_EVIDENCE | N/partial | 0/partial | NEGATIVE | 6 | 3 | 3 |
| 10 BROAD_ENTITY_NAME_COLLISION | Y | 1 | GRAPH_AUGMENTED | 6 | 3 | 3 |
| **Total** | | | | **60** | **30** | **30** |
| of which negative/unanswerable | | | | **12** (classes 8+9) | 6 | 6 |
| of which graph-native (3+4) | | | | **12** | 6 | 6 |
| of which cross-source | | | | ≥6 (subset of 4 + some 6/10) | | |

---

## 15. Graph-specific query design (genuine multi-hop)

A query is labeled `GRAPH_NATIVE` only if answering it truly requires
`Source A evidence + relationship + Source B evidence` (or `A→B→C`), where **no single Source /
single chunk contains the full answer.** Author's checklist to earn the label (Review C, §22-C):

1. The required fact is *split across Sources by construction* — Source A states A—rel, Source B
   states rel—B; neither restates the other.
2. A vector query over any single chunk cannot return the full `required_source_ids` (verified
   at authoring by reasoning about chunking, not by running a retriever).
3. Entities appearing is **not** sufficient to call a query "graph" — the *relationship* must be
   load-bearing.

`GRAPH_AUGMENTED` = graph plausibly helps but a strong vector retriever might also solve it
(collision classes). `VECTOR_SOLVABLE` = single-Source, vector should win. `NEGATIVE` = §17.

---

## 16. Vector metric formulas (exact)

After chunk→Source dedup (best rank kept, per `normalize_vector_results`), for ranked Source
list `ranked` and required set `GT`:

```
hit_at_k(k)      = any(GT ∩ ranked[:k])                       # bool
recall_at_k(k)   = |GT ∩ set(ranked[:k])| / |GT|
precision_at_k(k)= |GT ∩ set(ranked[:k])| / k                 # reported, interpret w/ |GT|
mrr              = 1 / rank_of_first_relevant  (rank from 1), else 0.0
```

Multi-source MRR ambiguity (§61): for `|GT| > 1` MRR is defined as the reciprocal rank of the
**first** relevant Source (documented choice), and additionally `FULL_SOURCE_SET_RECOVERED@K`
is reported (Source-set fully inside top-K). Where first-relevant MRR would mislead for a
3-source query, the per-class report leads with `FULL_SOURCE_SET_RECOVERED@K`, not MRR.

Negatives excluded from all vector hit/recall denominators (raise on empty GT).

---

## 17. Negative query design (stronger than 04)

GraphRAG-04 showed neither system abstains; 08 must make abstention observable and negatives
unambiguous. Negative classes (GT = ∅ unless a query is deliberately *partial*, frozen per
query):

| Negative class | Construction | GT |
|---|---|---|
| NO_MATCH | Topic absent from corpus entirely | ∅ |
| ENTITY_EXISTS_BUT_RELATION_DOES_NOT | Entity A present; the queried relation of A does not exist | ∅ |
| RELATION_EXISTS_BUT_TARGET_DOES_NOT | Relation type present elsewhere; the queried target absent | ∅ |
| PLAUSIBLE_COMBINATION_NOT_IN_CORPUS | A and B both present, A-rel-B never stated | ∅ |
| PARTIAL_FACT_ONLY | One required fact present, full requested relation absent | ∅ or partial (frozen) |
| CONTRADICTORY_RELATION | Corpus states A-rel-B; query asserts A-rel-C (false) | ∅ |

For every negative: measure `abstained` rate, `candidate_count` distribution, `candidate_fraction`
distribution, `false_positive_count`. **Non-empty output is NOT success** (§27). A negative must
be *clearly* unsupported — no hidden ambiguous true answer (Review D, §22-D, §83).

---

## 18. DEV / HOLDOUT split

- **30 DEV / 30 HOLDOUT** (50/50), disjoint queries, union = all 60 (§14).
- Every class represented in **both** splits (≥2 per class per split; table §14 gives 3/3).
- All negative classes present in HOLDOUT.
- Ground truth authored **before** any live run; benchmark version frozen at authoring.
- **No tuning on HOLDOUT** of any kind (retrieval, provider, thresholds). **No post-hoc query
  deletion** because a system performs badly (§42).
- HOLDOUT is authoritative for value conclusions (§41). DEV is only for execution-correctness
  validation. Sharp DEV↔HOLDOUT divergence ⇒ `VALUE = INCONCLUSIVE` unless justified.

---

## 19. Benchmark versioning

`BENCHMARK_VERSION = graphrag_08_eval_v1` (mirrors 04's scheme: version string = fixture dir
name, cross-checked in every fixture file, `namespace_tag = __graphrag08_eval_v1__`). Versioned
sub-artifacts: corpus version, query version, ground-truth version, split version — all `v1`.
**No runtime result may alter v1.** Any methodological change creates `v2` (§42). Baseline
identifiers: `VECTOR_BASELINE`, `GRAPHRAG_CURRENT_QUERY_EVIDENCE` (GQ),
`GRAPHRAG_STRUCTURED_QUERY_DATA_EVIDENCE` (GD).

---

## 20. Ground-truth authoring rules

GT is **manual and Source-level**, authored from the frozen corpus **before** any retriever
runs. Per query record: `query_id`, `query_class`, `split`, `answerable` (bool),
`required_source_ids`, `optional_support_source_ids` (only with rationale), `design_label`
(GRAPH_NATIVE/…), `rationale` (review-only, never sent to a retriever). GT must **not** be
derived from an LLM judge, a retriever, GraphRAG output, or vector output (§80 Review A). Load-
time validation mirrors 04 (unique ids; class enum; split enum; each required id exists &
unique; empty required set ⟺ answerable == false unless explicitly partial).

---

## 21. Required-vs-optional rule (conservative)

A Source is **REQUIRED** only if answering genuinely needs its evidence. Prefer the smallest
defensible required set; do not inflate multi-source truth to make GraphRAG look better. Doubt
resolves toward OPTIONAL (which cannot help recall) or exclusion. This directly guards Review G
(§86): value cannot be manufactured by padding required sets.

---

## 22. Adversarial reviews (design self-audit)

These are performed against the **design** now (fixtures do not exist yet). Each maps to a §80–86
required review; verdicts here are design-level PASS with the guardrail that produced them.

- **A — Benchmark leakage (§80):** GT authored before any run, manual, Source-level, no
  LLM/retriever/graph/vector input; DEV/HOLDOUT disjoint with class coverage in both; no post-hoc
  deletion (§42); benchmark frozen at authoring (§19). Query text carries no ids/labels/hints
  (corpus prose likewise). **Design PASS.**
- **B — Broad-candidate gaming (§81):** `candidate_fraction` (denominator = 75), `set_precision`,
  `false_positive_count`, negative `candidate_count`, and the §25 precision-preserving rule mean a
  system returning most of the corpus scores *low* precision and *high* fraction and cannot reach
  `VALUE = YES` (§39, §86). **Design PASS.**
- **C — Graph-native validity (§82):** §15 checklist requires the full required set to be split
  across Sources such that no single chunk answers; "entities appear" is explicitly insufficient.
  **Design PASS**, to be re-verified at fixture authoring (empirical risk R1, §93).
- **D — Negative quality (§83):** six negative constructions, each clearly unsupported; partial
  cases frozen per query; non-empty output never counted as success. **Design PASS**, re-verify at
  authoring (R2).
- **E — Isolation (§84):** per-run id namespace + tag allowlist; graph candidates allowlist-
  restricted; cleanup targets only created ids; fail-closed on residue; foreign Sources never
  indexed/queried/deleted/mutated (§28-30, §46). **Design PASS.**
- **F — Cost explosion (§85):** two-stage preflight, bounded corpus (75) and queries (60),
  explicit operator approval gate before full run, stop conditions (§33-34, §77). **Design PASS.**
- **G — Value logic (§86):** the decision framework (§67) cannot emit `YES` from high recall +
  low precision + high fraction; §25/§39 forbid it. **Design PASS.**

---

## 23. Complementarity metrics

Per answerable query, at K ∈ {1,3,5,10} (vector truncated to top-K; graph uses its full
unordered set — the deliberate budget asymmetry from 04, documented as an upper bound):

Success definitions:
- Vector success@K = `FULL_SOURCE_SET_RECOVERED` within top-K (required ⊆ top-K). "Any-hit@K"
  reported separately, never conflated.
- Graph success = `FULL_SOURCE_SET_RECOVERED` in the graph set (unordered; no K on the graph
  side).

Categories (kept separate for FULL vs PARTIAL — §63):
```
BOTH_FULL_SUCCESS, VECTOR_ONLY_FULL, GRAPH_ONLY_FULL, BOTH_FAIL_FULL
(optional) partial-recovery complementarity, reported separately, never mixed with FULL.
```

`GRAPH_ONLY_FULL` is the headline candidate-value signal — but it is credited only with its
`candidate_fraction` / `set_precision` alongside (§25).

---

## 24. Oracle union (interpreted correctly)

`ORACLE_UNION@K` answers only: *would evidence from BOTH systems together contain the required
Source set?* — `oracle_union_full@K = required ⊆ (vector[:k] ∪ graph_set)`. It is an offline
upper bound. It does **not** imply a practical fusion exists, and **no RRF conclusion may be
drawn from it** (§69). GraphRAG-04's oracle-union = 1.0 was exactly the artifact this benchmark
must reinterpret through fraction/precision.

---

## 25. Precision-preserving value rule

Graph-only recovery MUST be interpreted **together with** candidate_fraction / set_precision.
No arbitrary numeric threshold is hardcoded here (§68). Instead the decision (§67) requires,
for a `GRAPH_RETRIEVAL_VALUE_EVIDENCED = YES`:

1. Graph recovers required Sources that vector misses (`GRAPH_ONLY_FULL > 0` on HOLDOUT), **and**
2. it does so **without** a pathological breadth profile — i.e. graph `candidate_fraction`
   distribution (median/p90) is materially below the GraphRAG-04 ~0.79 regime and graph
   `set_precision` is non-trivial, **and**
3. the effect concentrates in graph-relevant classes (§40), not uniformly across all classes
   (which would signal broad coverage, not graph value).

A defensible numeric threshold may be *proposed after* seeing the HOLDOUT distribution, but only
if methodologically justified — never pre-committed to force a verdict.

---

## 26. Candidate breadth curves

Because graph evidence is unordered, report distributions, not single averages, per class and
per system (GQ, GD): median, p75, p90, p95, max, mean of `candidate_count` and
`candidate_fraction`. These curves are the primary lens on the §1.1 pathology.

## 27. Negative abstention analysis

Per negative class and overall: `% returning zero Sources` (abstained rate), median
`candidate_count`, mean/median/max `candidate_fraction`, `false_positive_count` prevalence. No
threshold is assumed — observational. Non-empty output is never success.

---

## 28. Provenance quality metrics

Continue strict canonical provenance accounting (reuse 04's `ProvenanceStats` + GraphRAG-07's
5-state model). Per evidence item classify: `VALID` (STRONG, admitted), `PARTIAL` (entity/
relation corroboration — counted, never Source-creating), `FOREIGN` (valid id, wrong table),
`MALFORMED/INVALID`, `UNKNOWN` (`unknown_source`/absent), `DUPLICATE`, `OFF_BENCHMARK` (valid
canonical Source outside the benchmark allowlist). Rates: `provenance_valid_rate`,
`provenance_foreign_rate`, `provenance_malformed_rate`, plus counts for the rest.

`OFF_BENCHMARK` uses the benchmark-owned Source allowlist (`benchmark_ids`). GD adds the
PARTIAL(entity/relation) accounting that GQ cannot expose — a measurable provenance-richness
delta.

## 29. Off-benchmark policy

Benchmark Sources = explicit allowlist (the created, tagged ids). Graph evidence mapping to a
valid canonical Source **outside** the allowlist = `OFF_BENCHMARK`: **not** counted as candidate
success, **not** deleted, **not** modified, reported separately. Vector retrieval MAY legitimately
return off-benchmark canonical Sources as ranked competitors (they make the vector metric harder,
not gamed). **DB choice (isolation) — see §30:** the design **recommends option A (isolated
namespace/database)** so the benchmark corpus is the whole world (candidate_fraction is exact and
off-benchmark ≈ 0 by construction), while the real production path is still exercised.

---

## 30. Isolation architecture

| Option | Realism | Safety | Complexity | Canonical-path fidelity | Cleanup risk | Verdict |
|---|---|---|---|---|---|---|
| A. Dedicated temporary Surreal namespace/database | High (real code path, clean world) | **Highest** (no foreign Sources present) | Moderate (spin-up/teardown) | Full (normal Source creation + processing) | Low (drop namespace) | **RECOMMENDED** |
| B. Existing DB + tag + strict cleanup (04's approach) | High | High (04 proved it) | Low | Full | Moderate (per-id cleanup must be exhaustive) | Fallback |
| C. Cloned test DB | High | High | High | Full | Moderate | Not preferred (heaviest) |

**Recommendation: Option A** (isolated namespace/database), with **Option B's per-id tagging,
allowlist, isolation-assertion and cleanup retained as a second safety layer inside it.** Option
A makes `candidate_fraction` exact (world = 75 benchmark Sources), removes off-benchmark noise
from the graph side entirely, and eliminates any risk to real user Sources — while still running
the genuine Open Notebook Source-creation → processing → vector index → GraphRAG lifecycle. If the
operator prefers to exercise the *real* canonical search seam against real competitors, Option B
is the documented fallback and vector off-benchmark competitors are reported per §28-29.

---

## 31. Canonical Source requirement (no shortcuts)

The benchmark MUST use the real Open Notebook canonical Source creation + normal processing:
```
synthetic benchmark Source
  → Open Notebook canonical Source (api/routers/sources.py::create_source path / Source(...).save())
  → normal source processing (process_source command → source_graph)
  → normal vector indexing (embed_source command / vectorize())
  → approved GraphRAG lifecycle (_maybe_enqueue_graphrag_index → graphrag index)
  → LightRAG sidecar
```
No benchmark-only indexing shortcut; no direct vector insert; no direct LightRAG insert. (The
existing `GraphRAGEvalRunner` already follows this shape via `embed_source_command` +
`service.index_source`.)

---

## 32. Provider / model posture (reproducibility, no probing)

Reuse the exact GraphRAG-04 synthetic-safe configuration for comparability (do **not** probe or
tune, do **not** call providers now):
- Embedding: `openai/text-embedding-3-small`, **1536 dimensions** (observed in 04).
- Graph/keyword LLM: `openai/gpt-4o-mini`.
- Provider: OpenRouter (04's executed path), LightRAG v1.5.6 sidecar.
Fixed provider/model configuration for reproducibility; no provider tuning; no model swap; no
LightRAG version change.

---

## 33. Provider-cost design (qualitative, no dollar figures)

Larger corpus ⇒ materially more external calls. Qualitative call inventory (per full run):

| Call type | Driver | Rough scale vs 04 (14→75 Sources, 28→60 queries) |
|---|---|---|
| Embedding (indexing) | one per chunk across 75 Sources | ~5× corpus → dominated by chunk count; **bound Source length** (§52) to cap |
| LightRAG entity-extraction LLM (indexing) | per chunk during graph index | ~5× corpus; the largest indexing cost |
| Query keyword-extraction LLM | per query, GQ & GD (unless cache hit / short-query fallback) | ~2× queries × (systems that call graph) |
| Query embedding | per query VDB lookups | ~2× queries |
| GQ final-answer LLM (discarded) | per GQ query only | 60 queries × (DEV+HOLDOUT runs) — **the GQ-vs-GD cost delta** |
| GD final-answer LLM | **none** (`only_need_context=True`) | 0 — the runtime-value signal |
| Vector embedding (query) | per query | ~2× queries |

No dollar estimate is asserted (no pricing evidence in-repo). The design instead bounds *volume*
(§34 preflight, §52 Source size, §77 budget guard) and makes the GQ↔GD provider-call delta an
explicit measured output (§35-36, §43).

---

## 34. Two-stage live execution plan

- **STAGE 1 — MICRO PRECHECK** (small subset, e.g. ~6–8 Sources + ~6 queries covering ≥1 of each
  metric-critical class + ≥1 negative): verify provider works; embedding dimension = 1536;
  canonical Source creation + processing; vector indexing; LightRAG indexing → PROCESSED; GQ and
  GD query semantics; provenance classification; **full cleanup proven** (zero tagged residue,
  foreign untouched). Emit a preflight artifact.
- **STAGE 2 — FULL FROZEN CORPUS** (75 Sources / 60 queries): only after preflight PASS and
  **explicit operator approval**.

No live run in this gate.

---

## 35. Current-query vs query-data value

Because GraphRAG-06 froze `RETRIEVAL_SEMANTICS_PARITY = YES`, GQ and GD are expected to return
identical Source sets. The benchmark measures both to (a) confirm parity empirically and (b)
quantify the *architectural* delta:
- provider-call difference (GQ triggers final-answer LLM; GD does not),
- latency difference (`latency_ms` per query per system),
- failure behavior (GraphRAG-06 §6: `/query` loses all evidence if final-answer LLM fails;
  `/query/data` preserves retrieval),
- provenance richness (GD exposes PARTIAL entity/relation corroboration; GQ references-only).

## 36. Two types of value (kept separate)

- **A. RETRIEVAL_VALUE** — does GraphRAG evidence find useful Sources beyond vector? →
  `GRAPH_RETRIEVAL_VALUE_EVIDENCED`. Measured identically for GQ and GD (parity).
- **B. STRUCTURED_ADAPTER / RUNTIME_VALUE** — does `/query/data` (GD) give architectural benefit
  over `client.query()` (GQ): no final-answer generation, lower provider cost/egress, lower
  latency, cleaner failure boundary, richer provenance structure? →
  `STRUCTURED_QUERY_DATA_RUNTIME_VALUE_EVIDENCED`.

## 37. Implementation-justification rule

The GraphRAG-07 adapter may be worth building even if **retrieval value is modest**, *if*
structured evidence substantially improves cost / failure isolation / provenance / security /
operational clarity (GraphRAG-06 already prefers Architecture B on these grounds). But the bar is
not lowered arbitrarily: §38 flags are decided on **separate** dimensions and combined by §67.

## 38. Required final value flags (what the benchmark must support producing)

```
GRAPH_RETRIEVAL_VALUE_EVIDENCED               = YES / NO / INCONCLUSIVE
STRUCTURED_QUERY_DATA_RUNTIME_VALUE_EVIDENCED = YES / NO / INCONCLUSIVE
STRUCTURED_EVIDENCE_VALUE_EVIDENCED           = YES / NO / INCONCLUSIVE
STRUCTURED_EVIDENCE_IMPLEMENTATION_READY      = YES / NO
```
`STRUCTURED_EVIDENCE_IMPLEMENTATION_READY = YES` only if: contract/safety = YES (already, 07)
**AND** value evidence sufficient (this benchmark) **AND** no execution blocker remains.

## 39. No automatic-YES rule

No single signal forces a verdict. Specifically forbidden: "any graph-only hit ⇒ VALUE = YES";
"lower provider calls ⇒ IMPLEMENT = YES". The decision weighs retrieval usefulness, candidate
breadth, negative behavior, provenance quality, runtime value, and implementation risk together
(§67).

## 40. Class-level analysis (required)

Report every metric **by query class**, and especially contrast DIRECT_LEXICAL / SEMANTIC /
TWO_HOP / THREE_HOP / COLLISION / NEGATIVE. GraphRAG value should appear in **graph-relevant
classes** (3, 4, and possibly 5/6/10), not uniformly across all classes via broad retrieval.
Uniform "value" across all classes is a red flag for the §1.1 artifact, not evidence.

## 41. HOLDOUT authoritative / 42. No post-hoc tuning

DEV validates execution correctness only — no retrieval/provider/threshold tuning on it that
feeds back into the fixture. Value conclusions rely on **HOLDOUT**. Once `graphrag_08_eval_v1`
is frozen: queries, Sources, ground truth, class labels, and split are immutable; only execution
bugs may be fixed; any methodological change → `v2`. Disappointing results are never grounds to
edit the benchmark.

---

## 43. Performance / runtime metrics

Per query and aggregate: per-query `latency_ms`, median latency, p95 latency; provider call
count if observable (embedding/keyword-LLM/final-answer-LLM counts); final-answer LLM calls
(expected GQ>0, GD=0); query failures; timeouts; response payload size if measurable safely.
**No content captured.**

## 44. Content-free artifact / 45. Query-text-in-artifact policy

Artifact carries only: ids, class labels, split, metrics, counts, latencies, statuses, model
ids, non-secret provider names, config flags. **Forbidden:** unnecessary query text, Source text,
chunk text, entity/relation descriptions, provider raw responses, keys, headers. **Query text
lives in the fixture (synthetic, non-sensitive); the result artifact references `query_id` only**
to minimize duplication and exposure surface. (Mirrors 04's `per_query` shape.)

---

## 46. Cleanup contract

Future live run must guarantee cleanup: record benchmark-owned Source ids **before** creation
commit (covers ambiguous commits); delete **only** owned Sources; run the approved GraphRAG
lifecycle cleanup (sidecar `delete_document_for_source`) + `DELETE source_embedding WHERE
source=$id` + `DELETE $id`; remove any temporary Model record; restore prior default embedding
model exactly; **no global purge**; verify **zero** tagged residue; verify pre-existing Sources
unchanged. In Option A, also drop the temporary namespace/database as a final backstop.
**Incomplete cleanup ⇒ benchmark sign-off fails.**

## 47. Interrupted-run recovery

A durable, **content-free** run manifest enables recovery: `run_id`, created Source ids,
temporary model ids, `state` (§78). No content stored. Location preference: an `.artifacts/`
run-local file (e.g. `.artifacts/graphrag-08/<run_id>/manifest.json`) — **no new migration, no
new DB table** (consistent with GraphRAG-07 `EVIDENCE_PERSISTENCE = TRANSIENT_ONLY`). On restart,
cleanup reads the manifest and removes exactly the recorded ids.

## 48. Temp model policy

If a temporary OpenRouter embedding Model is needed again: capture prior default state; create
via the normal supported path; record the id in the manifest; restore the prior default exactly
afterward; delete the temporary Model. No hidden permanent config change. (04 did this: temp
model deleted, default reset to prior `None`.)

## 49. Graph enable-flag policy / 50. Sidecar policy

`OPEN_NOTEBOOK_GRAPHRAG_ENABLED` is `false` now and stays `false` in this gate. Future execution
temporarily enables it **only** for the benchmark, then restores `false`. Sidecar: start
explicitly for the run, verify `/health` (core/api `0328`) and configured model names, run,
clean up, stop. No lingering sidecar afterward. No `.env` change now.

---

## 51. Benchmark fixture structure (future layout — not created now)

Follows 04's conventions with an explicit GT file (04 embedded GT in queries; 08 keeps GT
embedded per-query for load-time cross-validation, and adds a small top-level manifest schema
doc). Illustrative only:
```
tests/fixtures/graphrag_08_eval_v1/
    README.md
    corpus.json         # {benchmark_version, namespace_tag, description, sources:[{key,title,text}]}
    queries.json        # {benchmark_version, query_classes, ground_truth_policy, split_policy,
                        #  queries:[{query_id, query_class, split, text, answerable,
                        #            required_source_keys[], optional_support_source_keys[],
                        #            design_label, rationale}]}
```
Runtime `manifest.json` (created ids, temp model ids, state) is generated per run under
`.artifacts/`, **not** committed. **No fixture files are created in this gate.**

## 52. Source content design

Synthetic Sources must be: small enough for reasonable cost (bounded length/chunk count — this
directly caps embedding + entity-extraction volume, §33); rich enough for relationship structure;
deliberately overlapping; **fully synthetic**; free of Agribank/internal/customer facts; stable;
manually auditable. Prefer invented organizations/entities (as 04 did: Halcyon/Voss/Aurora/
Calderon). No public real-person data unless unavoidable (it is not).

## 53. Entity-graph design

Design corpus relationships that create: isolated clusters; shared hubs; cross-cluster bridges;
high-degree distractor entities; low-degree relevant entities; same-name entities in different
contexts. Concretely for n=75: ≥3 relation clusters, ≥2 shared hubs (one *relevant*, one
*distractor* high-degree), ≥1 cross-cluster bridge chain feeding THREE_HOP/CROSS_SOURCE, and a
pure-distractor block (analog of 04's S11–S13, scaled up).

## 54. Hub-bias test

Include BROAD_ENTITY_NAME_COLLISION queries (class 10) targeting a **high-degree** entity whose
correct answer is a *small* Source set, to detect "high-degree entity ⇒ large candidate
explosion." Measure candidate breadth for hub-related distractors specifically (§26 curves).

## 55. Source-length-bias test

Vary Source size / chunk count across the corpus (GraphRAG-05 flagged length/chunk-count bias).
Design checks so a larger Source does not appear "better" merely by contributing more chunks. No
scoring is implemented; evidence breadth is *observed* (`supporting_chunk_count` reported as a
diagnostic, never a rank — GraphRAG-07 invariant).

## 56. Duplicate-evidence test

Include Sources with repeated mentions of the same fact; verify the same Source reappears many
times in raw evidence yet Source-level dedup yields exactly one candidate. Keep evidence-count
diagnostics (`supporting_chunk_count`) separate from relevance.

## 57. Cross-source relation test

Design explicit relationships spanning Sources (THREE_HOP/CROSS_SOURCE) and evaluate whether
GraphRAG recovers **all** required Sources **without** returning most of the corpus — one of the
strongest value tests (`GRAPH_ONLY_FULL` credited with `candidate_fraction`, §23-25).

## 58. Partial-evidence test

Design PARTIAL_EVIDENCE queries where one required fact exists but the complete requested relation
does not, testing whether GraphRAG over-expands plausible relationships. GT frozen carefully per
query (∅ or partial). Non-empty output on a ∅ GT is a breadth/false-confidence observation, not
success.

## 59. Wrong-relation negative test

Pattern: Entity A exists; Entity B exists; relation R exists elsewhere; A-R-B does **not** exist
(PLAUSIBLE_COMBINATION_NOT_IN_CORPUS / CONTRADICTORY_RELATION). GraphRAG must not be rewarded for
returning the three pieces separately. Designed as a negative/unanswerable case (GT = ∅).

---

## 67. Graph-value decision framework (matrix)

No single metric dominates. `STRUCTURED_EVIDENCE_VALUE_EVIDENCED` combines the two value axes:

| Evidence dimension | Supports YES | Supports NO | Supports INCONCLUSIVE |
|---|---|---|---|
| Multi-hop full-set recovery (classes 3,4) | Graph recovers required multi-source sets vector misses on HOLDOUT | No graph_only_full beyond vector | Small/unstable counts, DEV↔HOLDOUT divergence |
| Cross-source recovery | GRAPH_ONLY_FULL > 0 in cross-source class w/ bounded fraction | none | ambiguous |
| Candidate set_precision | non-trivial, materially > 04's ~0.09 regime | near-04 low precision | mixed |
| Candidate_fraction (median/p90) | materially below 04 ~0.79 regime | near-corpus breadth | borderline |
| Negative behavior | meaningful abstention / low negative fraction | returns broad sets on negatives | mixed |
| Provenance quality | high valid_rate, low foreign/malformed; GD PARTIAL corroboration adds value | degraded provenance | mixed |
| Runtime/provider savings (GD vs GQ) | GD removes final-answer LLM, lower latency, cleaner failure | no measurable delta | unclear/noisy |
| Failure isolation (GD) | GD preserves retrieval where GQ loses it | no difference | untested |
| DEV/HOLDOUT consistency | consistent | — | sharp divergence ⇒ INCONCLUSIVE |

**Combination rule:** `STRUCTURED_EVIDENCE_VALUE_EVIDENCED = YES` requires
`GRAPH_RETRIEVAL_VALUE_EVIDENCED = YES` (under §25's precision-preserving gate) **OR**
(`GRAPH_RETRIEVAL_VALUE_EVIDENCED ≥ INCONCLUSIVE` **AND**
`STRUCTURED_QUERY_DATA_RUNTIME_VALUE_EVIDENCED = YES` with §37's substantial architectural
improvement). It is `NO` when retrieval value is absent/artifact-only **and** runtime value is
immaterial. It is `INCONCLUSIVE` on sharp DEV↔HOLDOUT divergence or unstable small-count effects.

## 68. No arbitrary threshold rule

No `precision > X` / `candidate_fraction < Y` is pre-committed. The report exposes
distributions; a decision threshold may be *proposed after* HOLDOUT results only if
methodologically defensible.

## 69. RRF remains out of scope / 70. Graph candidate impl remains blocked

Even if value is proven, RRF stays blocked: value evidence ≠ rank semantics.
`RRF_CANDIDATE_INTERFACE_READY = NO` and `GRAPH_CANDIDATE_IMPLEMENTATION_READY = NO` are
unchanged by this gate. GraphRAG-08 can support *structured evidence* implementation, never a
*ranked GraphCandidate* implementation.

## 71. Implementation-readiness relation

`STRUCTURED_EVIDENCE_IMPLEMENTATION_READY` may become `YES` only if `CONTRACT_AND_SAFETY_READY =
YES` (already) **AND** `STRUCTURED_EVIDENCE_VALUE_EVIDENCED = YES` **AND** no new blocker
appears. This gate produces none of those YES's — it produces the *means* to reach them.

## 72. No implementation in this gate

No eval Python, fixtures, runner, tests, provider configs, temporary models, or Source records
created. No live execution.

---

## 73. Benchmark spec table (04 → 08)

| Dimension | GraphRAG-04 | GraphRAG-08 proposed | Reason for change |
|---|---|---|---|
| Source count | 14 | **75** | Drive candidate_fraction from ~0.79 → ~0.15; expose broad-set pathology (§5) |
| Query count | 28 | **60** | 10 classes × per-split coverage (§5.3) |
| Query classes | 6 | **10** | Add ENTITY/RELATIONSHIP/DISTRACTOR/BROAD collisions + PARTIAL_EVIDENCE (§8) |
| Negative count | 4 | **12** (classes 8+9) | Stronger abstention analysis (§17) |
| Multi-hop count | two_hop 5 + three_hop 4 = 9 | ≥12 (TWO_HOP 6 + THREE_HOP 6) | More graph-native signal (§15) |
| Cross-source count | subset of three_hop | ≥6 explicit | Direct cross-source value test (§57) |
| Distractor density | S11–S13 + collisions | ≥3 clusters, ≥2 hubs, bridge chains, distractor block (§53) | Expose expansion/confusion at scale |
| DEV/HOLDOUT | 17/11 (~61/39) | **30/30 (50/50)** | More HOLDOUT power per class (§18) |
| Graph metrics | set_hit, set_recall | + set_precision, set_f1, candidate_fraction, false_positive_count (§12) | Penalize breadth (§6) |
| Vector metrics | Hit/Recall/MRR @{1,3,5} | + Precision@K, K=10, FULL_SET_RECOVERED (§16) | Larger corpus + multi-source truth |
| Candidate breadth | mean only (implicit) | median/p75/p90/p95/max/mean distributions (§26) | Single average hides pathology |
| Provenance metrics | valid/foreign/malformed/dup/off_benchmark | + PARTIAL corroboration (GD), 5-state (§28) | GraphRAG-07 model + GD richness |
| Provider/runtime metrics | timing metadata | latency p50/p95, provider-call counts, GQ-vs-GD final-answer delta (§43) | Measure runtime value (§36) |
| Systems | V + GQ | **V + GQ + GD** | Measure the future architecture (§7) |

## 74. Query-class table

Provided in §8 (purpose/answerable/GT cardinality/vector challenge/graph challenge/primary
metric/failure mode).

## 75. Metric table

| Metric | System | Formula | Interpretation | Valid for negatives? | Rank required? | Risk |
|---|---|---|---|---|---|---|
| hit_at_k | V | any(GT ∩ ranked[:k]) | ranked coverage | No (raise) | Yes (vector has rank) | — |
| recall_at_k | V | \|GT∩ranked[:k]\|/\|GT\| | ranked recall | No | Yes | — |
| precision_at_k | V | \|GT∩ranked[:k]\|/k | interpret w/ \|GT\| | No | Yes | small-GT noise |
| mrr | V | 1/rank_first_relevant | ranked quality | No | Yes | multi-source ambiguity (§16) |
| FULL_SOURCE_SET_RECOVERED@K | V | required ⊆ top-K | multi-source success | No | Yes | — |
| set_recall | GQ/GD | \|GT∩R\|/\|GT\| | set coverage | No (raise) | **No** | breadth inflates it |
| set_precision | GQ/GD | \|GT∩R\|/\|R\| | discrimination | No | No | **the anti-breadth guard** |
| set_f1 | GQ/GD | 2PR/(P+R) | balance | No | No | — |
| candidate_count | GQ/GD | \|R\| | breadth | **Yes** | No | — |
| candidate_fraction | GQ/GD | \|R\|/75 | corpus coverage | **Yes** | No | denominator must = benchmark size |
| candidate_inflation | GQ/GD | \|R\|/max(1,\|GT\|) | inflation | No (misleading) | No | undefined-ish on negatives → use count |
| false_positive_count | GQ/GD | \|R\\GT\| | wrong candidates | Yes (=\|R\|) | No | — |
| abstained | GQ/GD | \|R\|==0 | negative behavior | **Yes** | No | — |
| provenance_valid/foreign/malformed_rate | GQ/GD | counts/total | provenance health | Yes | No | — |
| latency_ms / p95 | V/GQ/GD | measured | runtime | Yes | No | provider nondeterminism |
| oracle_union_full@K | V+GQ/GD | required ⊆ (vec[:k] ∪ graphset) | upper bound only | No | partial | must NOT imply fusion (§24) |

## 76. Decision matrix

Provided in §67 (Evidence dimension × Supports YES/NO/INCONCLUSIVE), with the combination rule.

## 77. Cost / execution plan

Preflight size ~6–8 Sources + ~6 queries; full corpus 75 Sources / 60 queries. Phases per §78.
Safe stop points at every state transition. **Retry policy: NO automatic retry** of provider
calls at the benchmark layer (retries multiply cost and muddy nondeterminism analysis); a failed
query is recorded as a `failure`/`timeout` state and excluded from denominators (as 04 does).
Provider budget guard: a hard cap on total indexing+query calls, computed from 75 Sources ×
bounded chunk count + 60 queries × systems; exceeding it aborts to CLEANUP. Cleanup after each
phase. No dollar estimate.

## 78. Run state machine

```
PLANNED → PREFLIGHT → PREFLIGHT_PASS → FULL_INDEX → FULL_QUERY → ANALYZE → CLEANUP → COMPLETE
Failure transitions:
  PREFLIGHT_FAIL  (from PREFLIGHT)   → CLEANUP → COMPLETE ; results INVALID for value
  INDEX_FAIL      (from FULL_INDEX)  → CLEANUP ; results INVALID
  QUERY_PARTIAL   (from FULL_QUERY)  → ANALYZE allowed but flagged; value only if HOLDOUT complete
  CLEANUP_FAIL    (from CLEANUP)     → sign-off FAILS; manual remediation required
```
Valid-result rule: a value verdict may be drawn only from a run that reached `ANALYZE` with a
**complete HOLDOUT** and a subsequent successful `CLEANUP`. PREFLIGHT/INDEX failures yield no
value verdict. `QUERY_PARTIAL` yields a verdict only if HOLDOUT is fully covered.

## 79. Artifact contract (content-free)

```
run metadata: run_id, benchmark_version, git_commit, lightrag_version, provider/model ids,
              config flags, synthetic_only=true, answer_scored=false, state, UTC ts
per system:   V / GQ / GD identifiers
per query:    query_id, query_class, split, answerable, n_required,
              vector_state, graph_state, vector_candidate_ids, graph_candidate_ids,
              graph_provenance{valid,partial,foreign,malformed,unknown,duplicate,off_benchmark},
              latency_ms{v,gq,gd}, final_answer_llm_calls{gq,gd}
aggregates:   per-class + per-split metrics (§75), breadth distributions (§26),
              complementarity (§23), oracle (§24), negatives (§27)
```
No raw source text, no chunk text, no entity/relation descriptions, no provider raw content, no
keys/headers. Written under `.artifacts/graphrag-08/<run_id>/evaluation.json`.

---

## 80–86. Required adversarial reviews

Design-level results in §22 (A benchmark leakage, B broad-candidate gaming, C graph-native
validity, D negative quality, E isolation, F cost explosion, G value logic) — all **Design
PASS**, with C and D carrying empirical re-verification obligations at fixture-authoring time
(risks R1/R2, §93). No fixtures are created in this gate.

## 87. Security / governance (this gate)

`PROVIDER_TRAFFIC = NO`; `DATABASE_MUTATION = NO`; `SOURCE_MUTATION = NO`;
`LIGHTRAG_STORAGE_MUTATION = NO`; `SIDECAR_STARTED = NO`; `OPEN_NOTEBOOK_GRAPHRAG_ENABLED`
remains `false`; no `.env` changes; no credentials; no internal/private data. All corpus/queries
designed to be fully synthetic (§52).

---

## 88. Deliverables (this gate)

- This document: `docs/agribank/development/GRAPHRAG_08_LARGER_CORPUS_VALUE_EVALUATION_DESIGN.md`.
- Planning: `.planning/2026-08-31-graphrag-08-larger-corpus-value-eval/{task_plan,findings,progress}.md`.
- `docs/agribank/development/CURRENT_PHASE.md` (updated to GraphRAG-08 design gate).
- `.planning/.active_plan` (points to this plan).
- **No benchmark fixtures. No eval code.**

## 89. Final design flags

```
BENCHMARK_CORPUS_SIZE_FROZEN        = YES   (75 canonical Sources)
BENCHMARK_QUERY_COUNT_FROZEN        = YES   (60 queries)
QUERY_CLASSES_FROZEN                = YES   (10 classes, §8/§14)
NEGATIVE_DESIGN_FROZEN              = YES   (§17, 6 constructions, 12 queries)
GROUND_TRUTH_POLICY_FROZEN          = YES   (§20-21, manual, Source-level, pre-run)
DEV_HOLDOUT_SPLIT_FROZEN            = YES   (30/30, class-balanced, §18)
VECTOR_METRICS_FROZEN               = YES   (§16)
GRAPH_SET_METRICS_FROZEN            = YES   (§12)
CANDIDATE_BREADTH_METRICS_FROZEN    = YES   (§11, §26)
PROVENANCE_METRICS_FROZEN           = YES   (§28, 5-state + off_benchmark)
COMPLEMENTARITY_METRICS_FROZEN      = YES   (§23-24, FULL vs PARTIAL separated)
RUNTIME_METRICS_FROZEN              = YES   (§43)
ISOLATION_POLICY_FROZEN             = YES   (§30, Option A + Option B safety layer)
CLEANUP_POLICY_FROZEN               = YES   (§46-48)
PROVIDER_EXECUTION_PLAN_FROZEN      = YES   (§32-34, §77-78)
ARTIFACT_CONTRACT_FROZEN            = YES   (§44-45, §79)
VALUE_DECISION_FRAMEWORK_FROZEN     = YES   (§25, §67, §38-39)
GRAPH_RAG_08_EXECUTION_READY        = YES   (design complete; operator approval still required)

Retained (unchanged by this gate):
CONTRACT_AND_SAFETY_READY                     = YES
VALUE_EVIDENCE_READY                          = NO
STRUCTURED_EVIDENCE_IMPLEMENTATION_READY      = NO
QUERY_DATA_EXPOSES_VALID_RANK                 = NO
QUERY_DATA_EXPOSES_VALID_SCORE                = NO
RRF_CANDIDATE_INTERFACE_READY                 = NO
GRAPH_CANDIDATE_IMPLEMENTATION_READY          = NO
```

## 90. Execution-readiness rule

`GRAPH_RAG_08_EXECUTION_READY = YES` is asserted because: corpus/query scope frozen; ground-truth
policy frozen; metrics frozen; isolation frozen; cleanup frozen; provider plan frozen; artifact
contract frozen; decision framework frozen; and no HIGH methodological/security blocker remains
(the only open items are empirical, to be resolved *at fixture-authoring time*, §93 R1/R2 — they
do not block the design). **This does NOT authorize execution. Operator approval is still required
before any live benchmark.**

## 91. Stop conditions (checked — none fired)

Corpus 75 is large enough to expose broad-candidate behavior (fraction ~0.79→~0.15); query
classes test real graph relationships (§15 checklist); candidate breadth is penalized (§6, §12);
negatives are unambiguous (§17); ground truth does not depend on retriever output (§20); off-
benchmark handling is safe (§28-29); cleanup cannot touch foreign Sources (§30, §46); provider
usage is bounded (§34, §77); the benchmark does **not** require the production adapter (§3, GQ
zero-code + GD eval-only seam); it does **not** change LightRAG semantics; no rank metrics on
unordered results (§4). **No STOP condition fired.**
