# GraphRAG-PN02D-B1-R2 — Provider-Authorization Re-Preflight & Checkpoint Preparation

**Phase kind:** CONTROL-PLANE authorization gate. NOT the provider-backed B1 execution.
ZERO provider traffic. NOT checkpointed in this turn; the mandatory Codex independent
review is a separate gate that must run before any B1-R2 checkpoint.

## Purpose

Freeze the future B1 authorization envelope against the approved B0C-B implementation
baseline, and prove — from real Git — that a live provider-run authorization is **not
currently mintable** because the operator-approved B1-R2 annotated tag does not yet exist.
This removes the earlier circularity: the code can now *know* the exact expected B1-R2 tag
before that Git tag exists, and fail closed on its absence.

Everything reuses the frozen B0C-B structures (`OperatorRunGrant`,
`frozen_b1_operator_grant_template`, `current_approved_b1_r2_checkpoint`,
`b1_r2_refusal_reasons`, `verify_b1_r2_checkpoint`, `RealTrustedB1R2Reader`, the frozen
fixture hash / provider fingerprint / workload caps / operation allowlist). No second
authorization framework was created.

## Approved B0C-B baseline (bound)

| Field | Value |
|---|---|
| Implementation checkpoint tag | `graphrag-pn02db0cb-real-provider-wiring-approved` |
| Implementation checkpoint commit | `5abeaaa09b7157232b1ac5a234c9d8c50b542585` |
| Branch | `feature/graphrag-lifecycle` |
| Backup parity | local == backup == `5abeaaa…` (origin untouched) |

## Frozen B1-R2 authorization envelope

| Item | Value |
|---|---|
| Expected B1-R2 checkpoint tag | `graphrag-pn02db1r2-provider-authorization-preflight-approved` |
| B1-R2 tag exists in Git now | **NO** (fail-closed) |
| New B1 run_id | `pn02db1-fe3efb27-e720-48c6-b97e-2c0e6a60969d` |
| Retired (never reused) run_id | `pn02db1-daf6b760-7d68-4674-9222-ac9f962ef6c4` |
| Fixture | `graphrag_pn02_eval_v1` |
| Fixture hash | `9ce7df742810424d9ef7b7b34962187d9061245f81471f5fb1d7f2608f6899a6` (verified) |
| Synthetic-only | YES (`real_internal_data_allowed=False`) |
| Provider fingerprint | `pbf_1811d0bfd5cfad2743ffaa69` |
| Provider binding | OpenRouter · `openai/gpt-4o-mini` · `openai/text-embedding-3-small` · dim 1536 · secret env NAME `OPENROUTER_API_KEY` (name only, never a value) |
| Concurrency | index 1 / gd 1 / vector 1 |

## Workload caps (from the approved PN02 design — not invented)

| Operation | Planned | Maximum | Retry policy | Budget owner |
|---|---|---|---|---|
| Corpus source embeddings | 21 | 21 | none (once per isolated corpus) | corpus provisioner (separate budget) |
| Graph index operations | 24 | 24 | — | `GRAPH_INDEX_OPERATION` |
| Graph index attempts | — | 48 | ≤2 per source (delete-then-insert) | `GRAPH_INDEX_ATTEMPT` |
| Graph delete (per workspace derived doc) | — | 1 | — | `GRAPH_DELETE` |
| GD `/query/data` queries | — | 26 | caller-owned | `GD_QUERY` |
| Vector notebook queries | — | 26 | caller-owned | `VECTOR_QUERY` |
| Query embeddings | — | 26 | K3/K5 share one per vector query | `QUERY_EMBEDDING` |
| Final-answer calls | 0 | 0 | forbidden in B1 | `FINAL_ANSWER` (hard 0) |
| `client.query()` | 0 | 0 | forbidden | `CLIENT_QUERY` (hard 0) |
| Judge-model calls | 0 | 0 | forbidden in B1 | `JUDGE_MODEL` (hard 0) |

Corpus source embeddings (21) are metered **separately** from the query-embedding cap (26).

## Operation allowlist (frozen)

`VECTOR_QUERY_EMBEDDING`, `GD_QUERY_DATA`, `GRAPH_DELETE`, `INDEX_REQUIRED_EMBEDDING`,
`GRAPH_INDEX`, `VECTOR_NOTEBOOK_QUERY`. Final-answer generation, Ask integration,
production-retrieval activation, unbounded query loops, unbounded retries, and unapproved
delete are all excluded.

## Governance transition (the only production change)

`authmintlivepn02d`:

- `EXPECTED_B1_R2_CHECKPOINT_TAG = "graphrag-pn02db1r2-provider-authorization-preflight-approved"`.
- `_APPROVED_B1_R2_CHECKPOINT` flipped from `None` → `EXPECTED_B1_R2_CHECKPOINT_TAG`
  (`current_approved_b1_r2_checkpoint()` now returns the expected tag).
- The B0C-B implementation tag added to `_KNOWN_NON_B1_R2_CHECKPOINTS` (so neither the
  B0C-A design tag nor the B0C-B implementation tag can masquerade as B1-R2).

The mint's trust model is otherwise **unchanged** (RR3–RR5): it owns the trusted read
internally, exposes no trust-root parameter, and resolves governance + the real Git reader
without caller input.

## Pre-tag fail-closed property (load-bearing)

With governance frozen to the expected tag **but the tag absent from real Git**, the real
production mint fails closed:

```
current_approved_b1_r2_checkpoint() = graphrag-pn02db1r2-provider-authorization-preflight-approved
real B1-R2 tag exists in Git         = NO
→ trusted reader observes no such tag
→ b1_r2_tag_not_observed_in_git
→ live provider authorization NOT mintable
```

`EXPECTED_B1_R2_TAG_CONFIGURED = YES`, `REAL_B1_R2_TAG_CURRENTLY_EXISTS = NO`,
`PRETAG_LIVE_PROVIDER_AUTHORIZATION_MINTABLE = NO`.

Only a FUTURE operator-approved B1-R2 checkpoint that creates the exact annotated tag
(peeling to the approved B1-R2 HEAD, clean tree, matching baseline) can make the mint
satisfiable.

## Negative tests (all fail before any provider/backend activity)

Envelope (`validate_b1_r2_grant`) + git-observed (`verify_b1_r2_checkpoint`) + mint gates
cover: expected-tag-configured-but-absent; B0C-A cannot substitute; B0C-B implementation
tag cannot substitute; arbitrary tag at HEAD cannot substitute; wrong B1-R2 tag name;
correct tag / wrong peel; correct peel / wrong baseline HEAD; dirty tree; wrong B0C-B
implementation checkpoint; wrong / retired run_id; wrong provider fingerprint;
`synthetic_only=false`; workload-cap escalation; unauthorized operation; and — preserved
from RR4/RR5 — caller-controlled reader/approved-identity injection is impossible (the mint
exposes no such parameter; signature-asserted).

## Future-positive simulation

Exercised by patching the mint's INTERNAL governance + trusted-reader (module boundary) to
a SYNTHETIC future tag/commit and invoking the **production** mint signature (no trust-root
parameters). It proves the whole envelope becomes satisfiable after a real B1-R2 checkpoint,
**without creating the real tag** and **without yielding an operator-usable authorization
against the current repository**.

## Secret safety & counters

`SECRET_VALUES_IN_B1_R2 = 0` (only env NAMES/config metadata). Provider counters all zero:
`EXTERNAL_PROVIDER_NETWORK_CALLS = 0`, `REAL_PROVIDER_BOUND_LIGHTRAG_BOOT_COUNT = 0`,
`REAL_INDEX/GD/VECTOR/DELETE_OPERATIONS = 0`, `FINAL_ANSWER_CALLS = 0`,
`NORMAL_DB_MUTATIONS = 0`, `NEW_MIGRATIONS = 0`, `PRODUCTION_IMPORTS_EVAL = NO`.

## Files

- `open_notebook/integrations/graphrag/eval/authmintlivepn02d.py` — governance transition
  (expected tag frozen; B0C-B tag added to the non-B1-R2 denylist).
- `open_notebook/integrations/graphrag/eval/authb1r2pn02d.py` — NEW control-plane module:
  `EXPECTED`/run_id/impl constants, `build_b1_r2_operator_grant`, `validate_b1_r2_grant`,
  `b1_r2_authorization_manifest`, `b1_r2_preflight`.
- `tests/test_graphrag_pn02db1r2.py` — NEW B1-R2 tests.
- `tests/test_graphrag_pn02db0cb_adapters.py` / `_live.py` — updated the 6 tests that
  asserted the pre-transition governance state (fail-closed reason shifted from
  "not approved" to "tag not observed in Git").

## Governance (retained)

`B1_R2_CHECKPOINT_ALLOWED = NO`, `LIVE_PROVIDER_AUTHORIZATION_MINTED = NO`,
`PN02_PROVIDER_RUN_AUTHORIZED = NO`, `PN02D_B2_QA_AUTHORIZED = NO`,
`GRAPHRAG_PRODUCTION_INTEGRATION = NOT_APPROVED`, `LIGHTRAG_ASK_INTEGRATION = NOT_APPROVED`,
`GRAPH_RAG_09_JUSTIFIED = NO`. `CODEX_B1_R2_REVIEW = NOT_RUN`.

**STOP:** `GRAPH_RAG_PN02DB1R2_IMPLEMENTATION_READY_FOR_CODEX_REVIEW`. No checkpoint; no
tag; no Codex review in this turn.

---

## Codex Independent Review #1 → Remediation Cycle #1

Codex Independent Review #1 returned **decision B — REMEDIATION_REQUIRED (0 HIGH / 1
MEDIUM / 1 LOW)**. Every load-bearing property PASSED (pre-tag fail-closed, exact-tag-only,
B0C-A/B0C-B/arbitrary substitution blocked, trust-root regression ABSENT (RR4/RR5 intact),
baseline/peel binding, run_id crosscheck, fixture/fingerprint/synthetic/caps/allowlist/
concurrency, manifest-not-authority, provider-free preflight, simulation-creates-no-real-
auth, secret-safe, zero-provider). Two findings, both remediated this cycle (test/doc +
control-plane hardening; NO change to the mint trust model or the live path):

- **B1R2-M1 — MEDIUM (REFINEMENT) — REMEDIATED.** `validate_b1_r2_grant` did not validate
  `approved_git_commit`, so an empty / sentinel / symbolic-ref / malformed / wrong-length
  value passed the standalone control-plane envelope validator (NOT a live bypass — the
  mint and CLI already reject simulation/missing commits downstream, so the live path
  stayed fail-closed). Fix: added `is_valid_commit_sha` (exact 40-lowercase-hex, SHA-1
  shape) and a `b1_r2_approved_git_commit_invalid` reason in `validate_b1_r2_grant`. This
  is STRUCTURAL only — it is deliberately NOT hard-coded to the current commit, so a future
  B1-R2 checkpoint commit is accepted while empty/whitespace/sentinel/`HEAD`/branch/tag/
  non-hex/39-/41-hex/UPPER-hex values are rejected; the EXPECTED-baseline equality remains
  the mint's git-baseline gate + trusted-reader peel binding.
- **B1R2-L1 — LOW (NONE) — REMEDIATED.** The governance record said "30 B1-R2 tests"; there
  were 27 test *functions* collecting to 30 pytest *cases* via parametrization. Corrected to
  the accurate count. After Cycle #1 the B1-R2 suite is **31 test functions / 47 collected
  cases** (added a 14-case `approved_git_commit` negative parametrization + 3 positive/unit
  functions).

**Cycle #1 result:** `B1R2_M1_REMEDIATED=YES`, `B1R2_L1_REMEDIATED=YES`,
`APPROVED_GIT_COMMIT_VALIDATION=PASS`, `APPROVED_GIT_COMMIT_VALIDATION_HARDCODED_TO_CURRENT_SHA=NO`,
`PRETAG_LIVE_PROVIDER_AUTHORIZATION_MINTABLE=NO` (unchanged). All Codex-#1 PASS areas
preserved. `CODEX_B1_R2_REVIEW_1=FAIL_B` (retained); `CODEX_B1_R2_REREVIEW_1=NOT_RUN`;
`B1_R2_CHECKPOINT_ALLOWED=NO`. No production change to the mint/driver/CLI trust model.

**STOP:** `GRAPH_RAG_PN02DB1R2_REMEDIATION_CYCLE_1_READY_FOR_CODEX_REREVIEW`.

---

## Checkpoint-lifecycle model & test-robustness remediation

The B1-R2 checkpoint transitions the repository between two legitimate states, and the
test suite must stay green in BOTH:

- **STATE A — PRE-CHECKPOINT:** the expected tag is frozen in governance but the annotated
  Git tag is ABSENT → the trusted reader observes no tag → the B1-R2 Git prerequisite is
  UNSATISFIED → live mint fails closed (`b1_r2_tag_not_observed_in_git`).
- **STATE B — POST-CHECKPOINT:** the exact annotated tag exists and peels to the approved
  B1-R2 checkpoint commit at the authorized HEAD → the B1-R2 Git prerequisite is
  SATISFIABLE.

**Neither state authorizes a provider run.** The Git checkpoint is only ONE prerequisite;
`PN02_PROVIDER_RUN_AUTHORIZED` stays NO and no `LiveProviderRunAuthorization` is minted
until a separate operator authorization + a genuine real-preflight capability + operator
grant + clean baseline. In `b1_r2_preflight`, `live_provider_authorization_mintable`
reflects ONLY whether the control-plane B1-R2 Git gate would pass — it is NOT a statement
that a provider run has been authorized. (The manifest's `b1_r2_tag_exists_in_git_now` is a
prep-time snapshot value, not a live observation.)

### Blocker found at the first checkpoint attempt (and its remediation)

The first FINAL CHECKPOINT attempt was correctly BLOCKED before any Git mutation: several
committed tests asserted the pre-tag reality *unconditionally* against the REAL expected
tag (`observed_tag_exists is False`, `real_b1_r2_tag_exists is False`, or
`b1_r2_tag_not_observed_in_git` for the real `EXPECTED_B1_R2_CHECKPOINT_TAG`), so creating
the tag — the checkpoint's own artifact — would have turned the committed suite red.

**Remediation (tests/docs only — no production authorization logic changed):**
- **Permanent negatives** (tag-absent / substitution / `tag_not_observed`) now use the
  SYNTHETIC `C.TEST_B1R2_TAG` (which never becomes a real Git tag) — via scripted readers or
  a governance-only patch (`C.governance_expects_tag`, which pins the approved identity but
  keeps the real reader). These hold in State A and State B forever.
- **Real-expected-tag tests are now lifecycle-aware**: `test_real_b1_r2_checkpoint_git_state_is_valid`
  and `test_preflight_reports_git_gate_state` branch on the observed Git state and strongly
  assert the correct State-A or State-B invariant.
- **Grant-substitution direct-mint tests** now assert the stable `b1_r2_grant_identity_mismatch`
  (a grant's B1-R2 identity must equal the governance-approved identity) — load-bearing and
  independent of the real tag's existence.
- Added `test_git_gate_satisfiable_does_not_authorize_provider_run` (Git gate satisfiable ⇒
  provider run still NOT authorized). B1-R2 suite is now **32 test functions / 48 collected
  cases**. State-B greenness was verified via a forced-reader simulation (no Git mutation,
  no tag created).

`PRODUCTION_AUTHORIZATION_LOGIC_CHANGED = NO`. The production trust model (exact tag match,
real-Git trusted reader, baseline/peel binding, internal governance ownership, no caller
reader/identity injection, fingerprint/run_id/fixture/synthetic/caps/allowlist/concurrency)
is unchanged.

**STOP:** `GRAPH_RAG_PN02DB1R2_CHECKPOINT_TEST_REMEDIATION_READY_FOR_CODEX_REREVIEW`.
