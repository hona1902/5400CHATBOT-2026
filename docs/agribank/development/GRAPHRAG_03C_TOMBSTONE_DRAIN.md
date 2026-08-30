# GraphRAG-03C — Tombstone Drain / Retry / Idempotent Remote Delete

**Status: GraphRAG-03C APPROVED / COMPLETE** — signed off 2026-08-30. Karpathy CLEAN · Codex A APPROVE · Codex B APPROVE · no unresolved actionable HIGH. **Branch:** `feature/graphrag-lifecycle` · **Baseline:** `66560d5` (tag `graphrag-03b-approved`). **SurrealDB runtime:** `2.6.5` · **LightRAG pinned:** `v1.5.6` (contacted for deletion lifecycle only). **Egress:** synthetic / public / anonymized only; Boundary B remains **NOT approved** for internal data. **Next:** GraphRAG-03D RECONCILE — **NOT STARTED**.

This is the **first** GraphRAG phase permitted to perform a remote LightRAG deletion. It turns the durable deletion *intent* recorded by 03B into proven derived *convergence*, then resolves the tombstone. Contract preconditions were set in [`GRAPHRAG_03B_DURABLE_DELETE.md`](GRAPHRAG_03B_DURABLE_DELETE.md) §17.1.

---

## 1. Scope

Implemented: enumerate durable pending tombstones; per-tombstone canonical re-check + convergence state machine; idempotent LightRAG delete; **confirmed remote absence** before resolution; live-source convergence; live-empty→absent; `arm_id` compare-and-set resolution; transient retry via a fair, bounded, deferring drain; crash-safe re-drive independent of the command queue; a small periodic wake-up; migration **25** (`next_attempt_at`). **Not** implemented: RECONCILE (03D), REBUILD (03E), HybridRetriever/RRF/reranking, Ask/Chat/frontend, query routing, Boundary-B enablement, generation-aware doc_id.

## 2. Source-of-truth invariant

SurrealDB `source` is canonical; LightRAG is derived, rebuildable, removable; the `graphrag_deletion` tombstone table (NOT the command queue) is the durable source-of-truth for pending deletion work. Central asymmetry (AGR-005 §9): indexing may fail open; **deletion may not disappear silently**.

## 3. 03B dependency

03B (migration 24) provides: a deterministic tombstone row (`type::thing("graphrag_deletion", $before.id)`) written atomically with every source delete (incl. raw SurrealQL), fields `source_id / requested_at / status / arm_id`, and the read-only `list_pending_deletions`. 03C consumes and resolves these. Migration 24 is **frozen**.

## 4. Tombstone snapshot model

A drain reads an immutable snapshot `{source_id, arm_id, next_attempt_at}` and converges against **current** canonical state. It never trusts queued text (there is none) and never deletes a doc_id "because a tombstone exists" — see §6.

## 5. arm_id CAS semantics

`arm_id` (a fresh `rand::uuid()` minted by the event on every arm/re-arm) is the **authoritative** concurrency fence. Resolution and deferral are compare-and-set on the exact processed `arm_id`:
- resolve: `DELETE graphrag_deletion WHERE source_id=$s AND status='pending' AND arm_id=<uuid>$a RETURN BEFORE` → 1 row = resolved, 0 rows = superseded/re-armed.
- defer: `UPDATE ... SET next_attempt_at = time::now() + type::duration($d) WHERE ... arm_id=<uuid>$a` → 0 rows if re-armed (the newer arm, set immediately due, is left intact).
`requested_at`/`next_attempt_at` are **never** fences. (`deletion.py::resolve_tombstone_cas` / `defer_tombstone_cas`, live-verified on v2.6.5.)

## 6. Canonical Source re-check (never blindly delete)

Every attempt loads the CURRENT canonical Source by its losslessly-built RecordID (`record_id_for`) and branches (`drain.py::converge_tombstone`):

```
load CURRENT source
  ├─ absent                          -> converge to ABSENT
  ├─ present, empty/whitespace text  -> converge to ABSENT
  └─ present, non-empty text
        ├─ indexing flag ON  -> converge to CURRENT (03A delete-then-insert)
        └─ indexing flag OFF -> converge to ABSENT (no Boundary-B egress)
```

## 7. Absent-source branch

`_converge_to_absent`: confirm absence FIRST (already-absent resolves in one probe); else, **before** the destructive delete, RE-CHECK canonical state (`_source_became_live_current`) — the branch was chosen from an earlier read, so a recreate could have landed since; if the source is now live + non-empty with indexing on, the present doc is the CURRENT generation and must NOT be deleted, so the drain defers and re-drives through the current branch (closes the recreate-between-read-and-delete race, 03B §17.1.2). Otherwise drive an idempotent delete and DEFER for re-confirmation. Resolve (arm_id CAS DELETE) **only** on `ABSENT_CONFIRMED`. (A confirmed absence still resolves safely even if the source was recreated: the deleted generation is gone; the live source's own index job owns the new doc.)

## 8. Live-source convergence branch

Non-empty + flag ON reuses the approved 03A `lifecycle.index_source` with a `confirm_current` guard (reloaded current text, double TOCTOU check). `INDEXED` means the old generation was deleted (delete-then-insert) and the current text accepted — the confidentiality goal is met. Resolution is then **atomic with a canonical re-check** (`resolve_current_tombstone_cas`): the tombstone DELETE-CAS predicate also requires `source_id.full_text = <shipped text>` (a record-link dereference, one SurrealDB statement, verified live), so a redaction/edit/delete landing *after* the insert — with no re-arm — matches **zero rows**, leaves the tombstone pending, and the next attempt takes the empty→absent branch to remove the stale doc. This closes the read-then-CAS TOCTOU (there is no separate read/resolve gap). The async-insert-not-yet-processed case is an **availability** residual (the canonical source is rebuildable — identical to 03A's accepted residual, closed by REBUILD), not a confidentiality one, so `INDEXED` + canonical-fenced CAS is a safe resolve point; it also avoids reindex churn on large sidecars where single-page presence cannot be confirmed. `SUPERSEDED`/transient → defer and re-drive.

## 9. Live-empty-source branch

A live source with empty/whitespace `full_text` converges to **ABSENT** (explicit confirmed delete). 03A's `skipped_no_content` does not delete, so the drain must (§17.1.2). `skipped_no_content`/`superseded` never count as convergence.

## 10. Remote delete confirmation semantics

**`deletion_started` is acceptance, not absence** — verified LIVE against v1.5.6: with a broken embedding provider a background delete *failed* (`"0 successful, 1 failed"`) and the document *remained*. Absence is proven only by `POST /documents/paginated`, and only from a **single complete-snapshot** enumeration:

- **FOUND** — target doc_id present.
- **ABSENT_CONFIRMED** — target absent AND `total_pages <= 1` AND `total_count == len(documents)` (page_size=200, sort_field=`id`, asc). One server response = one consistent snapshot, so no cross-request offset-shift race can hide the target.
- **UNKNOWN** — anything else: `total_pages > 1`, count mismatch, timeout, HTTP error, parse/schema error, incomplete/uncertain.

Only `ABSENT_CONFIRMED` permits CAS resolution. **No `GET /documents` fallback and no multi-request offset traversal** are used as absence proof. (`client.py::confirm_document_absent`.)

## 11. Idempotency

Delete-of-already-absent returns `deletion_started` (idle) or `busy` — never a fatal error (verified live). Repeated processing converges safely: idempotent remote ops + arm_id CAS mean duplicate/concurrent drains cause at most one destructive effect and one resolution.

## 12. Retry / re-drive model (fair, bounded, no migration-25 backoff bloat)

Retry = the tombstone **stays pending** and a non-converged attempt **defers** `next_attempt_at = now + retry_delay` (default 60 s, floor 5 s), pushing the row out of the current "due" set. The due set = `status='pending' AND next_attempt_at <= time::now()` ordered by `next_attempt_at, id`, capped at `batch_size`/`max_rows`. Because resolved rows are DELETEd and deferred rows leave the due set, the next selection returns the *next* due rows — **no OFFSET over a mutating set, no starvation** even if early rows fail forever. In-command handling is finite per row (a failing row never aborts the batch and the command never raises for a per-row failure). Deletion is never abandoned (no max-attempts). See §21 for the fairness proof.

## 13. Drain discovery / scheduling mechanism

There is **no** scheduler/cron/APScheduler in the project and the surreal-commands queue cannot re-drive a crashed `running` job. So discovery is a **small, GraphRAG-specific, cancellable periodic wake-up** hosted by the **FastAPI lifespan** (`api/main.py`) — the least-coupled owned lifecycle (the worker CLI exposes no clean hook). It runs an immediate startup tick then every `interval` (default 300 s, floor 30 s), and only **enqueues** a bounded `graphrag_drain_deletions` command when work is due (`drain.py::graphrag_drain_wakeup_loop` / `enqueue_drain_if_pending`). The worker performs the HTTP. Cancelled deterministically on shutdown (`_stop_graphrag_drain_wakeup`: `task.cancel()` + await). A best-effort immediate wake-up also fires from `Source.delete` (optimisation only, §27). This is **not** a new global scheduler/daemon.

## 14. Queue-crash independence

The durable tombstone is the work source-of-truth. If a worker crashes at any point before the arm_id CAS resolve, the tombstone remains pending and is rediscovered by the next wake-up tick and re-driven from current state — independent of the command queue (which leaves a crashed job stuck `running`). The drain command uses `max_attempts=1` precisely because re-drive comes from the durable table + wake-up, not from queue retry.

## 15. Feature-flag behavior

Deletion draining is **independent** of `OPEN_NOTEBOOK_GRAPHRAG_ENABLED`. The delete/confirm path gates on **base_url only** (`service._require_client_for_deletion`), so a sidecar document from a previously-enabled period is removed even with indexing off. Indexing (the live-non-empty convergence) stays flag-gated; with the flag off, live-non-empty converges to **absent** instead of re-indexing, so draining never silently broadens egress.

## 16. Sidecar-unavailable behavior

- Flag OFF + sidecar configured: drain runs (delete/confirm), no provider call.
- Sidecar configured but DOWN: each attempt fails fast → tombstone stays pending → retried next tick (bounded, no hot loop).
- Sidecar config missing (no base_url): `_require_client_for_deletion` raises `GraphRAGDisabledError` → treated as "cannot reach → stay pending", never success.
- Permanent decommission/purge is a separate operational decision (out of scope).

## 17. Boundary A / Boundary B

03C introduces allowed network activity for the deletion lifecycle only: Open Notebook → LightRAG sidecar (**Boundary A**). The absent/empty branches use only the source-derived doc identity + paginated listing — **no** embedding/LLM/provider call. The live-non-empty branch reuses the approved 03A index path (**Boundary B**, synthetic only) and **only** when the flag is on; flag-off converges to absent. No credentials, content, titles, URLs, or response bodies are logged.

## 18. Batching

Bounded due-set selection (`list_due_deletions(limit)`), `batch_size` per query (default 50, clamped ≤200), hard `max_rows` per command (default 500, clamped ≤`MAX_DRAIN_MAX_ROWS`=5000 so a bad env var can never force an unbounded scan). No START/OFFSET. Deterministic order (`next_attempt_at, id`). A per-row defer failure (e.g. a near-impossible malformed source_id) is caught and never aborts the batch.

## 19. Concurrency

Multiple replicas / duplicate drain commands are safe: idempotent remote operations + arm_id CAS. The dedup guard is an **optimisation only**, not a distributed lock, and it considers **only unstarted `new` rows — never `running`**: the command queue marks a job `running` before execution and never clears it on a worker crash, so treating `running` as "active" would let one crashed drain permanently suppress every future drain (the exact crash-stuck-running trap). A healthy running drain plus a fresh enqueue is safe. No persistent `running` lease is introduced. The best-effort `Source.delete` wake-up routes through the **same** deduplicating `enqueue_drain_if_pending`, so a bulk cascade (`Notebook.delete` over many sources) enqueues at most one new drain, not one per source.

`defer_tombstone_cas` fences on `arm_id` **alone** (not source_id) so a malformed-identity row can still be moved out of the due set; this relies on `arm_id` uniqueness, which is DB-generated (`rand::uuid()` UUIDv7, unique per arm). A hand-duplicated `arm_id` (only reachable via corrupt raw-DB writes) could momentarily defer an unrelated row too, but that is a scheduling perturbation, not a confidentiality/correctness loss (the row returns after the delay; content is still deleted). `resolve`/`resolve_current` retain the source_id predicate and are only reached for validated ids.

## 20. Crash matrix (recovery for each point)

| Crash point | Recovery |
|---|---|
| before HTTP delete | tombstone pending → next tick re-drives |
| after delete request, before confirm | pending → re-confirm next tick (delete idempotent) |
| after remote deletion completes, before confirm | pending → confirm proves absence next tick → resolve |
| after confirm, before CAS resolve | pending → next tick confirms absent again → resolve (idempotent) |
| after CAS resolve, before local log | row already deleted → next tick sees nothing |
| during live-source reindex | 03A delete-then-insert + confirm_current; pending until INDEXED, then resolve |
| between delete and insert (reindex) | 03A 409/destructive_busy handling → transient → defer → re-drive |
| worker process death (stuck `running`) | irrelevant — re-drive is the durable tombstone + wake-up, not the queue |

## 21. Race matrix (selected; full list in tests)

Absent/remote-doc-exists; absent/already-absent; delete accepted-but-processing (stays pending); delete completes after local timeout (confirmed next tick); live+non-empty; live+empty; source recreated before/after existence check; deleted-again during convergence; arm A re-armed to B during delete/confirm/before CAS (stale CAS → 0 rows, B left due); two workers same arm (one CAS wins); ack loss (retry idempotent); 404/409/5xx/timeout/malformed (→ UNKNOWN/defer); flag OFF; sidecar missing/down; app/worker restart with pending tombstones; raw DB DELETE with no Python hook (periodic discovery); numeric/string-numeric/escaped RecordID (lossless); malformed identity (permanent, no HTTP); migration 25 up/down; rollback to 03B semantics. **In every case, deleted content never survives indefinitely and valid current content is never deleted-then-wrongly-resolved.**

## 22. RecordID handling

Identity is preserved losslessly via `record_id_for` (never `RecordID.parse`), so `source:123` (numeric) and `source:⟨123⟩` (string-numeric) and escaped ids bind distinct tombstones and distinct doc_ids. `compute_doc_id = "doc-" + md5(source_id)` (verified live: matched the sidecar's `DocStatusResponse.id`).

## 23. Logging / data minimization

Logs carry only source ids, arm ids, normalized outcomes, sanitized exception **class** names, and aggregate counts. Never: full_text, title, URL/path, LightRAG response bodies, Authorization headers, API keys, or raw exception strings. No error text is persisted (no `last_error` field). A **malformed** tombstone identity is the one case where the value itself could be path/URL/token/content-shaped, so it is NEVER echoed — the invalid-identity log is a fixed sanitized message with no value (asserted by a log-capture test).

## 24. Migration 25 decision

Migration 25 was proven **necessary** for global fairness (a bounded per-tick front-start traversal starves later rows if ≥cap early rows fail persistently; keyset alone doesn't fix cross-tick fairness). Minimal form: a single field `next_attempt_at: datetime DEFAULT time::now()`, an explicit backfill of pre-25 rows, and a `DEFINE EVENT OVERWRITE` that also stamps `next_attempt_at` on arm/re-arm. `25_down` restores the **exact** migration-24 event body (verified structurally identical modulo `OVERWRITE` vs `IF NOT EXISTS`) and removes the field. **No** `attempt_count`, `last_error`, `resolved_at`, or cursor rows. Statement order (FIELD → backfill → EVENT) + migrations running at API startup before traffic guarantee no upgrade window where the event references a missing field; an unmaterialised `next_attempt_at` still reads as its `time::now()` default (immediately due), so no tombstone is lost mid-upgrade.

## 25. Live SurrealDB evidence

Verified on v2.6.5: `DEFINE FIELD IF NOT EXISTS`, `time::now() + type::duration($d)`, DUE filter, arm-fenced defer/resolve returning 1/0, re-arm safety (stale-arm defer after re-arm → 0 rows), `DEFINE EVENT OVERWRITE`, `REMOVE FIELD IF EXISTS`, backfill of unmaterialised rows, migration 25 up/down/up, and the 14 fairness properties (`tests/test_graphrag_03c_drain.py` live-DB section).

## 26. Live LightRAG evidence

Ran the pinned `ghcr.io/hkuds/lightrag:v1.5.6` container (synthetic data + a local synthetic mock embedding/LLM provider — no real provider). Verified: doc registers as `doc-md5(source_id)` with `file_path=source_id`; paginated exposes `total_count/total_pages/has_next`, `page_size ∈ [10,200]`, `sort_field=id` deterministic; delete returns `deletion_started`; **acceptance ≠ absence** (broken-provider delete failed, doc remained); working-provider delete converged to absence within seconds; delete-of-already-absent is idempotent. Two automated live tests (`test_live_lightrag_delete_then_confirmed_absent`, `test_live_lightrag_full_roundtrip_synthetic`) pass against the running sidecar and skip when it is absent.

## 27. Exact files changed

**New:** `open_notebook/integrations/graphrag/drain.py`; `open_notebook/database/migrations/25.surrealql` + `25_down.surrealql`; `tests/test_graphrag_03c_drain.py`; this doc.
**Edited:** `open_notebook/integrations/graphrag/models.py` (AbsenceState, DocumentsPage); `client.py` (list_documents_page, confirm_document_absent, ABSENCE_PROBE_PAGE_SIZE); `service.py` (confirm_source_document_absent, `_require_client_for_deletion`, delete/confirm use it); `deletion.py` (next_attempt_at, list_due_deletions, has_due_deletions, resolve/defer CAS); `config.py` (load_drain_config + knobs); `commands/graphrag_commands.py` (drain command) + `commands/__init__.py` (register); `open_notebook/database/async_migrate.py` (register 25 up/down); `api/main.py` (lifespan wake-up start/stop); `open_notebook/domain/notebook.py` (best-effort wake-up in Source.delete); guard-test updates in `tests/test_graphrag_isolation.py`, `test_graphrag_lifecycle.py`, `test_graphrag_deletion.py`.
**NOT touched:** migration 24, 03A indexing identity, Ask/Chat/frontend, retriever, `fn::vector_search`, `source_delete` event.

## 28. Known limitations

- **Single-response absence ceiling (~200 docs) affects RESOLUTION bookkeeping only, never content removal.** A sidecar corpus larger than one page yields `UNKNOWN`, so the absent/empty branch cannot *prove* absence and the tombstone stays **pending** — but it still **issues the idempotent delete on every attempt**, so the content *is* removed regardless of corpus size; only the tombstone bookkeeping waits (for the corpus to fit a page, or for 03D RECONCILE to prove absence at scale). Confidentiality is therefore **not** ceiling-limited; resolution latency is. This is explicit, never silent success, and — per the approved D5 decision — 03C adds no `GET /documents` fallback and never uses multi-request offset traversal as absence proof. The live-non-empty branch resolves on `INDEXED` + canonical re-check (§8) and so is not ceiling-dependent (no large-corpus reindex churn).
- **Wake-up hosted by the API process.** If the API is down while the worker runs, a raw-DELETE tombstone waits for the API to return (the API is core infra).
- **Live-LightRAG full round-trip needs an embedding provider** to complete a delete; the provider-independent delete-of-absent path is always testable.
- **DocStatusResponse has no content_hash**, so absence is by presence only (sufficient for the deletion invariant).

## 29. Deferred GraphRAG-03D work

RECONCILE (defense-in-depth): paginated diff of sidecar vs SurrealDB by doc_id to purge orphans, re-index missing, and prove absence at scale (raising the 03C ceiling). 03C does **not** rely on 03D — the tombstone + arm_id CAS + re-drive is the primary deletion-correctness path.

## 30. Acceptance criteria

| # | Criterion | Status |
|---|---|---|
| 1 | Tombstone resolved only on CONFIRMED absence / confirmed current insert, never on `deletion_started` | ✅ |
| 2 | arm_id CAS resolve/defer; 0 rows ⇒ superseded/re-drive; requested_at/next_attempt_at never a fence | ✅ live |
| 3 | Absent / live-empty / live-non-empty branches per §17.1; never blind delete, never eternal skip | ✅ |
| 4 | Flag-OFF live-non-empty converges to absent (no Boundary-B egress); deletion flag-independent | ✅ |
| 5 | Fair, bounded drain (no OFFSET skip, no starvation, no hot loop) — migration 25 `next_attempt_at` | ✅ live |
| 6 | Crash/queue independence: durable tombstone + periodic wake-up re-drive | ✅ |
| 7 | Raw SurrealQL DELETE eventually drained without restart/operator action | ✅ |
| 8 | Idempotent delete-of-absent; concurrent/duplicate drains safe | ✅ live |
| 9 | Migration 25 up/down/up; 25_down restores exact 24 event; migration 24 frozen | ✅ live |
| 10 | No content/credentials in logs; no provider call in absent branch; malformed id ⇒ no HTTP | ✅ |
| 11 | 03C tests + 02/03A/03B regression + live SurrealDB + live LightRAG green | ✅ 288 pass |
| 12 | Full backend green except the 5 documented pre-existing baseline failures | ✅ 936 pass / 5 pre-existing |
| 13 | ruff + mypy clean on changed source | ✅ |
| 14 | Karpathy diff + Codex A/B, no unresolved actionable HIGH | ✅ Karpathy clean; Codex A & B both **approve** |

**COMPLETE — all criteria met; signed off 2026-08-30.** Review status: Karpathy diff clean (1 nit, kept). Codex A (delete/CAS/concurrency): 4 HIGH fixed across 5 passes (stale-read blind delete, resolve-on-acceptance, redaction read-then-CAS TOCTOU → atomic canonical-fenced resolve, stale-`running` dedup) + the >200 ceiling accepted as the user-approved D5 bookkeeping-latency limitation (content removal is not ceiling-limited). Codex B (retry/scheduling/security): 2 HIGH + 4 MEDIUM fixed (malformed-batch-abort, malformed-monopolization via arm-only defer, non-finite config clamp, invalid-identity log leak, bulk-delete queue flood) → **final verdict: approve**. No unresolved actionable HIGH.
