# Task Plan — GraphRAG-08B Failure-Classification Observability Hardening

## Goal
Make a future GraphRAG index failure DIAGNOSABLE with CONTENT-SAFE telemetry, WITHOUT changing
the retry decision, allowlist, max-attempts, concurrency, provider/model, or fixture. Offline /
eval-only. No provider traffic, no sidecar, no rerun, no commit.

## Hard invariants (task §3/§31/§46)
CLASSIFIER_SEMANTICS_CHANGED=NO · ALLOWLIST_CHANGED=NO · MAX_INDEX_ATTEMPTS=2 · CONCURRENCY_CHANGED
=NO · PROVIDER/MODEL_CHANGED=NO · FIXTURE_CHANGED=NO (hash a58a6853…143d). Raw provider/LightRAG
error text NEVER persists (manifest/artifact/report/logs/exception). Unknown/ambiguous = fail closed.

## Forensic (done)
- Concurrency (§17): compose sets NO MAX_ASYNC/MAX_PARALLEL → LightRAG defaults. ON submits ALL
  75 sources up-front (runner loop), then polls; LightRAG processes extraction with its own
  internal concurrency → overlapping OpenRouter calls. NOT changed.
- Failure surface (§18): track-time GET /documents/track_status exposes per-doc error_msg TEXT
  (no HTTP code). CAN_HTTP_STATUS_DIRECT=NO(track)/PARTIAL(submit via typed exc);
  CAN_PROVIDER_ERROR_TYPE=PARTIAL(text only); CAN_FAILURE_STAGE=PARTIAL(submit vs track surface).

## Design (observability only; decision functions UNCHANGED)
- index_retry08: KEEP classify_submit_exception / is_transient_reason / classify_failed_track as
  the frozen DECISION. ADD content-safe diagnostic layer:
  - coarse enums (TransientClass, NonTransientClass), reason codes, length buckets;
  - `_TRANSIENT_CLASS_PATTERNS` (diagnostic mapping of the SAME tokens) + transient_match_classes;
  - FailureDiagnostic dataclass (content-free); diagnose_submit_exception / diagnose_failed_track
    (read raw → classify → build diagnostic → discard raw → return diagnostic only).
  - retry decision inside diagnose == the frozen classify_* result (tested identical §29).
- runner08: collect per-source index_diagnostics + failed_source_ids during indexing; extend
  retry_accounting (add retry_exhausted, non_retryable); index_telemetry() content-free.
- precheck08: capture telemetry into state even on abort; failure-first content-free artifact
  written atomically BEFORE cleanup (§10/§12/§13); RUN_VALIDITY=FAILED, DEV/HOLDOUT=0.

## Phases
1. Forensic — complete. 2. index_retry08 diagnostic layer — in_progress. 3. runner telemetry +
precheck failure-first persistence. 4. Tests (§20-§29). 5. Regression + ruff + mypy.
6. Independent review (security-leak + methodology-frozen). 7. Docs (GRAPHRAG_08B + CURRENT_PHASE)
+ report. NO commit.

## Historical runs (preserve, do not reinterpret)
c531cf98a092 micro PASS · 8c9d83c77d92 full#1 FAILED_BEFORE_QUERY · 03bc96689656 full#2
FAILED_BEFORE_QUERY (S001, NON_RETRYABLE, attempt 1, DEV/HOLDOUT 0, VALUE_DECISION_MADE=NO).
Known for run#2: ERROR_TEXT_PRESENT=YES, TRANSIENT_ALLOWLIST_MATCH=NO, CLASSIFICATION=NON_RETRYABLE
(do NOT invent which token).
