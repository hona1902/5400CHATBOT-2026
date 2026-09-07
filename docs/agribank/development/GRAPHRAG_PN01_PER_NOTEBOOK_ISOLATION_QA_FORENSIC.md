# GraphRAG-PN01 — Per-Notebook Isolation & Question-Answering Architecture (Forensic / Design Gate)

**Status:** OFFLINE FORENSIC + ARCHITECTURE DESIGN ONLY. **No code, no provider traffic, no live
LightRAG, no DB mutation, no migration, no Ask integration, no frontend change, no production
adapter, no RRF, no GraphRAG-09, no PN02 implementation.** Docs only.

**This is a NEW research track. It is NOT GraphRAG-09.** It does **not** reopen or modify any
GraphRAG-08 conclusion. GraphRAG-08 is **CLOSED / APPROVED**.

**Authoritative baseline (verified from git):**
- Branch `feature/graphrag-lifecycle`
- HEAD `3068dbdef081acb16fd92e9fea10704511da84bd`
- Annotated tag `graphrag-08-architecture-review-approved`
- Working tree CLEAN
- LightRAG pinned `v1.5.6` (`open_notebook/integrations/graphrag/config.py:57`,
  `VERIFIED_LIGHTRAG_VERSION = "v1.5.6"`)

---

## 0. Frozen GraphRAG-08 inheritance (do not modify)

These are historical/frozen GraphRAG-08 conclusions carried in unchanged:

```
FINAL_OPERATOR_ARCHITECTURE_DECISION            = B
FINAL_OPERATOR_ARCHITECTURE_DECISION_NAME       = KEEP_GD_AS_PROVENANCE_ONLY_RESEARCH_PATH
VALUE_EVIDENCE_READY                            = YES
GRAPH_RETRIEVAL_VALUE_EVIDENCED                 = NO
STRUCTURED_QUERY_DATA_RUNTIME_VALUE_EVIDENCED   = YES
LIGHTRAG_OPTIONAL_RESEARCH_SIDECAR              = RETAINED
GRAPHRAG_RESEARCH_PATH                          = RETAINED_OPTIONAL
GRAPHRAG_PRODUCTION_INTEGRATION                 = NOT_APPROVED
LIGHTRAG_PRIMARY_RETRIEVER                      = NOT_APPROVED
LIGHTRAG_RRF_INPUT                              = NOT_APPROVED
LIGHTRAG_ASK_INTEGRATION                        = NOT_APPROVED
UNCONSTRAINED_SOURCE_EXPANSION                  = NOT_APPROVED
GRAPH_RAG_09_JUSTIFIED                          = NO
```

GraphRAG-08 measured (frozen): the shared global graph returns a **fixed broad ~20/75 candidate
surface**, `set_precision 0.065`, `~18.5 FP/query`, **negative abstention 0.0**, no valid
rank/score, GQ≡GD identical Source sets; GD (`/query/data`) gives identical evidence to GQ
(`client.query`) at ~6.5× lower latency. Full detail:
[`GRAPHRAG_08_VALUE_EVIDENCE_ARCHITECTURE_REVIEW.md`](GRAPHRAG_08_VALUE_EVIDENCE_ARCHITECTURE_REVIEW.md)
and [`GRAPHRAG_08_FULL_BENCHMARK_ATTEMPT_6_RESULT.md`](GRAPHRAG_08_FULL_BENCHMARK_ATTEMPT_6_RESULT.md).

---

## 1. New product requirement (the concrete use case)

> Each Open Notebook **notebook** owns its **own isolated LightRAG graph/workspace/index**; a
> question asked *inside* a notebook uses **only that notebook's graph data** and must not retrieve
> evidence from Sources that belong to other notebooks.

```
Notebook A (Sources A1,A2,A3) ─► LightRAG workspace A
Notebook B (Sources B1,B2,B3) ─► LightRAG workspace B
Ask inside A ─► workspace A ONLY ─► never returns B1/B2/B3 (unless also members of A)
```

**Core question:** *Can per-notebook LightRAG workspace isolation solve the source-scoping
limitation discovered in GraphRAG-08?*

### 1a. The key distinction PN01 must not confuse (§3)

GraphRAG-08 established `PRE_RETRIEVAL_SOURCE_SCOPE_CONTROL = NO` for **per-query Source
allowlisting inside a shared graph** — `/query/data` body is `{query, mode, only_need_context,
top_k}` with no per-query ids/scope (`gd_seam.py:158-164`); the run's allowlist is applied
**post**-retrieval (`gd_seam.py:188`). PN01 evaluates a **different** axis: scope by **workspace /
index ownership**, not per-query allowlist. Selecting a notebook's *workspace* before the query is a
**pre-retrieval** control that operates one level up from the per-query allowlist GraphRAG-08 found
impossible. **These two are not the same finding and must not be conflated.**

---

## 2. Open Notebook notebook/source lifecycle — forensic (source-verified)

All citations first-party in this repo.

| Concern | Finding | Location |
|---|---|---|
| Notebook identity | `class Notebook(ObjectModel)`, table `notebook`, id `notebook:<rand>` | `open_notebook/domain/notebook.py:16-21`; schema `migrations/1.surrealql:44` |
| Source identity | `class Source(ObjectModel)`, table `source`, id `source:<rand>` | `notebook.py:402-431`; schema `migrations/1.surrealql:2` |
| **Membership** | **SurrealDB `RELATION` edge `reference` `FROM source TO notebook`** (`in`=source, `out`=notebook) | `migrations/1.surrealql:54-56` |
| **Cardinality** | **MANY-TO-MANY** — plain edge table, **no uniqueness/cardinality constraint** | `migrations/1.surrealql:54-56` |
| Add source→notebook | `Source.add_to_notebook()` → `relate("reference", …)`; API `RELATE $source->reference->$notebook` (idempotent) | `notebook.py:526-530`; `api/routers/notebooks.py:346-373` |
| Remove source from notebook | **edge-only** `DELETE FROM reference WHERE out=$nb AND in=$src` — Source not deleted | `api/routers/notebooks.py:389-405` |
| Delete notebook | notes always deleted; sources **unlinked** by default; only **exclusive** sources hard-deleted when `delete_exclusive_sources=True`; chat sessions deleted | `notebook.py:211-315` |
| Delete source (global) | cascades `source_embedding` + `source_insight` (+ DB event `1.surrealql:29-32`) + writes durable `graphrag_deletion` tombstone + wakes drain | `notebook.py:642-684`; `migrations/24.surrealql:44-50`/`25.surrealql:44-51` |
| Source reprocess / re-embed | `Source.vectorize()` fire-and-forget `embed_source` (idempotent delete-then-insert); full retry `POST /sources/{id}/retry` | `notebook.py:532-576`; `commands/embedding_commands.py:304-386` |
| GraphRAG (experimental) re-index | delete-then-insert into sidecar (`index_source`) | `open_notebook/integrations/graphrag/lifecycle.py:76-222` |

### 2a. Shared Sources are a first-class, supported ON concept — `SOURCE_SHARED_ACROSS_NOTEBOOKS_SUPPORTED_BY_ON = YES`

Not a hypothesis. `SourceCreate.notebooks: Optional[List[str]]` (`api/models.py:309-313`) creates a
Source into **many** notebooks at once; a Source is later linkable to further notebooks
(`notebooks.py:346-373`); and the delete path *reasons about it explicitly* —
`Notebook.get_delete_preview()` / `delete()` compute `assigned_others =
count(->reference[WHERE out != $notebook_id])` to distinguish **exclusive** vs **shared** Sources,
surfaced to the API as `exclusive_source_count` / `shared_source_count` (`notebook.py:161-209`,
`183-199`, `250-258`; `api/models.py:782-784`). **This is the single most consequential fact for
PN01** (drives §9, §10, §21).

### 2b. Ask/Search are NOT notebook-scoped today; only Chat is (and via full-context, not retrieval)

- `POST /search`, `POST /search/ask`, `POST /search/ask/simple` carry **no** notebook id;
  `SearchRequest`/`AskRequest` have no `notebook_id` (`api/models.py:39-47`, `56-60`;
  `api/routers/search.py:21-230`).
- The Ask graph runs `await vector_search(term, 10, True, True)` with **no** notebook filter
  (`open_notebook/graphs/ask.py:106`); `vector_search`/`text_search` call `fn::vector_search` /
  `fn::text_search`, which scan the **whole DB** with no `reference` join
  (`notebook.py:804-876`; `migrations/1.surrealql:74-173`).
- **Chat** *is* notebook-scoped, but by **full-context injection** — `Notebook.get_context()`
  concatenates every source's full text + insights + every note of the one notebook
  (`notebook.py:70-134`; `graphs/chat.py:15,24`) — **not** by a scoped vector/graph query.

**Consequence:** ON has **no notebook-scoped retrieval path today**. A notebook-scoped LightRAG
workspace would introduce notebook-scoped *retrieval* where none exists. The simplest ON-native way
to add the same scoping is a `reference`-join filter on `fn::vector_search` (see §32).

### 2c. Existing GraphRAG flag

`OPEN_NOTEBOOK_GRAPHRAG_ENABLED` (default OFF) + `_BASE_URL`/`_TIMEOUT`/`_API_KEY`;
`configured = enabled and base_url` (`config.py:97-112`, `73-76`). The experimental router
`/search/graph` is documented as POC/diagnostic and **does not touch** vector/text/Ask/Chat
(`api/routers/graphrag.py:1-25`). No notebook-level flag exists.

---

## 3. LightRAG v1.5.6 workspace semantics — forensic

LightRAG runs as an **external HTTP sidecar**; its source is not vendored/installed in this repo.
LightRAG-internal citations below are **second-hand** — taken from the repo's own verified v1.5.6
forensic notes (`open_notebook/integrations/graphrag/eval/cell_isolation08.py:12-24`, checked
against commit `b33c6b0` during GraphRAG-08E.1) — and are flagged as such. ON-side facts are
first-party and exact. (This mirrors the GraphRAG-05/06/08 methodology, which also treated pinned
LightRAG as an external, separately-cloned artifact.)

### 3a. What a `workspace` isolates — `WORKSPACE_DATA_ISOLATION = STRONG` (as a mechanism)

A `workspace` is a **hard partition**, not a soft filter (second-hand, `cell_isolation08.py:12-24`,
citing pinned v1.5.6 source):

| Store | Isolated by workspace? | Evidence |
|---|---|---|
| KV (full docs, text chunks, **doc status**, **LLM response cache**, entity/relation KV) | YES — `working_dir/<workspace>/kv_store_*.json` | `kg/json_kv_impl.py:141-151` |
| Graph storage | YES — `working_dir/<workspace>/graph_*.graphml` | `kg/networkx_impl.py:33` |
| Vector storage (chunks/entities/relationships) | YES — under the workspace subdir (`vdb_*.json`) | `kg/faiss_impl.py:217-219` |
| In-memory shared storage | YES — keyed by `get_final_namespace(namespace, workspace)` | `kg/shared_storage.py:208` |

Two distinct non-empty workspaces on the same deployment therefore have **disjoint on-disk files**
(proved programmatically by `cross_cell_storage_isolated()` asserting path-set disjointness,
`cell_isolation08.py:150-160`) **and disjoint in-memory namespaces** → they **cannot** read each
other's KV/graph/vector/doc-status/cache data. The empty workspace maps to the bare `working_dir`
(the shared default). Charset: alnum + `_` (`cell_isolation08.py:45-47`).

### 3b. How workspace is selected — fixed at PROCESS START, not per query

**Critical.** (second-hand, `cell_isolation08.py:21-24`, quoted — abbreviated): *"the HTTP server's
workspace is FIXED at startup (`--workspace` = "Default workspace for all storage",
api/config.py:474-477); the insert endpoints accept no per-request workspace. So per-cell isolation
on ONE running server is NOT possible — a fresh server process (unique WORKSPACE) is required per
cell."*

| Selection mechanism | Supported? |
|---|---|
| Process/startup config (`--workspace` CLI / `WORKSPACE` env) | **YES — the only mechanism** |
| Environment variable at container start | YES (`-e WORKSPACE=<ws>`, `cell_provisioner08.py:559`) |
| HTTP header | NO |
| Query parameter | NO |
| Body parameter (`/query`, `/query/data`, insert) | NO |
| Switchable per-query on a running instance | **NO** |

ON-side confirmation (first-party, exact): the production client `GraphRAGClient.query()` sends
`{query, mode, include_references[, top_k]}` to `/query` with headers `Content-Type` + optional
`X-API-Key` only — **no workspace** (`client.py:132-136`, `538-561`). The eval `/query/data` seam
sends `{query, mode, only_need_context[, top_k]}` — **no workspace** (`gd_seam.py:104-118`,
`158-164`). **No production module sets a workspace anywhere** (grep across
`integrations/graphrag/`); the recorded policy is *"default single workspace is authoritative … do
not invent a workspace"* (`.planning/…graphrag-07…/findings.md:14-15`). The eval per-cell
provisioner sets it only as a **process-start env var** and then verifies the running container's
`WORKSPACE` via `docker inspect`, failing closed on mismatch
(`cell_provisioner08.py:554-561`, `749-750`, `1278-1409`).

**`WORKSPACE_DATA_ISOLATION = STRONG` as a primitive, but currently UNEXERCISED in production**
(everything lands in the single default workspace).

### 3c. Version scope (§31)

Every workspace behavior relied on here is scoped to **v1.5.6**. "Fixed-at-startup workspace, no
per-request selector" is a v1.5.6 fact; a future release exposing a per-request workspace selector
would be **materially new evidence** (and a legitimate reopen trigger under the GraphRAG-08 §15b
criteria). Do not silently generalize.

---

## 4. Architecture topologies for per-notebook isolation (§7 — evaluated, none chosen)

Because workspace is fixed at process start (§3b), "many workspaces" implies "many processes/
containers." Options:

| # | Topology | Isolation | Startup/mem | Concurrency | Cleanup | Op. complexity | Notes |
|---|---|---|---|---|---|---|---|
| A | One process, many workspaces (per-query switch) | — | — | — | — | — | **INFEASIBLE** in v1.5.6 (workspace not per-query, §3b) |
| B | One process/container **per active notebook** | STRONG | High (1 container/notebook) | Good (isolated) | Per-workspace teardown | High | Matches eval `cell_provisioner08` model |
| C | **Pool** of processes keyed by notebook (LRU) | STRONG | Bounded by pool size | Good | Evict + teardown | High (routing/eviction/warm-up) | Bounds memory; adds cold-start on miss |
| D | On-demand **ephemeral** process per notebook query | STRONG | Cold-start/query | Serialized per notebook | Ephemeral | Medium | Storage persists on disk per workspace → process can re-attach to `working_dir/<ws>`; pays provider/warm-up per query |
| E | **Shared** sidecar + **ON-native** notebook scoping (no workspace isolation) | via ON post-filter only | Low (1 sidecar) | Shared | N/A | Low | The GraphRAG-08 status quo; scoping is post-retrieval (leak-prone) unless ON owns it |

Feasible per-notebook-**isolating** topologies are **B/C/D**; A is impossible in v1.5.6; E does not
isolate at the graph layer (relies on ON post-filtering, the GraphRAG-08 boundary). No topology is
chosen in this gate.

---

## 5. Notebook_id → workspace_id mapping (§8 — design only)

A deterministic mapping is safe and required:

```
workspace_id = "nb_" + hex(sha256(canonical_notebook_record_id))[:N]
```

Requirements met: **stable** (keyed on the immutable `notebook:<rand>` record id, not the mutable
display name); **collision-resistant** (SHA-256 prefix); **safe charset** (hex ⊂ alnum+`_`, satisfies
LightRAG's `[^a-zA-Z0-9_]` sanitiser, §3a); **no title leakage** (opaque digest, never the notebook
name); **deletable/rebuildable** (pure function of the record id). Do **not** derive from the
display name (mutable, may leak sensitive titles). No implementation here.

---

## 6. Source-membership lifecycle cases (§9) under per-notebook workspaces

Given many-to-many membership (§2a), each canonical Source may map into **N** workspaces.

| Case | Situation | Per-notebook LightRAG lifecycle requirement |
|---|---|---|
| A | Source in one notebook only | Index its derived graph into that one workspace; delete-then-insert on change |
| B | **Same Source in Notebook A and B** | **Index the same canonical Source into BOTH workspace A and workspace B** (duplication, §10). Provider extraction cost paid per workspace |
| C | Source removed from A, remains in B | Remove derived graph data from **workspace A only**; workspace B untouched. (ON removal is edge-only, `notebooks.py:397-403`) |
| D | Source content changes | **Fan-out reindex** to **every** workspace whose notebook contains it (delete-then-insert per workspace) |
| E | Source deleted globally | Remove derived graph data from **all** member workspaces; ON already writes a durable tombstone + wakes the drain (`notebook.py:642-684`) — the drain would need per-workspace generalization |
| F | Notebook deleted | Destroy/retire that notebook's workspace (storage + process). Shared Sources' other workspaces untouched |
| G | Source moved A→B | = remove-from-A (Case C) + add-to-B (Case A) = delete from workspace A + insert into workspace B |

**Observation:** ON's canonical lifecycle already supports every case at the SurrealDB layer; the
new burden is **fanning the existing single-workspace derived-graph lifecycle (index / reindex /
tombstone / drain / reconcile / rebuild — the GraphRAG-03 series) out across N per-notebook
workspaces**, keyed by `reference` membership. `PER_NOTEBOOK_INDEX_LIFECYCLE_FEASIBLE = PARTIAL`
(mechanically feasible, materially more complex than the current single-workspace design).

---

## 7. Duplicate indexing (§10) — `DUPLICATE_PER_NOTEBOOK_INDEXING_ACCEPTABLE = CONDITIONAL`

A Source shared across K notebooks is indexed K times (Case B/D). Consequences:

| Axis | Effect |
|---|---|
| Storage | Derived KV/graph/vector duplicated ×K per shared Source (canonical Source/embeddings in SurrealDB stay single-copy) |
| Provider/index cost | Entity/relation **extraction LLM calls paid ×K** (extraction is the dominant GraphRAG cost) |
| Deletion | Global delete fans out to K workspaces (Case E) |
| Update | Content change fans out to K workspaces (Case D) |
| Rebuild | Per-workspace rebuild; a heavily-shared Source multiplies rebuild work |
| Consistency | K independent derived copies can transiently diverge (one workspace reindexed, another pending) |

**Verdict `CONDITIONAL`:** acceptable **only** when the shared-source fan-out factor is small and
provider cost is bounded (plausible for a feature-flagged, notebook-opt-in **research** sidecar over
small notebooks). It is **not** acceptable as an always-on, all-notebook default given uncontrolled
sharing. This is a real cost of choosing workspace-per-notebook over ON-native scoping (§32, where a
`reference`-join keeps a **single** derived copy).

---

## 8. Security, authorization & citation ownership (§11–§13)

```
AUTHORIZATION_OWNER            = OPEN_NOTEBOOK   (frozen conceptually)
LIGHTRAG_FINAL_ANSWER_OWNER    = NO
OPEN_NOTEBOOK_FINAL_ANSWER_OWNER = YES
```

- **Order of operations (frozen):** ON validates *user authorization* + *notebook membership/access*
  **first**; only then is the notebook's workspace selected. **Workspace selection is NOT
  authorization** — it is a data-partition primitive with no notion of users, roles, or notebook
  ACLs. (Present ON auth is dev-grade password middleware, per root `AGENTS.md`; PN01 does not
  change that, and any production deployment must define real authz — GraphRAG-08 Boundary B.)
- **Primary security invariant (§12):** *a query authorized for Notebook A must never return
  evidence from Notebook B unless those canonical Sources are also legitimate members of A.* Target:
  `CROSS_NOTEBOOK_GRAPH_EVIDENCE_LEAKAGE = 0`.
- **Can workspace isolation enforce it structurally?** Partly. Workspace isolation gives a **strong
  structural first line** (B cannot be *in* workspace A's stores, §3a) — genuinely stronger than the
  GraphRAG-08 shared-graph post-filter. **But it is defense-in-depth, not a replacement for ON
  validation:** ON still owns canonical Source IDs and citations, so it must **still** validate every
  returned Source ID (exists ∧ member of the current notebook ∧ authorized ∧ citation-eligible)
  before it becomes a citation — because (a) a shared Source legitimately *may* appear, (b) a wrong-
  workspace routing bug must fail safe, and (c) stale derived evidence after a membership change
  must be blocked (§23). `CITATION_OWNERSHIP = OPEN_NOTEBOOK`.

---

## 9. Question-answering options (§14–§16)

### Option 1 — LightRAG owns the answer (`client.query` `/query`)
`Notebook → workspace → client.query() → LightRAG final answer.` **Rejected as preferred.**
LightRAG runs an extra final-answer LLM ON already discards; LightRAG would own prompt/answer
style/citations — conflicting with ON's Ask ownership and the frozen
`LIGHTRAG_ASK_INTEGRATION = NOT_APPROVED`. `LIGHTRAG_CLIENT_QUERY_FINAL_ANSWER_AVAILABLE = YES`
(available, not wanted).

### Option 2 — Evidence-oriented (`/query/data`), ON owns the answer — **preferred seam**
`Notebook → ON authz → workspace → /query/data → structured evidence → ON validation → ON final
answer/citations.` GraphRAG-08 evidenced GQ≡GD identical Source sets and GD ~6.5× faster (skips the
discarded final-answer LLM). So Option 2 is **architecturally preferable** to Option 1 on ownership,
latency, and failure isolation. `LIGHTRAG_QUERY_DATA_EVIDENCE_AVAILABLE = YES`;
`PREFERRED_GRAPH_QUERY_SEAM = /query/data`. **Caveat:** "preferred seam" is about *transport*, not
retrieval value — GraphRAG-08 `GRAPH_RETRIEVAL_VALUE_EVIDENCED = NO` still stands; per-notebook value
is unmeasured (§10).

### Option 3 — Hybrid (ON vector + notebook-isolated `/query/data`) — described only
`Notebook Q → {ON vector retrieval} + {notebook-isolated /query/data} → ON evidence validation →
ON-owned context → ON answer + citations.` **No RRF** (graph output has no valid rank/score;
`RRF_CANDIDATE_INTERFACE_READY = NO` frozen). Any use of graph evidence here must be **explicitly-
designed non-RRF logic** (e.g. intersection/corroboration of already-selected Sources, ON-owned
gating). Possibilities only; nothing approved.

---

## 10. Does per-notebook isolation actually solve the GraphRAG-08 problem? (§17–§20)

**Honest answer: it solves a *different* problem, and only *partially* touches GraphRAG-08's.**

1. **What it genuinely solves (new vs 08):** structural **pre-retrieval notebook-level scoping** by
   workspace ownership — the shared-graph architecture could not do this (08 §2). At the *notebook*
   granularity, `PRE_RETRIEVAL_SCOPE_CONTROL` moves from NO to **YES**. This is a real, correct
   architectural gain and the strongest point in PN01's favour.

2. **What it does NOT solve — it only shrinks the corpus (§17):** GraphRAG-08's intra-graph defects —
   broad candidate surface, `precision 0.065`, `abstention 0.0`, no valid rank/score — are
   properties of LightRAG retrieval *within a workspace*. Per-notebook isolation reduces N (75 → a
   small notebook) but does **not** change these mechanisms. A small notebook graph may still return
   most of its own Sources for any question, still cannot abstain on unanswerable questions (§20),
   and still exposes no rank. `PER_NOTEBOOK_RETRIEVAL_VALUE = UNKNOWN` (a **hypothesis**, not
   evidence — must be measured, never invented).

3. **Negative queries (§20):** notebook isolation does **not** fix answerability. A small
   notebook-local graph can still return evidence for an unanswerable question (08 abstention 0.0).
   ON must still own query gating / evidence-sufficiency / abstention. Isolation ≠ answerability.

4. **Multi-hop (§19):** `MULTIHOP_INCREMENTAL_VALUE_EVIDENCED = INCONCLUSIVE` (frozen). Notebook
   isolation makes a *bounded, notebook-local* multi-hop benchmark **scientifically cleaner** (a
   scoped corpus with controllable cross-source chains), so it *could* justify a **new, different**
   measurement — but establishes no value by itself.

---

## 11. Reliability: rebuild, failure policy, vector-only fallback (§21–§24)

```
PER_NOTEBOOK_LIGHTRAG_WORKSPACE          = DERIVED / REBUILDABLE
LIGHTRAG_REQUIRED_FOR_NORMAL_NOTEBOOK_QA = NO
```

- **Rebuild (§22):** ON/SurrealDB stays canonical; each workspace is a pure function of its
  notebook's `reference` membership and can be destroyed and rebuilt. LightRAG must never own
  canonical notebook state. (ON already has a bounded rebuild dispatcher, GraphRAG-03E.)
- **Failure policy (§23):** keep the GraphRAG-lifecycle principle — indexing may **fail open**
  (notebook Ask continues vector-only if a Source's graph index fails or a workspace is
  unavailable); deletion may **not** disappear silently (durable tombstone). A membership change
  whose per-workspace graph deletion fails must **not** yield a stale citation — **ON post-validation
  (§8) is the backstop** that blocks stale/foreign evidence regardless of derived-store state.
- **Vector-only fallback (§24):** ON must work normally when LightRAG is disabled/unavailable/
  uninitialized/workspace-missing/provider-down → fall back to the existing ON retrieval/Ask path.
  This preserves the GraphRAG-08 maintenance guard (no production dependency, no mandatory sidecar).

---

## 12. Feature-flag & opt-in model (§25–§26)

Existing `OPEN_NOTEBOOK_GRAPHRAG_ENABLED` is a **global** switch. A per-notebook architecture would
*conceptually* need **global enable + per-notebook enable/disable** (question only — no config
added). Given the optional-research status, the conservative default is **notebook-level opt-in**
under a global enable (not automatic all-notebook indexing), which also bounds the duplicate-indexing
cost (§7). No implementation.

---

## 13. Resource model & what needs measuring (§27–§28)

- Storage grows with (#workspaces) and (shared-source fan-out); process-per-notebook (B) is memory-
  heavy; a pool (C) or ephemeral (D) trades memory for cold-start/routing complexity; the single-
  process multi-workspace ideal (A) is **impossible** in v1.5.6.
- **Requires measurement later (not invented here):** per-workspace memory footprint; container
  startup latency vs pool warm-hit rate; cold-start (D) per-query latency; provider extraction cost
  under realistic sharing; small-notebook retrieval quality (§10).
- **Reusable 08E learning (§28):** the eval `cell_isolation08`/`cell_provisioner08` **mechanisms**
  (unique WORKSPACE per process, path-disjointness proof, `docker inspect` workspace attestation,
  fail-closed owned-only teardown) are conceptually reusable as the *isolation pattern*. They are
  **eval-only** and must **not** be imported into production (`PRODUCTION_IMPORTS_EVAL = NO`, §14);
  a production per-notebook manager would be a **new production-facing** component. Distinguish
  diagnostic **fresh-cell** isolation from **long-lived per-notebook** workspaces (lifecycle,
  reuse, and rebuild differ).

---

## 14. Boundaries retained (§29–§30, §42)

```
PRODUCTION_IMPORTS_EVAL            = NO   (no cell_provisioner08/burst_runner08/benchmark/gd_seam into prod)
BOUNDARY_B                        = NOT_APPROVED for real internal Agribank data
GRAPHRAG_PRODUCTION_INTEGRATION   = NOT_APPROVED
LIGHTRAG_ASK_INTEGRATION          = NOT_APPROVED
STRUCTURED_EVIDENCE_IMPLEMENTATION_READY = NO
RRF_CANDIDATE_INTERFACE_READY     = NO
GRAPH_CANDIDATE_IMPLEMENTATION_READY = NO
```

Any future live PN02 evaluation uses **synthetic / public / approved-anonymized** data only.

---

## 15. Mandatory simpler-alternative comparison (§32) — `LIGHTRAG_PER_NOTEBOOK_UNIQUE_VALUE = NOT_PROVEN`

Alternative: **ON-native notebook-local logic without per-notebook LightRAG.** Because membership is
already a graph edge (`reference`), notebook-scoped **retrieval** is trivially ON-native:

- **Scoped vector retrieval:** add a `reference`-membership join/filter to `fn::vector_search`
  (`migrations/1.surrealql:139-173`) so Ask retrieves only the current notebook's Sources — a small,
  single-copy change (no duplicate indexing, no per-notebook process). ON already scopes Chat by
  notebook via full-context injection (§2b).
- **Relationship/entity linkage:** ON could store source-linkage/entities natively.

LightRAG's *unique* capability over this is **entity-relationship extraction + multi-hop
traversal**. But GraphRAG-08 found that capability's discriminative value **INCONCLUSIVE** and its
retrieval value **NO**. So per-notebook LightRAG's unique value **over the simpler ON path is
NOT_PROVEN** — the *scoping* benefit (the headline of the use case) is reproducible more simply
inside ON; only the *unmeasured* multi-hop benefit is LightRAG-specific.
`LIGHTRAG_PER_NOTEBOOK_UNIQUE_VALUE = NOT_PROVEN` (not NO — the multi-hop question is genuinely
open; not PLAUSIBLE-with-enthusiasm — no evidence supports it yet).

---

## 16. Architecture diagrams (§34)

**A. Current shared-graph limitation (GraphRAG-08)**
```
Notebook A ┐
Notebook B ┼─► ONE shared LightRAG default workspace ─► broad ~20/75 retrieval ─► ON POST-FILTER only
Notebook C ┘        (precision 0.065, abstention 0.0, no rank; leak-prone without ON filter)
```

**B. Per-notebook isolated graph (PN01 proposal)**
```
Notebook A (sources via `reference`) ─► workspace A (own KV/graph/vector, own process)
Notebook B (sources via `reference`) ─► workspace B (own process)
   shared Source S ∈ {A,B} ─► indexed into BOTH A and B (duplication, §7)
   Ask in A ─► workspace A ONLY (hard partition, §3a)
```

**C. Preferred QA candidate (evidence-oriented, ON-owned)**
```
Notebook Q
  ─► ON authorization + notebook membership check        (authz FIRST, §8)
  ─► ON primary vector retrieval (notebook-scoped)
  ─► [optional] notebook-isolated LightRAG /query/data    (structured evidence, no LLM answer)
  ─► ON evidence validation (exists ∧ member ∧ authorized ∧ citation-eligible)   ← backstop
  ─► ON-owned context construction (NO RRF)
  ─► ON final answer + citations
  (LightRAG unavailable ⇒ vector-only fallback, §24)
```

---

## 17. Decision matrix (§35)

Columns: **SG**=shared global LightRAG graph · **SP**=single-process multi-workspace ·
**PP**=process-per-notebook · **VO**=vector-only ON · **ON+G**=ON + optional notebook-local graph.

| Criterion | SG | SP | PP | VO | ON+G |
|---|---|---|---|---|---|
| Source isolation | Post-filter only | **N/A (infeasible v1.5.6)** | **Strong (workspace)** | ON-scoped (native) | Strong (workspace) + ON |
| Authorization safety | ON post-validate | — | ON validate + partition | ON-owned | ON-owned + partition |
| Cross-notebook leakage risk | Medium (filter-dependent) | — | **Low (structural)** | Low (native scope) | **Lowest (both)** |
| Retrieval-quality evidence | NO (08) | — | UNKNOWN (small-N) | vector baseline (real) | UNKNOWN incremental |
| Latency | +graph | — | +graph +process routing | Low | +optional graph |
| Storage | 1 shared | — | ×workspaces + dup | Low | ×workspaces + dup |
| Provider cost | 1× index | — | ×fan-out extraction | none extra | ×fan-out (opt-in) |
| Lifecycle complexity | Low | — | **High** (fan-out) | Low | High (opt-in bounded) |
| Fallback behavior | vector-only | — | vector-only | native | vector-only |
| Citation ownership | ON | — | ON | ON | ON |
| Production complexity | Low | — | **High** | Low | Medium-High |

**Reading:** PP/ON+G are the only options that *structurally* isolate; both are UNKNOWN on
incremental value and HIGH on lifecycle/cost. VO (or ON-native scoping) is the cheapest way to get
notebook-scoped QA and is the honest simpler baseline any PN02 must beat.

---

## 18. Primary feasibility answers (§36)

```
PER_NOTEBOOK_WORKSPACE_TECHNICALLY_FEASIBLE     = YES        (workspace = strong partition; process-per-workspace proven in eval)
PER_NOTEBOOK_QUERY_ROUTING_FEASIBLE             = PARTIAL    (routing = per-notebook PROCESS/CONTAINER selection; NO per-query workspace param on one instance)
PER_NOTEBOOK_INDEX_LIFECYCLE_FEASIBLE           = PARTIAL    (mechanically yes; shared-source fan-out + per-workspace lifecycle generalization add real complexity)
CROSS_NOTEBOOK_GRAPH_ISOLATION_FEASIBLE         = YES        (structural hard partition; still needs ON post-validation as backstop)
OPEN_NOTEBOOK_FINAL_ANSWER_OWNERSHIP_FEASIBLE   = YES        (/query/data returns evidence only; ON owns the answer)
LIGHTRAG_CLIENT_QUERY_FINAL_ANSWER_AVAILABLE    = YES        (available; not preferred)
LIGHTRAG_QUERY_DATA_EVIDENCE_AVAILABLE          = YES
```

---

## 19. Value question (§37) — separate from feasibility

```
PER_NOTEBOOK_LIGHTRAG_VALUE_EVIDENCED = NOT_YET
```
Technical isolation success ≠ QA/retrieval value. No per-notebook value has been measured; the
small-scoped-notebook retrieval question is a **new hypothesis** (`PER_NOTEBOOK_RETRIEVAL_VALUE =
UNKNOWN`), not evidence.

---

## 20. PN01 decision (§38)

```
PN01_DECISION      = C
PN01_DECISION_NAME = PER_NOTEBOOK_ARCHITECTURE_FEASIBLE_AND_BOUNDED_EVALUATION_JUSTIFIED
```

**Rationale.** Not **A** (isolation is technically feasible, §18) and not **D** (vendor/repo evidence
is sufficient to characterize feasibility). The choice is **B vs C**:

- PN01 identifies a **real, new** architectural distinction GraphRAG-08 never tested: workspace
  *ownership* gives structural **pre-retrieval notebook-level** scoping, whereas 08 only proved
  per-query Source allowlisting inside a *shared* graph is impossible. **08's negative scoping
  finding does not transfer to the notebook granularity** — so 08 does **not** settle this question.
- The operator has supplied a **concrete product use case** (notebook-scoped QA), which is exactly
  the GraphRAG-08 §15b reopen criterion "a concrete product need"; and PN02's question (value in
  **small, scoped** corpora, `CROSS_NOTEBOOK_LEAKAGE = 0`) is **scientifically new** vs 08's
  75-source global corpus (§40).
- Therefore a **bounded, synthetic, offline-designed** evaluation is justified to move
  `PER_NOTEBOOK_RETRIEVAL_VALUE` from UNKNOWN to measured **and** to structurally verify the leakage
  invariant — *before* any production consideration.

**C is deliberately narrow, and B remains co-defensible** (as A/STOP remained co-defensible in
GraphRAG-08): B ("feasible but not worth evaluating") is defensible because notebook *scoping* is
reproducible ON-native more simply (§15) and 08's intra-graph precision/abstention/rank defects are
untouched by isolation (§10). C wins **only** as authorization for a *research* evaluation, not
production — and it can stop at any gate. C does **not** approve production integration, Ask
integration, an adapter, RRF, or GraphRAG-09.

---

## 21. If C — PN02 design (§39–§41, design only, NOT implemented)

```
PN02_EVALUATION_JUSTIFIED = YES  (as DESIGN ONLY; separate operator authorization required to build/run)
PN02_NAME                 = GraphRAG-PN02 — Synthetic Per-Notebook Isolation & QA Evaluation
PN02_REQUIRES_NEW_CODE    = YES  (production-facing per-notebook interface + eval harness; must NOT import 08 eval-only modules)
```

**PN02 primary hypothesis (falsifiable):** *In multiple small, synthetic, per-notebook-isolated
workspaces, LightRAG (a) returns ZERO cross-notebook evidence, and (b) yields incremental
required-source / multi-hop recovery over a notebook-scoped vector baseline with acceptable precision
and non-zero negative abstention.* PN02 fails if leakage > 0, or if graph evidence gives no
incremental required-source recovery over the notebook-scoped vector baseline, or if it merely
reproduces 08's broad-surface / zero-abstention behavior at smaller N.

**PN02 must NOT (§40):** rerun LightRAG over the same 75-source global corpus. Its corpus is
**multiple small notebooks** with **overlapping and non-overlapping** Sources.

**PN02 required metrics (§41):** `CROSS_NOTEBOOK_LEAKAGE_RATE` (primary; target 0) ·
`SOURCE_PRECISION` · `SOURCE_RECALL` · `NEGATIVE_ABSTENTION` · `REQUIRED_SOURCE_RECOVERY` ·
`INCREMENTAL_GRAPH_FALSE_POSITIVES` · `MULTIHOP_REQUIRED_SOURCE_RECOVERY` · `LATENCY` ·
`WORKSPACE_STARTUP/REUSE cost` · `CITATION_VALIDITY`. **No fake graph rank/score metrics** (no
MRR/nDCG on the unordered graph set; ranked metrics stay on the vector baseline).

**PN02 scope guards:** synthetic/public data only (Boundary B); the notebook-scoped **vector
baseline** is the honest system to beat (§15); final answers generated by an **ON-side** test
harness (not LightRAG); a **new production-facing** per-notebook interface (no eval-only imports).
**Not implemented in this gate.**

---

## 22. Independent review (§45)

An independent reviewer challenged the twelve §45 questions (A–L). Summary of resolutions:

- **A (does a workspace actually isolate retrieval data?)** YES — hard partition, separate
  files + in-memory namespace (§3a); flagged second-hand for LightRAG internals.
- **B (can queries reliably target the right workspace?)** PARTIAL — only via per-notebook
  **process/container** selection; **no** per-query workspace param on one instance (§3b). Recorded
  honestly, not overstated.
- **C (confusing isolation with authorization?)** Guarded — §8 freezes workspace-selection ≠
  authorization; ON validates authz first and post-validates every returned Source.
- **D (shared Sources?)** Central — many-to-many is proven (§2a); duplication consequences
  enumerated (§6–§7); `DUPLICATE = CONDITIONAL`, not hand-waved YES.
- **E (per-notebook duplication excessive?)** Addressed — `CONDITIONAL`, opt-in default, and the
  single-copy ON-native alternative flagged (§7, §15).
- **F (works if LightRAG unavailable?)** YES — vector-only fallback frozen (§11/§24).
- **G (client.query answer vs ON Ask ownership?)** Resolved — Option 1 rejected;
  `LIGHTRAG_FINAL_ANSWER_OWNER = NO` (§9).
- **H (/query/data preferable?)** YES — preferred seam (§9), consistent with 08.
- **I (does isolation address the 08 broad-corpus problem or just reduce corpus size?)** The
  sharpest challenge — §10 concedes it **only reduces N** and does **not** fix intra-graph
  precision/abstention/rank; value UNKNOWN. This is why C is narrow and B co-defensible.
- **J (could ON-native be simpler?)** YES for scoping (§15) — `UNIQUE_VALUE = NOT_PROVEN`.
- **K (is PN02 scientifically new & falsifiable?)** YES — small scoped corpora + leakage=0, a
  different question from 08 (§21/§40).
- **L (reviving GraphRAG without a use case?)** The operator supplied a concrete use case
  (notebook-scoped QA); evaluated objectively — feasible, but value NOT_YET and unique-value
  NOT_PROVEN, hence a *research* C, not production.

No HIGH finding survives that would flip the decision; the dominant MEDIUM (question I — isolation ≠
retrieval-quality fix) is incorporated directly into the narrow-C framing and the co-defensible B.

---

## 23. Final report (§46)

```
GRAPH_RAG_PN01_PER_NOTEBOOK_ISOLATION_QA_FORENSIC = COMPLETE
AUTHORITATIVE_BASELINE                            = 3068dbdef081acb16fd92e9fea10704511da84bd
GRAPHRAG_08_STATUS                                = CLOSED / APPROVED
LIGHTRAG_VERSION                                  = v1.5.6

PER_NOTEBOOK_WORKSPACE_TECHNICALLY_FEASIBLE       = YES
PER_NOTEBOOK_QUERY_ROUTING_FEASIBLE               = PARTIAL
PER_NOTEBOOK_INDEX_LIFECYCLE_FEASIBLE             = PARTIAL
CROSS_NOTEBOOK_GRAPH_ISOLATION_FEASIBLE           = YES
WORKSPACE_DATA_ISOLATION                          = STRONG (mechanism; UNEXERCISED in production)
SOURCE_SHARED_ACROSS_NOTEBOOKS_SUPPORTED_BY_ON    = YES
DUPLICATE_PER_NOTEBOOK_INDEXING_ACCEPTABLE        = CONDITIONAL
LIGHTRAG_CLIENT_QUERY_FINAL_ANSWER_AVAILABLE      = YES
LIGHTRAG_QUERY_DATA_EVIDENCE_AVAILABLE            = YES
PREFERRED_FINAL_ANSWER_OWNER                      = OPEN_NOTEBOOK
PREFERRED_GRAPH_QUERY_SEAM                        = /query/data
LIGHTRAG_REQUIRED_FOR_NORMAL_NOTEBOOK_QA          = NO
PER_NOTEBOOK_LIGHTRAG_VALUE_EVIDENCED             = NOT_YET
LIGHTRAG_PER_NOTEBOOK_UNIQUE_VALUE                = NOT_PROVEN
GRAPHRAG_PRODUCTION_INTEGRATION                   = NOT_APPROVED
LIGHTRAG_ASK_INTEGRATION                          = NOT_APPROVED

PN01_DECISION                                     = C
PN01_DECISION_NAME                                = PER_NOTEBOOK_ARCHITECTURE_FEASIBLE_AND_BOUNDED_EVALUATION_JUSTIFIED

PN02_EVALUATION_JUSTIFIED                         = YES (design only; separate authorization required)
PN02_NAME                                         = GraphRAG-PN02 — Synthetic Per-Notebook Isolation & QA Evaluation
PN02_PRIMARY_HYPOTHESIS                           = In small, per-notebook-isolated synthetic workspaces, LightRAG returns
                                                    zero cross-notebook evidence AND gives incremental required-source /
                                                    multi-hop recovery over a notebook-scoped vector baseline with
                                                    acceptable precision and non-zero negative abstention (falsifiable).
PN02_REQUIRED_METRICS                             = CROSS_NOTEBOOK_LEAKAGE_RATE (primary=0), SOURCE_PRECISION, SOURCE_RECALL,
                                                    NEGATIVE_ABSTENTION, REQUIRED_SOURCE_RECOVERY, INCREMENTAL_GRAPH_FALSE_POSITIVES,
                                                    MULTIHOP_REQUIRED_SOURCE_RECOVERY, LATENCY, WORKSPACE_STARTUP/REUSE, CITATION_VALIDITY
PN02_REQUIRES_NEW_CODE                            = YES

GRAPH_RAG_09_JUSTIFIED                            = NO
```

**changed files:** this document +
[`CURRENT_PHASE.md`](CURRENT_PHASE.md) (added a separate PN01 research-track row; GraphRAG-08 left
CLOSED/APPROVED). No code, no tests, no fixtures, no migration, no `.env`, zero provider traffic,
sidecar not started, `GRAPHRAG_ENABLED` untouched. **NOT checkpointed** (operator review required).

---

## 24. STOP

```
GRAPH_RAG_PN01_PER_NOTEBOOK_ISOLATION_QA_FORENSIC_COMPLETE
```
No code · no provider traffic · no live LightRAG · no DB mutation · no Ask integration · no
production adapter · no PN02 implementation · no GraphRAG-09 · no checkpoint.
