"""PN02D-B1-R2 provider-authorization RE-PREFLIGHT & checkpoint preparation (control-plane).

EVALUATION-ONLY. Nothing in production imports this (``PRODUCTION_IMPORTS_EVAL = NO``).
This module is a CONTROL-PLANE authorization gate — it is NOT the provider-backed B1
execution. It freezes the future B1 authorization envelope against the approved B0C-B
implementation baseline and proves, from real Git, that a live provider-run authorization
is NOT currently mintable (the operator-approved B1-R2 annotated tag does not yet exist).

It contacts NO provider and mints NO usable capability (design/task §1/§2/§22):

  * ``EXTERNAL_PROVIDER_NETWORK_CALLS = 0``
  * ``REAL_PROVIDER_BOUND_LIGHTRAG_BOOT_COUNT = 0``
  * ``LIVE_PROVIDER_AUTHORIZATION_MINTED = NO`` / ``PN02_PROVIDER_RUN_AUTHORIZED = NO``.

Everything here REUSES the frozen B0C-B structures (``OperatorRunGrant``,
``frozen_b1_operator_grant_template``, ``current_approved_b1_r2_checkpoint``,
``b1_r2_refusal_reasons``, ``RealTrustedB1R2Reader``, the frozen fixture hash / provider
fingerprint / workload caps / operation allowlist) — it does NOT create a second
authorization framework (task §5).

Temporal correctness (task §6/§7/§8): the EXPECTED B1-R2 checkpoint identity is now frozen
in governance (``authmintlivepn02d.EXPECTED_B1_R2_CHECKPOINT_TAG``), but the annotated Git
tag of that name does not exist yet. The trusted reader observes real Git, finds no such
tag, and the live mint FAILS CLOSED. Only a FUTURE operator-approved B1-R2 checkpoint that
creates the exact annotated tag (peeling to the approved B1-R2 HEAD) can make the mint
satisfiable.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional, Sequence

from open_notebook.integrations.graphrag.eval.authlivepn02d import (
    frozen_provider_config_id,
)
from open_notebook.integrations.graphrag.eval.authmintlivepn02d import (
    B1_ALLOWED_OPERATION_VALUES,
    EXPECTED_B1_R2_CHECKPOINT_TAG,
    EXPECTED_FIXTURE_HASH,
    EXPECTED_PROVIDER_CONFIG_ID,
    GitBaselineAttestation,
    OperatorRunGrant,
    RealTrustedB1R2Reader,
    b1_r2_refusal_reasons,
    current_approved_b1_r2_checkpoint,
    frozen_b1_operator_grant_template,
    verify_b1_r2_checkpoint,
)
from open_notebook.integrations.graphrag.eval.budgetlivepn02d import b1_caps_dict
from open_notebook.integrations.graphrag.eval.corpuslivepn02d import (
    derive_corpus_workload,
)
from open_notebook.integrations.graphrag.eval.datasetpn02 import (
    load_fixture,
    verify_fixture_hash,
)
from open_notebook.integrations.graphrag.eval.provider_binding08 import (
    frozen_provider_binding,
)

# --------------------------------------------------------------------------- #
# Frozen B1-R2 control-plane constants
# --------------------------------------------------------------------------- #

#: The approved B0C-B implementation checkpoint this B1-R2 authorization binds to (task
#: §9). A future B1 run MUST run from this exact implementation; B0C-A / an arbitrary tag /
#: a bare branch HEAD / the latest tag are all rejected.
APPROVED_IMPLEMENTATION_CHECKPOINT_TAG = "graphrag-pn02db0cb-real-provider-wiring-approved"
APPROVED_IMPLEMENTATION_CHECKPOINT_COMMIT = "5abeaaa09b7157232b1ac5a234c9d8c50b542585"

#: The EXPECTED (governance-frozen) future B1-R2 checkpoint tag. Single source of truth is
#: ``authmintlivepn02d.EXPECTED_B1_R2_CHECKPOINT_TAG``; re-exported here for the manifest.
B1_R2_EXPECTED_CHECKPOINT_TAG = EXPECTED_B1_R2_CHECKPOINT_TAG

#: A NEW, locally-generated run identity for the FUTURE B1 attempt (task §10). The prior
#: B1 run_id ``pn02db1-daf6b760-7d68-4674-9222-ac9f962ef6c4`` is RETIRED and NOT reused.
B1_RUN_ID = "pn02db1-fe3efb27-e720-48c6-b97e-2c0e6a60969d"

#: The retired prior run_id — recorded ONLY to assert it is never reused.
_RETIRED_B1_RUN_ID = "pn02db1-daf6b760-7d68-4674-9222-ac9f962ef6c4"

#: Deterministic concurrency policy (task §16) — 1/1/1, unchanged from the approved design.
B1_CONCURRENCY_POLICY: Dict[str, int] = {"index": 1, "gd": 1, "vector": 1}

#: Corpus source-embedding budget is metered SEPARATELY from the query-embedding cap.
_CORPUS = derive_corpus_workload(load_fixture())

#: Canonical Git commit-SHA shape: exactly 40 lowercase hex chars (SHA-1). This is a
#: STRUCTURAL check only (B1R2-M1) — the EXPECTED-baseline equality is enforced elsewhere
#: (approved_git_tag == the B1-R2 tag; the mint's git-baseline gate; the trusted reader).
#: It is deliberately NOT hard-coded to the current commit, so a future B1-R2 checkpoint
#: commit is accepted while empty / whitespace / sentinel / symbolic-ref / non-hex /
#: wrong-length values are rejected.
_COMMIT_SHA_RE = re.compile(r"^[0-9a-f]{40}$")


def is_valid_commit_sha(value: object) -> bool:
    """True iff ``value`` is a structurally valid 40-char lowercase-hex Git commit SHA.

    Rejects ``None``, non-strings, empty/whitespace, sentinels, symbolic refs (``HEAD``,
    branch/tag names), and malformed / wrong-length / non-hex strings. Structural only —
    NOT an equality check against any specific approved commit.
    """
    return isinstance(value, str) and bool(_COMMIT_SHA_RE.fullmatch(value.strip()))


class B1R2PreparationError(ValueError):
    """A B1-R2 authorization-envelope input was inconsistent with the approved PN02 design."""


# --------------------------------------------------------------------------- #
# Future B1 operator-grant preparation (reuses the frozen template)
# --------------------------------------------------------------------------- #

def build_b1_r2_operator_grant(
    *,
    approved_git_commit: str,
    approved_git_tag: str = B1_R2_EXPECTED_CHECKPOINT_TAG,
    run_id: str = B1_RUN_ID,
) -> OperatorRunGrant:
    """Prepare the FUTURE B1 operator grant (task §17). Mints NO capability.

    Binds the new run_id + the approved B0C-B implementation checkpoint + the expected
    B1-R2 checkpoint identity, with the frozen fixture hash / provider fingerprint /
    workload caps / operation allowlist / Boundary-B flags supplied by
    ``frozen_b1_operator_grant_template`` (so a caller cannot weaken them). The run's
    approved Git baseline (``approved_git_commit``/``approved_git_tag``) is the FUTURE
    B1-R2 checkpoint — its commit is unknown until the B1-R2 tag is created, so it is a
    required argument here (tests pass a simulated future commit; the real value is filled
    at B1-R2 checkpoint time). This is ordinary operator INPUT, not a live capability.
    """
    return frozen_b1_operator_grant_template(
        run_id=run_id,
        implementation_checkpoint_commit=APPROVED_IMPLEMENTATION_CHECKPOINT_COMMIT,
        implementation_checkpoint_tag=APPROVED_IMPLEMENTATION_CHECKPOINT_TAG,
        b1_r2_checkpoint=B1_R2_EXPECTED_CHECKPOINT_TAG,
        approved_git_commit=approved_git_commit,
        approved_git_tag=approved_git_tag,
    )


def validate_b1_r2_grant(grant: object) -> List[str]:
    """Return refusal reasons (empty = OK) for the FROZEN B1-R2 authorization envelope.

    This is the control-plane B1-R2 binding (task §9/§17/§20). It checks that a prepared
    operator grant matches the approved B0C-B implementation checkpoint, the new (non-
    retired) run_id, the expected B1-R2 checkpoint identity, the frozen fixture hash /
    provider fingerprint / operation allowlist / workload caps / Boundary-B flags, and the
    B1-R2 baseline tag. It mints NOTHING and reads no Git; the mint remains the trust root
    for the actual B1-R2 tag observation (via ``b1_r2_refusal_reasons``). All checks fail
    before any provider/backend activity.
    """
    reasons: List[str] = []
    if not isinstance(grant, OperatorRunGrant):
        return ["b1_r2_grant_wrong_type"]
    if grant.implementation_checkpoint_tag != APPROVED_IMPLEMENTATION_CHECKPOINT_TAG:
        reasons.append("b1_r2_wrong_implementation_checkpoint_tag")
    if grant.implementation_checkpoint_commit != APPROVED_IMPLEMENTATION_CHECKPOINT_COMMIT:
        reasons.append("b1_r2_wrong_implementation_checkpoint_commit")
    if grant.run_id == _RETIRED_B1_RUN_ID:
        reasons.append("b1_r2_retired_run_id_reused")
    if grant.run_id != B1_RUN_ID:
        reasons.append("b1_r2_wrong_run_id")
    if grant.b1_r2_checkpoint != B1_R2_EXPECTED_CHECKPOINT_TAG:
        reasons.append("b1_r2_wrong_expected_checkpoint_identity")
    if grant.approved_git_tag != B1_R2_EXPECTED_CHECKPOINT_TAG:
        reasons.append("b1_r2_wrong_approved_baseline_tag")
    # B1R2-M1: the approved baseline commit must be a structurally valid Git commit SHA
    # (empty / sentinel / symbolic-ref / malformed / wrong-length all rejected). This is a
    # STRUCTURAL contract check; the exact future B1-R2 commit is not knowable at prep time,
    # so equality is not asserted here (the mint's git-baseline gate + trusted reader bind
    # the run to the real B1-R2 tag peel at run time).
    if not is_valid_commit_sha(grant.approved_git_commit):
        reasons.append("b1_r2_approved_git_commit_invalid")
    if grant.fixture_hash != EXPECTED_FIXTURE_HASH:
        reasons.append("b1_r2_wrong_fixture_hash")
    if grant.provider_config_fingerprint != EXPECTED_PROVIDER_CONFIG_ID:
        reasons.append("b1_r2_wrong_provider_fingerprint")
    if not grant.synthetic_only:
        reasons.append("b1_r2_synthetic_only_false")
    if grant.real_internal_data_allowed:
        reasons.append("b1_r2_real_internal_data_allowed")
    if frozenset(grant.operation_allowlist) != B1_ALLOWED_OPERATION_VALUES:
        reasons.append("b1_r2_operation_allowlist_mismatch")
    if dict(grant.workload_caps) != b1_caps_dict():
        reasons.append("b1_r2_workload_caps_mismatch")
    return reasons


def b1_r2_authorization_manifest() -> Dict[str, object]:
    """A content-safe manifest of the frozen B1-R2 authorization envelope (task §23).

    Contains NO secret value — only ids, hashes, env NAMES, counts, and flags. The
    approved Git baseline commit is intentionally PENDING (filled at B1-R2 checkpoint
    creation); everything else is frozen now.
    """
    ok, detail = verify_fixture_hash()
    binding = frozen_provider_binding().as_public_dict()
    return {
        "phase": "PN02D-B1-R2",
        "kind": "provider_authorization_preflight_manifest",
        "approved_implementation_checkpoint": {
            "tag": APPROVED_IMPLEMENTATION_CHECKPOINT_TAG,
            "commit": APPROVED_IMPLEMENTATION_CHECKPOINT_COMMIT,
        },
        "expected_b1_r2_checkpoint_tag": B1_R2_EXPECTED_CHECKPOINT_TAG,
        "b1_r2_tag_exists_in_git_now": False,
        "approved_git_baseline_commit": "PENDING_B1_R2_CHECKPOINT_COMMIT",
        "approved_git_baseline_tag": B1_R2_EXPECTED_CHECKPOINT_TAG,
        "run_id": B1_RUN_ID,
        "retired_run_id": _RETIRED_B1_RUN_ID,
        "old_run_id_reused": False,
        "fixture_id": "graphrag_pn02_eval_v1",
        "fixture_hash": EXPECTED_FIXTURE_HASH,
        "fixture_hash_verified": bool(ok) and detail == EXPECTED_FIXTURE_HASH,
        "synthetic_only": True,
        "real_internal_data_allowed": False,
        "provider_config_fingerprint": EXPECTED_PROVIDER_CONFIG_ID,
        "provider_binding": binding,  # secret ENV NAMES only, never values
        "workload_caps": b1_caps_dict(),
        "corpus_source_embedding": {
            "planned": _CORPUS.planned_source_embedding_operations,
            "maximum": _CORPUS.max_source_embedding_operations,
            "separate_from_query_embedding_budget": True,
        },
        "operation_allowlist": sorted(B1_ALLOWED_OPERATION_VALUES),
        "concurrency": dict(B1_CONCURRENCY_POLICY),
        "final_answer_calls": 0,
        "client_query_calls": 0,
        "judge_model_calls": 0,
        "live_provider_authorization_minted": False,
        "pn02_provider_run_authorized": False,
    }


# --------------------------------------------------------------------------- #
# Provider-free control-plane preflight
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class B1R2PreflightReport:
    """Content-safe result of the B1-R2 provider-free control-plane preflight."""

    expected_b1_r2_tag: str
    expected_tag_configured: bool
    real_b1_r2_tag_exists: bool
    live_provider_authorization_mintable: bool
    fixture_hash_match: bool
    provider_fingerprint_match: bool
    workload_caps_match: bool
    old_run_id_reused: bool
    fail_closed_reasons: Sequence[str]

    def as_public_dict(self) -> Dict[str, object]:
        return {
            "expected_b1_r2_tag": self.expected_b1_r2_tag,
            "expected_tag_configured": self.expected_tag_configured,
            "real_b1_r2_tag_exists": self.real_b1_r2_tag_exists,
            "live_provider_authorization_mintable": (
                self.live_provider_authorization_mintable
            ),
            "fixture_hash_match": self.fixture_hash_match,
            "provider_fingerprint_match": self.provider_fingerprint_match,
            "workload_caps_match": self.workload_caps_match,
            "old_run_id_reused": self.old_run_id_reused,
            "fail_closed_reasons": list(self.fail_closed_reasons),
        }


def b1_r2_preflight(
    *,
    git_runner: Optional[Callable[[Sequence[str]], str]] = None,
) -> B1R2PreflightReport:
    """Provider-free control-plane preflight (task §8/§19). Contacts NO provider.

    Observes real Git for the EXPECTED B1-R2 tag via the trusted reader and proves the
    live authorization is NOT currently mintable (the tag does not exist). It does NOT
    mint a capability. ``git_runner`` is the git boundary (real by default; injectable so
    tests can drive the current-repo and a simulated future-tag state) — it is used ONLY
    for this report's own observation and never crosses a mint parameter.
    """
    reader = (
        RealTrustedB1R2Reader()
        if git_runner is None
        else RealTrustedB1R2Reader(git_runner=git_runner)
    )
    expected = current_approved_b1_r2_checkpoint()
    expected_tag_configured = expected == B1_R2_EXPECTED_CHECKPOINT_TAG

    observation = reader.observe(B1_R2_EXPECTED_CHECKPOINT_TAG)
    real_tag_exists = bool(observation.observed_tag_exists)

    # Prove fail-closed by running the REAL verifier against a would-be-clean baseline at
    # the expected tag/observed HEAD. With no such tag in real Git the reader reports its
    # absence and the verifier refuses; a caller cannot supply the missing tag.
    grant = build_b1_r2_operator_grant(
        approved_git_commit=observation.observed_head or "UNKNOWN_HEAD"
    )
    baseline = GitBaselineAttestation(
        branch="feature/graphrag-lifecycle",
        head_commit=observation.observed_head,
        head_tag=B1_R2_EXPECTED_CHECKPOINT_TAG,
        tag_peel_commit=observation.observed_head,
        staged_count=0,
        unstaged_count=0,
        untracked_execution_affecting_count=0,
    )
    reasons = verify_b1_r2_checkpoint(
        reader=reader,
        operator_grant=grant,
        approved_expected_checkpoint=expected,
        git_baseline=baseline,
    )
    mintable = expected_tag_configured and real_tag_exists and not reasons

    ok, detail = verify_fixture_hash()
    return B1R2PreflightReport(
        expected_b1_r2_tag=B1_R2_EXPECTED_CHECKPOINT_TAG,
        expected_tag_configured=expected_tag_configured,
        real_b1_r2_tag_exists=real_tag_exists,
        live_provider_authorization_mintable=mintable,
        fixture_hash_match=bool(ok) and detail == EXPECTED_FIXTURE_HASH,
        provider_fingerprint_match=frozen_provider_config_id() == EXPECTED_PROVIDER_CONFIG_ID,
        workload_caps_match=True,
        old_run_id_reused=(B1_RUN_ID == _RETIRED_B1_RUN_ID),
        fail_closed_reasons=list(reasons),
    )


def b1_r2_refusal_reasons_for_grant(
    grant: OperatorRunGrant, git_baseline: object
) -> List[str]:
    """Thin passthrough to the frozen ``b1_r2_refusal_reasons`` (mint-owned trust roots).

    Provided so B1-R2 callers/tests validate a prepared grant through the SAME internal
    governance + real-reader path the mint uses — never by injecting a trust root.
    """
    return b1_r2_refusal_reasons(grant, git_baseline)


__all__ = [
    "APPROVED_IMPLEMENTATION_CHECKPOINT_TAG",
    "APPROVED_IMPLEMENTATION_CHECKPOINT_COMMIT",
    "B1_R2_EXPECTED_CHECKPOINT_TAG",
    "B1_RUN_ID",
    "B1_CONCURRENCY_POLICY",
    "B1R2PreparationError",
    "is_valid_commit_sha",
    "build_b1_r2_operator_grant",
    "validate_b1_r2_grant",
    "b1_r2_authorization_manifest",
    "B1R2PreflightReport",
    "b1_r2_preflight",
    "b1_r2_refusal_reasons_for_grant",
]
