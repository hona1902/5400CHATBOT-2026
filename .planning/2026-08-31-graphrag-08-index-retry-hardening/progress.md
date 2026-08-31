# Progress — GraphRAG-08 Index-Retry Hardening

## 2026-08-31
- Implemented eval-only index_retry08 (classify_submit_exception, is_transient_reason,
  classify_failed_track w/ eval-only sidecar error read + content containment) + runner08
  bounded-retry (max 2 attempts/source, single counter across submit+track surfaces, reindex
  = delete-then-insert, fail-closed on UNKNOWN/NON_RETRYABLE) + _assert_complete_corpus (100%
  gate) + graphrag_config param. precheck08 passes graphrag_config to both runners.
- Offline verification GREEN: 13 index-retry tests pass (A-L); 46 core 08/08A tests pass;
  GraphRAG regression (flag off) 470 pass / 8 skip / 0 fail; ruff clean; mypy Success (4 files).
- Independent adversarial review dispatched (retry-to-pass bias / >2 attempts / partial-corpus
  slip / foreign mutation / content leak / production boundary / test adequacy).
- No provider traffic, no sidecar, no rerun, no production change, no fixture change, no commit.
- Failed run 8c9d83c77d92 preserved: FULL_RUN_ATTEMPT=FAILED, VALUE_DECISION_MADE=NO.

## Independent review — outcome + resolution
Verdict: retry-accounting + hard-gate logic CORRECT for the contract (A/C/D/E/F clean — single
shared counter caps at 2 across both surfaces; UNKNOWN/NON_RETRYABLE never retried; partial
corpus can't reach run(); no foreign mutation / text-provider-model change; no raw-error leak;
no production import; graph stays unordered). 3 items, all FIXED:
- MEDIUM-1 (test gap): added 2 cross-surface tests — submit-retry-then-track-fail (2 attempts,
  no 3rd) and submit-transient-twice-aborts-at-cap — locking the shared-counter invariant a
  two-counter regression would otherwise pass.
- LOW-1: tightened transient-marker regex (5xx only in explicit http/status context; specific
  phrases; dropped bare \b5\d\d\b / bare "server error"/"unavailable"/"try again"/"temporar").
- LOW-2: _assert_complete_corpus now independently asserts processed_ids == created_ids (a new
  PROCESSED-tracking set), not merely submitted track_ids.
Post-fix: 15 index-retry tests pass; 46 core 08/08A tests pass; ruff + mypy clean.
