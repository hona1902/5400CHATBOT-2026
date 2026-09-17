"""PN02D-B1-EW5 — sanitized provider-error observability tests.

EVALUATION-ONLY, ZERO provider traffic. Proves the EXEC #6 diagnosis gap (a live provider
embedding failure reaching the content-safe CLI as only ``error_type = RuntimeError``) is
closed by a SAFE-BY-CONSTRUCTION structured diagnostic: fixed-vocabulary provider error
class, structured-only HTTP status, allowlisted error code, observed retryability, and a
request-reached tri-state — never a raw message/body/header/secret.

Also covers Remediation #1: the repository-owned safe OpenRouter embedding boundary that
preserves structured HTTP status before esperanto flattening (canonical 401-vs-404 distinct),
the completed EW6→EW7 governance repoint (current approved checkpoint == EW7, EW6/EW5 historical),
and the EW7 successor-checkpoint lifecycle (State A/B). No provider/model change, no
retry-behavior change, no OpenRouter calls.
"""

from __future__ import annotations

import json

import graphrag_pn02db0cb_common as C
import httpx
import pytest

import open_notebook.utils.embedding as emb
from open_notebook.integrations.graphrag.eval import authb1r2pn02d as B
from open_notebook.integrations.graphrag.eval import cli_live_pn02d as cli
from open_notebook.integrations.graphrag.eval.authlivepn02d import (
    PN02_PROVIDER_RUN_AUTHORIZED,
)
from open_notebook.integrations.graphrag.eval.authmintlivepn02d import (
    EXPECTED_B1_R2_CHECKPOINT_TAG,
    EXPECTED_EW1_CHECKPOINT_TAG,
    EXPECTED_EW2_CHECKPOINT_TAG,
    EXPECTED_EW3_CHECKPOINT_TAG,
    EXPECTED_EW4_CHECKPOINT_TAG,
    EXPECTED_EW5_CHECKPOINT_TAG,
    EXPECTED_EW6_CHECKPOINT_TAG,
    EXPECTED_EW7_CHECKPOINT_TAG,
    EXPECTED_PF1_CHECKPOINT_TAG,
    RealTrustedB1R2Reader,
    current_approved_b1_r2_checkpoint,
    verify_b1_r2_checkpoint,
)
from open_notebook.utils import provider_errors as pe

# Dangerous strings that must NEVER appear in any serialized diagnostic.
_SECRETISH = [
    "sk-or-v1-0123456789abcdef0123456789abcdef0123456789abcdef",
    "Bearer sk-or-abcdefghijklmnopqrstuvwxyz012345",
    "Authorization: Bearer sk-or-secrettoken",
    "OPENROUTER_API_KEY=sk-or-supersecretvalue",
]


def _req() -> httpx.Request:
    return httpx.Request("POST", "https://openrouter.ai/api/v1/embeddings")


def _http_status_error(status: int, *, body: str = "") -> httpx.HTTPStatusError:
    resp = httpx.Response(status, request=_req(), text=body)
    return httpx.HTTPStatusError(f"HTTP {status}", request=_req(), response=resp)


def _no_secret(obj: object) -> None:
    # Assert no actual credential-leak FORMS appear (fixed vocabulary labels such as
    # "authorization_error"/"authentication_error" are safe and expected).
    low = json.dumps(obj).lower()
    assert "sk-or-" not in low
    assert "bearer sk" not in low
    assert "bearer " not in low
    assert "authorization:" not in low
    assert "openrouter_api_key" not in low
    assert "api_key=" not in low


# --------------------------------------------------------------------------- #
# §9/§10/§20-§25 — structured HTTP-status classification (from attributes only)
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize(
    "status,expected_class,expected_retry",
    [
        (401, pe.AUTHENTICATION_ERROR, pe.NON_RETRYABLE),
        (403, pe.AUTHORIZATION_ERROR, pe.NON_RETRYABLE),
        (404, pe.NOT_FOUND, pe.NON_RETRYABLE),
        (400, pe.BAD_REQUEST, pe.NON_RETRYABLE),
        (429, pe.RATE_LIMIT, pe.RETRYABLE),
        (500, pe.SERVER_ERROR, pe.RETRYABLE),
        (503, pe.SERVER_ERROR, pe.RETRYABLE),
    ],
)
def test_http_status_classifies_structured(status, expected_class, expected_retry):
    # A secret-bearing response body must NOT change the safe structured result or leak.
    exc = _http_status_error(status, body="Bearer sk-or-secret leaked body")
    diag = pe.classify_provider_error(exc, operation="embedding")
    d = diag.as_public_dict()
    assert d["operation"] == "embedding"
    assert d["provider_error_class"] == expected_class
    assert d["provider_http_status"] == status  # structured attribute only
    assert d["retryability"] == expected_retry
    assert d["provider_request_reached"] == pe.REACHED_YES
    _no_secret(d)


# --------------------------------------------------------------------------- #
# §26 — network / timeout families (structured status absent)
# --------------------------------------------------------------------------- #

def test_timeout_classification():
    diag = pe.classify_provider_error(
        httpx.ReadTimeout("timed out", request=_req()), operation="embedding"
    )
    d = diag.as_public_dict()
    assert d["provider_error_class"] == pe.TIMEOUT
    assert d["provider_http_status"] is None
    assert d["retryability"] == pe.RETRYABLE
    assert d["provider_request_reached"] == pe.REACHED_YES


def test_network_error_classification():
    diag = pe.classify_provider_error(
        httpx.ConnectError("connection refused", request=_req()), operation="embedding"
    )
    d = diag.as_public_dict()
    assert d["provider_error_class"] == pe.NETWORK_ERROR
    assert d["provider_http_status"] is None
    assert d["retryability"] == pe.RETRYABLE
    assert d["provider_request_reached"] == pe.REACHED_NO


# --------------------------------------------------------------------------- #
# §27 — unknown / opaque provider error (e.g. esperanto flattened RuntimeError)
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("msg", _SECRETISH)
def test_opaque_runtime_error_safe_fallback(msg):
    # A bare RuntimeError carrying a secret-bearing provider message must classify UNKNOWN
    # with null status and NEVER leak the message (we never read it).
    diag = pe.classify_provider_error(
        RuntimeError(f"OpenRouter API error: HTTP 401: {msg}"), operation="embedding"
    )
    d = diag.as_public_dict()
    assert d["provider_error_class"] == pe.UNKNOWN_PROVIDER_ERROR
    assert d["provider_http_status"] is None  # NOT parsed from the message text
    assert d["provider_request_reached"] == pe.REACHED_UNKNOWN
    assert d["provider_error_code"] is None
    _no_secret(d)


# --------------------------------------------------------------------------- #
# §11/§28 — provider error-code allowlisting
# --------------------------------------------------------------------------- #

def test_error_code_safe_retained():
    class _E(Exception):
        code = "invalid_api_key"  # a descriptive code, not a credential

    diag = pe.classify_provider_error(_E(), operation="embedding")
    assert diag.provider_error_code == "invalid_api_key"


@pytest.mark.parametrize(
    "bad_code",
    [
        "sk-or-v1-deadbeefdeadbeefdeadbeefdeadbeef",  # real token
        "Bearer sk-or-secret",  # spaces + token
        "https://openrouter.ai/x?key=sk-or-abc",  # url + credential
        "x" * 80,  # long token
        "has spaces here",  # whitespace-heavy
    ],
)
def test_error_code_unsafe_dropped(bad_code):
    class _E(Exception):
        code = bad_code

    diag = pe.classify_provider_error(_E(), operation="embedding")
    assert diag.provider_error_code is None
    _no_secret(diag.as_public_dict())


# --------------------------------------------------------------------------- #
# §7/§14/§18/§19 — safe-by-construction: no raw message ever, secret defense
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("secret", _SECRETISH)
def test_no_secret_pattern_in_any_serialized_diagnostic(secret):
    for exc in (
        _http_status_error(401, body=secret),
        httpx.ConnectError(secret, request=_req()),
        RuntimeError(secret),
    ):
        d = pe.classify_provider_error(exc, operation="embedding").as_public_dict()
        _no_secret(d)


def test_diagnostic_public_dict_has_only_allowlisted_keys():
    d = pe.classify_provider_error(_http_status_error(401), operation="embedding").as_public_dict()
    assert set(d.keys()) == {
        "operation",
        "provider_name",
        "provider_error_class",
        "provider_http_status",
        "provider_error_code",
        "retryability",
        "provider_request_reached",
    }


# --------------------------------------------------------------------------- #
# §15 — attach / extract across the exception cause chain
# --------------------------------------------------------------------------- #

def test_attach_and_extract_through_cause_chain():
    original = _http_status_error(404)
    diag = pe.classify_provider_error(original, operation="embedding")
    wrapped = RuntimeError("wrapped")
    pe.attach_provider_diagnostic(wrapped, diag)
    outer = RuntimeError("outer")
    outer.__cause__ = wrapped
    found = pe.extract_attached_diagnostic(outer)
    assert found is not None and found.provider_error_class == pe.NOT_FOUND


# --------------------------------------------------------------------------- #
# §30/§31 — generate_embeddings integration (attaches diagnostic; preserves cause)
# --------------------------------------------------------------------------- #

class _FakeEmbeddingModel:
    model_name = "openai/text-embedding-3-small"

    def __init__(self, *, raiser=None, dim=1536):
        self._raiser = raiser
        self._dim = dim

    async def aembed(self, texts):
        if self._raiser is not None:
            raise self._raiser()
        return [[0.0] * self._dim for _ in texts]


@pytest.fixture
def _fast_retry(monkeypatch):
    # Keep the 3-attempt retry behavior (unchanged) but remove the sleep so tests are fast.
    monkeypatch.setattr(emb, "EMBEDDING_RETRY_DELAY", 0)


def _patch_model(monkeypatch, model):
    from open_notebook.ai import models as models_mod

    async def _get(**_kw):
        return model

    monkeypatch.setattr(models_mod.model_manager, "get_embedding_model", _get)


@pytest.mark.asyncio
async def test_generate_embeddings_attaches_structured_diagnostic_on_http_401(
    monkeypatch, _fast_retry
):
    _patch_model(monkeypatch, _FakeEmbeddingModel(raiser=lambda: _http_status_error(401)))
    with pytest.raises(RuntimeError) as ei:
        await emb.generate_embeddings(["hello"], command_id="ew5")
    diag = pe.extract_attached_diagnostic(ei.value)
    assert diag is not None
    assert diag.operation == "embedding"
    assert diag.provider_error_class == pe.AUTHENTICATION_ERROR
    assert diag.provider_http_status == 401
    assert diag.provider_request_reached == pe.REACHED_YES
    assert ei.value.__cause__ is not None  # cause preserved


@pytest.mark.asyncio
async def test_generate_embeddings_opaque_provider_error_is_unknown(monkeypatch, _fast_retry):
    def _raise():
        raise RuntimeError("OpenRouter API error: HTTP 404: Bearer sk-or-x")

    _patch_model(monkeypatch, _FakeEmbeddingModel(raiser=_raise))
    with pytest.raises(RuntimeError) as ei:
        await emb.generate_embeddings(["hello"], command_id="ew5")
    diag = pe.extract_attached_diagnostic(ei.value)
    assert diag is not None
    assert diag.provider_error_class == pe.UNKNOWN_PROVIDER_ERROR
    assert diag.provider_http_status is None
    _no_secret(diag.as_public_dict())


@pytest.mark.asyncio
async def test_generate_embeddings_success_path_1536(monkeypatch):
    _patch_model(monkeypatch, _FakeEmbeddingModel(dim=1536))
    out = await emb.generate_embeddings(["hello world"], command_id="ew5")
    assert len(out) == 1 and len(out[0]) == 1536


# --------------------------------------------------------------------------- #
# §16/§29 — content-safe CLI serialization surfaces the safe provider diagnostic
# --------------------------------------------------------------------------- #

def _write_manifest(tmp_path, grant) -> str:
    manifest = {
        "run_id": grant.run_id,
        "fixture_hash": grant.fixture_hash,
        "implementation_checkpoint_commit": grant.implementation_checkpoint_commit,
        "implementation_checkpoint_tag": grant.implementation_checkpoint_tag,
        "b1_r2_checkpoint": grant.b1_r2_checkpoint,
        "provider_config_fingerprint": grant.provider_config_fingerprint,
        "workload_caps": dict(grant.workload_caps),
        "operation_allowlist": sorted(grant.operation_allowlist),
        "approved_git_commit": grant.approved_git_commit,
        "approved_git_tag": grant.approved_git_tag,
        "synthetic_only": grant.synthetic_only,
        "real_internal_data_allowed": grant.real_internal_data_allowed,
    }
    p = tmp_path / "grant.json"
    p.write_text(json.dumps(manifest), encoding="utf-8")
    return str(p)


def test_cli_failed_payload_includes_safe_provider_error(tmp_path):
    grant = C.frozen_test_grant()
    path = _write_manifest(tmp_path, grant)

    def _raising_runner(**_kw):
        # Mimic generate_embeddings' final wrap: a RuntimeError carrying the attached
        # sanitized diagnostic (operation=embedding), original secret-bearing cause chained.
        diag = pe.classify_provider_error(
            _http_status_error(401, body="Bearer sk-or-secret"), operation="embedding"
        )
        err = RuntimeError("Failed to generate embeddings using model 'x': <cause>")
        pe.attach_provider_diagnostic(err, diag)
        raise err

    with C.approved_b1r2_governance():
        code, payload = cli._evaluate_execute_b1_live_composed(
            manifest_path=path,
            explicit_authorize=True,
            env={"PN02_PROVIDER_RUN_AUTHORIZED": "YES", "OPENROUTER_API_KEY": "dummy"},
            git_baseline_reader=lambda: C.clean_git_baseline(),
            fixture_hash_reader=C.verify_fixture_hash,
            live_runner=_raising_runner,
        )

    assert code == 4
    assert payload["result"] == "FAILED"
    assert payload["error_type"] == "RuntimeError"
    perr = payload["provider_error"]
    assert perr["operation"] == "embedding"
    assert perr["provider_error_class"] == pe.AUTHENTICATION_ERROR
    assert perr["provider_http_status"] == 401
    assert perr["retryability"] == pe.NON_RETRYABLE
    assert perr["provider_request_reached"] == pe.REACHED_YES
    _no_secret(payload)  # whole payload is secret-free


def test_cli_non_provider_error_still_safe(tmp_path):
    grant = C.frozen_test_grant()
    path = _write_manifest(tmp_path, grant)

    def _raising_runner(**_kw):
        raise RuntimeError("some non-provider internal failure")

    with C.approved_b1r2_governance():
        code, payload = cli._evaluate_execute_b1_live_composed(
            manifest_path=path,
            explicit_authorize=True,
            env={"PN02_PROVIDER_RUN_AUTHORIZED": "YES", "OPENROUTER_API_KEY": "dummy"},
            git_baseline_reader=lambda: C.clean_git_baseline(),
            fixture_hash_reader=C.verify_fixture_hash,
            live_runner=_raising_runner,
        )
    assert code == 4
    perr = payload["provider_error"]
    # No structured signal -> unknown, null status; still a well-formed safe payload.
    assert perr["provider_error_class"] == pe.UNKNOWN_PROVIDER_ERROR
    assert perr["provider_http_status"] is None
    _no_secret(payload)


# --------------------------------------------------------------------------- #
# §40/§41/§42 — EW7 successor checkpoint lifecycle + completed governance repoint
# --------------------------------------------------------------------------- #

def test_ew5_now_historical_current_is_ew7():
    # PN02D-B1-EW7 supersedes EW6 (bounded index-observation-timing moves HEAD past the
    # EW6 commit 0baacef): the current approved provider-authorization identity is now the exact
    # EW7 successor tag, and the re-exported grant/manifest identity follows it. EW5 (and EW6)
    # are now HISTORICAL (not current); their constant/tag strings are unchanged. The checkpoint
    # turn needs NO further governance code change.
    assert (
        EXPECTED_EW5_CHECKPOINT_TAG
        == "graphrag-pn02db1ew5-provider-error-observability-approved"
    )
    assert EXPECTED_EW7_CHECKPOINT_TAG != EXPECTED_EW6_CHECKPOINT_TAG
    assert EXPECTED_EW6_CHECKPOINT_TAG != EXPECTED_EW5_CHECKPOINT_TAG
    assert EXPECTED_EW5_CHECKPOINT_TAG != EXPECTED_EW4_CHECKPOINT_TAG
    assert current_approved_b1_r2_checkpoint() == EXPECTED_EW7_CHECKPOINT_TAG
    assert B.B1_R2_EXPECTED_CHECKPOINT_TAG == EXPECTED_EW7_CHECKPOINT_TAG
    assert current_approved_b1_r2_checkpoint() != EXPECTED_EW6_CHECKPOINT_TAG
    assert current_approved_b1_r2_checkpoint() != EXPECTED_EW5_CHECKPOINT_TAG
    assert current_approved_b1_r2_checkpoint() != EXPECTED_EW4_CHECKPOINT_TAG


def test_ew5_historical_tag_git_state_is_lifecycle_valid():
    # PN02D-B1-EW6: EW5 is now HISTORICAL (current approved == EW7). Lifecycle-aware — never a
    # permanent real-tag-absence assumption. STATE A (fresh clone): the EW5 tag is absent ->
    # empty peel. STATE B (EW5 tag present): it peels to a valid 40-hex commit — its OWN
    # historical commit; we do NOT assert peel == HEAD, because once EW6 is committed HEAD moves
    # past the EW5 commit (that is exactly why EW5 can no longer authorize the new HEAD).
    obs = RealTrustedB1R2Reader().observe(EXPECTED_EW5_CHECKPOINT_TAG)
    if not obs.observed_tag_exists:
        assert obs.observed_tag_peel == ""  # STATE A
    else:
        assert obs.checkpoint_tag == EXPECTED_EW5_CHECKPOINT_TAG
        assert len(obs.observed_tag_peel) == 40 and all(
            c in "0123456789abcdef" for c in obs.observed_tag_peel
        )  # STATE B — valid historical peel (not necessarily current HEAD)


def test_ew4_cannot_substitute_at_a_future_head():
    # §41: once EW5 source moves HEAD, the EW4 tag (peeling to its OWN commit) no longer peels
    # to the authorized HEAD, so it cannot authorize the modified HEAD (fail-closed).
    future = "e5e5e5e5" + "0" * 32
    ew4_commit = "1949b928" + "0" * 32
    grant = C.frozen_test_grant(b1_r2_checkpoint=EXPECTED_EW4_CHECKPOINT_TAG)
    reasons = verify_b1_r2_checkpoint(
        reader=C.b1r2_reader_ok(tag=EXPECTED_EW4_CHECKPOINT_TAG, peel=ew4_commit, head=future),
        operator_grant=grant,
        approved_expected_checkpoint=EXPECTED_EW4_CHECKPOINT_TAG,
        git_baseline=C.clean_git_baseline(commit=future, tag=EXPECTED_EW4_CHECKPOINT_TAG),
    )
    assert "b1_r2_tag_not_at_authorized_head" in reasons


def test_ew7_exact_tag_with_peel_is_accepted_by_git_gate():
    # §32/§39: the EXACT EW7 successor identity (the current approved checkpoint), trust-observed
    # at the authorized HEAD with a matching baseline, satisfies the control-plane Git gate (no
    # real tag created).
    future = "e7e7e7e7" + "0" * 32
    grant = C.frozen_test_grant(b1_r2_checkpoint=EXPECTED_EW7_CHECKPOINT_TAG)
    reasons = verify_b1_r2_checkpoint(
        reader=C.b1r2_reader_ok(tag=EXPECTED_EW7_CHECKPOINT_TAG, peel=future, head=future),
        operator_grant=grant,
        approved_expected_checkpoint=EXPECTED_EW7_CHECKPOINT_TAG,
        git_baseline=C.clean_git_baseline(commit=future, tag=EXPECTED_EW7_CHECKPOINT_TAG),
    )
    assert reasons == []


def test_wrong_ew7_peel_fails_closed():
    # §32: the exact EW7 tag observed with a peel that does NOT match the authorized HEAD fails
    # closed (a tag pointing somewhere other than the approved HEAD can never authorize).
    head = "aaaa1111" + "0" * 32
    wrong = "bbbb2222" + "0" * 32
    grant = C.frozen_test_grant(b1_r2_checkpoint=EXPECTED_EW7_CHECKPOINT_TAG)
    reasons = verify_b1_r2_checkpoint(
        reader=C.b1r2_reader_ok(tag=EXPECTED_EW7_CHECKPOINT_TAG, peel=wrong, head=head),
        operator_grant=grant,
        approved_expected_checkpoint=EXPECTED_EW7_CHECKPOINT_TAG,
        git_baseline=C.clean_git_baseline(commit=head, tag=EXPECTED_EW7_CHECKPOINT_TAG),
    )
    assert "b1_r2_tag_not_at_authorized_head" in reasons


@pytest.mark.parametrize(
    "substitute",
    [
        "graphrag-arbitrary-unrelated-tag",
        EXPECTED_EW6_CHECKPOINT_TAG,
        EXPECTED_EW5_CHECKPOINT_TAG,
        EXPECTED_EW4_CHECKPOINT_TAG,
        EXPECTED_EW3_CHECKPOINT_TAG,
        EXPECTED_EW2_CHECKPOINT_TAG,
        EXPECTED_EW1_CHECKPOINT_TAG,
        EXPECTED_PF1_CHECKPOINT_TAG,
        EXPECTED_B1_R2_CHECKPOINT_TAG,
    ],
)
def test_historical_or_arbitrary_tag_cannot_substitute_for_ew7(substitute):
    # §31/§35: naming ANY historical (EW6/EW5/EW4/EW3/EW2/EW1/PF1/B1-R2) or arbitrary tag as the
    # approved-expected identity cannot satisfy the EW7 checkpoint — the grant's B1-R2 identity
    # (the current EW7 successor) must EXACTLY equal the governance-approved identity
    # (exact-identity binding, not a denylist).
    future = "e7e7e7e7" + "0" * 32
    grant = C.frozen_test_grant(b1_r2_checkpoint=EXPECTED_EW7_CHECKPOINT_TAG)
    reasons = verify_b1_r2_checkpoint(
        reader=C.b1r2_reader_ok(tag=substitute, peel=future, head=future),
        operator_grant=grant,
        approved_expected_checkpoint=substitute,
        git_baseline=C.clean_git_baseline(commit=future, tag=substitute),
    )
    assert reasons, f"{substitute} must not satisfy the EW7 checkpoint"


def test_ew7_tag_alone_does_not_authorize_provider_run():
    # §33/§36: even with the Git gate satisfiable (exact EW7 tag at HEAD), the provider-run
    # governance flag stays NO — a valid checkpoint tag ALONE can never mint a live run; the
    # checkpoint identity and the operator grant remain separate.
    assert PN02_PROVIDER_RUN_AUTHORIZED is False


# --------------------------------------------------------------------------- #
# §12 — retryability is REPORTED, never enforced by this module
# --------------------------------------------------------------------------- #

def test_retryability_is_pure_reporting():
    # The classifier only maps a label; it holds no retry loop / sleep / side effect.
    import inspect

    src = inspect.getsource(pe)
    assert "sleep" not in src and "time." not in src
    assert pe.classify_provider_error(_http_status_error(429), operation="x").retryability == pe.RETRYABLE
    assert pe.classify_provider_error(_http_status_error(401), operation="x").retryability == pe.NON_RETRYABLE


# --------------------------------------------------------------------------- #
# Remediation #1 (M1) — CANONICAL provider boundary: structured status preserved
# BEFORE flattening, exercised through the exact repository-owned adapter.
# --------------------------------------------------------------------------- #

from open_notebook.ai.safe_openrouter_embedding import (  # noqa: E402
    SafeOpenRouterEmbeddingModel,
    build_safe_openrouter_embedding_model,
)


def _adapter_with_response(status: int, *, body: dict):
    """Build the REAL safe OpenRouter embedding adapter with a mocked transport (no network)."""
    model = build_safe_openrouter_embedding_model(
        model_name="openai/text-embedding-3-small",
        config={"api_key": "dummy-local-only", "base_url": "https://openrouter.ai/api/v1"},
    )

    def _handler(_request):
        return httpx.Response(status, json=body)

    model.async_client = httpx.AsyncClient(transport=httpx.MockTransport(_handler))
    return model


def _adapter_raising(exc: BaseException):
    model = build_safe_openrouter_embedding_model(
        model_name="openai/text-embedding-3-small", config={"api_key": "dummy-local-only"}
    )

    def _handler(_request):
        raise exc

    model.async_client = httpx.AsyncClient(transport=httpx.MockTransport(_handler))
    return model


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status,expected_class",
    [
        (400, pe.BAD_REQUEST),
        (401, pe.AUTHENTICATION_ERROR),
        (403, pe.AUTHORIZATION_ERROR),
        (404, pe.NOT_FOUND),
        (429, pe.RATE_LIMIT),
        (500, pe.SERVER_ERROR),
        (503, pe.SERVER_ERROR),
    ],
)
async def test_canonical_adapter_http_status_preserved(status, expected_class):
    # The exact production adapter path preserves a STRUCTURED status through a secret-bearing
    # provider body; the EW5 classifier then yields a distinct safe class. Proves canonical
    # 401 (auth) vs 404 (endpoint/capability) are distinguishable — closing B1EW5-R1-M1.
    model = _adapter_with_response(
        status, body={"error": {"message": "Bearer sk-or-secret leak", "code": "some_code"}}
    )
    with pytest.raises(pe.ProviderEmbeddingHTTPError) as ei:
        await model.aembed(["hello"])
    assert ei.value.status_code == status
    d = pe.classify_provider_error(ei.value, operation="embedding").as_public_dict()
    assert d["provider_error_class"] == expected_class
    assert d["provider_http_status"] == status
    assert d["provider_request_reached"] == pe.REACHED_YES
    _no_secret(d)


@pytest.mark.asyncio
async def test_canonical_adapter_401_vs_404_distinct():
    # Explicit A-vs-B: canonical 401 and 404 produce different structured classes + status.
    m401 = _adapter_with_response(401, body={"error": {"code": "invalid_api_key"}})
    m404 = _adapter_with_response(404, body={"error": {"code": "model_not_found"}})
    with pytest.raises(pe.ProviderEmbeddingHTTPError) as e401:
        await m401.aembed(["x"])
    with pytest.raises(pe.ProviderEmbeddingHTTPError) as e404:
        await m404.aembed(["x"])
    d401 = pe.classify_provider_error(e401.value, operation="embedding").as_public_dict()
    d404 = pe.classify_provider_error(e404.value, operation="embedding").as_public_dict()
    assert (d401["provider_error_class"], d401["provider_http_status"]) == (
        pe.AUTHENTICATION_ERROR, 401,
    )
    assert (d404["provider_error_class"], d404["provider_http_status"]) == (
        pe.NOT_FOUND, 404,
    )
    assert d401["provider_error_class"] != d404["provider_error_class"]


@pytest.mark.asyncio
async def test_canonical_adapter_network_error_is_transport_family():
    model = _adapter_raising(httpx.ConnectError("refused", request=_req()))
    with pytest.raises(httpx.ConnectError) as ei:
        await model.aembed(["x"])
    d = pe.classify_provider_error(ei.value, operation="embedding").as_public_dict()
    assert d["provider_error_class"] == pe.NETWORK_ERROR
    assert d["provider_http_status"] is None


@pytest.mark.asyncio
async def test_canonical_adapter_1536_success():
    model = _adapter_with_response  # noqa: F841 - keep name symmetry; success uses direct 200
    m = build_safe_openrouter_embedding_model(
        model_name="openai/text-embedding-3-small", config={"api_key": "dummy-local-only"}
    )
    m.async_client = httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda _r: httpx.Response(200, json={"data": [{"embedding": [0.0] * 1536}]})
        )
    )
    out = await m.aembed(["hello"])
    assert len(out) == 1 and len(out[0]) == 1536


@pytest.mark.asyncio
async def test_canonical_adapter_through_generate_embeddings(monkeypatch, _fast_retry):
    # End-to-end through the REAL generate_embeddings wrapping path using the REAL adapter:
    # a canonical 401 surfaces as a wrapped RuntimeError carrying a structured diagnostic.
    model = _adapter_with_response(401, body={"error": {"code": "invalid_api_key"}})
    _patch_model(monkeypatch, model)
    with pytest.raises(RuntimeError) as ei:
        await emb.generate_embeddings(["hello"], command_id="ew5-m1")
    diag = pe.extract_attached_diagnostic(ei.value)
    assert diag is not None
    assert diag.provider_error_class == pe.AUTHENTICATION_ERROR
    assert diag.provider_http_status == 401
    assert diag.provider_request_reached == pe.REACHED_YES


def test_model_manager_openrouter_embedding_uses_safe_adapter():
    # The canonical model-manager embedding-construction seam returns the repository-owned safe
    # adapter for provider "openrouter" (so the real B1 path resolves through it).
    import inspect

    from open_notebook.ai import models as models_mod

    src = inspect.getsource(models_mod.ModelManager.get_model)
    assert "build_safe_openrouter_embedding_model" in src
    assert issubclass(SafeOpenRouterEmbeddingModel, __import__(
        "esperanto.providers.embedding.openrouter", fromlist=["OpenRouterEmbeddingModel"]
    ).OpenRouterEmbeddingModel)
