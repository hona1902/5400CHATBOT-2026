# GraphRAG-06 Findings (pinned v1.5.6 b33c6b0 + current ON source)

All LightRAG cites = pinned tag v1.5.6 commit b33c6b0 in scratchpad clone. Code wins over docs.

## Current Open Notebook path (Forensic Target A)
- `GraphRAGClient.query()` client.py:538-608 is the ONLY query wired. No `query_data()` method
  exists in client.py (grep confirmed). Body `{query, mode:"hybrid", include_references:True,
  top_k?}` → `POST /query` (client.py:553-561).
- Consumes: `payload["response"]` (answer str, REQUIRED else GraphRAGProtocolError:565),
  `payload["references"][].{file_path→source_id, reference_id, content→excerpts}`,
  `payload["response_time"]→elapsed`.
- The answer string is REQUIRED to parse the response (raises if missing) but GraphRAG-04
  ignores it for evaluation (05 §1). references mapped to GraphReference(ordered implicitly via
  set; resolved=_looks_like_record_id).
- Exposed only via diagnostic `POST /api/search/graph` (to confirm from router). Ask/Chat
  untouched.

## Pinned /query/data forensic (v1.5.6 b33c6b0) — DECISIVE
- Route: `POST /query/data` query_routes.py:1013 (decorator) / handler query_data() :1309;
  `response_model=QueryDataResponse`; `dependencies=[Depends(combined_auth)]` :1015 (SAME
  X-API-Key auth as /query, get_combined_auth_dependency :316). Shares `QueryRequest` model
  (query min_length=3 :31; mode default; top_k etc). Body → `to_query_params(False)` :1413.
- Handler: `rag.aquery_data(request.query, param)` :1414 → `QueryDataResponse(**response)` :1418.
  Any Exception → `internal_server_error(e)` → HTTP 500 :1427-1429. Empty results are NOT an
  exception: aquery_data returns `{status:"failure", message:"Query returned no results",
  data:{}, metadata:{failure_reason:"no_results"}}` with HTTP 200 (lightrag.py:3874-3887).
- aquery_data lightrag.py:3701: builds `data_param` copy with `only_need_context=True` :3817
  ("Skip LLM generation"); for local/global/hybrid/mix calls `kg_query(... hashing_kv=
  self.llm_response_cache ...)` :3837-3848; naive→naive_query :3851; bypass→empty :3860.
  Ends `await self._query_done()` :3904.

## §7 ANSWER-GENERATION SEPARATION (the key new question) — CLASSIFICATION B
kg_query operate.py:4180:
- :4234 `get_keywords_from_query(...)` runs BEFORE the gate → :4415 → extract_keywords_only
  :4552. Keyword LLM: :4581-4583 reads keywords cache (cache_type="keywords"); on HIT :4584-90
  returns w/o LLM; on MISS :4614-4618 `use_model_func(kw_prompt, response_format=json_object)`
  using `role_llm_funcs["keyword"]` → **1 LLM CALL**; :4627-4655 WRITES keywords cache iff
  `enable_llm_cache`. Short-query (<50 chars) empty-keywords fallback forces ll=[query] :4247-9
  (no LLM), else returns fail_response.
- :4257 `_build_query_context(...)` → entities_vdb/relationships_vdb/chunks_vdb `.query()` →
  EMBED the query/keywords (embedding provider calls) + optional rerank (only if rerank model
  configured; ON sidecar has none per 05 §7 → no rerank call).
- :4275 `if only_need_context and not only_need_prompt: return context_result` → EARLY RETURN.
- :4301+ `# Call LLM` (final answer generation, use_model_func :4349) → ONLY reached when
  only_need_context is False (the /query path).
=> **B — NO_FINAL_ANSWER_LLM_BUT_OTHER_LLM_CALLS_REMAIN.** /query/data retains: keyword LLM (0
   or 1 call) + query embedding(s) + optional rerank(off). Avoids: the final-answer LLM call.

## §8 RETRIEVAL SEMANTICS PARITY = YES
/query (aquery_llm lightrag.py:3936) and /query/data (aquery_data :3837) call the IDENTICAL
`kg_query` with same knowledge_graph_inst, entities_vdb, relationships_vdb, text_chunks,
global_config, hashing_kv=self.llm_response_cache, chunks_vdb. Only diffs: only_need_context
(True for data), system_prompt, progress_callback. Same keyword extraction, same
_build_query_context, same top_k/chunk_top_k/token budgets, same round-robin merge, same
convert_to_user_format. => byte-identical retrieval; the ONLY behavioral delta is the final
answer LLM step. (naive path symmetrical: naive_query both.)

## §15/§16 SIDE EFFECTS: CORPUS=NO, CACHE=CONFIG_DEPENDENT
- kg_query/_build_query_context = READ-ONLY retrieval (VDB .query, graph reads, chunk reads).
  No graph/vector/doc-status/pipeline writes.
- _query_done lightrag.py:4081-4082 = `await self.llm_response_cache.index_done_callback()` —
  flush of the LLM cache ONLY (not corpus).
- Cache writes: keywords cache iff enable_llm_cache (operate.py:4632-4655); /query additionally
  writes the answer/query cache (operate.py:4316-4337+). => CORPUS_MUTATION=NO;
  CACHE_MUTATION=CONFIG_DEPENDENT. QUERY_DATA_SIDE_EFFECT_PROFILE=READ_MOSTLY (read-only corpus;
  optional cache write/flush).

## §11 DATA MINIMIZATION — field text classification (convert_to_user_format utils.py:6076-6197)
- entities[]: entity_name(DERIVED), entity_type(STRUCTURAL), description(**DERIVED_TEXT**, LLM),
  source_id(chunk-id IDENTIFIER), file_path(ON source_id IDENTIFIER; default "unknown_source"),
  created_at(STRUCTURAL), reference_id(STRUCTURAL, added by caller).
- relationships[]: src_id/tgt_id(DERIVED entity names), description(**DERIVED_TEXT**),
  keywords(**DERIVED_TEXT**), weight(STRUCTURAL default 1.0), source_id/file_path(IDENTIFIER),
  created_at(STRUCTURAL), reference_id.
- chunks[]: content(**RAW_SOURCE_TEXT**), file_path(IDENTIFIER), chunk_id(STRUCTURAL),
  reference_id. <-- biggest exposure; NEW vs current path.
- references[]: reference_id(STRUCTURAL freq index), file_path(IDENTIFIER_ONLY).
- metadata: query_mode(STRUCTURAL), keywords{high_level,low_level}(query-DERIVED text, echoes
  query terms — NOT source text), processing_info(counts, STRUCTURAL).
Current /query (ON client): include_references=True, include_chunk_content UNSET → references
carry file_path+reference_id only (query_routes.py:545 gates content on include_chunk_content);
ReferenceItem = {reference_id, file_path, content?}. => current path egresses IDENTIFIER_ONLY;
/query/data returns MORE text (chunk content + descriptions) in the sidecar→ON response.
=> DATA_MINIMIZATION_BETTER=PARTIAL: better provider egress (no context shipped to generation
LLM), richer sidecar→ON payload (needs ON-side projection that discards content pre-persist).

## §13 FAILURE ISOLATION — the answer IS the boundary
/query query_text :531 `aquery_llm` computes structured `data` (references/chunks) at :536-537
FIRST, then the final-answer LLM; ANY exception → HTTP 500 :580-582. So retrieval-success +
generation-failure (LLM timeout/rate-limit/unavailable) => 500 => ON gets NOTHING even though
valid evidence was already computed. /query/data has no final-answer LLM → that failure class is
removed; retrieval-success => 200+data; empty => 200+status:"failure". Shared residual failure:
keyword-extraction LLM (both paths) and sidecar/storage/embedding errors → 500.
Provenance timing (§5): references/chunks built during context construction (convert_to_user_
format), BEFORE and INDEPENDENT of answer generation; generation does not alter references and
is not necessary for provenance.

## Current /query conflation (§5 verdict)
client.query() (ON) conflates evidence retrieval with answer generation: it forces an LLM answer
(aquery_llm) that GraphRAG-04 discards, paying the generation token cost + a failure surface for
zero consumed value, while the evidence ON actually uses (references) is a strict subset of the
structured data /query/data already returns.

## DECISIONS (see doc §34)
QUERY_DATA_AVAILABLE=YES · AVOIDS_FINAL_ANSWER=YES · OTHER_LLM_REMAIN=YES · PARITY=YES ·
PROVENANCE=STRONG(chunk)/PARTIAL(entity·relation) · RANK=NO · SCORE=NO · CORPUS_MUT=NO ·
CACHE_MUT=CONFIG_DEPENDENT · MINIMIZATION_BETTER=PARTIAL · CONTRACT_DESIGNABLE=YES ·
IMPL_READY=NO · PREFERRED=B · ROLE=UNRANKED_EVIDENCE_ENGINE+PROVENANCE_ENRICHER+CONTEXT_EXPANDER
· RRF_READY=NO · GRAPH_CANDIDATE_IMPL_READY=NO
