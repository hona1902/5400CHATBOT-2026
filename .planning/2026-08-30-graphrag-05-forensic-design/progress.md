# GraphRAG-05 — Progress log

## Session 1 — 2026-08-30
- Read frozen inputs: GRAPHRAG_04_EVALUATION.md (§26.5 live baseline; RRF=NO, HYBRID=INCONCLUSIVE),
  CURRENT_PHASE.md, ON graphrag client.py / models.py / config.py (VERIFIED_LIGHTRAG_VERSION=v1.5.6).
- LightRAG is a sidecar (ghcr.io/hkuds/lightrag:v1.5.6), not vendored / not pip-installed.
  To satisfy "PINNED SOURCE WINS" with line-level cites, shallow-cloned tag v1.5.6
  (commit b33c6b0) into the out-of-repo scratchpad (read-only; public code; not committed).
  Confirmed _version.py __version__="1.5.6". No sidecar started; GRAPHRAG_ENABLED stayed false.
- Traced full query pipeline: /query → query_text → aquery → kg_query → _build_query_context
  (search → truncate → round-robin merge → build_context) → convert_to_user_format → response.
- KEY FINDINGS:
  - Every genuine query-relevance score (entity/relation/chunk embedding cosine; cross-encoder
    rerank_score) is COMPUTED then DROPPED before the HTTP surface.
  - Only exposed numerics: relationship `weight` (structural, /query/data only), `reference_id`
    (frequency index). ReferenceItem has no score/rank (matches GraphRAG-04).
  - NEW vs 04: /query/data structured endpoint (entities/relationships/chunks/references,
    no LLM answer) — richer provenance, still NO relevance score.
  - Hybrid final order = round-robin interleave → not a comparable rank.
  - Abstention (min_rerank_score / cosine floor) exists in engine but unexposed and off
    (no rerank model on ON sidecar).
- Wrote deliverables: GRAPHRAG_05_RANKED_CANDIDATE_FORENSIC.md (call graph §1, 12-surface table
  §2, /query/data §3, provenance §4, aggregation §5, graph-native §6, abstention §7,
  GraphSourceCandidate design §8, rank-contract=NONE §9, options A/B/C/D §10, future eval §12,
  adversarial review §13, all 9 decisions §14, final report §15). Planning trio + .active_plan +
  CURRENT_PHASE row.

### Decisions
- Preferred architecture = OPTION C (unranked, provenance-strong evidence set; no score/rank).
- RRF_CANDIDATE_INTERFACE_READY = NO (unchanged). GRAPH_CANDIDATE_IMPLEMENTATION_READY = NO.

### Verification evidence
- Design-only gate: PRODUCTION_CODE_CHANGED=NO, TEST_CODE_CHANGED=NO, MIGRATION_CHANGED=NO,
  PROVIDER_TRAFFIC=NO, DATABASE_MUTATION=NO, LIGHTRAG_STORAGE_MUTATION=NO. No commit/push/tag.

### Next
- Await operator review of the forensic/design gate. No implementation until approved.
- STOP at GRAPH_RAG_05_FORENSIC_DESIGN_GATE_COMPLETE.

## Session 2 — 2026-08-30 — REVIEW GATE
- Re-verified all decisive claims against the retained pinned clone (fresh reads, not memory):
  _get_edge_data (5838-5894), _perform_kg_search (4717-4935), BaseVectorStorage.query contract,
  VDB `distance` return in nano/faiss/qdrant/milvus/mongo/opensearch impls. All CONFIRMED.
- New precise cite: hybrid ROUND-ROBIN merges entities (4869-4888) AND relations (4890-4923),
  not just chunks; vector_chunks only in mix (4848). Strengthens "order is not a rank."
- Reviews A–F + architecture/phase-boundary/security: PASS. No misclassification found; no
  hidden query-relevance score survives to HTTP; provenance not overstated (STRONG chunk /
  PARTIAL kg); /query/data confirmed evidence-only (no relevance score, no LLM answer);
  aggregation verdict kept REQUIRES_EXPERIMENT (not YES) with sharpened wording.
- Documentation fixes only (2): tightened §5 aggregation conclusion; added §1 hybrid-interleave
  precision note. No production/test/migration/API/retriever/RRF/rerank code. Clone stayed
  outside repo (untracked). No provider traffic, no DB/LightRAG mutation, sidecar not started.
- Decisions unchanged. STOP at GRAPH_RAG_05_FORENSIC_REVIEW_COMPLETE. No commit/push/tag.
