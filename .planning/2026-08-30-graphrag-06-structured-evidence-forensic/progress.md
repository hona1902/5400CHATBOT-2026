# GraphRAG-06 Progress Log

## Session 1 — 2026-08-30
- Read GRAPHRAG_05_RANKED_CANDIDATE_FORENSIC.md (frozen input): /query/data already mapped
  (query_routes.py:1309 → aquery_data lightrag.py:3701 → only_need_context lightrag.py:3817 →
  convert_to_user_format utils.py:6076-6197; response schema lightrag.py:3717-3778). 05 asserted
  "only_need_context=True (no LLM)" — but that = no FINAL ANSWER; 06 §7 must verify whether
  keyword-extraction LLM survives.
- Located LightRAG clones: /d/Project Web/LightRAG is v1.5.0 (WRONG, do not use). Retained 05
  scratch clone gone. Re-cloned pinned v1.5.6 (b33c6b0) to scratchpad; commit verified.
- Read current client.py in full: only query() wired; no query_data(). Set up planning files.
- NEXT: trace pinned /query and /query/data; answer §7 keyword-extraction/cache question.

## Session 1 — completion
- Traced pinned v1.5.6 end-to-end: lightrag.py aquery/aquery_data/_query_done;
  operate.py kg_query/extract_keywords_only; utils.py convert_to_user_format;
  query_routes.py query_text/query_data + decorators/auth.
- Wrote docs/agribank/development/GRAPHRAG_06_STRUCTURED_EVIDENCE_FORENSIC.md (call graphs,
  §7 classification B, parity YES, egress matrix, schema table, contract, options A/B/C/D,
  preferred B, reviews A–F, decisions, final report).
- Updated CURRENT_PHASE.md GraphRAG-06 row; pointed .active_plan; marked all phases complete.
- Git audit (§37): only .active_plan + CURRENT_PHASE.md modified; new GRAPHRAG_06 doc + planning
  dir. No production/test/migration/provider files. Not staged, not committed.
- DECISION: PREFERRED_ARCHITECTURE=B (design target; impl NOT ready). §7=B. PARITY=YES.

## Session 2 — adversarial review gate
- Re-verified pinned HEAD b33c6b0 / 1.5.6 / api 0328 before re-reads.
- PARITY hardest attack: QueryParam=16 fields (base.py:90-164); aquery_data copy omits ONLY
  include_references (output flag; /query/data builds refs anyway). All retrieval fields copied.
  only_need_context used at 3 sites (4216 docstring, 4275 kg gate, 6310 naive gate) — never
  gates retrieval. => PARITY=YES HOLDS.
- Final-answer = 1 use_model_func call (operate.py:4349) + 1 query-cache write (~4379), both
  skipped by /query/data. FINAL_ANSWER_CALL_COUNT_REDUCTION=1/uncached; cost=EXPECTED_BUT_UNMEASURED.
- embedding_cache_config default enabled:False (lightrag.py:659); answer cache /query-only; VDB
  .query read-only. => CORPUS=NO, CACHE=CONFIG_DEPENDENT hold.
- Review J: generated answer has NO functional ON consumer — only diagnostic echo
  (api/routers/graphrag.py:266); eval reads result.references only (eval/runner.py:252), never
  .answer. => B preferred, C not needed now.
- DOC CHANGE (only one): added §0a Historical precision correction (05 "no LLM" shorthand →
  classification B) + §21a review-gate record. GraphRAG-05 checkpoint NOT altered.
- No conclusion strengthened to favor B. All decisions unchanged.

## Guardrails held so far
PRODUCTION_CODE_CHANGED=NO · TEST=NO · MIGRATION=NO · PROVIDER_TRAFFIC=NO · DB_MUTATION=NO ·
SOURCE_MUTATION=NO · LIGHTRAG_STORAGE_MUTATION=NO · SIDECAR_STARTED=NO. Only files touched:
docs/planning (to be written). Git clone of public pinned tag into scratchpad only.
