"""LIVE CLI for the PN02 driver — the ``execute-b1-live`` verb (PN02D-B0C-B).

EVALUATION-ONLY. Nothing in production imports this (``PRODUCTION_IMPORTS_EVAL =
NO``). This is a SEPARATE module from the offline ``liveclipn02d`` (design §15) so the
offline CLI keeps its no-provider guarantee and its ``test_cli_has_no_execute_b1_verb``
invariant. The verb exists in code (task §40) but performs a strict, fail-closed,
pre-binding validation and then STOPS at the governance authorization gate — it never
executes a real provider run in B0C-B (task §43/§60): there is no auto-execution, and
the authorized branch additionally requires operator-provisioned live seams that this
build does not wire.

``execute-b1-live`` requires (task §41): a run manifest (operator grant JSON), the
operator run_id, the frozen fixture hash, the approved implementation baseline
(git commit + tag), the frozen provider-config fingerprint, and ``synthetic_only=true``.
A missing/invalid field, a simulation authorization, a run_id / fixture-hash /
provider-fingerprint mismatch, or a dirty/unapproved git baseline is REFUSED before any
provider binding or runtime boot (task §60). ``dry-run-b1-live-plan`` prints the
content-safe provider-zero plan (task §59).
"""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence, Tuple

from open_notebook.integrations.graphrag.eval.authlivepn02d import (
    frozen_provider_config_id,
)
from open_notebook.integrations.graphrag.eval.authmintlivepn02d import (
    B1_ALLOWED_OPERATION_VALUES,
    EXPECTED_FIXTURE_HASH,
    GitBaselineAttestation,
    OperatorRunGrant,
    attest_approved_clean_baseline,
    b1_r2_refusal_reasons,
    frozen_b1_operator_grant_template,
)
from open_notebook.integrations.graphrag.eval.budgetlivepn02d import b1_caps_dict
from open_notebook.integrations.graphrag.eval.datasetpn02 import verify_fixture_hash
from open_notebook.integrations.graphrag.eval.driver_live_pn02d import plan_live_b1
from open_notebook.integrations.graphrag.eval.driverpn02d import B1RunOutcome
from open_notebook.integrations.graphrag.eval.provider_binding08 import (
    frozen_provider_binding,
)

#: Governance env token that would (with an explicit flag) open the authorization gate.
#: NEVER set in B0C-B; the verb refuses without it (PN02_PROVIDER_RUN_AUTHORIZED = NO).
PROVIDER_RUN_AUTHORIZED_ENV = "PN02_PROVIDER_RUN_AUTHORIZED"

#: Simulation/offline baseline sentinels a real run must never carry.
_SIMULATION_BASELINE_SENTINELS = frozenset({"", "SIMULATION", "OFFLINE", "DRYRUN"})

# Content-safe refusal reasons.
REASON_MANIFEST_MISSING = "manifest_missing"
REASON_MANIFEST_MALFORMED = "manifest_malformed"
REASON_NOT_AUTHORIZED = "pn02_provider_run_not_authorized"
#: LEGACY (pre-EW1): the authorized branch used to refuse with this because no production
#: real-seams composition existed. PN02D-B1-EW1 wires the real path, so this is NO LONGER
#: returned; retained only for import compatibility.
REASON_NO_LIVE_SEAMS = "live_seams_not_provisioned"
#: The provider credential (``OPENROUTER_API_KEY``) is absent — refuse BEFORE any Docker
#: boot / provider binding (task §19/§22). Name-only presence; the value is never read here.
REASON_PROVIDER_SECRET_MISSING = "provider_secret_missing"

#: The authorized real-execution runner: performs the two-boot B1 run and returns the
#: ``B1RunOutcome``. Real by default; a controlled offline test injects one that exercises the
#: REAL builder with fake external edges (proving CLI → builder → RealB1Driver.run) with zero
#: provider traffic. The driver OWNS the preflight→mint→boot2→execute ordering (not bypassed).
LiveRunner = Callable[..., B1RunOutcome]


def _default_live_runner(
    *,
    operator_grant: OperatorRunGrant,
    git_baseline: GitBaselineAttestation,
    observed_fixture_hash: str,
    env: Dict[str, str],
) -> B1RunOutcome:
    """Run the real in-process two-boot B1 execution (lazy import; asyncio boundary)."""
    import asyncio

    from open_notebook.integrations.graphrag.eval.realseamspn02d import (
        run_live_b1_execution,
    )

    return asyncio.run(
        run_live_b1_execution(
            operator_grant=operator_grant,
            git_baseline_attestation=git_baseline,
            observed_fixture_hash=observed_fixture_hash,
            env=env,
        )
    )

#: Untracked paths that are execution-affecting (a dirty tree for a live run, B0CB-H2).
_EXEC_AFFECTING_PREFIXES = ("open_notebook/", "commands/", "api/", "tests/", "prompts/")

#: A git-baseline reader — a real git read by default; injected/mocked in tests.
GitBaselineReader = Callable[[], GitBaselineAttestation]


class LiveCliRefusal(Dict[str, object]):
    """A content-safe refusal payload (a plain dict subclass for typed clarity)."""


def _git(args: Sequence[str]) -> str:
    try:
        out = subprocess.run(  # noqa: S603 - fixed argv, no shell
            ["git", *args], capture_output=True, text=True, timeout=10
        )
        return out.stdout.strip() if out.returncode == 0 else ""
    except Exception:  # noqa: BLE001 - a missing git is a content-safe empty result
        return ""


def _is_execution_affecting(path: str) -> bool:
    p = path.strip().strip('"').replace("\\", "/")
    if p.endswith(".py"):
        return True
    return any(p.startswith(prefix) for prefix in _EXEC_AFFECTING_PREFIXES)


def read_git_baseline(
    git_runner: Callable[[Sequence[str]], str] = _git,
) -> GitBaselineAttestation:
    """Read OBSERVED git baseline state from the real repository (content-safe, B0CB-H2).

    Counts staged / unstaged / execution-affecting-untracked changes from
    ``git status --porcelain`` so ``is_clean`` reflects the actual tree — never a
    caller-supplied boolean. ``git_runner`` is the git command boundary (real by
    default; injectable so the REAL parser can be unit-tested without a real repo).

    **B0CB-H2-R1 (fix):** the observed tag comes ONLY from
    ``git describe --tags --exact-match``. When HEAD has no exact tag, the observed
    tag and tag-peel stay EMPTY — there is NO branch-name / HEAD-SHA / manifest
    fallback, so a clean but UNTAGGED HEAD can never satisfy an approved-tag identity.
    Best-effort on a missing git: empty ids + zeroed counts (which FAIL the
    approved-baseline comparison rather than spoof cleanliness).
    """
    commit = git_runner(["rev-parse", "HEAD"])
    branch = git_runner(["rev-parse", "--abbrev-ref", "HEAD"])
    # Exact-tag ONLY. No fallback: an untagged HEAD keeps tag/peel empty (B0CB-H2-R1).
    tag = git_runner(["describe", "--tags", "--exact-match"])
    tag_peel = git_runner(["rev-list", "-n", "1", tag]) if tag else ""
    staged = unstaged = untracked_exec = 0
    porcelain = git_runner(["status", "--porcelain"])
    for line in porcelain.splitlines():
        if not line:
            continue
        code = line[:2]
        path = line[3:]
        if code == "??":
            if _is_execution_affecting(path):
                untracked_exec += 1
            continue
        index_status, worktree_status = code[0], code[1]
        if index_status not in (" ", "?"):
            staged += 1
        if worktree_status not in (" ", "?"):
            unstaged += 1
    return GitBaselineAttestation(
        branch=branch,
        head_commit=commit,
        head_tag=tag,
        tag_peel_commit=tag_peel,
        staged_count=staged,
        unstaged_count=unstaged,
        untracked_execution_affecting_count=untracked_exec,
    )


def parse_operator_grant(raw: Dict[str, object]) -> OperatorRunGrant:
    """Parse a manifest dict into an ``OperatorRunGrant`` (raises on a malformed shape)."""
    try:
        return OperatorRunGrant(
            run_id=str(raw["run_id"]),
            fixture_hash=str(raw["fixture_hash"]),
            implementation_checkpoint_commit=str(
                raw["implementation_checkpoint_commit"]
            ),
            implementation_checkpoint_tag=str(raw["implementation_checkpoint_tag"]),
            b1_r2_checkpoint=str(raw["b1_r2_checkpoint"]),
            provider_config_fingerprint=str(raw["provider_config_fingerprint"]),
            workload_caps=dict(raw["workload_caps"]),  # type: ignore[call-overload]
            operation_allowlist=frozenset(raw["operation_allowlist"]),  # type: ignore[call-overload]
            approved_git_commit=str(raw["approved_git_commit"]),
            approved_git_tag=str(raw["approved_git_tag"]),
            synthetic_only=bool(raw.get("synthetic_only", True)),
            real_internal_data_allowed=bool(raw.get("real_internal_data_allowed", False)),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"malformed operator grant: {type(exc).__name__}") from exc


def validate_live_run_inputs(
    *,
    grant: OperatorRunGrant,
    git_baseline: GitBaselineAttestation,
    observed_fixture_hash: str,
) -> List[str]:
    """Pre-binding validation of the live-run inputs (mirrors the mint, no minting).

    Returns a list of content-safe refusal reasons (empty = inputs would pass). Every
    check runs BEFORE any provider binding / runtime boot (task §41/§60). The git
    baseline is an OBSERVED attestation object (B0CB-H2), so a dirty tree or a
    baseline mismatch is refused here — a caller cannot assert cleanliness by hand.

    B1-R2 is DEFENSE-IN-DEPTH here (the mint is the trust root, task §14/B0CB-RR4-H1):
    this function exposes NO trust-root parameter. It calls the shared
    ``b1_r2_refusal_reasons`` which resolves the approved identity + trusted Git reader
    INTERNALLY (governance returns the EW1 successor tag; its annotated tag ABSENT → fail
    closed). The CLI can neither override the approved B1-R2 identity nor the trusted reader.
    """
    reasons: List[str] = []
    if not isinstance(grant, OperatorRunGrant):
        return ["operator_grant_wrong_type"]
    if not grant.run_id:
        reasons.append("run_id_missing")
    # simulation authorization: a SIMULATION/OFFLINE baseline is not a real run.
    if grant.approved_git_commit in _SIMULATION_BASELINE_SENTINELS:
        reasons.append("simulation_or_missing_git_commit_baseline")
    if grant.approved_git_tag in _SIMULATION_BASELINE_SENTINELS:
        reasons.append("simulation_or_missing_git_tag_baseline")
    if observed_fixture_hash != EXPECTED_FIXTURE_HASH:
        reasons.append("observed_fixture_hash_drift")
    if grant.fixture_hash != EXPECTED_FIXTURE_HASH:
        reasons.append("grant_fixture_hash_mismatch")
    # B0CB-H2: clean + approved git baseline attested from OBSERVED repo state.
    reasons.extend(
        attest_approved_clean_baseline(
            git_baseline,
            approved_commit=grant.approved_git_commit,
            approved_tag=grant.approved_git_tag,
        )
    )
    # B0CB-RR2-M1/RR3-H1/RR4-H1: the approved B1-R2 checkpoint is trust-observed in Git at
    # the authorized HEAD via the mint-owned resolver (no CLI-controllable trust root).
    reasons.extend(b1_r2_refusal_reasons(grant, git_baseline))
    if grant.provider_config_fingerprint != frozen_provider_config_id():
        reasons.append("provider_config_fingerprint_mismatch")
    if grant.real_internal_data_allowed or not grant.synthetic_only:
        reasons.append("boundary_b_violation")
    if frozenset(grant.operation_allowlist) != B1_ALLOWED_OPERATION_VALUES:
        reasons.append("operation_allowlist_mismatch")
    if dict(grant.workload_caps) != b1_caps_dict():
        reasons.append("workload_caps_mismatch")
    return reasons


def _governance_authorized(env: Dict[str, str], *, explicit_flag: bool) -> bool:
    """Whether the governance gate is open. Requires BOTH the env token and the flag.

    In B0C-B neither is set, so this is always False — the verb refuses (task §60).
    """
    token = (env.get(PROVIDER_RUN_AUTHORIZED_ENV, "") or "").strip().upper()
    return explicit_flag and token in {"YES", "TRUE", "1"}


def evaluate_execute_b1_live(
    *,
    manifest_path: Optional[str],
    explicit_authorize: bool,
    env: Dict[str, str],
    git_baseline_reader: GitBaselineReader = read_git_baseline,
    fixture_hash_reader: Callable[[], Tuple[bool, str]] = verify_fixture_hash,
    live_runner: LiveRunner = _default_live_runner,
) -> Tuple[int, Dict[str, object]]:
    """Evaluate ``execute-b1-live`` (PN02D-B1-EW1): validate, gate, then run the real path.

    Returns ``(exit_code, payload)``. Every fail-closed gate runs BEFORE any provider
    binding or runtime boot: manifest present/well-formed, fixture hash, clean+approved git
    baseline, the trust-observed B1-R2/EW1 checkpoint, provider fingerprint, caps/allowlist,
    the governance authorization gate, and — new in EW1 — provider-secret presence (name
    only). ONLY when all gates pass does it invoke ``live_runner`` (the driver-owned two-boot
    execution). A missing provider secret refuses with ``provider_secret_missing`` and never
    boots. This function reads no secret VALUE.
    """
    payload: Dict[str, object] = {
        "command": "execute-b1-live",
        "provider_traffic": 0,
        "runtime_booted": False,
        "provider_bound": False,
    }

    # 1) manifest / operator grant required (task §41/§60).
    if not manifest_path:
        payload["result"] = "REFUSED"
        payload["reasons"] = [REASON_MANIFEST_MISSING]
        return 2, payload
    path = Path(manifest_path)
    if not path.exists():
        payload["result"] = "REFUSED"
        payload["reasons"] = [REASON_MANIFEST_MISSING]
        return 2, payload
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError("manifest must be a JSON object")
        grant = parse_operator_grant(raw)
    except (ValueError, OSError):
        payload["result"] = "REFUSED"
        payload["reasons"] = [REASON_MANIFEST_MALFORMED]
        return 2, payload

    # 2) pre-binding input validation (fixture / clean+approved baseline / B1-R2 / caps).
    git_baseline = git_baseline_reader()
    ok, fixture_detail = fixture_hash_reader()
    observed_fixture_hash = fixture_detail if ok else "UNVERIFIED"
    reasons = validate_live_run_inputs(
        grant=grant,
        git_baseline=git_baseline,
        observed_fixture_hash=observed_fixture_hash,
    )
    payload["run_id"] = grant.run_id
    payload["operator_grant_id"] = grant.grant_id
    if reasons:
        payload["result"] = "REFUSED"
        payload["reasons"] = reasons
        return 2, payload

    # 3) governance authorization gate (env token + explicit operator flag).
    if not _governance_authorized(env, explicit_flag=explicit_authorize):
        payload["result"] = "REFUSED"
        payload["reasons"] = [REASON_NOT_AUTHORIZED]
        payload["pn02_provider_run_authorized"] = False
        return 3, payload

    # 3b) provider-secret presence gate (task §19/§22) — NAME-only, BEFORE any Docker boot
    #     or provider binding. A missing provider credential fails closed here so no runtime
    #     is ever booted and no provider is contacted; the value itself is never read.
    required = frozen_provider_binding().required_secret_envs()
    present = {n for n in required if (env.get(n, "") or "").strip()}
    missing = sorted(n for n in required if n not in present)
    if missing:
        payload["result"] = "REFUSED"
        payload["reasons"] = [REASON_PROVIDER_SECRET_MISSING]
        payload["pn02_provider_run_authorized"] = True
        payload["provider_bound"] = False
        payload["runtime_booted"] = False
        return 3, payload

    # 4) AUTHORIZED — run the real two-boot execution. ``RealB1Driver`` OWNS the security
    #    ordering (provider-free preflight → in-process mint → provider-bound Boot 2 →
    #    execute → cleanup); this function neither re-mints nor bypasses it. Any failure is
    #    normalized to a content-safe FAILED payload (type name only, never a secret).
    payload["pn02_provider_run_authorized"] = True
    try:
        outcome = live_runner(
            operator_grant=grant,
            git_baseline=git_baseline,
            observed_fixture_hash=observed_fixture_hash,
            env=env,
        )
    except Exception as exc:  # noqa: BLE001 - fail-closed; content-safe type name only
        payload["result"] = "FAILED"
        payload["reasons"] = ["live_execution_error"]
        payload["error_type"] = type(exc).__name__
        payload["provider_bound"] = False
        return 4, payload

    payload["result"] = outcome.state  # COMPLETE | FAILED
    payload["run_id"] = outcome.run_id
    payload["technical_status"] = outcome.technical_status.value
    payload["report_kind"] = outcome.report.get("report_kind")
    payload["failure_reason"] = outcome.failure_reason
    payload["provider_bound"] = True
    payload["runtime_booted"] = True
    return (0 if outcome.state == "COMPLETE" else 4), payload


def cmd_execute_b1_live(args: argparse.Namespace) -> int:
    import os

    exit_code, payload = evaluate_execute_b1_live(
        manifest_path=getattr(args, "manifest", None),
        explicit_authorize=bool(getattr(args, "authorize", False)),
        env=dict(os.environ),
    )
    print(json.dumps(payload, indent=2, sort_keys=True))
    return exit_code


def cmd_dry_run_b1_live_plan(args: argparse.Namespace) -> int:
    plan = plan_live_b1(run_id=args.run_id)
    if args.out:
        Path(args.out).write_text(
            json.dumps(plan, indent=2, sort_keys=True), encoding="utf-8"
        )
    print(json.dumps(plan, indent=2, sort_keys=True))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="graphrag-pn02-b1-live",
        description=(
            "LIVE PN02 driver CLI. execute-b1-live is authorization-gated and refuses "
            "any real provider run without an operator grant + open governance gate."
        ),
    )
    sub = parser.add_subparsers(dest="command", required=True)

    e = sub.add_parser(
        "execute-b1-live",
        help="validate a live run manifest and (fail-closed) refuse without authorization",
    )
    e.add_argument("--manifest", help="path to the operator run-grant manifest JSON")
    e.add_argument(
        "--authorize",
        action="store_true",
        help="operator intent flag (still requires the governance env token; refused otherwise)",
    )
    e.set_defaults(func=cmd_execute_b1_live)

    d = sub.add_parser(
        "dry-run-b1-live-plan",
        help="serialize the content-safe provider-zero live plan (no execution)",
    )
    d.add_argument("--run-id", default="DRYRUN")
    d.add_argument("--out", help="optional path to write the plan JSON")
    d.set_defaults(func=cmd_dry_run_b1_live_plan)
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())


__all__ = [
    "PROVIDER_RUN_AUTHORIZED_ENV",
    "REASON_MANIFEST_MISSING",
    "REASON_MANIFEST_MALFORMED",
    "REASON_NOT_AUTHORIZED",
    "REASON_NO_LIVE_SEAMS",
    "REASON_PROVIDER_SECRET_MISSING",
    "LiveRunner",
    "GitBaselineReader",
    "read_git_baseline",
    "parse_operator_grant",
    "validate_live_run_inputs",
    "evaluate_execute_b1_live",
    "frozen_b1_operator_grant_template",
    "build_parser",
    "main",
]
