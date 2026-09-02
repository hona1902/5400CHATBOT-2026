# GraphRAG-08 — Frozen-State Handoff (for a fresh Claude Code session)

**Written:** 2026-09-02 · **Purpose:** resume safely from the current frozen checkpoint without
reconstructing history. Documentation only — the next session must **not** auto-execute anything.

> ⚠️ **CORRECTION vs the handoff request.** The request was drafted from the **08B** vantage
> (HEAD `1d74fa4`, next action "Re-Authorization #3"). The repo has since advanced: **two more
> full-run attempts (#3 and #4) already happened — both were launcher defects, not benchmark or
> provider failures — and GraphRAG-08C (preflight hardening) was built and committed.** The true
> current tip is **08C**. This handoff reflects reality. The next full attempt is effectively
> **#5** (not #3), and it is still **NOT authorized**.

> ⚠️ **UPDATE (GraphRAG-08D history reconciliation).** Since this handoff was written,
> **full-run attempt #5 executed** (run_id `13e59a3edbb8`, auth label `REAUTHORIZATION_5`) — the
> **first post-launcher-fix** run. Its corrected launcher worked end-to-end and it **reached
> genuine GraphRAG indexing** (all 75 Sources attempted), then failed on logical Source **S001**
> at the **TRACK** surface (`DocStatus.FAILED`, **NON_RETRYABLE**,
> `TRACK_TEXT_PRESENT_NO_ALLOWLIST_MATCH`) — a real GraphRAG **extraction** failure, categorically
> different from the #4 launcher/import defect (#4 never reached graph indexing). No DEV/HOLDOUT
> ran; no value decision. **GraphRAG-08D** (deterministic repo-root resolver + fail-fast
> import-path preflight) was then built to permanently guard the #4 defect; it lives in the
> working tree (uncommitted at time of writing). The next full attempt is therefore **#6**, still
> **NOT authorized**. See §6 for the full attempt table (now #1–#5).

---

## 0. Current git checkpoint (VERIFIED)

```
branch                : feature/graphrag-lifecycle
HEAD                  : d1d9d65355cb6f2046df11b41809a3d2fcf3725e  ("GraphRAG-08C: harden full-run preflight observability")
tag at HEAD           : graphrag-08c-preflight-approved  (peels to d1d9d65)
working tree          : CLEAN
customization remote  : backup  (github.com/hona1902/5400CHATBOT-2026)  — branch+tags aligned to HEAD
upstream              : origin = lfnovo/open-notebook  — UNTOUCHED (never push customization here)
no force push anywhere.
```

## 1. Approved checkpoint chain (all real SHAs/tags, oldest → newest)

```
GraphRAG-07 contract          337456de41a28e03927538533e50141ad50c96cf   graphrag-07-contract-approved
GraphRAG-08 design            8d8f854e958cbcfc5b8254683b804cf3f7c1b459   graphrag-08-design-approved
GraphRAG-08 fixture/harness   356e8ae17c83b212ab565b93f62187352685cd66   graphrag-08-harness-approved
GraphRAG-08A Option-A isol.   2ccec550cec24e1c259610c21a172dfb67a46422   graphrag-08a-isolation-approved
GraphRAG-08 micro-precheck    450c31e1767469ca6e484b5e3c63489f0e515a1f   graphrag-08-micro-precheck-pass
GraphRAG-08B observability    1d74fa4d7c481d4341840bcdc9a0c2c9bc6ad9b0   graphrag-08b-observability-approved
GraphRAG-08C preflight        d1d9d65355cb6f2046df11b41809a3d2fcf3725e   graphrag-08c-preflight-approved   ← HEAD
```

## 2. Frozen fixture (IMMUTABLE)

```
version : graphrag_08_eval_v1     (tests/fixtures/graphrag_08_eval_v1/{corpus.json,queries.json,freeze.json})
counts  : 75 Sources · 60 queries · 30 DEV · 30 HOLDOUT · 12 negatives · 10 query classes
hash    : a58a68535c345e18f0263904f818e4e2068a164056408665d8bb9233eceb143d   (verify_integrity → MATCH, confirmed)
```

No change to corpus/queries/GT/DEV-HOLDOUT split/rationale/negative labels — nor to provider,
model, metric, concurrency, retry policy, or classifier — is permitted because of benchmark
results. A methodological change requires a **new fixture version (v2)**, never editing v1.
The 10 query classes: `direct_lexical, semantic_paraphrase, two_hop, three_hop_cross_source,
entity_collision, relationship_collision, distractor_term_collision, negative_unanswerable,
partial_evidence, broad_entity_name_collision`.

## 3. Architecture / three systems

- **V = VECTOR_BASELINE** — ranked Open Notebook `vector_search` over Sources (chunk→Source dedup,
  first/best rank kept). Genuinely ranked; keeps Hit@K / Recall@K / Precision@K /
  FULL_SET_RECOVERED@K / MRR at K∈{1,3,5,10}.
- **GQ = CURRENT_LIGHTRAG_QUERY_EVIDENCE** — current `GraphRAGService.query_strict()` / `client.query()`
  references; the generated final answer is **discarded** for evaluation (only canonical references
  scored). Unordered set.
- **GD = STRUCTURED_QUERY_DATA_EVIDENCE** — **evaluation-only** `/query/data` seam
  (`eval/gd_seam.py`, own httpx from `GraphRAGConfig`; NOT a production adapter). `only_need_context=True`;
  `GD_FINAL_ANSWER_CALLS = 0` invariant. Raw vendor schema contained in eval; unordered set.

Graph evidence (GQ, GD) is an **UNORDERED SET**: `ORDER_SEMANTICS = NONE`, no fake graph rank/score.
Graph metrics = set_precision/set_recall/set_f1/candidate_count/**candidate_fraction (denominator = 75
for the full run)**/false_positive_count/full+partial_source_set_recovered — never MRR/nDCG/Hit@K-by-order.
Frozen: `QUERY_DATA_EXPOSES_VALID_RANK=NO · QUERY_DATA_EXPOSES_VALID_SCORE=NO ·
RRF_CANDIDATE_INTERFACE_READY=NO · GRAPH_CANDIDATE_IMPLEMENTATION_READY=NO`.

**Broad-candidate interpretation (frozen):** high recall + low precision + high candidate_fraction =
BROAD COVERAGE, not retrieval quality. HOLDOUT (30) is authoritative for any value conclusion; DEV
(30) is secondary. Multi-hop/collision value must be read **beside** candidate_fraction.

## 4. Option-A isolation (GraphRAG-08A — proven)

`eval/isolation08.py`. Every DB-touching eval run uses a **dedicated temporary Surreal
namespace/database** (`graphrag_eval_<run_id>` / `graphrag_08_<run_id>`) via a **process-local
`SURREAL_NAMESPACE`/`SURREAL_DATABASE` env override** (the DB layer opens a fresh connection per call
and reads ns/db from env — no singleton). Proven: canonical schema bootstrap to **version 25** via
`AsyncMigrationManager` (no migration added; **50 files / 25 forward**), normal-DB **hard guard**
(rejects identity AND shared-namespace overlap; Option-B normal-DB path blocked in
`runner08.create_and_index` via `require_active_isolation()`), isolated vector storage (vector data
lives in Surreal `source_embedding`), owned + idempotent cleanup (`REMOVE NAMESPACE`), env
restoration verified. The runner indexes **in-process** (`embed_source_command` awaited directly;
`service.index_source` = direct HTTP), so the surreal-commands **worker is not on the precheck/full
execution path**.

## 5. Micro-precheck (PASS — the only PASS)

```
run_id  : c531cf98a092   status: PASS   (8 Sources, 6 DEV queries, 0 HOLDOUT)
sources : S002 S007 S021 S030 S039 S040 S052 S055
queries : GR08Q01 GR08Q13 GR08Q19 GR08Q25 GR08Q32 GR08Q43
```
V/GQ/GD all executed; **GQ and GD returned identical canonical Source sets on all 6** (empirically
confirms GraphRAG-06 parity); GD final-answer invariant held; embedding dim **1536**; cleanup +
restoration PASS; normal DB unchanged; LightRAG per-ID cleanup PASS; sidecar stopped;
`GRAPHRAG_ENABLED` restored false. **`candidate_fraction` median = 1.0 on the 8-Source micro corpus —
this is SMALL-CORPUS BROAD-CANDIDATE BEHAVIOR, NOT value evidence.** `VALUE_EVIDENCE_READY` stayed NO.

First-launch audit (not retry-to-pass): the micro-precheck's first launch aborted **before** Source
indexing/query due to an eval-only launcher `sys.path` issue (only 1 dimension-probe embedding call;
cleanup completed); the launcher was fixed and the actual micro-precheck ran **once**. Classify as
**EXECUTION_HARNESS_FIX**.

## 6. Full 75×60 benchmark attempts — ALL FAILED before graph indexing (NO value evidence)

Preserve each distinctly; none is a value result; do not aggregate; do not invent unseen error text.

| Attempt | run_id | outcome | where it died | GraphRAG value? |
|---|---|---|---|---|
| #1 | `8c9d83c77d92` | FAILED_BEFORE_QUERY | Graph indexing — Source **S002** hit LightRAG `DocStatus.FAILED` (pre-hardening runner aborted on first failure) | NO |
| #2 | `03bc96689656` | FAILED_BEFORE_QUERY | Graph indexing — Source **S001** `DocStatus.FAILED`, classified **NON_RETRYABLE** by the frozen classifier (error text present, no transient-marker match) | NO |
| #3 | (see note) | FAILED_AT_PREFLIGHT / anomaly | normal-DB baseline read returned **null** → root cause: standalone `uv run python <driver>` did **not** load `.env`, so SurrealDB `signin` failed `-32000 authentication` and was swallowed to null. Hardened by 08C. | NO |
| #4 | `2f783c62d6dc` (REAUTHORIZATION_4) | FAILED_BEFORE_QUERY | passed baseline gate + sidecar health + isolation + schema v25 + **created & vector-embedded all 75 Sources**, then died in `_vector_embed_all` at `from commands.embedding_commands import …` → `ModuleNotFoundError: No module named 'commands'` — **before any graph indexing** (`per_source_attempts={}`, `graphrag_indexed_count=0`). Hardened by 08D. | NO |
| #5 | `13e59a3edbb8` (REAUTHORIZATION_5) | FAILED_BEFORE_QUERY | **first post-launcher-fix run**: launcher OK end-to-end, normal-DB baseline concrete + unchanged, sidecar healthy, isolation PASS, schema v25, dim 1536, all 75 Sources created + vector-embedded, and **graph indexing REACHED (all 75 attempted)**; logical Source **S001** (`source:gr08ef5d8978700`) failed at the **TRACK** surface, `DocStatus.FAILED`, classified **NON_RETRYABLE** (`TRACK_TEXT_PRESENT_NO_ALLOWLIST_MATCH`; error-text present, length bucket 129–256, no transient/non-transient allowlist match, `retry_allowed=false`, attempt_count=1, max_attempts_observed=1); 75/75 complete-corpus gate aborted before any query (`graphrag_indexed_count=0`). Raw track error read only via the frozen 08B classifier and discarded — never persisted. | NO |

**Every attempt died at or before QUERY — GraphRAG retrieval/extraction VALUE has NEVER been
measured (0 DEV, 0 HOLDOUT).** Attempts #1/#2 were provider/indexing failures under load;
attempt **#5** was a genuine GraphRAG **extraction** failure on S001 at the TRACK surface — the
**first run to REACH graph indexing** (attempts #3/#4 never reached it: #3 an unloaded-`.env`
preflight anomaly, #4 the launcher/import defect). #3/#4 were **eval-only launcher defects**, not
benchmark/provider failures. In every case: **0 DEV, 0 HOLDOUT executed, VALUE_DECISION_MADE=NO**,
and cleanup + temp-namespace drop + sidecar stop + fixture-unchanged all held (normal DB unchanged
where observable).

## 7. GraphRAG-08B — retry + failure-classification observability (COMPLETE, committed)

Bounded index retry (`eval/index_retry08.py` + `runner08`):
- `MAX_INDEX_ATTEMPTS_PER_SOURCE = 2` (1 initial + 1 bounded retry), **single per-Source counter
  spanning submit + track surfaces**; reindex = delete-then-insert (same Source/provider/model/config).
- Retry ONLY a clearly transient failure (submit: `GraphRAGUnavailableError`/`ServerError`/
  `ConflictError`; track: sidecar `error_msg` matches the frozen transient allowlist). **Unknown /
  ambiguous / absent / unreadable → fail closed (no retry).**
- Hard **75/75 complete-corpus gate** (`_assert_complete_corpus`): any Source not PROCESSED after its
  allowed retry aborts the run **before any query**. Partial 74/75 can never enter DEV/HOLDOUT.

Content-safe diagnostics (GraphRAG-08B, `FailureDiagnostic`): surface/attempt/classification/reason-code/
coarse transient+non-transient class enums/error-text-present/**length bucket (never exact length)**/
http-status-class/exception **type name only** — the raw provider/LightRAG error text is read
transiently, classified, and **discarded**; never persisted to artifact/manifest/report/logs/exception;
no raw-text hashing. Failure telemetry **survives a pre-ANALYZE abort** (written atomically before
cleanup). Retry DECISION/allowlist/max-attempts/concurrency/provider/model/fixture **UNCHANGED**
(frozen-semantics regression + decision-twin consistency + an 18,917-string class/decision fuzz = **0
divergence**). Independently reviewed: no unresolved HIGH/MEDIUM (raw-leak CLEAN).

## 8. GraphRAG-08C — full-run preflight observability (COMPLETE, committed = current HEAD)

New modules `eval/preflight08.py` + `eval/sidecar_diag08.py`; `run_full_benchmark` now runs a
**fail-closed preflight**: `fixture verify → normal-DB baseline (identity + concrete Source count +
model baseline) → STOP before sidecar if unreadable → sidecar → isolation`. `compare_normal_db`
returns `NORMAL_DB_UNCHANGED = YES` **only from two concrete observations, never `null == null`**
(`NOT_PROVEN` blocks operational sign-off). `sidecar_diag08` emits content-safe SIDECAR_START reason
codes (container running/exit/health/restart via a targeted `docker inspect` template + TCP port +
one coarse health status — never logs/env/body). `authorization_label` is now a **metadata-only
param** (replaces the hard-coded `REAUTHORIZATION_*` string). This directly fixes the attempt-#3 null
anomaly (gate fail-closes if `.env` is unloaded). New `PrecheckState` fields: `authorization_label,
normal_db_before/after, normal_db_unchanged, preflight_blocked, isolation_entered, sidecar_diagnostic,
failure_stage, failure_reason_code`.

> Note: `CURRENT_PHASE.md`'s last GraphRAG row still says "Full 75×60 value benchmark (2 attempts)…"
> — it predates attempts #3/#4 and 08C and was **not** updated. The authoritative 08C/#3/#4 record is
> the git commit `d1d9d65`, the `.planning/` dirs, and the auto-memory `graphrag-08c-preflight-gate`.

## 9. Eval-only module map (`open_notebook/integrations/graphrag/eval/`)

`dataset08` (loader/validator/freeze) · `metrics08` (set metrics, breadth, complementarity) ·
`normalize`/`metrics`/`runner`/`report`/`dataset` (frozen GraphRAG-04 v04 — untouched) ·
`gd_seam` (GD /query/data) · `isolation08` (Option-A) · `runner08` (V/GQ/GD, bounded retry, 75/75
gate, telemetry) · `report08` (content-free artifact, by-split/by-class) · `index_retry08` (retry
decision + 08B diagnostics) · `precheck08` (micro-precheck + `run_full_benchmark` orchestrators) ·
`preflight08` + `sidecar_diag08` (08C). **Nothing in `open_notebook/`(prod)/`api/`/`commands/` imports
any of these** (dependency direction is eval → production only).

## 10. Test baseline (offline, at 08B/08C)

77 focused GraphRAG-08 tests pass; **GraphRAG regression (flag off) 488 pass / 8 skip / 0 fail**;
ruff clean; targeted mypy clean; no unresolved HIGH/MEDIUM independent-review findings. (08C added its
own preflight/sidecar-diag tests on top; re-run `uv run pytest tests/ -k graphrag` with
`OPEN_NOTEBOOK_GRAPHRAG_ENABLED=false` to reconfirm.)

## 11. Runtime posture at handoff (VERIFIED)

```
LightRAG sidecar        : STOPPED  (port 9621 CLOSED)
OPEN_NOTEBOOK_GRAPHRAG_ENABLED : false
provider traffic        : none active
normal DB               : not mutated
migrations              : 50 files total / schema version 25
.artifacts/             : gitignored (runtime artifacts never staged)
working tree            : clean
```
Infra facts for a future run: pinned image `ghcr.io/hkuds/lightrag:v1.5.6` present locally; SurrealDB
up on :8000; sidecar compose at `deploy/graphrag-poc/docker-compose.graphrag.yml`; providers via
OpenRouter (`.env`), embedding `openai/text-embedding-3-small` (1536), LLM `openai/gpt-4o-mini`.

## 12. Frozen decision flags

```
GRAPH_RAG_08_FIXTURE_READY                    = YES
GRAPH_RAG_08_EVAL_HARNESS_READY               = YES
SURREAL_OPTION_A_ISOLATION_IMPLEMENTED        = YES
GRAPH_RAG_08_MICRO_PRECHECK_PASS              = YES
GRAPH_RAG_08_INDEX_RETRY_HARDENING            = COMPLETE
GRAPH_RAG_08B_FAILURE_OBSERVABILITY           = COMPLETE
GRAPH_RAG_08C_PREFLIGHT_OBSERVABILITY         = COMPLETE
FULL_RUN_REAUTHORIZATION_READY (harness)      = YES
FULL_EXECUTION_AUTHORIZED                      = NO
VALUE_EVIDENCE_READY                           = NO
GRAPH_RETRIEVAL_VALUE_EVIDENCED                = NOT_RUN
STRUCTURED_QUERY_DATA_RUNTIME_VALUE_EVIDENCED  = NOT_RUN
STRUCTURED_EVIDENCE_VALUE_EVIDENCED            = NOT_RUN
STRUCTURED_EVIDENCE_IMPLEMENTATION_READY       = NO
QUERY_DATA_EXPOSES_VALID_RANK                  = NO
QUERY_DATA_EXPOSES_VALID_SCORE                 = NO
RRF_CANDIDATE_INTERFACE_READY                  = NO
GRAPH_CANDIDATE_IMPLEMENTATION_READY           = NO
RETRY_POLICY_CHANGED / ALLOWLIST_CHANGED / CONCURRENCY_CHANGED = NO
PARTIAL_CORPUS_ALLOWED = NO · FULL_INDEX_REQUIRED = 75/75 · MAX_INDEX_ATTEMPTS_PER_SOURCE = 2
```

## 13. ⚠️ Two launcher requirements for ANY future full run (learned from #3 and #4)

A future full-run driver **must** satisfy BOTH, or it fails before measuring anything:
1. **Load `.env` into the process env** (e.g. `dotenv.load_dotenv(".env")`). A bare `uv run python
   <driver>` does not auto-load it → SurrealDB signin fails → the 08C preflight correctly fail-closes
   (attempt #3 root cause).
2. **Put the repo root on `sys.path`** (`sys.path.insert(0, os.getcwd())`, or `PYTHONPATH=.`, or place
   the driver file in the repo root). `uv run python <script>` puts the *script's* dir on `sys.path[0]`,
   not the cwd, so a scratchpad driver can't import the top-level `commands` package (only
   `open_notebook.*` is an installed/editable package) → `ModuleNotFoundError: No module named 'commands'`
   in `_vector_embed_all` (attempt #4 root cause).

## 14. Next action (the next session must do this, then STOP)

The next operator decision is a **GraphRAG-08 Full Frozen Value Benchmark — explicit re-authorization
(effectively attempt #6; attempts #1–#5 have all run and failed at/before query — see §6)**. Because
attempt #5 already reached genuine graph indexing and then failed on S001 (TRACK / NON_RETRYABLE),
the operative open question before a #6 is the S001 extraction failure itself (the authorized,
ephemeral, non-persisting S001 forensic re-index — **AUTHORIZED, execution not yet evidenced**), not
the launcher path (permanently guarded by 08D). A fresh Claude session should:
1. verify HEAD/tag/branch = `d1d9d65` / `graphrag-08c-preflight-approved` / `feature/graphrag-lifecycle`
   (08D lives in the working tree, uncommitted, until its checkpoint);
2. verify working tree (08D eval module + tests + docs pending checkpoint); 3. verify fixture hash = `a58a6853…143d`;
4. read `CURRENT_PHASE.md`; 5. read the GraphRAG-08 design doc
   (`GRAPHRAG_08_LARGER_CORPUS_VALUE_EVALUATION_DESIGN.md`); 6. read 08A/08B/08D docs +
   auto-memory `graphrag-08c-preflight-gate`; 7. read the micro-precheck + attempt #1–#5 records;
8. verify sidecar down + `GRAPHRAG_ENABLED=false` + migrations 50/v25; 9. confirm the two launcher
   requirements (§13) are now enforced by the 08D preflight in whatever driver will run; 10. summarize
   readiness; **11. STOP and wait for explicit operator authorization.**

**Do NOT infer authorization from `FULL_RUN_REAUTHORIZATION_READY=YES`.** Only an explicit new
operator instruction may set `FULL_EXECUTION_AUTHORIZED=YES`.

## 15. If a full attempt is LATER authorized (do NOT run now)

Preserve: 75/75 Source indexing required before any query; 30 DEV + 30 HOLDOUT; frozen fixture/hash/GT;
frozen models (embedding `openai/text-embedding-3-small` dim 1536, LLM `openai/gpt-4o-mini`, pinned
LightRAG v1.5.6); frozen concurrency; frozen `MAX_INDEX_ATTEMPTS=2` + retry allowlist/classifier; no
partial-corpus analysis; no tuning after seeing DEV or HOLDOUT. Any failure before 75/75 indexing ⇒
`FAILED_BEFORE_QUERY`, no value conclusion. Only after 75/75 successfully indexes may V/GQ/GD query
execution begin, then compute the frozen metrics and the HOLDOUT-primary value decision
(`STRUCTURED_EVIDENCE_VALUE_EVIDENCED = YES/NO/INCONCLUSIVE`).

## 16. Hard boundaries (always)

No fixture edit; no retry-policy/allowlist/classifier/concurrency/provider/model change to make a run
pass; no partial-corpus evaluation; no production Structured Evidence Adapter / `query_data` /
`query_evidence` / RRF / ranked graph candidates; no production import of eval code; push only to
`backup`, never `origin`, never force. The failed runs are execution history, not value evidence, and
the unseen attempt-#2 error token must not be invented.

---

GRAPH_RAG_08B_FROZEN_STATE_HANDOFF_READY
