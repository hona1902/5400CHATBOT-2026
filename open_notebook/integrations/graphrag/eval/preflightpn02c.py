"""PN02C preflight orchestrator + eval-only CLI (task §21-§24/§40-§49).

EVALUATION-ONLY. Nothing in production imports this. Runs the whole PN02C
preflight with ZERO provider traffic and emits content-safe reports:

  1. precheck: fixture hash + version pin + workload ledger (task §1/§4/§21),
  2. plans: provisioning (8+8+8=24), shared-source, membership-removal (§22-§24),
  3. provision the three real owned runtimes provider-free (§27-§31),
  4. attest every workspace + prove wrong/unattested routes are rejected (§12-§16),
  5. account every provider workload counter as 0 (§17-§20/§37-§39),
  6. manifest + attestation + routing + workload reports (§40-§43),
  7. cleanup to 0 processes / 0 residue (§44),
  8. classify a technical outcome + a live-authorization decision A/B/C (§47/§49).

The decision NEVER authorizes live traffic: ``PN02_LIVE_AUTHORIZED`` stays NO.
Run: ``uv run python -m open_notebook.integrations.graphrag.eval.preflightpn02c``.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional, Tuple

from open_notebook.integrations.graphrag.eval import datasetpn02 as ds
from open_notebook.integrations.graphrag.eval import manifestpn02 as mf
from open_notebook.integrations.graphrag.eval import routingpn02c as rt
from open_notebook.integrations.graphrag.eval import workloadpn02 as wl
from open_notebook.integrations.graphrag.eval.provisionpn02c import (
    EndpointCollisionError,
    PartialStartupError,
    ProviderTrafficAttempted,
    ProviderTrafficGuard,
    ProvisionedTopology,
    StorageCollisionError,
    provision_runtimes,
)

EXPECTED_FIXTURE_HASH = (
    "9ce7df742810424d9ef7b7b34962187d9061245f81471f5fb1d7f2608f6899a6"
)


class PreflightOutcome(str, Enum):
    """Technical outcome taxonomy (task §47) — separate from any science result."""

    COMPLETED = "COMPLETED"
    FAILED_PRECHECK = "FAILED_PRECHECK"
    FAILED_FIXTURE_HASH = "FAILED_FIXTURE_HASH"
    FAILED_ENDPOINT_ALLOCATION = "FAILED_ENDPOINT_ALLOCATION"
    FAILED_STORAGE_ISOLATION = "FAILED_STORAGE_ISOLATION"
    FAILED_SIDECAR_START = "FAILED_SIDECAR_START"
    FAILED_VERSION_ATTESTATION = "FAILED_VERSION_ATTESTATION"
    FAILED_WORKSPACE_ATTESTATION = "FAILED_WORKSPACE_ATTESTATION"
    FAILED_ROUTING_ATTESTATION = "FAILED_ROUTING_ATTESTATION"
    FAILED_PROVIDER_TRAFFIC_GUARD = "FAILED_PROVIDER_TRAFFIC_GUARD"
    FAILED_CLEANUP = "FAILED_CLEANUP"


class PreflightDecision(str, Enum):
    """Live-authorization decision (task §49). C does NOT authorize live traffic."""

    PREFLIGHT_FAILED = "A"
    PREFLIGHT_PASSED_BUT_LIVE_NOT_JUSTIFIED = "B"
    PREFLIGHT_PASSED_AND_LIVE_AUTHORIZATION_GATE_JUSTIFIED = "C"


# --------------------------------------------------------------------------- #
# Actual consumed provider-workload counters (task §17-§20/§37-§39) — all 0
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class ConsumedCounters:
    graph_index_operations: int = 0
    graph_index_attempts: int = 0
    gd_queries: int = 0
    vector_query_operations: int = 0
    query_embedding_operations: int = 0
    final_answer_calls: int = 0
    judge_model_calls: int = 0
    query_data_calls: int = 0
    client_query_calls: int = 0
    document_insert_calls: int = 0
    external_provider_network_calls: int = 0
    normal_db_mutations: int = 0

    def all_zero(self) -> bool:
        return all(v == 0 for v in vars(self).values())

    def as_dict(self) -> Dict[str, int]:
        return dict(vars(self))


# --------------------------------------------------------------------------- #
# Plans (task §22/§23/§24) — computed, NOT executed
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class ProvisioningPlan:
    per_notebook_index_ops: Dict[str, int]
    total_index_ops: int
    shared_source_plan: Dict[str, List[str]]
    shared_source_plan_ok: bool
    removal_plan: Dict[str, int]


def build_provisioning_plan(fx: ds.FixturePN02) -> ProvisioningPlan:
    """Compute the exact future provisioning plan (task §22/§23/§24). No indexing."""
    per_nb = {nb: len(fx.members_of(nb)) for nb in fx.notebook_ids}
    total = sum(per_nb.values())

    shared_plan: Dict[str, List[str]] = {}
    shared_ok = True
    for sk in fx.shared_source_keys():
        owners = sorted(fx.notebooks_of(sk))
        shared_plan[sk] = owners
        if len(owners) != 2:
            shared_ok = False
    # SH_AB->{A,B}, SH_AC->{A,C}, SH_BC->{B,C} (task §23): each into exactly its 2,
    # never the third.
    expected = {
        "SH_AB": ["NB_A", "NB_B"],
        "SH_AC": ["NB_A", "NB_C"],
        "SH_BC": ["NB_B", "NB_C"],
    }
    for sk, owners in expected.items():
        if shared_plan.get(sk) != owners:
            shared_ok = False

    scenario = ds.membership_removal_scenario(fx)
    removal_plan = {
        "REFERENCE_EDGE_DELETE_OPERATIONS": 1,
        "GRAPH_DELETE_OPERATIONS": 1,
        "POST_REMOVAL_GD_QUERIES": 2,
        "POST_REMOVAL_VECTOR_QUERIES": 2,
        "POST_REMOVAL_FINAL_ANSWER_CALLS": 0,
        "removed_from": scenario.removed_from_notebook == "NB_A",
        "retained_in": scenario.retained_notebook == "NB_B",
        "shared_source_is_SH_AB": scenario.shared_source == "SH_AB",
    }
    return ProvisioningPlan(
        per_notebook_index_ops=per_nb,
        total_index_ops=total,
        shared_source_plan=shared_plan,
        shared_source_plan_ok=shared_ok,
        removal_plan=removal_plan,
    )


# --------------------------------------------------------------------------- #
# Result
# --------------------------------------------------------------------------- #

@dataclass
class PreflightResult:
    outcome: PreflightOutcome
    decision: PreflightDecision
    manifest: Dict[str, object] = field(default_factory=dict)
    attestation_report: Dict[str, object] = field(default_factory=dict)
    routing_report: Dict[str, object] = field(default_factory=dict)
    workload_report: Dict[str, object] = field(default_factory=dict)
    counters: ConsumedCounters = field(default_factory=ConsumedCounters)
    cleanup: Dict[str, int] = field(default_factory=dict)
    errors: Tuple[str, ...] = ()

    def to_dict(self) -> Dict[str, object]:
        return {
            "outcome": self.outcome.value,
            "decision": self.decision.value,
            "manifest": self.manifest,
            "attestation_report": self.attestation_report,
            "routing_report": self.routing_report,
            "workload_report": self.workload_report,
            "actual_consumed_counters": self.counters.as_dict(),
            "cleanup": self.cleanup,
            "errors": list(self.errors),
        }


# --------------------------------------------------------------------------- #
# Negative routing tests (task §14/§15/§16) — infrastructure-level, NO graph query
# --------------------------------------------------------------------------- #

def _run_routing_negatives(
    topology: ProvisionedTopology,
    attestations: Dict[str, mf.WorkspaceAttestation],
) -> Dict[str, object]:
    nbs = topology.notebook_ids
    a, b, c = nbs[0], nbs[1], nbs[2]
    ep = topology.endpoints
    ws = topology.workspace_ids

    # Wrong endpoint: route A to B's endpoint -> must reject (task §14).
    wrong_endpoint_rejected = False
    try:
        rt.route_query(
            topology, a, target_endpoint=ep[b], target_workspace_id=ws[a]
        )
    except rt.WrongEndpointRoutingError:
        wrong_endpoint_rejected = True

    # Wrong workspace: route B to C's workspace -> must reject (task §14).
    wrong_workspace_rejected = False
    try:
        rt.route_query(
            topology, b, target_endpoint=ep[b], target_workspace_id=ws[c]
        )
    except rt.WrongWorkspaceRoutingError:
        wrong_workspace_rejected = True

    # Correct route accepted.
    correct_route_accepted = False
    try:
        rt.route_query(
            topology, a, target_endpoint=ep[a], target_workspace_id=ws[a]
        )
        correct_route_accepted = True
    except rt.RoutingViolation:
        correct_route_accepted = False

    # Unattested route: a false attestation must be refused BEFORE any GD (task §15).
    unattested_rejected = False
    bad_att = mf.WorkspaceAttestation(
        notebook_id=a,
        workspace_id=ws[a],
        owned_endpoint_identity=ep[a],
        expected_storage_identity=topology.storage_identities[a],
        lightrag_version="v1.5.6",
        provider_binding_fingerprint="pbf_unattested",
        attested=False,
        failure_reasons=("simulated_unattested",),
    )
    try:
        rt.authorize_future_gd(topology, a, bad_att)
    except mf.UnattestedWorkspaceError:
        unattested_rejected = True

    # An attested + correctly-routed request passes the authorization gate
    # (still NEVER calls /query/data — the seam stays inert).
    attested_route_authorized = False
    try:
        rt.authorize_future_gd(topology, a, attestations[a])
        attested_route_authorized = True
    except rt.RoutingViolation:
        attested_route_authorized = False

    alias = rt.cross_workspace_storage_aliasing(topology)

    return {
        "CORRECT_ROUTE_ACCEPTED": correct_route_accepted,
        "WRONG_ENDPOINT_ROUTING_REJECTED": wrong_endpoint_rejected,
        "WRONG_WORKSPACE_ROUTING_REJECTED": wrong_workspace_rejected,
        "UNATTESTED_QUERY_AUTHORIZATION_REJECTED": unattested_rejected,
        "ATTESTED_ROUTE_AUTHORIZED": attested_route_authorized,
        "CROSS_WORKSPACE_STORAGE_ALIASING": alias.aliasing_count,
        "distinct_storage_identities": alias.distinct_storage_identities,
        "distinct_endpoints": len(set(ep.values())),
        "distinct_workspaces": len(set(ws.values())),
    }


# --------------------------------------------------------------------------- #
# Orchestrator (task §40-§49)
# --------------------------------------------------------------------------- #

def run_preflight(
    *,
    run_id: str,
    git_commit: str,
    fixture_dir: Optional[str] = None,
    base_storage_dir: Optional[str] = None,
    start_runtimes: bool = True,
) -> PreflightResult:
    """Execute the full PN02C preflight. Provider-free; always cleans up."""
    errors: List[str] = []
    guard = ProviderTrafficGuard()

    # --- Precheck: fixture + hash + version pin + workload ledger (§1/§4/§21) ---
    from pathlib import Path

    fdir = Path(fixture_dir) if fixture_dir else None
    try:
        fx = ds.load_fixture(fdir)
        ds.validate_fixture(fx)
    except Exception as exc:
        return PreflightResult(
            PreflightOutcome.FAILED_FIXTURE_HASH,
            PreflightDecision.PREFLIGHT_FAILED,
            errors=(f"fixture load/validation: {exc}",),
        )
    ok, detail = ds.verify_fixture_hash(fdir)
    if not ok or detail != EXPECTED_FIXTURE_HASH:
        return PreflightResult(
            PreflightOutcome.FAILED_FIXTURE_HASH,
            PreflightDecision.PREFLIGHT_FAILED,
            errors=(f"fixture hash: {detail}",),
        )
    try:
        mf.validate_lightrag_version(mf.LIGHTRAG_EVAL_VERSION)
        rt.attest_provider_config()
        ledger = wl.frozen_ledger()
        wl.validate_against_fixture(ledger, fx)
    except Exception as exc:
        return PreflightResult(
            PreflightOutcome.FAILED_PRECHECK,
            PreflightDecision.PREFLIGHT_FAILED,
            errors=(f"precheck: {exc}",),
        )

    plan = build_provisioning_plan(fx)

    # --- Provision the three owned runtimes provider-free (§27-§31) ---
    topology: Optional[ProvisionedTopology] = None
    try:
        topology = provision_runtimes(
            fx,
            run_id=run_id,
            base_storage_dir=base_storage_dir,
            start=start_runtimes,
            guard=guard,
        )
    except EndpointCollisionError as exc:
        return PreflightResult(
            PreflightOutcome.FAILED_ENDPOINT_ALLOCATION,
            PreflightDecision.PREFLIGHT_FAILED,
            errors=(str(exc),),
        )
    except StorageCollisionError as exc:
        return PreflightResult(
            PreflightOutcome.FAILED_STORAGE_ISOLATION,
            PreflightDecision.PREFLIGHT_FAILED,
            errors=(str(exc),),
        )
    except PartialStartupError as exc:
        return PreflightResult(
            PreflightOutcome.FAILED_SIDECAR_START,
            PreflightDecision.PREFLIGHT_FAILED,
            errors=(str(exc),),
        )
    except ProviderTrafficAttempted as exc:
        return PreflightResult(
            PreflightOutcome.FAILED_PROVIDER_TRAFFIC_GUARD,
            PreflightDecision.PREFLIGHT_FAILED,
            errors=(str(exc),),
        )

    try:
        # --- Attestation gate (§12/§13) ---
        attestations, all_attested = rt.attest_all(topology)
        attestation_report = {
            "per_workspace": {
                nb: {
                    "notebook_id": att.notebook_id,
                    "workspace_id": att.workspace_id,
                    "owned_endpoint_identity": att.owned_endpoint_identity,
                    "expected_storage_identity": att.expected_storage_identity,
                    "lightrag_version": att.lightrag_version,
                    "provider_binding_fingerprint": att.provider_binding_fingerprint,
                    "attested": att.attested,
                    "failure_reasons": list(att.failure_reasons),
                }
                for nb, att in attestations.items()
            },
            "WORKSPACE_A_ATTESTED": attestations[topology.notebook_ids[0]].attested,
            "WORKSPACE_B_ATTESTED": attestations[topology.notebook_ids[1]].attested,
            "WORKSPACE_C_ATTESTED": attestations[topology.notebook_ids[2]].attested,
            "ALL_WORKSPACES_ATTESTED": all_attested,
        }
        if not all_attested:
            outcome = PreflightOutcome.FAILED_WORKSPACE_ATTESTATION
            decision = PreflightDecision.PREFLIGHT_FAILED
            errors.append("one or more workspaces failed attestation")
        else:
            # --- Routing negatives (§14/§15/§16) ---
            routing_report = _run_routing_negatives(topology, attestations)
            routing_ok = (
                routing_report["CORRECT_ROUTE_ACCEPTED"] is True
                and routing_report["WRONG_ENDPOINT_ROUTING_REJECTED"] is True
                and routing_report["WRONG_WORKSPACE_ROUTING_REJECTED"] is True
                and routing_report["UNATTESTED_QUERY_AUTHORIZATION_REJECTED"] is True
                and routing_report["ATTESTED_ROUTE_AUTHORIZED"] is True
                and routing_report["CROSS_WORKSPACE_STORAGE_ALIASING"] == 0
                and routing_report["distinct_endpoints"] == 3
                and routing_report["distinct_workspaces"] == 3
                and routing_report["distinct_storage_identities"] == 3
            )
            if not routing_ok:
                outcome = PreflightOutcome.FAILED_ROUTING_ATTESTATION
                decision = PreflightDecision.PREFLIGHT_FAILED
                errors.append("routing attestation negative tests failed")
            else:
                guard.assert_zero()
                outcome = PreflightOutcome.COMPLETED
                decision = (
                    PreflightDecision.PREFLIGHT_PASSED_AND_LIVE_AUTHORIZATION_GATE_JUSTIFIED
                )

        # --- Reports (§40-§43) ---
        counters = ConsumedCounters(
            external_provider_network_calls=guard.external_provider_network_calls
        )
        workload_report: Dict[str, object] = {
            "FUTURE_ALLOWED_CAP": wl.as_dict(ledger),
            "PN02C_ACTUAL_CONSUMED": counters.as_dict(),
            "WORKLOAD_LEDGER_PREFLIGHT": "PASS",
            "provisioning_plan": {
                "per_notebook_index_ops": plan.per_notebook_index_ops,
                "TOTAL_GRAPH_INDEX_OPERATIONS_PLANNED": plan.total_index_ops,
                "SHARED_SOURCE_WORKSPACE_PLAN": (
                    "PASS" if plan.shared_source_plan_ok else "FAIL"
                ),
                "shared_source_plan": plan.shared_source_plan,
                "membership_removal_plan": plan.removal_plan,
            },
        }
        manifest = _build_manifest(
            topology, fx, run_id, git_commit, attestation_report, counters
        )
        routing_report_final = (
            routing_report if outcome is PreflightOutcome.COMPLETED else {}
        )
    finally:
        cleanup = topology.cleanup()
    cleanup_dict = {
        "PN02C_OWNED_PROCESSES_REMAINING": cleanup.processes_remaining,
        "PN02C_RUNTIME_STORAGE_RESIDUE": cleanup.storage_residue,
    }
    if cleanup.processes_remaining != 0 or cleanup.storage_residue != 0:
        errors.append("cleanup incomplete")
        outcome = PreflightOutcome.FAILED_CLEANUP
        decision = PreflightDecision.PREFLIGHT_FAILED

    manifest["cleanup"] = cleanup_dict
    return PreflightResult(
        outcome=outcome,
        decision=decision,
        manifest=manifest,
        attestation_report=attestation_report,
        routing_report=routing_report_final,
        workload_report=workload_report,
        counters=counters,
        cleanup=cleanup_dict,
        errors=tuple(errors),
    )


def _build_manifest(
    topology: ProvisionedTopology,
    fx: ds.FixturePN02,
    run_id: str,
    git_commit: str,
    attestation_report: Dict[str, object],
    counters: ConsumedCounters,
) -> Dict[str, object]:
    """Content-safe run manifest (task §40). Ids/counts/flags only — no secrets.

    Runtime fields are DELIBERATELY precise so the report cannot be misread as
    having booted a real LightRAG engine: the three runtimes are attestation
    micro-runtimes; the version is a config-pin match, NOT a real-runtime probe;
    and PN02D's REAL_LIGHTRAG_BOOT_GATE is required before any indexing (§2-§9).
    """
    mf.validate_boundary_b(mf.DEFAULT_BOUNDARY_B)
    n_attestation_runtimes = len(topology.identities)
    return {
        "run_id": run_id,
        "git_commit": git_commit,
        "fixture_name": ds.FIXTURE_NAME,
        "fixture_hash": EXPECTED_FIXTURE_HASH,
        # --- Version: config-pin match only, NOT a real-runtime attestation. ---
        "LIGHTRAG_VERSION_EXPECTED": mf.LIGHTRAG_EVAL_VERSION,
        "LIGHTRAG_CONFIG_VERSION_PIN_MATCH": "PASS",
        "REAL_LIGHTRAG_RUNTIME_VERSION_ATTESTED": False,
        "REAL_LIGHTRAG_RUNTIME_VERSION": "NOT_PROBED",
        "lightrag_version_source": "config_pin (VERIFIED_LIGHTRAG_VERSION); "
        "real-runtime live probe deferred to PN02D live gate",
        # --- Runtime terminology: attestation micro-runtimes, NOT LightRAG. ---
        "ATTESTATION_RUNTIME_COUNT": n_attestation_runtimes,
        "REAL_LIGHTRAG_RUNTIME_COUNT": 0,
        "REAL_LIGHTRAG_RUNTIME_BOOTED": False,
        "REAL_LIGHTRAG_RUNTIME_BOOT_PROVEN_PROVIDER_FREE": False,
        "REAL_LIGHTRAG_PROVIDER_FREE_BOOT_PROVEN": False,
        "REAL_LIGHTRAG_BOOT_GATE_REQUIRED_BEFORE_INDEX": True,
        "attestation_runtimes": {
            nb: {
                **ident.as_safe_dict(),
                "config_fingerprint": rt.runtime_config_fingerprint(ident),
            }
            for nb, ident in topology.identities.items()
        },
        "workload_caps": wl.as_dict(),
        "actual_consumed": counters.as_dict(),
        "boundary_b": {
            "REAL_INTERNAL_DATA_ALLOWED": False,
            "SYNTHETIC_ONLY": True,
            "dataset_class": mf.DatasetClass.SYNTHETIC_FIXTURE.value,
        },
        "provider_binding_configured": rt.provider_binding_configured(),
        "provider_traffic": counters.external_provider_network_calls,
        "attestation": {
            "ALL_WORKSPACES_ATTESTED": attestation_report.get(
                "ALL_WORKSPACES_ATTESTED"
            )
        },
        "pn02_live_authorized": False,
    }


# --------------------------------------------------------------------------- #
# CLI (task §51)
# --------------------------------------------------------------------------- #

def _git_commit() -> str:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
        )
        return out.stdout.strip()
    except Exception:  # pragma: no cover
        return "unknown"


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="preflightpn02c",
        description="GraphRAG-PN02C provider-free live preflight & routing attestation.",
    )
    parser.add_argument("--run-id", default=None, help="run identity (default: pn02c-<n>)")
    parser.add_argument(
        "--out", default=None, help="write the content-safe JSON report to this path"
    )
    parser.add_argument(
        "--no-start",
        action="store_true",
        help="structural mode: do not bind loopback runtimes",
    )
    parser.add_argument(
        "--base-storage-dir",
        default=None,
        help="parent dir for run-owned storage roots (default: OS temp)",
    )
    args = parser.parse_args(argv)

    import time

    run_id = args.run_id or f"pn02c-{int(time.time())}"
    result = run_preflight(
        run_id=run_id,
        git_commit=_git_commit(),
        base_storage_dir=args.base_storage_dir,
        start_runtimes=not args.no_start,
    )
    payload = json.dumps(result.to_dict(), indent=2, sort_keys=True)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(payload)
    else:
        print(payload)
    # Non-zero exit if preflight did not complete cleanly.
    return 0 if result.outcome is PreflightOutcome.COMPLETED else 1


__all__ = [
    "EXPECTED_FIXTURE_HASH",
    "PreflightOutcome",
    "PreflightDecision",
    "ConsumedCounters",
    "ProvisioningPlan",
    "build_provisioning_plan",
    "PreflightResult",
    "run_preflight",
    "main",
]


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
