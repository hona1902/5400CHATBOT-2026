# GraphRAG-PN02D-B1-EW8 — Scientific Result Observability (CLI-first)

**Status:** IMPLEMENTED + PROVIDER-FREE VERIFIED. Codex EW8 **Review #1 COMPLETE** (`C_PASS_WITH_LOW`; verified LOW `B1EW8-R1-L1` = stale current-state EW7 prose after the executable governance was already repointed to EW8). **Remediation #1 COMPLETE** (comment-only; `B1EW8-R1-L1` CLOSED — all instances now EW8; executable AST unchanged). Codex EW8 **Re-Review #2 COMPLETE** (`C_PASS_WITH_LOW`; confirmed `B1EW8-R1-L1` CLOSED; raised one NEW doc-only LOW `B1EW8-RR2-L1` = this document's own stale process banner). **Remediation #2 COMPLETE** (doc-only; `B1EW8-RR2-L1` CLOSED). Codex EW8 **Re-Review #3 COMPLETE** (`C_PASS_WITH_LOW`; confirmed `B1EW8-R1-L1` and the EW8-doc instances of `B1EW8-RR2-L1` CLOSED; raised one NEW doc-only LOW `B1EW8-RR3-L1` = a remnant `CURRENT_PHASE.md` EW8-row status prefix "READY FOR … REVIEW #1" that Remediation #2 left behind on the same row). **Remediation #3 COMPLETE** (this doc-only cleanup; `B1EW8-RR3-L1` CLOSED). **Next mandatory step = Independent Codex EW8 Re-Review #4** (target `D_PASS_CLEAN`; NOT yet performed — no verdict predeclared). NOT approved, NOT checkpointed, NOT a real B1 result. No commit/tag/push. Zero provider traffic. EW8 checkpoint NOT YET authorized. EXEC #12 NOT authorized.

**Current committed trust anchor (unchanged this turn):** EW7 checkpoint `ab90202e1fe4b9b5938cd927aa69c7bc3a4224f6` (tag `graphrag-pn02db1ew7-index-observation-timing-approved`, obj `00b2e3e29e68aec8580049d8ea42a05050666bbc`). Worktree DIRTY (implementation not yet checkpointed). **Future execution trust expectation is repointed to EW8** — proposed future tag `graphrag-pn02db1ew8-scientific-result-observability-approved` (NOT created). Committed anchor stays EW7 until a future EW8 checkpoint exists; governance already expects EW8, so the mint fails closed until that exact tag is trust-observed.

**Truthful record preserved:** the EW7 checkpoint was approved under the explicit **EW7-only operator review-gate exception** (persistent Codex-sandbox temp-env blocker; 0H/0M/0L code findings). Its Codex verdict is NOT rewritten as D_PASS_CLEAN.

## Why (EXEC #11 → EF5 → EW8)

EXEC #11 (the first real B1 run on the EW7 checkpoint) completed the **entire** frozen pipeline end-to-end — 21/21 corpus embeddings, **24/24** graph indexing (validating the EW7 timing fix live: `document_reposts_after_accepted_track=0`, no fatal 409, `track_resume_operations=0`), GD, vector, membership-removal, and the offline evaluation — with `state=COMPLETE`, `technical_status=COMPLETED`. But it was classified `BLOCKED / INCOMPLETE` because the **scientific verdict could not be observed**.

The **EF5 forensic** (`GRAPH_RAG_PN02DB1_EF5_SCIENTIFIC_RESULT_OBSERVABILITY_COMPLETE`, root cause `D = MULTIPLE_OBSERVABILITY_GAPS`, HIGH confidence) established: the frozen evaluator (`evaluatepn02.run_offline_evaluation` → `OfflineEvaluationResult` → `reportpn02.build_report`) **already computes** the scientific result into `outcome.report` — `report["stage1"]` (leakage: `metrics.cross_notebook_leak_query_count`/`cross_notebook_leakage_rate`, pass=0; `stage2_authorized_by_result`; `violations`), `report["retrieval"]`/`["multihop"]` (`.verdict`), `report["scientific_outputs"]` (`PER_NOTEBOOK_ISOLATION_EVIDENCED` / `_GRAPH_RETRIEVAL_VALUE_EVIDENCED` / `_GRAPH_QA_VALUE_EVIDENCED` / `_MULTIHOP_INCREMENTAL_VALUE_EVIDENCED` = `ScienceVerdict` YES/NO/INCONCLUSIVE/NOT_EVALUATED), `report["membership_removal"]`, and `report["driver"]["workload_ledger_snapshot"]` (GD/vector/query-embedding counts). **Gap 1 (primary):** the CLI payload assembly in `evaluate_execute_b1_live` dropped those scientific keys. **Gap 2:** `RealB1Driver.run` calls `_write_artifact(outcome.report)` on all paths but `artifact_writer` defaults `None` and is not plumbed through the canonical CLI, so the report was in-memory only and destroyed on process exit.

Key facts EF5 confirmed and EW8 preserves: **there is no single overall scientific PASS boolean** — the result is dimensional (an isolation/leakage HARD GATE via `evaluate_stage1` → `isolation_evidenced`, plus value-evidence verdicts that are research findings). `state="COMPLETE"`/`technical_status="COMPLETED"` are **technical-only** (set unconditionally after the evaluation runs); a technically-complete run can still fail the scientific isolation gate (leakage>0).

## Operator EW8 decision — MINIMAL CLI-first

- **MANDATORY:** surface the already-computed scientific result through the canonical CLI payload.
- **DEFERRED:** artifact-persistence plumbing (`ARTIFACT_PERSISTENCE_IMPLEMENTED = NO`; EF5 established `CLI_ONLY_SUFFICIENT_FOR_SCIENTIFIC_VERDICT = YES`). The existing driver `artifact_writer` seam is left untouched.

## What changed (production) — observability-only

**`open_notebook/integrations/graphrag/eval/cli_live_pn02d.py`** (the ONLY production change):
- New pure helper `_project_scientific_result(report)`: a content-safe PROJECTION of the fields the evaluator already computed into `outcome.report`. It **only reads** existing report keys + the workload-ledger spent counts; it **never recomputes** leakage/retrieval/multihop/isolation truth and **invents no synthetic overall PASS boolean**. Missing keys (a technical block before the query stage) stay `None`, so a technical `BLOCKED` never fabricates completed verdicts.
- `evaluate_execute_b1_live` payload now includes `payload["scientific_result"] = _project_scientific_result(outcome.report)` on both the COMPLETE and FAILED-returned paths. Existing payload fields (`result`, `technical_status`, `report_kind`, `failure_reason`, `index_metrics`, `index_membership_records`, …) are unchanged.

Projected fields: `stage1_status`, `stage2_authorized`, `isolation_evidenced`, `leakage_count` (= `cross_notebook_leak_query_count`), `leakage_rate`, `violations`, `retrieval_verdict`, `multihop_verdict`, `scientific_outputs`, `retrieval`, `multihop`, `membership_removal`, `query_embedding_attempts`/`gd_calls`/`vector_queries` (from `workload_ledger_snapshot` `spent` — calls only; no success inflation). GD/vector *success* counts are deliberately NOT surfaced (not unambiguous from the report; the retrieval/multihop verdicts encode the scientific outcome).

**Evaluator/envelope untouched:** `SCIENTIFIC_LOGIC_CHANGED = NO`, `EVALUATOR_LOGIC_CHANGED = NO`, `B1_RUN_ENVELOPE_CHANGED = NO`; EW7 timing (2.0s / 10 polls / 2 attempts / 48) and EW6 recovery semantics unchanged; provider/model/dimension unchanged; the deferred 401/404 embedding-retry defect is not changed. `state="COMPLETE"` remains technical-only.

## Governance repoint EW7 → EW8

- `authmintlivepn02d.py`: `EXPECTED_EW8_CHECKPOINT_TAG = "graphrag-pn02db1ew8-scientific-result-observability-approved"`; `_APPROVED_B1_R2_CHECKPOINT = EXPECTED_EW8`; EW7 demoted to HISTORICAL (its constant/tag/commit `ab90202` preserved); the historical current-identity pointers repointed to EW8.
- `authb1r2pn02d.py`: `B1_R2_EXPECTED_CHECKPOINT_TAG = EXPECTED_EW8_CHECKPOINT_TAG`; EW7 → HISTORICAL, EW8 → CURRENT.
- Cross-phase governance suites (B0C-B adapters/live, R2, EW1–EW7) updated to `current == EW8`; EW7 added to the retained-historical / substitution-rejection / distinctness sets. `VALID_EW8_TAG_ALONE_CAN_MINT = NO`; the mint fails closed until the exact EW8 annotated tag is trust-observed (which does not exist yet). EW7 identity (commit `ab90202`, tag/obj `00b2e3e2`) and all earlier historical identities preserved immutable.

## Verification (provider-free)

- NEW `tests/test_graphrag_pn02db1ew8.py`: scientific-projection tests (tech-complete+isolation-evidenced surfaced; tech-complete+leakage>0 → scientific isolation FAIL surfaced & distinct from technical BLOCKED; technical-BLOCKED fabricates no verdicts; **CLI serializes, does NOT recompute** — an internally-inconsistent report is echoed verbatim; leakage count/rate; retrieval/multihop/scientific_outputs; query/GD/vector counts from the ledger with no success inflation; membership-removal; secret- & content-safe projection; EXEC #11-shape now observable) + EW8 governance (repoint complete; lifecycle State A/B; EW7/EW6/EW5/EW4/EW3/EW2/EW1/PF1/B1-R2/arbitrary cannot substitute; wrong-peel fails closed; exact-tag accepted; tag-alone does not authorize).
- Cross-phase suites updated to `current == EW8` and pass.
- `ruff` clean; `mypy` 0 new (5 pre-existing in the untouched `concurrency_diag08.py`).

## Explicitly NOT done

- No scientific-logic/evaluator/metric/threshold change; no fixture/query-plan/index-plan change; no envelope change; no provider/model/dimension change; no artifact-writer plumbing (deferred); no provider-request interception; no embedding retry-classification remediation; no Codex this turn; no commit/tag/push; no EXEC #12; no B2; no production/Ask/GraphRAG-09.

## Next

Independent Codex EW8 **Re-Review #4** (target `D_PASS_CLEAN`; Review #1, Re-Review #2 and Re-Review #3 are all complete = `C_PASS_WITH_LOW`, with `B1EW8-R1-L1`, `B1EW8-RR2-L1` and `B1EW8-RR3-L1` all remediated — verdict of Re-Review #4 NOT predeclared) → operator-approved EW8 checkpoint (verify/stage/commit/create exact tag `graphrag-pn02db1ew8-scientific-result-observability-approved`/State-A→B validation/push — no further code changes) → fresh operator authorization for EXEC #12 (which will now surface the scientific verdict). A real B1 scientific result remains `EW8_REAL_PROVIDER_B1_RESULT = NOT_RUN` (EXEC #11's pipeline completed but its scientific verdict was unobservable and is not recoverable).
