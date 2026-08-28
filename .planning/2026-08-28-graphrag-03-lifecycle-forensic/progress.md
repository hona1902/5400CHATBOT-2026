# Progress — GraphRAG-03 lifecycle forensic

**2026-08-28** — Forensic stage complete. Documentation only.

## Done
- Recovered state from Git + repo docs; confirmed HEAD `bc5b413` = GraphRAG-02 checkpoint (branch `feature/graphrag-lightrag`; brief's `feature/graphrag-lifecycle` absent — flagged).
- Traced create/index, update/reindex, and all 7 delete paths in current source.
- Established `surreal_commands` durability semantics from the installed package (persist + boot-scan, but no crash re-drive).
- Verified LightRAG v1.5.6 delete/list/status/paginated API and doc_id derivation from pinned upstream source (not guessed).
- Produced 21-row failure matrix, 5 candidate durable-delete designs, rebuild + reconcile designs, identity contract.
- Wrote `docs/agribank/architecture/GRAPHRAG_LIFECYCLE_FORENSIC.md` (22 sections).
- Saved 3 memory files + index.

## Key results
- INDEX/REINDEX share one enqueue seam (`save_source`), `Note.save()` fail-open contract.
- REINDEX must be delete-then-insert (upstream rejects same-`file_source` re-insert).
- Delete bypass hole = raw `DELETE source`; DB event can't call HTTP → needs durable tombstone (option 1) if bounded-latency SLA required.
- doc_id = `"doc-"+md5(source_id)` — locally computable, content-stable.
- Migration NOT auto-required; decision hinges on retention SLA (open blocker).

## Blocking
- **User approval required before any implementation.** Boundary B, retention SLA, migration approval, reconcile trigger, branch target all open.
