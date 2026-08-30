# GraphRAG-05 — Ranked Graph Candidate Surface (FORENSIC / DESIGN GATE)

## Goal
Answer, from pinned LightRAG v1.5.6 source only: can it expose enough REAL retrieval evidence
to build a defensible ranked/scored canonical Source candidate interface? Design-only gate —
NO implementation, NO RRF/fusion/reranker, NO production/test/migration/API/frontend code, NO
provider traffic, NO DB/LightRAG-storage mutation. `OPEN_NOTEBOOK_GRAPHRAG_ENABLED` stays false.

## Frozen input
GraphRAG-04 approved: commit cb86a06, tag graphrag-04-approved.
RRF_CANDIDATE_INTERFACE_READY = NO; HYBRID_VALUE_EVIDENCED = INCONCLUSIVE. Not reopened.

## Pinned source
HKUDS/LightRAG tag v1.5.6, commit b33c6b0812cddf39206e48a9810112e51f025274
(_version.py __version__="1.5.6"). Read via read-only clone into out-of-repo scratchpad
(public code; not a provider call; not committed). Code wins over docs.

## Phases
### Phase 0 — Orient & obtain pinned source  **Status: complete**
Read frozen inputs (GRAPHRAG_04_EVALUATION.md, CURRENT_PHASE.md, client.py, models.py,
config.py). LightRAG not vendored/pip-installed → shallow-cloned tag v1.5.6 to scratchpad.

### Phase 1 — Trace query pipeline (Target A)  **Status: complete**
Full call graph /query → aquery → kg_query → _build_query_context (4 stages) → response.
Mapped where each score is created and lost. Source-cited in findings.md.

### Phase 2 — Score-surface inventory + classification (Targets B/C/D)  **Status: complete**
12-surface table (entity/relation/chunk cosine, rerank_score, weight, degree, reference_id,
occurrence, order, /query/data). Classified A–E. Discovered /query/data structured endpoint
(entities/relationships/chunks/references) — no score exposed. Provenance chains STRONG(chunk)/
PARTIAL(kg). Aggregation bias analysis. Abstention (min_rerank_score / cosine floor) unexposed.

### Phase 3 — Design + decisions + adversarial review  **Status: complete**
GraphSourceCandidate (unranked, no score field). Options A/B/C/D → prefer C. Rank contract =
NONE DEFENSIBLE. Future eval design (larger corpus). Adversarial review A/B/C/D. All 9 required
decisions recorded. Doc written: GRAPHRAG_05_RANKED_CANDIDATE_FORENSIC.md.

## Next Step
Design gate deliverables complete. STOP at GRAPH_RAG_05_FORENSIC_DESIGN_GATE_COMPLETE.
No commit / push / tag. No implementation. No GraphRAG-06.

## Decisions Made
| # | Decision | Rationale (pinned-source cite) |
|---|---|---|
| 1 | No direct scored Source surface | ReferenceItem has no score/rank (query_routes.py:236-244); convert_to_user_format emits no-score allowlist (utils.py:6076-6172) |
| 2 | Relevance scores exist internally but LOST | entity/relation/chunk cosine dropped at _get_node_data/_get_edge_data/_get_vector_context; rerank_score dropped by convert_to_user_format |
| 3 | Only exposed numerics are structural/frequency | weight (edge strength, utils 6142), rank (degree, operate 5601), reference_id (frequency, utils 6238-6245) |
| 4 | Hybrid order is not a rank | round-robin interleave (operate.py:5199-5246) |
| 5 | /query/data = better evidence surface, not scored | lightrag.py:3701; structured provenance, no relevance score |
| 6 | Prefer Option C (unranked evidence set) | only honest option on pinned v1.5.6; Option D needs LightRAG modification (out of scope) |
| 7 | Contract has NO score field | inventing a score is prohibited (task §16/§50) |

## Errors Encountered
| Error | Attempt | Resolution |
|-------|---------|------------|
| (none) | | |
