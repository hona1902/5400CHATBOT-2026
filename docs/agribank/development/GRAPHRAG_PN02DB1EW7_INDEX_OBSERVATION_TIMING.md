# GraphRAG-PN02D-B1-EW7 — Bounded Index-Observation Timing + Index Observability

**Status:** IMPLEMENTED + PROVIDER-FREE VERIFIED — READY FOR INDEPENDENT CODEX EW7 REVIEW #1 (target `D_PASS_CLEAN`). NOT approved, NOT checkpointed, NOT a real B1 result. No commit/tag/push. Zero provider traffic. EXEC #11 NOT authorized.

**Current committed trust anchor (unchanged this turn):** EW6 checkpoint `0baacef33f033482b97434702c47b8bce30b5943` (tag `graphrag-pn02db1ew6-index-conflict-recovery-approved`, obj `12fe9e7c…`). Worktree DIRTY (implementation not yet checkpointed). **Future execution trust expectation is repointed to EW7** — proposed future tag `graphrag-pn02db1ew7-index-observation-timing-approved` (NOT created). These are distinct: HEAD stays at the EW6 commit until a future EW7 checkpoint exists; governance already expects EW7 (so the mint fails closed until that tag is trust-observed).

## Why (EXEC #10 → EF4 → EW7)

EXEC #10 (the first real B1 run on the EW6 checkpoint after INFRA2 restored Docker) embedded all **21/21** corpus sources, then FAILED before the query stage with `index_completeness_below_24_of_24` (`technical_status=FAILED_BEFORE_QUERY`). The **EF4 forensic** (`GRAPH_RAG_PN02DB1_EF4_INDEX_COMPLETENESS_FORENSIC_COMPLETE`) established the root cause (Class B — poll/observation **timing-policy** defect, HIGH confidence):

`MembershipIndexExecutor._poll_track` looped `for _ in range(max_polls=10): await client.status()` with **ZERO inter-poll delay and no wall-clock deadline**. Per membership it did attempt-1 (submit + 10 rapid status calls) then, if still IN_PROGRESS, attempt-2 RESUMED the same track (correct EW6 behavior — no re-POST) + 10 more rapid calls, then failed. The accepted-track observation window was therefore **~20 back-to-back HTTP calls ≈ 1–2s wall-clock**, far shorter than real LightRAG graph-index latency (per-document LLM entity/relation extraction via gpt-4o-mini = many seconds). Memberships were abandoned while still IN_PROGRESS → `<24/24` by **observation exhaustion** — NOT a terminal LightRAG failure, NOT an HTTP 409, NOT a re-POST. The EXEC #8 fatal-409 signature did **not** recur (EW6's fix held); this was a separate blocker.

## Operator timing-policy decision (Option A + E)

- **A — bounded inter-poll delay**, `INDEX_POLL_INTERVAL_SECONDS = 2.0`.
- **E — index observability** (surface the metrics so a future run needs no forensic reconstruction).

**Timing semantics (exact):** no sleep before poll #1; after an IN_PROGRESS status, sleep 2.0s before the next poll; **no** sleep after a terminal PROCESSED/FAILED/TIMEOUT; **no** sleep after the final poll of a window. Poll count (10/attempt) and attempt count (2/op) are UNCHANGED. So a fully-exhausted 10-poll window sleeps exactly **9×2.0s = 18s**, and a fully-exhausted 2-window membership sleeps **18 + 18 = 36s** of deliberate wait (network/runtime latency additional). Rejected here: the wall-clock-deadline redesign (Option B).

## What changed (production)

**`open_notebook/integrations/graphrag/eval/indexlivepn02d.py`** (the primary change; the generic `GraphRAGClient` is untouched):
- New `INDEX_POLL_INTERVAL_SECONDS = 2.0` + `IndexPollSleeper` type. `MembershipIndexExecutor.__init__` takes `poll_interval_seconds` (default 2.0) and an injectable async `sleeper` (default `asyncio.sleep`; async, non-blocking — never `time.sleep`/busy-wait).
- `_poll_track` awaits the sleeper between consecutive IN_PROGRESS polls only (`if i < max_polls - 1`), giving `max_polls - 1` waits per exhausted window.
- **Observability:** a per-membership `_MembershipCounters` threads through `index_membership`/`_one_attempt`/`_poll_track`, counting `document_posts`, `status_polls`, `resume_operations`, `document_reposts_after_accepted_track` and `last_track_status`. `IndexOperationRecord` gains those content-safe fields; `IndexCompletionReport` gains the aggregate metrics (`graph_index_planned/successes/failures/attempts`, `index_membership_attempts`, `document_posts`, `index_status_polls`, `track_resume_operations`, `document_reposts_after_accepted_track`). Counter definitions per §12; `document_reposts_after_accepted_track` is provably 0 (the EW6 machine resumes a live accepted track, never re-POSTs — the counter validates that invariant).

**`open_notebook/integrations/graphrag/eval/cli_live_pn02d.py`:** the structured payload now surfaces `index_metrics` (the `IndexCompletionReport` dict) and `index_membership_records` (the content-safe per-membership records) on both the COMPLETE and FAILED-before-query outcomes — never source text, provider body, headers or secret.

**EW6 state machine intact:** accepted track preserved; retry after acceptance starts with track STATUS (never a second POST); no delete-then-insert; a fresh 409 with no prior accepted track fails closed (`ERR_CONFLICT`); reconciliation identity-scoped. **Frozen scientific envelope UNCHANGED** (`B1_RUN_ENVELOPE_CHANGED=NO`): `MAX_INDEX_ATTEMPTS_PER_OPERATION=2`, `MAX_GRAPH_INDEX_ATTEMPTS=48`, `GRAPH_DELETE_OPERATIONS=1`, GD 26 / query-embed 26 / source-embed 21 / final-answer 0 / concurrency 1/1/1, fixture hash `9ce7df74…6899a6`, run id `pn02db1-fe3efb27-…`. The completeness gate remains 24/24. The deferred 401/404 embedding retry-classification defect is NOT changed.

## Governance repoint EW6 → EW7

- `authmintlivepn02d.py`: `EXPECTED_EW7_CHECKPOINT_TAG = "graphrag-pn02db1ew7-index-observation-timing-approved"`; `_APPROVED_B1_R2_CHECKPOINT = EXPECTED_EW7`; EW6 demoted to HISTORICAL (its constant/tag/commit `0baacef` preserved); historical current-identity pointers repointed to EW7.
- `authb1r2pn02d.py`: imports `EXPECTED_EW7_CHECKPOINT_TAG`; `B1_R2_EXPECTED_CHECKPOINT_TAG = EXPECTED_EW7`; EW6 → HISTORICAL, EW7 → CURRENT.
- Cross-phase governance suites (B0C-B adapters/live, R2, EW1–EW6) updated to `current == EW7`; EW6 added to the retained-historical / substitution-rejection / distinctness sets. `VALID_EW7_TAG_ALONE_CAN_MINT = NO`; the mint fails closed until the exact EW7 annotated tag is trust-observed in real Git (which does not exist yet). EW6 identity (commit `0baacef`, tag) and all earlier historical identities preserved immutable.

## Verification (provider-free)

- NEW `tests/test_graphrag_pn02db1ew7.py`: timing (no sleep before poll #1; exactly 9 waits per exhausted 10-poll window; PROCESSED first poll → 0 waits; terminal FAILED → no extra wait; resume after first window → 1 POST/1 resume/0 reposts; delayed completion within the second window; PROCESSED on the final allowed poll #20; completion after poll #20 → bounded failure; max deliberate wait = 18 waits × 2.0s = 36.0s; normal fast path; fresh 409 fails closed) + observability (aggregate + per-membership metric keys; failure payload retains metrics; content-safe) + full provider-free 24/24 index stage (delayed completion, 24 POSTs, 24 resumes, 0 reposts) + EW7 governance (repoint complete; lifecycle State A/B; EW6/EW5/EW4/EW3/EW2/EW1/PF1/B1-R2/arbitrary cannot substitute; wrong-peel fails closed; exact-tag accepted; tag-alone does not authorize). Tests use a FAKE recording sleeper — `TESTS_USE_REAL_MULTISECOND_WAIT = NO`.
- Cross-phase suites updated to `current == EW7` and pass.
- `ruff` clean; `mypy` 0 new (5 pre-existing in the untouched `concurrency_diag08.py`).

## Explicitly NOT done

- No scientific-envelope change; no poll-count/attempt-count change; no wall-clock-deadline redesign (Option B); no provider/model/dimension change; no embedding retry-classification remediation; no Codex this turn; no commit/tag/push; no EXEC #11; no B2; no production/Ask/GraphRAG-09.

## Next

Independent Codex EW7 **Review #1** (target `D_PASS_CLEAN`) → operator-approved EW7 checkpoint (verify/stage/commit/create exact tag `graphrag-pn02db1ew7-index-observation-timing-approved`/State-A→B validation/push — no further code changes) → fresh operator authorization for EXEC #11. A real B1 result remains `EW7_REAL_PROVIDER_B1_RESULT = NOT_RUN`.
