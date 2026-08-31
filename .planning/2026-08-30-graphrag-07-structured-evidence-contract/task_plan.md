# GraphRAG-07 — Structured Evidence Contract & Implementation-Readiness Gate

**Goal:** Freeze a small, safe, Open Notebook-owned structured-evidence contract on pinned
LightRAG v1.5.6 `/query/data` and render an implementation-readiness verdict.
**FORENSIC / ARCHITECTURE / CONTRACT DESIGN ONLY.** Not an implementation phase. No production
code, no tests, no `/query/data` wiring, no GraphRAG-client change, no retrieval-behavior change,
no RRF/fusion/rerank, no migration, no DB/Source/LightRAG-storage mutation, no provider traffic.
`OPEN_NOTEBOOK_GRAPHRAG_ENABLED` stays false; sidecar stays stopped. Do NOT start GraphRAG-08.

## Relationship to the design pass
The design pass `GRAPHRAG_07_STRUCTURED_EVIDENCE_ADAPTER_CONTRACT.md` (produced first, same phase)
holds the exhaustive raw-schema field table, data classification, and ACL diagram. This
contract-freeze gate is authoritative: it carries that design forward and adds the operational
contract (config, feature-flag, timeout, cancellation, retry, workspace, stale-source, rollback,
backward-compat, observability, performance), ownership/failure tables, reviews A–G, the readiness
checklist, and the final decision flags. Deliverable = `GRAPHRAG_07_STRUCTURED_EVIDENCE_CONTRACT.md`.

## Frozen inputs (do NOT reopen — task §1)
GraphRAG-04 `cb86a06` (RRF NO; HYBRID_VALUE INCONCLUSIVE); GraphRAG-05 `833ec59`
(no scored surface; STRONG(chunk)/PARTIAL(KG); AGGREGATION REQUIRES_EXPERIMENT); GraphRAG-06
`d7e6a5b` (QUERY_DATA available, avoids final answer, parity YES, no rank/score, corpus mutation
NO, PREFERRED B, IMPL_READY NO). Pinned LightRAG v1.5.6 `b33c6b0` — read-only, out-of-repo,
not moved into repo; no new probe needed.

## Phases
### Phase 1 — Setup & source grounding — Status: complete
Confirm branch/HEAD/clean; read 04/05/06 + adapter-contract companion; read `config.py` for the
exact config surface (`GraphRAGConfig`, env vars, `DEFAULT_TIMEOUT_SECONDS`, no workspace var,
03E execute-lock precedent). Reuse `client.py`/`models.py` conventions from the prior turn.

### Phase 2 — Contract freeze — Status: complete
`GraphEvidenceResult`/`GraphSourceEvidence`/`GraphEvidenceDiagnostics`; evidence types; provenance
quality; invariants. No score/rank by construction; count = frequency.

### Phase 3 — Operational policy freeze — Status: complete
Config/flag (F1+F3), timeout, cancellation, retry (caller-owned), workspace (default), stale-source
(no DB lookup; consumer resolves), cache/corpus invariants, ordering (NONE), text retention
(ID-only), error model (reuse existing), diagnostics, logging, security, egress language.

### Phase 4 — Ownership / failure / policy tables + diagram + example — Status: complete
Ownership table; failure matrix; policy table; architecture diagram; synthetic example.

### Phase 5 — Reviews A–G + readiness checklist + STOP-condition check — Status: complete
Minimality, vendor leakage, provenance safety, false rank, privacy, rollback, phase boundary — all
PASS. §76 stop conditions: none fired. Readiness rule applied.

### Phase 6 — Decision flags + report + reconciliation + git audit — Status: complete
All contract/policy flags YES; `STRUCTURED_EVIDENCE_IMPLEMENTATION_READY = NO` (value UNEVIDENCED +
aggregation REQUIRES_EXPERIMENT + freeze awaiting review; no safety block). Update CURRENT_PHASE.md
07 row + `.active_plan`; git audit (docs/planning only). No stage/commit/push/tag.

## Verdict
CONTRACT FROZEN = YES; IMPLEMENTATION_READY = NO. A NO with a frozen contract is the intended,
acceptable outcome of this gate.
