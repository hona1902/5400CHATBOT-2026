# Progress — GraphRAG-08 Live Micro-Precheck

## Session 2026-08-31
- Static gate PASS: fixture hash MATCH (a58a6853…143d); tree clean; Option-A + Option-B guards
  present; 46 offline tests pass; ruff clean.
- Infra: pinned image ghcr.io/hkuds/lightrag:v1.5.6 present; docker compose v2.40.3; compose
  config validates; sidecar stopped; SurrealDB up.
- Subset (deterministic, caps respected): 8 sources (S002,S007,S021,S030,S039,S040,S052,S055),
  6 DEV queries (Q01 direct, Q13 two_hop, Q19 three_hop, Q25 entity_collision, Q32 rel_collision,
  Q43 negative). HOLDOUT=0. Covers direct/two-hop/three-hop/collision/negative + multi-source.
- Temp-Model seed path mapped: Model(name=openai/text-embedding-3-small, provider=openrouter,
  type=embedding, credential=None).save(); DefaultModels.get_instance().default_embedding_model
  = id; update(). Env-key fallback OPENROUTER_API_KEY; dim 1536. Restore = set prior + delete.
- Built eval-only orchestrator open_notebook/integrations/graphrag/eval/precheck08.py (ruff+import
  clean): isolation → seed temp model → dim probe → sidecar+GRAPHRAG enable → runner V/GQ/GD →
  content-free artifact+manifest → owned cleanup (LightRAG per-id, temp model, namespace drop,
  sidecar down, flag restore). try/finally cleanup on PASS or FAIL.
- LAUNCHED live micro-precheck in background. Awaiting completion + result JSON.

## Execution result
- First launch ABORTED by an eval-only LAUNCHER sys.path bug (`No module named 'commands'`)
  before any benchmark indexing/queries; only 1 dim-probe embedding call occurred. Cleanup ran
  fully (sidecar down, temp ns dropped, temp model deleted, normal DB unchanged). Fixed the
  scratchpad launcher (add repo root to sys.path) — NOT production, NOT the orchestrator, NOT
  methodology/provider. Benchmark work ran exactly ONCE (the fixed run).
- COMPLETE run c531cf98a092: 8 sources / 6 DEV queries / HOLDOUT 0. dim 1536. sidecar up→down.
  V/GQ/GD all EVALUATED for all 6. V hit@1=0.4 hit@5=1.0 full@5=1.0. GQ=GD set_recall 1.0,
  set_precision 0.204, candidate_fraction median 1.0 (expected small-corpus artifact on 8
  sources; NOT a value signal). GD final_answer_invariant holds. GQ↔GD parity: identical sets
  on all 6 (gq_only=0, gd_only=0) — confirms GraphRAG-06 parity; no blocker. Negative Q43:
  non-empty (8 cands) correctly NOT success. Provenance all valid (0 foreign/malformed).
- Cleanup/restore PASS: no graphrag_eval_ namespace leftover; normal DB 1→1 unchanged; no
  temp model in normal DB; sidecar stopped; GRAPHRAG_ENABLED false; fixture hash before==after.
  .artifacts gitignored. Content-free artifact (no source/query text).
- Post-cleanup offline regression: graphrag suite 457 pass/8 skip/0 fail; ruff+mypy clean.
- ⇒ GRAPH_RAG_08_MICRO_PRECHECK_PASS = YES → GRAPH_RAG_08_FULL_EXECUTION_READY = YES (does NOT
  authorize the full run). Value flags remain NOT_RUN. No commit (§63).
