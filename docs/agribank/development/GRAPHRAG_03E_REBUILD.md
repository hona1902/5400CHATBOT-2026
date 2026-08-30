# GraphRAG-03E — REBUILD / Canonical Full Convergence

**Status:** ✅ **COMPLETE / APPROVED** — signed off 2026-08-30 (live LightRAG v1.5.6 synthetic EXECUTE gate PASSED; Karpathy CLEAN; Codex A/B/C resolved; no unresolved actionable HIGH/MEDIUM).
**Branch:** `feature/graphrag-lifecycle` · **Baseline:** `94b8885` (tag `graphrag-03d-approved`)
**LightRAG pinned:** `v1.5.6` · **SurrealDB pinned:** `v2.6.5`
**Migrations:** none added — count stays **50**; migrations 24/25 byte-for-byte unchanged; **no migration 26**.

---

## 1. Scope

GraphRAG-03E adds **one operator-triggered, bounded canonical REBUILD dispatcher** that
re-drives the EXISTING GraphRAG-03A `graphrag_index_source` command (source_id ONLY)
over the CURRENT non-empty Open Notebook Sources. It has two modes — **PLAN** (read-only,
default) and **EXECUTE** (explicit dispatch) — and a keyset continuation contract for
corpora larger than one bounded run.

In scope: a new `open_notebook/integrations/graphrag/rebuild.py`, a clamped
`GraphRAGRebuildConfig`, the `graphrag_rebuild` surreal-command, and a property-oriented
test module. **Out of scope and explicitly NOT built:** any global LightRAG purge, any
second index engine, any deletion of foreign/unknown documents, any automatic/scheduled
trigger, any persistent rebuild-run state, any migration, any doc_id/identity change, any
`full_text` command payload, any Boundary-B approval widening.

## 2. Why rebuild exists

LightRAG v1.5.6 exposes **no `content_hash`** (only `content_length`/`content_summary` on
`DocStatusResponse`). Therefore 03D RECONCILE can only classify an owned, present,
live+non-empty, flag-ON document as **PRESENT_UNVERIFIED** — it cannot prove the remote
content matches the current canonical text, and it must never guess "stale" and blindly
reindex (that would be REBUILD creep + a false Boundary-B egress). 03E is the operator's
deliberate tool to **force** convergence of those PRESENT_UNVERIFIED documents (and to
re-derive after disaster recovery), by re-submitting each current non-empty Source through
the approved 03A delete-then-insert lifecycle.

## 3. Relationship to 03A / 03C / 03D

03E **reuses**, never duplicates:

| Reused primitive | Origin | Role in 03E |
|---|---|---|
| `graphrag_index_source` command (source_id ONLY) | 03A | the unit of work 03E enqueues; the worker reloads CURRENT source at execution |
| keyset canonical enumeration + RecordID cursor | 03D `_sweep_canonical_missing` | how 03E walks the `source` table safely on v2.6.5 |
| `_canonical_state` empty/non-empty semantics (`.strip()`) | 03C/03D | how 03E classifies a source without transmitting text |
| `record_id_for` lossless RecordID builder | models | cursor validation + numeric/string-numeric distinctness |
| `service.health()` (`GET /health`) | 02 | content-free EXECUTE preflight |
| `submit_command` enqueue seam | surreal_commands | fire-and-forget source_id-only dispatch |

03E does **not** touch the deletion lifecycle: empty/should-be-absent cleanup stays with
**03D REPAIR → 03B/03C** (see §15). 03E does **not** re-implement 03D's remote sweep — it
never lists or classifies remote documents.

## 4. Canonical source-of-truth

SurrealDB `source` is canonical; LightRAG is derived. The desired derived state is
determined by CURRENT canonical state at 03A execution time (not at 03E enumeration time):

    canonical live, non-empty text   -> derived doc should EXIST (current)  -> 03E dispatches 03A
    canonical live, empty/whitespace -> derived doc should be ABSENT        -> 03E reports (Option A)
    canonical absent (deleted)       -> derived doc should be ABSENT        -> 03E reports "vanished"

03E enumerates `source_id`s; 03A re-reads the CURRENT Source when each job runs. A source
updated/deleted/emptied between enumeration and execution is handled by 03A
(`indexed` current / `skipped_absent` / `skipped_no_content`), never by a queued text copy.

## 5. PLAN mode (default)

Strictly read-only. `mode="plan"` (the default) enumerates canonical sources by keyset,
classifies each via `_canonical_state`, and counts. It performs:

- **no** `health()` / LightRAG HTTP request (PLAN never probes the sidecar),
- **no** command enqueue, **no** tombstone arm, **no** provider call,
- **no** canonical mutation, **no** document content in the result or logs.

PLAN works from the canonical DB alone (no flag/config required) and reports
`execute_allowed` (flag ON AND base_url set) so an operator knows whether EXECUTE would
dispatch. `planned` == `eligible_nonempty` = the number of source_id-only 03A jobs EXECUTE
would enqueue. Completion is `PLAN_ONLY`.

## 6. EXECUTE mode (explicit opt-in)

`mode="execute"` must be explicitly requested. Order (preflight BEFORE any dispatch):

    1. validate continuation cursor (both modes)  -> invalid => INVALID_CURSOR, fail closed
    2. dedicated EXECUTE lock ON?                  -> else SKIPPED_EXECUTE_NOT_ALLOWED (no probe, no dispatch)
    3. flag ON?                                    -> else SKIPPED_DISABLED (zero dispatch)
    4. base_url set?                               -> else SKIPPED_NOT_CONFIGURED (no partial dispatch)
    5. content-free GET /health preflight healthy? -> else PREFLIGHT_FAILED (zero dispatch)
    6. bounded keyset sweep: for each CURRENT non-empty source, enqueue a source_id-only 03A job

The **dedicated EXECUTE lock** (`OPEN_NOTEBOOK_GRAPHRAG_REBUILD_EXECUTE_ENABLED`, default
OFF) is checked FIRST and is SEPARATE from `OPEN_NOTEBOOK_GRAPHRAG_ENABLED`: a corpus-wide
rebuild re-sends the whole corpus's text across Boundary B, so merely enabling GraphRAG for
ordinary 03A ingestion must NOT also unlock EXECUTE. While the lock is off, EXECUTE returns
`SKIPPED_EXECUTE_NOT_ALLOWED` without enumerating or even probing the sidecar. **This lock
does not approve Boundary B for real internal data** — it only prevents an accidental
corpus-wide dispatch and keeps EXECUTE an explicit, deliberate operator action (Codex-B).

A failed gate/preflight returns before enumeration — zero partial egress. Once dispatch
begins, a later sidecar/provider failure is an **03A** execution outcome; 03E never claims
those jobs indexed. `enqueued` counts ACCEPTED enqueue calls only; a `submit_command`
failure increments `enqueue_failures` (never `enqueued`).

## 7. Boundary-B constraints

REBUILD/REINDEX re-send full canonical text to the sidecar, which during indexing forwards
text to an LLM for entity/relation extraction — so an EXECUTE run can push the whole corpus
across **Boundary B** (sidecar → LLM/embedding provider). Boundary B remains **NOT APPROVED
for real internal data**. Therefore:

- EXECUTE is **operator-triggered only** — never at startup, never scheduled, never after
  migrations, never automatically over production sources (verified by
  `test_no_automatic_or_scheduled_rebuild`).
- EXECUTE is additionally gated by a **dedicated default-OFF lock**
  (`OPEN_NOTEBOOK_GRAPHRAG_REBUILD_EXECUTE_ENABLED`) so enabling GraphRAG for ordinary
  ingestion never unlocks a corpus-wide rebuild (§6). The lock is a mechanism brake, **not**
  a Boundary-B approval.
- All implementation/live tests use **synthetic/public/anonymized** content only.
- This slice does **not** introduce any production Boundary-B approval. Running EXECUTE
  against real internal Sources is prohibited until a separate egress decision exists.

**Safeguards are twofold:** (1) architectural — nothing in the app wiring invokes
`graphrag_rebuild`; it exists only as a manually-submitted command; and (2) the dedicated
default-OFF EXECUTE lock, so a corpus-wide egress can never be an accidental side effect of
GraphRAG being enabled. The config still cannot cryptographically distinguish "operator" from
"automation" that has the lock env set, so operating discipline (synthetic-only, lock off in
real-data environments) remains a documented requirement.

## 8. Source enumeration

Only `id` is selected in the enumeration (`SELECT VALUE id FROM source ...`); `full_text`
is read **one record at a time** via `_canonical_state` solely to decide empty/non-empty,
and is never transmitted, logged, or returned. Memory stays bounded regardless of corpus
size — 03E never runs `SELECT * FROM source` into memory.

## 9. Keyset / cursor design

Traversal is **keyset**, never OFFSET (offset paging over a mutating canonical table can
skip rows):

    first page:  SELECT VALUE id FROM source ORDER BY id ASC LIMIT $n
    continue:    SELECT VALUE id FROM source WHERE id > $last ORDER BY id ASC LIMIT $n

The cursor bound as `$last` **must be a `RecordID`**, rebuilt from the enumerated id via
`record_id_for`. On SurrealDB v2.6.5 a bound *string* makes `id > $last` non-strict (it
fails to exclude the boundary) and `SELECT full_text FROM $id` returns nothing (a live
source misread as absent) — a live-only bug the mocks cannot catch, guarded by
`test_live_keyset_continuation_covers_all_without_skip_or_repeat` and 03D's
`test_live_canonical_keyset_recordid_cursor_is_strict`. The continuation `next_cursor` is a
canonical source RecordID string (no content); numeric `source:123` and string-numeric
`source:⟨123⟩` stay distinct through the round-trip. An invalid cursor fails closed
(`INVALID_CURSOR`) before any enumeration or dispatch.

## 10. Batching / caps

`GraphRAGRebuildConfig` (env `OPEN_NOTEBOOK_GRAPHRAG_REBUILD_*`, all clamped on load, a bad
value can never force an unbounded scan):

| Knob | Env | Default | Clamp |
|---|---|---|---|
| `canonical_batch_size` | `…_REBUILD_CANONICAL_BATCH` | 100 | 1 .. 500 |
| `max_sources_per_run` | `…_REBUILD_MAX_SOURCES` | 1000 | 1 .. 50000 |
| `max_sample_ids` | `…_REBUILD_MAX_SAMPLE_IDS` | 20 | 1 .. 100 |
| `execute_enabled` | `…_REBUILD_EXECUTE_ENABLED` | **off** | truthy tokens only (`1/true/yes`) |

`max_sources_per_run` is the fairness cap (§13). The result returns only counts + capped
identity samples — never every source id.

## 11. Completion semantics

The completion vocabulary is deliberately honest (none of the words claim remote
convergence or per-job completion):

| Value | Meaning |
|---|---|
| `PLAN_ONLY` | read-only plan; nothing dispatched |
| `SKIPPED_EXECUTE_NOT_ALLOWED` | EXECUTE requested but the dedicated execute lock is OFF; no probe, zero dispatch |
| `SKIPPED_DISABLED` | EXECUTE requested but flag OFF; zero dispatch |
| `SKIPPED_NOT_CONFIGURED` | EXECUTE requested but base_url unset; zero dispatch |
| `PREFLIGHT_FAILED` | content-free health preflight failed; zero dispatch |
| `INVALID_CURSOR` | continuation cursor did not validate; failed closed |
| `DISPATCH_PARTIAL` | an enqueue/enumeration error occurred this run |
| `DISPATCH_INCOMPLETE` | cap hit with more rows available; continue with `next_cursor` |
| `REBUILD_DISPATCH_COMPLETE` | this sweep/continuation fully dispatched; no continuation, no failure |

**`REBUILD_DISPATCH_COMPLETE` means ONLY:** the canonical sources discovered within THIS
sweep / continuation sequence were fully processed for dispatch, with no continuation
remaining and no enqueue failure. It **does NOT** mean a globally atomic rebuild, that every
source that existed at some wall-clock instant was covered, that every source currently in
the DB was necessarily covered, that every enqueued 03A job completed
(**INDEX_COMMAND_COMPLETION** — a separate, 03A-owned outcome), that every source was
successfully indexed, or that remote content equality was verified
(**REMOTE_CONTENT_CONVERGENCE_VERIFIED** — unavailable on v1.5.6, no `content_hash`).
Sources may be created/updated/deleted/emptied while the sweep runs. Per-source, 03E claims
only *enqueued*, never *reindexed* or *verified* (cf. §33).

## 12. Queue limitations

surreal_commands persists a durable `command` row but: a job set to `running` before
execution has no lease/heartbeat, so a worker crash leaves it stuck; boot only re-scans
`status='new'`; exhausted-retry `failed` is never re-driven; the router exposes
submit/status/list/cancel with no re-drive. 03E therefore treats **enqueue acceptance as
"queued", never "indexed"**, and does not claim convergence from a successful `submit`.
`graphrag_rebuild` uses `retry={"max_attempts": 1}` (like 03C/03D) so a crashed sweep is
not retried in place — an operator re-runs it (from the start or from `next_cursor`).

## 13. Resume / retry, fairness, dedup

- **Crash/resume:** re-run PLAN/EXECUTE from the beginning (03A is idempotent/convergent,
  duplicate enqueues are harmless) or resume from the returned `next_cursor`. No persistent
  rebuild-run state is kept (no migration 26).
- **Fairness:** hitting `max_sources_per_run` with more canonical rows still available sets
  `continuation_required=True` + `next_cursor`. A single keyset **look-ahead** (one row past
  the last handled id) distinguishes "exactly the cap" (COMPLETE) from "more remain"
  (INCOMPLETE), so the exact boundary N==cap never false-signals continuation and N==cap+1
  never false-completes (`test_cap_boundary_exactly_max_is_complete`,
  `test_cap_boundary_max_plus_one_requires_continuation`). A capped run **never** silently
  returns COMPLETE while more rows are known to remain.
- **Cursor skip-safety under per-row failure (Codex A/C):** the resumable cursor tracks
  `last_good_id` — the last *fully-handled* source (dispatched, or classified
  empty/vanished/planned) — and that is the **only** value ever issued as `next_cursor`. On
  the first per-row failure (a structurally invalid id, a canonical state-read error, or an
  enqueue failure) the sweep **fails stop**: it never advances the cursor past an un-handled
  source, so a resume **re-attempts** that source rather than skipping it, and the run is
  `DISPATCH_PARTIAL` — never `REBUILD_DISPATCH_COMPLETE`. This closes the path where an
  enqueue failure could let a later continuation skip a PRESENT_UNVERIFIED source and still
  report complete (`test_enqueue_failure_never_advances_cursor_past_undispatched_source`,
  `test_enqueue_failure_midway_resumes_before_failed_source`,
  `test_invalid_enumerated_id_halts_partial_never_skips`,
  `test_canonical_state_error_halts_partial`). A structurally invalid id is also surfaced as
  an `invalid_source_id` sample (a remediation target).
- **Dedup:** deliberately NOT implemented. 03A is idempotent/convergent, so duplicate
  `graphrag_index_source` jobs are safe; dedup would be an optimization only, never a
  correctness primitive (`test_duplicate_execute_is_safe`).

## 14. Current-source reload

The work unit is `source_id` ONLY. At execution, 03A reloads the CURRENT Source and honours
its CURRENT text, deletion, and flag/config state. There is no queued `full_text` to
resurrect stale content — the same structural guarantee 03A/03D rely on.

## 15. Empty-source behavior — DECISION A (report only)

**Decision (approved before coding):** 03E EXECUTE indexes **non-empty sources only**.
Live-but-empty (whitespace) sources are:

- counted (`empty`), optionally included in capped identity samples,
- **never** sent to `graphrag_index_source`,
- **never** armed for deletion by 03E, and **never** counted as rebuilt.

Their desired remote state remains ABSENT, but that cleanup belongs to the existing
**03D REPAIR → 03B/03C** durable deletion lifecycle (which already safely arms
should-be-absent owned docs). 03E stays a **rebuild dispatcher, not a deletion
orchestrator** — minimum coupling, and consistent with "rebuild is not a deletion
mechanism". `test_execute_empty_source_reported_never_armed_never_dispatched` and the
structural `test_rebuild_module_never_deletes_arms_or_purges` guard this (the module never
references `arm_orphan_deletion` / `delete_document_for_source` / any tombstone helper).

A source deleted between enumeration and its state read is counted separately as
`vanished` and likewise never dispatched (`test_deleted_after_enumeration_not_resurrected`).

## 16. Flag-OFF behavior

With `OPEN_NOTEBOOK_GRAPHRAG_ENABLED` OFF, EXECUTE returns `SKIPPED_DISABLED` **before**
enumerating — zero enqueue, zero egress, and the sidecar is never probed. EXECUTE is never
silently turned into a delete-all. PLAN still counts locally (no remote call) and reports
`execute_allowed=False`. Deletion drift remains 03C/03D's responsibility.

## 17. Config / preflight

For PLAN: operates from the canonical DB only; no config required. For EXECUTE: flag ON +
`base_url` set are validated first (no partial dispatch on missing config), then a
content-free `GET /health` preflight runs before the first source is dispatched. `health()`
sends no body and no source content (verified against `client.py` — it is a pure liveness
probe), so it is safe to run automatically. If no safe preflight were available this would
be documented as a limitation; here one exists and is used.

## 18. Result model

`RebuildSummary` (typed; counts + capped samples only, no content, no raw payloads):

`mode` · `execute_allowed` (execute lock AND flag AND base_url) · `execute_not_allowed`
(lock OFF) · `skipped_disabled` · `skipped_not_configured` · `preflight_unhealthy` ·
`invalid_cursor` · `canonical_scanned` · `eligible_nonempty` · `empty` · `vanished` ·
`planned` · `enqueued` · `enqueue_failures` · `continuation_required` · `next_cursor` ·
`errors` · `notes` · `samples` (capped by `max_sample_ids`; classes include `planned`,
`empty`, `invalid_source_id`) · derived `completion` · derived `dispatch_complete`. The
`graphrag_rebuild` command output mirrors these fields.

## 19. Security / logging

No document text, titles, URLs, file paths, credentials, or raw provider payloads appear in
the result or logs — only record ids (opaque), normalized outcomes, sanitized exception
**class** names, and aggregate counts (`test_plan_samples_are_ids_only_no_content`, the
log-safe `__str__`). The only free-form value that could reach the sidecar is `source_id`,
and 03E never sends content — it sends `{"source_id": ...}` to the queue, and 03A (through
`build_sidecar_document` → `validate_source_id`) enforces the value allowlist.

## 20. No foreign-document deletion

03E never enumerates, classifies, or deletes remote documents. Foreign/UNKNOWN_OWNERSHIP
documents are entirely outside 03E; they are reported (never deleted) by 03D AUDIT. 03E adds
no destructive remote path of any kind.

## 21. No global purge

REBUILD is **not** a wipe-and-reinsert. It never deletes the LightRAG corpus, never drops
tables, never recreates sidecar storage, and never assumes every `doc-*` belongs to Open
Notebook. It rebuilds CURRENT canonical Open Notebook Sources; orphan/deletion drift remains
handled by 03B/03C/03D. Per source, convergence still uses 03A's idempotent, doc_id-stable
delete-then-insert.

## 22. No content-hash guarantee

Because LightRAG v1.5.6 exposes no `content_hash`, a successful 03A reindex means the CURRENT
canonical Source was submitted through the approved lifecycle — wording used is
**CURRENT_SOURCE_REINDEXED** (an 03A outcome), never **REMOTE_CONTENT_HASH_VERIFIED**. 03E
cannot cryptographically verify remote content equality, and the completion vocabulary
(§11) never claims it. This limitation is inherited, not introduced.

## 23. Failure / race matrix

| # | Canonical state / event | 03E action | Outcome | Safety | Resume | Exact guarantee |
|---|---|---|---|---|---|---|
| 1 | PLAN on empty DB | enumerate | 0 scanned, `PLAN_ONLY` | read-only | n/a | nothing dispatched |
| 2 | PLAN with live sources | enumerate+classify | counts, `PLAN_ONLY` | read-only | n/a | no egress |
| 3 | EXECUTE flag OFF | gate | `SKIPPED_DISABLED`, 0 dispatch | no egress | re-run when enabled | never delete-all |
| 3b | EXECUTE lock OFF (default) | gate first | `SKIPPED_EXECUTE_NOT_ALLOWED`, 0 dispatch, no probe | no accidental corpus egress | set lock deliberately | not a Boundary-B approval |
| 6b | enqueue fails mid-sweep | fail-stop | `DISPATCH_PARTIAL`, cursor=last good | failed source re-attempted, never skipped | resume from cursor | never COMPLETE on failure |
| 4 | EXECUTE config missing | gate | `SKIPPED_NOT_CONFIGURED`, 0 dispatch | no partial dispatch | re-run when configured | preflight-before-dispatch |
| 5 | sidecar down before dispatch | health preflight | `PREFLIGHT_FAILED`, 0 dispatch | no partial egress | re-run when healthy | zero partial dispatch |
| 6 | sidecar fails after dispatch | already enqueued | `enqueued` counted; 03A handles | 03A retry/permanent | 03A re-drives | enqueue ≠ indexed |
| 7 | worker down | enqueue only | jobs queued (`status=new`) | boot re-scans `new` | worker start drains | dispatch ≠ completion |
| 8 | worker crashes mid 03A job | n/a to 03E | 03A job stuck `running` | 03C/03D/rebuild re-drive | operator re-run | 03E claims only enqueue |
| 9 | rebuild crashes mid-page | partial dispatch | no COMPLETE claimed | idempotent | re-run / `next_cursor` | duplicate-safe |
| 10 | duplicate rebuild | re-enqueue | both dispatch | 03A convergent | harmless | idempotent |
| 11 | duplicate index jobs | re-enqueue | 03A idempotent | delete-then-insert | — | convergent |
| 12 | source updated after enumeration | enqueue source_id | 03A indexes CURRENT | no stale text | — | current-state at exec |
| 13 | source deleted after enumeration | enqueue source_id / `vanished` | 03A `skipped_absent` | no resurrection | — | no stale insert |
| 14 | source emptied after enumeration | enqueue source_id | 03A `skipped_no_content` | old doc stays until 03C/03D | 03D/03C cleanup | not counted indexed |
| 15 | source created during rebuild | maybe outside sweep | not in this sweep | fine | normal 03A ingestion indexes it | not a global snapshot |
| 16 | >max_sources corpus | cap + look-ahead | `DISPATCH_INCOMPLETE` + cursor | fair | continue with cursor | never false-complete |
| 17 | invalid continuation cursor | validate | `INVALID_CURSOR`, 0 dispatch | fail closed | fix cursor | no enumeration |
| 18 | numeric RecordID | keyset | distinct | lossless | — | numeric≠string-numeric |
| 19 | string-numeric RecordID | keyset | distinct | lossless | — | preserved |
| 20 | escaped RecordID | keyset | `record_id_for` | lossless | — | no double-escape |
| 21 | empty source | classify | `empty`, not dispatched (Option A) | not a rebuild | 03D/03C | desired ABSENT |
| 22 | existing pending tombstone | (03E ignores deletion) | untouched | no interference | 03C drains | orthogonal |
| 23 | foreign remote docs | not examined | untouched | never deleted | 03D reports | outside 03E |
| 24 | >200 remote docs | not examined | irrelevant to dispatch | — | — | 03E doesn't prove absence |
| 25 | provider unavailable | after dispatch | 03A transient/permanent | 03A owns | 03A retry | enqueue ≠ indexed |
| 26 | acknowledgment loss | after dispatch | 03A/queue owns | — | re-run safe | idempotent |
| 27 | operator reruns rebuild | re-enqueue | harmless | convergent | — | duplicate-safe |
| 28 | 03D running concurrently | independent | both safe | disjoint writes (03E no arm) | — | no shared mutation |
| 29 | 03C running concurrently | independent | both safe | 03E doesn't touch tombstones | — | orthogonal |
| 30 | no content_hash | inherent | `REBUILD_DISPATCH_COMPLETE` ≠ verified | honest terms | — | never claims verified |

## 24. Live test evidence

- **Live SurrealDB v2.6.5 (this session, PASSED):** `test_live_plan_enumerates_synthetic_sources`
  (PLAN counts synthetic sources, zero dispatch); `test_live_keyset_continuation_covers_all_without_skip_or_repeat`
  (small cap forces continuation; following `next_cursor` covers every synthetic source
  exactly once — real RecordID keyset, strict boundary).
- **Live LightRAG v1.5.6 synthetic EXECUTE end-to-end (PASSED):**
  `test_live_execute_end_to_end_through_worker` — the REAL operator path against the pinned
  sidecar (`ghcr.io/hkuds/lightrag:v1.5.6`, mock synthetic provider) with a REAL
  `surreal-commands` worker: creates a synthetic Source (version A), runs `graphrag_rebuild`
  EXECUTE (dedicated lock ON, flag ON, base_url set, real `GET /health` preflight), which
  enqueues a **source_id-only** `graphrag_index_source` command (verified from the real
  `command` row — no `full_text`), then UPDATES the Source to version B before the worker
  runs; a real worker reloads the CURRENT (B) Source and indexes it, and the document becomes
  **present** in the real sidecar under `doc-md5(source_id)`. The result claims only
  `REBUILD_DISPATCH_COMPLETE` (never REMOTE_CONTENT_CONVERGENCE_VERIFIED). Isolation: a
  read-only PLAN over a single-source keyset window confirms the ONLY candidate is our
  synthetic fixture before EXECUTE — the shared dev DB's real source is never dispatched.
- **Live EXECUTE lock (PASSED):** `test_live_execute_lock_off_blocks_before_egress` — with
  `OPEN_NOTEBOOK_GRAPHRAG_REBUILD_EXECUTE_ENABLED` unset, a real EXECUTE stops at the lock
  (`SKIPPED_EXECUTE_NOT_ALLOWED`): no health probe, no enumeration, no dispatch.
- **Live preflight failure (PASSED):** `test_live_execute_preflight_failure_zero_dispatch` —
  a real client pointed at a dead port fails `health()` → `PREFLIGHT_FAILED`, zero dispatch
  (the zero-partial-dispatch invariant, with a real client).
- Environment note: the `surreal-commands` worker CLI prints emoji and crashes on a Windows
  cp1252 console; the live test runs it with `PYTHONUTF8=1` and kills the whole process tree
  on teardown (Windows `terminate()` leaves an orphaned `python.exe` child otherwise). Both
  are test-harness concerns, unrelated to 03E behavior.

## 25. Exact files changed

- `open_notebook/integrations/graphrag/rebuild.py` — **new**: `RebuildSummary`, `rebuild()`,
  keyset `_sweep` with look-ahead cap resolution, `_canonical_state`, `_fetch_ids`,
  `_enqueue_index`, completion constants.
- `open_notebook/integrations/graphrag/config.py` — **+** `GraphRAGRebuildConfig`
  (incl. `execute_enabled`) + `load_rebuild_config` + clamped bounds constants + the
  `OPEN_NOTEBOOK_GRAPHRAG_REBUILD_EXECUTE_ENABLED` default-OFF lock.
- `commands/graphrag_commands.py` — **+** `GraphRAGRebuildInput` / `GraphRAGRebuildOutput` /
  `graphrag_rebuild` command (`max_attempts=1`); module docstring header updated.
- `commands/__init__.py` — **+** register/export `graphrag_rebuild_command`.
- `tests/test_graphrag_03e_rebuild.py` — **new**: 31 property-oriented tests (mock + 2 live-DB).
- `tests/test_graphrag_isolation.py` / `tests/test_graphrag_deletion.py` — updated the two
  pre-03E scope-creep guards to reflect 03E is now approved+registered, **keeping** the
  standalone-`graphrag_delete_source` guard.
- Docs: this file; `CURRENT_PHASE.md` updated.

## 26. Migration-26 decision

**No migration 26.** REBUILD is orchestration over canonical state + the existing command
system; the continuation cursor is a stateless operator-passed value, so no persistent
rebuild-run schema is needed. Migrations 24/25 remain frozen; count stays 50. Guarded by
`test_no_migration_26_and_count_is_50` and the existing isolation migration-count test.

## 27. Known limitations

1. **No remote content verification** (no `content_hash` on v1.5.6) — 03E guarantees
   dispatch, not remote content equality (§22).
2. **Enqueue ≠ indexing** — a crash-prone queue means `REBUILD_DISPATCH_COMPLETE` is a
   dispatch guarantee, not a completion/convergence guarantee (§11/§12).
3. **Not a global snapshot** — sources created behind an already-passed cursor are outside
   the sweep (normal 03A ingestion indexes them anyway).
4. **Operator-invocation trust is architectural** — the config cannot distinguish operator
   from automatic invocation; the safeguard is that nothing in the app auto-invokes the
   command (§7).
5. **Live-LightRAG content equality not asserted cryptographically** — the end-to-end test
   verifies document PRESENCE and (best-effort, via `content_summary`) that CURRENT (B)
   content was submitted; there is still no `content_hash` to prove remote equality (§22/§24).
6. **Test-file mypy** — the new test module carries the same fake-to-typed-function mypy
   pattern as the 03C/03D test modules (production code is mypy-clean).

## 28. Operational usage

    # Plan (read-only): how many current non-empty sources would be re-dispatched?
    submit_command open_notebook graphrag_rebuild {"mode": "plan"}

    # Execute (operator-triggered, synthetic-only until Boundary B approved):
    #   REQUIRES the dedicated lock set in the worker's environment FIRST:
    #     OPEN_NOTEBOOK_GRAPHRAG_REBUILD_EXECUTE_ENABLED=true
    #   (plus OPEN_NOTEBOOK_GRAPHRAG_ENABLED=true + base_url). Without the lock,
    #   EXECUTE returns SKIPPED_EXECUTE_NOT_ALLOWED and dispatches nothing.
    submit_command open_notebook graphrag_rebuild {"mode": "execute"}
    # If continuation_required: re-submit with the returned next_cursor:
    submit_command open_notebook graphrag_rebuild {"mode": "execute", "cursor": "<next_cursor>"}

**Recommended sequence (documented, NOT auto-chained):** run `graphrag_reconcile` (AUDIT) →
`graphrag_rebuild` (PLAN) to size the work → `graphrag_rebuild` (EXECUTE) over synthetic
non-empty sources → `graphrag_reconcile` (AUDIT) again. Empty-source cleanup: run
`graphrag_reconcile` (REPAIR) so 03B/03C drain should-be-absent docs.

## 29. Acceptance criteria

- [x] PLAN is strictly read-only (no health/HTTP/enqueue/arm/mutation/content).
- [x] EXECUTE requires explicit `mode="execute"`; default is PLAN.
- [x] EXECUTE is gated by a dedicated default-OFF lock (checked first) so enabling GraphRAG
      for ingestion never unlocks a corpus-wide rebuild; lock OFF = no probe, zero dispatch.
- [x] EXECUTE gates flag + config + content-free health BEFORE any dispatch; failed
      gate/preflight = zero partial dispatch.
- [x] A per-row failure (invalid id / state-read error / enqueue failure) fail-stops the
      sweep and never advances the resumable cursor past an un-handled source (never a
      false COMPLETE, never a skipped source).
- [x] Dispatch payload is `source_id` ONLY (no `full_text`); worker reloads CURRENT source.
- [x] Empty sources reported, never armed, never dispatched, never counted rebuilt (Decision A).
- [x] Keyset traversal + RecordID cursor; numeric/string-numeric distinct; invalid cursor
      fails closed; exact cap boundary (N==cap vs N==cap+1) verified.
- [x] Honest completion terminology; never claims remote content verified.
- [x] No migration 26; migrations 24/25 unchanged; count 50.
- [x] No global purge, no foreign-doc deletion, no automatic/scheduled trigger.
- [x] 03E tests pass (43, incl. 2 live SurrealDB + 3 live LightRAG v1.5.6 through a real
      worker); 03A–03D + full GraphRAG regression pass (373); full backend at baseline
      (1023 pass / 9 skip / 5 pre-existing platform failures); ruff clean; production mypy clean.
- [x] Live LightRAG synthetic EXECUTE acceptance gate PASSED (real worker; source_id-only
      dispatch; CURRENT-source reload; real document presence; dispatch-only completion).
- [x] Karpathy diff CLEAN.
- [x] Codex A (orchestration/fairness), Codex B (security/egress), Codex C
      (lifecycle/semantics) run; all HIGH findings **resolved** (execute lock; cursor
      fail-stop skip-safety) with regression tests; re-verified green.
- [x] User sign-off — **2026-08-30**.

## 30. What remains for later GraphRAG phases

- **Remote content verification** requires an upstream LightRAG capability (a `content_hash`
  or an exact by-id/keyset lookup); until then PRESENT_UNVERIFIED cannot be proven fresh and
  REBUILD is dispatch-only.
- **Boundary-B approval** for real internal data is a separate governance decision; only then
  can EXECUTE run over production sources.
- 03E does not solve the >200-doc authoritative-absence ceiling (that stays 03C/03D
  fail-closed) and is not a later GraphRAG phase (04+). No phase beyond 03E is started here.
