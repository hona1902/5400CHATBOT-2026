# GraphRAG-08E.3 — Real-Sidecar Attestation Hardening

**Status:** OFFLINE IMPLEMENTATION + PROVIDER-FREE REAL-SIDECAR SMOKE + TESTS + INDEPENDENT
REVIEW — attestation semantics only. **No provider traffic, no bounded concurrency sweep, no
Stage B, no attempt #6, no DEV/HOLDOUT, no frozen-parameter change.**

This phase fixes the two provisioner-attestation defects found at **GraphRAG-08E Live
Concurrency Diagnostic Reauthorization #2, Stage A** (the provider-free real-sidecar smoke),
which blocked the bounded live diagnostic before any provider traffic. It does **not** run the
diagnostic and does **not** authorize Stage B — a fresh operator reauthorization is required.

## 1. Preserved Stage-A record (honest)

```
GRAPH_RAG_08E_LIVE_DIAGNOSTIC_REAUTHORIZATION_2 = BLOCKED_AT_STAGE_A
STAGE_A_PROVIDER_TRAFFIC = NO   STAGE_A_INDEXING_CALLS = 0   STAGE_B_ALLOWED = NO
LIVE_DIAGNOSTIC_EXECUTED = NO   FULL_RUN_ATTEMPT_6_EXECUTED = NO   DEV/HOLDOUT = 0/0
cleanup = PASS   process/workspace residue = 0   foreign/normal-DB mutation = 0
```

The Stage-A smoke is **not** a diagnostic repetition, **not** H1/H2/H3 evidence, and **not** a
provider or GraphRAG-indexing failure — it was a provisioner attestation defect.

## 2. The two defects (confirmed against the real sidecar)

**Defect A — `VERSION_ATTESTATION_LITERAL_MISMATCH`.** The provisioner compared the pinned
`expected_version` (`v1.5.6`, the git-tag/pin form) to the reported version by exact string
equality. The real pinned LightRAG v1.5.6 `/health` reports `core_version = "1.5.6"` (no
leading `v`), so a healthy, correct sidecar was rejected with `'1.5.6' != 'v1.5.6'`. The
offline fakes had masked it by returning `"v1.5.6"` on both sides.

**Defect B — `RUNTIME_WORKSPACE_ATTESTATION`.** The committed `HealthResult` carried only
`healthy`/`detail`/`version`, so the prober's `getattr(health, "workspace", None)` was always
`None` and, with `require_runtime_workspace=True`, provisioning could never reach `PROVISIONED`.
This had been documented as the intentional fail-closed `L2` limitation and was now confirmed
live — **but** the underlying assumption ("v1.5.6 /health does not expose the workspace") turned
out to be **wrong**: the shared client simply *discarded* it.

## 3. Source/runtime forensic (pinned v1.5.6, provider-free)

Observed directly from a real `ghcr.io/hkuds/lightrag:v1.5.6` container (no provider bindings,
no indexing), corroborating the 08E.1 source trace (`--workspace` / `WORKSPACE` env fixes the
server workspace at startup; storage is scoped under `working_dir/<workspace>/`):

- **`/health` DOES expose the bound workspace.** The raw payload contains
  `configuration.workspace` (and `storage_workspaces`) plus `working_directory =
  "/app/data/rag_storage"` and `core_version = "1.5.6"`. So a **DIRECT** server self-report is
  available (the earlier `L2` "not exposed" assumption is corrected here).
- **`docker inspect` of the owned container** shows `WORKSPACE=<W>` in `Config.Env` and a mount
  `Destination=/app/data/rag_storage` whose `Source` is exactly the run-owned host root — the
  **only** place the host-side storage binding is observable at runtime (not in `/health`).

## 4. Version attestation contract (Defect A fix)

`versions_equivalent(expected, reported)` canonicalises with the trusted, already-present
`packaging.version.parse` (used elsewhere in `open_notebook/utils/version_utils.py`): `v1.5.6`
and `1.5.6` are the **same** PEP 440 release; a different patch/minor/major (`1.5.5`, `1.5.7`,
`2.0.0`, `1.5`) and any absent/malformed form (`""`, `abc`, `v`, `1.5.6-broken?`, `latest`) are
**not** equivalent (fail closed). It is **not** permissive — no broad string munging, no new
dependency. `_verify_health` now uses it in place of `==`.

## 5. Workspace/storage attestation contract (Defect B fix)

`WORKSPACE_ATTESTATION_KIND = DIRECT_HEALTH_CONFIG (+ DERIVED_OWNED_CONTAINER_CONFIG)`.
`WORKSPACE_DIRECT_RUNTIME_REPORT = YES` (v1.5.6 does expose it). Two evidence sources, **at
least one required**, and neither may be fabricated from the intended input (task §8):

- **DIRECT** — `parse_health_payload` extracts **only** `configuration.workspace`,
  `working_directory`, `status`, and the version from the raw `/health` (everything else,
  including binding hosts, is discarded — content-safe). The prober does its own raw
  content-safe GET (eval-only), never the shared client.
- **DERIVED** — an injected `CellRuntimeAttestor`; the live `DockerRuntimeAttestor` inspects
  **only the owned container** with targeted templates that emit **only** the `WORKSPACE` env
  value and the rag-storage mount (never the full `Config.Env`, so a provider key cannot leak,
  task §12/§29). It supplies ownership (`container_identity == handle.identifier`, task §30) and
  the host storage-mount evidence (`storage_source` must equal the run-owned working dir via
  `_paths_equal`, and `storage_dest` must be the expected in-container mount, task §13/§27).

Fail-closed rules retained: a present-but-mismatched value from either source fails
(`§26/§27`); missing evidence from both fails (`§24/§28`); `require_runtime_workspace=True` is
never silently relaxed (`§15/§16`). A failure inside `_attest_workspace` runs inside
`_verify_health`, inside the atomic `provision` rollback, so the process is stopped, the port
released, and the workspace disposed with **no** provisioned handle (`§31/§48`).

## 6. Provider-free real-sidecar smoke result

```
REAL_LOCAL_SIDECAR_PROVISION_TEST = PASS      PROVIDER_TRAFFIC = NO   INDEXING_CALLS = 0
SMOKE_PROCESS_STARTED = YES                    SMOKE_LIGHTRAG_VERSION = 1.5.6
PIN_EQUIVALENCE = PASS (v1.5.6 == 1.5.6)
WORKSPACE_DIRECT_RUNTIME_REPORT = YES
WORKSPACE_RUNTIME_ATTESTATION = PASS
WORKSPACE_ATTESTATION_METHOD = DIRECT_HEALTH_CONFIG + DERIVED_OWNED_CONTAINER_CONFIG
STORAGE_ROOT_ATTESTATION = PASS   PROCESS_OWNERSHIP = PASS   PORT_OWNERSHIP = PASS
PHYSICAL_WORKSPACE_FRESHNESS = PASS
SMOKE_CLEANUP = PASS   SMOKE_PROCESS_RESIDUE = 0   SMOKE_WORKSPACE_RESIDUE = 0
```

One dedicated throwaway smoke cell (`smoke08e3R_c1_r1`, not one of the eight diagnostic cells)
provisioned through the committed provisioner + real Docker/health/attestor primitives, with no
provider bindings passed to the container and no index/embed/LLM/query call. It reached
`PROVISIONED`, attested workspace both DIRECTly and DERIVED, and was disposed with verified zero
residue.

## 6a. Independent review (preserved honestly)

Independent adversarial review outcome: **PASS — 0 HIGH, 0 MEDIUM.** The two defects are
correctly fixed and fail-closed across version canonicalization, workspace-evidence,
secret-safety, foreign-container, scientific-boundary, and atomicity dimensions. LOW/nit items
and their disposition:

- **LOW-1 (live-wiring requirement).** With DIRECT-only attestation (no `runtime_attestor`
  injected), the host storage-**mount** is not attested from runtime truth (only the DIRECT
  `configuration.workspace`); cross-cell cache isolation still holds (unique workspace subdir),
  so the residual is a cleanup/residue-boundary gap, not contamination — and it does not violate
  the fail-closed contract (absence of BOTH evidence sources still fails closed). **Requirement
  for the live path:** always inject `DockerRuntimeAttestor` so the owned-container host mount is
  attested. The provider-free smoke did exactly this (`DIRECT+DERIVED`).
- **LOW-2 (FIXED).** `parse_health_payload` no longer falls back to `api_version` (which is not
  the release); only `core_version` is used for the pin check.
- **LOW-3 (live-only, validated here).** `_paths_equal` assumes `docker inspect` mount `Source`
  equals the host `working_dir` after realpath/normcase. On some Docker Desktop hosts the source
  may be a translated path; on the smoke host it matched exactly (`STORAGE_ROOT_ATTESTATION =
  PASS`). Re-confirm on the eventual Stage-B host.
- **NITs.** A test comment was corrected (a valid-but-different version is not "malformed");
  `working_directory` from /health is captured for reporting but not gated on (harmless).

## 7. Scope

Changes are confined to eval provisioner attestation (`cell_provisioner08.py`), its tests, and
docs. No change to Source creation, embedding, indexing, retry classifier/allowlist, diagnostic
concurrency treatment, the frozen plan (`[1,2,4,8]`, 2 reps, 30/64), the full-run runner,
provider/model, the LightRAG pin, or the fixture
(`a58a68535c345e18f0263904f818e4e2068a164056408665d8bb9233eceb143d`, unchanged). Production
imports no eval code. No migration, no `.env` edit, no runtime artifact.

## 8. Posture

```
LIVE_DIAGNOSTIC_AUTHORIZED = NO   LIVE_DIAGNOSTIC_EXECUTED = NO   ATTEMPT_6 = NOT_RUN
ROOT_CAUSE_CONFIRMED = NO   H1/H2/H3 = UNCONFIRMED
FULL_EXECUTION_AUTHORIZED = NO   VALUE_EVIDENCE_READY = NO
```

Even with the real smoke green, Stage B (the bounded concurrency diagnostic) requires a
**separate explicit operator reauthorization** — this phase authorizes nothing further.
