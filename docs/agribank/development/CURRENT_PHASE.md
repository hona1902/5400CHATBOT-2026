# Current Phase — GraphRAG programme status

| Phase | Status | Deliverable |
|---|---|---|
| Phase 0 — Governance & tooling baseline | ✅ **COMPLETE** | `AGRIBANK.md`, `DECISIONS.md`, Graphify baseline |
| GraphRAG-01 — Forensic & architecture decision | ✅ **COMPLETE** | [`../architecture/GRAPHRAG_FORENSIC.md`](../architecture/GRAPHRAG_FORENSIC.md) rev-2 · [`GRAPHRAG_DECISION.md`](GRAPHRAG_DECISION.md) (AGR-005) |
| GraphRAG-02 — Isolated LightRAG PoC | ✅ **COMPLETE** — accepted 2026-08-27 | [`../architecture/GRAPHRAG_POC.md`](../architecture/GRAPHRAG_POC.md) |
| GraphRAG-03 — Indexing lifecycle & durable deletion | 🔨 **IN PROGRESS** — forensic approved 2026-08-28; slices 03-A **COMPLETE** · 03-B **COMPLETE** · 03-C **COMPLETE** (03-D/03-E remain) | [`../architecture/GRAPHRAG_LIFECYCLE_FORENSIC.md`](../architecture/GRAPHRAG_LIFECYCLE_FORENSIC.md) · [`GRAPHRAG_03A_INDEXING.md`](GRAPHRAG_03A_INDEXING.md) · [`GRAPHRAG_03B_DURABLE_DELETE.md`](GRAPHRAG_03B_DURABLE_DELETE.md) · [`GRAPHRAG_03C_TOMBSTONE_DRAIN.md`](GRAPHRAG_03C_TOMBSTONE_DRAIN.md) |
| GraphRAG-04 → 07 | ⬜ Not started, not approved | — |

**Branch:** `feature/graphrag-lifecycle` (from `bc5b413`) · **LightRAG pinned:** `v1.5.6`

### GraphRAG-03 slice status
| Slice | Status |
|---|---|
| 03-A INDEX/REINDEX | ✅ **COMPLETE** — approved 2026-08-28 (no migration; count 46) |
| 03-B Durable deletion state + DB event (migration) | ✅ **COMPLETE / APPROVED** — signed off 2026-08-28. Migration **24** (`graphrag_deletion` SCHEMAFULL table + separate `graphrag_source_delete` event, Option A / flag-independent) with a per-arm **`arm_id`** (`rand::uuid()`) fence token closing the ABA re-arm race (verified on SurrealDB v2.6.5); read-only tombstone helper; 28 property tests. Migration count 46 → **48**. No HTTP/egress. Karpathy CLEAN · Codex APPROVE. See [`GRAPHRAG_03B_DURABLE_DELETE.md`](GRAPHRAG_03B_DURABLE_DELETE.md). |
| 03-C Tombstone draining / retry / idempotent delete | ✅ **COMPLETE / APPROVED** — signed off 2026-08-30. Migration **25** (`next_attempt_at` fair-drain field + event OVERWRITE; migration 24 frozen). Confirmed-absence probe via `POST /documents/paginated` single-response snapshot (`deletion_started` ≠ absence); `arm_id` CAS resolve/defer (stale CAS = 0 rows); live-source convergence (flag-OFF→absent, no Boundary-B egress; live-empty→absent; live-current→CURRENT canonical); small cancellable periodic wake-up in the FastAPI lifespan (startup kick + best-effort `Source.delete` wake-up; no new scheduler); durable tombstone is the work source-of-truth, re-drive independent of the crash-prone command queue; bounded due-set traversal (no OFFSET). 03C 42 tests (2 live-LightRAG against pinned v1.5.6) + 288 GraphRAG regression + full backend 936 pass / 2 skipped / 5 pre-existing baseline; ruff+mypy clean. Migration count 48 → **50**. Karpathy CLEAN · Codex A APPROVE · Codex B APPROVE. **Accepted limitation:** the >200-doc single-response absence ceiling stays `UNKNOWN`/pending (remote delete still re-driven; tombstone never falsely resolved; scalable proof deferred to 03D). See [`GRAPHRAG_03C_TOMBSTONE_DRAIN.md`](GRAPHRAG_03C_TOMBSTONE_DRAIN.md). |
| 03-D RECONCILE | ⬜ **NOT STARTED** |
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

**GraphRAG-03C** — HTTP-capable tombstone draining + retry + idempotent remote delete (migration 25) — ✅ **COMPLETE / APPROVED 2026-08-30** (Karpathy CLEAN, Codex A & B APPROVE, no unresolved actionable HIGH). Next is **GraphRAG-03D** (RECONCILE — defense-in-depth: scalable paginated diff to prove absence above the single-page ceiling + orphan purge, missing re-index, stuck-delete re-drive) — **NOT STARTED**, requires its own written go-ahead. 03C does **not** depend on 03D for correctness; the durable tombstone + `arm_id` CAS + periodic re-drive is the primary deletion-correctness path. 03-E (REBUILD) follows, gated on its own review.
