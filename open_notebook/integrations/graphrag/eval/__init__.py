"""GraphRAG-04 retrieval-evaluation harness (development/measurement only).

This subpackage is an OFFLINE evaluation harness. It measures the retrieval
quality of the systems that already exist — the Open Notebook vector search seam
(`vector_search`) and the currently-wired GraphRAG diagnostic query
(`GraphRAGClient.query` / `GraphRAGService.query_strict`, hybrid mode). It does
NOT implement HybridRetriever, RRF, fusion, reranking, or any Ask/Chat wiring,
and NOTHING in production imports it (AGR-005 GraphRAG-04 scope).

Rules baked into this harness:
  * Synthetic/public data only — Boundary B stays synthetic-only (AGR-005 §6).
  * The GraphRAG baseline is named GRAPHRAG_BASELINE_CURRENT_HYBRID: it is the
    wired hybrid path, which internally mixes graph + vector retrieval and also
    generates an (unavoidable) LLM answer. That answer is NEVER scored, judged,
    or used to decide relevance — only the returned reference provenance is.
  * LightRAG v1.5.6 references are an UNORDERED provenance set with no score/rank
    field, so graph rank metrics (MRR/nDCG) are N/A and never fabricated; graph
    is evaluated with set metrics only.
"""

VECTOR_BASELINE = "VECTOR_BASELINE"
GRAPHRAG_BASELINE = "GRAPHRAG_BASELINE_CURRENT_HYBRID"
