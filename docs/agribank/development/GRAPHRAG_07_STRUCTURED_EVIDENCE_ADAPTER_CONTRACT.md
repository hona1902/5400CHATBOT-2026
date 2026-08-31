# GraphRAG-07 — Structured Evidence Adapter Contract & Data-Minimization Design Gate

**Status: FORENSIC / ARCHITECTURE / CONTRACT DESIGN ONLY — no implementation.** No production
code, no tests, no `client.query_data()`, no `/query/data` wiring, no GraphRAG-client change,
no retrieval-semantics change, no RRF/fusion/reranker/aggregation, no migration, no API/
frontend, no provider traffic, no DB or LightRAG-storage mutation. `OPEN_NOTEBOOK_GRAPHRAG_
ENABLED` remained **false**; the sidecar was **not** started (every contract question below
was answerable statically from frozen GraphRAG-05/06 findings against pinned source). This
gate designs the boundary; it does **not** build it, and it does **not** open GraphRAG-08.

> **⚠ SUBORDINATE / HISTORICAL DESIGN PASS — the authoritative GraphRAG-07 contract is
> [`GRAPHRAG_07_STRUCTURED_EVIDENCE_CONTRACT.md`](GRAPHRAG_07_STRUCTURED_EVIDENCE_CONTRACT.md).**
> This document is the earlier design pass; it is retained for forensic history. Where the two
> disagree, the authoritative document **wins**. The independent review (2026-08-30) reconciled the
> following decisions frozen differently here — a future maintainer must follow the authoritative
> doc's values: diagnostics type = `GraphEvidenceDiagnostics` (not `EvidenceDiagnostics`); diagnostic
> fields = `canonical_source_count` / `unknown_source_count` / `duplicate_reference_count` (not
> `valid_source_count` / `unknown_provenance_count` / `deduplicated_count`); `final_answer_generation
> = False` (not `final_answer_call_skipped = True`); `GraphEvidenceResult` also carries
> `status: EvidenceStatus` (SUCCESS | EMPTY | DEGRADED; FAILURE = exception); `ProvenanceQuality` =
> 5 states `{STRONG, PARTIAL, INVALID, FOREIGN, UNKNOWN}` (not 3); `supporting_chunk_count` is
> REQUIRED-and-≥1 when present (not optional-per-instance; its *inclusion* is the open minimality
> question); and a caller **CANCELLED** propagates `asyncio.CancelledError` unwrapped (it is **not**
> mapped to `GraphRAGUnavailableError` — a *timeout* is). See the authoritative doc §2a.

**Frozen inputs (not reopened or weakened — task §2):**
- GraphRAG-04 (`cb86a06`, tag `graphrag-04-approved`): `RRF_CANDIDATE_INTERFACE_READY = NO`;
  `HYBRID_VALUE_EVIDENCED = INCONCLUSIVE`.
- GraphRAG-05 (`833ec59`, tag `graphrag-05-forensic-approved`):
  `LIGHTRAG_DIRECT_SCORED_SOURCE_SURFACE = NO`; `LOWER_LEVEL_QUERY_SCORE_AVAILABLE = PARTIAL`
  (internal-only, unexposed); `SOURCE_PROVENANCE = STRONG(chunk)/PARTIAL(KG)`;
  `SOURCE_LEVEL_AGGREGATION_DEFENSIBLE = REQUIRES_EXPERIMENT`; `GRAPH_NATIVE_RANKING_SIGNAL =
  NO`; `ABSTENTION_SIGNAL_AVAILABLE = UNCLEAR`; `GRAPH_CANDIDATE_CONTRACT_DESIGNABLE =
  YES(unranked)/NO(ranked)`; `GRAPH_CANDIDATE_IMPLEMENTATION_READY = NO`.
- GraphRAG-06 (`d7e6a5b`, tag `graphrag-06-forensic-approved`): `QUERY_DATA_AVAILABLE = YES`;
  `QUERY_DATA_AVOIDS_FINAL_ANSWER_GENERATION = YES`; `QUERY_DATA_OTHER_LLM_CALLS_REMAIN = YES`
  (classification **B**); `RETRIEVAL_SEMANTICS_PARITY = YES`; `QUERY_DATA_PROVENANCE_QUALITY =
  STRONG(chunk/reference)/PARTIAL(entity/relation)`; `QUERY_DATA_EXPOSES_VALID_RANK = NO`;
  `QUERY_DATA_EXPOSES_VALID_SCORE = NO`; `QUERY_DATA_CORPUS_MUTATION = NO`;
  `QUERY_DATA_CACHE_MUTATION = CONFIG_DEPENDENT`; `QUERY_DATA_DATA_MINIMIZATION_BETTER =
  PARTIAL` (`chunks[].content = RAW_SOURCE_TEXT`); `PREFERRED_ARCHITECTURE = B` (the
  `/query/data` seam over the answer-generating `/query`); `GRAPH_RAG_ROLE =
  UNRANKED_EVIDENCE_ENGINE + PROVENANCE_ENRICHER + CONTEXT_EXPANDER`;
  `STRUCTURED_EVIDENCE_IMPLEMENTATION_READY = NO`.

**Pinned source of truth.** `HKUDS/LightRAG tag v1.5.6 commit b33c6b0812cddf39206e48a9810112
e51f025274` (`__version__ = "1.5.6"`, `__api_version__ = "0328"`). GraphRAG-07 designs a
contract on top of facts GraphRAG-05/06 already established with `file:line` citations into
that pinned tree; per task §2 those facts are frozen and are **not** re-derived. No new clone
and no sidecar were needed — where this document restates a pinned-source fact, it cites the
approved forensic (e.g. *06 §10*), not a fresh read. Open Notebook citations use
`open_notebook/integrations/graphrag/` at HEAD `d7e6a5b`.

> **Boundary B (sidecar → external LLM/embedding/rerank providers) remains NOT APPROVED for
> real internal data.** Every design here is synthetic/public-only until a separate egress
> decision exists. This gate does not authorize any egress.

---

## 1. Documentation reconciliation (task §1)

Bootstrap inspection found one historical documentation drift: the CURRENT_PHASE.md GraphRAG-05
row read *"FORENSIC/DESIGN-ONLY COMPLETE — awaiting review."* Historical reality is that
GraphRAG-05 was **forensic-approved** and checkpointed at commit `833ec593f2cda5e46f39be78c8
fbfa56f7ebc816`, tag `graphrag-05-forensic-approved` — a fact independently corroborated by
the GraphRAG-06 approved document, which lists GraphRAG-05 among its *frozen approved inputs*
and builds on it. As part of GraphRAG-07's documentation work the 05 row is corrected to
**FORENSIC/DESIGN COMPLETE / APPROVED**. This is documentation reconciliation only: the
GraphRAG-05 commit is **not** amended or rewritten, and its findings are **not** changed.

---

## 2. Core question (task §3) and headline

> What EXACT Open Notebook-owned normalized contract should sit between LightRAG v1.5.6
> `/query/data` and future GraphRAG consumers, such that raw LightRAG schema never leaks
> upstream, canonical Source ownership stays authoritative, raw content is minimized,
> malformed/foreign provenance fails safe, no fake score/rank is introduced, vendor coupling
> is isolated, failure behavior is explicit, observability is content-free, and a future
> implementation is testable without changing retrieval semantics?

**Headline.** The contract is **designable and small**. Open Notebook must own an explicit
**anti-corruption layer (ACL)**: raw LightRAG JSON is validated, minimized, and provenance-
normalized inside `open_notebook/integrations/graphrag/`, and only an ON-owned
`GraphSourceEvidence` set (canonical `source_id`, evidence-type membership, a frequency
count, provenance quality — **no text, no score, no rank**) may cross that boundary. The
preferred shape is **Option C — Source-only evidence projection**, realized through the
strict ON-owned normalizing adapter discipline (Option B mechanism), because the only
GraphRAG value *demonstrated* to date is STRONG chunk/reference → canonical Source provenance;
graph-detail payload (entity/relation descriptions, endpoints, weights) has **no demonstrated
consumer** (`HYBRID_VALUE_EVIDENCED = INCONCLUSIVE`, 04) and is deferred. Evidence is
**TRANSIENT_ONLY**. `STRUCTURED_EVIDENCE_CONTRACT_DESIGNABLE = YES`;
`STRUCTURED_EVIDENCE_IMPLEMENTATION_READY = NO` — this gate designs, it does not build.

Target boundary (conceptual — designed, not implemented):

```
LightRAG /query/data
   → RAW LightRAG response (transient, Boundary-A internal)
   → GraphRAG anti-corruption / evidence adapter (ON-owned)
        · schema/version guard   · data minimization (text dropped)
        · provenance validation  · canonical Source normalization
        · deduplication          · content-free diagnostics
   → Open Notebook GraphEvidenceResult (source evidence + diagnostics; no text/score/rank)
   → future GraphRAG consumer (Ask/citation) — never parses LightRAG schema
```

---

## 3. Anti-corruption layer decision (task §6)

Verified rather than assumed, against current source and frozen findings:

| Driver | Evidence | Implication |
|---|---|---|
| Schema stability | `QueryDataResponse.data`/`metadata` typed `Dict[str,Any]` at the LightRAG API boundary (*06 §11/§14*) — field shape is convention, not a versioned schema | raw schema is unstable → must not be a dependency of ON consumers |
| Untyped Dict fields | same; `weight`/`reference_id`/`hl_keywords` semantics can shift across versions | consumers must not read vendor dicts |
| Vendor naming | `file_path` actually carries a `source_id`; `reference_id` is a frequency index, not a rank (*05 §2, 06 §10*) | vendor names are misleading; must be renamed at the boundary |
| Version drift | pinned `v1.5.6`/api `0328`; `/query/data` is newer than the `/query` ON already depends on | one ON-owned file must absorb churn |
| Citation ownership | canonical Source ids come only from `chunk/reference.file_path` via ON's own record-id validators (`is_valid_record_id`, `models.py:202`) | ON must own the mapping, not LightRAG |
| Canonical Source ownership | GraphRAG-03D STRONG-ownership rule is authoritative; FOREIGN/MALFORMED is never citable | normalization must reject, not pass through |
| Security / minimization | `chunks[].content = RAW_SOURCE_TEXT`, entity/relation `description = DERIVED_TEXT` (*06 §7*) | text must be dropped ON-side before persistence/logging |
| Test isolation | client already injects an `httpx` transport (`client.py:127`) so failure modes are testable without a sidecar | an ACL keeps that seam |
| Provider independence | ON already forbids Ask/frontend parsing vendor responses (`models.py` docstring: *"Everything LightRAG-shaped stops here"*) | extend the same rule to structured evidence |

**Decision:** `RAW_LIGHTRAG_SCHEMA_EXPOSED_UPSTREAM = NO`;
`NORMALIZED_EVIDENCE_ADAPTER_REQUIRED = YES`. No future Ask/citation/frontend code parses
LightRAG-specific dictionaries. This merely extends the existing boundary invariant (already
enforced for `/query` via `GraphReference`/`GraphQueryResult`) to the richer `/query/data`
surface.

---

## 4. Raw input contract (task §7) — what the future adapter would receive

Exact `/query/data` shape, **frozen from 06 §10 / §7** (pinned `convert_to_user_format`
`utils.py:6076-6197` + `aquery_data` metadata `lightrag.py:3764-3777`). Envelope:
`QueryDataResponse{status, message, data{entities[], relationships[], chunks[], references[]},
metadata{query_mode, keywords{high_level, low_level}, processing_info{...}}}`. No example raw
source text is reproduced here.

| Raw field (nested path) | Type | Req/Opt | Nullable | Cardinality | Text? | Provenance? | Structural? | Query-dep? | Vendor-specific? | Malformed possibilities |
|---|---|---|---|---|---|---|---|---|---|---|
| `status` | str | required | no | 1 | no | no | yes | no | envelope | value ∉ {success,failure}; missing |
| `message` | str | optional | yes | 1 | no | no | yes | no | envelope | absent |
| `data.entities[].entity_name` | str | opt | yes | 0..N | derived | weak | no | yes | yes | empty; duplicate |
| `data.entities[].entity_type` | str | opt | yes | 0..N | struct | no | yes | no | yes | absent |
| `data.entities[].description` | str | opt | yes | 0..N | **derived (LLM)** | no | no | no | yes | long text; injection |
| `data.entities[].source_id` | str | opt | yes | 0..N | id | yes (indirect) | no | yes | **yes (LightRAG chunk ids)** | multi-id string |
| `data.entities[].file_path` | str | opt | yes | 0..N | id | **yes** (PARTIAL) | no | yes | yes (means source_id) | `unknown_source`; multi; absent |
| `data.entities[].created_at` | str | opt | yes | 0..N | struct | no | yes | no | yes | absent |
| `data.relationships[].src_id`/`tgt_id` | str | opt | yes | 0..N | derived | weak | no | yes | yes | empty |
| `data.relationships[].description`/`keywords` | str | opt | yes | 0..N | **derived (LLM)** | no | no | no | yes | long text |
| `data.relationships[].weight` | float | opt | yes | 0..N | struct | no | **yes** | no | yes | looks-like-score (default 1.0) |
| `data.relationships[].source_id`/`file_path`/`created_at` | str | opt | yes | 0..N | id/struct | PARTIAL | mixed | yes | yes | many-to-many; `unknown_source` |
| `data.chunks[].content` | str | opt | yes | 0..N | **RAW_SOURCE_TEXT** | via file_path | no | yes | yes | huge; injection |
| `data.chunks[].file_path` | str | opt | yes | 0..N | id | **STRONG** | no | yes | yes (means source_id) | absent; foreign; malformed id |
| `data.chunks[].chunk_id` | str | opt | yes | 0..N | struct | indirect | yes | yes | **yes (LightRAG-internal)** | absent |
| `data.chunks[].reference_id` | str | opt | yes | 0..N | struct (freq) | via reference | yes | yes | **yes (frequency index)** | absent |
| `data.references[].reference_id` | str | opt | yes | 0..N | struct (freq) | via file_path | yes | yes | **yes (freq, not rank)** | absent |
| `data.references[].file_path` | str | opt | yes | 0..N | id | **STRONG** | no | yes | yes (means source_id) | absent; foreign; malformed |
| `metadata.query_mode` | str | opt | yes | 1 | no | no | yes | no | yes | absent |
| `metadata.keywords.{high,low}_level` | list[str] | opt | yes | 1 | **derived (echoes query)** | no | no | yes | yes | absent |
| `metadata.processing_info.*` | int | opt | yes | 1 | no | no | yes | no | yes | absent |

Two malformed-envelope realities the adapter must tolerate (frozen): an **empty result**
returns HTTP **200 with `status:"failure"`, `data:{}`** (`lightrag.py:3874-3887`, *06 §6*) —
not an HTTP error; and `data`/`metadata` sub-shapes are **loosely typed** upstream, so any
field may be absent, null, wrong-typed, or a list where a scalar was expected.

---

## 5. Data classification (task §8)

Every relevant raw field placed in exactly one class (`IDENTIFIER_ONLY`, `STRUCTURAL_METADATA`,
`FREQUENCY_SIGNAL`, `DERIVED_TEXT`, `RAW_SOURCE_TEXT`, `VENDOR_DIAGNOSTIC`, `UNKNOWN`),
re-verified against 06 §7:

| Raw field | Classification | Re-verify note |
|---|---|---|
| `chunks[].content` | **RAW_SOURCE_TEXT** | ON's own document text (*06 §7/§10*) — biggest exposure |
| `chunks[].file_path` / `references[].file_path` | **IDENTIFIER_ONLY** | canonical `source_id`; STRONG ownership |
| `entities[].file_path` / `relationships[].file_path` | IDENTIFIER_ONLY (PARTIAL binding) | may be `unknown_source` / many-to-many |
| `entities[].description` / `relationships[].description` / `keywords` | **DERIVED_TEXT** | LLM-generated at extraction time — treat as content |
| `entities[].entity_name` / `relationships[].src_id`/`tgt_id` | DERIVED_TEXT | entity label text |
| `entities[].entity_type` | STRUCTURAL_METADATA | category |
| `entities[].source_id` / `chunks[].chunk_id` | VENDOR_DIAGNOSTIC | LightRAG-internal ids; not ON ids |
| `relationships[].weight` | STRUCTURAL_METADATA | edge strength (default 1.0) — **not a score** |
| `chunks[].reference_id` / `references[].reference_id` | **FREQUENCY_SIGNAL** | citation-occurrence index — **not a rank** (*05 §2 #7*) |
| `created_at` | STRUCTURAL_METADATA | timestamp |
| `metadata.keywords.{hl,ll}` | DERIVED_TEXT (query-derived) | echoes query text |
| `metadata.query_mode` / `processing_info.*` | VENDOR_DIAGNOSTIC | mode + counts |
| `status` / `message` | VENDOR_DIAGNOSTIC | envelope |

Re-verified content-bearing fields (task §8 explicit): `chunks[].content` = **RAW_SOURCE_TEXT**;
entity descriptions / relationship descriptions = **DERIVED_TEXT**; `references[].*` fields =
`reference_id` FREQUENCY_SIGNAL + `file_path` IDENTIFIER_ONLY; `file_path` = IDENTIFIER_ONLY
(the sole canonical-ownership carrier). No content-bearing example is copied into this doc.

---

## 6. Raw-content retention (task §9) & persistence (task §10)

The adapter may **consume** the raw response transiently from the local sidecar (Boundary A),
but must **project** a smaller ON-owned structure and discard the raw payload. These three are
distinct security decisions and are decided separately:

| Content | Received transiently (Boundary A)? | Retained in normalized contract? | Persisted? | Decision |
|---|---|---|---|---|
| `chunks[].content` (RAW_SOURCE_TEXT) | yes (unavoidable on the wire) | **NO** | **NO** | `RAW_CHUNK_CONTENT_RETAINED = NO` |
| `entities[].description` (DERIVED_TEXT) | yes | **NO** | NO | `ENTITY_DESCRIPTION_RETAINED = NO` |
| `relationships[].description` (DERIVED_TEXT) | yes | **NO** | NO | `RELATION_DESCRIPTION_RETAINED = NO` |
| raw LightRAG `references[]` objects | yes | **NO** (only `file_path`→`source_id` survives) | NO | `RAW_LIGHTRAG_REFERENCES_RETAINED = NO` |

Rationale (task §9): default to the minimum necessary; do **not** keep raw content merely
because `/query/data` returns it. The demonstrated GraphRAG value is *which canonical Sources*
the graph surfaced (STRONG provenance) — not the chunk prose, which Ask already retrieves from
the canonical Source itself. Retaining chunk/description text would duplicate ON's own content
into a second store with staleness and exposure risk for zero demonstrated benefit.

**Persistence (task §10):** `EVIDENCE_PERSISTENCE_POLICY = TRANSIENT_ONLY`. Analysis: raw-text
exposure (avoided by dropping text), derived-text exposure (avoided), **stale evidence** (the
graph lags canonical Source edits/deletes — a persisted evidence row could cite a Source the
03B/03C lifecycle already tombstoned), deletion-lifecycle coupling (persistence would create a
new object the 03D reconcile/03B delete lifecycle must also sweep), data-retention/audit
(counts suffice; content must not persist), and DB-migration burden (persistence ⇒ a new table
⇒ migration 26 ⇒ out of scope, 24/25 frozen). No demonstrated use case requires persistence;
evidence is computed per query, consumed by the caller in-process, and dropped. A future phase
that discovers a real caching need must justify it and design its own migration under separate
approval. **No migration is designed here** (count stays 50).

---

## 7. Canonical Source ownership & normalization (task §11)

GraphRAG-03D ownership rules remain authoritative. Canonical ownership is established **only**
from a `file_path` that ON can losslessly validate as one of its own record ids.

**Owner:** `CANONICAL_SOURCE_NORMALIZATION_OWNER = GraphRAG integration/adapter layer`
(`open_notebook/integrations/graphrag/`). Split of responsibility (matches 06 §13 and the
existing `models.py` boundary invariant):
- **GraphRAG client layer** (`client.py`): owns LightRAG HTTP-schema knowledge, envelope
  parsing, and raw-payload disposal (drops all text on the way out).
- **GraphRAG normalization/adapter** (integration layer): owns canonical Source-ID validation
  (reusing `is_valid_record_id` / `record_id_for`, `models.py:202/251`, over
  `_PROVENANCE_TABLES = {source, note, source_insight}`, `client.py:86`), foreign/malformed
  rejection, dedup, and diagnostics.
- **Ask / citation / frontend:** never see LightRAG fields; consume only `GraphSourceEvidence`.

**Acceptable provenance inputs** (ownership-establishing): `chunks[].file_path`,
`references[].file_path`. Both are STRONG (lossless `source_id`, *06 §5*). Entity/relation
`file_path` is PARTIAL (many-to-many / `unknown_source`) and is **not** ownership-establishing.

**Rejection behavior** (never guess a Source):

| Input condition | Behavior |
|---|---|
| malformed source identifier (fails `is_valid_record_id`) | drop record, `malformed_provenance_count += 1` |
| invalid RecordID encoding (nested/unbalanced escape) | drop, count malformed (validators already reject, `models.py:190/197`) |
| foreign source (valid id, table not in `_PROVENANCE_TABLES`, or not an ON-owned record) | drop, `foreign_provenance_count += 1`; never cited |
| `unknown_source` literal / absent `file_path` | drop, `unknown_provenance_count += 1` |
| ambiguous / multi-source entity or relationship evidence | not ownership-establishing → PARTIAL; excluded from canonical Source creation (see §8) |
| multi-source chunk (should not occur: chunk→one file_path) | if >1 valid id, treat as malformed for that chunk, drop + count |

Live existence (does the Source still exist in SurrealDB?) is **structural-only** here, exactly
as `_looks_like_record_id` documents (`client.py:93`): structural validity is *shape*, not
authorization or proof of existence. A future implementation phase may add a live SurrealDB
existence check; this design does not require it to remain safe, because a structurally valid
but deleted id yields at worst a citation to a Source ON itself owns and can resolve.

---

## 8. Foreign / malformed / unknown / ambiguous policy (task §12) & quality model (task §13)

**Provenance-quality vocabulary (task §13)** — a small classification applied to each raw
evidence record during normalization:

- **STRONG** — lossless canonical Source ownership established (`chunk`/`reference` `file_path`
  is a structurally valid ON record id). The *only* class that may create a canonical Source.
- **PARTIAL** — graph evidence (entity/relationship) supports one or more Sources but ownership
  is many-to-many or indirect. May **corroborate** a Source that STRONG evidence already
  established (adds an evidence-type marker), but never creates one on its own.
- **UNKNOWN** — canonical ownership cannot be safely established (`unknown_source`, absent, or
  structurally invalid `file_path`). Never canonical; dropped and counted.

**Emission invariant (the safety core):** a `GraphSourceEvidence` is emitted **only** when at
least one STRONG anchor established its `source_id`. PARTIAL evidence alone never produces a
Source; UNKNOWN/FOREIGN/MALFORMED never produce a Source. Therefore an emitted Source's
`provenance_quality` is **STRONG by construction** — PARTIAL/UNKNOWN cannot masquerade as
canonical Source evidence (task §13 requirement). PARTIAL and the reject classes survive only
as content-free diagnostic **counts**.

**Frozen policies (task §12):**

| Policy | Decision | Rationale |
|---|---|---|
| `FOREIGN_PROVENANCE_POLICY` | **DROP_AND_COUNT** | preserve good evidence; never promote a foreign id to a canonical ON Source |
| `MALFORMED_PROVENANCE_POLICY` | **DROP_AND_COUNT** | a malformed id is not proof of ownership; validators already reject it |
| `UNKNOWN_PROVENANCE_POLICY` | **DROP_AND_COUNT** | `unknown_source`/absent → not citable; count for observability |
| `AMBIGUOUS_PROVENANCE_POLICY` | **DROP_FROM_CANONICAL_AND_COUNT** (PARTIAL corroboration only) | many-to-many KG evidence never *creates* a Source; may add an evidence-type marker to an already-STRONG source |

The policy preserves usable good evidence where safe (a valid chunk alongside a malformed one
still yields its Source) but **never** promotes unverified provenance into a canonical Source.
Worked micro-cases (no content shown):

| Scenario | Outcome |
|---|---|
| good chunk + malformed chunk | Source from the good chunk; `malformed_provenance_count=1` |
| good chunk + foreign chunk | Source from the good chunk; `foreign_provenance_count=1` |
| all malformed | `sources=[]`; `malformed_provenance_count=N`; `raw_evidence_present=true` |
| all foreign | `sources=[]`; `foreign_provenance_count=N`; `raw_evidence_present=true` |
| `unknown_source` only | `sources=[]`; `unknown_provenance_count=N` |
| mixed STRONG chunk + PARTIAL entity for same source | one Source, `evidence_types={DIRECT_CHUNK, GRAPH_ENTITY}`, quality STRONG |
| PARTIAL entity only (no STRONG anchor) | no Source; counted as PARTIAL/graph-only; context-only |

---

## 9. Normalized Source-level contract (task §14) — the smallest defensible shape

```text
GraphSourceEvidence                 # DESIGN SKETCH — NOT code
  source_id: str                    # REQUIRED. canonical ON record id, losslessly built via
                                    #   record_id_for over _PROVENANCE_TABLES. Invariant:
                                    #   is_valid_record_id(source_id) is True.
  evidence_types: frozenset[EvidenceType]  # REQUIRED, non-empty. which channels surfaced it.
  supporting_chunk_count: int       # REQUIRED-and->=1 when present (authoritative doc §2a);
                                    #   # distinct STRONG chunks/references for this source.
                                    #   FREQUENCY / EVIDENCE COUNT, NOT relevance. (Field *inclusion*
                                    #   is the open minimality question — top removal candidate.)
  provenance_quality: ProvenanceQuality  # REQUIRED. STRONG by construction (see §8 invariant).
  # NO score / NO rank / NO order-position field. Absent by design (§10).
```

Per-field justification:

| Field | Why it exists | Type | Semantics | Source of truth | Invariant | Content? | Persist? | Comparable across queries? | Relevance signal? |
|---|---|---|---|---|---|---|---|---|---|
| `source_id` | canonical citation target | `str` | ON record id | `chunk/reference.file_path` | `is_valid_record_id` true | no | no (transient) | yes (stable id) | **no** |
| `evidence_types` | which graph channel surfaced it (future graph value hook) | `frozenset` | subset of channels | normalization | non-empty | no | no | partially | **no** |
| `supporting_chunk_count` | evidence breadth / debugging | `int ≥ 1` | # distinct STRONG chunks | dedup step | ≥ 1 | no | no | **no — query-specific & corpus-dependent; cross-query comparison invalid** | **no — frequency, not relevance** |
| `provenance_quality` | audit + safety proof | enum | STRONG only at emission | §8 invariant | == STRONG | no | no | yes | **no** |

`supporting_chunk_count` is a **FREQUENCY / EVIDENCE COUNT, explicitly NOT a relevance score**
(task §14). It counts distinct STRONG-owned chunks/references for the Source; it must never be
sorted-on to imply relevance, exposed as a score, or thresholded as confidence.

---

## 10. Score / rank prohibition (task §15) — impossible by construction

`RANK_FIELD_ALLOWED = NO`; `SCORE_FIELD_ALLOWED = NO`. The contract carries no `rank`, `score`,
`relevance_score`, `confidence_score`, or `priority` field, and none of `reference order`,
`round-robin order` (`_merge_all_chunks`, *05 §1*), `node degree`, `relationship weight`,
`reference_id`, or `supporting_chunk_count` may be re-exposed or sorted-on as a disguised rank.
Enforcement is by **construction, not convention** (task §22 preference A/B/C → **B: no
rank/order semantics in the type at all**): the emitted collection is an **unordered set** of
`GraphSourceEvidence`; there is no position field, no sort key, and the diagnostics carry only
counts. Any future ranked semantics require a separately-approved phase (GraphRAG-05/06 frozen:
no honest relevance signal exists on pinned v1.5.6).

---

## 11. Evidence types (task §16) & entity/relationship payload (task §17)

**Evidence-type vocabulary.** Use ON-stable names, not vendor names, and only what maps to a
real channel:

```
EvidenceType = { DIRECT_CHUNK, GRAPH_ENTITY, GRAPH_RELATIONSHIP }
```

- `DIRECT_CHUNK` — a STRONG chunk/reference `file_path` established this Source (the ownership
  anchor; the only type that can appear alone).
- `GRAPH_ENTITY` — an entity whose PARTIAL provenance corroborates an already-STRONG Source.
- `GRAPH_RELATIONSHIP` — likewise from a relationship.

`REFERENCE` is **not** a separate type: `references[]` and `chunks[]` both carry the same STRONG
`file_path` join key, so both collapse into `DIRECT_CHUNK` ownership (avoids a vendor-shaped
distinction with no consumer value). `evidence_types` is set-membership only — a content-free,
size-`O(1)` hook that lets a future phase ask "did the graph, not just the vector chunk, surface
this Source?" without carrying any entity/relation payload.

**Entity/relationship payload policy (task §17):** the normalized output retains **none** of
entity description, relationship endpoints, relationship description, relationship `weight`, or
per-entity supporting text. Evaluated and rejected: graph value is `INCONCLUSIVE` (04), the
content is sensitive DERIVED_TEXT, it duplicates data ON already holds, it couples ON to vendor
naming, it has no Ask/citation consumer today, and it inflates payload. The only entity/relation
signal that survives is **set-membership** in `evidence_types` (and, if a STRONG anchor exists,
a `+1` to `supporting_chunk_count` is *not* applied for KG-only evidence — KG never inflates the
chunk count). No `GRAPH_DETAIL` tier is defined (see §12).

---

## 12. Two-level contract question (task §18) — decision

| Option | Simplicity | Security | Ask usefulness | Provenance | Graph value | Vendor coupling | Evolution | Verdict |
|---|---|---|---|---|---|---|---|---|
| 1 — flat `GraphEvidenceResult` (sources + diagnostics) | high | high (no text) | sufficient (canonical Sources) | STRONG | set-membership | low | additive | **preferred** |
| 2 — two-level (source_evidence + minimized graph_details) | med | lower (graph text) | marginal today | STRONG/PARTIAL | more, undemonstrated | med | heavier | deferred |
| 3 — Source evidence only (no diagnostics) | highest | high | sufficient | STRONG | none | lowest | additive | too thin (loses safety observability) |

**Decision: Option 1** — a flat `GraphEvidenceResult` holding a set of `GraphSourceEvidence`
plus content-free `EvidenceDiagnostics`. No `graph_details` tier is defined now: it would carry
PARTIAL-provenance, undemonstrated-value, sensitive text and has no consumer. A future approved
phase that demonstrates graph value can add a `graph_details` tier **additively** without
changing the Source-evidence shape. Option 3 is rejected only because dropping diagnostics would
remove the malformed/foreign/unknown accounting that makes the boundary auditable.

---

## 13. Deduplication (task §19) & ordering (task §20)

**Deduplication.** The dedup key is the **canonical `source_id`**. A Source that appears across
multiple chunks, multiple entities, multiple relationships, and the reference list collapses to
**one** `GraphSourceEvidence`. Rules:
- Same `source_id` from N distinct STRONG chunks/references → one Source; `supporting_chunk_count
  = N` (distinct chunk ids; a repeated chunk id counts once).
- Same entity/relationship repeated → adds its `EvidenceType` to the set once (idempotent); never
  increments `supporting_chunk_count`.
- What is **counted**: distinct STRONG chunk/reference ownership occurrences (breadth).
- What must **not** inflate significance: repeated ids, KG multiplicity, reference frequency —
  none of these become a score. Dedup is deterministic (set semantics), so the same raw response
  always yields the same normalized result.

**Ordering.** `NORMALIZED_OUTPUT_ORDER_SEMANTICS = NON_RELEVANCE`. The contract is conceptually
an unordered set (§10). If a concrete future implementation needs a deterministic sequence for
reproducible tests/logs, it must sort by **canonical `source_id`** (lexicographic) and label it
explicitly `NON_RELEVANCE_ORDER`. No consumer may infer relevance from list position; the
preferred realization keeps the public type a set so position cannot be read at all.

---

## 14. Empty / negative result contract (task §21)

Three semantically distinct zero-Source states must be distinguishable via content-free
diagnostics — never by exposing raw text:

| State | Meaning | Diagnostics signature |
|---|---|---|
| genuinely empty | `/query/data` returned `status:"failure"`/`data:{}` (*06 §6*) | `raw_evidence_present=false`, all counts 0 |
| all evidence rejected | raw evidence existed but every record was malformed/foreign/unknown | `raw_evidence_present=true`, `valid_source_count=0`, reject counts > 0 |
| valid but distractor-only | Sources found (breadth), relevance unknown (unranked by design) | `valid_source_count>0` |

Diagnostic fields for these states: `raw_evidence_present` (bool), `valid_source_count`,
`malformed_provenance_count`, `foreign_provenance_count`, `unknown_provenance_count`. None
carries text. This lets a caller/operator tell "the graph knows nothing" from "the graph
returned only un-ownable evidence" — a security-relevant distinction (the second can indicate a
foreign-corpus or schema-drift condition).

---

## 15. Full normalized result & diagnostics (task §22 / §23)

```text
GraphEvidenceResult                 # DESIGN SKETCH — NOT code; TRANSIENT_ONLY
  sources: frozenset[GraphSourceEvidence]   # unordered; rank impossible by construction
  diagnostics: EvidenceDiagnostics
  # NO answer, NO score, NO rank, NO ordering field. No graph_details tier (§12).

EvidenceDiagnostics                 # content-free (task §23)
  query_mode: str                   # e.g. "hybrid" (VENDOR_DIAGNOSTIC, safe)
  valid_source_count: int
  raw_evidence_present: bool
  chunk_count: int                  # distinct STRONG chunks observed
  entity_count: int                 # count only (no names/descriptions)
  relationship_count: int           # count only
  reference_count: int
  malformed_provenance_count: int
  foreign_provenance_count: int
  unknown_provenance_count: int
  deduplicated_count: int           # records collapsed by source_id
  final_answer_call_skipped: bool = True   # always true on /query/data (design invariant)
  latency_ms: int | None            # optional; timing only
```

Not every possible diagnostic is included (task §23 caution): entity/relationship *descriptions*,
query text, extracted keywords, provider payloads, and credentials are **excluded by design**.
Only counts, mode, booleans, and timing appear. `final_answer_call_skipped` documents the 06
failure-isolation property (the final-answer LLM is not called on `/query/data`).

---

## 16. Error taxonomy (task §24) & failure isolation (task §25)

Reuse the **existing** GraphRAG error hierarchy (`models.py:57-123`) rather than inventing new
classes; map future adapter conditions onto it:

| Condition | Existing type | Retry? | Open/closed | Result to caller |
|---|---|---|---|---|
| `SIDECAR_UNAVAILABLE` (refused/DNS) | `GraphRAGUnavailableError` (`client.py:162`) | caller may retry | fail-closed | raise (or fail-open to empty at service, existing `query()` pattern) |
| `TIMEOUT` | `GraphRAGUnavailableError` (`client.py:156`) | caller may retry | fail-closed | raise / fail-open empty |
| `AUTH_FAILURE` (401/403) | `GraphRAGConfigurationError` (`client.py:175`) | no | fail-closed | raise (our misconfig) |
| `UNSUPPORTED_SCHEMA` / wrong route (404/405) | `GraphRAGConfigurationError` (`client.py:180`) | no | fail-closed | raise (version mismatch) |
| `SCHEMA_INVALID` (bad envelope / non-JSON / wrong types) | `GraphRAGProtocolError` (`client.py:207/347`) | no (until upstream fixed) | fail-closed | raise; never partial-trust |
| `UPSTREAM_QUERY_FAILURE` (HTTP 5xx) | `GraphRAGServerError` (`client.py:167`) | caller may retry | fail-closed | raise |
| `PROVENANCE_INVALID` (per-record) | **not an exception** | n/a | **best-valid** | drop + count (§8) |
| `NO_VALID_EVIDENCE` (0 Sources, raw present) | **not an exception** | n/a | fail-open | empty `sources`, diagnostics explain (§14) |
| `CANCELLED` (caller cancellation) | propagate `asyncio.CancelledError` **unwrapped** — NOT mapped to a `GraphRAGError` (a *timeout* is the event that maps to `GraphRAGUnavailableError`) | no | neither — not a failure | no raw payload; no persistence (§17). Corrected per authoritative doc §2a/§7.3 |

No new exception classes are required; the existing taxonomy already separates *our misconfig*
(`Configuration`) from *sidecar rejection* (`Request`), *unreachable* (`Unavailable`), *server*
(`Server`), and *unparseable* (`Protocol`). Design guidance only.

**Failure isolation (task §25):** `NORMALIZATION_MODE = BEST_VALID_EVIDENCE_WITH_DIAGNOSTICS`
for **per-record provenance** (a malformed record never fails the whole query — good evidence
survives, §8), but **ALL_OR_NOTHING** for **envelope/schema integrity** (`SCHEMA_INVALID` /
version mismatch → raise, never partial-trust a payload whose shape we cannot verify). The
safety rule dominates: *never return unsafe canonical ownership to salvage a response* — best-
valid applies only to dropping bad records, never to admitting one. GraphRAG-06 established that
`/query/data` already removes the final-answer failure stage; the ON boundary adds per-record
tolerance on top without weakening ownership.

---

## 17. Schema-version shield (task §26), raw disposal (task §27), cancellation/concurrency (task §30/§31)

**Version shield:** `LIGHTRAG_SCHEMA_VERSION_SHIELD = HYBRID`. Two independent gates, both
fail-closed:
1. **Version gate** — reuse `VERIFIED_LIGHTRAG_VERSION = "v1.5.6"` (`config.py:57`) plus the
   `GET /health` `core_version`/`api_version` (`0328`) already read by `health()`
   (`client.py:241`). A route/method absence maps to `GraphRAGConfigurationError` (404/405,
   `client.py:180`) — the existing version-mismatch signal.
2. **Structural envelope gate** — validate `{status, message, data{...}, metadata{...}}` shape
   and field types at parse time; any deviation → `GraphRAGProtocolError`. This catches a
   *silent* field-shape change within the same version string (the loose-`Dict` risk, *06 §14*).

`EXACT_VERSION_GATE` alone is too brittle for a same-version field drift; `STRUCTURAL_VALIDATION`
alone misses a version bump that keeps shape but changes semantics — hence HYBRID. No version
*negotiation* is implemented; the adapter fails predictably, it does not adapt.

**Raw-payload lifecycle (task §27):** `receive → validate → normalize → minimize → discard`.
Precise policy: the raw response may **not** be logged (`RAW_PAYLOAD_LOGGING_ALLOWED = NO`), may
**not** be persisted (`RAW_PAYLOAD_PERSISTENCE_ALLOWED = NO`), may **not** escape the integration
layer (only `GraphEvidenceResult` crosses the boundary), and may **not** be embedded in an
exception message or `__cause__` (the existing errors deliberately never echo response bodies,
e.g. `client.py:199/351`). The raw dict is a local variable in the client that is projected and
dropped; `GraphQueryResult.raw` (the diagnostic escape hatch on the *legacy* `/query` type,
`models.py:488`) is **not** replicated on the structured type.

**Cancellation / timeout (task §30):** `/query/data` is an ordinary request/response call with
no background continuation (*06 §12*); a cancelled or timed-out request maps to
`GraphRAGUnavailableError`, produces **no** evidence, and — because evidence is TRANSIENT_ONLY —
persists nothing. ON shutdown mid-request simply drops the in-flight local payload.

**Concurrency / reentrancy (task §31):** the adapter is **stateless / per-request**. It owns no
mutable cross-request state, no evidence cache, and no credential store (credentials stay in the
config passed to the client, `client.py:129`). Concurrent queries share nothing; the only cache
is LightRAG-internal (config-dependent, *06 §9*), outside the ON adapter. Version/health may be
read per request or cached read-only; either is safe because it is immutable within a deployment.

---

## 18. Logging/redaction (task §28) & privacy/egress (task §29)

**Logging contract.** Allowed: query mode, source/candidate counts, entity/relationship/chunk/
reference counts, valid/partial/malformed/foreign/unknown counts, dedup count, latency, error
category, version. Forbidden: query text, chunk content, entity/relationship descriptions,
extracted keywords, raw file contents, raw provider response, credentials, authorization
headers. This matches ON's existing content-free logging (`client.py:352/605`).

**Source-ID logging:** `SOURCE_ID_LOGGING = DEBUG_ONLY`. Justification: a canonical `source_id`
is an internal ON record id (not customer content and not a secret), and it is genuinely useful
for provenance debugging — but under the Agribank internal-fork posture (AGRIBANK.md §4/§6) an
id can still be a low-sensitivity identifier tying a query to a specific internal document, so it
is emitted only at DEBUG, never at INFO/WARN, and never hashed-away where an operator debugging a
real provenance issue would need it. Counts (not ids) are the default INFO signal.

**Privacy / egress (task §29).** Two boundaries, and this gate designs only the ON side:
- **Boundary A** — ON ↔ local LightRAG sidecar. The adapter minimization operates here: it
  reduces data **retained** by ON, data **propagated** to ON consumers, accidental **logs/
  persistence**, and **vendor-schema exposure**.
- **Boundary B** — LightRAG ↔ external model providers. The adapter does **not** and cannot
  retroactively reduce retrieval-side egress that LightRAG already performed (keyword-LLM +
  query embeddings, *06 §8*). No claim is made that projection prevents Boundary-B retrieval
  egress. (`/query/data` does remove the final-answer generation egress vs `/query`, but that is
  a GraphRAG-06 property of the endpoint, not of this adapter.)

---

## 19. Adapter ownership / module boundary (task §33) & citation ownership (task §34)

Conceptual placement inside the existing package (no files created):

```
open_notebook/integrations/graphrag/
  client.py       # + hypothetical query_data(): POST /query/data, envelope+version guard,
                  #   DROP all text, return a raw-but-typed transient projection. Owns HTTP schema.
  models.py       # + GraphSourceEvidence, GraphEvidenceResult, EvidenceDiagnostics, EvidenceType,
                  #   ProvenanceQuality. Owns the ON contract types. (No LightRAG names.)
  <normalizer>    # provenance validation + canonical Source-ID mapping + dedup + diagnostics.
                  #   Reuses is_valid_record_id / record_id_for. Owns canonical ownership.
  service.py      # + evidence method: orchestrates client → normalizer, fail-open to empty.
```

Separation of concerns (task §33): **transport** (httpx) and **raw-schema parsing/disposal** live
in `client.py`; **normalization + canonical provenance validation** in the normalizer; the
**evidence contract types** in `models.py`; **consumers** (Ask/citation) never import LightRAG
schema. This avoids one giant class while not over-engineering (three collaborators, mirroring the
existing client/models/service split).

**Citation ownership (task §34):** the GraphRAG adapter outputs enough for citation mapping
(canonical `source_id` + STRONG provenance) but does **not** own citation rendering. Division:
- GraphRAG integration → canonical provenance (which Sources, STRONG-owned).
- Ask / citation layer → citation formatting/rendering (from ON's own Source records).
Ask must never parse LightRAG raw schema; entity/relation PARTIAL evidence is context-only and
is **not** citation-grade on its own (*06 §15*). No citation code is designed or written.

---

## 20. Security threat model (task §35) & payload limits (task §36)

| # | Threat | Classification |
|---|---|---|
| 1 | malicious/malformed `file_path` | **reject** (validator) + **count** |
| 2 | crafted Source RecordID (traversal/URL/token-shaped) | **reject** — `is_valid_record_id` rejects `../`, `://`, tokens, newlines (`client.py:96`) |
| 3 | foreign Source reference | **drop** + **count**; never cited |
| 4 | raw chunk content in exception | **prevent** — errors never echo bodies; raw dropped before raise |
| 5 | raw content in logs | **prevent** — content-free logging contract (§18) |
| 6 | vendor schema adds unexpected text field | **prevent/reject** — allowlist projection (only `file_path` extracted); structural gate raises on shape change |
| 7 | huge candidate payload | **fail** (bound) — see payload limits below |
| 8 | duplicate amplification | **drop** — dedup by `source_id`; counts never become scores (§13) |
| 9 | ambiguous KG provenance | **drop from canonical** + **count** — PARTIAL never creates a Source (§8) |
| 10 | `unknown_source` promotion | **reject** — UNKNOWN never canonical (§8) |
| 11 | sidecar compromised / malformed response | **fail-closed** — `GraphRAGProtocolError`; ON-side validation is the trust gate, sidecar output is untrusted |

**Payload limits (task §36):** `ADAPTER_PAYLOAD_LIMITS_REQUIRED = REQUIRES_IMPLEMENTATION_
FORENSIC`. Defensive bounds on entity/relationship/chunk/reference counts and per-string length
are **warranted** (threat #7), and the request already bounds counts via `top_k`/`chunk_top_k`
request-side — but Open Notebook has **no existing convention** that supplies a defensible
numeric cap for a `/query/data` response, so concrete numbers are deferred to the implementation
forensic rather than invented here. The design records that a hard structural cap (reject, not
truncate) is the intended posture; a truncation that silently drops evidence is **not** chosen
because it could hide a foreign/malformed condition.

---

## 21. Architecture options (task §40) & preferred (task §41)

| Option | Security | Data minimization | Vendor coupling | Graph value | Citation | Ask | Complexity | Drift resilience | Testability | Maintainability |
|---|---|---|---|---|---|---|---|---|---|---|
| **A** thin pass-through `/query/data` wrapper | poor (raw text/dicts leak) | poor | **high** | raw | unsafe | leaks schema | low | poor | poor | poor |
| **B** strict ON-owned normalized adapter (general) | high | high | low | flexible | safe | clean | med | high | high | high |
| **C** Source-only evidence projection | high | **highest** | **lowest** | set-membership only | safe (STRONG) | clean | **low-med** | **high** | high | high |
| **D** Source + optional minimized graph-detail | high (if text dropped) | good | med | more (undemonstrated) | safe | clean | med-high | med | med | med |
| **E** no implementation yet | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a |

**Preferred: OPTION C — Source-only evidence projection**, realized through the strict ON-owned
adapter discipline of Option B (B is the *mechanism*; C is the *scope*). Rationale (task §41 —
prefer the **smallest** contract that preserves *demonstrated* graph value, not the richest):
- The only demonstrated GraphRAG value is STRONG chunk/reference → canonical Source provenance
  (04/05/06). Graph-detail payload (D) carries PARTIAL provenance and *undemonstrated* value
  (`HYBRID_VALUE_EVIDENCED = INCONCLUSIVE`) at real security/coupling cost → deferred.
- C minimizes raw text (none retained), vendor coupling (only `file_path` crosses), ambiguous
  provenance (STRONG-only emission), and future migration burden (TRANSIENT_ONLY, no table),
  while retaining canonical Source evidence, provenance quality, a graph-specific hook
  (`evidence_types` set-membership), and useful diagnostics.
- A is rejected (raw leak); E is rejected because the contract *is* designable (`DESIGNABLE =
  YES`) — but note the preferred **option** being C does not make implementation ready:
  `STRUCTURED_EVIDENCE_IMPLEMENTATION_READY = NO` (§22). D remains the additive upgrade path if a
  future phase demonstrates graph value.

---

## 22. Future implementation acceptance contract (task §37)

`STRUCTURED_EVIDENCE_IMPLEMENTATION_READY = YES` may be set **only** in a future, separately-
approved phase, and only after design approval of **all** of:
1. the normalized contract (`GraphEvidenceResult` / `GraphSourceEvidence` / `EvidenceDiagnostics`);
2. raw-content policy (text dropped);
3. provenance policy (STRONG-only emission);
4. foreign/malformed/unknown/ambiguous policy;
5. schema-version shield (HYBRID);
6. error taxonomy mapping;
7. logging/redaction contract;
8. no-rank / no-score invariants (by construction);
9. the future test plan (§23);
10. the future evaluation plan (§24);
11. the implementation file scope (client/models/normalizer/service; no migration, no API/
    frontend, no Ask/Chat change; behind `OPEN_NOTEBOOK_GRAPHRAG_ENABLED`);
12. `ADAPTER_PAYLOAD_LIMITS_REQUIRED` resolved with concrete bounds (its implementation forensic).

**GraphRAG-07 does not set implementation-ready merely because the contract is designable.** The
gate is a design artifact; readiness is a later, evidence-backed decision.

---

## 23. Future test matrix (task §38) — DESIGN ONLY, not written

Valid `/query/data` response · empty response (`status:"failure"`) · one canonical Source ·
duplicate Source evidence (dedup to one) · multi-source entity (PARTIAL, no canonical creation) ·
multi-source relationship (same) · `unknown_source` (dropped+counted) · foreign Source
(dropped+counted) · malformed RecordID (rejected+counted) · missing `file_path` (unknown) ·
duplicate references (counted once) · raw `chunks[].content` dropped (not in result/logs/errors) ·
entity-description drop · relationship-description drop · score/rank **not modeled** (type has no
such field) · deterministic non-relevance ordering (if a sequence is materialized) · schema
mismatch (`GraphRAGProtocolError`) · wrong API version (`GraphRAGConfigurationError`) · sidecar
auth failure · timeout · cancellation · partial malformed + valid (best-valid survives) · all
malformed (empty sources + counts) · logs contain no content · exceptions contain no raw payload ·
no raw-response persistence · workspace/credential forwarding stays in client. Tests use the
existing injected-`httpx`-transport seam (`client.py:127`) — no live sidecar. **No test is
written or run in this phase.**

---

## 24. Future evaluation contract (task §39) — DESIGN ONLY

A later evaluation (reusing GraphRAG-04 harness discipline on the larger ≥60–100-source corpus
from 05 §12) must prove, comparing `CURRENT_QUERY_PATH` (`/query`) vs `STRUCTURED_QUERY_DATA_PATH`
(`/query/data` + adapter) on the same frozen synthetic set: retrieval semantics unchanged (parity,
06 §4); canonical provenance recall unchanged or explainable; raw-content retention reduced
(assert no text in the normalized result); final-answer provider call absent; **no fake rank**
(type carries none); candidate breadth remains measurable; negative-query behavior remains visible
(the §14 empty-state diagnostics); malformed/foreign accounting correct; provider-side retrieval
calls remain as expected (embeddings/keyword-LLM unchanged). **No MRR/nDCG** — GraphRAG remains
unranked. Do not rerun GraphRAG-04 now.

---

## 25. Required design diagram (task §42)

```
                    ┌───────────────────────── VENDOR BOUNDARY ─────────────────────────┐
LightRAG /query/data│  (external schema; loosely-typed Dict; misleading vendor names)   │
        │           └───────────────────────────────────────────────────────────────────┘
        v
   RawResponse  ······································· transient, Boundary-A internal only
        │
        v
  ┌───────────────┐   status/message/data/metadata shape + type check; version+api gate
  │  Schema Guard │   ── fail → GraphRAGProtocolError / GraphRAGConfigurationError (fail-closed)
  └───────┬───────┘
          v
  ┌───────────────┐   *** RAW TEXT DISPOSAL POINT ***
  │ Data Minimizer│   drop chunks[].content, entity/relation descriptions, keywords, raw refs
  └───────┬───────┘   → only file_path (+ vendor ids for local join) proceed; text is gone
          v
  ┌────────────────────┐  *** CANONICAL OWNERSHIP VALIDATION POINT ***
  │ Provenance Normalizer│ is_valid_record_id / record_id_for over _PROVENANCE_TABLES
  └───────┬─────────────┘  STRONG → source_id ; PARTIAL → corroborate-only ; UNKNOWN/FOREIGN/
          │                MALFORMED → ─────────────► malformed/foreign/unknown DIAGNOSTIC COUNTS
          v
  ┌───────────────┐   collapse by canonical source_id; distinct-chunk breadth count;
  │  Deduplicator │   frequency never becomes relevance
  └───────┬───────┘
          v
  ┌──────────────────────────── ON-OWNED CONTRACT BOUNDARY ───────────────────────────┐
  │  GraphEvidenceResult                                                               │
  │    ├── sources: frozenset[GraphSourceEvidence]   (source_id, evidence_types,       │
  │    │        supporting_chunk_count, provenance_quality=STRONG)  — NO text/score/rank│
  │    ├── (graph_details: DEFERRED — not defined this phase)                          │
  │    └── diagnostics: EvidenceDiagnostics          (counts / mode / timing only)     │
  └───────┬────────────────────────────────────────────────────────────────────────────┘
          v
  future GraphRAG consumer (Ask / citation) — never parses LightRAG schema
```

---

## 26. Contract table (task §43)

| Raw field | Classification | Needed? | Normalized field | Content? | Canonical prov.? | Retained? | Reason | Risk |
|---|---|---|---|---|---|---|---|---|
| `chunks[].file_path` | IDENTIFIER_ONLY | **yes** | `source_id` | no | **STRONG** | yes | canonical ownership anchor | foreign/malformed → reject |
| `references[].file_path` | IDENTIFIER_ONLY | yes | `source_id` | no | **STRONG** | yes | second ownership anchor | same |
| `chunks[].chunk_id` | VENDOR_DIAGNOSTIC | yes (local) | (dedup key only) | no | indirect | no | distinct-chunk count | vendor id — not emitted |
| `chunks[].content` | RAW_SOURCE_TEXT | no | — | **yes** | via file_path | **no** | Ask has the Source already | biggest exposure |
| `entities[].file_path` | IDENTIFIER_ONLY | partial | (corroborate) | no | PARTIAL | no | many-to-many | `unknown_source` |
| `entities[].description` | DERIVED_TEXT | no | — | **yes** | no | **no** | undemonstrated value | sensitive text |
| `entities[].entity_name`/`type` | DERIVED_TEXT/STRUCT | no | — | text | no | no | not needed | — |
| `entities[]` presence | — | yes | `evidence_types += GRAPH_ENTITY` | no | corroborate | yes (marker) | graph hook | none (set) |
| `relationships[].description`/`keywords` | DERIVED_TEXT | no | — | **yes** | no | **no** | undemonstrated | sensitive text |
| `relationships[].weight` | STRUCTURAL_METADATA | no | — | no | no | **no** | **looks like a score** | misuse as rank |
| `relationships[].file_path` | IDENTIFIER_ONLY | partial | (corroborate) | no | PARTIAL | no | many-to-many | — |
| `relationships[]` presence | — | yes | `evidence_types += GRAPH_RELATIONSHIP` | no | corroborate | yes (marker) | graph hook | none |
| `references[].reference_id` / `chunks[].reference_id` | FREQUENCY_SIGNAL | no | — | no | no | **no** | frequency ≠ rank | disguised-rank misuse |
| `entities[].source_id` | VENDOR_DIAGNOSTIC | no | — | no | no | no | LightRAG-internal | vendor coupling |
| `metadata.query_mode` | VENDOR_DIAGNOSTIC | yes | `diagnostics.query_mode` | no | no | yes (diag) | observability | — |
| `metadata.keywords.*` | DERIVED_TEXT | no | — | **yes (echoes query)** | no | **no** | query text | leakage |
| `metadata.processing_info.*` | VENDOR_DIAGNOSTIC | partial | `diagnostics.*_count` | no | no | yes (counts) | observability | — |
| (derived) | — | yes | `supporting_chunk_count` | no | no | yes | breadth (freq) | must not be a score |
| (derived) | — | yes | `provenance_quality=STRONG` | no | yes | yes | audit/safety | — |
| `status`/`data:{}` | VENDOR_DIAGNOSTIC | yes | `diagnostics.raw_evidence_present` | no | no | yes | empty-state (§14) | — |

---

## 27. Policy table (task §44)

| Policy | Decision | Reason | Failure behavior |
|---|---|---|---|
| raw chunk content | **drop** (`RETAINED=NO`) | RAW_SOURCE_TEXT; Ask already has the Source | never in result/log/exception |
| entity description | **drop** | DERIVED_TEXT, undemonstrated value | never surfaced |
| relationship description | **drop** | DERIVED_TEXT, undemonstrated value | never surfaced |
| foreign provenance | **DROP_AND_COUNT** | never promote foreign id to canonical Source | `foreign_provenance_count++` |
| malformed provenance | **DROP_AND_COUNT** | not proof of ownership | `malformed_provenance_count++` |
| unknown provenance | **DROP_AND_COUNT** | `unknown_source`/absent not citable | `unknown_provenance_count++` |
| ambiguous KG provenance | **drop from canonical; PARTIAL corroborate only** | many-to-many never creates a Source | counted; no Source created |
| raw payload logging | **NO** | content-free logging | not logged |
| raw payload persistence | **NO** | TRANSIENT_ONLY | not persisted; no migration |
| Source ID logging | **DEBUG_ONLY** | internal id, low sensitivity, debug value | INFO shows counts only |
| score | **not modeled** | no honest signal (05/06) | type has no field |
| rank | **not modeled** | round-robin/degree/weight are not relevance | type is an unordered set |
| ordering | **NON_RELEVANCE** | position must not imply relevance | set by default; sort by source_id if materialized |
| deduplication | **by canonical source_id** | one Source per record cluster | deterministic; freq ≠ relevance |
| schema mismatch | **fail-closed** | untrusted vendor shape | `GraphRAGProtocolError` / `ConfigurationError` |

---

## 28. Adversarial design review (task §46)

- **A — data minimization.** Attempt: prove unnecessary raw/derived content survives. The
  minimizer allowlists only `file_path` (+ vendor ids used solely as a local dedup key that is
  never emitted); `chunks[].content`, entity/relation descriptions, and `keywords` are dropped at
  the disposal point *before* normalization; the result and diagnostics carry no text field; raw
  is never logged/persisted/raised. **No content survives. PASS.**
- **B — provenance safety.** Attempt: make malformed/foreign/ambiguous provenance become
  canonical. Emission requires a STRONG anchor validated by `is_valid_record_id` (which rejects
  traversal/URL/token/newline shapes, `client.py:96`); PARTIAL corroborates only; UNKNOWN/FOREIGN/
  MALFORMED are dropped+counted; `unknown_source` is explicitly UNKNOWN. **No unsafe promotion
  path exists. PASS.**
- **C — misuse resistance.** Attempt: infer rank/relevance from ordering, count, degree, weight,
  `reference_id`. `weight`/`reference_id`/degree are dropped; `supporting_chunk_count` is a
  documented frequency (not sorted-on, not a score); the collection is an unordered set with no
  position field; if a sequence is ever materialized it is `NON_RELEVANCE_ORDER` by `source_id`.
  **Rank is impossible by construction. PASS.**
- **D — vendor coupling.** Attempt: prove raw LightRAG schema leaks beyond the adapter. Only ON
  types (`GraphSourceEvidence`/`GraphEvidenceResult`/`EvidenceDiagnostics`) cross the boundary;
  vendor names (`file_path`, `reference_id`, `weight`) never appear in the contract; Ask/citation/
  frontend import nothing LightRAG-shaped (existing `models.py` invariant extended). **PASS.**
- **E — schema drift.** Attempt: break the design with a plausible LightRAG response change. A new
  text field → not in the allowlist, ignored (or, if it changes envelope shape, the structural
  gate raises); a renamed `file_path` → structural gate raises `GraphRAGProtocolError`; a version
  bump → `VERIFIED_LIGHTRAG_VERSION` / health gate. HYBRID shield fails closed on both same-version
  drift and version bumps. **PASS (fails predictably, never silently mis-parses).**
- **F — logging/security.** Attempt: get raw content or credentials into logs/exceptions. Logging
  allowlist is counts/mode/timing/version/error-category only; errors never echo bodies; API key
  is never logged (`client.py:151/178`); `GraphQueryResult.raw` is not replicated on the structured
  type. **PASS.**
- **G — contract minimality.** Attempt: remove every field with no demonstrated consumer.
  `source_id` (citation), `provenance_quality` (audit/safety), `diagnostics` counts (empty-state
  + security accounting) are each justified; `evidence_types` and `supporting_chunk_count` are the
  two "soft" fields — kept as the minimal, content-free graph-value hook and breadth signal, but
  explicitly **optional** and droppable if a future review finds no consumer. `graph_details` tier
  and all entity/relation payload are already removed. **PASS (documented as the minimal set).**
- **H — phase boundary.** No production/test/retrieval/RRF/fusion/reranker/Ask/Chat/frontend/
  migration/API/provider/`.env` code exists; `OPEN_NOTEBOOK_GRAPHRAG_ENABLED` stayed false; sidecar
  not started; no DB/Source/LightRAG-storage mutation; no re-clone/live probe. Documentation +
  planning only. **PASS.**

Only documentation/design content was produced; no fix required an implementation change.

---

## 29. Security / side-effect gate (task §47)

```
PROVIDER_TRAFFIC                     = NO
DATABASE_MUTATION                    = NO
SOURCE_MUTATION                      = NO
LIGHTRAG_STORAGE_MUTATION            = NO
SIDECAR_STARTED                      = NO
OPEN_NOTEBOOK_GRAPHRAG_ENABLED       = false (unchanged)
INTERNAL/PRIVATE SOURCE DATA         = none
CREDENTIALS                          = none in tree
PINNED LIGHTRAG CLONE                = not re-cloned; frozen 05/06 findings reused (out-of-repo if ever needed)
```

---

## 30. Required decisions (task §45)

```
RAW_LIGHTRAG_SCHEMA_EXPOSED_UPSTREAM     = NO
NORMALIZED_EVIDENCE_ADAPTER_REQUIRED     = YES
RAW_CHUNK_CONTENT_RETAINED               = NO
ENTITY_DESCRIPTION_RETAINED              = NO
RELATION_DESCRIPTION_RETAINED            = NO
EVIDENCE_PERSISTENCE_POLICY              = TRANSIENT_ONLY
CANONICAL_SOURCE_NORMALIZATION_OWNER     = GraphRAG integration/adapter layer
                                           (open_notebook/integrations/graphrag/; reuses
                                           is_valid_record_id / record_id_for; NOT Ask/frontend)
FOREIGN_PROVENANCE_POLICY                = DROP_AND_COUNT
MALFORMED_PROVENANCE_POLICY              = DROP_AND_COUNT
UNKNOWN_PROVENANCE_POLICY                = DROP_AND_COUNT
AMBIGUOUS_PROVENANCE_POLICY              = DROP_FROM_CANONICAL_AND_COUNT (PARTIAL corroborate-only)
PROVENANCE_QUALITY_MODEL                 = STRONG | PARTIAL | UNKNOWN
                                           (emission STRONG-only; PARTIAL corroborates; UNKNOWN dropped)
SCORE_FIELD_ALLOWED                      = NO
RANK_FIELD_ALLOWED                       = NO
NORMALIZED_OUTPUT_ORDER_SEMANTICS        = NON_RELEVANCE (unordered set; NON_RELEVANCE_ORDER if materialized)
RAW_PAYLOAD_LOGGING_ALLOWED              = NO
RAW_PAYLOAD_PERSISTENCE_ALLOWED          = NO
LIGHTRAG_SCHEMA_VERSION_SHIELD           = HYBRID (VERIFIED_LIGHTRAG_VERSION + api 0328 gate
                                           AND structural envelope validation; fail-closed)
ADAPTER_PAYLOAD_LIMITS_REQUIRED          = REQUIRES_IMPLEMENTATION_FORENSIC
                                           (defensive caps warranted; numbers deferred)
STRUCTURED_EVIDENCE_CONTRACT_DESIGNABLE  = YES
STRUCTURED_EVIDENCE_IMPLEMENTATION_READY = NO
PREFERRED_ADAPTER_OPTION                 = C (Source-only projection, via the strict ON-owned
                                           adapter discipline of B; D is the additive upgrade path)
RRF_CANDIDATE_INTERFACE_READY            = NO   (mandated; unchanged)
GRAPH_CANDIDATE_IMPLEMENTATION_READY     = NO   (mandated; unchanged)
```

---

## 31. Final report (task §50)

```
GRAPH_RAG_07_DESIGN_GATE       = COMPLETE
DATA_MINIMIZATION_REVIEW       = PASS
PROVENANCE_SAFETY_REVIEW       = PASS
MISUSE_RESISTANCE_REVIEW       = PASS
VENDOR_COUPLING_REVIEW         = PASS
SCHEMA_DRIFT_REVIEW            = PASS
SECURITY_LOGGING_REVIEW        = PASS
CONTRACT_MINIMALITY_REVIEW     = PASS
PHASE_BOUNDARY_REVIEW          = PASS

FILES_CHANGED =
  docs/agribank/development/GRAPHRAG_07_STRUCTURED_EVIDENCE_ADAPTER_CONTRACT.md      (new)
  docs/agribank/development/CURRENT_PHASE.md          (GraphRAG-05 status reconciliation + 07 row)
  .planning/2026-08-30-graphrag-07-evidence-adapter-contract/{task_plan,findings,progress}.md
  .planning/.active_plan                              (slug updated)

PRODUCTION_CODE_CHANGED   = NO
TEST_CODE_CHANGED         = NO
MIGRATION_CHANGED         = NO
PROVIDER_TRAFFIC          = NO
DATABASE_MUTATION         = NO
SOURCE_MUTATION           = NO
LIGHTRAG_STORAGE_MUTATION = NO
SIDECAR_STARTED           = NO
```

**Summary (task §50 1–20).** (1) relevant `/query/data` schema = `{status, message,
data{entities,relationships,chunks,references}, metadata}`, all fields optional/loosely-typed,
empty = 200+`status:"failure"`; (2) classification: `chunks[].content`=RAW_SOURCE_TEXT,
entity/relation descriptions+keywords=DERIVED_TEXT, `chunk/reference.file_path`=IDENTIFIER_ONLY
(STRONG), entity/relation `file_path`=IDENTIFIER_ONLY (PARTIAL), `weight`=STRUCTURAL,
`reference_id`=FREQUENCY, mode/counts=VENDOR_DIAGNOSTIC; (3) raw-content retention: chunk content
NO, entity desc NO, relation desc NO, raw references NO; (4) canonical provenance: STRONG chunk/
reference `file_path` → `source_id` via ON validators, owned by the integration/adapter layer;
(5) foreign/malformed/unknown = DROP_AND_COUNT, ambiguous KG = drop-from-canonical + PARTIAL
corroborate; (6) quality model STRONG|PARTIAL|UNKNOWN, emission STRONG-only; (7) Source contract
= `GraphSourceEvidence{source_id, evidence_types, supporting_chunk_count(frequency), provenance_
quality}`; (8) optional graph-detail tier = **deferred** (undemonstrated value); (9) dedup by
`source_id`, ordering NON_RELEVANCE (unordered set); (10) diagnostics = counts/mode/timing only,
content-free, distinguishing three empty-states; (11) error taxonomy = reuse existing GraphRAG
errors, best-valid per-record + all-or-nothing on envelope; (12) schema shield = HYBRID; (13)
logging content-free, Source-ID DEBUG_ONLY; (14) persistence TRANSIENT_ONLY (no migration); (15)
threat model 11 cases (reject/drop/count/prevent/fail); (16) preferred = **Option C** (Source-only
projection via strict ON-owned adapter); (17) future scope isolated to GraphRAG integration, behind
the flag, no migration/API/frontend/Ask change; (18) future test matrix = §23; (19) future
evaluation = §24 (no MRR/nDCG); (20) unresolved risks: loose-`Dict` upstream typing (mitigated by
HYBRID shield), concrete payload caps deferred (`REQUIRES_IMPLEMENTATION_FORENSIC`), graph value
still `INCONCLUSIVE` pending the larger-corpus evaluation, and the STRONG-provenance liveness check
(structural-only here) deferred to implementation.

Explicit decision values are frozen in §30.

**GRAPH_RAG_07_STRUCTURED_EVIDENCE_ADAPTER_DESIGN_COMPLETE** — no commit, no push, no tag; no
GraphRAG-07 implementation; GraphRAG-08 not started.
```
