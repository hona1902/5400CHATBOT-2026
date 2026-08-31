# GraphRAG-08B — Failure-Classification Observability Hardening

**Status:** OFFLINE / EVALUATION-ONLY diagnostic hardening. No provider traffic, no sidecar,
no benchmark run, no retry-policy change, no fixture change.
**Date:** 2026-08-31
**Builds on:** the index-retry hardening (uncommitted, reviewed) and the two failed full-run
attempts. Fixture frozen at `a58a68535c345e18f0263904f818e4e2068a164056408665d8bb9233eceb143d`.

> 08B improves **observability only**: it makes a future GraphRAG index failure diagnosable
> **without** persisting raw provider/LightRAG error text, and **without** changing the retry
> decision, allowlist, max-attempts, concurrency, provider, model, or fixture.

---

## 1. Why this phase

Two authorized full-run attempts failed at Graph indexing/extraction, on **different** sources:

| Run | run_id | status | failing source | classification |
|---|---|---|---|---|
| micro-precheck | c531cf98a092 | PASS | — | — |
| full attempt #1 | 8c9d83c77d92 | FAILED_BEFORE_QUERY | S002 (index 01) | (pre-retry: abort on first FAILED) |
| full reauth #2 | 03bc96689656 | FAILED_BEFORE_QUERY | S001 (index 00) | NON_RETRYABLE (attempt 1) |

Both failures were at graph extraction, on different sources — a pattern more consistent with
**transient provider failures under the 75-source load** than with any source's content. But
run #2 was classified NON_RETRYABLE (an error text was present but matched no transient marker),
and the content-containment rule discarded the raw text, leaving a diagnostic gap:

> **A. genuine deterministic/non-retryable extraction failure** vs
> **B. a transient failure whose wording is not covered by the current narrow allowlist.**

08B closes the gap by emitting **content-safe** diagnostics — never the raw text.

---

## 2. Frozen retry policy (UNCHANGED)

`MAX_INDEX_ATTEMPTS_PER_SOURCE = 2`; the transient allowlist regex (`_TRANSIENT_MARKERS`) and the
decision functions `classify_submit_exception` / `is_transient_reason` / `classify_failed_track`
are **byte-identical in behaviour** (15 index-retry tests pass unchanged; a frozen-semantics
regression test locks the decision on a battery of inputs). `CLASSIFIER_SEMANTICS_CHANGED = NO`,
`ALLOWLIST_CHANGED = NO`, `CONCURRENCY_CHANGED = NO`.

---

## 3. Content-safe diagnostic contract

`index_retry08.FailureDiagnostic` — every field is content-free; the raw error text never appears:

| field | meaning |
|---|---|
| failure_surface | SUBMIT / TRACK |
| attempt_number | 1..2 |
| classification | TRANSIENT / NON_RETRYABLE / UNKNOWN (== frozen decision) |
| classification_reason_code | TYPED_TRANSIENT_EXCEPTION / TYPED_NON_RETRYABLE_EXCEPTION / UNKNOWN_EXCEPTION_FAIL_CLOSED / TRACK_TRANSIENT_ALLOWLIST_MATCH / TRACK_TEXT_PRESENT_NO_ALLOWLIST_MATCH / TRACK_TEXT_ABSENT / TRACK_TEXT_UNREADABLE |
| retry_allowed / retry_consumed | booleans |
| error_text_present | bool |
| error_text_length_bucket | EMPTY / 1_64 / 65_128 / 129_256 / 257_512 / 513_1024 / GT_1024 (never exact length) |
| matched_transient_classes | coarse enums (RATE_LIMIT, TIMEOUT, HTTP_5XX, …) |
| matched_non_transient_classes | coarse enums (AUTH, REQUEST_4XX, CONTENT_PARSE, SCHEMA, VALIDATION) |
| http_status_class | 4XX / 5XX / null (submit-time typed exceptions only) |
| exception_type | class name only (never the exception message) |
| logical_source_id / canonical_source_id | ids only |

**Raw-text lifecycle (containment):** `read raw (transient local in _fetch_failed_reason_ex) →
classify → build content-free FailureDiagnostic → discard raw → persist diagnostic only`. The raw
text and the exception message never enter the diagnostic, telemetry, artifact, manifest, logs, or
any persisted exception message. Per §19, **no hash/fingerprint of raw text** is stored — only
coarse categories.

**Class-mapping consistency:** `_TRANSIENT_CLASS_PATTERNS` (diagnostic labels) is kept consistent
with the frozen `is_transient_reason` decision by a test asserting, over a battery, that a
non-empty class match holds **iff** the decision is transient — so the labels can never diverge
from the frozen semantics.

---

## 4. Telemetry that survives a pre-ANALYZE abort

The prior gap (retry accounting only surfaced in ANALYZE) is fixed. `runner.index_telemetry()`
reads only counters populated during indexing, and the full-run orchestrator writes a
**content-free `failure_telemetry.json` atomically BEFORE destructive cleanup** on any indexing
abort. A failed-before-query run now reports: `sources_indexed_first_attempt`, `sources_retried`,
`retry_succeeded`, `retry_exhausted`, `non_retryable_failures`, `max_attempts_observed`,
`failed_logical_ids`, `per_source_attempts`, and the per-failure `failure_diagnostics` — with
`RUN_VALIDITY = FAILED`, `DEV/HOLDOUT = 0`, and no raw content. The failure-telemetry write is
wrapped so it can never block cleanup/restoration; writes are crash-safe (temp file + atomic
replace).

---

## 5. Concurrency + failure-surface forensic (documented; NOT changed)

- `FULL_RUN_GRAPH_INDEX_CONCURRENCY`: Open Notebook submits all 75 Sources up-front (sequential
  in-process `index_source` calls), then polls; LightRAG then processes extraction with its **own
  default internal concurrency** (the sidecar compose sets no `MAX_ASYNC`/`MAX_PARALLEL_INSERT`),
  so OpenRouter extraction requests overlap. **Not changed in 08B** (§16/§34 — a concurrency
  change needs separate evidence + authorization).
- `CAN_HTTP_STATUS_BE_OBSERVED_DIRECTLY = PARTIAL` — submit-time typed exceptions carry 4XX/5XX;
  track-time `DocStatus.FAILED` exposes only an `error_msg` text (no HTTP code).
- `CAN_PROVIDER_ERROR_TYPE_BE_OBSERVED = PARTIAL` — via the error_msg text only, not structured.
- `CAN_FAILURE_STAGE_BE_OBSERVED = PARTIAL` — SUBMIT vs TRACK surface is observable; the
  extraction sub-stage is only inferable from the (discarded) error text.

---

## 6. Historical runs (recorded, not reinterpreted)

The two failed runs and the micro-precheck remain separate historical records; they are **not**
benchmark value results and are not aggregated. For run #2 the only known facts are retained:
`ERROR_TEXT_PRESENT = YES`, `TRANSIENT_ALLOWLIST_MATCH = NO`, `CLASSIFICATION = NON_RETRYABLE` —
the specific unseen token is **not** invented.

---

## 7. Future decision rules (documented; require separate review + authorization)

- **Allowlist:** a change is permitted only after content-safe evidence (the new diagnostics)
  shows a specific recurring failure class is operationally transient and currently misclassified.
  Requires separate review, authorization, and tests. Not done here.
- **Concurrency:** not lowered because two runs failed; a change alters the full-run execution
  profile and requires separate evidence/review.

---

## 8. Boundaries honored

Eval/test scope only; production imports no eval code; `client.query()`, `vector_search`, Ask,
Chat, lifecycle, and migrations UNCHANGED; no fixture edit; no provider traffic; sidecar not
started; `GRAPHRAG_ENABLED` false. Retry policy/allowlist/max-attempts/concurrency unchanged.
No commit/tag/push in this gate (review precedes checkpoint).
