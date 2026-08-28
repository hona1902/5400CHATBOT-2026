# GraphRAG-03A — INDEX / REINDEX

**Status: GraphRAG-03A APPROVED / COMPLETE** — approved 2026-08-28. **Branch:** `feature/graphrag-lifecycle`. **Baseline:** `bc5b413`; forensic `6cd8333`.
**Scope:** INDEX / REINDEX lifecycle only. DELETE (durable/tombstone), RECONCILE, REBUILD, migration are later slices (03B–03E) and are **not** in this change.
**Egress:** synthetic / public / anonymized data only. Boundary B remains **NOT approved**.

Forensic basis: [`../architecture/GRAPHRAG_LIFECYCLE_FORENSIC.md`](../architecture/GRAPHRAG_LIFECYCLE_FORENSIC.md).

---

## Final verification (recorded 2026-08-28)

```text
GraphRAG tests:  219 passed
Backend pytest:  869 passed, 5 failed
                 (all 5 proven pre-existing: 4 Windows path artifacts in podcast
                  path tests + 1 proxy case-merge bug; unrelated to GraphRAG,
                  documented in GRAPHRAG_POC.md § Known Baseline Test Failures)
Ruff:            clean
Mypy:            clean — no issues (9 changed source files)
Karpathy diff:   clean — F1/F2/F3 resolved across 2 re-traces
Codex review:    no unresolved actionable HIGH — 6 adversarial passes; every
                 raised HIGH fixed or refuted against pinned LightRAG v1.5.6
                 source; two residuals explicitly deferred (see below)
Migrations:      unchanged (46) — no migration added
Commit:          nothing committed automatically
```

### Invariants established by this slice (explicit)

- **Queued payload contains `source_id` only** — no `full_text`/content field (`GraphRAGIndexInput`; asserted by `test_index_input_has_no_text_field`).
- **The worker reloads the CURRENT canonical Source** at execution via a direct, losslessly-keyed query — never trusts the payload for content.
- **Stale queued `full_text` cannot be resurrected** — there is no queued text to resurrect; an older job that runs after a newer save indexes current state.
- **Pre-delete confirm prevents an old job deleting a newer document** — `confirm_current` runs before any destructive action, so a superseded job performs neither delete nor insert (`test_superseded_does_not_delete_a_newer_jobs_document`).
- **Post-delete confirm prevents stale text egress after delete latency** — `confirm_current` is re-checked immediately before the POST, so text that went stale during the delete round-trip is not shipped (`test_second_confirm_after_delete_prevents_stale_egress`).
- **Delete-then-insert failure is derived-index degradation, never canonical corruption** — the command never mutates canonical `source`/embeddings; a failed index leaves SurrealDB and vector RAG untouched (`test_sidecar_timeout_does_not_corrupt_canonical`-class assertions).
- **INDEX/REINDEX remain fail-open** — the `save_source` enqueue seam never raises (`Note.save()` contract), so a GraphRAG queue/sidecar failure never fails source ingestion.
- **Durable deletion remains deferred to GraphRAG-03B/03C** — no tombstone, no DB-event change, no deletion draining here.
- **Reconciliation remains deferred to GraphRAG-03D** — orphan/stale purge is not implemented in this slice.

---

## Command contract

`graphrag_index_source` (`commands/graphrag_commands.py`), app `open_notebook`.

| | |
|---|---|
| **Input** | `GraphRAGIndexInput{source_id: str}` — **source_id only, no text** |
| **Output** | `GraphRAGIndexOutput{success, source_id, outcome, processing_time, track_id?, error_message?}` |
| **Outcomes** | `indexed` · `superseded` · `skipped_disabled` · `skipped_absent` · `skipped_no_content` · `permanent_failure` |
| **Retry** | `max_attempts=5`, exponential jitter 1–60s, `stop_on=[ValueError, ConfigurationError]` (mirrors `embedding_commands`) |
| **Retry trigger** | TRANSIENT outcomes **raise** so the retry layer re-drives; PERMANENT and skips return without raising |

## Stale-job semantics (the central rule)

The queued payload carries **`source_id` only**. There is no `full_text`/`content` field — a test (`test_index_input_has_no_text_field`) asserts this structurally. At execution the worker:

1. Checks the feature flag (skip if off).
2. Validates `source_id` (permanent failure if invalid — never reaches the sidecar).
3. `Source.get(source_id)` — loads **current** canonical state.
   - Not found → `skipped_absent` (safe no-op; an older INDEX firing after a DELETE indexes nothing).
   - No `full_text` → `skipped_no_content`.
4. Indexes the **current** `full_text`.

Because the newest text is read at execution, an older job running after a newer save still indexes current state. Stale-text resurrection is structurally impossible: there is no queued text to resurrect.

**Expected-version token: investigated, deferred.** Reloading current state already yields correctness; a `content_hash`/version token would add only optimization at the cost of new state, and no such field exists (forensic §3). Revisit in 03-D/03-E if needed. No schema added.

## Idempotency semantics — delete-then-insert

Verified against LightRAG v1.5.6:
- Re-POST of the same `file_source` is rejected as a filename duplicate (`pipeline.py:1121-1170`), so REINDEX must delete first.
- `doc_id = "doc-" + md5(canonical_source_id)` — deterministic, content-independent, locally computable (`client.compute_doc_id`; `pipeline.py:936-946`). No lookup needed.
- `DELETE /documents/delete_document` is background; `delete_document` normalizes: `deletion_started`/`not_found` → `GONE`; `busy` → `BUSY`; `not_allowed` → `REFUSED`; unknown → protocol error.

Flow (`integrations/graphrag/lifecycle.py::index_source`):
```
delete existing doc:
    GONE                 -> insert
    BUSY | REFUSED       -> TRANSIENT (do NOT insert into a racing delete)
    unavailable/5xx/conflict/protocol -> TRANSIENT
insert current text:
    accepted             -> INDEXED
    409 (async delete not settled) -> TRANSIENT
    422 / validation     -> PERMANENT
    unavailable/5xx      -> TRANSIENT
    not accepted         -> TRANSIENT
```
The internal delete is **fail-closed within the job** (an unconfirmed delete blocks the insert and retries). This is *not* the durable lifecycle DELETE (03-B/03-C) and sets no best-effort-delete precedent; the fail-open contract that protects ingestion lives at the enqueue seam, not here.

### Per-scenario
| Scenario | Result |
|---|---|
| Duplicate INDEX / REINDEX | same doc_id both times → one doc |
| INDEX then newer INDEX | each indexes current text; converges |
| Older job after newer job | reload-current ⇒ current text |
| Source deleted while pending | `skipped_absent`, nothing indexed |
| Insert OK, ack lost | retry re-runs delete-then-insert → same doc_id |
| Delete OK, insert fails | transient → retry (delete-of-absent = not_found) |
| Flag OFF at execution | `skipped_disabled`, no external call |
| Sidecar down/timeout/5xx | transient → retry; canonical + vector RAG untouched |

## Source reload behavior

The command never trusts the payload for content. `Source.get` is the single source of truth at execution time; a missing source is a clean terminal no-op.

## Failure matrix (03-A subset)

| Condition | Outcome | Canonical impact |
|---|---|---|
| Flag off (enqueue) | no command created | none (baseline) |
| Flag off (execution) | `skipped_disabled` | none |
| Enqueue submit raises | swallowed at seam | **none — ingestion still succeeds** |
| Invalid source_id | `permanent_failure` | none; no egress |
| Source absent | `skipped_absent` | none |
| No content | `skipped_no_content` | none |
| Sidecar unreachable/timeout/5xx | TRANSIENT → retry | none |
| Delete busy/refused | TRANSIENT → retry | none |
| Insert 409 | TRANSIENT → retry | none |
| Insert 422 | `permanent_failure` | none |

## Feature-flag behavior

- **Flag OFF at enqueue** (default): `_maybe_enqueue_graphrag_index` returns before submitting — no command row, byte-for-byte baseline.
- **Flag OFF at execution**: command no-ops as `skipped_disabled`, makes no external call, terminal (not left retryable — a disabled feature must not hold queue entries). Any resulting gap is reconciled by REBUILD/RECONCILE in later slices.
- **Flag ON but BASE_URL unset (misconfigured)**: **transient**, not a terminal skip. The command gates on `service.config.enabled` (the raw flag) separately from `service.config.base_url`; a flag-on-yet-unconfigured job raises so it re-drives once configured. (`service.enabled` = `configured` = flag AND base_url, so it is deliberately not used for the skip decision.)

## Known limitations (deferred, by design)

- **Emptying a source's text does not remove its prior sidecar document.** If a source previously indexed with non-empty text is reprocessed to empty text, the command returns `skipped_no_content` and does not delete the old document. Removing a document because content became empty is a *deletion* semantic with a retention guarantee — out of scope for index/reindex, and belonging to durable DELETE (03-B/03-C) and RECONCILE. Emitting a best-effort delete here would violate AGR-005 §9 (deletion must not be best-effort).

## Review findings & resolutions (2026-08-28)

Karpathy (2 passes): F1 (validation-error classification mismatch between delete/insert steps) — **fixed**; F2 (`not_found` docstring accuracy) — **doc-only fix**; F3 (type-name string matching) — **fixed** (isinstance).

Codex adversarial (1 pass, 2 HIGH + 2 MEDIUM), each verified against source before acting:
- **HIGH — `Source.get()` failure misclassified as deletion.** Confirmed: `ObjectModel.get` wraps every DB error as `NotFoundError` (`base.py:127`), so a transient outage would terminally skip a live source. **Fixed:** the command now does a direct existence query (`SELECT VALUE id FROM $id`); a DB error propagates (→ transient retry), only a truly empty result is `skipped_absent`. Test: `test_transient_db_error_is_not_skipped_as_absent`.
- **HIGH — async delete treated as safe-to-insert (delete/insert race).** Verified against upstream: a per-doc delete synchronously holds `destructive_busy` before returning `deletion_started`, and `insert_text` returns **HTTP 409** while that flag is held (`document_routes.py`). Our 409 handling classifies this **TRANSIENT → retry**, so *while the worker process survives and attempts remain* the operation converges (delete completes, retry inserts). **Bounded residual gap (accepted for 03-A):** the surreal-commands queue has no crash re-drive and retries cap at 5 in-process attempts (forensic §8), so a worker crash between `deletion_started` and a successful insert — or a lost ack, or 409-retry exhaustion — can leave the old doc deleted and the current doc not yet inserted. This is an **availability/quality** gap (not confidentiality); INDEX is fail-open/rebuildable and **REBUILD/RECONCILE (03-D/03-E) plus durable re-drive (03-B/03-C) are the designed recovery**. So 03-A does **not** claim loss-free convergence in the crash/ack-lost/exhausted-retry cases. `ack.accepted` also means *queued*, not *processed* (async insert); terminal `indexed` on acceptance is acceptable under the fail-open/rebuildable contract, documented not overclaimed.
- **HIGH (pass 2) — escaped record id double-escaped by `ensure_record_id`.** Confirmed: `RecordID.parse("source:⟨123⟩")` → `source:⟨⟨123\⟩⟩`, so the existence query and `Source.get` would miss a live escaped-id source and terminally skip it as absent — violating numeric vs string-numeric identity. **Fixed:** added `record_id_for()` (shares `validate_source_id`'s structural logic, returns the losslessly-*built* `RecordID` object); the command looks up and builds the Source via that object, never re-parsing the canonical string. Test: `test_escaped_record_id_is_queried_losslessly`.
- **MEDIUM — enabled-but-misconfigured terminally skipped.** **Fixed** (see flag behavior above). Test: `test_enabled_but_unconfigured_is_transient_not_terminal_skip`.
- **MEDIUM — empty text leaves prior doc.** **Documented** as a deferred deletion semantic (see Known limitations).

Codex adversarial (pass 3) — HIGH-3 verified fixed, HIGH-4 accepted as scoped, plus one NEW:
- **HIGH (pass 3) — TOCTOU: stale text egress after in-flight delete/redaction.** The command reads `full_text`, then work happens, then it mutates the sidecar; if the source is deleted/emptied/redacted in that window, the earlier snapshot could still be sent — confidentiality-relevant. **Mitigated:** `index_source` takes a `confirm_current` callback; the command re-reads canonical state and returns `superseded` (mutating nothing) if the text changed or the row vanished; a confirm error is TRANSIENT (no action under uncertainty).
- **HIGH (pass 4) — superseded-after-delete could erase a newer job's document.** Confirming *after* the destructive delete meant a superseded older job had already deleted the document a newer, already-completed job wrote, leaving the graph empty. **Fixed:** `confirm_current` runs **before any destructive action**, so a superseded job does neither delete nor insert. Test `test_superseded_does_not_delete_a_newer_jobs_document`.
- **HIGH (pass 5) — stale egress during the delete round-trip.** The delete is a network round-trip during which the source can be redacted/deleted; a single pre-delete confirm cannot cover it. **Fixed:** `confirm_current` is now checked **twice** — before the delete (prevents erasing a newer doc) *and* immediately after the delete, before the POST (prevents shipping text that went stale during the delete). A superseded post-delete outcome removes only our own stale document and does not reinsert. Test `test_second_confirm_after_delete_prevents_stale_egress`. **Irreducible residual (documented, deferred):** a change in the sub-step gap between the second confirm and the HTTP POST cannot be closed without a transactional sidecar; that, plus the ack-lost-then-source-deleted case (insert accepted, ack lost, source later deleted → retry sees absent, no cleanup), is covered by durable DELETE + RECONCILE (03-B/03-D) and query-time validation (AGR-005 §8). 03-A does not claim to close them.

Codex adversarial (pass 6) — two HIGH raised, both verified against pinned source:
- **Claimed HIGH — `deletion_started`→insert lets the background delete erase the new doc.** **Refuted by source (not a defect).** The delete endpoint acquires `destructive_busy` *synchronously* before returning `deletion_started`, and the background delete holds it until its own `finally` **after** all data removal; `insert_text` returns **HTTP 409** while `destructive_busy` is set. So a reindex insert issued during the still-running delete gets 409 → **TRANSIENT → retry**, and can only succeed once the delete has fully completed and released the slot. There is no insert-then-late-delete race for the same deterministic `doc_id`. Locked in by `test_insert_409_is_transient_not_permanent` and documented in `client.delete_document`.
- **HIGH — crash after delete, before insert, with no queue crash re-drive.** **Real, accepted, out of 03-A scope.** This is the known durability gap (forensic §8): a worker crash between the delete and a successful insert can leave the doc gone until a later save/REBUILD/RECONCILE. INDEX is fail-open/rebuildable; durable re-drive is exactly 03-B/03-C and RECONCILE is 03-D. 03-A does not and is not meant to close it (see below).

**Confidentiality & durability posture (summary).** 03-A is defense-in-depth against stale egress: source-id-only payload (no queued text), reload-current-at-execution, and a double `confirm_current` around the delete. Two residuals are **intentionally deferred** and do not block 03-A sign-off because INDEX/REINDEX carries no retention guarantee and is rebuildable: (1) the sub-step confirm→POST window and ack-loss (stale egress), and (2) crash-after-delete leaving a doc missing (availability). Both are closed by durable DELETE + RECONCILE (03-B/03-D) and, for egress, query-time validation (AGR-005 §8).

## Exact files touched

**New:**
- `open_notebook/integrations/graphrag/lifecycle.py` — `index_source` orchestration + `IndexResult`/`IndexOutcome`.
- `commands/graphrag_commands.py` — `graphrag_index_source` command.
- `tests/test_graphrag_lifecycle.py`, `tests/test_graphrag_command_seam.py`.
- `docs/agribank/development/GRAPHRAG_03A_INDEXING.md` (this file).

**Edited:**
- `open_notebook/integrations/graphrag/models.py` — `DeleteState`, `DeleteOutcome`, `GraphRAGConflictError`.
- `open_notebook/integrations/graphrag/client.py` — `compute_doc_id`, `delete_document`, HTTP 409 → `GraphRAGConflictError`.
- `open_notebook/integrations/graphrag/service.py` — `index_source`, `delete_document_for_source`.
- `open_notebook/graphs/source.py` — fail-open `_maybe_enqueue_graphrag_index` seam in `save_source` (flag-gated, LightRAG-agnostic).
- `commands/__init__.py` — register `graphrag_index_source_command`.
- `tests/test_graphrag_integration.py`, `tests/test_graphrag_isolation.py` — GraphRAG-02 guards updated to reflect the approved 03-A surface (409 conflict type; approved referrer/command set).

**NOT touched:** `Source.save()`, `save_source` content logic, `vectorize()`, `vector_search()`, `fn::vector_search`, `Source.delete()`, `source_delete` event, any migration (count stays 46), Ask, Chat, frontend.

## Deferred DELETE lifecycle

Durable DELETE (DB-event tombstone drained by an HTTP-capable, flag-independent worker), RECONCILE, and REBUILD are **not** in 03-A. The `delete_document`/`delete_document_for_source` methods added here serve only the reindex-internal remove-before-insert; they carry no retention guarantee. See forensic §17–§21.

## Verification

Targeted lifecycle + GraphRAG-02 regression + full backend pytest + ruff + mypy + Karpathy + independent Codex review. Recorded in `.planning/2026-08-28-graphrag-03a-index-reindex/progress.md`.
