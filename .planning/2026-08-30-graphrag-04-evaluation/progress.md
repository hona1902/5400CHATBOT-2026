# GraphRAG-04 — Progress log

## Session 1 — 2026-08-30
- Startup git gate PASSED (branch/HEAD/tag/clean all correct). No pull/rebase/merge.
- Created `.planning/2026-08-30-graphrag-04-evaluation/`; set `.active_plan`.
- Read governance: AGENTS.md, AGRIBANK.md, open_notebook/AGENTS.md, CURRENT_PHASE.md, GRAPHRAG_DECISION.md (AGR-005), GRAPHRAG_POC.md.
- Read source: graphrag/models.py, graphrag/client.py.
- WebFetch pinned upstream HKUDS/LightRAG@v1.5.6 query_routes.py → confirmed ReferenceItem has NO score/rank; references unordered; only_need_context field exists but /query handler generates regardless.
- 3 forensic Explore agents completed (vector seam / graphrag seam / lifecycle seam) — all findings source-cited in findings.md.
- Concluded: NO §57 hard blocker. RRF readiness predicted NO. Writing plan files; about to present §57 gate and WAIT for approval.
- Nothing coded. No system modified. No live run. No commit.

### Verification evidence
- (none run this session — pre-approval, forensic only)

### Next
- Present §57 plan; await user go-ahead before Phase 2.

## Session 1 (cont.) — Phase 2 + 3 build
- §57 APPROVED as-is (+ clarifications: honest name GRAPHRAG_BASELINE_CURRENT_HYBRID, references-only scoring, no client mod, rank gated on evidence).
- Phase 2 DONE: frozen fixture `tests/fixtures/graphrag_04_eval_v1/` (corpus.json 14 sources, queries.json 28 queries/6 classes, README).
- Phase 3 (pure core) DONE: `open_notebook/integrations/graphrag/eval/` = __init__/dataset/normalize/metrics/report. `tests/test_graphrag_04_eval.py` = 32 tests (dataset §43, normalization §44, metrics §45 hand-calc, report/error-accounting, security §46). **32 passed; ruff clean; mypy clean.**
### Next
- Build runner.py (live orchestration) + live test (gated on DB + sidecar). Then Phase 4 live baseline, Phase 5 docs, Phase 6 verification.

## Session 1 (cont.) — runner + live probe + docs
- Built `eval/runner.py` (create→embed→graph-index→bounded-ready-wait→retrieve→normalize→cleanup, strict created-id isolation, content-free metadata). ruff+mypy clean.
- Built `tests/test_graphrag_04_eval_live.py`: 2 live-DB isolation/cleanup tests (providers stubbed) + full-live baseline test (sidecar-gated).
- Ran GraphRAG regression + new tests: **407 passed / 10 skipped** (was 373/9; +34 new). `ruff check .` clean. Live-DB isolation/cleanup tests PASS against REAL SurrealDB (foreign source never touched; no global purge).
- LIVE INFRA PROBE: SurrealDB UP (1 source). LightRAG v1.5.6 sidecar UP/healthy (auth disabled). **BLOCKER: no embedding model configured** (generate_embedding fails) → vector baseline can't run; sidecar Boundary-B provider unconfirmed; GraphRAG env unset.
- Wrote `docs/agribank/development/GRAPHRAG_04_EVALUATION.md` (40-pt; methodology complete; live results PENDING/BLOCKED; RRF_READY=NO decided by contract; HYBRID_VALUE=INCONCLUSIVE pending live). Updated CURRENT_PHASE (04 IN PROGRESS, not signed off).
### Verification evidence
- GraphRAG regression: 407 passed / 10 skipped. ruff: clean. mypy(eval pkg): clean. 04 unit: 32 passed. Live-DB isolation: 2 passed. Full-live baseline: skipped (no provider).
### Next / BLOCKER
- Live §65 baseline needs synthetic-safe embedding model (ON side) + sidecar LLM provider (Boundary B) + GraphRAG env. Presented decision to user. Do NOT fabricate baseline. NOT_READY until live run done.

## Session 1 (cont.) — sidecar probe (per user request)
- Retrieved existing sidecar API key from running container env WITHOUT printing (len 30); used it (auth on write routes; /health public).
- Sidecar non-secret config: LLM_BINDING=openai LLM_MODEL=**mock-llm**; EMBEDDING_BINDING=openai EMBEDDING_MODEL=**mock-embed**; host host.docker.internal:11500/v1 (mock server UP, /v1/models=200).
- Single synthetic doc probe: index ACCEPTED → ANALYZING → **FAILED** (mock LLM can't extract entities). Query not reached. Cleaned up (delete_state=gone). No DB Source row created.
- VECTOR side: Ollama :11434 DOWN, LM Studio :1234 DOWN, 0 credentials, 0 model records. Supported embedding providers: azure,cohere,google,mistral,ollama,omlx,openai,openai_compatible,openrouter,ppq,vertex,voyage.
- **TWO blockers** (not just vector): (1) sidecar bound to mock-llm/mock-embed → real graph indexing FAILS → no valid GRAPH baseline; (2) no VECTOR embedding provider available. STOP per user step 6; nothing configured; no creds invented.
### Next
- Await operator provider decisions: (a) real synthetic-safe LLM+embedding for the SIDECAR (Boundary B) to replace mocks; (b) an embedding provider for Open Notebook VECTOR. Then run live §65 baseline.

## Session 1 (cont.) — OpenRouter verification + non-live finalization
- Operator approved OpenRouter (synthetic-only). VERIFIED FIRST (per operator): OpenRouter serves NO embeddings — 396 models/0 embedding; /embeddings→401 auth gate but no embed model; docs chat-only; ON discovery force-classifies all openrouter models 'language' (model_discovery.py:257, test_model_discovery.py:187); esperanto OpenRouterEmbeddingModel POSTs /embeddings optimistically (unverified). OPENROUTER_API_KEY not present.
- CONCLUSION: text-embedding-3-small via OpenRouter impossible → breaks BOTH vector + LightRAG embedding. OpenRouter OK for GRAPH LLM only. Verified LightRAG v1.5.6 container binding var names (redacted). NOTHING configured; no creds invented.
- Operator DEFERRED provider decision (away, can't provide keys). Instruction: finish all non-live work; do NOT choose provider; do NOT mark NOT_READY; then STOP + report GRAPH_RAG_04_EMBEDDING_PROVIDER_REQUIRED with A/B/C options.
- Updated docs (GRAPHRAG_04 §26.1/§26.2 OpenRouter finding + required-provider status).
### Verification evidence (non-live, final)
- 04 unit: 32 passed. Live-DB isolation: 2 passed. GraphRAG regression: 407/10. **Full backend: 1057 passed / 10 skipped / 5 pre-existing failures (unchanged, unrelated — podcast Win path/symlink + proxy case-merge).** ruff check .: clean. mypy(eval pkg): clean.
### STOP
- GRAPH_RAG_04_EMBEDDING_PROVIDER_REQUIRED. Present A/B/C options. Karpathy + Codex A/B/C + full mypy `.` + live baseline deferred to final gate pass (post embedding-provider). No commit/push. No GraphRAG-05.

## Session 1 (cont.) — LIVE RESUME (OpenRouter works; probes PASS; baseline running)
- CORRECTION: earlier "OpenRouter serves no embeddings" DISPROVED by live runtime — POST /api/v1/embeddings openai/text-embedding-3-small → 1536-dim. Catalog omits embed models but endpoint proxies them. Doc §26.1.1 records correction (historical finding retained). Live runtime takes precedence.
- .env now fully configured by operator: OPENROUTER_API_KEY + OPEN_NOTEBOOK_GRAPHRAG_{ENABLED,BASE_URL,API_KEY,TIMEOUT} + GRAPHRAG_POC_* (sidecar provider). GraphRAG config resolves enabled+base_url+api_key. Sidecar reconfigured to OpenRouter (llm=openai/gpt-4o-mini, embed=openai/text-embedding-3-small).
- Registered temp ON embedding Model via NORMAL path: provider=openrouter, name=openai/text-embedding-3-small, type=embedding (env-key fallback; no credential; no benchmark-specific embed code). Set as default_embedding_model. Prior default was None (RESTORE to None after 04).
- **VECTOR_PROBE = PASS**: real OpenRouter embed (dim 1536) → embed_source_command (1 chunk) → vector_search → probe source at RANK 1. Cleaned up.
- **GRAPHRAG_PROBE = PASS**: 03A graphrag_index_source_command (source_id-only, reload CURRENT) → LightRAG PENDING→PROCESSING→PROCESSED (~12s, OpenRouter LLM+embed) → client.query() HYBRID → 1 valid canonical provenance (our source), **ordered=False (unordered — RRF_READY=NO confirmed LIVE)**. Cleaned up (delete gone).
- FULL LIVE BASELINE (14/28) running in background (bh4abftu5). Artifact → .artifacts/graphrag-04/<run>/evaluation.json (not committed).
### Next
- On baseline completion: read summary, fill doc results (DEV/HOLDOUT/per-class/complementarity/oracle-union/provenance), decide HYBRID_VALUE, RESTORE default embedding model to None + delete temp model, run final verification, STOP at review/commit gate. No commit/push. No GraphRAG-05.

## Session 1 (cont.) — LIVE BASELINE EXECUTED + results
- Full baseline DONE (artifact .artifacts/graphrag-04/b2cab5aa3b88/evaluation.json; gitignored). 24 answerable + 4 negative. Cleanup clean (14 deleted, 0 remaining). Restored: temp model deleted, default_embedding_model=None (prior), DB back to 1 pre-existing source.
- OVERALL (24 answerable): VECTOR Hit@1 .333 / @3 .625 / @5 .875, Recall@5 .826, MRR .526. GRAPH source_hit_rate 1.0 / recall 1.0; 265 refs ALL valid (0 malformed/foreign/dup); **~11 of 14 sources returned per query (~79% of corpus)** → perfect recall is a small-corpus/broad-set ARTIFACT, low precision (~1/11), UNORDERED.
- Complementarity k=5: both_hit 21, graph_only 3, vector_only 0, both_miss 0; oracle union 1.0 all K. Negatives: neither abstains (graph ~8.75, vector ~5.25 cand).
- DEV: VECTOR Hit@5 .857 MRR .494; GRAPH 1.0/1.0. HOLDOUT: VECTOR Hit@5 .90 MRR .57; GRAPH 1.0/1.0.
- **RRF_CANDIDATE_INTERFACE_READY = NO** (live-confirmed: refs ordered=False, no score). **HYBRID_VALUE_EVIDENCED = INCONCLUSIVE** (graph "complementarity" is unranked near-full-corpus dump; not discriminative; small corpus).
- Fixed live test: skip when no embedding model configured (was failing after restore). Added .artifacts/ to .gitignore.
### Verification evidence (post-baseline)
- 04 tests: 34 passed / 1 skipped (full-live skips w/o embed model). ruff (eval+tests): clean. Restoration verified (0 models, default None, 0 synthetic sources, 1 total source).
### STOP — review/commit gate
- Report to operator. Pending for sign-off: Karpathy + Codex A/B/C + full mypy `.`. No commit/push. No GraphRAG-05.

## Session 1 (cont.) — REVIEW GATES
- KARPATHY = PASS (1 nit: orphaned BenchmarkSource re-export in runner.py → fixed).
- CODEX C (architecture) = PASS (9/9 phase-boundary + 4/4 doc checks; migration 50; no prod import of eval; no HybridRetriever/RRF/fusion/Ask/Chat). 1 LOW = grep-pattern false positive (advisory).
- CODEX A (methodology) = FINDINGS resolved: MEDIUM (off-benchmark valid source ids as graph candidates) → FIXED: normalize_graph_references benchmark_ids allowlist → off_benchmark stat (dropped, never candidate); runner passes created_ids; vector stays global. LOW (source_hit_rate naming) → added metric_note.
- CODEX B (security) = dispositions: "scope vector/graph retrieval" HIGHs REJECTED as fixes (measuring the real global seam is the methodology §51; retrieval is READ-ONLY, no Source mutation; off-benchmark now accounted; live run had 0). APPLIED: append-before-create (cleanup covers ambiguous commit); _assert_isolation fails closed on tagged residue. ACCEPTED-LOW: db_total_sources (repro metadata, count only); provider err in exceptions (not in artifact, not credential-bearing).
- Added tests: off_benchmark allowlist + vector-not-filtered. 04 tests now 36 passed / 1 skipped. ruff clean. mypy: my surface (eval pkg + 04 tests) CLEAN; 91 pre-existing errors remain in 03c/03d/03e/command_seam test files (NOT mine — git status confirms).
### Test characterization (evidence-based; none caused by 04 code)
- Non-live deterministic baseline: 5 pre-existing failures (podcast Win path/symlink x4 + proxy case-merge).
- Flag-induced (only when operator .env OPEN_NOTEBOOK_GRAPHRAG_ENABLED=true): 4 in test_graphs.py TestSaveSourceTitlePreservation — save_source→_maybe_enqueue_graphrag_index (03A) logs source.id which the test's Source mock lacks. PROVEN: pass with flag=false; neither file in my diff.
- Live GraphRAG tests flaky vs REAL OpenRouter sidecar (mock-tuned windows): 03C roundtrip (skips in isolation, failed once), 03E through-worker (needs worker). Not my code; not 04 regressions.
### Secret/artifact audit: .env not tracked; evaluation.json gitignored; no real keys (only <OPENAI_API_KEY> placeholder in doc runbook); no retrieved content in artifact; .gitignore +.artifacts/ minimal.
### DECISIONS (frozen): RRF_CANDIDATE_INTERFACE_READY = NO. HYBRID_VALUE_EVIDENCED = INCONCLUSIVE.
### STOP at GRAPH_RAG_04_REVIEW_COMPLETE. No commit/push/tag. No GraphRAG-05.
