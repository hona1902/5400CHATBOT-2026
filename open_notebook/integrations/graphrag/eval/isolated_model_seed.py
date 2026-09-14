"""Shared, provider-free frozen embedding-Model seed for isolated eval namespaces.

EVALUATION-ONLY. Nothing in production imports this (``PRODUCTION_IMPORTS_EVAL = NO``).

This is the SINGLE SOURCE OF TRUTH for writing the frozen embedding ``Model`` +
``DefaultModels.default_embedding_model`` binding into an ACTIVE isolated Surreal
namespace, used by BOTH the GraphRAG-08 micro-precheck / orchestrators (via
``model_seed_cm``) and the PN02D-B1 real execution path
(``realseamspn02d.run_live_b1_execution``).

A fresh isolated namespace (``isolation08.isolated_surreal_eval_runtime``) bootstraps
schema only — it holds NO ``model`` records and ``DefaultModels.default_embedding_model``
is ``None`` — so the normal embedding stack (``model_manager.get_embedding_model`` via
``embed_source_command`` / ``generate_embeddings``) and the real frozen-model attestor
(``realseamspn02d.build_real_model_attestor``) both fail closed. Seeding this ONE record
+ default binding makes all three (corpus source embedding, query embedding, model
attestation) resolve the SAME model.

Zero-provider posture: seeding is control/data setup ONLY. No embedding request, no
provider network call, no discovery (``MODEL_SEED_PROVIDER_CALLS = 0``); no stored
credential (the ``OPENROUTER_API_KEY`` env-key fallback is resolved late, by NAME, at
actual embed time).

Security model (PN02D-B1-EW2, final shape after B1EW2-RR4-M1 — Cycle #5):

  * **NO CALLER-SUPPLIED CLEANUP AUTHORITY, TRULY LEXICAL.** The public surface is a
    single async context manager, ``seeded_frozen_embedding_model``. There is NO exported
    cleanup function, NO module-level teardown primitive, NO ownership handle/token/
    dataclass, and NO module-reachable construction key. The destructive teardown is a
    **zero-argument closure defined inside the context manager** (``_cleanup_owned_seed``);
    all cleanup state (the created model id, the prior default, whether the default was
    changed, and the isolation identity) is captured from that invocation's **lexical
    locals**. Because there is nothing importable to call and the closure accepts no
    arguments, an in-process caller cannot supply, forge, retarget, or reconstruct cleanup
    authority — not even from observable DB state or constants.
  * **ISOLATION-BOUND.** Both the create and the cleanup mutation boundaries call the
    canonical ``isolation08.require_active_isolation()`` FIRST — before any DB access.
  * **STATE-BOUND, ordered, restore-before-delete.** Cleanup runs only for a model THIS
    scope created; before any destructive mutation it re-verifies active isolation, the
    isolation identity (no cross-namespace teardown), that the current default is still
    the owned model, and that the target still matches the frozen identity — otherwise it
    FAILS CLOSED (raises). It restores the prior default FIRST, then deletes the owned
    model (a delete never runs after a failed restore), so a failure cannot leave the
    default dangling on a removed record. A reused pre-existing exact-match model is left
    intact. The context manager's ``__aexit__`` runs cleanup exactly once.

The frozen identity is derived from the same constants the runtime binding
(``provider_binding08``) and the attestor (``vectoradapterpn02d``) validate against.
"""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from typing import AsyncIterator, Optional, Tuple

from loguru import logger

from open_notebook.integrations.graphrag.eval.provider_binding08 import (
    FROZEN_EMBEDDING_MODEL,
)
from open_notebook.integrations.graphrag.eval.vectoradapterpn02d import (
    FROZEN_EMBEDDING_PROVIDER,
)

#: The ``Model.type`` value for an embedding model (matches the normal model registry).
FROZEN_EMBEDDING_TYPE = "embedding"


class IsolatedModelSeedError(RuntimeError):
    """The frozen embedding model could not be seeded/cleaned up (fail-closed, content-free)."""


class ConflictingEmbeddingModelError(IsolatedModelSeedError):
    """A DIFFERENT default embedding model is already bound — never silently overwritten."""


class SeedCleanupOwnershipError(IsolatedModelSeedError):
    """Cleanup state does not match the owned seed (fail-closed; never destructive)."""


def _active_isolation_identity() -> Tuple[Optional[str], Optional[str]]:
    """The active isolated (namespace, database) — read AFTER ``require_active_isolation``.

    ``isolation08`` overrides these env vars to the temp identity while a runtime is active
    (and ``require_active_isolation`` verifies they equal the captured temp identity), so
    reading them here yields the active isolated identity without a new isolation08 accessor.
    """
    return (os.environ.get("SURREAL_NAMESPACE"), os.environ.get("SURREAL_DATABASE"))


def is_frozen_embedding_identity(model: object) -> bool:
    """Whether ``model`` is EXACTLY the frozen embedding identity (name+provider+type)."""
    return (
        str(getattr(model, "name", "") or "") == FROZEN_EMBEDDING_MODEL
        and str(getattr(model, "provider", "") or "") == FROZEN_EMBEDDING_PROVIDER
        and str(getattr(model, "type", "") or "") == FROZEN_EMBEDDING_TYPE
    )


@asynccontextmanager
async def seeded_frozen_embedding_model() -> AsyncIterator[str]:
    """Seed the frozen embedding model as the bound default inside the active isolation.

    The ONLY public entrypoint of the seed lifecycle. It yields the resolved frozen model
    id (an IDENTIFIER for legitimate use / reporting — NOT cleanup authority) and owns the
    entire teardown itself; a caller cannot supply, forge, or retarget what is cleaned up.

    Conflict policy (§15), so a mismatched model is NEVER silently accepted:

      * default unset (fresh isolated namespace) -> CREATE the frozen model + bind it;
      * default already the EXACT frozen identity -> REUSE it (idempotent, no create);
      * default bound to a DIFFERENT model -> ``ConflictingEmbeddingModelError`` (fail-closed).

    On exit, ONLY a model THIS scope created is torn down. The destructive teardown is a
    **zero-argument closure defined inside this function** (``_cleanup_owned_seed``) that
    reads the owned model id, prior default and creation-time isolation identity from this
    invocation's LEXICAL LOCALS. There is NO module-level teardown primitive, so an
    in-process caller has nothing importable to call with reconstructed cleanup state —
    cleanup authority is truly lexical to one CM invocation (B1EW2-RR4-M1). A reused
    pre-existing model is left intact. PROVIDER-FREE throughout.

    Active isolation is asserted **UNCONDITIONALLY** before any DB access — there is NO
    caller-controlled ``require_isolation`` / bypass parameter (B1EW2-RR5-H1): the create/
    bind boundary can never touch the normal application namespace. A unit test that must
    exercise the body without a live isolated runtime patches ``require_active_isolation``
    in this module rather than disabling production safety.
    """
    from open_notebook.integrations.graphrag.eval.isolation08 import (
        require_active_isolation,
    )

    # UNCONDITIONAL guard, before ANY DefaultModels/Model access (B1EW2-RR5-H1 / R1-H1).
    require_active_isolation()

    from open_notebook.ai.models import DefaultModels, Model

    # Private lifecycle state — lexical locals only; never returned, yielded, or reachable.
    created = False
    created_model_id: Optional[str] = None
    prior_default: Optional[str] = None
    owner_namespace, owner_database = _active_isolation_identity()

    async def _cleanup_owned_seed() -> None:
        """Ordered, fail-closed teardown of the seed THIS invocation created.

        A zero-argument closure: it accepts no caller cleanup state and is never returned,
        yielded, exported, registered, or attached to any object. Every value it acts on
        (``created_model_id``, ``prior_default``, ``owner_namespace``, ``owner_database``)
        is captured lexically from the enclosing invocation, so no external code can invoke
        it or reconstruct its authority. Fails closed (raises) on any isolation / identity /
        ownership mismatch BEFORE any mutation; restores the prior default before deleting
        the owned model, and never deletes after a failed restore.
        """
        # (1) active isolation, before any DB access; (2) same isolation identity as creation.
        require_active_isolation()
        if _active_isolation_identity() != (owner_namespace, owner_database):
            raise SeedCleanupOwnershipError(
                "active isolation identity differs from the seed's creation identity "
                "(cross-namespace teardown refused; fail-closed)"
            )

        # (3) the current default must still be the owned seeded model — else fail closed
        #     (some other operation changed it; do not overwrite/delete unrelated state).
        cleanup_defaults = await DefaultModels.get_instance()
        if str(cleanup_defaults.default_embedding_model) != str(created_model_id):
            raise SeedCleanupOwnershipError(
                "current default embedding model is no longer the owned seeded model — the "
                "default changed during the seed scope; failing closed (no destructive "
                "cleanup)"
            )

        # (4) the target must still match the frozen identity — a mutated/replaced record
        #     with the same id is never destructively deleted (Model.get raises if missing).
        #     created_model_id is non-None on the created path (the only path that runs this
        #     closure); str() both narrows for the type checker and matches step (3).
        owned = await Model.get(str(created_model_id))
        if not is_frozen_embedding_identity(owned):
            raise SeedCleanupOwnershipError(
                "owned model identity was mutated/replaced — refusing destructive delete"
            )

        # (5) restore the prior default FIRST, then delete — a delete NEVER runs after a
        #     failed restore, so the default can never dangle on a removed record.
        cleanup_defaults = await DefaultModels.get_instance()
        cleanup_defaults.default_embedding_model = prior_default
        await cleanup_defaults.update()
        await owned.delete()
        logger.debug(
            "[model-seed] owned temp model torn down (default restored, model deleted)"
        )

    defaults = await DefaultModels.get_instance()
    existing_id = defaults.default_embedding_model

    if existing_id:
        existing = await Model.get(existing_id)
        if existing is not None and is_frozen_embedding_identity(existing):
            model_id = str(existing_id)  # EXACT_MATCH_REUSE — not owned, no teardown
        else:
            raise ConflictingEmbeddingModelError(
                "a non-frozen default embedding model is already bound in the isolated "
                "namespace (refusing to overwrite; fail-closed)"
            )
    else:
        prior_default = existing_id  # None on a fresh namespace
        model = Model(
            name=FROZEN_EMBEDDING_MODEL,
            provider=FROZEN_EMBEDDING_PROVIDER,
            type=FROZEN_EMBEDDING_TYPE,
            credential=None,
        )
        await model.save()
        created_model_id = str(model.id)
        model_id = created_model_id
        defaults = await DefaultModels.get_instance()
        defaults.default_embedding_model = created_model_id
        await defaults.update()
        created = True

    try:
        yield model_id
    finally:
        if created and created_model_id is not None:
            await _cleanup_owned_seed()


__all__ = [
    "FROZEN_EMBEDDING_TYPE",
    "IsolatedModelSeedError",
    "ConflictingEmbeddingModelError",
    "SeedCleanupOwnershipError",
    "is_frozen_embedding_identity",
    "seeded_frozen_embedding_model",
]
