# Agribank Internal Fork — Decision Index

Use this file as an index of approved internal decisions. Large structural decisions should have a dedicated document under the appropriate `docs/agribank/` subdirectory or the upstream ADR/PDR directory when that is the better fit.

| ID | Decision | Status | Document |
|---|---|---|---|
| AGR-001 | Preserve upstream documentation and use an internal overlay | Accepted | `../README.md` |
| AGR-002 | Use Graphify as an advisory codebase map, not source of truth | Accepted | `../../../AGRIBANK.md` |
| AGR-003 | Persistent agent memory is historical context and must be revalidated | Accepted | `../../../AGRIBANK.md` |
| AGR-004 | Do not store real production/customer data or secrets in development memory/tests | Accepted | `../security/README.md` |
| AGR-005 | GraphRAG via LightRAG sidecar, additive alongside existing vector RAG | **PROPOSED — not approved** | `GRAPHRAG_DECISION.md` |
| AGR-006 | GraphRAG-09 bounded provenance-only integration (engineering scope) | **Accepted (engineering design) — production NOT approved** | `GRAPHRAG_09_PROVENANCE_INTEGRATION_SPEC.md` |

## AGR-006 — GraphRAG-09 bounded provenance-only integration

- **Status.** Accepted as the GraphRAG-09 **engineering** scope on 2026-10-02. This is not
  production approval and not a provider/data-egress authorization. Full specification:
  [`GRAPHRAG_09_PROVENANCE_INTEGRATION_SPEC.md`](GRAPHRAG_09_PROVENANCE_INTEGRATION_SPEC.md).
- **Context/problem.** "GraphRAG-09" had no approved in-repository definition; it existed only as a
  governance boundary token marked NOT_JUSTIFIED (see `GRAPHRAG_08_VALUE_EVIDENCE_ARCHITECTURE_REVIEW.md`).
  PN02D's durable closeout established QA-value provenance evidence but left retrieval and multihop
  unresolved, so the phase needed an explicit, bounded definition before any engineering could start.
- **Options considered.** (a) Graph-primary / RRF / HybridRetriever production retrieval — rejected:
  no supporting evidence (`GRAPH_RETRIEVAL_PRODUCTION_EVIDENCE_GATE = NOT_SATISFIED`). (b) LightRAG
  Ask answer generation — rejected/deferred: `LIGHTRAG_ASK_EVIDENCE_GATE = NOT_SATISFIED`. (c) Bounded
  provenance-only integration — accepted.
- **Decision.** GraphRAG-09 proceeds only as a bounded, feature-flagged, fail-safe provenance
  integration. Open Notebook's existing vector/source retrieval and answer-generation path remains
  canonical. GraphRAG may contribute corroboration/provenance metadata only after a graph reference
  is validated against a live, notebook-scoped Open Notebook record. Graph-primary retrieval, RRF/
  HybridRetriever production integration, and LightRAG Ask answer generation remain NOT approved and
  require a new scientific evidence gate plus a separate operator decision.
- **Evidence basis.** PN02D durable closeout (`GRAPHRAG_PN02D_RUN4_DURABLE_CLOSEOUT.md`):
  `QA_VALUE = YES` (arms QA-GD, QA-V+GD), retrieval `INCONCLUSIVE (R4)`, multihop `NO (M2)`, isolation
  evidenced, leakage `0`. Interpreted content-safely; the full evaluation artifact is not reproduced
  and no secrets are included.
- **Non-goals.** Graph-primary retrieval; vector replacement; RRF/HybridRetriever production
  deployment; LightRAG-authored answers; graph-authored Ask; unrestricted production GraphRAG;
  deleting existing research/PoC code.
- **Security/data implications.** Boundary B (sidecar → provider on real internal data) remains
  NOT approved; phases 09A–09G are provider-free; 09H uses synthetic/public/approved data only.
  SurrealDB stays the canonical data and scope authority; secrets stay content-safe.
- **Compatibility/upstream implications.** Additive and flag-gated (default OFF); flag OFF preserves
  the exact baseline. No change to upstream retrieval/answer contracts.
- **Migration/rollback implications.** No new migration in this decision; existing migrations 24/25
  are forward-compatible. Flag OFF restores vector-only behavior; GraphRAG failure never corrupts
  canonical state.
- **Tests/verification required.** Per-phase provider-free test gates plus a full offline baseline
  (pytest / ruff / mypy; frontend lint/test/build if affected) and independent review before any
  controlled live validation (09H) and before any production-readiness decision (09I).

## Decision template

When adding a decision, record:

- Context/problem.
- Options considered.
- Decision.
- Security/data implications.
- Compatibility/upstream implications.
- Migration/rollback implications.
- Tests/verification required.
