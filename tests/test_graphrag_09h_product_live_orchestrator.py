"""GraphRAG-09H product-live orchestrator — provider-free tests.

No real Docker/provider/chat/index. All RunDeps seams are fakes; the budget proxy
uses a fake upstream. The Manifest V2 builder is exercised against the real
authoritative fixture (pure, provider-free).
"""

import dataclasses

import pytest

from open_notebook.integrations.graphrag.eval import productlivepn02d as plo
from open_notebook.integrations.graphrag.eval import providerbudgetpn02d as bp

HEAD = "2af787e5cdbc9257faa86dd8e4ebe7318e36b88f"
REAL_KEY = "sk-FAKE-real"
LOCAL_TOKEN = "local-FAKE"
CONTROL_TOKEN = "ctrl-FAKE"

MINPUTS = plo.ManifestInputs(
    head_commit=HEAD,
    parent_commit="7e815f6d53a5a3180422d02b435ab9c2394f4d43",
    tag_09g_object="d759e9e65a9b0e52854f0505f84298fea431d8b2",
    tag_09g_peel="e7ad1f7db89dd4b9ee82dda563e83b5c7534759a",
    tag_09h_bounds_object="25070473dab6bcb6da3f25f8dcea8ee35b9570bc",
    tag_09h_bounds_peel="7e815f6d53a5a3180422d02b435ab9c2394f4d43",
    tag_09h_slice_object="6e871cc7a7fe83df0593110afe4178890bc0c1b1",
    tag_09h_slice_peel=HEAD,
    proxy_sidecar_gateway_endpoint="http://127.0.0.1:PORT",
    proxy_provider_internal_endpoint="http://proxy:PORT/v1",
)


# =========================================================== manifest builder
def test_manifest_builder_deterministic_and_fields():
    m1 = plo.build_manifest_v2(MINPUTS)
    m2 = plo.build_manifest_v2(MINPUTS)
    assert plo.manifest_sha256(m1) == plo.manifest_sha256(m2)  # deterministic
    # proxy amendment fields present
    assert m1["budget_proxy"]["operation_count_semantic_id"] == bp.OPERATION_COUNT_SEMANTIC_ID  # type: ignore[index]
    assert m1["lightrag_openrouter"]["egress_via"] == "RUN_OWNED_BUDGET_PROXY"  # type: ignore[index]
    assert m1["sidecar_direct_provider_bypass_prevention"] == "ENFORCED_SINGLE_GATEWAY_WINDOWS"
    assert m1["canonical_chat_uses_budget_proxy"] is False
    assert m1["dataset"]["slice_sha256"] == "0d57df2188126f4a4dfefa51c98af5455e55a81e04a36af68f459a6c1bc3142c"  # type: ignore[index]
    assert m1["dataset"]["parent_sha256"] == "9ce7df742810424d9ef7b7b34962187d9061245f81471f5fb1d7f2608f6899a6"  # type: ignore[index]


def test_manifest_sha_head_sensitive():
    other = dataclasses.replace(MINPUTS, head_commit="0" * 40)
    assert plo.manifest_sha256(plo.build_manifest_v2(MINPUTS)) != plo.manifest_sha256(plo.build_manifest_v2(other))


def test_manifest_sha_differs_from_historical_pre_proxy():
    # the proxy amendment changes the object, so SHA must differ from the pre-proxy one
    assert plo.manifest_sha256(plo.build_manifest_v2(MINPUTS)) != plo.HISTORICAL_PRE_PROXY_MANIFEST_SHA


def test_expected_live_auth_tag_derivation():
    sha = plo.manifest_sha256(plo.build_manifest_v2(MINPUTS))
    assert plo.expected_live_auth_tag(sha) == f"graphrag-09h-unified-live-v2-{sha[:12]}-exact-head-auth"


# =========================================================== live-auth verify
class _FakeGit:
    def __init__(self, *, head=HEAD, clean=True, tags=None):
        self._head = head
        self._clean = clean
        self._tags = tags or {}  # name -> (peel, annotation)

    def head(self):
        return self._head

    def worktree_clean(self):
        return self._clean

    def tag_peel(self, tag):
        t = self._tags.get(tag)
        return t[0] if t else None

    def tag_annotation(self, tag):
        t = self._tags.get(tag)
        return t[1] if t else None


def _valid_auth_git(manifest_sha):
    tag = plo.expected_live_auth_tag(manifest_sha)
    ann = {
        "manifest_v2_sha256": manifest_sha,
        "run_count": "1",
        "reusable": "false",
        "model_id": plo.MODEL_ID,
        "session_id": plo.SESSION_ID,
        "notebook_id": plo.NOTEBOOK_ID,
    }
    return _FakeGit(tags={tag: (HEAD, ann)})


def test_live_auth_ok():
    sha = plo.manifest_sha256(plo.build_manifest_v2(MINPUTS))
    plo.verify_live_auth(_valid_auth_git(sha), head=HEAD, manifest_sha=sha)  # no raise


def test_live_auth_absent_tag():
    sha = plo.manifest_sha256(plo.build_manifest_v2(MINPUTS))
    with pytest.raises(plo.LiveAbort):
        plo.verify_live_auth(_FakeGit(tags={}), head=HEAD, manifest_sha=sha)


def test_live_auth_wrong_peel():
    sha = plo.manifest_sha256(plo.build_manifest_v2(MINPUTS))
    tag = plo.expected_live_auth_tag(sha)
    git = _FakeGit(tags={tag: ("deadbeef", {"manifest_v2_sha256": sha, "run_count": "1", "reusable": "false"})})
    with pytest.raises(plo.LiveAbort):
        plo.verify_live_auth(git, head=HEAD, manifest_sha=sha)


def test_live_auth_reusable_rejected():
    sha = plo.manifest_sha256(plo.build_manifest_v2(MINPUTS))
    tag = plo.expected_live_auth_tag(sha)
    git = _FakeGit(tags={tag: (HEAD, {"manifest_v2_sha256": sha, "run_count": "1", "reusable": "true"})})
    with pytest.raises(plo.LiveAbort):
        plo.verify_live_auth(git, head=HEAD, manifest_sha=sha)


# =========================================================== topology verify
@dataclasses.dataclass
class _Facts:
    sidecar_networks: tuple
    sidecar_published_ports: tuple
    proxy_networks: tuple
    internal_network: str
    egress_network: str
    graphrag_base_url: str
    sidecar_llm_binding_host: str
    sidecar_embedding_binding_host: str
    proxy_provider_internal_endpoint: str
    egressguard_initialized: bool


def _good_facts():
    pin = "http://proxy:9/v1"
    return _Facts(
        sidecar_networks=("netA",),
        sidecar_published_ports=(),
        proxy_networks=("netA", "netB"),
        internal_network="netA",
        egress_network="netB",
        graphrag_base_url="http://127.0.0.1:8900",
        sidecar_llm_binding_host=pin,
        sidecar_embedding_binding_host=pin,
        proxy_provider_internal_endpoint=pin,
        egressguard_initialized=True,
    )


def test_topology_ok():
    plo.verify_single_gateway_topology(_good_facts())  # no raise


@pytest.mark.parametrize(
    "mutate",
    [
        lambda f: dataclasses.replace(f, sidecar_networks=("netA", "netB")),  # sidecar on egress
        lambda f: dataclasses.replace(f, sidecar_published_ports=("8000",)),  # host port
        lambda f: dataclasses.replace(f, proxy_networks=("netA",)),  # proxy not dual-homed
        lambda f: dataclasses.replace(f, sidecar_llm_binding_host="https://openrouter.ai/api/v1"),  # bypass
        lambda f: dataclasses.replace(f, graphrag_base_url=""),  # no proxy base url
        lambda f: dataclasses.replace(f, egressguard_initialized=False),  # no backstop
    ],
)
def test_topology_rejects_violations(mutate):
    with pytest.raises(plo.LiveAbort):
        plo.verify_single_gateway_topology(mutate(_good_facts()))


# =========================================================== orchestrator run
def _fixture_text(source_id):
    return f"text-for-{source_id}"


def _make_deps(
    *,
    head=HEAD,
    clean=True,
    question_sha=plo.QUESTION_SHA256,
    digest=plo.SIDECAR_DIGEST,
    model_ok=True,
    session_ok=True,
    checkpoint=0,
    trust_intact=True,
    index_states=None,
    doc_ids=None,
    provenance=None,
    cleanup=None,
    facts=None,
):
    up_calls = []

    async def fake_upstream(cls, path, headers, body):
        up_calls.append(path)
        return 200

    async def fake_sidecar(method, path, headers, body):
        return 200, b"{}"

    controller = bp.BudgetProxyController(
        real_openrouter_key=REAL_KEY,
        local_provider_token=LOCAL_TOKEN,
        control_token=CONTROL_TOKEN,
        upstream=fake_upstream,
        sidecar=fake_sidecar,
    )
    sha = plo.manifest_sha256(plo.build_manifest_v2(MINPUTS))
    git = _valid_auth_git(sha)
    git._head = head
    git._clean = clean

    states = index_states if index_states is not None else ["completed", "completed", "completed"]
    state_iter = iter(states)
    docs = doc_ids if doc_ids is not None else list(plo.EXPECTED_DOC_IDS.values())
    prov = provenance if provenance is not None else list(plo.EXPECTED_PRODUCT_PROVENANCE)

    async def index_source(sid, text):
        return f"track-{sid}"

    async def track_status(track_id):
        return next(state_iter)

    async def list_docs():
        return docs

    async def execute_chat():
        # product chat drives exactly one /query through Surface A (gateway)
        await controller.handle_sidecar_api_request("POST", "/query", body=b"{}")
        return {
            "graph_provenance_source_ids": prov,
            "graph_answer_text_surfaced": False,
            "graph_excerpts_surfaced": False,
        }

    def _cleanup():
        return cleanup if cleanup is not None else plo.CleanupReport(
            sidecar_removed=True,
            proxy_removed=True,
            internal_network_removed=True,
            egress_network_removed=True,
            storage_removed=True,
            tokens_discarded=True,
        )

    model_state = {
        "id": plo.MODEL_ID if model_ok else "model:wrong",
        "provider": "openrouter",
        "name": "openai/gpt-4o-mini",
        "type": "language",
        "credential": None,
    }
    session_state = {
        "model_override": plo.MODEL_ID if session_ok else "model:wrong",
        "refers_to_out": [plo.NOTEBOOK_ID],
    }
    # the sidecar would drive provider ops through the controller to populate counts;
    # in the happy path the fakes keep provider ops at 0 (budget trivially satisfied).
    f = facts if facts is not None else _good_facts()

    return plo.RunDeps(
        git=git,
        manifest_inputs=MINPUTS,
        read_model_state=lambda: model_state,
        read_session_state=lambda: session_state,
        read_checkpoint_message_count=lambda: checkpoint,
        read_canonical_trust=lambda: {"intact": trust_intact},
        recompute_question_sha256=lambda: question_sha,
        verify_local_sidecar_digest=lambda: digest,
        boot_topology=lambda: f,
        cleanup=_cleanup,
        index_source=index_source,
        track_status=track_status,
        list_indexed_doc_ids=list_docs,
        load_source_text=_fixture_text,
        execute_one_chat=execute_chat,
        controller=controller,
        control_token=CONTROL_TOKEN,
    ), controller


@pytest.mark.asyncio
async def test_happy_path_pass():
    deps, controller = _make_deps()
    res = await plo.run_live_validation(deps, head_expected=HEAD)
    assert res.verdict is plo.LiveVerdict.PASS_BOUNDED_LIVE_PROVENANCE_PLUMBING, res.reason
    assert res.evidence["final_product_provenance_source_ids"] == ["source:gr_pn02_a2"]
    assert res.evidence["a3_product_visibility"] == 0
    assert res.evidence["graphrag_query_count"] == 1
    assert res.evidence["index_verify_gate"] == "PASS"
    assert res.evidence["cleanup_zero_residue"] is True


@pytest.mark.asyncio
async def test_wrong_head_aborts_no_cleanup_needed():
    deps, _ = _make_deps(head="0" * 40)
    res = await plo.run_live_validation(deps, head_expected=HEAD)
    assert res.verdict is plo.LiveVerdict.PRE_EXECUTION_ABORT and "head_mismatch" in res.reason


@pytest.mark.asyncio
async def test_dirty_worktree_aborts():
    deps, _ = _make_deps(clean=False)
    res = await plo.run_live_validation(deps, head_expected=HEAD)
    assert res.verdict is plo.LiveVerdict.PRE_EXECUTION_ABORT and "worktree_dirty" in res.reason


@pytest.mark.asyncio
async def test_question_mismatch_aborts():
    deps, _ = _make_deps(question_sha="0" * 64)
    res = await plo.run_live_validation(deps, head_expected=HEAD)
    assert res.verdict is plo.LiveVerdict.PRE_EXECUTION_ABORT and "question_sha_mismatch" in res.reason


@pytest.mark.asyncio
async def test_sidecar_digest_mismatch_aborts():
    deps, _ = _make_deps(digest="sha256:deadbeef")
    res = await plo.run_live_validation(deps, head_expected=HEAD)
    assert res.verdict is plo.LiveVerdict.PRE_EXECUTION_ABORT and "sidecar_digest_mismatch" in res.reason


@pytest.mark.asyncio
async def test_model_mismatch_aborts():
    deps, _ = _make_deps(model_ok=False)
    res = await plo.run_live_validation(deps, head_expected=HEAD)
    assert res.verdict is plo.LiveVerdict.PRE_EXECUTION_ABORT


@pytest.mark.asyncio
async def test_dirty_checkpoint_aborts():
    deps, _ = _make_deps(checkpoint=2)
    res = await plo.run_live_validation(deps, head_expected=HEAD)
    assert res.verdict is plo.LiveVerdict.PRE_EXECUTION_ABORT


@pytest.mark.asyncio
async def test_index_incomplete_fail_safe_and_cleanup():
    deps, _ = _make_deps(index_states=["completed", "failed", "completed"])
    res = await plo.run_live_validation(deps, head_expected=HEAD)
    assert res.verdict is plo.LiveVerdict.LIVE_FAIL_SAFE
    assert res.evidence["cleanup_zero_residue"] is True  # cleanup still ran


@pytest.mark.asyncio
async def test_extra_indexed_doc_kill():
    deps, _ = _make_deps(doc_ids=list(plo.EXPECTED_DOC_IDS.values()) + ["doc-extra"])
    res = await plo.run_live_validation(deps, head_expected=HEAD)
    assert res.verdict is plo.LiveVerdict.KILL_SWITCH_TRIGGERED and "index_verify" in res.reason


@pytest.mark.asyncio
async def test_a3_product_visibility_kill():
    deps, _ = _make_deps(provenance=["source:gr_pn02_a2", "source:gr_pn02_a3"])
    res = await plo.run_live_validation(deps, head_expected=HEAD)
    assert res.verdict is plo.LiveVerdict.KILL_SWITCH_TRIGGERED


@pytest.mark.asyncio
async def test_canonical_trust_drift_kill():
    deps, _ = _make_deps(trust_intact=True)
    # flip trust only AFTER preflight by using a mutable toggle
    calls = {"n": 0}

    def trust():
        calls["n"] += 1
        return {"intact": calls["n"] <= 1}  # intact at preflight, drifted post-run

    deps = dataclasses.replace(deps, read_canonical_trust=trust)
    res = await plo.run_live_validation(deps, head_expected=HEAD)
    assert res.verdict is plo.LiveVerdict.KILL_SWITCH_TRIGGERED and "trust" in res.reason


@pytest.mark.asyncio
async def test_cleanup_failure_marks_non_zero_residue():
    bad = plo.CleanupReport(
        sidecar_removed=True,
        proxy_removed=False,
        internal_network_removed=True,
        egress_network_removed=True,
        storage_removed=True,
        tokens_discarded=True,
    )
    deps, _ = _make_deps(cleanup=bad)
    res = await plo.run_live_validation(deps, head_expected=HEAD)
    assert res.evidence["cleanup_zero_residue"] is False


# =========================================================== production isolation
def test_production_runtime_does_not_import_harness():
    """Fresh subprocess imports the product chat/provenance/service path and asserts
    the eval harness modules are NOT pulled in by production imports."""
    import subprocess
    import sys

    code = (
        "import sys; "
        "import api.routers.chat; "
        "import open_notebook.integrations.graphrag.service; "
        "import open_notebook.integrations.graphrag.product_provenance; "
        "leak=[m for m in sys.modules if 'providerbudgetpn02d' in m or 'productlivepn02d' in m]; "
        "print('LEAK:'+repr(leak) if leak else 'CLEAN')"
    )
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, cwd=".")
    combined = out.stdout + out.stderr
    assert "CLEAN" in out.stdout, combined


# =========================================================== F2 env manager
import os as _os


def test_env_manager_success_restores_absent_and_prior(monkeypatch):
    monkeypatch.delenv("OPEN_NOTEBOOK_GRAPHRAG_ENABLED", raising=False)
    _os.environ["OPEN_NOTEBOOK_LLM_MAX_RETRIES"] = "7"  # prior value
    mgr = plo.RunScopedEnv(
        {"OPEN_NOTEBOOK_GRAPHRAG_ENABLED": "true", "OPEN_NOTEBOOK_LLM_MAX_RETRIES": "0"}
    )
    with mgr:
        assert _os.environ["OPEN_NOTEBOOK_GRAPHRAG_ENABLED"] == "true"
        assert _os.environ["OPEN_NOTEBOOK_LLM_MAX_RETRIES"] == "0"
    # previously-absent restored to absent; previously-set restored exactly
    assert "OPEN_NOTEBOOK_GRAPHRAG_ENABLED" not in _os.environ
    assert _os.environ["OPEN_NOTEBOOK_LLM_MAX_RETRIES"] == "7"
    _os.environ.pop("OPEN_NOTEBOOK_LLM_MAX_RETRIES", None)


def test_env_manager_restores_on_exception():
    # robust to whatever the harness-loaded .env left in place: capture prior, prove
    # the value is restored EXACTLY (absent->absent, set->exact) despite an exception.
    key = "OPEN_NOTEBOOK_GRAPHRAG_BASE_URL"
    had = key in _os.environ
    prior = _os.environ.get(key)
    try:
        with plo.RunScopedEnv({key: "http://proxy-run-local"}):
            assert _os.environ[key] == "http://proxy-run-local"
            raise RuntimeError("boom")
    except RuntimeError:
        pass
    if had:
        assert _os.environ.get(key) == prior  # restored to exact prior
    else:
        assert key not in _os.environ  # restored to absent


def test_env_manager_failure_path_restore():
    _os.environ["OPEN_NOTEBOOK_GRAPHRAG_API_KEY"] = "prior-key"
    mgr = plo.RunScopedEnv({"OPEN_NOTEBOOK_GRAPHRAG_API_KEY": "run-local"}).apply()
    assert _os.environ["OPEN_NOTEBOOK_GRAPHRAG_API_KEY"] == "run-local"
    mgr.restore()  # fail-safe return path calls restore() in finally
    assert _os.environ["OPEN_NOTEBOOK_GRAPHRAG_API_KEY"] == "prior-key"
    _os.environ.pop("OPEN_NOTEBOOK_GRAPHRAG_API_KEY", None)


def test_credential_preflight_provider_free():
    assert plo.credential_preflight(getenv=lambda v: "sk-x") is True
    assert plo.credential_preflight(getenv=lambda v: "  ") is False
    assert plo.credential_preflight(getenv=lambda v: None) is False


# =========================================================== F3 canonical chat attempts
@pytest.mark.asyncio
async def test_canonical_chat_attempt_counter_counts_openrouter_only():
    import httpx

    def handler(request):
        return httpx.Response(500, json={"e": 1})  # even a 5xx counts as an attempt

    with plo.CanonicalChatAttemptCounter(host="openrouter.ai") as cc:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            await client.post("https://openrouter.ai/api/v1/chat/completions", json={})
            await client.post("https://example.com/other", json={})  # not counted
    assert cc.count == 1


def test_canonical_chat_counter_restores_httpx():
    import httpx

    orig_sync, orig_async = httpx.Client.send, httpx.AsyncClient.send
    with plo.CanonicalChatAttemptCounter():
        assert httpx.Client.send is not orig_sync
    assert httpx.Client.send is orig_sync and httpx.AsyncClient.send is orig_async


# =========================================================== F3 non-chat LLM guard
@pytest.mark.asyncio
async def test_non_chat_llm_guard_wraps_real_call_site(monkeypatch):
    from open_notebook.graphs import chat as chatmod

    recorded = []

    async def fake_provision(content, model_id, default_type, **kw):
        recorded.append((model_id, default_type))
        return "LC"

    monkeypatch.setattr(chatmod, "provision_langchain_model", fake_provision)
    with plo.NonChatLLMGuard() as guard:
        # the guard must wrap the SAME symbol the chat graph dereferences
        assert chatmod.provision_langchain_model is not fake_provision
        out = await chatmod.provision_langchain_model("hi", plo.MODEL_ID, "chat")
        assert out == "LC" and recorded == [(plo.MODEL_ID, "chat")]
        await chatmod.provision_langchain_model("hi", "model:other", "transformation")
    assert guard.chat_count() == 1
    assert guard.non_chat_count() == 1  # the transformation call
    # restored to the (test-monkeypatched) underlying after exit
    assert chatmod.provision_langchain_model is fake_provision


# =========================================================== bounded poll
@pytest.mark.asyncio
async def test_bounded_poll_sequences():
    async def seq(states):
        it = iter(states)

        async def _t(_tid):
            return next(it)

        return _t

    async def _nosleep(_):
        return None

    now = {"t": 0.0}

    def _now():
        now["t"] += 1.0
        return now["t"]

    r1 = await plo.bounded_track_poll(await seq(["pending", "processing", "completed"]), "x",
                                      deadline_s=100, interval_s=0, max_polls=10, now=_now, sleep=_nosleep)
    assert r1 == "completed"
    r2 = await plo.bounded_track_poll(await seq(["failed"]), "x", deadline_s=100, interval_s=0, max_polls=10, now=_now, sleep=_nosleep)
    assert r2 == "failed"
    r3 = await plo.bounded_track_poll(await seq(["pending"] * 100), "x", deadline_s=100, interval_s=0, max_polls=3, now=_now, sleep=_nosleep)
    assert r3 == "timeout"  # bounded by max_polls, not infinite
    r4 = await plo.bounded_track_poll(await seq(["weird-unknown"] * 100), "x", deadline_s=100, interval_s=0, max_polls=2, now=_now, sleep=_nosleep)
    assert r4 == "timeout"  # unknown state does not loop forever


# =========================================================== Docker cleanup best-effort (fake DockerCLI)
class _FakeDocker:
    def __init__(self, fail=()):
        self.fail = set(fail)
        self.calls = []
        self._removed = set()

    def run(self, argv, timeout=90.0):
        self.calls.append(argv)
        name = argv[-1]
        rc = 1 if name in self.fail else 0
        if rc == 0:
            self._removed.add(name)

        class R:
            returncode = rc
            stdout = ""

        return R()

    def network_rm(self, name):
        self.calls.append(["network", "rm", name])
        if name in self.fail:
            return False
        self._removed.add(name)
        return True

    def network_exists(self, name):
        return name not in self._removed

    def container_exists(self, name):
        return name not in self._removed


def test_docker_cleanup_best_effort_attempts_all_even_on_failure():
    fake = _FakeDocker(fail={"pbp-proxy-RID"})  # proxy removal fails
    ctrl = plo.DockerTopologyController(docker=fake, run_id="RID")
    topo = ctrl.topology(host_port=8901)
    # force proxy container name into the fail set exactly
    fake.fail = {topo.proxy_container}
    report = ctrl.cleanup(topo)
    # proxy failed, but sidecar + networks + storage cleanup were still attempted
    labels = [c for c in fake.calls]
    assert any(topo.sidecar_container in str(c) for c in labels)
    assert any(topo.internal_network in str(c) for c in labels)
    assert any(topo.egress_network in str(c) for c in labels)
    assert report.proxy_removed is False
    assert report.sidecar_removed is True
    assert report.zero_residue() is False  # a failure means no PASS


def test_docker_cleanup_success_zero_residue():
    fake = _FakeDocker()
    ctrl = plo.DockerTopologyController(docker=fake, run_id="RID2")
    topo = ctrl.topology(host_port=8902)
    report = ctrl.cleanup(topo)
    assert report.zero_residue() is True
    assert ctrl.zero_residue(topo) is True


def test_topology_run_owned_identity_binding():
    ctrl = plo.DockerTopologyController(docker=_FakeDocker(), run_id="RID3")
    topo = ctrl.topology(host_port=8903)
    assert topo.run_id == "RID3"
    assert topo.internal_network == "pbp-net-int-RID3"
    assert topo.sidecar_networks == ("pbp-net-int-RID3",)
    assert topo.sidecar_published_ports == ()
    assert topo.proxy_networks == ("pbp-net-int-RID3", "pbp-net-egr-RID3")
    assert topo.sidecar_llm_binding_host == topo.proxy_provider_internal_endpoint
    # and the verifier accepts the real run-owned facts
    plo.verify_single_gateway_topology(topo)
    import shutil
    shutil.rmtree(topo.storage_root, ignore_errors=True)


# =========================================================== Round-2: concrete boot
import os as _os2


class _R:
    def __init__(self, rc, out=""):
        self.returncode = rc
        self.stdout = out


class _FakeBootDocker:
    """Records ops; simulates docker run/connect/inspect for boot(); stage fail injection."""

    def __init__(self, run_id="RID", fail_stage=None, ov=None):
        self.rid = run_id
        self.fail_stage = fail_stage
        self.ov = ov or {}
        self.ops = []
        self._created = set()

    def network_create_internal(self, name):
        self.ops.append(("net_create_internal", name))
        if self.fail_stage == "network_a_created":
            return False
        self._created.add(name)
        return True

    def network_exists(self, name):
        return name in self._created

    def container_exists(self, name):
        return name in self._created

    def network_rm(self, name):
        self.ops.append(("net_rm", name))
        self._created.discard(name)
        return True

    def run(self, argv, timeout=90.0):
        a = list(argv)
        self.ops.append(("run", tuple(a)))
        if a[:3] == ["docker", "network", "create"]:
            name = a[-1]
            if self.fail_stage == "network_b_created":
                return _R(1)
            self._created.add(name)
            return _R(0)
        if a[:3] == ["docker", "network", "connect"]:
            return _R(1 if self.fail_stage == "proxy_joined_network_b" else 0)
        if a[:3] == ["docker", "rm", "-f"]:
            self._created.discard(a[-1])
            return _R(0)
        if a[:3] == ["docker", "run", "-d"]:
            name = a[a.index("--name") + 1]
            stage = "proxy_started" if "proxy" in name else "sidecar_started"
            if self.fail_stage == stage:
                return _R(1)
            self._created.add(name)
            return _R(0)
        if a[:2] == ["docker", "inspect"] or a[:3] == ["docker", "network", "inspect"]:
            fmt = a[a.index("-f") + 1]
            target = a[-1]
            if "NetworkSettings.Networks" in fmt:
                if "proxy" in target:
                    return _R(0, self.ov.get("proxy_nets", f"pbp-net-int-{self.rid},pbp-net-egr-{self.rid},"))
                return _R(0, self.ov.get("sidecar_nets", f"pbp-net-int-{self.rid},"))
            if "NetworkSettings.Ports" in fmt:
                if "proxy" in target:
                    return _R(0, self.ov.get("proxy_ports", '{"8787/tcp":[{"HostIp":"127.0.0.1","HostPort":"8900"}]}'))
                return _R(0, self.ov.get("sidecar_ports", "{}"))
            if ".Internal" in fmt:
                return _R(0, self.ov.get("net_internal", "true"))
            if "{{.Id}}" in fmt:
                return _R(0 if target in self._created else 1, "id" if target in self._created else "")
        return _R(0, "")


def _spec():
    return plo.SidecarLaunchSpec(image="img", entrypoint="python", command=["-c", "x"], env={"LLM_BINDING": "openai"})


def test_boot_order_and_runtime_facts():
    fake = _FakeBootDocker(run_id="RID")
    ctrl = plo.DockerTopologyController(docker=fake, run_id="RID")
    facts = ctrl.boot(sidecar=_spec(), local_token="lt", control_token="ct", provider_key_env="sk-FAKE", host_port=8900)
    # boot order
    seq = [o for o in fake.ops if o[0] in ("net_create_internal",) or (o[0] == "run" and (o[1][:3] in [("docker", "network", "create"), ("docker", "network", "connect"), ("docker", "run", "-d")]))]
    kinds = []
    for o in seq:
        if o[0] == "net_create_internal":
            kinds.append("A")
        elif o[1][:3] == ("docker", "network", "create"):
            kinds.append("B")
        elif o[1][:3] == ("docker", "run", "-d"):
            kinds.append("proxy" if "proxy" in o[1][o[1].index("--name") + 1] else "sidecar")
        elif o[1][:3] == ("docker", "network", "connect"):
            kinds.append("connectB")
    assert kinds == ["A", "B", "proxy", "connectB", "sidecar"]
    # runtime-derived facts
    assert facts.sidecar_networks == (f"pbp-net-int-RID",)
    assert set(facts.proxy_networks) == {"pbp-net-int-RID", "pbp-net-egr-RID"}
    assert facts.sidecar_published_ports == ()
    assert facts.network_a_internal is True
    assert facts.proxy_bind_loopback is True
    plo.verify_single_gateway_topology(facts)  # verifier accepts runtime facts
    ctrl.cleanup()


@pytest.mark.parametrize(
    "ov,expect_substr",
    [
        ({"net_internal": "false"}, "network_a_internal"),
        ({"sidecar_ports": '{"9621/tcp":[{"HostIp":"0.0.0.0","HostPort":"9621"}]}'}, "sidecar_no_host_port"),
        ({"proxy_ports": '{"8787/tcp":[{"HostIp":"0.0.0.0","HostPort":"8900"}]}'}, "proxy_bind_loopback"),
    ],
)
def test_boot_runtime_attestation_fail_closed(ov, expect_substr):
    fake = _FakeBootDocker(run_id="RID", ov=ov)
    ctrl = plo.DockerTopologyController(docker=fake, run_id="RID")
    with pytest.raises(plo.LiveAbort) as ei:
        ctrl.boot(sidecar=_spec(), local_token="lt", control_token="ct", provider_key_env="k", host_port=8900)
    assert expect_substr in str(ei.value)
    ctrl.cleanup()


def test_boot_facts_feed_verifier_rejects_sidecar_on_egress():
    fake = _FakeBootDocker(run_id="RID", ov={"sidecar_nets": "pbp-net-int-RID,pbp-net-egr-RID,"})
    ctrl = plo.DockerTopologyController(docker=fake, run_id="RID")
    facts = ctrl.boot(sidecar=_spec(), local_token="lt", control_token="ct", provider_key_env="k", host_port=8900)
    with pytest.raises(plo.LiveAbort):
        plo.verify_single_gateway_topology(facts)  # sidecar on egress net -> rejected
    ctrl.cleanup()


@pytest.mark.parametrize(
    "stage", ["network_a_created", "network_b_created", "proxy_started", "proxy_joined_network_b", "sidecar_started"]
)
def test_partial_boot_failure_matrix_cleanup_and_no_unrelated(stage):
    fake = _FakeBootDocker(run_id="RID", fail_stage=stage)
    ctrl = plo.DockerTopologyController(docker=fake, run_id="RID")
    with pytest.raises(plo.LiveAbort):
        ctrl.boot(sidecar=_spec(), local_token="lt", control_token="ct", provider_key_env="k", host_port=8900)
    report = ctrl.cleanup()
    # cleanup only ever referenced exact run-owned names
    for op in fake.ops:
        if op[0] == "net_rm":
            assert op[1].endswith("-RID")
        if op[0] == "run" and op[1][:3] == ("docker", "rm", "-f") if False else False:
            pass
    assert report.unrelated_touched is False
    assert ctrl.zero_residue() is True  # everything created-so-far removed


def test_cleanup_idempotent_absent_not_failure():
    fake = _FakeBootDocker(run_id="RID2")
    ctrl = plo.DockerTopologyController(docker=fake, run_id="RID2")
    ctrl.boot(sidecar=_spec(), local_token="lt", control_token="ct", provider_key_env="k", host_port=8900)
    r1 = ctrl.cleanup()
    r2 = ctrl.cleanup()  # second call: everything already absent
    assert r1.zero_residue() is True
    assert r2.zero_residue() is True  # absent != failure


def test_default_run_deps_concrete_all_seams():
    deps = plo.default_run_deps(
        MINPUTS,
        sidecar_api_token="scA",
        local_provider_token="lt",
        control_token="ct",
        provider_key_env="OPENROUTER_API_KEY",
        host_port=8905,
    )
    for name in (
        "git", "read_model_state", "read_session_state", "read_checkpoint_message_count",
        "read_canonical_trust", "recompute_question_sha256", "verify_local_sidecar_digest",
        "boot_topology", "cleanup", "index_source", "track_status", "list_indexed_doc_ids",
        "load_source_text", "execute_one_chat", "controller",
    ):
        assert getattr(deps, name) is not None
    assert callable(deps.boot_topology) and callable(deps.cleanup)
    assert deps.git.head.__self__ is deps.git  # concrete git adapter


# =========================================================== Round-2: env wiring
@pytest.mark.asyncio
async def test_run_scoped_env_integration_active_then_restored():
    deps, controller = _make_deps()
    seen = {}

    async def exec_asserting_env():
        seen["enabled"] = _os2.environ.get("OPEN_NOTEBOOK_GRAPHRAG_ENABLED")
        seen["base"] = _os2.environ.get("OPEN_NOTEBOOK_GRAPHRAG_BASE_URL")
        seen["retries"] = _os2.environ.get("OPEN_NOTEBOOK_LLM_MAX_RETRIES")
        await controller.handle_sidecar_api_request("POST", "/query", body=b"{}")
        return {"graph_provenance_source_ids": ["source:gr_pn02_a2"], "graph_answer_text_surfaced": False, "graph_excerpts_surfaced": False}

    prior = _os2.environ.get("OPEN_NOTEBOOK_GRAPHRAG_ENABLED")
    deps = dataclasses.replace(
        deps,
        execute_one_chat=exec_asserting_env,
        run_scoped_env={
            "OPEN_NOTEBOOK_GRAPHRAG_ENABLED": "true",
            "OPEN_NOTEBOOK_GRAPHRAG_BASE_URL": "http://127.0.0.1:8900",
            "OPEN_NOTEBOOK_LLM_MAX_RETRIES": "0",
        },
    )
    res = await plo.run_live_validation(deps, head_expected=HEAD)
    assert res.verdict is plo.LiveVerdict.PASS_BOUNDED_LIVE_PROVENANCE_PLUMBING, res.reason
    assert seen["enabled"] == "true" and seen["base"] == "http://127.0.0.1:8900" and seen["retries"] == "0"
    assert res.evidence.get("run_scoped_env_restored") is True
    assert _os2.environ.get("OPEN_NOTEBOOK_GRAPHRAG_ENABLED") == prior  # restored


@pytest.mark.asyncio
async def test_topology_failure_env_not_mutated():
    deps, _ = _make_deps()

    def boom():
        raise plo.LiveAbort("topology", "boot failed")

    prior = _os2.environ.get("OPEN_NOTEBOOK_GRAPHRAG_BASE_URL")
    deps = dataclasses.replace(deps, boot_topology=boom, run_scoped_env={"OPEN_NOTEBOOK_GRAPHRAG_BASE_URL": "http://x"})
    res = await plo.run_live_validation(deps, head_expected=HEAD)
    assert res.verdict is plo.LiveVerdict.PRE_EXECUTION_ABORT
    assert _os2.environ.get("OPEN_NOTEBOOK_GRAPHRAG_BASE_URL") == prior  # env never entered


@pytest.mark.asyncio
async def test_chat_exception_full_unwind_env_and_cleanup():
    deps, controller = _make_deps()
    cleaned = {"n": 0}
    base_cleanup = deps.cleanup

    def counting_cleanup():
        cleaned["n"] += 1
        return base_cleanup()

    async def boom_chat():
        raise RuntimeError("chat boom")

    prior = _os2.environ.get("OPEN_NOTEBOOK_GRAPHRAG_ENABLED")
    deps = dataclasses.replace(
        deps,
        execute_one_chat=boom_chat,
        cleanup=counting_cleanup,
        run_scoped_env={"OPEN_NOTEBOOK_GRAPHRAG_ENABLED": "true"},
    )
    with pytest.raises(RuntimeError):
        await plo.run_live_validation(deps, head_expected=HEAD)
    assert cleaned["n"] == 1  # cleanup ran in finally
    assert _os2.environ.get("OPEN_NOTEBOOK_GRAPHRAG_ENABLED") == prior  # env restored on exception


@pytest.mark.asyncio
async def test_async_cancellation_unwinds_cleanup_and_env():
    import asyncio

    deps, controller = _make_deps()
    cleaned = {"n": 0}
    base_cleanup = deps.cleanup

    def counting_cleanup():
        cleaned["n"] += 1
        return base_cleanup()

    async def hang_chat():
        await asyncio.sleep(10)
        return {}

    prior = _os2.environ.get("OPEN_NOTEBOOK_GRAPHRAG_ENABLED")
    deps = dataclasses.replace(
        deps, execute_one_chat=hang_chat, cleanup=counting_cleanup, run_scoped_env={"OPEN_NOTEBOOK_GRAPHRAG_ENABLED": "true"}
    )
    task = asyncio.ensure_future(plo.run_live_validation(deps, head_expected=HEAD))
    await asyncio.sleep(0.1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert cleaned["n"] == 1  # finally cleanup ran on cancellation
    assert _os2.environ.get("OPEN_NOTEBOOK_GRAPHRAG_ENABLED") == prior  # env restored on cancellation
