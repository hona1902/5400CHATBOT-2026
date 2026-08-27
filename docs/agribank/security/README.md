# Internal Security Baseline

This file is an internal overlay. Upstream security controls remain relevant but are not assumed to be sufficient for production deployment.

## Development data rules

- Use synthetic, masked, or anonymized data only.
- Do not place real customer/account data in source control, tests, screenshots, prompts, issue descriptions, persistent agent memory, or logs.
- Do not commit `.env`, provider keys, access tokens, private keys, or other secrets.
- Treat accidental exposure to an agent memory/indexing system as a security event requiring cleanup and credential revocation where applicable.

## Coding-agent tooling

### Graphify

- Local deterministic code/AST analysis is allowed for routine code mapping.
- Do not enable external semantic/deep processing for internal documents or sensitive corpora without explicit approval.
- Generated graph artifacts remain local unless a future approved policy says otherwise.

### Claude-Mem / persistent agent memory

- Use only for development continuity.
- Do not intentionally store production/customer data or secrets.
- Do not enable cloud synchronization unless explicitly approved.
- Verify remembered details against current source/tests before use.

## Production-hardening topics

Before production, explicitly design and test:

- Authentication and session security.
- Authorization/RBAC.
- Audit logging.
- TLS and reverse proxy.
- CORS and security headers.
- Rate limiting/abuse controls.
- Credential encryption and key lifecycle.
- Network segmentation and port exposure.
- Upload and URL-ingestion controls.
- Dependency/container vulnerability management.
- Backup, restore, retention, and secure deletion.
- AI provider approval and data-egress controls.
