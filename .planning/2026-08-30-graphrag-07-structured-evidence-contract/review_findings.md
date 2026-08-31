# GraphRAG-07 — Independent Contract Review (2026-08-30)

Independent adversarial review of the two GraphRAG-07 docs by (a) a fresh self-review pass and
(b) an independent Codex review (subagent, read-only). All findings are **documentation-only**;
none is a safety/security defect, none changes retrieval, and none flips
`STRUCTURED_EVIDENCE_IMPLEMENTATION_READY` (stays **NO**). Every finding is dispositioned with a
documentation-only fix. `D` = authoritative `GRAPHRAG_07_STRUCTURED_EVIDENCE_CONTRACT.md`;
`A` = companion `GRAPHRAG_07_STRUCTURED_EVIDENCE_ADAPTER_CONTRACT.md`.

## HIGH (all: cross-document contradiction on a not-yet-built contract — maintainer-clarity, not safety)
| ID | Finding | Disposition (documentation fix) |
|---|---|---|
| H-1 | `supporting_chunk_count` REQUIRED (D) vs OPTIONAL/≥0 (A) | FIXED — D §2a freezes REQUIRED-and-≥1 *when present*; *inclusion* is the open minimality question. A sketch line + banner corrected. |
| H-2 | diagnostics type `GraphEvidenceDiagnostics` (D) vs `EvidenceDiagnostics` (A) | FIXED — D §2a authoritative name; A banner supersedes. |
| H-3 | diagnostic field names diverge (`canonical_/valid_source_count`, `unknown_source_/unknown_provenance_count`, `duplicate_reference_/deduplicated_count`) | FIXED — D §2a authoritative list; A banner supersedes. |
| H-4 | `final_answer_generation=False` (D) vs `final_answer_call_skipped=True` (A) | FIXED — D §2a canonical name/polarity; A banner. |
| H-5 | CANCELLED: propagate `CancelledError` unwrapped (D) vs map to `GraphRAGUnavailableError` (A) | FIXED — D correct; A §16 row corrected inline + banner. Timeout still → Unavailable. |
| H-6 | `ProvenanceQuality` 5 states (D) vs 3 states (A) | FIXED — D §2a 5-state authoritative; INVALID/FOREIGN are internal classification states; emitted field only ever STRONG. A banner. |
| H-7 | A's `GraphEvidenceResult` sketch omits `status` | FIXED — A banner adds forward-ref to D §2/§5. |

## MEDIUM
| ID | Finding | Disposition |
|---|---|---|
| M-1 | stale/deleted-Source consumer drop is advisory, not a hard requirement | FIXED — D §7.5 adds HARD `STALE_SOURCE_CONSUMER_LOOKUP_REQUIRED=YES` (citation/consumer layer); query path stays lookup-free & mutation-free. New decision flag added. |
| M-2 | `evidence_types` set-size not prohibited as a rank proxy | FIXED — D §3 adds explicit no-rank-on-cardinality clause; joins the §7.7 prohibition list. |
| M-3 | `query_mode` raw vendor string crosses into ON type un-normalized | FIXED — D §2a documents it as an intentional opaque observability label; future ON-owned mode enum + UNKNOWN fallback if constrained. |
| M-4 | DEGRADED / `EvidenceStatus` not defined in A | FIXED — A banner + authoritative §5 reference (via §2a). |
| M-5 | A §9 marks `supporting_chunk_count` "weakly" comparable across queries (implicit-rank) | FIXED — A cell changed to "no — query-specific & corpus-dependent; cross-query comparison invalid". |

## LOW
| ID | Finding | Disposition |
|---|---|---|
| L-1 | `provenance_quality` is a constant (always STRONG); audit justification circular | FIXED — D §2a documents it as a runtime-checkable invariant assertion + removal candidate if STRONG-only emission is never relaxed. |
| L-2 | "supporting" name carries relevance connotation | FIXED — D §2a naming note (structural sense; rename deferred to avoid churn; updates both docs together). |

## Doc-hierarchy fix (task §1)
- A now carries a top-of-file **SUBORDINATE / HISTORICAL** banner pointing to D as authoritative and
  listing every superseded decision; D carries the reconciliation table (§2a). Forensic history in A
  is preserved (not deleted). A future maintainer can now tell which doc governs and reconcile all
  seven contradictions without judgement calls.

## Review verdict
- CONTRACT_AND_SAFETY: frozen and consistent after fixes.
- VALUE_EVIDENCE: still not proven (`HYBRID_VALUE_EVIDENCED=INCONCLUSIVE` 04;
  `SOURCE_LEVEL_AGGREGATION_DEFENSIBLE=REQUIRES_EXPERIMENT` 05).
- `STRUCTURED_EVIDENCE_IMPLEMENTATION_READY = NO` (unchanged; contract readiness ≠ value proven).
- Next unresolved gate: **LARGER-CORPUS STRUCTURED-EVIDENCE VALUE EVALUATION** — must run on existing
  currently-wired query surfaces (must NOT require building the adapter to justify building it), and
  must precede any adapter-implementation authorization. Not designed here (GraphRAG-08 not started).
