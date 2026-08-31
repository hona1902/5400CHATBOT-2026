# GraphRAG-07 — Structured Evidence Adapter Contract & Data-Minimization Design Gate

**Goal:** Design the EXACT Open Notebook-owned normalized contract (anti-corruption layer)
that would sit between pinned LightRAG v1.5.6 `/query/data` and future GraphRAG consumers.
**FORENSIC / ARCHITECTURE / CONTRACT DESIGN ONLY.** Not an implementation phase. No
production code, no tests, no `/query/data` integration, no GraphRAG-client change, no
retrieval-semantics change, no RRF/fusion/rerank, no migration, no DB/Source/LightRAG-storage
mutation, no provider traffic. `OPEN_NOTEBOOK_GRAPHRAG_ENABLED` stays false; sidecar stays
stopped. Do NOT start GraphRAG-08.

## Frozen inputs (do NOT reopen or weaken)
- GraphRAG-04 approved (`cb86a06`, tag `graphrag-04-approved`):
  `RRF_CANDIDATE_INTERFACE_READY=NO`, `HYBRID_VALUE_EVIDENCED=INCONCLUSIVE`.
- GraphRAG-05 forensic approved (`833ec59`, tag `graphrag-05-forensic-approved`):
  `LIGHTRAG_DIRECT_SCORED_SOURCE_SURFACE=NO`; no valid rank/score on any HTTP surface;
  `SOURCE_PROVENANCE=STRONG(chunk)/PARTIAL(KG)`; `GRAPH_CANDIDATE_IMPLEMENTATION_READY=NO`.
- GraphRAG-06 forensic approved (`d7e6a5b`, tag `graphrag-06-forensic-approved`):
  `QUERY_DATA_AVAILABLE=YES`, `AVOIDS_FINAL_ANSWER=YES`, `OTHER_LLM_CALLS_REMAIN=YES`,
  `RETRIEVAL_SEMANTICS_PARITY=YES`, provenance STRONG(chunk/reference)/PARTIAL(entity/relation),
  `RANK=NO`/`SCORE=NO`, `CORPUS_MUTATION=NO`, `CACHE_MUTATION=CONFIG_DEPENDENT`,
  `chunks[].content=RAW_SOURCE_TEXT`, `PREFERRED_ARCHITECTURE=B` (`/query/data` seam),
  `GRAPH_RAG_ROLE=UNRANKED_EVIDENCE_ENGINE+PROVENANCE_ENRICHER+CONTEXT_EXPANDER`,
  `STRUCTURED_EVIDENCE_IMPLEMENTATION_READY=NO`.

## Pinned source of truth
Pinned LightRAG `v1.5.6`, commit `b33c6b0812cddf39206e48a9810112e51f025274`
(`__version__=1.5.6`, `__api_version__=0328`). GraphRAG-07 designs a contract; it does NOT
need a new live probe — the `/query/data` schema, field classification, provenance quality,
and no-score/no-rank facts are FROZEN by GraphRAG-05/06 with `file:line` citations into the
pinned tree, and §2 forbids reopening them. No re-clone, no sidecar start.

## Starting checkpoint
Branch `feature/graphrag-lifecycle`; HEAD `d7e6a5b` (== tag `graphrag-06-forensic-approved`);
clean worktree. Confirmed before any doc edit.

## Documentation reconciliation (task §1)
CURRENT_PHASE.md GraphRAG-05 row still read "awaiting review". Historical reality: GraphRAG-05
was forensic-approved and checkpointed at `833ec59` / tag `graphrag-05-forensic-approved`.
Correct ONLY that status drift. Do NOT amend the 05 commit or its findings.

## Phases
### Phase 1 — Setup & frozen-input intake — Status: complete
Confirm branch/HEAD/clean; read AGENTS/CLAUDE/AGRIBANK/CURRENT_PHASE; read 04/05/06 approved
docs; read current client.py/models.py/config.py for existing conventions (error taxonomy,
record-id validators, version constant, content-free logging). No re-clone required.

### Phase 2 — Anti-corruption layer decision (task §6) — Status: complete
Decide `RAW_LIGHTRAG_SCHEMA_EXPOSED_UPSTREAM` / `NORMALIZED_EVIDENCE_ADAPTER_REQUIRED`.

### Phase 3 — Raw input contract + data classification (task §7/§8) — Status: complete
Document exact `/query/data` fields; classify each; re-verify content-bearing fields.

### Phase 4 — Content / persistence / provenance policy (task §9–§13) — Status: complete
Raw-content retention; persistence; canonical ownership + owner; foreign/malformed/unknown/
ambiguous policy; provenance-quality vocabulary.

### Phase 5 — Normalized contract design (task §14–§23) — Status: complete
Source-level contract; no-score/no-rank invariants; evidence types; entity/relation payload;
one- vs two-level; dedup; ordering; empty/negative; full result; diagnostics.

### Phase 6 — Robustness & boundaries (task §24–§36) — Status: complete
Error taxonomy; failure isolation; schema-version shield; raw disposal; logging/redaction;
privacy/egress; cancellation/timeout; concurrency; workspace/auth; module boundary; citation
ownership; threat model; payload limits.

### Phase 7 — Options, future gates, diagram, tables, decisions (task §37–§45) — Status: complete
Acceptance gates; future test matrix; future evaluation; options A–E + preferred; diagram;
contract table; policy table; explicit decision values.

### Phase 8 — Adversarial design review (task §46) — Status: complete
Reviews A–H (minimization, provenance safety, misuse resistance, vendor coupling, schema
drift, logging/security, contract minimality, phase boundary). Doc/design fixes only.

### Phase 9 — Reconciliation + git audit + report (task §1/§47/§49/§50) — Status: complete
Fix 05 status drift; add 07 row; update `.active_plan`; git audit (docs/planning only);
final report. No stage, no commit, no push, no tag.

## Absolute boundaries
Documentation/planning only. Any need for a live runtime probe = STOP and request approval.
