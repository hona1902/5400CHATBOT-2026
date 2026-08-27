# Internal Architecture Overlay

Keep upstream architecture documentation as the baseline. Record only internal additions, constraints, and deliberate divergences here.

## Initial architecture principles

- Preserve the established Next.js → FastAPI → SurrealDB separation unless an approved decision changes it.
- Preserve background-worker processing for long-running source, embedding, insight, and podcast jobs.
- Keep authorization enforcement in the backend even when the frontend hides unavailable actions.
- Treat AI-provider selection and data egress as governed architecture decisions.
- Treat Graphify as a generated navigation/index layer over the codebase, not part of the runtime product architecture.
- Treat Claude-Mem and other coding-agent memory as developer tooling, not application storage.

## Architecture documents to add when approved

- Authentication and identity architecture.
- RBAC/authorization model.
- Internal network and reverse-proxy topology.
- AI provider/data-egress architecture.
- Document-ingestion pipeline overlay.
- Audit logging architecture.
- Backup/restore topology.
