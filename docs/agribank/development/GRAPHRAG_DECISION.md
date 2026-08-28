# AGR-005 — GraphRAG via LightRAG Sidecar, Alongside Existing Vector RAG

**Status: GraphRAG-02 APPROVED / COMPLETE** — approved 2026-08-27.
**GraphRAG-03: NOT STARTED, NOT APPROVED.**

This approval is **narrow**. It covers the isolated LightRAG PoC only — an integration boundary plus an experimental diagnostic endpoint, exercised with synthetic data. It is **explicitly NOT** approval for:

| Not approved | Still gated by |
|---|---|
| Real internal data | Boundary B decision (§6) — **still pending** |
| Production deployment | GraphRAG-07 |
| Source ingestion integration | GraphRAG-03 |
| Durable deletion implementation | GraphRAG-03 |
| Database migration (incl. outbox/tombstone) | GraphRAG-03 + its own approval |
| Hybrid retrieval / RRF / reranking | GraphRAG-05 |
| Ask / Chat integration | GraphRAG-06 |
| Authorization redesign | GraphRAG-07 |

**Boundary B (sidecar → LLM/embedding provider) remains NOT APPROVED for real internal data.** Only synthetic data, public documents, and anonymized test fixtures are permitted — in this phase and every later one, until a separate egress decision exists. **Real internal data remains PROHIBITED.** Any requirement that would breach an approved boundary is a **stop-and-report** condition, not an implementation detail.

### GraphRAG-02 completion record (verified 2026-08-27)

| Fact | Result |
|---|---|
| GraphRAG tests | **189 passed** (`test_graphrag_integration.py`, `test_graphrag_isolation.py`, `test_graphrag_recordid.py`) |
| Backend tests | **839 passed**, 5 failed |
| Baseline failures | **5, proven pre-existing** — reproduced with every GraphRAG file stashed off disk (`5 failed, 50 passed`); documented in `GRAPHRAG_POC.md` § Known Baseline Test Failures |
| Ruff | Clean — all checks passed |
| Mypy | Clean — no issues |
| Karpathy diff | Clean — 2 passes, 5 findings, all resolved |
| Codex adversarial | **No unresolved HIGH** — 2 passes, 4 findings (2 HIGH, 2 MEDIUM), all resolved |
| Canonical SurrealDB RecordID round trip | **Verified** — structural validation; escaping preserved; numeric id vs numeric-string id remain distinct through the validator *and* the outbound payload |
| LightRAG `file_source` compatibility | **Verified** against pinned v1.5.6 — canonical forms survive `normalize_file_path` → `canonicalize_parser_hinted_basename` → `Path(x).name` unchanged; no extra transport encoding required |
| Boundary A | Approved — synthetic/public/anonymized data only |
| Boundary B | **NOT approved** for internal data |
| Real internal data | **None used**; remains prohibited |
| Commit state | Approved for commit; nothing committed automatically |

| | |
|---|---|
| ID | AGR-005 |
| Date | 2026-08-27 |
| Branch | `feature/graphrag-lightrag` |
| Supersedes | — |
| Forensic basis | [`../architecture/GRAPHRAG_FORENSIC.md`](../architecture/GRAPHRAG_FORENSIC.md) rev-2 |
| Review input | Codex adversarial review, 2026-08-27, reconciled against current checkout |
| Precedence | This record wins over the forensic document where they disagree (AGRIBANK §1) |

---

## 1. Context

Open Notebook has a working, tested vector RAG path: content-core extraction → `source.full_text` → async `embed_source` → `source_embedding` → `fn::vector_search`. Ask retrieval is vector-only via a single call in `graphs/ask.py::provide_answer`.

The goal is to **add** LightRAG GraphRAG *alongside* it — hybrid retrieval — without replacing, degrading, or coupling to the existing path. LightRAG runs as an independent sidecar, is never vendored, and Open Notebook must work unchanged when it is absent.

An adversarial review challenged the GraphRAG-01 proposal on twelve axes. Six findings were raised; all six were verified against the current checkout. Four were accepted as stated, two were accepted with modification after source evidence narrowed them, and none were rejected outright. Reconciliation additionally surfaced two facts the review missed — one strengthening a finding, one weakening it. Details in §16 and forensic §"rev-2 reconciliation provenance."

---

## 2. Decision

Adopt an **additive, flag-gated, fail-open LightRAG sidecar** with:

- **Indexing:** fire-and-forget `graphrag_index_source` command submitted after `full_text` persists, with an **isolated** exception contract (§9).
- **Retrieval:** new `HybridRetriever` composing the **untouched** `vector_search()` with `GraphRAGClient`, using **rank-based fusion** (§11), gated by an explicit `RetrievalScope` (§8) and **mandatory post-query validation** (§8, §12).
- **First delivery slice:** experimental `/api/search/graph` (zero blast radius), **not** wired into Ask.
- **Feature flag:** `OPEN_NOTEBOOK_GRAPHRAG_ENABLED`, default **off**.

Rejected: vendoring LightRAG; replacing vector RAG; synchronous indexing; wrapping `vector_search()` directly; letting LightRAG parse files; storing the graph in SurrealDB. Rationale in §17 and forensic §14.

---

## 3. Source of truth

**Open Notebook / SurrealDB is authoritative for all canonical data.** Non-negotiable.

| Data | Owner |
|---|---|
| `source.full_text`, `title`, `asset`, `topics` | Open Notebook — authoritative |
| `source_embedding`, `source_insight`, `note` | Open Notebook — unchanged |
| `reference` (notebook↔source), `refers_to` | Open Notebook — **sole** authority for scope decisions |
| Entity/relation graph, sidecar vectors/KV | LightRAG sidecar — **derived, disposable, rebuildable** |

Open Notebook never reads sidecar state to establish the correctness of its own data.

---

## 4. Derived-store ownership

LightRAG's store is a **derived index**, fully re-derivable from `source.full_text` + allowlisted metadata. It may be dropped and rebuilt at any time.

Two consequences that pull in opposite directions and must both hold:

- Losing sidecar data is an **availability/quality** event, recoverable by REBUILD ⇒ indexing may fail open.
- The sidecar holds a **copy of canonical document text** ⇒ retention and deletion are confidentiality-grade concerns, and deletion may **not** fail open.

"Derived" licenses tolerance for *missing* data. It licenses nothing about *retained* data.

---

## 5. Sidecar boundary

- Separate service/container. Open Notebook **never imports LightRAG code** (no vendoring).
- Sole interface: HTTP via a thin `GraphRAGClient` (httpx, timeout, circuit-breaker, fail-open).
- Internal network only; no new broadly-exposed ports (AGRIBANK §6).
- Sidecar endpoint authenticated (shared secret / mTLS); never reachable unauthenticated.
- Sidecar **must be configured not to parse files or fetch URLs** — it receives already-extracted canonical text only, so it adds no SSRF/LFI surface. Existing `_build_content_state` guards remain the only ingress.

---

## 6. Data-egress boundaries

Two boundaries, approved **separately**. Conflating them is the primary security risk in this design.

| | Boundary | Crosses | Governance |
|---|---|---|---|
| **A** | Open Notebook → sidecar | Process/container; internal network if self-hosted | Internal network + auth design |
| **B** | Sidecar → LLM / embedding provider | **Potentially the organizational perimeter** | **Requires approved data-egress decision** |

Rules:
- Boundary A being internal **never** implies Boundary B is acceptable. A localhost sidecar with a remote provider key still exports internal text.
- LightRAG indexing typically sends **document text** to an LLM for entity/relation extraction, so Boundary B volume during indexing can approach the full corpus — not a few short queries.
- Sidecar provider configuration is **in scope for security review** even though it is not Open Notebook code.
- **GraphRAG-02 → 05: synthetic, public, or anonymized data only.** No real internal content crosses Boundary B before an approved decision.
- AGRIBANK §8 (no alternate provider clients) applies in spirit to a sidecar holding independent credentials: an approved decision must name which models it may reach, with whose keys.

---

## 7. Metadata allowlist

**Allowlist, field-by-field. Never a model dump.**

Permitted: `source_id`, `content` (canonical `full_text`), `content_hash`, `title`, `notebook_ids` (prefilter hint only), `asset_type` (sanitized enum), `contract_version`.

**Forbidden:** raw `file_path`, any local filesystem path, original or signed URL, query strings, credentials, tokens, secrets, real customer identifiers.

Open Notebook retains original URL, raw file path, asset storage details, and all credential-bearing provenance. The UI resolves these from the `source` record via `source_id`; the sidecar never needs them.

Rationale: rev-1 included `asset.{url,file_path}`. Paths leak host layout and filenames; URLs can carry internal hostnames and tokens. Neither is required for citation. A `model_dump()` is how that leaked and how future fields would leak silently — hence the explicit allowlist plus `contract_version`.

> **UPSTREAM CONSTRAINT (recorded 2026-08-27, GraphRAG-02 implementation).**
> Verified by reading LightRAG v1.5.6 router source: `POST /documents/text` accepts **only** `text`, `file_source`, and `chunking`. There is **no arbitrary metadata field**.
>
> Therefore, of the allowlist above, only two values can physically be transmitted:
> - `source_id` → sent as **`file_source`** (the sole join-key slot).
> - `canonical_text` → sent as `text`.
>
> `title`, `content_hash`, `notebook_ids`, and `contract_version` are **not sent at all** — they remain in Open Notebook and are joined locally by `source_id`.
>
> This is **strictly less egress** than this section permits, so it stays inside the approved boundary; it narrows the contract rather than widening it. The allowlist is retained in code (`ALLOWED_METADATA_FIELDS`) as the governing rule for any future upstream version that *does* accept metadata.
>
> Consequence for query results: LightRAG returns the join key as `ReferenceItem.file_path`. For documents Open Notebook indexed, that field carries **our `source_id`, never a filesystem path**. The client maps it back at the boundary so no caller sees the misleading upstream name.

---

## 8. Retrieval scope

**Metadata is not authorization. Open Notebook is the only authority.**

Verified current baseline: `vector_search()` has no notebook parameter (`domain/notebook.py:809-815`), `fn::vector_search` takes none (`migrations/9.surrealql:4`), and neither `SearchRequest` nor `AskRequest` carries `notebook_id` (`api/models.py:39-47`, `56-61`). **Today's Ask/Search are global by design.**

Therefore the requirement is stated as: GraphRAG must not **widen** any endpoint's effective scope, and must be **capable** of honoring a narrower scope when a future notebook-scoped surface needs one. GraphRAG is not a fix for global scope and must not silently narrow it — that would be an unrequested change to existing RAG.

`RetrievalScope` (design only): `notebook_ids: list[str] | None` (None = global, today's semantics), `allowed_source_ids: set[str] | None`, plus reserved room for future authorization/tenant context.

- Scope is an **explicit required parameter**. Global must be *stated*, never inferred from omission.
- `notebook_ids` may go to the sidecar as an **efficiency prefilter**; correctness never depends on the sidecar honoring it.

**Mandatory post-query validation, in order, before fusion or prompt:**
1. Resolve each returned `source_id` against **live** SurrealDB records.
2. Drop rows whose source no longer exists.
3. When scope is narrower than global, re-check `reference` edges as they are **now** — not sidecar metadata, which is stale after a move or unlink.
4. Drop unresolved, unauthorized, or out-of-scope rows.
5. Only then fuse, rerank, build payload.

**Fail-closed:** if validation cannot run, drop graph rows and return vector-only. Never pass unvalidated rows through because validation was unavailable.

---

## 9. Failure isolation

**Invariant:** with LightRAG disabled, down, slow, or misconfigured, Open Notebook behaves exactly as today.

`graphrag_index_source` submission **must not** mirror `Source.vectorize()`'s exception contract. Verified: `vectorize()` raises `DatabaseOperationError` on submission failure (`domain/notebook.py:576`) and `save_source` awaits it **unguarded** (`graphs/source.py:224`) — so copying it would let a GraphRAG queue failure fail source processing.

**Binding pattern: `Note.save()`** (`domain/notebook.py:716-727`) — submit inside `try`, log, `return None`, never raise. Its own comment states the reasoning: the record is already durably saved, so a submission hiccup must not fail an otherwise-successful operation. Exactly the GraphRAG situation.

Guaranteed under: flag off · command unregistered · `submit_command` raising · sidecar refused/timeout/5xx/401 · malformed response ⇒ **source ingestion and vector indexing still complete.**

Retrieval: `GraphRAGClient.query()` wrapped in try/except + timeout; on any failure return vector-only. Circuit-breaker prevents a dead sidecar adding per-query latency. Precedents in repo: `_usable_engine` → "auto", `text_search` → `vector_search` on overflow.

**Asymmetric policy — the central rule of this decision:**

| Operation | Policy | Because |
|---|---|---|
| INDEX | **Fail-open** | Missing graph data degrades quality; recoverable by REBUILD |
| DELETE | **Must NOT fail open** | Missing deletion **retains content** |

Availability loss and confidentiality loss are not interchangeable.

---

## 10. Delete / reindex / rebuild lifecycle

Five verbs. **Designed in GraphRAG-03, not implemented now.**

| Verb | Requirement |
|---|---|
| INDEX | Isolated, fail-open (§9) |
| REINDEX | Idempotent by `source_id` + `content_hash`; delete-then-insert (mirrors `embedding_commands.py:334-337`) |
| DELETE | **Durable.** Survives sidecar downtime and process restart |
| REBUILD | Full re-derivation from canonical text; also disaster recovery |
| RECONCILE | Periodic diff of sidecar IDs vs. SurrealDB; purge orphans; bounded and scoped |

**"An orphaned GraphRAG doc is harmless" is withdrawn.** The sidecar stores `full_text`, so an orphan is a second persistent copy of deleted internal text outside SurrealDB.

**Verified asymmetry.** Vector cleanup is enforced *in the database*:
```surql
-- migrations/1.surrealql:29
DEFINE EVENT IF NOT EXISTS source_delete ON TABLE source WHEN ($after == NONE) THEN {
    delete source_embedding where source == $before.id;
    delete source_insight  where source == $before.id;
};
```
It fires even when `Source.delete()` is bypassed — and it can be: `Notebook.delete()` unlinks non-exclusive sources without deleting them (`domain/notebook.py:274-286`), and a direct `DELETE source` runs no Python at all.

**A SurrealDB event cannot make an outbound HTTP call.** GraphRAG deletion has no database-level backstop, so application-level best-effort is strictly weaker than what vector RAG already guarantees, and a lost delete is silent and permanent.

**Defense in depth — both required, neither sufficient alone:**
1. Durable deletion (§10) — bounds how long stale data persists.
2. Query-time validation (§8) — makes stale rows non-exploitable even mid-race.

Mechanism selection (outbox/tombstone table vs. retried command vs. reconcile-only) is a **GraphRAG-03 decision**. **No schema or migration in GraphRAG-01 or -02.**

---

## 11. Fusion strategy

| # | Strategy | Same embedding space? | Also needs |
|---|---|---|---|
| A | Shared ANN index / direct vector comparison | **Yes** — model, dimension, normalization, metric | Identical preprocessing |
| B | Weighted fusion of **raw** scores | Necessary, **not sufficient** | **Score calibration** |
| C | **RRF** (rank-only) | **No** | Stable per-system ordering |
| D | `source_id` / set union | **No** | Shared join key (§7 provides it) |
| E | Late cross-encoder / LLM rerank | **No** | Re-scores original text |

**Decision: RRF (C) for the first hybrid slice, optionally followed by late rerank (E).**

- **Raw-score weighted fusion (B) is prohibited in the first slice.** Graph relevance and cosine similarity are different quantities on different distributions; summing them is arithmetically valid and semantically meaningless. Revisit only with a measured calibration method on a synthetic evaluation set.
- **Strategy A is out of scope** — it would require sharing Open Notebook's ANN index, contradicting sidecar-owns-its-store (§3, §5).

---

## 12. Embedding compatibility rules

**The assumption that LightRAG must use Open Notebook's provider or embedding model is rejected as stated.** It is true only for one strategy.

- **Identical embedding spaces are required for strategy A only.** C, D, and E impose **no** constraint on the sidecar's embedding model, dimension, or provider.
- **The converse also fails:** same provider and model does *not* make LightRAG graph scores commensurate with `fn::vector_search` cosine similarity. B needs explicit calibration regardless of model identity.
- Because the chosen strategy is C, **the sidecar may use a different embedding model** — subject to §6 Boundary B approval, which is a *data-egress* question, not a compatibility one. These two concerns were previously conflated.

Practical consequence: embedding-model choice is removed from the critical path. It becomes a tunable, not a coupling.

---

## 13. Citation / provenance ownership

**LightRAG is not a citation authority. Open Notebook is.** The sidecar proposes evidence; Open Notebook decides what is citable.

Verified mechanics: `provide_answer` builds the allowed-ID set from `ids = [r["id"] for r in results]` (`graphs/ask.py:110`), and `prompts/ask/query_process.jinja` instructs the model to use only those IDs, exactly, with type prefixes intact. **`r["id"]` must be a live, resolvable record ID** — a synthetic entity ID here becomes a citation the UI cannot resolve.

Staged requirement:

**GraphRAG-02 — `/api/search/graph` only.** A GraphRAG-specific diagnostic schema (entity/relation IDs, graph scores, debug fields) is acceptable **because the endpoint feeds no prompt and emits no citation.** Must be labeled experimental/diagnostic and consumed by no prompt-building path.

**Before any GraphRAG row reaches Ask / Chat / a hybrid prompt (GraphRAG-06):**

| Field | Rule |
|---|---|
| `id` | **Existing Open Notebook record ID**, verified live. Never a synthetic LightRAG ID |
| `parent_id` | Same semantics as `fn::vector_search` |
| `title` | From the live record, not sidecar metadata |
| `matches` | Traceable supporting text spans, same shape as the vector path |
| `source_ids` | Full contributing set for multi-source entities, each validated |
| invalid rows | **Dropped** before prompt construction — never repaired or coerced |

**Calibration against existing behavior:** `fn::vector_search` already returns source-chunk rows whose `id` *is* the source (`source.id as id, source.id as parent_id`, `migrations/9.surrealql:8-11`) with chunk text in `matches` (`:62`). Source-granular citation with supporting text is the **existing** contract. Graph rows must be **no weaker** — they need not invent finer granularity than vector RAG has.

A graph assertion that cannot be traced to a live record with supporting text is a **ranking signal**, not a citation.

---

## 14. Rollback / removability criteria

Must be **demonstrated**:

1. Flag off ⇒ byte-for-byte baseline; zero new latency.
2. Sidecar removed from deployment ⇒ vector search, Ask, Chat, ingestion all work.
3. Sidecar datastore deleted ⇒ vector RAG unaffected.
4. Pending/stale `graphrag_index_source` jobs ⇒ worker starts cleanly; other commands unaffected.
5. Source deletion works when GraphRAG is disabled, unreachable, and mid-outage.
6. Deleting `open_notebook/graphrag/`, the flag, and the sidecar returns the repo to baseline with no orphaned imports or dead migrations.

**No hard dependency** from `Source` domain, `vector_search`, the Ask graph, or worker startup onto LightRAG runtime.

**Can LightRAG be removed without breaking existing vector RAG?** Yes — **provided** no migration touches core tables and no existing call site hard-depends on it. Both properties hold under the additive design and must be re-verified at every phase gate.

---

## 15. Security constraints

- Never commit secrets; sidecar credentials via env/secret manager (`OPEN_NOTEBOOK_ENCRYPTION_KEY` pattern).
- No real customer or production data in fixtures, tests, logs, prompts, screenshots, or agent memory (AGRIBANK §4, §6).
- Do not weaken existing SSRF validation, LFI guards, upload limits, or auth controls.
- Sidecar and store stay on the internal network; no new broadly-exposed ports.
- Sidecar authenticated; never unauthenticated.
- No alternate provider clients inside Open Notebook (AGRIBANK §8); sidecar provider config requires explicit approval (§6).
- Security-sensitive changes require focused tests and independent review (AGRIBANK §6, §11).
- Treat authentication, authorization, audit logging, credential storage, file ingestion, provider access, and backup/restore as security-sensitive throughout.

---

## 16. Phase boundaries

| Phase | Scope | NOT in scope |
|---|---|---|
| **GraphRAG-01** | Forensic + architecture decision (this record) | Any code |
| **GraphRAG-02** | Isolated `GraphRAGClient` + sidecar + health probe + experimental `/api/search/graph` | **No** Source-pipeline wiring, **no** Ask wiring, **no** migration, **no** real data |
| **GraphRAG-03** | INDEX/REINDEX/DELETE/REBUILD/RECONCILE + durable-deletion mechanism | Ask wiring; fusion |
| **GraphRAG-04** | Graph retrieval evaluation on synthetic dataset | Real data; Ask wiring |
| **GraphRAG-05** | `HybridRetriever` + RRF/rank fusion + `RetrievalScope` | Raw-score weighted fusion; Ask wiring |
| **GraphRAG-06** | Ask integration behind flag + **enforced citation contract** | Enabling by default |
| **GraphRAG-07** | Security, authorization, operational hardening, runbook | — |

---

## 17. Rejected alternatives

| # | Alternative | Rejected because |
|---|---|---|
| A1 | Vendor LightRAG into the repo | Violates no-vendoring; couples upstream merges to LightRAG internals; drags in dependencies |
| A2 | Replace vector RAG with GraphRAG | Explicit constraint; discards a working tested path; loses fail-open |
| A3 | Index synchronously in ingestion | Adds sidecar latency to source processing |
| A4 | Hybrid by wrapping `vector_search()` (R2) | Changes existing RAG for every caller; not additive |
| A5 | Let LightRAG ingest raw files/URLs | Double extraction; duplicate SSRF/LFI surface; loses canonical extractor |
| A6 | Store the graph in SurrealDB | Needs migration; couples graph store to primary DB |
| A7 | Mirror `Source.vectorize()`'s exception contract | **Breaks fail-open** — propagates submission failure into `save_source` (§9) |
| A8 | Best-effort-only deletion | Leaves a persistent copy of deleted text with **no** DB-level backstop (§10) |
| A9 | Sidecar-side `notebook_ids` filtering as the scope mechanism | Metadata is not authorization; makes the sidecar an access-control authority (§8) |
| A10 | Raw-score weighted fusion in the first slice | Incommensurate score distributions; needs calibration not yet designed (§11) |
| A11 | Require the sidecar to share Open Notebook's embedding model | Only needed for strategy A, which is out of scope; conflates egress with compatibility (§12) |
| A12 | Send `asset.{url,file_path}` in the metadata contract | Leaks host paths and possibly tokens; unnecessary — citation joins on `source_id` (§7) |

**Deletion-durability options — deferred to GraphRAG-03, not decided here:**

| Option | Strength | Cost |
|---|---|---|
| Outbox/tombstone table + retried worker | Strongest; survives restarts | **Requires a migration** — needs its own approval |
| Retried command only, no persistence | No migration | Lost if the queue is lost |
| RECONCILE-only, no delete hook | Simplest | Orphans persist until the next sweep |
| **Hybrid (likely):** best-effort immediate + durable retry + periodic RECONCILE | Bounded staleness, layered | Most moving parts |

---

## 18. Open questions (must be answered before the phases noted)

1. **[GraphRAG-02]** Boundary B: which provider/models may the sidecar reach, with whose credentials? Blocks any real-data use.
2. **[GraphRAG-03]** Deletion durability mechanism — and if an outbox table is chosen, does that migration get approved?
3. **[GraphRAG-03]** RECONCILE cadence and scope bounds; who operates it.
4. **[GraphRAG-04]** Synthetic evaluation corpus: what proves GraphRAG adds retrieval value over vector-only? Without this, later phases optimize an unmeasured system.
5. **[GraphRAG-05]** RRF `k` parameter and per-system weights.
6. **[GraphRAG-06]** Does any *current* surface actually need notebook-scoped retrieval, or does global remain correct? Determines whether `RetrievalScope` ships used or reserved.
7. **[GraphRAG-06]** Does Chat / Source Chat context assembly (`context_builder.py`, explicit-inclusion based) need graph augmentation, or is Ask sufficient?
8. **[GraphRAG-07]** Sidecar authentication mechanism: shared secret vs. mTLS.
9. **[GraphRAG-07]** Sidecar store backup/restore and retention policy — it holds canonical text copies.
10. **[GraphRAG-03]** Retention/purge SLA for sidecar copies of deleted sources: what maximum staleness window is acceptable?

---

## 19. Acceptance criteria for GraphRAG-02

GraphRAG-02 is complete **only** when all of the following pass with recorded evidence. An agent asserting completion is not completion (AGRIBANK §10).

**Scope compliance**
1. No change to `vector_search`, `text_search`, `fn::vector_search`, `fn::text_search`.
2. **No migration added or altered.**
3. No change to existing API request/response models.
4. No change to `frontend/`.
5. **No Source-pipeline wiring** — `graphs/source.py` untouched in this phase.
6. **No Ask wiring** — `graphs/ask.py` untouched in this phase.
7. New code confined to new modules + one `include_router` line + config/env additions.

**Functional**
8. `GraphRAGClient` with timeout, circuit-breaker, fail-open.
9. Health probe reports sidecar status without raising.
10. `/api/search/graph` returns a **diagnostic** schema, explicitly labeled experimental, consumed by no prompt path.
11. Flag `OPEN_NOTEBOOK_GRAPHRAG_ENABLED` defaults **off**; off ⇒ no client instantiated.

**Failure isolation** (forensic §18, tests 1-7)
12. Flag off, command unregistered, `submit_command` raising, connection refused, timeout, malformed response, 5xx and 401 — each proven not to affect source processing or vector RAG.

**Security**
13. Metadata allowlist enforced in code; **no** `asset.url` / `file_path` in any sidecar payload. Test asserts absence.
14. Sidecar authenticated; not exposed beyond the internal network.
15. Synthetic/public/anonymized data only. No real internal content crosses Boundary B.
16. No secrets committed.

**Rollback** (forensic §19, items 22-26)
17. All six removability criteria in §14 demonstrated.

**Baseline verification** (AGRIBANK §10)
18. `uv run pytest tests/` · `uv run ruff check .` · `uv run python -m mypy .` all pass.
19. Frontend baseline unaffected (no frontend change ⇒ confirm untouched).
20. Independent second-pass review of the final diff (AGRIBANK §11), given the security-sensitive surface.

---

## 20. Approval

| Item | Status |
|---|---|
| Architecture decision (§2) | ✅ Approved 2026-08-27 — **PoC scope only** |
| Data-egress Boundary A (§6) | ✅ Approved 2026-08-27 — synthetic/public/anonymized data only |
| Data-egress Boundary B (§6) | ⬜ **NOT approved** — blocks all real internal data |
| Metadata allowlist (§7) | ✅ Approved 2026-08-27 |
| Asymmetric failure policy (§9) | ✅ Approved 2026-08-27 (design); implementation deferred to GraphRAG-03 |
| Fusion strategy = RRF (§11) | ✅ Approved as direction; implementation deferred to GraphRAG-05 |
| Phase boundaries (§16) | ✅ Approved 2026-08-27 |
| GraphRAG-02 acceptance criteria (§19) | ✅ Approved 2026-08-27, extended by §21 |
| **GraphRAG-02 delivery** | ✅ **COMPLETE** — accepted 2026-08-27 against §21.12 |

**GraphRAG-02 is COMPLETE within §21. Phases 03-07 remain unapproved and not started.**

---

## 21. GraphRAG-02 approved scope (PoC)

Authorized 2026-08-27. Supplements §19; where they differ, this section is narrower and wins.

### 21.1 Phase goal

An **isolated integration boundary** between Open Notebook and LightRAG. Open Notebook must be able to: know whether GraphRAG is enabled · check sidecar health · push a synthetic document through the service directly · run an experimental graph query · receive normalized diagnostic results · fail open when the sidecar misbehaves.

**Not wired into real source ingestion.**

### 21.2 Architecture boundary

Preferred structure (final naming follows whatever the existing source conventions show is a better fit):

```
open_notebook/integrations/graphrag/
    __init__.py  ·  client.py  ·  config.py  ·  models.py  ·  service.py
```

All LightRAG-specific HTTP/API behavior lives **behind** this boundary. LightRAG URL handling, response parsing, exceptions, and HTTP code must not leak into domain, the Ask graph, or generic retrieval code.

### 21.3 Configuration

Minimum: `OPEN_NOTEBOOK_GRAPHRAG_ENABLED` (default **false**) · `OPEN_NOTEBOOK_GRAPHRAG_BASE_URL` · `OPEN_NOTEBOOK_GRAPHRAG_TIMEOUT`. Any additional key requires a documented reason. Flag false ⇒ baseline behavior unchanged. No secrets in the repo.

### 21.4 Client contract

`GraphRAGClient` isolates LightRAG's HTTP contract. Capabilities: `health()`, `index_document(...)` (**manual/synthetic PoC calls only**), `query(...)`.

`index_document` must **not** be wired into `Source.save()`, `save_source()`, `process_source`, `vectorize()`, or worker ingestion.

Where upstream semantics differ, adapt **inside the client** rather than letting Open Notebook depend on LightRAG's schema. Normalize timeout, connection refused, HTTP error, malformed JSON, and incompatible response into typed internal integration errors matching existing conventions.

### 21.5 Service contract

`GraphRAGService` is the facade Open Notebook calls. It enforces the feature flag, timeout, metadata allowlist, contract version, and call-site-appropriate fail-open semantics.

**No `model_dump()` of a `Source` into the sidecar payload.** Allowlist: `source_id`, `title`, `content_hash`, `notebook_ids` (only when the caller supplies them), `canonical_text`, `contract_version`. Never: `asset.file_path`, raw filesystem paths, `asset.url`, original/signed URLs, API keys, tokens, credentials, arbitrary model metadata. A genuine upstream requirement for another field must be documented before being added.

### 21.6 Experimental API

An experimental diagnostic endpoint (e.g. `/api/search/graph`; final name follows existing API conventions). It must not replace the existing search endpoint, and must not change `vector_search()`, Ask, or Chat. Not used by any production frontend path.

Its response may be **graph-native**, but must make explicit that it is **not** the Open Notebook citation contract. Synthetic LightRAG entity IDs must never be presented as Open Notebook citation IDs.

### 21.7 Retrieval scope

Do not redesign existing global search semantics. If the endpoint exposes scope, **global must be represented explicitly** — never inferred from a missing value. `RetrievalScope` may be implemented only if genuinely needed by this integration contract; otherwise document the interface and defer (no overengineering).

### 21.8 Failure isolation — must be demonstrated

Flag false ⇒ baseline · sidecar absent ⇒ startup succeeds · connection refused ⇒ controlled error, no crash · timeout ⇒ controlled result/error · malformed response ⇒ controlled integration error · LightRAG removed ⇒ vector RAG works. **No import-time dependency** may prevent startup when LightRAG is absent.

### 21.9 Prohibited in GraphRAG-02

No changes to `Source.save()`, `save_source()`, `process_source`, `Source.vectorize()`, `vector_search()`, `text_search()`, the Ask graph, or the Chat graph. No production `graphrag_index_source` command, outbox, tombstone table, or DB migration. No durable deletion, production reindex lifecycle, reconciliation job, `HybridRetriever`, production RRF, or production reranking. No production frontend change. **No real internal data.**

### 21.10 Required tests (≥16)

feature disabled · healthy sidecar · connection refused · timeout · HTTP 4xx · HTTP 5xx · malformed JSON · unexpected LightRAG schema · synthetic index success · query success · metadata allowlist · `file_path` not sent · URL not sent · secret-like extra fields not dumped to sidecar · imports/startup independent of LightRAG availability · existing vector retrieval regression.

**Mock the HTTP boundary. No real remote provider calls in automated tests.**

### 21.11 Sidecar

PoC sidecar lives outside core Open Notebook source; LightRAG is **not vendored** into the package. Check existing deployment conventions before adding Docker/config. Production compose must not hard-depend on LightRAG.

### 21.12 Definition of done

Boundary fully isolated · flag default OFF · baseline runs without LightRAG · synthetic document indexable via the PoC path · experimental query works · failures normalized · metadata allowlist verified · no real internal data · no existing RAG path modified · no ingestion path modified · no DB migration · tests pass · Karpathy diff clean or findings resolved · **no unresolved HIGH findings from independent Codex review**.

Verification: targeted GraphRAG tests → relevant backend regression → `ruff` → `mypy` (if changed files are in the typechecked surface) → `git diff` inspection → `/karpathy:diff` → independent Codex review. **No automatic commit.**

Any requirement that would breach an approved boundary ⇒ **stop and report.**
