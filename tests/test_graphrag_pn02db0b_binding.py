"""PN02D-B0B — provider-binding materialization (auth precedes binding; secret-safe)."""

from __future__ import annotations

import json

import graphrag_pn02db0b_common as C
import pytest

from open_notebook.integrations.graphrag.eval.authlivepn02d import (
    ProviderRunNotAuthorized,
    frozen_provider_config_id,
)
from open_notebook.integrations.graphrag.eval.provbindpn02d import (
    ProviderBindingMaterializationError,
    materialize_provider_binding,
    plan_sidecar_boot,
)
from open_notebook.integrations.graphrag.eval.provider_binding08 import (
    FROZEN_PROVIDER_SECRET_ENV,
)


def test_materialization_refused_without_provider_run_auth():
    with pytest.raises(ProviderRunNotAuthorized):
        materialize_provider_binding(None, present_secret_envs=frozenset({FROZEN_PROVIDER_SECRET_ENV}))


def test_materialization_attested_with_present_secret():
    auth = C.provider_run_auth("b1")
    m = materialize_provider_binding(
        auth, present_secret_envs=frozenset({FROZEN_PROVIDER_SECRET_ENV})
    )
    assert m.attested is True
    assert m.failure_reasons == ()
    assert m.provider_config_id == frozen_provider_config_id()
    assert m.llm_model == "openai/gpt-4o-mini"
    assert m.embedding_model == "openai/text-embedding-3-small"
    assert m.embedding_dim == 1536


def test_materialization_fails_closed_on_missing_secret():
    auth = C.provider_run_auth("b2")
    m = materialize_provider_binding(auth, present_secret_envs=frozenset())
    assert m.attested is False
    assert any("REQUIRED_RUNTIME_SECRET_MISSING" in r for r in m.failure_reasons)


def test_materialized_binding_carries_no_secret_value():
    import os

    auth = C.provider_run_auth("b3")
    m = materialize_provider_binding(
        auth, present_secret_envs=frozenset({FROZEN_PROVIDER_SECRET_ENV})
    )
    blob = json.dumps(m.as_public_dict())
    # The env-var NAME may appear (design §47); a secret VALUE never may.
    assert FROZEN_PROVIDER_SECRET_ENV in blob  # name only
    real_value = os.environ.get(FROZEN_PROVIDER_SECRET_ENV, "").strip()
    if real_value:
        assert real_value not in blob  # the ambient secret VALUE is never serialized
    for value_token in ("sk-", "bearer "):
        assert value_token not in blob.lower()
    # the secret container var maps to a NAME, never a value.
    assert m.container_secret_env_map["LLM_BINDING_API_KEY"] == FROZEN_PROVIDER_SECRET_ENV


def test_boot_plan_requires_auth_and_attested_binding():
    auth = C.provider_run_auth("b4")
    m = materialize_provider_binding(
        auth, present_secret_envs=frozenset({FROZEN_PROVIDER_SECRET_ENV})
    )
    plan = plan_sidecar_boot(
        auth, notebook_id="NB_A", workspace_id="nb_x", endpoint="eval-null://x",
        materialized=m,
    )
    blob = json.dumps(plan.as_public_dict())
    assert "LLM_BINDING_API_KEY" in blob  # secret var name only, inherited
    # public env carries model/host values but NO key value.
    assert plan.public_env["LLM_MODEL"] == "openai/gpt-4o-mini"
    assert "LLM_BINDING_API_KEY" not in plan.public_env


def test_boot_plan_refused_without_auth():
    auth = C.provider_run_auth("b5")
    m = materialize_provider_binding(
        auth, present_secret_envs=frozenset({FROZEN_PROVIDER_SECRET_ENV})
    )
    with pytest.raises(ProviderRunNotAuthorized):
        plan_sidecar_boot(
            None, notebook_id="NB_A", workspace_id="nb_x", endpoint="e", materialized=m
        )


def test_boot_plan_refused_when_binding_unattested():
    auth = C.provider_run_auth("b6")
    m = materialize_provider_binding(auth, present_secret_envs=frozenset())  # missing secret
    with pytest.raises(ProviderBindingMaterializationError):
        plan_sidecar_boot(
            auth, notebook_id="NB_A", workspace_id="nb_x", endpoint="e", materialized=m
        )
