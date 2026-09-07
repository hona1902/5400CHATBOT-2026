# GraphRAG-08E.7C — Live Burst-Reproduction Result Checkpoint

**Status:** DOCUMENTATION RESULT CHECKPOINT. Records the completed live screening result of
GraphRAG-08E.7 Live Authorization #1 and updates the full-benchmark readiness decision. **No
provider traffic, no live indexing, no live rerun, no DEV/HOLDOUT, no V/GQ/GD, no mitigation, no
production adapter, no GraphRAG-09.** Docs only; fixture unchanged.

Checkpoint: branch `feature/graphrag-lifecycle`, harness HEAD
`2858a11261bbc892c1477c8abf5ea006bc35070f`, tag `graphrag-08e7b-burst-harness-approved`, fixture
`a58a68535c345e18f0263904f818e4e2068a164056408665d8bb9233eceb143d` (75/60/30/30/12, unchanged).

## 1. Live run identity

```
GRAPH_RAG_08E7_LIVE_BURST_REPRODUCTION = COMPLETE
LIVE_AUTHORIZATION      = #1
DIAGNOSTIC_RUN_ID       = gr08e7a1
HARNESS_HEAD            = 2858a11261bbc892c1477c8abf5ea006bc35070f
HARNESS_TAG             = graphrag-08e7b-burst-harness-approved
RUNG_LEVELS_PLANNED     = [8,16,24,32,48,75]
RUNGS_ENTERED           = [8,16,24,32,48,75]
RUNGS_COMPLETED         = 6/6
STOP_REASON             = NONE
```

## 2. Reproduction result

```
HISTORICAL_SIGNATURE_REPRODUCED         = NO
REPRODUCTION_LEVEL                      = NONE
S001_HISTORICAL_EVENT_REPRODUCED_STRICT = NO
STRICT_S001_REPRODUCTION_LEVEL          = NONE
NOVEL_VALID_FAILURE_OBSERVED            = NO
NOVEL_FAILURE_LEVEL                     = NONE
NO_FAILURE_THROUGH_BURST_LEVEL          = 75
ROOT_CAUSE_CONFIRMED                    = NO
```

A `NO` reproduction is **not** the historical failure disproven — it means the attempt-#5 failure
did not reproduce under the current frozen environment across this bounded screening sweep.

## 3. Per-rung results

| Rung | submitted/planned | success | failure | retry | attempts | latency ms (min / median / max) | full_wave | valid | drain | cleanup | labels |
|---|---|---|---|---|---|---|---|---|---|---|---|
| C=8  | 8/8   | 8  | 0 | 0 | 8  | 12077 / 21976 / 30937   | PASS | YES | YES | PASS | CLEAN |
| C=16 | 16/16 | 16 | 0 | 0 | 16 | 12327 / 41617 / 66640   | PASS | YES | YES | PASS | CLEAN |
| C=24 | 24/24 | 24 | 0 | 0 | 24 | 13625 / 57694 / 92890   | PASS | YES | YES | PASS | CLEAN |
| C=32 | 32/32 | 32 | 0 | 0 | 32 | 15297 / 69062 / 123250  | PASS | YES | YES | PASS | CLEAN |
| C=48 | 48/48 | 48 | 0 | 0 | 48 | 17047 / 93365 / 174532  | PASS | YES | YES | PASS | CLEAN |
| C=75 | 75/75 | 75 | 0 | 0 | 75 | 12937 / 142687 / 277061 | PASS | YES | YES | PASS | CLEAN |

Every rung: full initial wave established, treatment valid, submit-all-then-poll, fully drained,
cleaned up. No legitimate Source failure at any rung.

## 4. Latency interpretation (performance evidence only)

```
BURST_LATENCY_PRESSURE = STRONG_AND_SCALE_CORRELATED
FAILURE_RATE           = 0 AT EVERY RUNG
```

Median per-Source latency rises 21976 → 41617 → 57694 → 69062 → 93365 → **142687 ms**; maximum
rises 30937 → 66640 → 92890 → 123250 → 174532 → **277061 ms** (~4.6 min/Source at the full
75-way burst). The single sidecar slows strongly and monotonically as the concurrent wave grows,
yet every Source PROCESSED. This is **load/performance evidence only** — it does **not** imply the
latency increase causes the historical failure.

## 5. Attempt-#5 comparison

The attempt-#5 historical condition — 75 frozen Sources, S001 first, submit-all burst, one
LightRAG sidecar/workspace, pinned LightRAG/provider/model stack — produced the historical S001
TRACK failure. 08E.7 Authorization #1 reproduced the same known burst regime: same 75 frozen
Sources, same S001→S075 ordering, same full-initial-wave semantics, one sidecar per treatment,
the C=75 full-corpus burst, same pinned LightRAG v1.5.6 / OpenRouter / `openai/gpt-4o-mini` /
`openai/text-embedding-3-small`. It obtained **75/75 PROCESSED, 0 failure, 0 retry**.

```
HISTORICAL_FAILURE_DETERMINISTIC_ON_75_WAY_BURST = NOT_SUPPORTED
```

This does **not** mean the historical failure was impossible or invalid.

## 6. Scientific interpretation

The attempt-#5 failure did **not** reproduce as a deterministic consequence of the known
75-source burst regime in the current frozen runtime/provider environment. The clean result is
**compatible with, but does NOT confirm**: a temporal provider condition, a provider
capacity/rate transient, a network/runtime transient, another historical environmental condition,
or a non-deterministic LightRAG/provider interaction. No single explanation is promoted to root
cause. 08E.7 is reproduction screening, not axis isolation.

## 7. Hypotheses

```
H1 = INCONCLUSIVE   H2 = INCONCLUSIVE   H3 = INCONCLUSIVE   ROOT_CAUSE_CONFIRMED = NO
```

Zero failures ⇒ no current failure-family, retry-classifier, or safe failure-reason evidence. H1
is **not** promoted to SUPPORTED merely because a temporal/provider explanation now looks more
plausible.

## 8. Budget

```
PLANNED_SOURCE_WORKLOAD_USED = 203   PLANNED_SOURCE_WORKLOAD_MAX = 203
ACTUAL_INDEX_ATTEMPTS_USED   = 203   ACTUAL_INDEX_ATTEMPTS_MAX   = 406
MAX_INDEX_ATTEMPTS_PER_SOURCE = 2    RETRIES = 0
WORKLOAD_GUARD_RESPECTED = YES   ATTEMPT_GUARD_RESPECTED = YES
```

203 Source-level indexing attempts is **not** an exact provider API-call count.

## 9. Safety

```
OPTION_A_SURREAL_ISOLATION = PASS   NORMAL_DB_UNCHANGED = PASS (normal-ns source count 1, pre-existing)
DOCKER_RUNTIME_ATTESTOR_USED = YES
ALL_ENTERED_RUNGS_FRESH = YES   ALL_ENTERED_WORKSPACES_UNIQUE = YES
CROSS_RUNG_LLM_CACHE_REUSE_POSSIBLE = NO   CROSS_RUNG_GRAPH_STATE_REUSE_POSSIBLE = NO
RUNG_CLEANUP = PASS (6/6)   TEMP_SURREAL_DISPOSED = YES   TEMP_MODEL_RESIDUE = 0
LIGHTRAG_RESIDUE = 0   SIDECAR_RUNNING_AFTER = NO   OPEN_NOTEBOOK_GRAPHRAG_ENABLED_AFTER = false
FIXTURE_INTEGRITY_BEFORE = a58a68535c345e18f0263904f818e4e2068a164056408665d8bb9233eceb143d
FIXTURE_INTEGRITY_AFTER  = a58a68535c345e18f0263904f818e4e2068a164056408665d8bb9233eceb143d
```

## 10. Provider accounting (content-safe)

```
PROVIDER = OpenRouter   LLM_MODEL = openai/gpt-4o-mini   EMBEDDING_MODEL = openai/text-embedding-3-small
PROVIDER_TRAFFIC = BOUNDED_BURST_DIAGNOSTIC_ONLY
SOURCE_LEVEL_INDEX_ATTEMPTS = 203   CANONICAL_SOURCE_EMBEDDINGS = 75
PROVIDER_INTERNAL_CALL_COUNT = NOT_OBSERVABLE / BOUNDED_NOT_EXACT
RAW_PROVIDER_RESPONSE_PERSISTED = NO   RAW_ERROR_PERSISTED = NO   SECRET_VALUE_REPORTED = NO
```

## 11. 08F decision

```
08F_MITIGATION_GATE_JUSTIFIED = NO
```

No reproduced failure, no confirmed mechanism, no evidence-supported mitigation → 08F is not
opened.

## 12. Full-benchmark attempt-#6 readiness (decision updated)

```
Before 08E.7:  FULL_ATTEMPT_6_JUSTIFIED_NOW = NOT_YET
After 08E.7:   FULL_ATTEMPT_6_JUSTIFIED_NOW = YES
FULL_ATTEMPT_6_AUTHORIZED   = NO
FULL_RUN_ATTEMPT_6_EXECUTED = NO
```

**Why attempt #6 is now justified:** the previously-blocking indexing stage — the full 75-source
burst that failed at attempt #5 — has now been **rehearsed successfully** at full scale under the
committed current stack (75/75 processed, 0 retries, 0 failures, clean isolation/cleanup, no
fixture drift). That is sufficient execution-readiness evidence to permit a **separate**
full-benchmark authorization. Attempt #6 remains scientifically useful as the first opportunity to
obtain the frozen GraphRAG-08 DEV/HOLDOUT retrieval-value evidence. The unknown historical failure
root cause is **not** treated as solved; a future full run must still fail closed if indexing
fails again. **This checkpoint does NOT authorize attempt #6.**

## 13. Value flags (retained until the full benchmark actually executes)

```
VALUE_EVIDENCE_READY = NO
GRAPH_RETRIEVAL_VALUE_EVIDENCED = NOT_RUN   STRUCTURED_QUERY_DATA_RUNTIME_VALUE_EVIDENCED = NOT_RUN
STRUCTURED_EVIDENCE_VALUE_EVIDENCED = NOT_RUN   STRUCTURED_EVIDENCE_IMPLEMENTATION_READY = NO
QUERY_DATA_EXPOSES_VALID_RANK = NO   QUERY_DATA_EXPOSES_VALID_SCORE = NO
RRF_CANDIDATE_INTERFACE_READY = NO   GRAPH_CANDIDATE_IMPLEMENTATION_READY = NO
```

## 14. Next step

```
C. NO_REPRODUCTION_THROUGH_75_REQUIRES_REVIEW
```

The result is frozen for operator review. A full-benchmark attempt #6 (the first retrieval-value
run) is now execution-ready but requires a separate explicit operator authorization; no
confirmation rerun, larger burst, or mitigation is performed here.
