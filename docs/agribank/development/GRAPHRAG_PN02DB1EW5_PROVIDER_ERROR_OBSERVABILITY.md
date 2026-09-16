# GraphRAG-PN02D-B1-EW5 — Sanitized Provider Error Observability

**Status:** IMPLEMENTATION + REMEDIATION #1–#5 COMPLETE — READY FOR INDEPENDENT CODEX RE-REVIEW #6 (target `D_PASS_CLEAN`). Executable engineering (M1 safe adapter + M2 EW5 governance repoint) is CLOSED and independently re-confirmed; the review lineage below records how the residual doc/comment/function-name-purity findings (RR2-M1, RR3-M1, RR4-M1/M2, RR5-M1/M2) were closed. Current executable state: EW5 governance repoint COMPLETE, safe OpenRouter adapter IMPLEMENTED, canonical 401-vs-404 DISTINCT, real-path A-vs-B = YES, EW4 historical, checkpoint requires no further production/governance code. Not yet checkpointed. No commit/tag/push. Zero provider traffic.

**Baseline HEAD (unchanged this turn):** `1949b9281d97733bd3438ecaade78d2554555a95` (EW4 tag `graphrag-pn02db1ew4-boot2-readiness-approved`). Worktree DIRTY (expected — implementation not yet checkpointed).

## Why

GraphRAG-PN02D-B1 EXEC #6 failed at the first live corpus source embedding
(`commands/embedding_commands.py:365 → generate_embeddings → embedding_model.aembed → esperanto OpenRouterEmbeddingModel.aembed → POST OpenRouter /embeddings`).
The EF2 forensic (`GRAPHRAG_PN02DB1EF2` memory) proved the local embedding path is sound
(a provider-free fake 1536-dim vector completes) and isolated the failure to the **live
provider call** — but the content-safe CLI reduced the exception to only
`error_type = RuntimeError`, too lossy to distinguish:

- **A. PROVIDER_AUTH_OR_CREDENTIAL** (e.g. 401/403), vs
- **B. PROVIDER_ENDPOINT_OR_CAPABILITY** (e.g. 404 route/model).

EW5 adds a **safe-by-construction** structured diagnostic so a later authorized run can
tell provider failure FAMILIES apart **without ever exposing a secret**.

## What changed (production)

- **NEW `open_notebook/utils/provider_errors.py`** — the classifier:
  - `ProviderErrorDiagnostic` (frozen dataclass; allowlisted fields only): `operation`,
    `provider_name`, `provider_error_class`, `provider_http_status`, `provider_error_code`,
    `retryability`, `provider_request_reached`.
  - `classify_provider_error(exc, *, operation, provider_name)` — reads ONLY the exception
    type + structured attributes and maps to a FIXED vocabulary. Never reads `str(exc)`,
    `repr(exc)`, a response body, request headers, or an API key.
  - `attach_provider_diagnostic` / `extract_attached_diagnostic` / `safe_provider_error_fields`
    (walks the `__cause__`/`__context__` chain, bounded).
- **`open_notebook/utils/embedding.py`** — `generate_embeddings` attaches a diagnostic
  (`operation="embedding"`) to the wrapped `RuntimeError`. **Message, retry count (3), and
  control flow are UNCHANGED; the original cause is preserved (`raise … from e`).**
- **`open_notebook/integrations/graphrag/eval/cli_live_pn02d.py`** — the FAILED payload now
  includes a `provider_error` object (safe fields), preferring the diagnostic attached at
  the failure source; otherwise it classifies the caught exception generically.
- **`authmintlivepn02d.py`** — `EXPECTED_EW5_CHECKPOINT_TAG` declared/exported AND (in
  Remediation #1, see below) governance repointed: `_APPROVED_B1_R2_CHECKPOINT ==
  EXPECTED_EW5_CHECKPOINT_TAG`, EW4 demoted to HISTORICAL. `authb1r2pn02d.py` re-exports the
  EW5 successor.

## Safety model

- **Safe by construction (§7/§19):** the primary boundary is allowlisted structured
  extraction + fixed vocabulary — NOT echo-then-regex. A narrow regex on the one free-ish
  field (error code) is defense-in-depth only, and drops any code that is not a strict
  short ASCII token or that carries token/`Bearer` material (a legitimate code such as
  `invalid_api_key` is retained).
- **HTTP status is structured-attribute-only (§10):** taken from `exc.response.status_code`
  or a direct integer status attribute — never parsed from exception text.
- **Retryability is REPORTED, never enforced (§12):** the classifier holds no retry loop or
  sleep. Runtime retry behavior is unchanged.

## Remediation #1 (closes Codex Review #1 findings M1 + M2)

**M1 — canonical structured status preserved before flattening.** NEW repository-owned
`open_notebook/ai/safe_openrouter_embedding.py`: `SafeOpenRouterEmbeddingModel`
subclasses the pinned esperanto `OpenRouterEmbeddingModel` and overrides ONLY `_handle_error`
so an HTTP `>= 400` raises `ProviderEmbeddingHTTPError` carrying a STRUCTURED integer
`status_code` (and a sanitized structured `code`, e.g. `invalid_api_key`) at the exact point
the `httpx` response is still in hand — never a raw message/body/header. `ModelManager.get_model`
routes provider `openrouter` embeddings through `build_safe_openrouter_embedding_model` (all
other providers/modalities untouched; the installed package is NOT edited —
`THIRD_PARTY_PACKAGE_FILES_CHANGED = NO`). Result: canonical 400/401/403/404/429/5xx now map
to distinct safe classes with the real status, and **401 (auth) vs 404 (endpoint/capability)
is distinguishable on the exact production adapter path** (`REAL_PATH_A_VS_B_DISCRIMINATION =
YES`, `DIAGNOSTIC_CAPTURE_POINT = APPROPRIATE`). Network/timeout still surface as `httpx`
exceptions → transport family. No raw-exception-text parsing.

**M2 — governance repoint EW4 → EW5 completed in-repo.** `_APPROVED_B1_R2_CHECKPOINT` now
= `EXPECTED_EW5_CHECKPOINT_TAG`; EW4 demoted to HISTORICAL (its constant/tag/commit facts
preserved); `authb1r2pn02d.B1_R2_EXPECTED_CHECKPOINT_TAG` re-exports EW5. The cross-phase
"current == EW4" invariant assertions in the B0C-B / R2 / EW1–EW4 suites were updated to
"current == EW5" now (not deferred). So after the EW5 commit + exact tag creation the current
checkpoint is EW5, EW4 cannot substitute (peel≠HEAD), the mint binds EW5, and the operator
grant stays separate — the checkpoint turn requires NO further governance/production code
(`CHECKPOINT_CAN_BE_CREATED_WITHOUT_NEW_CODE_CHANGES = YES`).

## Explicitly deferred / not done

- **Retry-classification defect (KNOWN_FOLLOWUP, §5/§38/§47):** EF2 observed 401/404 each
  retried 3× before wrapping. Remediation #1 does NOT change retry semantics
  (`EMBEDDING_MAX_RETRIES` still 3); the new structured status may make a future retry-policy
  remediation easier but it is out of scope here.
- **No provider/model/dimension/budget/scientific-envelope change; no EXEC #7; no B2.**

## Verification

- Provider-free tests (`tests/test_graphrag_pn02db1ew5.py`, 56): the original observability
  suite plus Remediation #1's canonical-adapter cases (real `SafeOpenRouterEmbeddingModel`
  via `httpx.MockTransport` for 400/401/403/404/429/5xx, 401-vs-404 distinct, network,
  1536 success, `generate_embeddings` end-to-end, model-manager wiring) and the M2 governance
  cases (repoint complete, exact-EW5 accepted, wrong-peel fail-closed, EW5 lifecycle State A/B,
  tag-alone-can't-mint) — plus Remediation #2's full historical-substitution parametrization
  (EW4/EW3/EW2/EW1/PF1/B1-R2/arbitrary all rejected).
- Governance cross-phase suites (B0C-B / R2 / EW1–EW5) updated to `current == EW5`.
- Regression (`-k graphrag`): **1280 passed / 9 skipped / 0 failed** (no regressions; +4 vs the
  1276 post-Remediation-#1 count = the added substitution parametrization).
- `ruff` clean; `git diff --check` clean; `mypy` clean on the new/changed modules (0 new; 5
  pre-existing errors remain in the untouched `concurrency_diag08.py`); `.venv` unchanged.

## Review lineage

- Codex Review #1 = **B_REMEDIATION_REQUIRED** (M1 esperanto flattening; M2 governance repoint) → Remediation #1.
- Codex Re-Review #2 = **B_REMEDIATION_REQUIRED** (executable M1+M2 CLOSED; RR2-M1 stale docs; RR2-L1 substitution-test params) → Remediation #2.
- Codex Re-Review #3 = **B_REMEDIATION_REQUIRED** (executable state re-confirmed clean; RR3-M1: ~14 stale current-state prose comments/docstrings in the control-plane modules still named EW4/EW3 as current) → Remediation #3 (module/doc comment/docstring sweep).
- Codex Re-Review #4 = **B_REMEDIATION_REQUIRED** (RR3-M1 still open — Rem #3 missed the cross-phase TEST files; RR4-M1: stale current-state comments + function names in tests/{r2,ew2,ew3,ew4}; RR4-M2: CURRENT_PHASE over-claim) → Remediation #4 (cross-phase test comment/docstring/function-name sweep + CURRENT_PHASE sync — assertions/parametrization UNCHANGED). **Rem #4 was again a cited-line-only patch and left residual stale current-state prose/function names — it did NOT achieve STALE=0.**
- Codex Re-Review #5 = **B_REMEDIATION_REQUIRED** (executable M1+M2 re-confirmed CLOSED; RR5-M1: residual stale current-state prose/function names across the changed surface — `authmintlivepn02d.py` "(that is now `EXPECTED_EW3/EW4`)" current-pointers, and `_cannot_substitute_for_ew3/ew4` test function names + current-state comments in tests/{ew2,ew3,ew4}; RR5-M2: CURRENT_PHASE/EW5-doc over-claim of "STALE=0" while RR5-M1 stood) → **Remediation #5 (this): a full idiom-level stale-prose/function-name sweep of ALL 17 changed files (NOT cited-line-only), verified with an independent final re-grep. Comments/docstrings/Markdown/test-function-names ONLY; production/governance executable, assertions, parametrization and fixtures UNCHANGED.** Fixes: `authmintlivepn02d.py` current-identity pointers (PF1/EW1/EW2/EW3 blocks) → `EXPECTED_EW5_CHECKPOINT_TAG`; renamed the current-EW5-asserting `test_ew2_..._cannot_substitute_for_ew3` → `_for_ew5`; reframed the self-contained historical scenario tests `test_..._cannot_substitute_for_ew1/ew3/ew4` → `_in_historical_ew1/ew3/ew4_scenario` (§20 historical-name allowance, made explicit so a static reviewer cannot read them as current-substitution); fixed stale current-state comments in tests/{ew1,ew2,ew3,r2}. Final independent re-grep: **0 stale current-state prose, 0 stale current-state test function names.**

## Next

Independent Codex EW5 **re-review #6** (target `D_PASS_CLEAN` — 0 HIGH / 0 MEDIUM / 0 LOW) →
operator-approved EW5 checkpoint (verify/stage/commit/create exact tag
`graphrag-pn02db1ew5-provider-error-observability-approved`/State-A→B validation/push — NO
further code changes) → fresh operator authorization for EXEC #7.
