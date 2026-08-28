# GraphRAG-03 — Source Lifecycle Forensic & Architecture Design

**Status:** FORENSIC + DESIGN ONLY — **NOT APPROVED, NOT IMPLEMENTED.**
**Date:** 2026-08-28 · **Baseline:** `bc5b413` (GraphRAG-02 checkpoint) · **Branch (actual):** `feature/graphrag-lightrag`
**LightRAG pinned:** `v1.5.6` — every upstream claim below was read from source at `?ref=v1.5.6`, never guessed.

> **Branch note.** The session brief names `feature/graphrag-lifecycle`. No such branch exists in this checkout; HEAD is `bc5b413` on `feature/graphrag-lightrag`, which is the stated GraphRAG-02 checkpoint. The forensic proceeded on the existing branch. Branch creation is left to the user.

> **Precedence (AGRIBANK §1).** Where this document disagrees with `GRAPHRAG_DECISION.md` (AGR-005), the decision record wins. This document is forensic input to a GraphRAG-03 decision that does not yet exist.

> **Egress.** Design and any future testing remain synthetic / public / anonymized only. Boundary B (sidecar → LLM/embedding provider) is **NOT approved**. Real internal data is prohibited. Nothing here implies otherwise.

---

## 0. Non-negotiable invariants (carried from AGR-005)

1. Open Notebook / SurrealDB `source` is the **sole source of truth**. LightRAG is **derived, rebuildable, removable**.
2. Existing vector RAG is independent and untouched.
3. `OPEN_NOTEBOOK_GRAPHRAG_ENABLED=false` ⇒ byte-for-byte baseline behavior.
4. LightRAG absent/down must not block Open Notebook startup, canonical ingestion, or vector RAG.
5. **INDEXING MAY FAIL OPEN. DELETION MAY NOT FAIL OPEN INDEFINITELY.** An index miss is availability/quality (recoverable by REBUILD). A delete miss is confidentiality/retention — the sidecar holds a copy of canonical text.

---

## 1. Current lifecycle map

```
CREATE (API /sources)
  └─ source.save()                     [record exists, full_text empty] (sources.py:502)
  └─ add_to_notebook() per notebook    (reference edge)                 (sources.py:507)
  └─ submit process_source (async)                                       (sources.py:522)
        └─ WORKER: process_source_command                               (source_commands.py:50)
              └─ source_graph.ainvoke
                    ├─ content_process   (content-core extraction)      (source.py:75)
                    └─ save_source
                          ├─ source.full_text = extraction.content      (source.py:210)  ◄── ONLY writer of full_text
                          ├─ await source.save()                        (source.py:216)
                          └─ if embed: await source.vectorize()         (source.py:224)
                                └─ submit embed_source (async, fire-and-forget)
                                      └─ WORKER: delete+insert source_embedding (embedding_commands.py:334)

UPDATE
  ├─ PUT /sources/{id}: title / topics ONLY — never full_text           (sources.py:905-919)
  └─ POST /sources/{id}/retry: re-runs process_source → new full_text   (sources.py:934)

DELETE
  ├─ DELETE /sources/{id} → Source.delete()                             (sources.py:1063 → notebook.py:642)
  ├─ create/retry rollback → Source.delete()                            (sources.py:551,559,614)
  ├─ Notebook.delete(delete_exclusive_sources=True) → Source.delete()   (notebook.py:266)
  ├─ Notebook.delete() default → UNLINK only (source survives)          (notebook.py:274-286)
  ├─ ObjectModel.delete() → repo_delete(id)                             (base.py:210)
  └─ raw SurrealQL `DELETE source` (no Python runs)
        └─ ALL of the above fire DB EVENT source_delete → cascade
              delete source_embedding + source_insight                  (1.surrealql:29-32)
```

**Key asymmetry.** Vector-store cleanup is guaranteed *inside the database* by `source_delete`. It fires on every delete path, including raw SurrealQL that runs no Python. **A SurrealDB event cannot make an outbound HTTP call**, so GraphRAG has no DB-level backstop. Any GraphRAG cleanup is application-level and strictly weaker than what vector RAG already enjoys.

---

## 2. Create / index path — safest enqueue point

`full_text` is durable after `await source.save()` at `graphs/source.py:216`. The safest place to enqueue a future `graphrag_index_source` is **immediately after the `vectorize()` block (`source.py:221-228`), inside `save_source`**, gated by `source.full_text` being non-empty and by the feature flag.

Rationale, verified:
- This is the same node where `vectorize()` already fires; it runs in the worker, off the request path.
- The command is fire-and-forget; latency does not touch ingestion.
- **Exception contract must copy `Note.save()` (`notebook.py:716-727`), not `vectorize()`.** `vectorize()` raises `DatabaseOperationError` on submission failure (`notebook.py:576`) and `save_source` awaits it **unguarded** (`source.py:224`) — so mirroring it would let a GraphRAG queue hiccup fail source processing. `Note.save()` submits inside `try`, logs, returns `None`, never raises. That is the fail-open contract required by invariant §0.5 for INDEX.

Enqueue seam options (ranked in §16): a direct `submit_command` in `save_source`, vs. a thin lifecycle-hook indirection so `graphs/source.py` never names GraphRAG.

**Failure of GraphRAG enqueue here is availability-only** and is recovered by REBUILD/RECONCILE. It is allowed to fail open.

---

## 3. Update / reindex path

**What "source update" means in Open Notebook (verified):**

| Change | Path | Touches `full_text`? | Needs REINDEX? |
|---|---|---|---|
| Title / topics edit | `PUT /sources/{id}` (`sources.py:914-919`) | No | **No** — LightRAG never received title (it is not transmitted; AGR-005 §7 upstream constraint) |
| Retry processing | `POST /sources/{id}/retry` → `process_source` (`sources.py:1011`) | **Yes** (re-extracts) | **Yes** |
| Re-create / re-ingest | `process_source` (`source.py:210`) | Yes | Yes |
| Notebook membership add/remove | `reference` edge (`notebook.py:284`, `source.add_to_notebook`) | No | **No** (see §6) |
| Insights / embeddings | separate tables | No | No — GraphRAG indexes `full_text` only |

**Conclusion:** the *only* content change that requires REINDEX is a `full_text` rewrite, and `full_text` has exactly one writer (`source.py:210`). REINDEX can therefore be co-located with INDEX at the same enqueue seam — the same command, made idempotent.

**Staleness detection.** There is **no `content_hash` field anywhere in the repo** (verified: no match for `content_hash|md5|sha256|hashlib`). The only change signal is `source.updated` (`1.surrealql:14`, `VALUE time::now()`), bumped on every save. LightRAG stores a `content_hash` internally (`pipeline.py:1046`) but **does not expose it** on `DocStatusResponse` (`document_routes.py:1003`). Therefore staleness cannot be read back from the sidecar and must be tracked Open Notebook-side.

**Do not invent a hash field unless required.** Two viable staleness signals already exist without a migration:
- `source.updated` timestamp vs. a per-source "last indexed at" marker, or
- LightRAG `DocStatusResponse.updated_at` vs. `source.updated` (both enumerable), accepting timestamp-only granularity.

A dedicated `content_hash` gives exact change detection but is a schema addition (§17). **Recommendation: defer the hash; use delete-then-insert keyed on the stable doc_id (§7, §12), which makes REINDEX correct regardless of whether content actually changed.**

**Critical upstream constraint (verified `pipeline.py:1121-1170`):** re-inserting the same `file_source` is **rejected as `duplicate_kind="filename"`**, never treated as an update. **REINDEX MUST be delete-then-insert**, mirroring the existing `embed_source` idempotency pattern (`embedding_commands.py:334-337`, "DELETE existing then bulk insert").

---

## 4. Delete paths (complete enumeration)

| # | Entry point | Runs Python `Source.delete()`? | Fires DB event? | GraphRAG cleanup reachable in Python? |
|---|---|---|---|---|
| 1 | `DELETE /sources/{id}` (`sources.py:1063`) | Yes (`notebook.py:642`) | Yes | Yes |
| 2 | Create rollback (`sources.py:551,559`) | Yes | Yes | Yes — but source was never indexed; delete is a no-op-safe call |
| 3 | Sync-path rollback (`sources.py:614`) | Yes | Yes | Yes |
| 4 | `Notebook.delete(delete_exclusive_sources=True)` (`notebook.py:266`) | Yes (per exclusive source) | Yes | Yes |
| 5 | `Notebook.delete()` **default** (`notebook.py:274-286`) | **No** — UNLINK only, source survives | No (source not deleted) | **N/A — must NOT delete** (§6) |
| 6 | `ObjectModel.delete()` on a `Source` (`base.py:210`) | Yes (Source overrides it) | Yes | Yes |
| 7 | **Raw SurrealQL `DELETE source`** (maintenance/admin/migration) | **No Python at all** | **Yes** | **NO — the bypass hole** |

**The confidentiality hole is path #7** (and any future code path that deletes via `repo_query` rather than `Source.delete()`). The DB event cleans vector data but cannot call LightRAG. A Python-side delete hook in `Source.delete()` covers paths 1-4 and 6 but is **silently bypassed** by path 7. This is why "best-effort delete in `Source.delete()`" is **not** an acceptable final architecture (AGR-005 A8).

---

## 5. Database-event implications

`DEFINE EVENT source_delete ON TABLE source WHEN ($after == NONE)` (`1.surrealql:29-32`) is the model to *emulate the guarantee of* but cannot be *reused for* GraphRAG:

- **What it proves:** the project already relies on DB-enforced derived-store cleanup that survives Python bypass. That is the bar GraphRAG deletion is measured against.
- **Why it can't call LightRAG:** SurrealDB events execute SurrealQL only — no HTTP, no external I/O. Confirmed by the event body (pure `delete` statements).
- **What it *could* do for GraphRAG:** an event could **write a durable record** (a tombstone/outbox row) inside the same delete transaction. That row is then drained by a worker/reconcile that *can* make HTTP calls. This converts path #7 from "invisible" to "recorded," closing the bypass hole at the database boundary — the one place all delete paths converge.

This is the single most important architectural lever in GraphRAG-03.

---

## 6. Notebook-unlink semantics — eligibility ≠ membership

Separate three concepts:

| Concept | Authority | GraphRAG relevance |
|---|---|---|
| **Source existence** | `source` record in SurrealDB | Deleting the record ⇒ delete from LightRAG |
| **Notebook membership** | `reference` edges (`notebook.py`) | **Irrelevant to index existence** |
| **Index eligibility** | Source exists AND has `full_text` | Governs whether it should be indexed at all |

`Notebook.delete()` default unlinks (`DELETE reference WHERE out = $notebook_id`, `notebook.py:284`) — the source persists and remains eligible. **A LightRAG document MUST NOT be deleted merely because one notebook reference is removed.** Only when the canonical `source` record itself is deleted (paths 1-4, 6, 7) does GraphRAG deletion become mandatory.

Today Ask/Search are **global** (verified in AGR-005 §8: no `notebook_id` on `vector_search`, `fn::vector_search`, `SearchRequest`, or `AskRequest`). GraphRAG-03 does not change this. `notebook_ids` remains at most a retrieval **prefilter hint** (GraphRAG-05), never a deletion trigger.

---

## 7. Identity contract

**Join identity is the canonical Open Notebook RecordID.** Carried across the boundary as LightRAG `file_source` (the sole metadata slot; AGR-005 §7 upstream constraint).

**Decisive verified fact — doc_id is locally computable and content-stable:**

`apipeline_enqueue_documents` (`pipeline.py:936-946`):
```
known_source = has_known_document_source(normalize_document_file_path(file_source))
if ids is not None:        doc_id = ids[index]              # not our path
elif known_source:         doc_id = "doc-" + md5(canonical) # ◄── OUR PATH
elif RAW:                  doc_id = "doc-" + md5(content)
else:                      doc_id = "doc-" + md5(f"{canonical}-{track_id}-{index}")
```
- `normalize_document_file_path` (`utils_pipeline.py:237`) strips `[hint]` segments and collapses `{"", "no-file-path", "unknown_source"}` (`utils_pipeline.py:55`) to `"unknown_source"`.
- `has_known_document_source` (`utils_pipeline.py:258`) is true for any non-placeholder source.
- `compute_mdhash_id` = `prefix + md5(str(arg))`, single-arg algorithm stable across upgrades (`utils.py:680,794`).

Because Open Notebook always sends a valid `source_id`, **`doc_id = "doc-" + md5(canonical_source_id)`** — deterministic, computable Open-Notebook-side without any list/search call, and **unchanged when content changes**. This is what makes DELETE and RECONCILE cheap and reliable.

**RecordID discipline (from GraphRAG-02, preserved):** validate structurally via `_validate_record_id` / `validate_source_id` (`integrations/graphrag/service.py:33-50`). `source:123` (numeric id) and `source:⟨123⟩` (string "123") are **distinct** and must stay distinct — no lossy sanitization. Lifecycle commands must **persist/serialize the canonical id exactly as validated** (the same canonical form fed to `file_source`), so the md5 join key is identical on index and delete. A serialization that normalizes numeric vs. numeric-string would compute a different doc_id and orphan the real one.

---

## 8. Existing command durability semantics (read from installed `surreal_commands`)

| Property | Behavior | Source |
|---|---|---|
| Persistence | `submit_command` → `db.create("command", {status:"new"})` — a durable SurrealDB row | `core/service.py:154-167` |
| Survives worker restart | Boot scan `SELECT * FROM command WHERE status='new' ORDER BY created ASC`, then LIVE query | `core/worker.py:110-111,143` |
| Survives API restart | Row is in the DB, independent of the submitting process | `core/service.py:114` |
| Retry | In-process tenacity only; `status` stays `"running"` across attempts | `core/service.py:243-254` |
| Terminal states | `completed` / `failed` / `canceled` written at end | `core/service.py:293-312` |
| **Crash mid-execution** | `status="running"` set **before** execution (`:225`); **no lease, no heartbeat**. Boot scan only re-picks `"new"`. | `core/service.py:225` + `worker.py:110` |

**The missing property for durable deletion:** *crash / terminal-failure re-drive.* A delete job that reaches `"running"` and then the worker crashes is **stuck forever**; a job that exhausts retries goes `"failed"` and is **never retried again**. The commands router (`api/routers/commands.py`) exposes submit / status / list / cancel — **no re-drive of stuck or failed jobs**. There is **no scheduler dependency** in the project (no APScheduler/Celery/cron in `pyproject.toml`).

⇒ The command queue is durable **for submission and worker-outage**, but **not** for crash-during-execution or exhausted-retry. Deletion needs a re-drive mechanism the queue does not provide.

---

## 9. Failure matrix

Desired-state legend: **A** = availability/quality only (may fail open); **C** = confidentiality/retention (must eventually converge).

| # | Scenario | Class | Desired state | Recovery |
|---|---|---|---|---|
| 1 | Canonical save OK, GraphRAG enqueue fails | A | Source fully processed; no graph doc | REBUILD/RECONCILE re-indexes; INDEX fails open (`Note.save()` contract) |
| 2 | Queue (SurrealDB) unavailable at enqueue | A (index) / **C (delete)** | Index: skip, log. Delete: **must be recorded durably before the canonical delete commits** | Index → reconcile. Delete → tombstone written in same txn (§5) |
| 3 | Worker unavailable | A/C | Jobs stay `status="new"`, drained on worker start | Built-in boot scan (`worker.py:110`) |
| 4 | Worker crash after dequeue (`running`) | A/C | Index: reconcile re-drives. Delete: **must be re-driven** | Reconcile detects orphan (delete) or missing doc (index); command queue alone will NOT re-drive |
| 5 | LightRAG connection refused | A/C | Index: fail open. Delete: retry + tombstone persists | Circuit-breaker; reconcile sweep |
| 6 | LightRAG timeout | A/C | Same as #5 | Same |
| 7 | LightRAG 4xx/5xx | A/C | Normalized error; delete tombstone retained until success | Retry with backoff; `busy` (see #18) is retry-needed |
| 8 | Index succeeds, ack lost | A | Doc exists; Open Notebook thinks it failed | Idempotent re-index = delete-then-insert (no duplicate; §12) |
| 9 | Duplicate INDEX | A | One doc (same doc_id) | Upstream filename-dedup + our delete-then-insert idempotency |
| 10 | Duplicate DELETE | C→A | Second delete is a no-op on absent doc_id | Delete idempotent by doc_id; `not_allowed`/absent treated as success |
| 11 | REINDEX races DELETE | **C** | Final state = **deleted** (delete wins) | Order by canonical truth at drain time: if source no longer exists, drop the reindex |
| 12 | DELETE races INDEX | **C** | Final state = deleted | Delete drains after checking canonical existence; reconcile purges any late orphan |
| 13 | Source deleted while index job pending | **C** | No doc remains | Index job checks canonical existence before insert; reconcile backstop |
| 14 | Source modified while old reindex pending | A | Latest content indexed | delete-then-insert with newest `full_text`; stale job superseded |
| 15 | Sidecar datastore lost | A | Graph empty | REBUILD from canonical `full_text` |
| 16 | Stale GraphRAG orphan exists | **C** | Orphan purged | RECONCILE diff by doc_id |
| 17 | App upgraded with jobs pending | A/C | Jobs resume | Durable rows survive; contract_version guards payload shape |
| 18 | Flag turned OFF with jobs pending | A/C | **Deletes must still drain** | Deletion drain path must NOT be gated by the enable flag (only indexing is); see §18 recommendation |
| 19 | LightRAG completely removed | A | Vector RAG unaffected; deletes are no-ops | Tombstones age out once sidecar confirmed gone (operator action) |
| 20 | Sidecar returns `status="busy"` on delete | **C** | Not deleted yet | Treat as transient; retry (do NOT count as success) — verified `document_routes.py:6114` |
| 21 | Two workers drain same tombstone | A | One delete | Idempotent by doc_id; claim/lease or dedup on drain |

**Row 18 is a design trap:** if the deletion drain is gated behind `OPEN_NOTEBOOK_GRAPHRAG_ENABLED`, turning the flag off with a copy of deleted text still in the sidecar violates §0.5. Deletion convergence must be independent of the *indexing* flag.

---

## 10. Candidate durable-delete designs

| Option | Atomic w/ canonical delete? | Worker down | LightRAG down | Crash between DB delete & enqueue | Idempotent | Stale-job handling | Observability | Complexity | Migration | Upstream coupling | Removable |
|---|---|---|---|---|---|---|---|---|---|---|---|
| **1. DB-event tombstone/outbox** (event writes `graphrag_deletion` row in the delete txn; worker drains) | **Yes** — same txn as `DELETE source`; covers raw SurrealQL (path #7) | Row persists, drained later | Row retained until 2xx/absent | **Covered** — row committed by the event, not by app code | Yes (doc_id) | Reconcile as backstop | Query the table | Medium | **Yes (1 table + 1 event)** | Low — additive event + table, no core edits | Drop table+event ⇒ baseline |
| **2. Tombstone written by `Source.delete()` (app-side)** | No — separate write; **misses path #7** | Persists | Retained | Partially — app crash before write loses it | Yes | Reconcile | Query table | Medium | Yes (1 table) | Medium — edits `Source.delete()` | Drop table + hook |
| **3. Retried command only (no persistence beyond command row)** | No | Command row persists (`new`) | Retried in-process | **Lost if crash leaves `running`** (§8) | Yes | **None** — stuck `running` never re-driven | Command list | Low | No | Low | Remove command |
| **4. RECONCILE-only (no delete hook)** | N/A | N/A | Sweep later | N/A | Yes | Is the mechanism | Sweep report | Low–Med | No | Low | Remove job |
| **5. Hybrid: best-effort immediate delete + durable record + periodic reconcile** | Best-effort now; **durable record for guarantee** | Record persists | Retry + reconcile | Covered iff the durable record is option-1 style | Yes | Reconcile bounds latency | Table + sweep report | Med–High | Depends on record choice | Low if record via event | Layered removal |

**The pivotal question — "how to guarantee eventual removal when canonical delete can happen through DB semantics that cannot make HTTP calls":**

- **Option 3 alone fails** the crash case (§8) — not durable enough.
- **Option 2 alone fails** path #7 (raw SurrealQL) — the exact bypass that raised R7 to High in GraphRAG-01.
- **Option 4 alone** is correct but **unbounded in latency** — a copy of deleted text lingers until the next sweep, and there is no scheduler, so "next sweep" is operator-driven.
- **Option 1** is the only mechanism that is **atomic with the canonical delete AND covers every path including raw SurrealQL**, because the DB event is where all delete paths converge (§5). It needs a migration.
- **Option 5** layers option 1 (guarantee) with an immediate best-effort call (low latency in the common case) and reconcile (backstop for orphans from any source, including doc-id drift or pre-tombstone history).

---

## 11. Rebuild design

REBUILD re-derives the entire graph from canonical state. Direct precedent exists: `rebuild_embeddings_command` (`embedding_commands.py:605`) + `POST /rebuild` (`embedding_rebuild.py:19`) — a fan-out that enumerates sources and submits one embed job each.

Design (mirroring that precedent):
- **Enumerate eligible sources:** `SELECT id, full_text FROM source WHERE full_text != none` (same predicate as `embedding_rebuild.py:56`). `full_text` **is sufficient** — it is the only content LightRAG receives.
- **Filter deleted/invalid:** enumerate live `source` records only; a source absent from SurrealDB is by definition not eligible.
- **Idempotency:** per-source delete-then-insert (doc_id stable) ⇒ safe to re-run; partial rebuilds converge.
- **Batching / rate-limit:** submit per-source `graphrag_index_source` jobs (fan-out), not one giant call — bounds sidecar load and respects LightRAG backpressure (`busy`). Reuse the existing per-item-job pattern.
- **Interruption/resume:** because each source is an independent idempotent job, a resumed rebuild simply re-submits; already-correct docs are overwritten identically.
- **Disaster recovery:** `DELETE /documents` (`document_routes.py:5569`) clears the sidecar, then REBUILD repopulates.

**Invariant:** REBUILD reads canonical text; it never writes canonical state. Deleting the entire LightRAG store destroys **no** canonical information (AGR-005 §14.3).

---

## 12. Reconciliation design

Diff canonical Sources against LightRAG documents, both keyed by the **stable doc_id** (§7).

**Enumeration primitive (verified):** `POST /documents/paginated` (`document_routes.py:6355`), `DocumentsRequest{status_filters, page, page_size 10-200, sort_field, sort_direction}` (`:1168`) → `PaginatedDocsResponse{documents:[DocStatusResponse], pagination, status_counts}`. `DocStatusResponse` (`:1003`) exposes `id`, `status`, `updated_at`, `file_path` (**= our source_id**), `error_msg`. `GET /documents/status_counts` (`:6536`) gives totals. The deprecated `GET /documents` caps at 1000 — use `/paginated`.

Detection table:

| Discrepancy | Signal | Action |
|---|---|---|
| Missing GraphRAG doc | source exists (eligible) but no doc_id in sidecar | Enqueue INDEX |
| **Orphan GraphRAG doc** | doc_id/`file_path` in sidecar but source absent in SurrealDB | **DELETE from sidecar** (confidentiality) |
| Stale content | `DocStatusResponse.updated_at` older than `source.updated` | Enqueue REINDEX (delete-then-insert) |
| Invalid provenance | `file_path` not a structurally valid RecordID | Flag + DELETE (never a valid Open Notebook doc) |
| Pending deletion | tombstone row unresolved past SLA | Re-drive DELETE |
| Failed indexing | `status == "failed"` (`error_msg` set) | Re-enqueue or surface to operator |

**Sufficiency of LightRAG API:** confirmed — list (`/paginated`), status (`DocStatusResponse.status`), and delete (`/delete_document` by doc_id) are all present and were read from pinned source. No missing primitive.

**Bounds:** paginate (≤200/page); scope by status filter; operator- or API-triggered (no scheduler exists). Reconcile is the backstop that makes orphan cleanup **not** depend on any local record surviving — because doc_id is derivable, an orphan is detectable purely by "sidecar has doc_id X, SurrealDB has no matching source."

---

## 13. Idempotency requirements

- **INDEX/REINDEX:** delete-then-insert by doc_id (upstream rejects same-`file_source` re-insert, §3). Re-running yields one identical doc.
- **DELETE:** by doc_id; absent doc / `not_allowed` ⇒ treat as success. `busy` ⇒ **retry, not success** (§9 row 20).
- **REBUILD:** union of idempotent per-source ops.
- **RECONCILE:** pure diff; re-runnable; converges.
- **Drain workers:** claim/lease or dedup so two workers draining one tombstone cause one delete (§9 row 21).

---

## 14. Race conditions

- **REINDEX vs DELETE / DELETE vs INDEX (rows 11-13):** resolve by **canonical truth at drain time** — every job re-checks `Source.get(id)` existence immediately before acting. If the source is gone, INDEX/REINDEX becomes a no-op (and any doc is deleted); DELETE proceeds. Reconcile is the final backstop.
- **Modified-while-pending (row 14):** delete-then-insert with the newest `full_text` read at drain time; a superseded reindex overwrites identically or is dropped if source deleted.
- **Two-worker drain (row 21):** idempotent doc_id + claim.
- Query-time validation (AGR-005 §8) remains the second line of defense: even mid-race, a stale sidecar row is dropped before it can reach a prompt because retrieval re-validates `source_id` against live SurrealDB. **Defense in depth: durable delete bounds retention; query validation bounds exploitability.**

---

## 15. Data-egress implications

Two-boundary model unchanged (AGR-005 §6):
- **Boundary A** (Open Notebook → sidecar): lifecycle commands cross it. Payload stays the §7 allowlist — `source_id` (as `file_source`) + `canonical_text` only. DELETE/RECONCILE send only doc_ids / read metadata; **no new content egress**.
- **Boundary B** (sidecar → LLM/embedding): **NOT approved.** REBUILD/REINDEX re-send full text to the sidecar, which during indexing forwards text to an LLM for extraction — so a REBUILD can push the whole corpus across Boundary B. **This makes REBUILD a Boundary-B-scale egress event and therefore synthetic-data-only until Boundary B is approved.**

No lifecycle operation may transmit `asset.file_path`, `asset.url`, tokens, or credentials. Reconcile reads `file_path` back but treats it strictly as a source_id join key, never a path (existing client contract, `client.py:317-320`).

---

## 16. Upstream coupling analysis

Core Open Notebook files a future implementation *could* touch, ranked by blast radius (prefer the lowest):

| Rank | Approach | Files touched | Blast radius |
|---|---|---|---|
| **A (preferred)** | New command module + new integration functions; enqueue via a thin lifecycle hook | `commands/graphrag_commands.py` (new), `open_notebook/integrations/graphrag/lifecycle.py` (new), 1 hook call-site | Minimal — additive |
| B | Same, but enqueue inline in `save_source` and `Source.delete()` | + `graphs/source.py` (1 block), `domain/notebook.py` `Source.delete()` (1 guarded call) | Small, but names GraphRAG inside domain/graph code |
| C | DB-event tombstone (option 1) | + new migration (`Nn.surrealql` + `Nn_down.surrealql`): 1 table + 1 event | Medium — schema change, but no core-table alteration |
| D (avoid) | LightRAG-specific logic scattered through Source domain | `domain/notebook.py` broadly, `graphs/source.py`, routers | Large — violates AGR-005 §21.2 isolation |

**Recommendation:** keep all LightRAG logic behind `open_notebook/integrations/graphrag/` (existing boundary). Add a **generic** lifecycle seam:
- INDEX/REINDEX: one guarded, fail-open `submit_command` call in `save_source` (or a hook it calls), contract copied from `Note.save()`.
- DELETE durability: a **DB event + tombstone table** (the only path that covers raw SurrealQL) drained by a new command that calls the integration.
- The event and table are additive; `Source.delete()` gets at most a best-effort immediate call (optimization, not the guarantee).

This keeps `graphs/source.py` and `domain/notebook.py` edits to single guarded call-sites, satisfies removability (drop the integration dir + migration + flag ⇒ baseline), and preserves upstream mergeability.

---

## 17. Migration / no-migration decision

**Determination:** the existing command/job infrastructure is durable for *submission* and *worker-outage* but is **missing crash-recovery / terminal-failure re-drive** (§8, verified in `surreal_commands` source). That missing property is exactly what deletion durability requires.

Two consequences:
1. **Deletion cannot rely on the command queue alone.** A retried command is Option 3, which the failure matrix (rows 2, 4) shows insufficient.
2. **Orphan *detection* needs no migration** — because doc_id is derivable from canonical source_id (§7), RECONCILE can find orphans by diffing the sidecar against SurrealDB with **no local record of deletions**.

Therefore:
- **If the accepted retention SLA is "eventually, bounded by reconcile cadence" and reconcile is run regularly ⇒ NO MIGRATION is strictly required.** RECONCILE-only (Option 4) is correct, just latency-unbounded, and with no scheduler the cadence is operator-driven.
- **If the accepted SLA is "atomic with delete, covering raw SurrealQL, bounded latency" ⇒ a MIGRATION IS REQUIRED:** exactly the missing property is *a durable deletion intent written in the same transaction as the canonical delete, drainable by an HTTP-capable worker.* That is Option 1 (a `graphrag_deletion` tombstone table + a `source_delete`-style event that inserts into it). Minimum schema: **one table + one event**, plus its `_down` migration. No existing table is altered; migration count moves 46 → 47.

**This is a decision for the user, not the agent** (AGRIBANK §5). The forensic recommends Option 5 (hybrid) with the Option-1 tombstone as its durable core, but explicitly flags that the migration needs its own approval (AGR-005 §10, §18 Q2).

---

## 18. Recommended architecture

A layered, flag-gated, identity-stable lifecycle:

1. **INDEX/REINDEX** — fail-open `submit_command("graphrag_index_source")` at `save_source` (`source.py:~221`), `Note.save()` exception contract, delete-then-insert idempotency by `doc_id = "doc-"+md5(source_id)`. Gated by `OPEN_NOTEBOOK_GRAPHRAG_ENABLED`.
2. **DELETE (durable core)** — a `source_delete`-style DB event writes a `graphrag_deletion` tombstone `{source_id, doc_id, requested_at, attempts}` **in the same transaction as the canonical delete**, so every path (including raw SurrealQL #7) is covered. A drain command deletes from LightRAG by doc_id, retries with backoff, treats absent/`not_allowed` as success and `busy` as retry, and clears the tombstone on confirmed removal. **The drain path is NOT gated by the indexing flag** (failure-matrix row 18) — deletes must converge even after the feature is turned off, as long as a sidecar might hold the copy.
3. **Best-effort immediate delete** — an optional synchronous call in `Source.delete()` for low latency in the common case; never the guarantee.
4. **REBUILD** — fan-out command mirroring `rebuild_embeddings`, enumerating `full_text != none`, per-source idempotent jobs. Boundary-B-scale ⇒ synthetic data only until Boundary B approved.
5. **RECONCILE** — operator/API-triggered diff via `/documents/paginated` vs. SurrealDB, keyed by doc_id; purges orphans, re-indexes missing, re-drives stuck deletes, re-enqueues failed. The backstop that makes correctness independent of any single mechanism.
6. **Query-time validation** (AGR-005 §8) remains the exploitability bound; durable delete is the retention bound. Both required.

All LightRAG specifics stay behind `open_notebook/integrations/graphrag/`.

---

## 19. Rejected alternatives

| # | Alternative | Rejected because |
|---|---|---|
| L1 | Best-effort delete only, in `Source.delete()` | Bypassed by raw SurrealQL (path #7); no backstop (AGR-005 A8) |
| L2 | Retried command only, no durable record | Command queue lacks crash re-drive (§8); lost on `running`+crash |
| L3 | Reuse `source_delete` event to call LightRAG | DB events cannot make HTTP calls (§5) |
| L4 | Treat REINDEX as re-`POST /documents/text` | Upstream rejects same-`file_source` as duplicate (§3) |
| L5 | Look up doc_id via list API before delete | Unnecessary — doc_id is locally derivable (§7); avoids a round-trip and a failure mode |
| L6 | Add `content_hash` field now for staleness | Not required — delete-then-insert is correct regardless; defer schema (§3, §17) |
| L7 | Delete LightRAG doc on notebook unlink | Confuses membership with existence; source still eligible (§6) |
| L8 | Gate deletion drain behind the enable flag | Flag-off with pending deletes retains a copy of deleted text (row 18) |
| L9 | Lossy source_id normalization to simplify doc_id | numeric vs numeric-string ids diverge ⇒ orphaned docs (§7, GraphRAG-02 lesson) |
| L10 | Scheduler-driven auto-reconcile now | No scheduler dependency exists; adding one is out of scope — operator/API trigger instead |

---

## 20. Open blockers

| # | Blocker | Blocks | Owner decision |
|---|---|---|---|
| 1 | **Boundary B not approved** | Any real internal data; REBUILD at corpus scale | User / security |
| 2 | **Retention/purge SLA** — atomic-bounded vs. reconcile-eventual | Whether the tombstone migration is required (§17) | User |
| 3 | **Migration approval** — `graphrag_deletion` table + event (if SLA demands it) | DELETE durability core | User (AGR-005 §18 Q2) |
| 4 | **RECONCILE trigger** — operator CLI vs. API vs. (future) scheduler | Reconcile cadence, hence orphan-latency bound | User |
| 5 | Branch target (`feature/graphrag-lifecycle` absent) | Where GraphRAG-03 lands | User |

---

## 21. Proposed GraphRAG-03 implementation slices

Each slice independently testable, flag-off = baseline, no real data. **None to start before approval.**

- **03-A — INDEX/REINDEX (fail-open):** `graphrag_index_source` command + integration `index/reindex` (delete-then-insert); enqueue seam in `save_source` with `Note.save()` contract; idempotency + canonical-existence check. Tests: enqueue on save, flag-off no-op, submit failure does not fail ingestion, idempotent re-index.
- **03-B — DELETE durability core:** *(migration-gated — needs blocker #2/#3)* `graphrag_deletion` tombstone table + `source_delete`-style insert event; drain command (retry/backoff, absent=success, busy=retry, flag-independent). Tests: raw-SurrealQL delete still records intent; worker-down persists; LightRAG-down retries; drain idempotent.
- **03-C — Best-effort immediate delete:** optional synchronous call in `Source.delete()`. Tests: fast-path deletes, failure falls through to tombstone.
- **03-D — REBUILD:** fan-out command + `POST /graphrag/rebuild` mirroring `rebuild_embeddings`. Tests: enumerate eligible, per-source idempotent, resume. Synthetic only.
- **03-E — RECONCILE:** diff command via `/documents/paginated`; orphan purge, missing re-index, stuck-delete re-drive; API/CLI trigger. Tests: orphan detected+purged, missing detected+indexed, stale detected.

Suggested order: 03-A → 03-E (reconcile makes A safe even without B) → 03-B/03-C (durability core, gated on migration approval) → 03-D.

---

## 22. Acceptance criteria (for the eventual GraphRAG-03 implementation, not this forensic)

1. Flag OFF ⇒ byte-for-byte baseline; no lifecycle side effects.
2. LightRAG absent/down ⇒ source create, retry, delete, and vector RAG all succeed.
3. INDEX/REINDEX failure never fails source ingestion (`Note.save()` contract proven by test).
4. REINDEX is delete-then-insert; no duplicate docs; idempotent under repeated runs.
5. **Every delete path — including raw `DELETE source` — results in eventual LightRAG removal** within the approved SLA (the core confidentiality criterion).
6. Deletion converges even with the enable flag OFF and after worker restart / LightRAG outage.
7. doc_id computed locally as `"doc-"+md5(source_id)`; numeric vs numeric-string ids stay distinct (test).
8. Notebook unlink does **not** delete a LightRAG doc while the source still exists.
9. REBUILD reconstructs solely from canonical `full_text`; deleting the sidecar store loses no canonical data.
10. RECONCILE detects and resolves missing / orphan / stale / failed / stuck-delete, bounded and paginated.
11. No real internal data; no Boundary B crossing without approval.
12. All LightRAG specifics confined to `open_notebook/integrations/graphrag/` + one command module; removability demonstrated (drop dir + migration + flag ⇒ baseline).
13. Baseline verification passes: `uv run pytest tests/`, `ruff check .`, `mypy .`; independent second-pass review (AGRIBANK §11) given the security-sensitive surface.
14. No unrelated refactors; migration (if any) is forward-compatible with a `_down`.

---

*Forensic complete. No code, migration, or command was written. Awaiting user approval before any implementation (AGRIBANK §5, §10).*
