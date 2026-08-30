# Findings — GraphRAG-03C

## Verified repo state (Phase 0)
- Branch `feature/graphrag-lifecycle`, HEAD `66560d53521f56fa01626b2a4e000d9624091bdd`, clean.
- Tag `graphrag-03b-approved` on HEAD.
- Highest migration = 24. Next = 25.

## 03B tombstone model (to verify against source)
Spec-stated (must confirm in migration 24 + source):
- Table `graphrag_deletion`, fields: source_id, requested_at, status, arm_id.
- DB event `graphrag_source_delete` fires on canonical Source DELETE.
- Each delete creates/re-arms one deterministic tombstone: status=pending, requested_at set,
  fresh arm_id = rand::uuid(). arm_id is CAS fence. requested_at informational only.

## VERIFIED tombstone model (migration 24 + deletion.py)
- Table `graphrag_deletion` SCHEMAFULL. Fields: source_id (record<source>), requested_at
  (datetime default time::now()), status (string default "pending"), arm_id (uuid).
- Row id is DETERMINISTIC: event UPSERTs `type::thing("graphrag_deletion", $before.id)`.
  → one row per source; re-delete re-arms in place (UPSERT). Row id = graphrag_deletion:<raw id>.
- Event `graphrag_source_delete ON TABLE source WHEN $after == NONE` → sets source_id, requested_at,
  status="pending", arm_id=rand::uuid(). Synchronous/in-transaction (SurrealDB v2). Fires on ALL
  delete paths incl raw `DELETE source`. arm_id fresh (UUIDv7) per re-arm; CAS fence.
- `deletion.list_pending_deletions()`: `SELECT * FROM graphrag_deletion WHERE status='pending'
  ORDER BY requested_at ASC` → List[DeletionTombstone(source_id, status, arm_id, requested_at)].
  NOT bounded — batching is explicitly deferred to 03C.
- DeletionTombstone.arm_id carried as canonical string; CAS binds as `<uuid>$arm_id`.

## VERIFIED LightRAG client contract (client.py, v1.5.6)
- `compute_doc_id(source_id) = "doc-" + md5(source_id)`. Deterministic, content-independent.
- `delete_document(doc_id)`: DELETE /documents/delete_document body `{"doc_ids":[doc_id]}`.
  Background delete; returns immediately. Status map:
    deletion_started → DeleteState.GONE ; not_found → GONE (defensive) ; busy → BUSY ;
    not_allowed → REFUSED ; unknown → raises GraphRAGProtocolError.
  ⚠️ CRITICAL: `deletion_started` is ACCEPTANCE, mapped to GONE. Per 03C §9 + 03B §17.1.1,
  GONE from delete_document does NOT prove remote absence → CANNOT resolve a tombstone on it.
- No absence-confirmation primitive exists on the client today. track_status is keyed by
  indexing track_id, not doc_id. → 03C likely needs a new client method to confirm doc absence
  (agent A verifying exact LightRAG endpoint; 03B §17.1.1 suggests "poll document status /
  paginated listing until compute_doc_id absent").
- HTTP error taxonomy: Unavailable(timeout/transport), ServerError(5xx), ConfigurationError
  (401/403/404/405), ConflictError(409), RequestError(4xx incl 422), ProtocolError(bad JSON/shape).
  API key never logged. Response bodies never echoed.

## VERIFIED 03A live-source lifecycle (lifecycle.index_source)
- `index_source(service, source_id, canonical_text, confirm_current)` → IndexOutcome(result, detail, track_id).
- IndexResult: INDEXED / SUPERSEDED / TRANSIENT / PERMANENT.
- Steps: confirm_current (pre) → delete-then-insert with GONE gate → confirm_current (pre-egress) → insert.
- Reusable AS-IS for 03C live-NON-empty branch. BUT its success (INDEXED) proves current insert
  accepted (IndexAck.accepted), NOT queryable-complete. For tombstone resolution the 03B contract
  (§17.1.1) says resolve only on CONFIRMED CURRENT INSERT — INDEXED (ack.accepted) is the confirmation
  bar for the live-non-empty branch per §17.1 (resolve after confirmed current insert), distinct from
  the absence-confirmation needed for delete/empty branches.

## VERIFIED command layer (commands/graphrag_commands.py)
- `graphrag_index_source_command` (03A) outcomes: skipped_disabled, skipped_absent,
  skipped_no_content, indexed, superseded, permanent_failure.
- `skipped_no_content` = live source with empty full_text → does NOT delete old doc (KNOWN 03A
  limitation). 03C live-empty branch MUST issue explicit confirmed delete (§17.1.2).
- Flag gate: `service.config.enabled` (raw flag) OFF → skipped_disabled (terminal). Flag ON but
  base_url unset → RuntimeError (retry). Existence check: repo_query("SELECT * FROM $id",{id:record_id})
  distinguishes true-absent (empty rows) from transient DB error (raises → retry).
- Retry config GRAPHRAG_INDEX_RETRY_CONFIG: max_attempts 5, exponential_jitter 1..60,
  stop_on [ValueError, ConfigurationError]. Raise to re-drive; return to terminate.

## 03B doc = the 03C CONTRACT (GRAPHRAG_03B_DURABLE_DELETE.md §17)
Mandatory 03C preconditions (all pre-approved architecture):
- §17.1 live-source convergence branch table (absent→confirmed delete; live-empty→confirmed delete;
  live-nonempty→converge-to-current via 03A index_source). Generation-agnostic; NO generation-aware
  doc_id, NO repo-wide id immutability.
- §17.1.1 CONFIRMED ABSENCE (not deletion_started) to resolve delete/empty tombstones. CAS resolve
  `WHERE id=$id AND status='pending' AND arm_id=<uuid>$observed`. Zero rows → re-armed/resolved →
  re-drive, do NOT treat as done. arm_id (not requested_at) is the fence.
- §17.1.2 live-empty = converge-to-absent (explicit delete). skipped_no_content/superseded ≠ convergence.
- 03C may add retry fields (attempts, last_error, next_retry_at, resolved_at) in its OWN migration
  (25) ONLY if proven necessary. May add batching. Decides flip-status vs delete-row for resolution.
- Drain must converge with flag OFF (deletion independent of enable flag).
- Deferred/available primitives: list_pending_deletions (enumerate), compute_doc_id (derive),
  record_id_for (load current), delete_document (idempotent absent=success/busy=retry).

## Scheduler / drain-trigger mechanisms — FORENSIC (LIFECYCLE_FORENSIC.md) + (agent B confirming)
DECISIVE forensic conclusions (GRAPHRAG_LIFECYCLE_FORENSIC.md):
- §8 (L183-189): surreal_commands sets status="running" BEFORE execution (core/service.py:225);
  NO lease, NO heartbeat. Worker boot scan only re-picks status="new" (worker.py:110). A job that
  reaches "running" then worker crashes = STUCK FOREVER. Exhausted retries → "failed", never retried.
  commands router exposes submit/status/list/cancel — NO re-drive of stuck/failed.
  ⇒ Queue durable for SUBMISSION + WORKER-OUTAGE, NOT for crash-during-execution / exhausted-retry.
- L187/377 (L10): "There is NO scheduler dependency in the project (no APScheduler/Celery/cron in
  pyproject.toml)." "Scheduler-driven auto-reconcile now — REJECTED: No scheduler dependency exists;
  adding one is out of scope — operator/API trigger instead."
- Recommended drain trigger: durable tombstone (work SoT) + operator/API-triggered drain + boot scan.
- L357: RECONCILE diffs via **GET /documents/paginated** vs SurrealDB keyed by doc_id → THIS is the
  absence-confirmation endpoint (agent A to confirm exact shape). Paginate ≤200/page (L280).
- L342: "If retention SLA is eventually/bounded-by-reconcile-cadence ⇒ NO MIGRATION strictly required."
  → strong signal migration 25 likely NOT needed (retry = stay-pending + future drain).
- Row 21 (L219): two-worker drain → idempotent by doc_id + claim/lease OR dedup. 03B RESOLVED this
  with arm_id CAS + idempotent remote ops, explicitly NOT requiring a lease.

### AGENT B CONFIRMED (drain trigger)
- FastAPI lifespan `api/main.py:184-217` runs migrations at boot ONLY (`_run_database_migrations`
  after `_wait_for_database`). One-shot injection point. Shutdown = log only. NO background task/loop.
- Worker boot drain `surreal_commands/core/worker.py:109-139`: on start re-reads
  `SELECT * FROM command WHERE status='new' ORDER BY created ASC`, dispatches via create_task; then
  LIVE query push for new 'new' rows. NOT stuck-running, NOT tombstone-aware.
- surreal_commands: NO delay/schedule/cron/recurring. submit_command inserts status='new' immediate.
  Self/cross re-submit IS an existing pattern (embedding_commands.py:459,588 call submit_command
  inside a command). → self-re-drive possible but immediate (must self-pace).
- NO APScheduler/Celery/cron/threading.Timer/asyncio loop anywhere in app code. Confirmed.
- graphrag_deletion IS the known "durable pending rows, no drainer" gap deferred to 03C.
- BOTTOM LINE: crash-safe re-drivable drain assemblable WITHOUT a new scheduler via
  [lifespan startup drain + worker boot-drain/retry + optional self-resubmit]. A true wall-clock
  periodic timer does NOT exist & would need adding (self-pacing loop or new scheduler = needs approval).

### DRAIN-TRIGGER = SPEC §16 HARD-STOP TERRITORY
No existing mechanism PERIODICALLY revisits pending tombstones. Available hooks:
- FastAPI lifespan (runs migrations at startup — agent C confirming exact trigger) → a STARTUP drain
  could be kicked here (reuses existing hook, NOT a new scheduler).
- Worker boot scan re-picks "new" commands only (not tombstone-aware, not stuck-running).
Candidate architectures to PRESENT for approval (do NOT silently build a daemon):
  A. graphrag_drain_deletions surreal command (worker, bounded batch) — the drain unit of work.
  B. API-startup best-effort submit of one drain (independent discovery incl. raw-DELETE tombstones
     + re-drive of crash-stuck prior work). Startup-only ⇒ latency bound = restart cadence.
  C. (needs explicit approval) lightweight asyncio periodic loop in lifespan for beyond-startup
     re-drive — this is the "new scheduler" piece §16 says to STOP+report before building.
  D. best-effort immediate wake-up on Python Source.delete path (optimization only; raw DELETE has
     no Python so cannot rely on it — §27).

## Enqueue/submit pattern (graphs/source.py:244-281, commands/__init__.py)
- `_maybe_enqueue_graphrag_index(source)` — best-effort, flag-gated, NEVER raises. Lazy imports
  config + `submit_command("open_notebook","graphrag_index_source",{"source_id":str(source.id)})`.
- Worker starts via `surreal-commands-worker --import-modules commands`; commands/__init__ imports
  each command fn. New drain command must be registered there.

## Command queue crash/re-drive + repo patterns — AGENT C COMPLETE
- Retry: RetryConfig (retry.py:41-86). exponential_jitter = wait_exponential + wait_random(0,wait_time).
  reraise=True → ORIGINAL exception re-raised on exhaustion. House convention: TRANSIENT → raise
  (retry re-drives); PERMANENT → return success=False (no re-drive). stop_on=[ValueError,ConfigurationError].
- CRASH: status="running" set BEFORE body (service.py:225), no lease/heartbeat. Boot scan = status='new'
  ONLY (worker.py:110-112). Stuck-running crash = STUCK FOREVER; exhausted retry = 'failed' never retried.
  → drain MUST own re-drive via durable table + CAS + external trigger. Documented forensic §8/§185-189.
- repo_query(q, vars) → list[dict]; stringifies RecordIDs; RuntimeError on in-band error/txn-conflict
  (re-raised, DEBUG, NO auto-retry). No pooling (conn per call). RecordID bound via ensure_record_id in vars.
- repo_update only emits `UPDATE $target MERGE $data` (no extra WHERE) → CAS must be raw repo_query.
- PAGINATION house style: `... ORDER BY x LIMIT $limit START $offset` (SurrealDB START, not OFFSET).
  Example api/routers/sources.py:306-317. Cap ≤200/page.
- CAS IDIOM (ALREADY TESTED LIVE in 03B, test_graphrag_deletion.py:415-428):
  `UPDATE graphrag_deletion SET status='resolved' WHERE source_id=$s AND status='pending'
   AND arm_id=<uuid>$a RETURN BEFORE;` → len(result)==0 = zero rows (re-armed/resolved) → re-drive.
  Params {"s": ensure_record_id(sid), "a": old_arm}. uuid cast `<uuid>$a`. RETURN BEFORE→[] = zero-rows.
  Works identically for DELETE ... WHERE ... RETURN BEFORE.
- Migration manager: hard-coded migrations 1-24 (async_migrate.py:96-225). Awaited in lifespan BEFORE
  yield, fail-fast (main.py:203-209). run_migration_up runs pending. Register 25 here IF added.

## DESIGN CONCLUSIONS (Phase 1 → Phase 2)
1. Migration 25: NOT needed (recommend). Retry = tombstone stays pending + future drain re-attempts
   (spec §20 preferred; forensic §342). In-command retry (max_attempts 5 backoff) handles per-invocation
   transient; durable tombstone + external re-drive handles crash. Sanitized error class returned in
   command OUTPUT, not persisted. No attempt_count/next_retry_at/last_error fields.
2. Resolution model: DELETE-row via CAS `DELETE graphrag_deletion WHERE id=$id AND status='pending'
   AND arm_id=<uuid>$a RETURN BEFORE` (recommend; no accumulation, privacy-min, pending-enum unchanged,
   re-arm re-creates deterministic row). Alt = status='resolved'. Both sanctioned by 03B §17.1.1.
3. Absence confirmation: NEW client method paging POST /documents/paginated → doc_id present? Boundary A.
   Bounded pages ≤200. Delete → poll-until-absent within BOUNDED window; else stay pending (re-drive).
4. Live-non-empty convergence: reuse 03A lifecycle.index_source (delete-then-insert, double confirm).
   INDEXED → resolve. ⚠️ BOUNDARY-B SUBTLETY: re-index egresses canonical text→sidecar→provider. Under
   synthetic-only policy OK when flag ON. When flag OFF (drain must still run for deletion), re-indexing
   would be indexing-while-disabled + Boundary B → RECOMMEND: flag-OFF live-non-empty converges to ABSENT
   (delete slot, removes stale old-generation content, no provider egress); flag-ON → converge-to-current
   per §17.1. NEEDS APPROVAL (§19 "STOP and report rather than silently broadening egress").
### D1 RESOLVED BY USER (explicit §16 approval, 2026-08-29): SMALL 03C-owned periodic wake-up
User REJECTS startup-only (raw DELETE after startup would sit pending until unrelated restart).
Approved required architecture:
- (1) startup kick, (2) best-effort immediate wake-up on Source.delete (optimization, NOT correctness),
  (3) PERIODIC durable-state discovery while alive (scan graphrag_deletion pending), (4) narrowly
  GraphRAG-specific (NO APScheduler/Celery/cron, NO generic scheduler, NO 2nd daemon, existing
  lifecycle hook w/ clean startup+shutdown, duplicate wake-ups safe), (5) PREFER worker lifecycle IF
  it can cleanly host a cancellable periodic task ELSE FastAPI lifespan (least-coupled), (6) periodic
  task only WAKES/ENQUEUES bounded work (no worker slot forever, no tight loop), (7) manual trigger
  optional-only, (8) raw-DELETE-after-startup discovered within bounded interval w/o restart, (9)
  multi-replica/duplicate safe via idempotent remote + arm_id CAS.
HOST DECISION: WORKER CANNOT cleanly host it — surreal_commands worker.py:94-174 listen_for_commands
  is the library's own indefinite LIVE loop; only injection = module import (no running loop at import,
  no shutdown callback into our code). → USE FastAPI LIFESPAN (api/main.py:184-217, we own it, clean
  startup before yield / shutdown after yield).
DESIGN:
  - Loop body lives in GraphRAG integration pkg (open_notebook/integrations/graphrag/drain wake-up
    helper), lazy-imported from lifespan (mirrors _maybe_enqueue_graphrag_index removability).
  - Lifespan startup (after migrations, before yield): app.state.graphrag_drain_task =
    asyncio.create_task(wakeup_loop(interval)). First tick runs immediately = startup kick (#1).
  - wakeup_loop: while True: try: enqueue_if_pending() except CancelledError: raise except Exception:
    log(sanitized); await asyncio.sleep(interval)  # sleep = cancel point; NOT tight loop (#6).
  - enqueue_if_pending: cheap `SELECT id FROM graphrag_deletion WHERE status='pending' LIMIT 1`; if
    any AND no drain command already new/running → submit_command("open_notebook",
    "graphrag_drain_deletions",{limit}). Enqueue-only (#6). Guard avoids pile-up; dup-safe (#9).
  - Shutdown (after yield): task.cancel(); try: await task except CancelledError: pass. Deterministic.
  - Interval env OPEN_NOTEBOOK_GRAPHRAG_DRAIN_INTERVAL_SECONDS (default 300, floor ~30). Bounded.
  - Raw DELETE after startup (#8): DB event writes tombstone → next tick (≤interval) finds it →
    enqueue → worker drains. No restart/operator needed.
  - NOT a substantial scheduler: one create_task/cancel pair + one lazy helper. Do NOT STOP again.

### Drain command (unchanged): new surreal command
   `graphrag_drain_deletions` (worker, bounded batch, owns transient retry) + API-lifespan startup
   best-effort submit (independent discovery incl raw-DELETE + crash re-drive) + optional best-effort
   wake-up submit on Python Source.delete (optimization). NEEDS APPROVAL on scope (self-continuation?
   manual API trigger?).

## LightRAG absence-confirmation endpoint — AGENT A COMPLETE — THE CRUX (RESOLVED)
- NO local/vendored LightRAG source. Consumed as pinned Docker image
  `ghcr.io/hkuds/lightrag:v1.5.6` via `deploy/graphrag-poc/docker-compose.graphrag.yml`
  (127.0.0.1:9621:9621, needs LIGHTRAG_API_KEY). Run live:
  `docker compose -f deploy/graphrag-poc/docker-compose.graphrag.yml up -d`
  then OPEN_NOTEBOOK_GRAPHRAG_BASE_URL=http://localhost:9621 + API_KEY.
- ABSENCE CONFIRMATION = `POST /documents/paginated` (document_routes.py:6355).
  Request DocumentsRequest{status_filters, page, page_size(10-200), sort_field, sort_direction}
  → PaginatedDocsResponse{documents:[DocStatusResponse{id,status,updated_at,file_path,error_msg}],
  pagination, status_counts}. Confirm absence by scanning pages and asserting
  compute_doc_id(source_id) NOT in documents[].id (equivalently file_path != source_id).
  ⚠️ NO by-id filter → confirming ONE doc absent requires scanning pages (O(N docs)). Bounded
  ≤200/page. DocStatusResponse has NO content_hash (can't read back content identity, only presence).
  → 03C must ADD a client method (e.g. document_present(doc_id) / paginated scan). NOT yet wired.
- Delete endpoint returns NO pollable track_id / completion handle — only deletion_started/busy/
  not_allowed. Completion is confirmed ONLY via paginated absence, per 03B §17.1.1.
- Other endpoints: GET /documents/status_counts (totals), GET /documents (deprecated, ≤1000),
  DELETE /documents (clears WHOLE sidecar — REBUILD only, NOT per-doc).
- NO live-sidecar test harness. All GraphRAG HTTP mocked via httpx.MockTransport
  (base_url "http://graphrag-sidecar.invalid:9621"). live_db fixture (test_graphrag_deletion.py:208)
  targets SurrealDB only (probes repo_query("RETURN true;"), skips if unreachable). NO test exercises
  /delete_document or /paginated. → LIVE LightRAG test (§34) requires running the Docker sidecar
  myself; availability in this env UNKNOWN → verify in Phase 4, potential sign-off limitation.

## Live-test environment (Phase 4 gates)
- Docker AVAILABLE: v28.5.1, server responding. Compose `deploy/graphrag-poc/docker-compose.graphrag.yml`
  present → live LightRAG sidecar (ghcr.io/hkuds/lightrag:v1.5.6) is runnable (image pull permitting).
- ⚠️ Sidecar needs LLM_BINDING + EMBEDDING_BINDING (Boundary B) to fully PROCESS an indexed doc.
  Bindings intentionally NOT defaulted. For the DELETE-branch live test I mainly need: doc appears in
  /documents/paginated → DELETE → confirm absent. Whether a doc registers in doc_status without a
  working embedding binding is UNKNOWN → test empirically in Phase 4, document honestly. Synthetic
  data only (§34). Needs GRAPHRAG_POC_API_KEY set.
- SurrealDB live gate (§35): REACHABLE on ws://127.0.0.1:8000/rpc (PID listening). Creds root/root,
  ns/db open_notebook. Live-DB CAS/enumeration/raw-DELETE tests feasible. LightRAG (9621) NOT running
  yet → start via compose in Phase 4.

## LIVE LightRAG v1.5.6 forensic (Phase 2b) — pinned image ghcr.io/hkuds/lightrag:v1.5.6
Runtime: core_version 1.5.6, api_version 0328. Booted with DEFAULT ollama binding (empty host) — server
up, processing will fail (no ollama). auth_mode disabled unless key set (we set synthetic key).
Health capabilities: scheduling_pages, typed_source_resolution, strict_active_count,
source_conflict_listing, source_conflict_repair, strict_point_reads. (These are internal storage
capabilities, NOT HTTP by-id endpoints — see path list.)

### Document endpoints (authoritative, from /openapi.json)
GET,DELETE /documents ; DELETE /documents/delete_document ; POST /documents/paginated ;
GET /documents/status_counts ; GET /documents/track_status/{track_id} ; POST /documents/text|texts ;
GET /documents/pipeline_status ; GET /documents/source_conflicts (+repair) ; GET supported_file_types ;
POST /documents/scan, /reprocess_failed, /clear_cache, /cancel_pipeline, /recovery/force_reset.
⇒ NO exact document-status-by-DOC_ID HTTP endpoint. track_status is by INDEXING track_id, not doc_id.
  No id/file_path server-side FILTER on paginated. So absence must come from LIST endpoints.

### POST /documents/paginated contract (DocumentsRequest → PaginatedDocsResponse)
Request: status_filter(DocStatus|null), status_filters([DocStatus]|null), page(def 1),
  page_size(def 50), sort_field ENUM['created_at','updated_at','id','file_path'] (def updated_at),
  sort_direction ['asc','desc'] (def desc).  ← sort_field='id' gives DETERMINISTIC total order.
Response PaginatedDocsResponse: documents[DocStatusResponse], pagination PaginationInfo, status_counts{}.
PaginationInfo: page,page_size,total_count,total_pages,has_next,has_prev (ALL present).
DocStatusResponse: id, content_summary, content_length, status, created_at, updated_at, track_id?,
  chunks_count?, error_msg?, metadata?, file_path.  (id = doc-md5; file_path = our source_id.)

### DELETE /documents/delete_document → DeleteDocByIdResponse
status ENUM EXACTLY ['deletion_started','busy','not_allowed'] (NO not_found), message, doc_id.
→ confirms client mapping; not_found branch is defensive/unreachable via this route on v1.5.6.

### GET /documents → DocsStatusesResponse{statuses: {status: [DocStatusResponse]}} (single response;
  reviewed-source says deprecated, ≤1000 cap — verify live).

## LIVE ABSENCE-PROBE FORENSIC RESULTS (pinned v1.5.6, empirical)
Setup: pinned sidecar + LOCAL SYNTHETIC mock OpenAI-compat provider (embed+llm, dim 1024) on host:11500
(no real provider, synthetic data only). All observations LIVE.

ITEM A — live mechanisms:
- NO exact by-doc-id HTTP probe. Enumeration endpoints only:
  * POST /documents/paginated: page(≥1), page_size [10..200] (1→422, 201→422 VERIFIED), sort_field
    ['created_at','updated_at','id','file_path'], sort_direction ['asc','desc']; returns documents[],
    pagination{page,page_size,total_count,total_pages,has_next,has_prev}, status_counts{}.
  * GET /documents → DocsStatusesResponse{statuses:{status:[DocStatusResponse]}} = SINGLE grouped
    snapshot in ONE response. VERIFIED N(flattened)==status_counts.all (completeness cross-check works).
  * GET /documents/status_counts → per-status + 'all'.
  * DELETE /documents/delete_document → deletion_started|busy|not_allowed.
- doc registers under id=doc-md5(source_id), file_path=source_id (populated; paginated does NOT break).
ITEM B/C — acceptance≠absence + race:
- LIVE PROVEN both delete outcomes:
  * broken embedding (ollama unreachable): DELETE→deletion_started, then background delete FAILED
    ("Deletion completed: 0 successful, 1 failed"; NanoVectorDBStorage[chunks] flush needs embedding),
    doc REMAINED present 30s+. ⇒ deletion_started is NOT proof; delete CAN fail leaving content.
  * working mock embedding: DELETE→deletion_started, doc ABSENT within 3s (total_count 1→0, GET
    /documents {} empty). ⇒ positive path confirmed.
- OFFSET-PAGINATION RACE: a SINGLE paginated request = consistent server snapshot. MULTI-request
  offset traversal IS unsafe (concurrent delete of a lower-sorted doc shifts target across an
  already-read boundary → false absent). ⇒ absence provable ONLY via a COMPLETE SINGLE-REQUEST
  enumeration; multi-page traversal must NOT yield CONFIRMED.
ITEM D — text-ingest + paginated: VERIFIED works, file_path non-null, sort-by-id deterministic
  (ids==sorted(ids)), pagination fields correct.
ITEM E — delete-of-already-absent LIVE: returns deletion_started when idle (safe no-op); can return
  busy when pipeline busy. Never a hard error. Enum has NO not_found (confirms client mapping).

### CHOSEN ABSENCE CONTRACT (item E/F) — FOUND / ABSENT_CONFIRMED / UNKNOWN
Probe = ONE complete single-request enumeration:
  A) POST /documents/paginated page=1 page_size=200 sort_field=id sort_direction=asc:
     - target id in documents            → FOUND
     - target absent AND total_pages<=1 AND total_count==len(documents)  → ABSENT_CONFIRMED
     - else (total_pages>1 / count!=len / any inconsistency)             → UNKNOWN
  (optional raise ceiling via GET /documents when N==status_counts.all ≤ cap — same single-snapshot proof)
ANY non-200 / JSON-parse fail / missing pagination fields / timeout / empty-only / request error → UNKNOWN.
ONLY ABSENT_CONFIRMED permits arm_id CAS resolve. FOUND/UNKNOWN → tombstone stays pending → re-drive.
WHY false-absence cannot occur: ABSENT_CONFIRMED requires the WHOLE set enumerated in ONE server
response, proven complete by an internal count invariant (total_pages<=1 ∧ total_count==len). A single
server enumeration cannot omit an existing target due to concurrent OTHER-doc mutation (the omission
race needs cross-request offset shifts). total_pages>1 (multi-request) ALWAYS → UNKNOWN, structurally
excluding the race. Recreation after snapshot = new generation → arm_id CAS re-arm + canonical re-check.
BOUNDED LIMITATION: single-page ceiling = 200 docs (or GET/documents cap ~1000). Beyond → UNKNOWN
(stay pending; delete still issued idempotently so content IS removed; tombstone bookkeeping resolved
later by a complete snapshot when corpus fits, or by 03D RECONCILE). Documented, not overclaimed.

### FAIR TRAVERSAL w/o migration 25 (item F / fairness condition)
- Drain command paginates pending in BOUNDED pages: `ORDER BY requested_at ASC LIMIT $page START $off`,
  page loop up to MAX_ROWS_PER_RUN (e.g. 500) — covers a large prefix each run, not just batch 1.
- FINITE per-row handling: per-row convergence wrapped in try/except; a row failure → row stays pending
  + CONTINUE to next row. The command NEVER raises for per-row transient failures (so in-command retry
  can't abort the batch / re-run START 0 on the same early failures). Command returns a summary
  {confirmed_deleted, converged, still_pending, errors_by_class}.
- Resolved rows are DELETEd (CAS) → front slots freed → window advances across ticks → no starvation.
- Genuinely-unconfirmable rows are only sidecar-wide outages (affect ALL equally → all stay pending →
  retried together next tick; bounded by sidecar recovery, no per-row hammer). Malformed rows impossible
  (schema record<source>). Per-row permanent failures essentially absent by design.
- Sidecar-down: each row fails FAST (timeout) → stays pending → next row → return; next tick after
  interval retries. No hot loop (interval-gated wake-up + enqueue-only + guard against duplicate drain).
- CONCLUSION: fair, bounded, no starvation, no hot loop → NO migration 25 required. ✓ fairness condition met.

## FAIRNESS RE-ANALYSIS (user challenge — LIMIT/OFFSET rejected)
User is CORRECT:
- OFFSET traversal over a MUTATING set is unsafe: page1 LIMIT200 START0 resolves+DELETEs K rows →
  set shrinks → page2 START200 skips K rows that shifted into [200-K,200). CONFIRMED reasoning-valid.
- Front-start bounded traversal (always take oldest LIMIT cap): if ≥cap FRONT rows fail persistently
  (row-specific), later rows are NEVER reached across ticks → permanent starvation. CONFIRMED.
- Keyset cursor (requested_at,id) fixes OFFSET-skip (#3) BUT re-starting from front each tick still
  starves later rows if ≥cap persistent front-failures (#4). So keyset alone is NOT globally fair.

CONCLUSION: global fairness across an arbitrarily large pending set, with bounded per-tick work AND
possible ≥cap persistent front-failures, CANNOT be guaranteed with the 03B schema (requested_at,
status, arm_id) and NO persistent scheduling state. → migration 25 IS necessary. Minimal form:

### MINIMAL MIGRATION 25 (proposed): single field `next_attempt_at: datetime`
Algorithm (no OFFSET, no cursor row):
  drain pass: SELECT ... WHERE status='pending' AND next_attempt_at <= time::now()
              ORDER BY next_attempt_at ASC, id ASC LIMIT $cap
  per row (finite handling, never abort):
    - converge (absent/empty→delete+confirm-absent; live-nonempty flag-on→03A index / flag-off→delete)
    - ABSENT_CONFIRMED / INDEXED → arm-fenced DELETE-CAS resolve (WHERE id AND status='pending' AND arm_id)
    - UNKNOWN / TRANSIENT / not-confirmed → arm-fenced DEFER: UPDATE SET next_attempt_at=time::now()+DELAY
      WHERE id AND status='pending' AND arm_id  (0 rows if re-armed → skip, row stays due)
Why fair (answers #1-7):
  #1 bounded: LIMIT $cap; a pass processes ≤cap DUE rows + ≤cap HTTP ops.
  #3 no OFFSET skip: each pass takes DUE rows; processed rows leave the DUE set (deleted or deferred to
     future) → next pass sees the NEXT due rows. No position-based paging.
  #4 no starvation: a failing row is DEFERRED to now+DELAY → leaves the due set → later rows become the
     head of the due set and are processed. Every row cycles through "due" over time.
  #6 re-arm: event sets next_attempt_at=time::now() (+ fresh arm_id) on re-arm → immediately DUE →
     discoverable next pass; our stale defer/resolve is arm-fenced → cannot hide a re-armed row.
  #7 no hot loop: deferred rows not retried until DELAY elapses; wake-up is interval-gated + dedup guard.
  #8 multi-replica: two drains may pick same due row → idempotent remote + arm-fenced resolve/defer →
     one wins CAS, other 0-rows (safe).
  #2 deterministic: ORDER BY next_attempt_at, id → stable, testable.
Minimal: ONE field. NO attempt_count, NO error strings (§30), NO cursor row. Fixed DELAY (or bounded
backoff) — fixed is sufficient for fairness+no-hot-loop. 25_down restores EXACT migration-24 event
(without next_attempt_at). Migration 24 FROZEN.
NEEDS: live SurrealQL v2.6.5 verification (datetime compare, time::now()+duration, DEFINE FIELD add,
arm-fenced UPDATE 0/1, ORDER BY) + USER APPROVAL of migration 25 (decision gate §21).

## MIGRATION 25 — LIVE-VERIFIED (v2.6.5) minimal fair-traversal state
Verified constructs (scratch tables, cleaned):
- DEFINE FIELD IF NOT EXISTS next_attempt_at ... DEFAULT time::now() → OK on existing SCHEMAFULL table.
- ORDER BY next_attempt_at requires the field in the projection → use SELECT * (v2.6.5 idiom rule).
- DUE filter `WHERE status='pending' AND next_attempt_at <= time::now()` → works; future rows excluded.
- arm-fenced DEFER `UPDATE ... SET next_attempt_at=time::now()+Ns WHERE status='pending' AND arm_id=<uuid>$a
  RETURN BEFORE` → 1 row (current arm), 0 rows (stale arm). Deferred row LEAVES the due set.
- arm-fenced DELETE-CAS `DELETE ... WHERE status='pending' AND arm_id=<uuid>$a RETURN BEFORE` → 1/0.
- RE-ARM SAFETY: re-arm (SET next_attempt_at=time::now(), arm_id=rand::uuid()) makes row DUE again; a
  stale-arm defer AFTER re-arm affects 0 rows → CANNOT hide a re-armed row. VERIFIED.
- DEFINE EVENT OVERWRITE → OK (25 overwrites event to add next_attempt_at; 25_down overwrites back to
  exact 24 body). REMOVE FIELD IF EXISTS → OK (25_down).
- ⚠️ New DEFINE FIELD DEFAULT does NOT materialize existing rows on SELECT (present=False) though DUE
  predicate still matched via default. → migration 25 MUST BACKFILL: `UPDATE graphrag_deletion SET
  next_attempt_at=time::now() WHERE next_attempt_at=NONE;` (verified: catches unmaterialized rows).

### FINAL migration 25 (minimal — ONE field):
25.surrealql:
  DEFINE FIELD IF NOT EXISTS next_attempt_at ON TABLE graphrag_deletion TYPE datetime DEFAULT time::now();
  UPDATE graphrag_deletion SET next_attempt_at = time::now() WHERE next_attempt_at = NONE;   -- backfill
  DEFINE EVENT OVERWRITE graphrag_source_delete ON TABLE source WHEN $after == NONE THEN {
      UPSERT type::thing("graphrag_deletion", $before.id) SET source_id=$before.id,
        requested_at=time::now(), status="pending", arm_id=rand::uuid(), next_attempt_at=time::now(); };
25_down.surrealql:
  DEFINE EVENT OVERWRITE graphrag_source_delete ON TABLE source WHEN $after == NONE THEN {   -- exact 24 body
      UPSERT type::thing("graphrag_deletion", $before.id) SET source_id=$before.id,
        requested_at=time::now(), status="pending", arm_id=rand::uuid(); };
  REMOVE FIELD IF EXISTS next_attempt_at ON TABLE graphrag_deletion;
NO attempt_count, NO error strings, NO cursor row. Fixed bounded defer DELAY (configurable, capped).
Deletion never abandoned (confidentiality) → no max-attempts. Migration 24 FROZEN (untouched file).

## Open decisions
- Migration 25 needed? (retry/backoff persistent state)
- Drain trigger mechanism (HARD STOP risk)
- Tombstone resolution model (DELETE row vs status=resolved)
