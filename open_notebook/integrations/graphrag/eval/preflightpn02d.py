"""PN02D-A REAL LightRAG data-plane preflight orchestrator (Gate 0 + Gate 1) + CLI.

EVALUATION-ONLY. Nothing in production imports this. PN02D-A replaces the PN02C
attestation micro-runtimes with REAL LightRAG ``v1.5.6`` Docker sidecars and proves,
BEFORE ANY INDEXING, that the per-notebook topology can be booted and attested with
ZERO provider traffic and ZERO data (task §7-§21). It performs NO document insert,
NO embedding, NO ``/query/data``, NO ``client.query``, NO final answer, NO judge.

Gate 0 (task §7-§13): boot EXACTLY ONE run-owned real sidecar on its own
``--internal`` network, attest the running version (three provider-free signals),
attest workspace/storage/endpoint identity, prove ``EXTERNAL_PROVIDER_NETWORK_CALLS
= 0`` via the socket-level egress guard, then shut down and clean up to 0 residue.

Gate 1 (task §14-§21): only after Gate 0 PASS, boot THREE real sidecars A/B/C
(one-to-one runtime/workspace/storage/endpoint/network), attest all three, prove
wrong-endpoint / wrong-workspace / unattested routes are REJECTED (reusing the PN02C
routing contract, never ``/query/data``), prove zero egress, and clean up.

Only Gate 0 PASS AND Gate 1 PASS mints ``RealLightRAGPreflightAuthorization`` — the
capability a FUTURE indexing seam must present. That capability authorizes NO
provider run (task §35): a separate operator decision is still required.

Run (authorized live gate only): ``uv run python -m
open_notebook.integrations.graphrag.eval.preflightpn02d --live``.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import subprocess
import sys
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from open_notebook.integrations.graphrag.eval import datasetpn02 as ds
from open_notebook.integrations.graphrag.eval import manifestpn02 as mf
from open_notebook.integrations.graphrag.eval import routingpn02c as rt
from open_notebook.integrations.graphrag.eval.attestpn02d import (
    RealRuntimeAttestation,
    RealVersionAttestation,
    attest_real_runtime,
    attest_version,
    mint_real_preflight_authorization,
)
from open_notebook.integrations.graphrag.eval.egressguardpn02d import (
    ProviderEgressGuard,
    observe_from_text,
)
from open_notebook.integrations.graphrag.eval.provisionpn02c import (
    FORBIDDEN_STORAGE_SUBSTRINGS,
    storage_identity_for,
)
from open_notebook.integrations.graphrag.eval.realsidecarpn02d import (
    EXPECTED_IMAGE_VERSION_LABEL,
    REAL_LIGHTRAG_IMAGE,
    DockerCLI,
    ProviderBindingInCommand,
    RealCleanupReport,
    RealRuntimeIdentity,
    RealSidecarSpec,
    RealTopology,
    allocate_real_storage_root,
    assert_no_provider_binding_in_command,
    build_run_command,
    endpoint_for,
    make_spec,
    wait_healthy,
)
from open_notebook.integrations.graphrag.eval.sidecar_diag08 import (
    build_sidecar_diagnostic,
)

EXPECTED_FIXTURE_HASH = (
    "9ce7df742810424d9ef7b7b34962187d9061245f81471f5fb1d7f2608f6899a6"
)
HEALTH_TIMEOUT_SECONDS = 120.0
STORAGE_OWNED_MARKER = "pn02da"

# Gate-0 uses a dedicated run-owned notebook identity (NOT an A/B/C fixture nb).
GATE0_NOTEBOOK_ID = "GATE0"
GATE0_RECORD_ID = "notebook:gr_pn02da_gate0"


class OutcomeDA(str, Enum):
    """Technical outcome taxonomy — separate from any scientific verdict (task §37)."""

    COMPLETED = "COMPLETED"
    FAILED_PRECHECK = "FAILED_PRECHECK"
    FAILED_FIXTURE_HASH = "FAILED_FIXTURE_HASH"
    FAILED_DOCKER_UNAVAILABLE = "FAILED_DOCKER_UNAVAILABLE"
    FAILED_IMAGE_MISSING = "FAILED_IMAGE_MISSING"
    FAILED_IMAGE_VERSION = "FAILED_IMAGE_VERSION"
    FAILED_NETWORK_ISOLATION = "FAILED_NETWORK_ISOLATION"
    FAILED_PROVIDER_BINDING_IN_COMMAND = "FAILED_PROVIDER_BINDING_IN_COMMAND"
    FAILED_SIDECAR_START = "FAILED_SIDECAR_START"
    FAILED_VERSION_ATTESTATION = "FAILED_VERSION_ATTESTATION"
    FAILED_WORKSPACE_ATTESTATION = "FAILED_WORKSPACE_ATTESTATION"
    FAILED_STORAGE_ATTESTATION = "FAILED_STORAGE_ATTESTATION"
    FAILED_ROUTING_ATTESTATION = "FAILED_ROUTING_ATTESTATION"
    FAILED_PROVIDER_EGRESS = "FAILED_PROVIDER_EGRESS"
    FAILED_CLEANUP = "FAILED_CLEANUP"


class DecisionDA(str, Enum):
    """Live-authorization decision (task §37). C authorizes NO provider run."""

    REAL_LIGHTRAG_PREFLIGHT_FAILED = "A"
    REAL_LIGHTRAG_PREFLIGHT_PASSED_BUT_PROVIDER_RUN_NOT_JUSTIFIED = "B"
    REAL_LIGHTRAG_PREFLIGHT_PASSED_AND_PROVIDER_RUN_AUTHORIZATION_GATE_JUSTIFIED = "C"


DECISION_NAMES = {
    DecisionDA.REAL_LIGHTRAG_PREFLIGHT_FAILED: "REAL_LIGHTRAG_PREFLIGHT_FAILED",
    DecisionDA.REAL_LIGHTRAG_PREFLIGHT_PASSED_BUT_PROVIDER_RUN_NOT_JUSTIFIED: (
        "REAL_LIGHTRAG_PREFLIGHT_PASSED_BUT_PROVIDER_RUN_NOT_JUSTIFIED"
    ),
    DecisionDA.REAL_LIGHTRAG_PREFLIGHT_PASSED_AND_PROVIDER_RUN_AUTHORIZATION_GATE_JUSTIFIED: (
        "REAL_LIGHTRAG_PREFLIGHT_PASSED_AND_PROVIDER_RUN_AUTHORIZATION_GATE_JUSTIFIED"
    ),
}


@dataclass(frozen=True)
class ZeroDataCounters:
    """Every provider-backed / data-plane counter — all structurally 0 (task §20)."""

    document_insert_calls: int = 0
    graph_index_operations: int = 0
    query_embedding_operations: int = 0
    gd_queries: int = 0
    vector_query_operations: int = 0
    query_data_calls: int = 0
    client_query_calls: int = 0
    final_answer_calls: int = 0
    judge_model_calls: int = 0
    normal_db_mutations: int = 0
    external_provider_network_calls: int = 0

    def all_zero(self) -> bool:
        return all(v == 0 for v in vars(self).values())

    def as_dict(self) -> Dict[str, int]:
        return dict(vars(self))


# --------------------------------------------------------------------------- #
# Per-runtime boot + attestation (shared by Gate 0 and Gate 1)
# --------------------------------------------------------------------------- #

@dataclass
class RuntimeBootResult:
    identity: RealRuntimeIdentity
    version: Optional[RealVersionAttestation] = None
    attestation: Optional[RealRuntimeAttestation] = None
    healthy: bool = False
    elapsed_seconds: float = 0.0
    sidecar_diagnostic: Dict[str, object] = field(default_factory=dict)
    egress_external_sockets: int = 0
    workspace_observed: str = ""
    mount_observed: str = ""
    failure_stage: Optional[OutcomeDA] = None
    error: Optional[str] = None


def _storage_attested(identity: RealRuntimeIdentity, observed_mount: str) -> Tuple[bool, List[str]]:
    """Attest the RUNTIME's storage is run-owned/fresh/not production (task §11).

    Our root already passed the forbidden-root guard at allocation and is a fresh
    mkdtemp. Here we corroborate the runtime's ACTUAL mount: it must be nonempty,
    carry the owned ``pn02da`` marker, and match no forbidden production/benchmark
    marker."""
    reasons: List[str] = []
    low = observed_mount.replace("\\", "/").lower()
    if not observed_mount:
        reasons.append("mount_source_empty")
        return False, reasons
    if STORAGE_OWNED_MARKER not in low:
        reasons.append("mount_source_not_owned")
    for bad in FORBIDDEN_STORAGE_SUBSTRINGS:
        # The bind DESTINATION is /app/data/rag_storage, but the SOURCE must not be a
        # production/benchmark root. "rag_storage" as a source-path marker is the risk.
        if bad in low and bad != "rag_storage":
            reasons.append(f"mount_source_forbidden:{bad}")
        elif bad == "rag_storage" and "/rag_storage" in low and STORAGE_OWNED_MARKER not in low:
            reasons.append("mount_source_forbidden:rag_storage")
    return (not reasons), reasons


def provision_runtime(
    docker: DockerCLI,
    spec: RealSidecarSpec,
    *,
    image_label_version: str,
    egress_guard: ProviderEgressGuard,
    timeout_seconds: float = HEALTH_TIMEOUT_SECONDS,
    poll_seconds: float = 2.0,
) -> RuntimeBootResult:
    """Boot ONE real sidecar and fully attest it provider-free. No cleanup here —
    the caller's topology owns teardown so even a partial boot is cleaned (§29)."""
    identity = RealRuntimeIdentity(
        notebook_id=spec.notebook_id,
        notebook_record_id=spec.notebook_record_id,
        workspace_id=spec.workspace_id,
        storage_root=spec.storage_root,
        storage_identity=storage_identity_for(spec.storage_root),
        endpoint=endpoint_for(spec.container_name),
        network_name=spec.network_name,
        container_name=spec.container_name,
        image=spec.image,
        image_version_label=image_label_version,
        lightrag_version_config=mf.canonical_version(mf.LIGHTRAG_EVAL_VERSION),
        synthetic_only=True,
        runtime_id=spec.container_name,
    )
    result = RuntimeBootResult(identity=identity)

    # 1. Own internal network (kernel-enforced zero egress) + verify isolation.
    if not docker.network_create_internal(spec.network_name):
        result.failure_stage = OutcomeDA.FAILED_SIDECAR_START
        result.error = "network create failed"
        return result
    is_internal = docker.network_is_internal(spec.network_name)
    if is_internal is not True:
        result.failure_stage = OutcomeDA.FAILED_NETWORK_ISOLATION
        result.error = f"network internal flag = {is_internal!r} (expected True)"
        return result

    # 2. Build + guard + run the boot command (no provider binding, no secret).
    argv = build_run_command(spec)
    try:
        assert_no_provider_binding_in_command(argv)
    except ProviderBindingInCommand as exc:
        result.failure_stage = OutcomeDA.FAILED_PROVIDER_BINDING_IN_COMMAND
        result.error = str(exc)
        return result
    run_res = docker.run(argv)
    if run_res.returncode != 0:
        result.failure_stage = OutcomeDA.FAILED_SIDECAR_START
        result.error = "docker run non-zero"
        return result
    result.identity = dataclasses.replace(
        identity, container_id=(run_res.stdout.strip()[:64] or None)
    )

    # 3. Wait until running + healthy.
    healthy, elapsed, obs = wait_healthy(
        docker, spec.container_name, timeout_seconds=timeout_seconds, poll_seconds=poll_seconds
    )
    result.healthy = healthy
    result.elapsed_seconds = elapsed
    result.sidecar_diagnostic = build_sidecar_diagnostic(
        obs, timeout_seconds=timeout_seconds, elapsed_seconds=elapsed
    ).as_dict()
    if not healthy:
        result.failure_stage = OutcomeDA.FAILED_SIDECAR_START
        result.error = "sidecar not healthy within timeout"
        return result

    # 4. Provider-free version attestation (3 independent signals).
    code, core_version, _status = docker.health(spec.container_name)
    import_version = docker.runtime_version(spec.container_name)
    version = attest_version(
        health_core_version=core_version,
        import_version=import_version,
        image_label_version=image_label_version,
    )
    result.version = version
    result.identity = dataclasses.replace(
        result.identity, runtime_version_observed=version.observed_version
    )

    # 5. Workspace + storage (runtime-observed).
    workspace_observed = docker.workspace_env(spec.container_name)
    mount_observed = docker.mount_source(spec.container_name)
    result.workspace_observed = workspace_observed
    result.mount_observed = mount_observed

    # 6. Egress detection (socket-level) — sampled after boot + probes.
    proc = docker.proc_net_tcp(spec.container_name)
    egress_obs = observe_from_text(proc)
    egress_guard.observe(egress_obs, seam=f"boot:{spec.notebook_id}")
    result.egress_external_sockets = egress_obs.external_peer_sockets

    # 7. Storage attestation (runtime mount).
    storage_ok, storage_reasons = _storage_attested(result.identity, mount_observed)

    # 8. Full runtime attestation.
    att = attest_real_runtime(
        result.identity,
        version=version,
        observed_workspace_id=workspace_observed,
        observed_endpoint=endpoint_for(spec.container_name),
        observed_storage_identity=result.identity.storage_identity,
        external_egress_sockets=egress_obs.external_peer_sockets,
    )
    result.attestation = att
    if not version.attested:
        result.failure_stage = OutcomeDA.FAILED_VERSION_ATTESTATION
        result.error = ";".join(version.failure_reasons)
    elif egress_obs.external_peer_sockets != 0:
        result.failure_stage = OutcomeDA.FAILED_PROVIDER_EGRESS
        result.error = f"external_peer_sockets={egress_obs.external_peer_sockets}"
    elif workspace_observed != spec.workspace_id:
        result.failure_stage = OutcomeDA.FAILED_WORKSPACE_ATTESTATION
        result.error = f"workspace observed {workspace_observed!r} != {spec.workspace_id!r}"
    elif not storage_ok:
        result.failure_stage = OutcomeDA.FAILED_STORAGE_ATTESTATION
        result.error = ";".join(storage_reasons)
    elif not att.attested:
        result.failure_stage = OutcomeDA.FAILED_WORKSPACE_ATTESTATION
        result.error = ";".join(att.failure_reasons)
    return result


# --------------------------------------------------------------------------- #
# Gate results
# --------------------------------------------------------------------------- #

@dataclass
class GateResult:
    gate: str
    passed: bool
    outcome: OutcomeDA
    runtime_count: int
    report: Dict[str, object] = field(default_factory=dict)
    cleanup: Optional[RealCleanupReport] = None
    errors: Tuple[str, ...] = ()


def _egress_field(guard: ProviderEgressGuard) -> object:
    """Report the observed external-egress count, or ``NOT_SAMPLED`` when the boot
    failed before egress could be observed (samples == 0) — so a failed-boot report
    can never be misread as "provider-free boot proven" (review L2)."""
    if guard.samples == 0:
        return "NOT_SAMPLED"
    return guard.external_provider_network_calls


def _runtime_report(res: RuntimeBootResult) -> Dict[str, object]:
    v = res.version
    return {
        "notebook_id": res.identity.notebook_id,
        "workspace_id": res.identity.workspace_id,
        "endpoint": res.identity.endpoint,
        "storage_identity": res.identity.storage_identity,
        "network_name": res.identity.network_name,
        "container_name": res.identity.container_name,
        "image": res.identity.image,
        "image_version_label": res.identity.image_version_label,
        "runtime_version_observed": res.identity.runtime_version_observed,
        "healthy": res.healthy,
        "version_attested": bool(v and v.attested),
        "version_signals": {
            "health_core_version": v.health_core_version if v else None,
            "import_version": v.import_version if v else None,
            "image_label_version": v.image_label_version if v else None,
        },
        "workspace_observed_matches": res.workspace_observed == res.identity.workspace_id,
        "external_egress_sockets": res.egress_external_sockets,
        "attested": bool(res.attestation and res.attestation.attested),
        "failure_stage": res.failure_stage.value if res.failure_stage else None,
    }


# --------------------------------------------------------------------------- #
# Gate 0 — single real sidecar (task §7-§13)
# --------------------------------------------------------------------------- #

def run_gate0(
    docker: DockerCLI,
    *,
    run_id: str,
    base_storage_dir: Optional[str] = None,
    timeout_seconds: float = HEALTH_TIMEOUT_SECONDS,
    poll_seconds: float = 2.0,
    image_label_version: str,
) -> GateResult:
    """Boot EXACTLY ONE real sidecar, attest it provider-free, clean up (task §7)."""
    egress_guard = ProviderEgressGuard()
    workspace_id = mf.workspace_id_for(GATE0_RECORD_ID)
    storage_root = allocate_real_storage_root(base_storage_dir, GATE0_NOTEBOOK_ID)
    spec = make_spec(
        run_id=run_id,
        notebook_id=GATE0_NOTEBOOK_ID,
        notebook_record_id=GATE0_RECORD_ID,
        workspace_id=workspace_id,
        storage_root=storage_root,
    )
    topology = RealTopology(run_id=run_id, docker=docker)
    # Register a provisional identity BEFORE boot so cleanup covers a partial start.
    provisional = RealRuntimeIdentity(
        notebook_id=spec.notebook_id,
        notebook_record_id=spec.notebook_record_id,
        workspace_id=spec.workspace_id,
        storage_root=spec.storage_root,
        storage_identity="",
        endpoint=endpoint_for(spec.container_name),
        network_name=spec.network_name,
        container_name=spec.container_name,
        image=spec.image,
        image_version_label=image_label_version,
        lightrag_version_config=mf.canonical_version(mf.LIGHTRAG_EVAL_VERSION),
        synthetic_only=True,
        runtime_id=spec.container_name,
    )
    topology.identities[spec.notebook_id] = provisional
    errors: List[str] = []
    try:
        res = provision_runtime(
            docker,
            spec,
            image_label_version=image_label_version,
            egress_guard=egress_guard,
            timeout_seconds=timeout_seconds,
            poll_seconds=poll_seconds,
        )
        topology.identities[spec.notebook_id] = res.identity
        report: Dict[str, object] = {
            "runtime": _runtime_report(res),
            "sidecar_diagnostic": res.sidecar_diagnostic,
            "EXTERNAL_PROVIDER_NETWORK_CALLS": _egress_field(egress_guard),
            "REAL_LIGHTRAG_PROVIDER_FREE_BOOT_PROVEN": res.failure_stage is None
            and egress_guard.samples > 0
            and egress_guard.external_provider_network_calls == 0,
            "egress_samples": egress_guard.samples,
            "DOCUMENT_INSERT_CALLS": 0,
            "QUERY_DATA_CALLS": 0,
            "CLIENT_QUERY_CALLS": 0,
            "EMBEDDING_CALLS": 0,
            "LLM_CALLS": 0,
        }
        outcome = res.failure_stage or OutcomeDA.COMPLETED
        try:
            egress_guard.assert_zero()
        except Exception as exc:
            outcome = OutcomeDA.FAILED_PROVIDER_EGRESS
            errors.append(str(exc))
        if res.error:
            errors.append(res.error)
        passed = outcome is OutcomeDA.COMPLETED
    finally:
        cleanup = topology.cleanup()
    if not cleanup.clean:
        passed = False
        outcome = OutcomeDA.FAILED_CLEANUP
        errors.append(
            f"cleanup: proc={cleanup.processes_remaining} "
            f"storage={cleanup.storage_residue} net={cleanup.networks_remaining}"
        )
    report["cleanup"] = {
        "REAL_LIGHTRAG_OWNED_PROCESSES_REMAINING": cleanup.processes_remaining,
        "REAL_LIGHTRAG_RUNTIME_STORAGE_RESIDUE": cleanup.storage_residue,
        "REAL_LIGHTRAG_OWNED_NETWORKS_REMAINING": cleanup.networks_remaining,
    }
    return GateResult(
        gate="GATE0",
        passed=passed,
        outcome=outcome if passed else outcome,
        runtime_count=1,
        report=report,
        cleanup=cleanup,
        errors=tuple(errors),
    )


# --------------------------------------------------------------------------- #
# Gate 1 — three real sidecars A/B/C (task §14-§21)
# --------------------------------------------------------------------------- #

def _run_routing_negatives(topology: RealTopology, attestations: Dict[str, RealRuntimeAttestation]) -> Dict[str, object]:
    """Reuse the PN02C routing contract against REAL endpoints — NO /query/data.

    ``RealTopology`` is intentionally attribute-compatible with the PN02C
    ``ProvisionedTopology`` (task §19 reuse), so the routing functions apply
    unchanged; the ``type: ignore[arg-type]`` markers record that deliberate
    duck-typing across the concrete-typed PN02C boundary.
    """
    nbs = topology.notebook_ids
    a, b, c = nbs[0], nbs[1], nbs[2]
    ep = topology.endpoints
    ws = topology.workspace_ids

    wrong_endpoint_rejected = False
    try:
        rt.route_query(topology, a, target_endpoint=ep[b], target_workspace_id=ws[a])  # type: ignore[arg-type]
    except rt.WrongEndpointRoutingError:
        wrong_endpoint_rejected = True

    wrong_workspace_rejected = False
    try:
        rt.route_query(topology, b, target_endpoint=ep[b], target_workspace_id=ws[c])  # type: ignore[arg-type]
    except rt.WrongWorkspaceRoutingError:
        wrong_workspace_rejected = True

    correct_route_accepted = False
    try:
        rt.route_query(topology, a, target_endpoint=ep[a], target_workspace_id=ws[a])  # type: ignore[arg-type]
        correct_route_accepted = True
    except rt.RoutingViolation:
        correct_route_accepted = False

    unattested_rejected = False
    bad_att = mf.WorkspaceAttestation(
        notebook_id=a,
        workspace_id=ws[a],
        owned_endpoint_identity=ep[a],
        expected_storage_identity=topology.storage_identities[a],
        lightrag_version="1.5.6",
        provider_binding_fingerprint="pbf_unattested",
        attested=False,
        failure_reasons=("simulated_unattested",),
    )
    try:
        rt.authorize_future_gd(topology, a, bad_att)  # type: ignore[arg-type]
    except mf.UnattestedWorkspaceError:
        unattested_rejected = True

    attested_route_authorized = False
    try:
        rt.authorize_future_gd(topology, a, attestations[a].workspace)  # type: ignore[arg-type]
        attested_route_authorized = True
    except rt.RoutingViolation:
        attested_route_authorized = False

    alias = rt.cross_workspace_storage_aliasing(topology)  # type: ignore[arg-type]
    return {
        "CORRECT_ROUTE_ACCEPTED": correct_route_accepted,
        "REAL_WRONG_ENDPOINT_ROUTE_REJECTED": wrong_endpoint_rejected,
        "REAL_WRONG_WORKSPACE_ROUTE_REJECTED": wrong_workspace_rejected,
        "REAL_UNATTESTED_ROUTE_REJECTED": unattested_rejected,
        "ATTESTED_ROUTE_AUTHORIZED": attested_route_authorized,
        "REAL_LIGHTRAG_STORAGE_ALIASING": alias.aliasing_count,
        "distinct_storage_identities": alias.distinct_storage_identities,
        "distinct_endpoints": len(set(ep.values())),
        "distinct_workspaces": len(set(ws.values())),
    }


def run_gate1(
    docker: DockerCLI,
    fx: ds.FixturePN02,
    *,
    run_id: str,
    base_storage_dir: Optional[str] = None,
    timeout_seconds: float = HEALTH_TIMEOUT_SECONDS,
    poll_seconds: float = 2.0,
    image_label_version: str,
) -> GateResult:
    """Boot THREE real sidecars A/B/C, attest identity + routing, clean up (§14)."""
    egress_guard = ProviderEgressGuard()
    routes = mf.routing_manifest(fx)
    topology = RealTopology(run_id=run_id, docker=docker)
    results: Dict[str, RuntimeBootResult] = {}
    attestations: Dict[str, RealRuntimeAttestation] = {}
    errors: List[str] = []
    outcome = OutcomeDA.COMPLETED
    try:
        for nb in fx.notebook_ids:
            route = routes[nb]
            storage_root = allocate_real_storage_root(base_storage_dir, nb)
            spec = make_spec(
                run_id=run_id,
                notebook_id=nb,
                notebook_record_id=route.notebook_record_id,
                workspace_id=route.workspace_id,
                storage_root=storage_root,
            )
            # Register provisional identity for cleanup coverage before boot.
            topology.identities[nb] = RealRuntimeIdentity(
                notebook_id=nb,
                notebook_record_id=route.notebook_record_id,
                workspace_id=route.workspace_id,
                storage_root=storage_root,
                storage_identity="",
                endpoint=endpoint_for(spec.container_name),
                network_name=spec.network_name,
                container_name=spec.container_name,
                image=spec.image,
                image_version_label=image_label_version,
                lightrag_version_config=mf.canonical_version(mf.LIGHTRAG_EVAL_VERSION),
                synthetic_only=True,
                runtime_id=spec.container_name,
            )
            res = provision_runtime(
                docker,
                spec,
                image_label_version=image_label_version,
                egress_guard=egress_guard,
                timeout_seconds=timeout_seconds,
                poll_seconds=poll_seconds,
            )
            topology.identities[nb] = res.identity
            results[nb] = res
            if res.attestation is not None:
                attestations[nb] = res.attestation
            if res.failure_stage is not None:
                outcome = res.failure_stage
                if res.error:
                    errors.append(f"{nb}: {res.error}")
                break

        all_attested = len(attestations) == len(fx.notebook_ids) and all(
            a.attested for a in attestations.values()
        )
        routing_report: Dict[str, object] = {}
        if outcome is OutcomeDA.COMPLETED and all_attested:
            routing_report = _run_routing_negatives(topology, attestations)
            routing_ok = (
                routing_report["CORRECT_ROUTE_ACCEPTED"] is True
                and routing_report["REAL_WRONG_ENDPOINT_ROUTE_REJECTED"] is True
                and routing_report["REAL_WRONG_WORKSPACE_ROUTE_REJECTED"] is True
                and routing_report["REAL_UNATTESTED_ROUTE_REJECTED"] is True
                and routing_report["ATTESTED_ROUTE_AUTHORIZED"] is True
                and routing_report["REAL_LIGHTRAG_STORAGE_ALIASING"] == 0
                and routing_report["distinct_endpoints"] == 3
                and routing_report["distinct_workspaces"] == 3
                and routing_report["distinct_storage_identities"] == 3
            )
            if not routing_ok:
                outcome = OutcomeDA.FAILED_ROUTING_ATTESTATION
                errors.append("routing negatives failed")
        elif outcome is OutcomeDA.COMPLETED and not all_attested:
            outcome = OutcomeDA.FAILED_WORKSPACE_ATTESTATION
            errors.append("not all runtimes attested")

        try:
            egress_guard.assert_zero()
        except Exception as exc:
            outcome = OutcomeDA.FAILED_PROVIDER_EGRESS
            errors.append(str(exc))

        report: Dict[str, object] = {
            "runtimes": {nb: _runtime_report(r) for nb, r in results.items()},
            "REAL_LIGHTRAG_RUNTIME_COUNT": len(results),
            "REAL_LIGHTRAG_WORKSPACE_COUNT": len(set(topology.workspace_ids.values())),
            "REAL_LIGHTRAG_STORAGE_ROOT_COUNT": len(
                set(topology.storage_identities.values())
            ),
            "REAL_LIGHTRAG_ENDPOINT_COUNT": len(set(topology.endpoints.values())),
            "REAL_LIGHTRAG_ALL_VERSION_ATTESTED": (
                "PASS"
                if results and all(r.version and r.version.attested for r in results.values())
                else "FAIL"
            ),
            "REAL_LIGHTRAG_ALL_WORKSPACES_ATTESTED": "PASS" if all_attested else "FAIL",
            "routing": routing_report,
            "EXTERNAL_PROVIDER_NETWORK_CALLS": _egress_field(egress_guard),
            "REAL_LIGHTRAG_THREE_RUNTIME_PROVIDER_TRAFFIC": _egress_field(egress_guard),
            "egress_samples": egress_guard.samples,
        }
        passed = outcome is OutcomeDA.COMPLETED
    finally:
        cleanup = topology.cleanup()
    if not cleanup.clean:
        passed = False
        outcome = OutcomeDA.FAILED_CLEANUP
        errors.append(
            f"cleanup: proc={cleanup.processes_remaining} "
            f"storage={cleanup.storage_residue} net={cleanup.networks_remaining}"
        )
    report["cleanup"] = {
        "REAL_LIGHTRAG_OWNED_PROCESSES_REMAINING": cleanup.processes_remaining,
        "REAL_LIGHTRAG_RUNTIME_STORAGE_RESIDUE": cleanup.storage_residue,
        "REAL_LIGHTRAG_OWNED_NETWORKS_REMAINING": cleanup.networks_remaining,
    }
    return GateResult(
        gate="GATE1",
        passed=passed,
        outcome=outcome,
        runtime_count=len(results),
        report=report,
        cleanup=cleanup,
        errors=tuple(errors),
    )


# --------------------------------------------------------------------------- #
# Full PN02D-A orchestrator
# --------------------------------------------------------------------------- #

@dataclass
class PreflightResultDA:
    outcome: OutcomeDA
    decision: DecisionDA
    boot_gate: str  # PASS|FAIL
    gate0: Dict[str, object] = field(default_factory=dict)
    gate1: Dict[str, object] = field(default_factory=dict)
    counters: ZeroDataCounters = field(default_factory=ZeroDataCounters)
    provider_config: Dict[str, object] = field(default_factory=dict)
    authorization_minted: bool = False
    errors: Tuple[str, ...] = ()

    def to_dict(self) -> Dict[str, object]:
        return {
            "GRAPH_RAG_PN02DA_REAL_LIGHTRAG_DATA_PLANE_PREFLIGHT": (
                "COMPLETE" if self.outcome is OutcomeDA.COMPLETED else "BLOCKED"
            ),
            "AUTHORITATIVE_BASELINE": "7553c0f88cd14fe1cec6f573ebc9af61f6cf7789",
            "PN02_FIXTURE_HASH": EXPECTED_FIXTURE_HASH,
            "outcome": self.outcome.value,
            "PN02DA_DECISION": self.decision.value,
            "PN02DA_DECISION_NAME": DECISION_NAMES[self.decision],
            "REAL_LIGHTRAG_BOOT_GATE": self.boot_gate,
            "gate0": self.gate0,
            "gate1": self.gate1,
            "actual_consumed_counters": self.counters.as_dict(),
            "provider_config": self.provider_config,
            "PN02_PROVIDER_RUN_AUTHORIZATION_GATE_JUSTIFIED": (
                "YES" if self.decision
                is DecisionDA.REAL_LIGHTRAG_PREFLIGHT_PASSED_AND_PROVIDER_RUN_AUTHORIZATION_GATE_JUSTIFIED
                else "NO"
            ),
            "PN02_PROVIDER_RUN_AUTHORIZED": "NO",
            "GRAPHRAG_PRODUCTION_INTEGRATION": "NOT_APPROVED",
            "LIGHTRAG_ASK_INTEGRATION": "NOT_APPROVED",
            "GRAPH_RAG_09_JUSTIFIED": "NO",
            "authorization_minted": self.authorization_minted,
            "scientific_status": {
                "PER_NOTEBOOK_ISOLATION_EVIDENCED": "NOT_YET",
                "PER_NOTEBOOK_GRAPH_RETRIEVAL_VALUE_EVIDENCED": "NOT_YET",
                "PER_NOTEBOOK_GRAPH_QA_VALUE_EVIDENCED": "NOT_YET",
                "PER_NOTEBOOK_MULTIHOP_INCREMENTAL_VALUE_EVIDENCED": "NOT_YET",
            },
            "errors": list(self.errors),
        }


def _provider_config_report() -> Dict[str, object]:
    """Inspect the FUTURE live provider/auth config by env-var NAME only (task §24).

    Presence is NOT authorization (task §25). No value is ever read."""
    import os

    def present(name: str) -> str:
        return "YES" if os.environ.get(name, "").strip() else "NO"

    return {
        "LLM_PROVIDER_CONFIG_PRESENT": present("GRAPHRAG_POC_LLM_BINDING"),
        "EMBEDDING_PROVIDER_CONFIG_PRESENT": present("GRAPHRAG_POC_EMBEDDING_BINDING"),
        "OPENROUTER_KEY_PRESENT": (
            present("OPENROUTER_API_KEY")
            if os.environ.get("OPENROUTER_API_KEY", "").strip()
            else "NOT_REQUIRED_FOR_BOOT"
        ),
        "SIDE_CAR_AUTH_CONFIG_PRESENT": present("GRAPHRAG_POC_API_KEY"),
        "note": "PN02D-A boot uses NO provider binding and NO sidecar key; presence "
        "is future-config only and is NOT authorization (task §24/§25).",
    }


def run_preflight_da(
    docker: DockerCLI,
    *,
    run_id: str,
    fixture_dir: Optional[str] = None,
    base_storage_dir: Optional[str] = None,
    timeout_seconds: float = HEALTH_TIMEOUT_SECONDS,
    poll_seconds: float = 2.0,
) -> PreflightResultDA:
    """Run Gate 0 then (only on PASS) Gate 1; mint the authorization iff both pass."""
    errors: List[str] = []
    provider_config = _provider_config_report()

    # --- Precheck: docker + image + fixture hash (task §7). ---
    if not docker.available():
        return PreflightResultDA(
            OutcomeDA.FAILED_DOCKER_UNAVAILABLE,
            DecisionDA.REAL_LIGHTRAG_PREFLIGHT_FAILED,
            boot_gate="FAIL",
            provider_config=provider_config,
            errors=("docker unavailable",),
        )
    if not docker.image_present(REAL_LIGHTRAG_IMAGE):
        return PreflightResultDA(
            OutcomeDA.FAILED_IMAGE_MISSING,
            DecisionDA.REAL_LIGHTRAG_PREFLIGHT_FAILED,
            boot_gate="FAIL",
            provider_config=provider_config,
            errors=(f"image missing: {REAL_LIGHTRAG_IMAGE}",),
        )
    image_label = docker.image_version_label(REAL_LIGHTRAG_IMAGE)
    if mf.canonical_version(image_label) != mf.canonical_version(EXPECTED_IMAGE_VERSION_LABEL):
        return PreflightResultDA(
            OutcomeDA.FAILED_IMAGE_VERSION,
            DecisionDA.REAL_LIGHTRAG_PREFLIGHT_FAILED,
            boot_gate="FAIL",
            provider_config=provider_config,
            errors=(f"image label {image_label!r} != {EXPECTED_IMAGE_VERSION_LABEL}",),
        )

    fdir = Path(fixture_dir) if fixture_dir else None
    try:
        fx = ds.load_fixture(fdir)
        ds.validate_fixture(fx)
    except Exception as exc:
        return PreflightResultDA(
            OutcomeDA.FAILED_FIXTURE_HASH,
            DecisionDA.REAL_LIGHTRAG_PREFLIGHT_FAILED,
            boot_gate="FAIL",
            provider_config=provider_config,
            errors=(f"fixture load/validate: {exc}",),
        )
    ok, detail = ds.verify_fixture_hash(fdir)
    if not ok or detail != EXPECTED_FIXTURE_HASH:
        return PreflightResultDA(
            OutcomeDA.FAILED_FIXTURE_HASH,
            DecisionDA.REAL_LIGHTRAG_PREFLIGHT_FAILED,
            boot_gate="FAIL",
            provider_config=provider_config,
            errors=(f"fixture hash: {detail}",),
        )

    # --- Gate 0: single real sidecar. ---
    gate0 = run_gate0(
        docker,
        run_id=f"{run_id}-g0",
        base_storage_dir=base_storage_dir,
        timeout_seconds=timeout_seconds,
        poll_seconds=poll_seconds,
        image_label_version=image_label,
    )
    errors.extend(gate0.errors)
    if not gate0.passed:
        return PreflightResultDA(
            outcome=gate0.outcome,
            decision=DecisionDA.REAL_LIGHTRAG_PREFLIGHT_FAILED,
            boot_gate="FAIL",
            gate0=gate0.report,
            provider_config=provider_config,
            errors=tuple(errors),
        )

    # --- Gate 1: three real sidecars (only after Gate 0 PASS). ---
    gate1 = run_gate1(
        docker,
        fx,
        run_id=f"{run_id}-g1",
        base_storage_dir=base_storage_dir,
        timeout_seconds=timeout_seconds,
        poll_seconds=poll_seconds,
        image_label_version=image_label,
    )
    errors.extend(gate1.errors)

    auth = mint_real_preflight_authorization(
        gate0_passed=gate0.passed,
        gate1_passed=gate1.passed,
        fixture_hash=EXPECTED_FIXTURE_HASH,
        run_id=run_id,
        runtime_count=gate1.runtime_count,
    )
    if gate0.passed and gate1.passed and auth is not None:
        outcome = OutcomeDA.COMPLETED
        decision = DecisionDA.REAL_LIGHTRAG_PREFLIGHT_PASSED_AND_PROVIDER_RUN_AUTHORIZATION_GATE_JUSTIFIED
    else:
        outcome = gate1.outcome if not gate1.passed else OutcomeDA.FAILED_PRECHECK
        decision = DecisionDA.REAL_LIGHTRAG_PREFLIGHT_FAILED

    return PreflightResultDA(
        outcome=outcome,
        decision=decision,
        boot_gate="PASS" if gate0.passed else "FAIL",
        gate0=gate0.report,
        gate1=gate1.report,
        provider_config=provider_config,
        authorization_minted=auth is not None,
        errors=tuple(errors),
    )


# --------------------------------------------------------------------------- #
# CLI (task §32)
# --------------------------------------------------------------------------- #

def _git_commit() -> str:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=True
        )
        return out.stdout.strip()
    except Exception:  # pragma: no cover
        return "unknown"


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="preflightpn02d",
        description="GraphRAG-PN02D-A REAL LightRAG data-plane preflight (Gate 0 + Gate 1).",
    )
    parser.add_argument(
        "--live",
        action="store_true",
        help="REQUIRED to actually boot real Docker sidecars (authorized gate only).",
    )
    parser.add_argument("--run-id", default=None)
    parser.add_argument("--out", default=None, help="write content-safe JSON report here")
    parser.add_argument("--base-storage-dir", default=None)
    parser.add_argument("--timeout", type=float, default=HEALTH_TIMEOUT_SECONDS)
    args = parser.parse_args(argv)

    if not args.live:
        print(
            "PN02D-A boots REAL LightRAG Docker sidecars. Re-run with --live to "
            "execute the authorized provider-free gate.",
            file=sys.stderr,
        )
        return 2

    run_id = args.run_id or f"pn02da-{int(time.time())}"
    result = run_preflight_da(
        DockerCLI(),
        run_id=run_id,
        base_storage_dir=args.base_storage_dir,
        timeout_seconds=args.timeout,
    )
    payload = json.dumps(result.to_dict(), indent=2, sort_keys=True)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(payload)
    else:
        print(payload)
    return 0 if result.outcome is OutcomeDA.COMPLETED else 1


__all__ = [
    "EXPECTED_FIXTURE_HASH",
    "GATE0_NOTEBOOK_ID",
    "GATE0_RECORD_ID",
    "OutcomeDA",
    "DecisionDA",
    "DECISION_NAMES",
    "ZeroDataCounters",
    "RuntimeBootResult",
    "provision_runtime",
    "GateResult",
    "run_gate0",
    "run_gate1",
    "PreflightResultDA",
    "run_preflight_da",
    "main",
]


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
