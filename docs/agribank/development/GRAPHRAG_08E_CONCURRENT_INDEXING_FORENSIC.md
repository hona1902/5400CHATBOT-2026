# GraphRAG-08E — Concurrent Indexing / Load-Interaction Forensic + Diagnostic Harness Gate

**Status:** OFFLINE FORENSIC COMPLETE · DIAGNOSTIC-HARNESS **IMPLEMENTED (offline, eval-only)**
— awaiting independent-review sign-off + checkpoint. **No OpenRouter traffic, no live
concurrency diagnostic executed, no attempt #6, no DEV/HOLDOUT, no change to frozen
concurrency / retry classifier / allowlist / fixture / provider / model / timeout.**

`FORENSIC_RESULT = CONCURRENCY_LOAD_CAUSE_SUPPORTED` · `ROOT_CAUSE_CONFIRMED = NO` ·
`LIVE_DIAGNOSTIC_EXECUTED = NO` · H1/H2/H3 = **UNCONFIRMED**.

> Scope note: the harness design (§4) was derived from the confirmed S001 forensic finding
> and the stated 08E constraints; a prior detailed 08E specification was not available in the
> working session context. The implementation below follows the operator's approved 08E
> build order.

## Implementation (offline, eval-only)

New `open_notebook/integrations/graphrag/eval/concurrency_diag08.py` — pure diagnostic
instrument, imported by nothing in production:

- **Bounded plan + caps** (`ConcurrencyDiagnosticPlan` / `validate_plan`): allowed levels
  `{1,2,4,8}`, ≤4 levels, ≤8 Sources/level (≥ its concurrency), ≤3 reps, ≤64 total
  submissions; any breach fails closed (`DiagnosticPlanError`). `estimate_budget` is static
  (no provider call).
- **Deterministic synthetic selection** (`select_diagnostic_sources`): S001 anchor first,
  then sorted keys; Sources only, never HOLDOUT/queries.
- **Diagnostic taxonomy** (`ErrorFamily` / `classify_error_family`): PROVIDER_RATE_OR_CAPACITY,
  PROVIDER_TIMEOUT_OR_NETWORK, EMPTY_OR_MALFORMED_RESPONSE, PARSE_OR_SCHEMA_FAILURE,
  LIGHTRAG_INTERNAL, UNKNOWN_SAFE — labelling only, cannot change the retry decision.
- **Decision-twin** (`retry_decision` / `characterize_failure`): retryable yes/no is
  DELEGATED to the frozen `index_retry08.is_transient_reason`; a test proves zero divergence.
- **Raw-error containment**: `characterize_failure(text)` uses the raw text transiently and
  returns only content-safe fields (`family`, `retryable`, reason code, length bucket); no
  dataclass retains raw text.
- **Content-free records + aggregation** (`AttemptRecord` / `aggregate`): success/failure
  counts, failure-rate-by-concurrency, family distribution, retryable distribution, latency
  summary — no source/query/chunk/answer text.
- **Conservative interpretation** (`interpret_hypotheses`): H1/H2/H3 verdicts in
  {SUPPORTED, WEAKLY_SUPPORTED, NOT_SUPPORTED, INCONCLUSIVE}; `root_cause_confirmed` is
  hard-wired `False`.
- **Fail-closed live seam** (`run_sweep`): the FUTURE live path — requires
  `authorized_live=True` AND active Option-A isolation (`require_active_isolation`) AND a
  validated plan, and INJECTS the indexer (`index_level_fn`); it makes no provider/sidecar/DB
  call itself. No caller passes `authorized_live=True` in this phase.

Tests: `tests/test_graphrag_08e_concurrency_diag.py` — 41 tests (plan/caps/selection,
decision-twin parity, taxonomy, raw-error non-persistence, aggregation, non-definitive
interpretation, fail-closed live guards, adversarial no-mutation). All provider/network
mocked. GraphRAG regression (flag off) **563 pass / 8 skip / 0 fail**; ruff + targeted mypy
clean; fixture hash unchanged. No production import of eval code.

## 1. Motivating evidence (established, not re-run here)

- **Attempt #5** (`13e59a3edbb8`): all 75 Sources created + vector-embedded; graph indexing
  reached; logical Source **S001** failed at the **TRACK** surface, `DocStatus.FAILED`,
  classified **NON_RETRYABLE** (`TRACK_TEXT_PRESENT_NO_ALLOWLIST_MATCH`, error-text present,
  length bucket 129–256, no transient/non-transient allowlist match); 75/75 gate aborted.
- **S001 clean-slate isolated forensic** (attempt 2, `gr08-cb1882d0de90`): S001 indexed
  **successfully alone** from a reset LightRAG store, first attempt, no failure.
  ⇒ `S001_ISOLATED_RESULT = PASS`, `DETERMINISTIC_SOURCE_SPECIFIC = NO`,
  `CONCURRENCY_LOAD_CAUSE = SUPPORTED`.

The #5 failure is therefore an **interaction effect under concurrent indexing**, not S001's
content. 08E investigates that interaction — offline first.

## 2. Offline forensic of the concurrent-indexing path (verified against source)

1. **Submit-all-up-front.** `runner08._graph_index_with_retry` submits every created Source
   to the sidecar in a loop (`_submit_index_bounded` per canonical id) **before** polling any
   track status (runner08.py:490–494). For the full run this hands **75 documents** to the
   sidecar in immediate succession.
2. **No Open-Notebook-side concurrency control.** `EvalRunConfig08` has no concurrency field;
   there is no `asyncio.Semaphore`/`gather` throttle in the eval indexing path. The ON side
   never limits how many of the 75 docs are extracted in parallel.
3. **Concurrency is entirely sidecar-side, at LightRAG defaults.** The PoC compose
   (`deploy/graphrag-poc/docker-compose.graphrag.yml`) sets **no** `MAX_ASYNC` /
   `MAX_PARALLEL_INSERT` / LLM-concurrency env, and `.env` sets no `GRAPHRAG_POC_*`
   concurrency override — only provider bindings/keys. So LightRAG v1.5.6 applies its
   **default internal extraction concurrency**, issuing many concurrent `openai/gpt-4o-mini`
   calls (and embeddings) to **OpenRouter** while the 75-doc burst drains.
4. **Failure classification path.** A TRACK-time `DocStatus.FAILED` is read once
   (`index_retry08._fetch_failed_reason_ex` → `GET /documents/track_status/{track_id}`),
   matched against the frozen transient allowlist (`_TRANSIENT_MARKERS`: 429 / rate-limit /
   timeout / temporarily unavailable / connection reset|aborted|refused / http 5xx /
   internal server error / bad gateway / gateway timeout / service unavailable / overloaded /
   server is busy / please try again). No match ⇒ `NON_RETRYABLE` ⇒ fail closed ⇒ 75/75 gate
   aborts. This is exactly the #5 outcome (`TRACK_TEXT_PRESENT_NO_ALLOWLIST_MATCH`).

## 3. Hypotheses (to be discriminated by the harness — NOT concluded here)

- **H1 — load-induced transient provider error, allowlist miss (classifier gap).** Under the
  75-way burst, OpenRouter returns a rate/overload/timeout condition on some extraction call;
  LightRAG surfaces it as a `DocStatus.FAILED` whose error **text does not contain** an
  allowlist token (e.g. a provider-specific phrasing, or a LightRAG-wrapped generic
  "extraction failed" message). Result: a genuinely transient, retryable-in-principle failure
  is classified NON_RETRYABLE. If true, the corrective gate is a **classifier/allowlist
  review** (a later, separately-authorized decision — never edited here).
- **H2 — genuinely non-transient failure produced only under contention.** Under load the LLM
  returns an empty/malformed/truncated extraction that LightRAG rejects deterministically
  (parse/schema), i.e. a real non-retryable error that simply does not occur at low load. If
  true, the corrective gate concerns **concurrency limiting** (bounding parallel inserts /
  provider concurrency), again a separately-authorized decision.
- **H3 — LightRAG v1.5.6 behaviour under concurrency** (shared-state/race in the sidecar
  during parallel insert). Lower prior probability; S001 alone is clean.

The frozen classifier cannot itself distinguish H1 from H2 (both present as
`TRACK_TEXT_PRESENT_NO_ALLOWLIST_MATCH` today). Discriminating them requires the ephemeral
error-family characterization the diagnostic harness would provide.

## 4. Proposed eval-only diagnostic harness (DESIGN — approval required before build)

An eval-only module (proposed `open_notebook/integrations/graphrag/eval/concurrency_diag08.py`)
that, **only when later explicitly authorized to run live**, reproduces the load interaction
under Option-A isolation and characterizes the failure — **without** changing any frozen
parameter:

- **Bounded concurrency sweep.** Index a bounded synthetic subset (e.g. K Sources, K ≪ 75) at
  increasing *effective* parallelism, achieved by controlling how many docs are submitted
  before draining (an ON-side submit-batching knob that is a **diagnostic instrument only**,
  never a change to the frozen full-run submit-all policy), to find the concurrency level at
  which TRACK failures first appear.
- **Content-safe failure characterization.** Reuse the frozen 08B `FailureDiagnostic`
  (surface / reason-code / transient+non-transient class enums / length bucket / http class)
  for every failure. Under a **narrow, separately-granted ephemeral authorization** (like the
  S001 forensic), read each raw TRACK error **once in-process** to derive coarse error-family
  flags (rate/timeout/overload vs parse/empty/schema vs auth vs unknown) — never persisting,
  logging, hashing, quoting, or committing the raw text.
- **Output:** a content-free report — failure rate vs concurrency, and the coarse error-family
  distribution — sufficient to decide H1 vs H2 vs H3 and whether the classifier allowlist has
  a real gap. It **proposes** no code change; any allowlist/concurrency change is a later gate.
- **Isolation + cleanup:** identical to the S001 forensic (fresh Option-A temp namespace, new
  ids, per-ID LightRAG ownership, temp model, full cleanup/restore, sidecar down after,
  `GRAPHRAG_ENABLED` restored, fixture unchanged).

**This harness is not built or run in this phase.** This document is the forensic + design; a
follow-up implementation phase (offline build + tests + independent review) and, separately, a
live-diagnostic authorization would be required.

## 5. Frozen / non-goals (unchanged)

No edit to: transient retry classifier or allowlist, `MAX_INDEX_ATTEMPTS_PER_SOURCE=2`,
benchmark concurrency policy (submit-all-up-front for the real run), fixture
`a58a68535c345e18f0263904f818e4e2068a164056408665d8bb9233eceb143d`, Source text, provider
(OpenRouter), models (`openai/text-embedding-3-small` 1536 / `openai/gpt-4o-mini`), LightRAG
v1.5.6, or the 120s sidecar health timeout. No production code change; production imports no
eval code. No DEV/HOLDOUT; no full attempt #6; no value decision
(`GRAPH_RETRIEVAL_VALUE_EVIDENCED` / `STRUCTURED_EVIDENCE_VALUE_EVIDENCED` stay `NOT_RUN`).

## 6. Gate flags

```
GRAPH_RAG_08E_CONCURRENCY_FORENSIC              = COMPLETE (offline)
CONCURRENT_SUBMIT_PATTERN                        = SUBMIT_ALL_UP_FRONT (verified)
ON_SIDE_CONCURRENCY_LIMIT                        = NONE (verified)
SIDECAR_CONCURRENCY                              = LIGHTRAG_DEFAULT (no compose/.env override)
CLASSIFIER_ALLOWLIST_GAP_HYPOTHESIS (H1)         = PLAUSIBLE — UNCONFIRMED (needs harness)
LOAD_INDUCED_NONTRANSIENT_HYPOTHESIS (H2)        = PLAUSIBLE — UNCONFIRMED (needs harness)
LIGHTRAG_CONCURRENCY_BEHAVIOUR_HYPOTHESIS (H3)   = LOWER_PRIOR — UNCONFIRMED
DIAGNOSTIC_HARNESS_IMPLEMENTED                    = YES (offline, eval-only)
LIVE_DIAGNOSTIC_AUTHORIZATION_ATTEMPT_1           = BLOCKED_BEFORE_PROVIDER (cell isolation undefined; see GRAPHRAG_08E1_CELL_ISOLATION_HARDENING.md)
FROZEN_CLASSIFIER_REUSED (decision-twin)          = YES
LIVE_CONCURRENCY_DIAGNOSTIC_RUN                   = NO
ALLOWLIST_OR_CONCURRENCY_CHANGED                 = NO
FULL_EXECUTION_AUTHORIZED / ATTEMPT_6            = NO
GRAPH_RAG_08E_READY_FOR_LIVE_DIAGNOSTIC_AUTH      = pending independent-review sign-off
```
