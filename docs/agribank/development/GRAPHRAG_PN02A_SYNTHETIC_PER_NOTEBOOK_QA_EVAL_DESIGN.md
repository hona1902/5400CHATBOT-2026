# GraphRAG-PN02A — Synthetic Per-Notebook Isolation & QA Evaluation (Design Freeze)

**Status:** OFFLINE EXPERIMENT DESIGN ONLY. **No code, no provider traffic, no live LightRAG, no DB
mutation, no PN02 implementation, no Ask production integration, no RRF, no GraphRAG-09.** Docs only;
**not checkpointed** (operator review required). Continuation of the separate **GraphRAG-PN** track.

**Authoritative baseline (verified from git):** branch `feature/graphrag-lifecycle`, HEAD
`e75a500b82a58295f58d8654b038e3a588669597`, annotated tag
`graphrag-pn01-per-notebook-forensic-approved` (peels → `e75a500…`), working tree CLEAN.
GraphRAG-08 = **CLOSED/APPROVED**; GraphRAG-09 = **NOT_JUSTIFIED**; PN01 = **APPROVED** (Decision C).
LightRAG pinned `v1.5.6`.

**PN01 findings retained (frozen inheritance):** `WORKSPACE_DATA_ISOLATION=STRONG`,
`PER_NOTEBOOK_WORKSPACE_TECHNICALLY_FEASIBLE=YES`, `PER_NOTEBOOK_QUERY_ROUTING_FEASIBLE=PARTIAL`
(process/container selection, no per-query workspace switch), `PER_NOTEBOOK_INDEX_LIFECYCLE_FEASIBLE=PARTIAL`,
`CROSS_NOTEBOOK_GRAPH_ISOLATION_FEASIBLE=YES`, `SOURCE_SHARED_ACROSS_NOTEBOOKS_SUPPORTED_BY_ON=YES`,
`PREFERRED_FINAL_ANSWER_OWNER=OPEN_NOTEBOOK`, `PREFERRED_GRAPH_QUERY_SEAM=/query/data`,
`LIGHTRAG_REQUIRED_FOR_NORMAL_NOTEBOOK_QA=NO`, `PER_NOTEBOOK_LIGHTRAG_VALUE_EVIDENCED=NOT_YET`,
`LIGHTRAG_PER_NOTEBOOK_UNIQUE_VALUE=NOT_PROVEN`, `GRAPHRAG_PRODUCTION_INTEGRATION=NOT_APPROVED`,
`LIGHTRAG_ASK_INTEGRATION=NOT_APPROVED`, `GRAPH_RAG_09_JUSTIFIED=NO`,
`QUERY_DATA_EXPOSES_VALID_RANK/SCORE=NO`, `RRF_CANDIDATE_INTERFACE_READY=NO`.

---

## 1. Scientific question & why it is new vs GraphRAG-08

GraphRAG-08 tested **one shared 75-Source corpus** and found broad low-precision retrieval with no
abstention. PN02 asks a **different** question over **multiple small isolated notebook corpora**:

> **(Q-ISO)** Does per-notebook LightRAG **workspace isolation** provide *safe* notebook-scoped graph
> evidence — zero cross-notebook leakage?
> **(Q-VAL)** Does that notebook-local evidence add *useful* QA value over a notebook-scoped vector
> baseline in **small** notebooks (~8 Sources), or does the GraphRAG-08 broad-candidate behavior
> persist at notebook scale?

These are measured **separately** (§2). PN02 must **not** re-answer GraphRAG-08 (do not rerun the
75-Source global benchmark). `PN02_ANSWERS_A_NEW_QUESTION = YES`.

---

## 2. Two-stage structure (gate ordering)

```
STAGE 1 — ISOLATION + EVIDENCE VALIDITY   (hard security/isolation gates; §19-§20)
     └─ must PASS (fail-closed, §42) before →
STAGE 2 — OPEN-NOTEBOOK-OWNED QA VALUE    (§25-§30)
```
Answer-quality results may **never** be reported if Stage 1 isolation fails — this prevents a good
answer score from masking a leakage defect.

---

## 3. Synthetic fixture — frozen topology

**Fixture identity:** `graphrag_pn02_eval_v1` (NEW; `graphrag_08_eval_v1` and its hash are
**untouched**, §46). All content synthetic, non-sensitive, deterministic, human-authored, frozen
before any retriever (§7). Fixture **content files + hash** are authored/frozen at **PN02B** (design
first, per the GraphRAG-08 design-gate→fixture-freeze convention, §61); this gate freezes the exact
**counts, matrix, taxonomy, GT schema, and gates**.

### 3a. Counts (frozen — the §6 recommended topology, accepted)

```
NOTEBOOK_COUNT                    = 3          (NB_A, NB_B, NB_C — opaque logical IDs)
CANONICAL_SOURCE_COUNT            = 21         (18 notebook-unique + 3 shared)
NOTEBOOK_UNIQUE_SOURCE_COUNT      = 18         (A1..A6, B1..B6, C1..C6)
SHARED_SOURCE_COUNT               = 3          (SH_AB, SH_AC, SH_BC)
WORKSPACE_MEMBERSHIP_COUNT        = 24         (membership edges; = graph index operations, §39)
SOURCES_PER_NOTEBOOK              = 8          (6 unique + 2 shared)
```
The recommended topology is **accepted unchanged** — it yields 8 Sources/notebook (enough distractors
for precision, not trivially tiny) with deliberate pairwise sharing so leakage accounting is
non-trivial but unambiguous (§9).

### 3b. Source ↔ Notebook membership matrix (frozen)

| Notebook | Unique members | Shared members | Total |
|---|---|---|---|
| **NB_A** | A1 A2 A3 A4 A5 A6 | SH_AB, SH_AC | 8 |
| **NB_B** | B1 B2 B3 B4 B5 B6 | SH_AB, SH_BC | 8 |
| **NB_C** | C1 C2 C3 C4 C5 C6 | SH_AC, SH_BC | 8 |

Membership edges = 6×3 unique + (SH_AB→{A,B}) + (SH_AC→{A,C}) + (SH_BC→{B,C}) = 18 + 6 = **24**.
Canonical Sources = 18 + 3 = **21**. Modeled on the ON `reference` RELATION edge (many-to-many,
`migrations/1.surrealql:54-56`); shared Sources carry **two** edges (PN01 §2a). Derived per-workspace
graph copies (§39) are **distinct** from canonical Sources: `CANONICAL_SOURCE_COUNT=21` vs
`WORKSPACE_MEMBERSHIP_COUNT=24`.

---

## 4. Content design plan (frozen intent; text authored at PN02B)

Each notebook has a distinct theme with deterministic answer tokens. Content includes: unique
notebook-local facts, semantic paraphrases, entity relationships, **multi-hop chains** (facts split
across ≥2 member Sources), shared facts (in SH_*), and **cross-notebook collision distractors**
(§8). No runtime LLM-generated truth (§31).

**Frozen collision anchor (illustrative, exact tokens fixed at PN02B):**
- **NB_A** A2: *"Project Orion — internal code **AX-17**"*; A1: *"Talos-9 coordinates Project Orion"*;
  A4: *"Project Orion ships module **M-Alpha**."* (multi-hop A1+A4).
- **NB_B** B2: *"Project Orion — internal code **BX-94**"* (same entity name, **different** code).
- **NB_C** C2: *"Project Orion — internal code **CX-51**"*.

A query inside NB_A for "Project Orion's internal code" must return **AX-17 / A2** and must **never**
return B2 or C2 (leakage), despite the lexical collision. Analogous collisions seeded for a second
entity family per §8.

**Shared-fact anchors:** SH_AB holds a fact legitimately citable by NB_A **and** NB_B; SH_AC by A&C;
SH_BC by B&C. Used for the SHARED_SOURCE class (§10) and the membership-removal test (§32).

---

## 5. Cross-notebook collision & shared-source semantics (frozen)

Legitimate vs forbidden citation sets, per queried notebook:

| Query in | May cite (member) | MUST NOT cite (non-member ⇒ leakage) |
|---|---|---|
| NB_A | A1..A6, SH_AB, SH_AC | B*, C*, SH_BC |
| NB_B | B1..B6, SH_AB, SH_BC | A*, C*, SH_AC |
| NB_C | C1..C6, SH_AC, SH_BC | A*, B*, SH_AB |

A shared Source appearing in a notebook it legitimately belongs to is **NOT** leakage (§3). Any
non-member Source appearing **is** leakage (hard fail, §20).

---

## 6. Query taxonomy & count (frozen)

**8 classes × 3 notebooks = 24 queries** (one of each class per notebook; every notebook therefore
has exactly one negative and one multi-hop). `QUERY_COUNT = 24`.

| # | Class | Intent | answerable | multihop |
|---|---|---|---|---|
| 1 | `DIRECT_LOCAL` | literal notebook-unique fact | YES | no |
| 2 | `SEMANTIC_LOCAL` | paraphrase of a notebook-unique fact | YES | no |
| 3 | `MULTIHOP_LOCAL` | answer needs ≥2 member Sources | YES | **yes** |
| 4 | `SHARED_SOURCE` | answerable via a shared member Source | YES | no |
| 5 | `CROSS_NOTEBOOK_COLLISION` | terms collide with another notebook; only this notebook's evidence is valid | YES | no |
| 6 | `NEGATIVE_UNANSWERABLE` | fact absent from this notebook (may exist in another) | **NO** | no |
| 7 | `RELATIONSHIP_CORROBORATION` | relationship among member Sources corroborates a member fact | YES | no |
| 8 | `PARTIAL_EVIDENCE` | 1 REQUIRED + OPTIONAL_SUPPORT; partial coverage expected | YES | no |

This taxonomy is **per-notebook QA specific** (leakage/shared/collision classes), not a copy of the
GraphRAG-08 taxonomy. Class-to-each-notebook applicability verified: all 8 apply to all 3 (the
collision/negative anchors are seeded symmetrically, §4). **No live query additions later.**

---

## 7. Ground-truth schema (authored BEFORE any retriever)

Per query, frozen:
```
query_id · queried_notebook_id · class · answerable(Y/N)
REQUIRED[]            (canonical Source IDs; minimal set that must be recovered)
OPTIONAL_SUPPORT[]    (member Sources that corroborate but are not required)
FORBIDDEN[]           (explicit non-member collision-bait Sources; leakage if returned)
expected_answer_facts[]       (deterministic tokens, e.g. "AX-17")
forbidden_answer_facts[]      (tokens available ONLY from non-member Sources, e.g. "BX-94")
expected_abstention(Y/N) · multihop_required(Y/N) · required_citations[]
```
**Invariants (verified from the corpus alone, before retrieval):** R1 — every `MULTIHOP_LOCAL` has
`|REQUIRED|≥2` and no single member Source answers it; R2 — every `NEGATIVE_UNANSWERABLE` has
`REQUIRED=∅`, `answerable=NO`, `expected_abstention=Y`; R3 — `FORBIDDEN` for a query in notebook X is
a subset of X's non-members and always includes the specific collision counterpart(s) (e.g. NB_A
Orion query FORBIDDEN ⊇ {B2, C2}). **Leakage is defined structurally** (returned ⊄ current members),
so FORBIDDEN sharpens accounting but does not narrow the leakage gate.

---

## 8. Systems & seams

### 8a. Stage-1 systems (frozen)
- **V** — notebook-scoped Open-Notebook **vector baseline**.
- **GD** — notebook-isolated LightRAG **`/query/data`** structured evidence.
- **GQ NOT required** (`GQ_REQUIRED = NO`): PN01/08 established GQ≡GD Source-set parity and GD's
  runtime advantage; rerunning `client.query()` would spend final-answer provider budget for no new
  evidence. GQ is **omitted** (no tiny control retained — the parity is already frozen evidence).

### 8b. Vector baseline seam (frozen, eval-only — §16)
Production `fn::vector_search` (`migrations/1.surrealql:139-173`) is **corpus-global** (no notebook
scope) and limits before ranking — **not** used. PN02 freezes an **eval-only, notebook-local**
scoped query performing direct membership selection (pre-retrieval scope, **not** post-filter):
```
member_ids = SELECT VALUE in FROM reference WHERE out = $notebook_id           -- current members only
V(k)       = SELECT source AS item_id,
                    math::max(vector::similarity::cosine(embedding, $q)) AS sim
             FROM source_embedding WHERE source IN $member_ids
             GROUP BY item_id ORDER BY sim DESC LIMIT $k
```
Candidate set is **structurally** confined to current members → V cannot leak by construction (it is
the honest baseline any graph arm must beat). Production code is **untouched**.

### 8c. Vector K (frozen now — §17)
`VECTOR_K_VALUES = {3, 5}` (both frozen, **not** tuned post-hoc). Rationale: 8 Sources/notebook; K=3
tests tight precision (recall risk on `MULTIHOP_LOCAL`/`PARTIAL_EVIDENCE`), K=5 tests broader recall
while staying < notebook size (never trivially returns all 8). **Stage-2 QA arms use K=5** as the
frozen answer-context vector set; K=3 is a Stage-1 precision diagnostic.

### 8d. GD semantics (frozen — §18)
`/query/data`, `mode=HYBRID`, `only_need_context=True`, **`top_k` UNSET** (vendor default) so natural
candidate breadth is observed for the §24 breadth question (top_k is a count lever, not source
scoping — 08). **Unordered set**; `QUERY_DATA_EXPOSES_VALID_RANK/SCORE=NO`; **no MRR/nDCG for GD**;
no fake graph ranking. STRONG-anchor projection to canonical Source IDs reuses the PN01/08
gd_seam-style normalization (eval-only).

---

## 9. Stage-1 metrics & hard gates

### 9a. Isolation/evidence metrics (§19)
`CROSS_NOTEBOOK_LEAKAGE_RATE` · `CROSS_NOTEBOOK_LEAK_QUERY_COUNT` ·
`CROSS_NOTEBOOK_LEAK_SOURCE_OCCURRENCES` · `SOURCE_PRECISION` · `SOURCE_RECALL` · `SET_F1` ·
`REQUIRED_SOURCE_RECOVERY` · `FULL_REQUIRED_SET_RECOVERED` · `CANDIDATE_COUNT` ·
`CANDIDATE_FRACTION_OF_NOTEBOOK` (denominator = 8) · `FALSE_POSITIVE_COUNT` ·
`NEGATIVE_EVIDENCE_RETURN_RATE` · `PROVENANCE_VALIDITY` · `CITATION_MEMBERSHIP_VALIDITY`.

### 9b. HARD Stage-1 pass conditions (§20 — fail ⇒ STOP before Stage 2)
```
CROSS_NOTEBOOK_LEAKAGE_RATE            = 0     (exact; no averaging away a leak, §48)
CROSS_NOTEBOOK_LEAK_QUERY_COUNT        = 0
CROSS_NOTEBOOK_LEAK_SOURCE_OCCURRENCES = 0
PROVENANCE_FOREIGN                     = 0
PROVENANCE_MALFORMED                   = 0
CITATION_MEMBERSHIP_INVALID            = 0
```
Applies to **both** V and GD (V is structurally leak-proof by §8b; GD is the system under test). Any
failure ⇒ `PN02 Stage-1 FAILS`, fail-closed (§42), **no Stage 2**.

### 9c. Incremental graph-value metrics (§22 — retrieval value ≠ isolation) — EXACT formulas

All incremental metrics are computed **vs the Stage-2 vector set `V5(q)`** (K=5; K=3 reported as a
secondary diagnostic only). Notation for a query `q` executed in notebook `N`: `M(N)` = current
members of `N` (`|M(N)|=8` initially); `REQ(q)`/`OPT(q)` = required / optional-support Source sets;
`GD(q)` = GD's returned canonical Source set; `GDm(q) = GD(q) ∩ M(N)` (member subset — any Source in
`GD(q)\M(N)` is a **Stage-1 leakage** event, not counted here); `I(q) = GDm(q) \ V5(q)` (the
increment GD adds beyond V5). `POS` = the 21 non-negative queries; `NEG` = the 3 negatives.

```
GRAPH_NEW_REQUIRED_SOURCES(q)   = | (REQ(q) ∩ GDm(q)) \ V5(q) |            # required Sources GD adds that V5 missed
GRAPH_NEW_FALSE_POSITIVES(q)    = | I(q) \ (REQ(q) ∪ OPT(q)) |            # member non-required/non-optional Sources GD adds beyond V5
GD_CANDIDATE_COUNT(q)           = | GDm(q) |
GD_CANDIDATE_FRACTION(q)        = | GDm(q) | / | M(N) |                    # denominator 8
FULL_REQ_RECOVERED(S, q)        = ( REQ(q) ⊆ S ) ? 1 : 0

INCREMENTAL_REQUIRED_SOURCE_RECOVERY = Σ_{q∈POS} GRAPH_NEW_REQUIRED_SOURCES(q)      # total count
INCREMENTAL_FALSE_POSITIVE_COUNT     = Σ_{q∈POS} GRAPH_NEW_FALSE_POSITIVES(q)       # total count
INCREMENTAL_GRAPH_PRECISION          = INCREMENTAL_REQUIRED_SOURCE_RECOVERY
                                       / Σ_{q∈POS} |I(q)|                            # UNDEFINED if Σ|I(q)| = 0
NEGATIVE_EVIDENCE_RETURN_RATE(sys)   = |{ q∈NEG : sys returns ≥1 member Source }| / 3   # sys ∈ {V5, GD}
MULTIHOP_INCREMENTAL_REQUIRED_RECOVERY =
      |{ q∈MULTIHOP : FULL_REQ_RECOVERED(V5(q),q)=0 ∧ FULL_REQ_RECOVERED(V5(q)∪GDm(q),q)=1 ∧ GD_CANDIDATE_FRACTION(q)<1.0 }|
```
Derived counters used by §17: `N_GAIN_NOTEBOOKS_D` = number of distinct notebooks that contain at
least one `q∈POS` with `GRAPH_NEW_REQUIRED_SOURCES(q) ≥ 1` **and** `GD_CANDIDATE_FRACTION(q) < 1.0`
(a "discriminative gain" query — GD added a required Source V5 missed **without** returning the whole
notebook). **No RRF; any V∪GD union is diagnostic only** (`RRF_CANDIDATE_INTERFACE_READY=NO`, §23).

---

## 10. Stage-2 — Open-Notebook-owned QA

`FINAL_ANSWER_OWNER = OPEN_NOTEBOOK`. Flow (§25): question → ON authorization + notebook membership →
evidence arm → ON context construction → **ON final-answer LLM** → ON citations. LightRAG
`client.query()` is **not** a principal answer path (`LIGHTRAG_CLIENT_QUERY_AS_FINAL_ANSWER=NO`).

### 10a. Arms (§26)
- **QA-V** — ON answer from V(K=5) evidence.
- **QA-GD** — ON answer from notebook-local GD evidence.
- **QA-V+GD** — ON answer from a **frozen deterministic** evidence combination (§10b).

### 10b. Frozen V+GD combination rule (§27 — no fake rank, no post-hoc tuning)
```
evidence = [ ordered V(K=5) member Sources ]
        ++ [ GD member Sources NOT already in V(K=5), sorted by canonical source_id (NOT any score) ]
cap: |evidence| ≤ K5_CAP = 8   (= notebook size; never exceeds a notebook's own Sources)
hard filter: every admitted Source ∈ current members of the queried notebook (drop any non-member)
```
No pseudo-rank is invented (GD additions ordered by canonical ID, a stable non-relevance order);
canonical Source IDs preserved; non-members structurally excluded. Frozen **before** any live result.
If this rule proves indefensible in review, **QA-V+GD is omitted rather than re-tuned**.

### 10c. QA ground truth & grading (§28, §31)
Deterministic `EXPECTED_ANSWER_FACTS` / `FORBIDDEN_ANSWER_FACTS` / `EXPECTED_ABSTENTION` /
`REQUIRED_CITATIONS` authored with the fixture. **Deterministic token & citation checks are
authoritative and sufficient** for every §17 decision. `FORBIDDEN_ANSWER_FACTS` are deterministic
tokens (e.g. `BX-94`), so `HALLUCINATED_FORBIDDEN_FACT_RATE` is a deterministic substring/token check
— **no** judge model is required. `JUDGE_MODEL_CALLS = 0` is frozen (§13); no LLM-as-ground-truth,
and no unbudgeted judge may run. Any judge would need a **separate** frozen cap and could not feed a
§17 verdict.

### 10d. QA metrics (§29–§30)
`ANSWER_FACT_ACCURACY` · `ANSWER_REQUIRED_FACT_RECALL` · `HALLUCINATED_FORBIDDEN_FACT_RATE` ·
`NEGATIVE_ANSWER_ABSTENTION_RATE` · `CITATION_VALIDITY_RATE` · `CITATION_INVALID_COUNT` (= citations
failing validity **or** membership) · `CITATION_REQUIRED_SOURCE_COVERAGE` ·
`CROSS_NOTEBOOK_ANSWER_LEAKAGE_RATE` · `ANSWER_LATENCY`. **Answer leakage** (§30): an answer contains
a fact available **only** from a non-member Source (e.g. NB_A answer emitting `BX-94`) — measured
**separately** from evidence-set leakage (§9 Stage-1).

### 10e. QA hard-safety conditions (§6 — required for ANY positive QA claim)
An arm may contribute to `PER_NOTEBOOK_GRAPH_QA_VALUE_EVIDENCED = YES` **only if** all three hold
(the `SAFE(G)` predicate of §17c), each an exact `= 0` check over all 24 queries:
```
S1  CROSS_NOTEBOOK_ANSWER_LEAKAGE_RATE = 0     (no answer fact sourced only from a non-member Source)
S2  CITATION_INVALID_COUNT             = 0     (every emitted citation is valid AND a current member)
S3  HALLUCINATED_FORBIDDEN_FACT_RATE   = 0     (deterministic forbidden-token check; strict)
```
Any `S1/S2/S3 > 0` on an arm both (a) disqualifies that arm from a positive claim and (b) is reported
as a **Stage-2 safety failure**. These are independent of, and additional to, the Stage-1 evidence
gates (§9b).

---

## 11. Lifecycle transition tests

### 11a. Membership removal (INCLUDED — §32/§33)
`MEMBERSHIP_REMOVAL_TEST_INCLUDED = YES`. Transition: **SH_AB** (member of A & B) → remove from **A**,
retain in **B**. Method: delete the `reference` edge `SH_AB→NB_A` (edge-only, mirrors the per-source unlink endpoint
`api/routers/notebooks.py:389-405` — `DELETE FROM reference WHERE out=$notebook_id AND in=$source_id`
at :397-403) + delete SH_AB's derived graph data from **workspace A only**. Re-run the
SHARED_SOURCE probe in A and in B:
- **Postcondition A:** SH_AB must **no longer** appear as valid evidence/citation for NB_A.
- **Postcondition B:** SH_AB **remains** valid for NB_B.
Tests per-notebook graph deletion + membership routing + canonical authorization validation.
- **Failure-safety (§33):** if the LightRAG deletion from workspace A **fails**, ON membership
  post-validation must **still** block SH_AB from becoming valid NB_A evidence/citation →
  `STALE_GRAPH_EVIDENCE_ACCEPTED_AS_VALID = 0` (defense-in-depth; simulated by asserting validation
  independently of derived-store success).

### 11b. Source update (EXCLUDED — §34)
`SOURCE_UPDATE_TEST_INCLUDED = NO` — deferred. Reindex/update mechanics are already covered by the
GraphRAG-03 series; including it would expand PN02 beyond the primary isolation/QA question without
adding to Q-ISO/Q-VAL.

### 11c. Notebook delete (EXCLUDED — §35)
`NOTEBOOK_DELETE_TEST_INCLUDED = NO` — deferred. Lifecycle completeness, not retrieval/QA value.

---

## 12. Workspace topology, attestation & routing (§36–§39)

- **Topology:** 3 notebook-specific LightRAG containers, **one per notebook**, each bound to one
  unique workspace/storage (`WORKSPACE_PROCESS_MODEL = ONE_CONTAINER_PER_NOTEBOOK`). No production
  process manager (PN02 tests feasibility/value, not final ops topology, §32/§55). The offline
  harness (PN02B) **reuses the eval isolation/attestation stack** (08E `cell_isolation08` /
  `cell_provisioner08` / `DockerRuntimeAttestor` concepts) adapted to notebook workspaces — eval-only,
  **never imported into production** (`PRODUCTION_IMPORTS_EVAL=NO`).
- **Attestation before any query (§37):** owned container · expected workspace · storage root ·
  provider binding (frozen OpenRouter models) · LightRAG version `v1.5.6` · endpoint ownership ·
  correct notebook↔workspace mapping. **No query against an unattested workspace** (§42).
- **Routing table (§38):** `notebook logical ID → workspace_id = "nb_"+sha256(notebook_record_id)[:16]
  → owned endpoint` (opaque, deterministic, PN01 §5). **No notebook titles/display names** as
  identity.
- **Shared-source indexing (§39):** SH_* indexed **independently** into each member workspace
  (expected duplication). Track `CANONICAL_SOURCE_COUNT=21`, `WORKSPACE_MEMBERSHIP_COUNT=24`,
  `GRAPH_INDEX_OPERATIONS` separately.

---

## 13. Provider workload budget & retry (§40–§41)

**Frozen hard caps** (LightRAG-internal provider call multiplicity is **not observable**, so caps are
in harness-countable units, not vendor API calls). Every provider-backed path is bounded; no path has
unexplained headroom.

**Indexing / embedding (canonical, single-copy in the ON store):**
```
MAX_CANONICAL_SOURCE_EMBEDDINGS   = 21     ── one vectorize op per canonical Source (21); shared Sources single-copy in ON store
PLANNED_GRAPH_INDEX_OPERATIONS    = 24     ── one graph index per membership edge (§3b: 18 unique + 6 shared); shared Source indexed once per member workspace
MAX_INDEX_ATTEMPTS_PER_OPERATION  = 2      ── frozen index_retry08; reindex = delete-then-insert
MAX_GRAPH_INDEX_ATTEMPTS          = 48     ── 24 × 2
MEMBERSHIP_REMOVAL_REINDEX_OPS    = 0      ── removal is a DELETE, not a reindex; SOURCE_UPDATE_TEST=NO ⇒ no reindex fan-out anywhere ⇒ planned indexes stay exactly 24
```

**GD queries — `MAX_GD_QUERIES = 26`, exact breakdown (no headroom):**
```
BASELINE_STAGE1_GD_QUERIES        = 24     ── one GD /query/data per benchmark query, all 24 (incl. the 3 negatives, which GD must be probed on for NEGATIVE_EVIDENCE_RETURN_RATE)
MEMBERSHIP_REMOVAL_GD_QUERIES     = 2      ── post-removal re-probe of the two existing SHARED_SOURCE queries (one in A, one in B); their pre-removal runs are already inside the 24
OTHER_GD_QUERIES                  = 0
TOTAL_GD_QUERIES                  = 26     ── 24 + 2; no safety headroom (a value ≠ 26 is a design violation)
```

**Vector queries — `MAX_VECTOR_QUERY_OPERATIONS = 26`, `MAX_QUERY_EMBEDDING_OPERATIONS = 26`:**
```
BASELINE_STAGE1_VECTOR_QUERIES    = 24     ── one scored notebook-scoped retrieval per query; K=3 and K=5 are SLICES of the same scored list, NOT separate queries
MEMBERSHIP_REMOVAL_VECTOR_QUERIES = 2      ── post-removal re-probe of the same two SHARED_SOURCE queries (confirms V also drops SH_AB in A via membership scoping)
MAX_VECTOR_QUERY_OPERATIONS       = 26     ── 24 + 2
MAX_QUERY_EMBEDDING_OPERATIONS    = 26     ── one query-string embedding per vector query execution (GD retrieval-side embedding is vendor-internal, not separately counted)
```

**Final-answer calls — `MAX_FINAL_ANSWER_CALLS = 72`, exact breakdown:**
```
STAGE2_FINAL_ANSWER_CALLS         = 72     ── 24 queries × 3 arms {QA-V, QA-GD, QA-V+GD}; a negative query still consumes one answer attempt per arm (to measure abstention)
MEMBERSHIP_REMOVAL_FINAL_ANSWER   = 0      ── the removal test is EVIDENCE-level (GD + vector + citation-membership validation) only; it runs NO final-answer generation
FINAL_ANSWER_TECHNICAL_RETRY_CAP  = 0      ── no retry-to-improve; if Stage-2 is blocked (Stage-1 FAIL), actual final-answer calls = 0
JUDGE_MODEL_CALLS                 = 0      ── grading is DETERMINISTIC (tokens + citations); NO LLM judge is budgeted. A judge, if ever desired, requires a SEPARATE frozen cap and MUST NOT draw from this budget (§31)
```

**Membership-removal workload (§14) — total, itemized:**
```
GRAPH_DELETE_OPS (SH_AB from workspace A)  = 1     (provider/derived-store delete)
REFERENCE_EDGE_DELETE (SH_AB→NB_A)         = 1     (SurrealDB, not provider)
POST_REMOVAL_GD_VALIDATION_QUERIES         = 2     (counted in the 26 GD total above)
POST_REMOVAL_VECTOR_VALIDATION_QUERIES     = 2     (counted in the 26 vector total above)
POST_REMOVAL_FINAL_ANSWER_CALLS            = 0
```
The removal workload is fully **inside** the declared caps (its 2 GD + 2 vector re-probes are the
`MEMBERSHIP_REMOVAL_*` lines above); it adds **zero** graph index ops and **zero** final-answer calls.

**Retry policy (§41 — reuse approved bounded behavior only):**
```
MAX_INDEX_ATTEMPTS_PER_OPERATION  = 2      (reuse frozen index_retry08; delete-then-insert on reindex)
MAX_QUERY_TECHNICAL_RETRIES       = 1      (transport-level only; never to change results)
FINAL_ANSWER_RETRIES              = 0      (NO retry-to-improve-answer)
```

---

## 14. Vector-only fallback (§43) & boundaries

- `LIGHTRAG_REQUIRED_FOR_NORMAL_NOTEBOOK_QA = NO` — a **provider-free control** must confirm: if a
  notebook's graph workspace is unavailable/unattested, ON **vector-only** QA (QA-V) still runs.
- **Boundary B (§45):** NOT approved for real internal data. PN02 execution uses **only** the frozen
  `graphrag_pn02_eval_v1` synthetic fixture (or other explicitly-approved synthetic/public data). No
  user notebooks, no Agribank internal documents.
- **Production boundary (§55):** even on positive PN02 evidence,
  `GRAPHRAG_PRODUCTION_INTEGRATION=NOT_APPROVED` and `LIGHTRAG_ASK_INTEGRATION=NOT_APPROVED` remain
  during and after execution; a later separate architecture gate is required.

---

## 15. Cleanup & observability (§53–§54)

- **Cleanup (required):** all synthetic notebook sidecars, all workspaces, all run-owned storage,
  temporary Surreal state (if used), temporary models, synthetic Source state. **Normal DB
  unchanged; no runtime residue.**
- **Content-safe observability (§53):** container/process count, workspace storage size (if
  safe/easy), startup + cleanup duration, derived-Source duplication count. No production-scale
  extrapolation.
- **Latency, measured separately (§52):** workspace startup · ready-to-query · GD query · vector
  query · ON final-answer · total QA. Stages not compared as if equivalent.

---

## 16. Fixture versioning & integrity (§46–§47)

`FIXTURE_NAME = graphrag_pn02_eval_v1` (immutable once checkpointed; any post-live content/methodology
change ⇒ `v2`). `graphrag_08_eval_v1` untouched. `FIXTURE_HASH_REQUIRED = YES`;
`FIXTURE_HASH_VALUE = TO_BE_GENERATED_AND_FROZEN_AT_PN02B_IMPLEMENTATION_CHECKPOINT`. **Hash method
(frozen now):** SHA-256 over a canonical, sorted-key JSON serialization of {source contents,
membership matrix, query list, ground truth} (deterministic ordering; no timestamps). Hash must match
before **and** after any provider traffic. No hash fabricated at design stage.

---

## 17. Decision rules (frozen — §48–§51) — FULLY MECHANICAL

Every rule below is computable from the §9 metrics with **no** post-run judgment, weighting, or
threshold tuning. All comparisons are exact integer/rational comparisons on **frozen** metrics. No
adjectives ("useful", "acceptable", "near-zero", "materially", "≈all", "beats", "not regress") carry
decision weight anywhere in this section.

### 17a. Isolation (§48) — `PER_NOTEBOOK_ISOLATION_EVIDENCED`
```
YES  iff  ALL §9b hard gates = 0
          (CROSS_NOTEBOOK_LEAKAGE_RATE, CROSS_NOTEBOOK_LEAK_QUERY_COUNT,
           CROSS_NOTEBOOK_LEAK_SOURCE_OCCURRENCES, PROVENANCE_FOREIGN,
           PROVENANCE_MALFORMED, CITATION_MEMBERSHIP_INVALID)  ── all exactly 0, over ALL 24 queries + 2 removal re-probes
NO   iff  any of the above > 0
```
A single leak ⇒ NO. **No averaging.** (There is no INCONCLUSIVE state for isolation.)

### 17b. Retrieval value (§49) — `PER_NOTEBOOK_GRAPH_RETRIEVAL_VALUE_EVIDENCED` (first match wins)
Uses (§9c): `INCREMENTAL_REQUIRED_SOURCE_RECOVERY` (= `NEW_REQ`), `INCREMENTAL_FALSE_POSITIVE_COUNT`
(= `NEW_FP`), `N_GAIN_NOTEBOOKS_D`, and `NEG_RETURN(GD)` / `NEG_RETURN(V5)` (=
`3·NEGATIVE_EVIDENCE_RETURN_RATE`).
```
R0.  Stage-1 isolation = NO (17a)            → NOT_EVALUATED   (retrieval value is not reported)
R1.  NEW_REQ == 0                            → NO              (GD adds no required Source V5 missed, anywhere)
R2.  N_GAIN_NOTEBOOKS_D == 0                 → NO              (every new-required gain came from a full-8-source return; coverage, not discrimination — the 08 §40 trap)
R3.  N_GAIN_NOTEBOOKS_D ≥ 2
     AND NEW_REQ > NEW_FP                     (criterion C: strictly more incremental required than incremental member false positives)
     AND NEG_RETURN(GD) ≤ NEG_RETURN(V5)      (criterion E: GD returns evidence on no MORE negatives than V5)
                                             → YES
R4.  otherwise                               → INCONCLUSIVE   (real discriminative gain exists but confined to 1 notebook, OR NEW_FP ≥ NEW_REQ, OR GD regresses negatives)
```
**Criterion C** = the exact count comparison `NEW_REQ > NEW_FP` — **this is the operative rule to
implement.** It is **not** the same as `INCREMENTAL_GRAPH_PRECISION > 0.5`: because the increment
`I(q)` also contains optional-support members (`NEW_OPT = Σ_{q∈POS} |I(q) ∩ OPT(q)|`, excluded from
`NEW_FP` by the `\(REQ∪OPT)` term in §9c), pooled precision `> 0.5 ⟺ NEW_REQ > NEW_FP + NEW_OPT`,
which is strictly stronger whenever `NEW_OPT > 0`. Implement `NEW_REQ > NEW_FP` (optional
corroboration is not counted against the graph), **not** a precision threshold. **Criterion D
(breadth)** is folded into `N_GAIN_NOTEBOOKS_D`:
per §4 the **only** breadth disqualifier is `GD_CANDIDATE_FRACTION(q) = 1.0` (returns all 8) — such a
query never counts as a discriminative gain; fractions `< 1.0` (including 7/8) are governed entirely
by the C comparison, so no arbitrary sub-1.0 cutoff is invented. **Criterion E** = exact count
comparison on the 3 negatives.

### 17c. QA value (§50) — `PER_NOTEBOOK_GRAPH_QA_VALUE_EVIDENCED` (first match wins)
Per QA arm compute over the frozen query set: three **primary** dimensions **P1**
`ANSWER_REQUIRED_FACT_RECALL` (over POS), **P2** `CITATION_REQUIRED_SOURCE_COVERAGE` (over POS),
**P3** `NEGATIVE_ANSWER_ABSTENTION_RATE` (over the 3 NEG); three **hard-safety** dimensions **S1**
`CROSS_NOTEBOOK_ANSWER_LEAKAGE_RATE`, **S2** `CITATION_INVALID_COUNT`, **S3**
`HALLUCINATED_FORBIDDEN_FACT_RATE`. Comparison **scope = OVERALL aggregate** across all 24 queries
(per-notebook and per-class values are **reported as diagnostics only**, never weighted into the
verdict). For a graph arm `G ∈ {QA-GD, QA-V+GD}` vs baseline `B = QA-V`:
```
SAFE(G)      := Stage-1 = YES  AND  S1(G)=0  AND  S2(G)=0  AND  S3(G)=0
IMPROVES(G)  := P1(G) > P1(B)  OR  P2(G) > P2(B)  OR  P3(G) > P3(B)       # strict > on ≥1 primary
NO_REGRESS(G):= P1(G) ≥ P1(B)  AND  P2(G) ≥ P2(B)  AND  P3(G) ≥ P3(B)     # tie (=) counts as no-regression
POSITIVE(G)  := SAFE(G) AND IMPROVES(G) AND NO_REGRESS(G)

Q0.  Stage-1 isolation = NO (17a)                                  → NOT_EVALUATED
Q1.  ∃ G with POSITIVE(G) = true                                   → YES        (report which arm(s))
Q2.  ∀ G: SAFE(G)=true AND IMPROVES(G)=false                       → NO         (no arm improves any primary; none unsafe)
Q3.  otherwise                                                     → INCONCLUSIVE
```
`Q3` (INCONCLUSIVE) therefore covers exactly: an arm that improves a primary **but** regresses another
primary (mixed), and an arm whose only improvement comes with a hard-safety violation `¬SAFE(G)`
(that arm's improvement cannot count — and any `S1/S2/S3 > 0` is **also** reported as a Stage-2
safety failure, §6). **Tie behavior is explicit:** exact equality on a primary is "no regression"
but never counts as "improvement"; a YES requires a strict `>` on at least one primary while all
three hard-safety dimensions are 0.

### 17d. Multi-hop (§51) — `PER_NOTEBOOK_MULTIHOP_INCREMENTAL_VALUE_EVIDENCED` (first match wins)
Uses only the **3** `MULTIHOP_LOCAL` queries (one per notebook). Let `MH =
MULTIHOP_INCREMENTAL_REQUIRED_RECOVERY` (§9c: count of the 3 where GD completes the ≥2-Source required
set V5 alone missed, with `GD_CANDIDATE_FRACTION < 1.0`); each such query is in a distinct notebook,
so `MH` also counts distinct notebooks.
```
M0.  Stage-1 isolation = NO (17a)   → NOT_EVALUATED
M1.  MH ≥ 2                          → YES            (consistent multi-hop completion across ≥2 of 3 notebooks)
M2.  MH == 0                         → NO
M3.  MH == 1                         → INCONCLUSIVE   (a single query cannot establish general multi-hop value)
```

### 17e. INCONCLUSIVE is predeclared, not an escape hatch (§8)
The only INCONCLUSIVE branches are **R4**, **Q3**, and **M3** above, each defined by exact metric
conditions fixed **before** execution. No result may be relabeled INCONCLUSIVE for any reason outside
those branches.

**Statistical-power caveat (frozen expectation, non-decisional).** PN02 is a **screening**
experiment: the isolation arm (Q-ISO, leakage=0) is a hard structural gate well-powered by
construction; the value arms rest on n=21/24 (multi-hop n=3), and with `top_k` unset over 8-Source
workspaces GD may return all 8 members on gain queries (→ `N_GAIN_NOTEBOOKS_D` small → R2/R4). Landing
INCONCLUSIVE or NO on value is an expected, decision-relevant outcome (§18-P); PN02 screens whether a
per-notebook value effect plausibly exists, it does not attempt to prove a fine-grained effect size.

---

## 18. Independent review (§58)

An independent reviewer challenged the sixteen §58 questions (A–P). Summary:

- **A (3 notebooks enough for leakage?)** Yes for a *screening* leakage test — pairwise sharing
  (SH_AB/AC/BC) + symmetric collisions exercise every direction A↔B, A↔C, B↔C; leakage is a hard
  0-target so even one directed leak fails it. Scaling notebook count is a later concern, not a
  validity blocker.
- **B (8 Sources/notebook enough for precision?)** Yes — 8 with 6 distractors makes
  `CANDIDATE_FRACTION_OF_NOTEBOOK` meaningful and defeats the "return everything and still look
  good" artifact (K<8, GD breadth observed).
- **C (do shared Sources make leakage accounting ambiguous?)** No — §5 fixes exact legitimate vs
  forbidden sets per notebook; a shared Source is leakage **only** in the third notebook that does
  not own it (e.g. SH_AB in NB_C).
- **D (FORBIDDEN sets explicit?)** Yes — GT schema §7 mandates an explicit `FORBIDDEN[]` per query
  (R3), always including the collision counterpart(s).
- **E (could a global Source be treated as notebook-local?)** Guarded — V uses a structural
  `WHERE source IN member_ids` seam (§8b); GD uses a **physically separate** workspace per notebook
  (§12); ON post-validates membership on every returned Source (§10/§11a). Three independent layers.
- **F (evidence leakage vs answer leakage distinguished?)** Yes — separate metrics
  `CROSS_NOTEBOOK_LEAKAGE_RATE` (Stage 1, evidence) vs `CROSS_NOTEBOOK_ANSWER_LEAKAGE_RATE` (Stage 2,
  answer facts), §9/§10d/§30.
- **G (negatives genuine within the queried notebook?)** Yes — R2; a negative's fact is absent from
  the queried notebook (may exist in another, which also probes leakage-driven false answers).
- **H (multi-hop genuinely multi-source?)** Yes — R1 (`|REQUIRED|≥2`, no single member answers).
- **I (vector baseline uses exact notebook membership?)** Yes — §8b resolves current members via the
  `reference` edge and scopes the vector query to them (pre-retrieval, not post-filter).
- **J (GD uses structural workspace isolation?)** Yes — one container/workspace per notebook, fixed
  at process start (PN01 §3b), attested before query (§12).
- **K (Stage 2 blocked if isolation fails?)** Yes — §2/§42 fail-closed ordering.
- **L (does QA-V+GD introduce fake graph ranking?)** No — §10b appends GD Sources by canonical ID
  (non-relevance order), no pseudo-score; rule frozen pre-result; omitted if indefensible.
- **M (workloads bounded?)** Yes — §13 hard caps in harness-countable units; vendor call
  multiplicity explicitly not translated.
- **N (answer quality scored without circular LLM judging?)** Yes — deterministic tokens/citations
  authoritative; judge secondary only (§10c/§31).
- **O (genuinely new vs 08?)** Yes — small isolated corpora + leakage-0 + notebook-local incremental
  value, not the 75-Source global question (§1).
- **P (would a positive PN02 change a future architecture decision?)** Yes — it would satisfy a
  GraphRAG-08 §15b reopen criterion (a controlled, falsifiable per-notebook secondary-evidence design
  showing incremental value **without** unconstrained expansion), which is the sole route by which a
  *future* architecture gate could reconsider `LIGHTRAG_PER_NOTEBOOK_UNIQUE_VALUE`. A negative result
  strengthens the STOP posture. Either way the result is decision-relevant, so the experiment is
  worth implementing.

**Findings (independent review executed; all resolved):** **no HIGH**, and no arithmetic or
source-citation error in any load-bearing claim (the reviewer independently re-verified
`1.surrealql:139-173`/`:54-56`/`:16-20` and the `reference` edge direction `in`=source/`out`=notebook
against `notebook.py:36/284/530`). **MEDIUM-1** — a secondary citation for the membership-removal
unlink was ambiguous/wrong-file; **FIXED** to the exact endpoint `api/routers/notebooks.py:389-405`
(`DELETE FROM reference WHERE out=$notebook_id AND in=$source_id`, :397-403). **LOW-1** naming drift
(`WORKSPACE_(SOURCE_)MEMBERSHIP_COUNT`) **harmonized**; **LOW-2** query-string embedding workload
**added** to the budget (`MAX_QUERY_EMBEDDINGS=26`, §13); **LOW-3** value-arm statistical-power
caveat **added** (§17). The prempted design MEDIUMs (shared-source leakage accounting, V+GD fake-rank
risk, LLM-judge circularity) remain covered by §5 / §10b / §10c. No finding blocks the design or
flips Decision C.

**Decision-rule refinement review (§16 A–J, executed after §17/§9c/§13 were made mechanical).** A
focused independent pass confirmed: retrieval/QA/multi-hop rules are **fully mechanical** first-match
tables of exact integer/rational comparisons on frozen metrics (A, D); no `≈`/`near-zero`/`materially`
survives as a criterion (B, C — the only occurrence is the forbidden-adjective list in the §17
preamble); the **one-required-gain + many-false-positives loophole is closed** on both axes —
retrieval via pooled `NEW_REQ > NEW_FP` **and** `N_GAIN_NOTEBOOKS_D ≥ 2`, QA via `SAFE(G)`
(`S1=S2=S3=0`, so a hallucinated forbidden fact disqualifies an arm) (E); negatives are decisional,
not merely reported (F: retrieval criterion E + QA P3); every provider workload is exactly bounded
with self-consistent arithmetic — 21 / 24 (×2=48) / 26 / 26 / 26 / 72 / judge 0 (G), and the
membership-removal workload fits inside the caps (0 reindex, 0 final-answer) (H); Stage 2 cannot run
after any Stage-1 failure (I: R0/Q0/M0 = NOT_EVALUATED); and no LLM judge can create unbudgeted
traffic (J: `JUDGE_MODEL_CALLS = 0`, deterministic grading). **No HIGH.** **MEDIUM-1** — a descriptive
gloss in §17b wrongly equated criterion C to `INCREMENTAL_GRAPH_PRECISION > 0.5` (they differ by
`NEW_OPT`); **FIXED** — the operative rule is the count comparison `NEW_REQ > NEW_FP`, and the doc now
states the precision form is strictly stronger and must not be implemented in its place. **LOW-1** —
the single `GRAPH_DELETE_OPS = 1` sits outside the four named provider caps but is itemized and
bounded (one deletion, not generative traffic); accepted. Nothing flips Decision C.

---

## 19. PN02A decision (§56–§57)

```
PN02A_DECISION      = C
PN02A_DECISION_NAME = PN02_DESIGN_FROZEN_AND_IMPLEMENTATION_JUSTIFIED
```
C is selected: the fixture counts/matrix are exact (§3), query taxonomy/count exact (§6), GT schema
exact (§7), Stage-1 security gates exact and fail-closed (§9b/§42), provider workload bounded (§13),
answer ownership clear (`OPEN_NOTEBOOK`, §10), no GraphRAG-08 contradiction (frozen flags retained),
and every decision rule is falsifiable (§17). Not A (design is scientifically valid), not D (no
unresolved repo/vendor fact — the vector seam and workspace semantics are source-verified), not B
(the question is decision-relevant, §18-P).

```
PN02_IMPLEMENTATION_JUSTIFIED = YES
PN02_LIVE_AUTHORIZED          = NO
NEXT_PHASE                    = GraphRAG-PN02B — Synthetic Per-Notebook Evaluation Harness Implementation (OFFLINE ONLY)
```

---

## 20. Final report (§62)

```
GRAPH_RAG_PN02A_SYNTHETIC_PER_NOTEBOOK_QA_DESIGN = COMPLETE
AUTHORITATIVE_HEAD                = e75a500b82a58295f58d8654b038e3a588669597
PN01_STATUS                       = APPROVED
PN02_FIXTURE_NAME                 = graphrag_pn02_eval_v1
NOTEBOOK_COUNT                    = 3
CANONICAL_SOURCE_COUNT            = 21
WORKSPACE_MEMBERSHIP_COUNT        = 24
SOURCES_PER_NOTEBOOK              = 8
SHARED_SOURCE_COUNT               = 3
QUERY_COUNT                       = 24
QUERY_CLASSES                     = DIRECT_LOCAL, SEMANTIC_LOCAL, MULTIHOP_LOCAL, SHARED_SOURCE,
                                    CROSS_NOTEBOOK_COLLISION, NEGATIVE_UNANSWERABLE,
                                    RELATIONSHIP_CORROBORATION, PARTIAL_EVIDENCE
VECTOR_K_VALUES                   = {3, 5}  (Stage-2 QA uses K=5)
FINAL_ANSWER_OWNER                = OPEN_NOTEBOOK
GRAPH_QUERY_SEAM                  = /query/data
GQ_REQUIRED                       = NO
STAGE_1_ISOLATION_REQUIRED        = YES
CROSS_NOTEBOOK_LEAKAGE_REQUIRED   = 0
MEMBERSHIP_REMOVAL_TEST_INCLUDED  = YES
SOURCE_UPDATE_TEST_INCLUDED       = NO
NOTEBOOK_DELETE_TEST_INCLUDED     = NO
MAX_GRAPH_INDEX_OPERATIONS        = 24   (= PLANNED_GRAPH_INDEX_OPERATIONS; membership-removal adds 0 reindex)
MAX_GRAPH_INDEX_ATTEMPTS          = 48   (24 × 2)
MAX_GD_QUERIES                    = 26
GD_QUERY_BUDGET_BREAKDOWN         = 24 baseline (all classes incl. 3 negatives) + 2 membership-removal re-probe + 0 other = 26 (no headroom)
MAX_VECTOR_QUERY_OPERATIONS       = 26   (24 baseline + 2 removal re-probe; K=3/K=5 are slices of one scored query)
MAX_QUERY_EMBEDDING_OPERATIONS    = 26   (one query-string embedding per vector query)
MAX_CANONICAL_SOURCE_EMBEDDINGS   = 21
MAX_FINAL_ANSWER_CALLS            = 72
FINAL_ANSWER_BUDGET_BREAKDOWN     = 24 queries × 3 arms {QA-V,QA-GD,QA-V+GD} = 72; removal test 0; judge 0; retry 0
JUDGE_MODEL_CALLS                 = 0    (deterministic grading; a judge would need a separate frozen cap)
MEMBERSHIP_REMOVAL_WORKLOAD       = 1 graph-delete + 1 reference-edge delete + 2 GD + 2 vector validation queries + 0 final-answer (all inside the caps above)
RETRY_CAPS                        = index/operation=2, query-technical=1, final-answer=0
FIXTURE_HASH_REQUIRED             = YES
FIXTURE_HASH_VALUE                = TO_BE_FROZEN_AT_PN02B
RETRIEVAL_DECISION_RULE_MECHANICAL = YES  (§17b first-match table: R1 NEW_REQ==0→NO; R2 N_GAIN_NOTEBOOKS_D==0→NO;
                                     R3 N_GAIN_NOTEBOOKS_D≥2 ∧ NEW_REQ>NEW_FP ∧ NEG_RETURN(GD)≤NEG_RETURN(V5)→YES; else INCONCLUSIVE)
QA_DECISION_RULE_MECHANICAL        = YES  (§17c: YES iff ∃ arm SAFE ∧ IMPROVES(strict > on ≥1 of P1/P2/P3) ∧ NO_REGRESS;
                                     NO iff all arms safe ∧ no improvement; else INCONCLUSIVE; SAFE ⇒ S1=S2=S3=0)
MULTIHOP_DECISION_RULE_MECHANICAL  = YES  (§17d: MH≥2→YES; MH==0→NO; MH==1→INCONCLUSIVE)
VAGUE_NEAR_ZERO_LANGUAGE_REMAINS   = NO
VAGUE_APPROX_ALL_LANGUAGE_REMAINS  = NO
STAGE_2_AFTER_STAGE_1_FAILURE_POSSIBLE = NO   (§2/§9b/§42 fail-closed)
PN02A_DECISION                    = C
PN02A_DECISION_NAME               = PN02_DESIGN_FROZEN_AND_IMPLEMENTATION_JUSTIFIED
PN02_IMPLEMENTATION_JUSTIFIED     = YES
PN02_LIVE_AUTHORIZED              = NO
GRAPHRAG_PRODUCTION_INTEGRATION   = NOT_APPROVED
LIGHTRAG_ASK_INTEGRATION          = NOT_APPROVED
GRAPH_RAG_09_JUSTIFIED            = NO
INDEPENDENT_REVIEW                = PASS — design pass (HIGH 0 · MEDIUM 1 resolved · LOW 3 resolved) +
                                    refinement pass §16 A–J (HIGH 0 · MEDIUM 1 resolved [C≠precision>0.5 gloss] · LOW 1 accepted); 0 unresolved
changed files                     = docs/agribank/development/GRAPHRAG_PN02A_SYNTHETIC_PER_NOTEBOOK_QA_EVAL_DESIGN.md (new);
                                    docs/agribank/development/CURRENT_PHASE.md (PN02A row)
```

**No checkpoint** (operator review required — §63). No code, no tests, no fixture JSON, no migration,
no `.env`, zero provider traffic, sidecar not started, `GRAPHRAG_ENABLED` untouched.

---

## 21. STOP

```
GRAPH_RAG_PN02A_SYNTHETIC_PER_NOTEBOOK_ISOLATION_QA_DESIGN_COMPLETE
```
No code · no provider traffic · no live LightRAG · no DB mutation · no PN02B implementation · no Ask
production integration · no RRF · no GraphRAG-09 · no checkpoint.
