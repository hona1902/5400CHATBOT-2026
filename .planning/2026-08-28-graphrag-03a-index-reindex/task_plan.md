# GraphRAG-03A — INDEX / REINDEX implementation plan — CLOSED

**Status: COMPLETE — APPROVED 2026-08-28.** Plan closed.
**Branch:** `feature/graphrag-lifecycle` · **Baseline:** `bc5b413` · forensic committed `6cd8333`.
**Scope:** INDEX / REINDEX lifecycle ONLY. No DELETE, tombstone, migration, reconcile, rebuild, scheduler, Ask/Chat/frontend, no real data.

See `plan.md` (this dir) for the full design and `progress.md` for the review log.

## Checklist — all done
- [x] `open_notebook/integrations/graphrag/lifecycle.py` — orchestration (reload + validate + double-confirm delete-then-insert)
- [x] `commands/graphrag_commands.py` — `graphrag_index_source` command (retry config, source_id identity, lossless record-id)
- [x] Register in `commands/__init__.py`
- [x] Fail-open enqueue seam in `graphs/source.py::save_source` (Note.save() contract)
- [x] `tests/test_graphrag_lifecycle.py` + `tests/test_graphrag_command_seam.py` — property tests
- [x] Docs: `GRAPHRAG_03A_INDEXING.md`, forensic status + §21, `CURRENT_PHASE.md`
- [x] Verify: 219 GraphRAG / 869 backend (5 pre-existing) / ruff / mypy / karpathy / 6 Codex passes — no unresolved actionable HIGH
- [x] User sign-off 2026-08-28 — no auto-commit

**Next:** 03-B (durable deletion state + DB event) — NOT started; requires separate go-ahead.
