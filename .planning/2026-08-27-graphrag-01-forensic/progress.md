# Progress Log

## Session: 2026-08-27

### Current Status
- **Phase:** 6 - Adversarial review reconciled; decision record written (COMPLETE). **BLOCKED awaiting user approval of `GRAPHRAG_DECISION.md` before GraphRAG-02.**
- **Started:** 2026-08-27

### Phase 6 — Adversarial review reconciliation (2026-08-27)
Codex adversarial review returned `needs-attention` / no-ship with 6 findings (3 high, 3 medium).
All 6 verified against current checkout before classification. **No code, dependency, migration, or API change made.**

Classification: **4 ACCEPT · 2 ACCEPT WITH MODIFICATION · 0 REJECT**

| # | Finding | Class | Verifying evidence |
|---|---|---|---|
| 1 | Best-effort delete retains/resurfaces deleted text | ACCEPT (impact raised) | `migrations/1.surrealql:29` DB event has no HTTP equivalent; `domain/notebook.py:274-286` bypass path |
| 2 | Notebook scoping is metadata-only | ACCEPT (restated) | `domain/notebook.py:809-815`, `migrations/9.surrealql:4`, `api/models.py:39-47,56-61` — global is *by design* |
| 3 | Mirroring `Source.vectorize` breaks fail-open | ACCEPT | `domain/notebook.py:576` raises; `graphs/source.py:224` unguarded await |
| 4 | Fusion confuses raw-score vs rank-only | ACCEPT | Matrix added; RRF chosen; embedding coupling removed from critical path |
| 5 | Citation asserted without a result contract | ACCEPT **WITH MODIFICATION** | `graphs/ask.py:110` + `query_process.jinja` confirm ID risk; but `migrations/9.surrealql:8-11,62` show source-granular citation w/ spans is the *existing* contract → requirement is "no weaker", staged by phase |
| 6 | Metadata leaks asset paths/URLs | ACCEPT | `GRAPHRAG_FORENSIC.md:176` rev-1 contract; removed, replaced with allowlist |

Findings the review missed, surfaced during reconciliation:
- `Note.save()` (`domain/notebook.py:716-727`) is the **existing correct fail-open submit idiom** — adopted as binding pattern for #3 instead of inventing one.
- `DEFINE EVENT source_delete` (`migrations/1.surrealql:29`) means vector cleanup is **DB-enforced** while GraphRAG deletion cannot be — raises #1 from "housekeeping" to confidentiality-grade, R7 Low→High.

Also added beyond the review: two-boundary data-egress model (localhost sidecar ≠ safe provider egress), 26 required failure/rollback tests, revised 7-phase roadmap.

### Deliverables (Phase 6)
- Updated `docs/agribank/architecture/GRAPHRAG_FORENSIC.md` → rev-2 (corrections in §4A, §8, §9, §10, §11, §12, §13; new §18 tests, §19 rollback, §20 roadmap, reconciliation provenance table).
- Created `docs/agribank/development/GRAPHRAG_DECISION.md` (AGR-005, **PROPOSED**, 20 sections, 12 rejected alternatives, 10 open questions, 20 GraphRAG-02 acceptance criteria).
- Indexed AGR-005 in `docs/agribank/development/DECISIONS.md`.

### Actions Taken
- Read CLAUDE.md / AGENTS.md / AGRIBANK.md; confirmed Superpowers, planning-with-files, Graphify installed.
- Initialized planning session `2026-08-27-graphrag-01-forensic`.
- Used existing Graphify graph (`.graphify/`, built same day) as navigation index; located `Source` bridge node + communities.
- Verified source lifecycle by direct reads: `api/routers/sources.py`, `commands/source_commands.py`, `commands/embedding_commands.py`, `open_notebook/graphs/source.py`.
- Verified retrieval/Ask/Chat/citation: `api/routers/search.py`, `graphs/ask.py`, `graphs/chat.py`, `graphs/source_chat.py`, `api/routers/chat.py`, `open_notebook/utils/context_builder.py`, `domain/notebook.py`.
- Verified schema + search fns: `migrations/1.surrealql`, `3.surrealql`, `4.surrealql`. Confirmed no pre-existing lightrag/graphrag refs.
- Wrote findings.md and the 17-section report `docs/agribank/architecture/GRAPHRAG_FORENSIC.md`.
- Verified via `git status` that NO source/schema/migration/frontend files were modified (only report + planning added).

### Test Results
| Test | Expected | Actual | Status |
|------|----------|--------|--------|
| git status (no source edits) | only docs/planning untracked | only GRAPHRAG_FORENSIC.md + .planning/ + .playwright-cli/ | PASS |

### Errors
| Error | Resolution |
|-------|------------|
| `grep -rli` over full repo timed out (120s, large .venv/.uv-cache) | Re-ran scoped to open_notebook/api/commands/prompts/docs — confirmed no lightrag refs |

### Decisions for user approval (before GraphRAG-02)
1. Indexing = I1 (fire-and-forget `graphrag_index_source` after full_text persists).
2. Retrieval = R1 additive HybridRetriever, first delivered as R3 new `/api/search/graph` path (zero blast radius).
3. Fail-open + `OPEN_NOTEBOOK_GRAPHRAG_ENABLED` flag (default off).
4. **Blocker:** data-egress decision record required before any code (sending full_text to sidecar + provider).
