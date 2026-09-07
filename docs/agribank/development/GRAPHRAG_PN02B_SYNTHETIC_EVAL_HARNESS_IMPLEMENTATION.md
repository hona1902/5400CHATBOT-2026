# GraphRAG-PN02B — Synthetic Per-Notebook Evaluation Harness (Offline Implementation)

**Status:** OFFLINE IMPLEMENTATION. **No provider traffic, no live LightRAG, no live
`/query/data`, no final-answer LLM, no real internal data, no production Ask/GraphRAG integration,
no RRF, no GraphRAG-09.** Not checkpointed (operator review required). Continuation of the separate
**GraphRAG-PN** track.

**Authoritative baseline (verified from git):** branch `feature/graphrag-lifecycle`, HEAD
`5415667b441a65305ff7cb9f122a439cf772f03c`, annotated tag `graphrag-pn02a-eval-design-approved`
(peels → `5415667…`), working tree CLEAN at start. GraphRAG-08 = **CLOSED/APPROVED**; PN01 =
**APPROVED**; PN02A = **APPROVED** (Decision C). LightRAG pinned `v1.5.6`.

**Authoritative spec:** [`GRAPHRAG_PN02A_SYNTHETIC_PER_NOTEBOOK_QA_EVAL_DESIGN.md`](GRAPHRAG_PN02A_SYNTHETIC_PER_NOTEBOOK_QA_EVAL_DESIGN.md).
PN02B implements the frozen PN02A design without reinterpreting fixture topology, query taxonomy,
ground truth, metrics, the R0–R4 / Q0–Q3 / M0–M3 decision tables, workload caps, or the Stage-1
fail-closed semantics.

---

## 1. What PN02B is (and is not)

PN02B implements the **offline evaluation harness** for the frozen `graphrag_pn02_eval_v1` synthetic
per-notebook fixture: the fixture files, eval-only Python modules, deterministic decision rules,
result schemas, reproducibility tooling, an offline CLI, and the test suite. It **does not** execute
the live/provider-backed benchmark. Every provider-backed seam is an **inert stub** or a **contract**;
the live runner is a future phase (PN02C+), still `PN02_LIVE_AUTHORIZED = NO`.

`PRODUCTION_IMPORTS_EVAL = NO` — the dependency direction is eval → production only, verified by a
test (`test_no_production_import_of_pn02_eval`).

---

## 2. Fixture — `graphrag_pn02_eval_v1`

Files under `tests/fixtures/graphrag_pn02_eval_v1/`:

| File | Contents |
|---|---|
| `corpus.json` | 3 notebooks, 21 canonical Sources (with synthetic text), 24 membership edges |
| `queries.json` | 24 queries with full frozen ground truth |
| `freeze.json` | canonical hash + counts (freeze marker) |

**Frozen counts (validated automatically):**

```
NOTEBOOK_COUNT              = 3
CANONICAL_SOURCE_COUNT     = 21   (18 notebook-unique + 3 shared)
UNIQUE_SOURCE_COUNT        = 18   (A1..A6, B1..B6, C1..C6)
SHARED_SOURCE_COUNT        = 3    (SH_AB, SH_AC, SH_BC)
WORKSPACE_MEMBERSHIP_COUNT = 24   (18 unique edges + 6 shared edges)
SOURCES_PER_NOTEBOOK       = 8    (6 unique + 2 shared)
QUERY_COUNT                = 24   (8 classes × 3 notebooks)
NEGATIVE_COUNT             = 3    MULTIHOP_COUNT = 3
```

**Topology (frozen):** NB_A = {A1..A6, SH_AB, SH_AC}; NB_B = {B1..B6, SH_AB, SH_BC};
NB_C = {C1..C6, SH_AC, SH_BC}. Shared Sources are **one canonical record each** appearing in two
membership edges (not duplicated canonical records).

**Collision design (leakage observability):** the entity *Project Orion* appears in all three
notebooks with a **different** internal code per notebook — **AX-17** (A2), **BX-94** (B2),
**CX-51** (C2). Symmetric negatives form a cycle: NB_A asks a B-only fact (Marisol berth depth
"14 meters"), NB_B asks a C-only fact (Verdant "pH 6.5"), NB_C asks an A-only fact (Aurora "CP-7").
The two `SHARED_SOURCE` queries in NB_A and NB_B both target **SH_AB** (the membership-removal
re-probe pair).

**Identities (task §6):** canonical `notebook_id` (NB_A/B/C), an opaque synthetic `record_id`
(`notebook:gr_pn02_a/b/c`), and a derived `workspace_id = "nb_" + sha256(record_id)[:16]` — never
derived from a display title. Canonical Source keys are logical (`A1`, `SH_AB`), never runtime
`source:` RecordIDs.

**Fixture hash (frozen at PN02B):**

```
PN02_FIXTURE_NAME = graphrag_pn02_eval_v1
PN02_FIXTURE_HASH = 9ce7df742810424d9ef7b7b34962187d9061245f81471f5fb1d7f2608f6899a6
```

Hash method (PN02A §16, **not** the 08 raw-byte hash): SHA-256 over a **canonical, sorted-key JSON
serialization** of `{fixture_version, notebooks, source contents, membership matrix, query list +
ground truth}` — deterministic ordering, no timestamps, no derived routing/workspace ids. A hash test
and a mutation-guard test enforce the freeze (any content change alters the hash).

**Ground-truth schema per query** (`queries.json`): `query_id`, `notebook_id`, `query_class`,
`question`, `answerable`, `required_source_ids`, `optional_support_source_ids`, `forbidden_source_ids`,
`expected_answer_facts`, `forbidden_answer_facts`, `expected_abstention`, `required_citation_source_ids`,
`multi_hop_required`, `rationale` (review-only). The loader validates: REQUIRED/OPTIONAL/citations are
members; FORBIDDEN are non-members; no source is both REQUIRED and FORBIDDEN (or OPTIONAL and
FORBIDDEN); negatives have empty REQUIRED + abstention; multi-hop ⇒ |REQUIRED| ≥ 2; every expected
token is present in the query's required member Sources; every forbidden token is present ONLY in a
non-member Source (so answer-leakage detection is sound).

---

## 3. Module layout (`open_notebook/integrations/graphrag/eval/`, all `*pn02*`, eval-only)

| Module | Role |
|---|---|
| `datasetpn02.py` | loader, full validator, canonical-serialization hash, removal scenario |
| `normalizepn02.py` | normalize retriever output to canonical fixture Source ids; provenance (valid/foreign/malformed); vector = ranked, GD = unordered |
| `schemaspn02.py` | result schemas (V/GD evidence, QA answers, removal probes), `TechnicalOutcome` error taxonomy, `ScienceVerdict`, `ScientificOutputs` |
| `metricspn02.py` | exact Stage-1, incremental-retrieval (§9c), and QA (§10d) formulas + deterministic answer grader |
| `combinepn02.py` | frozen QA-V+GD combination rule (§10b) — no RRF, no rank, no score |
| `stage1pn02.py` | Stage-1 evaluator + HARD gate + `Stage1Authorization` capability (structural fail-closed) |
| `decisionspn02.py` | R0–R4, Q0–Q3, M0–M3 first-match tables + exact 8/8 full-notebook trap |
| `membershippn02.py` | membership-removal evaluator + defense-in-depth backstop (`post_validate`) |
| `workloadpn02.py` | workload ledger + retry policy + cap enforcement (`check_cap`) |
| `manifestpn02.py` | run manifest, routing, attestation, no-unattested-query, version pin, Boundary-B guard |
| `live_seam_pn02.py` | inert Protocols + non-invokable stubs (no HTTP client) |
| `reportpn02.py` | content-safe artifact serializers |
| `evaluatepn02.py` | offline orchestrator with the fail-closed Stage-2 gate |
| `offlinepn02.py` | OFFLINE CLI (`validate` / `hash` / `evaluate-results`) |

---

## 4. Offline interfaces, schemas, decision engine

**Result schemas (§15):** `VectorEvidenceResult` (ranked), `GDEvidenceResult` (unordered — a
`ValueError` is raised if an ordered set is passed, so a fake rank cannot enter), `QAAnswerResult`
(deterministic; `answer_text` is content and is never serialized to a content-safe artifact),
`RemovalProbeResult`. Provenance fields live on `ProvenancePN02`.

**Error taxonomy vs scientific outputs (§56/§57):** `TechnicalOutcome` (COMPLETED / FAILED_BEFORE_INDEX
/ FAILED_WORKSPACE_ATTESTATION / FAILED_INDEXING / FAILED_STAGE1_ISOLATION / FAILED_GD_QUERY /
FAILED_VECTOR_QUERY / FAILED_FINAL_ANSWER) is kept **separate** from `ScienceVerdict`
(YES/NO/INCONCLUSIVE/NOT_EVALUATED). A technical failure never becomes a scientific NO.

**Decision engine (fully mechanical, §17):**
- **Retrieval R0–R4** — first match: R0 isolation NO → NOT_EVALUATED; R1 NEW_REQ==0 → NO; R2
  N_GAIN_NOTEBOOKS_D==0 → NO; R3 (N_GAIN_NOTEBOOKS_D≥2 AND NEW_REQ>NEW_FP AND NEG_RETURN(GD)≤
  NEG_RETURN(V5)) → YES; else R4 INCONCLUSIVE. Criterion C is the **strict count** comparison
  `NEW_REQ > NEW_FP` (never a precision-0.5 threshold).
- **Full-notebook trap (§24)** — `is_full_notebook_return(f)` is `f == 1.0` **exactly**; 7/8 (0.875) is
  not full. A query returning all 8 members never counts as a discriminative gain.
- **QA Q0–Q3** — SAFE(G) requires isolation AND S1=S2=S3=0; IMPROVES strict `>` on ≥1 of P1/P2/P3;
  NO_REGRESS `≥` on all three; POSITIVE = SAFE ∧ IMPROVES ∧ NO_REGRESS. Q1 YES if any arm POSITIVE;
  Q2 NO if all arms SAFE and none improves; else Q3 INCONCLUSIVE.
- **Multi-hop M0–M3** — MH≥2 → YES; MH==0 → NO; MH==1 → INCONCLUSIVE. MH counts distinct notebooks
  where GD completes the ≥2-Source required set V5 alone missed with GD fraction < 1.0.

There are **no fuzzy thresholds/adjectives** in the decision code — only exact integer/rational
comparisons on frozen metrics.

---

## 5. Stage-1 fail-closed enforcement (§20/§21)

`evaluate_stage1` computes the six hard metrics + STALE over all probes (24 baseline + 2 removal
re-probes) and mints a `Stage1Authorization` **only on a full PASS** (all of
`CROSS_NOTEBOOK_LEAKAGE_RATE`, `CROSS_NOTEBOOK_LEAK_QUERY_COUNT`,
`CROSS_NOTEBOOK_LEAK_SOURCE_OCCURRENCES`, `PROVENANCE_FOREIGN`, `PROVENANCE_MALFORMED`,
`CITATION_MEMBERSHIP_INVALID`, `STALE_GRAPH_EVIDENCE_ACCEPTED_AS_VALID` = 0). The authorization is an
**unforgeable capability** (constructing it directly raises `PermissionError`). Every Stage-2 entry
requires it via `require_stage2_authorization`, and the orchestrator computes **no** Stage-2 metric
when Stage 1 fails.

```
STAGE_2_AFTER_STAGE_1_FAILURE_POSSIBLE = NO   (structural, not a warning)
```

Proven by `test_stage1_failure_blocks_stage2_via_orchestrator`,
`test_authorization_cannot_be_forged`, and `test_require_stage2_authorization_blocks_without_auth`.

---

## 6. Membership removal + defense-in-depth (§32–§34)

`membership_removal_scenario` derives the frozen transition (remove **SH_AB** from **NB_A**, retain in
**NB_B**) from the fixture. `evaluate_removal` applies the ON backstop `post_validate` (accepted =
returned ∩ current members) and proves:
- **Postcondition A:** SH_AB is not accepted for NB_A after removal.
- **Postcondition B:** SH_AB remains accepted for NB_B.
- **Defense-in-depth:** even when the simulated graph delete **fails** and GD still returns the stale
  SH_AB, the backstop rejects it → `STALE_GRAPH_EVIDENCE_ACCEPTED_AS_VALID = 0`. A companion test
  shows a *broken* backstop (validating against pre-removal members) would wrongly accept it — proving
  the metric detects the failure it guards. No real deletion call is made.

---

## 7. Workload ledger (§13/§35–§38)

`frozen_ledger()` validates the exact arithmetic and `validate_against_fixture` cross-checks the fixture:

```
PLANNED_GRAPH_INDEX_OPERATIONS = 24   (= membership edges; 18 unique + 6 shared)
MAX_INDEX_ATTEMPTS_PER_OPERATION = 2   MAX_GRAPH_INDEX_ATTEMPTS = 48   (24 × 2)
MEMBERSHIP_REMOVAL_REINDEX_OPS = 0     GRAPH_DELETE_OPERATIONS = 1
MAX_CANONICAL_SOURCE_EMBEDDINGS = 21   (canonical Sources, single-copy in ON store)
MAX_GD_QUERIES = 26                    (24 baseline + 2 removal re-probe)
MAX_VECTOR_QUERY_OPERATIONS = 26       MAX_QUERY_EMBEDDING_OPERATIONS = 26
MAX_FINAL_ANSWER_CALLS = 72            (24 queries × 3 arms; removal 0)
JUDGE_MODEL_CALLS = 0                  FINAL_ANSWER_RETRIES = 0   MAX_QUERY_TECHNICAL_RETRIES = 1
```

`check_cap(name, observed, cap)` is the single primitive a future live runner must call before any
provider-backed op — it raises `WorkloadCapExceeded` so caps cannot be exceeded silently.

---

## 8. Manifests, attestation, version pin, Boundary-B (§39–§45)

- **Routing (§40):** `workspace_id = "nb_" + sha256(record_id)[:16]`; endpoints are non-openable
  placeholders (`eval-null://…`). PN02B never opens them.
- **Attestation (§41/§42):** `attest_workspace` compares supplied observations (workspace/endpoint/
  storage/version) to frozen expectations; `require_attested_before_query` refuses a GD query against
  an unattested workspace. `provider_binding_fingerprint` hashes a **secret-free** config identity.
- **Version pin (§43):** `v1.5.6`; `canonical_version` tolerates the historical `v`-prefix mismatch
  (`1.5.6` ≡ `v1.5.6`), and a wrong version raises `LightRAGVersionError`.
- **Boundary-B (§44):** `validate_boundary_b` refuses any run whose dataset class is not an approved
  synthetic/public/anonymized class, or that sets `real_internal_data_allowed=true` /
  `synthetic_only=false`. `build_run_manifest` calls it.

---

## 9. Network / provider denial (§46/§59)

There is **no HTTP client** in any PN02 module. `live_seam_pn02` exposes only `Protocol`s and inert
stubs that raise `LiveExecutionNotAuthorized` before any side effect (`PN02_LIVE_AUTHORIZED = False`,
`STRUCTURED_EVIDENCE_IMPLEMENTATION_READY = False`). The offline test suite blocks `socket.socket` and
`socket.create_connection` while running the full evaluation and the CLI, so any accidental network
seam fails the test rather than silently succeeding.

`NORMAL_DB_MUTATIONS = 0` — PN02B is pure fixture/evaluator code; it opens no DB connection.

---

## 10. Offline CLI (§58)

`python -m open_notebook.integrations.graphrag.eval.offlinepn02 <verb>`:
- `validate` — validate the frozen fixture (counts + content consistency).
- `hash` — print and verify the fixture hash against the freeze marker.
- `evaluate-results <file> [--out <file>]` — evaluate a **local content-free result JSON** (a future
  live runner's output) through the full offline pipeline and print a content-safe report.

Deliberately **no** `run-benchmark` verb (nothing here can be mistaken for a live run). It starts no
sidecar, calls no provider, queries no LightRAG, and opens no socket.

---

## 11. Tests

Targeted PN02B tests (`tests/test_graphrag_pn02_*.py`): **94 passing**.
- `_fixture` (18) — frozen shape, canonical-vs-duplicated, shared semantics, GT membership,
  forbidden-fact grounding, deterministic hash, freeze + mutation guard.
- `_stage1` (10) — five distinct FAILING isolation scenarios (leakage / foreign / malformed / invalid
  citation membership / stale accepted), all-clean PASS, fail-closed capability.
- `_retrieval` (10) — R0–R4 table, strict C boundary, negatives tie, exact 8/8 trap, 7/8 gain,
  adversarial one-required-many-FP.
- `_qa` (14) — Q0–Q3 table, forbidden-fact-gain-cannot-be-YES (§29), answer leakage (§30), citation
  validity, deterministic grading from fixture GT.
- `_multihop` (8) — M0–M3, single-notebook = INCONCLUSIVE, ≥2 = YES, full-notebook does not count.
- `_membership` (5) — normal removal postconditions + defense-in-depth backstop.
- `_workload` (7) — caps, arithmetic, fixture cross-check, cap enforcement, tamper detection.
- `_manifest` (8) — routing determinism, attestation pass/fail, version pin, Boundary-B, content-free.
- `_combine` (6) — dedup, membership filter, cap, canonical-id order, forbidden exclusion.
- `_offline` (7) — network-denial, CLI verbs, fail-closed orchestration, determinism, inert live seam,
  no-production-import.

Broader GraphRAG regression (flag off): **803 passed, 9 skipped, 0 failed** (the 9 skips are
live-gated). `ruff` clean; `mypy` clean on all 14 PN02 modules.

---

## 11a. Independent review (§68)

An independent adversarial reviewer challenged all sixteen §68 questions A–R against the frozen PN02A
design and the implementation, re-verifying the fixture hash, `validate_fixture`, fact-grounding,
network/import isolation, and workload arithmetic from source. **Verdict: 0 HIGH, 0 MEDIUM, 3 LOW.**
Every A–R question resolved OK (fixture matches PN02A; canonical-not-duplicated; shared-source
semantics; FORBIDDEN validity; Stage-2 unreachable after Stage-1 fail; R0–R4/Q0–Q3/M0–M3 exact; no
fuzzy threshold; exact 8/8 trap; no fake GD rank; stale-source rejected; no reachable network; caps
bounded; technical≠scientific; deterministic canonical hash; no production import; methodology
unchanged).

LOW findings, dispositioned:
- **LOW-1** (doc) — the canonical hash payload includes `fixture_version`/`namespace_tag`/`notebooks`
  beyond §16's literal `{sources, memberships, queries, GT}`. This is a **deliberate, documented,
  stricter** extension (a notebook `record_id`/`theme` edit also breaks the hash), recorded in
  `freeze.json.hash_method` and §2 above. **No code change** (the frozen PN02A doc is not edited
  opportunistically).
- **LOW-2** (fixed) — a Stage-1-PASS run with no QA answers supplied now reports the QA verdict as
  `NOT_EVALUATED` / rule `QA_NOT_SUPPLIED` (a data-absent technical state), never a scientific `Q3`
  INCONCLUSIVE (`evaluatepn02.py`; test `test_qa_not_supplied_is_not_evaluated_not_q3`).
- **LOW-3** (fixed) — the P1/P2 macro-average aggregation is now documented in `metricspn02.py`
  (applied identically to baseline and graph arms, so the strict-`>`/`≥` decisions are unaffected).

## 12. Known limitations

- PN02B does **not** run the benchmark — all evidence is over synthetic result records; the live
  runner (PN02C+) is not implemented and not authorized.
- The QA-V+GD combination rule is frozen (§10b); if a future live result shows it indefensible, the
  arm is **omitted**, never re-tuned.
- Value verdicts on this fixture are expected to land NO/INCONCLUSIVE on a clean run (screening
  design; PN02A §17 statistical-power caveat). That is decision-relevant, not a harness defect.

---

## 13. Future PN02C (NOT implemented here)

The next possible gate is **GraphRAG-PN02C — Synthetic Per-Notebook Live Preflight / Isolation
Provisioning Gate**: three workspace-bound sidecars, routing ownership, attestation, fixture
provisioning, budget calculation, Boundary-B, provider configuration — **without** yet assuming QA
value. PN02B ships the inert contracts (`live_seam_pn02`, `manifestpn02`) that PN02C would implement.
**Not authorized now.**

---

## 14. Retained flags

```
PN02B_STATUS                     = IMPLEMENTING (offline harness complete; awaiting operator review)
PN02_LIVE_AUTHORIZED             = NO
GRAPHRAG_PRODUCTION_INTEGRATION  = NOT_APPROVED
LIGHTRAG_ASK_INTEGRATION         = NOT_APPROVED
GRAPH_RAG_09_JUSTIFIED           = NO
PRODUCTION_IMPORTS_EVAL          = NO
STRUCTURED_EVIDENCE_IMPLEMENTATION_READY = NO
RRF_CANDIDATE_INTERFACE_READY    = NO
```

**No checkpoint** (operator review required). No `git add/commit/tag/push`. No provider traffic,
sidecar not started, `GRAPHRAG_ENABLED` untouched, no `.env` change, no migration.
