"""GraphRAG-09H Run #6 SEC1 — provider-model credential logging remediation (provider-free).

Reconfirms and guards the fix for the confirmed raw-credential logging defect: the
provisioning DEBUG path and the "not a LanguageModel" error path formatted the WHOLE
provider-model object, whose repr (e.g. Esperanto's) embeds a plaintext API key, leaking
the credential to logs/exception text.

All secrets here are SYNTHETIC. No provider/network traffic: the provisioning path is
exercised with injected fakes whose OWN repr deliberately embeds fake secrets, so the test
proves the amended repo-owned code never serializes the whole object.
"""

import contextlib

import pytest

import open_notebook.ai.provision as provision
from open_notebook.exceptions import ConfigurationError

# --- SYNTHETIC secrets (never real) -------------------------------------------------- #
SYNTHETIC_API_KEY = "sk-or-v1-SYNTHETIC_SECRET_DO_NOT_LOG"
SYNTHETIC_BEARER = "Bearer SYNTHETIC_SECRET_BEARER"
SYNTHETIC_MARKERS = ("SYNTHETIC_SECRET", SYNTHETIC_API_KEY, "SYNTHETIC_SECRET_BEARER")

# --- safe (non-secret) identity values ----------------------------------------------- #
SAFE_PROVIDER = "openrouter"
SAFE_MODEL_NAME = "openai/gpt-4o-mini"
SAFE_MODEL_TYPE = "language"


class _FakeCredentialModel:
    """Fake provider model whose OWN repr/str embeds plaintext secrets (mimics Esperanto).

    Carries allowlisted safe identity fields AND credential-like fields. If repo code ever
    stringifies/reprs the whole object, the synthetic secret leaks — which the fix forbids.
    """

    def __init__(self) -> None:
        self.provider = SAFE_PROVIDER
        self.model_name = SAFE_MODEL_NAME
        self.model_type = SAFE_MODEL_TYPE
        self.api_key = SYNTHETIC_API_KEY  # NEVER allowlisted
        self.authorization = SYNTHETIC_BEARER  # NEVER allowlisted
        self.token = "SYNTHETIC_SECRET_TOKEN"  # NEVER allowlisted

    def __repr__(self) -> str:  # the leak surface a safe helper must never touch
        return (
            f"{type(self).__name__}(api_key={self.api_key!r}, "
            f"authorization={self.authorization!r}, token={self.token!r}, "
            f"model_name={self.model_name!r})"
        )

    __str__ = __repr__


class _DummyLC:
    """A harmless LangChain stand-in returned by to_langchain() (no secrets, offline)."""


class _FakeCredentialLanguageModel(_FakeCredentialModel):
    """A credential-bearing fake that IS the provision isinstance base (happy DEBUG path)."""

    def to_langchain(self) -> _DummyLC:
        return _DummyLC()


class _ReprExplodes:
    """Its repr raises — proves the safe helper never calls repr()/str() on the object."""

    provider = SAFE_PROVIDER
    model_name = SAFE_MODEL_NAME

    def __repr__(self) -> str:  # noqa: D401
        raise RuntimeError("repr must never be called by the safe logger")

    __str__ = __repr__


@contextlib.contextmanager
def _capture_logs():
    """Capture all loguru records (>=DEBUG) emitted within the block as plain messages."""
    from loguru import logger

    records: list[str] = []
    sink_id = logger.add(records.append, level="DEBUG", format="{message}")
    try:
        yield records
    finally:
        logger.remove(sink_id)


def _assert_no_synthetic_secret(blob: str) -> None:
    for marker in SYNTHETIC_MARKERS:
        assert marker not in blob, f"synthetic secret {marker!r} leaked into: {blob!r}"


class _FakeModelManager:
    def __init__(self, model) -> None:
        self._model = model

    async def get_model(self, model_id, **kwargs):
        return self._model

    async def get_default_model(self, default_type, **kwargs):
        return self._model


# --------------------------------------------------------------------------- unit: helper
def test_safe_model_description_excludes_credentials_keeps_identity():
    desc = provision._safe_model_description(_FakeCredentialModel())
    _assert_no_synthetic_secret(desc)
    # Safe identity survives (§11): class + allowlisted scalar fields.
    assert "_FakeCredentialModel" in desc
    assert f"provider={SAFE_PROVIDER}" in desc
    assert f"model_name={SAFE_MODEL_NAME}" in desc
    # Credential-like field names/values are never emitted.
    assert "api_key" not in desc and "authorization" not in desc and "token" not in desc


def test_safe_model_description_never_calls_object_repr():
    # §12: the helper reads allowlisted attrs only; it must not repr()/str() the object.
    desc = provision._safe_model_description(_ReprExplodes())
    assert "_ReprExplodes" in desc
    assert f"provider={SAFE_PROVIDER}" in desc


def test_safe_model_description_falls_back_to_class_name():
    class _Bare:
        pass

    assert provision._safe_model_description(_Bare()) == "_Bare"


# ---------------------------------------------------------------- integration: DEBUG path
@pytest.mark.asyncio
async def test_provision_debug_log_excludes_credential(monkeypatch):
    monkeypatch.delenv("OPEN_NOTEBOOK_LLM_MAX_RETRIES", raising=False)
    monkeypatch.delenv("OPEN_NOTEBOOK_LLM_REQUEST_TIMEOUT_SECONDS", raising=False)
    model = _FakeCredentialLanguageModel()
    monkeypatch.setattr(provision, "model_manager", _FakeModelManager(model))
    monkeypatch.setattr(provision, "LanguageModel", _FakeCredentialLanguageModel)

    with _capture_logs() as records:
        result = await provision.provision_langchain_model("hi", "model:x", "chat")

    assert isinstance(result, _DummyLC)
    blob = "\n".join(records)
    assert "Using model:" in blob  # the DEBUG line still fires
    _assert_no_synthetic_secret(blob)
    assert SAFE_PROVIDER in blob and SAFE_MODEL_NAME in blob  # safe identity logged


# ---------------------------------------------------------------- integration: error path
@pytest.mark.asyncio
async def test_provision_error_path_excludes_credential(monkeypatch):
    monkeypatch.delenv("OPEN_NOTEBOOK_LLM_MAX_RETRIES", raising=False)
    monkeypatch.delenv("OPEN_NOTEBOOK_LLM_REQUEST_TIMEOUT_SECONDS", raising=False)
    # A credential-bearing model that is NOT the (patched) LanguageModel base -> error path.
    not_a_language_model = _FakeCredentialModel()
    monkeypatch.setattr(provision, "model_manager", _FakeModelManager(not_a_language_model))
    monkeypatch.setattr(provision, "LanguageModel", _FakeCredentialLanguageModel)

    with _capture_logs() as records:
        with pytest.raises(ConfigurationError) as excinfo:
            await provision.provision_langchain_model("hi", "model:x", "chat")

    exc_text = str(excinfo.value)
    blob = "\n".join(records)
    _assert_no_synthetic_secret(exc_text)  # raised message carries no secret
    _assert_no_synthetic_secret(blob)  # debug + error logs carry no secret
    # Useful diagnostics remain: the class name is still surfaced for triage.
    assert "_FakeCredentialModel" in exc_text
