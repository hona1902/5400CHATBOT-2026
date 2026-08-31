# GraphRAG-07 — Findings

## Repository / checkpoint (verified)
- Branch `feature/graphrag-lifecycle`; HEAD `d7e6a5ba0bb9ecd039d7a5d58823e45a65baea49`
  (== tag `graphrag-06-forensic-approved`); worktree clean before any edit.
- `OPEN_NOTEBOOK_GRAPHRAG_ENABLED` env-driven, defaults false (`config.py:104`); sidecar
  not started; no re-clone of pinned LightRAG needed (frozen 05/06 findings reused).

## Frozen input reuse (no re-derivation, per task §2)
- `/query/data` schema, field classification, provenance quality (STRONG chunk/reference,
  PARTIAL entity/relation), and no-score/no-rank facts are FROZEN by GraphRAG-06 §7/§10 and
  GraphRAG-05, each citing `file:line` in pinned `v1.5.6` (`b33c6b0`). GraphRAG-07 designs a
  contract on top of them; it does not reopen them.
- Empty result is HTTP 200 + `status:"failure"` + `data:{}` (06 §6) — not an HTTP error.
- `chunks[].content = RAW_SOURCE_TEXT`; entity/relation `description = DERIVED_TEXT` (06 §7).

## Existing conventions the design reuses (verified in source)
- Error taxonomy already sufficient: `GraphRAGUnavailable/Configuration/Request/Conflict/
  Server/Protocol/Validation` (`models.py:57-123`). No new exception classes needed.
- Record-id validators: `is_valid_record_id` / `record_id_for` (`models.py:202/251`) over
  `_PROVENANCE_TABLES = {source, note, source_insight}` (`client.py:86`). `_looks_like_record_id`
  is structural-only (shape, not authorization/existence) (`client.py:89-112`).
- Version constant `VERIFIED_LIGHTRAG_VERSION = "v1.5.6"` (`config.py:57`); health reads
  `core_version`/`api_version` (`client.py:241`); 404/405 → `GraphRAGConfigurationError`
  (`client.py:180`) is the version-mismatch signal.
- Content-free logging pattern already in place (`client.py:352/605`); errors never echo bodies
  (`client.py:199/351`). Boundary invariant "everything LightRAG-shaped stops here" (`models.py`).
- Only `client.query()` (→ `/query`) is wired; there is NO `query_data()` today (06 §1). Ask/Chat
  untouched. `GraphQueryResult.raw` escape hatch exists on the legacy type (`models.py:488`) and is
  deliberately NOT replicated on the structured contract.

## Design decisions (rationale in the main doc §30)
- ACL required: `RAW_LIGHTRAG_SCHEMA_EXPOSED_UPSTREAM=NO`, `NORMALIZED_EVIDENCE_ADAPTER_REQUIRED=YES`.
- Text dropped: chunk content / entity desc / relation desc / raw refs all `RETAINED=NO`.
- `EVIDENCE_PERSISTENCE_POLICY=TRANSIENT_ONLY` — no table, no migration (count stays 50).
- Provenance: STRONG-only emission; PARTIAL corroborates; UNKNOWN/FOREIGN/MALFORMED
  DROP_AND_COUNT; ambiguous KG never creates a Source.
- Contract: `GraphSourceEvidence{source_id, evidence_types{DIRECT_CHUNK,GRAPH_ENTITY,
  GRAPH_RELATIONSHIP}, supporting_chunk_count(frequency, NOT relevance), provenance_quality}`
  inside a flat `GraphEvidenceResult{sources(frozenset), diagnostics}`. No score/rank/order field
  → rank impossible by construction. Diagnostics content-free.
- Schema shield HYBRID (version gate + structural envelope validation, fail-closed).
- Preferred adapter option = **C** (Source-only projection via strict ON-owned adapter); D
  (graph-detail) deferred because `HYBRID_VALUE_EVIDENCED=INCONCLUSIVE`.
- `ADAPTER_PAYLOAD_LIMITS_REQUIRED=REQUIRES_IMPLEMENTATION_FORENSIC` (caps warranted; numbers
  deferred; no ON convention supplies one).
- `STRUCTURED_EVIDENCE_CONTRACT_DESIGNABLE=YES`; `STRUCTURED_EVIDENCE_IMPLEMENTATION_READY=NO`.
- Mandated unchanged: `RRF_CANDIDATE_INTERFACE_READY=NO`, `GRAPH_CANDIDATE_IMPLEMENTATION_READY=NO`.

## Adversarial reviews A–H: all PASS (main doc §28)
Minimization (no text survives), provenance safety (no unsafe promotion), misuse resistance
(rank impossible by construction), vendor coupling (only ON types cross), schema drift (HYBRID
fails closed), logging/security (no content/creds), contract minimality (soft fields documented
+ optional), phase boundary (docs/planning only).

## Documentation reconciliation
CURRENT_PHASE.md GraphRAG-05 row corrected from "awaiting review" → "FORENSIC/DESIGN COMPLETE /
APPROVED" (tag `graphrag-05-forensic-approved`, commit `833ec59`). 05 commit/findings NOT amended.

## Open / deferred risks
- Upstream `data`/`metadata` are `Dict[str,Any]` (loose) — mitigated by HYBRID structural gate.
- STRONG-provenance liveness (does the Source still exist in SurrealDB?) is structural-only here;
  a live existence check is deferred to implementation (safe by default — worst case cites an
  ON-owned deleted Source).
- Concrete payload caps deferred to the implementation forensic.
- Hybrid graph value remains `INCONCLUSIVE` (04) pending the larger-corpus evaluation (05 §12).
