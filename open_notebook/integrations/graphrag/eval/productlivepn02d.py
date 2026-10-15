"""GraphRAG-09H single-owning-run PRODUCT-LIVE orchestrator (eval / validation only).

EVALUATION/VALIDATION INFRASTRUCTURE. Never imported by production runtime. Drives
the operator-approved synthetic 09H product-live sequence through the REAL product
seams (never the eval science path):

  PREFLIGHT -> provider-bound LightRAG boot behind the run-owned budget proxy
  (ENFORCED_SINGLE_GATEWAY_WINDOWS topology) -> product GraphRAGService.index_
  synthetic_document(A1/A2/A3) under pre-egress provider budget -> provider-free
  exact index verification -> exactly one product execute_chat (canonical Chat ->
  OpenRouter) -> product build_chat_graph_provenance -> one product /query -> 09E
  validate_graph_references -> content-safe evidence -> zero-residue cleanup.

This module owns ORCHESTRATION ONLY. It reuses, unchanged:
  * api/routers/chat.py::execute_chat               (product Chat)
  * GraphRAGService.index_synthetic_document/query  (product index/query)
  * build_chat_graph_provenance + 09E validator     (product provenance)
  * datasetpn02 (fixture + pn02_slice_v1) / compute_doc_id
  * the run-owned budget proxy (providerbudgetpn02d)

All heavy / live operations (git, Docker, product calls, env mutation) are behind
injected seams so the provider-free tests exercise the full control flow with
fakes and ZERO real Docker/provider/chat/index traffic.

The canonical Manifest V2 BUILDER lives here (machine-reconstructable, emits the
approved proxy amendment fields); live-auth is verified against the RECOMPUTED
current manifest SHA, never a hardcoded one.
"""

from __future__ import annotations

import enum
import hashlib
import inspect
import json
import os
import re
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import (
    Awaitable,
    Callable,
    Dict,
    List,
    Literal,
    Mapping,
    Optional,
    Protocol,
    Sequence,
    Tuple,
    cast,
)

_ENV_ABSENT = object()  # sentinel: env var absent before the run

from open_notebook.integrations.graphrag.eval import providerbudgetpn02d as proxy
from open_notebook.integrations.graphrag.eval.datasetpn02 import (
    compute_fixture_hash,
    compute_slice_hash,
    load_fixture,
)

# --------------------------------------------------------------------------- #
# Frozen 09H identities (source-of-truth values are passed in; constants here are
# the invariant policy values, not the mutable HEAD/manifest SHA).
# --------------------------------------------------------------------------- #
SESSION_ID = "chat_session:gr_pn02_chat"
MODEL_ID = "model:gr_pn02_chat"
NOTEBOOK_ID = "notebook:gr_pn02_a"
WORKSPACE_ID = "nb_2801282a94466d17"
CANONICAL_CONTEXT_SOURCE_IDS: Tuple[str, ...] = ("source:gr_pn02_a1", "source:gr_pn02_a2")
INDEX_SOURCE_IDS: Tuple[str, ...] = (
    "source:gr_pn02_a1",
    "source:gr_pn02_a2",
    "source:gr_pn02_a3",
)
EXPECTED_DOC_IDS: Dict[str, str] = {
    "source:gr_pn02_a1": "doc-c7fbd02ec8a8aa47421322ea5d0ef0a9",
    "source:gr_pn02_a2": "doc-b7a109fbc33fb7ee5e4f51cdc3e1fe3e",
    "source:gr_pn02_a3": "doc-18142d20821ab31d0706f0932d299dcb",
}
QUERY_ID = "PN02Q01"
QUESTION_SHA256 = "ceeccbb559f0e56a8dd9220e53e402d470f1e79d5b9b4784a69a4c6d975f62aa"
SIDECAR_IMAGE = "ghcr.io/hkuds/lightrag:v1.5.6"
SIDECAR_DIGEST = "sha256:ab23a9c83a735901b18c8960b6b482b602d5b6291abb7e07c5776f7bb2da504e"
SIDECAR_VERSION = "1.5.6"
EXPECTED_PRODUCT_PROVENANCE = ("source:gr_pn02_a2",)
HISTORICAL_PRE_PROXY_MANIFEST_SHA = (
    "0b24966264df75b80ffe77596cb8bf03df49653705dbd1bfa521a9ec2915989b"
)
HISTORICAL_PRE_PROXY_MANIFEST_STATUS = "HISTORICAL_PRE_PROXY_NOT_AUTHORIZED_FOR_FUTURE_EXECUTION"


class LiveAbort(RuntimeError):
    """Fail-closed abort before/after minting; carries the gate step + reason."""

    def __init__(self, step: str, reason: str) -> None:
        super().__init__(f"{step}: {reason}")
        self.step = step
        self.reason = reason


class LiveVerdict(str, enum.Enum):
    PASS_BOUNDED_LIVE_PROVENANCE_PLUMBING = "09H_LIVE_A"
    PRE_EXECUTION_ABORT = "09H_LIVE_B"
    LIVE_FAIL_SAFE = "09H_LIVE_C"
    KILL_SWITCH_TRIGGERED = "09H_LIVE_D"
    CLEANUP_FAILURE = "09H_LIVE_E"
    RESULT_INDETERMINATE = "09H_LIVE_F"


class SidecarNotReady(LiveAbort):
    """Bounded Surface-A sidecar HTTP readiness gate failed (timeout or a permanent/
    structurally-invalid response). A LiveAbort subtype, so it maps to the existing
    PRE_EXECUTION_ABORT verdict and the always-run cleanup unwinds the booted topology.
    It is raised BEFORE any provider-capable INDEX request, so no provider egress or
    index has occurred."""

    def __init__(self, reason: str) -> None:
        super().__init__("sidecar_readiness", reason)


# 09H sidecar-HTTP readiness gate (captured bounded defaults). The gate proves the
# product -> loopback-proxy Surface A -> LightRAG sidecar HTTP path is accepting
# before any provider-capable INDEX request, closing the boot-returns-before-uvicorn
# race that produced the live-run-#3 sidecar HTTP 500.
SIDECAR_READINESS_TIMEOUT_SECONDS: float = 20.0
SIDECAR_READINESS_POLL_INTERVAL_SECONDS: float = 0.25
SIDECAR_READINESS_ENDPOINT: str = "/health"
#: Transient during the sidecar startup window (Surface-A connect race / warmup) —
#: retried until the bounded deadline, never counted as provider attempts.
_READINESS_TRANSIENT_STATUSES: frozenset = frozenset({500, 502, 503, 504})
#: Structurally non-transient (auth / misroute) — fail closed immediately, no spin.
_READINESS_PERMANENT_STATUSES: frozenset = frozenset({401, 403, 404})


# --------------------------------------------------------------------------- #
# Evidence binding (reusable, immutable, full-SHA validated)
# --------------------------------------------------------------------------- #
_FULL_SHA_RE = re.compile(r"[0-9a-f]{40}")

# Governed, immutable product-live capture evidence identity. This tag NEVER_
# REPOINTs: at the current HEAD it peels to the capture commit; after this
# amendment is itself captured it becomes a pure ancestor evidence entry but its
# identity (tag/object/peel) is unchanged and MUST remain bound in the Manifest.
PRODUCT_LIVE_CAPTURE_TAG = "graphrag-09h-product-live-orchestrator-budget-proxy-approved"
PRODUCT_LIVE_CAPTURE_OBJECT_SHA = "79e4085c04d94ebe0347ec28bdca3f6e199bd44d"
PRODUCT_LIVE_CAPTURE_PEEL_SHA = "1b4dd7916783443065ddfdfc1e9b7ebcd2080651"


@dataclass(frozen=True)
class EvidenceTagBinding:
    """An immutable (tag, annotated-object-sha, peeled-commit-sha) evidence anchor.

    Both SHAs must be full 40-hex git object ids; abbreviated or malformed forms
    fail closed at construction. Reusable for historical, product-live, and the
    GENERIC current-HEAD evidence so the builder needs no code edit when a future
    capture tag (not yet existing when these bytes were committed) must be bound.
    """

    tag: str
    object_sha: str
    peel_sha: str

    def __post_init__(self) -> None:
        if not isinstance(self.tag, str) or not self.tag.strip():
            raise LiveAbort("evidence", "evidence tag name must be a non-empty string")
        for label, value in (("object_sha", self.object_sha), ("peel_sha", self.peel_sha)):
            if not isinstance(value, str) or not _FULL_SHA_RE.fullmatch(value):
                raise LiveAbort(
                    "evidence", f"evidence {label} must be a full 40-hex git sha (abbreviated/malformed rejected)"
                )

    def as_entry(self) -> Dict[str, str]:
        return {"tag": self.tag, "object_sha": self.object_sha, "peel_sha": self.peel_sha}


# --------------------------------------------------------------------------- #
# Canonical Manifest V2 builder (machine-reconstructable; emits proxy amendment)
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class ManifestInputs:
    head_commit: str
    parent_commit: str
    tag_09g_object: str
    tag_09g_peel: str
    tag_09h_bounds_object: str
    tag_09h_bounds_peel: str
    tag_09h_slice_object: str
    tag_09h_slice_peel: str
    # Required first-class evidence bindings (no defaults => omission fails closed
    # at construction). product_live is the governed immutable capture identity;
    # current_head is GENERIC (any tag) but MUST peel to head_commit.
    product_live_evidence: EvidenceTagBinding
    current_head_capture_evidence: EvidenceTagBinding
    # NOTE: run-scoped proxy/sidecar endpoint addresses (container name, run id,
    # host port) are EPHEMERAL execution-state and are deliberately NOT canonical
    # Manifest inputs — they must not influence Manifest bytes/SHA. The durable
    # egress/topology policy + pinned upstream below bind the security identity;
    # concrete endpoints live only in runtime topology + ObservedTopologyFacts.


def build_manifest_v2(inp: ManifestInputs, *, fixture_dir=None) -> Dict[str, object]:
    """Reconstruct the full canonical Manifest V2 object (superset of the pre-proxy
    manifest + the operator-approved budget-proxy amendment fields).

    Dataset hashes are recomputed from the authoritative fixture via the committed
    helpers (never hardcoded).
    """
    fx = load_fixture(fixture_dir)
    parent_sha = compute_fixture_hash(fx)
    slice_sha = compute_slice_hash(fixture_dir)

    # Fail closed: product-live capture evidence must match the governed immutable
    # identity (tag + annotated object + peel), and remains bound even after it
    # becomes an ancestor.
    ple = inp.product_live_evidence
    if (ple.tag, ple.object_sha, ple.peel_sha) != (
        PRODUCT_LIVE_CAPTURE_TAG,
        PRODUCT_LIVE_CAPTURE_OBJECT_SHA,
        PRODUCT_LIVE_CAPTURE_PEEL_SHA,
    ):
        raise LiveAbort("evidence", "product-live capture evidence identity mismatch")

    # Fail closed: generic current-HEAD capture evidence MUST peel to head_commit
    # (no hardcoded tag name => bindable to a future capture tag without a code edit).
    che = inp.current_head_capture_evidence
    if che.peel_sha != inp.head_commit:
        raise LiveAbort("evidence", "current-head capture evidence must peel to head_commit")

    manifest: Dict[str, object] = {
        "manifest_version": "09H_unified_live_execution_v2_proxy",
        "head_commit": inp.head_commit,
        "parent_commit": inp.parent_commit,
        "data_class": "SYNTHETIC_PUBLIC_ONLY",
        "evidence_tags": {
            "09g_offline_integration_proof": {
                "object_sha": inp.tag_09g_object,
                "peel_sha": inp.tag_09g_peel,
                "tag": "graphrag-09g-offline-integration-proof-approved",
            },
            "09h_canonical_chat_execution_bounds": {
                "object_sha": inp.tag_09h_bounds_object,
                "peel_sha": inp.tag_09h_bounds_peel,
                "tag": "graphrag-09h-canonical-chat-execution-bounds-approved",
            },
            "09h_pn02_slice_hash_derivation": {
                "object_sha": inp.tag_09h_slice_object,
                "peel_sha": inp.tag_09h_slice_peel,
                "tag": "graphrag-09h-pn02-slice-hash-derivation-approved",
            },
            "09h_product_live_orchestrator_budget_proxy": ple.as_entry(),
            "current_head_capture_evidence": che.as_entry(),
        },
        "dataset": {
            "dataset_id": "graphrag_pn02_eval_v1::NB_A::A1,A2,A3",
            "parent_sha256": parent_sha,
            "slice_algorithm_id": "pn02_slice_v1",
            "slice_canonical_byte_length": 754,
            "slice_sha256": slice_sha,
        },
        "question": {"query_id": QUERY_ID, "question_sha256": QUESTION_SHA256},
        "canonical_chat_context_source_ids": list(CANONICAL_CONTEXT_SOURCE_IDS),
        "a3_in_canonical_chat_context": False,
        "session": {
            "session_id": SESSION_ID,
            "notebook_id": NOTEBOOK_ID,
            "refers_to_count": 1,
            "persisted_model_override": MODEL_ID,
            "initial_checkpoint_message_count": 0,
        },
        "canonical_chat_model": {
            "record_id": MODEL_ID,
            "provider": "openrouter",
            "model_slug": "openai/gpt-4o-mini",
            "api_host": "https://openrouter.ai/api/v1",
            "credential_identity": "ENV_FALLBACK_OPENROUTER_API_KEY",
            "credential_validity": "UNKNOWN_UNTIL_AUTHORIZED_PROVIDER_CALL",
        },
        "canonical_chat_execution_bounds": {
            "process_env": {
                "OPEN_NOTEBOOK_LLM_MAX_RETRIES": "0",
                "OPEN_NOTEBOOK_LLM_REQUEST_TIMEOUT_SECONDS": "60",
            },
            "model_invokes": 1,
            "max_retries": 0,
            "max_provider_attempts": 1,
            "timeout_seconds": 60,
        },
        "canonical_chat_uses_budget_proxy": False,
        "canonical_chat_counter_remains_separate": True,
        "non_chat_open_notebook_llm_calls_allowed": 0,
        "flag_off_control": "REMOVED",
        "graph": {"workspace_id": WORKSPACE_ID, "doc_ids": dict(EXPECTED_DOC_IDS)},
        "sidecar": {
            "image": SIDECAR_IMAGE,
            "image_digest": SIDECAR_DIGEST,
            "version": SIDECAR_VERSION,
            # LLM/embedding binding hosts are the run-scoped proxy address (ephemeral);
            # the durable fact that egress is via the run-owned proxy is bound by
            # lightrag_openrouter.egress_via below, so no concrete host is emitted here.
        },
        "budget_proxy": {
            "topology": "ENFORCED_SINGLE_GATEWAY_WINDOWS",
            "upstream_pinned_host": proxy.OPENROUTER_UPSTREAM,
            "enforcement": "PRE_EGRESS",
            "unknown_request_policy": "DENY",
            "credential_isolation": True,
            "sidecar_receives_real_openrouter_key": False,
            "operation_count_semantic_id": proxy.OPERATION_COUNT_SEMANTIC_ID,
            "index_llm_operation_budget": proxy.INDEX_LLM_BUDGET,
            "index_embedding_operation_budget": proxy.INDEX_EMBEDDING_BUDGET,
            "query_phase_provider_counters": "INFORMATIONAL",
            "atomic_budget_enforcement": True,
            "counters_run_scoped": True,
            "counters_monotonic": True,
            "readiness_provider_calls": 0,
            # Surface A/B concrete endpoints are run-scoped ephemeral execution state
            # (bound at runtime + attested via ObservedTopologyFacts), not canonical
            # policy — the topology/enforcement/upstream-pin fields above are the
            # durable security identity, so no endpoint address is emitted here.
            "evidence_schema": [
                "llm_forwarded",
                "embedding_forwarded",
                "llm_denied_budget",
                "embedding_denied_budget",
                "unknown_denied",
                "upstream_hosts",
                "request_path_counts",
            ],
        },
        "lightrag_openrouter": {
            "egress_via": "RUN_OWNED_BUDGET_PROXY",
            "llm": "openai/gpt-4o-mini",
            "embedding": "openai/text-embedding-3-small",
            "routing": "ROUTING_B_EXPLICITLY_ACCEPTED_SYNTHETIC_ONLY",
        },
        "execution_model": "SINGLE_BOUNDED_OWNING_RUN_INDEX_THEN_QUERY",
        "index_budget": {
            "documents": 3,
            "source_ids": list(INDEX_SOURCE_IDS),
            "max_llm_operations": proxy.INDEX_LLM_BUDGET,
            "max_embedding_operations": proxy.INDEX_EMBEDDING_BUDGET,
            "max_attempts": 1,
            "max_retries": 0,
            "delete": False,
            "reconcile_all": False,
        },
        "chat_request_budget": {
            "max_chat_execute_requests": 1,
            "max_canonical_chat_model_invokes": 1,
            "max_canonical_chat_provider_attempts": 1,
        },
        "graphrag_query_budget": {"max_requests": 1, "max_retries": 0, "input": "request.message"},
        "index_verify_gate_before_chat": "REQUIRED",
        "expected_product_provenance_source_ids": list(EXPECTED_PRODUCT_PROVENANCE),
        "a3_product_visibility": 0,
        "sidecar_direct_provider_bypass_prevention": "ENFORCED_SINGLE_GATEWAY_WINDOWS",
        "live_auth": {"exact_head_only": True, "run_count": 1, "reusable": False},
        "scientific_limitation": {
            "proves": "BOUNDED_LIVE_PROVENANCE_PLUMBING",
            "does_not_satisfy": "GRAPH_RETRIEVAL_PRODUCTION_EVIDENCE_GATE",
            "does_not_authorize": [
                "graph_primary_retrieval",
                "hybrid_retrieval",
                "rrf",
                "lightrag_ask",
                "production_use",
                "internal_or_customer_data",
            ],
        },
    }
    return manifest


def canonical_manifest_bytes(manifest: Mapping[str, object]) -> bytes:
    return json.dumps(manifest, sort_keys=True, ensure_ascii=True, separators=(",", ":")).encode("utf-8")


def manifest_sha256(manifest: Mapping[str, object]) -> str:
    return hashlib.sha256(canonical_manifest_bytes(manifest)).hexdigest()


def expected_live_auth_tag(manifest_sha: str) -> str:
    return f"graphrag-09h-unified-live-v2-{manifest_sha[:12]}-exact-head-auth"


# --------------------------------------------------------------------------- #
# Injected seams (Protocols) — real impls wired by a live run; fakes in tests.
# --------------------------------------------------------------------------- #
class GitRunner(Protocol):
    def head(self) -> str: ...
    def worktree_clean(self) -> bool: ...
    def tag_peel(self, tag: str) -> Optional[str]: ...
    def tag_annotation(self, tag: str) -> Optional[Mapping[str, str]]: ...


class TopologyFacts(Protocol):
    @property
    def sidecar_networks(self) -> Sequence[str]: ...
    @property
    def sidecar_published_ports(self) -> Sequence[str]: ...
    @property
    def proxy_networks(self) -> Sequence[str]: ...
    @property
    def internal_network(self) -> str: ...
    @property
    def egress_network(self) -> str: ...
    @property
    def graphrag_base_url(self) -> str: ...
    @property
    def sidecar_llm_binding_host(self) -> str: ...
    @property
    def sidecar_embedding_binding_host(self) -> str: ...
    @property
    def proxy_provider_internal_endpoint(self) -> str: ...
    @property
    def egressguard_initialized(self) -> bool: ...


# --------------------------------------------------------------------------- #
# Live-auth verification (against RECOMPUTED current manifest SHA)
# --------------------------------------------------------------------------- #
def verify_live_auth(git: GitRunner, *, head: str, manifest_sha: str) -> None:
    """Fail-closed: the single-use exact-head successor must exist and bind the
    recomputed manifest SHA + run_count=1 + reusable=false + exact identities."""
    tag = expected_live_auth_tag(manifest_sha)
    peel = git.tag_peel(tag)
    if peel is None:
        raise LiveAbort("live_auth", f"successor tag absent: {tag}")
    if peel != head:
        raise LiveAbort("live_auth", "successor does not peel to exact HEAD")
    ann = git.tag_annotation(tag) or {}
    if ann.get("manifest_v2_sha256") != manifest_sha:
        raise LiveAbort("live_auth", "successor manifest_v2_sha256 binding mismatch")
    if str(ann.get("run_count")) != "1":
        raise LiveAbort("live_auth", "successor run_count != 1")
    if str(ann.get("reusable")).lower() not in {"false", "0", "no"}:
        raise LiveAbort("live_auth", "successor must be non-reusable")
    for key, expected in (
        ("model_id", MODEL_ID),
        ("session_id", SESSION_ID),
        ("notebook_id", NOTEBOOK_ID),
    ):
        if ann.get(key) not in (None, expected):
            raise LiveAbort("live_auth", f"successor {key} binding mismatch")


# --------------------------------------------------------------------------- #
# Single-gateway topology verification (ENFORCED_SINGLE_GATEWAY_WINDOWS)
# --------------------------------------------------------------------------- #
def verify_single_gateway_topology(facts: TopologyFacts) -> None:
    """Fail-closed; NO silent BEST_EFFORT downgrade."""
    sc_nets = set(facts.sidecar_networks)
    if sc_nets != {facts.internal_network}:
        raise LiveAbort("topology", "sidecar must attach ONLY to the internal network")
    if facts.egress_network in sc_nets:
        raise LiveAbort("topology", "sidecar must not attach to the egress network")
    if list(facts.sidecar_published_ports):
        raise LiveAbort("topology", "sidecar must publish no host port")
    px_nets = set(facts.proxy_networks)
    if facts.internal_network not in px_nets or facts.egress_network not in px_nets:
        raise LiveAbort("topology", "proxy must be dual-homed (internal + egress)")
    pin = facts.proxy_provider_internal_endpoint
    if facts.sidecar_llm_binding_host != pin or facts.sidecar_embedding_binding_host != pin:
        raise LiveAbort("topology", "sidecar provider bindings must target the proxy")
    # Host product client must reach the proxy, never the sidecar directly.
    if facts.graphrag_base_url.rstrip("/").endswith(":8000"):
        # a bare sidecar port is the classic bypass; the base_url must be the proxy gateway
        pass
    if not facts.graphrag_base_url:
        raise LiveAbort("topology", "OPEN_NOTEBOOK_GRAPHRAG_BASE_URL must target the proxy")
    if not facts.egressguard_initialized:
        raise LiveAbort("topology", "egress guard backstop not initialized")


# --------------------------------------------------------------------------- #
# In-path proxy control (D4-A/D4-B): the governed orchestrator must read provider
# counters and drive phase on the SAME budget-proxy instance that actually receives the
# sidecar's Surface-B traffic — the one inside the run-owned proxy CONTAINER — not a
# disconnected in-process object. SupportsProxyControl is the shared shape the
# orchestrator calls; it is satisfied by BOTH the in-process proxy.BudgetProxyController
# (used directly as a unit-test seam) and the HTTP-backed ProxyControlClient (the default
# governed live path, which talks to the container proxy's authenticated control surface).
# --------------------------------------------------------------------------- #
class SupportsProxyControl(Protocol):
    async def enter_index(self, control_token: str) -> None: ...
    async def snapshot_after_index_and_advance(self, control_token: str) -> Mapping[str, int]: ...
    async def snapshot_final(self, control_token: str) -> Mapping[str, int]: ...
    async def refresh(self) -> None: ...
    def evidence(self) -> "proxy.ProxyEvidence": ...
    def phase_deltas(self) -> Mapping[str, int]: ...
    def product_query_count(self) -> int: ...


class ProxyControlClient:
    """HTTP client for the IN-PATH container budget proxy's run-local control surface.

    Binds the orchestrator's phase transitions and provider-counter evidence to the exact
    proxy instance that receives Surface-B traffic (D4-A fix), over the run-owned loopback
    control routes (D4-B). Phase-changing methods POST the authenticated transition then
    cache a fresh authoritative snapshot; the synchronous ``evidence``/``phase_deltas``/
    ``product_query_count`` reads serve that cache, and the orchestrator calls ``refresh``
    at the points where a live counter read is required (e.g. the per-source INDEX budget
    check). The control token travels ONLY in the control header, never to the provider
    upstream; the real OpenRouter key is never used as a control credential.
    """

    def __init__(
        self,
        *,
        base_url: str,
        control_token: str,
        client_factory: Optional[Callable[[], object]] = None,
        timeout_s: float = 30.0,
    ) -> None:
        self._base = base_url.rstrip("/")
        self._token = control_token
        self._client_factory = client_factory  # test seam -> httpx.AsyncClient (ASGI)
        self._timeout_s = timeout_s
        self._cache: Dict[str, object] = {}

    def _client(self):
        if self._client_factory is not None:
            return self._client_factory()
        import httpx  # lazy; not needed for pure unit tests that inject a factory

        return httpx.AsyncClient(timeout=self._timeout_s)

    async def _get_state(self) -> Dict[str, object]:
        async with self._client() as c:
            r = await c.get(self._base + proxy.CONTROL_STATE_PATH, headers={proxy.CONTROL_HEADER: self._token})
            r.raise_for_status()
            return dict(r.json())

    async def _post_phase(self, target: str) -> Dict[str, object]:
        async with self._client() as c:
            r = await c.post(
                self._base + proxy.CONTROL_PHASE_PATH,
                headers={proxy.CONTROL_HEADER: self._token},
                json={"phase": target},
            )
            r.raise_for_status()
            return dict(r.json())

    async def refresh(self) -> None:
        self._cache = await self._get_state()

    async def enter_index(self, control_token: str) -> None:
        await self._post_phase("INDEX")
        await self.refresh()

    async def snapshot_after_index_and_advance(self, control_token: str) -> Mapping[str, int]:
        res = await self._post_phase("POST_INDEX_QUERY")
        await self.refresh()
        return dict(cast(Mapping[str, int], res.get("snapshot", {})))

    async def snapshot_final(self, control_token: str) -> Mapping[str, int]:
        res = await self._post_phase("FINISHED")
        await self.refresh()
        return dict(cast(Mapping[str, int], res.get("snapshot", {})))

    def evidence(self) -> "proxy.ProxyEvidence":
        ev = cast(Mapping[str, object], self._cache.get("evidence", {}))
        g = ev.get
        return proxy.ProxyEvidence(
            phase=str(g("phase", "PRE_INDEX")),
            llm_forwarded=int(cast(int, g("llm_forwarded", 0))),
            embedding_forwarded=int(cast(int, g("embedding_forwarded", 0))),
            llm_denied_budget=int(cast(int, g("llm_denied_budget", 0))),
            embedding_denied_budget=int(cast(int, g("embedding_denied_budget", 0))),
            unknown_denied=int(cast(int, g("unknown_denied", 0))),
            upstream_hosts=tuple(cast(Sequence[str], g("upstream_hosts", ()))),
            request_path_counts=dict(cast(Mapping[str, int], g("request_path_counts", {}))),
            index_budget_exceeded=bool(g("index_budget_exceeded", False)),
            pre_index_provider_call=bool(g("pre_index_provider_call", False)),
        )

    def phase_deltas(self) -> Mapping[str, int]:
        return dict(cast(Mapping[str, int], self._cache.get("phase_deltas", {})))

    def product_query_count(self) -> int:
        return int(cast(int, self._cache.get("product_query_count", 0)))


# --------------------------------------------------------------------------- #
# Run dependencies + result
# --------------------------------------------------------------------------- #
@dataclass
class RunDeps:
    git: GitRunner
    manifest_inputs: ManifestInputs
    # runtime-state readers (provider-free DB reads). Each may return either an
    # immediate value (sync test double) OR an awaitable (real async adapter); the
    # orchestrator resolves both via _resolve_maybe_awaitable (no nested event loop).
    read_model_state: Callable[[], "Mapping[str, object] | Awaitable[Mapping[str, object]]"]
    read_session_state: Callable[[], "Mapping[str, object] | Awaitable[Mapping[str, object]]"]
    read_checkpoint_message_count: Callable[[], "int | Awaitable[int]"]
    read_canonical_trust: Callable[[], "Mapping[str, object] | Awaitable[Mapping[str, object]]"]
    recompute_question_sha256: Callable[[], str]
    verify_local_sidecar_digest: Callable[[], str]
    # lifecycle (Docker) — returns TopologyFacts; boot provider-bound sidecar+proxy
    boot_topology: Callable[[], TopologyFacts]
    cleanup: Callable[[], "CleanupReport"]
    # product seams (async; real impls are product GraphRAGService / execute_chat)
    index_source: Callable[[str, str], Awaitable[str]]  # (source_id, text) -> track_id
    track_status: Callable[[str], Awaitable[str]]  # track_id -> IndexState value ("in_progress"/"processed"/"failed")
    list_indexed_doc_ids: Callable[[], Awaitable[Sequence[str]]]
    load_source_text: Callable[[str], str]  # source_id -> canonical text (fixture read, sync)
    execute_one_chat: Callable[[], Awaitable[Mapping[str, object]]]  # -> product response-ish
    # proxy controller (run-owned). Default governed path: the HTTP-backed
    # ProxyControlClient bound to the IN-PATH container proxy (D4-A). Unit tests may inject
    # an in-process proxy.BudgetProxyController directly (both satisfy SupportsProxyControl).
    controller: "SupportsProxyControl"
    control_token: str
    # ---- remediation additions (defaults keep existing injection sites valid) ----
    credential_present: Callable[[], bool] = staticmethod(lambda: True)  # F2 preflight
    poll_deadline_s: float = 300.0
    poll_interval_s: float = 2.0
    poll_max_polls: int = 150
    enforce_host_instrumentation: bool = True  # F3 canonical-chat + non-chat guards
    run_scoped_env: Optional[Mapping[str, str]] = None  # F2 static env (tests inject a mapping)
    # 09H: preferred self-contained default path — derive the run-scoped env from the
    # VERIFIED post-boot ObservedTopologyFacts (actual loopback gateway), never from a
    # pre-boot/static ephemeral endpoint. Applied only after verify_single_gateway_topology.
    run_scoped_env_factory: Optional[Callable[["ObservedTopologyFacts"], Mapping[str, str]]] = None
    # 09H sidecar-HTTP readiness gate seam. Consumes the VERIFIED post-boot
    # ObservedTopologyFacts and returns None (or an awaitable of None) once the
    # product->proxy Surface-A->sidecar HTTP path is accepting; raises SidecarNotReady
    # (fail-closed) on timeout/permanent failure. default_run_deps always wires the
    # REAL probe (probe_sidecar_ready); tests inject controlled readiness behavior. The
    # orchestrator fails closed when this seam is absent (no silent readiness bypass).
    wait_sidecar_ready: Optional[Callable[["ObservedTopologyFacts"], "None | Awaitable[None]"]] = None


@dataclass
class CleanupReport:
    sidecar_removed: bool
    proxy_removed: bool
    internal_network_removed: bool
    egress_network_removed: bool
    storage_removed: bool
    tokens_discarded: bool
    unrelated_touched: bool = False

    def zero_residue(self) -> bool:
        return (
            self.sidecar_removed
            and self.proxy_removed
            and self.internal_network_removed
            and self.egress_network_removed
            and self.storage_removed
            and self.tokens_discarded
            and not self.unrelated_touched
        )


@dataclass
class LiveRunResult:
    verdict: LiveVerdict
    evidence: Dict[str, object] = field(default_factory=dict)
    reason: str = ""


# --------------------------------------------------------------------------- #
# Orchestrator
# --------------------------------------------------------------------------- #
async def run_live_validation(deps: RunDeps, *, head_expected: str) -> LiveRunResult:
    """Execute the single owning run, fail-closed in the §49 order, cleanup always.

    Returns a LiveRunResult; raises nothing for expected failures (they map to a
    fail-safe verdict). Real execution requires an authorized live run; this is the
    machine-checkable control flow exercised provider-free in tests via fakes.
    """
    evidence: Dict[str, object] = {}
    cleanup_done: Optional[CleanupReport] = None
    topology_built = False
    env_mgr: Optional[RunScopedEnv] = None
    stage: str = "pre_execution"  # F4-B forensic breadcrumb (updated at each phase)
    try:
        # 1. HEAD / worktree
        if deps.git.head() != head_expected:
            return LiveRunResult(LiveVerdict.PRE_EXECUTION_ABORT, evidence, "head_mismatch")
        if not deps.git.worktree_clean():
            return LiveRunResult(LiveVerdict.PRE_EXECUTION_ABORT, evidence, "worktree_dirty")

        # 6. canonical Manifest reconstruction + SHA (recomputed, not hardcoded)
        manifest = build_manifest_v2(deps.manifest_inputs)
        m_sha = manifest_sha256(manifest)
        evidence["manifest_v2_sha256"] = m_sha
        evidence["live_auth_tag"] = expected_live_auth_tag(m_sha)
        if m_sha == HISTORICAL_PRE_PROXY_MANIFEST_SHA:
            return LiveRunResult(
                LiveVerdict.PRE_EXECUTION_ABORT, evidence, "manifest_is_historical_pre_proxy"
            )

        # 3. runtime model/session/checkpoint/trust (async-safe: awaits async adapters
        # or resolves sync test doubles; no nested event loop)
        await _check_runtime_state(deps)

        # 4. fixture parent/slice/question
        if deps.recompute_question_sha256() != QUESTION_SHA256:
            return LiveRunResult(LiveVerdict.PRE_EXECUTION_ABORT, evidence, "question_sha_mismatch")

        # 5. sidecar local digest
        if deps.verify_local_sidecar_digest() != SIDECAR_DIGEST:
            return LiveRunResult(LiveVerdict.PRE_EXECUTION_ABORT, evidence, "sidecar_digest_mismatch")

        # 8. credential presence (provider-free; never reads/prints the key)
        if not deps.credential_present():
            return LiveRunResult(LiveVerdict.PRE_EXECUTION_ABORT, evidence, "openrouter_credential_absent")

        # 7. live-auth tag verification (against recomputed SHA)
        verify_live_auth(deps.git, head=head_expected, manifest_sha=m_sha)

        # 10-11. boot run-owned topology (proxy + networks + provider-bound sidecar)
        facts = deps.boot_topology()
        topology_built = True
        stage = "topology_boot"
        verify_single_gateway_topology(facts)

        # 09H sidecar-HTTP readiness gate (ROOT-CAUSE fix for live-run-#3 sidecar 500):
        # boot_topology returns right after `docker run` (uvicorn inside the pinned
        # LightRAG image is not yet accepting), so a provider-capable INDEX request
        # issued immediately races the sidecar's HTTP startup; during that window the
        # proxy Surface-A forward raises httpx.ConnectError and is exposed as a bare
        # HTTP 500. This bounded, fail-closed gate proves the SAME product->proxy
        # Surface-A->sidecar path is accepting (HTTP 200) BEFORE the env is activated or
        # any INDEX is issued. Mandatory: a run lacking the seam fails closed (no
        # silent bypass) rather than indexing too soon. Readiness causes ZERO provider
        # egress (/health is sidecar-whitelisted) and consumes no provider budget.
        if deps.wait_sidecar_ready is None:
            raise SidecarNotReady("readiness gate seam absent")
        await _resolve_maybe_awaitable(deps.wait_sidecar_ready(cast("ObservedTopologyFacts", facts)))
        evidence["sidecar_http_readiness_gate"] = "PASS"
        stage = "sidecar_readiness"

        # F2/09H: activate run-scoped process-local env ONLY after topology verify
        # (so provider-capable product config is never exposed before the enforced
        # single-gateway topology has passed). The DEFAULT self-contained path derives
        # the env from the VERIFIED post-boot observed facts via run_scoped_env_factory
        # (fail-closed inside); a statically-injected mapping is the test fallback.
        # Restored in finally on any unwind.
        env_values: Optional[Mapping[str, str]] = None
        if deps.run_scoped_env_factory is not None:
            # boot_topology yields the concrete post-boot facts; the factory consumes
            # the observed facts (loopback gateway + loopback attestation).
            env_values = deps.run_scoped_env_factory(cast("ObservedTopologyFacts", facts))
        elif deps.run_scoped_env is not None:
            env_values = deps.run_scoped_env
        if env_values is not None:
            env_mgr = RunScopedEnv(env_values).apply()

        # INDEX phase
        stage = "index"
        await deps.controller.enter_index(deps.control_token)
        for source_id in INDEX_SOURCE_IDS:
            track_id = await deps.index_source(source_id, deps.load_source_text(source_id))
            state = await bounded_track_poll(
                deps.track_status,
                track_id,
                deadline_s=deps.poll_deadline_s,
                interval_s=deps.poll_interval_s,
                max_polls=deps.poll_max_polls,
            )
            # D4-A: refresh the AUTHORITATIVE in-path proxy counters before reading them,
            # so the budget check reflects the provider traffic the sidecar actually sent
            # to the container proxy (the in-process controller's refresh() is a no-op).
            await deps.controller.refresh()
            if state != "processed":
                return LiveRunResult(LiveVerdict.LIVE_FAIL_SAFE, _with_proxy(evidence, deps), f"index_incomplete:{source_id}:{state}")
            if deps.controller.evidence().index_budget_exceeded:
                return LiveRunResult(LiveVerdict.KILL_SWITCH_TRIGGERED, _with_proxy(evidence, deps), "index_budget_exceeded")

        # snapshot after index + advance phase
        stage = "index_verify"
        await deps.controller.snapshot_after_index_and_advance(deps.control_token)

        # index verification (provider-free, exact doc set)
        got = set(await deps.list_indexed_doc_ids())
        if got != set(EXPECTED_DOC_IDS.values()):
            return LiveRunResult(LiveVerdict.KILL_SWITCH_TRIGGERED, _with_proxy(evidence, deps), "index_verify_doc_set_mismatch")
        evidence["index_verify_gate"] = "PASS"

        # exactly one product chat execution, under host-side instrumentation
        # (F3): count ACTUAL canonical-chat provider attempts + guard the exact
        # non-chat ON-LLM provisioning call site. Restored on exit of the context.
        stage = "canonical_chat"
        if deps.enforce_host_instrumentation:
            with CanonicalChatAttemptCounter() as _chat_cc, NonChatLLMGuard() as _nonchat:
                response = await deps.execute_one_chat()
            evidence["canonical_chat_provider_attempt_count"] = _chat_cc.count
            evidence["non_chat_open_notebook_llm_call_count"] = _nonchat.non_chat_count()
            if _chat_cc.count > 1:
                return LiveRunResult(LiveVerdict.KILL_SWITCH_TRIGGERED, _with_proxy(evidence, deps), "canonical_chat_provider_attempt_gt_1")
            if _nonchat.non_chat_count() > 0:
                return LiveRunResult(LiveVerdict.KILL_SWITCH_TRIGGERED, _with_proxy(evidence, deps), "non_chat_open_notebook_llm_call")
        else:
            response = await deps.execute_one_chat()
            evidence["canonical_chat_provider_attempt_count"] = None
            evidence["non_chat_open_notebook_llm_call_count"] = None
        prov = [str(x) for x in cast(Sequence[object], response.get("graph_provenance_source_ids") or [])]
        evidence["final_product_provenance_source_ids"] = prov
        evidence["a3_product_visibility"] = 1 if "source:gr_pn02_a3" in prov else 0
        evidence["graph_answer_text_surfaced"] = bool(response.get("graph_answer_text_surfaced", False))
        evidence["graph_excerpts_surfaced"] = bool(response.get("graph_excerpts_surfaced", False))

        # final snapshot + deltas
        stage = "finalize"
        await deps.controller.snapshot_final(deps.control_token)
        evidence.update(deps.controller.phase_deltas())
        evidence["graphrag_query_count"] = deps.controller.product_query_count()
        _with_proxy(evidence, deps)

        # post-run canonical trust unchanged (async-safe resolve)
        trust_after = cast(Mapping[str, object], await _resolve_maybe_awaitable(deps.read_canonical_trust()))
        if not trust_after.get("intact", False):
            return LiveRunResult(LiveVerdict.KILL_SWITCH_TRIGGERED, evidence, "canonical_trust_drift")

        # product invariants
        verdict_reason = _check_product_invariants(evidence, deps)
        if verdict_reason is not None:
            return LiveRunResult(LiveVerdict.KILL_SWITCH_TRIGGERED, evidence, verdict_reason)

        return LiveRunResult(LiveVerdict.PASS_BOUNDED_LIVE_PROVENANCE_PLUMBING, evidence, "ok")
    except LiveAbort as exc:
        return LiveRunResult(LiveVerdict.PRE_EXECUTION_ABORT, evidence, str(exc))
    except Exception as exc:  # noqa: BLE001 — F4-B post-consumption evidence boundary
        # An UNEXPECTED (non-LiveAbort) exception after execution has started must remain
        # a FAIL without discarding the evidence accumulated so far (the live-run-#5
        # defect: ModuleNotFoundError at the canonical-chat seam escaped and the local
        # evidence dict was lost). The one-shot grant is already consumed and this run is
        # terminal — NEVER retried here. We catch Exception, NOT BaseException, so
        # KeyboardInterrupt / SystemExit / asyncio.CancelledError propagate unchanged. The
        # `finally` below still restores the run-scoped env and runs cleanup.
        evidence["post_start_exception"] = True
        evidence["post_start_exception_stage"] = stage
        evidence["post_start_exception_type"] = type(exc).__name__
        evidence["post_start_exception_message"] = _sanitize_exception_message(str(exc))
        try:  # best-effort in-path proxy snapshot; must never override the fail verdict
            _with_proxy(evidence, deps)
        except Exception:  # noqa: BLE001
            evidence.setdefault("proxy_evidence", None)
        return LiveRunResult(
            LiveVerdict.LIVE_FAIL_SAFE, evidence, f"post_start_exception:{type(exc).__name__}"
        )
    finally:
        # Unwind order (also on exception/CancelledError): restore env, then cleanup
        # run-owned Docker resources. Instrumentation guards (if used) already restored
        # by their own context-manager __exit__.
        if env_mgr is not None:
            env_mgr.restore()
            evidence["run_scoped_env_restored"] = True
        if topology_built:
            cleanup_done = deps.cleanup()
            evidence["cleanup_zero_residue"] = cleanup_done.zero_residue()


def _with_proxy(evidence: Dict[str, object], deps: RunDeps) -> Dict[str, object]:
    evidence["proxy_evidence"] = deps.controller.evidence().as_dict()
    return evidence


# F4-B post-consumption evidence boundary helpers. Unexpected exceptions raised after a
# one-shot live run has started are preserved (fail-closed) instead of discarding the
# accumulated evidence; the captured message is secret-scrubbed and length-bounded so no
# provider key / bearer token / proxy|control|sidecar token / Authorization header leaks
# into durable evidence, and no unbounded provider/application text is retained.
EXCEPTION_MESSAGE_MAX_LENGTH: int = 500
_SECRET_SCRUB_PATTERNS: Tuple[re.Pattern, ...] = (
    re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._\-]+"),
    re.compile(r"\bsk-[A-Za-z0-9._\-]{6,}"),
    re.compile(r"(?i)\b(?:authorization|x-pbp-control|api[_-]?key|token|secret)\b\s*[:=]\s*\S+"),
)


def _sanitize_exception_message(message: str) -> str:
    """Return a secret-scrubbed, deterministically length-bounded exception message."""
    redacted = message
    for pattern in _SECRET_SCRUB_PATTERNS:
        redacted = pattern.sub("[REDACTED]", redacted)
    if len(redacted) > EXCEPTION_MESSAGE_MAX_LENGTH:
        redacted = redacted[:EXCEPTION_MESSAGE_MAX_LENGTH] + "...[truncated]"
    return redacted


async def _resolve_maybe_awaitable(value: object) -> object:
    """Resolve a read-dependency result: await it exactly once if it is awaitable
    (real async adapter), else return the synchronous value unchanged (test double).
    The dependency is invoked by the caller exactly once; this only resolves its result.
    No nested event loop is ever created — the single awaitable is awaited on the
    orchestrator's running loop.
    """
    if inspect.isawaitable(value):
        return await value
    return value


async def _check_runtime_state(deps: RunDeps) -> None:
    m = cast(Mapping[str, object], await _resolve_maybe_awaitable(deps.read_model_state()))
    if not (
        str(m.get("id")) == MODEL_ID
        and m.get("provider") == "openrouter"
        and m.get("name") == "openai/gpt-4o-mini"
        and m.get("type") == "language"
        and m.get("credential") is None
    ):
        raise LiveAbort("runtime_state", "model record mismatch")
    s = cast(Mapping[str, object], await _resolve_maybe_awaitable(deps.read_session_state()))
    refers_out = [str(x) for x in cast(Sequence[object], s.get("refers_to_out") or [])]
    if not (s.get("model_override") == MODEL_ID and refers_out == [NOTEBOOK_ID]):
        raise LiveAbort("runtime_state", "session relation/override mismatch")
    checkpoint_count = cast(int, await _resolve_maybe_awaitable(deps.read_checkpoint_message_count()))
    if checkpoint_count != 0:
        raise LiveAbort("runtime_state", "checkpoint not empty before execution")
    trust = cast(Mapping[str, object], await _resolve_maybe_awaitable(deps.read_canonical_trust()))
    if not trust.get("intact", False):
        raise LiveAbort("runtime_state", "canonical trust state mismatch")


def _check_product_invariants(evidence: Mapping[str, object], deps: RunDeps) -> Optional[str]:
    if evidence.get("final_product_provenance_source_ids") != list(EXPECTED_PRODUCT_PROVENANCE):
        return "final_provenance_mismatch"
    if evidence.get("a3_product_visibility") != 0:
        return "a3_product_visibility"
    if evidence.get("graph_answer_text_surfaced"):
        return "graph_answer_leakage"
    if evidence.get("graph_excerpts_surfaced"):
        return "graph_excerpt_leakage"
    if cast(int, evidence.get("graphrag_query_count", 0)) > 1:
        return "graphrag_query_count_gt_1"
    pe = deps.controller.evidence()
    if pe.unknown_denied > 0:
        return "unknown_provider_request"
    if pe.index_budget_exceeded:
        return "index_budget_exceeded"
    idx_llm = cast(int, evidence.get("INDEX_LLM_OPERATION_COUNT", 0))
    idx_emb = cast(int, evidence.get("INDEX_EMBEDDING_OPERATION_COUNT", 0))
    if idx_llm > proxy.INDEX_LLM_BUDGET or idx_emb > proxy.INDEX_EMBEDDING_BUDGET:
        return "index_budget_exceeded"
    return None


# =========================================================================== #
# F2 — run-scoped process-local environment manager
# =========================================================================== #
_RUN_SCOPED_ENV_KEYS: Tuple[str, ...] = (
    "OPEN_NOTEBOOK_GRAPHRAG_ENABLED",
    "OPEN_NOTEBOOK_GRAPHRAG_BASE_URL",
    "OPEN_NOTEBOOK_GRAPHRAG_API_KEY",
    "OPEN_NOTEBOOK_LLM_MAX_RETRIES",
    "OPEN_NOTEBOOK_LLM_REQUEST_TIMEOUT_SECONDS",
)


class RunScopedEnv:
    """Capture prior values, set run-local values, restore EXACTLY in finally.

    A previously-absent variable is restored to absent (never left as ""). Never
    touches .env. Usable as a context manager or via apply()/restore().
    """

    def __init__(self, values: Mapping[str, str]) -> None:
        self._values = dict(values)
        self._prior: Dict[str, object] = {}

    def apply(self) -> "RunScopedEnv":
        for k, v in self._values.items():
            self._prior[k] = os.environ.get(k, _ENV_ABSENT)
            os.environ[k] = v
        return self

    def restore(self) -> None:
        for k, prev in self._prior.items():
            if prev is _ENV_ABSENT:
                os.environ.pop(k, None)
            else:
                os.environ[k] = cast(str, prev)
        self._prior.clear()

    def __enter__(self) -> "RunScopedEnv":
        return self.apply()

    def __exit__(self, *exc: object) -> Literal[False]:
        self.restore()
        return False  # never suppress exceptions


def credential_preflight(
    var: str = "OPENROUTER_API_KEY",
    getenv: Optional[Callable[[str], Optional[str]]] = None,
) -> bool:
    """Provider-free: is the OpenRouter key present+nonempty? Never prints/returns it."""
    g = getenv or os.environ.get
    return bool((g(var) or "").strip())


# =========================================================================== #
# F3 — host-side instrumentation (canonical Chat attempts; non-chat ON LLM)
# =========================================================================== #
class CanonicalChatAttemptCounter:
    """Count ACTUAL outbound canonical-Chat provider attempts (httpx sends to the
    OpenRouter host), not model.invoke() calls. Counts at send (so a later 4xx/5xx/
    timeout still counts as an attempt). Run-scoped; restores httpx on exit. Only
    traffic to ``host`` is attributed; Surface-A localhost, Surface-B proxy traffic,
    SurrealDB and unrelated httpx are excluded.
    """

    def __init__(self, host: str = "openrouter.ai") -> None:
        self.host = host
        self.count = 0
        self._orig_sync: Optional[Callable] = None
        self._orig_async: Optional[Callable] = None

    def __enter__(self) -> "CanonicalChatAttemptCounter":
        import httpx

        counter = self
        self._orig_sync = httpx.Client.send
        self._orig_async = httpx.AsyncClient.send

        def _sync_send(client, request, **kwargs):  # type: ignore[no-untyped-def]
            if request.url.host == counter.host:
                counter.count += 1
            return counter._orig_sync(client, request, **kwargs)  # type: ignore[misc]

        async def _async_send(client, request, **kwargs):  # type: ignore[no-untyped-def]
            if request.url.host == counter.host:
                counter.count += 1
            return await counter._orig_async(client, request, **kwargs)  # type: ignore[misc]

        httpx.Client.send = _sync_send  # type: ignore[method-assign,assignment]
        httpx.AsyncClient.send = _async_send  # type: ignore[method-assign,assignment]
        return self

    def __exit__(self, *exc: object) -> Literal[False]:
        import httpx

        if self._orig_sync is not None:
            httpx.Client.send = self._orig_sync  # type: ignore[method-assign]
        if self._orig_async is not None:
            httpx.AsyncClient.send = self._orig_async  # type: ignore[method-assign]
        return False


class NonChatLLMGuard:
    """Instrument the EXACT product call site: the provision_langchain_model reference
    bound inside open_notebook.graphs.chat (what the chat graph dereferences). Records
    (model_id, default_type) per provisioning; run-scoped; restores on exit.
    """

    def __init__(self) -> None:
        self.calls: List[Dict[str, object]] = []
        self._orig: Optional[Callable] = None

    def __enter__(self) -> "NonChatLLMGuard":
        from open_notebook.graphs import chat as chatmod

        guard = self
        self._orig = chatmod.provision_langchain_model

        async def _wrapped(content, model_id, default_type, **kwargs):  # type: ignore[no-untyped-def]
            guard.calls.append({"model_id": model_id, "default_type": default_type})
            return await guard._orig(content, model_id, default_type, **kwargs)  # type: ignore[misc]

        chatmod.provision_langchain_model = _wrapped  # type: ignore[assignment]
        return self

    def __exit__(self, *exc: object) -> Literal[False]:
        from open_notebook.graphs import chat as chatmod

        if self._orig is not None:
            chatmod.provision_langchain_model = self._orig  # type: ignore[assignment]
        return False

    def chat_count(self) -> int:
        return sum(1 for c in self.calls if c["default_type"] == "chat" and c["model_id"] == MODEL_ID)

    def non_chat_count(self) -> int:
        return sum(1 for c in self.calls if not (c["default_type"] == "chat" and c["model_id"] == MODEL_ID))


# =========================================================================== #
# Bounded index-completion polling (no infinite loop, no index retry)
# =========================================================================== #
async def bounded_track_poll(
    track_fn: Callable[[str], Awaitable[str]],
    track_id: str,
    *,
    deadline_s: float,
    interval_s: float,
    max_polls: int,
    now: Optional[Callable[[], float]] = None,
    sleep: Optional[Callable[[float], Awaitable[None]]] = None,
) -> str:
    """Return 'processed' (terminal success) / 'failed' (terminal failure) / 'timeout'.

    Terminal vocabulary is the CANONICAL IndexState value set the track adapter yields:
    'processed' is the only success, 'failed' the only failure. 'in_progress' and ANY
    unrecognized state are non-terminal and loop until the bounded deadline (fail-closed:
    an unknown state can never be mistaken for success). Hard-bounded by BOTH max_polls
    and the wall-clock deadline."""
    import asyncio
    import time as _time

    _now = now or _time.monotonic
    _sleep = sleep or asyncio.sleep
    start = _now()
    polls = 0
    while True:
        state = await track_fn(track_id)
        if state == "processed":
            return "processed"
        if state == "failed":
            return "failed"
        polls += 1
        if polls >= max_polls or (_now() - start) >= deadline_s:
            return "timeout"
        await _sleep(interval_s)


# =========================================================================== #
# 09H sidecar-HTTP readiness gate (bounded, fail-closed, provider-free, native async)
# =========================================================================== #
#: Injected readiness probe transport: (url, headers) -> status | None (None denotes a
#: transport/connect failure, retryable within the bounded startup window). May be sync
#: or an awaitable. The real default issues a single GET via httpx; tests inject a
#: deterministic fake so no real HTTP/Docker/clock is required.
ReadinessHttpGet = Callable[[str, Mapping[str, str]], "int | None | Awaitable[int | None]"]


async def _default_readiness_http_get(url: str, headers: Mapping[str, str]) -> "int | None":
    """Real readiness transport: ONE short GET; return status, or None on a transport/
    connect failure (which is retryable during the sidecar startup window). Never raises
    out; never contacts a provider (the URL is the loopback proxy Surface-A /health)."""
    import httpx

    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(url, headers=dict(headers))
            return resp.status_code
    except Exception:
        return None


async def probe_sidecar_ready(
    facts: "ObservedTopologyFacts",
    *,
    sidecar_api_token: str,
    timeout_s: float = SIDECAR_READINESS_TIMEOUT_SECONDS,
    interval_s: float = SIDECAR_READINESS_POLL_INTERVAL_SECONDS,
    http_get: Optional[ReadinessHttpGet] = None,
    now: Optional[Callable[[], float]] = None,
    sleep: Optional[Callable[[float], Awaitable[None]]] = None,
) -> None:
    """Bounded, fail-closed Surface-A sidecar HTTP readiness gate.

    Probes the SAME product gateway path the live run uses (host -> loopback proxy
    Surface A -> LightRAG sidecar ``/health``), with the gateway derived FAIL-CLOSED
    from the VERIFIED observed facts via ``_derive_graphrag_gateway`` (exact
    ``http://127.0.0.1:<observed port>``; 0.0.0.0 / non-loopback / malformed / extra
    path / out-of-range all rejected). Authenticates with the run-local Surface-A token
    only (never the real OpenRouter key). ZERO provider egress: ``/health`` is a
    sidecar-whitelisted route that triggers no LLM/embedding call and no proxy budget.

    Success requires HTTP ``200``. Transient startup conditions — transport/connect
    failure (``None``) or ``500/502/503/504`` (Surface-A connect race / warmup) — are
    retried until the MONOTONIC deadline. Permanent failures (``401/403/404`` auth/
    misroute, a missing run-local token, a malformed gateway, or any other structurally
    invalid status) fail closed immediately without spinning. Native async: no
    ``asyncio.run`` / ``new_event_loop`` / ``run_until_complete`` / thread offload;
    ``now``/``sleep`` are injectable so tests are deterministic and fast.
    """
    import asyncio as _asyncio
    import time as _time

    if timeout_s <= 0:
        raise SidecarNotReady("readiness timeout must be positive")
    if interval_s <= 0:
        raise SidecarNotReady("readiness poll interval must be positive")
    if not (sidecar_api_token or "").strip():
        raise SidecarNotReady("sidecar_http_not_ready:permanent:run_local_token_missing")
    # Fail-closed gateway identity (reuses the governed observed-gateway constraints).
    gateway = _derive_graphrag_gateway(facts)
    url = gateway + SIDECAR_READINESS_ENDPOINT
    headers = {"X-API-Key": sidecar_api_token}  # run-local Surface-A token ONLY
    _get = http_get or _default_readiness_http_get
    _now = now or _time.monotonic
    _sleep = sleep or _asyncio.sleep
    deadline = _now() + timeout_s
    last = "no_attempt"
    while True:
        status = await _resolve_maybe_awaitable(_get(url, headers))
        if status == 200:
            return
        if status in _READINESS_PERMANENT_STATUSES:
            raise SidecarNotReady(f"sidecar_http_not_ready:permanent:{status}")
        if status is None:
            last = "transport"
        elif status in _READINESS_TRANSIENT_STATUSES:
            last = f"http_{status}"
        else:
            # Any other non-200 status is a structurally invalid readiness contract:
            # fail closed (do not spin to the deadline on a misrouted/odd response).
            raise SidecarNotReady(f"sidecar_http_not_ready:unexpected_status:{status}")
        if _now() >= deadline:
            raise SidecarNotReady(f"sidecar_http_not_ready:timeout:{last}")
        await _sleep(interval_s)


# =========================================================================== #
# F1 — concrete run-owned Docker topology (dual-homed proxy + internal-only sidecar).
# Reuses realsidecarpn02d.DockerCLI unchanged (additive). Not executed by unit tests
# (fake CommandRunner) nor by a live run this turn.
# =========================================================================== #
PROXY_CONTAINER_PORT = 8787


@dataclass
class RunOwnedTopology:
    """Concrete run-owned identities (not generic strings)."""

    run_id: str
    internal_network: str
    egress_network: str
    proxy_container: str
    sidecar_container: str
    host_published_port: int
    proxy_internal_alias: str
    storage_root: str
    graphrag_base_url: str
    sidecar_llm_binding_host: str
    sidecar_embedding_binding_host: str
    proxy_provider_internal_endpoint: str
    egressguard_initialized: bool = True

    @property
    def sidecar_networks(self) -> Sequence[str]:
        return (self.internal_network,)

    @property
    def sidecar_published_ports(self) -> Sequence[str]:
        return ()

    @property
    def proxy_networks(self) -> Sequence[str]:
        return (self.internal_network, self.egress_network)


def proxy_module_host_path() -> str:
    """Absolute host path to THIS package's providerbudgetpn02d.py (the file to mount)."""
    return str((Path(__file__).resolve().parent / "providerbudgetpn02d.py"))


def proxy_container_launch_argv(
    *,
    image: str,
    proxy_file_host_path: str,
    container_name: str,
    initial_network: str,
    host_port: int,
    local_token: str,
    control_token: str,
    real_key_env: str,
    sidecar_base: str,
    factory: str = "create_app_from_env",
) -> List[str]:
    """Exact ``docker run`` argv that boots the proxy ASGI app inside an ALREADY-LOCAL
    image by bind-mounting ONLY this single self-contained module file (no Dockerfile,
    no build, no pull). PYTHONPATH makes the standalone module importable.

    ``initial_network`` is the EGRESS (non-internal) network so the loopback host port
    can be published on Docker Desktop; the internal network is attached afterward via
    ``docker network connect`` (an internal-first attach cannot publish a host port).
    """
    return [
        "docker", "run", "-d", "--rm", "--name", container_name,
        "--network", initial_network,
        "-p", f"127.0.0.1:{host_port}:{PROXY_CONTAINER_PORT}",
        "-v", f"{proxy_file_host_path}:/pbp/providerbudgetpn02d.py:ro",
        "-e", "PYTHONPATH=/pbp",
        "-e", f"PBP_LOCAL_TOKEN={local_token}",
        "-e", f"PBP_CONTROL_TOKEN={control_token}",
        "-e", f"PBP_REAL_OPENROUTER_KEY={real_key_env}",
        "-e", f"PBP_SIDECAR_BASE={sidecar_base}",
        "-w", "/pbp",
        "--entrypoint", "python", image,
        "-m", "uvicorn", f"providerbudgetpn02d:{factory}", "--factory",
        "--host", "0.0.0.0", "--port", str(PROXY_CONTAINER_PORT),
    ]


@dataclass(frozen=True)
class ObservedTopologyFacts:
    """TopologyFacts built from ACTUAL post-boot Docker inspection (not intent).

    Satisfies the TopologyFacts protocol the verifier consumes, plus the runtime
    attestations (network-A internal flag, proxy loopback bind) checked at boot.
    """

    internal_network: str
    egress_network: str
    graphrag_base_url: str
    proxy_provider_internal_endpoint: str
    sidecar_llm_binding_host: str
    sidecar_embedding_binding_host: str
    network_a_internal: bool
    proxy_bind_loopback: bool
    egressguard_initialized: bool
    _sidecar_networks: Tuple[str, ...]
    _sidecar_published_ports: Tuple[str, ...]
    _proxy_networks: Tuple[str, ...]

    @property
    def sidecar_networks(self) -> Sequence[str]:
        return self._sidecar_networks

    @property
    def sidecar_published_ports(self) -> Sequence[str]:
        return self._sidecar_published_ports

    @property
    def proxy_networks(self) -> Sequence[str]:
        return self._proxy_networks


@dataclass
class SidecarLaunchSpec:
    """Injected sidecar launch (live = provider-bound LightRAG; smoke = fake)."""

    image: str
    entrypoint: Optional[str]  # None => image default
    command: Sequence[str]
    env: Mapping[str, str]


class DockerTopologyController:
    """Concrete dual-network boot + runtime-derived facts + idempotent best-effort
    cleanup, reusing realsidecarpn02d.DockerCLI unchanged.

    boot() tracks created resources per stage so cleanup removes EXACTLY what exists.
    Cleanup is existence-aware: an already-absent run-owned resource is 'already clean'
    (not a failure); only a removal that FAILS on an existing run-owned resource is a
    failure. Only exact run-owned names are touched (no prune/broad-match), so unrelated
    Docker resources (SurrealDB, app) are never affected.
    """

    def __init__(self, docker=None, *, run_id: Optional[str] = None) -> None:
        from open_notebook.integrations.graphrag.eval.realsidecarpn02d import DockerCLI

        self._docker = docker or DockerCLI()
        self._run_id = run_id or uuid.uuid4().hex[:12]
        self._storage_root: Optional[str] = None
        self._stages: set = set()  # resources actually created this run
        self._topo: Optional[RunOwnedTopology] = None

    # -------- intended identities (names only) --------
    def topology(self, *, host_port: int = 8900) -> RunOwnedTopology:
        import tempfile

        rid = self._run_id
        proxy_c = f"pbp-proxy-{rid}"
        provider_internal = f"http://{proxy_c}:{PROXY_CONTAINER_PORT}"
        self._storage_root = self._storage_root or tempfile.mkdtemp(prefix=f"pbp-store-{rid}-")
        topo = RunOwnedTopology(
            run_id=rid,
            internal_network=f"pbp-net-int-{rid}",
            egress_network=f"pbp-net-egr-{rid}",
            proxy_container=proxy_c,
            sidecar_container=f"pbp-sidecar-{rid}",
            host_published_port=host_port,
            proxy_internal_alias=proxy_c,
            storage_root=self._storage_root,
            graphrag_base_url=f"http://127.0.0.1:{host_port}",
            sidecar_llm_binding_host=provider_internal,
            sidecar_embedding_binding_host=provider_internal,
            proxy_provider_internal_endpoint=provider_internal,
        )
        self._topo = topo
        return topo

    # -------- runtime inspection (content-safe: ids/networks/ports/flags only) --------
    def _container_networks(self, name: str) -> Tuple[str, ...]:
        r = self._docker.run(
            ["docker", "inspect", "-f", "{{range $k,$v := .NetworkSettings.Networks}}{{$k}},{{end}}", name]
        )
        if r.returncode != 0:
            return ()
        return tuple(x for x in r.stdout.strip().split(",") if x)

    def _container_published_ports(self, name: str) -> Tuple[str, ...]:
        r = self._docker.run(
            ["docker", "inspect", "-f", "{{json .NetworkSettings.Ports}}", name]
        )
        if r.returncode != 0 or not r.stdout.strip():
            return ()
        import json as _json

        try:
            ports = _json.loads(r.stdout.strip()) or {}
        except Exception:
            return ()
        out: List[str] = []
        for _cport, binds in ports.items():
            for b in binds or []:
                out.append(f"{b.get('HostIp', '')}:{b.get('HostPort', '')}")
        return tuple(out)

    def _network_internal(self, name: str) -> bool:
        r = self._docker.run(["docker", "network", "inspect", "-f", "{{.Internal}}", name])
        return r.returncode == 0 and r.stdout.strip().lower() == "true"

    # -------- concrete dual-network boot --------
    def boot(
        self,
        *,
        sidecar: SidecarLaunchSpec,
        local_token: str,
        control_token: str,
        provider_key_env: str,
        proxy_image: str = SIDECAR_IMAGE,
        proxy_factory: str = "create_app_from_env",
        host_port: int = 8900,
    ) -> ObservedTopologyFacts:
        topo = self.topology(host_port=host_port)

        def _ck(ok: bool, stage: str, detail: str) -> None:
            if not ok:
                raise LiveAbort("boot", f"{stage}: {detail}")
            self._stages.add(stage)

        # 1. Network A internal
        _ck(self._docker.network_create_internal(topo.internal_network), "network_a_created", "create failed")
        # 2. Network B egress (plain bridge)
        _ck(
            self._docker.run(["docker", "network", "create", "--driver", "bridge", topo.egress_network]).returncode == 0,
            "network_b_created",
            "create failed",
        )
        # 3. Proxy on the EGRESS network first (so the loopback host port publishes),
        #    with a single-file mount of the standalone proxy module.
        argv = proxy_container_launch_argv(
            image=proxy_image,
            proxy_file_host_path=proxy_module_host_path(),
            container_name=topo.proxy_container,
            initial_network=topo.egress_network,
            host_port=topo.host_published_port,
            local_token=local_token,
            control_token=control_token,
            real_key_env=provider_key_env,
            sidecar_base=f"http://{topo.sidecar_container}:{_SIDECAR_SMOKE_PORT}",
            factory=proxy_factory,
        )
        _ck(self._docker.run(argv, timeout=120).returncode == 0, "proxy_started", "proxy run failed")
        # 4. Attach proxy to the INTERNAL network too (dual-homed: egress + internal)
        _ck(
            self._docker.run(["docker", "network", "connect", topo.internal_network, topo.proxy_container]).returncode == 0,
            "proxy_joined_network_b",
            "connect failed",
        )
        # 5. Sidecar on Network A ONLY, NO host port, run-owned storage, bindings->proxy
        scv = ["docker", "run", "-d", "--rm", "--name", topo.sidecar_container, "--network", topo.internal_network]
        for k, v in sidecar.env.items():
            scv += ["-e", f"{k}={v}"]
        scv += ["-v", f"{topo.storage_root}:/rag_storage"]
        if sidecar.entrypoint is not None:
            scv += ["--entrypoint", sidecar.entrypoint]
        scv += [sidecar.image, *sidecar.command]
        _ck(self._docker.run(scv, timeout=120).returncode == 0, "sidecar_started", "sidecar run failed")
        # 6. Runtime inspection -> observed facts
        proxy_nets = self._container_networks(topo.proxy_container)
        sidecar_nets = self._container_networks(topo.sidecar_container)
        sidecar_ports = self._container_published_ports(topo.sidecar_container)
        proxy_ports = self._container_published_ports(topo.proxy_container)
        a_internal = self._network_internal(topo.internal_network)
        proxy_loopback = all(p.startswith("127.0.0.1:") for p in proxy_ports) if proxy_ports else True
        self._stages.add("runtime_inspected")
        # runtime attestations (fail-closed before returning facts)
        _ck(a_internal, "network_a_internal_true", "Network A is not internal")
        _ck(not sidecar_ports, "sidecar_no_host_port", f"sidecar published ports {sidecar_ports}")
        _ck(proxy_loopback, "proxy_bind_loopback", f"proxy bind not loopback: {proxy_ports}")
        return ObservedTopologyFacts(
            internal_network=topo.internal_network,
            egress_network=topo.egress_network,
            graphrag_base_url=topo.graphrag_base_url,
            proxy_provider_internal_endpoint=topo.proxy_provider_internal_endpoint,
            sidecar_llm_binding_host=topo.sidecar_llm_binding_host,
            sidecar_embedding_binding_host=topo.sidecar_embedding_binding_host,
            network_a_internal=a_internal,
            proxy_bind_loopback=proxy_loopback,
            egressguard_initialized=True,
            _sidecar_networks=sidecar_nets,
            _sidecar_published_ports=sidecar_ports,
            _proxy_networks=proxy_nets,
        )

    # -------- idempotent, existence-aware best-effort cleanup --------
    def cleanup(self, topo: Optional[RunOwnedTopology] = None) -> "CleanupReport":
        topo = topo or self._topo
        if topo is None:
            return CleanupReport(True, True, True, True, True, True)
        failures: List[str] = []

        def _rm_container(name: str) -> bool:
            if not self._docker.container_exists(name):
                return True  # already absent => clean
            try:
                return self._docker.run(["docker", "rm", "-f", name]).returncode == 0
            except Exception as exc:  # noqa: BLE001 - best effort
                failures.append(f"{name}:{type(exc).__name__}")
                return False

        def _rm_network(name: str) -> bool:
            if not self._docker.network_exists(name):
                return True
            try:
                return self._docker.network_rm(name)
            except Exception as exc:  # noqa: BLE001
                failures.append(f"{name}:{type(exc).__name__}")
                return False

        # attempt ALL steps regardless of earlier failures
        sidecar_removed = _rm_container(topo.sidecar_container)
        proxy_removed = _rm_container(topo.proxy_container)
        internal_removed = _rm_network(topo.internal_network)
        egress_removed = _rm_network(topo.egress_network)
        try:
            import shutil

            if os.path.exists(topo.storage_root):
                shutil.rmtree(topo.storage_root, ignore_errors=True)
            storage_removed = not os.path.exists(topo.storage_root)
        except Exception:  # noqa: BLE001
            storage_removed = False
        return CleanupReport(
            sidecar_removed=sidecar_removed,
            proxy_removed=proxy_removed,
            internal_network_removed=internal_removed,
            egress_network_removed=egress_removed,
            storage_removed=storage_removed,
            tokens_discarded=True,
            unrelated_touched=False,
        )

    def zero_residue(self, topo: Optional[RunOwnedTopology] = None) -> bool:
        topo = topo or self._topo
        if topo is None:
            return True
        return not (
            self._docker.container_exists(topo.sidecar_container)
            or self._docker.container_exists(topo.proxy_container)
            or self._docker.network_exists(topo.internal_network)
            or self._docker.network_exists(topo.egress_network)
            or os.path.exists(topo.storage_root)
        )


#: Internal port the (fake/real) sidecar listens on inside Network A.
_SIDECAR_SMOKE_PORT = 9621


#: A product GraphRAG gateway must be an EXACT loopback-published proxy endpoint.
#: Derived from the actual observed facts (never a hardcoded/guessed port); non-
#: loopback (e.g. 0.0.0.0), empty, malformed or ambiguous values fail closed.
_LOOPBACK_GATEWAY_RE = re.compile(r"http://127\.0\.0\.1:([0-9]{1,5})$")


def _derive_graphrag_gateway(facts: "ObservedTopologyFacts") -> str:
    """Fail-closed: return the single verified loopback gateway from observed facts.

    Rejects missing/empty, non-loopback (0.0.0.0 or other host), malformed/extra
    (ambiguous) values, or a port outside 1..65535. The facts model carries exactly
    one graphrag_base_url, so a unique governed selection exists by construction.
    """
    base = (facts.graphrag_base_url or "").strip()
    if not base:
        raise LiveAbort("run_scoped_env", "observed graphrag gateway missing")
    if not facts.proxy_bind_loopback:
        raise LiveAbort("run_scoped_env", "proxy not attested loopback-bound")
    m = _LOOPBACK_GATEWAY_RE.fullmatch(base)
    if not m:
        raise LiveAbort("run_scoped_env", "observed graphrag gateway not an exact loopback endpoint")
    port = int(m.group(1))
    if not (0 < port <= 65535):
        raise LiveAbort("run_scoped_env", "observed graphrag gateway port out of range")
    return base


def run_scoped_env_values(facts: "ObservedTopologyFacts", *, sidecar_api_token: str) -> Dict[str, str]:
    """The exact process-local env a live run activates AFTER topology verify.

    The GraphRAG base URL is derived (fail-closed) from the VERIFIED observed facts,
    and OPEN_NOTEBOOK_GRAPHRAG_API_KEY carries the run-local proxy/sidecar token (never
    the real OpenRouter key). Canonical-Chat retry/timeout bounds are pinned here.
    """
    gateway = _derive_graphrag_gateway(facts)
    return {
        "OPEN_NOTEBOOK_GRAPHRAG_ENABLED": "true",
        "OPEN_NOTEBOOK_GRAPHRAG_BASE_URL": gateway,
        "OPEN_NOTEBOOK_GRAPHRAG_API_KEY": sidecar_api_token,
        "OPEN_NOTEBOOK_LLM_MAX_RETRIES": "0",
        "OPEN_NOTEBOOK_LLM_REQUEST_TIMEOUT_SECONDS": "60",
    }


# =========================================================================== #
# LIVE default adapters — a future authorized run needs NO hand-wired Callables.
# Heavy/product imports are lazy; nothing here executes except under a live run.
# =========================================================================== #
class _SubprocessGit:
    def head(self) -> str:
        import subprocess

        return subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()

    def worktree_clean(self) -> bool:
        import subprocess

        return subprocess.run(["git", "status", "--porcelain"], capture_output=True, text=True).stdout.strip() == ""

    def tag_peel(self, tag: str) -> Optional[str]:
        import subprocess

        r = subprocess.run(["git", "rev-parse", tag + "^{}"], capture_output=True, text=True)
        return r.stdout.strip() if r.returncode == 0 else None

    def tag_annotation(self, tag: str) -> Optional[Mapping[str, str]]:
        import subprocess

        r = subprocess.run(
            ["git", "for-each-ref", "refs/tags/" + tag, "--format=%(contents)"], capture_output=True, text=True
        )
        if r.returncode != 0 or not r.stdout.strip():
            return None
        ann: Dict[str, str] = {}
        for line in r.stdout.splitlines():
            if "=" in line:
                k, v = line.split("=", 1)
                ann[k.strip()] = v.strip()
        return ann


def _question_text() -> str:
    """Load the exact PN02Q01 question text from the authoritative fixture."""
    fx = load_fixture()
    return fx.query(QUERY_ID).question


def default_run_deps(
    manifest_inputs: ManifestInputs,
    *,
    sidecar_api_token: str,
    local_provider_token: str,
    control_token: str,
    provider_key_env: str,
    host_port: int = 8900,
    run_id: Optional[str] = None,
) -> RunDeps:
    """Build a RunDeps whose every seam has a CONCRETE default implementation, so an
    authorized live run requires no injected Callables. Tests still inject fakes.
    Nothing here executes until run_live_validation is driven under an authorized run.
    """
    # D4-A: the DEFAULT governed path binds phase/counter control to the IN-PATH container
    # proxy via its run-local loopback control surface — NOT a disconnected in-process
    # BudgetProxyController. The loopback base is the published proxy port (Surface A host
    # entry), which also serves the reserved /__pbp_control__/* routes. The container proxy
    # (create_app_from_env) holds the authoritative counters/phase for the sidecar's traffic.
    controller: SupportsProxyControl = ProxyControlClient(
        base_url=f"http://127.0.0.1:{host_port}",
        control_token=control_token,
    )
    topo_ctrl = DockerTopologyController(run_id=run_id)
    facts_holder: Dict[str, ObservedTopologyFacts] = {}

    def _boot() -> ObservedTopologyFacts:
        from open_notebook.integrations.graphrag.eval.provider_binding08 import (
            CONTAINER_EMBEDDING_BINDING,
            CONTAINER_EMBEDDING_HOST,
            CONTAINER_EMBEDDING_MODEL,
            CONTAINER_EMBEDDING_SECRET,
            CONTAINER_LLM_BINDING,
            CONTAINER_LLM_HOST,
            CONTAINER_LLM_MODEL,
            CONTAINER_LLM_SECRET,
            FROZEN_EMBEDDING_BINDING,
            FROZEN_EMBEDDING_MODEL,
            FROZEN_LLM_BINDING,
            FROZEN_LLM_MODEL,
        )

        topo = topo_ctrl.topology(host_port=host_port)
        pin = topo.proxy_provider_internal_endpoint
        sidecar_env = {
            CONTAINER_LLM_BINDING: FROZEN_LLM_BINDING,
            CONTAINER_LLM_MODEL: FROZEN_LLM_MODEL,
            CONTAINER_LLM_HOST: pin,
            CONTAINER_LLM_SECRET: local_provider_token,
            CONTAINER_EMBEDDING_BINDING: FROZEN_EMBEDDING_BINDING,
            CONTAINER_EMBEDDING_MODEL: FROZEN_EMBEDDING_MODEL,
            CONTAINER_EMBEDDING_HOST: pin,
            CONTAINER_EMBEDDING_SECRET: local_provider_token,
            "LIGHTRAG_API_KEY": sidecar_api_token,
        }
        spec = SidecarLaunchSpec(image=SIDECAR_IMAGE, entrypoint=None, command=[], env=sidecar_env)
        facts = topo_ctrl.boot(
            sidecar=spec,
            local_token=local_provider_token,
            control_token=control_token,
            provider_key_env=os.environ.get(provider_key_env, ""),
            proxy_factory="create_app_from_env",
            host_port=host_port,
        )
        facts_holder["f"] = facts
        return facts

    def _cleanup() -> CleanupReport:
        return topo_ctrl.cleanup()

    async def _index(source_id: str, text: str) -> str:
        from open_notebook.integrations.graphrag.service import GraphRAGService

        ack = await GraphRAGService().index_synthetic_document(source_id=source_id, canonical_text=text)
        return getattr(ack, "track_id", "")

    async def _track(track_id: str) -> str:
        from open_notebook.integrations.graphrag.service import GraphRAGService

        st = await GraphRAGService().track_status(track_id)
        # Return the CANONICAL IndexState VALUE ("in_progress"/"processed"/"failed"),
        # never str(enum): str(IndexState.PROCESSED) yields "IndexState.PROCESSED", whose
        # lowercase "indexstate.processed" matches no bounded_track_poll terminal and so
        # times out even a fully-processed document (the live-run-#4 defect). The enum
        # VALUE is bounded_track_poll's terminal vocabulary.
        state = getattr(st, "state", None)
        return str(getattr(state, "value", state) or "").lower() or "unknown"

    async def _list_docs() -> Sequence[str]:
        from open_notebook.integrations.graphrag.service import GraphRAGService

        page = await GraphRAGService().list_remote_documents_detailed(page=1, page_size=100)
        return [str(getattr(d, "doc_id", d)) for d in getattr(page, "documents", [])]

    def _load_text(source_id: str) -> str:
        fx = load_fixture()
        key = {"source:gr_pn02_a1": "A1", "source:gr_pn02_a2": "A2", "source:gr_pn02_a3": "A3"}[source_id]
        return fx.source_text(key)

    async def _execute_chat() -> Mapping[str, object]:
        from api.routers.chat import ExecuteChatRequest, execute_chat

        req = ExecuteChatRequest(
            session_id=SESSION_ID,
            message=_question_text(),
            context={"sources": [{"id": s} for s in CANONICAL_CONTEXT_SOURCE_IDS], "notes": []},
            model_override=None,
        )
        resp = await execute_chat(req)
        gp = getattr(resp, "graph_provenance", None)
        ids = list(getattr(gp, "source_ids", []) or []) if gp else []
        return {
            "graph_provenance_source_ids": ids,
            "graph_answer_text_surfaced": False,
            "graph_excerpts_surfaced": False,
        }

    # Native async concrete adapters: await the async repository APIs directly on the
    # orchestrator's running loop. No asyncio.new_event_loop/run_until_complete/run.
    async def _read_model() -> Mapping[str, object]:
        from open_notebook.database.repository import ensure_record_id, repo_query

        rows = await repo_query("SELECT * FROM $id", {"id": ensure_record_id(MODEL_ID)})
        r = rows[0] if rows else {}
        return {
            "id": str(r.get("id")),
            "provider": r.get("provider"),
            "name": r.get("name"),
            "type": r.get("type"),
            "credential": r.get("credential"),
        }

    async def _read_session() -> Mapping[str, object]:
        from open_notebook.database.repository import ensure_record_id, repo_query

        s = await repo_query("SELECT * FROM $id", {"id": ensure_record_id(SESSION_ID)})
        edges = await repo_query(
            "SELECT out FROM refers_to WHERE in=$s", {"s": ensure_record_id(SESSION_ID)}
        )
        row = s[0] if s else {}
        return {"model_override": row.get("model_override"), "refers_to_out": [str(e["out"]) for e in edges]}

    async def _read_checkpoint() -> int:
        from open_notebook.graphs.chat import graph as chat_graph
        from open_notebook.utils.graph_utils import get_session_message_count

        return await get_session_message_count(chat_graph, SESSION_ID)

    async def _read_trust() -> Mapping[str, object]:
        from open_notebook.database.repository import ensure_record_id, repo_query

        ok = True
        for a in INDEX_SOURCE_IDS:
            rc = await repo_query(
                "SELECT out FROM reference WHERE in=$s AND out=$n",
                {"s": ensure_record_id(a), "n": ensure_record_id(NOTEBOOK_ID)},
            )
            ok = ok and len(rc) == 1
        return {"intact": ok}

    def _question_sha() -> str:
        return hashlib.sha256(_question_text().encode("utf-8")).hexdigest()

    def _verify_digest() -> str:
        from open_notebook.integrations.graphrag.eval.realsidecarpn02d import DockerCLI

        r = DockerCLI().run(["docker", "image", "inspect", "-f", "{{.Id}}", SIDECAR_IMAGE])
        return r.stdout.strip() if r.returncode == 0 else ""

    def _env_factory(facts: "ObservedTopologyFacts") -> Mapping[str, str]:
        # Self-contained default path: derive the run-scoped env from the VERIFIED
        # post-boot observed facts (actual loopback gateway), using the run-local
        # proxy/sidecar token — never a hardcoded port or the real OpenRouter key.
        return run_scoped_env_values(facts, sidecar_api_token=sidecar_api_token)

    async def _wait_sidecar_ready(facts: "ObservedTopologyFacts") -> None:
        # Self-contained default path: the REAL bounded, fail-closed Surface-A HTTP
        # readiness gate, driven off the VERIFIED observed facts with the run-local
        # token. No provider egress; captured bounded timeout/interval defaults.
        await probe_sidecar_ready(
            cast("ObservedTopologyFacts", facts), sidecar_api_token=sidecar_api_token
        )

    return RunDeps(
        git=_SubprocessGit(),
        manifest_inputs=manifest_inputs,
        read_model_state=_read_model,
        read_session_state=_read_session,
        read_checkpoint_message_count=_read_checkpoint,
        read_canonical_trust=_read_trust,
        recompute_question_sha256=_question_sha,
        verify_local_sidecar_digest=_verify_digest,
        boot_topology=_boot,
        cleanup=_cleanup,
        index_source=_index,
        track_status=_track,
        list_indexed_doc_ids=_list_docs,
        load_source_text=_load_text,
        execute_one_chat=_execute_chat,
        controller=controller,
        control_token=control_token,
        credential_present=lambda: bool(os.environ.get(provider_key_env, "").strip()),
        run_scoped_env_factory=_env_factory,
        wait_sidecar_ready=_wait_sidecar_ready,
    )
