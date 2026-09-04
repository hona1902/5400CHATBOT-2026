# GraphRAG-08E.5 — Live Cell Provider-Binding Injection Hardening

**Status:** OFFLINE IMPLEMENTATION + TESTS + INDEPENDENT REVIEW — provider-binding transport
+ attestation only. **No provider traffic, no provider-backed embedding/indexing, no
container start, no bounded diagnostic, no attempt #6, no DEV/HOLDOUT, no frozen-parameter
change.**

This phase fixes the one remaining execution-runtime defect found at GraphRAG-08E Live
Concurrency Diagnostic Reauthorization #4. It does **not** run the diagnostic and does
**not** authorize Stage B.

## 1. Preserved Reauthorization #4 record (honest)

```
GRAPH_RAG_08E_LIVE_DIAGNOSTIC_REAUTHORIZATION_4 = BLOCKED_PRE_PROVIDER
BLOCKER = PER_CELL_PROVIDER_BINDINGS_NOT_INJECTED
PROVIDER_TRAFFIC = NONE   CELLS_COMPLETED = 0   ACTUAL_TOTAL_SUBMISSIONS = 0
SIDECAR_CONTAINERS_STARTED = 0   OPTION_A_SURREAL_ISOLATION = NOT_ENTERED
NORMAL_DB_MUTATION = NO   DEV/HOLDOUT = 0/0   ATTEMPT_6 = NOT_RUN
H1/H2/H3 = UNCONFIRMED   ROOT_CAUSE_CONFIRMED = NO
```

Reauthorization #4 produced **zero** diagnostic evidence — it is not a repetition, provider
failure, or LightRAG failure.

## 2. Confirmed defect + forensic

The committed `DockerCellProcessController.start` started each fresh cell container with only
`--name`, `-p`, `-e WORKSPACE`, `-v <storage>`, and optionally `-e LIGHTRAG_API_KEY`. It did
**not** pass the OpenRouter LLM/embedding bindings; `CellProcessSpec` had no binding fields;
nothing in the eval live path set them. The bindings live only in
`deploy/graphrag-poc/docker-compose.graphrag.yml` (which the `docker run` path does not use),
so a cell container fell back to the image's Ollama default (`http://localhost:11434`,
confirmed in the 08E.3 `/health` forensic) — every extraction would fail on a missing binding.

**Verified pinned-v1.5.6 container variables** (from the compose service the image reads):
`LLM_BINDING`, `LLM_MODEL`, `LLM_BINDING_HOST`, `LLM_BINDING_API_KEY`, `EMBEDDING_BINDING`,
`EMBEDDING_MODEL`, `EMBEDDING_BINDING_HOST`, `EMBEDDING_BINDING_API_KEY`.

## 3. Frozen binding contract

New `provider_binding08.py` — `DiagnosticProviderBinding08`, a typed immutable structure that
carries only **content-safe** values (public binding/model/host strings + the secret env
**NAMES**), never a secret value. `frozen_provider_binding()` returns the single approved
benchmark binding and `validate()` fails closed on any drift (this is NOT a model-selector):

```
LIGHTRAG_VERSION        = v1.5.6
LLM_BINDING             = openai
LLM_MODEL               = openai/gpt-4o-mini
LLM_BINDING_HOST        = https://openrouter.ai/api/v1   (public; not a credential)
LLM_SECRET_ENV          = OPENROUTER_API_KEY             (variable NAME only)
EMBEDDING_BINDING       = openai
EMBEDDING_MODEL         = openai/text-embedding-3-small
EMBEDDING_DIMENSION     = 1536
EMBEDDING_BINDING_HOST  = https://openrouter.ai/api/v1
EMBEDDING_SECRET_ENV    = OPENROUTER_API_KEY
```

The two secret boundaries stay separate (§6): the **sidecar** auth key is
`GRAPHRAG_POC_API_KEY`; the **external provider** credential is `OPENROUTER_API_KEY`.

## 4. Secret-safe Docker transport

`DockerCellProcessController.start` now injects the binding: **public** values on argv
(`-e LLM_BINDING=openai`, …), and **secret** values by environment INHERITANCE — the value is
resolved late from its source env, placed in the `subprocess.run(env=…)` map, and passed to
Docker with a **bare** `-e <NAME>` (never `-e NAME=value`). So a provider/sidecar key never
appears on argv, in a repr, log, exception, `AttemptRecord`, or artifact. The pre-existing
`LIGHTRAG_API_KEY` argv placement is converted to the same inheritance mechanism. A missing
required provider secret fails closed at launch, naming only the variable.

## 5. Runtime attestation of the binding

`DockerRuntimeAttestor` + `parse_runtime_inspect` + `CellRuntimeAttestation` gain: the four
PUBLIC binding values (`LLM_BINDING`/`LLM_MODEL`/`EMBEDDING_BINDING`/`EMBEDDING_MODEL`) via a
whitelist template that emits ONLY those keys (never the `*_API_KEY` vars), and provider-secret
PRESENCE (a `PRESENT` sentinel, never the value). The provisioner's `_attest_provider_binding`
readiness gate (behind `require_provider_binding`) proves the owned container is bound to the
frozen models AND that both provider secrets are present — else `CellProviderBindingError`
(fail closed, no Ollama/default fallback). This is ADDITIONAL to the 08E.3 workspace/storage/
version/ownership attestation, which is unchanged.

## 6. Orchestrator + default_live_deps

`OrchestratorDeps` gains `provider_binding`; `run()` requires + validates it and, after the
authorization gate, fails fast (content-safe, name-only) on a missing provider secret
(`REQUIRED_RUNTIME_SECRET_MISSING=<name>`). `default_live_deps` now wires the frozen binding
into a `ProvisionerConfig(provider_binding=…, require_provider_binding=True)`, so **every** cell
receives the identical frozen binding. Credential/binding presence never implies authorization:
`authorized_live=True` AND Option-A active AND a valid provisioned+attested cell are still
required before any provider-backed indexing.

## 7. Execution completeness (the key acceptance criterion, §72/§83)

After 08E.5, a future authorized run —
`LiveDiagnosticOrchestrator08(bench, default_live_deps(eval_root, …)).run(authorized_live=True)`
— executes the complete chain (Option-A → temp Model → Sources+canonical embedding → fresh
container **with provider bindings injected** → workspace/storage/version/provider-binding
attested → owned endpoint → index → poll → `AttemptRecord` → cleanup → global cleanup) with
**no new code**. The operator supplies only the authorization and the runtime secrets
(`OPENROUTER_API_KEY`, and `GRAPHRAG_POC_API_KEY` if the sidecar enforces auth); no manual
public-provider wiring is required.

```
FUTURE_LIVE_RUN_REQUIRES_NEW_CODE = NO
FUTURE_LIVE_RUN_REQUIRES_MANUAL_PUBLIC_PROVIDER_WIRING = NO
FUTURE_LIVE_RUN_REQUIRES_ONLY_OPERATOR_AUTHORIZATION_AND_RUNTIME_SECRETS = YES
```

## 7a. Independent review (preserved honestly)

Outcome: **PASS — 0 HIGH.** The reviewer confirmed the secret-safety design is sound (no
secret value reaches argv, repr, logs, exceptions, or attestation), the fail-closed gates are
correctly layered, the frozen binding is enforced with no silent substitution, the two secret
boundaries are separate, and **execution completeness = YES** (no new code for the next
reauthorization). Findings and disposition:

- **MEDIUM-1 — `EMBEDDING_DIM` not transported/attested — documented (no transport change).**
  The binding validates `embedding_dim=1536` but injects no `EMBEDDING_DIM` container var. This
  is **intentional and compose-aligned**: `docker-compose.graphrag.yml` (the approved PoC
  reference) also omits `EMBEDDING_DIM`, the pinned image's `openai` embedding binding derives
  the dimension from the model/endpoint, and injecting a var compose does not set would break
  the §44 compose-parity guarantee. The field now carries an explicit comment; a live run's
  first cell embedding is where any real dim mismatch would surface (uniform, operator-visible)
  — flagged for the reauthorization, not a silent gap.
- **LOW-1 — provider host not attested — FIXED.** `LLM_BINDING_HOST`/`EMBEDDING_BINDING_HOST`
  are now in the whitelist template + attestation, and `_attest_provider_binding` verifies them
  against the frozen host (a wrong/localhost host fails closed). Pinned by a new test.
- **LOW-3 — binding gate lacked ownership/liveness checks — FIXED.** The gate now also requires
  `evidence_available`, `container_identity == owned handle`, and `running is True` (symmetry
  with the workspace gate). Pinned by a new foreign-container test.
- **LOW-2 — attestor inspected twice per cell (workspace + binding) — accepted.** The container
  config is immutable after start, so both inspects observe identical state; this is pure
  efficiency (a single-inspect optimization is a future cleanup), not a correctness defect.

## 8. Scope

Only provider-binding transport/attestation changed. No change to Source content, the cell
plan (`[1,2,4,8]`, 2 reps, 30/64), concurrency, retry policy/allowlist/`MAX_INDEX_ATTEMPTS`,
provider/model choice, the LightRAG pin, or the fixture
(`a58a68535c345e18f0263904f818e4e2068a164056408665d8bb9233eceb143d` unchanged). No migration,
no `.env`, no production change; production imports no eval code. No secret value is stored,
logged, or committed.

## 9. Posture

```
LIVE_DIAGNOSTIC_AUTHORIZED = NO   LIVE_DIAGNOSTIC_EXECUTED = NO   ATTEMPT_6 = NOT_RUN
PROVIDER_TRAFFIC = NO   ROOT_CAUSE_CONFIRMED = NO   H1/H2/H3 = UNCONFIRMED
FULL_EXECUTION_AUTHORIZED = NO   VALUE_EVIDENCE_READY = NO
```

Running the bounded diagnostic (Stage B) still requires a **separate explicit operator
reauthorization**.
