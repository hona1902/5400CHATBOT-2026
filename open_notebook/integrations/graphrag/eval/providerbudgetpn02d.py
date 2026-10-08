"""GraphRAG-09H run-owned provider BUDGET PROXY (eval / live-validation only).

EVALUATION/VALIDATION INFRASTRUCTURE. Never imported by production runtime (API,
worker, commands, app startup). It exists so a future separately-authorized
synthetic 09H product-live run can enforce + exactly count the LightRAG sidecar's
provider operations through a run-owned gateway.

Two logical gateway surfaces in one run-owned proxy process:

  * Surface A — PRODUCT SIDECAR API GATEWAY: host product GraphRAG client -> proxy
    -> LightRAG sidecar (over the run-owned internal Docker network). Carries the
    LightRAG application APIs (index / track / documents / query / health). These
    requests are NOT provider LLM/embedding operations and never touch the
    provider budget counters; the product ``/query`` request IS counted separately
    (request_path_counts) for the product-level GRAPHRAG_QUERY_COUNT<=1 invariant.

  * Surface B — PROVIDER EGRESS GATEWAY: LightRAG sidecar -> proxy -> OpenRouter.
    ONLY this surface classifies, budgets and pins provider requests.

Governance contract (operator-approved, GRAPHRAG_09H_BUDGET_PROXY_AND_MANIFEST_
AMENDMENT_OPERATOR_DECISION):

  * operation-count semantic = ``provider_forwarded_request_attempts_including_
    retries_index_phase_delta`` (ATTEMPTS forwarded upstream, incl. LightRAG-
    internal retries; INDEX = after-index minus baseline delta).
  * INDEX ceilings: 25 LLM / 25 embedding forwarded attempts, enforced PRE-EGRESS
    (the 26th is denied locally and never reaches OpenRouter).
  * QUERY-phase provider counters are INFORMATIONAL (no ceiling here; the product
    GRAPHRAG_QUERY_COUNT<=1 remains the authoritative query kill-switch).
  * DENY-by-default: unknown method/path/model/malformed => denied pre-egress +
    kill condition.
  * upstream hard-pinned to https://openrouter.ai/api/v1 (caller-supplied upstream
    is ignored).
  * credential isolation: the sidecar authenticates to the proxy with a run-owned
    LOCAL token; the real OpenRouter key lives ONLY in this proxy process and is
    swapped onto the upstream leg. Never logged / printed / hashed / persisted /
    placed in evidence.
  * content-safe evidence: integer counters + coarse hostnames + request-path
    counts only. Never prompt / request body / response / Authorization / key /
    source text.

No secrets in this module's output. No network I/O and NO web-framework import at
module import time. The Starlette ASGI surfaces — ``build_surface_a_app`` (host ->
sidecar product API gateway), ``build_surface_b_app`` (sidecar -> OpenRouter
provider egress) and the combined ``build_proxy_app`` — import Starlette lazily
inside the constructor, so this module stays importable as a standalone file (no
``open_notebook`` package import) inside the pinned LightRAG image for the run-owned
proxy container. ``create_app_from_env`` is the uvicorn ``--factory`` entry for that
container: the LIVE upstream is hard-pinned to OpenRouter and CANNOT be changed by
env/caller/path/body; a SEPARATE ``create_smoke_app`` constructor (never used live)
takes an injected fake upstream for the provider-free Docker smoke. The budget logic
is exercised provider-free through ``BudgetProxyController`` directly and through the
ASGI apps via an in-process transport with injected fakes.
"""

from __future__ import annotations

import asyncio
import enum
from dataclasses import dataclass
from typing import Awaitable, Callable, Dict, Mapping, Optional, Tuple

# --------------------------------------------------------------------------- #
# Frozen 09H constants
# --------------------------------------------------------------------------- #
OPENROUTER_UPSTREAM: str = "https://openrouter.ai/api/v1"
FROZEN_LLM_MODEL: str = "openai/gpt-4o-mini"
FROZEN_EMBEDDING_MODEL: str = "openai/text-embedding-3-small"
INDEX_LLM_BUDGET: int = 25
INDEX_EMBEDDING_BUDGET: int = 25
OPERATION_COUNT_SEMANTIC_ID: str = (
    "provider_forwarded_request_attempts_including_retries_index_phase_delta"
)
LLM_PATH_SUFFIX: str = "/chat/completions"
EMBEDDING_PATH_SUFFIX: str = "/embeddings"


class RunPhase(str, enum.Enum):
    PRE_INDEX = "PRE_INDEX"
    INDEX = "INDEX"
    POST_INDEX_QUERY = "POST_INDEX_QUERY"
    FINISHED = "FINISHED"


class ProviderClass(str, enum.Enum):
    LLM = "LLM"
    EMBEDDING = "EMBEDDING"
    UNKNOWN = "UNKNOWN"


class BudgetProxyError(RuntimeError):
    """A budget-proxy invariant was violated."""


class ControlAuthError(BudgetProxyError):
    """A phase/snapshot control call presented a wrong/absent control token."""


# --------------------------------------------------------------------------- #
# Request abstraction (content-safe: no body bytes retained here)
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class ProviderRequest:
    """Minimal content-safe view of a sidecar->provider request for classification.

    ``model`` is parsed from the JSON body by the thin HTTP layer and passed in as
    a scalar; classification itself is primary on method+path (model is a secondary
    validation field), so the raw prompt body is never required here.
    """

    method: str
    path: str
    model: Optional[str] = None


def classify(req: ProviderRequest) -> ProviderClass:
    """Classify by HTTP method + path suffix only (no prompt/body content)."""
    if req.method.upper() != "POST":
        return ProviderClass.UNKNOWN
    path = req.path.rstrip("/")
    if path.endswith(LLM_PATH_SUFFIX):
        return ProviderClass.LLM
    if path.endswith(EMBEDDING_PATH_SUFFIX):
        return ProviderClass.EMBEDDING
    return ProviderClass.UNKNOWN


def _model_allowed(cls: ProviderClass, model: Optional[str]) -> bool:
    """Secondary validation: the model slug must be the frozen one for its class.

    ``None`` (model not surfaced to classification) is tolerated — path is primary.
    """
    if model is None:
        return True
    if cls is ProviderClass.LLM:
        return model == FROZEN_LLM_MODEL
    if cls is ProviderClass.EMBEDDING:
        return model == FROZEN_EMBEDDING_MODEL
    return False


# --------------------------------------------------------------------------- #
# Decision + evidence
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class ForwardDecision:
    """Outcome of the pre-egress budget check. ``forwarded`` => reached upstream."""

    classification: ProviderClass
    forwarded: bool
    status: int
    reason: str


@dataclass
class ProxyEvidence:
    """Content-safe counters snapshot. Integers + coarse strings only."""

    phase: str
    llm_forwarded: int
    embedding_forwarded: int
    llm_denied_budget: int
    embedding_denied_budget: int
    unknown_denied: int
    upstream_hosts: Tuple[str, ...]
    request_path_counts: Dict[str, int]
    index_budget_exceeded: bool
    pre_index_provider_call: bool

    def as_dict(self) -> Dict[str, object]:
        return {
            "phase": self.phase,
            "llm_forwarded": self.llm_forwarded,
            "embedding_forwarded": self.embedding_forwarded,
            "llm_denied_budget": self.llm_denied_budget,
            "embedding_denied_budget": self.embedding_denied_budget,
            "unknown_denied": self.unknown_denied,
            "upstream_hosts": list(self.upstream_hosts),
            "request_path_counts": dict(sorted(self.request_path_counts.items())),
            "index_budget_exceeded": self.index_budget_exceeded,
            "pre_index_provider_call": self.pre_index_provider_call,
        }


#: Injected upstream transport. Real implementation performs HTTPS to the pinned
#: OpenRouter host (credential already swapped by the controller). Tests inject a
#: fake. Signature: (classification, path, forwarded_headers, body) -> (status).
Upstream = Callable[
    [ProviderClass, str, Mapping[str, str], bytes], Awaitable[int]
]

#: Injected sidecar transport for Surface A (host->sidecar). Real implementation
#: forwards over the internal Docker network to the LightRAG container.
SidecarTransport = Callable[
    [str, str, Mapping[str, str], bytes], Awaitable[Tuple[int, bytes]]
]


# --------------------------------------------------------------------------- #
# Controller
# --------------------------------------------------------------------------- #
class BudgetProxyController:
    """Run-scoped, monotonic, atomic provider budget enforcement + gateway logic.

    Counters never reset; snapshots are non-mutating reads. The INDEX ceiling is
    enforced atomically (check-and-increment under a single asyncio.Lock) so no
    concurrency can forward more than the ceiling.
    """

    def __init__(
        self,
        *,
        real_openrouter_key: str,
        local_provider_token: str,
        control_token: str,
        upstream: Upstream,
        sidecar: Optional[SidecarTransport] = None,
    ) -> None:
        if not real_openrouter_key:
            raise BudgetProxyError("proxy requires the real OpenRouter key (held only here)")
        if not local_provider_token:
            raise BudgetProxyError("proxy requires a run-owned local provider token")
        if not control_token:
            raise BudgetProxyError("proxy requires a run-owned control token")
        # Secret held ONLY in this process object; never logged/serialized/evidence.
        self.__real_key = real_openrouter_key
        self.__local_token = local_provider_token
        self.__control_token = control_token
        self._upstream = upstream
        self._sidecar = sidecar
        self._lock = asyncio.Lock()
        self._phase = RunPhase.PRE_INDEX
        self._llm_forwarded = 0
        self._embedding_forwarded = 0
        self._llm_denied_budget = 0
        self._embedding_denied_budget = 0
        self._unknown_denied = 0
        self._index_budget_exceeded = False
        self._pre_index_provider_call = False
        self._upstream_hosts: Dict[str, int] = {}
        self._request_path_counts: Dict[str, int] = {}
        self._snapshots: Dict[str, Dict[str, int]] = {}

    # ----- content-safe evidence / readiness -----
    def evidence(self) -> ProxyEvidence:
        return ProxyEvidence(
            phase=self._phase.value,
            llm_forwarded=self._llm_forwarded,
            embedding_forwarded=self._embedding_forwarded,
            llm_denied_budget=self._llm_denied_budget,
            embedding_denied_budget=self._embedding_denied_budget,
            unknown_denied=self._unknown_denied,
            upstream_hosts=tuple(sorted(self._upstream_hosts)),
            request_path_counts=dict(self._request_path_counts),
            index_budget_exceeded=self._index_budget_exceeded,
            pre_index_provider_call=self._pre_index_provider_call,
        )

    def healthz(self) -> Dict[str, object]:
        """Local readiness: initialized state, zero provider calls, no secret."""
        ev = self.evidence().as_dict()
        ev["ready"] = True
        return ev

    # ----- phase / snapshot control (run-owned token required) -----
    def _auth_control(self, token: str) -> None:
        if token != self.__control_token:
            raise ControlAuthError("invalid run-owned control token")

    def _counter_snapshot(self) -> Dict[str, int]:
        return {
            "llm_forwarded": self._llm_forwarded,
            "embedding_forwarded": self._embedding_forwarded,
        }

    async def enter_index(self, control_token: str) -> None:
        self._auth_control(control_token)
        async with self._lock:
            if self._phase is not RunPhase.PRE_INDEX:
                raise BudgetProxyError(f"cannot enter INDEX from {self._phase}")
            self._snapshots["baseline_before_index"] = self._counter_snapshot()
            self._phase = RunPhase.INDEX

    async def snapshot_after_index_and_advance(self, control_token: str) -> Dict[str, int]:
        self._auth_control(control_token)
        async with self._lock:
            if self._phase is not RunPhase.INDEX:
                raise BudgetProxyError(f"cannot close INDEX from {self._phase}")
            snap = self._counter_snapshot()
            self._snapshots["snapshot_after_index"] = snap
            self._phase = RunPhase.POST_INDEX_QUERY
            return dict(snap)

    async def snapshot_final(self, control_token: str) -> Dict[str, int]:
        self._auth_control(control_token)
        async with self._lock:
            snap = self._counter_snapshot()
            self._snapshots["snapshot_after_query"] = snap
            self._phase = RunPhase.FINISHED
            return dict(snap)

    def phase_deltas(self) -> Dict[str, int]:
        """INDEX = after-index - baseline; QUERY = after-query - after-index."""
        base = self._snapshots.get("baseline_before_index", {"llm_forwarded": 0, "embedding_forwarded": 0})
        ai = self._snapshots.get("snapshot_after_index")
        aq = self._snapshots.get("snapshot_after_query")
        out: Dict[str, int] = {}
        if ai is not None:
            out["INDEX_LLM_OPERATION_COUNT"] = ai["llm_forwarded"] - base["llm_forwarded"]
            out["INDEX_EMBEDDING_OPERATION_COUNT"] = ai["embedding_forwarded"] - base["embedding_forwarded"]
            if aq is not None:
                out["QUERY_PHASE_LLM_OPERATION_COUNT"] = aq["llm_forwarded"] - ai["llm_forwarded"]
                out["QUERY_PHASE_EMBEDDING_OPERATION_COUNT"] = aq["embedding_forwarded"] - ai["embedding_forwarded"]
        return out

    # ----- Surface B: provider egress gateway -----
    async def handle_provider_request(
        self,
        req: ProviderRequest,
        *,
        local_auth: str,
        body: bytes = b"",
        extra_headers: Optional[Mapping[str, str]] = None,
    ) -> ForwardDecision:
        """Classify -> validate -> atomically budget-check+reserve -> forward (pinned).

        Returns a ForwardDecision. On any denial NO upstream call is made.
        """
        cls = classify(req)
        reserved = False
        async with self._lock:
            # local token auth: the sidecar must present the run-owned local token
            if local_auth != self.__local_token:
                self._unknown_denied += 1
                return ForwardDecision(cls, False, 401, "sidecar_local_token_invalid")
            if cls is ProviderClass.UNKNOWN or not _model_allowed(cls, req.model):
                self._unknown_denied += 1
                return ForwardDecision(cls, False, 400, "unknown_or_disallowed_provider_request")
            if self._phase is RunPhase.PRE_INDEX:
                # A provider call before INDEX is a kill condition (unexpected).
                self._pre_index_provider_call = True
                self._unknown_denied += 1
                return ForwardDecision(cls, False, 409, "provider_call_before_index_phase")
            if self._phase is RunPhase.FINISHED:
                self._unknown_denied += 1
                return ForwardDecision(cls, False, 409, "provider_call_after_run_finished")
            if self._phase is RunPhase.INDEX:
                if cls is ProviderClass.LLM and self._llm_forwarded >= INDEX_LLM_BUDGET:
                    self._llm_denied_budget += 1
                    self._index_budget_exceeded = True
                    return ForwardDecision(cls, False, 429, "index_llm_budget_exceeded")
                if cls is ProviderClass.EMBEDDING and self._embedding_forwarded >= INDEX_EMBEDDING_BUDGET:
                    self._embedding_denied_budget += 1
                    self._index_budget_exceeded = True
                    return ForwardDecision(cls, False, 429, "index_embedding_budget_exceeded")
            # POST_INDEX_QUERY: no index ceiling, still classified/counted/pinned.
            # Reserve the forwarded ATTEMPT count atomically (attempts, not successes).
            if cls is ProviderClass.LLM:
                self._llm_forwarded += 1
            else:
                self._embedding_forwarded += 1
            self._request_path_counts[_norm_path(req.path)] = (
                self._request_path_counts.get(_norm_path(req.path), 0) + 1
            )
            reserved = True
        # Egress OUTSIDE the lock. Credential swap: drop any inbound auth, set the
        # real OpenRouter key (held only here) for the upstream leg. Upstream host
        # is pinned by the injected transport; caller-supplied host is ignored.
        assert reserved
        fwd_headers = _swap_auth(extra_headers or {}, self.__real_key)
        status = await self._upstream(cls, _relative_provider_path(req.path), fwd_headers, body)
        self._upstream_hosts[_host_of(OPENROUTER_UPSTREAM)] = (
            self._upstream_hosts.get(_host_of(OPENROUTER_UPSTREAM), 0) + 1
        )
        return ForwardDecision(cls, True, status, "forwarded")

    # ----- Surface A: product sidecar API gateway (no provider counting) -----
    async def handle_sidecar_api_request(
        self,
        method: str,
        path: str,
        *,
        headers: Optional[Mapping[str, str]] = None,
        body: bytes = b"",
    ) -> Tuple[int, bytes]:
        """Reverse-proxy a host product API call to the sidecar. Not a provider op."""
        if self._sidecar is None:
            raise BudgetProxyError("sidecar transport not wired for Surface A")
        self._request_path_counts[_norm_path(path)] = (
            self._request_path_counts.get(_norm_path(path), 0) + 1
        )
        return await self._sidecar(method, path, headers or {}, body)

    def product_query_count(self) -> int:
        """Count of product /query calls proxied on Surface A (GRAPHRAG_QUERY_COUNT)."""
        total = 0
        for path, n in self._request_path_counts.items():
            if path.rstrip("/").endswith("/query") or path.rstrip("/") == "/query":
                total += n
        return total


# --------------------------------------------------------------------------- #
# helpers (content-safe; never touch secrets beyond the swap)
# --------------------------------------------------------------------------- #
def _norm_path(path: str) -> str:
    p = path.split("?", 1)[0]
    return p if p.startswith("/") else "/" + p


def _relative_provider_path(path: str) -> str:
    """Return only the OpenAI-compatible suffix; the upstream host is pinned."""
    p = _norm_path(path).rstrip("/")
    if p.endswith(LLM_PATH_SUFFIX):
        return LLM_PATH_SUFFIX
    if p.endswith(EMBEDDING_PATH_SUFFIX):
        return EMBEDDING_PATH_SUFFIX
    return p


def _host_of(url: str) -> str:
    rest = url.split("://", 1)[-1]
    return rest.split("/", 1)[0]


def _swap_auth(headers: Mapping[str, str], real_key: str) -> Dict[str, str]:
    """Return headers with any inbound auth replaced by the real upstream key.

    The returned mapping is for the upstream leg only; the real key is never
    returned to a caller, logged, or placed in evidence.
    """
    out = {k: v for k, v in headers.items() if k.lower() not in {"authorization", "x-api-key"}}
    out["Authorization"] = f"Bearer {real_key}"
    return out


# --------------------------------------------------------------------------- #
# Real HTTPS upstream + ASGI surfaces — constructed only by a live run, never at
# import and never exercised by the provider-free tests.
# --------------------------------------------------------------------------- #
def build_https_openrouter_upstream(timeout_seconds: float = 60.0) -> Upstream:
    """Real upstream: HTTPS to the hard-pinned OpenRouter host, normal cert verify.

    No TLS interception. The request/response bodies are forwarded but never logged
    or stored. Caller-supplied upstream host is impossible: the base is fixed here.
    """

    async def _upstream(
        cls: ProviderClass, rel_path: str, headers: Mapping[str, str], body: bytes
    ) -> int:
        import httpx  # lazy; not needed for provider-free tests

        url = OPENROUTER_UPSTREAM + rel_path
        async with httpx.AsyncClient(timeout=timeout_seconds) as client:
            resp = await client.post(url, headers=dict(headers), content=body)
            return resp.status_code

    return _upstream


def build_local_sidecar_transport(base_url: str, timeout_seconds: float = 60.0) -> SidecarTransport:
    """Real Surface-A transport: forward a product API call to the LightRAG sidecar
    over the run-owned internal network. No provider egress; bodies not logged."""

    async def _sidecar(method: str, path: str, headers: Mapping[str, str], body: bytes) -> Tuple[int, bytes]:
        import httpx  # lazy

        url = base_url.rstrip("/") + _norm_path(path)
        async with httpx.AsyncClient(timeout=timeout_seconds) as client:
            resp = await client.request(method, url, headers=dict(headers), content=body)
            return resp.status_code, resp.content

    return _sidecar


def _bearer(headers: Mapping[str, str]) -> str:
    """Extract a bearer token from an Authorization header (content-safe)."""
    auth = headers.get("authorization") or headers.get("Authorization") or ""
    return auth[7:] if auth.lower().startswith("bearer ") else auth


def _parse_model(body: bytes) -> Optional[str]:
    """Best-effort read of the OpenAI-compatible ``model`` field for secondary
    validation. Only the model slug is extracted; prompt/body is never stored."""
    import json

    try:
        obj = json.loads(body.decode("utf-8") or "{}")
    except Exception:
        return None
    model = obj.get("model") if isinstance(obj, dict) else None
    return model if isinstance(model, str) else None


# --------------------------------------------------------------------------- #
# ASGI surfaces (Starlette imported lazily so the module stays import-light)
# --------------------------------------------------------------------------- #
def build_surface_b_app(controller: "BudgetProxyController"):
    """Surface B: sidecar -> proxy -> OpenRouter. Only POST /chat/completions and
    POST /embeddings are accepted; everything else denies pre-egress. Classification
    uses method+path (model secondary). The real upstream is pinned in the controller."""
    from starlette.applications import Starlette
    from starlette.requests import Request
    from starlette.responses import JSONResponse, Response
    from starlette.routing import Route

    async def _provider(request: "Request") -> "Response":
        body = await request.body()
        model = _parse_model(body)
        req = ProviderRequest(method=request.method, path=request.url.path, model=model)
        decision = await controller.handle_provider_request(
            req,
            local_auth=_bearer(request.headers),
            body=body,
            extra_headers={"Content-Type": request.headers.get("content-type", "application/json")},
        )
        # Content-safe: return only a status marker, never upstream body/secret.
        return JSONResponse({"forwarded": decision.forwarded, "reason": decision.reason}, status_code=decision.status)

    async def _deny(request: "Request") -> "Response":
        # Any other provider path/method is unknown => counted + denied pre-egress.
        req = ProviderRequest(method=request.method, path=request.url.path, model=None)
        decision = await controller.handle_provider_request(req, local_auth=_bearer(request.headers))
        return JSONResponse({"forwarded": False, "reason": decision.reason}, status_code=decision.status)

    routes = [
        Route("/chat/completions", _provider, methods=["POST"]),
        Route("/embeddings", _provider, methods=["POST"]),
        Route("/healthz", lambda r: JSONResponse(controller.healthz()), methods=["GET"]),
        Route("/{path:path}", _deny, methods=["GET", "POST", "PUT", "DELETE"]),
    ]
    return Starlette(routes=routes)


def build_surface_a_app(controller: "BudgetProxyController"):
    """Surface A: host product GraphRAG client -> proxy -> LightRAG sidecar. Transparent
    reverse proxy for the product API paths; never increments provider counters."""
    from starlette.applications import Starlette
    from starlette.requests import Request
    from starlette.responses import JSONResponse, Response
    from starlette.routing import Route

    async def _forward(request: "Request") -> "Response":
        body = await request.body()
        status, out = await controller.handle_sidecar_api_request(
            request.method, request.url.path, headers=dict(request.headers), body=body
        )
        return Response(content=out, status_code=status, media_type="application/json")

    routes = [
        Route("/healthz", lambda r: JSONResponse(controller.healthz()), methods=["GET"]),
        Route("/{path:path}", _forward, methods=["GET", "POST", "PUT", "DELETE"]),
    ]
    return Starlette(routes=routes)


def build_proxy_app(controller: "BudgetProxyController"):
    """Combined run-owned proxy app on one port: provider paths (/chat/completions,
    /embeddings) hit Surface B; all other product paths hit Surface A. The sidecar
    reaches this app (internal net) for provider calls; the host reaches it (published
    port) for product API calls — distinct path spaces, one app."""
    from starlette.applications import Starlette
    from starlette.requests import Request
    from starlette.responses import JSONResponse, Response
    from starlette.routing import Route

    async def _provider(request: "Request") -> "Response":
        body = await request.body()
        req = ProviderRequest(method=request.method, path=request.url.path, model=_parse_model(body))
        d = await controller.handle_provider_request(
            req,
            local_auth=_bearer(request.headers),
            body=body,
            extra_headers={"Content-Type": request.headers.get("content-type", "application/json")},
        )
        return JSONResponse({"forwarded": d.forwarded, "reason": d.reason}, status_code=d.status)

    async def _product(request: "Request") -> "Response":
        body = await request.body()
        status, out = await controller.handle_sidecar_api_request(
            request.method, request.url.path, headers=dict(request.headers), body=body
        )
        return Response(content=out, status_code=status, media_type="application/json")

    routes = [
        Route("/healthz", lambda r: JSONResponse(controller.healthz()), methods=["GET"]),
        Route("/chat/completions", _provider, methods=["POST"]),
        Route("/embeddings", _provider, methods=["POST"]),
        Route("/{path:path}", _product, methods=["GET", "POST", "PUT", "DELETE"]),
    ]
    return Starlette(routes=routes)


def _controller_from_env(*, upstream: Upstream, sidecar: Optional[SidecarTransport]) -> "BudgetProxyController":
    import os

    return BudgetProxyController(
        real_openrouter_key=os.environ.get("PBP_REAL_OPENROUTER_KEY", ""),
        local_provider_token=os.environ.get("PBP_LOCAL_TOKEN", ""),
        control_token=os.environ.get("PBP_CONTROL_TOKEN", ""),
        upstream=upstream,
        sidecar=sidecar,
    )


def create_app_from_env():
    """uvicorn --factory entry for the LIVE run-owned proxy container.

    Upstream is HARD-PINNED to OpenRouter (build_https_openrouter_upstream); there is
    NO env/caller override of the upstream host. The sidecar base (Surface A target)
    is read from env (run-owned internal address), NOT the provider upstream.
    """
    import os

    sidecar = build_local_sidecar_transport(os.environ.get("PBP_SIDECAR_BASE", "http://127.0.0.1:9621"))
    controller = _controller_from_env(upstream=build_https_openrouter_upstream(), sidecar=sidecar)
    return build_proxy_app(controller)


def create_smoke_app():
    """uvicorn --factory entry for the PROVIDER-FREE Docker smoke ONLY.

    Never used by a live run. The upstream is a local no-egress stub that returns 200
    WITHOUT contacting any network, so a container boot + /healthz + deny-path can be
    exercised with zero provider calls. (Surface B forwarding to a real fake upstream
    is proven in-process in tests via an injected fake upstream.)
    """
    import os

    async def _noop_upstream(cls: ProviderClass, rel_path: str, headers: Mapping[str, str], body: bytes) -> int:
        return 200  # never contacts any network

    sidecar = build_local_sidecar_transport(os.environ.get("PBP_SIDECAR_BASE", "http://127.0.0.1:9621"))
    controller = _controller_from_env(upstream=_noop_upstream, sidecar=sidecar)
    return build_proxy_app(controller)
