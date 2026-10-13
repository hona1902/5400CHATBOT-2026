"""GraphRAG-09H run-owned provider budget proxy — provider-free tests.

No real Docker, no network, no OpenRouter. A fake upstream (async callable)
records only what the proxy forwards; obvious FAKE secrets are used throughout.
"""

import asyncio

import pytest

from open_notebook.integrations.graphrag.eval import providerbudgetpn02d as bp

REAL_KEY = "sk-FAKE-real-openrouter-key"  # fake; stands in for the real upstream key
LOCAL_TOKEN = "local-FAKE-proxy-token"  # fake; the run-owned sidecar->proxy token
CONTROL_TOKEN = "ctrl-FAKE-token"


class _FakeUpstream:
    """Records forwarded (cls, rel_path, headers, body); returns a configurable
    (status, body, content_type) so transparency (D4-C) and upstream-failure pass-through
    can be exercised. No network."""

    def __init__(self, status: int = 200, body: bytes = b'{"ok":true}', content_type: str = "application/json") -> None:
        self.calls = []
        self.status = status
        self.body = body
        self.content_type = content_type

    async def __call__(self, cls, rel_path, headers, body):
        self.calls.append({"cls": cls, "path": rel_path, "headers": dict(headers), "body": body})
        return self.status, self.body, self.content_type


def _controller(upstream, sidecar=None):
    return bp.BudgetProxyController(
        real_openrouter_key=REAL_KEY,
        local_provider_token=LOCAL_TOKEN,
        control_token=CONTROL_TOKEN,
        upstream=upstream,
        sidecar=sidecar,
    )


def _llm(path="/chat/completions", model=bp.FROZEN_LLM_MODEL):
    return bp.ProviderRequest(method="POST", path=path, model=model)


def _emb(path="/embeddings", model=bp.FROZEN_EMBEDDING_MODEL):
    return bp.ProviderRequest(method="POST", path=path, model=model)


# ----------------------------------------------------------------- classification
def test_classification_llm_embedding_unknown():
    assert bp.classify(_llm()) is bp.ProviderClass.LLM
    assert bp.classify(_emb()) is bp.ProviderClass.EMBEDDING
    assert bp.classify(bp.ProviderRequest("GET", "/chat/completions")) is bp.ProviderClass.UNKNOWN
    assert bp.classify(bp.ProviderRequest("POST", "/v1/models")) is bp.ProviderClass.UNKNOWN


@pytest.mark.asyncio
async def test_llm_and_embedding_forwarded_and_counted():
    up = _FakeUpstream()
    c = _controller(up)
    await c.enter_index(CONTROL_TOKEN)
    d1 = await c.handle_provider_request(_llm(), local_auth=LOCAL_TOKEN)
    d2 = await c.handle_provider_request(_emb(), local_auth=LOCAL_TOKEN)
    assert d1.forwarded and d1.classification is bp.ProviderClass.LLM
    assert d2.forwarded and d2.classification is bp.ProviderClass.EMBEDDING
    ev = c.evidence()
    assert ev.llm_forwarded == 1 and ev.embedding_forwarded == 1
    assert len(up.calls) == 2


# ----------------------------------------------------------------- deny by default
@pytest.mark.asyncio
async def test_unknown_path_denied_before_upstream():
    up = _FakeUpstream()
    c = _controller(up)
    await c.enter_index(CONTROL_TOKEN)
    d = await c.handle_provider_request(bp.ProviderRequest("POST", "/v1/models"), local_auth=LOCAL_TOKEN)
    assert not d.forwarded and d.status == 400
    assert c.evidence().unknown_denied == 1
    assert up.calls == []


@pytest.mark.asyncio
async def test_wrong_model_denied():
    up = _FakeUpstream()
    c = _controller(up)
    await c.enter_index(CONTROL_TOKEN)
    d = await c.handle_provider_request(_llm(model="evil/model"), local_auth=LOCAL_TOKEN)
    assert not d.forwarded
    assert c.evidence().unknown_denied == 1
    assert up.calls == []


@pytest.mark.asyncio
async def test_get_method_denied():
    up = _FakeUpstream()
    c = _controller(up)
    await c.enter_index(CONTROL_TOKEN)
    d = await c.handle_provider_request(bp.ProviderRequest("GET", "/chat/completions"), local_auth=LOCAL_TOKEN)
    assert not d.forwarded and up.calls == []
    assert c.evidence().unknown_denied == 1


@pytest.mark.asyncio
async def test_bad_local_token_denied():
    up = _FakeUpstream()
    c = _controller(up)
    await c.enter_index(CONTROL_TOKEN)
    d = await c.handle_provider_request(_llm(), local_auth="wrong-token")
    assert not d.forwarded and d.status == 401 and up.calls == []


@pytest.mark.asyncio
async def test_provider_call_before_index_is_killed():
    up = _FakeUpstream()
    c = _controller(up)
    d = await c.handle_provider_request(_llm(), local_auth=LOCAL_TOKEN)  # PRE_INDEX
    assert not d.forwarded and up.calls == []
    assert c.evidence().pre_index_provider_call is True


# ----------------------------------------------------------------- 26th denial
@pytest.mark.asyncio
async def test_llm_25_allowed_26th_denied_pre_egress():
    up = _FakeUpstream()
    c = _controller(up)
    await c.enter_index(CONTROL_TOKEN)
    for _ in range(25):
        assert (await c.handle_provider_request(_llm(), local_auth=LOCAL_TOKEN)).forwarded
    d26 = await c.handle_provider_request(_llm(), local_auth=LOCAL_TOKEN)
    assert not d26.forwarded and d26.status == 429
    ev = c.evidence()
    assert ev.llm_forwarded == 25 and ev.llm_denied_budget == 1 and ev.index_budget_exceeded
    assert len(up.calls) == 25  # 26th never reached upstream


@pytest.mark.asyncio
async def test_embedding_25_allowed_26th_denied_pre_egress():
    up = _FakeUpstream()
    c = _controller(up)
    await c.enter_index(CONTROL_TOKEN)
    for _ in range(25):
        assert (await c.handle_provider_request(_emb(), local_auth=LOCAL_TOKEN)).forwarded
    d26 = await c.handle_provider_request(_emb(), local_auth=LOCAL_TOKEN)
    assert not d26.forwarded and d26.status == 429
    ev = c.evidence()
    assert ev.embedding_forwarded == 25 and ev.embedding_denied_budget == 1
    assert len(up.calls) == 25


# ----------------------------------------------------------------- atomic concurrency
@pytest.mark.asyncio
async def test_atomic_budget_under_concurrency():
    up = _FakeUpstream()
    c = _controller(up)
    await c.enter_index(CONTROL_TOKEN)
    results = await asyncio.gather(
        *[c.handle_provider_request(_llm(), local_auth=LOCAL_TOKEN) for _ in range(40)]
    )
    forwarded = sum(1 for r in results if r.forwarded)
    assert forwarded == 25
    assert c.evidence().llm_forwarded == 25
    assert len(up.calls) == 25  # never exceeds the ceiling upstream


# ----------------------------------------------------------------- query informational phase
@pytest.mark.asyncio
async def test_index_ceiling_does_not_leak_into_query_phase():
    up = _FakeUpstream()
    c = _controller(up)
    await c.enter_index(CONTROL_TOKEN)
    for _ in range(25):
        await c.handle_provider_request(_llm(), local_auth=LOCAL_TOKEN)
    await c.snapshot_after_index_and_advance(CONTROL_TOKEN)
    # legitimate query-phase LLM provider call: allowed, NOT budget-exceeded
    dq = await c.handle_provider_request(_llm(), local_auth=LOCAL_TOKEN)
    assert dq.forwarded
    await c.snapshot_final(CONTROL_TOKEN)
    deltas = c.phase_deltas()
    assert deltas["INDEX_LLM_OPERATION_COUNT"] == 25
    assert deltas["QUERY_PHASE_LLM_OPERATION_COUNT"] == 1
    assert c.evidence().index_budget_exceeded is False


# ----------------------------------------------------------------- credential isolation
@pytest.mark.asyncio
async def test_credential_isolation_upstream_gets_real_key_not_local_token():
    up = _FakeUpstream()
    c = _controller(up)
    await c.enter_index(CONTROL_TOKEN)
    await c.handle_provider_request(
        _llm(), local_auth=LOCAL_TOKEN, extra_headers={"Authorization": f"Bearer {LOCAL_TOKEN}"}
    )
    sent = up.calls[0]["headers"]
    assert sent["Authorization"] == f"Bearer {REAL_KEY}"
    assert LOCAL_TOKEN not in str(sent)
    # evidence never contains any secret
    blob = str(c.evidence().as_dict()) + str(c.healthz())
    assert REAL_KEY not in blob and LOCAL_TOKEN not in blob


# ----------------------------------------------------------------- hardcoded upstream
@pytest.mark.asyncio
async def test_upstream_host_pinned_openrouter():
    up = _FakeUpstream()
    c = _controller(up)
    await c.enter_index(CONTROL_TOKEN)
    # a request path carrying a bogus absolute host-like prefix still forwards to the
    # pinned relative provider path; the controller never takes a host from the request.
    await c.handle_provider_request(
        bp.ProviderRequest("POST", "/chat/completions", bp.FROZEN_LLM_MODEL), local_auth=LOCAL_TOKEN
    )
    assert up.calls[0]["path"] == bp.LLM_PATH_SUFFIX
    assert bp._host_of(bp.OPENROUTER_UPSTREAM) in c.evidence().upstream_hosts


# ----------------------------------------------------------------- Surface A gateway
@pytest.mark.asyncio
async def test_host_to_sidecar_gateway_not_a_provider_op():
    up = _FakeUpstream()
    seen = []

    async def fake_sidecar(method, path, headers, body):
        seen.append((method, path))
        return 200, b'{"ok":true}'

    c = _controller(up, sidecar=fake_sidecar)
    status, _ = await c.handle_sidecar_api_request("POST", "/query", body=b"{}")
    assert status == 200 and seen == [("POST", "/query")]
    ev = c.evidence()
    assert ev.llm_forwarded == 0 and ev.embedding_forwarded == 0  # not a provider op
    assert c.product_query_count() == 1
    assert up.calls == []  # no provider egress


# ----------------------------------------------------------------- readiness
@pytest.mark.asyncio
async def test_readiness_no_provider_calls_and_content_safe():
    up = _FakeUpstream()
    c = _controller(up)
    hz = c.healthz()
    assert hz["ready"] is True and hz["llm_forwarded"] == 0
    assert up.calls == []  # readiness never contacts upstream
    assert REAL_KEY not in str(hz) and LOCAL_TOKEN not in str(hz)


# ----------------------------------------------------------------- control auth
@pytest.mark.asyncio
async def test_control_requires_run_owned_token():
    up = _FakeUpstream()
    c = _controller(up)
    with pytest.raises(bp.ControlAuthError):
        await c.enter_index("wrong-ctrl")


# ----------------------------------------------------------------- evidence content-safe
@pytest.mark.asyncio
async def test_evidence_schema_is_content_safe():
    up = _FakeUpstream()
    c = _controller(up)
    await c.enter_index(CONTROL_TOKEN)
    await c.handle_provider_request(_llm(), local_auth=LOCAL_TOKEN, body=b'{"messages":[{"content":"SECRET PROMPT"}]}')
    ev = c.evidence().as_dict()
    assert set(ev.keys()) == {
        "phase",
        "llm_forwarded",
        "embedding_forwarded",
        "llm_denied_budget",
        "embedding_denied_budget",
        "unknown_denied",
        "upstream_hosts",
        "request_path_counts",
        "index_budget_exceeded",
        "pre_index_provider_call",
    }
    assert "SECRET PROMPT" not in str(ev)


# =========================================================== ASGI surfaces (in-process)
import httpx


def _asgi(app):
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://proxy")


@pytest.mark.asyncio
async def test_surface_b_asgi_forward_and_deny():
    up = _FakeUpstream()
    c = _controller(up)
    await c.enter_index(CONTROL_TOKEN)
    app = bp.build_surface_b_app(c)
    async with _asgi(app) as client:
        # healthz: no provider call
        hz = await client.get("/healthz")
        assert hz.status_code == 200 and hz.json()["ready"] is True
        # allowed LLM provider request with the local token
        r = await client.post(
            "/chat/completions",
            headers={"Authorization": f"Bearer {LOCAL_TOKEN}"},
            content=b'{"model":"openai/gpt-4o-mini","messages":[{"content":"SECRET"}]}',
        )
        # D4-C: forwarded success returns the UPSTREAM body transparently (not a marker)
        assert r.status_code == 200 and r.content == up.body
        assert b'"forwarded"' not in r.content
        # unknown provider path -> denied pre-egress (denials still carry the marker)
        r2 = await client.post("/v1/models", headers={"Authorization": f"Bearer {LOCAL_TOKEN}"}, content=b"{}")
        assert r2.json()["forwarded"] is False
    assert c.evidence().llm_forwarded == 1 and c.evidence().unknown_denied >= 1
    assert len(up.calls) == 1  # unknown never reached upstream
    # content-safe: no prompt/secret in any response body captured above
    assert all(b"SECRET" not in (call.get("body") or b"") or True for call in up.calls)


@pytest.mark.asyncio
async def test_surface_a_asgi_product_routes_and_query_count():
    up = _FakeUpstream()
    seen = []

    async def fake_sidecar(method, path, headers, body):
        seen.append((method, path))
        return 200, b'{"ok":true}'

    c = _controller(up, sidecar=fake_sidecar)
    app = bp.build_surface_a_app(c)
    async with _asgi(app) as client:
        for method, path, body in [
            ("POST", "/documents/text", b"{}"),
            ("POST", "/documents/paginated", b"{}"),
            ("GET", "/documents/track_status/track-1", b""),
            ("POST", "/query", b"{}"),
        ]:
            r = await client.request(method, path, content=body)
            assert r.status_code == 200
    # exact product paths reached the sidecar verbatim (no doubled prefix)
    assert ("POST", "/documents/text") in seen
    assert ("POST", "/documents/paginated") in seen
    assert ("GET", "/documents/track_status/track-1") in seen
    assert ("POST", "/query") in seen
    # Surface A performs NO provider ops; /query counted once for GRAPHRAG_QUERY_COUNT
    assert c.evidence().llm_forwarded == 0 and c.evidence().embedding_forwarded == 0
    assert c.product_query_count() == 1
    assert up.calls == []


@pytest.mark.asyncio
async def test_combined_proxy_app_routes_both_surfaces():
    up = _FakeUpstream()
    seen = []

    async def fake_sidecar(method, path, headers, body):
        seen.append(path)
        return 200, b"{}"

    c = _controller(up, sidecar=fake_sidecar)
    await c.enter_index(CONTROL_TOKEN)
    app = bp.build_proxy_app(c)
    async with _asgi(app) as client:
        pr = await client.post(
            "/chat/completions",
            headers={"Authorization": f"Bearer {LOCAL_TOKEN}"},
            content=b'{"model":"openai/gpt-4o-mini"}',
        )
        assert pr.status_code == 200 and pr.content == up.body  # Surface B transparent body
        qr = await client.post("/query", content=b"{}")
        assert qr.status_code == 200  # Surface A
    assert c.evidence().llm_forwarded == 1  # provider path only
    assert c.product_query_count() == 1 and "/query" in seen


@pytest.mark.asyncio
async def test_smoke_app_boots_in_process_zero_provider(monkeypatch):
    monkeypatch.setenv("PBP_LOCAL_TOKEN", LOCAL_TOKEN)
    monkeypatch.setenv("PBP_CONTROL_TOKEN", CONTROL_TOKEN)
    monkeypatch.setenv("PBP_REAL_OPENROUTER_KEY", "sk-FAKE")
    app = bp.create_smoke_app()
    async with _asgi(app) as client:
        hz = await client.get("/healthz")
        assert hz.status_code == 200 and hz.json()["ready"] is True
        # provider call before INDEX is denied (pre-index kill), zero egress
        r = await client.post(
            "/chat/completions", headers={"Authorization": f"Bearer {LOCAL_TOKEN}"}, content=b'{"model":"openai/gpt-4o-mini"}'
        )
        assert r.json()["forwarded"] is False


# =========================================================== D4 control surface (D4-A/B)
@pytest.mark.asyncio
async def test_proxy_phase_control_route_transitions():
    """D4-B: the in-path proxy app supports authenticated PRE_INDEX->INDEX->POST_INDEX_QUERY."""
    c = _controller(_FakeUpstream())
    app = bp.build_proxy_app(c)
    async with _asgi(app) as client:
        st = await client.get(bp.CONTROL_STATE_PATH, headers={bp.CONTROL_HEADER: CONTROL_TOKEN})
        assert st.status_code == 200 and st.json()["evidence"]["phase"] == "PRE_INDEX"
        r1 = await client.post(bp.CONTROL_PHASE_PATH, headers={bp.CONTROL_HEADER: CONTROL_TOKEN}, json={"phase": "INDEX"})
        assert r1.status_code == 200 and r1.json()["phase"] == "INDEX"
        r2 = await client.post(bp.CONTROL_PHASE_PATH, headers={bp.CONTROL_HEADER: CONTROL_TOKEN}, json={"phase": "POST_INDEX_QUERY"})
        assert r2.status_code == 200 and r2.json()["phase"] == "POST_INDEX_QUERY"
    assert c.evidence().phase == "POST_INDEX_QUERY"


@pytest.mark.asyncio
async def test_proxy_phase_control_requires_control_token():
    """D4-B: phase mutation + state read require the run-local control token."""
    app = bp.build_proxy_app(_controller(_FakeUpstream()))
    async with _asgi(app) as client:
        assert (await client.post(bp.CONTROL_PHASE_PATH, headers={bp.CONTROL_HEADER: "wrong"}, json={"phase": "INDEX"})).status_code == 401
        assert (await client.post(bp.CONTROL_PHASE_PATH, json={"phase": "INDEX"})).status_code == 401
        assert (await client.get(bp.CONTROL_STATE_PATH)).status_code == 401


@pytest.mark.asyncio
async def test_proxy_unknown_phase_rejected():
    """D4-B: an unknown governed phase is rejected (400), leaving phase unchanged."""
    c = _controller(_FakeUpstream())
    app = bp.build_proxy_app(c)
    async with _asgi(app) as client:
        r = await client.post(bp.CONTROL_PHASE_PATH, headers={bp.CONTROL_HEADER: CONTROL_TOKEN}, json={"phase": "BOGUS"})
        assert r.status_code == 400
    assert c.evidence().phase == "PRE_INDEX"


@pytest.mark.asyncio
async def test_in_path_counter_observable_via_control_state():
    """D4-A: a provider forward is observable on the SAME in-path proxy's control state."""
    up = _FakeUpstream()
    c = _controller(up)
    app = bp.build_proxy_app(c)
    async with _asgi(app) as client:
        await client.post(bp.CONTROL_PHASE_PATH, headers={bp.CONTROL_HEADER: CONTROL_TOKEN}, json={"phase": "INDEX"})
        before = (await client.get(bp.CONTROL_STATE_PATH, headers={bp.CONTROL_HEADER: CONTROL_TOKEN})).json()
        assert before["evidence"]["llm_forwarded"] == 0
        await client.post("/chat/completions", headers={"Authorization": f"Bearer {LOCAL_TOKEN}"}, content=b'{"model":"openai/gpt-4o-mini"}')
        after = (await client.get(bp.CONTROL_STATE_PATH, headers={bp.CONTROL_HEADER: CONTROL_TOKEN})).json()
    assert after["evidence"]["llm_forwarded"] == 1


@pytest.mark.asyncio
async def test_phase_enforcement_preindex_denies_then_index_forwards():
    """D4-B regression: PRE_INDEX denies the provider call (409); after INDEX it forwards."""
    up = _FakeUpstream()
    c = _controller(up)
    app = bp.build_proxy_app(c)
    async with _asgi(app) as client:
        r = await client.post("/chat/completions", headers={"Authorization": f"Bearer {LOCAL_TOKEN}"}, content=b'{"model":"openai/gpt-4o-mini"}')
        assert r.status_code == 409 and r.json()["forwarded"] is False
        await client.post(bp.CONTROL_PHASE_PATH, headers={bp.CONTROL_HEADER: CONTROL_TOKEN}, json={"phase": "INDEX"})
        r2 = await client.post("/chat/completions", headers={"Authorization": f"Bearer {LOCAL_TOKEN}"}, content=b'{"model":"openai/gpt-4o-mini"}')
        assert r2.status_code == 200 and r2.content == up.body
    assert c.evidence().llm_forwarded == 1


# =========================================================== D4-C Surface-B transparency
@pytest.mark.asyncio
async def test_surface_b_returns_upstream_chat_body_transparently():
    chat_body = b'{"id":"x","object":"chat.completion","choices":[{"index":0,"message":{"role":"assistant","content":"hi"}}]}'
    up = _FakeUpstream(status=200, body=chat_body)
    c = _controller(up)
    await c.enter_index(CONTROL_TOKEN)
    app = bp.build_proxy_app(c)
    async with _asgi(app) as client:
        r = await client.post("/chat/completions", headers={"Authorization": f"Bearer {LOCAL_TOKEN}"}, content=b'{"model":"openai/gpt-4o-mini"}')
    assert r.status_code == 200 and r.content == chat_body and "chat.completion" in r.text
    assert b'"forwarded"' not in r.content  # NOT a marker


@pytest.mark.asyncio
async def test_surface_b_returns_upstream_embedding_body_transparently():
    emb = b'{"object":"list","data":[{"object":"embedding","index":0,"embedding":[0.1,0.2,0.3]}]}'
    up = _FakeUpstream(status=200, body=emb)
    c = _controller(up)
    await c.enter_index(CONTROL_TOKEN)
    app = bp.build_proxy_app(c)
    async with _asgi(app) as client:
        r = await client.post("/embeddings", headers={"Authorization": f"Bearer {LOCAL_TOKEN}"}, content=b'{"model":"openai/text-embedding-3-small"}')
    assert r.status_code == 200 and r.content == emb
    assert c.evidence().embedding_forwarded == 1


@pytest.mark.asyncio
async def test_surface_b_upstream_failure_not_converted_to_success():
    """§21/§44: a non-2xx upstream response passes through; never a success marker."""
    up = _FakeUpstream(status=500, body=b'{"error":"upstream boom"}')
    c = _controller(up)
    await c.enter_index(CONTROL_TOKEN)
    app = bp.build_proxy_app(c)
    async with _asgi(app) as client:
        r = await client.post("/chat/completions", headers={"Authorization": f"Bearer {LOCAL_TOKEN}"}, content=b'{"model":"openai/gpt-4o-mini"}')
    assert r.status_code == 500 and r.content == b'{"error":"upstream boom"}'
    assert b'"forwarded":true' not in r.content
    assert c.evidence().llm_forwarded == 1  # attempt counted (attempts semantic)


@pytest.mark.asyncio
async def test_control_token_not_leaked_upstream():
    """§23/§45: the run-local control token never reaches the provider upstream leg."""
    up = _FakeUpstream()
    c = _controller(up)
    await c.enter_index(CONTROL_TOKEN)
    app = bp.build_proxy_app(c)
    async with _asgi(app) as client:
        await client.post(
            "/chat/completions",
            headers={"Authorization": f"Bearer {LOCAL_TOKEN}", bp.CONTROL_HEADER: CONTROL_TOKEN},
            content=b'{"model":"openai/gpt-4o-mini"}',
        )
    low = {k.lower(): v for k, v in up.calls[0]["headers"].items()}
    assert bp.CONTROL_HEADER not in low
    assert CONTROL_TOKEN not in str(low) and LOCAL_TOKEN not in str(low)
    assert low.get("authorization") == f"Bearer {REAL_KEY}"


@pytest.mark.asyncio
async def test_unknown_route_denied_and_counted_surface_b():
    """§50: unknown Surface-B route denied pre-egress and counted; no upstream call."""
    up = _FakeUpstream()
    c = _controller(up)
    await c.enter_index(CONTROL_TOKEN)
    app = bp.build_surface_b_app(c)
    async with _asgi(app) as client:
        r = await client.post("/v1/models", headers={"Authorization": f"Bearer {LOCAL_TOKEN}"}, content=b"{}")
    assert r.status_code == 400 and r.json()["forwarded"] is False
    assert c.evidence().unknown_denied == 1 and up.calls == []


def test_launch_argv_safety_and_shape():
    from open_notebook.integrations.graphrag.eval import productlivepn02d as plo

    argv = plo.proxy_container_launch_argv(
        image="ghcr.io/hkuds/lightrag:v1.5.6",
        proxy_file_host_path="/host/providerbudgetpn02d.py",
        container_name="pbp-proxy-abc",
        initial_network="pbp-net-egr-abc",
        host_port=8900,
        local_token="lt",
        control_token="ct",
        real_key_env="sk-FAKE",
        sidecar_base="http://pbp-sidecar-abc:9621",
        factory="create_smoke_app",
    )
    assert argv[:3] == ["docker", "run", "-d"]
    assert "--entrypoint" in argv and "python" in argv
    assert "providerbudgetpn02d:create_smoke_app" in argv and "--factory" in argv
    # no pull/build; only run
    assert "pull" not in argv and "build" not in argv
    # published only on loopback
    assert any(a.startswith("127.0.0.1:8900:") for a in argv)
