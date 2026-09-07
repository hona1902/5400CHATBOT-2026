# GraphRAG-08 — Value-Evidence Architecture Review Gate

**Status:** OFFLINE ARCHITECTURE / FORENSIC / DECISION REVIEW. **No code, no provider traffic, no
live LightRAG, no benchmark rerun, no RRF, no adapter, no Ask integration, no attempt #7, no
GraphRAG-09.** Docs only; fixture unchanged. Decides whether the evidenced `/query/data` runtime
seam justifies any bounded future production-oriented GraphRAG path, given the benchmark showed
`GRAPH_RETRIEVAL_VALUE_EVIDENCED = NO` but `STRUCTURED_QUERY_DATA_RUNTIME_VALUE_EVIDENCED = YES`.

Checkpoint: HEAD `11ac8c9a679396548e24bdbfb604b50714392efb`, tag
`graphrag-08-value-evidence-approved`, tree CLEAN, fixture
`a58a68535c345e18f0263904f818e4e2068a164056408665d8bb9233eceb143d` (unchanged).

## 1. Starting evidence (frozen — GraphRAG-08 Attempt #6)

```
75/75 indexed first-attempt (0 retries/failures)   VALUE_EVIDENCE_READY = YES
GRAPH_RETRIEVAL_VALUE_EVIDENCED = NO   STRUCTURED_QUERY_DATA_RUNTIME_VALUE_EVIDENCED = YES
GQ_GD_SOURCE_SET_PARITY = IDENTICAL   QUERY_DATA_EXPOSES_VALID_RANK = NO / SCORE = NO
RRF_CANDIDATE_INTERFACE_READY = NO   GRAPH_CANDIDATE_IMPLEMENTATION_READY = NO
STRUCTURED_EVIDENCE_IMPLEMENTATION_READY = NO   ROOT_CAUSE_CONFIRMED = NO   08F = NOT_JUSTIFIED
```
HOLDOUT: V Hit@1 0.000 / @5 0.667 / @10 0.958, Recall@10 0.875, MRR 0.267, FullSet@10 0.750.
GQ/GD (identical): set_recall 0.972, **set_precision 0.065**, F1 0.120, candidate_fraction 0.267
(20/75), **~18.42 false positives/query**, full_source_set_recovered 0.917, **negative abstention
0.0**, provenance clean (0 foreign/malformed/off-benchmark). Latency p50: GD 968ms · V 1109ms · GQ
6344ms.

`HIGH_RECALL_ALONE_IS_NOT_VALUE = YES` — broad coverage is not evidence of useful retrieval.

## 2. The linchpin forensic — pre- vs post-retrieval scope control

The committed `/query/data` request body is `{query, mode, only_need_context, top_k}`
(gd_seam.py:158–164) — **no per-query Source-allowlist / ids / scope parameter**. The run's
`benchmark_ids` allowlist is applied **after** the payload returns (`_normalize(strong_file_paths,
allowlist=benchmark_ids)`, gd_seam.py:188; off-run ids counted `off_benchmark` and dropped,
:145–151). LightRAG v1.5.6 retrieves over the **entire indexed workspace** graph/vector stores
(consistent with the GraphRAG-05/06 forensic of `kg_query` / `_build_query_context`).

```
PRE_RETRIEVAL_SOURCE_SCOPE_CONTROL = NO
POST_RETRIEVAL_FILTER_ONLY         = YES
```

**Consequence:** graph breadth, internal retrieval precision, and provider work **cannot be
constrained at the source**. A vector-first primary retriever cannot narrow *what LightRAG
searches* — it can only discard results afterward, which does not reduce noise generation,
internal precision, or provider egress. This directly undermines any "vector-first → constrained
graph expansion" architecture. (The body does expose `top_k`, a breadth **count** lever — untested
here, and the benchmark surface returned ~20 — but `top_k` controls *how many* items return, not
*which Sources* are searched; it is not source-scoping and does not authorize/constrain by Source.)

## 3. Role-by-role capability assessment (§5)

| Role | Classification | Evidence |
|---|---|---|
| A. Primary retriever | **NOT_SUPPORTED** | precision 0.065, broad fixed 20/75 surface, no ranking |
| B. Vector replacement | **NOT_SUPPORTED** | worse precision, no rank; V is a genuine ranked retriever |
| C. RRF candidate generator | **NOT_SUPPORTED** | no valid graph rank/score (§7); unordered broad set |
| D. Answerability detector | **NOT_SUPPORTED** | negative abstention 0.0 — returns 20 candidates for all 12 unanswerable |
| E. Secondary evidence **expander** (adds new Sources) | **NOT_SUPPORTED** | pre-retrieval scoping impossible; broad surface ⇒ noise/FP; §18 |
| F. Provenance corroborator (already-selected Sources) | **CONDITIONALLY_PLAUSIBLE** | clean provenance + GraphRAG-07 contract; but value **UNPROVEN** (never measured) |
| G. Relationship-context provider | **CONDITIONALLY_PLAUSIBLE** | entities/relationships present; value **UNPROVEN**; provenance PARTIAL for entity/relation |
| H. Eval-only / research-only | **SUPPORTED** | where all current evidence lands |

Roles A–D are decisively NOT_SUPPORTED. F/G survive only as *unproven* research possibilities and
only in a **corroboration** (no-expansion) framing.

## 4. RRF review (§7)

The graph interface provides no valid rank and no defensible score; order/entity-order/
relation-order/`supporting_chunk_count`/provenance-count/appearance-frequency are **not**
validated relevance signals (GraphRAG-05). `CAN_CURRENT_GRAPH_OUTPUT_BE_USED_AS_A_DEFENSIBLE_RRF_
INPUT = NO`; `DIRECT_RRF_JUSTIFIED = NO`. Feeding all 20 GD Sources into RRF would not solve the
candidate-quality problem (no rank + broad surface). **Do not implement RRF.**

## 5. `/query/data` value — retrieval quality vs evidence transport (§8)

- **Retrieval quality:** NO positive evidence (precision 0.065; identical to GQ).
- **Structured evidence transport (evidenced):** GD returns machine-consumable, Source-ID +
  provenance-bearing evidence; forces `only_need_context=True` (no discarded final-answer LLM);
  ~6.5× lower latency than GQ; cleaner failure isolation (GraphRAG-06: `/query` maps a generation
  exception to HTTP 500 and discards computed evidence — `/query/data` removes that surface).
  These are the *only* evidenced benefits; GD is **not** better at selecting Sources than GQ.

## 6. Negative-query problem (§13)

`CAN CURRENT GRAPH OUTPUT SELF-DECIDE WHETHER EXPANSION SHOULD RUN? = NO` — with negative
abstention 0.0, the graph cannot gate itself. Any retained secondary path must have an **Open
Notebook-side** trigger/gate; graph broadness must never own the expansion decision.
`NEGATIVE_QUERY_SELF_ABSTENTION = NOT_SUPPORTED`.

## 7. Class-level & complementarity (§15/§16/§33)

Every query class (direct_lexical, semantic_paraphrase, two_hop, three_hop_cross_source,
entity/relationship/distractor collisions, partial_evidence, broad_entity_name_collision,
negative_unanswerable) returned a uniform `candidate_count ≈ 20` — **no class exhibits
discriminative graph usefulness distinct from broad-corpus coverage**. `MULTIHOP_INCREMENTAL_
VALUE_EVIDENCED = INCONCLUSIVE` (aggregate recall does not isolate discriminative multi-hop
value, and the uniform 20-candidate surface argues against it). Complementarity:
`COMPLEMENTARITY_EXISTS = YES`; `COMPLEMENTARITY_IS_DISCRIMINATIVE = INCONCLUSIVE`;
`COMPLEMENTARITY_JUSTIFIES_PRODUCTION_EXPANSION = NO` (graph_only recovery is the mechanical
consequence of returning ¼ of the corpus; oracle union is an **upper bound only, NOT** an
RRF/fusion validation).

## 8. Ownership, boundaries, contract

- **Authorization/citation (§11/§25):** `AUTHORIZATION_OWNER = OPEN_NOTEBOOK`. LightRAG must never
  own Source authorization, notebook membership, canonical Source IDs, or citation validity. Since
  scope control is post-retrieval only, ON authorization is enforced by dropping unauthorized
  Sources after the fact — acceptable for *authorization* (correctness preserved) but it does not
  reduce graph work or noise.
- **Boundary B (§12):** LightRAG→provider egress remains **NOT approved for real internal Agribank
  data**; all evidence is synthetic/public. No deployment readiness is claimed.
- **GraphRAG-07 contract (§34):** `IS_GRAPHRAG_07_CONTRACT_STILL_ARCHITECTURALLY_VALID = YES` —
  the Source-ID + provenance, unordered (no rank), no-raw-chunk-text contract is sound. **Contract
  validity ≠ retrieval value:** a good interface can wrap a low-value retriever. `/query/data`
  remains the preferred structured seam over `client.query()` **only** for structure/latency/
  failure-isolation, not for better Sources (§35).
- **Production import boundary (§26):** `PRODUCTION_IMPORTS_EVAL = NO` — a future production
  adapter (if ever approved) needs a new production-facing interface, not an eval import.
- **Version/provider scope (§36/§37):** `CURRENT_DECISION_SCOPE = LightRAG v1.5.6 + OpenRouter +
  gpt-4o-mini + text-embedding-3-small (frozen)`. A materially different version/provider requires
  a new evidence gate — but "a different model might work" is not a licence for open-ended
  experimentation.

## 9. Mandatory simpler-alternative comparison (§52)

Alternative: vector retrieval + Open-Notebook-owned metadata/relationship/Source-linkage logic,
**without** LightRAG. `DOES LIGHTRAG PROVIDE A UNIQUE EVIDENCED CAPABILITY JUSTIFYING ITS ADDED
COMPLEXITY? = NOT_PROVEN.` LightRAG's entity/relationship graph is a *unique capability*, but the
benchmark shows it adds **no evidenced discriminative value**, and its provenance/relationship
utility for already-selected Sources was never measured. The simpler no-LightRAG path is not
ruled out.

## 10. Decision matrix

| Capability | Benchmark evidence | Current capability | Production suitability | Decision |
|---|---|---|---|---|
| Primary retrieval | precision 0.065 | broad unranked | unsuitable | REJECT |
| Vector replacement | worse than V | no rank | unsuitable | REJECT |
| RRF candidate generation | no rank/score | none | unsuitable | REJECT |
| Negative-query detection | abstention 0.0 | none | unsuitable | REJECT |
| Structured evidence transport | GD runtime YES | present | interface OK; value NOT_PROVEN | RESEARCH ONLY |
| Provenance corroboration | not measured | plausible | NOT_PROVEN | RESEARCH ONLY |
| Relationship context | not measured | partial | NOT_PROVEN | RESEARCH ONLY |
| Secondary Source expansion | breadth/FP high; scoping=NO | broad only | unsuitable | REJECT |
| Final-answer generation | out of scope | ON-owned | ON keeps it | ON OWNS |
| Authorization | ON-owned | ON-owned | ON keeps it | ON OWNS |

## 11. Risk register (§42)

| Risk | Level | Note |
|---|---|---|
| Broad candidate noise (18.5 FP/q) | HIGH | inherent to workspace-global retrieval; not fixable post-hoc |
| Negative-query over-expansion (abstention 0) | HIGH | ON must own the gate |
| No graph rank/score | HIGH | blocks RRF/fusion |
| Provider egress (Boundary B) | HIGH | not approved for internal data |
| Pre-retrieval scoping impossible | MEDIUM/HIGH | can't constrain LightRAG search; only post-filter |
| Latency/cost addition | MEDIUM | a secondary graph call adds work; GD<GQ but graph precision poor |
| State/cache/workspace complexity | MEDIUM | long-lived sidecar, one workspace |
| Vendor-version coupling | MEDIUM | evidence scoped to v1.5.6 |
| Citation/provenance ownership | LOW (if ON-owned) | contract preserves ON ownership |
| Maintenance burden vs demonstrated value | HIGH | value NOT_PROVEN |

## 12. Benefit register (§43)

| Benefit | Status |
|---|---|
| GD lower latency than GQ (~6.5×) | **EVIDENCED** |
| Structured, machine-readable, Source-ID+provenance evidence | **EVIDENCED** |
| Clean provenance in benchmark (0 foreign/malformed/off-benchmark) | **PROPERTY** (a benefit only once the evidence is actually consumed) |
| Failure isolation (no final-answer LLM in the evidence path) | **EVIDENCED** (design/06) |
| Relationship/context corroboration for already-selected Sources | **PLAUSIBLE** (unmeasured) |
| Incremental required-Source recovery over vector | **UNPROVEN** (coverage artifact) |

## 13. Product-value question (§44)

`WOULD INTEGRATING CURRENT GRAPHRAG EVIDENCE INTO ASK TODAY IMPROVE ANSWER QUALITY ENOUGH TO
JUSTIFY ADDED COMPLEXITY? = NOT_PROVEN` → for **today**, **NO**. `ASK_INTEGRATION_JUSTIFIED_TODAY
= NO`.

## 14. Architecture diagrams (conceptual)

**Unsupported path** (do not build): `Query → Vector → LightRAG broad-20 → merge ALL → Ask`.
Unsupported because it injects ~18.5 false positives/query, cannot abstain on negatives, cannot
scope LightRAG pre-retrieval, and has no rank to prioritize.

**Only conditionally-plausible future research path (NOT implemented, NOT production):**
`Query → ON gating → ON primary (vector) retrieval → [optional] GD structured evidence, INTERSECTED
with already-selected Sources for relationship/provenance corroboration only → ON validation/filter
→ ON-owned final context/answer`. Graph adds **no new Sources** here; it only annotates Sources ON
already trusts. Ownership boundaries (authorization, citation, final answer) stay with ON.

## 15. Final architecture decision

```
FINAL_OPERATOR_ARCHITECTURE_DECISION = B — KEEP_GD_AS_PROVENANCE_ONLY_RESEARCH_PATH
ARCHITECTURE_DECISION                = B
ARCHITECTURE_DECISION_NAME           = KEEP_GD_AS_PROVENANCE_ONLY_RESEARCH_PATH
GRAPHRAG_PRODUCTION_INTEGRATION      = NOT_APPROVED
GRAPHRAG_RESEARCH_PATH               = RETAINED_OPTIONAL
```

**What B means (the current approved boundary).** LightRAG remains integrated in the Open Notebook
repository as an **OPTIONAL, feature-flagged GraphRAG research/evidence sidecar**. The existing
lifecycle/sidecar integration, indexing machinery, `/query/data` seam, provenance normalization,
evaluation harnesses, Option-A isolation, security boundaries, and benchmark tooling remain valid
**research** infrastructure (`LIGHTRAG_PRESENT_IN_REPOSITORY = YES`, `LIGHTRAG_OPTIONAL_SIDECAR_
SUPPORTED = YES`, `LIGHTRAG_RESEARCH_PATH = OPEN`). B does **NOT** approve: LightRAG as primary
retriever, a vector replacement, direct merge into Ask, an RRF input, unrestricted Source
expansion, answerability decisions, a production Structured Evidence Adapter, or GraphRAG-09.

`KEEP_GD_AS_PROVENANCE_ONLY_RESEARCH_PATH` describes the **current** approved research boundary —
provenance/relationship corroboration for Sources **already selected/authorized by Open Notebook**
(`MECHANICALLY_FEASIBLE = PARTIAL`, `PRODUCT_VALUE = UNPROVEN`). It does **not** permanently
prohibit future graph-expansion research; future expansion would require new evidence that
breadth/noise and authorization constraints can be controlled (§15b). `UNCONSTRAINED_SOURCE_
EXPANSION_JUSTIFIED = NO` — current `/query/data` has no pre-retrieval Source allowlist (§2),
returns broad candidate sets, has no valid rank/score, and had zero negative self-abstention in
the frozen benchmark; Open Notebook post-filtering alone does not solve the internal retrieval
breadth problem.

**Why B instead of A.** A — `STOP_GRAPHRAG_PRODUCTION_PATH` — remains technically defensible on the
retrieval-value evidence alone. B is selected because the operator has an explicit project
objective to retain LightRAG in Open Notebook as **optional research infrastructure**, and the
benchmark did evidence a real `/query/data` structured-runtime advantage plus clean provenance
mechanics — **but that benefit is insufficient for production adoption today** (`ASK_INTEGRATION_
JUSTIFIED_TODAY = NO`; `LIGHTRAG_UNIQUE_VALUE_OVER_SIMPLER_ON_ARCHITECTURE = NOT_PROVEN`). B
therefore preserves a narrow, falsifiable research path **without pretending retrieval value was
demonstrated**, and preserves the ability to stop later. (C — bounded expansion — rejected:
expansion unjustified + pre-retrieval scoping impossible, §2. D — inconclusive — rejected: the
evidence suffices.)

### 15a. Maintenance guard (the HIGH risk, §11)

B must NOT create active production maintenance obligations. Frozen: **no** production dependency
on LightRAG; **no** eval-module import into application runtime (`PRODUCTION_IMPORTS_EVAL = NO`);
**no** GraphRAG-required deployment; **no** GraphRAG-required Ask path; **no** mandatory sidecar
for normal Open Notebook operation. LightRAG stays **optional / feature-flagged**; the primary
production retrieval path is the Open Notebook vector path (`Query → ON retrieval → authorized
canonical Sources → Ask`), with LightRAG out of it for now. Do not convert research harnesses into
application runtime paths.

### 15b. Reopen / advance criteria (beyond provenance-only)

A new architecture/evaluation gate may be **proposed** if at least one holds: (1) a materially
newer LightRAG release provides defensible per-query **pre-retrieval Source scoping**; (2) a valid
graph **relevance rank/score** becomes available; (3) a bounded provenance/relationship experiment
demonstrates **incremental useful evidence** for already-authorized Sources; (4) a concrete Open
Notebook **product requirement** emerges where relationship-graph context has plausible unique
value over simpler ON-native logic; (5) a **controlled** secondary-evidence design can be made
falsifiable **without unconstrained Source expansion**. These permit a *proposal*; they do **NOT**
automatically authorize GraphRAG-09.

### 15c. `/query/data` status

`/query/data` remains the preferred LightRAG evidence-level seam over `client.query()` for any
future research (structured, same Source set as GQ, ~6.5× lower latency, no discarded final-answer
generation, cleaner evidence/answer separation). `STRUCTURED_QUERY_DATA_RUNTIME_VALUE_EVIDENCED =
YES` does **NOT** imply `GRAPH_RETRIEVAL_VALUE_EVIDENCED = YES`. Retained as a research/eval finding
only — **not** a production adoption decision.

### Stopping criteria (§38)
If a future provenance-corroboration evaluation fails to show incremental required-evidence value
with acceptable noise/negative behavior, or if LightRAG cannot be constrained/validated safely,
**STOP** the GraphRAG path. No open-ended research loop; no production adapter, RRF, or GraphRAG-09
without a new, separately-approved evidence gate.

### What NOT to do next (§39, reaffirmed)
No direct RRF of V+GD; no feeding all 20 GD Sources into Ask; no using graph order/`supporting_
chunk_count` as rank/score; no always-on graph; no LightRAG-authored answers; no graph-as-
authorization; no adapter before architecture evidence; no GraphRAG-09 merely because 08 completed.

## 16. Retained flags (unchanged by this review)

```
VALUE_EVIDENCE_READY = YES   GRAPH_RETRIEVAL_VALUE_EVIDENCED = NO
STRUCTURED_QUERY_DATA_RUNTIME_VALUE_EVIDENCED = YES
QUERY_DATA_EXPOSES_VALID_RANK = NO   QUERY_DATA_EXPOSES_VALID_SCORE = NO
RRF_CANDIDATE_INTERFACE_READY = NO   GRAPH_CANDIDATE_IMPLEMENTATION_READY = NO
STRUCTURED_EVIDENCE_IMPLEMENTATION_READY = NO
ROOT_CAUSE_CONFIRMED = NO   H1/H2/H3 = INCONCLUSIVE   08F_MITIGATION_GATE_JUSTIFIED = NO
```

## 17. Next step, research questions & posture (decision B)

```
GRAPHRAG_PRODUCTION_INTEGRATION = NOT_APPROVED   GRAPHRAG_RESEARCH_PATH = RETAINED_OPTIONAL
NEXT_EXPERIMENT_REQUIRED = NO   NEXT_EXPERIMENT_NAME = NONE
GRAPH_RAG_09_JUSTIFIED = NO
```

There is **no mandatory next live experiment** and no deferred experiment is scheduled — the
research path staying open is **not** an approved new phase. `GRAPHRAG_07_CONTRACT_STILL_VALID =
YES` — interface/contract validity is independent of product/retrieval value and remains useful
architecture work.

**Bounded future research questions (questions, NOT authorized work):**
- Can graph relationship/provenance evidence add useful information for Sources already selected by
  vector retrieval?
- Can Open-Notebook-owned gating determine when a GraphRAG evidence call is worth making?
- Can a future LightRAG version support true pre-retrieval Source scoping?
- Can incremental graph evidence provide required multi-hop context without introducing
  unacceptable new false positives?

Pursuing any of these requires a new proposal + evidence gate (§15b); none authorizes GraphRAG-09
or Ask integration. Boundary B stays unchanged (§8): provider-backed LightRAG research remains
limited to synthetic/public/approved-anonymized data until separately approved.
