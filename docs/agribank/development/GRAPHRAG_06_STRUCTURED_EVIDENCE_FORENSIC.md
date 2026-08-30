# GraphRAG-06 — Structured Evidence Surface (FORENSIC / DESIGN GATE)

**Status: FORENSIC/DESIGN-ONLY — no implementation.** No production code, no retrieval code,
no `client.query_data()`, no `/query/data` wiring, no RRF/fusion/reranker/aggregation, no tests,
no migrations, no API/frontend, no provider traffic, no DB or LightRAG-storage mutation.
`OPEN_NOTEBOOK_GRAPHRAG_ENABLED` remained **false**; the sidecar was **not** started (every
contract question below was answerable statically from pinned source).

**Frozen inputs (not reopened or weakened):**
- GraphRAG-04 approved: commit `cb86a06…`, tag `graphrag-04-approved`
  (`RRF_CANDIDATE_INTERFACE_READY = NO`, `HYBRID_VALUE_EVIDENCED = INCONCLUSIVE`).
- GraphRAG-05 forensic approved: commit `833ec59…`, tag `graphrag-05-forensic-approved`
  (`LIGHTRAG_DIRECT_SCORED_SOURCE_SURFACE = NO`; no valid rank/score on any HTTP surface;
  `SOURCE_PROVENANCE = STRONG(chunk)/PARTIAL(KG)`; `GRAPH_CANDIDATE_IMPLEMENTATION_READY = NO`).

**Pinned source of truth.** All LightRAG findings are read from the pinned tag, not docs and not
`main`:

```
HKUDS/LightRAG  tag v1.5.6  commit b33c6b0812cddf39206e48a9810112e51f025274
lightrag/_version.py:  __version__ = "1.5.6"   __api_version__ = "0328"
```

The GraphRAG-05 out-of-repo scratch clone was no longer on disk; a local `/d/Project Web/LightRAG`
checkout is **v1.5.0** (`c2b9a5c6`) and is **not** valid evidence for v1.5.6. Per the same method
GraphRAG-05 used and recorded, the pinned tag was re-obtained by a read-only
`git clone --depth 1 --branch v1.5.6` into the out-of-repo scratchpad (public open-source code;
**not** a provider call, **not** internal-data egress, **not** committed). The HEAD commit hash was
verified to equal `b33c6b0812…` before any read. Where docs and code disagree, **code wins**; every
finding cites `file:line` in the pinned tree.

---

## 0a. Historical precision correction (GraphRAG-05 shorthand)

GraphRAG-05 §3 recorded, of `/query/data`: *"One call without answer generation? Yes
(`only_need_context=True`, **no LLM**)."* Read literally, "no LLM" is **imprecise**. The
GraphRAG-05 checkpoint is approved/frozen and is **not** amended, rewritten, or altered; this
correction is recorded here in GraphRAG-06 only.

**Corrected classification (pinned v1.5.6, executable behavior):** `/query/data` eliminates the
**final-answer generation** LLM call **only**. Retrieval-side provider/model work **remains**:
- **keyword-extraction LLM** (`operate.py:4234 → 4618`), subject to keyword-cache behavior;
- **query embedding** calls (VDB `.query`);
- **reranker** if a rerank model is configured in some runtime (none in ON's deployed sidecar).

Therefore the correct label is **`NO_FINAL_ANSWER_LLM_BUT_OTHER_LLM_CALLS_REMAIN`**, never
`NO_LLM`. Throughout this document `/query/data` is described as *"skips final-answer generation
but retains retrieval-side model work,"* not *"LLM-free."*

---

## 0. GraphRAG-06 question

> Should Open Notebook introduce a **structured GraphRAG evidence seam** based on pinned
> LightRAG v1.5.6 `/query/data` instead of relying on the current **answer-generating**
> `client.query()` (`/query`) for future GraphRAG evidence consumption?

**Answer (headline): YES — Option B is the preferred future direction, but implementation is NOT
ready (design gate).** `/query/data` is the same retrieval as `/query` with the final-answer LLM
step removed; it exposes strictly **more provenance** (entities/relations/chunks, not just the
frequency-ranked reference list), a **cleaner failure boundary**, and **lower provider egress** —
while adding **no** rank and **no** score (both remain `NO`, per GraphRAG-05). The current
`client.query()` forces an LLM answer that GraphRAG-04 **discards**, so it pays generation cost and
a failure surface for zero consumed value. It is **not** implemented here; a future isolated
integration phase and the larger-corpus evaluation from GraphRAG-05 §12 gate any build.

---

## 1. Current path — exact call graph (Forensic Target A)

Open Notebook wires exactly one query: `GraphRAGClient.query()`
(`open_notebook/integrations/graphrag/client.py:538-608`), reached only through the diagnostic
`POST /api/search/graph` (`api/routers/graphrag.py:50`) via
`GraphRAGService.query_strict()`/`query()` (`service.py:263-306`). **There is no `query_data()`
method in the client** (verified: only `query()` exists). Ask/Chat are untouched.

```
ON GraphRAGService.query_strict()          service.py:280
  └─ GraphRAGClient.query(mode=hybrid)      client.py:538
       body {query, mode:"hybrid", include_references:true, top_k?}   client.py:553-559
  └─ POST /query                            query_routes.py:447  query_text()
       param = to_query_params(False)       query_routes.py:524
       result = await rag.aquery_llm(...)   query_routes.py:531   ← FULL pipeline + LLM ANSWER
         └─ kg_query(...)                   operate.py:4180
              ├─ get_keywords_from_query    operate.py:4234  → extract_keywords_only :4552
              │     KEYWORD-EXTRACTION LLM   operate.py:4618  (or keywords-cache hit :4584)
              ├─ _build_query_context        operate.py:4257  (VDB .query = query EMBEDDING; rerank if cfg)
              │     convert_to_user_format   utils.py:6076    → data.{entities,relations,chunks,references}
              ├─ [only_need_context? NO]     operate.py:4275  (gate not taken on /query)
              └─ FINAL-ANSWER LLM            operate.py:4301-4349  ← generation over full context
       llm_response.content + data.references   query_routes.py:535-537
       (content added to references ONLY if include_chunk_content)   query_routes.py:545
       any Exception → HTTP 500              query_routes.py:580-582
  → QueryResponse{response, references:[ReferenceItem{reference_id,file_path,content?}], response_time}
  └─ client maps references[].file_path → source_id (unordered GraphReference)   client.py:569-608
```

**Answers to task §5 (traced, not inferred):**
- **Endpoint used now:** `POST /query` (answer-generating). `/query/data` is **not** called.
- **Forces an LLM answer?** **Yes.** `query_text` always calls `aquery_llm` (query_routes.py:531);
  in hybrid mode this reaches the final-answer LLM (operate.py:4301-4349).
- **What ON consumes:** `response` (answer string; **required to parse**, else
  `GraphRAGProtocolError`, client.py:564-567), `references[].{file_path→source_id, reference_id,
  content→excerpts}`, `response_time`.
- **What ON ignores:** the **answer string itself** for evaluation (GraphRAG-04 discards it); the
  internally-computed `data.{entities, relationships, chunks}` are **never surfaced** by `/query`
  to ON (they exist in `result["data"]`, query_routes.py:536, but only `references` is returned).
- **References before or after generation?** **Before.** `data.references`/`chunks` come from
  `convert_to_user_format` during context construction (operate.py:4257 / utils.py:6076), computed
  before the final LLM call. Generation does **not** alter references.
- **Is answer generation necessary for provenance?** **No.** Provenance is fully built during
  retrieval; the answer is downstream and discarded.
- **Does generation change retrieval/context or references?** **No** — retrieval/context are fixed
  before generation; `conversation_history` "is sent to LLM only, does not affect retrieval"
  (query_routes.py:459).
- **Token/provider cost incurred purely because of generation:** one **final-answer LLM call** over
  the entire assembled context (the largest prompt on the path, operate.py:4301-4313) — pure waste
  given ON discards the answer.
- **Failure modes introduced by generation:** the final-answer LLM adds a failure point
  (timeout/rate-limit/provider-unavailable). **Retrieval can succeed but generation fail**, and
  because any exception in `aquery_llm` maps to **HTTP 500** (query_routes.py:580-582), the
  already-computed valid evidence in `data` is **lost to ON** (§6, §7 below).
- **Does `client.query()` conflate evidence retrieval with answer generation?** **Yes.** It couples
  a discarded generation step to the evidence ON actually wants, inheriting generation cost and
  failure for no benefit.

---

## 2. `/query/data` — exact call graph (Forensic Target B)

```
POST /query/data                             query_routes.py:1013 (decorator) / :1309 query_data()
  response_model=QueryDataResponse; Depends(combined_auth)   query_routes.py:1014-1015
  param = request.to_query_params(False)     query_routes.py:1413
  response = await rag.aquery_data(query, param)   query_routes.py:1414
    └─ aquery_data                           lightrag.py:3701
         data_param = QueryParam(..., only_need_context=True, ...)   lightrag.py:3815-3831
         [local/global/hybrid/mix] kg_query(... hashing_kv=llm_response_cache ...)  lightrag.py:3837
              ├─ get_keywords_from_query      operate.py:4234   KEYWORD-EXTRACTION LLM (or cache hit)
              ├─ _build_query_context         operate.py:4257   (query EMBEDDING; rerank if cfg)
              │     convert_to_user_format    utils.py:6076
              └─ only_need_context → RETURN   operate.py:4275   ← EARLY RETURN, no final-answer LLM
         [naive] naive_query(...)             lightrag.py:3851
         [bypass] empty data                  lightrag.py:3860
         await self._query_done()             lightrag.py:3904  (flush llm_response_cache only)
  return QueryDataResponse(**response)        query_routes.py:1418
  any Exception → HTTP 500                     query_routes.py:1427-1429
```

`aquery_data` reuses the same `kg_query` as `aquery_llm` but forces `only_need_context=True`
(lightrag.py:3817), so the pipeline **early-returns at operate.py:4275** before the final-answer LLM
(operate.py:4301+). `to_query_params(False)` drops `include_chunk_content`/`include_progress`
(query_routes.py:221-233); `/query/data` "always includes references" regardless of
`include_references` (query_routes.py:1408-1410).

---

## 3. Answer-generation separation (task §7) — CLASSIFICATION **B**

The most important GraphRAG-06 question. Do **not** simplify to "/query/data = no LLM."

Traced in `kg_query` (operate.py:4180): keyword extraction runs **before** the `only_need_context`
gate.

| Step | Location | Runs on `/query/data`? | LLM/provider work |
|---|---|---|---|
| Keyword extraction | operate.py:4234 → 4552 | **Yes** | `use_model_func(kw_prompt, json_object)` operate.py:4618 — **1 LLM call**, unless keywords-cache **hit** (:4584-4590) or short-query (<50 chars) empty-keyword fallback forces `ll=[query]` (:4247-4249, no LLM) |
| Query embedding | operate.py:4257 → `*_vdb.query()` | **Yes** | embedding-provider call(s) to embed query/keywords for VDB search |
| Rerank | utils.py `apply_rerank_if_enabled` | **only if a rerank model is configured** | ON sidecar configures **LLM + embedding only, no rerank** (GraphRAG-05 §7) → **no rerank call** |
| Final-answer generation | operate.py:4301-4349 | **No** (early return :4275) | **avoided** |

**Classification (task §7): B — `NO_FINAL_ANSWER_LLM_BUT_OTHER_LLM_CALLS_REMAIN`.** `/query/data`
still triggers the keyword-extraction LLM (0 or 1 call) and query-embedding calls; it eliminates
**only** the final-answer LLM generation. It is emphatically **not** `A (NO_LLM)`.

---

## 4. Retrieval-semantics parity (task §8) = **YES**

`/query` (`aquery_llm`, lightrag.py:3936-3948) and `/query/data` (`aquery_data`, lightrag.py:3837-
3848) call the **identical** `kg_query` with the same `knowledge_graph_inst`, `entities_vdb`,
`relationships_vdb`, `text_chunks`, `global_config`, `hashing_kv=self.llm_response_cache`, and
`chunks_vdb`. The **only** differences are `only_need_context` (True for data),
`system_prompt`, and `progress_callback`.

| Dimension | Same? | Evidence |
|---|---|---|
| keyword extraction | Yes | both → `get_keywords_from_query` operate.py:4234 |
| local/global/hybrid logic | Yes | both → `_build_query_context` operate.py:4257 |
| top_k / chunk_top_k | Yes | same `data_param` copy carries them lightrag.py:3821-3822 |
| chunk / entity / relation selection | Yes | same `_perform_kg_search` / merge |
| reranker path | Yes | same `apply_rerank_if_enabled` (both gated on config) |
| context/token budget | Yes | same `max_*_tokens` fields lightrag.py:3823-3825 |
| reference generation | Yes | same `convert_to_user_format` utils.py:6076 |
| truncation / ordering | Yes | same round-robin merge (operate.py:5199-5246, per 05 §1) |
| query params/defaults | Yes | both from `to_query_params(False)` |

`RETRIEVAL_SEMANTICS_PARITY = YES`. A future integration would **not** silently change retrieval
semantics; the sole behavioral delta is the removed final-answer LLM. (Naive path is symmetric:
both call `naive_query`.)

---

## 5. Structured provenance (task §9) — STRONG(chunk) / PARTIAL(entity·relation)

Unchanged from GraphRAG-05 §4 and re-verified against the pinned `convert_to_user_format`
(utils.py:6076-6197):

```
chunk    → chunk.file_path            → canonical source_id      [STRONG, direct, lossless]
entity   → entity.file_path/source_id → chunk(s) → file_path(s)  → source_id  [PARTIAL, many-to-many]
relation → relation.file_path         → chunk(s) → file_path(s)  → source_id  [PARTIAL, many-to-many]
reference→ reference.file_path                                    → source_id  [STRONG]
```

- **Chunk / reference provenance is STRONG:** `file_path` carries the `source_id` ON passed as
  `file_source`; GraphRAG-04 measured 265 live references **100% valid** (0 malformed/foreign/
  duplicate). `file_path → source_id` uses the same structural RecordID helpers as the outbound
  boundary (`is_valid_record_id`, client.py:89-112) — lossless.
- **Entity/relation provenance is PARTIAL:** derived from one-or-more chunks → can map to
  **multiple** sources or default to `"unknown_source"` (utils.py:6105/6117/6144). An entity/
  relation does not cleanly own one Source.
- **GraphRAG-03D STRONG-ownership rule remains authoritative.** FOREIGN/MALFORMED provenance is
  never citable; never `entity name → guess source`. Do **not** weaken canonical ownership
  validation.

`QUERY_DATA_PROVENANCE_QUALITY = STRONG (chunk·reference) / PARTIAL (entity·relation)` — at least as
good as, and strictly richer than, the current references-only surface.

---

## 6. Failure-mode forensic (task §13) — the answer IS the boundary

| Scenario | Current `/query` | `/query/data` |
|---|---|---|
| retrieval OK, generation OK | 200 + answer + references | 200 + structured data |
| retrieval OK, **final-answer LLM fails** | **HTTP 500 — all evidence lost** (query_routes.py:580-582; `data` computed at :536 is discarded) | **n/a** — no final-answer LLM; retrieval result already returned |
| retrieval OK, **keyword LLM fails** | 500 | 500 (shared failure point) |
| empty results | 200 + "No relevant context found" + `references:[]` | **200 + `status:"failure"`, `data:{}`** (lightrag.py:3874-3887) — not an HTTP error |
| sidecar unreachable / timeout | client `GraphRAGUnavailableError` (client.py:156-164) | same client mapping |
| malformed provenance | dropped by client `_looks_like_record_id` (client.py:89-112) | same ON-side validation applies |

**`/query/data` creates a cleaner fail-open/fail-closed boundary:** it removes the single largest
*additional* failure surface (final-answer generation over the whole context) and never conflates a
generation failure with an evidence-retrieval failure. Residual shared failures (keyword LLM,
embedding, storage, sidecar) affect both paths equally. The forced answer in `/query` is therefore a
genuine failure boundary, not a cosmetic one (Review D holds).

---

## 7. Data minimization (task §11) — field classification

`IDENTIFIER_ONLY` · `STRUCTURAL_METADATA` · `DERIVED_TEXT` (LLM-generated) · `RAW_SOURCE_TEXT`.

| Field | Source | Class |
|---|---|---|
| `entities[].entity_name` / `entity_type` | extraction | DERIVED_TEXT / STRUCTURAL |
| `entities[].description` | LLM (extraction-time) | **DERIVED_TEXT** |
| `entities[].source_id` (chunk ids) / `file_path` / `created_at` | KG metadata | IDENTIFIER / IDENTIFIER / STRUCTURAL |
| `relationships[].src_id`/`tgt_id` | entity names | DERIVED_TEXT |
| `relationships[].description` / `keywords` | LLM | **DERIVED_TEXT** |
| `relationships[].weight` | extraction-time edge strength (default 1.0) | STRUCTURAL |
| `relationships[].source_id`/`file_path`/`created_at` | KG metadata | IDENTIFIER / STRUCTURAL |
| `chunks[].content` | document text | **RAW_SOURCE_TEXT** |
| `chunks[].file_path` / `chunk_id` / `reference_id` | metadata | IDENTIFIER / STRUCTURAL / STRUCTURAL |
| `references[].reference_id` / `file_path` | frequency index / source id | STRUCTURAL / **IDENTIFIER_ONLY** |
| `metadata.query_mode` / `processing_info.*` | mode / counts | STRUCTURAL |
| `metadata.keywords.{high_level,low_level}` | LLM keyword extraction (echoes query) | DERIVED_TEXT (query-derived, **not** source text) |

**Comparison.** ON's current `/query` call sets `include_references=True` but **not**
`include_chunk_content`, so `content` is not populated (query_routes.py:545) — the current
sidecar→ON payload is **IDENTIFIER_ONLY** (`reference_id` + `file_path`). `/query/data` returns
**more** text (`chunks[].content` = raw source, plus entity/relation descriptions). This is ON's
**own** source content returning from ON's **own** sidecar (trust Boundary A, §8) — not new external
egress — but it must be minimized ON-side: a future adapter should project to the minimum necessary
(§9) and **discard** `content`/descriptions before any persistence or logging.

`QUERY_DATA_DATA_MINIMIZATION_BETTER = PARTIAL`: **better** on provider egress (the assembled
context is never shipped to a generation LLM), **richer** on the sidecar→ON response (requires an
ON-side minimizing projection). No source content is copied into this report.

---

## 8. Trust boundaries (task §12) & egress matrix (task §32)

- **Boundary A:** Open Notebook ↔ LightRAG sidecar (same internal trust domain; `/query` and
  `/query/data` are both A-internal HTTP).
- **Boundary B:** LightRAG ↔ external LLM/embedding/rerank providers (the real egress boundary).

Removing final-answer generation changes **Boundary B**: it removes the LLM **generation** call
(the assembled retrieval context is no longer shipped to a generation model), but leaves the
retrieval-side keyword-LLM and embedding calls unchanged. It does **not** achieve "no egress."

**Egress / call matrix (per single hybrid query, ON's deployed sidecar config: LLM+embedding, no
rerank):**

| Operation | Current `/query` | `/query/data` |
|---|---|---|
| Embedding provider (query embedding) | Yes | Yes |
| LLM keyword extraction | Yes (0/1; cache-dependent) | Yes (0/1; cache-dependent) |
| **Final-answer LLM** | **Yes** | **No** |
| Reranker | No (not configured) | No (not configured) |
| Raw source text → external provider | Yes (context → generation LLM) | **No** |
| Derived text → external provider | Yes (context → generation LLM) | **No** |
| Sidecar→ON raw/derived text in response | reference ids only (content off) | chunks content + descriptions (minimize ON-side) |
| Cache write (keywords) | config-dependent | config-dependent |
| Cache write (answer/query) | config-dependent | **No** (no answer produced) |
| Corpus mutation | No | No |
| Failure impact | generation failure → 500, evidence lost | retrieval result preserved |

---

## 9. Side effects (task §15/§16): CORPUS = **NO**, CACHE = **CONFIG_DEPENDENT**

- `kg_query` / `_build_query_context` are **read-only retrieval** (VDB `.query`, graph reads, chunk
  reads); no graph/vector/document-status/pipeline/workspace writes.
- `_query_done` (lightrag.py:4081-4082) flushes **only** `llm_response_cache.index_done_callback()`
  — a cache flush, **not** corpus.
- Cache **writes**: keyword cache iff `enable_llm_cache` (operate.py:4632-4655). `/query`
  additionally writes the answer/query cache; `/query/data` does not (no answer).

`QUERY_DATA_CORPUS_MUTATION = NO`. `QUERY_DATA_CACHE_MUTATION = CONFIG_DEPENDENT` (keyword cache +
cache flush, gated on `enable_llm_cache`; never a semantic-corpus write). `QUERY_DATA_SIDE_EFFECT_
PROFILE = READ_MOSTLY` (read-only corpus; optional cache write/flush) — a cache write is **not**
"fully read-only."

---

## 10. Response schema (task §33) — `/query/data`

Fields per `convert_to_user_format` (utils.py:6076-6197) + `aquery_data` metadata
(lightrag.py:3764-3777). None is query-relevance-scored (GraphRAG-05); `weight` is structural.

| Field | Type | Meaning | Text? | Query-dep? | Provenance-bearing? | Canonical Source map | Stable? | Keep in contract? | Risk |
|---|---|---|---|---|---|---|---|---|---|
| `status` | str | success/failure | no | — | no | — | yes | diagnostic only | — |
| `message` | str | human status | no | — | no | — | yes | no | — |
| `data.entities[].entity_name` | str | entity id | derived | yes | weak | via chunks (PARTIAL) | med | no | many-to-many |
| `data.entities[].entity_type` | str | category | struct | no | no | — | med | optional | — |
| `data.entities[].description` | str | LLM description | **derived** | no | no | — | low | **no** (minimize) | raw-ish text |
| `data.entities[].source_id` | str | chunk ids | id | yes | yes | indirect | med | no | LightRAG-internal ids |
| `data.entities[].file_path` | str | ON source id | id | yes | **yes** | PARTIAL | high | maybe | `unknown_source` default |
| `data.entities[].created_at` | str | timestamp | struct | no | no | — | high | no | — |
| `data.relationships[].src_id/tgt_id` | str | entity names | derived | yes | weak | via chunks | med | no | — |
| `data.relationships[].description/keywords` | str | LLM text | **derived** | no | no | — | low | **no** (minimize) | raw-ish text |
| `data.relationships[].weight` | float | edge strength (1.0) | struct | no | no | — | med | no | **looks like a score; is not** |
| `data.relationships[].source_id/file_path/created_at` | str | metadata | id/struct | yes | PARTIAL | PARTIAL | high | maybe | many-to-many |
| `data.chunks[].content` | str | **document chunk text** | **RAW_SOURCE** | yes | yes | via file_path | high | **no** (minimize) | biggest exposure |
| `data.chunks[].file_path` | str | ON source id | id | yes | **yes** | **STRONG** | high | **yes** | — |
| `data.chunks[].chunk_id` | str | chunk id | struct | yes | yes | indirect | high | optional | LightRAG-internal |
| `data.chunks[].reference_id` | str | freq index | struct | yes | yes | via reference | med | optional | frequency, not rank |
| `data.references[].reference_id` | str | citation index | struct | yes | yes | via file_path | med | optional | frequency (05 §2 #7) |
| `data.references[].file_path` | str | ON source id | id | yes | **yes** | **STRONG** | high | **yes** | — |
| `metadata.query_mode` | str | mode | struct | — | no | — | high | diagnostic | — |
| `metadata.keywords.{hl,ll}` | list[str] | extracted keywords | derived (query) | yes | no | — | med | diagnostic only | echoes query text |
| `metadata.processing_info.*` | int | counts | struct | — | no | — | med | diagnostic | not per-candidate |

**No `score`/`rank` field on any entity/chunk/reference.** `QUERY_DATA_EXPOSES_VALID_RANK = NO`,
`QUERY_DATA_EXPOSES_VALID_SCORE = NO` (frozen from GraphRAG-05; re-verified).

---

## 11. Auth / API contract (task §14)

- **Route:** `POST /query/data` (query_routes.py:1013), `response_model=QueryDataResponse`.
- **Auth:** `dependencies=[Depends(combined_auth)]` (query_routes.py:1015), the **same**
  `get_combined_auth_dependency(api_key)` as `/query` (query_routes.py:316). Same **X-API-Key**
  semantics ON's client already sends (client.py:132-136, `utils_api.py` API key). **No new
  credential concept** is required.
- **Request model:** shared `QueryRequest` (`query` min_length=3; `mode`; `top_k`; …). The existing
  client body maps cleanly; `include_references` is ignored (`/query/data` always includes them).
- **Response model:** `QueryDataResponse{status, message, data, metadata}` — a formal Pydantic model
  (query_routes.py:261-269), not a leaked dict at the API layer (though `data`/`metadata` are typed
  as `Dict`, so field-level typing is loose — see §14 coupling).

The existing GraphRAG client infrastructure could theoretically call `/query/data` with **no**
credential/env change (design observation only; not implemented).

---

## 12. Cancellation / timeout / async (task §15)

`/query/data` is an ordinary async request/response handler; it launches **no** long-lived
background work (contrast document ingestion/deletion which run in the background). It awaits the
keyword-LLM and embedding provider calls; timeout/cancellation behavior is the same request-scoped
model as `/query`. ON's client already maps `httpx.TimeoutException`/`TransportError` →
`GraphRAGUnavailableError` (client.py:156-164). It leaves no pipeline state and mutates no corpus
storage (§9). `QUERY_DATA_SIDE_EFFECT_PROFILE = READ_MOSTLY`.

---

## 13. Structured-evidence contract (task §17–§19) — DESIGN ONLY, not implemented

Consistent with GraphRAG-05 §8 (`GraphSourceCandidate`, unranked, no score). Two conceptual layers:

**(a) Transient runtime projection** (what the adapter reads from `/query/data`, then discards):

```text
GraphEvidenceResult                # design sketch; NOT code; runtime-only, not persisted
  source_candidates: list[GraphSourceEvidence]
  diagnostics: EvidenceDiagnostics # counts only (see §15)
  # NO answer, NO score, NO rank. chunk content / descriptions are read then DROPPED.
```

**(b) Minimal Source-level evidence** (the only shape that may cross into ON persistence/citation):

```text
GraphSourceEvidence                # design sketch; NOT code
  source_id: str                   # REQUIRED. canonical ON id from chunk/reference.file_path
                                   #   (lossless via record_id_for); invariant is_valid_record_id.
  evidence_types: frozenset        # REQUIRED. subset {CHUNK, ENTITY, RELATION} that surfaced it.
  supporting_chunk_count: int      # OPTIONAL. # distinct retrieved chunks for this source.
                                   #   FREQUENCY signal (D), explicitly NOT relevance. >= 0.
  provenance_quality: enum         # REQUIRED. VALID | FOREIGN | MALFORMED (GraphRAG-03D).
  # NO score / NO rank field. Absent by design (no honest relevance signal exists; 05 §9).
```

Option evaluation (task §10, evidence-consumption shape):

| Option | Provenance | Graph value | Payload | Coupling | Minimization | Verdict |
|---|---|---|---|---|---|---|
| 1 references-only | STRONG | reference set | tiny | low | best | = today's `/query` evidence; no graph richness |
| 2 chunks + references | STRONG | + chunk grounding | med | low | good (drop content) | solid citation substrate |
| 3 entities + relations + chunks + references | STRONG/PARTIAL | multi-hop context | large | med | needs projection | richest; most to minimize |
| **4 normalized `GraphSourceEvidence`** | STRONG | set-membership + counts | **small** | **low (ON-owned)** | **best** | **preferred** — minimum necessary |

**Ownership (task §19):** the **GraphRAG client layer** owns LightRAG HTTP-schema knowledge and raw
payload disposal; the **GraphRAG integration/service layer** owns canonical Source-ID normalization,
foreign/malformed rejection, dedup, and diagnostics. **Ask/Chat/frontend must never parse
vendor-specific LightRAG responses.** Prefer **B: normalize behind an ON-owned adapter** over
exposing the raw LightRAG schema upstream — it shields ON from LightRAG upgrades, enforces canonical
ownership, and preserves provider independence.

---

## 14. Vendor coupling / upgrade analysis (task §28)

- `/query/data` is a **public, documented** route with a formal `QueryDataResponse` **envelope**
  (status/message/data/metadata), but `data`/`metadata` are typed as `Dict[str, Any]` at the API
  boundary — **field-level shape is convention, not a versioned schema**. Internal `hl_keywords`/
  `weight`/`reference_id` semantics could shift across LightRAG versions.
- The endpoint is newer than the `/query` ON already depends on, so it is **not** more stable than
  the current dependency, but not less either — both are gated by `VERIFIED_LIGHTRAG_VERSION`
  (config.py) and the client's 404/405 → version-mismatch mapping (client.py:180-185).
- **Anti-corruption layer** (conceptual): a thin `/query/data` adapter in the client layer that (1)
  validates the envelope, (2) extracts only `data.chunks[].file_path` / `data.references[].file_path`
  / evidence-type membership, (3) drops all text, (4) emits `GraphSourceEvidence`. LightRAG field
  churn is then absorbed in one file. **Not written here.**

---

## 15. Observability / logging (task §20) & citations (task §21) — design only

**Future logging contract (allowed, content-free):** query mode; candidate/source counts; entity/
relation/chunk counts; valid vs malformed vs foreign provenance counts; latency; provider-call
count if available. **Never log:** query text, chunk content, entity/relationship descriptions,
extracted keywords, raw provider payloads, credentials. (Matches ON's existing content-free logging,
client.py:605-607.)

**Citations:** `references[]` already suffices for canonical Source citation; `chunks[]` would give
**better source grounding** (chunk-level, STRONG provenance) and could later support "why this
Source" explanations without exposing LightRAG internals (normalize first). Entity/relation evidence
is PARTIAL and **not** citation-grade on its own. **No citation code is written; Ask is not
modified.**

---

## 16. Graph value without rank (task §22)

GraphRAG-05 concluded there is **no defensible ranked source surface**. The remaining useful role:

- **UNRANKED_EVIDENCE_ENGINE** (primary): returns a provenance-strong candidate **set**
  (set-membership, not a score) — multi-hop recall the vector path misses (GraphRAG-04 oracle-union).
- **PROVENANCE_ENRICHER**: chunk/reference `file_path` → canonical Source, lossless (STRONG).
- **CONTEXT_EXPANDER**: entities/relationships give supporting context for a future answer step
  (PARTIAL provenance; context only, not citation).

**Not** a `RANKED_RETRIEVER` (frozen; unchanged). `GRAPH_RAG_ROLE = UNRANKED_EVIDENCE_ENGINE +
PROVENANCE_ENRICHER + CONTEXT_EXPANDER`.

---

## 17. Architecture options (task §23) & preferred (task §24)

| Option | Retrieval fidelity | Provider cost | Egress | Failure isolation | Provenance | Payload | Coupling | Preferred? |
|---|---|---|---|---|---|---|---|---|
| **A** keep `client.query()` (`/query`) | full | **+final-answer LLM (wasted)** | context→gen LLM | poor (gen failure loses evidence) | references only | small | low | no |
| **B** `/query/data` structured evidence seam | **identical** (§4) | **no final-answer LLM** | **no gen egress** | **clean** | STRONG + richer | med (minimize) | med | **YES (design target)** |
| **C** dual: `/query/data` for evidence, `/query` only where a generated answer is *intentionally* wanted | identical | as B + optional | as B | clean | as B | as B | med | only if a generated-answer consumer appears (none today) |
| **D** no change until a future LightRAG version | full | +wasted LLM | context→gen LLM | poor | references only | small | low | no |

**Preferred: OPTION B** as the future direction. It meets every task §24 gate: same/understood
retrieval semantics (§4 YES); **no fake rank/score** (unranked contract, §13); provenance **≥**
current (strictly richer, §5); no unacceptable privacy expansion (same trust Boundary A + ON-side
minimization, §7); **cleaner failure boundary** (§6); manageable coupling (ACL adapter, §14).
Decisive rationale: ON **already discards** the generated answer (GraphRAG-04), so `/query`'s
generation is pure cost + failure surface for **zero** consumed value, and the evidence ON uses is a
strict subset of `/query/data`'s output. Option C is the fallback **iff** a genuine consumer of a
LightRAG-generated answer ever appears (none in VISION/Ask today). `client.query()` may be retained
as a pure diagnostic, but should not be the evidence path.

This is a **design-direction** decision, **not** an authorization to implement:
`STRUCTURED_EVIDENCE_IMPLEMENTATION_READY = NO`.

---

## 18. Future implementation scope (task §25) — design only, NOT now

If B (or C) is pursued in a later approved phase, isolate to GraphRAG integration code:

| Hypothetical change | Responsibility | Contract | Migration? | Test seam | Rollback |
|---|---|---|---|---|---|
| `client.query_data()` | client.py | `POST /query/data` → validate envelope → typed evidence | none | injected `httpx` transport (existing pattern) | additive method; unused until wired |
| `service` evidence method | service.py | normalize → `GraphSourceEvidence`, reject foreign/malformed | none | fake client | fail-open to None (existing `query()` pattern) |
| adapter/ACL | client/integration | drop text, map file_path→source_id | none | unit | delete file |

No new DB table, no API route, no worker job, no Ask/Chat/frontend change, behind
`OPEN_NOTEBOOK_GRAPHRAG_ENABLED`. **Vendor-specific LightRAG parsing must not leak into Ask/
frontend.**

---

## 19. Future test plan (task §26) & evaluation (task §27) — DESIGN ONLY, do not write/run

**Tests to add later:** request schema; X-API-Key auth; timeout; sidecar unavailable; empty result
(200 + `status:"failure"`); malformed provenance; foreign provenance; duplicate provenance; chunk→
Source mapping; KG many-to-many provenance; **no fabricated rank/score**; no generated-answer
dependency; no content-bearing logs; provider-call reduction (final-answer avoided); retrieval
semantic parity; cancellation; **no corpus mutation** (assert cache-only side effects).

**Evaluation to run later** (reuse GraphRAG-04 harness discipline; the larger corpus of GraphRAG-05
§12, **≥60–100** sources): compare `CURRENT_QUERY_PATH` vs `STRUCTURED_QUERY_DATA_PATH` on the same
frozen synthetic set — canonical provenance recall; candidate-set size; malformed/foreign counts;
query success rate; latency; **final-answer provider calls avoided**; token/provider-call reduction;
negative-query evidence breadth; graph evidence richness; retrieval parity. **No rank/nDCG/MRR** (no
legitimate rank). Do **not** rerun GraphRAG-04 now.

---

## 20. Adversarial design review (task §36)

- **A — semantic parity.** Attempt: `/query/data` ≠ `/query` retrieval. **Disproved:** both call the
  identical `kg_query` with identical args except `only_need_context`/`system_prompt`/`progress`
  (lightrag.py:3837 vs 3936). Same keyword extraction, context build, merge, `convert_to_user_
  format`. **Held: PARITY = YES.**
- **B — security/privacy.** Attempt: structured output increases raw-content exposure / provider
  egress. **Partially true and bounded:** the sidecar→ON **response** carries more text
  (`chunks[].content`, descriptions), but external **provider egress drops** (no context→generation
  LLM). Mitigation: ON-side minimizing projection + content-free logging (§7, §15). **Held:
  MINIMIZATION_BETTER = PARTIAL, egress net-lower; requires ON-side projection.**
- **C — provenance.** Attempt: ambiguous/foreign/many-to-many mappings make the contract unsafe.
  **Bounded:** chunk/reference provenance is STRONG (lossless, 100% live-valid); entity/relation is
  PARTIAL and is **excluded** from citation (context-only) with FOREIGN/MALFORMED rejection
  (GraphRAG-03D). **Held: contract restricted to STRONG provenance for ownership/citation.**
- **D — failure isolation.** Attempt: answer generation is not really the failure boundary.
  **Disproved:** `aquery_llm` maps any generation exception to HTTP 500 (query_routes.py:580-582),
  discarding evidence already computed at :536; `/query/data` early-returns before generation
  (operate.py:4275). **Held: generation IS an additional, evidence-losing failure surface.**
- **E — vendor coupling.** Attempt: `/query/data` is too private/unstable to depend on.
  **Bounded:** public documented route + Pydantic envelope, but `data`/`metadata` are loosely typed
  `Dict` → field churn risk. Mitigation: ON-owned ACL adapter (§14). **Held: depend only behind an
  adapter; not implemented.**
- **F — phase boundaries.** No production/test/retrieval/RRF/fusion/reranker/Ask/Chat/frontend/
  migration/API code written. Documentation + planning only. **Held.**

---

## 21. STOP-condition check (task §35)

None of the STOP conditions fired: retrieval semantics are **unchanged** (not undocumented);
provenance is **not weaker** than current (strictly richer); the schema envelope is public/Pydantic
(coupling manageable via ACL); the route does **not** depend on final-answer generation (it removes
it); extra raw text is bounded to the internal Boundary-A response and is minimizable ON-side;
future integration is additive and isolated; canonical Source ownership is enforceable (STRONG chunk
provenance + GraphRAG-03D); the benefit is **not** cosmetic (real cost/failure/provenance gains).
Therefore the conclusion is an affirmative **design preference (Option B)**, gated by a future
implementation phase and the GraphRAG-05 §12 evaluation — **not** "NO CHANGE YET," and **not** an
implementation.

---

## 21a. Adversarial review gate (Session 2 — re-verification against pinned executable code)

Every decisive claim re-read from the retained pinned clone (HEAD re-verified `b33c6b0812…`,
`__version__ 1.5.6`, api `0328`), preferring executable behavior over docstrings/comments.

- **Parity, hardest attack — `aquery_data` reconstructs `QueryParam`.** `QueryParam` has 16 fields
  (base.py:90-164). The `aquery_data` copy (lightrag.py:3815-3831) reproduces every
  **retrieval-affecting** field (mode, top_k, chunk_top_k, max_entity/relation/total_tokens,
  hl/ll_keywords, enable_rerank) and the LLM-only fields (response_type, user_prompt,
  conversation_history — the last two explicitly "not used for retrieval," base.py:143). The **only**
  field not copied is `include_references` (base.py:160) — an **output/serialization** flag, and
  `/query/data` builds references unconditionally regardless. **No retrieval-affecting field is
  dropped → `RETRIEVAL_SEMANTICS_PARITY = YES` holds.**
- **`only_need_context` never gates retrieval.** It appears at exactly three sites in operate.py:
  a docstring (4216), the kg_query gate (4275), and the naive_query gate (6310) — **all after**
  `_build_query_context`/vector retrieval. No retrieval branch is conditioned on it. Confirmed
  executable, not comment-based.
- **Final-answer call count.** kg_query reaches exactly **one** `use_model_func(...)` generation
  call (operate.py:4349) on the uncached path, then one answer-cache write (`cache_type="query"`,
  ~4379). `/query/data` early-returns at 4275 and reaches neither.
  `FINAL_ANSWER_CALL_COUNT_REDUCTION = 1 per successful uncached query`; latency/cost benefit is
  **`EXPECTED_BUT_UNMEASURED`** (no live measurement this phase — no numeric % claims made).
- **Failure isolation is one stage, not "no provider failure."** `/query/data` removes the
  **final-answer** failure stage (query_routes.py:580-582 maps generation exceptions to HTTP 500,
  discarding evidence computed at :536). It does **not** remove keyword-LLM or embedding failures —
  those remain shared with `/query`. Wording kept as "removes the final-answer failure stage."
- **Egress: embeddings ARE provider calls.** The matrix (§8) separates LLM / EMBEDDING / RERANK.
  Both paths issue query-embedding calls (Boundary B); "only the final-answer LLM is avoided" — not
  "only one LLM call remains" as if embeddings stayed inside Boundary A.
- **Cache mutation re-attack.** Writes reachable from `/query/data`: keyword cache
  (operate.py:4632-4655, iff `enable_llm_cache`), `_query_done` llm-cache flush
  (lightrag.py:4081-4082). The **answer/query** cache write is `/query`-only (unreachable via the
  4275 early return). `embedding_cache_config` defaults `enabled: False` (lightrag.py:659-665) — a
  config-dependent semantic-cache, off by default. VDB `.query` methods are read-only similarity
  search (no upsert on the query path). **`CORPUS_MUTATION = NO`; `CACHE_MUTATION =
  CONFIG_DEPENDENT`** both survive.
- **Option B vs C (no speculation).** The generated answer has **no functional ON consumer**: it is
  read only at `api/routers/graphrag.py:266` (diagnostic echo to an operator), and the eval harness
  consumes **only** `result.references` (`eval/runner.py:252`), never `.answer`. No current consumer
  → **B remains preferred; C is a fallback only if such a consumer is ever introduced.**
- **Provenance / minimization / rank / role — unchanged.** STRONG(chunk·reference)/PARTIAL(entity·
  relation); `chunks[].content = RAW_SOURCE_TEXT` (minimize ON-side, `MINIMIZATION_BETTER =
  PARTIAL`); no valid rank/score; role stays UNRANKED_EVIDENCE_ENGINE + PROVENANCE_ENRICHER +
  CONTEXT_EXPANDER (**not** RANKED_RETRIEVER). Implementation stays **NOT READY** (§18 preconditions
  unmet; design preference ≠ readiness).

No conclusion was strengthened to favor Option B; the one documentation change from this gate is the
**§0a historical precision correction**.

---

## 22. Required decisions (task §34)

```
QUERY_DATA_AVAILABLE                     = YES   (POST /query/data, query_routes.py:1013/1309)
QUERY_DATA_AVOIDS_FINAL_ANSWER_GENERATION= YES   (only_need_context=True lightrag.py:3817;
                                                  early return operate.py:4275)
QUERY_DATA_OTHER_LLM_CALLS_REMAIN        = YES   (keyword-extraction LLM operate.py:4234→4618
                                                  + query embeddings; classification B)
RETRIEVAL_SEMANTICS_PARITY               = YES   (identical kg_query; lightrag.py:3837 vs 3936)
QUERY_DATA_PROVENANCE_QUALITY            = STRONG (chunk·reference) / PARTIAL (entity·relation)
QUERY_DATA_EXPOSES_VALID_RANK            = NO    (frozen; no score/rank field; round-robin order)
QUERY_DATA_EXPOSES_VALID_SCORE           = NO    (frozen; only structural weight)
QUERY_DATA_CORPUS_MUTATION               = NO    (read-only retrieval; _query_done flushes cache)
QUERY_DATA_CACHE_MUTATION                = CONFIG_DEPENDENT (keyword cache iff enable_llm_cache;
                                                  operate.py:4632-4655; no answer cache write)
QUERY_DATA_DATA_MINIMIZATION_BETTER      = PARTIAL (lower provider egress; richer sidecar→ON
                                                  payload; requires ON-side projection)
STRUCTURED_EVIDENCE_CONTRACT_DESIGNABLE  = YES   (unranked GraphSourceEvidence, §13)
STRUCTURED_EVIDENCE_IMPLEMENTATION_READY = NO
PREFERRED_ARCHITECTURE                   = B     (design target; C is fallback if a generated-answer
                                                  consumer ever appears)
GRAPH_RAG_ROLE                           = UNRANKED_EVIDENCE_ENGINE + PROVENANCE_ENRICHER
                                                  + CONTEXT_EXPANDER  (NOT RANKED_RETRIEVER)
RRF_CANDIDATE_INTERFACE_READY            = NO    (mandated; unchanged)
GRAPH_CANDIDATE_IMPLEMENTATION_READY     = NO    (mandated; unchanged)
```

---

## 23. Final report (task §38)

```
GRAPH_RAG_06_FORENSIC       = COMPLETE
PINNED_SOURCE_VERIFICATION  = PASS   (clone HEAD == b33c6b0812…, __version__ 1.5.6, api 0328)
CURRENT_PATH_FORENSIC       = PASS
QUERY_DATA_FORENSIC         = PASS
SEMANTIC_PARITY_REVIEW      = PASS
PROVENANCE_REVIEW           = PASS
SECURITY_PRIVACY_REVIEW     = PASS   (egress net-lower; minimization PARTIAL, projection required)
FAILURE_ISOLATION_REVIEW    = PASS
VENDOR_COUPLING_REVIEW      = PASS   (depend only behind ON-owned ACL)
PHASE_BOUNDARY_REVIEW       = PASS

FILES_CHANGED =
  docs/agribank/development/GRAPHRAG_06_STRUCTURED_EVIDENCE_FORENSIC.md            (new)
  docs/agribank/development/CURRENT_PHASE.md                                       (GraphRAG-06 row)
  .planning/2026-08-30-graphrag-06-structured-evidence-forensic/{task_plan,findings,progress}.md
  .planning/.active_plan                                                          (slug updated)

PRODUCTION_CODE_CHANGED   = NO
TEST_CODE_CHANGED         = NO
MIGRATION_CHANGED         = NO
PROVIDER_TRAFFIC          = NO
DATABASE_MUTATION         = NO
SOURCE_MUTATION           = NO
LIGHTRAG_STORAGE_MUTATION = NO
SIDECAR_STARTED           = NO
```

**Summary.** (1) current `client.query()` → `/query` forces a discarded final-answer LLM; (2)
`/query/data` runs the identical retrieval and early-returns before generation; (3) LLM/provider
avoided = the **final-answer generation** call; retained = keyword-extraction LLM + query
embeddings; (4) retrieval-semantics parity = YES; (5) response schema = `{status,message,data{
entities,relationships,chunks,references},metadata}`, no score/rank; (6) provenance STRONG(chunk·
reference)/PARTIAL(entity·relation); (7) raw/derived text exposure = `chunks[].content` +
descriptions in the sidecar→ON response (minimize ON-side); (8) corpus mutation NO, cache mutation
config-dependent; (9) failure isolation cleaner (generation no longer loses evidence); (10) auth =
same X-API-Key, no new credential; (11) contract = unranked `GraphSourceEvidence`; (12) options
A/B/C/D as §17; (13) preferred = **B** (design target, impl NOT ready); (14) future scope isolated
to GraphRAG integration; (15) future test plan §19; (16) future eval on ≥60–100-source corpus §19;
(17) unresolved risks: loose `Dict` typing of `data`/`metadata` (coupling → ACL); keyword-LLM
remains a shared failure/egress point; hybrid value still `INCONCLUSIVE` pending the larger-corpus
evaluation (GraphRAG-04/05).

**GRAPH_RAG_06_FORENSIC_DESIGN_GATE_COMPLETE** — no commit, no push, no tag; no GraphRAG-06
implementation.
```
