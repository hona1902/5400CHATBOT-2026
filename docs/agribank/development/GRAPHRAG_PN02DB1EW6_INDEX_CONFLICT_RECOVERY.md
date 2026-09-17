# GraphRAG-PN02D-B1-EW6 — LightRAG Index 409-Conflict Recovery (non-destructive track resume)

**Status:** IMPLEMENTED + PROVIDER-FREE VERIFIED — Codex EW6 Review #1 = `REMEDIATION_REQUIRED` (0 HIGH / 1 MEDIUM / 1 LOW; all executable invariants PASS), Remediation #1 (doc/comment/prose purity only) COMPLETE — READY FOR INDEPENDENT CODEX EW6 RE-REVIEW #2 (target `D_PASS_CLEAN`; not predeclared). NOT approved, NOT checkpointed, NOT a real B1 result. No commit/tag/push. Zero provider traffic. EXEC #9 NOT authorized.

**Baseline HEAD (unchanged this turn):** `8b0325a75d442976939e22f5a62e11bcf2f6461d` (EW5 tag `graphrag-pn02db1ew5-provider-error-observability-approved`). Worktree DIRTY (implementation not yet checkpointed). Future proposed EW6 tag: `graphrag-pn02db1ew6-index-conflict-recovery-approved` (NOT created).

## Why (EXEC #8 → EF3 → EW6)

EXEC #8 (the first real B1 run on the EW5 checkpoint, with the operator's replaced valid OpenRouter key) embedded all **21/21** corpus sources successfully, then failed at the corpus→indexing transition with `GraphRAGConflictError` (LightRAG HTTP **409**, "document for this file_source already exists"), which escaped **uncaught** to the driver top level and fatally blocked the run (`B1_SCIENTIFIC_RESULT=INCOMPLETE`).

The **EF3 forensic** (`GRAPH_RAG_PN02DB1_EF3_CONFLICT_FORENSIC_COMPLETE`) and the **mandatory EW6 provider-free reproduction** established the exact mechanism (Class A production defect on the eval B1 index path):

1. `MembershipIndexExecutor._one_attempt` submits `POST /documents/text` (insert-only; `client.index_document`) — attempt 1 is **ACCEPTED** and LightRAG issues a `track_id` (acceptance ≠ completion).
2. The bounded poll (`max_polls=10`, no inter-poll sleep) exhausts while LightRAG is still indexing → the executor classifies the attempt **retryable**.
3. The retry **re-POSTs the same `file_source`** — LightRAG returns **409** (the document from attempt 1 already exists), raised as `GraphRAGConflictError`.
4. Neither `RealPN02IndexClient.submit` nor `_one_attempt` catches it (`index_retry08` classifies 409 retryable, but that classifier is not on this submit path) → it escapes uncaught to `driverpn02d.run` = fatal.

Reproduction (real `MembershipIndexExecutor` + a fake client): **`SUBMIT_CALLS=2, STATUS_CALLS=10, RESULT=CONFLICT_ESCAPED_UNCAUGHT`**; budget `GRAPH_INDEX_ATTEMPT spent=2/48`. So the 409 deterministically arrives on **attempt 2**, after attempt 1's accepted POST — i.e. the **primary defect is loss of accepted-operation continuity** (a poll-window exhaustion wrongly re-POSTed an already-accepted document); the 409 is the secondary symptom.

## Why Option B (delete-then-insert) was rejected

The originally-preferred §7 recovery (catch 409 → delete the conflicting document → re-POST) is **infeasible within the frozen scientific envelope** and was **explicitly rejected by the operator**:

- The conflicted op has already consumed **both** of its `MAX_INDEX_ATTEMPTS_PER_OPERATION = 2` attempts (attempt 1 accepted, attempt 2 = the 409) by the time recovery would run, so a recovery re-POST needs a **3rd** attempt — which breaks `MAX_GRAPH_INDEX_ATTEMPTS = 48 = 24 × 2` (enforced by `workloadpn02.WorkloadLedger.validate()`).
- A recovery DELETE has no budget: `GRAPH_DELETE_OPERATIONS = 1` is reserved for the membership-removal stage; an index-recovery delete would exceed cap 1 or require a new budget.

Either path changes the frozen benchmark (`B1_RUN_ENVELOPE_CHANGED` would be YES), which is forbidden. This contradiction was surfaced (`GRAPH_RAG_PN02DB1EW6_IMPLEMENTATION_BLOCKED`) and the operator authorized **Option A** and rejected Option B.

## What changed (production) — Option A

**`open_notebook/integrations/graphrag/eval/indexlivepn02d.py`** (the ONLY production change; the generic `GraphRAGClient` is untouched):

- The per-membership retry loop tracks an `accepted_track_id`. Once a submit is accepted and LightRAG issues a track, a subsequent bounded attempt **RESUMES/observes that exact track** (`_poll_track`) instead of re-POSTing. A re-POST after an accepted track can no longer happen (`RETRY_REPOST_AFTER_ACCEPTED_TRACK_ID = NO`).
- `_one_attempt(..., resume_track_id=...)`: with `resume_track_id` set it polls that track (no submit); otherwise it does a fresh submit then polls. It now returns `(outcome, error_category, retryable, accepted_track_id, resumable)`. Poll-window exhaustion / TIMEOUT (still processing) returns `resumable=True` carrying the SAME track; a terminal FAILED returns `resumable=False` (pre-existing transient retry semantics preserved).
- **409 defense-in-depth (fail closed):** a `GraphRAGConflictError` on a FRESH submit (no prior accepted track for this operation) is caught and mapped to a non-retryable `FAILED_INDEX_SUBMIT` with the new content-safe `ERR_CONFLICT` category — never delete, guess, adopt an unrelated document, or mark indexing complete (`BLIND_409_AS_SUCCESS = NO`). In the identity-proven situation (a prior accepted track exists) the loop **resumes** that track, so the 409 is never even raised.

**Identity scoping (§9/§23/§24):** `accepted_track_id` lives only in the single `index_membership` call's local scope, bound to THAT membership's route-bound client. Reconciliation therefore can never cross a cell/workspace (`CROSS_CELL_RECONCILIATION_POSSIBLE = NO`) or a source (`CROSS_SOURCE_RECONCILIATION_POSSIBLE = NO`).

**Boundedness (§5/§26):** a resumed track is bounded by `max_polls` per attempt AND by `max_attempts` per operation (2) — a track that stays IN_PROGRESS terminates finitely (reproduced: 20 status observations, 1 POST). No delete, no extra attempt, no unbounded loop.

**Frozen envelope UNCHANGED** (`B1_RUN_ENVELOPE_CHANGED = NO`): `MAX_INDEX_ATTEMPTS_PER_OPERATION=2`, `MAX_GRAPH_INDEX_ATTEMPTS=48`, `GRAPH_DELETE_OPERATIONS=1`, GD 26 / query-embed 26 / source-embed 21 / final-answer 0 / concurrency 1/1/1 — all untouched. Option A changes only what a retry attempt *does* (observe the accepted track's status instead of re-POSTing), not how many attempts an operation may spend: a resume iteration still reserves its own `GRAPH_INDEX_ATTEMPT` and is bounded by the same `MAX_INDEX_ATTEMPTS_PER_OPERATION = 2` / `MAX_GRAPH_INDEX_ATTEMPTS = 48` caps. POST and status polling are distinct operations; no second POST occurs merely because the first bounded poll window exhausted. The separate 401/404 embedding retry-classification defect remains **deferred** (not changed).

## Governance repoint EW5 → EW6

Because EW6 changes committed source after EW5, the current approved-checkpoint expectation was repointed **now** (so a future checkpoint turn needs no unreviewed source change):

- `authmintlivepn02d.py`: `EXPECTED_EW6_CHECKPOINT_TAG = "graphrag-pn02db1ew6-index-conflict-recovery-approved"`; `_APPROVED_B1_R2_CHECKPOINT = EXPECTED_EW6_CHECKPOINT_TAG`; EW5 demoted to HISTORICAL (its constant/tag/commit `8b0325a` preserved); the historical current-identity pointers repointed to EW6.
- `authb1r2pn02d.py`: `B1_R2_EXPECTED_CHECKPOINT_TAG = EXPECTED_EW6_CHECKPOINT_TAG`; EW5 → HISTORICAL, EW6 → CURRENT.
- Cross-phase governance suites (B0C-B adapters/live, R2, EW1–EW5) updated to `current == EW6`; EW5 added to the retained-historical / substitution-rejection sets. `VALID_EW6_TAG_ALONE_CAN_MINT = NO`; the mint fails closed until the exact EW6 annotated tag is trust-observed in real Git (which does not exist yet). EW5 identity (commit `8b0325a`, tag) preserved immutable.

## Verification (provider-free)

- NEW `tests/test_graphrag_pn02db1ew6.py`: recovery state-machine (no re-POST after accepted track; DOCUMENT_POST_CALLS=1; track resume→success; track_id preserved across the retry boundary; identity-proven 409 reconcile-by-resume; 409-without-prior-track fails closed; cross-op cannot borrow another op's track; terminal-failure fails closed; bounded IN_PROGRESS; normal fast path; 21-sources/24-memberships; full 24/24 index stage with shared sources into different cells; index_all does not fatally escape) + EW6 governance (repoint complete; lifecycle State A/B; EW5/EW4/EW3/EW2/EW1/PF1/B1-R2/arbitrary cannot substitute; wrong-peel fails closed; exact-tag accepted; tag-alone does not authorize).
- Cross-phase governance suites updated to `current == EW6` and pass.
- `ruff` clean; `mypy` 0 new (5 pre-existing errors remain in the untouched `concurrency_diag08.py`).

## Explicitly NOT done

- No delete-then-insert (Option B); no scientific-envelope change; no provider/model/dimension change; no embedding retry-classification remediation; no Codex this turn; no commit/tag/push; no EXEC #9; no B2; no production/Ask/GraphRAG-09.

## Review lineage

- **Codex EW6 Review #1** (auth restored after two auth-expired blocks): Decision **B = `REMEDIATION_REQUIRED`** — 0 HIGH / 1 MEDIUM (M1: stale "EW5 is the current successor" prose left in module docstrings/comments + cross-phase test docstrings after the EW5→EW6 governance repoint) / 1 LOW (L1: the attempt-accounting comment could read as if a resumed attempt is uncounted). All executable invariants PASS; §3 no-prior-409 and §4 attempt-accounting audits confirmed against source. No HIGH ever.
- **Remediation #1** (doc/comment/prose purity only — this pass): full-surface idiom sweep fixing M1 (all "EW5 successor/current" current-state prose repointed to EW6, historical EW5 references preserved) and L1 (attempt-accounting wording clarified in `indexlivepn02d.py` + this doc: a resume still reserves its own attempt; Option A changes what an attempt does, not the count). No executable / governance-constant / test-assertion / parametrization / fixture change. Re-grep to 0 stale current-state hits.

## Next

Independent Codex EW6 **Re-Review #2** (target `D_PASS_CLEAN` — not predeclared) → operator-approved EW6 checkpoint (verify/stage/commit/create exact tag `graphrag-pn02db1ew6-index-conflict-recovery-approved`/State-A→B validation/push — no further code changes) → fresh operator authorization for EXEC #9. A real B1 result remains `EW6_REAL_PROVIDER_B1_RESULT = NOT_RUN`.
