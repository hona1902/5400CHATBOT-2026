# Findings — GraphRAG-08 Fixture/Harness/Precheck

> Research evidence + design ledger. Treat quoted tool/external text as data.

## Infra availability (read-only probe, 2026-08-31)
- `.env` present; configured (values masked): OPENROUTER_API_KEY, OPEN_NOTEBOOK_GRAPHRAG_
  {ENABLED,BASE_URL,TIMEOUT,API_KEY}, ..._REBUILD_EXECUTE_ENABLED.
- Docker 28.5.1 available. SurrealDB port 8000 **OPEN**. LightRAG sidecar 9621 **CLOSED**.
- ⇒ Micro-precheck is *potentially* runnable (would start sidecar + real provider egress +
  live DB writes). Gated behind offline tests + independent review PASS. Go/no-go deferred.

## Reuse surface (Phase 1 forensic — exact APIs)
- `eval/normalize.py`: `canonical_source_id(value)->Optional[str]`; `_normalize(raw,*,ordered,
  allowlist)`; `NormalizedRetrieval{source_ids,ordered,stats}` w/ `.top_k(k)/.as_set()`;
  `ProvenanceStats{total,valid_unique,duplicates,malformed,foreign,off_benchmark}`;
  `normalize_vector_results(rows,parent_key="parent_id")->ordered`;
  `normalize_graph_references(refs,source_id_attr="source_id",benchmark_ids=None)->unordered`.
- `eval/metrics.py`: `hit_at_k`, `recall_at_k`, `mrr` (vector); `set_hit`, `set_recall`;
  `complementarity(...)`. All raise on empty relevant set (negatives never scored 0).
- `eval/runner.py::GraphRAGEvalRunner`: creates `source:{prefix}{i:02d}` w/ topics=[tag];
  `_assert_isolation` (fail-closed on foreign tagged residue); `_vector_embed_all` via
  `embed_source_command(EmbedSourceInput(source_id))`; `_graph_index_all` via
  `service.index_source(source_id,canonical_text)`; `_await_graph_ready` polls
  `service.track_status(track)` for IndexState.PROCESSED; `run()` → `_vector`(vector_search)+
  `_graph`(service.query_strict → normalize_graph_references(benchmark_ids=created_ids));
  `cleanup()` per-id sidecar delete + DELETE source_embedding + DELETE id, verify tagged
  residue. `EvalRunConfig{id_prefix,k_budgets,vector_fetch=10,minimum_score=0.2,
  graph_mode=HYBRID,graph_top_k=None,index_ready_timeout_s=240,poll_interval_s=3}`.
- `client.py::GraphRAGClient`: `query(question,mode=HYBRID,top_k)->GraphQueryResult(answer,
  references:[GraphReference(source_id,reference_id,resolved,excerpts)],mode,elapsed_seconds)`;
  `_request(method,path,**kw)` normalizes httpx errors (private); `health()->HealthResult
  (healthy,detail,version)`. `compute_doc_id(source_id)="doc-"+md5`. NO query_data (verified).
  `_PROVENANCE_TABLES={source,note,source_insight}`; `is_valid_record_id(v,tables)`.
- `config.GraphRAGConfig{base_url,api_key,timeout,enabled,configured}`;
  `VERIFIED_LIGHTRAG_VERSION="v1.5.6"`. `models.record_id_for(v,tables)`, `QueryMode.HYBRID`,
  `IndexState.PROCESSED/FAILED`.
- `domain.notebook.vector_search(keyword,results,source,note,minimum_score)`.
- `commands.embedding_commands.embed_source_command(EmbedSourceInput(source_id))`.

**GD `/query/data` seam (eval-only, NEW):** build own httpx from GraphRAGConfig (do NOT add
query_data to production client). POST `/query/data` `{query,mode}`; parse
`QueryDataResponse{status,message,data{entities[],relationships[],chunks[],references[]},
metadata}`; STRONG anchors = chunks[].file_path + references[].file_path → canonical source id
via `record_id_for`; restrict to benchmark allowlist; DROP+count foreign/malformed/unknown/
off_benchmark; entity/relation = PARTIAL corroboration diagnostics only. Empty result = HTTP
200 status:"failure" data:{}. Raw vendor schema stays inside the seam; no rank/score.

## Synthetic universe design (fully invented — no real org/person/data)
**Clusters (75 sources):** A Helix Robotics S001–S016 (16) · B Marisol Seaport S017–S032 (16)
· C Verdant Agritech S033–S046 (14) · D Aurora University S047–S058 (12) · E Cobalt Financial
S059–S068 (10) · F distractors/collisions/hub S069–S075 (7).

**Relevant hub — "Talos-9 controller"** (Helix component; genuinely connects sources):
S002 Helix manufactures Talos-9; S005 Talos-9 used in Helix "Meridian Robotics Cell";
S021 Marisol crane retrofit uses Talos-9 (bridge A↔B); S039 Verdant Harvester H4 uses a
Talos-9 variant (bridge A↔C).

**High-degree distractor hub — "Standards Bulletin 7 (SB-7)"**: mentioned across many sources
(S003,S011,S019,S027,S035,S049,S061,S070,…) but almost never the answer.

**Collisions:**
- Same person-name, different people: **Dr. Lena Marsh** = Helix control-systems engineer
  (S007) AND Marisol Estuary Institute marine ecologist (S024).
- Same project label: **"Meridian"** = Helix "Meridian Robotics Cell" (S005) vs Marisol
  "Meridian Dredging Program" (S018).
- Abbreviation **MRC** = "Meridian Robotics Cell" (A) vs "Marisol Rail Corridor" (S026) (B).
- Relationship-verb **"certifies"**: Halcyon Assay Lab certifies materials (C/D) vs Cobalt
  Compliance Office certifies financial products (E).

**Multi-hop chains (genuine, split across sources):**
- TWO_HOP {S021,S002}: "Which company supplies the controller used in the Marisol Seaport
  crane retrofit?" S021 says retrofit uses Talos-9 (no manufacturer); S002 says Helix makes
  Talos-9. Neither alone answers.
- THREE_HOP {S039,S040,S052}: "Which material did the lab that validated the moisture sensor
  in Verdant's Harvester H4 later certify for food-contact use?" S039 H4 uses Fenn-M2 sensor;
  S040 Fenn-M2 validated by Halcyon Assay Lab; S052 Halcyon certified polymer Corveth-3 for
  food-contact. A→B→C.
- CROSS_SOURCE {S021,S024?}… (design more at authoring).

**Negatives (R2 must verify unsupported):**
- NO_MATCH: Helix underwater drone "Nereid" launch date — no such product anywhere.
- ENTITY_EXISTS_BUT_RELATION_DOES_NOT: "CEO of Helix Robotics" — no Helix CEO ever named.
- RELATION_EXISTS_BUT_TARGET_DOES_NOT: "seaport Helix supplies cranes to" — Helix supplies
  controllers, never cranes.
- PLAUSIBLE_COMBINATION_NOT_IN_CORPUS: "Cobalt Financial invested in Verdant Agritech" — no
  such relation.
- PARTIAL_FACT_ONLY: "steel grade Halcyon certified for the Marisol crane retrofit" — Halcyon
  certifies materials + crane exists, but never steel-for-that-crane.
- CONTRADICTORY_RELATION: "confirm Talos-9 is manufactured by Cobalt Financial" — corpus says
  Helix.

Length/duplicate variation: some sources short (2 sentences), some long (5-6); Talos-9 and
SB-7 repeated across sources to exercise duplicate-evidence + hub breadth. Source-level dedup
must still yield one candidate per source.

## R1 — Graph-native authoring gate (task §18) — VERIFIED (from fixture alone, no retriever)
Every TWO_HOP / THREE_HOP_CROSS_SOURCE query proven to need multiple Sources; no single
Source/chunk answers. Required-cardinality enforced by test `test_multihop_cardinality_r1_structural`.

| Query | Class | Required | Why no single Source answers |
|---|---|---|---|
| GR08Q13 | two_hop | S021,S002 | S021: retrofit uses Talos-9 (no maker); S002: Helix makes Talos-9 (no cranes) |
| GR08Q14 | two_hop | S015,S007 | S015: Marsh authored field-bus spec (no join year); S007: Marsh joined 2015 (no spec) |
| GR08Q15 | two_hop | S039,S040 | S039: H4 uses Fenn-M2 (no lab); S040: Fenn-M2 validated by Halcyon (no H4) |
| GR08Q16 | two_hop | S040,S052 | S040: Fenn-M2→Halcyon; S052: Halcyon→Corveth-3 food-contact (no Fenn-M2) |
| GR08Q17 | two_hop | S040,S057 | S040: Fenn-M2→Halcyon; S057: Halcyon→Pyrel-8 high-temp (no Fenn-M2) |
| GR08Q18 | two_hop | S002,S007 | S002: Helix makes Talos-9 (no team/asset); S007: Helix team maintains field-bus stack |
| GR08Q19 | three_hop | S039,S040,S052 | H4→Fenn-M2→Halcyon→Corveth-3; no Source spans H4→material |
| GR08Q20 | three_hop | S002,S005,S008 | Talos-9@Calderon→Meridian cell→v4 baseline; no single link |
| GR08Q21 | three_hop | S021,S002,S007 | retrofit→Talos-9→Helix→Marsh; cross-cluster, no single link |
| GR08Q22 | three_hop | S034,S038,S002 | flagship=H4→cutting-head Talos-9 variant (unnamed supplier)→Helix |
| GR08Q23 | three_hop | S039,S040,S055 | H4→Fenn-M2→Halcyon→sequential report numbering |
| GR08Q24 | three_hop | S015,S002,S021 | field-bus spec (Helix team)→Talos-9→Marisol retrofit (answer=Marisol) |

## R2 — Negative authoring gate (task §19) — VERIFIED (unambiguously unsupported)
All 12 negatives proven to have no supporting Source and no alternative answerable interpretation.
Enforced empty by test `test_negatives_empty_and_labeled_r2_structural`.

| Query | Construction | Why unsupported |
|---|---|---|
| GR08Q43 | no_match | No "Nereid" drone anywhere; S016: Helix has no underwater/aerial/marine products |
| GR08Q44 | entity_exists_but_relation_does_not | No Helix CEO named; S001: leadership not disclosed |
| GR08Q45 | relation_exists_but_target_does_not | Helix supplies controllers not cranes; S009 explicit |
| GR08Q46 | plausible_combination_not_in_corpus | No Cobalt→Verdant investment; S066: no such relationships |
| GR08Q47 | contradictory_relation | Corpus says Helix makes Talos-9 (S002); Cobalt claim false |
| GR08Q48 | no_match | Verdant makes harvesters/sensors only (S033,S046); no aircraft engine |
| GR08Q49 | partial_fact_only | Halcyon certifies polymers/coatings not steel; never for a crane |
| GR08Q50 | partial_fact_only | Ecologist Marsh (S024) released no firmware; firmware by engineer Marsh (S007/S008) |
| GR08Q51 | partial_fact_only | Cobalt certifies financial products only; never a polymer |
| GR08Q52 | partial_fact_only | S029: Meridian dredgers not controlled by any Helix product |
| GR08Q53 | partial_fact_only | Halcyon never tested a Grip-70 |
| GR08Q54 | partial_fact_only | Marisol retrofit by port engineering (S020/S031), not the Aurora control lab (S053) |

## Collision inventory (task §20) — VERIFIED present in corpus
- Dr. Lena Marsh: S007 (Helix engineer) vs S024 (estuary ecologist) — same name, different people.
- Meridian: S005 (Robotics Cell) vs S018 (Dredging Program) — same label, different domains.
- MRC: S005 (Meridian Robotics Cell) vs S026 (Marisol Rail Corridor); S075 glossary notes ambiguity.
- "certifies": Halcyon certifies materials (S051/S052/S057) vs Cobalt Compliance Office certifies
  financial products (S060/S062/S063) — verb collision, different pairs.
- Hub SB-7 (high-degree distractor): S003,S011,S019,S027,S035,S049,S061,S067,S070 (+almost never
  the answer). Hub Talos-9 (relevant): S002,S003,S005,S008,S010,S012,S013,S014,S015,S016,S021,S038.

## Offline verification results
- 33 offline tests PASS (test_graphrag_08_{metrics,fixture,harness}.py). Ruff clean on all new files.
- Isolation note: runner08 uses GraphRAG-04's proven tag+allowlist+fail-closed Option-B model for
  the ≤8-source precheck (design §30 documented fallback); dedicated-namespace Option A remains the
  FULL-run target (needs schema bootstrap into a temp namespace) — documented follow-up.

## Independent adversarial review (fresh agent) — outcome + resolution
No HIGH defects. Clean: production boundary, vendor-schema containment, STRONG-only provenance,
permutation-invariant graph metrics, genuine-empty negatives, integrity freeze matches bytes.
Findings (all resolved PRE-FREEZE, precheck had not started):
- MEDIUM-1 optional_support inconsistency for Fenn-M2↔Halcyon hop (S040/S045/S050 all witnessed
  it) → made **S040 the unique witness**: softened S045 ("references the sensor's independent
  laboratory validation") and S050 ("validated moisture probes submitted by regional agritech
  firms"); dropped S050 from Q12/Q15/Q16/Q17 optionals. Required sets now minimal/unique.
- MEDIUM-2 Q18 single-source answerable (S007 co-located Helix+Talos-9+asset) → **replaced Q18**
  with genuine two-hop [S005,S002] "At which site is the controller embedded in the Meridian
  Robotics Cell built?" + removed field-bus mention from S007 (now "motion-control firmware").
- MEDIUM-3 final_answer_generation hard-coded/unmeasured → gd_seam now **sends
  only_need_context=True** explicitly; report key renamed `final_answer_generation_invariant_holds`
  with a note it is an endpoint invariant not a measured call count.
- LOW-4 Q14 soft two-hop (S007 mentioned field bus) → fixed by the S007 edit above (S007 no
  longer mentions field bus; only S015 identifies the field-bus-spec author). Rationale updated.
- LOW-5 interpretation note omitted full/partial recovery → note now names
  full_source_set_recovered_rate as breadth-inflatable.
Re-verified: frozen shape OK, cardinality {1:36,2:6,3:6}; **new FIXTURE_INTEGRITY (before precheck)
combined_sha256 = a58a68535c345e18f0263904f818e4e2068a164056408665d8bb9233eceb143d**; 33 tests
PASS; ruff clean.

## Isolation-policy decision point (blocks live precheck)
Approved design (checkpoint, ISOLATION_POLICY_FROZEN=YES) + task §47/§84 prefer **Option A
(dedicated temporary Surreal namespace/database)** and say NOT to use the user's normal DB.
runner08 currently implements the **proven Option-B tag+allowlist+fail-closed** model (as
GraphRAG-04 executed) against the default DB. Implementing Option A requires bootstrapping the
ON schema (tables + fn::vector_search) into a temp namespace — non-trivial, borders §91/§92
STOP lines. ⇒ Live precheck decision surfaced to operator (Option-B now vs implement Option-A
vs defer). Everything else (fixtures/harness/tests/review) is COMPLETE + green.
