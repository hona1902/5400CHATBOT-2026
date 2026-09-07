# GraphRAG-PN02C — Synthetic Per-Notebook Live Preflight, Workspace Provisioning & Routing Attestation Gate

**Status:** ✅ ROUTING / ATTESTATION PREFLIGHT APPROVED — checkpointed at tag `graphrag-pn02c-routing-preflight-approved`; **NOT** the live benchmark; real LightRAG boot NOT_YET_ATTESTED.
**Baseline:** HEAD `8eff12849b7badc00359cbf414f59c011594d145` · branch `feature/graphrag-lifecycle` · tag `graphrag-pn02b-offline-harness-approved` (peel `8eff128`) · working tree CLEAN.
**Decision:** `PN02C_DECISION = C — PN02C_PREFLIGHT_PASSED_AND_LIVE_AUTHORIZATION_GATE_JUSTIFIED`.
**`PN02_LIVE_AUTHORIZED = NO`** (C proposes a *separate* operator gate; it authorizes nothing).

This is an **internal Agribank-fork** document. It adds no upstream behavior and no production
runtime imports any PN02C code.

---

## 1. Scientific role (what PN02C answers, and what it does NOT)

PN02C answers exactly one question:

> Can the approved per-notebook topology be **provisioned and attested safely with ZERO provider
> traffic** before any provider-backed evaluation?

It does **not** answer whether GraphRAG retrieves well, improves QA, or has multi-hop value. Those
remain future PN02-live questions. Accordingly, all four scientific verdicts stay:

| Verdict | Value |
|---|---|
| `PER_NOTEBOOK_ISOLATION_EVIDENCED` | NOT_YET |
| `PER_NOTEBOOK_GRAPH_RETRIEVAL_VALUE_EVIDENCED` | NOT_YET |
| `PER_NOTEBOOK_GRAPH_QA_VALUE_EVIDENCED` | NOT_YET |
| `PER_NOTEBOOK_MULTIHOP_INCREMENTAL_VALUE_EVIDENCED` | NOT_YET |

**Infrastructure attestation is not equivalent to evidence-retrieval isolation under indexed data.**

---

## 2. Fixture and hash (revalidated, unchanged)

| Field | Value |
|---|---|
| `PN02_FIXTURE_NAME` | `graphrag_pn02_eval_v1` |
| `PN02_FIXTURE_HASH` | `9ce7df742810424d9ef7b7b34962187d9061245f81471f5fb1d7f2608f6899a6` |
| `FIXTURE_VALIDATION` | PASS |
| `FIXTURE_HASH_VALIDATION` | PASS |
| Counts | 3 notebooks · 21 canonical sources · 24 membership edges · 24 queries |

The frozen fixture was **not** modified. Any content change would require `graphrag_pn02_eval_v2`.

---

## 3. Runtime model — design decision (disclosed limitation)

The real LightRAG runtime in this programme is a **Docker sidecar image `v1.5.6`**, not a
pip-installable package (`import lightrag` is unavailable here). A real LightRAG *server* startup's
provider-safety is **not guaranteed** — the entire GraphRAG-08E history is about how fraught real
sidecar startup and provider binding are. Booting it during a preflight therefore risks provider
traffic (task §5) for **zero preflight value**.

**Decision:** PN02C does **not** boot the real LightRAG retrieval sidecar. It provisions **real,
owned local resources** and attests them provider-free. The three runtimes are **attestation
micro-runtimes**, NOT LightRAG runtimes — they exist only to prove routing / ownership / attestation:

- **Endpoints** — a distinct real loopback endpoint per notebook (`http://127.0.0.1:<port>`) bound
  by an owned stdlib `ThreadingHTTPServer` **attestation micro-runtime** that serves ONLY
  `GET /health` and `GET /identity` and returns 404 for everything else (including `/query/data`
  and `/documents`). It imports no provider SDK and makes no outbound call, so provider traffic is 0
  by construction.
- **Storage** — a distinct run-owned storage root per notebook (`mkdtemp`, forbidden-root guard).
- **Attestation runtimes** — a distinct owned attestation micro-runtime per notebook, one-to-one
  with its workspace. These are routing/ownership/attestation test runtimes, **not** LightRAG
  processes.

**Runtime terminology (frozen — do not overstate):**

| Field | Value |
|---|---|
| `ATTESTATION_RUNTIME_COUNT` | 3 |
| `REAL_LIGHTRAG_RUNTIME_COUNT` | 0 |
| `REAL_LIGHTRAG_RUNTIME_BOOTED` | NO |
| `REAL_LIGHTRAG_RUNTIME_BOOT_PROVEN_PROVIDER_FREE` | NO |

**Version attestation is a CONFIG-PIN attestation, NOT a runtime attestation:** the attestation
micro-runtime reports, and PN02C attests, the **configured pin `v1.5.6`**
(`config.py:VERIFIED_LIGHTRAG_VERSION`), tolerating the historical `v`-prefix form. No real LightRAG
engine version was observed. A **live LightRAG engine version probe is explicitly deferred** to the
future live gate, which must boot the real sidecar (provider-free) as its first step and attest the
version from the running engine.

| Field | Value |
|---|---|
| `LIGHTRAG_VERSION_EXPECTED` | v1.5.6 |
| `LIGHTRAG_CONFIG_VERSION_PIN_MATCH` | PASS |
| `REAL_LIGHTRAG_RUNTIME_VERSION_ATTESTED` | NO |
| `REAL_LIGHTRAG_RUNTIME_VERSION` | NOT_PROBED |
| `REAL_LIGHTRAG_PROVIDER_FREE_BOOT_PROVEN` | NO |

`LIGHTRAG_RUNTIME_VERSION_MATCH` is **not** claimed and must not be claimed until an actual LightRAG
engine is booted and queried through a provider-free version/config seam. PN02C did **not** boot the
real Docker LightRAG `v1.5.6` sidecar because GraphRAG-08E history did not establish that its startup
is guaranteed provider-free — this is a **safety decision, not a PN02C failure**.

### 3a. Precise scope — what PN02C proved and did NOT prove

**PN02C proved:** routing machinery · workspace identity mapping · endpoint ownership · storage
ownership · wrong-route rejection · unattested-route rejection · collision detection · fixture/hash
continuity · workload preflight · cleanup · the provider-zero behavior of the PN02C attestation
machinery.

**PN02C did NOT prove:** real LightRAG process startup · real LightRAG workspace loading · real
LightRAG runtime version · real LightRAG storage-backend initialization · real LightRAG
provider-free startup · real `/query/data` behavior · real graph isolation after indexing.

---

## 4. Attestation topology proven (executed run `pn02c-live-1`)

Each row is an **attestation micro-runtime** (not a LightRAG runtime) and its owned resources:

| Notebook | Record id | Workspace id | Attestation endpoint (real, owned) | Storage identity |
|---|---|---|---|---|
| NB_A | `notebook:gr_pn02_a` | `nb_2801282a94466d17` | `http://127.0.0.1:63009` | `st_4a30f52c8e37434d` |
| NB_B | `notebook:gr_pn02_b` | `nb_ca49abb97532ce9a` | `http://127.0.0.1:63011` | `st_f817925d052b7311` |
| NB_C | `notebook:gr_pn02_c` | `nb_45f0f09aff5bb661` | `http://127.0.0.1:63013` | `st_4a346f1c976f5dc3` |

Workspace ids are opaque, deterministic `"nb_" + sha256(record_id)[:16]` — derived from the canonical
record id, **never** from a mutable notebook title. Endpoints/ports are freshly allocated per run;
storage identities are `"st_" + sha256(realpath)[:16]`. `ATTESTATION_RUNTIME_COUNT = WORKSPACE_COUNT =
ENDPOINT_COUNT = STORAGE_ROOT_COUNT = 3`, all distinct, one-to-one; `REAL_LIGHTRAG_RUNTIME_COUNT = 0`.

---

## 5. Attestation gate (fail-closed)

Each **attestation micro-runtime** is attested by reading its provider-free loopback `/identity` and
comparing every field to the frozen expectation. `attested` is True only when workspace id, endpoint
ownership, storage identity, and **configured-version pin** all match, synthetic-only holds, and
provider-backed execution is NOT authorized. This attests ownership/routing/config-pin — **not** a
real LightRAG engine.

| Gate | Result |
|---|---|
| `WORKSPACE_A_ATTESTED` | PASS |
| `WORKSPACE_B_ATTESTED` | PASS |
| `WORKSPACE_C_ATTESTED` | PASS |
| `ALL_WORKSPACES_ATTESTED` | PASS |

An unattested workspace is **not** query-authorized (reuses PN02B `require_attested_before_query`).

---

## 6. Routing negative tests (infrastructure-level, no graph query)

| Test | Result |
|---|---|
| `CORRECT_ROUTE_ACCEPTED` (NB_A → its own endpoint+workspace) | PASS |
| `WRONG_ENDPOINT_ROUTING_REJECTED` (NB_A → NB_B endpoint) | PASS |
| `WRONG_WORKSPACE_ROUTING_REJECTED` (NB_B → NB_C workspace) | PASS |
| `UNATTESTED_QUERY_AUTHORIZATION_REJECTED` (false attestation → refused before any GD) | PASS |
| `CROSS_WORKSPACE_STORAGE_ALIASING` | 0 |
| `ENDPOINT_COLLISION_CHECK` | PASS |
| `STORAGE_COLLISION_CHECK` | PASS |

None of these invoke retrieval, `/query/data`, or any provider seam. The GD seam remains the inert,
non-invocable PN02B stub; `authorize_future_gd` only proves the authorization *gate* refuses an
unattested or wrongly-routed request.

---

## 7. Workload preflight & provisioning plan (validated, NOT consumed)

Future caps (from the PN02B ledger) were validated and left unconsumed:

`PLANNED_GRAPH_INDEX_OPERATIONS=24` · `MAX_GRAPH_INDEX_ATTEMPTS=48` · `GRAPH_DELETE_OPERATIONS=1` ·
`MAX_GD_QUERIES=26` · `MAX_VECTOR_QUERY_OPERATIONS=26` · `MAX_QUERY_EMBEDDING_OPERATIONS=26` ·
`MAX_FINAL_ANSWER_CALLS=72` · `JUDGE_MODEL_CALLS=0` → `WORKLOAD_LEDGER_PREFLIGHT = PASS`.

Provisioning plan (computed, not executed): NB_A=8, NB_B=8, NB_C=8 → **24** graph index ops.
`SHARED_SOURCE_WORKSPACE_PLAN = PASS` — SH_AB→{NB_A,NB_B}, SH_AC→{NB_A,NB_C}, SH_BC→{NB_B,NB_C}
(each shared source into exactly its two workspaces, never the third).
Membership-removal plan (frozen, not executed): `REFERENCE_EDGE_DELETE_OPERATIONS=1`,
`GRAPH_DELETE_OPERATIONS=1`, `POST_REMOVAL_GD_QUERIES=2`, `POST_REMOVAL_VECTOR_QUERIES=2`,
`POST_REMOVAL_FINAL_ANSWER_CALLS=0` (SH_AB removed from NB_A, retained in NB_B).

---

## 8. Provider-traffic guard & secret handling

`PROVIDER_TRAFFIC = 0`. Every actual consumed counter is 0:

`ACTUAL_GRAPH_INDEX_OPERATIONS=0` · `ACTUAL_GRAPH_INDEX_ATTEMPTS=0` · `ACTUAL_GD_QUERIES=0` ·
`ACTUAL_VECTOR_QUERY_OPERATIONS=0` · `ACTUAL_QUERY_EMBEDDING_OPERATIONS=0` ·
`ACTUAL_FINAL_ANSWER_CALLS=0` · `JUDGE_MODEL_CALLS=0` · `QUERY_DATA_CALLS=0` ·
`CLIENT_QUERY_CALLS=0` · `DOCUMENT_INSERT_CALLS=0` · `EXTERNAL_PROVIDER_NETWORK_CALLS=0` ·
`NORMAL_DB_MUTATIONS=0`.

- No provider client is ever constructed. A `ProviderTrafficGuard` counter would trip (raise) if any
  provider seam were invoked during preflight; it is asserted to be 0 at the end.
- The only network op PN02C performs is a stdlib loopback `GET /identity` (guarded to `127.0.0.1`).
- `PROVIDER_BINDING_CONFIGURED` is reported by the env var **NAME** (`OPENROUTER_API_KEY`) only —
  its value is never read into any artifact, log, report, manifest, or fingerprint. Absence of the
  key is not a PN02C failure (preflight is provider-free); it is reported as `NO`.
- Provider **model configuration** is attested by name without any call: LLM `openai/gpt-4o-mini`,
  embedding `openai/text-embedding-3-small`, embedding dim `1536`.
- A test sets a fake `OPENROUTER_API_KEY` and asserts no secret substring appears anywhere in the
  serialized report; the config fingerprint is a SHA-256 over a secret-free identity string.

---

## 9. Cleanup

`PN02C_OWNED_PROCESSES_REMAINING = 0` · `PN02C_RUNTIME_STORAGE_RESIDUE = 0`.

Every owned micro-runtime is shut down and joined; every run-owned storage root is removed. Normal
Open Notebook services, SurrealDB, unrelated LightRAG processes, and other developer processes are
untouched. The preflight was run twice with fresh run identities (reproducibility), both clean.

---

## 10. Code, tests, isolation

Three new **eval-only** modules under `open_notebook/integrations/graphrag/eval/` (never imported by
production; verified by a source guard):

- `provisionpn02c.py` — real loopback endpoint + run-owned storage + provider-free micro-runtime
  provisioning; collision checks; frozen A→B→C startup with per-step verification and
  partial-startup rollback; cleanup.
- `routingpn02c.py` — real-endpoint attestation (reuses PN02B `WorkspaceAttestation` /
  `require_attested_before_query` / `provider_binding_fingerprint`); fail-closed router;
  provider-config attestation by name; cross-workspace storage-aliasing check; config fingerprint.
- `preflightpn02c.py` — orchestrator (precheck → plans → provision → attest → routing negatives →
  reports → cleanup → decision), content-safe manifest/attestation/routing/workload reports, failure
  taxonomy, and an eval-only preflight CLI
  (`python -m open_notebook.integrations.graphrag.eval.preflightpn02c`).

**Tests:** 28 PN02C tests pass (`tests/test_graphrag_pn02c_preflight.py`). GraphRAG regression
(flag off) **832 pass / 9 skip / 0 fail**. `ruff` + `mypy` clean on all three modules.
`NORMAL_DB_MUTATIONS = 0`; `PRODUCTION_IMPORTS_EVAL = NO`.

---

## 11. Failure taxonomy (technical) vs decision (governance)

Technical outcomes are kept separate from any scientific verdict: `FAILED_PRECHECK`,
`FAILED_FIXTURE_HASH`, `FAILED_ENDPOINT_ALLOCATION`, `FAILED_STORAGE_ISOLATION`,
`FAILED_SIDECAR_START`, `FAILED_VERSION_ATTESTATION`, `FAILED_WORKSPACE_ATTESTATION`,
`FAILED_ROUTING_ATTESTATION`, `FAILED_PROVIDER_TRAFFIC_GUARD`, `FAILED_CLEANUP`, `COMPLETED`.
This run: **`COMPLETED`**. PN02C contains no scientific retrieval/QA YES/NO result.

---

## 11a. Independent review (§55 A–O)

An independent adversarial reviewer verified every A–O question against source (not this summary),
the imported fail-closed primitives, and the repo-wide import graph. **Verdict: PASS on all A–O** —
the zero-provider-traffic, isolation, attestation-before-authorization, secret-safety, and
no-production-import guarantees all hold.

- **MEDIUM (resolved):** on a verification error path (a reachable `fetch_identity()` loopback
  failure), the in-flight runtime's socket/thread + `mkdtemp` storage were created but not yet
  registered into the topology dicts, so `_rollback()` missed them → a leaked process + temp dir,
  defeating the 0-residue guarantee for that *failed* run. **Fixed** in `provision_runtimes` by
  tracking the in-flight runtime/storage and cleaning them in the `except` before `_rollback()`;
  pinned by `test_partial_startup_cleans_in_flight_runtime_and_storage` (28 PN02C tests now pass).
- **LOW (accepted):** live-mode endpoint attestation compares `runtime.endpoint` to itself
  (tautological) — not a hole, because `route_query` independently enforces endpoint ownership at
  authorization. Non-provider workload counters are declarative structural-0 constants (only the
  provider-network counter is guard-wired); they are genuinely 0 because no such code path exists,
  fenced by the no-provider-seam import test. The provider-free guarantee is structural + test-fenced
  rather than runtime-enforced.

No correctness bug, zero-traffic violation, or normal-DB/production-path mutation was found.

## 12. Limitations

1. **No real LightRAG engine was booted.** Version attestation is a config-pin attestation, not a
   live engine probe. The future live-authorization gate must boot the real `v1.5.6` sidecar
   provider-free and re-attest the version from the running engine before any indexing.
2. **Runtimes are in-process owned micro-servers**, not the real LightRAG process/container. This is
   sufficient to prove endpoint ownership, storage isolation, routing, and fail-closed attestation
   — the PN02C question — but is not evidence about retrieval behavior.
3. **No indexed data exists**, so no isolation/retrieval/QA/multi-hop value is (or can be) evidenced
   here.

---

## 12a. Mandatory PN02D first gate — `REAL_LIGHTRAG_BOOT_GATE` (frozen future rule)

`REAL_LIGHTRAG_BOOT_GATE_REQUIRED_BEFORE_INDEX = YES`.

Before **any** document indexing, embedding, `/query/data`, or final-answer call, PN02D MUST first
perform a real-sidecar boot gate, in this exact sequence:

1. Start exactly **one** run-owned real LightRAG `v1.5.6` sidecar.
2. Verify the **actual running version** through a provider-free version/config seam.
3. Verify the runtime's **workspace / storage identity**.
4. Verify startup caused `EXTERNAL_PROVIDER_NETWORK_CALLS = 0`.
5. Shut down and clean up.

Only if this one-sidecar provider-free boot gate **passes** may PN02D consider starting the full
A/B/C real-LightRAG topology. **If the real sidecar performs unexpected provider traffic during
startup, STOP before indexing** (fail closed).

## 12b. Three-runtime real-LightRAG live gate (future work only — not performed now)

After the single-sidecar real boot gate passes, PN02D must **separately** verify, before any fixture
indexing:

`REAL_LIGHTRAG_RUNTIME_COUNT = 3` · `REAL_LIGHTRAG_WORKSPACE_COUNT = 3` ·
`REAL_LIGHTRAG_STORAGE_ROOT_COUNT = 3` · `REAL_LIGHTRAG_ALL_WORKSPACES_ATTESTED = PASS`.

This is future work. PN02C does **not** perform it.

---

## 13. Decision and what comes next

`PN02C_DECISION = C — PN02C_PREFLIGHT_PASSED_AND_LIVE_AUTHORIZATION_GATE_JUSTIFIED`.

**Precise meaning of C:** the routing / attestation / preflight machinery is sufficiently validated
to justify a **separate operator-controlled live authorization gate**. **C does NOT mean** any of:
real LightRAG runtime attested · provider-backed execution authorized · fixture indexing authorized ·
`/query/data` authorized · QA execution authorized. C authorizes **no** traffic of any kind.

The separate gate — e.g. **GraphRAG-PN02D — Synthetic Per-Notebook Live Evaluation Authorization &
Execution** — is a distinct operator decision, **not** started automatically, and its first step is
the `REAL_LIGHTRAG_BOOT_GATE` of §12a.

Retained frozen: `PN02_LIVE_AUTHORIZATION_GATE_JUSTIFIED=YES`, `PN02_LIVE_AUTHORIZED=NO`,
`GRAPHRAG_PRODUCTION_INTEGRATION=NOT_APPROVED`, `LIGHTRAG_ASK_INTEGRATION=NOT_APPROVED`,
`GRAPH_RAG_09_JUSTIFIED=NO`, `PRODUCTION_IMPORTS_EVAL=NO`. Boundary B unchanged:
`REAL_INTERNAL_DATA_ALLOWED=false`, `SYNTHETIC_ONLY=true`.
