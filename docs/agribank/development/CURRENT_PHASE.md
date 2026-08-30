# Current Phase — GraphRAG programme status

| Phase | Status | Deliverable |
|---|---|---|
| Phase 0 — Governance & tooling baseline | ✅ **COMPLETE** | `AGRIBANK.md`, `DECISIONS.md`, Graphify baseline |
| GraphRAG-01 — Forensic & architecture decision | ✅ **COMPLETE** | [`../architecture/GRAPHRAG_FORENSIC.md`](../architecture/GRAPHRAG_FORENSIC.md) rev-2 · [`GRAPHRAG_DECISION.md`](GRAPHRAG_DECISION.md) (AGR-005) |
| GraphRAG-02 — Isolated LightRAG PoC | ✅ **COMPLETE** — accepted 2026-08-27 | [`../architecture/GRAPHRAG_POC.md`](../architecture/GRAPHRAG_POC.md) |
| GraphRAG-03 — Indexing lifecycle & durable deletion | ✅ **COMPLETE** — forensic approved 2026-08-28; slices 03-A **COMPLETE** · 03-B **COMPLETE** · 03-C **COMPLETE** · 03-D **COMPLETE** · 03-E **COMPLETE / APPROVED** | [`../architecture/GRAPHRAG_LIFECYCLE_FORENSIC.md`](../architecture/GRAPHRAG_LIFECYCLE_FORENSIC.md) · [`GRAPHRAG_03A_INDEXING.md`](GRAPHRAG_03A_INDEXING.md) · [`GRAPHRAG_03B_DURABLE_DELETE.md`](GRAPHRAG_03B_DURABLE_DELETE.md) · [`GRAPHRAG_03C_TOMBSTONE_DRAIN.md`](GRAPHRAG_03C_TOMBSTONE_DRAIN.md) · [`GRAPHRAG_03D_RECONCILE.md`](GRAPHRAG_03D_RECONCILE.md) · [`GRAPHRAG_03E_REBUILD.md`](GRAPHRAG_03E_REBUILD.md) |
| GraphRAG-04 — Synthetic retrieval evaluation / quality baseline | ✅ **COMPLETE / APPROVED** — signed off 2026-08-30. Frozen synthetic benchmark v1 (14 sources / 28 queries / 6 classes / DEV 17 · HOLDOUT 11) + eval-only harness; **live synthetic baseline EXECUTED** (OpenRouter `text-embedding-3-small` + `gpt-4o-mini` through LightRAG v1.5.6; both probe gates PASS; cleanup+restore verified). **RRF_CANDIDATE_INTERFACE_READY = NO** (LightRAG refs unordered/no score, live-confirmed `ordered=False`); **HYBRID_VALUE_EVIDENCED = INCONCLUSIVE** (graph returns a broad unordered candidate surface ≈11/14 sources per query — incl. distractor/negative — so 1.0 source-hit/recall is coverage, not precision). Karpathy CLEAN · Codex A/B/C resolved (no unresolved actionable HIGH). GraphRAG-04 mypy surface **clean**; full-project `mypy .` still reports **91 pre-existing errors in untouched GraphRAG-03 test files** (04 adds zero). **NO migration** (count 50). | [`GRAPHRAG_04_EVALUATION.md`](GRAPHRAG_04_EVALUATION.md) |
| GraphRAG-05 — Ranked graph candidate surface (FORENSIC / DESIGN GATE) | 🟨 **FORENSIC/DESIGN-ONLY COMPLETE — awaiting review; not an implementation phase.** Traced the full pinned LightRAG **v1.5.6** (commit `b33c6b0`) query pipeline `/query → aquery → kg_query → _build_query_context → convert_to_user_format`. Finding: every genuine query-relevance score (entity/relation/chunk embedding **cosine**, cross-encoder **rerank_score**) is **computed then dropped before any HTTP surface**; the only numerics that reach the API are **structural** (relationship `weight`, node degree) and **frequency** (`reference_id`). Hybrid final order is a **round-robin interleave** (not a rank). New vs 04: the **`/query/data`** structured endpoint (entities/relationships/chunks/references, no LLM answer) gives **richer provenance but still no relevance score**. Decisions: `LIGHTRAG_DIRECT_SCORED_SOURCE_SURFACE = NO`; `LOWER_LEVEL_QUERY_SCORE_AVAILABLE = PARTIAL` (internal-only, unexposed); `SOURCE_PROVENANCE_FOR_SCORED_EVIDENCE = STRONG` (chunk) / PARTIAL (kg); `SOURCE_LEVEL_AGGREGATION_DEFENSIBLE = REQUIRES_EXPERIMENT`; `GRAPH_NATIVE_RANKING_SIGNAL = NO`; `ABSTENTION_SIGNAL_AVAILABLE = UNCLEAR`; `GRAPH_CANDIDATE_CONTRACT_DESIGNABLE = YES` (unranked) / NO (ranked); `GRAPH_CANDIDATE_IMPLEMENTATION_READY = NO`; **`RRF_CANDIDATE_INTERFACE_READY = NO`**. Preferred **Option C** — expose GraphRAG as an **unranked, provenance-strong evidence set** (`GraphSourceCandidate`, no score field); Option D (surface a real score) requires modifying pinned LightRAG and is out of scope. **No production/test/migration/API/frontend code; zero provider traffic; no DB / LightRAG-storage mutation; sidecar not started; `GRAPHRAG_ENABLED` stayed false; no migration (count 50).** | [`GRAPHRAG_05_RANKED_CANDIDATE_FORENSIC.md`](GRAPHRAG_05_RANKED_CANDIDATE_FORENSIC.md) |
| GraphRAG-06 → 07 | ⬜ Not started, not approved | — |

**Branch:** `feature/graphrag-lifecycle` (from `bc5b413`) · **LightRAG pinned:** `v1.5.6`

### GraphRAG-03 slice status
| Slice | Status |
|---|---|
| 03-A INDEX/REINDEX | ✅ **COMPLETE** — approved 2026-08-28 (no migration; count 46) |
| 03-B Durable deletion state + DB event (migration) | ✅ **COMPLETE / APPROVED** — signed off 2026-08-28. Migration **24** (`graphrag_deletion` SCHEMAFULL table + separate `graphrag_source_delete` event, Option A / flag-independent) with a per-arm **`arm_id`** (`rand::uuid()`) fence token closing the ABA re-arm race (verified on SurrealDB v2.6.5); read-only tombstone helper; 28 property tests. Migration count 46 → **48**. No HTTP/egress. Karpathy CLEAN · Codex APPROVE. See [`GRAPHRAG_03B_DURABLE_DELETE.md`](GRAPHRAG_03B_DURABLE_DELETE.md). |
| 03-C Tombstone draining / retry / idempotent delete | ✅ **COMPLETE / APPROVED** — signed off 2026-08-30. Migration **25** (`next_attempt_at` fair-drain field + event OVERWRITE; migration 24 frozen). Confirmed-absence probe via `POST /documents/paginated` single-response snapshot (`deletion_started` ≠ absence); `arm_id` CAS resolve/defer (stale CAS = 0 rows); live-source convergence (flag-OFF→absent, no Boundary-B egress; live-empty→absent; live-current→CURRENT canonical); small cancellable periodic wake-up in the FastAPI lifespan (startup kick + best-effort `Source.delete` wake-up; no new scheduler); durable tombstone is the work source-of-truth, re-drive independent of the crash-prone command queue; bounded due-set traversal (no OFFSET). 03C 42 tests (2 live-LightRAG against pinned v1.5.6) + 288 GraphRAG regression + full backend 936 pass / 2 skipped / 5 pre-existing baseline; ruff+mypy clean. Migration count 48 → **50**. Karpathy CLEAN · Codex A APPROVE · Codex B APPROVE. **Accepted limitation:** the >200-doc single-response absence ceiling stays `UNKNOWN`/pending (remote delete still re-driven; tombstone never falsely resolved; scalable proof deferred to 03D). See [`GRAPHRAG_03C_TOMBSTONE_DRAIN.md`](GRAPHRAG_03C_TOMBSTONE_DRAIN.md). |
| 03-D RECONCILE | ✅ **COMPLETE / APPROVED** — signed off 2026-08-30. Defense-in-depth reconcile: bounded streaming remote sweep + STRONG ownership proof (`file_path` lossless `source` id AND `compute_doc_id`==doc.id); owned orphan / live-empty / flag-OFF → **arm durable 03B/03C tombstone** (new `deletion.arm_orphan_deletion`, DB-generated `arm_id`, no re-arm churn) drained by 03C; authoritative-missing (single-response snapshot, flag ON) → `source_id`-only 03A enqueue; FOREIGN/UNKNOWN/PRESENT_UNVERIFIED report-only. AUDIT default, REPAIR opt-in; `graphrag_reconcile` surreal-command (**no scheduler, no new API**). **NO migration 26** (24/25 frozen; count stays 50). **Forensic GATE (task §9/§35):** pinned v1.5.6 cannot prove scalable authoritative absence (offset paging ≤200, no by-id lookup, no corpus cursor, no `content_hash`) → 03D does **not** resolve tombstones and does **not** claim the >200 ceiling solved (documented blocker; needs upstream by-id/keyset). 54 new tests (mock + live SurrealDB v2.6.5 + live LightRAG v1.5.6); GraphRAG regression 338 pass / 1 skip; full backend 983 pass / 6 skip / 5 pre-existing baseline failures unchanged; ruff+mypy clean. **Karpathy CLEAN · Codex A APPROVE · Codex B resolved · Codex C (via `codex review`) P2/P3 resolved, final re-review no new actionable HIGH/MEDIUM.** No unresolved actionable HIGH. See [`GRAPHRAG_03D_RECONCILE.md`](GRAPHRAG_03D_RECONCILE.md). |
| 03-E REBUILD | ✅ **COMPLETE / APPROVED** — signed off 2026-08-30. Operator-triggered canonical rebuild **dispatcher** that re-drives the existing 03A `graphrag_index_source` (source_id ONLY) over CURRENT non-empty sources to force convergence of PRESENT_UNVERIFIED docs. **PLAN** (read-only, default: enumerate/classify/count; no health/HTTP/enqueue/arm/mutation) vs **EXECUTE** (explicit; gate flag+config+content-free `GET /health` BEFORE any dispatch → source_id-only enqueue). **Decision A:** empty sources reported, never armed/dispatched (cleanup stays 03D→03B/03C); 03E is a dispatcher, not a deletion orchestrator. Keyset traversal + **RecordID** cursor (no OFFSET; numeric≠string-numeric; invalid cursor fails closed); `max_sources_per_run` fairness cap with a look-ahead so the exact boundary N==cap vs N==cap+1 never false-completes. Honest completion vocabulary — `REBUILD_DISPATCH_COMPLETE` = this sweep/continuation fully dispatched (no continuation, no enqueue failure); never INDEX_COMMAND_COMPLETION, never REMOTE_CONTENT_CONVERGENCE_VERIFIED (no `content_hash` on v1.5.6). No global purge, no foreign-doc deletion, no automatic/scheduled trigger, no `full_text` payload. **NO migration 26** (24/25 frozen; count stays **50**). **Dedicated default-OFF EXECUTE lock** (`OPEN_NOTEBOOK_GRAPHRAG_REBUILD_EXECUTE_ENABLED`, checked first) so enabling GraphRAG for ingestion never unlocks a corpus-wide Boundary-B rebuild (Codex-B). **Cursor skip-safety:** a per-row failure (invalid id / state-read error / enqueue failure) fail-stops the sweep and never advances the resumable cursor past an un-handled source, so a resume re-attempts it and the run is DISPATCH_PARTIAL, never a false COMPLETE (Codex-A/C). New `rebuild.py` + `GraphRAGRebuildConfig` + `graphrag_rebuild` command (`max_attempts=1`); 43 property tests (mock + 2 live SurrealDB v2.6.5 + **3 live LightRAG v1.5.6 through a REAL surreal-commands worker, PASSED** — real EXECUTE → source_id-only dispatch → worker reloads CURRENT source → document present in the real sidecar; execute-lock-off and preflight-failure zero-dispatch invariants). GraphRAG regression 373 pass; full backend 1023 pass / 9 skip / 5 pre-existing platform failures; ruff clean; production mypy clean. **Karpathy CLEAN · Codex A/B/C run — all HIGH findings resolved with regression tests, re-verified green; no unresolved actionable HIGH/MEDIUM.** **Live LightRAG synthetic EXECUTE acceptance gate PASSED (synthetic-only; env restored, no worker leak, no secrets in tree).** See [`GRAPHRAG_03E_REBUILD.md`](GRAPHRAG_03E_REBUILD.md). **Karpathy CLEAN · Codex A/B/C resolved · no unresolved actionable HIGH/MEDIUM.** |

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

**GraphRAG-03 is COMPLETE** — all slices (03-A … 03-E) approved. **GraphRAG-03E** — REBUILD (operator-triggered bounded canonical rebuild dispatcher; forced convergence of PRESENT_UNVERIFIED docs) — ✅ **COMPLETE / APPROVED 2026-08-30**: PLAN default (strictly read-only) / explicit EXECUTE behind a dedicated default-OFF lock; content-free preflight before dispatch; bounded keyset traversal + lossless RecordID continuation cursor + hard source cap; `last_good_id` fail-stop so an incomplete/partial sweep is never reported complete; source_id-only reuse of the 03A lifecycle (03A reloads CURRENT Source); empty Sources report-only; no tombstone mutation, no global purge, no foreign/unknown mutation, no `full_text` payload; `REBUILD_DISPATCH_COMPLETE` is dispatch-only (never a remote-content-verification claim). **NO migration 26** (24/25 frozen; count 50). Live LightRAG v1.5.6 synthetic EXECUTE gate PASSED (real worker + real sidecar; execute-lock-off and preflight zero-partial-dispatch verified; CURRENT-source A→B reload verified). Karpathy CLEAN; Codex A/B/C resolved; no unresolved actionable HIGH/MEDIUM.

**Accepted limitations (carried forward):** LightRAG v1.5.6 exposes no `content_hash`, so a successful 03A execution means the CURRENT canonical Source was submitted through the approved lifecycle — NOT that remote content equality was independently verified; REBUILD is not a globally atomic snapshot; `REBUILD_DISPATCH_COMPLETE` is dispatch completion for the bounded sweep/continuation only. The >200-doc authoritative-absence ceiling remains an upstream-capability blocker (03C/03D).

**No later GraphRAG phase (04+) has been started.** Boundary B remains NOT APPROVED for real internal data; any real-data rebuild needs a separate egress decision.
