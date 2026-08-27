# Task Plan: Phase GraphRAG-01 — Forensic + Architecture Proposal (NO CODE)

## Goal
Produce `docs/agribank/architecture/GRAPHRAG_FORENSIC.md`: a forensic map of Open Notebook's
source lifecycle + retrieval pipeline, and a proposed LightRAG sidecar GraphRAG architecture
(fail-open, additive, non-invasive). No implementation, no migration, no dependency, no source edits.

## Hard constraints (from user + AGRIBANK.md)
- No implementation changes, no migration, no new dependency, no schema change, no frontend change.
- Do not modify/refactor existing code. Source + tests are source of truth.
- No real data/secrets in any artifact.
- LightRAG = independent sidecar; do NOT vendor its source; ON must work if LightRAG is down.
- Do NOT change existing RAG or API contracts.

## Next Step
**BLOCKED — awaiting user approval of `docs/agribank/development/GRAPHRAG_DECISION.md` (AGR-005).**
GraphRAG-02 must not begin until the §20 approval table is signed off. Boundary B (sidecar → provider egress) additionally blocks any real-data use.

## Current Phase
Phase 6 complete (adversarial review reconciled + decision record written). No code written in any phase.

## Phases

### Phase 1: Tooling & governance baseline
- [x] Read CLAUDE.md / AGENTS.md / AGRIBANK.md
- [x] Confirm Superpowers + Planning-with-Files + Graphify installed
- [x] Init planning session
- **Status:** complete

### Phase 2: Forensic — source ingestion lifecycle
- [x] API endpoint receiving source (routers)
- [x] Source service layer
- [x] Domain model Source (canonical text storage)
- [x] Chunking + embedding
- [x] Background command / worker execution
- [x] Storage (SurrealDB schema)
- **Status:** complete

### Phase 3: Forensic — retrieval + Ask/Chat + citation
- [x] Vector/text search entrypoints
- [x] Retrieval pipeline
- [x] Ask graph + Chat graph
- [x] Citation generation
- [x] notebook↔source relationship
- **Status:** complete

### Phase 4: Integration analysis (Graphify blast radius)
- [x] Candidate indexing insertion point (no ingestion slowdown)
- [x] Candidate hybrid retrieval insertion point (no vector-RAG break)
- [x] Existing abstractions reusable for GraphRAGClient/Service/Indexer/HybridRetriever
- [x] Fail-open mechanism
- [x] Metadata contract + provenance for citation
- **Status:** complete

### Phase 5: Write report + design proposal
- [x] Write docs/agribank/architecture/GRAPHRAG_FORENSIC.md (17 required sections)
- [x] >= 2 architecture options, recommended one
- [x] Update findings.md + progress.md
- [x] Present conclusions to user; STOP for approval
- **Status:** complete

## Decisions Made
| Decision | Rationale |
|----------|-----------|
| Reuse existing Graphify graph (.graphify built 2026-08-27) rather than rebuild | Graph is same-day fresh vs current checkout; policy allows advisory use, verified against source |
| Report only, no code, stop after proposal | Explicit user instruction + AGRIBANK phase discipline |

## Errors Encountered
| Error | Resolution |
|-------|------------|
