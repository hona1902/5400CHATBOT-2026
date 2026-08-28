# Task Plan — GraphRAG-02: Isolated LightRAG PoC

**Approved scope:** `docs/agribank/development/GRAPHRAG_DECISION.md` §21 (APPROVED FOR GRAPHRAG-02 POC ONLY).
**Boundary B NOT approved** ⇒ synthetic / public / anonymized data only.

## Verified upstream contract (LightRAG **v1.5.6**, pinned)

Latest stable tag; v1.5.7rc2 is a prerelease and is NOT used. Read from router source at `?ref=v1.5.6`, not from docs.

| Capability | Endpoint | Request | Response |
|---|---|---|---|
| health | `GET /health` | — | 200 always (liveness). Config only when authenticated |
| index | `POST /documents/text` | `InsertTextRequest{text (min_length=1, whitespace-only rejected), file_source?, chunking?}` | `InsertResponse{status: success\|partial_success\|failure, message, track_id}` |
| track | `GET /documents/track_status/{track_id}` | — | `TrackStatusResponse{track_id, documents[], total_count, status_summary}` |
| query | `POST /query` | `QueryRequest{query, mode, top_k, only_need_context, include_references, …}` | `QueryResponse{response, references[ReferenceItem{reference_id, file_path, content}], response_time}` |

- Auth: **`X-API-Key`** header (`APIKeyHeader`, `lightrag/api/utils_api.py:400`). Default port **9621**.
- Route prefixes: `/documents` (`document_routes.py:4247`); query routes unprefixed.
- `mode` ∈ `local | global | hybrid | naive | mix | bypass`.
- `DocStatus` (`lightrag/base.py:888`): `pending → parsing → analyzing? → processing → processed | failed` (+ deprecated `preprocessed`). **7 states, not 3** — insert is async.

## Decisions taken (user-approved 2026-08-27)

1. **Metadata:** upstream `/documents/text` accepts **no arbitrary metadata field**. Send `source_id` in `file_source` as the sole join key. `title` / `content_hash` / `notebook_ids` / `contract_version` are **NOT sent** — retained in Open Notebook, joined locally. Strictly less egress than approved §7; amend §7 to record the constraint.
2. **Async insert:** expose `track_id` and add `track_status()` so the PoC can confirm indexing completed before querying.

`ReferenceItem.file_path` carries **our `source_id`**, never a filesystem path. The client must map it back and must never treat it as a path.

## Repo conventions to follow (verified)

- Env vars: `os.environ.get(...)` (`open_notebook/config.py`; `ai/models.py` passim).
- Exceptions: subclass `OpenNotebookError` (`open_notebook/exceptions.py`).
- httpx already a dependency (`pyproject.toml:39` `httpx[socks]>=0.27.0`) — no new dep.
- Router registration: `app.include_router(x.router, prefix="/api", tags=[...])` (`api/main.py:383-406`).
- Tests: `tests/test_*.py`, `pytest` + `pytest-asyncio`, `TestClient`, `unittest.mock`. **No `respx`** — use `httpx.MockTransport` to avoid a new dependency.

## Structure

```
open_notebook/integrations/graphrag/
    __init__.py   config.py   models.py   client.py   service.py
api/routers/graphrag.py        (+1 include_router line in api/main.py)
tests/test_graphrag_*.py
deploy/graphrag-poc/           (dev-only sidecar profile; production compose untouched)
docs/agribank/architecture/GRAPHRAG_POC.md
```

## TDD order

1. Write tests for config + error normalization + allowlist (fail).
2. `config.py`, `models.py` → pass.
3. Tests for client transport behaviors (fail) → `client.py` → pass.
4. Tests for service flag/allowlist/fail-open (fail) → `service.py` → pass.
5. Tests for endpoint (fail) → `api/routers/graphrag.py` + registration → pass.
6. Regression + full baseline.

## Hard prohibitions (§21.9)

No changes to `Source.save()`, `save_source()`, `process_source`, `Source.vectorize()`, `vector_search()`, `text_search()`, Ask graph, Chat graph. No production index command, outbox, tombstone, migration, durable deletion, reindex lifecycle, reconciliation, `HybridRetriever`, production RRF/rerank, production frontend change. **No real internal data.**

## Definition of done

§21.12 — plus: `ruff`, `mypy`, `git diff` review, `/karpathy:diff`, independent Codex review with no unresolved HIGH. **No automatic commit.**

## Current status

**COMPLETE — approved by user 2026-08-27.** Plan closed.

Final: 189 GraphRAG tests · 839 backend passed (5 pre-existing failures) · ruff clean · mypy clean ·
Karpathy clean (5 findings resolved) · Codex no unresolved HIGH (4 findings resolved).

Approved for commit; **not committed automatically**. GraphRAG-03 NOT started and NOT approved.
Boundary B remains unapproved: real internal data prohibited.
