# Current Phase — Phase 0: Governance and Tooling Baseline

## Objective

Create a safe, repeatable coding-agent workflow before major internal fork changes begin.

## In scope

- [ ] Add internal fork rules without rewriting upstream documentation.
- [ ] Keep root `CLAUDE.md` importing root `AGENTS.md` and the internal overlay.
- [ ] Add nested `CLAUDE.md` loaders for backend and frontend rules.
- [ ] Add `.graphify/` to `.gitignore`.
- [ ] Install Graphify and build a local AST code graph.
- [ ] Decide how the Graphify graph will be refreshed during development.
- [ ] Install/review Claude-Mem only after confirming its local storage and cloud-sync policy for this development environment.
- [ ] Capture backend test/lint/typecheck baseline.
- [ ] Capture frontend lint/test/build baseline.
- [ ] Confirm `origin` and `upstream` remotes.

## Out of scope

- Authentication redesign.
- RBAC implementation.
- Production deployment changes.
- Internal document ingestion changes.
- UI redesign.
- Database schema changes unrelated to tooling governance.

## Acceptance criteria

- Repository rules are present and load correctly in Claude Code.
- Existing upstream rules remain intact.
- Graphify generated artifacts are not accidentally committed.
- Persistent memory policy explicitly forbids storing real production/customer data and unapproved cloud sync.
- Baseline backend/frontend verification results are recorded.
- No product behavior is changed by this phase.

## Verification record

Fill this after running the baseline:

```text
Date:
Commit:
Python:
Node:
Claude Code:
Graphify:
Claude-Mem:

Backend pytest:
Backend ruff:
Backend mypy:
Frontend lint:
Frontend tests:
Frontend build:
```

## Next phase

Phase 1 — Upstream and dependency baseline, after Phase 0 acceptance criteria are met.
