# Task Plan — GraphRAG-08 Index-Retry Hardening (offline, pre-reauthorization)

## Goal
Add a NARROW, eval-only bounded transient-retry policy for individual GraphRAG index
failures + a hard 100%-corpus gate, so a re-authorized full run tolerates a single transient
provider failure without ever evaluating a partial corpus. Offline implementation + review
only. NO provider traffic, NO rerun, NO production change, NO fixture change, NO commit unless
instructed.

## Contract (task §1-§8)
- 75/75 required before ANY DEV/HOLDOUT query; partial corpus NEVER evaluates. No denominator/
  GT change, no Source replacement/drop.
- MAX_INDEX_ATTEMPTS_PER_SOURCE = 2 (1 + 1). No 3rd attempt, no retry-to-pass, no new retry loop.
- Retry ONLY clearly-transient failures. Fail closed when the cause is unclassifiable.
- Retry uses the approved reindex path (delete-then-insert), same Source/provider/model/config.
- Failed run 8c9d83c77d92 stays recorded as FULL_RUN_ATTEMPT=FAILED, VALUE_DECISION_MADE=NO.

## Design
- Classification signals (production client exposes no per-doc cause; §4 fail-closed):
  - Submit-time: typed exception. Transient = GraphRAGUnavailableError/ServerError/ConflictError.
    All else (4xx/auth/schema/validation/unknown) = non-retryable.
  - Track-time DocStatus.FAILED: eval-only direct read of sidecar /documents/track_status error
    text (like the GD seam), matched to a transient-marker allowlist; absent/ambiguous/non-match
    = fail closed. Raw text discarded; only a coarse category returned (content containment).
- Single per-source attempt counter spans submit + track surfaces; cap 2.
- 100%-corpus gate: _graph_index_with_retry returns only when ALL PROCESSED; _assert_complete_corpus
  re-checks counts before run(). create_and_index order = create→vector→graph(retry)→gate→(run later).

## Files (eval-only; production imports none)
- NEW index_retry08.py — classify_submit_exception / is_transient_reason / classify_failed_track
  (+ eval-only _fetch_failed_reason).
- runner08.py — EvalRunConfig08.max_index_attempts_per_source; __init__ graphrag_config; replace
  _graph_index_all+_await_graph_ready with _submit_index_bounded + _graph_index_with_retry +
  _assert_complete_corpus. (allow_holdout + report08 by_split/by_class already added this session.)
- precheck08.py — pass graphrag_config=load_config() to both runners; run_full_benchmark.
- NEW tests/test_graphrag_08_index_retry.py — 13 tests (A-L).

## Progress
### Phase 1 — Forensic (classification signal) — complete
### Phase 2 — Implement index_retry08 + runner integration — complete
### Phase 3 — Tests (A-L) — complete (13 pass)
### Phase 4 — Ruff + mypy + graphrag regression — running
### Phase 5 — Independent adversarial review — running
### Phase 6 — Docs + final report — pending. NO commit unless instructed.

## Decisions
| # | Decision | Rationale |
|---|----------|-----------|
| 1 | Fail closed on unclassifiable track failures | §4; production surface exposes no cause |
| 2 | Eval-only sidecar error read (own httpx) | classify without production change (§10) |
| 3 | Single attempt counter across submit+track surfaces | cap 2 total, never a 3rd attempt (§2) |
| 4 | Retry = delete-then-insert reindex, same Source | approved reindex path (§5) |
