# GraphRAG-09 — Provenance Integration Specification

## 1. Status and authority

`STATUS = ENGINEERING_SPECIFICATION — 09A FORMAL SPECIFICATION FREEZE (docs-only)`

This document is the authoritative definition of the GraphRAG-09 **engineering** scope. It
was produced by converting the operator-approved GraphRAG-09 formal design into an in-repository
specification. It is **not** a claim of implementation, **not** production approval, and **not** a
live-execution authorization.

Precedence follows `AGRIBANK.md §1`: current source code and verified tests outrank this spec.
Where this spec and a prior document disagree about GraphRAG-09's intended scope, this spec wins
once it has passed independent review and been captured; until then it is a reviewed-pending draft.

Four distinct axes are tracked separately throughout and must never be conflated:

- **Engineering design** — what may be built.
- **Scientific evidence** — what PN02D did and did not establish.
- **Production approval** — a separate operator decision (09I).
- **Provider / data-egress authorization** — Boundary B, a separate operator decision.

Governance decisions frozen by the operator on 2026-10-02 are recorded in `DECISIONS.md` (AGR-006).

## 2. Background

Until this spec, "GraphRAG-09" existed only as a governance boundary token — undefined in the
repository and consistently marked NOT_AUTHORIZED / NOT_JUSTIFIED (see
`GRAPHRAG_08_VALUE_EVIDENCE_ARCHITECTURE_REVIEW.md`). The GraphRAG track to date:

- GraphRAG-02 — isolated LightRAG PoC (complete).
- GraphRAG-03 — indexing lifecycle and durable deletion, slices 03A–03E (complete).
- GraphRAG-04 — synthetic retrieval evaluation (complete).
- GraphRAG-05/06/07 — ranked-candidate / structured-evidence / adapter-contract forensics (design gates).
- GraphRAG-08 — larger-corpus value evaluation; CLOSED with operator Decision B
  `KEEP_GD_AS_PROVENANCE_ONLY` (graph retrieval value not evidenced; `/query/data` runtime value evidenced).
- GraphRAG-PN01 / PN02 — per-notebook isolation and QA evaluation.
- GraphRAG-PN02D — durable reproducibility closeout
  (`PN02D_DURABLE_REPRODUCIBILITY_CLOSEOUT_ACCEPTED`), which established durable QA-value provenance
  evidence and is an input evidence milestone only.

GraphRAG-09 is defined here as the bounded productization of that provenance evidence — nothing
more.

## 3. Formal objective

> Build and validate a bounded, feature-flagged, fail-safe GraphRAG provenance integration for Open
> Notebook that corroborates and enriches evidence ONLY after GraphRAG references are validated
> against live, notebook-scoped Open Notebook records, while preserving the existing vector/source
> retrieval and answer-generation path as the canonical baseline.

Explicit exclusions from the objective: graph-primary retrieval; vector replacement; RRF production
fusion; HybridRetriever production integration; LightRAG-authored final answers; graph-authored Ask
responses; unrestricted production GraphRAG.

`GRAPHRAG09_SCOPE_CLASS = PROVENANCE_ONLY`

## 4. Scientific evidence boundary

PN02D (durable closeout `GRAPHRAG_PN02D_RUN4_DURABLE_CLOSEOUT.md`) established, as an independent
durable result:

- `QA_VALUE = YES` for graph-detail provenance/corroboration (positive arms `QA-GD`, `QA-V+GD`).
- Retrieval `INCONCLUSIVE (R4)`.
- Multihop `NO (M2)`.
- Isolation evidenced; leakage `0`; violations `[]`.

Therefore:

- `PN02D_PROVENANCE_EVIDENCE_GATE = SATISFIED` — sufficient to enter engineering design for the
  bounded provenance-only scope only.
- `GRAPH_RETRIEVAL_PRODUCTION_EVIDENCE_GATE = NOT_SATISFIED`.
- `LIGHTRAG_ASK_EVIDENCE_GATE = NOT_SATISFIED`.

This spec does **not** assert GraphRAG retrieval superiority, universal retrieval gain, multihop
superiority, or production readiness. `SCIENTIFIC_SCOPE_OVERCLAIM = NO`.

Any future graph-primary retrieval, RRF production fusion, or LightRAG Ask answer generation requires
a **new scientific evidence gate** and a **separate operator governance decision**.

## 5. Scope

In scope for GraphRAG-09:

1. Formal specification (this document) — 09A.
2. Production boundary hardening of the existing product-side GraphRAG surface — 09B.
3. Error and observability semantics (fix `unknown_provider_error` masking) — 09C.
4. Production validation of the existing INDEX/REINDEX/DELETE/RECONCILE/REBUILD lifecycle — 09D.
5. A new **citable provenance query seam** (validate graph references against live records) — 09E.
6. Bounded product integration of the validated provenance surface — 09F.
7. Offline integration test consolidation — 09G.
8. Controlled live validation on synthetic/approved data only — 09H.
9. Production-readiness governance decision — 09I.

## 6. Non-goals

`RRF_PRODUCTION_INTEGRATION = NOT_APPROVED`
`HYBRID_RETRIEVER_PRODUCTION_INTEGRATION = NOT_APPROVED`
`LIGHTRAG_ASK_INTEGRATION = NOT_APPROVED`
`LIGHTRAG_ASK_PHASE_DEFERRED = YES`

GraphRAG-09 does not: make graph retrieval a primary or canonical retrieval source; replace or
degrade `vector_search`; deploy RRF/HybridRetriever to production; generate final answers from
LightRAG; inject graph output into production Ask prompts; widen Open Notebook scope from sidecar
state; cross Boundary B; or delete existing research/PoC/eval code merely because it is currently
unapproved for production.

## 7. Canonical retrieval authority

`VECTOR_BASELINE_REMAINS_CANONICAL = YES`

Open Notebook's existing vector/source retrieval remains canonical. The canonical Ask path is
`open_notebook/graphs/ask.py::provide_answer`, which retrieves through the scoped
`vector_search(...)` primitive (`open_notebook/domain/notebook.py`). GraphRAG must not replace,
wrap, reorder, or gate that path within the approved GraphRAG-09 scope. GraphRAG may only supply
bounded provenance/corroboration metadata for sources the canonical path already selected, and only
after the validation in §8.

## 8. Provenance and citation contract

The approved pipeline, from unvalidated graph output to citable provenance:

```
GraphQueryResult.references            (open_notebook/integrations/graphrag/models.py; UNVALIDATED, non-citable)
  -> GraphReference.source_id          (recovered from LightRAG ReferenceItem.file_path)
  -> validate_source_id(...)           (structural canonical RecordID validation; service.py)
  -> resolve live Source record        (SurrealDB authoritative; absent/deleted -> reject)
  -> validate notebook<->source membership via the canonical `reference` relation (AGR-005 §3)
  -> emit typed VALIDATED provenance / corroboration object
```

Only after all steps succeed may a graph reference contribute citable provenance. Invariants:

- `GRAPH_REF_TO_LIVE_RECORD_VALIDATION = MANDATORY`
- `NOTEBOOK_SCOPE_ENFORCED = YES`
- `STALE_GRAPH_RECORD_REJECTED = YES`
- `FOREIGN_NOTEBOOK_RECORD_REJECTED = YES`

GraphRAG must never independently introduce an unvalidated source into answer context, and a graph
reference that does not resolve to a live, in-scope record is dropped (diagnostic-only), never cited.

## 9. Notebook isolation contract

The Open Notebook notebook↔source `reference` relation is the **sole** scope authority (AGR-005 §3).
Sidecar state is never a scope authority. Every validated provenance object is notebook-scoped. A
graph reference resolving to a source outside the active notebook is rejected as foreign. No graph
state may widen canonical Open Notebook scope.

`CROSS_NOTEBOOK_LEAKAGE = 0` (target and gate).

## 10. Security model

- Open Notebook / SurrealDB remains the canonical data authority; sidecar state is derived,
  disposable, non-authoritative.
- Secrets are never logged or echoed; the GraphRAG API key stays content-safe
  (`config.py` reads it, never returns it).
- Provider-secret propagation uses the approved provisioning abstraction; no alternate provider
  clients are instantiated outside it.
- Sidecar network exposure is bounded to loopback/internal scope; the main `docker-compose.yml`
  never hard-depends on the sidecar.
- New product routes require authentication/authorization equivalent to other user-facing features,
  with permission tests.
- No stale graph content may remain citable after deletion; no provider egress under uncertainty.
- Error reporting must not expose raw internal content or credentials (see §12, §19).

## 11. Feature flag contract

- Primary flag `OPEN_NOTEBOOK_GRAPHRAG_ENABLED`, default **OFF**, read at call time
  (`open_notebook/integrations/graphrag/config.py`).
- Flag OFF restores the exact canonical vector-only baseline: no client constructed, no network,
  ingestion byte-for-byte unchanged (`open_notebook/graphs/source.py` enqueue seam is a no-op).
- Secondary locks remain independent and are not unlocked by the primary flag
  (`OPEN_NOTEBOOK_GRAPHRAG_REBUILD_EXECUTE_ENABLED` default OFF; deletion drain / reconcile gate on
  `OPEN_NOTEBOOK_GRAPHRAG_BASE_URL` so deletion converges even when indexing is disabled).
- `PRODUCTION_FLAG_ENABLEMENT_BEFORE_09I = NO`. Enablement before 09I is permitted only in unit
  tests, isolated provider-free integration contexts, and an explicitly authorized controlled 09H
  environment.

## 12. Failure and fallback contract

`GRAPH_FAILURE_DEGRADES_TO_BASELINE = YES`

For the provenance query seam, each of: sidecar unavailable; timeout; malformed response; validation
rejection; graph error — results in provenance omission and continuation of the canonical vector
flow. GraphRAG must never be a hard dependency for Open Notebook boot or for Ask.

Existing lifecycle semantics are preserved: ingestion fail-open (never blocks a durable source save);
deletion fail-closed (never best-effort); no egress under uncertainty (supersede recheck before send).

## 13. Boundary B / provider-egress contract

`REAL_INTERNAL_DATA_PROVIDER_EGRESS = NOT_APPROVED`
`BOUNDARY_B_REAL_INTERNAL_DATA = NOT_APPROVED`

GraphRAG-09 engineering authorization is **not** data-egress authorization. Phases 09A–09G are
provider-free. Phase 09H may use only synthetic data, public data, or separately approved
non-sensitive test data.

If real internal data ever becomes genuinely necessary for validation: **STOP** and produce a
separate `GRAPHRAG_BOUNDARY_B_GOVERNANCE_DECISION` covering exact fields leaving the system,
destination provider, retention, logging, secrets, encryption, redaction, data minimization,
contractual/privacy constraints, rollback, and explicit operator authorization. Boundary B is never
crossed automatically.

## 14. Live-auth contract

- `CURRENT_HEAD_LIVE_AUTHORIZED = NO`.
- Historical YRH2 (`graphrag-pn02db3yrh2-b3-live-auth-successor-approved`) remains `ANCESTOR_STALE`;
  it is not restored.
- `NEW_LIVE_AUTH_SUCCESSOR_CREATED = NO` through 09A–09G.
- A new exact-head live-auth successor may be created only immediately before the first explicitly
  approved 09H provider-backed execution, and only after implementation complete, provider-free
  readiness reconciliation, independent review `D_PASS_CLEAN`, and explicit operator authorization.
- Standing provider authority is not preserved unnecessarily (deliberate fail-closed posture).

## 15. Phase plan — 09A through 09I

### 09A — Formal specification freeze
Docs-only, provider-free. Produce this spec, the AGR-006 decision record, and a CURRENT_PHASE block.
Exit: spec written; AGR-006 recorded; CURRENT_PHASE updated; exactly three doc files dirty;
independent review `D_PASS_CLEAN`. No runtime code.

### 09B — Production boundary hardening
Provider-free. Audit/harden `OPEN_NOTEBOOK_GRAPHRAG_*`, `GraphRAGConfig`/`load_config`, service
gates (`service.py`), client/network/timeout/API-key handling (`client.py`), and PoC/env separation.
Exit: `FLAG_OFF_EQUALS_BASELINE = PROVEN`; `POC_CONFIG_DOES_NOT_LEAK_TO_PRODUCT = PROVEN`. No new
product capability. Independent review required (security-sensitive).

### 09C — Error & observability semantics
Provider-free. Remediate `unknown_provider_error` masking in
`open_notebook/utils/provider_errors.py`. Distinguish at minimum: provider auth/error; configuration
error; sidecar HTTP/version error; local DB/auth error; timeout/network error; feature disabled;
validation error. Preserve content-safe reporting. Independent review required.

### 09D — Lifecycle production validation
Provider-free harness first. Validate the existing INDEX/REINDEX (03A), DELETE drain (03C),
RECONCILE (03D), REBUILD (03E) implementations (`commands/graphrag_commands.py`,
`open_notebook/integrations/graphrag/{lifecycle,drain,reconcile,rebuild,deletion}.py`, migrations
24/25). Reuse; do not rewrite proven logic without evidence. Validate idempotency, retry safety,
stale deletion, fail-closed delete, fail-open ingest, disabled-state convergence, bounded batches.

### 09E — Citable provenance query seam (central new capability)
Provider-free (fakes/fixtures first). Implement the §8 pipeline producing a typed validated
provenance object from `service.query_strict()` output. No graph-primary retrieval, no RRF, no answer
generation. Gates: `GRAPH_OUTPUT_REQUIRES_LIVE_RECORD_VALIDATION = YES`; `NOTEBOOK_SCOPE_ENFORCED =
YES`; `STALE_GRAPH_RECORD_REJECTED = YES`. Independent review required (new product surface).

### 09F — Product integration of provenance-only surface
Integrate the validated provenance object additively. If it touches Ask/Chat, GraphRAG contributes
only validated provenance metadata/evidence; the existing answer-generation path remains
authoritative. Gates: `GRAPH_AUTHORED_FINAL_ANSWER = NO`; `VECTOR_BASELINE_REMAINS_AVAILABLE = YES`;
`FLAG_OFF_RESTORES_BASELINE = YES`. New routes/UI carry authN/authZ and i18n per `frontend/AGENTS.md`.

### 09G — Offline integration test consolidation
Provider-free. Cover config; lifecycle; citation identity; notebook isolation; stale deletion;
flag-OFF baseline; sidecar unavailable; timeout; malformed response; invalid source identity; error
taxonomy; authZ; secret non-disclosure. Baseline: `uv run pytest tests/`, `uv run ruff check .`,
`uv run python -m mypy .`, and `frontend/` lint/test/build if affected. Independent review required
before live validation.

### 09H — Controlled live validation (earliest provider-required phase)
Entry requires ALL of: implementation complete; provider-free suite green; independent review
`D_PASS_CLEAN`; provider-free readiness reconciliation; explicit operator authorization; a NEW
exact-head live-auth successor created immediately before execution; synthetic/public/separately
approved data only. `REAL_INTERNAL_DATA = NO` unless Boundary B is separately approved later.

### 09I — Production-readiness decision (governance)
Engineering completion is not production approval. Review security, authN/authZ, audit, TLS/network,
secrets, provider/data-egress policy, backup/restore, runbook, UAT, rollback, and 09H evidence. Only
explicit operator approval may change `GRAPHRAG_PRODUCTION_INTEGRATION` from `NOT_APPROVED`.

## 16. Engineering acceptance criteria

(Separate from production approval.) At minimum: approved spec; production boundary clean
(`FLAG_OFF_EQUALS_BASELINE = PROVEN`, `POC_CONFIG_DOES_NOT_LEAK_TO_PRODUCT = PROVEN`); error
classification fixed; lifecycle invariants proven; validated provenance contract implemented;
notebook isolation proven; stale graph references rejected; flag OFF exact baseline; provider-free
suite green; independent reviews clean; controlled 09H validation clean on allowed data.

`GRAPHRAG09_ENGINEERING_COMPLETE != PRODUCTION_APPROVED`.

## 17. Production approval criteria

(Kept separate; 09I.) At minimum: explicit Boundary B / data-egress decision if applicable;
authN/authZ; audit; TLS/network; secret management; provider policy; backup/restore; deployment
runbook; UAT; rollback; security review; controlled live-validation evidence; explicit operator
approval.

## 18. Rollback / kill-switch requirements

- `OPEN_NOTEBOOK_GRAPHRAG_ENABLED=off` restores the exact vector-only baseline.
- GraphRAG failure never corrupts canonical source or vector state (ingest fail-open, delete
  fail-closed).
- Delete/reindex consistency is maintained across a flag-off transition (drain gates on base URL and
  converges).
- Bounded timeouts; sidecar unavailable degrades to no provenance, never raises into Ask.
- Provider unavailable produces no partial/garbage index; sidecar startup failure never blocks API
  boot.
- Stale-graph detection via RECONCILE; EXECUTE rebuild stays behind its own default-OFF lock.

## 19. Open prerequisite backlog

The generic `unknown_provider_error` classifier (`open_notebook/utils/provider_errors.py`,
`UNKNOWN_PROVIDER_ERROR` fallback in `classify_provider_error`) can mask non-provider failures
(historically a SurrealDB auth/config failure and a GraphRAG sidecar configuration failure were
surfaced under this label).

`STATUS = OPEN` · `SEVERITY = MEDIUM` · `PN02D_RUN4_VALIDITY_IMPACT = NONE`.

`ERROR_CLASSIFICATION_TREATMENT = FIX_AS_GRAPHRAG09_PREREQUISITE_SUBPHASE` — remediated in 09C before
any production-facing provenance query seam is considered ready. Not remediated in 09A.

## 20. Explicit governance boundaries

```
GRAPHRAG09_SCOPE_CLASS                       = PROVENANCE_ONLY
PN02D_PROVENANCE_EVIDENCE_GATE               = SATISFIED
GRAPH_RETRIEVAL_PRODUCTION_EVIDENCE_GATE     = NOT_SATISFIED
LIGHTRAG_ASK_EVIDENCE_GATE                   = NOT_SATISFIED
BOUNDARY_B_REAL_INTERNAL_DATA                = NOT_APPROVED
VECTOR_BASELINE_REMAINS_CANONICAL            = YES
GRAPH_OUTPUT_REQUIRES_LIVE_RECORD_VALIDATION = YES
RRF_PRODUCTION_INTEGRATION                   = NOT_APPROVED
HYBRID_RETRIEVER_PRODUCTION_INTEGRATION      = NOT_APPROVED
LIGHTRAG_ASK_INTEGRATION                     = NOT_APPROVED
LIGHTRAG_ASK_PHASE_DEFERRED                  = YES
ERROR_CLASSIFICATION_TREATMENT               = FIX_AS_GRAPHRAG09_PREREQUISITE_SUBPHASE
GRAPHRAG_PRODUCTION_INTEGRATION              = NOT_APPROVED
CURRENT_HEAD_LIVE_AUTHORIZED                 = NO
PN02D_REOPENED                               = NO
```

PN02D remains closed and is an input evidence milestone only; its artifacts and tags are not
modified or repointed by GraphRAG-09.
