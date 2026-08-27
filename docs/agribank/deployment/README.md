# Internal Deployment Notes

Do not treat development defaults as the production topology.

## Production deployment must define

- Approved host/container platform.
- Reverse proxy and TLS termination.
- Authentication integration.
- Network boundaries and allowed ports.
- SurrealDB exposure policy.
- Worker process lifecycle and restart policy.
- Secret injection and encryption-key storage.
- Persistent volume locations and permissions.
- Backup/restore and disaster-recovery procedures.
- Monitoring, health checks, log retention, and alerting.
- Approved AI-provider endpoints and outbound network access.
- Upgrade and rollback procedure for upstream merges and internal releases.

## Development-only reminder

Local development may expose frontend/API/database ports for convenience. Production should expose only the minimum approved entry points and keep database/worker internals private unless a documented architecture requires otherwise.
