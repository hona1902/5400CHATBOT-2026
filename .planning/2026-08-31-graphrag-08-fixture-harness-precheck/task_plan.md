# Task Plan — GraphRAG-08 Fixture Authoring + Eval-Only Harness + Micro-Precheck Gate

## Goal
Prepare GraphRAG-08 for a later full value run: (A) author frozen `graphrag_08_eval_v1`
fixtures (75 sources / 60 queries / 30 DEV·30 HOLDOUT / 10 classes / 12 negatives, manual
Source-level GT); (B) validate fixtures + R1/R2 gates; (C) eval-only GD `/query/data` seam;
(D) V/GQ/GD runner support; (E) offline tests; (F) ONE bounded synthetic DEV-only
micro-precheck (≤8 sources, ≤6 DEV queries) — only if real infra/providers are actually
available; (G) cleanup + runtime restore. Then STOP. No full run, no production adapter,
no staging/commit/push.

Outcome flag to determine (NOT execute full run): `GRAPH_RAG_08_FULL_EXECUTION_READY = YES/NO`.

## Hard boundaries (task §4/§81/§86/§96/§105)
- No production Structured Evidence Adapter / query_evidence() / client.query_data().
- No change to client.query(), vector semantics, Ask/Chat/frontend, lifecycle, migrations.
- Production must NOT import eval code (dependency: eval → production only).
- Graph evidence UNORDERED — no MRR/nDCG/Hit@K-by-order on GQ/GD; no fake rank/score.
- Micro-precheck ≤8 sources, ≤6 DEV queries, 0 HOLDOUT. Provider traffic ONLY in precheck.
- No .env edit; process-local GRAPHRAG enable; restore false after. No staging/commit/tag/push.
- Fixture v1 immutable after first provider-backed precheck (hash before == after).

## Reuse surface (forensic — Phase 1 complete)
Frozen GraphRAG-04 modules under `open_notebook/integrations/graphrag/eval/` — DO NOT edit:
`dataset.py` (v04, 6 classes), `metrics.py`, `normalize.py`, `runner.py`, `report.py`,
`__init__.py`. REUSE from them: `normalize.canonical_source_id`, `normalize._normalize`,
`normalize.NormalizedRetrieval/ProvenanceStats`, `normalize.normalize_vector_results`,
`normalize.normalize_graph_references` (has benchmark_ids allowlist), `metrics.hit_at_k/
recall_at_k/mrr` (vector), `runner.GraphRAGEvalRunner` isolation/cleanup patterns,
`client.compute_doc_id`, `models.record_id_for/is_valid_record_id/QueryMode/IndexState`,
`config.GraphRAGConfig/VERIFIED_LIGHTRAG_VERSION`, `service` (index_source/track_status/
query_strict/delete_document_for_source), `commands.embedding_commands.embed_source_command`,
`domain.notebook.vector_search`.

NEW GraphRAG-08 eval-only modules to add (all under eval/, none imported by production):
- `dataset08.py` — v1 loader: 10 QueryClass, Split, answerable, required/optional keys,
  static validator (§24), integrity hashing (§25), freeze marker.
- `metrics08.py` — set_precision/set_recall/set_f1/candidate_count/candidate_fraction/
  false_positive_count/full_source_set_recovered/partial_source_set_recovered + breadth
  distribution helper + negative metrics + complementarity(full/partial). Permutation-invariant.
- `gd_seam.py` — eval-only GD `/query/data` caller (own httpx via GraphRAGConfig; NOT on
  production client) → normalize to canonical source ids + provenance diagnostics; raw vendor
  schema contained; no rank/score.
- `runner08.py` — three-system (V/GQ/GD) runner with isolation namespace option, DEV subset
  selection for precheck, run manifest, cleanup, content-free metadata.
- `report08.py` — content-free artifact (run_type, value_run=NO, holdout_used=NO), metrics,
  breadth, complementarity, GQ/GD parity, provenance, latencies.
- `manifest08.py` — content-free run manifest (created ids, temp model, namespace, state).

Fixtures: `tests/fixtures/graphrag_08_eval_v1/{corpus.json, queries.json, freeze.json}`.
Tests: `tests/test_graphrag_08_fixture.py`, `tests/test_graphrag_08_metrics.py`,
`tests/test_graphrag_08_harness.py` (offline; GD parsing via mock), optional
`tests/test_graphrag_08_precheck_live.py` (gated, skip if no infra).

## Next Step
Phase 5 — finish static quality (mypy running) + GraphRAG regression (flag off); then Phase 6
independent review; then Phase 7 go/no-go on live micro-precheck (infra present but high-risk).

## Phases
### Phase 1 — Repo forensic / reuse surface
**Status:** complete — read whole eval package + client/dataset/metrics/normalize/runner/report.

### Phase 2 — Fixture authoring (corpus + queries + GT)
**Status:** complete
- corpus.json (75 sources), queries.json (60 queries, embedded GT + rationale). Offline
  structural check + loader both green: 75/60/30-30/12, 10×6 classes, GT cardinality {1:36,
  2:6, 3:6}, all 6 negative constructions, zero GT errors.

### Phase 3 — Fixture validator + integrity freeze
**Status:** complete
- dataset08.py loader + validate_frozen_shape + compute/verify_integrity. freeze.json written;
  combined_sha256 = fda0c24e…88618 (FIXTURE_INTEGRITY_BEFORE). verify_integrity: True.

### Phase 4 — Eval-only harness (metrics08, gd_seam, runner08, report08)
**Status:** complete
- metrics08 (set metrics + breadth + full/partial recovery + complementarity_full, permutation-
  invariant), gd_seam (eval-only /query/data, own httpx, STRONG-anchor projection, vendor schema
  contained), runner08 (V/GQ/GD, Option-B isolation, DEV subset selection, manifest, cleanup),
  report08 (content-free artifact). No manifest08 (folded into runner08.RunManifest).

### Phase 5 — Offline tests + static quality + regression
**Status:** complete
- 33 GR08 tests PASS; GraphRAG regression (flag off) 444 passed / 8 skipped / 0 failed; ruff
  clean; mypy Success (all new modules). Production-import boundary verified.

### Phase 6 — Independent adversarial review (fixture + harness)
**Status:** complete
- Fresh-agent review: no HIGH; all dimensions clean. MEDIUM-1/2/3 + LOW-4/5 (GT quality) all
  FIXED pre-freeze; fixture re-frozen (new sha a58a6853…143d); re-verified green.

### Phase 7 — Micro-precheck (≤8 src / ≤6 DEV) OR honest NOT-RUN
**Status:** blocked-on-decision — harness ready; approved isolation policy is Option A
  (dedicated namespace) but runner08 implements proven Option-B (live DB, tagged, fail-closed).
  §47/§84 steer away from the live DB. Surfacing go/no-go to operator (Option B now / implement
  Option A / defer). Live run also = OpenRouter egress + docker sidecar image pull.

### Phase 8 — Cleanup, runtime restore, final audit + report (NO stage/commit)
**Status:** pending — final report + git audit regardless of precheck decision.

### Phase 8 — Cleanup, runtime restore, final audit + report (NO stage/commit)
**Status:** pending

## Decisions Made
| # | Decision | Rationale |
|---|----------|-----------|
| 1 | New v08 modules, never edit frozen v04 modules | Protect GraphRAG-04 approved baseline; clean separation |
| 2 | GD seam builds own httpx from GraphRAGConfig, not on production client | §31 forbids production client.query_data(); contains vendor schema in eval |
| 3 | Precheck runs only if infra genuinely available; else report NOT-RUN honestly | Never fabricate a provider-backed pass |

## Errors Encountered
| Error | Attempt | Resolution |
|-------|---------|------------|
| (none yet) | | |
