# GraphRAG Forensic & Architecture Proposal — Phase GraphRAG-01

**Status:** Forensic + proposal only. No implementation, no migration, no dependency, no schema/API/frontend change.
**Branch:** `feature/graphrag-lightrag` · **Date:** 2026-08-27
**Revision:** 2026-08-27 rev-2 — reconciled against an adversarial review. Six findings were re-verified against the current checkout; §4A, §8, §9, §10, §11, §12, §13 were corrected and §18–§20 added. Binding decisions moved to [`../development/GRAPHRAG_DECISION.md`](../development/GRAPHRAG_DECISION.md) (**PROPOSED, not approved**). Where this document and the decision record disagree, the decision record wins.
**Source of truth:** current checkout source + tests + migrations. Graphify (`.graphify/`, built same day) used only as a navigation index; every finding below was verified by direct source read.
**Goal (long-term):** Add LightRAG GraphRAG *alongside* Open Notebook's existing vector RAG (Vector RAG + LightRAG GraphRAG + fusion/rerank = hybrid retrieval). LightRAG runs as an **independent sidecar service**; it is never vendored, and Open Notebook must keep working when LightRAG is unavailable (**fail-open**).

> This document is the deliverable for GraphRAG-01. It ends with a recommendation for approval. **No code has been written and none of the constraints above have been touched.**

---

## 1. Current architecture

Three tiers (per `AGENTS.md`): Next.js frontend (3000) → FastAPI (5055) → SurrealDB (8000), plus a **surreal-commands worker** that runs async jobs (source processing, embedding, insights, podcasts). Without the worker these jobs queue forever.

Runtime shape relevant to RAG:

```
Frontend ──HTTP──> FastAPI (api/routers/*, prefix /api)
                      │
                      ├─ domain layer (open_notebook/domain/*)   ← Source, Notebook, Note, search fns
                      ├─ graphs layer (open_notebook/graphs/*)    ← LangGraph: source, ask, chat, source_chat, transformation
                      ├─ commands (commands/*)                    ← surreal-commands @command jobs, executed by worker
                      ├─ ai layer (open_notebook/ai/*)            ← provision_langchain_model, model_manager (SINGLE provider gateway)
                      └─ repository (open_notebook/database/*)    ← repo_query/repo_insert, migrations (SurrealQL)
                             │
                          SurrealDB  ← source / source_embedding / source_insight / note / notebook + fn::vector_search, fn::text_search
```

Key architectural facts (verified):
- **All AI/embedding calls funnel through `open_notebook/ai/provision.py` + `open_notebook/ai/models.py::model_manager`.** AGRIBANK §8 forbids instantiating alternate provider clients outside this abstraction.
- **All long-running work is a surreal-commands `@command`** submitted fire-and-forget (`submit_command`) or via `CommandService.submit_command_job`, and executed by the worker. Ingestion HTTP responses return before embedding completes.
- **Search is two SurrealQL functions** (`fn::vector_search`, `fn::text_search`) wrapped by `vector_search()`/`text_search()` in `open_notebook/domain/notebook.py`.
- No existing `lightrag`/`graphrag` references anywhere in `open_notebook/`, `api/`, `commands/`, `prompts/`, `docs/` — clean slate.

---

## 2. Source ingestion flow

Verified end-to-end path (`upload → … → citation`):

| Stage | Location | Notes |
|---|---|---|
| upload / receive | `POST /api/sources` → `api/routers/sources.py::create_source` (+ `/sources/json`) | multipart or JSON; `parse_source_form_data` |
| file persistence | `save_uploaded_file` → `UPLOADS_FOLDER` | atomic unique-name; path-traversal guarded |
| input validation | `_build_content_state` | **SSRF guard** (`validate_url`) for links, **LFI guard** for uploads, `_assert_file_supported` (content-core header check) |
| record creation | `_create_source_async_path` / `_create_source_sync_path` | `Source(...).save()` with placeholder title `"Processing..."`; `add_to_notebook` (edge `reference`) |
| job submission | `CommandService.submit_command_job("open_notebook","process_source", SourceProcessingInput)` (async) or `execute_command_sync` (sync, thread) | async returns immediately with `command_id` |
| worker command | `commands/source_commands.py::process_source_command` | surreal-commands `@command`, retry up to 15× |
| **canonical extraction** | `open_notebook/graphs/source.py::source_graph` → `content_process` → **content-core `extract_content(url\|file_path\|content)`** | **This is the single canonical document-extraction pipeline.** Handles PDF/DOCX/URL/YouTube/audio. Emits `ExtractionOutput{title, content}` |
| normalization + save | `save_source` node | sets `source.full_text = extraction.content`, `source.asset`, title (preserves user title); `await source.save()` |
| chunking + embedding | if `embed`: `source.vectorize()` → **fire-and-forget** `embed_source` command → `embed_source_command` | `detect_content_type` → `chunk_text` → `generate_embeddings` (batched via `model_manager`) → bulk `repo_insert("source_embedding")` |
| transformations/insights | `trigger_transformations` → `transform_content` → `source.add_insight` → `create_insight` command (+ `embed_insight`) | fire-and-forget |
| storage | SurrealDB: `source.full_text`, `source_embedding.{order,content,embedding}`, `source_insight`, edge `reference` | schema in `migrations/1.surrealql` |

**Critical design implication:** by the time `save_source` completes, `source.full_text` (canonical text) is durably persisted, and embedding is a *separate, already-decoupled* async job. This is the natural, proven seam for GraphRAG indexing — a second fire-and-forget job that consumes the same canonical `full_text`. No re-parsing of PDF/DOCX is needed or wanted.

---

## 3. Current retrieval flow

Three distinct "retrieval" behaviors exist; only one is similarity retrieval:

1. **Vector search** — `open_notebook/domain/notebook.py::vector_search(keyword, results, source, note, minimum_score=0.2)`:
   - embeds the query via `generate_embedding` (→ `model_manager`),
   - calls SurrealQL `fn::vector_search($embed,$match_count,$sources,$show_notes,$min_similarity)`,
   - `fn::vector_search` (migration 4/latest) does cosine similarity over `source_embedding ∪ source_insight ∪ note`, groups by id, returns `id, parent_id (=source.id), title, similarity, matches(=content)`.
2. **Text search** — `text_search(...)` → `fn::text_search` (BM25 over title/full_text/chunk/insight/note). On SurrealDB highlight "position overflow" it **falls back to `vector_search`** (existing fail-open precedent).
3. **Context assembly (not retrieval)** — `build_notebook_context` / `build_source_context` (`open_notebook/utils/context_builder.py`) select sources/notes by *explicit inclusion config*, token-budgeted. Used by Chat and Source Chat.

API surface:
- `POST /api/search` (`api/routers/search.py`) → vector or text search.
- `POST /api/search/ask` + `/ask/simple` → **Ask graph** (`open_notebook/graphs/ask.py`).
- `POST /api/chat/execute` + `POST /api/chat/context` → **Chat** (`api/routers/chat.py` + `graphs/chat.py`).
- Source Chat graph (`graphs/source_chat.py`).

**Ask graph internals (the RAG core):** `agent` (LLM builds a `Strategy` of ≤5 `Search{term,instructions}`) → `trigger_queries` fan-out → **`provide_answer` calls `await vector_search(term, 10, True, True)`** for each → per-search LLM answer with `payload["ids"] = [r["id"] …]` → `write_final_answer` synthesizes. **Ask retrieval is vector-only** (the `text_search` branch is commented out). This single `vector_search` call in `provide_answer` is the highest-leverage hybrid-retrieval insertion point.

---

## 4. Candidate integration points

Located by Graphify blast-radius (Source is the top bridge node) then verified in source.

### 4A. Indexing insertion (must NOT slow ingestion)
| Option | Where | Ingestion impact | Verdict |
|---|---|---|---|
| **I1 (preferred)** New fire-and-forget `graphrag_index_source` command, submitted from `save_source` (or `process_source_command`) right after `full_text` persists — placement mirrors `Source.vectorize()`; **exception contract must NOT** (see correction below) | `graphs/source.py::save_source` submits; new `commands/graphrag_commands.py` runs on worker | **Zero** added HTTP latency; runs on worker like `embed_source` | ✅ Recommended |
| I2 Extend `embed_source_command` to also push to GraphRAG | `commands/embedding_commands.py` | Couples two concerns; a GraphRAG hiccup could affect embedding retries | ⚠️ Rejected (violates isolation) |
| I3 Index synchronously inside `save_source` | `graphs/source.py` | Adds LightRAG latency to the processing job / sync path | ❌ Rejected |

I1 also gives independent retry, independent failure, and a natural place for a fail-open guard (skip if LightRAG down; source ingestion already succeeded).

> **CORRECTION (rev-2) — do not mirror `Source.vectorize()`'s exception contract.**
> Verified: `Source.vectorize()` raises `DatabaseOperationError` when `submit_command` fails (`open_notebook/domain/notebook.py:576`), and `save_source` awaits it **unguarded** (`open_notebook/graphs/source.py:224`). Copying that contract would let a GraphRAG queue/registration failure fail source processing — violating the §6 fail-open invariant.
>
> The correct in-repo idiom is **`Note.save()`** (`open_notebook/domain/notebook.py:716-727`): submit inside `try`, log on failure, `return None`, never raise — explicitly justified in its own comment because the record "is already durably saved above, so a submission hiccup here shouldn't fail an otherwise-successful save."
>
> **Binding rule:** `graphrag_index_source` submission mirrors `Note.save()`'s isolation, not `vectorize()`'s propagation. `save_source` must remain byte-for-byte in behavior when the flag is off, and must not gain a new failure mode when it is on.

### 4B. Hybrid retrieval insertion (must NOT break vector RAG)
| Option | Where | Blast radius | Verdict |
|---|---|---|---|
| **R1 (preferred, additive)** New `hybrid_search()` in a new module that internally calls the **untouched** `vector_search()` + `GraphRAGClient.query()`, fuses + reranks. New Ask path / flag-gated swap of the one call in `ask.py::provide_answer` | new `open_notebook/retrieval/` (or `domain`), optional 1-line change in `ask.py` behind flag | Small, reversible; vector RAG identical when flag off / LightRAG down | ✅ Recommended |
| R2 Wrap inside `vector_search()` itself | `domain/notebook.py` | Changes existing RAG for every caller (search endpoint, ask) | ❌ Rejected for GraphRAG-01 (edits existing RAG) |
| R3 New endpoint `POST /api/search/graph` only | `api/routers/search.py` (new route) | Zero impact on existing routes; but not "hybrid" until wired into Ask | ➕ Good first delivery slice |

### 4C. Reusable abstractions (for the four new components)
| New component | Reuses / mirrors |
|---|---|
| `GraphRAGClient` | httpx client pattern; config from env (`open_notebook/config.py` + dotenv); fail-open style of `_usable_engine` |
| `GraphRAGService` | thin service like `api/command_service.py`; **must** route LLM/embeddings through `provision_langchain_model` / `model_manager` (AGRIBANK §8) — or delegate them to the sidecar's own configured models under an approved data-egress decision |
| `GraphRAGIndexer` | mirrors `Source.vectorize()` + `embed_source_command` (fire-and-forget `@command`) |
| `HybridRetriever` | composes existing `vector_search()` + `GraphRAGClient`; rerank util new |

---

## 5. Proposed LightRAG sidecar architecture

LightRAG runs as a **separate service** (own container / process), reached only over HTTP by a thin client. Open Notebook stays the **canonical extractor**; it feeds LightRAG already-extracted text.

```
                 ┌────────────────────────── Open Notebook (unchanged core) ──────────────────────────┐
   Source ─▶ content-core extraction ─▶ source.full_text (SurrealDB)                                   │
                                            │                                                          │
                                            ├─▶ existing: Source.vectorize() ─▶ embed_source ─▶ source_embedding
                                            │                                                          │
                                            └─▶ NEW (fire-and-forget): graphrag_index_source ──────────┼──▶ GraphRAGClient
                                                                                                       │        │ HTTP (insert doc + metadata)
                                                                                                       │        ▼
                                                                                            ┌───────── LightRAG sidecar ─────────┐
                                                                                            │  own store (graph + kv + vectors)  │
                                                                                            └────────────────────────────────────┘
   Query ─▶ Ask graph provide_answer                                                                   │        ▲
              │                                                                                         │        │ HTTP (query)
              ├─▶ existing vector_search()  ──────────────┐                                            │        │
              └─▶ (flag) GraphRAGClient.query() ──────────┤                                            │        │
                                                          ▼                                            │        │
                                                  Retrieval fusion ─▶ rerank ─▶ LLM context ───────────┘────────┘
```

- Sidecar is stateless to Open Notebook except via its HTTP API; its persistent graph/vector store is **its own**, keyed by `source_id`.
- Open Notebook never imports LightRAG code (no vendoring). Only a `GraphRAGClient` (httpx) talks to it.
- Deployment: an additional service in `docker-compose` (future phase), on the internal network only (AGRIBANK §6 — do not expose new ports broadly). Not added in this phase.

---

## 6. Failure isolation strategy (fail-open)

**Invariant: if LightRAG is down, slow, or misconfigured, Open Notebook behaves exactly as today.**

- **Indexing:** `graphrag_index_source` is a *separate* fire-and-forget command. If the client can't reach the sidecar, it logs and returns failure for *that job only*; source ingestion + `embed_source` already succeeded independently. Retries are the command's own concern (bounded, like `embed_source`). A permanent GraphRAG outage never blocks or fails source creation.
- **Retrieval:** `HybridRetriever` wraps the `GraphRAGClient.query()` call in try/except + timeout. On any error/timeout it returns only the `vector_search()` results (which are computed regardless). Modeled on existing precedents: `_usable_engine` fallback to "auto", `text_search`→`vector_search` overflow fallback, ContentSettings try/except default.
- **Feature flag:** a single env flag (e.g. `OPEN_NOTEBOOK_GRAPHRAG_ENABLED`, read at boot like CORS/upload-size settings). Off ⇒ zero new behavior, zero new latency, no client instantiated.
- **Circuit-breaker (design):** client short-circuits after N consecutive failures for a cooldown, so a dead sidecar doesn't add per-query latency.
- **Health:** client exposes a health probe; Ask/search degrade silently to vector-only.

---

## 7. Data ownership

| Data | Owner | GraphRAG relationship |
|---|---|---|
| Canonical `full_text`, `title`, `asset`, topics | Open Notebook (`source` table) | **Authoritative.** Sent to LightRAG as input; never re-derived by LightRAG |
| `source_embedding` (chunks + vectors) | Open Notebook | Unchanged; vector RAG owns it |
| `source_insight`, `note` | Open Notebook | Unchanged |
| notebook↔source (`reference`), session↔notebook (`refers_to`) | Open Notebook | Authoritative; passed as metadata to LightRAG for scoping |
| Entity/relation graph, GraphRAG vectors/KV | **LightRAG sidecar** | Derived, disposable, rebuildable from Open Notebook canonical text |

Rebuild story: because LightRAG's store is fully derivable from `source.full_text` + metadata, it can be dropped and re-indexed at any time (mirrors the existing `rebuild_embeddings` coordinator pattern). Open Notebook never depends on LightRAG for correctness of its own data.

---

## 8. Metadata contract

Every document Open Notebook pushes to LightRAG MUST carry (minimum):

> **CORRECTION (rev-2) — the earlier contract leaked provenance. `asset.url` / `asset.file_path` are REMOVED.**
> Raw file paths expose host filesystem layout and original filenames; URLs can carry internal hostnames, signed query parameters, or access tokens. None of it is needed: citation joins on `source_id` alone. The contract is now a **strict allowlist** — never a model dump.

```jsonc
{
  "source_id":    "source:xxxx",      // REQUIRED — join key back to Open Notebook, LightRAG doc id
  "content":      "<source.full_text>", // CANONICAL text (no LightRAG re-extraction)
  "content_hash": "sha256(full_text)",  // idempotency / change detection for re-index
  "title":        "…",                // for citation display
  "notebook_ids": ["notebook:yyyy"],  // ONLY if required for sidecar-side prefilter; NOT authorization (§10)
  "asset_type":   "link|upload|text", // sanitized enum ONLY — never a path or URL
  "contract_version": 2
}
```

Contract rules:
- **Allowlist, not a dump.** Serialize field-by-field from an explicit allowlist. Never `model_dump()`/`dict(source)` into the sidecar payload — that is how `asset` leaked in rev-1 and how any future field would leak silently.
- **Never sent to the sidecar:** raw `file_path`, any local filesystem path, original/signed URL, query strings, credentials, tokens, secrets, `OPEN_NOTEBOOK_ENCRYPTION_KEY`-protected material, real customer identifiers.
- **Open Notebook retains** original URL, raw file path, asset storage details, and all credential-bearing provenance. The UI already resolves these from the `source` record via `source_id`; the sidecar never needs them.
- `source_id` is the stable join key; all GraphRAG query results must resolve back to it for citation and scoping.
- `content` is always Open Notebook's canonical `full_text` — **LightRAG must be configured NOT to parse files itself.**
- `notebook_ids` is a **retrieval prefilter hint only**. It is metadata, not an access-control decision (see §10 correction).
- No secrets, no real customer data in the contract or in examples (AGRIBANK §6). Synthetic data only in tests.
- Contract is versioned (`contract_version`); changing it is a decision record, not an ad-hoc edit.

---

## 9. Indexing lifecycle

1. Source processed → `full_text` persisted (existing).
2. `save_source` (or `process_source_command`) submits fire-and-forget `graphrag_index_source {source_id}` **only if** `OPEN_NOTEBOOK_GRAPHRAG_ENABLED` and `full_text` non-empty. Never awaited.
3. Worker runs `graphrag_index_source_command`: load Source → build metadata contract (§8) → `GraphRAGClient.upsert(document)` (idempotent by `source_id` + `content_hash`).
4. **REINDEX:** re-processing/retry re-sends with new `content_hash`; client upserts (delete-then-insert by `source_id`, mirroring `embed_source_command`'s "DELETE existing then insert", `commands/embedding_commands.py:334-337`).
5. **DELETE:** see the correction below — best-effort-only is withdrawn.
6. **REBUILD:** a coordinator command mirroring `rebuild_embeddings_command` submits one `graphrag_index_source` per existing source.
7. **RECONCILE:** see the correction below.

All steps off the HTTP ingestion path; all fail-open **for indexing**; none change existing embedding behavior.

> **CORRECTION (rev-2) — "an orphaned GraphRAG doc is harmless" is WITHDRAWN. It is not.**
>
> The sidecar stores canonical `full_text`. An orphan is therefore a **second persistent copy of deleted internal document text living outside SurrealDB**. For an internal fork this is a data-retention and confidentiality issue, not a housekeeping detail.
>
> **Verified asymmetry the original document missed.** Vector cleanup does not depend on application code succeeding — it is enforced *inside the database*:
> ```surql
> -- open_notebook/database/migrations/1.surrealql:29
> DEFINE EVENT IF NOT EXISTS source_delete ON TABLE source WHEN ($after == NONE) THEN {
>     delete source_embedding where source == $before.id;
>     delete source_insight  where source == $before.id;
> };
> ```
> So `source_embedding` is cleaned up even if `Source.delete()` (`domain/notebook.py:642`) is bypassed entirely — and it *can* be: `Notebook.delete()` unlinks non-exclusive sources without deleting them (`domain/notebook.py:274-286`), and any direct `DELETE source` fires the event but runs no Python.
>
> **A SurrealDB event cannot make an outbound HTTP call.** GraphRAG deletion therefore has *no* database-level backstop. Best-effort application-level delete is strictly weaker than what vector RAG already guarantees, and a lost delete is silent and permanent.
>
> **Required lifecycle — five verbs, all designed in GraphRAG-03, none implemented now:**
>
> | Verb | Requirement |
> |---|---|
> | INDEX | Fail-open, isolated (§4A correction). Indexing loss is acceptable — it is recoverable by REBUILD. |
> | REINDEX | Idempotent by `source_id` + `content_hash`. Delete-then-insert. |
> | DELETE | **Durable, not best-effort.** Must survive sidecar downtime and process restart. |
> | REBUILD | Full re-derivation from canonical `full_text`. Also the disaster-recovery path. |
> | RECONCILE | Periodic diff of sidecar `source_id` set against SurrealDB; purge sidecar IDs with no live source. Bounded and scoped. |
>
> **Asymmetric failure policy (this is the core rule):** *indexing* may fail open, because a missing graph entry degrades quality only. *Deletion* may **not** fail open, because a missing deletion retains content. Loss-of-availability and loss-of-confidentiality are not interchangeable risks.
>
> **Second, independent defense — query-time validation (§10).** Even a perfect delete pipeline can race an in-flight query. Every `source_id` returned by the sidecar must be re-resolved against live SurrealDB records *before* fusion or prompt insertion; unresolvable IDs are dropped. This makes stale sidecar rows non-exploitable rather than merely unlikely, and it is the reason DELETE durability and query validation are both required rather than either alone.
>
> Deletion-durability mechanism (outbox/tombstone table vs. retried command vs. reconcile-only) is evaluated in the decision record §"Rejected alternatives". **No schema or migration is created in GraphRAG-01 or GraphRAG-02** — an outbox table, if chosen, is a GraphRAG-03 migration requiring its own approval.

---

## 10. Query lifecycle

1. Ask graph `agent` produces `Strategy` (unchanged).
2. For each search term, `HybridRetriever.retrieve(term, k, scope)`:
   - `vec = await vector_search(term, k, source=True, note=True)` (**existing, untouched**),
   - `graph = await GraphRAGClient.query(term, mode=…, scope=…)` inside try/except+timeout (empty list on failure),
   - **validate** graph rows against live Open Notebook records (mandatory — see correction),
   - **fusion**: rank-based merge by `source_id` (see fusion matrix correction),
   - **rerank**: optional cross-encoder / LLM rerank down to `k`,
   - return unified rows preserving `parent_id`/`source_id` + `title` for citation.
3. `provide_answer` consumes the unified rows exactly as it consumes `vector_search` rows today (`payload["results"]`, `payload["ids"]`).
4. Flag off or LightRAG down ⇒ step 2 returns pure `vector_search` output ⇒ identical to today.

Scope: GraphRAG-01 only *designs* this. Whether the first implementation slice is a **new `/api/search/graph` endpoint** (R3, zero blast radius) or the **flag-gated `provide_answer` swap** (R1) is a GraphRAG-02 decision; both are additive.

> **CORRECTION (rev-2a) — metadata is not authorization; scope must be an explicit input.**
>
> Rev-1's step 2 passed no scope at all and relied on `notebook_ids` metadata inside the sidecar. **Metadata is not an access-control mechanism.** A sidecar-side filter is a filter the *sidecar* decides; LightRAG must never be the authorization authority. Open Notebook is, always.
>
> **Verified current baseline — and an important nuance.** Today's retrieval is **globally scoped by design**, not accidentally under-scoped:
> - `vector_search(keyword, results, source, note, minimum_score)` has **no** notebook parameter (`domain/notebook.py:809-815`), and `fn::vector_search` takes none either (`migrations/9.surrealql:4`).
> - `SearchRequest` and `AskRequest` carry **no** `notebook_id` (`api/models.py:39-47`, `56-61`).
>
> So `/api/search` and `/api/search/ask` searching all notebooks is **existing intended behavior**. GraphRAG must not be described as "fixing" it, and must not silently narrow it — that would be an unrequested behavior change to existing RAG.
>
> **The actual requirement, stated precisely:** GraphRAG must not *widen* the effective scope of any endpoint it is wired into, and must be *capable* of honoring a narrower scope when a future notebook-scoped surface requires one.
>
> `RetrievalScope` abstraction (design only):
> ```python
> # DESIGN SKETCH — not implemented in this phase
> class RetrievalScope:
>     notebook_ids:       list[str] | None   # None = global, matching today's semantics
>     allowed_source_ids: set[str]  | None   # None = no allowlist constraint
>     # reserved for future authorization/tenant context; unused today
> ```
> - `scope` is an **explicit required parameter** of hybrid retrieval. No implicit global default that a caller can forget — global must be *stated* (`notebook_ids=None`), never inferred from omission.
> - For endpoints replicating today's global Ask/Search, scope is explicitly global. Documented, not accidental.
> - `notebook_ids` may be passed to the sidecar as a **prefilter for efficiency only**. Correctness never depends on the sidecar honoring it.
>
> **Mandatory post-query validation pipeline**, in this order, before any row reaches fusion or a prompt:
> 1. Resolve each returned `source_id` against **live** SurrealDB records.
> 2. Drop rows whose source no longer exists (stale sidecar / lost delete).
> 3. When scope is narrower than global, re-check the `reference` edge against **current** edges — not against sidecar metadata, which can be arbitrarily stale after a source is moved or unlinked.
> 4. Drop anything unresolved, unauthorized, or out of scope.
> 5. Only then fuse, rerank, and build the prompt payload.
>
> Failing this validation is **fail-closed**: if validation cannot run, drop the graph rows and return vector-only results. Never pass unvalidated rows through on the grounds that validation was unavailable.

> **CORRECTION (rev-2b) — the fusion matrix. "Same embedding model" was over-claimed.**
>
> Rev-1 §13/R2 said to "configure the sidecar to use the same provider/model," treating embedding mismatch as a generic risk. That conflates two independent properties: **shared vector space** and **commensurate scores**. Neither implies the other.
>
> | # | Fusion strategy | Same embedding space required? | Also required |
> |---|---|---|---|
> | A | Shared ANN index / direct vector comparison | **Yes** — same model, dimension, normalization, distance metric | Identical preprocessing |
> | B | Weighted fusion of **raw** similarity scores | Necessary but **not sufficient** | **Score calibration.** Graph relevance and cosine similarity are different quantities on different distributions; adding them is arithmetically valid and semantically meaningless |
> | C | **Reciprocal Rank Fusion** (rank-only) | **No** | Only a stable per-system ordering. Consumes ranks, discards score magnitudes |
> | D | `source_id` / result-set union | **No** | A shared join key — which the §8 contract already guarantees |
> | E | Late cross-encoder / LLM rerank | **No** | The reranker re-scores original text; upstream scores are only candidate generation |
>
> Two conclusions, both narrower than rev-1:
> - **Identical embedding spaces are required only for A, and only for A.** Strategies C, D, and E place *no* constraint on LightRAG's embedding model, dimension, or provider. The sidecar may legitimately use a different embedding model under an approved data-egress decision.
> - **The converse also fails:** using the same provider and model does *not* make LightRAG's graph scores comparable to `fn::vector_search`'s cosine similarity. Same model, still incommensurate. B needs explicit calibration regardless.
>
> **Recommendation for the first hybrid slice: RRF (C), optionally followed by late rerank (E).** RRF needs no shared vector space and no calibration, so it removes embedding-model coupling from the critical path entirely. **Raw-score weighted fusion (B) is prohibited in the first slice**; it may be revisited only with a measured calibration method on a synthetic evaluation set. Strategy A is out of scope — it would require the sidecar to share Open Notebook's ANN index, which contradicts the sidecar-owns-its-store design (§7).

---

## 11. Citation strategy

Current grounding (verified): retrieval rows carry `parent_id` (= `source.id`) + `title`; Ask passes `payload["ids"]`; Source Chat tracks `context_indicators`; prompt templates (`prompts/ask/*`, `chat/system`, `source_chat/system`) instruct the model to ground in provided IDs. There is **no dedicated citation module** to change.

GraphRAG citation plan (additive, preserves the existing contract):
- Every GraphRAG result is resolved to `source_id` via the metadata contract (§8) **before** entering the prompt payload, so downstream ID-based citation works unchanged.
- Graph-derived answers (entities/relations) attach the set of contributing `source_id`s so the model cites the same source records the UI already knows.
- No change to prompt templates required for the vector path; a GraphRAG-specific addition (if any) is a new template, not an edit to existing ones (upstream compatibility, AGRIBANK §7/§9).

> **CORRECTION (rev-2) — "resolves to `source_id`, therefore citation works unchanged" was asserted, not demonstrated. It needs an enforced row contract, staged by phase.**
>
> **Verified citation mechanics.** `provide_answer` builds the allowed-ID set directly from retrieval rows — `ids = [r["id"] for r in results]` (`graphs/ask.py:110`) — and `prompts/ask/query_process.jinja` instructs the model: *"Do not make up documents or document ids… Always use the complete ID exactly as it is provided, including its type prefix,"* closing with the explicit allowlist `{{ids}}`. So **`r["id"]` must be a real, resolvable Open Notebook record ID.** A synthetic LightRAG entity ID injected here would be faithfully emitted as a citation the UI cannot resolve — a broken, unverifiable citation.
>
> **Where I narrow the review's claim.** It implied graph rows lacking per-chunk evidence spans are a defect against current behavior. The source shows otherwise: `fn::vector_search` already returns source-chunk rows whose `id` **is the source** (`source.id as id, source.id as parent_id`, `migrations/9.surrealql:8-11`) with chunk text aggregated into `matches` (`:62`). Source-granular citation carrying supporting text is therefore the *existing* contract, not a new GraphRAG concession. The real requirement is that graph rows be **no weaker** than that — a real record ID plus traceable supporting text — not that they invent finer granularity than vector RAG has.
>
> **Staged requirement (accepted with this modification):**
>
> **GraphRAG-02 — experimental `/api/search/graph` only.** A GraphRAG-specific *diagnostic* schema is acceptable: entity/relation IDs, graph-native scores, debug fields. Justification is structural, not lenient — this endpoint is **not wired into Ask/Chat and feeds no prompt**, so no citation can be emitted from it. It must be explicitly labeled experimental/diagnostic and must not be consumed by any prompt-building path.
>
> **Before ANY GraphRAG row reaches Ask / Chat / a hybrid prompt (GraphRAG-06) — strict contract, enforced in code and tests:**
>
> | Field | Rule |
> |---|---|
> | `id` | **Must be an existing Open Notebook record ID** (`source:…` / `note:…` / `source_insight:…`), verified live. Never a synthetic LightRAG entity/relation ID. |
> | `parent_id` | Preserved with the same semantics as `fn::vector_search` (source-owning record). |
> | `title` | Resolved from the live Open Notebook record, not from sidecar metadata. |
> | `matches` | Traceable supporting text spans, same shape as the vector path. Graph assertions with no supporting text are **not citable**. |
> | `source_ids` | For multi-source entities/relations: the full contributing set, each independently validated. |
> | invalid rows | **Dropped before prompt construction** — never repaired, never passed through, never coerced into a plausible-looking ID. |
>
> **Ownership rule: LightRAG is not a citation authority. Open Notebook is.** The sidecar proposes evidence; Open Notebook decides what is citable. A graph-derived assertion that cannot be traced to a live record with supporting text is a retrieval *signal*, usable for ranking, and must not become a citation.

---

## 12. Security & privacy considerations

- **Data egress (highest priority):** sending `full_text` to a sidecar, and the sidecar calling an LLM/embedding provider, is a governed data-egress decision (AGRIBANK §6, §8). LightRAG's model access must go through Open Notebook's approved provider routes/keys or an explicitly approved sidecar config. No internal document text may reach an unapproved provider.

> **CORRECTION (rev-2) — there are TWO distinct egress boundaries. Rev-1 blurred them, and the blur is the actual danger.**
>
> | | Boundary | Crosses | Governance |
> |---|---|---|---|
> | **A** | Open Notebook → LightRAG sidecar | Process/container. Internal network if self-hosted | Internal network + auth design (§ this section) |
> | **B** | LightRAG sidecar → LLM / embedding provider | **Potentially the organizational perimeter** | **Requires an approved data-egress decision** |
>
> **The trap:** "the sidecar runs on localhost" reads as safe and settles Boundary A — while saying nothing whatsoever about Boundary B. A localhost sidecar configured with a remote provider key sends internal document text to an external API. LightRAG's indexing is *especially* exposed here: entity/relation extraction typically sends **document text to an LLM**, so Boundary B traffic during indexing can approach full-corpus volume rather than a few short queries.
>
> **Binding rules:**
> - Boundary A being internal **never** implies Boundary B is acceptable. They are approved separately.
> - The sidecar's provider configuration is in scope for review even though the sidecar is not Open Notebook code — deployment config is part of the security surface.
> - **GraphRAG-02 through GraphRAG-05 use synthetic, public, or anonymized test data only.** No real internal document content crosses Boundary B until an approved provider/data-egress decision exists (AGRIBANK §6, §12).
> - AGRIBANK §8 forbids alternate provider clients inside Open Notebook. A sidecar with its own independent provider credentials is the same concern wearing a different hat — an approved decision must state explicitly which models the sidecar may reach and with whose keys.
- **No new provider clients** outside `provision`/`model_manager` without an approved ADR (AGRIBANK §8).
- **Network boundary:** sidecar and its store stay on the internal network; no new broadly-exposed ports (AGRIBANK §6). Client→sidecar over TLS/internal only.
- **SSRF/LFI:** GraphRAG never fetches URLs or reads files itself (it only receives already-extracted canonical text), so it introduces no new SSRF/LFI surface. Existing guards in `_build_content_state` remain the only ingress.
- **Secrets:** sidecar credentials via env/secret manager, encrypted at rest (`OPEN_NOTEBOOK_ENCRYPTION_KEY` pattern). Never committed.
- **No real data in artifacts/tests/memory** (AGRIBANK §4, §6) — synthetic corpora only.
- **Auth:** sidecar endpoint authenticated (shared secret/mTLS); not reachable unauthenticated.
- Security-sensitive changes require focused tests + independent review (AGRIBANK §6, §11).

---

## 13. Risks

| # | Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|---|
| R1 | Data egress of internal document text to unapproved LLM/provider via sidecar | Med | **High** | Approved data-egress decision before GraphRAG-02; route models through provision abstraction; keep sidecar internal |
| R2 | ~~Embedding-model mismatch~~ **Restated (rev-2):** using a fusion strategy whose embedding/calibration preconditions are unmet | Med | Med | **Not** "use the same model." Pick RRF (rank-only) or `source_id` union ⇒ mismatch is a non-issue by construction. Same model is required only for shared-vector-space comparison; raw-score weighted fusion needs calibration *in addition to* a shared space. See §10 fusion matrix |
| R3 | Sidecar latency degrades Ask | Med | Med | Timeout + circuit-breaker + fail-open to vector-only |
| R4 | Silent divergence between `source.full_text` and LightRAG copy after edits | Med | Low/Med | `content_hash` idempotency + upsert-on-reprocess + rebuild coordinator |
| R5 | Scope creep into existing RAG / API / schema | Med | High | Additive-only rule; flag-gated; R1/I1 chosen specifically to avoid editing existing RAG |
| R6 | Duplicate parsing / double extraction cost | Low | Med | Contract forbids sidecar file parsing; only canonical text sent |
| R7 | Orphaned GraphRAG docs on delete failure — **retains deleted document text outside SurrealDB** | Med | ~~Low~~ **High** (rev-2: confidentiality/retention, not housekeeping) | Durable deletion (not best-effort) + query-time `source_id` validation + RECONCILE job. See §9 correction |
| R8 | Upstream merge friction | Low | Med | Keep new code additive + under internal modules; document divergence (AGRIBANK §7) |
| R9 | **(new, rev-2)** GraphRAG enqueue failure propagates into source processing, breaking ingestion | Med | **High** | Isolated best-effort submit mirroring `Note.save()`, never `vectorize()`. Required failure tests (§18). See §4A correction |
| R10 | **(new, rev-2)** Synthetic LightRAG entity ID becomes an unresolvable citation in Ask | Med | **High** | Strict row contract before any Ask wiring; `id` must be a live record ID; invalid rows dropped. See §11 correction |
| R11 | **(new, rev-2)** Internal text reaches an unapproved provider via the sidecar (Boundary B) while Boundary A "localhost" is mistaken for full safety | Med | **High** | Two-boundary model; synthetic data only until approved egress decision. See §12 correction |
| R12 | **(new, rev-2)** Metadata-only scoping lets graph rows widen an endpoint's effective scope | Med | **High** | Explicit `RetrievalScope`; fail-closed post-query re-validation against current `reference` edges. See §10 correction |

---

## 14. Alternatives considered

- **A1 — Vendor LightRAG into the repo.** Rejected: violates "independent sidecar / no vendoring", couples upstream merges to LightRAG internals, bloats blast radius, drags LightRAG deps into Open Notebook (forbidden this phase and undesirable long-term).
- **A2 — Replace vector RAG with GraphRAG.** Rejected: explicit user constraint ("KHÔNG thay thế RAG hiện tại"); removes a working, tested path; loses fail-open.
- **A3 — Index inside the synchronous ingestion path.** Rejected: adds LightRAG latency to source processing; breaks "no ingestion slowdown".
- **A4 — Hybrid by wrapping `vector_search()` directly (R2).** Rejected for GraphRAG-01: changes existing RAG for all callers; not additive; higher blast radius.
- **A5 — Let LightRAG ingest raw files/URLs itself.** Rejected: double extraction, duplicate SSRF/LFI surface, loses Open Notebook as canonical extractor.
- **A6 — Store GraphRAG graph inside SurrealDB (new tables).** Rejected this phase: requires schema change/migration (forbidden) and couples the graph store to the primary DB; sidecar owning its store is cleaner and rebuildable.

---

## 15. Recommended architecture

**Adopt the preferred flow: canonical extraction → (existing vectorization ‖ async GraphRAG indexing via I1) → LightRAG sidecar; query = existing vector retrieval ‖ LightRAG retrieval → fusion → rerank → LLM context (R1), all fail-open and flag-gated.**

Concretely, for the *future* implementation phases:
- **Indexing = I1**: new fire-and-forget `graphrag_index_source` command submitted after `full_text` persists, mirroring `Source.vectorize()`. Zero ingestion latency, independent failure/retry.
- **Retrieval = R1 (additive)**: new `HybridRetriever` composing the **untouched** `vector_search()` with a `GraphRAGClient`; fuse + rerank; expose first as a **new `/api/search/graph`-style path (R3)** to keep blast radius at zero, then optionally flag-gate the single `provide_answer` call.
- **Four new components**, all additive: `GraphRAGClient` (httpx, fail-open, circuit-breaker), `GraphRAGService` (thin, models via provision abstraction), `GraphRAGIndexer` (surreal-commands `@command`), `HybridRetriever` (fusion + rerank).
- **Fail-open everywhere**; **feature flag** `OPEN_NOTEBOOK_GRAPHRAG_ENABLED` (default off); **metadata contract** (§8) as the join/citation key; **data-egress decision recorded** before any code.

This satisfies every stated constraint: existing RAG untouched, API/schema/frontend untouched, no vendoring, fail-open, canonical extraction owned by Open Notebook, and citation preserved via `source_id`.

---

## 16. Files likely to change in FUTURE implementation (not this phase)

> Indicative only. No edits made in GraphRAG-01.

**New files (additive — preferred, lowest blast radius):**
- `commands/graphrag_commands.py` — `graphrag_index_source` (+ rebuild coordinator) `@command`s.
- `open_notebook/graphrag/client.py` — `GraphRAGClient` (httpx, timeout, circuit-breaker, fail-open).
- `open_notebook/graphrag/service.py` — `GraphRAGService` (models via `provision`/`model_manager`).
- `open_notebook/graphrag/indexer.py` — `GraphRAGIndexer` (metadata contract builder).
- `open_notebook/retrieval/hybrid.py` — `HybridRetriever` (fusion + rerank).
- `api/routers/graphrag.py` (or `search.py` new route) — optional `/api/search/graph` (R3).
- `tests/…` — synthetic-data unit/integration tests (fail-open, contract, fusion).
- `docs/agribank/architecture/` — data-egress decision record; `DECISIONS.md` entry.

**Existing files touched minimally (only when wiring is approved):**
- `open_notebook/graphs/source.py` — 1 fire-and-forget submit in `save_source` (flag-gated). *Additive line; existing behavior unchanged when flag off.*
- `open_notebook/domain/notebook.py` — optional GraphRAG delete hook in `Source.delete()` (best-effort, fail-open).
- `api/main.py` — `include_router` for optional new router.
- `open_notebook/config.py` / `.env.example` — new env flag + sidecar URL.
- `docker-compose.yml` — add sidecar service (internal network).
- `open_notebook/graphs/ask.py` — *only if* R1 chosen over R3: swap the single `vector_search` call for `hybrid_search`, flag-gated.

---

## 17. Files that should NOT need to change

- **Existing search core:** `open_notebook/domain/notebook.py::vector_search`/`text_search` bodies, and SurrealQL `fn::vector_search`/`fn::text_search` (**no new migration**).
- **Embedding pipeline:** `commands/embedding_commands.py`, `open_notebook/utils/embedding.py`, `open_notebook/utils/chunking.py` — vector RAG stays byte-for-byte.
- **SurrealDB schema / migrations:** none added or altered (`migrations/*.surrealql`). No `source`, `source_embedding`, `source_insight`, `note`, `reference` change.
- **Existing API contracts:** `/api/sources*`, `/api/search`, `/api/search/ask*`, `/api/chat*` request/response models (`api/models.py`) unchanged.
- **Context assembly:** `open_notebook/utils/context_builder.py` (Chat/Source-Chat) unchanged.
- **Frontend:** entire `frontend/` — no change (AGRIBANK §9).
- **AI provisioning:** `open_notebook/ai/provision.py`, `models.py` signatures unchanged (reused, not modified).
- **content-core extraction path:** `content_process` extraction logic unchanged — it remains the canonical extractor.

---

## Verification / provenance

- All call paths verified by reading: `api/routers/sources.py`, `api/routers/search.py`, `api/routers/chat.py`, `commands/source_commands.py`, `commands/embedding_commands.py`, `open_notebook/graphs/{source,ask,chat,source_chat}.py`, `open_notebook/domain/notebook.py`, `open_notebook/utils/context_builder.py`, `open_notebook/database/migrations/{1,3,4}.surrealql`, `api/main.py`, `open_notebook/config.py`.
- Graphify (`.graphify/GRAPH_REPORT.md`) used to locate the `Source` bridge node and community boundaries; treated as index, not truth (AGRIBANK §3).
- No `lightrag`/`graphrag` references pre-exist in product code — additive integration is unobstructed.
- **No source code, schema, migration, dependency, or frontend was modified in this phase.**

### rev-2 reconciliation provenance (2026-08-27)

Adversarial review reconciled against the current checkout. Verified by direct read, not accepted on assertion:

| Claim under test | Evidence | Outcome |
|---|---|---|
| `vectorize()` propagates submission failure into `save_source` | `domain/notebook.py:552-576`; `graphs/source.py:224` (unguarded `await`) | Confirmed — §4A corrected |
| A correct fail-open submit idiom already exists | `domain/notebook.py:716-727` (`Note.save()`, try/except/log/`return None`, with rationale comment) | **Found during reconciliation, not in the review** — adopted as the binding pattern |
| Vector cleanup is DB-enforced, GraphRAG cannot be | `migrations/1.surrealql:29-32` (`DEFINE EVENT source_delete`) | **Found during reconciliation** — strengthens R7 from Low to High |
| Source delete can bypass `Source.delete()` | `domain/notebook.py:274-286` (`Notebook.delete()` unlinks without deleting) | Confirmed — reinforces need for RECONCILE |
| `vector_search` has no notebook filter | `domain/notebook.py:809-815`; `migrations/9.surrealql:4` | Confirmed |
| Ask/Search are global by design, not under-scoped | `api/models.py:39-47`, `56-61` (no `notebook_id`) | Confirmed — narrows the review's scoping claim |
| Ask citation IDs must be real record IDs | `graphs/ask.py:110`; `prompts/ask/query_process.jinja` | Confirmed |
| Vector rows already cite at source granularity with spans | `migrations/9.surrealql:8-11, 62` | Confirmed — narrows the review's citation claim to "no weaker than existing" |
| Upsert idiom for REINDEX exists | `commands/embedding_commands.py:334-337, 386` | Confirmed |
| No pre-existing `lightrag`/`graphrag` references | scoped grep over `open_notebook/`, `api/`, `commands/`, `prompts/` | Confirmed — clean slate |

---

## 18. Required failure tests (future phases — none written in GraphRAG-01/02 design)

Fail-open is a claim about behavior; behavior claims require tests. AGRIBANK §10 baseline plus:

**Indexing isolation (§4A) — each must prove source processing and vector indexing still complete:**
1. `OPEN_NOTEBOOK_GRAPHRAG_ENABLED=false` — no client instantiated, no submission attempted.
2. `graphrag_index_source` command not registered.
3. `submit_command` raises — must **not** surface from `save_source`.
4. Sidecar connection refused.
5. Sidecar timeout / hang.
6. Malformed or non-JSON sidecar response.
7. Sidecar returns HTTP 5xx, and separately 401/403.

**Deletion & staleness (§9):**
8. Source deleted while sidecar unreachable — deletion intent durably retained, replayed on recovery.
9. Sidecar returns a `source_id` whose record no longer exists — dropped before fusion.
10. Source deleted mid-query — no citation to the deleted source.
11. RECONCILE purges orphans and leaves live entries untouched.
12. `Notebook.delete()` unlink path does not silently orphan sidecar entries.

**Scope (§10):**
13. Graph row from a non-scoped notebook — dropped when scope is narrower than global.
14. Source unlinked from a notebook after indexing — stale `notebook_ids` metadata does **not** grant access.
15. Global-scope endpoints return exactly today's results (regression guard).
16. Validation itself failing ⇒ fail-closed to vector-only.

**Fusion (§10) & citation (§11):**
17. RRF correctness with deliberately different embedding dimensions on each side.
18. Raw-score weighted fusion is rejected/unavailable in the first slice.
19. Synthetic entity ID never enters `payload["ids"]`.
20. Graph row without supporting text is not citable.
21. Ask output cites only IDs present in the validated allowlist.

**Rollback (§19):** items 22-26 below.

All tests use synthetic data only (AGRIBANK §4, §6).

---

## 19. Rollback / removability acceptance criteria

The architecture must **demonstrate**, not assert, that LightRAG is fully removable:

22. `OPEN_NOTEBOOK_GRAPHRAG_ENABLED=false` ⇒ byte-for-byte baseline behavior; zero new latency.
23. Sidecar service removed from deployment ⇒ vector search, Ask, Chat, source ingestion all work.
24. Sidecar's entire datastore deleted ⇒ vector RAG unaffected (it never reads sidecar state).
25. Pending/stale `graphrag_index_source` jobs in the queue ⇒ worker starts cleanly; other commands unaffected.
26. Source deletion works with GraphRAG disabled, enabled-but-unreachable, and mid-outage.

**No hard dependency** may be created from `Source` domain, `vector_search`, the Ask graph, or worker startup onto LightRAG runtime. Verifiable by removing `open_notebook/graphrag/` and confirming the baseline still imports, boots, and passes tests.

**Full removal check:** deleting the GraphRAG modules, the flag, and the sidecar must return the repo to baseline behavior with no orphaned imports or dead migrations. This is the concrete answer to "can LightRAG be removed without breaking existing vector RAG" — yes, **provided** no migration is added to core tables and no existing call site hard-depends on it. Both properties are preserved by the additive I1/R1/R3 design and must be re-verified at each phase gate.

---

## 20. Revised phase roadmap (supersedes any earlier phase list)

| Phase | Scope | Explicitly NOT in scope |
|---|---|---|
| **GraphRAG-01** | Forensic + architecture proposal. **This document.** | Any code |
| **GraphRAG-02** | Isolated `GraphRAGClient` + sidecar + health probe + experimental `/api/search/graph` (diagnostic schema only) | **No** Source-pipeline wiring, **no** Ask wiring, **no** DB migration, **no** real data |
| **GraphRAG-03** | Async indexing lifecycle: INDEX / REINDEX / DELETE / REBUILD / RECONCILE design + durable-deletion mechanism | Ask wiring; hybrid fusion |
| **GraphRAG-04** | Graph retrieval evaluation on a synthetic dataset | Production data; Ask wiring |
| **GraphRAG-05** | `HybridRetriever` with RRF / rank fusion + `RetrievalScope` | Raw-score weighted fusion; Ask wiring |
| **GraphRAG-06** | Ask integration behind flag + **strict provenance/citation contract enforced** | Enabling by default |
| **GraphRAG-07** | Security, authorization, operational hardening, runbook | — |

Each phase requires passing acceptance criteria and verification evidence before the next begins (AGRIBANK §10). **An agent asserting completion is not completion.**

