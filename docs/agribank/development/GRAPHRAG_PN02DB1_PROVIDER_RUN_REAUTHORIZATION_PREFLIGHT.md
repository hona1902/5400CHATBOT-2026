# GraphRAG-PN02D-B1-R — Provider Run Reauthorization Preflight

**Status:** COMPLETE — technical/security/methodology preconditions satisfied.
**Decision:** **C — `B1_REAUTHORIZATION_OPERATOR_APPROVAL_JUSTIFIED`.**
**Provider run authorized:** **NO** (`PN02_PROVIDER_RUN_AUTHORIZED = NO`). This preflight
authorizes no traffic; it justifies requesting a *separate, explicit* operator
authorization for exactly one B1 run.

This was a **zero-provider** reauthorization preflight. No provider request, no
provider-bound LightRAG boot, no indexing, no embeddings, no `/query/data`, no live
vector query, no `client.query()`, no final answers, no B1 execution, no B2. Every
figure below was produced from git, from source reads, from the frozen fixture, and
from the committed offline simulation running against fakes under explicit
external-network denial.

---

## 1. Authoritative baseline (verified from git)

| Item | Expected | Observed | Verdict |
|---|---|---|---|
| Branch | `feature/graphrag-lifecycle` | `feature/graphrag-lifecycle` | ✅ |
| HEAD | `2e76cf5f1c80ed96940ffc3af84aafd631eb6c9c` | same | ✅ |
| Working tree | CLEAN | CLEAN | ✅ |
| B0B tag | `graphrag-pn02db0b-offline-live-driver-approved` (annotated) | annotated tag obj `523859e…` | ✅ |
| B0B tag peel | `fb21bc1d341beb4d82b57377ae387d588d59c6ae` | `fb21bc1…` | ✅ |
| B0A tag | `graphrag-pn02db0a-live-driver-design-approved` (annotated) | annotated | ✅ |
| B0A tag peel | `a2e83030be51f2a1ad1bd2516f8b2ac2e48a02b2` | `a2e8303…` | ✅ |
| PN02D-A tag peel | — | `8e0961792caa937c3cead33a8569b1e0c4dc79d7` | ✅ present |

**HEAD `2e76cf5` vs tagged B0B `fb21bc1`:** the only diff is
`docs/agribank/development/CURRENT_PHASE.md` (2 lines) — the docs-only tracker
reconciliation. **No implementation code changed after the reviewed checkpoint.**

- `POST_B0B_HEAD_ONLY_DOCS_RECONCILIATION = YES`
- `IMPLEMENTATION_TREE_UNCHANGED_SINCE_B0B = YES`
- `B0B_CHECKPOINT_PRESENT = YES`, `B0B_TAG_VALID = YES` (annotated, not moved)

## 2. Governance continuity (CURRENT_PHASE, unchanged)

PN02D-A = APPROVED · PN02D-B0A = APPROVED · PN02D-B0B = OFFLINE IMPLEMENTATION
APPROVED · PN02D-B1 = **historical BLOCKED attempt preserved** (not rewritten as a
scientific failure) · PN02D-B1-REAUTH = GATE JUSTIFIED / NOT_AUTHORIZED · PN02D-B2 =
NOT_AUTHORIZED · `PN02_PROVIDER_RUN = NOT_AUTHORIZED` ·
`GRAPHRAG_PRODUCTION_INTEGRATION = NOT_APPROVED` · `LIGHTRAG_ASK_INTEGRATION =
NOT_APPROVED` · `GRAPH_RAG_09_JUSTIFIED = NO`.

## 3. Frozen fixture

Loaded, validated, and canonical hash recomputed offline:

- `FIXTURE_VALIDATION = PASS`
- `FIXTURE_HASH_VALIDATION = PASS` — live hash
  `9ce7df742810424d9ef7b7b34962187d9061245f81471f5fb1d7f2608f6899a6` == frozen.
- Topology: notebooks 3 · canonical sources 21 · workspace memberships 24 · queries 24.
- `SYNTHETIC_ONLY = true`, `REAL_INTERNAL_DATA_ALLOWED = false` (Boundary B).

## 4. Authorization chain (verified from source)

`authlivepn02d.py`, `provbindpn02d.py`, `attestpn02d.py`:

```
RealLightRAGPreflightAuthorization  (attestpn02d; minted only on Gate0∧Gate1 PASS)
  → PN02ProviderRunAuthorization    (operator grant; mint requires the preflight cap
                                      + fixture-hash match + Boundary-B + provider-cfg
                                      fingerprint match + non-empty run_id)
  → provider-binding materialization (materialize_provider_binding: auth check is the
                                      FIRST statement — REJECTS before binding)
  → IndexingAuthorization           (minted after binding attestation PASS)
  → QueryAuthorization              (minted ONLY at 24/24 completeness)
  → provider operation              (operation allowlist enforced)
```

- Every capability uses a module-private `_AUTH_KEY` sentinel + `__slots__`; direct
  construction raises `PermissionError`. `require_*` guards use `isinstance` — a bare
  `True`/`None` cannot satisfy them. `BOOLEAN_ONLY_PROVIDER_AUTH_BYPASS = NO`.
- `PROVIDER_AUTHORIZATION_PRECEDES_BINDING = YES` (`provbindpn02d.py:101`,
  `provbindpn02d.py:170`).
- `AUTHORIZATION_CHAIN_IMPLEMENTED = YES`.

## 5. Operation allowlist (`authlivepn02d.B1_ALLOWED_OPERATION_CLASSES`)

Allowed: `GRAPH_INDEX`, `INDEX_REQUIRED_EMBEDDING`, `GD_QUERY_DATA`,
`VECTOR_QUERY_EMBEDDING`, `VECTOR_NOTEBOOK_QUERY`, `GRAPH_DELETE`.
Forbidden (structurally rejected): `CLIENT_QUERY`, `LIGHTRAG_FINAL_ANSWER`, `QA_V`,
`QA_GD`, `QA_V_GD`, `JUDGE_MODEL`, `PRODUCTION_ASK`. `B1_OPERATION_ALLOWLIST_VALID = PASS`.

## 6. Routing & document identity

- `routelivepn02d.PN02Router`: notebook → workspace → endpoint → storage, identity
  derived from the canonical notebook `record_id` (never a display title).
  `validate_route` rejects wrong endpoint / wrong workspace **before** any backend op;
  `resolve_membership_route` rejects a non-member `(source, notebook)` edge.
  `PER_NOTEBOOK_ROUTING_REAUTH = PASS`, `CROSS_NOTEBOOK_ROUTE_ALLOWED = NO`.
- `docidpn02d`: logical key = `(workspace_id, canonical_source_id)`;
  `ENDPOINT_IDENTITY_IS_DOCUMENT_IDENTITY = NO`; vendor id `doc-`+md5(canonical_source_id)
  is content-independent (identical across workspaces, safe only under isolation).
  `removallivepn02d` refuses a cross-workspace delete (`CrossWorkspaceDeleteRefused`).
  `SHARED_SOURCE_DELETE_ISOLATION_REAUTH = PASS`.

## 7. Exact notebook-local vector baseline (hard gate)

`vectorlivepn02d.NotebookLocalVectorExecutor`: the candidate universe is the queried
notebook's CURRENT member ids, resolved **before** ranking and handed to the backend as
`candidate_source_ids`; the backend never sees a non-member. A defense-in-depth drop
removes any non-member the backend might return (never a rescue-post-filter). One query
embedding; K=3/K=5 are slices of one ranking.

- `EXACT_NOTEBOOK_LOCAL_VECTOR_REAUTH = PASS`
- `GLOBAL_TOPK_THEN_POSTFILTER_PRESENT = NO`
- `VECTOR_K3_K5_ONE_RANKING = YES`
- Adversarial + question-collision protections covered by the B0B query suite
  (per-workspace member snapshots drive each ranking; results keyed per workspace, not
  by shared question text): `NOTEBOOK_LOCAL_VECTOR_ADVERSARIAL_TEST = PASS`,
  `PER_WORKSPACE_VECTOR_RESULT_ISOLATION = PASS`.

## 8. Workload ledger + stateful budget (pre-op enforcement)

`workloadpn02` constants → `budgetlivepn02d.b1_caps()`:

| Class | Cap |
|---|---|
| GRAPH_INDEX_OPERATION | 24 |
| GRAPH_INDEX_ATTEMPT (max) | 48 (24 × 2/op) |
| GRAPH_DELETE | 1 |
| GD_QUERY | 26 (24 + 2 removal) |
| VECTOR_QUERY | 26 |
| QUERY_EMBEDDING | 26 |
| FINAL_ANSWER | **0** |
| CLIENT_QUERY | **0** |
| JUDGE_MODEL | **0** |

`StatefulBudgetGuard.reserve` checks the *projected* (post-op) count against the frozen
`check_cap` primitive and only commits if it passes — the op that would exceed a cap
never mutates the counter and never dispatches. `WORKLOAD_LEDGER_REAUTH = PASS`,
`PRE_OPERATION_BUDGET_ENFORCEMENT = PASS`. (The ledger constant
`MAX_FINAL_ANSWER_CALLS = 72` is the Stage-2 total; B1 overrides it to 0.)

## 9. Dry-run plan (`driverpn02d.plan_b1`, no execution)

`provider_traffic = 0`; 24 index ops; shared-source routing SH_AB→{NB_A,NB_B},
SH_AC→{NB_A,NB_C}, SH_BC→{NB_B,NB_C}; GD 24 baseline + 2 reprobes; vector 24 + 2; delete
SH_AB at NB_A (1); caps as above; full authorization-requirements chain; 6 allowed op
classes. `B1_DRY_RUN_PLAN = PASS`, `SHARED_SOURCE_ROUTING_PLAN = PASS`.

## 10. Full offline simulation (external network denied)

`run_offline_b1_simulation` on a loop with non-loopback `connect()` forbidden:

- state `COMPLETE`, technical `COMPLETED`; 24/24 index complete.
- Budget spent: GRAPH_INDEX_OPERATION 24/24 · GRAPH_INDEX_ATTEMPT 24/48 · GD_QUERY 26/26
  · VECTOR_QUERY 26/26 · QUERY_EMBEDDING 26/26 · GRAPH_DELETE 1/1 · FINAL_ANSWER 0/0 ·
  CLIENT_QUERY 0/0 · JUDGE_MODEL 0/0.
- `provider_traffic = 0`; isolation evidenced; Stage-1 / R0-R4 / M0-M3 all reached
  (frozen `evaluatepn02` evaluator owns every verdict — driver reimplements none).
- Cleanup invoked; owned processes / storage residue / networks remaining = 0/0/0.
- Stale-evidence scenario (`delete_succeed=False`): `stale_graph_evidence_accepted_as_valid
  = 0`; NB_A drops SH_AB, NB_B retains it; both postconditions hold.

`FULL_OFFLINE_B1_SIMULATION = PASS`, `NETWORK_DENIAL_TEST = PASS`,
`INDEX_COMPLETENESS_GATE_REAUTH = PASS` (23/24 → `FAILED_BEFORE_QUERY`, 0 queries),
`STALE_EVIDENCE_DEFENSE_REAUTH = PASS`, `RESULT_NORMALIZATION_TO_PN02B = YES`.

## 11. Provider config identity & secret presence (name only)

- provider = OpenRouter (`https://openrouter.ai/api/v1`); LLM `openai/gpt-4o-mini`;
  embedding `openai/text-embedding-3-small`; embedding dim `1536`.
- Secret-free fingerprint `pbf_1811d0bfd5cfad2743ffaa69`
  (`PROVIDER_CONFIG_FINGERPRINT_SECRET_FREE = PASS`).
- `LLM_PROVIDER_CONFIG_PRESENT = YES`, `EMBEDDING_PROVIDER_CONFIG_PRESENT = YES` (frozen
  code constants).
- `OPENROUTER_KEY_PRESENT = YES` (present in `.env`; value never read/printed).
  `SIDE_CAR_AUTH_CONFIG_PRESENT = NOT_REQUIRED` (no `LIGHTRAG_API_KEY`; PN02D-A booted
  the sidecar without one). `LIGHTRAG_IMAGE_PIN = v1.5.6`, `LIGHTRAG_IMAGE_AVAILABLE =
  YES` (local image inspected, not pulled).

## 12. Single-run authorization semantics

`PN02ProviderRunAuthorization` binds: fixture hash, git baseline commit + tag, synthetic
flag, provider-config fingerprint, workload caps, allowed op classes, and `run_id`. Mint
fails closed on a fixture-hash mismatch, a Boundary-B violation, or a provider-cfg
mismatch; the driver also re-verifies the fixture hash at precheck. It is an in-memory,
unforgeable object (private sentinel; no serialized replayable token), minted fresh per
run. It therefore **cannot survive a changed fixture hash and cannot authorize B2** (QA
/ final-answer / judge are in the forbidden set, capped 0). `SINGLE_RUN_AUTHORIZATION_MODEL
= YES` (run-bound + fixture-bound). This requirement is materially present — not a
reauthorization blocker.

## 13. Concurrency & retry

The orchestrator runs index ops and queries **sequentially** (`for` loops; no fan-out):
`INDEX_CONCURRENCY = 1`, `GD_CONCURRENCY = 1`, `VECTOR_CONCURRENCY = 1`.
`UNBOUNDED_PROVIDER_CONCURRENCY = NO`. Index retry is bounded at 2 attempts/op (48 total)
and fires only on a *transient technical* reason via the frozen
`index_retry08.is_transient_reason` twin; submit acceptance is never treated as
completion. `SCIENTIFIC_RESULT_RETRY_ALLOWED = NO`.

## 14. Error taxonomy & cleanup

`outcomespn02d.DriverTechnicalOutcome` (FAILED_PRECHECK, FAILED_PROVIDER_AUTHORIZATION,
FAILED_PROVIDER_BINDING, FAILED_RUNTIME_ATTESTATION, FAILED_INDEX_SUBMIT,
FAILED_INDEX_COMPLETION, FAILED_INDEX_CAP, FAILED_BEFORE_QUERY, FAILED_GD_QUERY,
FAILED_VECTOR_QUERY, FAILED_MEMBERSHIP_REMOVAL, FAILED_STAGE1_ISOLATION, FAILED_CLEANUP,
COMPLETED) maps losslessly to the evaluator-boundary `schemaspn02.TechnicalOutcome`; a
technical failure never becomes a scientific `NO`. `TECHNICAL_FAILURE_DISTINCT_FROM_
SCIENTIFIC_NO = YES`. Cleanup runs in a `finally`, is idempotent, owns only run-created
resources, and swallows its own exceptions; the offline cleanup-path tests (partial
setup / index / GD / vector / removal failure / unexpected exception) pass.
`CLEANUP_OWNED_ONLY = PASS`.

## 15. Production import audit

No file under `open_notebook/` (non-eval), `api/`, or `commands/` imports any B0B eval
module — confirmed by the committed guard test and an independent grep.
`PRODUCTION_IMPORTS_EVAL = NO`.

## 16. Provider-zero attestation for this preflight

`EXTERNAL_PROVIDER_NETWORK_CALLS = 0` · `REAL_LIGHTRAG_BOOT_COUNT = 0` ·
`REAL_INDEX_OPERATIONS = 0` · `REAL_GD_QUERIES = 0` · `REAL_VECTOR_PROVIDER_QUERIES = 0`
· `REAL_FINAL_ANSWER_CALLS = 0` · `NORMAL_DB_MUTATIONS = 0`.

## 17. Tests

- Targeted B0B suite (9 files): **87 passed**.
- Full PN02 reauth set (B0B + pn02 offline + pn02d preflight + stage1): **140 passed**.
- Broader GraphRAG + PN02 regression: **954 passed / 9 skipped / 0 failed** — no new
  regressions vs the B0B checkpoint.

## 18. Independent review

An independent adversarial reviewer read all 17 `*pn02d.py` modules, the frozen
`attestpn02d` capability, the `membershippn02` evaluator, and the import surface, and
challenged the §42 A–T questions against source. **Result: 0 HIGH, 0 MEDIUM; all
expected-safe answers hold** (each cited to file:line):

- No provider op without a capability (`isinstance` gate, `authlivepn02d.py:272`); binding
  cannot precede auth (`provbindpn02d.py:101,170`); capability not directly constructible
  and fixture-pinned at mint (`authlivepn02d.py:135,176,233`; `allowed_operation_classes`
  forced to the B1 set at `:260`); vector restriction precedes ranking
  (`vectorlivepn02d.py:145,157`); 23/24 → `FAILED_BEFORE_QUERY`, zero queries
  (`authlivepn02d.py:381`, `driverpn02d.py:294`); budget checks projected count before
  dispatch (`budgetlivepn02d.py:110`); no path to `client.query`/final-answer/QA/judge
  (forbidden set + hard-0 caps; no `execute-b1` verb); stale-evidence stays 0 via the frozen
  evaluator's `post_validate` intersection (`membershippn02.py:91,103`); only secret env
  NAMES are ever emitted; nothing outside `eval/`/`tests/`/`docs/` imports the harness.

**Two LOW defense-in-depth observations (both fail-closed; not blockers, not exploitable
by any committed path):**

- **LOW-1:** the executors store `provider_run_auth` without an `isinstance` check in
  `__init__`, relying on the driver's prior `require_provider_run_authorization` and on the
  mint gating. A non-capability would fail (rejected at the forbidden-op check, else
  `AttributeError`) — fail-closed but caller-dependent rather than structural. A
  `require_provider_run_authorization(...)` in each executor `__init__` would make it
  structural. Recommended (non-blocking) hardening for the B1 wiring phase.
- **LOW-2:** `driver.run(run_id=...)` mints the downstream `Indexing`/`Query`
  authorizations with the call's `run_id` but does not compare it to
  `provider_run_auth.run_id`. Fixture-hash replay IS pinned at mint and the fixture is
  frozen, so there is no cross-fixture bypass; the `run_id` field is simply not enforced
  as a run-identity match. No security impact in this offline, fakes-only, zero-traffic
  harness. Recommended (non-blocking): enforce `run_id` equality when the future operator
  B1 authorization is wired, so the single-run binding is checked at each op, not only at
  mint.

`INDEPENDENT_REVIEW = PASS` — HIGH 0 / MEDIUM 0 / LOW 2 (both accepted as non-blocking
hardening notes for the future B1 wiring phase; neither weakens the B0B fail-closed
guarantees).

## 19. Decision

**C — `B1_REAUTHORIZATION_OPERATOR_APPROVAL_JUSTIFIED`.** All technical, security, and
methodology preconditions for a future single B1 provider-backed run are satisfied and
the reviewed implementation is unchanged since its approved checkpoint. **C does not mean
`PN02D_B1_PROVIDER_RUN_AUTHORIZED = YES`.** A separate, explicit operator authorization —
bound to one `run_id`, this fixture hash, the B0B checkpoint identity, the provider-config
fingerprint, the synthetic-only flag, and the workload caps — must be granted before any
provider traffic. This preflight minted no `PN02ProviderRunAuthorization` and executed no
B1/B2.

`PN02D_B1_REAUTHORIZATION_GATE = PASS` · `PN02D_B1_OPERATOR_AUTHORIZATION_JUSTIFIED = YES`
· `PN02D_B1_PROVIDER_RUN_AUTHORIZED = NO` · `PN02_PROVIDER_RUN_AUTHORIZED = NO` ·
`PN02D_B2_QA_AUTHORIZED = NO` · `GRAPHRAG_PRODUCTION_INTEGRATION = NOT_APPROVED` ·
`LIGHTRAG_ASK_INTEGRATION = NOT_APPROVED` · `GRAPH_RAG_09_JUSTIFIED = NO`.
