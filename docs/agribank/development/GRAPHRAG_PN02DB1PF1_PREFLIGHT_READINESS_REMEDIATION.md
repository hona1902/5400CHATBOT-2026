# GraphRAG-PN02D-B1-PF1 — Provider-Free Preflight Readiness Remediation

**Phase kind:** OFFLINE remediation of a real-Docker defect in the B0C-B provider-free
preflight. ZERO provider traffic (provider-free sidecars only — no OpenRouter/OpenAI, no
secret bound to any container). NOT checkpointed in this turn; the mandatory Codex
independent review is a separate gate that must run before any PF1 checkpoint.

## Authorization blocker that motivated this phase

The PN02D-B1 Final Operator Run Authorization was correctly BLOCKED
(`AUTHORIZATION_BLOCKED_PREFLIGHT`) after Docker was made available: the genuine
provider-free preflight (`RealPN02RuntimeManager.boot_preflight` + the real
`RealProviderFreePreflightRunner`/`DockerCLI`) launched all three sidecars but returned
`gate0=gate1=False`, so no `RealLightRAGPreflightAuthorization` could be minted and the
live mint was never invoked. Every OTHER authorization gate was GREEN (checkpoint/parity,
run_id, fixture hash `9ce7df74…`, synthetic-only, provider fingerprint
`pbf_1811d0bfd5cfad2743ffaa69`, secret present, caps, allowlist, concurrency, grant
validity, B1-R2 Git gate).

## Real-Docker evidence

A single-sidecar provider-free diagnostic that **waits for readiness** before probing
passed cleanly against the pinned `ghcr.io/hkuds/lightrag:v1.5.6`:

```
health = 200 · core_version = 1.5.6 · import_version = 1.5.6 · image_label = v1.5.6
attest_version(...) = True   (EXPECTED_IMAGE_VERSION_LABEL 'v1.5.6' canonicalizes to 1.5.6)
```

So the version-attestation LOGIC is correct against real Docker — there is **no** version
mismatch, no `v1.5.6`-vs-`1.5.6` defect, no provider/daemon/workspace problem.

## Root cause — `ROOT_CAUSE_CONFIRMED = READINESS_RACE`

`boot_preflight` called `version_signals()` (the `DockerCLI.health` exec probe)
**immediately** after `docker run -d`. A container reports `container_running` as soon as
`docker run -d` returns, but the LightRAG HTTP app answers `/health` only a few seconds
later. So the first `health` read returned no status/core version →
`PreflightRunError` → `gate_ok=False`. The B0C-B tests injected a **fake** `DockerCLI`
whose health returns instantly, so this startup timing was never exercised — the exact
"not exercised against real Docker in B0C-B" gap the runner's own docstring warned about,
the same class as the 08E.2 → 08E.3 real-sidecar Stage-A failure.

## Fix — readiness before version attestation (no version-policy change)

Frozen order: **`docker run -d` → `wait_ready` → `version_signals` → `attest_version`.**

- New `PreflightRunnerLike.wait_ready(handle)` + `RealProviderFreePreflightRunner.wait_ready`,
  reusing the battle-tested `realsidecarpn02d.wait_healthy` (bounded poll of
  `inspect_state` + a health **status code only** — no body, no provider call). No
  duplicate polling logic was introduced.
- `boot_preflight` awaits `wait_ready(handle)` BETWEEN `launch` and `version_signals`.
- Bounded + fail-closed: `PREFLIGHT_READINESS_TIMEOUT_S = 120.0` (poll 2 s). On timeout —
  or if the container stops — `wait_ready` raises `PreflightRunError`, the per-runtime
  failure sets `gate_ok=False`, and `teardown_preflight` still tears every launched
  runtime down. A never-ready runtime can never be attested.
- The clock (`now`/`sleep`/`readiness_timeout_s`/`readiness_poll_s`) is injectable so the
  race is unit-testable deterministically.

`VERSION_CANONICALIZATION_CHANGED = NO`. `VERSION_ATTESTATION_POLICY_WEAKENED = NO` — the
three independent signals + `attest_version` (expected label `v1.5.6`) are unchanged.
`PROVIDER_SECRET_BOUND_TO_PREFLIGHT_CONTAINER = NO` — the boot command still carries only
empty provider bindings + an opaque `WORKSPACE`, guarded by
`assert_no_provider_binding_in_command`.

## Test coverage

Offline (deterministic, no Docker) in `tests/test_graphrag_pn02db1pf1.py`:

- **Ordering** — `boot_preflight` calls `wait_ready` before `version_signals` for every
  launched handle (`READINESS_BEFORE_VERSION_SIGNALS_TEST`).
- **Delayed startup** — a container that becomes healthy only after several polls: the
  wait polls past the not-ready window and then attests; probing without the wait fails
  (`DELAYED_STARTUP_TEST`).
- **Timeout fail-closed + cleanup** — a never-ready container: `wait_ready` raises, the
  gate fails closed, no capability is mintable, and every container/network is torn down
  (`READINESS_TIMEOUT_FAIL_CLOSED_TEST` / `READINESS_TIMEOUT_CLEANUP_TEST`).

Docker-gated (skipped when Docker / the pinned image is absent, per the existing
`skipif` convention — offline coverage is retained so CI without Docker stays meaningful):

- Real sidecar: `launch → wait_ready → version_signals → attest_version = True`.
- Real `boot_preflight` across all three PN02 notebook workspaces →
  `gate0 = gate1 = PASS`, three distinct container identities (workspace isolation),
  a genuine `RealLightRAGPreflightAuthorization` is mintable, **no** live provider-run
  authorization, and 0 residual owned containers/networks.

## Cleanup & zero-provider posture

Provider-free sidecars only; `EXTERNAL_PROVIDER_NETWORK_CALLS = 0`,
`PROVIDER_BOUND_LIGHTRAG_BOOT_COUNT = 0`, `REAL_PROVIDER_EMBEDDINGS = 0`,
`NORMAL_DB_MUTATIONS = 0`. All test containers/networks/temp storage are torn down.

## Checkpoint continuity — SUCCESSOR CHECKPOINT REQUIRED

`B1_R2_TAG_MUST_PEEL_TO_CURRENT_HEAD = YES` and
`NEW_CODE_COMMIT_INVALIDATES_CURRENT_B1_R2_GIT_GATE = YES` — confirmed from source
(`verify_b1_r2_checkpoint` requires `observed_tag_peel == observed_head`;
`attest_approved_clean_baseline` requires `head_commit == approved_commit ==
tag_peel_commit`) and empirically (a HEAD past `611532c` yields
`b1_r2_tag_not_at_authorized_head`). Committing this fix moves HEAD off `611532c`, so the
historical B1-R2 tag no longer peels to the authorized HEAD and the B1-R2 Git gate is
superseded.

Therefore `SUCCESSOR_CHECKPOINT_REQUIRED = YES`,
`SUCCESSOR_CHECKPOINT_TAG = graphrag-pn02db1pf1-preflight-readiness-approved`,
`SUCCESSOR_TAG_CURRENTLY_EXISTS = NO`. The successor identity is frozen through the
existing checkpoint-authority model (no second trust framework):

- `authmintlivepn02d.EXPECTED_PF1_CHECKPOINT_TAG` is the new constant; the
  governance-approved identity (`current_approved_b1_r2_checkpoint()` /
  `_APPROVED_B1_R2_CHECKPOINT`) is repointed to it; `authb1r2pn02d` binds the grant's
  expected identity + baseline tag to it.
- `EXPECTED_B1_R2_CHECKPOINT_TAG` is RETAINED as a distinct HISTORICAL constant — the
  B1-R2 annotated tag is IMMUTABLE at `611532c` and is never moved (§4).
- The PF1 tag does not exist yet, so the production mint stays **FAIL CLOSED**
  (`b1_r2_tag_not_observed_in_git`): `LIVE_PROVIDER_AUTHORIZATION_CURRENTLY_MINTABLE = NO`.

Tests distinguish the historical B1-R2 approval (immutable at `611532c`) from the current
successor PF1 approval (absent until the successor checkpoint), and no test reintroduces a
real-B1-R2-tag-absent assumption.

## Governance retained

`LIVE_PROVIDER_AUTHORIZATION_MINTED = NO`, `PN02_PROVIDER_RUN_AUTHORIZED = NO`,
`PN02D_B1_EXECUTION_STARTED = NO`, `PN02D_B2_QA_AUTHORIZED = NO`,
`GRAPHRAG_PRODUCTION_INTEGRATION = NOT_APPROVED`, `LIGHTRAG_ASK_INTEGRATION = NOT_APPROVED`,
`GRAPH_RAG_09_JUSTIFIED = NO`. `CODEX_B1PF1_REVIEW = NOT_RUN`,
`B1PF1_CHECKPOINT_ALLOWED = NO`.

## Files

- `open_notebook/integrations/graphrag/eval/runtimelivepn02d.py` — readiness wait
  (`wait_ready` + `boot_preflight` ordering; injectable clock; reuse `wait_healthy`).
- `open_notebook/integrations/graphrag/eval/authmintlivepn02d.py` — successor governance
  freeze (`EXPECTED_PF1_CHECKPOINT_TAG`; approved identity repointed; B1-R2 retained as
  historical).
- `open_notebook/integrations/graphrag/eval/authb1r2pn02d.py` — grant expected identity /
  baseline tag repointed to the PF1 successor.
- `tests/test_graphrag_pn02db1pf1.py` — NEW readiness tests (offline + Docker-gated).
- `tests/graphrag_pn02db0cb_common.py` — `FakePreflightRunner.wait_ready` + call-order
  record; `FakeDockerCLI.inspect_state` returns a real `SidecarObservation`.
- `tests/test_graphrag_pn02db1r2.py`, `tests/test_graphrag_pn02db0cb_adapters.py`,
  `tests/test_graphrag_pn02db0cb_live.py` — historical-vs-successor identity updates.

## Codex Independent Review #1 → LOW-finding remediation cycle #1

Codex Independent Review #1 = **decision C — PASS_WITH_LOW_FINDINGS (0 HIGH / 0 MEDIUM / 1
LOW)**; every readiness / version-policy / provider-free / trust-chain / successor-checkpoint
/ authorization-regression / zero-provider area passed (Codex static review; its sandbox
could not run tests — `CODEX_TEST_EXECUTION = BLOCKED_BY_CODEX_ENVIRONMENT`).

- **B1PF1-R1-L1 — LOW (DESIGN=NONE) — REMEDIATED.** The `authb1r2pn02d.py` top-of-file
  "Temporal correctness" docstring (and the `b1_r2_preflight` docstring) still described the
  historical B1-R2 checkpoint as the current governance identity and said its tag "does not
  exist", after the executable governance had already moved to `EXPECTED_PF1_CHECKPOINT_TAG`.
  Fix: the docstrings now distinguish the HISTORICAL B1-R2 checkpoint (real, approved,
  immutable, peels to `611532c…`) from the CURRENT PF1 successor (governance-approved, tag
  absent → mint fail-closed). **Docstrings/comments only — `EXECUTABLE_LOGIC_CHANGED = NO`**
  (ruff + mypy clean; AST skeleton unchanged; governance still returns PF1 and fails closed;
  offline targeted 175 pass). No Git ref touched: B1-R2 tag immutable at `611532c`, PF1 tag
  still absent.

`CODEX_B1PF1_REVIEW_1 = C_PASS_WITH_LOW_FINDINGS` (retained — Review #1 is not rewritten as
clean); `B1PF1_R1_L1_REMEDIATED = YES`; `CODEX_B1PF1_REREVIEW_1 = NOT_RUN`;
`B1PF1_CHECKPOINT_ALLOWED = NO`.

**STOP:** `GRAPH_RAG_PN02DB1PF1_LOW_REMEDIATION_READY_FOR_CODEX_REREVIEW`. No Codex; no
checkpoint; no tag; no provider run.
