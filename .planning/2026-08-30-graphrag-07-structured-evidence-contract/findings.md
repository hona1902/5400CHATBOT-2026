# GraphRAG-07 Contract-Freeze — Findings

## Repository / checkpoint (verified)
- Branch `feature/graphrag-lifecycle`; HEAD `d7e6a5b` (== tag `graphrag-06-forensic-approved`).
- Worktree carried only the earlier same-phase design-pass docs (adapter-contract + its planning
  folder) as uncommitted; no production/test/migration files touched.
- `OPEN_NOTEBOOK_GRAPHRAG_ENABLED` defaults false (`config.py:104`); sidecar not started; pinned
  LightRAG not re-cloned or moved into the repo.

## Config surface grounding (config.py, verified)
- `GraphRAGConfig{enabled, base_url, timeout, api_key}` via `OPEN_NOTEBOOK_GRAPHRAG_ENABLED /
  _BASE_URL / _TIMEOUT / _API_KEY`; `load_config()` per-request; `DEFAULT_TIMEOUT_SECONDS = 30.0`.
- `config.configured = enabled and base_url` — the same gate a future `query_evidence()` reuses.
- **No workspace env var**; the client sends no `LIGHTRAG-WORKSPACE` header → default single
  workspace is authoritative → WORKSPACE_POLICY = default, no mixing, do not invent a workspace.
- 03E precedent: a DEDICATED default-OFF `..._REBUILD_EXECUTE_ENABLED` lock exists **because**
  rebuild fans the whole corpus across Boundary B. A single read-only evidence query is NOT a
  destructive fan-out → FEATURE_FLAG = reuse GRAPHRAG_ENABLED + additive method (F1+F3), no new lock.

## Frozen contract (rationale in the doc)
- `GraphEvidenceResult{sources:frozenset[GraphSourceEvidence], diagnostics, status}`.
- `GraphSourceEvidence{source_id, evidence_types{DIRECT_CHUNK,GRAPH_ENTITY,GRAPH_RELATIONSHIP},
  supporting_chunk_count(FREQUENCY, ≥1, per-Source, post-dedup), provenance_quality(==STRONG)}`.
- No score/rank/confidence/relevance/priority field — rank impossible by construction (unordered set).
- Provenance model STRONG|PARTIAL|INVALID|FOREIGN|UNKNOWN; STRONG-only emission; PARTIAL corroborates;
  FOREIGN/INVALID/UNKNOWN DROP_AND_REPORT (schema breach = FAIL_CLOSED ProtocolError).
- Result states SUCCESS|EMPTY|DEGRADED (result objects) + FAILURE (exception). Diagnostics inside the
  result, content-free. Error model reuses the existing GraphRAGError hierarchy — no new classes.

## Operational policy freezes
- Text: ID/provenance-only (Option A). RAW_CHUNK_TEXT = NEVER; descriptions/keywords dropped; no
  `raw_*` on the contract; `GraphQueryResult.raw` escape hatch NOT replicated.
- Config reuse; F1+F3 flag; timeout reuse (30 s default); cancellation propagated (CancelledError,
  cancel HTTP, no persist, no bg retry); retry CALLER_OWNED / NO_RETRY at adapter.
- Stale/deleted Source: syntax+ownership only, NO DB lookup on the read path; emit `source_id`;
  the consumer resolves and drops on miss; query path never triggers reconcile/lifecycle mutation.
- CORPUS_MUTATION = NO invariant; cache = accept upstream, do not change (no semantics change).
- Ordering NONE (unordered set); sort-by-source_id only if a sequence is materialized (NON_RELEVANCE).
- Rollback additive; no migration/table/worker; delete module / don't call / flag off.
- Backward compat: `client.query()` untouched and API-compatible; `query_evidence()` additive.
- Vendor upgrade boundary = client.py only; HYBRID version+structural shield fails closed.
- Security: existing X-API-Key; never log keys/content; no raw persistence; egress language kept
  precise (final-answer gen eliminated; Boundary-B retrieval egress REMAINS).
- Payload caps = REQUIRES_IMPLEMENTATION_EVALUATION (bounded by upstream top_k; reject-over-cap posture).
- No `schema_version` field (internal-only, not serialized/persisted).

## Reviews A–G: all PASS. §76 stop conditions: none fired.

## Readiness verdict
CONTRACT_FROZEN = YES; all policy flags YES. IMPLEMENTATION_READY = NO for two non-safety reasons:
(1) value UNEVIDENCED (`HYBRID_VALUE_EVIDENCED=INCONCLUSIVE` 04) + `SOURCE_LEVEL_AGGREGATION_
DEFENSIBLE=REQUIRES_EXPERIMENT` (05) — larger-corpus evaluation (runnable on the existing /query
surface) must show value first; (2) this freeze awaits independent review (Agribank §10/§11). Per
§75 the MUST-remain-NO safety items are all resolved, so the rule permits readiness; it is withheld
by judgment, not blocked by a stop condition.

## Open / deferred (non-blocking to the freeze)
- Loose upstream `Dict[str,Any]` typing of `data`/`metadata` — mitigated by HYBRID structural gate.
- Parsing cost / response size — bounded-by-upstream / requires-live-measurement.
- Concrete payload caps — implementation-phase measurement.
