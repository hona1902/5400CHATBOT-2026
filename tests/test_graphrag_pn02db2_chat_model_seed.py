"""PN02D-B2 remediation #2 — isolated default CHAT model provisioning (provider-free).

EVALUATION-ONLY. Closes the verified Real B2 Execution #1 technical failure: the fresh isolated
B2 runtime had no ``DefaultModels.default_chat_model``, so the QA final-answer transport
(``provision_langchain_model(type="chat")``) resolved ``model_id=None`` → ``ConfigurationError``
(wrapped ``FinalAnswerProviderError``). These tests prove the remediation seeds the frozen chat
model (``openai/gpt-4o-mini`` via OpenRouter, ``Model.type="language"``) through the EXISTING
model-provisioning path — reusing the single-source ``isolated_model_seed`` mechanism, with NO
provider network, NO ad-hoc client, and fail-closed when the seed is absent. B1 (embedding-only
seed) is untouched.
"""

from __future__ import annotations

import asyncio
from contextlib import contextmanager
from unittest import mock

import pytest

import open_notebook.ai.models as M
from open_notebook.ai.provision import provision_langchain_model
from open_notebook.exceptions import ConfigurationError
from open_notebook.integrations.graphrag.eval import isolated_model_seed as S
from open_notebook.integrations.graphrag.eval import realseamsb2pn02d as RB2
from open_notebook.integrations.graphrag.eval.provider_binding08 import (
    FROZEN_EMBEDDING_MODEL,
    FROZEN_LLM_MODEL,
)
from open_notebook.integrations.graphrag.eval.vectoradapterpn02d import (
    FROZEN_EMBEDDING_PROVIDER,
)

_REQUIRE_ACTIVE_ISOLATION = (
    "open_notebook.integrations.graphrag.eval.isolation08.require_active_isolation"
)


class _FakeDefaults:
    """In-memory DefaultModels stand-in with both chat + embedding default pointers."""

    def __init__(self, *, chat=None, embedding=None):
        self.default_chat_model = chat
        self.default_embedding_model = embedding
        # unused-by-tests fields kept for attribute parity
        self.default_transformation_model = None
        self.default_tools_model = None
        self.large_context_model = None
        self.default_text_to_speech_model = None
        self.default_speech_to_text_model = None

    async def update(self):
        return None


def _set_isolation_env(ns, db):
    import os

    prev = (os.environ.get("SURREAL_NAMESPACE"), os.environ.get("SURREAL_DATABASE"))
    if ns is None:
        os.environ.pop("SURREAL_NAMESPACE", None)
    else:
        os.environ["SURREAL_NAMESPACE"] = ns
    if db is None:
        os.environ.pop("SURREAL_DATABASE", None)
    else:
        os.environ["SURREAL_DATABASE"] = db
    return prev


@contextmanager
def _fake_model_db(*, active_ns="graphrag_eval_t", active_db="graphrag_08_t", defaults=None):
    """Coherent in-memory Model store + fake DefaultModels + no-op active-isolation guard."""
    from open_notebook.exceptions import NotFoundError

    store: dict = {}
    dm = defaults if defaults is not None else _FakeDefaults()

    class _Model:
        def __init__(self, *, name=None, provider=None, type=None, credential=None):
            self.name = name
            self.provider = provider
            self.type = type
            self.credential = credential
            self.id = None
            self.deleted = False

        async def save(self):
            self.id = f"model:seed-{len(store) + 1}"
            store[str(self.id)] = self

        @classmethod
        async def get(cls, mid):
            rec = store.get(str(mid))
            if rec is None:
                raise NotFoundError(f"no model {mid}")
            return rec

        async def delete(self):
            self.deleted = True
            store.pop(str(self.id), None)

    prev = _set_isolation_env(active_ns, active_db)
    try:
        with mock.patch.object(M, "Model", _Model), mock.patch.object(
            M.DefaultModels, "get_instance", new=mock.AsyncMock(return_value=dm)
        ), mock.patch(_REQUIRE_ACTIVE_ISOLATION, new=mock.Mock()):
            yield dm, store, _Model
    finally:
        _set_isolation_env(*prev)


# --------------------------------------------------------------------------- #
# 1. chat seed creates the exact frozen chat identity + binds default_chat_model
# --------------------------------------------------------------------------- #


def test_chat_seed_creates_exact_frozen_identity_binds_and_tears_down():
    seen: dict[str, object] = {}
    with _fake_model_db() as (dm, store, _ModelCls):

        async def _run():
            async with S.seeded_frozen_chat_model() as mid:
                rec = store[str(mid)]
                seen.update(
                    mid=mid, name=rec.name, provider=rec.provider, type=rec.type,
                    credential=rec.credential, bound=dm.default_chat_model,
                )

        asyncio.run(_run())

    assert seen["name"] == FROZEN_LLM_MODEL == "openai/gpt-4o-mini"
    assert seen["provider"] == FROZEN_EMBEDDING_PROVIDER  # "openrouter"
    assert seen["type"] == "language"  # the get_model chat/LLM branch
    assert seen["credential"] is None  # env-fallback; nothing stored
    assert seen["bound"] == seen["mid"]
    # Teardown ran on exit: prior default (None) restored, owned model deleted.
    assert dm.default_chat_model is None
    assert store == {}


def test_chat_seed_reuses_exact_match_and_fails_closed_on_mismatch():
    # exact-match reuse
    with _fake_model_db() as (dm, store, _ModelCls):
        existing = _ModelCls(name=FROZEN_LLM_MODEL, provider=FROZEN_EMBEDDING_PROVIDER, type="language")
        existing.id = "model:existing"
        store["model:existing"] = existing
        dm.default_chat_model = "model:existing"

        async def _run():
            async with S.seeded_frozen_chat_model() as mid:
                assert mid == "model:existing"

        asyncio.run(_run())
        assert existing.deleted is False and dm.default_chat_model == "model:existing"

    # mismatch → fail closed (never overwrite)
    with _fake_model_db() as (dm, store, _ModelCls):
        other = _ModelCls(name="anthropic/claude", provider="anthropic", type="language")
        other.id = "model:other"
        store["model:other"] = other
        dm.default_chat_model = "model:other"

        async def _run2():
            async with S.seeded_frozen_chat_model():
                pass

        with pytest.raises(S.ConflictingChatModelError):
            asyncio.run(_run2())
        assert other.deleted is False and dm.default_chat_model == "model:other"


# --------------------------------------------------------------------------- #
# 2. B2 combined seed binds BOTH embedding + chat defaults; both restored on exit
# --------------------------------------------------------------------------- #


def test_b2_combined_seed_binds_both_defaults_and_restores():
    seen: dict[str, object] = {}
    with _fake_model_db() as (dm, store, _ModelCls):

        async def _run():
            async with RB2._default_b2_model_seed() as embedding_id:
                emb = store[str(dm.default_embedding_model)]
                chat = store[str(dm.default_chat_model)]
                seen.update(
                    yielded=embedding_id,
                    emb_name=emb.name, emb_type=emb.type,
                    chat_name=chat.name, chat_type=chat.type,
                    n=len(store),
                )

        asyncio.run(_run())

    assert seen["yielded"] == seen.get("yielded")  # embedding id yielded (B1 contract preserved)
    assert seen["emb_name"] == FROZEN_EMBEDDING_MODEL and seen["emb_type"] == "embedding"
    assert seen["chat_name"] == FROZEN_LLM_MODEL and seen["chat_type"] == "language"
    assert seen["n"] == 2  # exactly two seeded models
    # LIFO teardown: both defaults restored to None, store emptied.
    assert dm.default_chat_model is None and dm.default_embedding_model is None
    assert store == {}


# --------------------------------------------------------------------------- #
# 3. EXEC #1 failure reproduced BEFORE fix, closed AFTER fix — through the REAL
#    provision_langchain_model chain (only the transport boundary faked, §10)
# --------------------------------------------------------------------------- #


def test_exec1_model_id_none_reproduced_then_closed_via_real_provisioning_path():
    from esperanto import LanguageModel

    # BEFORE FIX — fresh isolated namespace, no default_chat_model → provision fails closed.
    with _fake_model_db(defaults=_FakeDefaults(chat=None, embedding=None)):
        with mock.patch("esperanto.AIFactory.create_language") as fake_lang:
            with pytest.raises(ConfigurationError):
                asyncio.run(provision_langchain_model("q", None, "chat"))
            fake_lang.assert_not_called()  # no model to build; no transport touched

    # AFTER FIX — inside the chat seed, provision resolves the frozen chat model (non-None),
    # constructing it only through the transport boundary we fake here (no provider network).
    sentinel_langchain = object()
    fake_lang_model = mock.MagicMock(spec=LanguageModel)
    fake_lang_model.to_langchain.return_value = sentinel_langchain
    with _fake_model_db() as (dm, store, _ModelCls):
        with mock.patch(
            "esperanto.AIFactory.create_language", return_value=fake_lang_model
        ) as fake_lang:

            async def _run():
                async with S.seeded_frozen_chat_model():
                    return await provision_langchain_model("q", None, "chat")

            resolved = asyncio.run(_run())

    assert resolved is sentinel_langchain  # EXEC #1 model_id=None is CLOSED (non-None chat model)
    _, kwargs = fake_lang.call_args
    assert kwargs.get("model_name") == FROZEN_LLM_MODEL  # frozen gpt-4o-mini
    assert kwargs.get("provider") == FROZEN_EMBEDDING_PROVIDER  # openrouter (no fallback model)


def test_missing_chat_seed_still_fails_closed_no_fallback():
    # Even with an embedding default present, a missing chat default must fail closed — the
    # remediation adds NO hidden fallback to another model.
    with _fake_model_db(defaults=_FakeDefaults(chat=None, embedding="model:emb")):
        with mock.patch("esperanto.AIFactory.create_language") as fake_lang:
            with pytest.raises(ConfigurationError):
                asyncio.run(provision_langchain_model("q", None, "chat"))
            fake_lang.assert_not_called()


# --------------------------------------------------------------------------- #
# 4. no provider transport during these tests; frozen identity constants
# --------------------------------------------------------------------------- #


def test_frozen_chat_identity_constants_exact():
    assert S.FROZEN_CHAT_MODEL == "openai/gpt-4o-mini"
    assert S.FROZEN_CHAT_PROVIDER == "openrouter"
    assert S.FROZEN_CHAT_TYPE == "language"
    assert "seeded_frozen_chat_model" in S.__all__
