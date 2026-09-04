# GraphRAG-08E.6 — Full-Run Scale/Accumulation Reproduction Forensic & Diagnostic Design Gate

**Status:** OFFLINE FORENSIC + DESIGN ONLY. **No provider traffic, no live diagnostic, no
attempt #6, no DEV/HOLDOUT, no V/GQ/GD, no mitigation, no production adapter, no GraphRAG-09.**
No code changed (docs only). Checkpoint verified: HEAD `7aa019d924d2931addc45cb607f522fb50a7c3b3`,
tag `graphrag-08e5-provider-binding-approved`, tree CLEAN, fixture
`a58a68535c345e18f0263904f818e4e2068a164056408665d8bb9233eceb143d`.

This gate does **not** run anything. It asks: *what material execution dimensions existed in
full-run attempt #5 but were NOT exercised by the clean bounded diagnostic #5-R1?* — and, if a
further diagnostic is warranted, designs the **smallest** experiment that could distinguish the
unresolved axis.

---

## 1. Frozen #5-R1 result (the valid bounded live diagnostic)

```
GRAPH_RAG_08E_LIVE_CONCURRENCY_DIAGNOSTIC = COMPLETE
DIAGNOSTIC_REAUTHORIZATION                = #5-R1
CELLS_COMPLETED                           = 8/8
ACTUAL_TOTAL_SUBMISSIONS                  = 30   (cap 64)
FAILURES                                  = 0
RETRIES                                   = 0
FAILURE_RATE_AT_LEVEL_1/2/4/8             = 0 / 0 / 0 / 0
ROOT_CAUSE_CONFIRMED                      = NO
H1 = INCONCLUSIVE   H2 = INCONCLUSIVE   H3 = INCONCLUSIVE
FULL_RUN_ATTEMPT_6_EXECUTED               = NO
```

Every one of the 30 submissions reached LightRAG `PROCESSED` on the first attempt. With zero
failures there is **no** failure-family evidence, **no** retry-classifier evidence, and **no**
failure-reason evidence — so H1/H2/H3 stay **INCONCLUSIVE** (§10). No mitigation is justified by
#5-R1 alone.

### 1a. Corrected latency interpretation (performance evidence only — NOT root-cause)

Measured per-level latency (ms):

| Level | median | max |
|---|---|---|
| L1 | 15305 | 15313 |
| L2 | 12866 | 15422 |
| L4 | 15717.5 | 25765 |
| L8 | 21616 | 36921 |

```
LATENCY_VS_CONCURRENCY = HIGHER TAIL / PRESSURE AT L4–L8; NOT STRICTLY MONOTONIC ACROSS ALL LEVELS
```

The **median dips at L2** (12866 < L1's 15305) because L2 first admits the faster non-anchor
Sources, so the median is not strictly monotonic. The honest signal is a **rising tail** (max
15313 → 15422 → 25765 → 36921) and higher central latency at L4–L8 — i.e. the sidecar slows
under more concurrent extraction but stays **correct**. This is performance evidence, not
evidence of the #5 failure mechanism.

### 1b. S001 finding

S001 previously failed in full-run attempt #5. In #5-R1, **S001 PROCESSED successfully in every
treatment/repetition it was selected into** (levels 1/2/4/8, both reps). The prior S001 failure
was **not reproduced**.

```
DETERMINISTIC_SOURCE_SPECIFIC_FAILURE = NOT_SUPPORTED
```

This does **not** prove S001 can never fail, and does **not** disprove the full-run failure
cause — it only rules out a deterministic, content-intrinsic S001 defect (consistent with the
earlier isolated S001 PASS).

---

## 2. Forensic question

**What execution dimensions differed materially between (A) full-run attempt #5 and (B) the
successful bounded diagnostic #5-R1?** Reconstructed below from committed code and content-safe
run records only — not inferred.

## 3. Reconstruction — full-run attempt #5 (`13e59a3edbb8`, auth label REAUTHORIZATION_5)

Driver: `precheck08.run_full_benchmark()` → one `GraphRAG08EvalRunner.create_and_index()`.

| # | Dimension | Attempt-#5 fact | Grade |
|---|---|---|---|
| 1 | Sources | **75** (`FROZEN_SOURCE_COUNT=75` dataset08.py:38; `sk=…all 75` precheck08.py:493) | PROVEN |
| 2 | Submission ordering | corpus order; **S001 submitted first** (canonical `source:gr08ef5d8978700`); runner08.py:362/369/490 | PROVEN (order) / SUPPORTED (S001=idx0) |
| 3 | All-at-once vs progressive | **SUBMIT-ALL-UP-FRONT** — all 75 submitted before any polling (runner08.py:490–494, poll starts :497) | PROVEN |
| 4 | Effective client-side concurrency | **No ON-side limit** — no semaphore/gather throttle (`EvalRunConfig08` has no concurrency field) | PROVEN |
| 5 | Sidecar/process count | **ONE long-lived sidecar** — `start_sidecar()` once (precheck08.py:568), `stop_sidecar()` once (:683); one `base_url` reused for all 75 | PROVEN |
| 6 | Workspace count | **ONE shared default workspace** (compose sets no `WORKSPACE`; v1.5.6 default) | SUPPORTED (proven-by-absence) |
| 7 | Storage lifetime | **shared/long-lived** single rag-storage volume across all 75 (SHARED_BUT_OWNED) | PROVEN (Surreal) / SUPPORTED (LightRAG store) |
| 8 | LLM-cache lifetime | **single shared** `kv_store_llm_response_cache` for the whole run (one workspace) | SUPPORTED |
| 9 | Graph/index lifetime | **one accumulating graph** across all 75 in the single sidecar | SUPPORTED |
| 10 | Provider/model/version | OpenRouter · `openai/text-embedding-3-small` (dim **1536**) · `openai/gpt-4o-mini` · LightRAG **v1.5.6** | PROVEN (embed/version) / SUPPORTED (LLM binding, from untracked `.env`) |
| 11 | Retry policy | **max 2/source**, frozen transient allowlist, fail-closed (`max_index_attempts_per_source=2` runner08.py:81) | PROVEN |
| 12 | Polling | bounded poll of all tracks, **5 s** interval, **1800 s** deadline (precheck08.py:624–626) | PROVEN |
| 13 | Time between submissions | **no deliberate spacing** — back-to-back awaited submits, no sleep between submissions | PROVEN |
| 14 | 75 accumulate in ONE sidecar? | **YES** (single sidecar + single workspace + accumulating store) | PROVEN (single sidecar) / SUPPORTED (accumulation) |
| 15 | Extraction overlap across docs | **YES** — sidecar extracts many docs in parallel at LightRAG default internal concurrency (many concurrent `gpt-4o-mini` calls to OpenRouter as the 75-doc burst drains) | SUPPORTED |
| 16 | Failure after accumulated success? | **S001 = first submitted; at gate-abort `graphrag_indexed_count=0`** — NO source recorded PROCESSED before abort, i.e. the failure occurred under the concurrent 75-doc in-flight burst, NOT after cumulative *completed* work | PROVEN |
| 17 | S001 failure | **TRACK-surface `DocStatus.FAILED`**, classified **NON_RETRYABLE**, reason `TRACK_TEXT_PRESENT_NO_ALLOWLIST_MATCH` (error-text present, length bucket 129–256, no allowlist match, `attempt_count=1`); error **family UNKNOWN** (raw text read once, discarded by design) | PROVEN (classification) / family UNKNOWN by design |

**Key nuance (row 16):** because `graphrag_indexed_count=0` at the S001 failure, the failure was
**not** driven by cumulative *completed* scale (nothing had completed). It occurred while **75
documents were concurrently in flight in one sidecar** — implicating the *instantaneous*
concurrent-burst / in-flight-state condition rather than accumulated finished work.

## 4. Reconstruction — bounded diagnostic #5-R1 (`gr08e5r1`)

| Dimension | #5-R1 fact | Grade |
|---|---|---|
| Structure | 8 independent cells, levels **[1,2,4,8]**, 2 reps, **30** submissions (cap 64) | PROVEN (artifact) |
| Process | **fresh LightRAG container per cell** (`run_sweep` provisions per (level,rep)) | PROVEN |
| Workspace | **fresh unique workspace per cell** | PROVEN |
| Storage | **fresh owned storage per cell**, disposed at teardown | PROVEN |
| Within-cell submission | **`asyncio.gather` over the ≤8-source subset** — a concurrent single wave into that ONE cell sidecar (live_indexer08.py:246) | PROVEN |
| Max instantaneous concurrency | **8** (largest level) | PROVEN |
| Cross-cell cache/graph reuse | **NONE** (08E.1 fresh-process isolation) | PROVEN |
| Cumulative docs per sidecar | **≤8** (each fresh cell handles only its level's subset) | PROVEN |
| Outcome | **30/30 PROCESSED, 0 failures, 0 retries** | PROVEN (artifact) |
| Provider/models/version/retry/content | **identical to attempt #5** (frozen) | PROVEN |

**What cell isolation reset/removed vs the full run:** the single long-lived sidecar, the one
shared workspace, the accumulating LLM-cache and graph, the 75-way concurrent in-flight burst,
and the 75-doc cumulative volume — **all** were reduced to ≤8 and made fresh-per-cell.

## 5. Concurrency ≠ scale — the five independent axes (§8)

| Axis | Definition | Attempt #5 | #5-R1 |
|---|---|---|---|
| **A. Instantaneous concurrency** | docs concurrently in-flight in one sidecar | **75** | **≤8** |
| **B. Cumulative load** | total docs one sidecar handles over its lifetime | **75** (0 *completed* at failure) | **≤8** |
| **C. State accumulation** | cache/graph/doc-status retained in one process/workspace | **75-doc shared, accumulating** | **fresh per cell** |
| **D. Burstiness** | provider-call density over time | **75-doc burst → high** | **≤8-doc → low** |
| **E. Process lifetime** | how long one sidecar stays active while work accumulates | **one long-lived (all 75)** | **fresh per cell (≤8)** |

## 6. Delta matrix

| DIMENSION | FULL_ATTEMPT_5 | DIAGNOSTIC_5_R1 | STATUS | CAUSAL_RELEVANCE |
|---|---|---|---|---|
| corpus size | 75 | 8 distinct (S001–S008) | DIFFERENT | MEDIUM |
| cumulative submissions / sidecar | 75 (0 completed at failure) | ≤8 / fresh sidecar | DIFFERENT | MEDIUM |
| instantaneous concurrency (1 sidecar) | 75-way burst | ≤8-way | DIFFERENT | **HIGH** |
| process lifetime | one long-lived | fresh per cell | DIFFERENT | HIGH |
| workspace lifetime | one shared | fresh per cell | DIFFERENT | MEDIUM |
| LLM-cache lifetime | shared/accumulating | fresh per cell | DIFFERENT | MEDIUM |
| graph state lifetime | accumulating across 75 | fresh per cell (≤8) | DIFFERENT | MEDIUM |
| provider request burstiness | 75-doc burst | ≤8-doc | DIFFERENT | **HIGH** |
| provider cumulative volume / sidecar | 75 docs' extraction | ≤8 docs' | DIFFERENT | MEDIUM |
| submission scheduling | submit-all-then-poll | gather-then-poll (per cell) | SAME PATTERN / DIFFERENT SCALE | MEDIUM |
| retry behavior | max 2, frozen allowlist | identical | SAME | LOW |
| Source identity/content | S001–S075 frozen | S001–S008 frozen subset | SUBSET (same content) | LOW |
| LightRAG version | v1.5.6 | v1.5.6 | SAME | LOW |
| provider | OpenRouter | OpenRouter | SAME | LOW |
| models | gpt-4o-mini + tes-3-small | identical | SAME | LOW |
| embedding dimension | 1536 | 1536 | SAME | LOW |
| network/runtime environment | same host; **later wall-clock** than #5-R1 | same host | UNKNOWN (temporal delta) | UNKNOWN |

## 7. Review of the original hypotheses (§9) — meaning, not retroactive change

H1/H2/H3 are **failure-cause** hypotheses; they can only be *discriminated* by observing a
failure and characterizing its error family. #5-R1 produced no failure, so none can move off
INCONCLUSIVE (§10). Critically, **H1/H2/H3 as framed do not by themselves separate**:

- provider *instantaneous rate* pressure (axis A/D) — a load-induced transient (H1-flavoured);
- provider *cumulative capacity* pressure over a long-lived sidecar (axis B/D);
- long-lived LightRAG *state* interaction (axis C, ≈ H3);
- cache/state *accumulation* effects (axis C);
- document-count / graph-size effects (axis B).

Rather than rewrite the historical hypotheses, we record **subordinate future diagnostic
questions**:

- **Q-A:** Does a single fresh sidecar begin to fail as *instantaneous* concurrent extraction is
  scaled from 8 toward ~72, with everything else frozen?
- **Q-B/C:** Does a single fresh sidecar begin to fail as *cumulative* sources grow (fed in
  low-concurrency waves so instantaneous concurrency is held constant and low), isolating scale /
  state accumulation from instantaneous concurrency?
- **Q-D:** Is provider-call concurrency (LightRAG-internal) observable/controllable independent of
  Source concurrency? (Source concurrency ≠ provider-call concurrency — §15.)

## 8. The missing pressure axis (§11)

```
PRIMARY_DIFFERENCE_AXIS = E — MULTIPLE AXES CONFOUNDED
  (untested regime = the HIGHER-SCALE confounded burst — a single fresh sidecar taking a large
   concurrent wave, in which instantaneous concurrency (A), cumulative-submitted scale (B),
   in-flight state (C) and provider burst (D) all rise TOGETHER, exactly as in #5; #5-R1
   exercised this SAME confounded regime only up to ≤8 — clean. NO single axis is isolated by
   either run.)
```

**Evidence:** In attempt #5 the single 75-doc burst into one long-lived sidecar simultaneously
maximized axes A, B, C, D and E (§5); #5-R1 reduced **all** of them together (≤8, fresh per
cell). In #5-R1 every level has instantaneous concurrency **== cumulative-submitted** (one
`gather` wave; `default_plan` sets `concurrency==source_count`), so A, B and D are **inseparable
in #5-R1 too** — it is a smaller instance of the same confounded burst, not an axis-isolating
control. Neither run can attribute a mechanism. What #5-R1 *does* establish is that the confounded
regime at scale ≤8 is **clean**, ruling out the low end; the 8→75 range is untested. The forensic
detail that **no source had reached PROCESSED** when S001 failed (row 16) means cumulative
*completed*-doc scale is only **weakly implicated** (nothing had completed) — but during the
75-doc concurrent burst substantial **in-flight/partial** state (in-flight LLM calls, partial
graph writes, cache entries) had already accumulated, and the completion order at abort is
unknown, so in-flight state volume (C) and submitted-scale (B) **remain fully in play** alongside
instantaneous concurrency / provider burst (A/D). No axis is demoted.

**Answer to review question G** (could a clean #5-R1 be explained by per-cell fresh-state
resets?): **YES — this is the leading explanation.** #5-R1's per-cell fresh sidecar + ≤8-way
wave structurally could not reproduce the 75-way burst or any accumulation, so its cleanliness is
expected and is **not** evidence against the full-run failure.

## 9. Design goal & candidate next diagnostic (DESIGN ONLY — not run, not built)

Smallest bounded experiment that pushes the confounded burst regime toward the #5 scale while
holding everything else frozen. **This is a REPRODUCTION experiment, not an axis-isolation one:**
because #5-R1 already covered the confounded burst regime up to ≤8 cleanly (where A≈B≈D by
construction), the primary candidate **extends that same proven-clean machinery to a larger
concurrent wave into a single fresh sidecar** — a **burst-reproduction sweep** — to find whether
the #5 TRACK failure reappears at scale. It deliberately does **not** separate instantaneous
concurrency from cumulative-submitted scale or burst (they rise together in one wave — that is the
point: it recreates #5's condition). Axis *separation* is deferred to the secondary sequential arm
below and is worth building **only if** this arm reproduces the failure. A predeclared
stop-on-first-reproduction rule minimizes provider traffic.

```
NEXT_DIAGNOSTIC_NAME       = GraphRAG-08E.7 (proposed) — Single-Sidecar Burst-Reproduction Sweep
NEXT_DIAGNOSTIC_OBJECTIVE  = Determine whether the attempt-#5 TRACK failure REPRODUCES as the
                              confounded concurrent burst (instantaneous concurrency == cumulative-
                              submitted == burst, one wave) into ONE fresh sidecar is scaled from 8
                              toward ~72 (all frozen params). If it reproduces, characterize the
                              error family (finally discriminating H1 vs H2 vs H3) via the frozen
                              content-safe taxonomy — and THEN the secondary sequential arm can
                              separate scale/state (B/C) from instantaneous concurrency (A). If clean
                              to ~72, the #5 failure is NOT reproduced even at near-full-corpus burst
                              — evidence for a temporal/provider-capacity-at-#5 transient (H1 with an
                              environment/time dependency).
NEXT_DIAGNOSTIC_TREATMENTS = one FRESH isolated sidecar per treatment; ONE concurrent wave per
                              treatment; wave size C ∈ {8 (control), 16, 24, 32, 48, ~72}
                              (exact ladder + top value predeclared in the 08E.7 gate, justified from
                              the 75-source fixture; NOT frozen here); S001-anchored deterministic
                              prefix of the frozen fixture (S001 + next C−1 sorted keys); 1 repetition
                              initially; PREDECLARED STOP-ON-FIRST-REPRODUCTION (stop escalating once a
                              level shows failures) to bound traffic. NOTE: at each rung C is
                              simultaneously the instantaneous concurrency AND the cumulative-submitted
                              count AND the burst — a reproduction rung, not an isolated axis.
NEXT_DIAGNOSTIC_PROVIDER_BUDGET = bounded + predeclared in the 08E.7 gate; illustratively a stepped
                              8+16+24+32+48+72 single-wave ladder ≤ ~200 submissions worst-case, and
                              LESS with the stop rule. It has NO retrieval / DEV / HOLDOUT / V/GQ/GD,
                              so it is far below a 75×60 value benchmark — BUT the top rung (~72) is
                              essentially attempt #5's full-corpus INDEXING burst (only queries are
                              omitted); it is not smaller in indexing/provider load and is reached
                              ONLY if every lower rung stays clean (that is the deliberate reproduction
                              target, per §13's caution — approached, not defaulted to).
NEXT_DIAGNOSTIC_REQUIRES_NEW_CODE = YES
```

**Why new code (§25):** the committed harness cannot express this — `ALLOWED_LEVELS=(1,2,4,8)`,
`MAX_SOURCES_PER_LEVEL=8`, `MAX_TOTAL_SUBMISSIONS=64` (concurrency_diag08.py:60–64) cap a cell at
8 sources / 64 total; `validate_plan` enforces those; `select_diagnostic_sources` independently
caps the selected count at `MAX_SOURCES_PER_LEVEL` (concurrency_diag08.py:341); and `run_sweep`
provisions a fresh sidecar per (level,rep). A 16→72 ladder breaks **all** of these. The
**execution machinery is reused** (`index_cell` already gathers a subset concurrently into one
fresh attested cell — exactly a burst; provisioner/attestor/binding/isolation/cleanup unchanged);
the new work is a **new bounded, predeclared, higher-cap plan** (wave sizes up to ~72, matching
total budget) + aggregation keyed by the higher wave ladder. These higher caps are a
**diagnostic-instrument bound only** — NOT a change to the frozen full-run/benchmark concurrency
policy (mirroring 08E's "submit-batching knob is a diagnostic instrument only" framing).
**Not implemented in this gate.**

**Secondary/optional arm (only if the burst arm reproduces, to separate A/D from B/C):** a
**cumulative-scale / long-lived-sidecar** diagnostic — one fresh sidecar per treatment fed N
sources in **low fixed-concurrency sequential waves** (instantaneous concurrency held at a
proven-clean w, e.g. 4), varying cumulative N ∈ {8, 24, 48, ~72}, so scale/state accumulation is
isolated from instantaneous concurrency. Also requires new code (wave-fed long-lived cell +
accumulation aggregation). **Design only.**

### Design invariants carried forward (§16–§18)
- **No cross-treatment state reuse** — every treatment is a fresh process/workspace/storage,
  disposed before the next (identical to #5-R1 / 08E.1).
- **Deterministic predeclared selection** — S001-anchored fixture prefix; no cherry-picking of
  previously-passing/failing Sources.
- **Predeclare before any live run** — treatment count, source count/treatment, concurrency
  ladder, submission order, retry cap (unchanged frozen `MAX_INDEX_ATTEMPTS_PER_SOURCE=2`),
  failure stop semantics, provider budget, cleanup rules, interpretation criteria. **No tuning
  after results.**
- **Frozen everything else** — provider/models/version/retry/allowlist/Source content/fixture.
- **Attempt-#6 boundary (§19):** distinct from the full 75×60 value benchmark; NO DEV/HOLDOUT, NO
  V/GQ/GD, NO retrieval metrics. Attempt #6 remains separately authorized later.

## 10. Decisions

```
S001_FAILURE_REPRODUCED            = NO
H1 = INCONCLUSIVE   H2 = INCONCLUSIVE   H3 = INCONCLUSIVE
ROOT_CAUSE_CONFIRMED               = NO
08F_MITIGATION_GATE_JUSTIFIED      = NO      (zero failures, no confirmed cause, no mitigation to gate)
FULL_ATTEMPT_6_JUSTIFIED_NOW       = NOT_YET (clean #5-R1, root cause unknown, #5 failure not reproduced;
                                              running #6 blind would re-hit the unexplained failure or
                                              pass by luck without diagnosis)
NEXT_DIAGNOSTIC_REQUIRED           = YES     (GraphRAG-08E.7 burst-concurrency sweep — design above)
VALUE_EVIDENCE_READY               = NO
GRAPH_RETRIEVAL_VALUE_EVIDENCED               = NOT_RUN
STRUCTURED_QUERY_DATA_RUNTIME_VALUE_EVIDENCED = NOT_RUN
STRUCTURED_EVIDENCE_VALUE_EVIDENCED           = NOT_RUN
```

Retrieval value remains unevidenced: a successful *indexing* diagnostic establishes execution
correctness, not retrieval value (§20).

## 11. Frozen / non-goals (unchanged)

No change to: the transient retry classifier/allowlist, `MAX_INDEX_ATTEMPTS_PER_SOURCE=2`,
benchmark concurrency policy, fixture `a58a6853…143d`, Source content, provider (OpenRouter),
models (`openai/text-embedding-3-small` 1536 / `openai/gpt-4o-mini`), LightRAG v1.5.6. No
production/eval code change in this gate (docs only); production imports no eval code; no
migration (count 50); no `.env` edit; zero provider traffic; sidecar not started; `GRAPHRAG_ENABLED`
untouched. No attempt #6; no DEV/HOLDOUT; no V/GQ/GD; no mitigation; no production adapter; no
GraphRAG-09.
