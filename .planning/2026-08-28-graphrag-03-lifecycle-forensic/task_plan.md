# Task Plan — GraphRAG-03 (stage 1): Lifecycle Forensic & Architecture Design

**Date:** 2026-08-28 · **Baseline commit:** `bc5b413` (GraphRAG-02 checkpoint) · **Branch (actual):** `feature/graphrag-lightrag`

> **Branch discrepancy:** the session brief names `feature/graphrag-lifecycle`. That branch does not exist in this checkout; HEAD is `bc5b413` on `feature/graphrag-lightrag`, which *is* the stated GraphRAG-02 checkpoint. Forensic proceeded on the existing branch. Creating/switching branches is left to the user.

## Scope — FORENSIC + DESIGN ONLY

**No implementation.** No `graphrag_index_source`, no delete/reindex command, no migration, no change to `Source.save()`, `save_source()`, `vector_search()`, Ask, Chat, or frontend. No LightRAG install/change. No real internal data. No automatic commit.

Deliverable: `docs/agribank/architecture/GRAPHRAG_LIFECYCLE_FORENSIC.md` with 22 required sections.

## Method

1. Recover state from Git + repository docs (AGENTS.md, AGRIBANK.md, GRAPHRAG_DECISION.md, GRAPHRAG_FORENSIC.md, GRAPHRAG_POC.md, CURRENT_PHASE.md).
2. Trace create/index, update/reindex, and every delete path in current source — not from memory or prior planning files.
3. Establish command-queue durability semantics by reading the installed `surreal_commands` package, not its docs.
4. Verify LightRAG delete/list/status API against **pinned v1.5.6 source**, never guessed.
5. Produce failure matrix, candidate durable-delete designs, and a migration/no-migration determination grounded in the above.

## Verification for this stage

Documentation-only change. `git status --short` must show only docs/planning files.

## Status

- [x] Context recovery from Git + docs
- [x] A — create/index path traced
- [x] B — update/reindex semantics determined
- [x] C — all delete paths traced
- [x] D — durable-delete mechanisms evaluated
- [x] E — rebuild design
- [x] F — reconciliation design (verified against pinned LightRAG source)
- [x] G — identity contract
- [x] H — notebook-reference / eligibility semantics
- [x] I — data-egress boundaries restated
- [x] Failure matrix (21 rows)
- [x] Upstream coupling analysis
- [x] Migration decision
- [x] Forensic document written
- [ ] **User approval — BLOCKING before any implementation**
