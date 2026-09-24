"""Real (operator-granted) provider-run mint path for the PN02 LIVE driver (PN02D-B0C-B).

EVALUATION-ONLY. Nothing in production imports this (``PRODUCTION_IMPORTS_EVAL =
NO``). This module is the REAL counterpart to
``driverpn02d.build_simulation_provider_run_authorization`` (design §7/§13/§14): it
mints a provider-run capability for a FUTURE authorized live run, driven by an
*operator-approved one-run grant* and a *genuine* real-preflight capability — never
from simulated gate booleans.

Two things are minted here:

  * the frozen ``PN02ProviderRunAuthorization`` the shared executors already consume
    (via ``authlivepn02d.mint_provider_run_authorization``, with a REAL git baseline,
    never ``"SIMULATION"``); and
  * a distinct **``LiveProviderRunAuthorization``** capability — an unforgeable
    (module-private ``_LIVE_AUTH_KEY``-minted, ``__slots__``) wrapper the live runtime
    manager / provider binder / ``execute-b1-live`` entrypoint require. A boolean
    (``PN02_PROVIDER_RUN_AUTHORIZED == True``) is deliberately *insufficient* (task
    §12): every live seam requires this capability object, and a simulation
    authorization (a plain ``PN02ProviderRunAuthorization``) is rejected by
    ``require_live_provider_run_authorization`` because it is the wrong TYPE (task
    §16). B0C-B mints this only with mock/synthetic inputs against mocked backends —
    it authorizes NO real provider run (``PN02_PROVIDER_RUN_AUTHORIZED`` stays NO).

Secret safety (design §17): NO secret value is ever accepted, stored, hashed,
logged, serialized, or placed in an exception here — only public config identity,
env NAMES, ids, counts, and flags.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import (
    Callable,
    Dict,
    FrozenSet,
    List,
    Mapping,
    Optional,
    Protocol,
    Sequence,
    Tuple,
)

from open_notebook.integrations.graphrag.eval.attestpn02d import (
    RealLightRAGPreflightAuthorization,
    require_real_preflight_authorization,
)
from open_notebook.integrations.graphrag.eval.authlivepn02d import (
    B1_ALLOWED_OPERATION_CLASSES,
    PN02ProviderRunAuthorization,
    ProviderOperationClass,
    RunIdConsistencyError,
    assert_run_ids_consistent,
    frozen_provider_config_id,
    mint_provider_run_authorization,
)
from open_notebook.integrations.graphrag.eval.budgetlivepn02d import (
    b1_caps_dict,
    b2_caps_dict,
)

#: The frozen PN02 fixture hash (design §5). Re-verified live at mint.
EXPECTED_FIXTURE_HASH = (
    "9ce7df742810424d9ef7b7b34962187d9061245f81471f5fb1d7f2608f6899a6"
)

#: The frozen provider-config fingerprint (design §6/§11).
EXPECTED_PROVIDER_CONFIG_ID = "pbf_1811d0bfd5cfad2743ffaa69"

#: Git-baseline sentinels that mark a SIMULATION/offline auth. A real live run must
#: NOT carry any of these — the whole point of the real mint (design §7/§14).
_SIMULATION_BASELINE_SENTINELS = frozenset({"", "SIMULATION", "OFFLINE", "DRYRUN"})

#: The frozen B1 operation allowlist as string values (design §13/§44).
B1_ALLOWED_OPERATION_VALUES: FrozenSet[str] = frozenset(
    c.value for c in B1_ALLOWED_OPERATION_CLASSES
)

#: The frozen PN02D-B2 operation allowlist as string values. It is the B1 allowlist PLUS the
#: three Open-Notebook-owned QA final-answer arms (PN02A §10a) — and NOTHING else. The
#: LightRAG-owned final answer (``LIGHTRAG_FINAL_ANSWER``), ``CLIENT_QUERY``, ``JUDGE_MODEL``
#: and any production op stay FORBIDDEN in B2 exactly as in B1 (deterministic grading, no
#: judge; ON owns the answer). A B1 run NEVER carries these QA ops (B1 excludes QA, design
#: §43); the B1 allowlist above is unchanged.
B2_ALLOWED_OPERATION_VALUES: FrozenSet[str] = B1_ALLOWED_OPERATION_VALUES | frozenset(
    {
        ProviderOperationClass.QA_V.value,
        ProviderOperationClass.QA_GD.value,
        ProviderOperationClass.QA_V_GD.value,
    }
)


class LiveProviderRunAuthorizationError(ValueError):
    """The inputs to mint a LiveProviderRunAuthorization were invalid (fail-closed)."""


class LiveProviderRunNotAuthorized(RuntimeError):
    """A LIVE seam (binder/runtime/entrypoint) was reached without a live capability."""


class OperatorGrantError(ValueError):
    """An operator run grant was malformed, defaulted, or of the wrong TYPE."""


class GitBaselineError(ValueError):
    """The observed git baseline is dirty or does not match the approved baseline (H2)."""


# --------------------------------------------------------------------------- #
# Git-baseline attestation (design §42, B0CB-H2) — OBSERVED repository state
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class GitBaselineAttestation:
    """A content-safe attestation of OBSERVED repository state (design §42/B0CB-H2).

    A future live run must attest not just the approved commit/tag but that the working
    tree is CLEAN — an approved HEAD/tag with unapproved working-tree changes must never
    drive provider-bound execution. This carries the OBSERVED state; the mint compares
    it against the operator grant's approved baseline. It is produced by a real git
    reader (``cli_live_pn02d.read_git_baseline``) or an injected fake in tests — NOT a
    caller-supplied boolean. ``is_clean`` is derived from the counts, so a caller cannot
    assert cleanliness without also zeroing the observed change counts.
    """

    branch: str
    head_commit: str
    head_tag: str
    tag_peel_commit: str
    staged_count: int
    unstaged_count: int
    untracked_execution_affecting_count: int

    @property
    def is_clean(self) -> bool:
        return (
            self.staged_count == 0
            and self.unstaged_count == 0
            and self.untracked_execution_affecting_count == 0
        )

    def dirty_reasons(self) -> Tuple[str, ...]:
        reasons: List[str] = []
        if self.staged_count != 0:
            reasons.append("staged_changes_present")
        if self.unstaged_count != 0:
            reasons.append("unstaged_changes_present")
        if self.untracked_execution_affecting_count != 0:
            reasons.append("untracked_execution_affecting_present")
        return tuple(reasons)

    def as_public_dict(self) -> dict:
        return {
            "branch": self.branch,
            "head_commit": self.head_commit,
            "head_tag": self.head_tag,
            "tag_peel_commit": self.tag_peel_commit,
            "staged_count": self.staged_count,
            "unstaged_count": self.unstaged_count,
            "untracked_execution_affecting_count": (
                self.untracked_execution_affecting_count
            ),
            "is_clean": self.is_clean,
        }


def attest_approved_clean_baseline(
    attestation: object,
    *,
    approved_commit: str,
    approved_tag: str,
) -> List[str]:
    """Return refusal reasons (empty = OK) for a clean, approved git baseline (H2).

    Requires: a genuine ``GitBaselineAttestation`` (not a bare boolean/look-alike), a
    CLEAN tree (no staged/unstaged/untracked-execution-affecting changes), and the
    observed HEAD commit + tag + tag-peel matching the approved baseline. All checks
    are fail-closed BEFORE any binding/runtime/backend invocation.
    """
    if not isinstance(attestation, GitBaselineAttestation):
        return ["git_baseline_attestation_wrong_type"]
    reasons: List[str] = list(attestation.dirty_reasons())
    if not approved_commit or approved_commit in _SIMULATION_BASELINE_SENTINELS:
        reasons.append("approved_commit_missing_or_simulation")
    if not approved_tag or approved_tag in _SIMULATION_BASELINE_SENTINELS:
        reasons.append("approved_tag_missing_or_simulation")
    # B0CB-H2-R1: an EMPTY observed tag/peel (no exact tag on HEAD) can never satisfy an
    # approved-tag identity — there is no branch-name fallback upstream, so this fails.
    if not attestation.head_tag:
        reasons.append("no_exact_tag_on_head")
    if not attestation.tag_peel_commit:
        reasons.append("no_observed_tag_peel")
    if attestation.head_commit != approved_commit:
        reasons.append("git_commit_baseline_mismatch")
    if attestation.head_tag != approved_tag:
        reasons.append("git_tag_baseline_mismatch")
    if attestation.tag_peel_commit != approved_commit:
        reasons.append("tag_peel_mismatch")
    # §4: the actual HEAD must equal the peeled approved tag commit.
    if attestation.head_commit != attestation.tag_peel_commit:
        reasons.append("head_not_at_tag_peel")
    return reasons


# --------------------------------------------------------------------------- #
# B1-R2 checkpoint verification (B0CB-RR2-M1 / B0CB-RR3-H1) — trusted-reader model
# --------------------------------------------------------------------------- #
#
# TRUST MODEL (B0CB-RR3-H1 correction). Trust originates from a TRUSTED READER that
# INDEPENDENTLY observes real Git inside the mint boundary — NEVER from caller-supplied
# data. The mint no longer accepts a pre-built attestation object asserting the RESULT;
# it accepts an (injected, real-by-default) reader and invokes it itself. The reader is
# the ONLY producer of a ``TrustedB1R2Observation`` (module-private ``__slots__`` key
# mint), so a self-constructed public look-alike cannot cross the security boundary
# (``SELF_CONSTRUCTED_PUBLIC_DATACLASS_CAN_AUTHORIZE = NO``). The approved-EXPECTED B1-R2
# identity comes from GOVERNANCE (``current_approved_b1_r2_checkpoint``), now the PN02D-B1-EW8
# checkpoint tag; the trusted reader observes real Git and, while that annotated tag is ABSENT,
# a live authorization is not currently mintable, regardless of what a caller supplies
# (fail-closed).

class B1R2CheckpointError(ValueError):
    """The B1-R2 checkpoint identity is missing/unapproved/not trust-observed (RR2-M1/RR3-H1)."""


#: Placeholder/sentinel values that can NEVER be an approved B1-R2 checkpoint identity.
#: The configured approved identity is the EW8 checkpoint tag; these sentinels are never it, so
#: any of them (or an unset identity) fails closed.
_B1_R2_SENTINELS = frozenset(
    {
        "", "NOT_STARTED", "NOT_AUTHORIZED", "TBD", "PENDING", "FUTURE", "UNKNOWN",
        "NONE", "SIMULATION", "OFFLINE", "DRYRUN", "PLACEHOLDER", "B1-R2-NOT-STARTED",
    }
)

#: Checkpoints that are DEFINITELY NOT the B1-R2 checkpoint. Even if a caller names one as
#: the approved-expected identity it can never authorize a B1-R2 run. This is an EXACT-
#: string SAFETY denylist of known-other checkpoints (NOT a naming/prefix/approval
#: heuristic): the B0C-A design tag AND the B0C-B implementation tag both really peel to a
#: real commit, so without this they could masquerade as B1-R2 (B0CB-RR3-H1 exploit class;
#: PN02D-B1-R2 §20.2/§20.3 — neither B0C-A nor B0C-B may substitute). §8/§11.
_KNOWN_NON_B1_R2_CHECKPOINTS = frozenset(
    {
        "graphrag-pn02db0ca-real-provider-wiring-design-approved",
        "graphrag-pn02db0cb-real-provider-wiring-approved",
    }
)

#: HISTORICAL — the PN02D-B1-R2 provider-authorization-preflight checkpoint tag. It is an
#: IMMUTABLE record of approved commit ``611532c`` (created BACKUP-only). PN02D-B1-PF1 then
#: found a real-Docker readiness defect in the provider-free preflight; fixing it moves HEAD
#: off ``611532c``, and the B1-R2 tag no longer peels to the authorized HEAD, so the B1-R2
#: Git gate is superseded (``b1_r2_tag_not_at_authorized_head``). This constant is retained
#: for reference/history ONLY — it is NO LONGER the governance-approved identity.
EXPECTED_B1_R2_CHECKPOINT_TAG = "graphrag-pn02db1r2-provider-authorization-preflight-approved"

#: HISTORICAL — the PN02D-B1-PF1 preflight-readiness checkpoint tag. It is an IMMUTABLE
#: record of approved commit ``082dc95`` (created BACKUP-only) and WAS the governance-approved
#: identity through the PF1 phase. PN02D-B1-EW1 then wires the missing real B1 execution
#: composition (the real-seams builder + real ``execute-b1-live`` entrypoint); committing that
#: production change moves HEAD off ``082dc95``, and the PF1 tag no longer peels to the
#: authorized HEAD, so the PF1 Git gate is superseded (``b1_r2_tag_not_at_authorized_head``).
#: This constant is retained for reference/history ONLY — it is NO LONGER the governance-
#: approved identity (that is now ``EXPECTED_EW8_CHECKPOINT_TAG``).
EXPECTED_PF1_CHECKPOINT_TAG = "graphrag-pn02db1pf1-preflight-readiness-approved"

#: HISTORICAL as of PN02D-B1-EW2 — the PN02D-B1-EW1 real-execution-wiring checkpoint tag,
#: an IMMUTABLE record of approved commit ``1b8ca5b`` and the governance-approved identity
#: THROUGH the EW1 phase. PN02D-B1-EW2 then adds the isolated-namespace embedding-model seed
#: (the real B1 execution defect fix); committing that production change moves HEAD off
#: ``1b8ca5b``, and the EW1 tag no longer peels to the authorized HEAD, so the EW1 Git gate is
#: superseded. Retained for reference/history ONLY — it is NO LONGER the governance-approved
#: identity (that is now ``EXPECTED_EW8_CHECKPOINT_TAG``).
EXPECTED_EW1_CHECKPOINT_TAG = "graphrag-pn02db1ew1-real-execution-wiring-approved"

#: PN02D-B1-EW2 checkpoint. Its annotated tag now EXISTS in real Git (peeling to the approved
#: EW2 commit ``707c8782``). Retained for reference/history ONLY — it is NO LONGER the
#: governance-approved identity: the EW3 isolation-runtime-id fix (a real B1 execution defect
#: fix) is a NEW production change that moves HEAD past ``707c8782``, so the EW2 tag no longer
#: peels to the authorized HEAD and the EW2 Git gate is superseded (that is now
#: ``EXPECTED_EW8_CHECKPOINT_TAG``).
EXPECTED_EW2_CHECKPOINT_TAG = "graphrag-pn02db1ew2-isolated-model-seed-approved"

#: HISTORICAL as of PN02D-B1-EW4 — the PN02D-B1-EW3 isolation-runtime-id-compatibility
#: checkpoint tag. Its annotated tag EXISTS in real Git peeling to the approved EW3 commit
#: ``9f4172815c91cbcebd7ea3ffbb38333e4db37bf0`` and it WAS the governance-approved identity
#: THROUGH the EW3 phase. PN02D-B1-EW4 then adds the missing Boot-2 (provider-bound execution)
#: readiness wait (the real B1 execution defect exposed by EXEC #4 / diagnosed by EF1); that
#: NEW production change moves HEAD past ``9f41728``, so the EW3 tag no longer peels to the
#: authorized HEAD and the EW3 Git gate is superseded. Retained for reference/history ONLY —
#: it is NO LONGER the governance-approved identity (that is now ``EXPECTED_EW8_CHECKPOINT_TAG``).
EXPECTED_EW3_CHECKPOINT_TAG = "graphrag-pn02db1ew3-isolation-id-compat-approved"

#: HISTORICAL as of PN02D-B1-EW5 — the PN02D-B1-EW4 Boot-2 readiness-parity checkpoint tag.
#: Its annotated Git tag EXISTS peeling to the approved EW4 commit
#: ``1949b9281d97733bd3438ecaade78d2554555a95`` and it WAS the governance-approved identity
#: THROUGH the EW4 phase. PN02D-B1-EW5 adds sanitized provider-error observability (the EXEC #6
#: first-corpus-embedding diagnosis gap surfaced by EF2, remediated to preserve structured
#: provider status) — that NEW production change moves HEAD past ``1949b92``, so the EW4 tag no
#: longer peels to the authorized HEAD and the EW4 Git gate is superseded. Retained for
#: reference/history ONLY — it is NO LONGER the governance-approved identity (that is now
#: ``EXPECTED_EW8_CHECKPOINT_TAG``).
EXPECTED_EW4_CHECKPOINT_TAG = "graphrag-pn02db1ew4-boot2-readiness-approved"

#: HISTORICAL as of PN02D-B1-EW6 — the PN02D-B1-EW5 sanitized-provider-error-observability
#: checkpoint tag. Its annotated Git tag EXISTS peeling to the approved EW5 commit
#: ``8b0325a75d442976939e22f5a62e11bcf2f6461d`` and it WAS the governance-approved identity
#: THROUGH the EW5 phase. PN02D-B1-EW6 adds the non-destructive index-conflict recovery
#: (the EXEC #8 LightRAG HTTP 409 defect diagnosed by EF3: a retry re-POSTed an already-
#: accepted ``file_source``; EW6 resumes/observes the accepted track instead) — that NEW
#: production change moves HEAD past ``8b0325a``, so the EW5 tag no longer peels to the
#: authorized HEAD and the EW5 Git gate is superseded. Retained for reference/history ONLY —
#: it is NO LONGER the governance-approved identity (that is now ``EXPECTED_EW8_CHECKPOINT_TAG``).
EXPECTED_EW5_CHECKPOINT_TAG = "graphrag-pn02db1ew5-provider-error-observability-approved"

#: HISTORICAL as of PN02D-B1-EW7 — the PN02D-B1-EW6 index-conflict-recovery checkpoint tag.
#: Its annotated Git tag EXISTS peeling to the approved EW6 commit
#: ``0baacef33f033482b97434702c47b8bce30b5943`` and it WAS the governance-approved identity
#: THROUGH the EW6 phase. PN02D-B1-EW7 adds the bounded index-observation timing (the EXEC #10
#: <24/24 defect diagnosed by EF4: the count-only poll loop had ZERO inter-poll delay and
#: abandoned accepted documents while still IN_PROGRESS; EW7 waits between polls) — that NEW
#: production change moves HEAD past ``0baacef``, so the EW6 tag no longer peels to the
#: authorized HEAD and the EW6 Git gate is superseded. Retained for reference/history ONLY —
#: it is NO LONGER the governance-approved identity (that is now ``EXPECTED_EW8_CHECKPOINT_TAG``).
EXPECTED_EW6_CHECKPOINT_TAG = "graphrag-pn02db1ew6-index-conflict-recovery-approved"

#: HISTORICAL as of PN02D-B1-EW8 — the PN02D-B1-EW7 index-observation-timing checkpoint tag.
#: Its annotated Git tag EXISTS peeling to the approved EW7 commit
#: ``ab90202e1fe4b9b5938cd927aa69c7bc3a4224f6`` and it WAS the governance-approved identity
#: THROUGH the EW7 phase. PN02D-B1-EW8 adds scientific-result observability (EF5: the frozen
#: evaluator already computed the scientific result into ``outcome.report`` but the CLI dropped
#: it; EW8 projects it) — that NEW production change moves HEAD past ``ab90202``, so the EW7 tag
#: no longer peels to the authorized HEAD and the EW7 Git gate is superseded. Retained for
#: reference/history ONLY — it is NO LONGER the governance-approved identity (that is now
#: ``EXPECTED_EW8_CHECKPOINT_TAG``).
EXPECTED_EW7_CHECKPOINT_TAG = "graphrag-pn02db1ew7-index-observation-timing-approved"

#: The EXACT operator/governance-approved provider-authorization checkpoint identity, now
#: FROZEN to the PN02D-B1-EW8 SUCCESSOR checkpoint (the scientific-result-observability change
#: that moves HEAD past the historical EW7 commit ``ab90202``). This is a control-plane
#: declaration of the future successor tag — it is NOT the tag itself. The annotated Git tag of
#: this name does not exist yet; the trusted reader observes real Git and, finding no such tag,
#: the live mint FAILS CLOSED (PN02D-B1-EW8 §39). Only after a future operator-approved EW8
#: checkpoint creates this exact annotated tag (peeling to the approved EW8 HEAD) can the mint
#: become satisfiable. Freezing the EXPECTED identity before the tag exists removes circularity
#: (the same pattern used for B1-R2 → PF1 → EW1 → EW2 → EW3 → EW4 → EW5 → EW6 → EW7).
EXPECTED_EW8_CHECKPOINT_TAG = "graphrag-pn02db1ew8-scientific-result-observability-approved"

#: Governance state for the provider-authorization checkpoint. FROZEN to the successor
#: ``EXPECTED_EW8_CHECKPOINT_TAG`` (SOLE source of the approved-EXPECTED identity for the
#: real mint — never a caller string / Git-tag heuristic). It is non-None, but the live
#: authorization is STILL not mintable until the trusted reader observes that exact tag in
#: real Git (which does not yet exist): the mint fails closed with
#: ``b1_r2_tag_not_observed_in_git`` (PN02D-B1-EW8 §39). The mint remains FAIL CLOSED before
#: the successor tag exists. EW1, PF1, B1-R2, EW2, EW3, EW4, EW5, EW6 and EW7 are retained as
#: HISTORICAL identities only and can never substitute (exact-identity + trusted-reader §35).
_APPROVED_B1_R2_CHECKPOINT: Optional[str] = EXPECTED_EW8_CHECKPOINT_TAG

#: HISTORICAL — the FIRST PN02D-B2 live-QA-wiring checkpoint tag. It is an IMMUTABLE record of
#: approved commit ``41eb3f3`` (annotated tag object ``dbeba1f3``, created BACKUP-only). A
#: post-checkpoint lifecycle forensic then found two State-A-only mint-identity tests that hard-
#: coded ``b1_r2_tag_not_observed_in_git`` and fail once this tag exists at HEAD (a test-design
#: lifecycle defect — the runtime trust gate was proven correct). The operator selected a
#: SUCCESSOR checkpoint (no retroactive exception); fixing the tests moves HEAD off ``41eb3f3``,
#: so this historical tag no longer peels to the authorized HEAD, and it is NO LONGER the
#: governance-approved B2 identity. It is retained for reference/history ONLY and can never
#: authorize a successor B2 run (exact-identity + trusted-reader + not-at-authorized-HEAD).
HISTORICAL_B2_CHECKPOINT_TAG = "graphrag-pn02db2-qa-live-wiring-approved"

#: HISTORICAL — the PN02D-B2 QA-live-wiring LIFECYCLE (successor) checkpoint tag. It is an
#: IMMUTABLE record of approved commit ``40d3a6f`` (annotated tag object ``a6d38aae``); it was the
#: governance-approved B2 identity between the successor-lifecycle checkpoint and the chat-model
#: remediation. The Real B2 Execution #1 failure (missing isolated default chat model) drove a
#: provider-free chat-model + live_wiring remediation whose D_PASS_CLEAN diff moves HEAD off
#: ``40d3a6f``; a NEW governance identity (below) supersedes it. This predecessor tag is retained
#: for reference/history ONLY and can NEVER authorize the current B2 run — even though it still
#: peels correctly to ``40d3a6f`` (which is now an ancestor, not the authorized HEAD): the B2 gate
#: verifies the EXACT current identity, and an ancestor checkpoint never authorizes a later HEAD.
HISTORICAL_B2_LIFECYCLE_CHECKPOINT_TAG = "graphrag-pn02db2-qa-live-wiring-lifecycle-approved"

#: The EXACT operator/governance-approved checkpoint identity for a PN02D-**B2** live QA run.
#: This is a SEPARATE authorization identity from B1 (never a shared "current checkpoint" that
#: could silently collapse B2 back onto EW8/B1). It is now the CHAT-MODEL-REMEDIATION identity —
#: superseding BOTH the first-B2 tag ``HISTORICAL_B2_CHECKPOINT_TAG`` and the lifecycle/successor
#: tag ``HISTORICAL_B2_LIFECYCLE_CHECKPOINT_TAG`` (both immutable/historical, above). It is a
#: control-plane declaration of the future B2 checkpoint tag — NOT the tag itself. The annotated
#: Git tag of this name does not exist yet; the trusted reader observes real Git and, finding no
#: such tag, a B2 mint FAILS CLOSED (``b1_r2_tag_not_observed_in_git``). B2 becomes mintable only
#: after a future operator-approved checkpoint creates this EXACT annotated tag peeling to the
#: approved B2 HEAD — and neither EW8 (a B1 HEAD), the historical first-B2 tag, NOR the historical
#: lifecycle/predecessor tag can ever satisfy it, because the B2 gate verifies THIS identity.
#: Freezing the EXPECTED identity before the tag exists removes circularity (the same pattern used
#: for B1-R2 → PF1 → EW1 … → EW8, and now first-B2 → lifecycle-B2 → chat-model-B2).
EXPECTED_B2_CHECKPOINT_TAG = "graphrag-pn02db2-chat-model-remediation-approved"

#: Governance state for the B2 provider-authorization checkpoint. FROZEN to the current
#: ``EXPECTED_B2_CHECKPOINT_TAG`` (SOLE source of the approved-EXPECTED identity for the B2 mint —
#: never a caller string / EW8 fallback / historical-first-B2 fallback / historical-lifecycle-B2
#: fallback / Git-tag heuristic). Non-None, but a B2 live authorization is NOT mintable until the
#: trusted reader observes that exact tag in real Git (which does not exist yet): the B2 mint fails
#: closed. B1 identities (EW8 and all historical EWn/PF1/B1-R2) AND both historical B2 tags (first
#: and lifecycle/predecessor) can never substitute for it (exact-identity + trusted-reader).
_APPROVED_B2_CHECKPOINT: Optional[str] = EXPECTED_B2_CHECKPOINT_TAG

#: HISTORICAL evidence of the B3B live-observability-wiring checkpoint. IMMUTABLE; it peels to
#: commit 3eaf28b, which is now an ANCESTOR of the reviewed/checkpointed HEAD. It is retained ONLY
#: as historical provenance and is NEVER the governed B3 LIVE authorization identity (using it
#: would fail closed under exact-HEAD trust, as PN02D-B3I correctly discovered).
HISTORICAL_B3B_CHECKPOINT_TAG = "graphrag-pn02db3b-live-observability-wiring-approved"

#: HISTORICAL evidence of the B3J live-auth successor checkpoint. IMMUTABLE; created at commit
#: b5892409 by the B3J checkpoint, where it peeled to HEAD while B3J was the current live identity.
#: The approved PN02D-B3P implementation checkpoint then advanced HEAD to d539b13, so this tag is now
#: an ANCESTOR and is retained ONLY as historical provenance — it is NEVER the current governed B3
#: LIVE authorization identity (using it now fails closed under exact-HEAD trust, the stale-successor
#: condition, no grandfathering).
HISTORICAL_B3J_CHECKPOINT_TAG = "graphrag-pn02db3j-b3-live-auth-successor-approved"

#: HISTORICAL evidence of the B3Q live-auth successor checkpoint. IMMUTABLE (tag object c2458b78,
#: peel 9acc6aa); it peeled to HEAD while B3Q was the current live identity. The approved PN02D-B3U
#: QA-value observability implementation checkpoint then advanced HEAD to 8cc979d, so this tag is now
#: an ANCESTOR and is retained ONLY as historical provenance — it is NEVER the current governed B3
#: LIVE authorization identity (using it now fails closed under exact-HEAD trust, the stale-successor
#: condition, no grandfathering — as PN02D-B3U's post-checkpoint probe confirmed).
HISTORICAL_B3Q_CHECKPOINT_TAG = "graphrag-pn02db3q-b3-live-auth-successor-approved"

#: PN02D-B3V: the PREDECLARED successor trust anchor for a governed real B3 LIVE authorization at the
#: CURRENT (B3U-checkpointed) HEAD. This annotated tag does NOT exist yet — it is created ONLY by a
#: future, separately-authorized B3V checkpoint (after independent review) pointing at that new commit,
#: so its peel then equals the new HEAD and exact-HEAD trust is satisfiable WITHOUT advancing HEAD past
#: its own identity (the recurring drift the B3B→B3J→B3Q→B3V chain fixes; the B3U/B3P implementation tags
#: must NOT be reused as the live identity). While this tag is ABSENT from real Git the B3 live mint FAILS
#: CLOSED (tag-not-observed) — the correct, intended state during implementation/review. Only the tag
#: NAME is depended on here; no future commit/tag object SHA is hardcoded. Predecessors (all IMMUTABLE,
#: now ancestors, NEVER the live identity): ``HISTORICAL_B3B_CHECKPOINT_TAG``, ``HISTORICAL_B3J_CHECKPOINT_TAG``,
#: ``HISTORICAL_B3Q_CHECKPOINT_TAG``.
EXPECTED_B3_LIVE_CHECKPOINT_TAG = "graphrag-pn02db3v-b3-live-auth-successor-approved"

#: Backward-compatible alias. The governed B3 LIVE authorization identity is now the B3V successor
#: tag (``EXPECTED_B3_LIVE_CHECKPOINT_TAG``); the historical wiring/successor tags are
#: ``HISTORICAL_B3B_CHECKPOINT_TAG`` / ``HISTORICAL_B3J_CHECKPOINT_TAG`` / ``HISTORICAL_B3Q_CHECKPOINT_TAG``.
#: This alias is retained so existing importers/tests that refer to "the expected B3 live identity"
#: continue to resolve it — it is NOT any historical tag.
EXPECTED_B3B_CHECKPOINT_TAG = EXPECTED_B3_LIVE_CHECKPOINT_TAG

#: Governance state for the B3 LIVE provider-authorization checkpoint. FROZEN to the predeclared
#: ``EXPECTED_B3_LIVE_CHECKPOINT_TAG`` (SOLE source of the approved-EXPECTED B3 live identity).
#: Non-None, but not mintable until the trusted reader observes that EXACT tag in real Git at the
#: authorized HEAD; else fails closed. B1/EW8, the B2 tag, the historical B3B/B3J/B3Q tags (now
#: ancestors), and the B3P/B3U implementation tags can never substitute — exact-identity + exact-HEAD, no grandfathering.
_APPROVED_B3B_CHECKPOINT: Optional[str] = EXPECTED_B3_LIVE_CHECKPOINT_TAG

#: Module-private capability key — only a trusted reader can mint a TrustedB1R2Observation.
_B1_R2_TRUSTED_KEY = object()


def current_approved_b1_r2_checkpoint() -> Optional[str]:
    """The operator/governance-approved provider-authorization checkpoint identity.

    Returns the frozen ``EXPECTED_EW8_CHECKPOINT_TAG`` — the PN02D-B1-EW8 SUCCESSOR that
    supersedes the historical EW7 checkpoint (the scientific-result-observability change
    moves HEAD past ``ab90202``, so the EW7 tag no longer peels to the authorized HEAD). It is
    non-None, but a live authorization is still not currently mintable: the trusted reader
    observes real Git and, while no annotated tag of that name exists, the mint fails closed
    with ``b1_r2_tag_not_observed_in_git`` (PN02D-B1-EW8 §39). The value is NEVER derived from
    a caller string, a Git-tag naming heuristic, or a prefix match. EW1, PF1, B1-R2, EW2, EW3,
    EW4, EW5, EW6 and EW7 remain HISTORICAL identities that can never substitute for the current EW8 successor.

    This is a MODULE-LEVEL governance function the mint (and CLI defense-in-depth) look up
    and call INTERNALLY — no caller/parameter can override its result (B0CB-RR4-H1). Tests
    that must simulate the future post-tag-creation state patch THIS function and the
    trusted-reader factory at the module boundary; they never pass an approved identity or
    reader through a public mint parameter, and they never create the real tag.
    """
    return _APPROVED_B1_R2_CHECKPOINT


class TrustedB1R2Observation:
    """Trusted Git observation of a candidate B1-R2 tag — the trusted-reader OUTPUT.

    Produced ONLY by a trusted reader (``RealTrustedB1R2Reader``) via the module-private
    ``_B1_R2_TRUSTED_KEY``; a direct construction raises ``PermissionError``. This is the
    unforgeability boundary (§7/§16): a caller cannot fabricate a "trusted" observation,
    and ``isinstance`` on a public dataclass is NOT relied upon as proof — this type
    cannot be built outside a trusted reader at all.
    """

    __slots__ = ("checkpoint_tag", "observed_tag_exists", "observed_tag_peel", "observed_head")

    def __init__(
        self,
        key: object,
        *,
        checkpoint_tag: str,
        observed_tag_exists: bool,
        observed_tag_peel: str,
        observed_head: str,
    ) -> None:
        if key is not _B1_R2_TRUSTED_KEY:
            raise PermissionError(
                "TrustedB1R2Observation is produced ONLY by a trusted B1-R2 reader "
                "(RealTrustedB1R2Reader); it cannot be constructed directly (fail-closed, "
                "§7/§16)"
            )
        self.checkpoint_tag = checkpoint_tag
        self.observed_tag_exists = observed_tag_exists
        self.observed_tag_peel = observed_tag_peel
        self.observed_head = observed_head

    def as_public_dict(self) -> dict:
        return {
            "checkpoint_tag": self.checkpoint_tag,
            "observed_tag_exists": self.observed_tag_exists,
            "observed_tag_peel": self.observed_tag_peel,
            "observed_head": self.observed_head,
        }


class TrustedB1R2Reader(Protocol):
    """A trusted reader that observes real Git for a candidate B1-R2 checkpoint tag."""

    def observe(self, checkpoint_tag: str) -> TrustedB1R2Observation: ...


def _default_git_runner(args: Sequence[str]) -> str:
    """The default real git command boundary (content-safe: empty string on any error)."""
    import subprocess

    try:
        out = subprocess.run(  # noqa: S603 - fixed argv, no shell
            ["git", *list(args)], capture_output=True, text=True, timeout=10
        )
        return out.stdout.strip() if out.returncode == 0 else ""
    except Exception:  # noqa: BLE001 - a missing git is a content-safe empty result
        return ""


class RealTrustedB1R2Reader:
    """The trusted B1-R2 reader — the ONLY producer of ``TrustedB1R2Observation``.

    It observes ACTUAL Git state for the candidate tag (resolving it ONLY as a
    ``refs/tags/<name>`` tag object — a branch name or raw SHA does not qualify) and mints
    an unforgeable observation with the module-private key. ``git_runner`` is the git
    boundary (real subprocess by default; injectable so the REAL reader logic is
    unit-testable without creating a real tag). The current approved EW8 checkpoint tag does
    not yet exist in real Git, so a real observation reports ``observed_tag_exists=False``.
    """

    def __init__(
        self, git_runner: Callable[[Sequence[str]], str] = _default_git_runner
    ) -> None:
        self._git = git_runner

    def observe(self, checkpoint_tag: str) -> TrustedB1R2Observation:
        name = (checkpoint_tag or "").strip()
        head = self._git(["rev-parse", "HEAD"])
        tag_exists = bool(self._git(["tag", "--list", name])) if name else False
        tag_peel = self._git(["rev-list", "-n", "1", f"refs/tags/{name}"]) if name else ""
        return TrustedB1R2Observation(
            _B1_R2_TRUSTED_KEY,
            checkpoint_tag=checkpoint_tag,
            observed_tag_exists=tag_exists,
            observed_tag_peel=tag_peel,
            observed_head=head,
        )


def verify_b1_r2_checkpoint(
    *,
    reader: TrustedB1R2Reader,
    operator_grant: object,
    approved_expected_checkpoint: Optional[str],
    git_baseline: object,
) -> List[str]:
    """Return refusal reasons (empty = OK) for a trust-observed, approved B1-R2 checkpoint.

    Trust flow (B0CB-RR3-H1): the approved-EXPECTED identity is governance-sourced (never
    a caller assertion); the ``reader`` INDEPENDENTLY observes Git for that EXACT identity
    and returns a ``TrustedB1R2Observation`` that only a trusted reader can produce; and
    the observed HEAD is bound to the approved Git baseline HEAD (§10). Fail-closed on:

      * no approved-expected identity / a sentinel (an unset/placeholder identity, §9);
      * the approved-expected identity being a KNOWN-non-B1-R2 checkpoint (B0C-A) or the
        implementation checkpoint itself (§8/§11 — B0C-A cannot substitute);
      * the operator grant's B1-R2 identity not EXACTLY equalling the approved-expected
        identity (§8);
      * a reader output that is not a genuine ``TrustedB1R2Observation`` (a look-alike);
      * the reader observing a different tag, a nonexistent tag, an empty peel/HEAD, a tag
        not at the authorized HEAD, or a HEAD not bound to the approved Git baseline.
    """
    approved = (approved_expected_checkpoint or "").strip()
    # §9: with NO approved B1-R2 identity (an unset/sentinel governance value) the mint fails
    # closed BEFORE observing Git — a caller cannot supply the missing operator approval.
    if not approved or approved.upper() in _B1_R2_SENTINELS:
        return ["b1_r2_not_approved_fail_closed"]

    reasons: List[str] = []
    # §8/§11: the approved-expected identity must be a genuine, distinct B1-R2 checkpoint.
    if approved in _KNOWN_NON_B1_R2_CHECKPOINTS:
        reasons.append("b1_r2_approved_identity_is_known_non_b1_r2_checkpoint")
    if isinstance(operator_grant, OperatorRunGrant):
        if approved == operator_grant.implementation_checkpoint_tag:
            reasons.append("b1_r2_identity_equals_implementation_checkpoint_tag")
        if approved == operator_grant.implementation_checkpoint_commit:
            reasons.append("b1_r2_identity_equals_implementation_checkpoint_commit")
        # §8: the grant's B1-R2 identity must EXACTLY match the approved-expected identity.
        if operator_grant.b1_r2_checkpoint != approved:
            reasons.append("b1_r2_grant_identity_mismatch")
    else:
        reasons.append("b1_r2_operator_grant_wrong_type")

    # §6/§7: the TRUSTED reader independently observes Git for the EXACT approved identity.
    observation = reader.observe(approved)
    if not isinstance(observation, TrustedB1R2Observation):
        # A look-alike/foreign object from an untrusted reader can never authorize.
        reasons.append("b1_r2_observation_not_trusted_reader_output")
        return reasons
    if observation.checkpoint_tag != approved:
        reasons.append("b1_r2_reader_observed_wrong_tag")
    if not observation.observed_tag_exists:
        reasons.append("b1_r2_tag_not_observed_in_git")
    if not observation.observed_tag_peel:
        reasons.append("b1_r2_tag_peel_not_observed")
    if not observation.observed_head:
        reasons.append("b1_r2_head_not_observed")
    # the approved B1-R2 tag must be present AT the authorized HEAD (Git-verified).
    if observation.observed_tag_peel != observation.observed_head:
        reasons.append("b1_r2_tag_not_at_authorized_head")
    # §10: bind the trust-observed HEAD to the approved Git baseline HEAD / tag peel.
    if isinstance(git_baseline, GitBaselineAttestation):
        if observation.observed_head != git_baseline.head_commit:
            reasons.append("b1_r2_head_not_bound_to_approved_baseline")
        if observation.observed_head != git_baseline.tag_peel_commit:
            reasons.append("b1_r2_head_not_bound_to_baseline_tag_peel")
    else:
        reasons.append("b1_r2_git_baseline_unavailable_for_binding")
    return reasons


def _build_trusted_b1_r2_reader() -> RealTrustedB1R2Reader:
    """Construct the DEFAULT real-Git trusted reader used by the mint (B0CB-RR4-H1).

    A MODULE-LEVEL factory the mint (and CLI defense-in-depth) look up and call
    INTERNALLY — no caller/parameter can substitute a reader or its git boundary. Tests
    that must model a synthetic future Git state patch THIS factory at the module boundary
    (e.g. to return ``RealTrustedB1R2Reader(git_runner=<scripted>)``); production/live
    callers never influence it.
    """
    return RealTrustedB1R2Reader()


def b1_r2_refusal_reasons(operator_grant: object, git_baseline: object) -> List[str]:
    """Resolve the B1-R2 trust roots INTERNALLY and return refusal reasons (empty = OK).

    This is the ONLY B1-R2 gate the mint uses (and the CLI reuses for defense-in-depth).
    Both trust roots — the approved-EXPECTED identity and the trusted reader — are resolved
    from module-level functions (``current_approved_b1_r2_checkpoint`` +
    ``_build_trusted_b1_r2_reader``) with NO caller input (B0CB-RR4-H1): this function
    takes NO reader/identity parameter. ``current_approved_b1_r2_checkpoint()`` returns the
    EW8 checkpoint tag; while that annotated tag is ABSENT from real Git it fails closed. It
    returns refusal reasons only; it CANNOT mint a capability. Tests simulate a future
    approval by patching the two
    module-level functions above — never by passing trust roots here.
    """
    approved_expected = current_approved_b1_r2_checkpoint()
    reader = _build_trusted_b1_r2_reader()
    return verify_b1_r2_checkpoint(
        reader=reader,
        operator_grant=operator_grant,
        approved_expected_checkpoint=approved_expected,
        git_baseline=git_baseline,
    )


def current_approved_b2_checkpoint() -> Optional[str]:
    """The operator/governance-approved checkpoint identity for a PN02D-**B2** live QA run.

    Returns the frozen ``EXPECTED_B2_CHECKPOINT_TAG`` — a SEPARATE identity from
    ``current_approved_b1_r2_checkpoint()`` (which stays EW8). Non-None, but a B2 live
    authorization is NOT mintable until the trusted reader observes that EXACT annotated tag
    in real Git (which does not exist yet): the B2 mint fails closed with
    ``b1_r2_tag_not_observed_in_git``. The value is NEVER derived from a caller string, an EW8
    fallback, or a Git-tag naming heuristic. EW8 and all historical B1 identities can never
    substitute. Tests simulate a future B2 approval by patching THIS function (and the trusted
    reader factory) — never by passing trust roots.
    """
    return _APPROVED_B2_CHECKPOINT


def b2_r2_refusal_reasons(operator_grant: object, git_baseline: object) -> List[str]:
    """Resolve the **B2** trust roots INTERNALLY and return refusal reasons (empty = OK).

    Identical trust machinery to :func:`b1_r2_refusal_reasons` — the SAME hardened
    :func:`verify_b1_r2_checkpoint` verifier and the SAME internal
    ``_build_trusted_b1_r2_reader`` (no duplicated verifier, no weakened checks) — but the
    approved-EXPECTED identity is ``current_approved_b2_checkpoint()`` (the B2 tag), NOT the
    EW8/B1 identity. Both trust roots are resolved from module-level functions with NO caller
    input (B0CB-RR4-H1): this function takes NO reader/identity parameter. While the B2 tag is
    ABSENT from real Git it fails closed; EW8 peeling to today's HEAD cannot satisfy it because
    the grant's ``b1_r2_checkpoint`` must EXACTLY equal the B2 identity and the reader must
    observe THAT tag at HEAD.
    """
    approved_expected = current_approved_b2_checkpoint()
    reader = _build_trusted_b1_r2_reader()
    return verify_b1_r2_checkpoint(
        reader=reader,
        operator_grant=operator_grant,
        approved_expected_checkpoint=approved_expected,
        git_baseline=git_baseline,
    )


def current_approved_b3b_checkpoint() -> Optional[str]:
    """The operator/governance-approved checkpoint identity for a governed real B3 live
    OBSERVABILITY run (PN02D-B3D/B3J/B3Q/B3V). Returns the predeclared successor
    ``EXPECTED_B3_LIVE_CHECKPOINT_TAG`` (``graphrag-pn02db3v-b3-live-auth-successor-approved``) — a
    SEPARATE identity from ``current_approved_b1_r2_checkpoint()`` (EW8),
    ``current_approved_b2_checkpoint()`` (the B2 tag), the historical B3B wiring tag
    (``HISTORICAL_B3B_CHECKPOINT_TAG``), the historical B3J and B3Q successor tags
    (``HISTORICAL_B3J_CHECKPOINT_TAG`` / ``HISTORICAL_B3Q_CHECKPOINT_TAG``) — all now ancestors — the
    B3H governance tag, and the B3P/B3U implementation tags. Non-None, but a B3 live authorization is
    NOT mintable until the trusted reader observes that EXACT annotated tag at the authorized HEAD;
    else it fails closed (PN02D-B3I / the
    post-B3P stale-successor state correctly hit this while the tag is absent). NEVER derived from a
    caller string, a B1/B2/B3B/B3J/B3H/B3P fallback, an ancestor, or a Git-tag heuristic. Tests
    simulate approval by patching THIS function (and the trusted reader factory) — never by passing
    trust roots.
    """
    return _APPROVED_B3B_CHECKPOINT


def b3b_r2_refusal_reasons(operator_grant: object, git_baseline: object) -> List[str]:
    """Resolve the **B3B** trust roots INTERNALLY and return refusal reasons (empty = OK).

    Identical trust machinery to :func:`b1_r2_refusal_reasons` / :func:`b2_r2_refusal_reasons` —
    the SAME hardened :func:`verify_b1_r2_checkpoint` verifier and the SAME internal
    ``_build_trusted_b1_r2_reader`` (no duplicated verifier, no weakened checks) — but the
    approved-EXPECTED identity is ``current_approved_b3b_checkpoint()`` (the current governed B3
    LIVE identity, i.e. the ``EXPECTED_B3_LIVE_CHECKPOINT_TAG`` B3V successor tag — NOT the
    historical B3B wiring tag, and NOT the B1/EW8 or B2 identity). Takes NO reader/identity
    parameter. Exact-HEAD is required: a checkpoint tag that peels to an ancestor of HEAD (or any
    non-HEAD commit) fails closed (``b1_r2_tag_not_at_authorized_head``); no ancestor
    grandfathering. No other identity can satisfy it (the grant's ``b1_r2_checkpoint`` must EXACTLY
    equal the current governed B3 live identity).
    """
    approved_expected = current_approved_b3b_checkpoint()
    reader = _build_trusted_b1_r2_reader()
    return verify_b1_r2_checkpoint(
        reader=reader,
        operator_grant=operator_grant,
        approved_expected_checkpoint=approved_expected,
        git_baseline=git_baseline,
    )


@dataclass(frozen=True)
class _AuthProfile:
    """INTERNAL, module-private mint profile (PN02D-B2). It selects the phase-specific
    checkpoint gate, workload caps, and operation allowlist for a live mint.

    It is chosen by WHICH public mint entry is called (B1 vs B2) — it is NEVER a public
    parameter and can NEVER be supplied by a CLI/live caller (§22/§23/§24). Its ``refusal_fn``
    resolves BOTH trust roots internally (governance identity + trusted reader); a profile
    cannot inject an arbitrary reader, an "accept anything" gate, or a caller-named checkpoint.
    """

    name: str
    refusal_fn: Callable[[object, object], List[str]]
    expected_caps_dict: Callable[[], Dict[str, int]]
    allowlist: FrozenSet[str]


#: The DEFAULT B1 profile — byte-identical to the pre-B2 mint (EW8 gate, B1 caps FINAL_ANSWER=0,
#: B1 allowlist). The public ``mint_live_provider_run_authorization`` uses ONLY this profile.
_B1_AUTH_PROFILE = _AuthProfile(
    name="B1",
    refusal_fn=b1_r2_refusal_reasons,
    expected_caps_dict=b1_caps_dict,
    allowlist=B1_ALLOWED_OPERATION_VALUES,
)

#: The B2 profile — B2 checkpoint gate (fails closed until the B2 tag exists), B2 caps
#: (FINAL_ANSWER=72), B2 allowlist (B1 + QA-V/QA-GD/QA-V+GD). Used ONLY by the public
#: ``mint_live_b2_provider_run_authorization`` entry.
_B2_AUTH_PROFILE = _AuthProfile(
    name="B2",
    refusal_fn=b2_r2_refusal_reasons,
    expected_caps_dict=b2_caps_dict,
    allowlist=B2_ALLOWED_OPERATION_VALUES,
)

#: The B3 OBSERVABILITY auth profile (PN02D-B3D; the ``name`` retains the historical "B3B"
#: label). Its checkpoint gate is ``current_approved_b3b_checkpoint()`` = the current governed B3
#: LIVE identity (``EXPECTED_B3_LIVE_CHECKPOINT_TAG``, the B3V successor tag) — it FAILS CLOSED
#: until that tag exists AT HEAD. Reuses the B2-style caps (FINAL_ANSWER=72) and the B2 QA
#: allowlist (B1 + QA-V/QA-GD/QA-V+GD) because a B3 observability run is a DIAGNOSTIC replay of the
#: same QA envelope. Used ONLY by the public ``mint_live_b3_provider_run_authorization`` entry; the
#: distinct ``name`` marks the OBSERVABILITY run type. It reuses the B2 caps/allowlist but NEVER
#: the B2 checkpoint identity — the gate verifies the current B3 live tag, so the B2 tag (an
#: ancestor at HEAD) can never authorize a B3 run, and a B3 grant can never authorize a B2 run.
_B3B_AUTH_PROFILE = _AuthProfile(
    name="B3B-OBSERVABILITY",
    refusal_fn=b3b_r2_refusal_reasons,
    expected_caps_dict=b2_caps_dict,
    allowlist=B2_ALLOWED_OPERATION_VALUES,
)


# --------------------------------------------------------------------------- #
# Operator one-run grant (design §7/§13) — operator INPUT, content-safe
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class OperatorRunGrant:
    """An operator-approved one-run grant (design §7/§13). Content-safe: NO secret.

    This is the out-of-band operator decision that binds a live run's identity. It is
    ordinary operator INPUT (not itself unforgeable) — its authority is checked by
    ``mint_live_provider_run_authorization`` against a genuine real-preflight
    capability, the frozen fixture hash / provider fingerprint, and the approved git
    baseline. Every field is a non-secret id / count / flag.
    """

    run_id: str
    fixture_hash: str
    #: The approved B0C-B implementation checkpoint (commit + tag), design §13.
    implementation_checkpoint_commit: str
    implementation_checkpoint_tag: str
    #: The future B1-R2 checkpoint identity recorded in the run manifest (design §13).
    b1_r2_checkpoint: str
    provider_config_fingerprint: str
    workload_caps: Mapping[str, int]
    operation_allowlist: FrozenSet[str]
    #: The approved git baseline a real run must observe (design §13/§42).
    approved_git_commit: str
    approved_git_tag: str
    synthetic_only: bool = True
    real_internal_data_allowed: bool = False

    @property
    def grant_id(self) -> str:
        """A content-safe, non-secret identity for this grant (for manifests)."""
        return f"grant:{self.run_id}:{self.implementation_checkpoint_tag}"


# --------------------------------------------------------------------------- #
# LiveProviderRunAuthorization (design §12/§16) — unforgeable live capability
# --------------------------------------------------------------------------- #

_LIVE_AUTH_KEY = object()


class LiveProviderRunAuthorization:
    """The capability every LIVE provider-backed seam requires (design §12/§16).

    Minted ONLY by ``mint_live_provider_run_authorization`` from a genuine real
    preflight + an operator grant + a live fixture/baseline re-verification. It cannot
    be constructed directly (a manual attempt raises ``PermissionError``), and it is a
    DISTINCT type from ``PN02ProviderRunAuthorization`` — so a simulation authorization
    (which is only ever a plain ``PN02ProviderRunAuthorization``) is rejected wherever
    a live capability is required (task §16). It wraps the underlying provider-run
    authorization the shared executors consume. No secret value ever appears on it.
    """

    __slots__ = (
        "_provider_run_auth",
        "run_id",
        "fixture_hash",
        "operator_grant_id",
        "git_baseline_commit",
        "git_baseline_tag",
        "b1_r2_checkpoint",
    )

    def __init__(
        self,
        key: object,
        *,
        provider_run_auth: PN02ProviderRunAuthorization,
        run_id: str,
        fixture_hash: str,
        operator_grant_id: str,
        git_baseline_commit: str,
        git_baseline_tag: str,
        b1_r2_checkpoint: str,
    ) -> None:
        if key is not _LIVE_AUTH_KEY:
            raise PermissionError(
                "LiveProviderRunAuthorization is minted only by "
                "mint_live_provider_run_authorization after a genuine real preflight + "
                "operator grant; it cannot be constructed directly (fail-closed, §12)"
            )
        self._provider_run_auth = provider_run_auth
        self.run_id = run_id
        self.fixture_hash = fixture_hash
        self.operator_grant_id = operator_grant_id
        self.git_baseline_commit = git_baseline_commit
        self.git_baseline_tag = git_baseline_tag
        self.b1_r2_checkpoint = b1_r2_checkpoint

    @property
    def provider_run_authorization(self) -> PN02ProviderRunAuthorization:
        """The underlying capability the shared executors consume (design §15)."""
        return self._provider_run_auth

    def as_public_dict(self) -> dict:
        """Content-safe view for the run manifest / artifacts (no secret)."""
        return {
            "capability": "LiveProviderRunAuthorization",
            "run_id": self.run_id,
            "fixture_hash": self.fixture_hash,
            "operator_grant_id": self.operator_grant_id,
            "git_baseline_commit": self.git_baseline_commit,
            "git_baseline_tag": self.git_baseline_tag,
            # Attested (trust-observed at the authorized HEAD) — the mint only succeeds
            # when verify_b1_r2_checkpoint passed via the trusted reader (RR3-H1), so this
            # identity is verified against real Git, not caller-asserted.
            "b1_r2_checkpoint": self.b1_r2_checkpoint,
            "b1_r2_checkpoint_attested": True,
            "provider_run_authorization": self._provider_run_auth.as_public_dict(),
        }


def _mint_live_provider_run_authorization_with_profile(
    *,
    profile: _AuthProfile,
    operator_grant: OperatorRunGrant,
    real_preflight_auth: Optional[RealLightRAGPreflightAuthorization],
    git_baseline_attestation: object,
    observed_fixture_hash: str,
    expected_fixture_hash: str = EXPECTED_FIXTURE_HASH,
) -> LiveProviderRunAuthorization:
    """INTERNAL profile-driven mint core (PN02D-B2). ``profile`` is module-private and is set
    ONLY by the public B1/B2 entry wrappers below — never by a CLI/live caller (§22/§23/§24).
    All phase-invariant gates (real-preflight, grant type, simulation-baseline, run-id,
    fixture-hash chain, clean git-baseline attestation, provider fingerprint, Boundary B) are
    identical for B1 and B2; only the checkpoint-identity gate, the workload caps, and the
    operation allowlist come from ``profile`` (B1 defaults = the pre-B2 behavior).

    Mint the LIVE provider-run capability — the ONLY real construction path (§7/§13).

    Fails closed BEFORE any secret access or runtime start on: a missing/forged
    real-preflight capability (incl. one minted for another fixture — B0CB-H1), an
    operator grant of the wrong TYPE, a simulation/offline git baseline, a live
    fixture-hash drift, a missing/dirty/mismatched git-baseline attestation (B0CB-H2),
    a missing/unapproved/not-trust-observed B1-R2 checkpoint (B0CB-RR2-M1/RR3-H1/RR4-H1),
    a provider-fingerprint mismatch, a Boundary-B violation, a workload-cap or
    operation-allowlist drift, or a run_id inconsistency. It contacts NO provider
    (design §14): the returned capability only *permits* a run whose backends are
    supplied separately (mocked in B0C-B).

    ``git_baseline_attestation`` must be a genuine ``GitBaselineAttestation`` carrying
    OBSERVED repository state (a bare boolean is rejected — B0CB-H2).

    B1-R2 trust (B0CB-RR3-H1/RR4-H1): the mint OWNS the trusted read and exposes NO
    trust-root parameter. There is NO ``trusted_b1_r2_reader`` and NO
    ``approved_expected_b1_r2_checkpoint`` argument — a caller cannot substitute the
    approved identity or the Git reader. The mint resolves BOTH internally via
    ``_resolve_b1_r2_verification`` (governance identity from
    ``current_approved_b1_r2_checkpoint`` + the default real-Git ``RealTrustedB1R2Reader``).
    Governance returns the EW8 checkpoint tag; while that annotated tag is ABSENT from real Git
    the trusted reader observes its absence and this fails closed regardless of any caller
    input. Tests simulate a future approval by patching those
    two module-level functions — never through this signature.
    """
    # 1) genuine real-preflight capability required first (design §7).
    require_real_preflight_authorization(real_preflight_auth)
    assert real_preflight_auth is not None  # narrowed by the guard above

    # 2) capability-type hardening: the grant must be a real OperatorRunGrant, not a
    #    dict/look-alike with matching fields (task §16).
    if not isinstance(operator_grant, OperatorRunGrant):
        raise OperatorGrantError(
            "operator_grant must be an OperatorRunGrant instance, not a look-alike "
            "(fail-closed, task §16)"
        )

    # 3) a real run must NOT carry a simulation/offline baseline (design §7/§14).
    if operator_grant.approved_git_commit in _SIMULATION_BASELINE_SENTINELS:
        raise LiveProviderRunAuthorizationError(
            "a real live run requires an approved git commit baseline "
            "(not a SIMULATION/OFFLINE sentinel)"
        )
    if operator_grant.approved_git_tag in _SIMULATION_BASELINE_SENTINELS:
        raise LiveProviderRunAuthorizationError(
            "a real live run requires an approved git tag baseline "
            "(not a SIMULATION/OFFLINE sentinel)"
        )

    # 4) run_id present + consistent across the grant and the preflight (design §8/L-1).
    if not operator_grant.run_id:
        raise LiveProviderRunAuthorizationError("operator_grant.run_id must be non-empty")
    try:
        assert_run_ids_consistent(operator_grant.run_id, real_preflight_auth)
    except RunIdConsistencyError as exc:
        raise LiveProviderRunAuthorizationError(
            "real-preflight capability run_id does not match the operator grant run_id"
        ) from exc

    # 5) B0CB-H1: the FULL fixture-identity chain must agree BEFORE minting —
    #    real_preflight_auth.fixture_hash == observed == grant == frozen. A preflight
    #    capability minted for a different fixture (same run_id) is refused here.
    if observed_fixture_hash != expected_fixture_hash:
        raise LiveProviderRunAuthorizationError(
            "observed fixture hash drifted from the frozen fixture hash"
        )
    if operator_grant.fixture_hash != expected_fixture_hash:
        raise LiveProviderRunAuthorizationError(
            "operator_grant.fixture_hash does not match the frozen fixture hash"
        )
    if real_preflight_auth.fixture_hash != expected_fixture_hash:
        raise LiveProviderRunAuthorizationError(
            "real-preflight capability fixture_hash does not match the frozen fixture "
            "hash (B0CB-H1: preflight minted for a different fixture)"
        )

    # 6) B0CB-H2: a clean, approved git baseline must be ATTESTED from observed repo
    #    state (not a caller boolean) BEFORE minting — dirty tree or baseline mismatch
    #    fails closed here, before any binding/runtime/backend invocation.
    baseline_reasons = attest_approved_clean_baseline(
        git_baseline_attestation,
        approved_commit=operator_grant.approved_git_commit,
        approved_tag=operator_grant.approved_git_tag,
    )
    if baseline_reasons:
        raise GitBaselineError(
            "git baseline attestation failed: " + ", ".join(baseline_reasons)
        )

    # 6b) B0CB-RR2-M1/RR3-H1/RR4-H1: the mint OWNS the trusted B1-R2 read entirely.
    #     `_resolve_b1_r2_verification` resolves BOTH trust roots INTERNALLY — the
    #     governance-approved identity (`current_approved_b1_r2_checkpoint`) and the default
    #     real-Git reader (`_build_trusted_b1_r2_reader`) — with NO caller input. There is
    #     no parameter to substitute either. Governance returns the EW8 checkpoint tag; while
    #     that annotated tag is ABSENT from real Git this fails closed BEFORE mint regardless
    #     of any caller.
    b1_r2_reasons = profile.refusal_fn(operator_grant, git_baseline_attestation)
    if b1_r2_reasons:
        raise B1R2CheckpointError(
            "B1-R2 checkpoint verification failed: " + ", ".join(b1_r2_reasons)
        )

    # 7) provider-config fingerprint == frozen (design §6/§11).
    expected_cfg = frozen_provider_config_id()
    if operator_grant.provider_config_fingerprint != expected_cfg:
        raise LiveProviderRunAuthorizationError(
            "operator_grant.provider_config_fingerprint does not match the frozen "
            "provider config"
        )
    if expected_cfg != EXPECTED_PROVIDER_CONFIG_ID:
        raise LiveProviderRunAuthorizationError(
            "frozen provider-config fingerprint drifted from the pinned value"
        )

    # 8) Boundary B (design §6/§13).
    if operator_grant.real_internal_data_allowed:
        raise LiveProviderRunAuthorizationError("real_internal_data_allowed must be False")
    if not operator_grant.synthetic_only:
        raise LiveProviderRunAuthorizationError("synthetic_only must be True")

    # 9) operation allowlist == the frozen B1 allowlist (design §13/§44).
    if frozenset(operator_grant.operation_allowlist) != profile.allowlist:
        raise LiveProviderRunAuthorizationError(
            "operator_grant.operation_allowlist does not match the frozen "
            f"{profile.name} allowlist"
        )

    # 10) workload caps == the frozen B1 caps (design §13/§45).
    if dict(operator_grant.workload_caps) != profile.expected_caps_dict():
        raise LiveProviderRunAuthorizationError(
            f"operator_grant.workload_caps do not match the frozen {profile.name} caps"
        )

    # Mint the underlying provider-run capability with a REAL git baseline (the frozen
    # ``mint_provider_run_authorization`` re-enforces the real-preflight, fixture hash,
    # Boundary B, and provider fingerprint).
    provider_run_auth = mint_provider_run_authorization(
        real_preflight_auth=real_preflight_auth,
        fixture_hash=observed_fixture_hash,
        expected_fixture_hash=expected_fixture_hash,
        git_baseline_commit=operator_grant.approved_git_commit,
        git_baseline_tag=operator_grant.approved_git_tag,
        run_id=operator_grant.run_id,
        workload_caps=operator_grant.workload_caps,
        synthetic_only=operator_grant.synthetic_only,
        real_internal_data_allowed=operator_grant.real_internal_data_allowed,
        approved_provider_config_id=operator_grant.provider_config_fingerprint,
    )
    return LiveProviderRunAuthorization(
        _LIVE_AUTH_KEY,
        provider_run_auth=provider_run_auth,
        run_id=operator_grant.run_id,
        fixture_hash=observed_fixture_hash,
        operator_grant_id=operator_grant.grant_id,
        git_baseline_commit=operator_grant.approved_git_commit,
        git_baseline_tag=operator_grant.approved_git_tag,
        b1_r2_checkpoint=operator_grant.b1_r2_checkpoint,
    )


def mint_live_provider_run_authorization(
    *,
    operator_grant: OperatorRunGrant,
    real_preflight_auth: Optional[RealLightRAGPreflightAuthorization],
    git_baseline_attestation: object,
    observed_fixture_hash: str,
    expected_fixture_hash: str = EXPECTED_FIXTURE_HASH,
) -> LiveProviderRunAuthorization:
    """Mint a **B1** LIVE provider-run capability — the ONLY real B1 construction path.

    Byte-identical to the pre-B2 behavior: it delegates to the internal profile-driven core
    with the fixed private ``_B1_AUTH_PROFILE`` (EW8 checkpoint gate via
    ``current_approved_b1_r2_checkpoint``, B1 caps with FINAL_ANSWER=0, B1 allowlist). The
    public signature exposes NO profile / trust-root / checkpoint parameter — a caller cannot
    select the B2 profile or override the trusted read (B0CB-RR4-H1; §22/§23).
    """
    return _mint_live_provider_run_authorization_with_profile(
        profile=_B1_AUTH_PROFILE,
        operator_grant=operator_grant,
        real_preflight_auth=real_preflight_auth,
        git_baseline_attestation=git_baseline_attestation,
        observed_fixture_hash=observed_fixture_hash,
        expected_fixture_hash=expected_fixture_hash,
    )


def mint_live_b2_provider_run_authorization(
    *,
    operator_grant: OperatorRunGrant,
    real_preflight_auth: Optional[RealLightRAGPreflightAuthorization],
    git_baseline_attestation: object,
    observed_fixture_hash: str,
    expected_fixture_hash: str = EXPECTED_FIXTURE_HASH,
) -> LiveProviderRunAuthorization:
    """Mint a **B2** LIVE provider-run capability — the ONLY real B2 construction path (PN02D-B2).

    Same hardened core as B1 but with the fixed private ``_B2_AUTH_PROFILE``: the B2 checkpoint
    gate (``current_approved_b2_checkpoint`` → ``EXPECTED_B2_CHECKPOINT_TAG``), B2 caps
    (FINAL_ANSWER=72), and the B2 allowlist (B1 + QA-V/QA-GD/QA-V+GD). Because the B2 tag is
    ABSENT from real Git, this FAILS CLOSED today (``b1_r2_tag_not_observed_in_git``); EW8
    (which peels to today's HEAD) can NEVER authorize a B2 run because the gate verifies the B2
    identity, not EW8. The public signature exposes NO profile / trust-root / checkpoint
    parameter (§22/§23); a valid B2 tag alone still cannot mint without a matching operator
    grant.
    """
    return _mint_live_provider_run_authorization_with_profile(
        profile=_B2_AUTH_PROFILE,
        operator_grant=operator_grant,
        real_preflight_auth=real_preflight_auth,
        git_baseline_attestation=git_baseline_attestation,
        observed_fixture_hash=observed_fixture_hash,
        expected_fixture_hash=expected_fixture_hash,
    )


def mint_live_b3_provider_run_authorization(
    *,
    operator_grant: OperatorRunGrant,
    real_preflight_auth: Optional[RealLightRAGPreflightAuthorization],
    git_baseline_attestation: object,
    observed_fixture_hash: str,
    expected_fixture_hash: str = EXPECTED_FIXTURE_HASH,
) -> LiveProviderRunAuthorization:
    """Mint a **B3B** OBSERVABILITY live provider-run capability (PN02D-B3D) — the ONLY real B3
    observational construction path.

    Same hardened core as B1/B2 but with the fixed private ``_B3B_AUTH_PROFILE``: the B3 live
    checkpoint gate (``current_approved_b3b_checkpoint`` → the current governed B3 live identity
    ``EXPECTED_B3_LIVE_CHECKPOINT_TAG``, the B3V successor tag) plus the B2-style caps
    (FINAL_ANSWER=72) and QA allowlist. Because that tag must be observed at the EXACT authorized
    HEAD, this fails closed unless that holds; the B2 tag (an ancestor at HEAD) can NEVER authorize
    a B3 run, and a valid B3 live tag alone still cannot mint without a matching operator grant.
    Exposes NO profile / trust-root / checkpoint parameter (§22/§23).
    The B1 and B2 mints are UNCHANGED and cannot be reached through this entry.
    """
    return _mint_live_provider_run_authorization_with_profile(
        profile=_B3B_AUTH_PROFILE,
        operator_grant=operator_grant,
        real_preflight_auth=real_preflight_auth,
        git_baseline_attestation=git_baseline_attestation,
        observed_fixture_hash=observed_fixture_hash,
        expected_fixture_hash=expected_fixture_hash,
    )


def require_live_provider_run_authorization(
    auth: object,
) -> LiveProviderRunAuthorization:
    """Guard every LIVE seam (binder/runtime/corpus/entrypoint), fail-closed (§12/§16).

    Rejects anything that is not a genuine ``LiveProviderRunAuthorization`` — including
    a plain (simulation) ``PN02ProviderRunAuthorization``, a dict look-alike, or
    ``None`` — BEFORE any secret access or runtime start.
    """
    if not isinstance(auth, LiveProviderRunAuthorization):
        raise LiveProviderRunNotAuthorized(
            "LIVE provider-backed seam blocked: a valid LiveProviderRunAuthorization "
            "is required (a simulation authorization or boolean is insufficient) — "
            "fail-closed (task §12/§16)"
        )
    return auth


def frozen_b1_operator_grant_template(
    *,
    run_id: str,
    implementation_checkpoint_commit: str,
    implementation_checkpoint_tag: str,
    b1_r2_checkpoint: str,
    approved_git_commit: str,
    approved_git_tag: str,
) -> OperatorRunGrant:
    """Build an OperatorRunGrant with every FROZEN field pre-filled (design §13).

    The operator supplies only run identity + the approved checkpoint/baseline; the
    fixture hash, provider fingerprint, workload caps, allowlist, and Boundary-B flags
    are pinned to their frozen values here so a caller cannot silently weaken them.
    """
    return OperatorRunGrant(
        run_id=run_id,
        fixture_hash=EXPECTED_FIXTURE_HASH,
        implementation_checkpoint_commit=implementation_checkpoint_commit,
        implementation_checkpoint_tag=implementation_checkpoint_tag,
        b1_r2_checkpoint=b1_r2_checkpoint,
        provider_config_fingerprint=frozen_provider_config_id(),
        workload_caps=b1_caps_dict(),
        operation_allowlist=B1_ALLOWED_OPERATION_VALUES,
        approved_git_commit=approved_git_commit,
        approved_git_tag=approved_git_tag,
        synthetic_only=True,
        real_internal_data_allowed=False,
    )


def frozen_b2_operator_grant_template(
    *,
    run_id: str,
    implementation_checkpoint_commit: str,
    implementation_checkpoint_tag: str,
    b1_r2_checkpoint: str,
    approved_git_commit: str,
    approved_git_tag: str,
) -> OperatorRunGrant:
    """Build a PN02D-**B2** OperatorRunGrant with every FROZEN field pre-filled.

    Identical to :func:`frozen_b1_operator_grant_template` EXCEPT the two B2-distinct frozen
    values: ``workload_caps=b2_caps_dict()`` (FINAL_ANSWER=72) and
    ``operation_allowlist=B2_ALLOWED_OPERATION_VALUES`` (B1 + QA-V/QA-GD/QA-V+GD). The caller
    still supplies the B2 checkpoint identity as ``b1_r2_checkpoint`` (the grant's single
    checkpoint field); the B2 mint verifies it against ``current_approved_b2_checkpoint()``.
    B1 grants (and the B1 template above) are UNCHANGED — a B1 grant never carries B2 caps or
    the QA ops, so it can never authorize a B2 run.
    """
    return OperatorRunGrant(
        run_id=run_id,
        fixture_hash=EXPECTED_FIXTURE_HASH,
        implementation_checkpoint_commit=implementation_checkpoint_commit,
        implementation_checkpoint_tag=implementation_checkpoint_tag,
        b1_r2_checkpoint=b1_r2_checkpoint,
        provider_config_fingerprint=frozen_provider_config_id(),
        workload_caps=b2_caps_dict(),
        operation_allowlist=B2_ALLOWED_OPERATION_VALUES,
        approved_git_commit=approved_git_commit,
        approved_git_tag=approved_git_tag,
        synthetic_only=True,
        real_internal_data_allowed=False,
    )


def frozen_b3b_operator_grant_template(
    *,
    run_id: str,
    implementation_checkpoint_commit: str,
    implementation_checkpoint_tag: str,
    b1_r2_checkpoint: str,
    approved_git_commit: str,
    approved_git_tag: str,
) -> OperatorRunGrant:
    """Build a PN02D-**B3B** OBSERVABILITY OperatorRunGrant with every FROZEN field pre-filled.

    Identical FROZEN shape to :func:`frozen_b2_operator_grant_template` (``workload_caps=
    b2_caps_dict()`` FINAL_ANSWER=72; ``operation_allowlist=B2_ALLOWED_OPERATION_VALUES``) — a
    B3 observability run replays the same QA envelope. The caller supplies the current governed B3
    live checkpoint identity as ``b1_r2_checkpoint`` (the grant's single checkpoint field); the B3
    mint verifies it against ``current_approved_b3b_checkpoint()`` (the current B3 live identity,
    i.e. the ``EXPECTED_B3_LIVE_CHECKPOINT_TAG`` B3V successor tag — not the historical B3B tag),
    so a B2 grant (B2 tag) can never authorize a B3 run and vice-versa. ``run_id`` here is the
    DISTINCT B3 observation run id and must not be the frozen B2 scientific run id.
    """
    return OperatorRunGrant(
        run_id=run_id,
        fixture_hash=EXPECTED_FIXTURE_HASH,
        implementation_checkpoint_commit=implementation_checkpoint_commit,
        implementation_checkpoint_tag=implementation_checkpoint_tag,
        b1_r2_checkpoint=b1_r2_checkpoint,
        provider_config_fingerprint=frozen_provider_config_id(),
        workload_caps=b2_caps_dict(),
        operation_allowlist=B2_ALLOWED_OPERATION_VALUES,
        approved_git_commit=approved_git_commit,
        approved_git_tag=approved_git_tag,
        synthetic_only=True,
        real_internal_data_allowed=False,
    )


__all__ = [
    "EXPECTED_FIXTURE_HASH",
    "EXPECTED_PROVIDER_CONFIG_ID",
    "B1_ALLOWED_OPERATION_VALUES",
    "B2_ALLOWED_OPERATION_VALUES",
    "LiveProviderRunAuthorizationError",
    "LiveProviderRunNotAuthorized",
    "OperatorGrantError",
    "GitBaselineError",
    "GitBaselineAttestation",
    "attest_approved_clean_baseline",
    "B1R2CheckpointError",
    "TrustedB1R2Observation",
    "TrustedB1R2Reader",
    "RealTrustedB1R2Reader",
    "verify_b1_r2_checkpoint",
    "b1_r2_refusal_reasons",
    "b2_r2_refusal_reasons",
    "b3b_r2_refusal_reasons",
    "current_approved_b1_r2_checkpoint",
    "current_approved_b2_checkpoint",
    "current_approved_b3b_checkpoint",
    "EXPECTED_B2_CHECKPOINT_TAG",
    "EXPECTED_B3B_CHECKPOINT_TAG",
    "EXPECTED_B3_LIVE_CHECKPOINT_TAG",
    "HISTORICAL_B3B_CHECKPOINT_TAG",
    "HISTORICAL_B3J_CHECKPOINT_TAG",
    "HISTORICAL_B3Q_CHECKPOINT_TAG",
    "HISTORICAL_B2_CHECKPOINT_TAG",
    "HISTORICAL_B2_LIFECYCLE_CHECKPOINT_TAG",
    "EXPECTED_EW8_CHECKPOINT_TAG",
    "EXPECTED_EW7_CHECKPOINT_TAG",
    "EXPECTED_EW6_CHECKPOINT_TAG",
    "EXPECTED_EW5_CHECKPOINT_TAG",
    "EXPECTED_EW4_CHECKPOINT_TAG",
    "EXPECTED_EW3_CHECKPOINT_TAG",
    "EXPECTED_EW2_CHECKPOINT_TAG",
    "EXPECTED_B1_R2_CHECKPOINT_TAG",
    "EXPECTED_PF1_CHECKPOINT_TAG",
    "EXPECTED_EW1_CHECKPOINT_TAG",
    "OperatorRunGrant",
    "LiveProviderRunAuthorization",
    "mint_live_provider_run_authorization",
    "mint_live_b2_provider_run_authorization",
    "mint_live_b3_provider_run_authorization",
    "require_live_provider_run_authorization",
    "frozen_b1_operator_grant_template",
    "frozen_b2_operator_grant_template",
    "frozen_b3b_operator_grant_template",
]
