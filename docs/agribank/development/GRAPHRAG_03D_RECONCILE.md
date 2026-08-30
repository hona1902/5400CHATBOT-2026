# GraphRAG-03D — RECONCILE / Drift Detection / Safe Repair

**Status: GraphRAG-03D APPROVED / COMPLETE** — signed off 2026-08-30. Karpathy CLEAN ·
Codex A APPROVE · Codex B resolved · Codex C resolved (run via `codex review`; focused
re-review clean) · no unresolved actionable HIGH/MEDIUM. **Branch:**
`feature/graphrag-lifecycle` · **Baseline:** `c830e5a` (tag `graphrag-03c-approved`).
**SurrealDB runtime:** `2.6.5` · **LightRAG pinned:** `v1.5.6`. **Egress:** synthetic /
public / anonymized only; Boundary B remains **NOT approved** for internal data.

Forensic basis: [`../architecture/GRAPHRAG_LIFECYCLE_FORENSIC.md`](../architecture/GRAPHRAG_LIFECYCLE_FORENSIC.md) §12.
Depends on: [`GRAPHRAG_03A_INDEXING.md`](GRAPHRAG_03A_INDEXING.md) ·
[`GRAPHRAG_03B_DURABLE_DELETE.md`](GRAPHRAG_03B_DURABLE_DELETE.md) ·
[`GRAPHRAG_03C_TOMBSTONE_DRAIN.md`](GRAPHRAG_03C_TOMBSTONE_DRAIN.md).

---

## 1. Scope

Implemented: a bounded, operator-triggered **reconcile** that compares canonical
SurrealDB `source` records against LightRAG derived documents, classifies drift, and
applies ONLY safe repairs by **reusing** the existing lifecycle — 03B/03C durable
deletion for owned orphans / should-be-absent docs, and 03A indexing for
authoritatively-missing live sources. AUDIT (detect-only) is the default; REPAIR is
opt-in. Delivered as a `graphrag_reconcile` surreal-commands command.

**NOT** implemented (deliberately, per approval): REBUILD (03E); bulk reindex-every-source;
tombstone **resolution** by 03D (stays with 03C at its proven ceiling); a second remote
delete engine; content-drift convergence; HybridRetriever/RRF/reranking; Ask/Chat/frontend;
generation-aware doc_id; repository-wide source-id immutability; **any migration** (24/25
frozen, no 26); any new scheduler/daemon; any new public API.

## 2. Source-of-truth model

SurrealDB `source` is canonical; LightRAG is derived, rebuildable, removable. The desired
derived state is a pure function of CURRENT canonical state:

| Canonical state | Indexing flag | Desired derived state |
|---|---|---|
| absent | any | **ABSENT** |
| live, empty/whitespace text | any | **ABSENT** |
| live, non-empty text | ON | **PRESENT** (current) |
| live, non-empty text | OFF | **ABSENT** (no Boundary-B egress) |

No reconcile action ever mutates canonical `source` data.

## 3. Defense-in-depth role

03D does **not** replace primary deletion correctness. That remains 03B (durable
tombstone written atomically with every source delete, incl. raw SurrealQL) + 03C
(arm_id-CAS drain, periodic wake-up re-drive). 03D is a backstop that finds drift the
primary path structurally cannot see — chiefly **orphans with no local tombstone** (a
source deleted before 03B shipped, or a document that drifted from canonical) — and
routes each finding back through the SAME 03B/03C/03A primitives.

## 4. 03A / 03B / 03C dependencies (reused, never duplicated)

- **03A** `index_source` / the `graphrag_index_source` command — missing-doc repair
  enqueues it with `source_id` ONLY (worker reloads CURRENT source).
- **03B** `graphrag_deletion` tombstone table (migrations 24/25) — orphan / should-be-absent
  repair arms a row here.
- **03C** `converge_tombstone` drain + `arm_id` CAS resolution + wake-up loop — performs the
  actual remote delete and resolution. 03D calls `enqueue_drain_if_pending` after arming.
- Identity: `record_id_for` (lossless), `compute_doc_id = "doc-"+md5(source_id)`,
  `is_valid_record_id`, `_INDEXABLE_TABLES` — all reused, no second identity implementation.

## 5. Remote ownership contract (STRONG — proven, never assumed)

A remote document is Open-Notebook-**owned** only when BOTH hold
(`reconcile.classify_ownership`):

- **A.** `doc.file_path` parses losslessly as a canonical `source` RecordID
  (`is_valid_record_id(file_path, {source})` / `record_id_for`), and
- **B.** `compute_doc_id(canonical(file_path)) == doc.id`.

Outcomes: **OWNED** (both hold, returns the canonical source_id) · **FOREIGN** (no
usable `file_path`, or it is not a canonical `source` id) · **UNKNOWN_OWNERSHIP**
(valid `source` id but the doc id does not match our deterministic derivation —
inconsistent provenance). A doc whose id merely starts with `doc-` is **never** owned on
that basis. FOREIGN / UNKNOWN_OWNERSHIP are **report-only** — never a destructive target
(task §5/§6/§28). Verified live against v1.5.6: a really-inserted synthetic doc carries
`file_path == our source_id` and `id == compute_doc_id(source_id)` → OWNED.

## 6. Canonical inventory

- Existence/empty check: `SELECT full_text FROM $id` per record, reduced to
  absent/empty/non-empty with the SAME `.strip()` semantics as the 03C drain; `full_text`
  is read locally ONLY to decide empty/non-empty and is **never** transmitted, logged, or
  returned. One record at a time → bounded memory regardless of corpus size.
- Missing sweep enumerates ids by **keyset** (`WHERE id > $last ORDER BY id ASC LIMIT n`),
  never OFFSET, capped at `max_records`. Only `id` is selected in the enumeration.
- No assets / notebooks / credentials / unrelated metadata are loaded.

## 7. Remote inventory

`POST /documents/paginated` via `client.list_documents_detailed` →
`RemoteDocumentsPage{documents:[RemoteDocument{doc_id,file_path,status}], page, page_size,
total_count, total_pages, has_next}`. `content_summary`/content fields are never surfaced.
The 03C absence probe (`list_documents_page` / `confirm_document_absent`) is now a thin
projection over the same parser — its single-response contract is byte-identical.

## 8. Inventory completeness model

- **Orphan detection (remote sweep) uses POSITIVE observation** and therefore tolerates
  incomplete/racy enumeration: an offset shift under concurrent mutation can only make a
  run MISS an orphan (a re-run converges), it can NEVER manufacture a false orphan. Multi-
  page offset enumeration is thus acceptable here.
- **Missing detection requires a COMPLETE authoritative snapshot** — the exact 03C proof:
  `total_pages <= 1` AND the id count equals `total_count`. Above that ceiling the whole
  missing phase is marked INCOMPLETE and skipped.
- Any bound hit (`max_records`; **and a page bound** `max_records/page_size + 1` that stops
  an empty-page/`has_next=True` loop), any listing error, a **malformed page** (non-dict rows
  dropped by the client → `RemoteDocumentsPage.malformed`), an **unreadable row** (dict with
  no id), or an inability to establish absence sets `incomplete_inventory = True`, so a
  truncated/racy/partial run is **never** read as "no drift".

## 9. Scalable absence analysis (the forensic GATE — task §8/§9/§35)

**Verdict: pinned LightRAG v1.5.6 CANNOT prove scalable authoritative absence.** Evidence
(OpenAPI contract of the live pinned runtime + prior 03C live verification):

- `POST /documents/paginated` is **offset-only**, `page_size` hard-capped at **200**, no
  by-id/file_path equality filter, no cursor/next-token.
- `GET /documents` returns an **unbounded in-memory dump** with **no** consistency/version
  guarantee — not a scalable authoritative snapshot.
- **No exact by-id lookup**; `track_status/{id}` keys on the ingest batch, not the doc id.
  The only opaque cursor in the API is `GET /documents/source_conflicts` (conflicts, not
  the corpus).
- **No `content_hash`** anywhere on `DocStatusResponse` (only `content_length` /
  `content_summary`).

Therefore 03D **does not** raise the 03C >200 tombstone-resolution ceiling and **does not**
resolve tombstones from multi-page absence. This is an explicit, documented **blocker**:
solving it needs an upstream LightRAG capability (an exact by-id/file_path lookup, or a
corpus-wide keyset cursor). 03D never downgrades UNKNOWN→ABSENT to finish the phase.

## 10. Classification matrix

| Class | Signal | 03D action (REPAIR) | AUDIT |
|---|---|---|---|
| OWNED, canonical absent (**orphan**) | doc owned + `SELECT full_text FROM $id` empty result | arm durable tombstone → 03C deletes | count + sample |
| OWNED, canonical live-empty (**should-be-absent**) | owned + empty/whitespace text | arm durable tombstone → 03C deletes | count |
| OWNED, live non-empty, flag OFF (**should-be-absent**) | owned + text + indexing off | arm durable tombstone → 03C deletes (no egress) | count |
| OWNED, live non-empty, flag ON (**present-unverified**) | owned + text + indexing on | **no action** (content drift = 03E) | count |
| FOREIGN | no `file_path` / not a `source` id | **report only** | count + sample |
| UNKNOWN_OWNERSHIP | valid `source` id, doc-id mismatch | **report only** | count + sample |
| CANONICAL missing (authoritative) | live non-empty + doc_id ∉ complete snapshot + flag ON | enqueue `source_id`-only 03A index | count + sample |
| CANONICAL missing (uncertain / >ceiling) | snapshot incomplete | **UNKNOWN** — no reindex | incomplete flag |

## 11. Audit mode (default)

Detect + classify + count + capped samples. **Mutates nothing**: no arm, no enqueue, no
remote call beyond read-only listing. A destructive/repair action requires explicit REPAIR
intent.

## 12. Repair mode (opt-in)

`repair=True`. Arms durable deletion intents for owned orphans / should-be-absent docs and,
when indexing is ON and absence is authoritative, enqueues `source_id`-only 03A index
repairs. It **never** deletes remotely itself and **never** resolves a tombstone. After
arming any intent it calls `enqueue_drain_if_pending` so 03C converges promptly (dedup +
idempotent; correctness never depends on it).

## 13. Orphan repair (reuse 03C, no second delete engine)

`deletion.arm_orphan_deletion(source_id)`:
- UPSERTs `type::thing("graphrag_deletion", $sid)` — the **same deterministic key** the
  migration-24/25 event uses (`type::thing("graphrag_deletion", $before.id)`), so an
  orphan-armed tombstone and an event-armed one collapse to ONE row (live-verified).
- `arm_id = rand::uuid()` is **DB-generated** (server-side, never minted in Python);
  `status="pending"`, `requested_at=now`, `next_attempt_at=now` mirror the event so 03C
  drains it identically.
- **Only arms when no pending tombstone already exists** (`pending_deletion_exists`) — no
  needless re-arm churn that could no-op an active 03C CAS (task §24). The check-then-UPSERT
  is best-effort anti-churn, not a lock: the deterministic key makes a concurrent
  reconcile / real delete collapse to one row regardless.
- `record_id_for` binds numeric vs string-numeric vs escaped identities losslessly; a
  dangling `record<source>` link (source already gone) is allowed.

## 14. Missing repair (reuse 03A)

Only when flag ON + REPAIR + a COMPLETE authoritative snapshot + the live non-empty
source's `compute_doc_id` ∉ the snapshot id set is a source a missing candidate. Before
enqueuing, REPAIR does a **fresh single-response `confirm_source_document_absent`** for that
candidate (the snapshot may have gone stale since it was taken); a reindex is enqueued
ONLY on a fresh `ABSENT_CONFIRMED`, so a document that raced in after the snapshot is not
needlessly reindexed, and an `UNKNOWN` fresh result marks the run incomplete instead of
reindexing blindly. The enqueue is `graphrag_index_source` with `{source_id}` ONLY (no
`full_text` — the worker reloads CURRENT source and no-ops if it changed/emptied/vanished,
closing any TOCTOU). Above the ceiling → UNKNOWN / INCOMPLETE, never a reindex (no REBUILD
creep, no false Boundary-B egress). AUDIT counts missing candidates from the snapshot but
performs no confirm and no enqueue.

## 15. Flag-OFF behavior

Missing detection/repair is skipped entirely (a missing doc's desired state is ABSENT when
indexing is off). Owned orphans / should-be-absent docs are still armed for deletion
(confidentiality is flag-independent — Boundary A only, no canonical text egress).

## 16. Live-empty behavior

An owned doc whose canonical source is live but empty/whitespace converges to ABSENT via an
armed tombstone (03A's `skipped_no_content` does not delete, so the tombstone → 03C does).

## 17. Tombstone interaction

03D **discovers** drift and **arms** intents; it does **not** enumerate/resolve tombstones.
Resolution stays with 03C (`arm_id` CAS on CONFIRMED absence at the single-response ceiling).
A structural test asserts `reconcile.py` references none of `resolve_tombstone_cas`,
`resolve_current_tombstone_cas`, `defer_tombstone_cas`, `delete_document_for_source`,
`confirm_source_document_absent`.

## 18. arm_id CAS interaction

03D never performs a CAS. By arming only when no pending tombstone exists, it avoids
regenerating `arm_id` under an in-flight 03C drain (which would make that drain's resolve
CAS match zero rows → a harmless SUPERSEDED re-drive, but avoidable churn). A concurrent
real re-arm (source re-deleted) simply overwrites the same deterministic row.

## 19. Concurrency

- **reconcile + 03A:** missing repair enqueues source_id-only; 03A reloads current state
  → safe (a stale enqueue no-ops).
- **reconcile + 03C:** arming an already-pending intent is skipped; arming a fresh intent is
  drained normally; 03D never resolves, so it cannot clear a newer intent.
- **Source recreation:** an armed tombstone is re-checked by 03C against CURRENT canonical
  state before any destructive action (03C §7) — a recreated live source is reindexed, not
  wrongly deleted.
- **Duplicate reconcile:** idempotent — positive detection + arm-if-absent + idempotent
  enqueue; two runs converge to the same armed set.
- **Pagination mutation:** tolerated for orphan detection (under-detection only); missing
  detection refuses to act unless the snapshot is provably complete.

## 20. Batching / memory bounds

`GraphRAGReconcileConfig` (all clamped on load): `remote_page_size` 10..200,
`canonical_batch_size` 1..500, `max_records` 1..50000 (per phase), `max_sample_ids` 1..100.
No full corpus is held in memory: the remote sweep streams page→doc→classify; the missing
sweep holds one complete-snapshot id set (bounded by the 200-doc ceiling that gates it) and
enumerates canonical ids by keyset one bounded batch at a time. Hitting any cap sets
`incomplete_inventory`.

## 21. Security / logging

Logs and the result carry only counts, normalized outcomes, sanitized exception **class**
names, and **capped sample identities** (record ids / doc-id hashes) — never document text,
titles, URLs, file paths, credentials, or raw sidecar bodies. `full_text` is read only to
classify empty/non-empty and never leaves the process. A malformed/foreign `file_path` is
never turned into an arbitrary delete/HTTP target (ownership must be STRONG first).

## 22. Boundary A / Boundary B

Reconcile enumeration + deletion arming are **Boundary A** only (Open Notebook ↔ sidecar
listing; DB writes). Missing repair reuses the 03A index path (**Boundary B**, synthetic
only) and ONLY when the flag is on. No new egress; no canonical text is transmitted by
reconcile itself.

## 23. Live LightRAG evidence (pinned v1.5.6)

Against the running `ghcr.io/hkuds/lightrag:v1.5.6` (auth via `X-API-Key` from local config;
key never echoed/committed): the paginated detailed contract returns `id` + `file_path` +
`pagination{total_count,total_pages}`; `page_size=201` is rejected (real single-response
ceiling); a really-inserted synthetic doc is classified **OWNED** with the correct
source_id; and an end-to-end run (real sidecar + real DB) positively observes an owned
**orphan**, arms a durable deletion intent, and leaves the remote doc **present** (03D does
not delete — 03C does later).

## 24. Live SurrealDB evidence (v2.6.5)

`arm_orphan_deletion` creates exactly one row with a DB-generated `arm_id`, `status=pending`,
`next_attempt_at` set, and no content fields; a second arm while pending is suppressed (no
churn); a real source delete (event-armed tombstone) followed by `arm_orphan_deletion`
yields ONE row (deterministic-identity interoperability); numeric vs string-numeric ids arm
two DISTINCT tombstones.

## 25. Failure / race matrix (selected; full list in tests)

Remote owned orphan (no tombstone → armed); orphan with existing pending tombstone (not
re-armed); live-empty owned (armed); flag-OFF owned live-nonempty (armed, no egress); flag-ON
live-nonempty + authoritative-missing (source_id-only 03A enqueue); >ceiling missing
(UNKNOWN/INCOMPLETE, no reindex); foreign / unknown-provenance (report only); doc-id/provenance
mismatch (UNKNOWN_OWNERSHIP); numeric/string-numeric distinct; multi-page sweep visits all
pages; max_records cap → INCOMPLETE; remote listing error → INCOMPLETE (not healthy);
base_url unset → INCOMPLETE (not "no drift"); AUDIT mutates nothing; reconcile+03C /
reconcile+03A safe; recreated source re-checked by 03C.

## 26. Exact files changed

**New:** `open_notebook/integrations/graphrag/reconcile.py`;
`tests/test_graphrag_03d_reconcile.py`; this doc.
**Edited:** `config.py` (`GraphRAGReconcileConfig` + `load_reconcile_config` + clamped
constants); `models.py` (`RemoteDocument`, `RemoteDocumentsPage`); `client.py`
(`list_documents_detailed` + `list_documents_page` projection refactor); `deletion.py`
(`pending_deletion_exists`, `arm_orphan_deletion`); `service.py`
(`list_remote_documents_detailed`); `commands/graphrag_commands.py` (`graphrag_reconcile`
command) + `commands/__init__.py` (register); guard-test updates in
`tests/test_graphrag_isolation.py` and `tests/test_graphrag_deletion.py`.
**NOT touched:** migrations 24/25 (frozen — **no migration 26**), 03A/03B/03C behavior,
`api/main.py`, `graphs/source.py`, Ask/Chat/frontend, retriever, `fn::vector_search`,
the `graphrag_source_delete` event. Migration count stays **50**.

## 27. Known limitations

- **>200-doc authoritative absence is NOT solvable in v1.5.6** (§9) → 03D does not resolve
  tombstones and missing-detection is ceiling-limited. Documented blocker, not hidden;
  needs an upstream by-id/keyset capability.
- **No content-drift detection** (no `content_hash`) → an owned present doc is
  PRESENT_UNVERIFIED, never "stale"; content convergence is deferred to 03E.
- **Operator-triggered** (no scheduler) — reconcile runs when the `graphrag_reconcile`
  command is submitted; 03C's continuous drain remains the always-on deletion path.
- **Missing repair is best-value for small corpora** (ceiling-limited); large corpora rely
  on 03A fail-open + 03E REBUILD.

## 28. Deferred GraphRAG-03E (REBUILD)

Full re-derivation from canonical text; forced convergence of PRESENT_UNVERIFIED docs;
disaster recovery. A Boundary-B-scale egress event → synthetic-only until Boundary B is
approved. Not in 03D.

## 29. Migration-26 decision

**No migration 26.** Reconcile is ephemeral (inventory → classification → existing
lifecycle repair) and needs no persistent run-state; orphan arming reuses the existing
`graphrag_deletion` schema (migrations 24/25). Migrations 24 and 25 remain **frozen**.

## 30. Acceptance criteria

| # | Criterion | Status |
|---|---|---|
| 1 | Ownership proven (file_path lossless `source` id AND compute_doc_id match); FOREIGN/UNKNOWN never destructive | ✅ (unit + live) |
| 2 | Owned orphan / live-empty / flag-OFF → arm durable tombstone (reuse 03C), never direct delete | ✅ (mock + live e2e) |
| 3 | No needless re-arm churn; deterministic identity interoperates with the delete event | ✅ live |
| 4 | 03D never resolves tombstones and never runs a 2nd delete engine | ✅ structural + no-side-effects guard |
| 5 | Missing repair: authoritative single-page only, flag ON, source_id-only 03A enqueue; >ceiling → UNKNOWN, no reindex | ✅ |
| 6 | AUDIT default mutates nothing; REPAIR opt-in | ✅ |
| 7 | Bounded memory/inventory; INCOMPLETE (never false healthy) on cap/error/base_url-unset | ✅ |
| 8 | No content/credentials in results or logs; capped samples only | ✅ |
| 9 | No migration (24/25 frozen; count 50); no scheduler; no new API | ✅ |
| 10 | >200 absence ceiling documented as blocker, NOT claimed solved; no false absence | ✅ |
| 11 | 03D tests + 02/03A/03B/03C regression + live LightRAG + live SurrealDB green | ✅ 338 GraphRAG pass / 1 skip |
| 12 | Full backend green except the 5 documented pre-existing baseline failures | ✅ 983 pass / 6 skip / 5 pre-existing |
| 13 | ruff + mypy clean on changed source | ✅ (`mypy .` exit 0; only pre-existing 03A test arg-type notes) |
| 14 | Karpathy diff + Codex A/B/C, no unresolved actionable HIGH | ✅ Karpathy CLEAN; Codex A APPROVE; Codex B resolved; **Codex C ran (codex review) — P2/P3 resolved, final re-review no new actionable HIGH/MEDIUM** — see §31 |

**COMPLETE — all criteria met; signed off 2026-08-30.** RECONCILE is defense-in-depth over
the 03A/03B/03C lifecycle, never primary deletion correctness. Remaining GraphRAG-03E
(REBUILD) is not started.

## 31. Review findings & resolutions

- **Karpathy diff: CLEAN** — every code hunk traces to the task; no collateral files, no
  reformatting; migrations 24/25 untouched (count 50); two environmental nits only.
- **Codex A (ownership/deletion): no exploitable findings** — independently verified the
  two-part ownership gate, deletion-path narrowness, `arm_orphan_deletion` idempotency +
  DB-generated `arm_id`, and the `type::thing` key collapse.
- **Codex B (inventory/absence/scale): 1 HIGH + 3 MEDIUM + 1 LOW — all resolved:**
  - **#1 HIGH (stale complete-snapshot → false missing / redundant reindex):** REPAIR now
    does a **fresh per-candidate `confirm_source_document_absent`** before enqueue; a
    raced-in doc (FOUND) is not reindexed; UNKNOWN → incomplete (§14).
  - **#2 MEDIUM (silent canonical-state error → false healthy):** a Phase-B `_canonical_state`
    error now sets `incomplete_inventory`.
  - **#3 MEDIUM (malformed page reported complete):** the client flags `malformed` when
    non-dict rows are dropped, and an unreadable row (no id) → `incomplete_inventory`.
  - **#4 MEDIUM (empty-page `has_next` loop):** `_sweep_remote` is now bounded by a **page
    cap** as well as `max_records`; exceeding it → incomplete.
  - **#5 LOW (offset duplicate counting):** accepted/documented — under offset pagination a
    concurrent insert can re-surface a scanned doc; arming is idempotent and ownership is
    proven, so at worst a count is inflated (no wrong action). Not fixed by design.
- **Live-only bug caught by 03D's own live SurrealDB test (not by mocks or Codex):**
  `repo_query("SELECT VALUE id ...")` returns **stringified** ids; binding a string as
  `$last` makes `id > $last` non-strict on v2.6.5, and `SELECT full_text FROM $id` with a
  string returns nothing (a live source misread as absent). **Fixed:** Phase B rebuilds each
  enumerated id with `record_id_for` before use (RecordID cursor + record `FROM $id`),
  guarded by `test_live_canonical_keyset_recordid_cursor_is_strict`.
- **Codex C (repair/boundaries): ran via the supported `codex review --uncommitted` path**
  (Codex CLI 0.150.1, read-only, foreground — NOT the blocked companion script; no
  tooling-permission changes, no safety-control bypass). Initial pass returned 1 MEDIUM + 1
  LOW (both **resolved**), then five focused re-reviews iteratively hardened the
  pagination-completeness path to fully fail-closed:
  - **P2 (pagination completeness) — resolved:** `_sweep_remote` continues while
    `total_pages` indicates more pages (not just `has_next`) and now claims completeness
    ONLY when the whole corpus was a single internally-consistent page; a unified
    `_is_complete_snapshot` oracle gates the remote sweep, the Phase-B missing snapshot, AND
    the fresh repair snapshot — failing closed on `has_next`, multi-page, invalid/negative
    `total_pages`, `total_count` over/under-report, dropped (non-dict) rows, and
    blank/whitespace ids (client strips ids). Any disagreement → `incomplete_inventory`,
    never a false COMPLETE / false ABSENT / reindex-from-unreliable-snapshot.
  - **P3 (error accounting) — resolved:** all three listing-failure paths (remote sweep,
    Phase-B snapshot, repair snapshot) increment `summary.errors`.
  - Final focused re-review: **fully closed, no new actionable HIGH/MEDIUM.**
  - The 03D missing-repair path no longer relies on the 03C `confirm_document_absent` proof;
    it uses the same fail-closed `_is_complete_snapshot` oracle (03C untouched).
