"""GraphRAG-09H canonical-Chat execution-bound controls (provider-free).

Covers the run-scoped, internal, default-unset execution bounds that
``provision_langchain_model`` applies for the 09H canonical Chat call:

  * OPEN_NOTEBOOK_LLM_MAX_RETRIES          -> returned LangChain model .max_retries (retry bound)
  * OPEN_NOTEBOOK_LLM_REQUEST_TIMEOUT_SECONDS -> injected as config["timeout"], consumed by
    Esperanto _get_timeout() -> the httpx client handed to the LangChain model (timeout bound)

No provider/network traffic: the provisioning path is exercised with injected fakes, and the
one Esperanto-layer assertion constructs a model WITHOUT invoking it (construction is offline).
"""

import pytest

import open_notebook.ai.provision as provision
from open_notebook.exceptions import ConfigurationError

MAX_RETRIES_ENV = "OPEN_NOTEBOOK_LLM_MAX_RETRIES"
TIMEOUT_ENV = "OPEN_NOTEBOOK_LLM_REQUEST_TIMEOUT_SECONDS"


# --------------------------------------------------------------------------- fakes
class _FakeLC:
    """OpenAI-compatible LangChain stand-in with a writable max_retries (default 2)."""

    def __init__(self) -> None:
        self.max_retries = 2  # framework-default sentinel; patch must override only when set


class _FakeLCNoRetries:
    """A provisioned model that exposes NO enforceable max_retries."""

    __slots__ = ()  # no max_retries attribute, and cannot be set


class _FakeLanguageModel:
    """Stands in for esperanto LanguageModel (provision isinstance check is patched to this)."""

    def __init__(self, lc) -> None:
        self._lc = lc

    def to_langchain(self):
        return self._lc


class _FakeModelManager:
    """Records the kwargs/model_id the provisioner passes, returns a fake language model."""

    def __init__(self, lc) -> None:
        self._lc = lc
        self.calls: list[dict] = []

    async def get_model(self, model_id, **kwargs):
        self.calls.append({"fn": "get_model", "model_id": model_id, "kwargs": dict(kwargs)})
        return _FakeLanguageModel(self._lc)

    async def get_default_model(self, default_type, **kwargs):
        self.calls.append({"fn": "get_default_model", "type": default_type, "kwargs": dict(kwargs)})
        return _FakeLanguageModel(self._lc)


@pytest.fixture
def install_fakes(monkeypatch):
    """Install a fake model_manager + patch the isinstance base; return the manager + lc."""

    def _install(lc=None):
        lc = lc if lc is not None else _FakeLC()
        mm = _FakeModelManager(lc)
        monkeypatch.setattr(provision, "model_manager", mm)
        monkeypatch.setattr(provision, "LanguageModel", _FakeLanguageModel)
        return mm, lc

    return _install


def _clear_env(monkeypatch):
    monkeypatch.delenv(MAX_RETRIES_ENV, raising=False)
    monkeypatch.delenv(TIMEOUT_ENV, raising=False)


# --------------------------------------------------------------------------- T1 retry bound
@pytest.mark.asyncio
async def test_retry_bound_zero_applied_to_returned_model(monkeypatch, install_fakes):
    _clear_env(monkeypatch)
    monkeypatch.setenv(MAX_RETRIES_ENV, "0")
    mm, lc = install_fakes()
    result = await provision.provision_langchain_model("hi", "model:x", "chat", max_tokens=8192)
    assert result is lc
    assert result.max_retries == 0  # effective model observed


# --------------------------------------------------------------------------- T2 timeout propagation
@pytest.mark.asyncio
async def test_timeout_injected_into_provider_config(monkeypatch, install_fakes):
    _clear_env(monkeypatch)
    monkeypatch.setenv(TIMEOUT_ENV, "60")
    mm, lc = install_fakes()
    await provision.provision_langchain_model("hi", "model:x", "chat", max_tokens=8192)
    call = mm.calls[-1]
    assert call["kwargs"].get("timeout") == 60.0  # reaches get_model config (→ config.update → esperanto)


def test_timeout_reaches_esperanto_http_layer_offline():
    """Effective-layer evidence: Esperanto consumes config['timeout'] (→ its httpx client).

    Construction only — no model invocation, no network.
    """
    from esperanto import AIFactory

    m = AIFactory.create_language(
        provider="openai",
        model_name="gpt-4o-mini",
        config={"api_key": "sk-test-not-real", "timeout": 60},
    )
    assert m._get_timeout() == 60.0


# --------------------------------------------------------------------------- T3 both together
@pytest.mark.asyncio
async def test_both_bounds_simultaneous(monkeypatch, install_fakes):
    _clear_env(monkeypatch)
    monkeypatch.setenv(MAX_RETRIES_ENV, "0")
    monkeypatch.setenv(TIMEOUT_ENV, "60")
    mm, lc = install_fakes()
    result = await provision.provision_langchain_model("hi", "model:x", "chat")
    assert result.max_retries == 0
    assert mm.calls[-1]["kwargs"].get("timeout") == 60.0


# --------------------------------------------------------------------------- T4 unset compatibility
@pytest.mark.asyncio
async def test_unset_behavior_identical_to_baseline(monkeypatch, install_fakes):
    _clear_env(monkeypatch)
    mm, lc = install_fakes()
    result = await provision.provision_langchain_model("hi", "model:x", "chat", max_tokens=8192)
    assert result is lc
    assert result.max_retries == 2  # unchanged (no override)
    assert "timeout" not in mm.calls[-1]["kwargs"]  # no injection
    assert mm.calls[-1]["kwargs"].get("max_tokens") == 8192  # unrelated kwargs preserved


# --------------------------------------------------------------------------- T5 malformed retry
@pytest.mark.parametrize("bad", ["abc", "-1", "1.5", ""])
@pytest.mark.asyncio
async def test_malformed_retry_fails_closed(monkeypatch, install_fakes, bad):
    _clear_env(monkeypatch)
    monkeypatch.setenv(MAX_RETRIES_ENV, bad)
    install_fakes()
    with pytest.raises(ConfigurationError):
        await provision.provision_langchain_model("hi", "model:x", "chat")


# --------------------------------------------------------------------------- T6 malformed timeout
@pytest.mark.parametrize("bad", ["abc", "0", "-1", "nan", "inf", "-inf", ""])
@pytest.mark.asyncio
async def test_malformed_timeout_fails_closed(monkeypatch, install_fakes, bad):
    _clear_env(monkeypatch)
    monkeypatch.setenv(TIMEOUT_ENV, bad)
    install_fakes()
    with pytest.raises(ConfigurationError):
        await provision.provision_langchain_model("hi", "model:x", "chat")


# --------------------------------------------------------------------------- T7 unenforceable retry
@pytest.mark.asyncio
async def test_explicit_retry_on_unsupported_model_fails_closed(monkeypatch, install_fakes):
    _clear_env(monkeypatch)
    monkeypatch.setenv(MAX_RETRIES_ENV, "0")
    install_fakes(lc=_FakeLCNoRetries())  # no max_retries attribute
    with pytest.raises(ConfigurationError):
        await provision.provision_langchain_model("hi", "model:x", "chat")


# --------------------------------------------------------------------------- T8 model_override path
@pytest.mark.asyncio
async def test_model_override_path_resolves(monkeypatch, install_fakes):
    _clear_env(monkeypatch)
    mm, lc = install_fakes()
    result = await provision.provision_langchain_model("hi", "model:gr_pn02_chat", "chat")
    assert result is lc
    assert mm.calls[-1]["fn"] == "get_model"
    assert mm.calls[-1]["model_id"] == "model:gr_pn02_chat"


# --------------------------------------------------------------------------- T4b default path unchanged (no model_id)
@pytest.mark.asyncio
async def test_default_path_unset_no_injection(monkeypatch, install_fakes):
    _clear_env(monkeypatch)
    mm, lc = install_fakes()
    result = await provision.provision_langchain_model("hi", None, "chat")
    assert result is lc
    assert mm.calls[-1]["fn"] == "get_default_model"
    assert "timeout" not in mm.calls[-1]["kwargs"]
    assert result.max_retries == 2
