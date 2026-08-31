# Task Plan — GraphRAG-08 Larger-Corpus Structured Evidence Value Evaluation

## Goal
Produce a **design gate only** deliverable answering whether a larger-corpus benchmark
*could* evidence enough real retrieval/provenance/runtime value to justify implementing
the GraphRAG-07 Structured Evidence Adapter. **No execution, no code, no fixtures, no
benchmark run, no provider traffic, no DB mutation.** Output is documentation/planning.

Target decision this phase *designs the means to answer* (does NOT answer):
`STRUCTURED_EVIDENCE_VALUE_EVIDENCED = YES / NO / INCONCLUSIVE`.

## Hard boundaries (from task spec §4, §87)
- No production code, no adapter, no client.query_data(), no /query/data integration.
- No changes to client.query(), HybridRetriever, RRF, vector_search, LightRAG version/mode.
- No fixtures, no eval code, no migrations, no DB/Source/LightRAG mutation.
- No sidecar start, no provider calls, GRAPHRAG_ENABLED stays false, no .env change.
- No rank/score metrics on unordered graph results. No fake rank/score.

## Frozen inputs (checkpoints)
- GraphRAG-04 approved: cb86a06 (tag graphrag-04-approved)
- GraphRAG-05 forensic approved: 833ec59 (tag graphrag-05-forensic-approved)
- GraphRAG-06 forensic approved: d7e6a5b (tag graphrag-06-forensic-approved)
- GraphRAG-07 contract approved: 337456d (tag graphrag-07-contract-approved)
- LightRAG pinned: HKUDS v1.5.6 commit b33c6b0

## Next Step
Phase 5 — final read-only git audit; then report GRAPH_RAG_08 design-gate COMPLETE. No
staging/commit/tag. Design gate is otherwise complete; execution needs operator approval.

## Phases

### Phase 1 — Restore context & inventory prior artifacts
**Status:** complete
- Located .planning history (graphrag-01..07), docs/agribank/development GRAPHRAG_04..07,
  tests/fixtures/graphrag_04_eval_v1, tests/test_graphrag_04_eval*.py.

### Phase 2 — Read-only evidence extraction (allowed inspection)
**Status:** complete
- 4 Explore agents returned; all consolidated into findings.md sections A–D.

### Phase 3 — Author GRAPHRAG_08 design document
**Status:** complete
- Authored docs/agribank/development/GRAPHRAG_08_LARGER_CORPUS_VALUE_EVALUATION_DESIGN.md
  covering §0–§91: corpus size analysis (→75), query classes (10)/counts (60), negatives (12),
  GT policy, DEV/HOLDOUT (30/30), vector + graph-set + breadth + provenance + complementarity +
  runtime metrics with exact formulas, isolation (Option A), cleanup/recovery, provider plan +
  state machine, artifact contract, value decision matrix, Reviews A–G, all §89 flags.
- docs/agribank/development/GRAPHRAG_08_LARGER_CORPUS_VALUE_EVALUATION_DESIGN.md
- Cover all required spec sections: corpus size analysis + recommendation; query classes
  table; negative design; distractor/entity-graph design; GT policy; DEV/HOLDOUT; vector
  metrics; unordered graph set metrics; candidate-breadth metrics; provenance metrics;
  complementarity/oracle; runtime/provider metrics; isolation; cleanup/recovery; provider
  execution plan + state machine; artifact contract; value decision framework/matrix.
- Include required tables (§73 benchmark spec, §74 query class, §75 metric, §76 decision).
- Adversarial reviews A–G (§80–86).

### Phase 4 — Update phase-tracking docs & final flags
**Status:** complete
- Updated CURRENT_PHASE.md GraphRAG-08 row (design gate complete, EXECUTION_READY=YES,
  retained flags). .active_plan already points here. §89 flags stated in design doc.

### Phase 5 — Final git audit (read-only)
**Status:** complete
- git status/diff confirm: 2 modified (.planning/.active_plan pointer; CURRENT_PHASE.md 1 line)
  + 2 untracked (GRAPHRAG_08 design doc; this plan dir). grep for .py/migrations/tests/
  frontend/.env = NONE. No stage/commit/tag done.

## Decisions Made
| # | Decision | Rationale |
|---|----------|-----------|
| 1 | Use planning-with-files structure exactly as deliverables specify | Task §88 names task_plan/findings/progress + .active_plan |
| 2 | Delegate large-doc reads to parallel Explore agents | Keep context lean; AGRIBANK §2 verify-against-source |

## Errors Encountered
| Error | Attempt | Resolution |
|-------|---------|------------|
| (none yet) | | |
