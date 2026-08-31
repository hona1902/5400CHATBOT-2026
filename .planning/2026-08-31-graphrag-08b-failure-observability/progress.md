# Progress — GraphRAG-08B Failure Observability

## 2026-08-31
- Forensic (concurrency + failure surface) documented; nothing changed.
- Implemented content-safe diagnostic layer in index_retry08 (FailureDiagnostic, diagnose_* ,
  coarse class enums, length buckets, reason codes, _fetch_failed_reason_ex present/absent/
  unreadable). Decision functions unchanged.
- runner08: per-failure diagnostics + telemetry surviving pre-ANALYZE abort (index_telemetry,
  retry_accounting +retry_exhausted/non_retryable, failed_source_ids, _logical_id).
- precheck08: atomic write + failure-first content-free telemetry before cleanup + state fields.
- Tests: +14 08B diagnostics; 15 index-retry unchanged; regression 486 pass/8 skip/0 fail; ruff+mypy
  clean; fixture hash a58a6853...143d unchanged.
- Docs: GRAPHRAG_08B_FAILURE_CLASSIFICATION_OBSERVABILITY.md.
- Independent review (security-leak + methodology-frozen + §32 distinguishability) dispatched.
- Boundaries: no provider traffic, no sidecar, GRAPHRAG_ENABLED false, no fixture/allowlist/
  concurrency/policy change, no production import, no commit.
