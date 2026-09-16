"""Repository-owned safe OpenRouter embedding boundary (PN02D-B1-EW5 remediation #1).

Closes finding B1EW5-R1-M1. The pinned esperanto ``OpenRouterEmbeddingModel`` handles an
HTTP ``>= 400`` inside its own ``_handle_error`` and raises a **bare** ``RuntimeError`` whose
only carrier of the status is the message text. By the time the EW5 diagnostic classifier
runs (at ``generate_embeddings``' except block) the structured status is gone, so real
OpenRouter 401/403/404/400/429/5xx all collapse to ``unknown_provider_error`` / status
``None`` — auth (A) is indistinguishable from endpoint/capability (B).

This subclass overrides ONLY ``_handle_error`` so that, at the exact point the ``httpx``
response is still in hand, it raises a ``ProviderEmbeddingHTTPError`` carrying the STRUCTURED
integer status (and an already-sanitized structured error code when the provider supplies a
safe one). No raw provider message/body/header/secret is ever captured or re-raised; the
status comes from ``response.status_code`` (a structured attribute), never from parsing text.

We do NOT edit the installed third-party package. The dedicated esperanto class is subclassed
in-repo and substituted at the model-manager embedding-construction seam for provider
``openrouter`` only; every other provider/modality is untouched. Network/timeout failures
still surface as ``httpx`` exceptions straight from the client (the classifier already maps
those to transport families), so this override is scoped to the HTTP-response error path.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from esperanto.providers.embedding.openrouter import OpenRouterEmbeddingModel

from open_notebook.utils.provider_errors import (
    ProviderEmbeddingHTTPError,
    sanitize_provider_error_code,
)


class SafeOpenRouterEmbeddingModel(OpenRouterEmbeddingModel):
    """OpenRouter embedding model that preserves STRUCTURED HTTP-error metadata.

    Behaviour is identical to the base class except that an HTTP ``>= 400`` response raises
    a ``ProviderEmbeddingHTTPError`` (carrying ``status_code`` + an optional safe ``code``)
    instead of a bare, status-less ``RuntimeError``. Both are ``RuntimeError`` subclasses, so
    downstream retry/wrapping in ``generate_embeddings`` is unchanged.
    """

    def _handle_error(self, response: Any) -> None:  # noqa: ANN401 - httpx.Response (duck)
        status = getattr(response, "status_code", None)
        if not isinstance(status, int) or status < 400:
            return
        # Best-effort SAFE structured code from the OpenAI-compatible error object — a token
        # like "invalid_api_key". NEVER the message/body; any unsafe value drops to None.
        code: Optional[str] = None
        try:
            data = response.json()
            if isinstance(data, dict):
                err = data.get("error")
                if isinstance(err, dict):
                    code = sanitize_provider_error_code(err.get("code") or err.get("type"))
        except Exception:  # noqa: BLE001 - a body we cannot parse safely yields no code
            code = None
        raise ProviderEmbeddingHTTPError(status_code=status, code=code)


def build_safe_openrouter_embedding_model(
    *, model_name: str, config: Optional[Dict[str, Any]] = None
) -> SafeOpenRouterEmbeddingModel:
    """Construct the safe OpenRouter embedding model exactly as ``AIFactory.create_embedding``
    would build the base class (``model_name`` + ``config`` carrying api_key/base_url), so the
    canonical model-manager path resolves through this boundary with no other change."""
    return SafeOpenRouterEmbeddingModel(model_name=model_name, config=dict(config or {}))


__all__ = ["SafeOpenRouterEmbeddingModel", "build_safe_openrouter_embedding_model"]
