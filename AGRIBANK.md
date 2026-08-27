# Agribank Internal Fork — Development Rules

This repository is an internal fork of Open Notebook. These rules define how coding agents and developers should work on the fork while preserving upstream compatibility, security, traceability, and maintainability.

## 1. Rule precedence and source of truth

When information conflicts, use this precedence order:

1. Current source code.
2. Automated tests and verified runtime behavior.
3. Database migrations and persisted schema.
4. Git history and the currently checked-out commit.
5. Project rules in `AGENTS.md`, this file, and nested component rules.
6. Approved architecture/decision records under `docs/`.
7. Fresh Graphify results generated from the current checkout.
8. Active planning files under `docs/agribank/development/`.
9. Persistent agent memory such as Claude-Mem or native auto-memory.

Historical memory is evidence of what happened before, not proof of the current implementation. Never modify code from memory alone without verifying the current source and tests.

Installed agent skills and plugins extend these repository rules. They do not override architectural, security, testing, or data-handling constraints defined by the repository.

## 2. Development workflow

For non-trivial changes:

1. Inspect the current implementation before proposing changes.
2. Use Graphify for repository-wide dependency, call-path, and blast-radius analysis when the change spans multiple modules.
3. Read only the relevant source files identified by the analysis and verify assumptions against the code.
4. For architecture or behavior changes, produce a plan before implementation.
5. Keep changes scoped to the requested phase. Do not opportunistically refactor unrelated areas.
6. Add or update tests that protect the intended behavior.
7. Run the required verification commands before declaring completion.
8. Record approved structural decisions in the decision documentation instead of relying only on chat history or memory.

For small, localized changes, avoid unnecessary ceremony, but still preserve existing conventions and run the relevant tests.

## 3. Graphify policy

Graphify is the preferred codebase map for cross-module reasoning.

- Use Graphify to inspect architecture, imports, calls, flows, dependencies, bridge nodes, and likely blast radius before large changes.
- Prefer the deterministic local code/AST graph for routine code analysis.
- Treat Graphify as an index and navigation aid, not as the source of truth.
- If Graphify may be stale, update or rebuild it before relying on the graph.
- Verify important graph findings against the current source and tests before editing.
- After Graphify narrows the affected area, use targeted source reads rather than broad repository-wide reading.
- Do not enable semantic/deep processing of internal documents or other potentially sensitive corpora unless that processing path has been explicitly approved.
- Generated `.graphify/` artifacts are local working data and should not be committed unless a deliberate repository policy later changes this rule.

## 4. Persistent memory policy

Persistent agent memory may be used to preserve development continuity across sessions.

- Memory is advisory historical context, never the source of truth.
- Revalidate remembered implementation details against the current checkout.
- Prefer memory for previous investigations, root causes, rejected approaches, recurring failures, and prior decisions that can then be verified.
- Never intentionally store production customer data, account data, credentials, passwords, API keys, tokens, private keys, or real banking records in persistent development memory.
- Use synthetic, masked, or anonymized data in development and tests.
- Do not enable cloud synchronization of development memory unless explicitly approved under the applicable internal data-handling policy.
- If sensitive information is accidentally exposed to a memory system, stop work and follow the appropriate cleanup/revocation process before continuing.

## 5. Planning and decision records

Long-lived internal planning lives under `docs/agribank/development/`.

- `MASTER_PLAN.md` describes the long-term roadmap.
- `CURRENT_PHASE.md` tracks the active implementation phase and acceptance criteria.
- `DECISIONS.md` indexes approved internal decisions.

Temporary agent scratch files are not architecture documentation. When a structural decision is approved, record it in the project documentation or the upstream ADR/PDR structure as appropriate.

Do not treat a planning file as evidence that code was implemented. Verify the current source, tests, and Git history.

## 6. Security and data-handling baseline

This fork is intended for internal use and must not assume upstream development defaults are sufficient for production.

- Never commit secrets.
- Never add real customer or production banking data to fixtures, examples, screenshots, logs, prompts, or tests.
- Use synthetic or anonymized data for development.
- Do not weaken existing SSRF validation, credential protection, upload limits, or authentication controls without an approved security decision.
- Do not expose SurrealDB, worker services, debug endpoints, or development ports to broader networks without an approved deployment design.
- Treat authentication, authorization, audit logging, credential storage, file ingestion, model/provider access, and backup/restore as security-sensitive areas.
- Security-sensitive changes require focused tests and an independent review before completion.
- Production deployment must define explicit authentication, authorization, auditability, TLS/reverse-proxy behavior, secret management, backup/restore, network boundaries, and approved AI-provider/data-egress policy.

## 7. Upstream compatibility

Preserve a clean relationship with `lfnovo/open-notebook` so upstream security and bug fixes remain adoptable.

- Avoid unnecessary edits to upstream documentation and unrelated files.
- Keep internal documentation under `docs/agribank/` whenever possible.
- Prefer additive internal layers over invasive rewrites unless the product requirement justifies the divergence.
- Keep an `upstream` Git remote pointing to the original Open Notebook repository.
- Never merge upstream changes blindly. Review security changes, migrations, provider changes, API changes, frontend breaking changes, and dependency changes before integration.
- When internal behavior intentionally diverges from upstream, document the reason.

## 8. Backend constraints

The root and backend `AGENTS.md` files remain authoritative for implementation details. In addition:

- Keep routers thin and business logic in the established service/domain layers.
- Preserve async-first behavior.
- Treat migrations as forward-compatible production changes; do not delete old migrations merely to simplify development.
- Background commands must remain safe under retries and duplicate execution where applicable.
- Do not instantiate alternate AI provider clients outside the repository's provisioning abstraction unless an approved architecture decision explicitly changes that design.

## 9. Frontend constraints

The frontend `AGENTS.md` remains authoritative. In addition:

- UI redesign must not silently alter API contracts, authorization semantics, data models, or background workflows.
- Preserve the existing i18n architecture for all user-facing strings.
- Preserve the existing shared API client and query/mutation conventions unless an approved architecture change replaces them.
- New internal administration pages must receive the same testing and permission checks as user-facing features.

## 10. Required verification

Use the smallest relevant test set during iteration, then run the full applicable baseline before phase completion.

Backend baseline:

```bash
uv run pytest tests/
uv run ruff check .
uv run python -m mypy .
```

Frontend baseline from `frontend/`:

```bash
npm run lint
npm run test
npm run build
```

For user-facing flows, run the relevant Playwright/E2E checks when available.

A phase is not complete because an agent says it is complete. Completion requires passing acceptance criteria, tests, and verification evidence.

## 11. Review policy

For security-sensitive, migration-heavy, or broad architectural changes:

- Perform a second-pass review independent from the implementation pass.
- Review the final diff, not only individual files.
- Check for unintended scope expansion, compatibility regressions, missing tests, secret/data leakage, and stale Graphify or memory assumptions.
- When using Codex or another reviewer, treat its findings as review input and verify them against source and tests before applying changes.

## 12. Prohibited shortcuts

Do not:

- bypass tests to make a phase appear complete;
- remove security checks because they complicate development;
- hard-code production credentials, internal secrets, or customer identifiers;
- rewrite large unrelated sections while implementing a narrow requirement;
- treat Graphify, Claude-Mem, a planning document, or an agent summary as more authoritative than the current repository;
- enable new external data-processing or synchronization paths for internal material without explicit approval;
- claim compatibility, security, or completion without verification.
