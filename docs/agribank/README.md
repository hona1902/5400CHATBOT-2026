# Agribank Internal Fork Documentation

This directory contains documentation specific to the internal Open Notebook fork. Upstream Open Notebook documentation should remain unchanged whenever practical so upstream updates can be reviewed and merged with fewer conflicts.

## Structure

- `development/` — roadmap, current phase, and internal decision index.
- `architecture/` — internal architecture overlays and integration diagrams.
- `security/` — security baseline, threat-model notes, data-flow controls, and hardening decisions.
- `deployment/` — internal deployment topology, environment requirements, backup/restore, and operations.

## Documentation rule

The current source code and automated tests remain the source of truth for implemented behavior. These documents describe approved intent, architecture, and operational policy. When documentation and code differ, investigate the discrepancy instead of assuming either side is current.
