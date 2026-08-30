# GraphRAG-03E — REBUILD / CANONICAL FULL CONVERGENCE

**Branch:** feature/graphrag-lifecycle
**Baseline (03D approved):** 94b8885178e272b820524cf103e10ab83c94e20b (tag graphrag-03d-approved)
**Migration count at baseline:** 50 (25 up + 25 down). No migration 26. 24/25 FROZEN.
**Status:** PLANNING → awaiting pre-coding decision sign-off (§30, §37)

## Purpose
Operator-triggered, bounded canonical REBUILD that re-drives the EXISTING 03A
`graphrag_index_source` (source_id-only) lifecycle over CURRENT non-empty Open
Notebook Sources, to force content convergence that 03D cannot verify (no
LightRAG content_hash → owned present docs are PRESENT_UNVERIFIED).

REBUILD is **not** a global purge, **not** a second index engine, **not**
automatic/scheduled. It never deletes foreign/unknown docs.

## Key design decisions (present before coding)
- **D1 PLAN default.** `mode="plan"` default: read-only enumerate+classify+count,
  zero remote/provider egress, zero enqueue, zero arm. `mode="execute"` opt-in.
- **D2 Reuse, don't duplicate.** New `rebuild.py` reuses 03A command
  (`graphrag_index_source`, source_id-only), the reconcile keyset canonical
  enumeration (RecordID cursor), `_canonical_state` empty/nonempty check,
  `submit_command` enqueue seam, `service.health()` preflight. No new index engine.
- **D3 Empty-source = Option A (minimum coupling).** EXECUTE indexes non-empty
  only; empty/absent sources are counted + reported, NOT armed for deletion.
  Cleanup stays with 03D REPAIR + 03C drain (which already safely arm/drain
  owned should-be-absent docs). Rebuild is not a deletion mechanism (§4/§30).
- **D4 Completion terminology.** `REBUILD_DISPATCH_COMPLETE` (all eligible
  source_ids in the swept range enqueued; no continuation; no enqueue failures)
  vs `DISPATCH_INCOMPLETE` (cap hit → continuation) / `DISPATCH_PARTIAL`
  (enqueue failures). NEVER "rebuild complete" / "content verified". Per-source
  we claim only *enqueued*, never *reindexed/verified* (§11/§12/§33).
- **D5 Fairness + continuation, no persistent state.** Hard cap
  `max_sources_per_run`; when more remain → `continuation_required=True` +
  `next_cursor` (last source_id RecordID string, no content). Operator re-invokes
  with cursor. Keyset only (no OFFSET). Invalid cursor → fail closed.
- **D6 No migration 26.** Stateless orchestration over canonical state + existing
  commands. Crash/resume = re-run (03A idempotent/convergent) or resume via cursor.
- **D7 EXECUTE gating.** flag OFF → `skipped_disabled` (no dispatch); base_url
  unset → `skipped_not_configured` (no partial dispatch); then content-free
  `health()` preflight before enqueueing.
- **D8 Config.** New clamped `GraphRAGRebuildConfig`
  (`OPEN_NOTEBOOK_GRAPHRAG_REBUILD_*`): canonical_batch_size, max_sources_per_run,
  max_sample_ids. Mirrors drain/reconcile clamping convention.
- **D9 Dedup = optimization only, omitted.** 03A is idempotent/convergent so
  duplicate enqueue is harmless; no dedup correctness primitive (§13).

## Phases
- [x] P0 Context recovery + forensic reads (DONE — see findings.md)
- [x] P1 Present plan + empty-source decision → sign-off gate (§30/§37) — APPROVED (A)
- [x] P2 TDD: property-oriented tests (tests/test_graphrag_03e_rebuild.py) — 31 tests
- [x] P3 Implement rebuild.py + GraphRAGRebuildConfig + graphrag_rebuild command
- [x] P4 Docs: GRAPHRAG_03E_REBUILD.md + CURRENT_PHASE.md update
- [x] P5 Verification: 03E 31 pass (2 live-DB); GraphRAG regression 364 pass; full
      backend 1014 pass/6 skip/5 pre-existing; ruff clean; production mypy clean.
      Live-LightRAG synthetic EXECUTE not run (no sidecar this session).
- [x] P6 Reviews: Karpathy CLEAN; Codex A/B/C — 3 HIGH findings all RESOLVED (execute
      lock; cursor fail-stop skip-safety) + 8 regression tests; re-verified green.
- [~] P7 Final report + READY/NOT_READY signal. Do NOT commit/push.

## Hard invariants (sign-off blockers)
- No global LightRAG purge. No foreign/unknown doc deletion.
- No automatic/scheduled rebuild. No full_text in command payload/result/logs.
- No migration 26; migrations 24/25 byte-for-byte unchanged; migration count 50.
- No doc_id/identity change. No Boundary-B approval for real internal data.
- No "remote content verified" claim.
