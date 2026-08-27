# Agribank Internal Fork — Decision Index

Use this file as an index of approved internal decisions. Large structural decisions should have a dedicated document under the appropriate `docs/agribank/` subdirectory or the upstream ADR/PDR directory when that is the better fit.

| ID | Decision | Status | Document |
|---|---|---|---|
| AGR-001 | Preserve upstream documentation and use an internal overlay | Accepted | `../README.md` |
| AGR-002 | Use Graphify as an advisory codebase map, not source of truth | Accepted | `../../../AGRIBANK.md` |
| AGR-003 | Persistent agent memory is historical context and must be revalidated | Accepted | `../../../AGRIBANK.md` |
| AGR-004 | Do not store real production/customer data or secrets in development memory/tests | Accepted | `../security/README.md` |
| AGR-005 | GraphRAG via LightRAG sidecar, additive alongside existing vector RAG | **PROPOSED — not approved** | `GRAPHRAG_DECISION.md` |

## Decision template

When adding a decision, record:

- Context/problem.
- Options considered.
- Decision.
- Security/data implications.
- Compatibility/upstream implications.
- Migration/rollback implications.
- Tests/verification required.
