# GraphRAG-04 Synthetic Retrieval Benchmark v1

Frozen, versioned, human-authored benchmark for **retrieval-only** evaluation
(GraphRAG-04). Synthetic/fictional data only — no real entities, no internal data.

## Files
- `corpus.json` — 14 fictional canonical Sources (`sources[].{key,title,text}`).
- `queries.json` — 28 queries (`queries[].{query_id,query_class,split,text,relevant_source_keys,rationale}`).

## Design
- Two independent relation chains — **research** (Project Halcyon → Dr. Elena Voss →
  Meridian Institute → Novak Foundation) and **shipping** (Aurora Shipping → MV Cormorant →
  Captain Riko Tan) — that cross at the **Aurora-7 catalyst** and the city **Calderon**.
- Deliberate collisions: name `Voss` (Elena the chemist vs Marcus the botanist), term
  `Aurora` (Shipping vs Bakery vs catalyst), name `Halcyon` (Project vs Beach Resort).
- Pure distractors: S11 Aurora Bakery, S12 Marcus Voss, S13 Halcyon Resort.

## Query classes (6)
`direct` · `paraphrase` · `two_hop` · `three_hop` · `distractor` · `negative`
(≈ 5/5/5/4/5/4). Some relational queries have **multiple** relevant sources.

## Ground truth
Source-level, manually authored from the corpus **before** any retriever ran. Independent
of vector/graph output and of any generated answer. `negative` queries have an empty
`relevant_source_keys` (genuinely unanswerable — negative controls). Documents contain no
query ids, labels, or retrieval hints; `rationale` is review metadata, never indexed.

## DEV / HOLDOUT split
~61% DEV / ~39% HOLDOUT, every class represented in both. HOLDOUT is **frozen after
GraphRAG-04 approval** and must not be tuned on by any later phase.

## Isolation
The live harness creates each Source under a unique per-run id namespace
(`source:gr04eval<run><key>`) and the `topics` tag `__graphrag04_eval_v1__`, retains the exact
created id set, and only ever creates/indexes/deletes those ids — never a global sweep.
