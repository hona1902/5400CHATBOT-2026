# GraphRAG-08 — Full Benchmark Attempt #6 Value-Evidence Result Checkpoint

**Status:** DOCUMENTATION RESULT CHECKPOINT. Freezes the first fully valid larger-corpus
GraphRAG-08 DEV/HOLDOUT value benchmark. **No production adapter, no RRF, no GraphRAG-09, no 08F
reopen, no provider traffic, no live indexing, no benchmark rerun.** Docs only; fixture unchanged.

Checkpoint: branch `feature/graphrag-lifecycle`, harness HEAD
`bc5964f01a11a469f877baea39a19d74255c3a41`, tag `graphrag-08e7c-live-burst-result-approved`,
fixture `a58a68535c345e18f0263904f818e4e2068a164056408665d8bb9233eceb143d` (75/60/30/30/12,
unchanged).

## 1. Run identity

```
GRAPH_RAG_08_FULL_BENCHMARK_ATTEMPT_6 = COMPLETE
RUN_ID                = gr08-3bdb09b4dd1e   AUTHORIZATION_LABEL = FULL_RUN_ATTEMPT_6
HARNESS_HEAD          = bc5964f01a11a469f877baea39a19d74255c3a41
HARNESS_TAG           = graphrag-08e7c-live-burst-result-approved
GRAPH_INDEXED_SOURCES = 75/75   DEV_EXECUTED = 30/30   HOLDOUT_EXECUTED = 30/30
INDEXING_FAILURES = 0   INDEXING_RETRIES = 0
```

Canonical committed entrypoint `precheck08.run_full_benchmark(authorization_label=
"FULL_RUN_ATTEMPT_6")` — the 08E.7 screening harness was **not** substituted.

## 2. Indexing result

```
INDEXING_COMPLETE = YES   INDEXING_SUCCESSES = 75   INDEXING_FAILURES = 0   INDEXING_RETRIES = 0
SOURCES_INDEXED_FIRST_ATTEMPT = 75   MAX_ATTEMPTS_OBSERVED = 1   FIRST_FAILURE_SOURCE = NONE
RAW_ERROR_PERSISTED = NO
```

The full 75-Source indexing burst — the attempt-#1…#5 blocker — completed clean on first
attempt. This does **not** solve the historical root cause: `ROOT_CAUSE_CONFIRMED = NO`,
`H1/H2/H3 = INCONCLUSIVE` (a successful run does not rewrite the historical failure hypotheses).

## 3. Vector baseline (V) — ranked

| K | Hit@K | Recall@K | FullSet@K |
|---|---|---|---|
| **HOLDOUT** (authoritative) — Hit@1 0.000 · Hit@3 0.417 · Hit@5 0.667 · Hit@10 0.958 · Recall@5 0.521 · Recall@10 0.875 · MRR 0.267 · FullSet@5 0.417 · FullSet@10 0.750 |
| **DEV** — Hit@1 0.083 · Hit@3 0.292 · Hit@5 0.500 · Hit@10 0.917 · Recall@5 0.410 · Recall@10 0.847 · MRR 0.292 · FullSet@5 0.333 · FullSet@10 0.750 |
| **OVERALL** — Hit@1 0.042 · Hit@3 0.354 · Hit@5 0.583 · Hit@10 0.938 · Recall@5 0.465 · Recall@10 0.861 · MRR 0.279 · FullSet@5 0.375 · FullSet@10 0.750 |

V is a genuine ranked retriever: precision improves with K (Hit@5 0.667, Hit@10 0.958 on HOLDOUT),
weak at top-1.

## 4. GQ (client.query) and GD (/query/data) — unordered Source-set metrics

```
GQ_GD_SOURCE_SET_PARITY = IDENTICAL   (gq_eq_gd_count = 60, gq_only = 0, gd_only = 0)
```

| | HOLDOUT | DEV | OVERALL |
|---|---|---|---|
| set_recall_mean | 0.972 | 0.965 | 0.969 |
| **set_precision_mean** | **0.065** | **0.065** | **0.065** |
| set_f1_mean | 0.120 | 0.119 | 0.119 |
| candidate_fraction | 0.267 | 0.267 | 0.267 |
| candidate_count | 20 / 75 | 20 / 75 | 20 / 75 |
| false_positive_count_mean | 18.42 | 18.50 | 18.46 |
| full_source_set_recovered_rate | 0.917 | 0.917 | 0.917 |

GQ and GD return the **same** essentially fixed 20-of-75 candidate surface for every query.

## 5. Negative queries

```
NEGATIVE_QUERY_COUNT = 12
GQ_NEGATIVE_ABSTENTION_RATE = 0.0   GD_NEGATIVE_ABSTENTION_RATE = 0.0
```

Both GQ and GD returned the broad 20-candidate surface for **unanswerable** queries — no
abstention. This is important negative evidence in the value decision.

## 6. Provenance

```
FOREIGN_PROVENANCE = 0   MALFORMED_PROVENANCE = 0   OFF_BENCHMARK_PROVENANCE = 0
```

Provenance quality is clean, but clean provenance does **not** compensate for low retrieval
precision.

## 7. Latency (p50 / p95 ms)

```
GD = 968 / 1109    GQ = 6344 / 8843    V = 1109 / 1484
```

GD is materially faster than GQ because it avoids the discarded final-answer generation step. GD
does **not** have better retrieval quality than GQ — their Source evidence sets are identical.

## 8. Value decisions

```
VALUE_EVIDENCE_READY = YES
```
A fully valid benchmark now exists for architectural decision-making. This flag does **not** mean
GraphRAG retrieval value is positive.

```
GRAPH_RETRIEVAL_VALUE_EVIDENCED = NO
```
GQ/GD obtain very high recall (0.97) and full-set recovery (0.917) predominantly through a broad,
essentially fixed 20-of-75 candidate surface: precision ≈ 0.065, F1 ≈ 0.12, ~18.5 false
positives/query, candidate_fraction ≈ 0.267, and **zero negative abstention**. High graph recall
is therefore **coverage, not discriminative Source-level retrieval**. The graph candidate surface
is not a successful retriever.

```
STRUCTURED_QUERY_DATA_RUNTIME_VALUE_EVIDENCED = YES
```
GD produces the **same** Source evidence set as GQ while avoiding unnecessary final-answer
generation — substantially lower latency (~6.5×), lower generation work, cleaner failure
isolation, and a structured machine-consumable evidence path. It does **not** mean better
precision/recall/candidate selection than GQ, nor that a production adapter is ready.

### 8a. Complementarity interpretation

```
COMPLEMENTARITY_PRESENT = YES
COMPLEMENTARITY_DEMONSTRATES_USEFUL_GRAPH_RETRIEVAL = NO
```
The benchmark reported strong graph recovery of Sources missed at vector top positions
(e.g. graph_only_full@1 = 42/48; oracle_union_full_rate 0.917 — upper bound only, **NOT** an
RRF/fusion validation). But returning a large fixed fraction of the corpus mechanically raises the
chance of covering required Sources; coverage alone is not useful retrieval value.

## 9. HOLDOUT-authoritative decision

HOLDOUT (authoritative) confirms: V = real ranked retrieval, strong Hit@10/Recall@10, weak top-1;
GQ/GD = very high recall, very low precision, broad candidate surface, zero negative abstention.
The negative graph-retrieval decision is therefore supported by **HOLDOUT**, not merely DEV.

## 10. Architectural implications (frozen; narrow)

```
QUERY_DATA_EXPOSES_VALID_RANK = NO   QUERY_DATA_EXPOSES_VALID_SCORE = NO
RRF_CANDIDATE_INTERFACE_READY = NO   GRAPH_CANDIDATE_IMPLEMENTATION_READY = NO
STRUCTURED_EVIDENCE_IMPLEMENTATION_READY = NO
08F_MITIGATION_GATE_JUSTIFIED = NO
```

- Current LightRAG **v1.5.6** should **not** be treated as a ranked retriever, a vector
  replacement, an RRF-ready candidate generator, or a precise answerability detector.
- The evidence supports retaining `/query/data` **only** as a potentially useful structured
  evidence / provenance / context-expansion seam — subject to a **separate** architecture review.
  Production adoption is **not** approved.
- **RRF is not justified:** it requires meaningful ranked candidate lists; current GQ/GD evidence
  is unordered and broad. Do **not** "fix" the result by feeding all 20 graph Sources into RRF —
  without a meaningful graph rank/score that would not solve the candidate-quality problem.
- **08F is not reopened:** no indexing failure occurred in Attempt #6; no failure mechanism is
  confirmed.

### 10a. Future design questions (NOT decisions, NOT implemented)

Could Open Notebook consume GD structured evidence as a **secondary** evidence-expansion source
**after** a stronger primary retriever has narrowed the candidate space? Related future
architecture questions: vector-first candidate gating; query-conditioned graph evidence expansion;
strict Source authorization; negative-query suppression; Source-level dedup/filtering; Open
Notebook-owned reranking. None is implemented or blessed here.

## 11. Full benchmark status

```
FULL_ATTEMPT_6_AUTHORIZED = YES_COMPLETED   FULL_RUN_ATTEMPT_6_EXECUTED = YES
FULL_RUN_ATTEMPT_6_STATUS = COMPLETE
```
No attempt #7 is authorized.

## 12. Safety

```
OPTION_A_SURREAL_ISOLATION = PASS   NORMAL_DB_UNCHANGED = PASS (source count 1→1)
LIGHTRAG_VERSION = v1.5.6   PROVIDER = OpenRouter   LLM_MODEL = openai/gpt-4o-mini
EMBEDDING_MODEL = openai/text-embedding-3-small   EMBEDDING_DIMENSION = 1536
PROVIDER_TRAFFIC = FULL_FROZEN_BENCHMARK_ONLY
RAW_PROVIDER_RESPONSE_PERSISTED = NO   RAW_ERROR_PERSISTED = NO   SECRET_VALUE_REPORTED = NO
TEMP_SURREAL_DISPOSED = YES   TEMP_MODEL_RESIDUE = 0   LIGHTRAG_RESIDUE = 0
SIDECAR_RUNNING_AFTER = NO   OPEN_NOTEBOOK_GRAPHRAG_ENABLED_AFTER = false
FIXTURE_CHANGED = NO   (before == after == a58a68535c345e18f0263904f818e4e2068a164056408665d8bb9233eceb143d)
```

## 13. Next review gate

```
Next: GraphRAG-08 Value Evidence Architecture Review
```
A fully valid value benchmark now exists. The negative graph-retrieval value finding and the
positive `/query/data` runtime finding are frozen for a separate architecture/decision review; no
production adapter, RRF, or GraphRAG-09 is authorized here.
