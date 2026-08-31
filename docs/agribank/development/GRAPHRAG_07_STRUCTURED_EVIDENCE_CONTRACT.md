# GraphRAG-07 — Structured Evidence Contract & Implementation-Readiness Gate

**Status: FORENSIC / ARCHITECTURE / CONTRACT DESIGN ONLY — no implementation.** No production
code, no tests, no `client.query_data()`, no `/query/data` wiring, no GraphRAG-client change, no
retrieval-behavior change, no RRF/fusion/reranker, no migration, no API/frontend/Ask/Chat change,
no provider traffic, no DB / Source / LightRAG-storage mutation. `OPEN_NOTEBOOK_GRAPHRAG_ENABLED`
remained **false**; the sidecar was **not** started. This gate **freezes** the contract and the
operational policies and renders an implementation-readiness verdict; it does **not** build, and
it does **not** open GraphRAG-08.

**Companion document.** The exhaustive raw-`/query/data` field table, data classification, the
anti-corruption diagram, and the full field-by-field contract/policy tables live in the design
pass **[`GRAPHRAG_07_STRUCTURED_EVIDENCE_ADAPTER_CONTRACT.md`](GRAPHRAG_07_STRUCTURED_EVIDENCE_ADAPTER_CONTRACT.md)**
(same phase, produced first). This document is the authoritative GraphRAG-07 output: it carries
that design forward and adds the operational contract (config, feature-flag, timeout,
cancellation, retry, workspace, stale-source, rollback, backward-compat, observability,
performance), the ownership/failure tables, adversarial reviews A–G, the readiness checklist,
and the final decision flags. Where the two agree they are identical by construction; where this
document adds a new policy, it is the frozen one.

**Frozen checkpoints (task §0):** GraphRAG-04 `cb86a06` (tag `graphrag-04-approved`); GraphRAG-05
`833ec59` (tag `graphrag-05-forensic-approved`); GraphRAG-06 `d7e6a5b` (tag
`graphrag-06-forensic-approved`). Pinned LightRAG `v1.5.6` commit `b33c6b0` — the retained clone,
if used, is a **read-only, out-of-repo** spot-check only; it is **not** moved or copied into this
repository, and this gate needed no new probe.

**Frozen 04/05/06 decisions (task §1) — authoritative, not reopened.** They are restated in the
companion doc's header and are carried unchanged: `RRF_CANDIDATE_INTERFACE_READY = NO`;
`HYBRID_VALUE_EVIDENCED = INCONCLUSIVE`; `LIGHTRAG_DIRECT_SCORED_SOURCE_SURFACE = NO`;
`SOURCE_PROVENANCE = STRONG(chunk/reference)/PARTIAL(entity/relation)`;
`SOURCE_LEVEL_AGGREGATION_DEFENSIBLE = REQUIRES_EXPERIMENT`; `GRAPH_NATIVE_RANKING_SIGNAL = NO`;
`QUERY_DATA_AVAILABLE = YES`; `QUERY_DATA_AVOIDS_FINAL_ANSWER_GENERATION = YES`;
`QUERY_DATA_OTHER_LLM_CALLS_REMAIN = YES`; `RETRIEVAL_SEMANTICS_PARITY = YES`;
`QUERY_DATA_EXPOSES_VALID_RANK = NO`; `QUERY_DATA_EXPOSES_VALID_SCORE = NO`;
`QUERY_DATA_CORPUS_MUTATION = NO`; `QUERY_DATA_CACHE_MUTATION = CONFIG_DEPENDENT`;
`STRUCTURED_EVIDENCE_CONTRACT_DESIGNABLE = YES`; `PREFERRED_ARCHITECTURE = B`; `GRAPH_RAG_ROLE =
UNRANKED_EVIDENCE_ENGINE + PROVENANCE_ENRICHER + CONTEXT_EXPANDER`.

> **Boundary B (sidecar → external LLM/embedding/rerank providers) remains NOT APPROVED for real
> internal data.** Synthetic/public content only, every phase.

---

## 1. Core purpose (task §2) and headline verdict

GraphRAG-06 established `/query/data` as architecturally promising. GraphRAG-07 answers: **can a
small, safe, Open Notebook-owned structured-evidence contract be frozen precisely enough for a
future implementation phase?**

**Verdict: the CONTRACT and all safety/operational POLICIES are FROZEN
(`STRUCTURED_EVIDENCE_CONTRACT_FROZEN = YES`), but `STRUCTURED_EVIDENCE_IMPLEMENTATION_READY =
NO`.** Readiness is withheld for exactly two non-safety reasons, both inherited from frozen
inputs, neither a §76 stop condition and neither fixable by contract design:
1. **Value is not yet evidenced** — `HYBRID_VALUE_EVIDENCED = INCONCLUSIVE` (04) and
   `SOURCE_LEVEL_AGGREGATION_DEFENSIBLE = REQUIRES_EXPERIMENT` (05). The larger-corpus evaluation
   (05 §12; runnable on the *existing* `/query` surface, no new adapter needed) must show the
   structured evidence adds recall/provenance value before spending an implementation phase.
2. **This contract freeze is itself awaiting independent review** (Agribank §10/§11: a gate's
   output is review input, not a self-certified green light).

Every §76 STOP condition was checked and **none fired** (§13): no forced schema leak, ownership
is enforceable, no fake rank/score, foreign/malformed resolved, no raw-text persistence, rollback
needs no migration, no LightRAG or vector-retrieval modification, parity assumptions intact,
security boundary unambiguous. A `NO` readiness verdict with a frozen contract is the intended,
acceptable outcome of this gate.

---

## 2. The frozen normalized contract (task §5, §8, §43, §44)

Design sketches — **NOT code**. Project-native immutable style (frozen `dataclass`, matching
`models.py` `GraphReference`/`GraphQueryResult`), internal-only, `TRANSIENT_ONLY`.

```text
GraphEvidenceResult                       # top-level; runtime-only, never persisted
  sources: frozenset[GraphSourceEvidence]  # unordered set — rank impossible by construction
  diagnostics: GraphEvidenceDiagnostics    # content-free counts/mode/timing
  status: EvidenceStatus                    # SUCCESS | EMPTY | DEGRADED  (FAILURE is an exception)

GraphSourceEvidence                        # one per canonical Source; dedup key = source_id
  source_id: str                           # REQUIRED. canonical ON id (record_id_for over
                                           #   _PROVENANCE_TABLES). Invariant: is_valid_record_id.
  evidence_types: frozenset[EvidenceType]  # REQUIRED, non-empty. {DIRECT_CHUNK, GRAPH_ENTITY,
                                           #   GRAPH_RELATIONSHIP}
  supporting_chunk_count: int              # REQUIRED, >= 1. FREQUENCY / EVIDENCE COUNT of distinct
                                           #   STRONG chunks/references. *** NOT a relevance score ***
  provenance_quality: ProvenanceQuality    # REQUIRED. == STRONG at emission (invariant, §4)
  # NO score / rank / confidence / relevance / priority field. Absent by construction.

GraphEvidenceDiagnostics                   # content-free (task §19)
  query_mode: str                          # e.g. "hybrid"
  canonical_source_count: int
  raw_evidence_present: bool
  entity_count: int; relationship_count: int; chunk_count: int; reference_count: int
  malformed_provenance_count: int; foreign_provenance_count: int; unknown_source_count: int
  duplicate_reference_count: int
  final_answer_generation: bool = False    # invariant: /query/data never generates a final answer
  latency_ms: int | None
```

**Field semantics + classification (task §43 contract table):**

| Field | Type | Req | Meaning | Origin | Data class | Relevance signal? | Loggable? | Persistable? | Invariant | Risk |
|---|---|---|---|---|---|---|---|---|---|---|
| `source_id` | str | yes | canonical Source | `chunk/reference.file_path` | IDENTIFIER_ONLY | **no** | DEBUG_ONLY | no (transient) | `is_valid_record_id` true | wrong-record if re-parsed → use `record_id_for` |
| `evidence_types` | frozenset | yes | channels that surfaced it | normalization | STRUCTURAL_METADATA | **no** | yes (as set) | no | non-empty; deduped | must not encode order |
| `supporting_chunk_count` | int≥1 | yes | # distinct STRONG chunks | dedup step | FREQUENCY_SIGNAL | **no — frequency** | yes | no | ≥ 1 | misuse as score |
| `provenance_quality` | enum | yes | ownership grade | §4 | STRUCTURAL_METADATA | no | yes | no | == STRONG | — |
| `diagnostics.*_count` | int | yes | counts | normalization | STRUCTURAL_METADATA | no | yes | no | ≥ 0 | none |
| `diagnostics.query_mode` | str | yes | mode | request | VENDOR_DIAGNOSTIC | no | yes | no | ∈ modes | — |
| `status` | enum | yes | result state | §5 | STRUCTURAL_METADATA | no | yes | no | see §5 | — |

**Contract invariants (task §44), frozen:** `source_id` always canonical; **no FOREIGN, INVALID,
or UNKNOWN evidence in `sources`**; no `score`/`rank`/`confidence`/`relevance`/`priority` field;
list/set position carries **no** relevance; duplicate `source_id` forbidden (set + dedup key);
`evidence_types` deduplicated and non-empty; all counts non-negative; no raw provider payload; no
credentials; no raw LightRAG model object; diagnostics content-free.

`supporting_chunk_count` is documented **only** as a FREQUENCY / EVIDENCE COUNT (task §8/§36): the
number of distinct STRONG-owned chunks/references for that Source, counted **after** dedup, per
Source. It must never be sorted-on, thresholded as confidence, or described as relevance. The
three other candidate counts (`entity_evidence_count`, `relationship_evidence_count`,
`reference_count` per-Source) were evaluated (task §36) and **removed** from the Source object —
they add complexity, tempt relevance misuse, and have no consumer; the aggregate versions survive
only in content-free diagnostics.

---

## 2a. Cross-document reconciliation — authoritative names & values (independent review, 2026-08-30)

The independent contract review found that the design companion
(`GRAPHRAG_07_STRUCTURED_EVIDENCE_ADAPTER_CONTRACT.md`, written first) disagrees with this
authoritative document on several type/field names, one enum size, and one exception disposition.
**This document governs.** The companion's differing decisions are **superseded** as below; a
future implementer follows the "Authoritative" column only.

| Concern | Companion (superseded) | **Authoritative (this doc)** |
|---|---|---|
| diagnostics type name | `EvidenceDiagnostics` | **`GraphEvidenceDiagnostics`** |
| valid-source count field | `valid_source_count` | **`canonical_source_count`** |
| unknown count field | `unknown_provenance_count` | **`unknown_source_count`** |
| dedup count field | `deduplicated_count` | **`duplicate_reference_count`** |
| final-answer flag | `final_answer_call_skipped = True` | **`final_answer_generation = False`** (same fact, canonical polarity) |
| `GraphEvidenceResult` shape | `{sources, diagnostics}` (no status) | **`{sources, diagnostics, status: EvidenceStatus}`** (§2/§5) |
| `ProvenanceQuality` enum | 3 states `{STRONG, PARTIAL, UNKNOWN}` | **5 states `{STRONG, PARTIAL, INVALID, FOREIGN, UNKNOWN}`** (§4) |
| CANCELLED disposition | maps to `GraphRAGUnavailableError` (raise) | **propagate `asyncio.CancelledError` unwrapped — never a `GraphRAGError`** (§5/§7.3) |
| `supporting_chunk_count` cardinality | "OPTIONAL / ≥ 0" | **REQUIRED and `≥ 1` on every emitted Source when the field is included** (see below) |

- **`ProvenanceQuality` 5 states (H-6).** `INVALID` (structurally malformed `file_path`) and
  `FOREIGN` (valid-format id, wrong ownership table) are **distinct internal classification
  states** feeding `malformed_provenance_count` / `foreign_provenance_count`. The **emitted**
  `GraphSourceEvidence.provenance_quality` still only ever carries `STRONG` (§4 invariant); the
  other four are normalizer-internal + diagnostic.
- **CANCELLED (H-5).** A *caller cancellation* propagates `asyncio.CancelledError` unwrapped and is
  **not** a failure. A *timeout* (a distinct event) still maps to `GraphRAGUnavailableError`. The
  companion conflated the two; its mapping is corrected there and superseded here.
- **`supporting_chunk_count` inclusion (H-1, review A).** When the contract includes the field it is
  REQUIRED and `≥ 1` on every emitted Source (emission guarantees ≥1 STRONG anchor); it is **never**
  optional-per-instance. Whether the field is *included at all* is the single open **minimality**
  decision, resolved at the implementation forensic — it is the top removal candidate if no consumer
  needs an evidence-breadth count.
- **`provenance_quality` retention (L-1).** On an emitted Source this field is a **constant**
  (`STRONG`), so it carries no per-record information. It is retained **only** as a runtime-checkable
  assertion guarding a future implementation that might relax STRONG-only emission; if no later phase
  relaxes that invariant, it is a removal candidate at that review.
- **Naming (L-2).** "supporting" in `supporting_chunk_count` is structural ("chunks that anchor this
  Source"), **not** an endorsement of relevance; a rename (e.g. `distinct_chunk_count`) was
  considered and deferred to avoid churn — any rename updates both documents together.
- **`diagnostics.query_mode` (M-3).** An **opaque observability label**: the raw LightRAG
  `metadata.query_mode` string is passed through un-enumerated (ON does not validate the vendor mode
  vocabulary). A future implementation needing a constrained value space defines an ON-owned mode
  enum with an `UNKNOWN` fallback; until then the passthrough is intentional and content-free (a mode
  name, never query text).

---

## 3. Evidence types (task §9, §35) — frozen

```
EvidenceType = { DIRECT_CHUNK, GRAPH_ENTITY, GRAPH_RELATIONSHIP }
```

| Type | Meaning | Derived from | Provenance chain | Contains raw text? | Graph-native? | Deterministic on v1.5.6? |
|---|---|---|---|---|---|---|
| `DIRECT_CHUNK` | a STRONG chunk/reference anchored this Source (the ownership anchor; only type that can appear alone) | `chunks[].file_path` / `references[].file_path` | file_path → source_id (lossless) | no | no (vector-grounded) | yes |
| `GRAPH_ENTITY` | an entity's PARTIAL provenance corroborates an already-STRONG Source | `entities[].file_path` via chunks | many-to-many | no | yes | yes |
| `GRAPH_RELATIONSHIP` | ditto from a relationship | `relationships[].file_path` via chunks | many-to-many | no | yes | yes |

`REFERENCE` collapses into `DIRECT_CHUNK` (same STRONG `file_path` join key — a vendor-shaped
distinction with no consumer value). `MULTI_HOP` / `CROSS_SOURCE_RELATION` are **not** modeled:
they cannot be derived deterministically from pinned v1.5.6 without inventing semantics (task §9,
§35). Many-to-many entity/relation evidence never *duplicates* a Source — it adds one membership
marker to the single `GraphSourceEvidence` for each STRONG-owned Source it corroborates (task §35).

**No-rank on set cardinality (review M-2).** The **size** of `evidence_types` (1 vs 2 vs 3 members)
MUST NOT be read as a rank or relevance signal: a Source surfaced by all three channels is **not**
"more relevant" than one surfaced by a single `DIRECT_CHUNK` — set-size is a structural artifact of
graph topology, not a quality metric. This joins `supporting_chunk_count`, `weight`, node degree,
`reference_id`, and list position on the frozen no-disguised-rank prohibition (§7.7).

---

## 4. Provenance-quality model (task §10) & policy (task §11) — frozen

**Vocabulary** (retain only operationally useful states): `STRONG`, `PARTIAL`, `INVALID`,
`FOREIGN`, `UNKNOWN`.
- **STRONG** — lossless canonical mapping satisfying GraphRAG-03D ownership (`chunk`/`reference`
  `file_path` validates via `is_valid_record_id`). The **only** class admitted to `sources`.
- **PARTIAL** — valid supporting evidence but many-to-many/indirect (entity/relation). Corroborates
  a STRONG Source (adds an `EvidenceType`); never creates a Source. Distinct from INVALID.
- **FOREIGN** — valid-looking id mapping outside the expected ownership boundary (wrong table / not
  ON-owned). Dropped + counted.
- **INVALID** — malformed/unverifiable provenance (fails structural validation). Dropped + counted.
- **UNKNOWN** — `unknown_source` literal / absent `file_path`. Dropped + counted.

**Emission invariant:** a `GraphSourceEvidence` is emitted **only** from a STRONG anchor; therefore
its `provenance_quality` is STRONG by construction. PARTIAL corroborates; FOREIGN/INVALID/UNKNOWN
never enter `sources` and survive only as diagnostic counts. `PARTIAL` is deliberately **not**
conflated with `INVALID` (task §10).

**Handling matrix (task §11) — FAIL_OPEN vs FAIL_CLOSED vs DROP_AND_REPORT:**

| Condition | Policy | Result |
|---|---|---|
| malformed `file_path` / unparseable RecordID | **DROP_AND_REPORT** | drop record; `malformed_provenance_count++` |
| foreign Source id | **DROP_AND_REPORT** | drop; `foreign_provenance_count++`; never cited |
| `unknown_source` / missing `file_path` | **DROP_AND_REPORT** | drop; `unknown_source_count++` |
| missing `references`/`chunks` arrays (but envelope valid) | **FAIL_OPEN** | `EMPTY` / `DEGRADED` per §5; diagnostics explain |
| duplicate references | **DROP_AND_REPORT** (dedup) | counted once; `duplicate_reference_count++` |
| unexpected LightRAG **field type** / missing required envelope field | **FAIL_CLOSED** | `GraphRAGProtocolError` (never partial-trust) |
| future/unknown **response field** (additive, envelope intact) | **FAIL_OPEN** (ignore) | not in allowlist → ignored; envelope still validated |

Security-sensitive provenance prefers **fail-closed or drop-and-report**; **unknown provenance
never becomes a guessed canonical Source** (task §11). A *structural* schema breach (wrong type,
missing required field) is fail-closed; a *content* breach (one bad record among good) is
drop-and-report so good evidence survives.

---

## 5. Result-state, error model & diagnostics (task §17, §18, §19)

**Result states** (adopted — they are needed to distinguish security-relevant zero-Source cases,
companion §14):

| State | Meaning | Signature |
|---|---|---|
| `SUCCESS` | ≥1 canonical Source, no rejects | `canonical_source_count>0`, reject counts 0 |
| `DEGRADED` | ≥1 canonical Source **and** ≥1 dropped malformed/foreign/unknown | `canonical_source_count>0`, some reject count >0 |
| `EMPTY` | valid query, **zero** canonical Sources | `canonical_source_count==0`; `raw_evidence_present` tells "graph knew nothing" (false) from "all rejected" (true) |
| `FAILURE` | no trustworthy result | **raised as an exception**, not a result object |

Diagnostics live **inside** `GraphEvidenceResult` (not a separate object): they are always small,
content-free, and always wanted with the result. `SUCCESS`/`EMPTY`/`DEGRADED` are result objects;
`FAILURE` is an exception so a caller cannot accidentally treat a failed call as empty evidence.

**Error model (task §17)** — reuse the existing `GraphRAGError` hierarchy (`models.py:57-123`); no
new exception classes. Mapping and disposition:

| Condition | Existing type | Disposition |
|---|---|---|
| `SIDECAR_UNAVAILABLE` (refused/DNS) | `GraphRAGUnavailableError` | exception (FAILURE) |
| `TIMEOUT` | `GraphRAGUnavailableError` | exception (FAILURE) |
| `CANCELLED` | (propagate `asyncio.CancelledError`, see §8) | neither — cancellation, not failure |
| `AUTH_FAILURE` (401/403) | `GraphRAGConfigurationError` | exception (FAILURE) |
| `UNSUPPORTED_SCHEMA` / route absent (404/405) | `GraphRAGConfigurationError` | exception (FAILURE) |
| `INVALID_RESPONSE` (non-JSON, wrong types, missing required) | `GraphRAGProtocolError` | exception (FAILURE) |
| `UPSTREAM_INTERNAL_ERROR` / `PROVIDER_FAILURE` (HTTP 5xx) | `GraphRAGServerError` | exception (FAILURE) |
| `MALFORMED_PROVENANCE` (per-record) | — | `DEGRADED` result + count |
| `FOREIGN_PROVENANCE` (per-record) | — | `DEGRADED` result + count |
| `NO_EVIDENCE` (0 Sources) | — | `EMPTY` result |

Raw LightRAG/provider exception text is **never** propagated upward: the existing errors already
refuse to echo response bodies (`client.py:199/351`), and that discipline extends to the evidence
path (no content, no provider payload, no host details, no credentials, no internal implementation
detail in any surfaced message).

---

## 6. Ownership boundaries (task §6, §60) — frozen

```
Open Notebook consumers (future Ask / citation / provenance debugger / GraphRAG eval)
        │  see ONLY GraphEvidenceResult (ON-owned types)
        ▼
Structured Evidence Service (service.py)      — orchestration, fail-open policy
        ▼
Canonical Evidence Normalizer (integration)   — Source-ID validation, foreign/malformed, dedup, diagnostics
        ▼
LightRAG Adapter + HTTP client (client.py)    — VENDOR_SCHEMA_BOUNDARY: raw schema stops here
        ▼
POST /query/data  (LightRAG v1.5.6)
```

**Ownership table (task §60):**

| Concern | Owner | Reason | Allowed knowledge | Forbidden knowledge |
|---|---|---|---|---|
| HTTP schema / URLs / field names | **client.py** | vendor boundary | LightRAG JSON shape | leaking it upward |
| auth header (X-API-Key) | client.py | credential confinement | key value | exposing/logging key |
| timeout / cancellation | client.py | transport concern | httpx config | — |
| LightRAG version compatibility | client.py + config | single vendor boundary | `VERIFIED_LIGHTRAG_VERSION`, api `0328` | — |
| response parsing / validation | client.py (+ envelope guard) | vendor boundary | raw dict | passing raw dict up |
| Source ID normalization | **normalizer** (integration) | ON owns canonical ids | `is_valid_record_id`/`record_id_for` | inventing ids |
| strong-ownership checks | normalizer | 03D authoritative | `_PROVENANCE_TABLES` | promoting foreign/unknown |
| foreign/malformed provenance | normalizer | safety | counts | guessing a Source |
| dedup / evidence grouping | normalizer | determinism | source_id key | order-as-rank |
| raw-content minimization | client + normalizer | privacy | drop text | retaining text |
| diagnostics | normalizer | observability | counts | content |
| logging / redaction | all layers | content-free rule | counts/mode/timing | content/creds |
| **Ask / Chat** | consumer | must stay vendor-agnostic | `GraphEvidenceResult` | **any LightRAG field name** |
| **frontend** | consumer | must stay vendor-agnostic | ON DTO | **any LightRAG field name** |

**`VENDOR_SCHEMA_BOUNDARY = the GraphRAG HTTP client / adapter (`client.py`).** Raw LightRAG
`/query/data` structures (entities/relationships/chunks/references dicts, `weight`, `reference_id`,
`file_path`) **may not** escape the integration package (task §7). Confirmed by Review B (§12).

---

## 7. Operational policies — frozen

### 7.1 Text retention / data minimization (task §14, §15, §16, §40)
`RAW_SCHEMA_ESCAPE = NO`; `RAW_CHUNK_TEXT = NEVER` (not even OPTIONAL_TRANSIENT — the contract has
no text-bearing field, and Ask retrieves chunk text from the canonical Source itself, so an
optional flag would add exposure surface for no demonstrated need); `ENTITY_DESCRIPTION = DROP`;
`RELATIONSHIP_DESCRIPTION = DROP`; `metadata.keywords = DROP` (echoes query). Retention posture is
**Option A — ID/provenance-only contract** (task §14). Raw response retention (task §40):
`raw_response`/`raw_entities`/`raw_relationships`/`raw_chunks`/`raw_references` = **NO** on the
public contract; the raw dict is a transient local in the client, projected then discarded; the
`GraphQueryResult.raw` escape hatch on the *legacy* type (`models.py:488`) is **not** replicated.

### 7.2 Config / auth (task §21) & feature flag / rollout (task §22)
`CONFIG_POLICY = reuse existing GraphRAGConfig` (`enabled`, `base_url`, `timeout`, `api_key`;
`load_config()` per-request). No new credential concept. `FEATURE_FLAG_POLICY = F1+F3`: the future
`query_evidence()` method lives behind the existing `OPEN_NOTEBOOK_GRAPHRAG_ENABLED` flag (same
`config.configured` gate as `client.query()`) **and** is purely additive / explicitly-called (F3) —
**no** new `OPEN_NOTEBOOK_GRAPHRAG_STRUCTURED_EVIDENCE_ENABLED` flag. Rationale vs the 03E
precedent: a dedicated default-OFF lock exists for REBUILD because it fans the whole corpus across
Boundary B; a single read-only evidence query is **not** a destructive fan-out, so it needs no
extra lock (avoids configuration sprawl, task §22). Any decision to switch a *consumer* (e.g. Ask)
onto the evidence path is that consumer's explicit adoption decision — it is where an adoption
toggle would live, not on the adapter method.

### 7.3 Timeout (task §27), cancellation (task §28), retry (task §29)
`TIMEOUT_POLICY = reuse OPEN_NOTEBOOK_GRAPHRAG_TIMEOUT` (total HTTP timeout, default 30 s,
`config.py:18`); timeout maps to `GraphRAGUnavailableError` (`client.py:156`) — a caller **can**
distinguish it from a config/auth failure by exception type, though not from a connect failure
(both are `Unavailable`, which is acceptable: both mean "sidecar not usable now"). No per-phase
timeout. `CANCELLATION_POLICY`: propagate `asyncio.CancelledError` unwrapped; cancel the in-flight
HTTP request; cancellation is **never** converted to a permanent failure and **never** persists
evidence (TRANSIENT_ONLY) and triggers **no** background retry. `RETRY_POLICY = CALLER_OWNED` for
the evidence query — it is a user-facing read operation and must **not** inherit the indexing
lifecycle's auto-retry:

| Class | Policy |
|---|---|
| network connect error | NO_RETRY (surface `Unavailable`; caller decides) |
| timeout | NO_RETRY (caller-owned) |
| HTTP 429 | NO_RETRY at adapter (surface; caller/back-pressure owns it) |
| HTTP 409 | N/A (no insert on the read path) |
| HTTP 500 | NO_RETRY (surface `ServerError`) |
| malformed response | NO_RETRY (fail-closed `ProtocolError`) |
| auth failure | NO_RETRY (config error) |

### 7.4 Workspace (task §26)
`WORKSPACE_POLICY = default single workspace; no per-request workspace mixing`. Current ON config
exposes **no** workspace env var and the client sends **no** `LIGHTRAG-WORKSPACE` header
(`client.py:132-136`), so the sidecar's default workspace is authoritative. The evidence path
inherits this unchanged; it must **not** silently mix evidence across workspaces. If a future
multi-workspace need arises it is a separate, explicit config addition — not invented here.

### 7.5 Stale / deleted Source (task §32, §33, §34) & DB lookup
`SOURCE_OWNERSHIP` validation is **syntax + structural ownership only** (`OPTION DB1`): parse
`file_path` → validate canonical RecordID via `record_id_for` → `_PROVENANCE_TABLES` membership →
accept as `source_id`. **No DB lookup** in the normalization path (`DB_LOOKUP = NO`). Rationale:
ownership is establishable purely from the deterministic Source-ID encoding (03D / `compute_doc_id`
determinism); a live existence check adds DB cost and a race window for no correctness gain at this
layer. `STALE_SOURCE_POLICY`: if LightRAG returns provenance for a Source that no longer exists in
SurrealDB, the evidence path **drops nothing on that basis** here (it cannot tell — no lookup) and
emits the structurally-valid `source_id`; the **consumer** (future Ask/citation) resolves the
Source and, on a miss, drops it with a diagnostic. The query path **never** triggers reconciliation
or any lifecycle mutation (task §34) — the 03B/03C/03D lifecycle owns staleness; a read must not
mutate. `OPTION DB3` (optional validation mode) is noted as a future consumer-side choice, not an
adapter default.

**CONSUMER REQUIREMENT — HARD (review M-1).** Because normalization does no existence lookup, a
structurally-valid `source_id` for a Source that has since been deleted from SurrealDB **can**
appear in `sources`. Any consumer that surfaces a `source_id` to a user or serializes it as a
citation **MUST** perform a live canonical Source existence check and **drop** the id (recording a
diagnostic) on a miss. GraphRAG evidence identity is **not** proof of current existence; a consumer
that skips this check and shows the stale id to a user **violates** the stale-source policy. This is
frozen as `STALE_SOURCE_CONSUMER_LOOKUP_REQUIRED = YES` (at the citation/consumer layer). It does
**not** move the lookup onto the query/normalization path (which stays mutation-free and lookup-free
by design) — it fixes the authority for staleness at the consumer, where current truth lives.

### 7.6 Cache / corpus mutation (task §30, §31)
`CACHE_MUTATION`: accept LightRAG's config-dependent cache behavior as upstream semantics; the
adapter does **not** change LightRAG caching, does **not** expose it in the ON contract, and does
**not** attempt to disable it (that would change retrieval semantics). `CORPUS_MUTATION = NO`
(frozen invariant, task §31): the evidence query performs **no** Source mutation, **no** GraphRAG
indexing-lifecycle mutation, **no** LightRAG document insert/delete, **no** canonical DB write.
Cache writes (keyword cache, config-dependent) are **distinct from** corpus mutation and do not
violate this invariant.

### 7.7 Ordering (task §12, §13)
`ORDER_SEMANTICS = NONE` — the public contract is an **unordered semantic set** (`frozenset`);
position cannot be read. If a concrete implementation materializes a sequence for deterministic
tests/logs, it sorts by canonical `source_id` and labels it `NON_RELEVANCE_ORDER`. Response order
from LightRAG (round-robin interleave, *05 §1*) **must not** silently become ranking — dedup by
`source_id` discards it.

---

## 8. Current `client.query()` future role (task §23) & backward compatibility (task §52)
`client.query()` (→ `/query`, answer-generating) **remains untouched and API-compatible**. The
future `query_evidence()` is **additive**; no caller silently switches behavior. GraphRAG-07 is
**not** authorized to replace, deprecate, or remove current production behavior. Any eventual
retirement of `query()` for evidence use is a separate, later, explicitly-authorized decision. This
preserves the frozen `RETRIEVAL_SEMANTICS_PARITY = YES` property and a clean rollback (§9).

---

## 9. Rollback (task §51) & vendor-upgrade boundary (task §53) — frozen
`ROLLBACK_PATH`: because the adapter is additive, `client.query()` is unchanged, evidence is
`TRANSIENT_ONLY`, and **no migration/table/worker** is introduced, rollback = (a) config: leave the
existing GraphRAG flag off, or (b) caller selection: consumers simply do not call
`query_evidence()`, or (c) code: delete the additive module(s). **No DB migration, no data cleanup,
no downtime, no caller rewrite** (Review F, §12). If the design ever required a migration, that is a
§76 STOP — it does not here (count stays 50).

`VENDOR_UPGRADE_BOUNDARY`: a LightRAG version change should require edits **only** inside
`client.py`/adapter. Post-upgrade compatibility tests required: envelope shape, `file_path`
semantics, `status:"failure"` empty behavior, route/auth presence, and the `VERIFIED_LIGHTRAG_
VERSION` bump. The `LIGHTRAG_SCHEMA_VERSION_SHIELD = HYBRID` (version gate + structural envelope
validation, fail-closed) ensures an unannounced change fails predictably rather than mis-parsing.

---

## 10. Observability (task §56), performance (task §57), payload bounds (task §37)
`OBSERVABILITY` (design only, not instrumented): minimum useful counters =
`graphrag_evidence_requests`, `graphrag_evidence_success|empty|degraded|failure`,
`graphrag_evidence_sources` (histogram), `graphrag_malformed_provenance`,
`graphrag_foreign_provenance`, `graphrag_evidence_latency`. **No high-cardinality labels** (never
`query`, `source_id`, entity name).

`PERFORMANCE` unknowns (task §57), classified honestly:

| Aspect | Class |
|---|---|
| `/query/data` response size | BOUNDED_BY_UPSTREAM (`top_k`/`chunk_top_k`) |
| entity/relationship expansion size | BOUNDED_BY_UPSTREAM |
| source count per query | BOUNDED_BY_UPSTREAM |
| parsing cost | REQUIRES_LIVE_MEASUREMENT |
| DB ownership-validation cost | KNOWN = zero (no DB lookup, §7.5) |
| text-copying cost | KNOWN ≈ zero (text dropped, not copied) |
| memory lifetime | KNOWN (request-scoped, transient) |

`ADAPTER_PAYLOAD_LIMITS_REQUIRED = REQUIRES_IMPLEMENTATION_EVALUATION` (task §37): defensive hard
caps (reject, not truncate) on Sources/chunks/entities/relationships/raw-bytes are the intended
posture (threat #7, companion §20), but Open Notebook has no existing convention that yields a
defensible number for a `/query/data` response, and upstream `top_k`/`chunk_top_k` already bound
counts request-side, so concrete values are deferred to the implementation phase's own measurement.
This is an explicitly-deferred **empirical constant** (§75-eligible): it does not affect contract
correctness, is measurable safely, and the conservative behavior (reject over cap) exists.

`SCHEMA_VERSIONING` (task §38): the contract is **internal-only** Python types, not persisted or
externally serialized, so **no `schema_version` field** now (avoid premature versioning); one is
added only if/when the contract is ever exposed over the API or persisted.

---

## 11. Synthetic conceptual example (task §45) — abstract, no real content

Two synthetic Sources; one surfaced by chunk + entity, one by chunk only; one dropped foreign ref;
demonstrates dedup, PARTIAL corroboration, no rank, no score. IDs are illustrative placeholders.

```text
# INPUT (conceptual /query/data, text fields elided):
data.chunks      = [ {file_path: source:syn_a, chunk_id: c1}, {file_path: source:syn_a, chunk_id: c2},
                     {file_path: source:syn_b, chunk_id: c3}, {file_path: <malformed>, chunk_id: c4} ]
data.entities    = [ {file_path: source:syn_a}, {file_path: unknown_source} ]
data.references  = [ {file_path: source:foreignsys:x, reference_id: 1} ]   # foreign table

# NORMALIZED GraphEvidenceResult (design sketch):
status  = DEGRADED
sources = {
  GraphSourceEvidence(source_id="source:syn_a", evidence_types={DIRECT_CHUNK, GRAPH_ENTITY},
                      supporting_chunk_count=2, provenance_quality=STRONG),
  GraphSourceEvidence(source_id="source:syn_b", evidence_types={DIRECT_CHUNK},
                      supporting_chunk_count=1, provenance_quality=STRONG),
}   # unordered set — position means nothing
diagnostics = { query_mode:"hybrid", canonical_source_count:2, raw_evidence_present:true,
                chunk_count:3, entity_count:1, relationship_count:0, reference_count:0,
                malformed_provenance_count:1, foreign_provenance_count:1, unknown_source_count:1,
                duplicate_reference_count:0, final_answer_generation:false }
# No score, no rank. syn_a is NOT "more relevant" than syn_b — count is breadth, not relevance.
```

(The exact SurrealDB-escaped presentation of an all-digit/id — e.g. `source:⟨…⟩` — is validated by
`is_valid_record_id`; the plain form above is illustrative only.)

---

## 12. Adversarial reviews (task §65–§71) — all PASS

- **A — contract minimality (§65).** Every field challenged for a known consumer: `source_id`
  (citation), `evidence_types` (graph-value hook + `DEGRADED`/parity introspection),
  `supporting_chunk_count` (breadth/debug; the one "soft" field, explicitly optional-to-drop if a
  future review finds no consumer), `provenance_quality` (audit/safety), diagnostics (safety
  accounting). Removed: per-Source entity/relation/reference counts, raw text, entity/relation
  descriptions, vendor ids, metadata prose, `raw_*`. **PASS.**
- **B — vendor leakage (§66).** No LightRAG field name (`file_path`, `reference_id`, `weight`,
  `entity_name`, `hl_keywords`) appears in any ON-owned contract type; raw dicts stop at
  `client.py`; consumers import only `GraphEvidenceResult`. **PASS.**
- **C — provenance safety (§67).** Malformed/foreign/unknown/stale/many-to-many/duplicate cases each
  have a deterministic safe outcome (§4 matrix; §11 example) with no guessing; STRONG-only emission.
  **PASS.**
- **D — false rank (§68).** No place presents array position, `reference_id`, `supporting_chunk_
  count`, edge weight, node degree, or entity/relationship counts as relevance: order is a set,
  frequency is labeled frequency, weight/degree/reference_id are dropped. **PASS (rank impossible by
  construction).**
- **E — privacy (§69).** Contract retains identifiers/provenance only; chunk text, entity/relation
  descriptions, file-path-as-text, provider error bodies, raw request/response all excluded. **PASS.**
- **F — rollback (§70).** Disable/revert needs no migration, no data cleanup, no caller rewrite, no
  downtime (additive method; `query()` untouched; transient evidence). **PASS.**
- **G — phase boundaries (§71).** No production Python, tests, migration, API route, client method,
  `GraphEvidenceResult` code, provider config, `.env`, DB change, sidecar call, or GraphRAG
  enablement was introduced. Documentation + planning only. **PASS.**

---

## 13. §76 STOP-condition check
None fired: (1) raw schema is **not** forced into Ask/frontend (ACL confines it); (2) Source
ownership **is** safely enforceable (structural + 03D); (3) contract requires **no** fake
score/rank; (4) foreign provenance **resolved** (DROP_AND_REPORT); (5) malformed provenance
**resolved** (DROP_AND_REPORT / fail-closed on schema); (6) raw source text is **not** persisted
(TRANSIENT_ONLY, no text field); (7) rollback needs **no** migration; (8) implementation needs
**no** LightRAG modification; (9) implementation needs **no** vector-retrieval change; (10)
`/query/data` parity assumptions are **not** contradicted (06 §4, re-checked against current
`client.py` — only `query()` exists, no parity claim broken); (11) security boundary is **not**
ambiguous (§6/§14). Therefore the contract is frozen; readiness is NO only for the value/aggregation
and review reasons in §1, not for any stop condition.

---

## 14. Security / data-egress contract (task §54, §55)
`SECURITY_CONTRACT` (frozen): use existing local sidecar X-API-Key auth; never log keys
(`client.py:151/178`); never return provider credentials; never serialize auth headers; reject
foreign provenance and malformed canonical ids; do **not** persist the raw `/query/data` response;
minimize raw source text (drop all); emit no internal content in exceptions; keep egress boundaries
documented. No new credential type. `DATA_EGRESS` language (frozen, task §55): `/query/data`
eliminates **final-answer generation** egress, **not** all provider activity — **Boundary B egress
remains** for retrieval-side keyword-LLM + query embeddings (06 §8). The contract must **never**
be described as "structured evidence = no LLM egress." `diagnostics.final_answer_generation=false`
records only the final-answer property, not an all-egress claim.

---

## 15. Required architecture diagram (task §59)
```
                  Open Notebook
                       │  (future Ask / citation / provenance debugger / GraphRAG eval)
                       ▼
           Structured Evidence Service            [service.py — orchestration, fail-open]
                       │
                       ▼
          Canonical Evidence Normalizer           [integration — ON owns canonical ids]
              │                     │
              │                     └── foreign / malformed / unknown ──► content-free diagnostics
              ▼
        LightRAG Adapter + HTTP client            [client.py — *** VENDOR_SCHEMA_BOUNDARY ***]
              │        │  *** RAW TEXT DISPOSAL POINT: drop chunk content + descriptions ***
              ▼
       POST /query/data  (LightRAG v1.5.6)
              │
              ▼
   entities / relations / chunks / references  (raw vendor JSON — never escapes the client)
              │
              ▼
          GraphEvidenceResult (sources / diagnostics / status — no text, no score, no rank)

TRUST BOUNDARIES:
  A: Open Notebook ↔ LightRAG sidecar     (internal; where this contract's minimization applies)
  B: LightRAG ↔ external providers         (retrieval-side LLM + embeddings STILL occur; not
                                            reduced by this adapter — only final-answer gen is gone)
```

---

## 16. Failure matrix (task §62)
| Failure | Trigger | Adapter behavior | Normalized outcome | Retry? | Loggable | Security risk |
|---|---|---|---|---|---|---|
| sidecar down | connect refused/DNS | `GraphRAGUnavailableError` | FAILURE (exception) | NO | error category | none |
| auth failure | 401/403 | `GraphRAGConfigurationError` | FAILURE | NO | category (no key) | key-leak if mishandled → prevented |
| timeout | httpx timeout | `GraphRAGUnavailableError` | FAILURE | NO | timeout=true, latency | none |
| cancelled | caller cancels | propagate `CancelledError` | (none) | NO | — | none |
| HTTP 429 | rate limit | `GraphRAGRequestError`/surface | FAILURE | NO (caller) | category | none |
| HTTP 500 | upstream error | `GraphRAGServerError` | FAILURE | NO | category | provider-body leak → prevented |
| malformed JSON | bad body | `GraphRAGProtocolError` | FAILURE | NO | category | body leak → prevented |
| schema mismatch | wrong types/fields | `GraphRAGProtocolError` | FAILURE (fail-closed) | NO | version, category | none |
| malformed provenance | bad file_path | drop + count | DEGRADED | n/a | malformed_count | promotion → prevented |
| foreign provenance | foreign id | drop + count | DEGRADED | n/a | foreign_count | cross-corpus cite → prevented |
| unknown_source | `unknown_source`/absent | drop + count | DEGRADED/EMPTY | n/a | unknown_count | guessed Source → prevented |
| empty evidence | `status:"failure"`/`data:{}` | recognize | EMPTY | n/a | raw_evidence_present=false | none |
| stale Source | Source deleted since index | emit id (no lookup); consumer drops | SUCCESS/DEGRADED (consumer resolves) | n/a | (consumer) | stale cite → consumer-side drop |

---

## 17. Policy table (task §63)
| Policy | Decision | Rationale |
|---|---|---|
| RAW_SCHEMA_ESCAPE | **NO** | ACL confines vendor schema to client.py |
| RAW_CHUNK_TEXT | **NEVER** | Ask has the Source; text = exposure with no need |
| ENTITY_DESCRIPTION | **DROP** | DERIVED_TEXT, undemonstrated value |
| RELATIONSHIP_DESCRIPTION | **DROP** | DERIVED_TEXT, undemonstrated value |
| SOURCE_DEDUP | **by canonical source_id** | one Source per record cluster; determinism |
| ORDER_SEMANTICS | **NONE (unordered set)** | no honest relevance signal; position ≠ rank |
| FOREIGN_PROVENANCE | **DROP_AND_REPORT** | never promote foreign id to canonical Source |
| MALFORMED_PROVENANCE | **DROP_AND_REPORT / FAIL_CLOSED on schema** | not proof of ownership; untrusted shape fails closed |
| STALE_SOURCE | **emit id, consumer resolves; no query-path mutation** | reads must not mutate lifecycle |
| CACHE_MUTATION | **accept upstream; do not change** | preserve retrieval semantics |
| TIMEOUT | **reuse OPEN_NOTEBOOK_GRAPHRAG_TIMEOUT** | no new semantics |
| RETRY | **CALLER_OWNED (NO_RETRY at adapter)** | user-facing read, not lifecycle write |
| WORKSPACE | **default single workspace; no mixing** | current config forwards none |
| FEATURE_FLAG | **reuse GRAPHRAG_ENABLED + additive method (F1+F3)** | no destructive fan-out; no sprawl |
| ROLLBACK | **additive; no migration** | delete module / don't call / flag off |

---

## 18. Future implementation file map (task §46) & minimum scope (task §47) — design only
| File | Why / responsibility | Expected change | Test seam | Rollback impact |
|---|---|---|---|---|
| `open_notebook/integrations/graphrag/models.py` | ON contract types | + `GraphEvidenceResult`, `GraphSourceEvidence`, `GraphEvidenceDiagnostics`, `EvidenceType`, `ProvenanceQuality`, `EvidenceStatus` | pure types | delete additions |
| `.../client.py` | HTTP + vendor boundary | + `query_evidence()`/`query_data()` (envelope+version guard, drop text) | injected `httpx` transport (`client.py:127`) | additive method; unused until called |
| `.../<normalizer>.py` (new) | canonical provenance + dedup + diagnostics | new small module reusing `is_valid_record_id`/`record_id_for` | unit (pure) | delete file |
| `.../service.py` | orchestration, fail-open | + evidence method | fake client | fail-open to `EMPTY`/None (existing `query()` pattern) |

**Explicitly NOT touched:** `Source` model, `vector_search`, Ask, Chat, frontend, worker lifecycle,
migrations, API routes. **Minimum future scope (task §47):** (1) typed models; (2) `/query/data`
client method; (3) vendor response parser; (4) canonical provenance normalizer; (5) content-free
diagnostics; (6) focused unit tests; (7) live synthetic v1.5.6 integration proof — proposal only.

---

## 19. Future test matrix (task §48) & live-proof gate (task §49) & parity eval (task §50) — design only
**Unit:** valid empty response · single Source · duplicate Source (dedup) · malformed file_path ·
foreign source · unknown_source · chunk mapping · entity many-to-many · relation many-to-many ·
duplicate evidence · **no rank** · **no score** · deterministic non-relevance ordering (if used) ·
diagnostics counts · raw text excluded · exception redaction. **Client:** auth header · base URL ·
workspace (default) · timeout · cancellation · 401 · 403 · 429 · 500 · malformed JSON · unknown
schema field (ignored) · missing required field (fail-closed). **Integration (synthetic-only, real
LightRAG v1.5.6):** `/query/data` · no final-answer generation · canonical provenance · **no corpus
mutation** · no non-benchmark Source mutation · cleanup.
**Live-proof gate (task §49) — future acceptance:** `STRUCTURED_EVIDENCE_CLIENT_PROBE`,
`STRUCTURED_PROVENANCE_PROBE`, `NO_FINAL_ANSWER_GENERATION`, `NO_FAKE_RANK`, `NO_FAKE_SCORE`,
`NO_CORPUS_MUTATION`, `FOREIGN_PROVENANCE_REJECTION`, `MALFORMED_PROVENANCE_HANDLING`, `CLEANUP`
all **= PASS**, synthetic/public data only.
**Parity eval (task §50):** same synthetic queries, `client.query()` vs structured adapter; compare
canonical Source set, reference/provenance set, entity/relation/chunk evidence, candidate breadth,
malformed/foreign counts, latency, final-answer LLM calls, error rate. **No ranking metrics, no
RRF, no benchmark run now.**

---

## 20. Contract-readiness checklist (task §58) & readiness rule (task §75)
All contract/policy items **FROZEN**: normalized result schema · Source evidence schema ·
provenance policy · foreign policy · malformed policy · dedup policy · order policy · text-retention
policy · error model · diagnostics model · logging policy · config policy · feature-flag policy ·
timeout policy · cancellation policy · retry policy · workspace policy · stale-source policy ·
rollback policy · test plan · live-proof plan · security contract. Per task §75 the MUST-remain-NO
items (provenance ownership, malformed, foreign, text retention, vendor boundary, error model,
rollback, security, timeout/cancellation) are **all resolved**, so the rule **permits** readiness —
but two non-safety gates keep it NO: unproven value (`HYBRID_VALUE_EVIDENCED = INCONCLUSIVE`,
`SOURCE_LEVEL_AGGREGATION_DEFENSIBLE = REQUIRES_EXPERIMENT`) and pending independent review of this
freeze. The one explicitly-deferred empirical constant (`ADAPTER_PAYLOAD_LIMITS_REQUIRED =
REQUIRES_IMPLEMENTATION_EVALUATION`) is §75-eligible (no correctness impact, safely measurable,
conservative reject-over-cap behavior exists) and does **not** by itself block readiness.

---

## 21. Security / side-effect gate (task §72)
```
PROVIDER_TRAFFIC               = NO
DATABASE_MUTATION              = NO
SOURCE_MUTATION                = NO
LIGHTRAG_STORAGE_MUTATION      = NO
SIDECAR_STARTED                = NO
OPEN_NOTEBOOK_GRAPHRAG_ENABLED = false (unchanged)
CREDENTIALS                    = none
INTERNAL SOURCE CONTENT        = none
PROVIDER RESPONSE CONTENT      = none
PINNED CLONE                   = not moved/copied into repo; no new probe
```

---

## 22. Final decision flags (task §74)
```
STRUCTURED_EVIDENCE_CONTRACT_FROZEN      = YES
RAW_VENDOR_SCHEMA_CONTAINED              = YES
SOURCE_OWNERSHIP_POLICY_FROZEN           = YES
PROVENANCE_POLICY_FROZEN                 = YES
FOREIGN_PROVENANCE_POLICY_FROZEN         = YES
MALFORMED_PROVENANCE_POLICY_FROZEN       = YES
DEDUP_POLICY_FROZEN                      = YES
ORDER_SEMANTICS_FROZEN                   = YES   (ORDER_SEMANTICS = NONE / unordered set)
TEXT_RETENTION_POLICY_FROZEN             = YES   (ID/provenance-only; RAW_CHUNK_TEXT = NEVER)
ERROR_MODEL_FROZEN                       = YES
DIAGNOSTICS_CONTRACT_FROZEN              = YES
LOGGING_POLICY_FROZEN                    = YES
CONFIG_POLICY_FROZEN                     = YES   (reuse GraphRAGConfig)
FEATURE_FLAG_POLICY_FROZEN               = YES   (F1+F3: reuse GRAPHRAG_ENABLED, additive method)
TIMEOUT_POLICY_FROZEN                    = YES
CANCELLATION_POLICY_FROZEN               = YES
RETRY_POLICY_FROZEN                      = YES   (CALLER_OWNED / NO_RETRY at adapter)
WORKSPACE_POLICY_FROZEN                  = YES   (default single workspace)
STALE_SOURCE_POLICY_FROZEN               = YES   (emit id; consumer resolves; no query-path mutation)
STALE_SOURCE_CONSUMER_LOOKUP_REQUIRED    = YES   (HARD, at citation/consumer layer; §7.5 / review M-1)
ROLLBACK_PATH_DEFINED                    = YES   (additive; no migration)
TEST_PLAN_COMPLETE                       = YES   (design)
LIVE_PROOF_PLAN_COMPLETE                 = YES   (design)
SECURITY_CONTRACT_FROZEN                 = YES
STRUCTURED_EVIDENCE_IMPLEMENTATION_READY = NO    (contract frozen; value UNEVIDENCED +
                                                  aggregation REQUIRES_EXPERIMENT + freeze awaiting
                                                  review; no §76 stop condition; no safety block)
QUERY_DATA_EXPOSES_VALID_RANK            = NO
QUERY_DATA_EXPOSES_VALID_SCORE           = NO
RRF_CANDIDATE_INTERFACE_READY            = NO
GRAPH_CANDIDATE_IMPLEMENTATION_READY     = NO
```

---

## 23. Final report (task §78)
```
GRAPH_RAG_07_CONTRACT_GATE   = COMPLETE
CONTRACT_MINIMALITY_REVIEW   = PASS
VENDOR_LEAKAGE_REVIEW        = PASS
PROVENANCE_SAFETY_REVIEW     = PASS
FALSE_RANK_REVIEW            = PASS
PRIVACY_REVIEW               = PASS
ROLLBACK_REVIEW              = PASS
PHASE_BOUNDARY_REVIEW        = PASS
SECURITY_REVIEW              = PASS

FILES_CHANGED =
  docs/agribank/development/GRAPHRAG_07_STRUCTURED_EVIDENCE_CONTRACT.md            (new — this doc)
  docs/agribank/development/GRAPHRAG_07_STRUCTURED_EVIDENCE_ADAPTER_CONTRACT.md    (companion, prior pass)
  docs/agribank/development/CURRENT_PHASE.md                                       (GraphRAG-07 row)
  .planning/2026-08-30-graphrag-07-structured-evidence-contract/{task_plan,findings,progress}.md
  .planning/.active_plan

PRODUCTION_CODE_CHANGED   = NO
TEST_CODE_CHANGED         = NO
MIGRATION_CHANGED         = NO
PROVIDER_TRAFFIC          = NO
DATABASE_MUTATION         = NO
SOURCE_MUTATION           = NO
LIGHTRAG_STORAGE_MUTATION = NO
SIDECAR_STARTED           = NO
```

**Summary (task §78 1–24).** (1) preferred contract = flat `GraphEvidenceResult{sources:frozenset,
diagnostics, status}` (Option A / ID-provenance-only, Source-level); (2) fields = `source_id`,
`evidence_types`, `supporting_chunk_count`, `provenance_quality` (+ content-free diagnostics +
status); (3) semantics per §2 (count = frequency, never relevance); (4) ownership = vendor schema
confined to `client.py`, canonical ids owned by the normalizer, Ask/frontend vendor-agnostic; (5)
`VENDOR_SCHEMA_BOUNDARY = client.py/adapter`; (6) provenance model STRONG|PARTIAL|INVALID|FOREIGN|
UNKNOWN, STRONG-only emission; (7) foreign/malformed/unknown = DROP_AND_REPORT (schema breach =
FAIL_CLOSED); (8) dedup by `source_id`; (9) ordering NONE (unordered set); (10) text retention =
ID/provenance-only, RAW_CHUNK_TEXT NEVER, descriptions dropped; (11) error model reuses existing
`GraphRAGError` types, FAILURE=exception; (12) diagnostics content-free counts/mode/timing; (13)
logging content-free, Source-ID DEBUG_ONLY, no keys/content; (14) config reuse
`GraphRAGConfig`, flag F1+F3 (no new flag); (15) rollout additive behind existing flag, rollback
without migration; (16) timeout reuse existing, cancellation propagated, retry CALLER_OWNED; (17)
workspace default single; (18) stale/deleted Source = emit id, consumer resolves, no query-path
mutation, no DB lookup; (19) rollback additive/no-migration; (20) future file map = models/client/
normalizer/service only; (21) full future unit+client+integration test matrix; (22) live synthetic
proof gate (9 PASS conditions); (23) unresolved empirical = payload caps + parsing cost + response
size (bounded-by-upstream / requires-live-measurement); (24) unresolved architectural risks = loose
upstream `Dict` typing (mitigated by HYBRID shield), value still INCONCLUSIVE (04) + aggregation
REQUIRES_EXPERIMENT (05) — the only true blockers to implementation readiness, both non-safety.

**GRAPH_RAG_07_STRUCTURED_EVIDENCE_CONTRACT_GATE_COMPLETE** — no commit, no push, no tag; no
implementation; GraphRAG-08 not started. Contract FROZEN; `STRUCTURED_EVIDENCE_IMPLEMENTATION_READY
= NO`.
```
