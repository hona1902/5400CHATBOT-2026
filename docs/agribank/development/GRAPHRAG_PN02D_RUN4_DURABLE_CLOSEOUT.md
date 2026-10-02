# PN02D Run #4 Durable Result Closeout

Immutable, content-safe milestone record for the first fully durable-complete
post-hardening PN02D B3 run. This document binds the external durable artifact by
identity and digest only; it is **not** the scientific system of record (that remains
the external `result.json`), and it contains no secrets, credentials, raw prompts, or
raw evaluation source content.

## 1. Milestone status

`PN02D_DURABLE_REPRODUCIBILITY_CLOSEOUT_ACCEPTED`

This is a **PN02D durable-evidence closeout only**. It is explicitly **not**:

- production approval;
- GraphRAG-09 authorization;
- LightRAG Ask approval;
- any live-execution authorization.

## 2. Evidence identity

| Field | Value |
|---|---|
| Run ID | `pn02db3postyrh2r4-27aca9de-6d3b-4d0c-9ccc-9c3af4e3a068` |
| Source / pre-closeout HEAD | `18bcc71bdb7dfe4620f08ab7c7f0a5b282249711` |
| Live-auth tag | `graphrag-pn02db3yrh2-b3-live-auth-successor-approved` (YRH2) |
| Live-auth tag object | `88859f77cc9ba1f636f206b79fd6b9a15e1486c3` |
| Live-auth tag peel | `18bcc71bdb7dfe4620f08ab7c7f0a5b282249711` |
| **Canonical artifact SHA-256** | `d528aa910440bcc530e23c2a35300373278802e211a021d6202fa3415520b0fe` |
| **Physical file SHA-256** | `b049dc76500e3ad577961897a017b7282ab41ed1e2bcc58f587256460df9c278` |
| `_SUCCESS` | VALID (present) |
| Ledger | row 8, consumed |
| Run reusable | NO |
| Independent review | `D_PASS_CLEAN` (HIGH=0, MEDIUM=0, LOW=0) |
| `CLOSEOUT_COMMIT_CSUCC` | `PENDING_CAPTURE` |

The **canonical artifact SHA-256** (`artifact_sha256`, computed over the canonical
envelope excluding the self-referential digest and `written_at_utc`) and the **physical
file SHA-256** (the whole on-disk `result.json` bytes) are **different by design** and
must never be conflated. The authoritative durable artifact lives outside the repository
at `~/.open-notebook/eval_results/<run_id>/result.json` and is classified
`EXTERNAL_DURABLE_EVIDENCE` — it is not committed, copied, normalized, or duplicated here.

**CSUCC binding (self-reference note):** the closeout commit hash cannot be written into a
pre-commit document without a self-referential dependency. Therefore `CLOSEOUT_COMMIT_CSUCC`
stays `PENDING_CAPTURE` in this reviewed doc, and the actual closeout commit identity is
bound after capture via the annotated evidence tag whose peel **is** the closeout commit —
no post-review in-document mutation is required.

## 3. Technical result

- Technical status: `COMPLETED`
- B3 completeness: `COMPLETE`; Stage 1: `PASS`
- GraphRAG indexing: `24/24` complete, failures `0`
- Durable finalization: `SUCCESS`; `_SUCCESS` valid; artifact schema + hash validation passed
- Cleanup successful (run-owned temporary namespace/database removed; no container/network leaks)
- Normal application database (`open_notebook`/`open_notebook`) remained intact

## 4. Scientific scope

Established — an **independent durable Q1/YES result** on PN02:

- Durable PN02 QA-value observation; `qa_value_status = QA_VALUE_DEMONSTRATED_ON_PN02`
- Decision: rule `Q1`, verdict `YES`; positive arms `QA-GD`, `QA-V+GD`
- Fact recall: QA-GD `0.952381`, QA-V+GD `0.952381` (QA-V `0.0`)
- Citation coverage: QA-GD `0.97619`, QA-V+GD `0.952381`
- Negative abstention: `9/9`
- Isolation evidenced; leakage `0`; violations `[]`
- Retrieval: `INCONCLUSIVE (R4)`; Multihop: `NO (M2)` — dimensional observations, not failures

This is an **independent** durable result; it is **not** described as exact numerical
replication of any prior run.

Run #4 does **not** establish: generalized product superiority; universal GraphRAG benefit;
production readiness; GraphRAG-09 approval; LightRAG Ask production approval; general
multihop improvement. `SCIENTIFIC_SCOPE_OVERCLAIM = NO`.

Historical separation: `RUN4_DEPENDS_ON_B3YR3 = NO`; `HISTORICAL_B3YR3_CLOSEOUT_REOPENED =
NO`. Any similarity to the historical B3Y-R3 Q1/YES outcome is context only; Run #4 stands
on its own durable evidence.

## 5. Retention / schema closure

`RUN2_SCHEMA_FAILURE_REPRODUCED = NO`. The five fields whose absence caused the Run #2
persistence failure were all persisted with correct types:

- `diagnostics.b3_mode = "OBSERVABILITY_ONLY"`
- `retrieval.rule = "R4"`
- `retrieval.neg_return_v5 = 0` (integer, not bool)
- `multihop.rule = "M2"`
- `scientific_outputs.notes = list[str]`

## 6. GraphRAG indexing configuration closure

`GRAPHRAG_INDEXING_CONFIG_GAP_CLOSED = YES`. The Run #3 blocker was the eval-only missing
configuration `GRAPHRAG_POC_API_KEY`, which binds both the index client's `X-API-Key` and
the per-cell sidecar's `LIGHTRAG_API_KEY`; client/sidecar key symmetry was validated and
GraphRAG indexing completed `24/24` in Run #4.

`PRODUCT_GRAPHRAG_FLAG_REMAINED_DISABLED = YES` — the eval builds its `GraphRAGConfig`
directly and never consults `OPEN_NOTEBOOK_GRAPHRAG_ENABLED`; no production GraphRAG
integration was activated.

Run #4 used an explicit 8-name environment allowlist (names only, no values):
`SURREAL_URL`, `SURREAL_USER`, `SURREAL_PASSWORD`, `SURREAL_NAMESPACE`, `SURREAL_DATABASE`,
`OPEN_NOTEBOOK_ENCRYPTION_KEY`, `OPENROUTER_API_KEY`, `GRAPHRAG_POC_API_KEY`.
`BLIND_DOTENV_PROPAGATION = NO`; `OPEN_NOTEBOOK_GRAPHRAG_ENABLED = NOT_PROPAGATED`.

## 7. Independent review

`REVIEW_VERDICT = D_PASS_CLEAN` — HIGH=0, MEDIUM=0, LOW=0, FINDINGS=NONE.

The review independently verified, from the durable artifact: the canonical digest; schema
validation; QA-value arithmetic; the Q1 decision rule; isolation evidence; provider
accounting; indexing completeness; the ledger; cleanup; and the absence of secret /
raw-payload leakage.

The reviewer's initial pooled citation-coverage calculation was corrected to the
implementation's documented macro-mean; no artifact finding resulted (`ARTIFACT_DEFECT =
NO`; `OPEN_FINDING_CREATED_FROM_REVIEWER_METHOD_ERROR = NO`).

## 8. Open non-blocking backlog

Carried-forward observability item (not introduced by Run #4):

- The generic `unknown_provider_error` classifier can mask non-provider failures (historically
  a SurrealDB auth/config failure and a GraphRAG sidecar configuration failure were surfaced
  under this label).
- `STATUS = OPEN`; `SEVERITY = MEDIUM`; `RUN4_RESULT_VALIDITY_IMPACT = NONE`.

This is **not** declared fixed and is **not** remediated as part of this closeout.

## 9. Governance boundaries

- `GRAPH_RAG_09_AUTHORIZED = NO`
- `GRAPHRAG_PRODUCTION_INTEGRATION = NOT_APPROVED`
- `LIGHTRAG_ASK_INTEGRATION = NOT_APPROVED`

The future closeout evidence tag (§10) is **not** a live-auth tag and carries no execution
authority.

## 10. Future execution semantics

Upon the approved closeout capture, the closeout commit will advance HEAD beyond YRH2's
peel, so YRH2 is **expected to become `ANCESTOR_STALE`**. This is intentional, deliberate
fail-closed behavior: no live-auth successor is created as part of PN02D closeout.

`FUTURE_EXECUTION_REQUIRES_NEW_LIVE_AUTH_SUCCESSOR = YES` — any future B3/provider execution
must create a new, independently reviewed live-auth successor.

The proposed future evidence tag `graphrag-pn02db4-durable-result-closeout-approved` is an
`IMMUTABLE_EVIDENCE_TAG`: `IS_LIVE_AUTH_TAG = NO`, `CAN_AUTHORIZE_EXECUTION = NO`,
`CAN_SATISFY_EXACT_HEAD_LIVE_AUTH = NO`, `NEVER_REPOINT = YES`. It is not created in the
implementation phase.

*Current state during implementation:* `HEAD = 18bcc71bdb7dfe4620f08ab7c7f0a5b282249711`,
`YRH2 = EXACT_HEAD` — the stale transition above is future, not current.
