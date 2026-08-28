# Current Phase — GraphRAG programme status

| Phase | Status | Deliverable |
|---|---|---|
| Phase 0 — Governance & tooling baseline | ✅ **COMPLETE** | `AGRIBANK.md`, `DECISIONS.md`, Graphify baseline |
| GraphRAG-01 — Forensic & architecture decision | ✅ **COMPLETE** | [`../architecture/GRAPHRAG_FORENSIC.md`](../architecture/GRAPHRAG_FORENSIC.md) rev-2 · [`GRAPHRAG_DECISION.md`](GRAPHRAG_DECISION.md) (AGR-005) |
| GraphRAG-02 — Isolated LightRAG PoC | ✅ **COMPLETE** — accepted 2026-08-27 | [`../architecture/GRAPHRAG_POC.md`](../architecture/GRAPHRAG_POC.md) |
| GraphRAG-03 — Indexing lifecycle & durable deletion | 🔨 **IN PROGRESS** — forensic approved 2026-08-28; slice 03-A **COMPLETE** | [`../architecture/GRAPHRAG_LIFECYCLE_FORENSIC.md`](../architecture/GRAPHRAG_LIFECYCLE_FORENSIC.md) · [`GRAPHRAG_03A_INDEXING.md`](GRAPHRAG_03A_INDEXING.md) |
| GraphRAG-04 → 07 | ⬜ Not started, not approved | — |

**Branch:** `feature/graphrag-lifecycle` (from `bc5b413`) · **LightRAG pinned:** `v1.5.6`

### GraphRAG-03 slice status
| Slice | Status |
|---|---|
| 03-A INDEX/REINDEX | ✅ **COMPLETE** — approved 2026-08-28 (no migration; count 46) |
| 03-B Durable deletion state + DB event (migration) | ⬜ **NOT STARTED** — approved in principle; requires separate go-ahead |
| 03-C Tombstone draining / retry / idempotent delete | ⬜ Not started |
| 03-D RECONCILE | ⬜ Not started |
| 03-E REBUILD | ⬜ Not started |

> **Boundary B (sidecar → LLM/embedding provider) is NOT APPROVED for internal data.** Real internal data is **prohibited** in every phase until a separate egress decision exists. Synthetic, public, or anonymized content only.

---

## GraphRAG-02 — completed scope

An isolated, flag-gated LightRAG integration boundary with experimental diagnostic endpoints. Default **OFF**; not wired into source ingestion, Ask, or Chat.

Delivered: `open_notebook/integrations/graphrag/` (config · models · client · service) · `api/routers/graphrag.py` (+2 lines in `api/main.py`) · three test modules · dev-only sidecar compose profile · frozen contract documentation.

### Acceptance criteria (§21.12) — all met

- [x] LightRAG boundary fully isolated; never imported or vendored
- [x] Flag defaults OFF; baseline unchanged when off
- [x] Open Notebook boots and operates without LightRAG present
- [x] Synthetic document indexable via the PoC path
- [x] Experimental graph query works
- [x] Failures normalized; no `httpx` exception escapes
- [x] Metadata allowlist verified at field **and value** level
- [x] No real internal data used
- [x] No existing RAG path modified
- [x] No source ingestion path modified
- [x] No DB migration (count unchanged at 46)
- [x] Tests pass
- [x] Karpathy diff clean (2 passes, 5 findings resolved)
- [x] Codex review: no unresolved HIGH (2 passes, 4 findings resolved)
- [x] User sign-off — 2026-08-27

### Verification record

```text
Date:    2026-08-27
Branch:  feature/graphrag-lightrag
LightRAG pinned: v1.5.6 (contract read from router source, not docs)

GraphRAG tests:  189 passed
Backend pytest:  839 passed, 5 failed
                 (all 5 proven pre-existing: stashing every GraphRAG file
                  reproduced them at 5 failed / 50 passed)
Backend ruff:    All checks passed
Backend mypy:    Success — no issues
Frontend:        untouched (verified by test)
Migrations:      unchanged (46 files)

Karpathy diff:   clean — 5 findings across 2 passes, all resolved
Codex review:    no unresolved HIGH — 4 findings across 2 passes, all resolved
RecordID:        canonical round trip verified; numeric vs numeric-string
                 identity preserved; LightRAG file_source compatibility verified
Commit:          approved for commit; nothing committed automatically
```

### Known baseline failures (not GraphRAG-related, not fixed here)

Five backend tests fail independently of this work — four Windows-environment artifacts (symlink privilege, path separators, `file://` drive letters) and one genuine logic bug in proxy env-var case merging. Full evidence and per-test detail in [`../architecture/GRAPHRAG_POC.md`](../architecture/GRAPHRAG_POC.md) § Known Baseline Test Failures.

---

## Blockers for later phases

| Blocker | Blocks |
|---|---|
| **Boundary B not approved** | Any real internal data, in every phase |
| Deletion-durability mechanism undecided | GraphRAG-03; an outbox would need its own migration approval |
| No synthetic evaluation corpus | GraphRAG-04 — without it, later phases optimize an unmeasured system |
| Is notebook-scoped retrieval needed today? | Whether `RetrievalScope` ships used or reserved |

## Next phase

**GraphRAG-03B** — durable deletion state + `source_delete`-style DB event (the tombstone migration). Approved in principle by the 03-A approval, but **not started** and **requires a separate written go-ahead** before any code or migration. 03-C (tombstone draining), 03-D (RECONCILE), and 03-E (REBUILD) follow, each gated on its own review.
