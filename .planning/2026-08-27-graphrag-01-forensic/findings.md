# Findings — GraphRAG-01 Forensic

> Verified against current checkout on branch `feature/graphrag-lightrag` (2026-08-27).
> Graphify graph (.graphify/, built same day) used as navigation index only; every
> claim below confirmed by direct source read. No secrets/real data included.

## Source ingestion lifecycle (verified call path)
1. `POST /api/sources` → `api/routers/sources.py::create_source` (multipart or JSON via `/sources/json`).
   - Form parsed by `parse_source_form_data`; upload saved by `save_uploaded_file` → `UPLOADS_FOLDER`.
   - `_build_content_state` does SSRF guard (`validate_url`) for links + LFI guard for uploads + `_assert_file_supported`.
2. Two paths:
   - **async** `_create_source_async_path`: `Source(...).save()` (placeholder title), `add_to_notebook`, then `CommandService.submit_command_job("open_notebook","process_source", SourceProcessingInput)`. Returns immediately with command_id.
   - **sync** `_create_source_sync_path`: `execute_command_sync(...,"process_source", timeout=300)` in a thread.
3. `commands/source_commands.py::process_source_command` (surreal-commands `@command`, worker) → invokes `open_notebook/graphs/source.py::source_graph.ainvoke`.
4. `source_graph` (LangGraph): `content_process` (content-core `extract_content` = **canonical extraction**, url/file/text) → `save_source` (sets `source.full_text`, `source.asset`, title; if `embed`: `source.vectorize()`) → conditional `transform_content` (per transformation → `source.add_insight`).
5. `Source.vectorize()` (domain/notebook.py) submits **fire-and-forget** `embed_source` command.
6. `commands/embedding_commands.py::embed_source_command` (worker): load Source → DELETE old `source_embedding` → `detect_content_type` → `chunk_text` → `generate_embeddings` (batched) → bulk `repo_insert("source_embedding", ...)`.

## Canonical text & storage
- Canonical/full text: `source.full_text` (table `source`, `full_text` field). **Single source of truth for text.** (migration 1.surrealql)
- Chunks + vectors: table `source_embedding` (fields: `source` rec-link, `order` int, `content` string, `embedding` array<float>).
- Insights: `source_insight` (own embedding). Notes: `note` (own embedding).
- notebook↔source: graph edge `reference` (RELATE source->reference->notebook). session↔notebook: `refers_to`.
- DB event `source_delete` cascades delete of embeddings+insights; `Source.delete()` also unlinks + removes file.

## Retrieval / Ask / Chat / Citation
- Search entrypoints: `open_notebook/domain/notebook.py::vector_search()` / `text_search()` → SurrealQL `fn::vector_search` / `fn::text_search` (defined in migrations 1,3,4,9,10).
- `fn::vector_search` returns rows: `id, parent_id (=source.id), title, similarity, matches(content)`. Cosine over `source_embedding` ∪ `source_insight` ∪ `note`, filtered by `min_similarity` (default 0.2), LIMIT match_count.
- API: `POST /api/search` (`api/routers/search.py`) → vector or text search.
- **Ask** = `POST /api/search/ask[/simple]` → `open_notebook/graphs/ask.py` (LangGraph): `agent` builds Strategy (≤5 searches) → `provide_answer` fan-out each calls `await vector_search(term,10,True,True)` → per-search LLM answer → `write_final_answer`. **Ask uses vector_search ONLY** (text_search commented out).
- **Chat** = `POST /api/chat/execute` → context comes from `POST /api/chat/context` = `build_notebook_context` (explicit source/note inclusion config; NOT similarity retrieval). Chat graph (`graphs/chat.py`) just injects context string into system prompt.
- **Source Chat** = `graphs/source_chat.py` → `build_source_context` (single source full_text + insights, token-budgeted). No retrieval.
- Citation: not a discrete module. Grounding = provenance carried in retrieval rows (`parent_id`=source id, `title`) + `context_indicators` in source_chat + prompt templates under `prompts/` (ask/*, chat/system, source_chat/system). Answers cite via IDs passed into prompt payload (`ask.py` sets `payload["ids"]`).

## Abstractions available for reuse
- AI provisioning: `open_notebook/ai/provision.py::provision_langchain_model(...)` + `open_notebook/ai/models.py::model_manager` (embedding/chat model resolution). All model access goes through this — GraphRAG LLM/embedding must too (AGRIBANK §8).
- Background jobs: surreal-commands `@command` in `commands/` + `CommandService.submit_command_job` / `submit_command` (fire-and-forget). Worker = `make worker-start`.
- Repo: `open_notebook/database/repository.py::repo_query/repo_insert/ensure_record_id`.
- Config/env: `open_notebook/config.py` + `.env` (dotenv). Feature flags read from env at boot (see `engine_runtime_missing`, CORS parsing).
- Router registration: `api/main.py` include_router with `/api` prefix (23 routers).
- Fail-open precedent already in code: `_usable_engine` (falls back to auto), `text_search` falls back to `vector_search` on overflow, ContentSettings load wrapped in try/except.

## Key insertion points identified
- **Indexing (no ingestion slowdown):** mirror `Source.vectorize()` — submit a NEW fire-and-forget `graphrag_index_source` command from `save_source` (graphs/source.py) or from `process_source_command`, AFTER `full_text` is persisted. Runs on worker, off the HTTP path. Never awaited by ingestion.
- **Hybrid retrieval (no vector-RAG break):** wrap at `vector_search()` boundary OR add a `HybridRetriever` used by a NEW ask path. Preferred: additive — keep `vector_search` untouched; add `hybrid_search` that calls existing `vector_search` + `GraphRAGClient.query`, fuses + reranks. Ask graph `provide_answer` is the single highest-leverage call site (one line: `vector_search` → `hybrid_search`), but changing it edits existing RAG → for GraphRAG-01 keep as design option, gated by flag.

## Constraints reconfirmed (no action this phase)
No code, no migration, no dep, no schema change, no frontend, no LightRAG install. Report + proposal only.

## Env / dims note
- Embedding batch size env: `OPEN_NOTEBOOK_EMBEDDING_BATCH_SIZE`. Embedding model resolved via `model_manager.get_embedding_model()` (dimension is provider-dependent, not hardcoded). LightRAG must be configured to use the SAME provider/model via provision abstraction or share Open Notebook's embeddings; do not let it embed independently with a mismatched model.
