# GraphRAG-03A — INDEX / REINDEX — Implementation Plan

Verified against `bc5b413` + LightRAG v1.5.6 pinned source.

## 1. Command identity & stale-job rule (the central design)

**The queued payload carries `source_id` ONLY — never `full_text`.** This is the single rule that makes stale-job resurrection structurally impossible.

`graphrag_index_source(source_id)` at worker execution time:
1. `source = await Source.get(source_id)` — load CURRENT canonical record.
2. If not found → **safe no-op**, return `success=True, skipped="source_absent"`. (An older INDEX firing after a DELETE indexes nothing.)
3. If `full_text` empty/whitespace → no-op, `skipped="no_content"`.
4. `validate_source_id(source_id)` (existing `service.py:33`) → canonical form; on `GraphRAGValidationError` return permanent failure (no egress).
5. delete-then-insert CURRENT `full_text` into LightRAG (§2).

Because the newest `full_text` is read at execution time, an older job that runs after a newer save still indexes the *current* state — it cannot resurrect old text. Two jobs for the same source converge to the same current state (idempotent).

**Expected-version token — investigated, DEFERRED.** A `content_hash`/version token in the payload could let a job detect "I am stale, skip." But: (a) reloading current state already yields correctness (a stale job re-indexes current content — wasteful at worst, never wrong); (b) no `content_hash` field exists (forensic §3) and adding one is schema work explicitly out of 03-A; (c) LightRAG doesn't expose its internal hash. So the token adds no correctness, only optimization, at the cost of new state. **Decision: rely on reload-current; document the token as a possible 03-D/03-E optimization.** No persistent schema added for this.

## 2. Idempotency semantics (verified against pinned upstream)

Verified facts:
- Re-`POST /documents/text` with the same `file_source` is **rejected as `duplicate_kind="filename"`** (`pipeline.py:1121-1170`) → naive re-insert does NOT update. So REINDEX must delete first.
- `doc_id = "doc-" + md5(canonical_source_id)` — locally computable, content-stable (`pipeline.py:936-946`).
- `adelete_by_doc_id` returns `status ∈ {success, not_found, not_allowed, fail}` (`lightrag.py:5387`). **`not_found` = clean idempotent no-op.**
- `DELETE /documents/delete_document` runs in background and can return `status="busy"` (`document_routes.py:6111`).
- Insert can return HTTP 409 (same-name conflict), 400 (invalid source), or be busy-gated.

**Chosen semantic — delete-then-insert, but NOT blind:**
```
compute doc_id from source_id (local, deterministic)
DELETE doc_id:
    success | not_found  -> proceed to insert   (idempotent: absent is fine)
    busy                 -> RETRY (raise transient) — do NOT insert into a racing delete
    not_allowed | fail   -> RETRY (transient)
INSERT current full_text (file_source = source_id):
    accepted             -> success
    409 filename-conflict -> a doc still exists (delete not yet materialized, async):
                             treat as transient RETRY, not success
    timeout/5xx/unavailable -> transient RETRY
    400/validation       -> permanent failure (no retry)
```

Why not blind delete-then-insert: LightRAG delete is *background* (`deletion_started` ≠ deleted). An insert fired immediately after can hit the still-present doc (409) or race the destructive slot (busy). We treat those as transient and let the retry layer re-drive — the operation converges without corrupting canonical state (canonical is never touched here at all).

**This slice does NOT add the durable DELETE.** The delete used here is the *reindex-internal* delete (remove-old-before-insert-new), issued only inside an index job that is about to insert. It is not the lifecycle DELETE (that's 03-B/03-C, durable/tombstoned). A new capability `delete_document(doc_id)` is added to the client/service for this internal use; it is fail-**closed within the job** (a failed delete blocks the insert and retries), which is correct for reindex and sets no "best-effort delete" precedent.

### Per-scenario behavior
| Scenario | Behavior |
|---|---|
| Duplicate INDEX | Both reload current, both delete-then-insert same doc_id → one doc |
| Duplicate REINDEX | Same as above |
| INDEX then newer INDEX | Each indexes current full_text; last-completing wins with current state (idempotent) |
| Older job after newer job | Reload-current ⇒ indexes current state, not stale payload |
| Source deleted while job waits | `Source.get` → not found → no-op, nothing indexed |
| Insert OK but ack lost | Retry re-runs delete-then-insert → same doc_id, no dup |
| Delete OK, insert fails | Transient → retry re-drives whole op (delete of now-absent = not_found, then insert) |
| Flag disabled while pending | Job no-ops as skipped (§4) |
| LightRAG unavailable | Transient failure → retry; canonical + vector RAG untouched |

## 3. Call site (verified)

`open_notebook/graphs/source.py::save_source`, after the existing `vectorize()` block (`source.py:216-228`). `full_text` is durable at `:216`.

```python
# after the `if state["embed"]: await source.vectorize()` block
await _maybe_enqueue_graphrag_index(source)   # fail-open, flag-gated
```
`_maybe_enqueue_graphrag_index` (small local helper in source.py, LightRAG-agnostic):
- if `not load_config().enabled` → return (no side effect, flag OFF ⇒ baseline).
- if no `full_text` → return.
- `try: submit_command("open_notebook", "graphrag_index_source", {"source_id": str(source.id)}); except Exception: logger.warning(...)` → **never raises** (Note.save() contract, `notebook.py:716-727`).

Invariant guaranteed: canonical save succeeds + GraphRAG submit fails ⇒ ingestion still succeeds. `vectorize()` behavior unchanged. The only import added to source.py is the flag check + submit_command (already imported in domain layer); LightRAG HTTP stays in the integration package.

Note: `submit_command` validates against the local registry, so `commands/graphrag_commands.py` must be importable when the API/worker submits. The worker imports `commands` (Makefile `--import-modules commands`), and `commands/__init__.py` re-exports each module — so I register there.

## 4. Feature-flag semantics for pending jobs (documented decision)

- **Flag OFF at enqueue** (common): `_maybe_enqueue` returns early → **no command created**. Baseline byte-for-byte.
- **Flag OFF at execution** (job queued while ON, flag flipped OFF before worker runs): the command **no-ops as skipped** — returns `success=True, skipped="graphrag_disabled"`, makes **no external call**. Rationale grounded in command infra: a job that raised would go `failed` and (per forensic §8) never re-drive; a job that returns success is terminal and clean. Since indexing is fail-open and rebuildable, skipping is correct — REBUILD/RECONCILE (later slices) reconcile any gap. Not left retryable: there is no auto-retry of `new`-after-success, and we don't want a disabled feature holding queue entries.

## 5. Files touched (exact)

**New:**
- `open_notebook/integrations/graphrag/lifecycle.py` — `async def index_source(source_id, canonical_text) -> IndexOutcome`: compute doc_id, delete-then-insert via `GraphRAGService`, normalize outcomes. Pure orchestration; imports only the integration package + typed errors.
- `commands/graphrag_commands.py` — `graphrag_index_source` command: reload Source, guard existence/content/flag, call `lifecycle.index_source`, map transient vs permanent to retry semantics (mirror `embedding_commands` retry config).
- `tests/test_graphrag_lifecycle.py` — property tests (§7).
- `docs/agribank/development/GRAPHRAG_03A_INDEXING.md`.

**Edited (minimal):**
- `open_notebook/integrations/graphrag/service.py` — add `delete_document(doc_id)` + `index_document` already exists; add a thin `reindex`-style method if cleaner. LightRAG specifics stay here.
- `open_notebook/integrations/graphrag/client.py` — add `delete_document(doc_ids)` hitting `DELETE /documents/delete_document`, normalizing `busy/not_found/deletion_started` + doc_id compute helper (`"doc-"+md5`).
- `open_notebook/graphs/source.py` — add the fail-open helper + one call in `save_source`.
- `commands/__init__.py` — import + `__all__` the new command.
- `.env.example` — (already has the flag block; no change needed) — verify only.

**NOT touched:** `Source.save()`, `save_source()` content logic, `vectorize()`, `vector_search()`, `fn::vector_search`, `Source.delete()`, `source_delete` event, any migration (count stays 46), Ask, Chat, frontend.

## 6. doc_id helper

`_compute_doc_id(source_id) -> "doc-" + md5(canonical(source_id))` in the client, mirroring `normalize_document_file_path` (strip nothing — our source_ids have no `[hint]`; canonical = validated source_id string). Unit-tested against the derivation so an upstream change is caught.

## 7. Tests (property-oriented; each states the property that breaks if wrong)

Mock the HTTP boundary via `httpx.MockTransport` (no `respx`, no live sidecar), matching GraphRAG-02.

1. **Flag OFF ⇒ no GraphRAG call.** *Breaks if:* disabled path still instantiates a client / makes a request. Assert transport never invoked.
2. **Canonical save succeeds when enqueue fails.** *Breaks if:* submit exception propagates out of `save_source`. Patch `submit_command` to raise; assert `save_source` returns normally and source persisted.
3. **Valid source enqueues INDEX.** *Breaks if:* seam doesn't submit with `{source_id}` (and no `full_text` in payload). Assert payload keys == {source_id}.
4. **Worker reloads current Source at execution.** *Breaks if:* command uses payload text. Assert command calls `Source.get(source_id)`.
5. **Stale job ignores stale queued text.** *Breaks if:* payload could carry text. Construct job with only source_id; DB has new text; assert indexed text == DB text. (Structural: payload has no text field.)
6. **Source deleted before execution ⇒ not indexed.** *Breaks if:* absent source still calls insert. `Source.get` raises/None → assert no transport insert, `skipped=source_absent`.
7. **Duplicate INDEX safe.** *Breaks if:* second run creates a second doc. Assert delete-then-insert both times, same doc_id.
8. **Duplicate REINDEX safe.** Same property via explicit re-enqueue.
9. **Sidecar timeout doesn't corrupt canonical state.** *Breaks if:* timeout mutates/deletes source. Assert source row unchanged; job transient-fails.
10. **Sidecar unavailable doesn't affect vector RAG.** *Breaks if:* index failure touches embeddings. Assert `embed_source`/vector path independent (source_embedding untouched).
11. **Malformed sidecar response normalized.** *Breaks if:* raw httpx/JSON error escapes. Assert typed `GraphRAGProtocolError` handled → transient.
12. **Canonical RecordID identity preserved.** *Breaks if:* doc_id computed from a lossy id. Assert `file_source` == exact canonical source_id.
13. **Numeric vs string-numeric distinct.** *Breaks if:* `source:123` and `source:⟨123⟩` collapse. Assert distinct doc_ids / distinct validation.
14. **No forbidden metadata egress.** *Breaks if:* payload carries file_path/url/full_text-as-metadata/etc. Assert outbound body keys ⊆ {text, file_source}; assert none of FORBIDDEN_METADATA_FIELDS present.
15. **No change to Ask/Chat/vector retrieval.** *Breaks if:* those modules imported/modified. Assert by test that `graphs/ask.py`, chat, `vector_search` unchanged (import-level / behavior smoke).
16. **No migration count change.** *Breaks if:* a migration was added. Assert `len(migrations) == 46`.
17. (bonus) **busy on internal delete ⇒ retry, not silent skip.** *Breaks if:* busy treated as success and insert proceeds into a racing delete.
18. (bonus) **flag flipped OFF at execution ⇒ skipped, no external call.**

## 8. Verification & stop
Targeted lifecycle tests → GraphRAG-02 regression (`test_graphrag_*`) → backend pytest → `ruff check .` → `mypy .` → `/karpathy:diff` → independent Codex adversarial review (stale-job resurrection, failure isolation, dup commands, delete/index races, ingestion coupling, flag semantics, egress, RecordID identity, removability). **No auto-commit.** Then report and STOP for 03-A sign-off.
