# Progress Log — GraphRAG-02 PoC

## Session: 2026-08-27

### Current Status
**GraphRAG-02 COMPLETE — approved by user 2026-08-27.** Plan closed.
**NOT committed** (§21.12) — awaiting explicit commit instruction.
Boundary B still unapproved ⇒ real internal data prohibited. GraphRAG-03 NOT started.

---

## What was built

Isolated LightRAG integration boundary, flag-gated (default OFF), diagnostic-only.

**New files (13):**
- `open_notebook/integrations/graphrag/{__init__,config,models,client,service}.py`
- `api/routers/graphrag.py` (+2 lines in `api/main.py`)
- `tests/test_graphrag_{integration,isolation,recordid}.py`
- `deploy/graphrag-poc/{docker-compose.graphrag.yml,.gitignore}`
- `docs/agribank/architecture/GRAPHRAG_POC.md`
- `.planning/2026-08-27-graphrag-02-poc/{task_plan,progress}.md`

**Modified (tracked):** `api/main.py` (+5), `.env.example` (+12), `GRAPHRAG_DECISION.md`, `CURRENT_PHASE.md`, `DECISIONS.md`.

---

## Upstream contract (LightRAG **v1.5.6**, pinned — read from router source, not docs)

| Capability | Endpoint | Notes |
|---|---|---|
| health | `GET /health` | 200 even unauthenticated (liveness only) |
| index | `POST /documents/text` | `{text, file_source?, chunking?}` → `{status, message, track_id}` |
| track | `GET /documents/track_status/{id}` | 7-state `DocStatus` pipeline |
| query | `POST /query` | `{query, mode, top_k, include_references}` → `{response, references[], response_time}` |

- Auth `X-API-Key` (`utils_api.py:400`); port 9621; `/documents` prefix.
- **No metadata field** ⇒ only `source_id` (as `file_source`) + `canonical_text` cross the wire.
- `file_source` is transformed by `normalize_file_path` → `canonicalize_parser_hinted_basename` → **`Path(x).name`** (`parser/routing.py:1090`). Verified all canonical id forms survive unchanged ⇒ **no base64url encoding needed**.

---

## Key technical decisions

1. **Fail-open pattern = `Note.save()`, NOT `Source.vectorize()`.** `vectorize()` raises `DatabaseOperationError` (`domain/notebook.py:576`) and `save_source` awaits it unguarded (`graphs/source.py:224`). `Note.save()` (`:716-727`) is the correct in-repo idiom.
2. **Metadata: only wire-transmissible fields in the runtime signature.** `title`/`content_hash`/`notebook_ids` removed from router/service/builder rather than built-and-discarded. Full allowlist retained in docs for future phases.
3. **`source_id` validated STRUCTURALLY, not by regex over presentation** (see below).
4. **Two query methods.** `query_strict()` raises (diagnostic endpoint); `query()` fails open to `None` (future GraphRAG-05 hybrid).
5. **Indexing restricted to `source`;** provenance recognises `source`/`note`/`source_insight`. Asymmetry deliberate.
6. **No deletion capability** — durable delete is GraphRAG-03 and must not fail open.

### The RecordID problem (most subtle issue in this phase)

`str(RecordID)` escapes identifiers in `⟨…⟩` (U+27E8/9) when they contain chars outside `[A-Za-z0-9_]` **or have no alphabetic char** (`surrealdb/data/types/record_id.py::_escape_identifier`). So `source:123` (a real fixture id, `tests/test_domain.py:283`) canonicalizes to `source:⟨123⟩`.

The first regex fix **wrongly rejected** these. Current validator pipeline:

```
split table:identifier → unwrap one escape layer → validate table (indexable)
→ validate UNESCAPED identifier [A-Za-z0-9_-]{1,128} → re-serialize via str(RecordID)
```

- Escaping **preserved on the wire**: `RecordID('source',123) != RecordID('source','123')`; collapsing would merge distinct documents.
- **`RecordID.parse()` deliberately NOT used** — it double-escapes already-escaped strings (`source:⟨123⟩` → `source:⟨⟨123\⟩⟩`) and raises on multi-colon values. Pinned by test.
- Validation applies to the *unescaped* value, so `source:⟨../../secret⟩` cannot smuggle a payload.
- Homoglyph/Unicode variants (‹›, <>, ＜＞, 〈〉, zero-width, BOM, fullwidth colon/solidus, combining marks, Cyrillic lookalikes) all rejected — verified manually, ASCII-only pattern handles by construction.
- `GraphRAGValidationError` (we refused; zero egress) → 422, distinct from `GraphRAGRequestError` (sidecar rejected) → 400.

---

## Review rounds

**Round 1 — Karpathy (4 findings, all fixed):** dead `title`/`content_hash` threading; unreachable `if False` test branch; 4xx→400 collapse; `List[Any]`.

**Round 2 — Codex adversarial (2 findings, both fixed):**
- **[HIGH]** `source_id` value unvalidated despite egress claims — original tests used only benign `source:abc`, proving shape not property.
- **[MEDIUM]** endpoint lost error taxonomy via fail-open `query()`.

**Round 3 — user-directed RecordID forensic (mismatch found, fixed):** the round-2 regex rejected valid escaped ids. Reported before changing code; user chose direction B (parse/validate underlying, preserve canonical escaping). Karpathy re-trace of the fix found one unreachable `len < 2` branch — removed, replaced with a real nested-escaping check.

First Codex attempt failed for a **tooling** reason (`/codex:review` no longer accepts focus text → use `adversarial-review`), not a code issue.

---

## Round 4 — Codex adversarial on the RecordID fix (2 findings, both fixed)

1. **[HIGH] Numeric RecordIDs silently re-keyed.** `validate_source_id` unwrapped to a Python `str` and always rebuilt `RecordID(table, str)`, so numeric `source:123` returned `source:⟨123⟩` — re-keying it onto the *string* identity and potentially merging two distinct documents. **My own test missed this**: it asserted the SDK's behavior, never the validator's.
   **Fix:** an unescaped all-digit identifier re-serializes through `int`. Both identities now asserted through the validator *and* the outbound `file_source` payload.
2. **[MEDIUM] Provenance check was prefix-only.** `_looks_like_record_id` returned `True` for `source:https://internal/doc?token=x`, `source:../../secret`, `source:a\nb` — a misleading `resolved=True` trust signal on exactly the values the boundary distrusts.
   **Fix:** shared structural validator (`is_valid_record_id`), widened to the provenance table set, so inbound/outbound rules cannot drift. Shared logic moved to **`models.py`** because `service` imports `client` (circular import otherwise).

Karpathy re-trace: single implementation, no duplicated validation, `validate_source_id` is now a thin delegate. 3 unused imports cleaned by ruff.

---

## Verification (final)

| Check | Result |
|---|---|
| GraphRAG tests | **189 passed** (integration + isolation + recordid) |
| Full backend | **839 passed, 5 failed** — all 5 pre-existing |
| ruff | All checks passed |
| mypy | Success, 6 files |
| Migrations | 46 (unchanged) |
| Frontend | 0 GraphRAG refs |
| Prohibited files | 0 GraphRAG refs across all 9 |

### Baseline failures — PROVEN pre-existing
Method: `git stash push -u` all GraphRAG files (integration pkg, router, main.py, 3 test files, .env.example) → same **5 failed, 50 passed** with the package absent from disk → `git stash pop`, stash list empty, 188 tests passing again.

1. `test_symlink_escape_is_rejected` — `WinError 1314` (symlink privilege)
2. `test_file_uri_under_root_becomes_relative` — `file://C:\...` drive-letter path
3. `test_path_structure` — `\data\...` vs `/data/...` separators
4. `test_path_works_on_posix` — `Path.parts` asserts POSIX semantics
5. `test_merges_both_case_variants` — `no_proxy`/`NO_PROXY` case merge drops a variant (**genuine logic bug**, not platform; `api/main.py`, but reproduces with our 2 lines stashed)

#1-4 are Windows artifacts. Documented in `GRAPHRAG_POC.md` § "Known Baseline Test Failures".

---

## Outstanding

- [ ] Codex review of the RecordID fix (running, job `b6b28znz9`)
- [ ] User sign-off
- Boundary B unapproved ⇒ no real internal data, any phase
- GraphRAG-03 NOT started

### Outcome
Signed off 2026-08-27 after 4 review passes (2 Karpathy, 2 Codex) and a user-directed RecordID
forensic. 9 findings total, all resolved, no unresolved HIGH. Documentation frozen in
GRAPHRAG_POC.md §0. Nothing committed.
