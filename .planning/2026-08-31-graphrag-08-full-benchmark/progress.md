# Progress — GraphRAG-08 Full Value Benchmark (RE-AUTHORIZATION #2)

## 2026-08-31
- §0 hardening intact (retry markers present); §2/§3 fixture gate PASS (75/60/30/30/12, hash
  a58a6853...143d). Posture clean (sidecar closed, GRAPHRAG_ENABLED false).
- Added additive retry-accounting telemetry (runner.index_attempts + retry_accounting()); policy
  BYTE-IDENTICAL (all 15 index-retry tests + 8 harness pass; ruff+mypy clean).
- Failed run 8c9d83c77d92 preserved separately (FULL_RUN_ATTEMPT=FAILED, VALUE_DECISION_MADE=NO);
  micro-precheck c531cf98a092 excluded from aggregates.
- LAUNCHED authorized full run #2 (all 75 sources w/ bounded retry + 60 queries DEV+HOLDOUT,
  denominator 75). Awaiting result + artifact for the value decision.

## Result — FULL RUN #2 (reauthorization 2) FAILED at indexing; clean cleanup
- run_id 03bc96689656, state FAILED. Failure: `GraphRAG indexing FAILED for source:gr08eb006c36800
  (cause=NON_RETRYABLE, not retryable) — full corpus not indexed`. Source index 00 = S001.
- Hardened retry behaved per policy: read the sidecar failure cause, it did NOT match the (tightened)
  transient-marker allowlist → classified NON_RETRYABLE → FAIL CLOSED → no retry → aborted before any
  query. 75/75 gate correctly prevented partial-corpus evaluation.
- Safety: cleanup + restoration COMPLETE — sidecar started+stopped; temp namespace dropped; temp
  model deleted; LightRAG per-id cleanup ok; normal DB 1→1 unchanged; no graphrag_eval_ residue;
  GRAPHRAG_ENABLED false; fixture hash before==after; dim 1536. No artifact (aborted at FULL_INDEX).
- DIAGNOSTIC UNCERTAINTY (honest): run #1 failed on S002, run #2 on S001 — DIFFERENT sources each run
  → pattern consistent with TRANSIENT provider failures under the 75-source load, NOT source-content
  issues. Run #2's cause was NON_RETRYABLE (error text present but no transient-marker match). The
  content-containment design discards the raw text, so I cannot distinguish (a) a genuinely
  deterministic extraction failure from (b) a transient failure my tightened LOW-1 regex mis-classified.
- Per §12/§78: STOP + cleanup + report. Authorization #2 CONSUMED. NO autonomous re-run; a THIRD
  authorization is required. Value decision NOT made; all *_VALUE_EVIDENCED = NOT_RUN.
- Recommendation (needs review + new authorization, NOT done now): add a CONTENT-SAFE classification
  diagnostic (per failed source: category + error-text-present + length + matched-token-class, never
  raw text) so a future failure's cause is diagnosable; and/or re-balance the transient classifier.
