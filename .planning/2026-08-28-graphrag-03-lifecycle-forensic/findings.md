# Findings — GraphRAG-03 lifecycle forensic (2026-08-28)

All line references verified against commit `bc5b413`.

## Open Notebook side

- **Only one writer of `full_text`:** `graphs/source.py:210` inside `save_source`, reached only via `process_source` (`commands/source_commands.py:96`) — from source creation (`api/routers/sources.py:522`) or retry (`:1011`). `PUT /sources/{id}` (`:905-919`) changes **title/topics only** — never content.
- **Safest enqueue point:** after `await source.save()` at `graphs/source.py:216`, alongside/after the `vectorize()` block at `:221-228`. `full_text` is durable at that point.
- **Exception contract must be `Note.save()`, not `vectorize()`.** `vectorize()` raises `DatabaseOperationError` on submit failure (`domain/notebook.py:576`) and `save_source` awaits it unguarded (`graphs/source.py:224`) ⇒ copying it would fail ingestion. `Note.save()` (`domain/notebook.py:716-727`) submits in `try`, logs, returns `None`.
- **Delete paths (5):** `Source.delete()` (`domain/notebook.py:642`) ← API `DELETE /sources/{id}` (`sources.py:1063`) + 3 rollback sites (`sources.py:551,559,614`); `Notebook.delete(delete_exclusive_sources=True)` (`domain/notebook.py:266`); `ObjectModel.delete()`→`repo_delete` (`base.py:210`); raw SurrealQL `DELETE source`; nothing else.
- **`Notebook.delete()` default UNLINKS, does not delete** (`domain/notebook.py:274-286`): `DELETE reference WHERE out = $notebook_id`. Source survives ⇒ **must not** trigger GraphRAG deletion.
- **DB event is the vector backstop** (`open_notebook/database/migrations/1.surrealql:29-32`): `DEFINE EVENT source_delete` cascades `source_embedding` + `source_insight`. Fires on raw `DELETE source` too. **A SurrealDB event cannot make an outbound HTTP call** ⇒ GraphRAG has no equivalent backstop.
- **No `content_hash` field exists anywhere** in the repo (grep for `content_hash|md5|sha256|hashlib` → no matches). `source.updated` (`1.surrealql:14`, `VALUE time::now()`) is the only change signal.
- **Command durability (read from installed `surreal_commands`):**
  - `submit_command` → `db.create("command", {..., status: "new"})` (`core/service.py:154-167`). **Persisted in SurrealDB** — survives worker and API restart.
  - Worker on boot: `SELECT * FROM command WHERE status = 'new' ORDER BY created ASC` (`core/worker.py:110-111`), then a LIVE query. **So a queued job survives a worker outage.**
  - **The gap:** `execute_command` sets `status="running"` *before* execution (`core/service.py:225`) and only writes terminal status at the end (`:293`). A worker crash mid-execution leaves the row **stuck at `"running"` forever** — the boot scan only re-picks `"new"`. **No lease, no heartbeat, no requeue.**
  - Retries are **in-process only** (tenacity, `core/service.py:243-254`); `status` stays `"running"` across attempts. Exhausted retries → `"failed"`, never retried again.
  - ⇒ **Missing property: crash-recovery / terminal-failure re-drive.** This is exactly what deletion durability needs.

## LightRAG v1.5.6 (verified from pinned source, not docs)

- `DELETE /documents/delete_document` (`api/routers/document_routes.py:6120`), body `DeleteDocRequest{doc_ids: List[str], delete_file: bool, delete_llm_cache: bool}` (`:966`), response `DeleteDocByIdResponse{status: Literal["deletion_started","busy","not_allowed"], message, doc_id}` (`:6111-6118`). **Deletion is by LightRAG `doc_id`, background, and can refuse with `busy`.**
- **`doc_id` is deterministic from `file_source`:** `pipeline.py:936-946` — `has_known_document_source(canonical)` true ⇒ `doc_id = "doc-" + md5(canonical_file_source)`; only for placeholder sources does it hash content. `normalize_document_file_path` (`utils_pipeline.py:237`) strips `[hint]`, collapses `{"", "no-file-path", "unknown_source"}` (`utils_pipeline.py:55`). `compute_mdhash_id` = prefix + md5 of single `str(arg)` (`utils.py:680,794`). ⇒ **We can compute doc_id locally; no lookup needed, and it is stable across content edits.**
- **Re-insert of the same `file_source` is REJECTED as a duplicate,** not treated as an update: `resolve_existing_doc_source` → `SourceUnique` ⇒ `duplicate_kind="filename"` (`pipeline.py:1121-1170`). ⇒ **REINDEX must be delete-then-insert.**
- Enumeration for RECONCILE: `POST /documents/paginated` (`:6355`) with `DocumentsRequest{status_filters, page, page_size 10-200, sort_field, sort_direction}` (`:1168`) → `PaginatedDocsResponse{documents, pagination, status_counts}`; `GET /documents` (`:6008`) is deprecated and caps at 1000. `GET /documents/status_counts` (`:6536`). `DocStatusResponse` (`:1003`) exposes `id, status, created_at, updated_at, chunks_count, error_msg, file_path, track_id, content_length`. **`file_path` carries our `source_id`** ⇒ join key available on both sides.
- `DELETE /documents` (`:5569`) clears everything — REBUILD-from-scratch primitive.
- No `content_hash` is exposed on `DocStatusResponse` (it is stored internally, `pipeline.py:1046`) ⇒ **staleness cannot be detected from the sidecar**; must be tracked Open Notebook-side.

## Consequences for design

1. Deletion durability **cannot** rest on the existing command queue alone — the missing property is crash/terminal-failure re-drive.
2. But a **tombstone/outbox is not the only option**: because `doc_id` is derivable from canonical `source_id`, RECONCILE can enumerate the sidecar and diff against SurrealDB **without any local record of what was deleted**. Orphan detection needs no tombstone.
3. What reconciliation alone cannot bound is *latency* of purge. Combination = best-effort immediate delete + durable retry + periodic reconcile.
