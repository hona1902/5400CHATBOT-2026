import math
import os

from esperanto import LanguageModel
from langchain_core.language_models.chat_models import BaseChatModel
from loguru import logger

from open_notebook.ai.models import model_manager
from open_notebook.exceptions import ConfigurationError
from open_notebook.utils import token_count

# Internal, server/process-only execution-bound controls for canonical LLM
# provisioning (GraphRAG-09H). These are NOT model/credential DB fields and NOT
# public API/request parameters — an untrusted client cannot set provider bounds.
# They are read from the process environment and default to UNSET: when unset,
# provisioning behaves exactly as before (no retry override, no timeout injection).
# A bounded run (e.g. the 09H synthetic live validation) sets them inline for that
# process only. Parsing is fail-closed: an explicitly-set but invalid value raises
# rather than silently falling back to a default.
_MAX_RETRIES_ENV = "OPEN_NOTEBOOK_LLM_MAX_RETRIES"
_REQUEST_TIMEOUT_ENV = "OPEN_NOTEBOOK_LLM_REQUEST_TIMEOUT_SECONDS"


def _parse_env_max_retries() -> int | None:
    """Parse OPEN_NOTEBOOK_LLM_MAX_RETRIES. Returns None when unset; raises on invalid.

    Valid: a non-negative integer. 0 means one initial request and zero retries.
    """
    raw = os.environ.get(_MAX_RETRIES_ENV)
    if raw is None:
        return None
    raw = raw.strip()
    if raw == "":
        raise ConfigurationError(f"{_MAX_RETRIES_ENV} is set but empty (expected an integer >= 0)")
    try:
        value = int(raw)
    except ValueError:
        raise ConfigurationError(f"{_MAX_RETRIES_ENV} must be an integer >= 0")
    if value < 0:
        raise ConfigurationError(f"{_MAX_RETRIES_ENV} must be >= 0")
    return value


def _parse_env_request_timeout() -> float | None:
    """Parse OPEN_NOTEBOOK_LLM_REQUEST_TIMEOUT_SECONDS. None when unset; raises on invalid.

    Valid: a finite number > 0 (seconds). Rejects empty, non-numeric, zero, negative,
    NaN and infinity.
    """
    raw = os.environ.get(_REQUEST_TIMEOUT_ENV)
    if raw is None:
        return None
    raw = raw.strip()
    if raw == "":
        raise ConfigurationError(f"{_REQUEST_TIMEOUT_ENV} is set but empty (expected a number > 0)")
    try:
        value = float(raw)
    except ValueError:
        raise ConfigurationError(f"{_REQUEST_TIMEOUT_ENV} must be a finite number > 0")
    if not math.isfinite(value):
        raise ConfigurationError(f"{_REQUEST_TIMEOUT_ENV} must be finite (NaN/inf rejected)")
    if value <= 0:
        raise ConfigurationError(f"{_REQUEST_TIMEOUT_ENV} must be > 0")
    return value


def _apply_max_retries(model: BaseChatModel, max_retries: int) -> BaseChatModel:
    """Apply an explicit retry bound to the returned LangChain model, fail-closed.

    Esperanto's ``to_langchain()`` does not forward ``max_retries`` (the underlying
    LangChain OpenAI-compatible client would otherwise use its framework default),
    so the bound is enforced here on the returned model. If the returned model does
    not expose an enforceable ``max_retries`` (e.g. a non-OpenAI-compatible provider),
    an explicitly-requested bound MUST NOT be silently ignored — it raises.
    """
    if not hasattr(model, "max_retries"):
        raise ConfigurationError(
            f"{_MAX_RETRIES_ENV} was explicitly set but the provisioned model "
            f"({type(model).__name__}) has no enforceable 'max_retries'; refusing to "
            f"proceed with an unenforced retry bound."
        )
    try:
        model.max_retries = max_retries
    except Exception as e:  # noqa: BLE001 - surface as a clear config error, fail closed
        raise ConfigurationError(
            f"{_MAX_RETRIES_ENV} could not be applied to the provisioned model: {type(e).__name__}"
        ) from e
    if getattr(model, "max_retries", None) != max_retries:
        raise ConfigurationError(
            f"{_MAX_RETRIES_ENV} was not enforced on the provisioned model "
            f"(expected {max_retries})."
        )
    return model


async def provision_langchain_model(
    content, model_id, default_type, **kwargs
) -> BaseChatModel:
    """
    Returns the best model to use based on the context size and on whether there is a specific model being requested in Config.
    If context > 105_000, returns the large_context_model
    If model_id is specified in Config, returns that model
    Otherwise, returns the default model for the given type
    """
    # Run-scoped internal execution bounds (unset => no change to existing behavior).
    # Parsed fail-closed BEFORE any provisioning so an invalid explicit value stops here.
    max_retries = _parse_env_max_retries()
    request_timeout = _parse_env_request_timeout()
    if request_timeout is not None and "timeout" not in kwargs:
        # Injected into the provider config BEFORE Esperanto constructs its HTTP
        # client: get_model/get_default_model do config.update(kwargs), and
        # Esperanto's _get_timeout() reads config["timeout"] (highest priority),
        # which sets the httpx client timeout used by the LangChain model.
        kwargs = {**kwargs, "timeout": request_timeout}

    tokens = token_count(content)
    model = None
    selection_reason = ""

    if tokens > 105_000:
        selection_reason = f"large_context (content has {tokens} tokens)"
        logger.debug(
            f"Using large context model because the content has {tokens} tokens"
        )
        model = await model_manager.get_default_model("large_context", **kwargs)
    elif model_id:
        selection_reason = f"explicit model_id={model_id}"
        model = await model_manager.get_model(model_id, **kwargs)
    else:
        selection_reason = f"default for type={default_type}"
        model = await model_manager.get_default_model(default_type, **kwargs)

    logger.debug(f"Using model: {model}")

    if model is None:
        logger.error(
            f"Model provisioning failed: No model found. "
            f"Selection reason: {selection_reason}. "
            f"model_id={model_id}, default_type={default_type}. "
            f"Please check Settings → Models and ensure a default model is configured for '{default_type}'."
        )
        raise ConfigurationError(
            f"No model configured for {selection_reason}. "
            f"Please go to Settings → Models and configure a default model for '{default_type}'."
        )

    if not isinstance(model, LanguageModel):
        logger.error(
            f"Model type mismatch: Expected LanguageModel but got {type(model).__name__}. "
            f"Selection reason: {selection_reason}. "
            f"model_id={model_id}, default_type={default_type}."
        )
        raise ConfigurationError(
            f"Model is not a LanguageModel: {model}. "
            f"Please check that the model configured for '{default_type}' is a language model, not an embedding or speech model."
        )

    langchain_model = model.to_langchain()
    if max_retries is not None:
        langchain_model = _apply_max_retries(langchain_model, max_retries)
    return langchain_model
