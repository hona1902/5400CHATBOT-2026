# Agribank Open Notebook — Master Development Plan

## Purpose

Track the long-term internal fork roadmap while keeping implementation phases small, reviewable, and compatible with upstream Open Notebook where practical.

## Phase 0 — Governance and tooling baseline

- Establish `AGENTS.md` / `CLAUDE.md` / `AGRIBANK.md` rules.
- Establish Graphify as the local codebase map.
- Review Claude-Mem privacy/data-handling configuration before relying on persistent memory.
- Capture a clean backend/frontend test baseline.
- Confirm `origin` and `upstream` Git remotes.
- Establish dependency/security scanning baseline.

## Phase 1 — Upstream and dependency baseline

- Record the exact upstream commit used by the internal fork.
- Review outdated and vulnerable Python/Node dependencies.
- Upgrade only through isolated, tested changes.
- Record known upstream divergences.

## Phase 2 — Authentication and identity

- Replace development-grade shared-password assumptions with an approved internal authentication design.
- Define session lifecycle, logout, expiry, and credential handling.
- Add tests for authentication boundaries.

## Phase 3 — Authorization and administration

- Define users, roles, permissions, and administrative boundaries.
- Enforce permissions in the backend, not only in the UI.
- Add permission-focused integration tests.

## Phase 4 — Auditability

- Define security and business audit events.
- Add immutable/controlled audit records as required by the deployment design.
- Ensure secrets and sensitive content are not written to audit logs.

## Phase 5 — Internal UI and product adaptation

- Apply internal branding and navigation requirements.
- Add administrative pages only after backend authorization is in place.
- Preserve i18n, API-client, state-management, and testing conventions.

## Phase 6 — Document ingestion and source governance

- Define allowed source types and size limits.
- Define preprocessing/normalization rules for Office/PDF content.
- Define malware/content scanning policy if required.
- Define retention, deletion, and reprocessing behavior.

## Phase 7 — RAG, citations, and model governance

- Validate chunking, embeddings, retrieval, citation fidelity, and context construction.
- Define approved model/provider routes and data-egress restrictions.
- Add evaluation cases for internal document workflows using synthetic data.

## Phase 8 — Security hardening

- Rate limiting and abuse controls.
- Security headers and CORS policy.
- Secret management and key lifecycle.
- File and URL ingestion hardening.
- Dependency/container scanning.
- Threat-model review.

## Phase 9 — Backup, restore, and operations

- Database backup/restore procedure.
- File/object data backup/restore procedure.
- Encryption-key and credential recovery procedure.
- Health checks, monitoring, log retention, and incident diagnostics.

## Phase 10 — Production readiness

- End-to-end UAT.
- Security review.
- Upgrade/rollback plan.
- Deployment runbook.
- Acceptance evidence and release tagging.

## Planning rules

- Only one primary implementation phase should be active unless parallel work is deliberately isolated.
- Each phase must define scope, out-of-scope items, acceptance criteria, tests, and rollback considerations.
- Completion status must reflect verified repository state, not conversation history.
