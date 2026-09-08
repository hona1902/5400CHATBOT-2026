# GraphRAG-PN02D-B0B — Offline Per-Notebook Live-Driver Implementation

**Status: `OFFLINE IMPLEMENTATION COMPLETE / CANDIDATE REVIEW` — the frozen B0A live-driver
architecture is implemented as eval-only code with ZERO provider traffic. No real LightRAG
boot, no indexing, no embedding, no `/query/data`, no `client.query`, no vector-provider call,
no final answers, no B1 execution, no B2, no production Ask, no GraphRAG-09. NOT checkpointed —
operator review required.**

This phase implements the design frozen in
[`GRAPHRAG_PN02DB0A_PER_NOTEBOOK_LIVE_DRIVER_DESIGN.md`](GRAPHRAG_PN02DB0A_PER_NOTEBOOK_LIVE_DRIVER_DESIGN.md).
Every layer runs offline behind injected seams; a future re-authorized B1 supplies real
backends for the SAME injected Protocols with no new driver code.

---

## 0. Authoritative baseline (verified from git)

| Item | Expected | Result |
|---|---|---|
| Branch | `feature/graphrag-lifecycle` | ✅ |
| HEAD (authoritative baseline) | `6a4cf89eb77de6d73cd6d97a29853e372990b85d` | ✅ |
| Design freeze commit | `a2e83030be51f2a1ad1bd2516f8b2ac2e48a02b2` | ✅ |
| Design tag | `graphrag-pn02db0a-live-driver-design-approved` | ✅ present, annotated |
| Design tag peel | `a2e83030be51f2a1ad1bd2516f8b2ac2e48a02b2` | ✅ |
| Working tree at start | CLEAN | ✅ |
| Fixture | `graphrag_pn02_eval_v1` | ✅ hash `9ce7df74…6899a6` verified live |

Retained governance (unchanged):

```
GraphRAG-08                     = CLOSED / APPROVED
PN01/PN02A/PN02B/PN02C/PN02D-A  = APPROVED
PN02D-B1                        = BLOCKED — LIVE DRIVER ABSENT (awaiting approved B0B wiring)
PN02D-B0A                       = APPROVED (design frozen)
PN02D-B0B                       = OFFLINE IMPLEMENTATION COMPLETE / CANDIDATE REVIEW (this doc)
PN02D-B1-REAUTH                 = NOT_AUTHORIZED
PN02D-B2                        = NOT_AUTHORIZED
PN02_PROVIDER_RUN               = NOT_AUTHORIZED
GRAPHRAG_PRODUCTION_INTEGRATION = NOT_APPROVED
LIGHTRAG_ASK_INTEGRATION        = NOT_APPROVED
GRAPH_RAG_09_JUSTIFIED          = NO
```

## 1. Implemented module architecture (eval-only)

All new code is under `open_notebook/integrations/graphrag/eval/` (`PRODUCTION_IMPORTS_EVAL =
NO`; dependency direction is eval → production only). Layers map to design §5 A–J.

| Module | Layer | Responsibility |
|---|---|---|
| `docidpn02d.py` | identity | derived doc id `doc-+md5(source_id)`; `(workspace_id, canonical_source_id)` logical key; run-owned `DerivedDocMappingStore`; endpoint-not-identity |
| `budgetlivepn02d.py` | G | `StatefulBudgetGuard` wrapping `workloadpn02.check_cap`; reserve-before-op; B1 final-answer/client-query/judge caps = 0 |
| `authlivepn02d.py` | auth | `ProviderOperationClass` allowlist; `PN02ProviderRunAuthorization` + `IndexingAuthorization` + `QueryAuthorization` (unforgeable, `_AUTH_KEY`) |
| `provbindpn02d.py` | B | auth-gated `materialize_provider_binding` (frozen OpenRouter binding, secret-safe); `plan_sidecar_boot` |
| `routelivepn02d.py` | A | `PN02Router`: notebook→workspace→endpoint→storage; wrong-route/unauthorized-membership rejection |
| `outcomespn02d.py` | taxonomy | `DriverTechnicalOutcome` + lossless map to `schemaspn02.TechnicalOutcome` |
| `indexlivepn02d.py` | C | `MembershipIndexExecutor`: submit→poll→terminal, bounded retry, `IndexOperationRecord`, 24/24 gate |
| `gdlivepn02d.py` | D | `GDQueryExecutor`: attestation+query-auth gated `/query/data`; `normalize_graph` |
| `vectorlivepn02d.py` | E | `NotebookLocalVectorExecutor`: candidate restriction BEFORE ranking; one embedding → K3/K5 slices |
| `removallivepn02d.py` | F | `MembershipRemovalExecutor`: endpoint-scoped delete; delete-failure backstop |
| `fakeslivepn02d.py` | fakes | index/GD/vector/delete fakes + per-workspace graph topology |
| `driverpn02d.py` | I | `B1OfflineDriver` orchestrator; `plan_b1` dry-run; `run_offline_b1_simulation` |
| `liveclipn02d.py` | CLI | `validate-live-driver` / `dry-run-b1-plan` / `run-offline-b1-simulation` (NO `execute-b1`) |

## 2. Authorization chain (fail-closed, design §6)

```
RealLightRAGPreflightAuthorization   (PN02D-A; minted on Gate0∧Gate1 PASS)
  → PN02ProviderRunAuthorization     (mint verifies real-preflight + fixture hash + Boundary B + provider-config id)
  → provider-binding materialization (unreachable before PN02ProviderRunAuthorization)
  → IndexingAuthorization            (minted after binding attestation PASS)
  → QueryAuthorization               (minted ONLY at 24/24 index completeness)
  → provider-backed operation        (operation allowlist enforced)
```

Each capability is an unforgeable object (module-private `_AUTH_KEY`, `__slots__`, ctor raises
`PermissionError` on any other key), minted only inside its verifier, carrying `fixture_hash` +
`run_id`. B0B never mints a *real* provider run: `PN02_PROVIDER_RUN_AUTHORIZED = False`, and the
offline simulation drives fakes through a controlled test/CLI factory
(`build_simulation_provider_run_authorization`, which obtains the preflight capability from the
frozen PN02D-A mint with simulated gate booleans — no separate backdoor).

**`PROVIDER_AUTHORIZATION_PRECEDES_BINDING = YES`:** `materialize_provider_binding` and
`plan_sidecar_boot` both call `require_provider_run_authorization` first — binding is structurally
unreachable without the capability.

## 3. Routing & document identity (design §11–§18)

- `PN02Router.validate_route` rejects a wrong endpoint (`WrongEndpointRoutingError`) or wrong
  workspace (`WrongWorkspaceRoutingError`) before any backend op. `resolve_membership_route`
  refuses a `(source_key, notebook_id)` that is not a fixture membership edge
  (`UnauthorizedMembershipError`).
- Workspace id = `"nb_"+sha256(record_id)[:16]` — never the display theme.
- `ENDPOINT_IDENTITY_IS_DOCUMENT_IDENTITY = NO`. Authoritative key = `(workspace_id,
  canonical_source_id)`. Vendor id = `doc-+md5(source_id)` (content-independent). Shared `SH_AB`
  yields two distinct logical rows (A, B) with the SAME vendor id — safe only under workspace
  isolation.

## 4. Provider binding model (design §9, secret-safe)

Frozen OpenRouter binding (`provider_binding08.frozen_provider_binding`): LLM
`openai/gpt-4o-mini`, embedding `openai/text-embedding-3-small` (dim 1536), host
`https://openrouter.ai/api/v1`, secret env NAME `OPENROUTER_API_KEY`. Public config on argv;
secret by env-inheritance name-only map. Materialization records only names + a config
fingerprint; a required-secret name-presence check yields content-safe
`REQUIRED_RUNTIME_SECRET_MISSING=<name>`. **No secret value is ever read, hashed, logged, or
serialized.** B0B injects a *simulated* presence set, so it depends on no ambient `.env`.

## 5. Indexing, completeness, retries (design §12–§17)

`MembershipIndexExecutor` submits one op per membership, polls the track-status surface for a
terminal state (submit acceptance ≠ completion), and retries only frozen transient classes
(`index_retry08.is_transient_reason` decision-twin), capped at
`MAX_INDEX_ATTEMPTS_PER_OPERATION = 2`. The stateful budget guard is reserved BEFORE every
attempt; cap exhaustion (`GRAPH_INDEX_ATTEMPT` = 48) yields `FAILED_INDEX_CAP` with zero backend
calls. `QueryAuthorization` is minted only at 24/24; 23/24 blocks all GD/vector queries
(`FAILED_BEFORE_QUERY`).

## 6. GD adapter & vector exact semantics (design §18–§23)

- **GD:** attestation-gated per-notebook `/query/data`; candidate ids normalized via
  `normalize_graph(allowlist=fx.source_keys)` to an UNORDERED set; foreign/malformed dropped +
  counted (feed the Stage-1 exact-`=0` gate). No `client.query`/final answer.
- **Vector (net-new):** `PN02_VECTOR_BASELINE_CANDIDATE_UNIVERSE =
  CURRENT_SOURCES_OF_QUERIED_NOTEBOOK_ONLY`. The executor resolves current member ids and hands
  ONLY those to the backend for scoring, so a global-top-K-then-post-filter design is
  structurally impossible (`GLOBAL_TOPK_THEN_POSTFILTER_PRESENT = NO`). One embedding →
  one ranked list → K=3/K=5 are slices (`VECTOR_K3_K5_ONE_RANKING = YES`).

## 7. Membership removal (design §28–§30)

`MembershipRemovalExecutor` deletes the derived doc at NB_A's endpoint only. A delete for
`(workspace_A, SH_AB)` dispatched to NB_B is refused (`CrossWorkspaceDeleteRefused`). Even a
FAILED delete is safe: the ON `post_validate` backstop drops the stale `SH_AB` so
`STALE_GRAPH_EVIDENCE_ACCEPTED_AS_VALID = 0` (verified with `delete_succeed=False`). Vector
re-probes recompute the candidate universe from the post-removal snapshot BEFORE ranking, so
`SH_AB` is excluded from NB_A and retained in NB_B.

## 8. Workload guard (design §26/§58)

Frozen B1 caps enforced pre-op: graph-index ops 24, index attempts 48, GD 26, vector 26, query
embeddings 26, graph delete 1, final answers 0, `client.query` 0, judge 0. A full clean
simulation spends exactly 24 / 24 / 26 / 26 / 26 / 1 / 0 / 0 / 0.

## 9. Offline orchestrator, dry-run, network denial

`B1OfflineDriver.run` executes the frozen order (design §40) against injected fakes and hands
normalized `schemaspn02` records to `evaluatepn02.run_offline_evaluation` — it reimplements NO
scientific logic (`DUPLICATE_SCIENTIFIC_LOGIC = NO`). `plan_b1` serializes the full content-safe
plan without executing. The full simulation runs to completion under explicit socket denial
(`test_network_denial_full_run`), proving zero network.

## 10. Result normalization → PN02B (design §42)

The driver emits `VectorEvidenceResult` / `GDEvidenceResult` / `RemovalProbeResult` and calls
the frozen evaluator, reaching Stage-1 → R0-R4 / M0-M3. The clean simulation yields
`ISOLATION = YES`, `RETRIEVAL = R1/NO`, `MULTIHOP = M2/NO` (verdicts are the evaluator's; the
fakes carry no science).

## 11. Tests

`tests/test_graphrag_pn02db0b_*.py` (+ `graphrag_pn02db0b_common.py` helper): **87 tests, all
pass.** Coverage: authorization missing / provider-authorization missing / wrong fixture hash /
wrong git baseline / wrong route / wrong workspace / unauthorized membership / shared-source
isolation / 24/24 completeness / 23/24 query block / index retry & attempt cap / GD cap / vector
cap / embedding cap / `client.query` & final-answer forbidden / operation allowlist / global-top-K
post-filter forbidden / one ranking → K3/K5 / foreign & malformed provenance / removed membership /
delete-failure stale-evidence rejection / cross-workspace delete refusal / cleanup on failure /
secret-safe artifacts / network denial / provider-zero full offline run / determinism /
lossless outcome mapping / no-production-import.

## 12. Independent review

A second-pass adversarial review (challenges A–T of the phase spec) was performed against source
and tests. All HIGH/MEDIUM findings resolved (see the final report). Notable in-pass fix: the
clean-topology fake keyed vector rankings by question, but fixture questions repeat across
parallel notebooks (18 unique of 24) — fixed to per-workspace backends so no notebook's ranking
bleeds into another.

## 13. Limitations & posture

- B0B is offline: `B0B_EXTERNAL_PROVIDER_NETWORK_CALLS = 0`, `B0B_REAL_LIGHTRAG_BOOT_COUNT = 0`,
  real index/GD/vector/final-answer = 0, `NORMAL_DB_MUTATIONS = 0`.
- The GD backend is `question`-keyed at the fake level; the real B1 backend is per-endpoint
  (`gd_seam.GDQueryClient`) and needs only a thin adapter exposing raw candidate ids.
- B1 remains **UNAUTHORIZED**. A separate operator prompt is required (design §48). No checkpoint
  is made in this phase.

## 14. Verification evidence

- Targeted: 87 B0B tests pass.
- Regression: PN02B/C/D-A + broader GraphRAG suite (flag off) — no new failures (see report).
- `ruff check` clean on all new files; `mypy` clean on all 13 B0B modules (the only mypy errors in
  the eval import graph are the 5 pre-existing `object`-attr errors in `concurrency_diag08.py`,
  documented in the 08E.2 baseline — untouched here).
