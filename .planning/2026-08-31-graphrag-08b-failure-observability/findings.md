# Findings — GraphRAG-08B Failure Observability

## Forensic
- Concurrency: sidecar compose sets no MAX_ASYNC/MAX_PARALLEL → LightRAG defaults. ON submits all
  75 up-front then polls; LightRAG extraction concurrency overlaps OpenRouter calls. NOT changed.
- Failure surface: track-time exposes per-doc error_msg TEXT only (no HTTP code); submit-time maps
  to typed exceptions (4XX/5XX partial). CAN_HTTP_STATUS_DIRECT=PARTIAL; CAN_PROVIDER_ERROR_TYPE=
  PARTIAL; CAN_FAILURE_STAGE=PARTIAL.

## Design (decision UNCHANGED)
- Kept classify_submit_exception / is_transient_reason / classify_failed_track as the frozen
  decision. classify_failed_track now routes through _fetch_failed_reason_ex (present/absent/
  unreadable) but the decision mapping is identical (PRESENT+transient→TRANSIENT; else NON_RETRYABLE;
  ABSENT/UNREADABLE→UNKNOWN). 15 retry tests pass unchanged.
- Added content-safe FailureDiagnostic + diagnose_submit_exception/diagnose_failed_track; coarse
  transient/non-transient class enums; length buckets; reason codes. Raw text read transiently,
  discarded; never in as_dict/telemetry/artifact/logs. No raw-text hashing (§19).
- runner: index_diagnostics, failed_source_ids, index_attempts, retry_accounting (+retry_exhausted,
  non_retryable), index_telemetry, _logical_id. runner now records diagnostics on submit+track fail.
- precheck: _atomic_write_json (temp+os.replace), _capture_index_telemetry, _write_failure_telemetry
  (content-free, written BEFORE cleanup, wrapped so it can't block cleanup), PrecheckState telemetry
  fields, run_validity.

## Verification
- 14 08B diagnostic tests pass; 15 index-retry tests pass (decision unchanged); GraphRAG regression
  (flag off) 486 pass / 8 skip / 0 fail; ruff + mypy clean; fixture hash unchanged.
- §32 distinguishability: YES — reason codes distinguish TYPED_TRANSIENT_EXCEPTION (A),
  TRACK_TRANSIENT_ALLOWLIST_MATCH (B), TRACK_TEXT_PRESENT_NO_ALLOWLIST_MATCH (C), TRACK_TEXT_ABSENT
  (D), TRACK_TEXT_UNREADABLE (E) — all without raw text.

## Independent review — outcome (fill on return)
(pending)

## Independent review — outcome + resolution
Verdict: PRIMARY concern (raw diagnostic error-text leak) CLEAN. Retry semantics unchanged CLEAN
(decision twins agree; 18,917-string fuzz → 0 class/decision divergence). Telemetry correct,
cleanup independent, production boundary clean, §32 distinguishability YES for all 5 cases. No
HIGH/MEDIUM. 2 LOW + 2 test gaps — all FIXED:
- LOW-1 (real): the two outer `{exc}` handlers stringified str(exc) → could carry non-08B provider
  text (e.g. embedding error_message, sidecar health detail) into st.failures (in-memory only, not
  persisted to artifact/manifest). FIXED: dropped `: {exc}`, keep type name only.
- LOW-2: _atomic_write_json orphaned a .tmp on serialization failure. FIXED: try/finally unlink.
- Gap 1: added end-to-end test driving REAL secret error text through diagnose_failed_track into
  runner.index_telemetry() and asserting containment.
- Gap 2: added consistency test that classify_failed_track and diagnose_failed_track return the
  same classification for identical fetch results (PRESENT-transient/nontransient/ABSENT/UNREADABLE).
Post-fix: 16 08B tests + 15 retry tests pass; regression 488 pass/8 skip/0 fail; ruff+mypy clean;
fixture unchanged.
