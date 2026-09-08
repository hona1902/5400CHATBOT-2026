# GraphRAG-PN02D-A — Real LightRAG Boot & Data-Plane Attestation Gate

**Status:** ✅ REAL-LIGHTRAG DATA-PLANE PREFLIGHT **COMPLETE** — Gate 0 **PASS** + Gate 1 **PASS**,
provider-free, ZERO data, ZERO queries, ZERO provider traffic. **NOT** the PN02 live benchmark.
Checkpointed at tag `graphrag-pn02da-real-lightrag-preflight-approved` on operator approval.

**Baseline:** HEAD `7553c0f88cd14fe1cec6f573ebc9af61f6cf7789` · branch `feature/graphrag-lifecycle` ·
annotated tag `graphrag-pn02c-routing-preflight-approved` (peel `7553c0f88cd14fe1cec6f573ebc9af61f6cf7789`) ·
working tree CLEAN.

**Decision:** `PN02DA_DECISION = C —
REAL_LIGHTRAG_PREFLIGHT_PASSED_AND_PROVIDER_RUN_AUTHORIZATION_GATE_JUSTIFIED`.
**C authorizes NO provider run** (`PN02_PROVIDER_RUN_AUTHORIZED = NO`): a separate operator decision is
still required.

This is an internal Agribank-fork document. It adds no upstream behavior; **no production runtime
imports any PN02D-A code** (`PRODUCTION_IMPORTS_EVAL = NO`).

---

## 1. What PN02D-A answers (and what it does NOT)

PN02D-A replaces the PN02C **attestation micro-runtimes** with the **REAL LightRAG `v1.5.6` Docker
engine** and proves, BEFORE any indexing, exactly six things (task §0):

1. real LightRAG can boot under the intended per-notebook (PN) topology;
2. the actual RUNTIME version is `v1.5.6` (observed, not merely config-pinned);
3. workspace / storage / endpoint identity is correct;
4. the one-notebook-per-runtime topology can be established (A/B/C);
5. startup causes **ZERO external provider traffic**;
6. all real runtimes shut down and clean up safely.

It does **NOT** index, embed, run `/query/data`, call `client.query`, generate a final answer, run a
judge, or evaluate retrieval/QA/multi-hop value. All four scientific verdicts therefore stay
**NOT_YET** (§13). **A real empty-workspace boot is infrastructure evidence, not indexed-data
isolation evidence.**

### PN02C → PN02D-A distinction

| | PN02C | PN02D-A |
|---|---|---|
| Runtime | stdlib micro-runtime (in-process) | **REAL** `ghcr.io/hkuds/lightrag:v1.5.6` container |
| `REAL_LIGHTRAG_RUNTIME_COUNT` | 0 | **1 (Gate 0)** then **3 (Gate 1)** |
| Version attestation | config-pin (`VERIFIED_LIGHTRAG_VERSION`) | **runtime-observed** (3 provider-free signals) |
| Provider-free boot proven | NO | **YES** (kernel + config + socket detection) |
| Endpoint | loopback micro-server | container identity on its own `--internal` network |

PN02C §12a explicitly required this real-sidecar boot gate before any indexing; PN02D-A performs it.

---

## 2. Authoritative baseline & fixture (revalidated)

| Field | Value |
|---|---|
| `AUTHORITATIVE_BASELINE` | `7553c0f88cd14fe1cec6f573ebc9af61f6cf7789` |
| `PN02_FIXTURE_NAME` | `graphrag_pn02_eval_v1` |
| `PN02_FIXTURE_HASH` | `9ce7df742810424d9ef7b7b34962187d9061245f81471f5fb1d7f2608f6899a6` |
| `FIXTURE_VALIDATION` | PASS |
| `FIXTURE_HASH_VALIDATION` | PASS (live == frozen) |

No fixture Source was indexed. The frozen fixture was not modified.

---

## 3. Real engine (pinned) & its verified boot contract

Image: **`ghcr.io/hkuds/lightrag:v1.5.6`** (present locally, image id `ab23a9c83a73`,
`org.opencontainers.image.version = v1.5.6`, revision `b33c6b08…`). Never `:latest`, never a newer or
substituted tag.

Characterized empirically (provider-free throwaway boot, then fully cleaned up):

- boots with **EMPTY provider bindings** (`LLM_BINDING` / `EMBEDDING_BINDING` and their host/key vars);
- `GET /health` (unauthenticated) → `200` with `core_version = 1.5.6`, `api_version = 0328`,
  `status = healthy`;
- the installed package reports `lightrag.__version__ = 1.5.6`;
- **the image defines NO HEALTHCHECK**, so a naïve `docker inspect '{{.State.Health.Status}}'` errors
  (nil map) — PN02D-A uses a nil-safe inspect template (`{{if .State.Health}}…{{else}}none{{end}}`) and
  determines health from the HTTP `/health` status code, not the container health field;
- `--internal` Docker networks give kernel-enforced zero egress but disable host port publishing, so
  the engine is probed via `docker exec` on the container's own loopback (`python`, since `curl` is
  absent).

---

## 4. Provider-egress guard (three independent layers, task §4/§5)

`EXTERNAL_PROVIDER_NETWORK_CALLS` must be exactly **0**, including *attempted* calls. Logs are not
trusted as the sole signal. PN02D-A layers:

1. **Kernel prevention** — each sidecar runs on its OWN run-owned `--internal` Docker network, verified
   `Internal = true` via `docker network inspect`. Such a network has no route off-host, so external
   provider egress is **impossible by construction** (the same "0 by construction" guarantee PN02C
   made for its micro-runtimes). A non-internal network fails `FAILED_NETWORK_ISOLATION`.
2. **Config prevention** — the boot command sets all provider binding vars EMPTY and carries no
   provider host/model/key and no `LIGHTRAG_API_KEY`; `assert_no_provider_binding_in_command` refuses
   to launch a command that carries any nonempty provider target.
3. **Socket-level detection** — after boot and after every probe, the container's own kernel socket
   table (`/proc/net/tcp` + `/proc/net/tcp6`, read via `docker exec`) is parsed and any socket with a
   real remote peer whose IP is **not loopback** (127.0.0.0/8 or `::1`) is counted. A LISTEN socket
   (`0A`, remote `:0000`) and Docker's `127.0.0.11` DNS resolver are correctly excluded; a SYN_SENT /
   ESTABLISHED to any external host would be counted (detecting even a blocked attempt). The
   `ProviderEgressGuard` is fail-closed: a nonzero count raises `ProviderEgressDetected` → `FAILED_
   PROVIDER_EGRESS` → STOP before indexing.

Observed across Gate 0 and all three Gate 1 runtimes: **external peer sockets = 0**.

Content-safety: the guard ingests only the coarse socket table and emits only integer counts; it never
reads a request body, an `Authorization` header, an env value, or a provider payload.

The socket sample is **defense-in-depth behind the kernel prevention layer**, not the primary
guarantee: it is a post-boot TCP (`/proc/net/tcp` + `tcp6`) observation, so the `--internal` network
(egress impossible) and empty bindings (nothing to call) remain the load-bearing controls. On a boot
that fails before egress can be observed, the report emits `EXTERNAL_PROVIDER_NETWORK_CALLS =
NOT_SAMPLED` and `REAL_LIGHTRAG_PROVIDER_FREE_BOOT_PROVEN = false` (never a bare `0`), so a failed boot
can never be misread as provider-free-proven.

---

## 5. Gate 0 — single real sidecar (executed run `pn02da-live-1-g0`)

`REAL_LIGHTRAG_BOOT_GATE = PASS`.

| Field | Value |
|---|---|
| Sidecar | `pn02da_c_9119633be3d5` on `pn02da_net_9119633be3d5` (`Internal=true`) |
| Booted / healthy | YES / YES (healthy in ~7s via HTTP `/health` 2XX) |
| `REAL_LIGHTRAG_RUNTIME_VERSION_ATTESTED` | **YES** |
| `REAL_LIGHTRAG_RUNTIME_VERSION` | **v1.5.6** |
| version signals | health `core_version=1.5.6` · `lightrag.__version__=1.5.6` · image label `v1.5.6` |
| workspace | `nb_9af1e7f81e5158da` (observed == expected) |
| storage identity | `st_089d2b7ce43bb6ba` (run-owned, forbidden-root-guarded, fresh mkdtemp) |
| endpoint | `lightrag+docker://pn02da_c_9119633be3d5` |
| `EXTERNAL_PROVIDER_NETWORK_CALLS` | **0** |
| `DOCUMENT_INSERT_CALLS` / `QUERY_DATA_CALLS` / `CLIENT_QUERY_CALLS` / embedding / LLM | 0 / 0 / 0 / 0 / 0 |
| cleanup | processes 0 · storage residue 0 · networks 0 |

Version attestation is **runtime-observed** (three independent provider-free signals agree), not a
config pin. `/query/data` was never used as a version probe.

---

## 6. Gate 1 — three real runtimes A/B/C (executed run `pn02da-live-1-g1`)

Only entered because Gate 0 PASSED. Each notebook → its own real LightRAG runtime, workspace, storage,
endpoint, and `--internal` network (one-to-one; no runtime owns two notebooks; no two runtimes share
writable storage or a network).

| Notebook | Container | Workspace id | Storage identity | Version |
|---|---|---|---|---|
| NB_A | `pn02da_c_f2937acb5249` | `nb_2801282a94466d17` | `st_bae2223cd9fbe69d` | v1.5.6 |
| NB_B | `pn02da_c_5125630c10c1` | `nb_ca49abb97532ce9a` | `st_d5c98f88239fc5a7` | v1.5.6 |
| NB_C | `pn02da_c_5c0788f6dc37` | `nb_45f0f09aff5bb661` | `st_61e46b045e1a93c7` | v1.5.6 |

The workspace ids are the deterministic `"nb_" + sha256(record_id)[:16]` values and match the PN02C
routing manifest exactly (derived from canonical record ids, never a display title).

| Field | Value |
|---|---|
| `REAL_LIGHTRAG_RUNTIME_COUNT` | 3 |
| `REAL_LIGHTRAG_WORKSPACE_COUNT` | 3 |
| `REAL_LIGHTRAG_STORAGE_ROOT_COUNT` | 3 |
| `REAL_LIGHTRAG_ENDPOINT_COUNT` | 3 |
| `REAL_LIGHTRAG_ALL_VERSION_ATTESTED` | PASS |
| `REAL_LIGHTRAG_ALL_WORKSPACES_ATTESTED` | PASS |
| `REAL_LIGHTRAG_STORAGE_ALIASING` | 0 |
| `REAL_LIGHTRAG_THREE_RUNTIME_PROVIDER_TRAFFIC` | 0 |
| cleanup | processes 0 · storage residue 0 · networks 0 |

### Routing negatives (reuse the PN02C contract against REAL endpoints — NO `/query/data`)

`RealTopology`/`RealRuntimeIdentity` are attribute-compatible with the PN02C `ProvisionedTopology`/
`RuntimeIdentity`, so `routingpn02c.route_query` / `authorize_future_gd` /
`cross_workspace_storage_aliasing` and `manifestpn02.require_attested_before_query` apply unchanged:

| Test | Result |
|---|---|
| `CORRECT_ROUTE_ACCEPTED` | PASS |
| `REAL_WRONG_ENDPOINT_ROUTE_REJECTED` | PASS |
| `REAL_WRONG_WORKSPACE_ROUTE_REJECTED` | PASS |
| `REAL_UNATTESTED_ROUTE_REJECTED` | PASS |

None of these calls retrieval or any provider seam.

### Shared-source routing plan (reverified, not indexed)

`SH_AB → {NB_A, NB_B}`, `SH_AC → {NB_A, NB_C}`, `SH_BC → {NB_B, NB_C}` (each shared source into exactly
its two workspaces, never the third) → `REAL_TOPOLOGY_SHARED_SOURCE_PLAN = PASS`. The PN02B workload
ledger caps (`PLANNED_GRAPH_INDEX_OPERATIONS=24`, `MAX_GRAPH_INDEX_ATTEMPTS=48`,
`GRAPH_DELETE_OPERATIONS=1`, `MAX_GD_QUERIES=26`, `MAX_VECTOR_QUERY_OPERATIONS=26`,
`MAX_QUERY_EMBEDDING_OPERATIONS=26`, `MAX_FINAL_ANSWER_CALLS=72`, `JUDGE_MODEL_CALLS=0`) remain future
limits; **PN02D-A consumed 0 of every provider-backed workload.**

---

## 7. HARD no-index authorization capability (task §34/§35)

`RealLightRAGPreflightAuthorization` mirrors PN02B's `Stage1Authorization`: an unforgeable capability
minted **only** by `mint_real_preflight_authorization` when **Gate 0 PASS AND Gate 1 PASS**. Direct
construction raises `PermissionError`. Any future live indexing seam guarded by
`require_real_preflight_authorization` is unreachable through the normal PN02 path without both gates.
PN02D-A implements **no indexing** — `assert_indexing_gated` only proves the gate denies without a
valid authorization.

On this run the capability WAS minted (both gates passed). It authorizes **no** provider run:
`PN02_PROVIDER_RUN_AUTHORIZATION_GATE_JUSTIFIED = YES`, `PN02_PROVIDER_RUN_AUTHORIZED = NO`.

---

## 8. Provider binding status (future config — presence is NOT authorization, task §24/§25)

Inspected by env-var NAME only; no value is ever read:

| Field | Value |
|---|---|
| `LLM_PROVIDER_CONFIG_PRESENT` | NO |
| `EMBEDDING_PROVIDER_CONFIG_PRESENT` | NO |
| `OPENROUTER_KEY_PRESENT` | NOT_REQUIRED_FOR_BOOT |
| `SIDE_CAR_AUTH_CONFIG_PRESENT` | NO |

PN02D-A boot uses no provider binding and no sidecar key, so it carries **no secret at all**. PN02D-A
does not test any credential (task §25).

---

## 9. Cleanup, secret & normal-service safety

- `REAL_LIGHTRAG_OWNED_PROCESSES_REMAINING = 0`, `REAL_LIGHTRAG_RUNTIME_STORAGE_RESIDUE = 0`,
  owned networks remaining 0. Every owned container is stopped+removed, every owned `--internal`
  network removed, every owned storage root deleted. Run twice with fresh identities, both clean.
- Docker container and network listings are **identical before and after** the run; the only
  pre-existing container (`open-notebook-surrealdb-1`) and the standard networks are untouched.
  `NORMAL_DB_MUTATIONS = 0`. Normal Open Notebook, SurrealDB, and unrelated Docker resources were not
  stopped or modified.
- No secret is generated, printed, or persisted. The generated `docker run` argv is content-safe
  (pinned image, internal network name, opaque workspace hash, empty binding vars, owned storage mount)
  and `redact_command` masks any `*_API_KEY` / `*_TOKEN` before any logging.

---

## 10. Code, tests, isolation

Four new **eval-only** modules under `open_notebook/integrations/graphrag/eval/` (never imported by
production):

- `egressguardpn02d.py` — socket-level provider-egress detection + fail-closed `ProviderEgressGuard`.
- `realsidecarpn02d.py` — real-sidecar boot command/config generation, `RealRuntimeIdentity`, injectable
  `DockerCLI` (targeted content-safe inspects/execs only), nil-safe inspect template, `RealTopology`
  + owned-resource cleanup.
- `attestpn02d.py` — three-signal runtime version attestation, full runtime attestation, and the
  `RealLightRAGPreflightAuthorization` capability.
- `preflightpn02d.py` — Gate 0 + Gate 1 orchestrator, technical-outcome taxonomy, decision A/B/C,
  content-safe report, and an eval-only CLI (`… preflightpn02d --live`; `--live` is REQUIRED to boot
  real Docker).

**Tests:** 34 offline tests in `tests/test_graphrag_pn02d_preflight.py` (command/config generation,
version/workspace/storage mismatch rejection, provider-attempt rejection, three-runtime identity /
storage / endpoint uniqueness, wrong-route rejection, cleanup ownership, fixture-hash mismatch
rejection, no-index-before-both-gates, unforgeable authorization, egress parsing, no-provider-seam
import guard) drive the whole Gate 0 / Gate 1 logic via a configurable `FakeDocker` runner. The one
authorized **provider-free real-Docker integration** was executed as this gate. `ruff` + `mypy` clean.

---

## 11. Failure taxonomy (technical) vs decision (governance)

Technical outcomes stay separate from any scientific verdict: `FAILED_PRECHECK`, `FAILED_FIXTURE_HASH`,
`FAILED_DOCKER_UNAVAILABLE`, `FAILED_IMAGE_MISSING`, `FAILED_IMAGE_VERSION`, `FAILED_NETWORK_ISOLATION`,
`FAILED_PROVIDER_BINDING_IN_COMMAND`, `FAILED_SIDECAR_START`, `FAILED_VERSION_ATTESTATION`,
`FAILED_WORKSPACE_ATTESTATION`, `FAILED_STORAGE_ATTESTATION`, `FAILED_ROUTING_ATTESTATION`,
`FAILED_PROVIDER_EGRESS`, `FAILED_CLEANUP`, `COMPLETED`. This run: **`COMPLETED`**. (A first live
attempt correctly surfaced the nil-HEALTHCHECK inspect bug as `FAILED_SIDECAR_START` and cleaned up to
0 residue before the fix — the gate is fail-closed.)

---

## 12. Limitations

1. **No document was indexed and no query was run.** A real *empty-workspace* boot proves the topology
   and provider-free startup — it is **not** evidence about retrieval, QA, isolation-under-data, or
   multi-hop value.
2. Version attestation is runtime-observed at boot; it does not exercise `/query/data` or any provider
   path.
3. Endpoint identity is the container's identity on its own `--internal` network (host port publishing
   is intentionally disabled for the egress guarantee); routing is proven by the ownership contract,
   not by dialing graph data.

---

## 13. Scientific status (unchanged)

| Verdict | Value |
|---|---|
| `PER_NOTEBOOK_ISOLATION_EVIDENCED` | NOT_YET |
| `PER_NOTEBOOK_GRAPH_RETRIEVAL_VALUE_EVIDENCED` | NOT_YET |
| `PER_NOTEBOOK_GRAPH_QA_VALUE_EVIDENCED` | NOT_YET |
| `PER_NOTEBOOK_MULTIHOP_INCREMENTAL_VALUE_EVIDENCED` | NOT_YET |

---

## 14. Decision & what comes next

`PN02DA_DECISION = C — REAL_LIGHTRAG_PREFLIGHT_PASSED_AND_PROVIDER_RUN_AUTHORIZATION_GATE_JUSTIFIED`.

**Meaning of C:** the real LightRAG data-plane topology can be booted and attested safely with zero
provider traffic and zero data, so a **separate operator-controlled provider-run authorization gate** is
justified. **C does NOT authorize** indexing, embeddings, `/query/data`, QA, or any provider call.

Retained frozen: `PN02_PROVIDER_RUN_AUTHORIZED = NO`, `GRAPHRAG_PRODUCTION_INTEGRATION = NOT_APPROVED`,
`LIGHTRAG_ASK_INTEGRATION = NOT_APPROVED`, `GRAPH_RAG_09_JUSTIFIED = NO`, `PRODUCTION_IMPORTS_EVAL = NO`.
Boundary B unchanged: `REAL_INTERNAL_DATA_ALLOWED = false`, `SYNTHETIC_ONLY = true`.

Checkpointed at tag `graphrag-pn02da-real-lightrag-preflight-approved` on operator approval (Decision C
approved in principle; canonical on-disk audit passed). C authorizes NO provider run.
