# GraphRAG-08E.7A — Single-Sidecar Burst-Reproduction Experimental Design Freeze

**Status:** OFFLINE FORENSIC + EXPERIMENT-DESIGN ONLY. **No implementation, no provider traffic,
no OpenRouter, no live LightRAG indexing, no attempt #6, no DEV/HOLDOUT, no V/GQ/GD, no
mitigation, no retry/allowlist change, no production adapter, no GraphRAG-09.** No code changed
(docs only). This gate freezes an exact, reproducible, bounded 08E.7 **screening** experiment; it
does **not** implement or authorize it.

Checkpoint verified: branch `feature/graphrag-lifecycle`, HEAD
`452923a6cd64c28d055efc7b0ca449d8e1c8c1ff`, tag `graphrag-08e6-scale-forensic-approved` (peels to
HEAD), tree CLEAN, fixture `a58a68535c345e18f0263904f818e4e2068a164056408665d8bb9233eceb143d`.

**Precision addendum applied:** three experimental semantics tightened before operator checkpoint —
(1) `BURST_SCHEDULING_MODE = SUBMIT_ALL_THEN_POLL` frozen as required (§15); (2) the stop rule is a
**ladder** stop with a current-rung bounded **drain**, never in-flight cancellation, plus a
full-initial-wave treatment-validity requirement (§10/§17); (3) the reproduction flag is **split**
into a broad `CLASSIFIER_SIGNATURE_REPRODUCED` and a strict `S001_HISTORICAL_EVENT_REPRODUCED_STRICT`
(+ separate `HISTORICAL_ATTEMPT_NUMBER_MATCH`), with non-exclusive rung classification (§11/§12).

> Note on cross-references: this document was written against the **08E.7A authorizing prompt**,
> so a bare `§N` generally refers to that prompt's section of that number (e.g. §33 implementation
> delta, §36/§37 budget accounting, §40 decision table). This document's own headers are numbered
> §1–§22 independently; where the text points at one of its own sections it also names that
> section by topic. Prompt section numbers above 22 have no same-numbered section in this document.

---

## 1. Frozen prior evidence (08E.6 starting point)

```
GraphRAG-08E #5-R1 = COMPLETE — 8/8 cells, 30/30 submissions, 0 failures, 0 retries
S001_FAILURE_REPRODUCED = NO
H1 = INCONCLUSIVE   H2 = INCONCLUSIVE   H3 = INCONCLUSIVE   ROOT_CAUSE_CONFIRMED = NO
PRIMARY_DIFFERENCE_AXIS = E — MULTIPLE AXES CONFOUNDED
08F_MITIGATION_GATE_JUSTIFIED = NO   FULL_ATTEMPT_6_JUSTIFIED_NOW = NOT_YET
```

## 2. Purpose (what 08E.7 is, and is NOT)

08E.7 is a **SINGLE-SIDECAR BURST-REPRODUCTION SCREENING SWEEP**, **not** a causal-isolation
experiment. Primary question: *at what burst scale, if any, does the historical attempt-#5
failure regime reappear under the current frozen environment?* It **intentionally** lets these
rise together within each rung — instantaneous Source concurrency, cumulative submitted Sources
in the wave, in-flight LightRAG state, provider-request burstiness, shared process/workspace
state — because that is attempt #5's condition. **It does not isolate any of those mechanisms**
(axis separation is a later, separate gate — §29).

## 3. Exact attempt-#5 reconstruction (committed evidence)

### 3a. Source submission order — **PROVEN**
- Fixture `tests/fixtures/graphrag_08_eval_v1/corpus.json` lists **75** sources in **ascending
  key order S001…S075** (verified: file order == `sorted(keys)`, first key `S001`).
- The full-run harness submits in **corpus order**: `create_and_index()` creates Sources in
  `enumerate(selected_sources)` order (runner08.py:362), appends to `created_ids` in that order
  (:369), and `_graph_index_with_retry` submits `for canonical in self.created_ids` (runner08.py:490)
  **before** polling (:497). Selection is all 75 in `bench.sources` order (precheck08.py:493).
- Therefore attempt-#5 submission order = **S001, S002, …, S075**, and **S001 was submitted
  first** (`source:gr08ef5d8978700`). For this fixture the full-run order, the ascending-key
  order, and the diagnostic's `sorted`-key selection all **coincide** — so a nested-prefix
  selection is unambiguous and matches history.

`ATTEMPT_5_SOURCE_ORDER_RECONSTRUCTED = YES (PROVEN)`.

### 3b. Failure signature — **reconstructed from committed code + docs**
The historical S001 failure, as recorded by the committed classifier (`index_retry08.py`) and the
committed docs (CURRENT_PHASE.md:17; GRAPHRAG_08E6…:101; GRAPHRAG_08_FROZEN_STATE_HANDOFF.md):

```
failure_surface            = TRACK                              (index_retry08.py:280)
terminal_state             = DocStatus.FAILED
classification             = NON_RETRYABLE                      (index_retry08.py:263)
classification_reason_code = TRACK_TEXT_PRESENT_NO_ALLOWLIST_MATCH   (index_retry08.py:125,267)
retry_allowed              = False                              (index_retry08.py:284)
error_text_present         = True
error_text_length_bucket   = "129_256"                          (index_retry08.py:134)
matched_transient_classes  = ()   matched_non_transient_classes = ()   (no allowlist match)
family                     = UNKNOWN (raw text read once, discarded by design)
logical_source_id          = S001  (canonical source:gr08ef5d8978700)
attempt_count              = 1     max_attempts_observed = 1  (NON_RETRYABLE consumes no retry)
raw_error_text_persisted   = NO (correct — never persisted anywhere)
```

No committed run-artifact/manifest exists for run `13e59a3edbb8` (artifacts are content-free and
gitignored); the authoritative record is the committed docs + the `ReasonCode` enum.
`HISTORICAL_FAILURE_SIGNATURE_RECONSTRUCTED = YES`.

## 4. Treatment ladder (frozen)

```
BURST_LEVELS = [8, 16, 24, 32, 48, 75]
TOP_RUNG     = 75
```

**Why 75, not 72 (§3/§4):** 75 is chosen **not** because "higher is better" but because it is the
exact frozen full-corpus submission burst that attempt #5 ran — reproduction fidelity requires
the historical scale. No source-traced evidence indicates the historical effective burst was
anything other than the full 75-Source submission, so `~72`/`72`/rounded substitutes are
rejected. The ladder **approaches** 75 progressively (8→16→24→32→48→75), never jumping straight
from 8 to 75, so a threshold (if any) is discovered at the smallest reproducing rung.

## 5. Source selection & order (frozen — nested prefixes)

```
SOURCE_ORDER_RULE     = frozen attempt-#5 submission order = corpus.json file order = S001…S075
SOURCE_SELECTION_RULE = deterministic nested prefix: treatment C = the first C Sources in that order
```

| Treatment C | Sources |
|---|---|
| 8  | S001–S008 |
| 16 | S001–S016 |
| 24 | S001–S024 |
| 32 | S001–S032 |
| 48 | S001–S048 |
| 75 | S001–S075 (full corpus) |

Larger rungs strictly extend smaller ones; **S001 stays first** (its historical position);
**no** cherry-picking, **no** resampling, **no** S001 duplication, **no** S001-only treatment,
**no** moving S001. The 75 rung matches the historical corpus ordering exactly.

## 6. Source content (frozen — immutable fixture)

Same synthetic fixture Sources, same IDs, same exact contents; **no** run-specific suffix,
whitespace perturbation, randomized marker, or duplicated synthetic copy to inflate load. Fixture
hash `a58a6853…143d` immutable (any change ⇒ a new fixture version, not this experiment).

## 7. S001 (frozen)

S001 was historically the first submitted Source (§3a, PROVEN). Preserve that position; do **not**
duplicate, move, or special-case it.

## 8. Repetition policy (frozen)

```
SCREENING_REPETITIONS_PER_LEVEL = 1
```

08E.7 is a bounded **screening** sweep for threshold discovery, not a confirmatory study. If a
reproduction (or any valid failure) occurs, **do not** auto-run a second repetition — a separate
operator-reviewed **confirmation** gate is required. This prevents retry-to-pass, retry-to-fail,
post-result tuning, and unbounded cost.

## 9. Budget & retry-attempt accounting (frozen — mandatory §36/§37)

**How the committed harness counts:** `LiveCellIndexer08.submission_count += 1` fires **per index
attempt** (initial *and* retry), inside `while attempts < MAX_INDEX_ATTEMPTS_PER_SOURCE`
(live_indexer08.py:275–277); the committed cap `MAX_TOTAL_SUBMISSIONS = 64` is therefore an
**attempt** cap, not a planned-Source cap. Retry happens only on a **transient** classification
(live_indexer08.py:315). So "submission" ≠ "planned Source" — they must not both be called "203".

```
MAX_PLANNED_SOURCE_WORKLOAD = 203     (exact: 8 + 16 + 24 + 32 + 48 + 75, one screening rep, full ladder)
MAX_INDEX_ATTEMPTS_TOTAL    = 406     (exact hard cap: 203 × MAX_INDEX_ATTEMPTS_PER_SOURCE(2))
```

- `PLANNED_SOURCE_WORKLOAD` counts each planned Source once (203).
- `ACTUAL_INDEX_ATTEMPTS` counts every submit attempt incl. retry; its hard maximum is **406**.
- **Retry attempts count toward the 406 attempt budget, NOT the 203 planned-Source budget.**
- **Expected clean-path cost is the FULL 203 planned submissions** (not "far below"): #5-R1 was
  clean at C=8, so the likely outcome is zero failures → all six rungs run → 203 source-extractions.
  The ladder-stop rule (§10, `STOP_LADDER_AFTER_FIRST_RUNG_WITH_VALID_FAILURE`) reduces cost **only
  if** a failure actually occurs.
  Note the nested prefixes re-index the early Sources repeatedly (S001–S008 six times, S009–S016
  five times, …), so 203 planned submissions ≈ **2.7× a single full-corpus (75) index** in
  extraction work. The 203/406 caps are exact and frozen independent of the stop rule; the only
  true "far below" comparison is against a full 75×60 **value** benchmark (which additionally runs
  60 queries × V/GQ/GD — none of which 08E.7 does).

Provider-call accounting (§7/§35): LightRAG issues multiple internal provider calls per Source, so
```
SOURCE_SUBMISSION_BUDGET   = EXACT (203 planned / 406 attempts hard cap)
PROVIDER_INTERNAL_CALL_COUNT = NOT_PREDECLARED_EXACTLY
PROVIDER_TRAFFIC             = BOUNDED_BY_FROZEN_SOURCE_SUBMISSION_PLAN
```
No fabricated exact provider-call or token/dollar count. A future run reports provider-call counts
only where genuinely observable.

## 10. Stop rules (frozen — LADDER stop, not in-flight cancellation)

```
STOP_LADDER_AFTER_FIRST_RUNG_WITH_VALID_FAILURE = YES
CURRENT_RUNG_ALREADY_SUBMITTED_WORK             = DRAIN_TO_BOUNDED_TERMINAL_OUTCOME
NEXT_RUNG_AFTER_VALID_FAILURE                   = NO
```

The stop is a **ladder** stop, **not** an immediate cancellation of the in-flight burst. Once a
rung's full C-Source initial wave has been submitted (§17, full-wave establishment), a legitimate
Source failure does **not** cancel the other already-submitted Sources: the full C-Source burst
*is* the experimental treatment, and cancelling outstanding work mid-rung to save cost would change
the very quantities under test (provider burst, in-flight state, shared-sidecar load).

- **Current-rung drain rule.** If one or more legitimate Source failures are observed in a valid,
  fully-established rung, continue bounded observation of the already-submitted C-Source wave until
  the rung reaches its predeclared completion/drain condition (every submitted Source reaches a
  terminal state, or the bounded poll deadline / frozen attempt cap is hit). Apply frozen retries
  only where the frozen classifier authorizes them. Do **not** submit any extra Source, do **not**
  rerun the rung, do **not** start a new rung.
- **Ladder stop.** After the rung is fully drained and classified (§12), if it contained **any**
  legitimate valid Source failure, **STOP the ladder** — do not enter the next burst level. Record
  `FIRST_FAILURE_RUNG = C` plus the three independent flags: `CLASSIFIER_SIGNATURE_REPRODUCED`,
  `S001_HISTORICAL_EVENT_REPRODUCED_STRICT`, `NOVEL_VALID_FAILURE` (§11/§12). Do **not** rerun C,
  change retry, open mitigation, or run confirmation — a separate confirmation gate is required.

**Safety exception.** The drain rule governs a *valid experimental Source failure only*; it never
overrides a safety/runtime fail-stop. Container/workspace/storage/endpoint/provider-binding
attestation failure, cleanup failure, budget breach, runtime corruption, or cancellation are
**invalid-treatment / harness** conditions (§17) that may stop the experiment immediately. Never
conflate a VALID SOURCE FAILURE with an INVALID TREATMENT / HARNESS FAILURE.

**Cost semantics.** Stop-on-first-failure saves provider work from **later rungs**, not necessarily
from the remainder of the **current** rung (its C-Source initial burst is already submitted).
Example: a first valid failure at C=48, after all 48 initial Sources were submitted, **skips C=75**
but still treats the **full 48-Source wave** as the entered treatment (drained, then classified).
A ladder that stops this way leaves rungs above C **unobserved** — its result must never be reported
as "did not reproduce through 75" (that phrasing belongs only to the fully-clean ladder, §13).

## 11. Reproduction criteria (frozen — TWO distinct flags, predeclared BEFORE execution)

The historical failure has a **broad classifier signature** that can recur on *any* Source, and a
**strict Source-level identity** (S001). These are **separate** flags; one boolean must never imply
the other, and a failure on any Source must never be treated as the same historical S001 event by
signature alone.

### 11a. Classifier-signature reproduction
```
REPRODUCTION_SIGNATURE_MATCH_MODE = EXACT_SAFE_CODE (on retry_reason_code)

CLASSIFIER_SIGNATURE_REPRODUCED = YES  iff a legitimate terminal Source failure in a fully VALID
treatment (§17) has ALL of:
    terminal_status    == FAILED       (a legitimate terminal indexing failure)
    retry_reason_code  == "TRACK_TEXT_PRESENT_NO_ALLOWLIST_MATCH"   (PRIMARY discriminant)
    error_text_present == True        (implied by the reason code)
    retryable          == False       (implied by the reason code; == NON_RETRYABLE)
  (surface = TRACK — the only surface this diagnostic's poll path characterizes)
```
Do **not** require the same raw text, length bucket, or family (raw text is non-persisted; the
historical `family` was UNKNOWN; requiring them would over-constrain). `retry_reason_code` is the
primary and sufficient discriminant (`concurrency_diag08.characterize_failure`,
concurrency_diag08.py:199, the decision-twin of the full-run `diagnose_failed_track`). **Meaning:**
the same frozen classifier *signature* reappeared — **NOT** that the mechanism is confirmed the
same, and **NOT** that the historical S001 event was reproduced. `TRACK_TEXT_PRESENT_NO_ALLOWLIST_MATCH`
fires for *any* present non-transient error text (index_retry08.py:263–267), so a same-signature
failure on a *different* Source (e.g. S037) is a signature match but **not** the historical S001
event. `family` and `error_text_length_bucket` are recorded for post-hoc comparison only.

### 11b. Strict S001 historical-event reproduction
Here "**strict**" means **Source-identity (S001) + the classifier signature** — the historical
attempt number is **not** part of this flag; it is tracked adjacently as
`HISTORICAL_ATTEMPT_NUMBER_MATCH` (per the authorizing prompt's design), so a reader should not
expect "STRICT" to bundle the attempt count.
```
S001_HISTORICAL_EVENT_REPRODUCED_STRICT = YES  iff, in a fully VALID treatment:
    logical_source_id  == "S001"
    terminal_status    == FAILED
    retry_reason_code  == "TRACK_TEXT_PRESENT_NO_ALLOWLIST_MATCH"
    error_text_present == True
    retryable          == False
HISTORICAL_ATTEMPT_NUMBER_MATCH = (attempt_number == 1) ? YES : NO      — recorded SEPARATELY
```
The historical S001 failure occurred on **attempt #1** (a NON_RETRYABLE consumes no retry). If the
same signature appears on S001 only after an earlier retryable attempt, record the actual
`attempt_number` and set `HISTORICAL_ATTEMPT_NUMBER_MATCH = NO` — do **not** silently claim an exact
attempt-count match. Strict-event reproduction is a strict **subset** of classifier-signature
reproduction (11b ⇒ 11a on S001), and may co-occur with it.

Even when either flag is YES, `ROOT_CAUSE_CONFIRMED = NO` (§14) — this is reproduction screening,
not mechanism isolation; same-signature vs same-mechanism is undistinguishable from committed
evidence (historical family UNKNOWN, raw text discarded).

## 12. Novel-failure criterion & rung classification (frozen)

```
NOVEL_VALID_FAILURE = YES  iff a legitimate terminal Source failure occurs in a VALID treatment for
                      which CLASSIFIER_SIGNATURE_REPRODUCED = NO for THAT failure
                      (e.g. a transient-allowlist match that terminally fails after retry exhaustion,
                       a TIMEOUT terminal, or TRACK_TEXT_ABSENT/UNREADABLE = UNKNOWN).
```
A same-classifier-signature failure on a *different* Source is **not** "novel" — it is
`CLASSIFIER_SIGNATURE_REPRODUCED = YES` with `S001_HISTORICAL_EVENT_REPRODUCED_STRICT = NO`. A novel
failure is **never** labeled a reproduction.

**Rung classification** (after draining a valid entered rung, over ALL its safe Source outcomes):

| Outcome | Condition |
|---|---|
| **A — CLEAN** | no legitimate Source failures |
| **B — CLASSIFIER_SIGNATURE_REPRODUCED** | ≥1 legitimate failure matches §11a |
| **C — S001_HISTORICAL_EVENT_REPRODUCED_STRICT** | S001 itself meets §11b (may co-occur with B) |
| **D — NOVEL_VALID_FAILURE** | ≥1 legitimate failure does not match §11a |

Outcomes are **not mutually exclusive** — a rung with several differently-failing Sources may carry
more than one of B/C/D. Do not force a single label.

## 13. No-failure outcome (frozen)

If every valid rung (8,16,24,32,48,75) drains cleanly (no legitimate Source failure):
```
FIRST_FAILURE_RUNG                      = NONE
CLASSIFIER_SIGNATURE_REPRODUCED         = NO
S001_HISTORICAL_EVENT_REPRODUCED_STRICT = NO
NOVEL_VALID_FAILURE                     = NO
CLEAN_THROUGH_BURST_LEVEL               = 75
```
This means **only** that the historical classifier signature and the strict S001 event did not
reproduce under this frozen screening sweep. It does **not** mean the historical failure was
impossible, that H1/H2/H3 are disproven, that the provider has no transient behavior, or that
attempt #5 was invalid. (Distinct from a ladder that stopped early on a failing rung, which leaves
rungs above C **unobserved** — §10.)

## 14. Root-cause & hypothesis interpretation (frozen)

Even if the signature reproduces, default `ROOT_CAUSE_CONFIRMED = NO`: 08E.7 is a reproduction
experiment, not an axis-isolation one — a reproduction establishes a scale/load *regime*
associated with recurrence; it does not by itself distinguish instantaneous concurrency vs
provider burst vs in-flight state vs submitted scale vs shared sidecar state. On reproduction, the
content-safe characterization may finally provide H1/H2/H3-relevant evidence **under predeclared
rules only** (no retroactive redefinition). With no failure, H1/H2/H3 stay INCONCLUSIVE.

## 15. Treatment & concurrency semantics (frozen)

- **Per-rung isolation (§12/§16):** each C is an independent treatment — fresh LightRAG
  process/container, fresh unique workspace, fresh run-owned storage, fresh LLM cache, fresh graph
  state, fresh doc-status; submit exactly C Sources as ONE wave; full cleanup; the next rung starts
  from a new fresh sidecar. **No cross-treatment reuse.**
- **Single sidecar within a rung (§13):** all C Sources of a rung use ONE sidecar / ONE workspace /
  ONE storage for the whole wave (reproduces attempt-#5 shared-state). **Not** one sidecar per Source.
- **Within-rung concurrency (§14):** `TARGET_SOURCE_CONCURRENCY = C`; the C Sources are submitted
  as one burst; not serialized, not throttled below C (the historical runner imposed no ON-side
  limit — runner08 `EvalRunConfig08` has no concurrency field).
- **Burst scheduling mode (FROZEN — REQUIRED, not optional):** `BURST_SCHEDULING_MODE =
  SUBMIT_ALL_THEN_POLL`. Attempt #5's proven shape was *submit every Source in the burst, THEN
  poll/observe terminal states* (runner08.py:490–497). The 08E.7 implementation MUST reproduce that
  shape for treatment C: (1) create one valid fresh isolated sidecar; (2) resolve the exact nested
  prefix of C Sources; (3) submit all C **initial** Source indexing requests in the historical order
  **without** waiting for Source #1 to reach terminal before submitting later Sources (i.e. do not
  let the diagnostic helper's normal submit+poll-per-source coupling serialize the wave); (4) once
  the full C-Source initial wave is submitted, poll/observe using bounded committed semantics; (5)
  process retries only as the frozen classifier authorizes; (6) drain to the rung outcome (§10);
  (7) cleanup; (8) only then may the next rung start. `ATTEMPT5_SCHEDULING_REPRODUCTION =
  SUBMIT_ALL_THEN_POLL`. The committed `LiveCellIndexer08.index_cell` currently `asyncio.gather`s
  submit+poll per Source (live_indexer08.py:246) — that gather-per-source coupling is **NOT** an
  acceptable substitute for this experiment; a submit-all-then-poll wave mode is a **REQUIRED**
  implementation deliverable (§19). The experiment must NOT be described as high-fidelity attempt-#5
  burst reproduction if gather-per-source semantics are retained.
- **Provider-call concurrency (§15):** `SOURCE_SUBMISSION_CONCURRENCY = C`;
  `PROVIDER_INTERNAL_CONCURRENCY = OBSERVATIONAL / NOT EXACTLY CONTROLLED` (LightRAG default
  internal extraction concurrency; Source concurrency ≠ provider-call concurrency).

## 16. Frozen provider / retry (unchanged)

```
LightRAG   = v1.5.6
Provider   = OpenRouter
LLM        = openai/gpt-4o-mini   (binding openai, host https://openrouter.ai/api/v1)
Embedding  = openai/text-embedding-3-small  (binding openai, dim 1536)
Retry      = MAX_INDEX_ATTEMPTS_PER_SOURCE = 2 ; frozen index_retry08 classifier + allowlist + taxonomy
```
No model/provider/version change; no retry-policy or diagnostic-specific allowlist change.

## 17. Valid-failure & rung-failure semantics (frozen — §18/§38)

A Source failure counts as experimental evidence **only** in a fully **VALID** treatment:
version-attestation PASS, workspace-attestation PASS, storage-attestation PASS,
provider-binding-attestation PASS, provider-secret-presence PASS, endpoint-ownership PASS,
Option-A/canonical-Source setup PASS, **and** the Source reaches a legitimate terminal indexing
failure. Otherwise:

| Event | Meaning |
|---|---|
| legitimate Source terminal FAILED (valid treatment) | **experimental observation** |
| provisioning / container / attestation failure | **invalid treatment / runtime stop** (not a Source failure) |
| cleanup failure | **runtime stop** |
| budget breach (planned 203 / attempts 406) | **harness failure** (hard stop) |
| cancellation | **incomplete** — no causal interpretation |

**Treatment-validity — full initial wave (FROZEN):**
```
FULL_INITIAL_WAVE_ESTABLISHED          = YES   (required for a rung to count as treatment C)
PARTIAL_WAVE_COUNTS_AS_VALID_TREATMENT = NO
```
For a rung C to count scientifically as treatment C, **all C intended initial Source submissions
must enter the committed indexing contract under the valid owned sidecar.** Distinguish:
- **(A) a legitimate provider/LightRAG Source outcome** expressed through the normal indexing
  contract (submit accepted → track → terminal). A Source accepted into the contract that later
  reaches `DocStatus.FAILED` is a **valid Source failure** (experimental data), not a
  wave-establishment failure.
- **(B) a harness/transport failure that prevents even issuing/accepting the intended C-Source
  wave** (e.g. the submit call itself errors on transport). Then `FULL_INITIAL_WAVE_ESTABLISHED =
  NO`, `TREATMENT_VALID = NO` → **stop and report a runtime/harness failure**. Never silently
  analyze a partial (e.g. 48-of-75) burst as C=75.

## 18. Content-safe experiment artifact (design of future fields)

```
Per Source attempt: run_id · checkpoint_sha · fixture_hash · treatment_level(C) · logical_source_id ·
  attempt_number · terminal_status · failure_reason_code · failure_family · retryable ·
  error_text_length_bucket · latency_ms · cleanup_state
Derived flags: classifier_signature_match(bool) · s001_strict_event_match(bool) ·
  historical_attempt_number_match(bool) · novel_valid_failure(bool)
Per rung: full_initial_wave_established(bool) · treatment_valid(bool) ·
  rung_outcome{CLEAN | CLASSIFIER_SIGNATURE_REPRODUCED | S001_HISTORICAL_EVENT_REPRODUCED_STRICT | NOVEL_VALID_FAILURE (non-exclusive)}
Per run: first_failure_rung · clean_through_burst_level · planned_source_workload · index_attempts_used · root_cause_confirmed(=NO)
```
**Never**: Source content, raw failure reason text, raw provider response, secrets.

## 19. Implementation delta (§33 — NOT implemented here)

Structural limits in the committed harness that **block** 08E.7 as specified:

| Committed limit | Location | 08E.7 needs |
|---|---|---|
| `ALLOWED_LEVELS = (1,2,4,8)` | concurrency_diag08.py:60 | levels {8,16,24,32,48,75} |
| `MAX_SOURCES_PER_LEVEL = 8` | concurrency_diag08.py:62 | up to 75 |
| `MAX_DIAGNOSTIC_LEVELS = 4` | concurrency_diag08.py:61 | 6 |
| `MAX_TOTAL_SUBMISSIONS = 64` | concurrency_diag08.py:64 | attempt cap 406 |
| `select_diagnostic_sources` caps count at `MAX_SOURCES_PER_LEVEL`, uses `sorted(keys)` | concurrency_diag08.py:341,345 | up to 75 (sorted == corpus order for this fixture, so nested prefix already correct — only the cap must rise) |
| `default_plan` reps = 2 | concurrency_diag08.py | 1 (screening) |
| `run_sweep` runs ALL cells (no early stop) | concurrency_diag08.py:565 | **ladder-stop-after-first-rung-with-valid-failure** + **current-rung bounded drain** (NEW) |
| `index_cell` gathers submit+poll per Source | live_indexer08.py:246 | **REQUIRED `SUBMIT_ALL_THEN_POLL` wave mode** (submit all C initial, THEN poll) — NEW; gather-per-source not acceptable for this experiment |
| — | — | **NEW:** full-initial-wave-establishment validation (partial wave ⇒ TREATMENT_VALID=NO); dual reproduction flags `CLASSIFIER_SIGNATURE_REPRODUCED` + `S001_HISTORICAL_EVENT_REPRODUCED_STRICT` + `HISTORICAL_ATTEMPT_NUMBER_MATCH` + `NOVEL_VALID_FAILURE` + non-exclusive rung classification; experiment-specific hard caps **203 planned / 406 attempts** (both mechanically guarded, distinct); aggregation keyed by the burst ladder |

**Reuse (§34) — no parallel live stack:** Option-A isolation (`isolation08`), frozen provider
binding (`provider_binding08`), secret-safe Docker transport + `DockerRuntimeAttestor` + cell
provisioner (`cell_provisioner08`), per-cell endpoint ownership, `LiveCellIndexer08`
(`index_cell`'s concurrent wave = the burst), content-free `AttemptRecord`, raw-error containment,
cleanup, and `LiveDiagnosticOrchestrator08` are all reused unchanged. The higher caps are a
**diagnostic-instrument bound only** — NOT a change to the frozen full-run/benchmark concurrency
policy.

```
08E7_IMPLEMENTATION_REQUIRES_NEW_CODE = YES  (new bounded higher-cap plan [8,16,24,32,48,75] + raised
                                              selection cap + distinct 203 planned / 406 attempt hard caps
                                              + REQUIRED SUBMIT_ALL_THEN_POLL wave mode + full-initial-wave
                                              validation + current-rung bounded drain + ladder-stop-after-
                                              first-rung + dual reproduction flags (classifier-signature +
                                              strict-S001-event + attempt-number) + non-exclusive rung
                                              classification; live STACK fully reused, no parallel stack)
```

## 20. Follow-ups (design intent only — NOT authorized, NOT implemented)

- **If the signature reproduces at level C (§29):** a separate confirmation / axis-isolation gate
  — e.g. same C sequential vs burst; same C with controlled wave size; same cumulative Source
  count at lower instantaneous concurrency; fresh-state vs within-treatment accumulated state.
- **If a future valid sweep reaches 75 cleanly (§30):** do **not** auto-rerun and do **not**
  auto-run attempt #6; return to operator review on whether the environment has changed enough to
  scientifically justify attempt #6. No such authorization exists here.

## 21. Boundaries retained

```
08E.7 = SCREENING REPRODUCTION SWEEP (not a confirmatory study)
Attempt #6 = NOT_AUTHORIZED   (no DEV/HOLDOUT/V/GQ/GD/retrieval metrics)
08F = NOT_OPENED   08F_MITIGATION_GATE_JUSTIFIED = NO
FULL_EXECUTION_AUTHORIZED = NO   VALUE_EVIDENCE_READY = NO
GRAPH_RETRIEVAL_VALUE_EVIDENCED / STRUCTURED_QUERY_DATA_RUNTIME_VALUE_EVIDENCED /
STRUCTURED_EVIDENCE_VALUE_EVIDENCED = NOT_RUN
```

## 22. §40 Design decision table (frozen)

```
BURST_LEVELS                 = [8, 16, 24, 32, 48, 75]
TOP_RUNG                     = 75
REPETITIONS_PER_LEVEL        = 1
BURST_SCHEDULING_MODE        = SUBMIT_ALL_THEN_POLL   (required; gather-per-source not acceptable)
FULL_INITIAL_WAVE_REQUIRED   = YES   (partial wave ⇒ TREATMENT_VALID=NO, runtime stop)
SOURCE_SELECTION_RULE        = deterministic nested prefix (first C of the attempt-#5 order)
SOURCE_ORDER_RULE            = frozen attempt-#5 submission order = corpus.json order = S001…S075 (PROVEN)
MAX_PLANNED_SOURCE_WORKLOAD  = 203      (distinct budget; unique planned Sources across the ladder)
MAX_INDEX_ATTEMPTS_TOTAL     = 406      (distinct budget; 203 × MAX_INDEX_ATTEMPTS_PER_SOURCE=2; both mechanically guarded)
CURRENT_RUNG_ALREADY_SUBMITTED_WORK = DRAIN_TO_BOUNDED_TERMINAL_OUTCOME  (no in-flight cancellation)
STOP_RULE                    = STOP_LADDER_AFTER_FIRST_RUNG_WITH_VALID_FAILURE (drain current rung, then STOP)
CLASSIFIER_SIGNATURE_CRITERION      = valid treatment + terminal FAILED + retry_reason_code=
                               TRACK_TEXT_PRESENT_NO_ALLOWLIST_MATCH + error_text_present=True + retryable=False
                               (== classifier SIGNATURE, NOT confirmed mechanism, NOT the S001 event)
STRICT_S001_HISTORICAL_EVENT_CRITERION = the classifier-signature criterion AND logical_source_id==S001;
                               HISTORICAL_ATTEMPT_NUMBER_MATCH = (attempt_number==1) recorded separately
NOVEL_VALID_FAILURE_CRITERION = valid terminal failure with CLASSIFIER_SIGNATURE_REPRODUCED=NO for that failure
RUNG_CLASSIFICATION          = {CLEAN | CLASSIFIER_SIGNATURE_REPRODUCED | S001_HISTORICAL_EVENT_REPRODUCED_STRICT
                               | NOVEL_VALID_FAILURE} — non-exclusive
NO_FAILURE_RULE              = all 6 rungs clean → CLASSIFIER_SIGNATURE_REPRODUCED=NO,
                               S001_HISTORICAL_EVENT_REPRODUCED_STRICT=NO, NOVEL_VALID_FAILURE=NO,
                               CLEAN_THROUGH_BURST_LEVEL=75 (distinct from an early ladder stop, which leaves
                               rungs above C unobserved — §10/§13)
PARTIAL_WAVE_COUNTS_AS_VALID_TREATMENT = NO
ROOT_CAUSE_CONFIRMED         = NO   (even if a reproduction flag is YES — reproduction ≠ isolation)
PROVIDER / MODELS            = OpenRouter · openai/gpt-4o-mini · openai/text-embedding-3-small (1536)
LIGHTRAG_VERSION             = v1.5.6
RETRY_POLICY                 = MAX_INDEX_ATTEMPTS_PER_SOURCE=2 ; frozen classifier/allowlist/taxonomy (unchanged)
```
