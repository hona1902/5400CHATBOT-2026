# GraphRAG-05 — Findings (pinned LightRAG v1.5.6 @ b33c6b0)

All cites are `file:line` in the pinned tree unless prefixed `ON:` (Open Notebook repo).

## A. Wired query path
- ON: `GraphRAGClient.query()` client.py:538 → POST /query, body {query, mode:"hybrid",
  include_references:true, top_k?} client.py:553-559. Maps references[].file_path→source_id,
  returns unordered set (ordered=False).
- `POST /query` query_routes.py:447 `query_text` → `rag.aquery` lightrag.py:3643 → `kg_query`
  operate.py:4180 → `_build_query_context` operate.py:5440.
- `ReferenceItem` query_routes.py:236-244 = {reference_id, file_path, content?} — NO score/rank.
- `QueryResponse` query_routes.py:247-258 = {response, references?, response_time?}.
- Default QueryParam.mode = "mix" base.py:93; client overrides to hybrid. enable_rerank =
  env RERANK_BY_DEFAULT default true base.py:155. cosine_better_than_threshold default 0.2
  base.py:229.

## B. Where scores are created and LOST
- Entity retrieval `_get_node_data` operate.py:5563: `entities_vdb.query()` :5574 returns
  results ranked by cosine; consumer keeps entity_name + `rank`(=node degree, structural)
  :5595-5607; DROPS cosine. Comment "Entities are sorted by cosine similarity" :5618.
- Edge retrieval `_get_edge_data` operate.py:5838: `relationships_vdb.query()` :5849; edges
  sorted by (rank, weight) :5672-5673 or "vector search order (sorted by similarity)" :5882;
  keeps `weight`(structural, extraction-time); DROPS cosine.
- Vector/naive chunks `_get_vector_context` operate.py:4660: `chunks_vdb.query()` :4690 returns
  results with `distance`; builds {content, created_at, file_path, source_type, chunk_id}
  :4700-4709; DROPS distance.
- Chunk merge `_merge_all_chunks` operate.py:5153: ROUND-ROBIN interleave of vector/entity/
  relation lists :5199-5246; merged dicts {content, file_path, chunk_id} — no score.
- Rerank `apply_rerank_if_enabled` utils.py:5470: if rerank_model_func set → doc["rerank_score"]
  = relevance_score :5543 (genuine cross-encoder relevance); ELSE warn + return unranked
  :5494-5498. `process_chunks_unified` utils.py:5601 applies it :5630-5640, then filters by
  min_rerank_score default 0.5 :5643-5657 (defaults missing score to 1.0 :5652 → no filter
  without a rerank model), then chunk_top_k cut :5668-5670.
- `pick_by_vector_similarity` utils.py:5271: returns "chunk IDs sorted by similarity (highest
  first)" — IDs only :5393-5401; similarity discarded.
- References `generate_reference_list_from_chunks` utils.py:6200: reference_id assigned by
  file-occurrence FREQUENCY desc, first-appearance :6238-6245.
- `convert_to_user_format` utils.py:6076: rebuilds entities :6099-6120 / relationships
  :6136-6161 / chunks :6164-6172 / references with a FIXED allowlist — NO score/similarity/
  rerank_score/distance on any; only relationship `weight` (default 1.0) survives :6142/6156.

## C. Structured endpoint /query/data
- `POST /query/data` query_routes.py:1309 → `rag.aquery_data` lightrag.py:3701; sets
  only_need_context=True :3817 (no LLM answer); kg_query/naive_query → raw_data =
  convert_to_user_format. `QueryDataResponse` query_routes.py:261-269 {status,message,data,
  metadata}.
- Data shape lightrag.py:3717-3778: entities{entity_name,entity_type,description,source_id,
  file_path,created_at,reference_id} NO score; relationships{...,weight,...} weight=structural
  strength; chunks{content,file_path,chunk_id,reference_id} NO score; references{reference_id,
  file_path}; metadata.processing_info = counts. NOT wired into ON (client calls /query only).

## D. Provenance
- chunk.file_path → source_id: STRONG, lossless (GraphRAG-04 live: 265 refs, 100% valid, 0
  malformed/foreign/dup). Same RecordID helpers as outbound boundary.
- entity/relation → chunk(s) → file_path(s) → source: PARTIAL (multi-source; "unknown_source"
  default utils.py:6105/6117/6144).

## E. Abstention
- cosine_better_than_threshold default 0.2 base.py:229 (silent VDB floor; value not surfaced).
- min_rerank_score default 0.5 utils.py:5644 can empty chunk set :5664-5665; needs a rerank
  model (ON sidecar has none: GraphRAG-04 §26.1 bindings = LLM+embedding only). Not exposed.

## Decisions
LIGHTRAG_DIRECT_SCORED_SOURCE_SURFACE = NO
LOWER_LEVEL_QUERY_SCORE_AVAILABLE = PARTIAL (internal only, unexposed)
SOURCE_PROVENANCE_FOR_SCORED_EVIDENCE = STRONG (chunk) / PARTIAL (kg)
SOURCE_LEVEL_AGGREGATION_DEFENSIBLE = REQUIRES_EXPERIMENT (only after a real score exists)
GRAPH_NATIVE_RANKING_SIGNAL = NO (relevance signals VECTOR-equivalent; graph numerics structural)
ABSTENTION_SIGNAL_AVAILABLE = UNCLEAR (exists in engine, unexposed, off as deployed)
GRAPH_CANDIDATE_CONTRACT_DESIGNABLE = YES (unranked) / NO (ranked)
GRAPH_CANDIDATE_IMPLEMENTATION_READY = NO
RRF_CANDIDATE_INTERFACE_READY = NO

## REVIEW-GATE re-verification (Session 2, pinned clone re-read)
| Claim | File:line | Function | Semantics | Survives to HTTP | Lost |
|---|---|---|---|---|---|
| entity cosine | operate.py:5574,5595-5618 | _get_node_data | entities_vdb.query ranks by cosine; keeps entity_name+rank(degree) | order only | cosine value |
| relation cosine | operate.py:5849,5873-5882 | _get_edge_data | relationships_vdb.query ranks by cosine; keeps weight(structural) | order + weight | cosine value |
| chunk cosine | operate.py:4690,4700-4709 | _get_vector_context | chunks_vdb.query returns `distance`; keeps content/ids | — | distance |
| VDB returns distance | kg/nano_vector_db_impl.py:444 (also faiss:440, qdrant:785, milvus:2367, mongo:3954, opensearch:5808) | query | storage layer emits `distance` (=__metrics__ cosine) | — | dropped by operate consumers |
| hybrid entity interleave | operate.py:4869-4888 | _perform_kg_search | round-robin merge local+global entities | order (interleaved) | any global rank |
| hybrid relation interleave | operate.py:4890-4923 | _perform_kg_search | round-robin merge local+global relations | order (interleaved) | any global rank |
| chunk round-robin | operate.py:5199-5246 | _merge_all_chunks | vector/entity/relation interleave; vector_chunks only in mix (4848) | order (interleaved) | score (none present) |
| rerank_score | utils.py:5470,5543,5494-5498 | apply_rerank_if_enabled | cross-encoder relevance; needs rerank_model_func (ON sidecar: none) | NO | dropped by convert_to_user_format |
| min_rerank_score | utils.py:5644-5665,5652 | process_chunks_unified | absolute cutoff default 0.5; missing→1.0 (no filter w/o reranker) | NO (filters silently) | score |
| convert_to_user_format | utils.py:6076-6197 | — | fixed allowlist; only relationship weight numeric | weight, reference_id | all similarity/rerank |
| ReferenceItem | query_routes.py:236-244 | — | {reference_id,file_path,content?} | those 3 fields | no score/rank |
| /query/data | query_routes.py:1309; lightrag.py:3701,3817 | query_data/aquery_data | only_need_context=True (no LLM); raw_data=convert_to_user_format | structured provenance | no relevance score |
Result: every decisive report claim CONFIRMED against pinned source. Two precision edits
applied (aggregation wording; hybrid entity/relation interleave note). No claim weakened for
lack of support; none strengthened beyond source.

## STOP conditions triggered (task §23) — legitimate outcome
- only order available is round-robin interleave (operate.py:5199-5246);
- only exposed numerics are structural weight/degree (utils 6142 / operate 5601), no query
  relevance;
- a usable query score requires modifying pinned LightRAG (patch the drop sites or add an
  endpoint) → out of scope.
