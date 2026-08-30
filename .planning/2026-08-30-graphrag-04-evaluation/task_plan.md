# GraphRAG-04 — Synthetic Retrieval Evaluation / Quality Baseline

## Goal
Reproducible SYNTHETIC/PUBLIC retrieval-quality baseline comparing existing VECTOR_BASELINE
vs current GRAPHRAG_BASELINE (LightRAG v1.5.6 hybrid) at the CANONICAL SOURCE level.
Measure current behavior; do not tune the systems. Decide RRF_CANDIDATE_INTERFACE_READY
and HYBRID_VALUE_EVIDENCED. Evaluation only — no HybridRetriever/RRF/Ask/Chat/frontend/migration.

## Approved checkpoint
branch feature/graphrag-lifecycle · HEAD 4f0e43a · tag graphrag-03e-approved · migrations frozen at 50.

## Phases
### Phase 0 — Context recovery & forensic  **Status: complete**
git gate PASSED; governance + design docs read; 3 forensic agents (vector/graphrag/lifecycle) done;
findings.md fully source-cited. No §57 hard blocker found.

### Phase 1 — §57 forensic/design gate  **Status: complete**
Presented 15 forensic answers + design plan. **APPROVED as-is 2026-08-30.** Config: measure current
answer-generating hybrid path (score references only); ~14 sources / ~28 queries.

### Phase 2 — Benchmark v1 fixtures (frozen, committed)  **Status: in_progress**
Synthetic corpus (12–16 sources, fictional), 24–30 queries (6 classes), source-level ground truth,
DEV/HOLDOUT split. + dataset validation tests (§43).

### Phase 3 — Evaluator harness (eval-only, no production change)  **Status: pending**
Source normalization adapters (vector chunk→parent_id dedup w/ best rank; graph provenance→source set,
malformed/foreign separated, RecordID lossless via graphrag `record_id_for`). Metrics module
(ranked: Hit@K/Recall@K/MRR for vector; set: hit/recall/provenance-coverage for graph; complementarity;
oracle-union). Error accounting. + unit tests w/ hand-calculated fixtures (§44,§45) + security tests (§46).

### Phase 4 — Live baseline run (synthetic-only)  **Status: BLOCKED**
Infra probe 2026-08-30: SurrealDB UP (1 source, clean); LightRAG v1.5.6 sidecar UP/healthy.
BLOCKER: no embedding model configured in Open Notebook (vector baseline can't run) + sidecar
LLM provider (Boundary B) unconfirmed + GraphRAG env not set. Runner + live test built & ready;
live-DB isolation/cleanup tests PASS. Awaiting user decision on synthetic-safe provider config.
Bring up SurrealDB + worker + LightRAG v1.5.6 sidecar + synthetic-safe providers. Create tagged synthetic
sources → vector index + graphrag index → bounded readiness wait → run VECTOR + GRAPHRAG retrieval →
normalize → metrics (DEV+HOLDOUT, per class, complementarity, oracle-union) → cleanup via approved delete
lifecycle → verify no synthetic sources remain. Artifact `.artifacts/graphrag-04/<run-id>/evaluation.json` (not committed).

### Phase 5 — Documentation + decisions  **Status: pending**
`docs/agribank/development/GRAPHRAG_04_EVALUATION.md` (40-point template §58). RRF_CANDIDATE_INTERFACE_READY
and HYBRID_VALUE_EVIDENCED with evidence. Update CURRENT_PHASE.md (NOT complete until sign-off).

### Phase 6 — Verification gates  **Status: pending**
Targeted 04 tests + full GraphRAG regression (03A–03E) + full backend + ruff + mypy + Karpathy + Codex A/B/C.
Final report. NO commit, NO push, NO GraphRAG-05.

## Next Step
GraphRAG-04 = COMPLETE / APPROVED (signed off 2026-08-30). Live baseline done via OpenRouter (te3-small
+ gpt-4o-mini) through LightRAG v1.5.6; both probes PASS; Karpathy CLEAN; Codex A/B/C resolved. Final:
RRF_CANDIDATE_INTERFACE_READY = NO; HYBRID_VALUE_EVIDENCED = INCONCLUSIVE. Local posture restored
(GRAPHRAG_ENABLED=false). Checkpoint: commit "GraphRAG-04: add synthetic retrieval evaluation baseline"
+ tag graphrag-04-approved + push to backup only (never origin). STOP at GRAPH_RAG_04_CHECKPOINT_COMPLETE.
No GraphRAG-05.

All phases (0–6) COMPLETE.

## OpenRouter verification (2026-08-30)
OpenRouter serves NO embeddings (396 models, 0 embedding; docs chat-only; ON discovery classifies all
openrouter models as language). → text-embedding-3-small via OpenRouter impossible → breaks BOTH vector
+ LightRAG embedding. OpenRouter OK for GRAPH-side LLM only. OPENROUTER_API_KEY not present. Operator
deferred provider decision (away); continue non-live work; do NOT choose a provider; do NOT mark NOT_READY.

## Decisions Made
| # | Decision | Rationale |
|---|---|---|
| 1 | Names: VECTOR_BASELINE / GRAPHRAG_BASELINE_CURRENT_HYBRID (user-mandated honest name). Not "graph-only" | hybrid mode internally mixes graph+vector; answer-generation is unavoidable overhead of the wired path, answer NOT scored |
| 2 | Compare at canonical SOURCE level; vector key = parent_id, graph key = file_path→source_id | apples-to-apples per §10 |
| 3 | RRF_CANDIDATE_INTERFACE_READY predicted NO | LightRAG v1.5.6 references carry no score/rank; order not relevance |
| 4 | Measure CURRENT answer-generating query path; do NOT add only_need_context | §38/§51 measure, don't modify |
| 5 | Isolation = topics tag + throwaway notebook + retained created-id set; never global sweep | §5 no real-source accidents |
| 6 | NO migration (24/25 frozen, count 50) | §52 dev eval harness needs no schema |

## Errors Encountered
| Error | Attempt | Resolution |
|-------|---------|------------|
| (none yet) | | |
