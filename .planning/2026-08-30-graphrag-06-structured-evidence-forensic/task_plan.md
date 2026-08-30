# GraphRAG-06 — Structured Evidence Surface (FORENSIC / DESIGN GATE)

**Goal:** Decide whether Open Notebook should introduce a structured GraphRAG evidence seam
based on pinned LightRAG v1.5.6 `/query/data` (structured context output) instead of the
current answer-generating `client.query()` → `/query`. **Documentation/design ONLY.** No
production code, no tests, no retrieval change, no integration, no provider calls, no DB or
LightRAG-storage mutation. `OPEN_NOTEBOOK_GRAPHRAG_ENABLED` stays false; sidecar stays stopped.

## Frozen inputs (do NOT reopen or weaken)
- GraphRAG-04 approved: commit `cb86a06…`, tag `graphrag-04-approved`.
- GraphRAG-05 forensic approved: commit `833ec59…`, tag `graphrag-05-forensic-approved`.
- RRF_CANDIDATE_INTERFACE_READY = NO; GRAPH_CANDIDATE_IMPLEMENTATION_READY = NO.
- No valid rank / no valid score on any pinned v1.5.6 HTTP surface (05 §9).
- Pinned LightRAG: HKUDS v1.5.6, commit b33c6b0.

## Pinned source
Read-only shallow clone of tag v1.5.6 (commit `b33c6b0812cddf39206e48a9810112e51f025274`,
`__version__="1.5.6"`, `__api_version__="0328"`) into out-of-repo scratchpad
`…/scratchpad/lightrag-v1.5.6`. Verified commit hash matches. Public code; not committed;
not a provider call; not internal-data egress. Same method GraphRAG-05 used and approved.

## Phases
### Phase 1 — Setup & frozen-input intake — **Status:** complete
Read 04/05 docs, current client, verify pinned clone. Confirm current path has ONLY `query()`,
no `query_data()`.

### Phase 2 — Forensic Target A: current client.query() → /query path — **Status:** complete
Traced. Only query() wired (no query_data). Forces LLM answer (aquery_llm); ON consumes
response+references, discards answer+structured data; references built BEFORE generation;
generation unnecessary for provenance; conflates retrieval+generation.

### Phase 3 — Forensic Target B: /query/data end-to-end — **Status:** complete
Traced POST /query/data:1013 → query_data → aquery_data(only_need_context) → kg_query →
convert_to_user_format → QueryDataResponse. Schema table done (doc §10).

### Phase 4 — Answer-generation separation (§7) — **Status:** complete
Classification **B**. Keyword-extraction LLM (operate.py:4234→4618) runs before the
only_need_context gate (4275); only final-answer LLM (4301+) is skipped. Cache write iff
enable_llm_cache.

### Phase 5 — Parity (§8) + cache/corpus mutation (§15,§16) — **Status:** complete
PARITY=YES (identical kg_query). CORPUS_MUTATION=NO; CACHE_MUTATION=CONFIG_DEPENDENT;
side-effect profile READ_MOSTLY.

### Phase 6 — Provenance (§9), data minimization (§11), egress matrix (§12,§32) — **Status:** complete
STRONG(chunk·ref)/PARTIAL(entity·relation). chunks[].content=RAW_SOURCE_TEXT. Egress matrix +
schema table written. MINIMIZATION_BETTER=PARTIAL.

### Phase 7 — Failure (§13), auth (§14), contract (§17–21), options (§23) — **Status:** complete
Failure isolation cleaner; same X-API-Key auth; unranked GraphSourceEvidence contract; A/B/C/D;
PREFERRED=B; future scope/test/eval; role UNRANKED_EVIDENCE_ENGINE+PROVENANCE_ENRICHER+
CONTEXT_EXPANDER.

### Phase 8 — Deliverable doc + CURRENT_PHASE + reviews (§36) + git audit — **Status:** complete
GRAPHRAG_06_STRUCTURED_EVIDENCE_FORENSIC.md + CURRENT_PHASE row written; reviews A–F held.
Git audit pending final run.

## Next Step
Run git status/diff audit (§37); confirm only docs/planning changed; deliver final report.
Do NOT commit/push/tag; do NOT implement.

## Decisions Made
- Re-cloned pinned v1.5.6 (retained 05 scratch clone was gone; local /d/Project Web/LightRAG is
  v1.5.0 and MUST NOT be used as v1.5.6 evidence). Verified commit b33c6b0.

## Errors Encountered
| Error | Attempt | Resolution |
|-------|---------|------------|
| broad `find` hit .uv-cache, 120s timeout | 1 | scoped to open_notebook/api/commands; killed bg task |
