# GraphRAG-PN02D-B2 — Live QA Wiring (Option A) — Implementation

**Status:** IMPLEMENTED + PROVIDER-FREE VERIFIED — awaiting Independent Codex B2 Live Wiring Review #1. NOT approved, NOT checkpointed, NOT a real B2 result. No commit/tag/push. Zero provider traffic. Real B2 execution NOT authorized.

**Current committed governance checkpoint (unchanged):** EW8 — `95670adce1ac243f894b14de74c564cabc5325b4` (tag `graphrag-pn02db1ew8-scientific-result-observability-approved`). B1 is CLOSED (EXEC #12 authoritative). The B2 **future** checkpoint tag `graphrag-pn02db2-qa-live-wiring-approved` does **NOT** exist yet; a real B2 run fails closed until it is created by a future operator-approved B2 checkpoint. EW8 can never authorize a B2 run.

**Do NOT read this as a Codex pass or an approval.** The Independent Codex B2 Live Wiring Review #1 has not run.

## B1 CLOSED / B2 design frozen (context)

B1 is scientifically observed and closed. The B2 QA experiment is frozen in PN02A: baseline **QA-V** (Open-Notebook answer from V(K=5) member evidence) vs graph arms **QA-GD** and **QA-V+GD** (`combinepn02.combine_v_gd`, cap 8); Stage-2 K=5; deterministic grading (tokens + citations, **no LLM judge**); frozen budget `MAX_FINAL_ANSWER_CALLS=72` (24 queries × 3 arms), 0 retry, 0 judge, membership-removal final-answer 0; leakage hard gate 0. Frozen B2 scientific run id: `pn02db2-6469b191-db13-4d2f-864a-4079578efcf4`. Index policy: fresh isolated build per real B2 run (reusing EW6 recovery + EW7 timing).

## Option A architecture (additive optional QA-stage seam)

The blocker (`run_offline_evaluation` invoked only inside the shared B1 orchestrator with `qa_results=None`) was resolved by operator **Option A**: an additive **optional QA-stage seam** inside the shared orchestrator. When the seam is absent, B1 is byte-identical (evaluator receives `qa_results=None`, 0 final-answer calls, cap 0). `B1_EXECUTABLE_PATH_CHANGED_BY_B2 = NO_OBSERVABLE_BEHAVIOR_CHANGE`.

- `driverpn02d.py` — `QAStageSeam` protocol + `B1DriverDeps.qa_stage_seam=None`; stage 9b `qa_results = None if seam is None else await seam(...)` → the existing `run_offline_evaluation`.
- `driver_live_pn02d.py` — threads the optional `qa_stage_seam` and a `mint_fn` (default = B1 mint) through `build_live_b1_driver_deps` and `RealB1Driver` (both default → byte-identical B1).
- `budgetlivepn02d.py` — `b2_caps()`/`b2_caps_dict()` (FINAL_ANSWER=72; B1 default stays 0).
- `qastagepn02db2.B2QAStage` — the injected stage: deterministic 24×3=72 plan, arm evidence (QA-V=V5 members / QA-GD=GD members / QA-V+GD=combine_v_gd) with a HARD notebook-member prefilter before every answer call, a fail-closed 72-cap budget guard, verbatim citation preservation. It computes NO scientific result.
- `real_final_answer_seam_pn02d.RealFinalAnswerSeam` — the ON-owned final-answer seam (one injected `completion_fn` call, no retry, EW5 safe structured errors, abstention). LightRAG does NOT generate final answers.

## B2 mint-identity security boundary (separate from B1)

`authmintlivepn02d.py` gained a **parallel** B2 authorization identity that reuses the same unforgeable verifier (`verify_b1_r2_checkpoint`) and trusted reader — no duplicated verifier, no weakened checks, no public trust-root override:

- `EXPECTED_B2_CHECKPOINT_TAG = "graphrag-pn02db2-qa-live-wiring-approved"`, `current_approved_b2_checkpoint()`, `b2_r2_refusal_reasons()`.
- `B2_ALLOWED_OPERATION_VALUES = B1 ∪ {QA_V, QA_GD, QA_V_GD}` (minimal; `LIGHTRAG_FINAL_ANSWER`/`CLIENT_QUERY`/`JUDGE_MODEL` stay forbidden).
- Internal `_AuthProfile` (`_B1_AUTH_PROFILE`/`_B2_AUTH_PROFILE`); the mint body is a private `_mint_..._with_profile`; the public `mint_live_provider_run_authorization` delegates the B1 profile (byte-identical) and the new public `mint_live_b2_provider_run_authorization` delegates the B2 profile. `current_approved_b1_r2_checkpoint()` stays EW8 (B1/EXEC unaffected).

Because the B2 tag is absent from Git, a B2 run/CLI fails closed today with `b1_r2_tag_not_observed_in_git`; EW8 (which peels to today's HEAD) cannot substitute; a valid B2 tag alone still cannot mint without a matching operator grant.

## B2 live layer + CLI

- `realseamsb2pn02d.py` — `run_live_b2_execution` (production; reuses the B1 two-boot/mint-ordering/index/GD/vector/removal orchestration via the shared composed helper's additive `driver_kwargs`, injecting the B2 QA stage + the B2 mint) and a private test-composition helper. No duplicated two-boot/mint/index/GD/vector pipeline. The real ON completion transport is built lazily through the repository provisioning abstraction (`provision_langchain_model`) — never an ad-hoc client.
- `driverb2pn02d.RealB2Driver` — a THIN adaptor: `RealB1Driver` configured with the B2 QA stage + `mint_live_b2_provider_run_authorization`. No new grading/metrics/verdict/leakage/index/auth logic.
- `cli_live_pn02d.py` — the `execute-b2-live` verb + `evaluate_execute_b2_live` (fixed B2 profile; no public override) + `_project_b2_scientific_result`. `execute-b1-live` and `_project_scientific_result` are behaviorally unchanged (delegation with B1 defaults + a B1 regression test).

The **existing** `evaluatepn02` remains the sole QA decision path (`NEW_QA_EVALUATOR_IMPLEMENTATIONS = 0`). The CLI does not recompute Q0-Q3.

## Content safety

`_project_b2_scientific_result` is a pure projection of the fields the evaluator already computed (three layers kept separate — `TECHNICAL_RESULT` / `ISOLATION_HARD_GATE` / `QA_VALUE_VERDICT`; plus per-arm QAArmMetrics P1/P2/P3/S1/S2/S3, run id, safe counts) — never answer text, question text, context, source snippets, raw completion, provider body, or secret. No synthetic global PASS boolean. Raw QA answers are ephemeral (never persisted); the artifact uses the existing injectable `artifact_writer` sink with default persistence NONE.

## Verification (provider-free)

- `tests/test_graphrag_pn02db2_mint_identity.py` (23) — B2 fails closed today; EW8/EW7/arbitrary can't substitute; wrong-peel fails closed; tag-alone can't mint; B1 grant can't authorize B2; State A/B lifecycle-aware; B1 mint byte-identical.
- `tests/test_graphrag_pn02db2_live_wiring.py` (6) — `execute-b2-live` fails closed today (no boot); the provider-free live harness proves `qa_results` reach the existing evaluator (`qa_not_supplied` gone; 72 final-answer calls); content-safe projection with sentinel assertions; secret-safe final-answer error; B1 CLI regression.
- `tests/test_graphrag_pn02db2_qa_wiring.py` (13) — the Option-A core seam.
- Full `-k graphrag` suite green; ruff clean; mypy 0-new; `git diff --check` clean.

## Next

Independent Codex B2 Live Wiring Review #1 → (remediation if needed) → operator-approved B2 checkpoint (creates the exact `graphrag-pn02db2-qa-live-wiring-approved` tag) → separate explicit real-B2 authorization → exactly one `execute-b2-live` run. GraphRAG-09 / production integration / LightRAG Ask remain NOT approved.
