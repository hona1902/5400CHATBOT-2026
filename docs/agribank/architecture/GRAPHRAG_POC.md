# GraphRAG-02 — Isolated LightRAG PoC

**Status: APPROVED / COMPLETE** — accepted 2026-08-27. Contract below is **frozen** for GraphRAG-02.
**Authority:** [`../development/GRAPHRAG_DECISION.md`](../development/GRAPHRAG_DECISION.md) §21.
**Date:** 2026-08-27 · **Branch:** `feature/graphrag-lightrag` · **LightRAG pinned:** `v1.5.6`

> **Boundary B (sidecar → LLM/embedding provider) is NOT approved for real internal data.** Synthetic, public, or anonymized content only. **Real internal data is prohibited.**

---

## 0. Frozen GraphRAG-02 contract

```
Open Notebook
    ↓
GraphRAGService      flag gate · metadata allowlist · source_id validation · fail-open policy
    ↓
GraphRAGClient       sole owner of LightRAG's HTTP contract · normalizes every failure
    ↓
LightRAG sidecar     separate container · own store · never imported, never vendored
```

**Wire document payload — exactly two fields, nothing else:**

```json
{
  "text": "<canonical_text>",
  "file_source": "<validated_canonical_source_id>"
}
```

**RecordID guarantees:**

| Property | Status |
|---|---|
| Validated **structurally** (parse → unwrap → validate → re-serialize), not by regex over the presentation string | ✅ |
| Canonical identity **preserved** — SurrealDB escaping transmitted intact, never flattened | ✅ |
| Numeric id vs numeric-**string** id remain **distinct** (`source:123` ≠ `source:⟨123⟩`) through validator *and* wire payload | ✅ |
| LightRAG provenance round trip — canonical id → `file_source` → `ReferenceItem.file_path` → `GraphReference.source_id` recovers the identical string | ✅ |
| Indexing restricted to table `source`; provenance recognises `source`/`note`/`source_insight` | ✅ |
| Inbound provenance validated with the **same** structural rules as outbound | ✅ |

Anything beyond this — ingestion wiring, Ask/Chat, hybrid retrieval, durable deletion, migrations — is a later phase and unapproved.

---

## 1. Selected LightRAG version

**Pinned: `v1.5.6`** — latest stable tag as of 2026-08-27. `v1.5.7rc2` exists but is a prerelease and is deliberately not used.

The contract below was established by **reading the router source at `?ref=v1.5.6`**, not from prose documentation, per the instruction not to guess endpoints or schemas. Recorded in code as `VERIFIED_LIGHTRAG_VERSION` (`open_notebook/integrations/graphrag/config.py`) so a sidecar upgrade that shifts the contract is a visible decision.

## 2. Verified upstream API contract

| Capability | Endpoint | Request | Response |
|---|---|---|---|
| Health | `GET /health` | — | 200 even unauthenticated (liveness); full config only when authenticated |
| Index text | `POST /documents/text` | `InsertTextRequest{text, file_source?, chunking?}` | `InsertResponse{status, message, track_id}` |
| Track | `GET /documents/track_status/{track_id}` | — | `TrackStatusResponse{track_id, documents[], total_count, status_summary}` |
| Query | `POST /query` | `QueryRequest{query, mode, top_k, include_references, …}` | `QueryResponse{response, references[], response_time}` |

- **Auth:** `X-API-Key` header (`APIKeyHeader`, `lightrag/api/utils_api.py:400`). LightRAG enforces it only when `LIGHTRAG_API_KEY` is configured — **unset means no auth at all.**
- **Port:** 9621. **Prefix:** `/documents`; query routes unprefixed.
- **Modes:** `local | global | hybrid | naive | mix | bypass`.
- **`text`** has `min_length=1` and rejects whitespace-only input.

### 2.1 Two constraints that shaped the design

**(a) There is no metadata field.** `POST /documents/text` accepts only `text`, `file_source`, `chunking`. So:

- `source_id` travels in **`file_source`** — the only join-key slot available.
- `title`, `content_hash`, `notebook_ids`, `contract_version` are **not sent at all**. They stay in Open Notebook and are joined locally by `source_id`.

This is **stricter than** the approved §7 allowlist (less egress, not more), so it stays inside the approved boundary. Decision §7 is annotated with this constraint.

`ReferenceItem.file_path` in query responses therefore carries **our `source_id`**, never a filesystem path. `client.py` maps it back to `source_id` so no caller sees the misleading upstream name; `GraphReference.resolved` flags whether the value is even shaped like an Open Notebook record id.

**(b) Indexing is asynchronous, with 7 states.** `DocStatus` (`lightrag/base.py:888`) is `pending → parsing → analyzing? → processing → processed | failed` (plus deprecated `preprocessed`). Acceptance is not completion. Collapsed into `IndexState{in_progress, processed, failed}`; unknown future states normalize to `in_progress` so an upstream addition degrades to "keep waiting" rather than breaking.

## 3. Local sidecar setup

```bash
export GRAPHRAG_POC_API_KEY="$(openssl rand -hex 16)"   # never commit this
docker compose -f deploy/graphrag-poc/docker-compose.graphrag.yml up -d
```

Then in your local `.env`:

```bash
OPEN_NOTEBOOK_GRAPHRAG_ENABLED=true
OPEN_NOTEBOOK_GRAPHRAG_BASE_URL=http://localhost:9621
OPEN_NOTEBOOK_GRAPHRAG_API_KEY=<same value as GRAPHRAG_POC_API_KEY>
```

Deliberately **separate from the root `docker-compose.yml`**: production must not hard-depend on LightRAG (§21.11). The main compose file is unchanged, so `docker compose up` behaves exactly as before. Port bound to `127.0.0.1` only, mirroring the `surrealdb` service. `deploy/graphrag-poc/data/` is gitignored — it holds copies of indexed text.

Provider bindings are intentionally **not defaulted**. Supply them only after confirming the target is approved for synthetic/public data.

## 4. Boundary A — Open Notebook → sidecar

Crosses a process/container boundary; internal network when self-hosted. Only `GraphRAGClient` (httpx) speaks to it. LightRAG is **never imported or vendored** — enforced by a test that greps `open_notebook/` and `api/` for `import lightrag`.

## 5. Boundary B — sidecar → LLM/embedding provider

**Potentially crosses the organizational perimeter. NOT APPROVED for real internal data.**

"The sidecar runs on localhost" settles Boundary A and says nothing about Boundary B: a localhost sidecar holding a remote provider key exports every document it indexes. LightRAG's entity/relation extraction sends **document text** to an LLM, so indexing volume across B can approach the full corpus rather than a few short queries.

Consequently GraphRAG-02 uses synthetic/public/anonymized content only, and no automated test contacts a real provider (all HTTP is mocked at the transport).

## 6. Metadata contract as implemented

`build_sidecar_document()` takes **keyword scalars, not a `Source` object** — there is no object present to `model_dump()`, which is precisely how `asset.file_path` / `asset.url` leaked into the rev-1 design. Adding a field requires editing both the function and `ALLOWED_METADATA_FIELDS`, and a desync raises.

Sent to the sidecar: `text` (canonical synthetic text), `file_source` (= validated canonical `source_id`). Nothing else:

```json
{ "text": "<canonical_text>", "file_source": "source:abc123" }
```

The `file_source` value is the **canonical serialized RecordID with escaping intact** — for a string id of `"123"` that means `source:⟨123⟩` is transmitted verbatim, never flattened to `source:123` (§6.1).

**Verified round trip against pinned v1.5.6.** LightRAG passes `file_source` through `normalize_file_path` → `canonicalize_parser_hinted_basename` → `Path(file_path).name` (`lightrag/parser/routing.py:1090`). That takes a **basename**, so any value containing a path separator would be silently truncated. All canonical Open Notebook id forms — bare, escaped-digits, escaped-hyphen, escaped-underscore — pass through **unchanged**, confirmed by test. No additional transport encoding (base64url or similar) is required, and none was introduced.

The reverse direction is also asserted: canonical id → `file_source` → `ReferenceItem.file_path` → `GraphReference.source_id` recovers the identical string, with `resolved=True`. The client's provenance matcher recognises escaped forms; matching only the bare form would have reported legitimate escaped ids as unresolved.

**The runtime signature carries only these two fields.** `title`, `content_hash`, and `notebook_ids` are *not* accepted by the router, the service, or the builder — passing them raises `TypeError`, asserted by test. Earlier drafts accepted them, added them to a dict, and then dropped them before transmission; that reads as though they were sent and would mislead whoever wires up GraphRAG-03.

The **full forward-looking allowlist** remains the governing contract for any future upstream version that does accept metadata, and is retained in `ALLOWED_METADATA_FIELDS` and in decision §7 — in the architecture documents, not in a dead runtime signature.

### 6.1 `source_id` is value-validated structurally, not by regex

A field-name allowlist does **not** by itself bound egress. `source_id` is the one free-form string that crosses to the sidecar (as `file_source`), so an unvalidated value could carry exactly what the allowlist claims to exclude — `/uploads/private.pdf`, `https://internal/doc?token=…`, or a raw secret — through a permitted field name.

**A regex over the presentation string is the wrong tool**, and the first attempt at this fix demonstrated why. `str(RecordID)` wraps an identifier in SurrealDB escape delimiters `⟨…⟩` (U+27E8/U+27E9) when it contains anything outside `[A-Za-z0-9_]` **or has no alphabetic character at all** (`surrealdb/data/types/record_id.py::_escape_identifier`). So `source:⟨0123456789⟩` and `source:⟨abc-def⟩` are legitimate canonical forms, and a regex over the raw string cannot distinguish a real escaped id from an injected one. `source:123` is an existing fixture id (`tests/test_domain.py:283`) whose canonical form is `source:⟨123⟩` — the first regex rejected it.

`validate_source_id()` is therefore **structural**:

```
input → split "table:identifier" → unwrap one layer of escaping
      → validate table (indexable allowlist)
      → validate UNESCAPED identifier against [A-Za-z0-9_-]{1,128}
      → re-serialize via str(RecordID) → canonical wire value
```

Three properties this buys:

- **Security checks apply to the real value, not its presentation.** Wrapping a path in escape brackets (`source:⟨../../secret⟩`) does not bypass the guard, because validation happens after unwrapping. Asserted by test.
- **Escaping is preserved on the wire, and numeric identity with it.** `source:123` (numeric id) and `source:⟨123⟩` (string id `"123"`) are **distinct records** in SurrealDB — `RecordID('source', 123) != RecordID('source', '123')`. Because `str(RecordID)` escapes only the *string* form, rebuilding every identifier as `RecordID(table, str)` would emit `source:⟨123⟩` for a numeric input and silently re-key it onto the string identity. The validator therefore re-serializes an unescaped all-digit identifier through `int`, so both identities survive as themselves — asserted through the validator *and* through the outbound `file_source` payload.
- **The SDK decides what needs escaping, not us.** Re-serialization goes through `str(RecordID(table, identifier))`, so the rule cannot drift from the SDK on upgrade.

**Why not `RecordID.parse()`?** It is lossy for exactly the inputs this validator exists to accept. `parse()` treats an already-escaped string as a *literal* identifier, so re-serializing double-escapes: `source:⟨123⟩` → `source:⟨⟨123\⟩⟩`. It also raises on any value with a second colon, which would turn a rejectable input into an unhandled exception. A test pins this so nobody "simplifies" the validator into a lossy round trip.

Malformed escaping (unbalanced, embedded, or nested delimiters) is rejected structurally rather than character-classed.

Validation runs **before** any network call, and the rejection message **never echoes the offending value** — it may be the very token being refused, and that message reaches logs. The API request model bounds length only; it deliberately does *not* mirror a pattern, since the service layer is the security boundary and a presentation-level regex there would reintroduce the original bug. An invalid value surfaces as `GraphRAGValidationError` → HTTP 422, distinct from `GraphRAGRequestError` (which means the *sidecar* rejected a request we actually sent).

### 6.2 Indexable tables vs. provenance tables

These sets are deliberately different:

| | Tables | Rationale |
|---|---|---|
| **Indexable** (`_INDEXABLE_TABLES`) | `source` only | `index_document()` only ever sends `Source.full_text`. Nothing else is a canonical document. |
| **Provenance** (`_PROVENANCE_TABLES`, client) | `source`, `note`, `source_insight` | `fn::vector_search` returns all three (`migrations/9.surrealql`), so a future hybrid layer may legitimately surface them. |

Narrowing indexing to `source` is a tightening from the earlier three-table validator: `note:abc` and `source_insight:abc` are now rejected at the indexing boundary while still being *recognised* as provenance. A later retrieval layer can support more record types without the document-indexing boundary having to.

**Inbound provenance is validated structurally too.** `_looks_like_record_id()` was originally a prefix check, which reported `source:https://internal/doc?token=x`, `source:../../secret`, and `source:a\nb` as `resolved=True` — a misleading trust signal on precisely the values this boundary exists to distrust. It now calls the *same* structural validator as the outbound path, widened to the provenance table set, so inbound and outbound rules cannot drift apart. The sidecar may hold documents indexed outside this guarded path or echo corrupted provenance, so returned values get no more benefit of the doubt than submitted ones.

The shared validation lives in `models.py` (not `service.py`) because `service` imports `client` — putting it in `service` created a circular import.

Never sent: `asset.file_path`, raw filesystem paths, `asset.url`, original or signed URLs, API keys, tokens, credentials, arbitrary model metadata. Tests assert absence of each by serializing the outbound payload and searching it.

## 7. Normalized client contract

Everything LightRAG-shaped stops at `client.py`. Failures map to typed errors subclassing `OpenNotebookError`:

| Condition | Internal error | HTTP (diagnostic endpoint) |
|---|---|---|
| Flag off / no base URL | `GraphRAGDisabledError` | 503 |
| Connection refused, DNS, timeout | `GraphRAGUnavailableError` | 504 |
| HTTP 401/403 (bad or missing API key) | `GraphRAGConfigurationError` | 502 |
| HTTP 404/405 (endpoint absent → version mismatch) | `GraphRAGConfigurationError` | 502 |
| Other HTTP 4xx (e.g. 422 schema rejection) | `GraphRAGRequestError` | 400 |
| HTTP 5xx | `GraphRAGServerError` | 502 |
| Malformed JSON, non-object, unexpected schema | `GraphRAGProtocolError` | 502 |

The split matters for a diagnostic endpoint: 401/403/404/405 are **our** misconfiguration (wrong key, or a sidecar version that lacks the pinned endpoint), not bad input from the caller. Returning 400 for those would blame the caller for a deployment fault and send an operator looking in the wrong place. Only genuine request-level rejections (422 and similar) map to 400.

No `httpx` exception escapes the boundary — asserted by test.

## 8. Failure modes and fail-open behavior

| Scenario | Behavior |
|---|---|
| Flag off | No client constructed, no network setup; baseline unchanged |
| Sidecar absent at boot | App boots normally; health reports `enabled=false`/unhealthy, not 404 |
| Connection refused / timeout | Controlled error; process does not crash |
| Malformed or unexpected response | `GraphRAGProtocolError`; nothing partially parsed is used |
| LightRAG removed entirely | Vector RAG unaffected — it never reads sidecar state |

Two query methods exist deliberately, because the diagnostic and retrieval paths need opposite behavior:

| Method | Behavior | For |
|---|---|---|
| `query_strict()` | Raises typed `GraphRAGError`s | The diagnostic endpoint — preserves the full status taxonomy above |
| `query()` | Fails open to `None` | Future hybrid retrieval (GraphRAG-05) — degrades to vector-only without the caller catching anything |

The endpoint uses `query_strict()`. Routing it through `query()` meant every failure — bad API key, version mismatch, 422, 5xx, timeout, malformed JSON — collapsed into one generic 503, which defeats the purpose of a diagnostic endpoint. Found in adversarial review; the taxonomy is now asserted end-to-end by endpoint-level tests, not just at the client.

`index_synthetic_document()` also **raises** rather than failing open: a manual PoC call that silently did nothing would be misleading.

**No deletion capability is exposed.** Durable deletion is GraphRAG-03 and must not fail open (§9); shipping a best-effort delete here would set the wrong precedent.

## 9. Synthetic test procedure

```bash
# 1. Health
curl localhost:5055/api/search/graph/health

# 2. Index a synthetic document (NEVER real internal content)
curl -X POST localhost:5055/api/search/graph/index \
  -H 'Content-Type: application/json' \
  -d '{"source_id":"source:synthetic1",
       "canonical_text":"Widgets are fictional devices used in synthetic test corpora. The Foo Widget was designed by the Bar Team."}'

# 3. Poll until state=processed (indexing is async)
curl localhost:5055/api/search/graph/status/<track_id>

# 4. Query
curl -X POST localhost:5055/api/search/graph \
  -H 'Content-Type: application/json' \
  -d '{"query":"Who designed the Foo Widget?","mode":"hybrid"}'
```

Automated: `uv run pytest tests/test_graphrag_integration.py tests/test_graphrag_isolation.py` — 90 tests, all HTTP mocked via `httpx.MockTransport` (no new test dependency, no live sidecar, no provider calls).

## 10. Known limitations

- **References are not validated against live records.** A returned `source_id` may name a deleted source. `resolved` reflects *shape only* — not existence, and not authorization. Live validation is GraphRAG-05/06 (§8).
- **`answer` is ungrounded sidecar prose.** Diagnostic only; not citable.
- **No scope enforcement.** `RetrievalScope` is designed (§8) but deliberately not implemented — nothing here needs it, and building it now would be speculative.
- **No idempotency.** `content_hash` is computed nowhere and re-indexing the same `source_id` is not deduplicated; that is REINDEX in GraphRAG-03.
- **No circuit breaker.** Per-call timeouts only. A breaker matters when a hot retrieval path is involved (GraphRAG-05), not for manual diagnostic calls.
- **No retry.** A failed PoC call is simply retried by hand.
- **Sidecar is unauthenticated unless `LIGHTRAG_API_KEY` is set.** The compose file makes the key required to avoid that default.

## 11. Explicit non-goals for this phase

Not built, by approved scope (§21.9): source-ingestion wiring · Ask/Chat integration · `graphrag_index_source` production command · outbox or tombstone table · DB migration · durable deletion · production reindex lifecycle · reconciliation job · `HybridRetriever` · production RRF or reranking · frontend consumption · any use of real internal data.

Verified by test: the ingestion and retrieval files (`graphs/source.py`, `graphs/ask.py`, `graphs/chat.py`, `domain/notebook.py`, `commands/*`, `api/routers/search.py`, `api/routers/sources.py`) contain **no** GraphRAG reference, migration count is unchanged at 46, and only `api/routers/graphrag.py` imports the integration package.

## 12. Deferred to GraphRAG-03+

| Phase | Deferred work |
|---|---|
| **03** | INDEX/REINDEX/DELETE/REBUILD/RECONCILE; durable-deletion mechanism (outbox vs. retried command vs. reconcile-only); `content_hash` idempotency; retention/purge SLA |
| **04** | Synthetic evaluation corpus; does GraphRAG actually beat vector-only? |
| **05** | `HybridRetriever`; RRF; `RetrievalScope` implementation; live `source_id` validation; circuit breaker |
| **06** | Ask integration; strict citation row contract; drop-invalid-before-prompt |
| **07** | Sidecar auth mechanism (shared secret vs. mTLS); backup/restore; operational runbook |

## 13. Files added / changed

**Added:** `open_notebook/integrations/graphrag/{__init__,config,models,client,service}.py` · `api/routers/graphrag.py` · `tests/test_graphrag_{integration,isolation}.py` · `deploy/graphrag-poc/{docker-compose.graphrag.yml,.gitignore}` · this document.

**Changed (minimal):** `api/main.py` — one import entry plus one `include_router` line. `.env.example` — commented GraphRAG block. `docs/agribank/development/GRAPHRAG_DECISION.md` — status, §7 constraint, §21. `docs/agribank/development/CURRENT_PHASE.md`.

## 14. Verification record

| Check | Result |
|---|---|
| `pytest tests/test_graphrag_*.py` | **189 passed** |
| `pytest tests/` (full backend) | **839 passed, 5 failed** — all 5 pre-existing, proven by stashing every GraphRAG file (see § Known Baseline Test Failures) |
| `ruff check .` | All checks passed |
| `mypy` (new files) | Success, no issues in 6 source files |
| Frontend | Untouched — no change, verified by test |
| Migrations | Unchanged (46 files) |

### Known Baseline Test Failures

Five backend tests fail on this machine **independently of GraphRAG-02**. Not fixed here — out of approved scope (§21.9), and fixing them would mix unrelated changes into a PoC diff.

**Evidence method.** All GraphRAG files were removed from the working tree (`git stash push -u` covering `open_notebook/integrations/`, `api/routers/graphrag.py`, `api/main.py`, the three test files, and `.env.example`), then the four affected test files were run. Result: **the same 5 failures, `5 failed, 50 passed`**, with the integration package absent from disk. The tree was then restored (`git stash pop`, stash list empty, 186 GraphRAG tests passing again).

| # | Test | Failure reason | File under test | Touched by GraphRAG-02? |
|---|---|---|---|---|
| 1 | `test_podcast_audio_containment.py::TestResolveContainedAudioPath::test_symlink_escape_is_rejected` | `OSError: [WinError 1314] A required privilege is not held by the client` — creating a symlink requires elevation or Developer Mode on Windows | `open_notebook/podcasts/` | No |
| 2 | `test_podcast_audio_paths.py::TestToRelativeAudioPath::test_file_uri_under_root_becomes_relative` | `ValueError: Generated audio file path is outside the podcasts folder: file://C:\...` — a `file://` URI with a Windows drive letter is not recognised as being under the root | `open_notebook/podcasts/` | No |
| 3 | `test_podcast_path.py::TestBuildEpisodeOutputDir::test_path_structure` | `AssertionError: '\data\podcasts\episodes\…' == '/data/podcasts/episodes/…'` — backslash vs forward-slash separator | `commands/podcast_commands.py` | No |
| 4 | `test_podcast_path.py::TestBuildEpisodeOutputDir::test_path_works_on_posix` | `AssertionError` on `Path.parts` — first element `'\data\…'` instead of `('/', 'data', …)`; the test asserts POSIX semantics | `commands/podcast_commands.py` | No |
| 5 | `test_proxy.py::test_merges_both_case_variants` | `AssertionError: 'lower.example.com' in 'UPPER.example.com,host.docker.internal,…'` — when `no_proxy` and `NO_PROXY` are both set, only one case variant survives the merge | `api/main.py` proxy env handling | **`api/main.py` yes, but only 2 unrelated lines** |

**On #5 specifically.** GraphRAG-02 does modify `api/main.py` — one import entry and one `include_router` line. Neither touches proxy handling, and the failure reproduces with those lines stashed away (see evidence above), so it is not caused by this phase. Unlike #1-#4 it is a genuine logic bug (case-insensitive env-var merge dropping a variant) rather than a platform artifact, and is worth a separate issue.

**Classification.** #1-#4 are Windows-environment artifacts (symlink privilege, path separators, `file://` drive letters) and would likely pass on Linux/CI. #5 is platform-independent. None are GraphRAG-related; none are masked by this phase.

### 14.1 Review history

| Pass | Findings | Resolution |
|---|---|---|
| Karpathy diff #1 | 4 (dead `title`/`content_hash` threading; unreachable `if False` test branch; 4xx→400 collapse; `List[Any]`) | All fixed. Dead params **removed** rather than retained, so the signature matches the wire contract |
| Codex adversarial #1 | 2 (1 HIGH: `source_id` value unvalidated despite the egress claim · 1 MEDIUM: endpoint lost the error taxonomy via fail-open `query()`) | Both fixed — see §6.1 and §8. Tests 92 → 122 |
| RecordID forensic (user-directed) | 1 mismatch: the regex from the previous round **rejected valid escaped ids**, e.g. the existing fixture id `source:123` | Reported before changing code; fixed structurally per approved direction. Tests 122 → 186 |
| Karpathy diff #2 | 1 (unreachable `len < 2` branch in the new unwrapper) | Removed; replaced with a real nested-escaping check |
| Codex adversarial #2 | 2 (1 HIGH: numeric RecordIDs silently re-keyed as string-digit ids · 1 MEDIUM: provenance check was prefix-only, marking paths/URLs `resolved=True`) | Both fixed — see §6.1 and §6.2. Tests 186 → 189 |

### 14.2 Resolved findings register (all 9)

Every finding raised across four review passes, and how each was closed. All resolved; none deferred.

| # | Pass | Sev | Finding | Resolution |
|---|---|---|---|---|
| K1 | Karpathy #1 | — | `title`/`content_hash` threaded through three layers, added to a dict, then discarded — read as though transmitted | Parameters **removed** from router/service/builder; signature now matches the wire contract. Full allowlist retained in docs only |
| K2 | Karpathy #1 | — | Unreachable `if False` branch in a test; name claimed more than it asserted | Deleted; replaced with a parametrized transport-error test. `health()` excluded on purpose (non-raising contract) |
| K3 | Karpathy #1 | — | Every upstream 4xx collapsed to HTTP 400, blaming the caller for sidecar misconfiguration | Added `GraphRAGConfigurationError`; 401/403 and 404/405 → 502; genuine 422 stays 400 |
| K4 | Karpathy #1 | — | `List[Any]` where the element type was known, suppressing type checking | `List[GraphReferenceModel]` |
| C1 | Codex #1 | **HIGH** | `source_id` value unvalidated — a path, URL, or token could reach the sidecar through an allowlisted field name. Tests used only benign `source:abc`, proving the allowlist's *shape* not the egress *property* | Value-level validation before any network call; rejection never echoes the value; hostile values asserted to raise **and** make zero outbound requests |
| C2 | Codex #1 | MEDIUM | Diagnostic endpoint routed through fail-open `query()`, flattening 422/401/404/5xx/timeout/malformed-JSON into one 503 | Split `query_strict()` (raises, diagnostics) from `query()` (fails open, future hybrid); endpoint-level tests assert each status maps distinctly |
| F1 | RecordID forensic | **BLOCKER** | The C1 regex **rejected valid ids** — `str(RecordID)` escapes all-digit and non-alphanumeric identifiers, so the live fixture id `source:123` canonicalizes to `source:⟨123⟩` and was refused | Reported before any code change; replaced with structural validation per approved direction B (§6.1) |
| C3 | Codex #2 | **HIGH** | Numeric RecordIDs silently re-keyed: unwrapping to `str` and rebuilding `RecordID(table, str)` turned `source:123` into `source:⟨123⟩`, merging two distinct identities. The test asserted the SDK's behavior, never the validator's | Unescaped all-digit identifiers re-serialize through `int`; both identities asserted through validator **and** outbound payload |
| C4 | Codex #2 | MEDIUM | `_looks_like_record_id` was a prefix check, marking `source:../../secret` and `source:https://…?token=x` as `resolved=True` — a false trust signal | Shared structural validator (`is_valid_record_id`) widened to the provenance table set, so inbound and outbound rules cannot drift |

**Pattern worth carrying forward: three of these nine were tests that asserted the implementation rather than the property they claimed.** C1's cases were all benign; C3's asserted SDK behavior without calling the validator. Both suites passed green while the defect was live. When a test is the *evidence* for a security claim, it has to exercise the hostile case, not the happy one.
