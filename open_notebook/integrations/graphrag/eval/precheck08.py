"""GraphRAG-08 AUTHORIZED live micro-precheck orchestrator (EVALUATION-ONLY).

Nothing in production imports this. It wires together the already-approved pieces
for ONE bounded, provider-backed execution-correctness run:

  Option-A isolation (isolation08) -> temp embedding Model seed (normal supported
  path) -> pinned LightRAG sidecar -> runner08 (V/GQ/GD) over a DEV subset within
  the HARD caps (<=8 Sources, <=6 DEV queries, HOLDOUT=0) -> content-free artifact
  -> mandatory owned cleanup + full restoration.

Guarantees baked in:
  * The whole DB side runs inside a dedicated temporary Surreal namespace (08A);
    the temp namespace is dropped on exit, removing benchmark Sources + the temp
    Model atomically. The runner's Option-A guard refuses the normal DB.
  * LightRAG documents are deleted per run-owned canonical Source id BEFORE the
    namespace is dropped (SHARED_BUT_OWNED; no broad purge).
  * Provider egress is bounded by the caps. No fixture edit, no HOLDOUT, no tuning.
  * The artifact is content-free (report08). Raw provider payloads never persist.

This module performs live provider traffic and starts a Docker sidecar; it must be
invoked only under the explicit live-micro-precheck authorization.
"""

from __future__ import annotations

import asyncio
import json
import subprocess
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

from loguru import logger

_COMPOSE_FILE = "deploy/graphrag-poc/docker-compose.graphrag.yml"
_EXPECTED_EMBED_DIM = 1536


@dataclass
class PrecheckState:
    run_id: str = ""
    state: str = "PLANNED"
    temp_namespace: str = ""
    temp_database: str = ""
    temp_model_id: Optional[str] = None
    prior_default_embedding: Optional[str] = None
    selected_source_keys: tuple = ()
    selected_query_ids: tuple = ()
    created_ids: List[str] = field(default_factory=list)
    normal_namespace: str = ""
    normal_database: str = ""
    normal_source_count_before: Optional[int] = None
    normal_source_count_after: Optional[int] = None
    embedding_dimension_observed: Optional[int] = None
    sidecar_started: bool = False
    sidecar_stopped: bool = False
    lightrag_cleanup_ok: bool = False
    temp_model_cleanup_ok: bool = False
    fixture_hash_before: str = ""
    fixture_hash_after: str = ""
    failures: List[str] = field(default_factory=list)


# ---- sidecar helpers -------------------------------------------------------

def _compose(args: List[str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["docker", "compose", "--env-file", ".env", "-f", _COMPOSE_FILE, *args],
        capture_output=True,
        text=True,
        timeout=300,
    )


def start_sidecar() -> None:
    out = _compose(["up", "-d"])
    if out.returncode != 0:
        # Never echo full stderr (may include env); surface only the return code.
        raise RuntimeError(f"sidecar compose up failed (rc={out.returncode})")


def stop_sidecar() -> None:
    _compose(["down"])


def sidecar_running() -> bool:
    out = _compose(["ps", "--status", "running", "-q"])
    return bool(out.stdout.strip())


async def await_sidecar_health(config, *, timeout_s: float = 120.0) -> Dict[str, object]:
    """Poll the sidecar /health until healthy (content-free result)."""
    from open_notebook.integrations.graphrag.client import GraphRAGClient

    client = GraphRAGClient(config)
    deadline = time.monotonic() + timeout_s
    last = ""
    while time.monotonic() < deadline:
        health = await client.health()
        if health.healthy:
            return {"healthy": True, "version": health.version}
        last = health.detail
        await asyncio.sleep(3.0)
    raise RuntimeError(f"sidecar not healthy within {timeout_s}s: {last}")


# ---- temp embedding Model seed (normal supported path) ---------------------

async def seed_temp_embedding_model() -> tuple[str, Optional[str]]:
    """Create a temp OpenRouter embedding Model in the CURRENT (isolated) DB and
    set it as the default. Returns (temp_model_id, prior_default). Env-key fallback
    (OPENROUTER_API_KEY); no stored credential. Must be called inside isolation."""
    from open_notebook.ai.models import DefaultModels, Model

    defaults = await DefaultModels.get_instance()
    prior = defaults.default_embedding_model
    model = Model(
        name="openai/text-embedding-3-small",
        provider="openrouter",
        type="embedding",
        credential=None,
    )
    await model.save()
    model_id = str(model.id)
    defaults = await DefaultModels.get_instance()
    defaults.default_embedding_model = model_id
    await defaults.update()
    return model_id, prior


async def restore_default_and_delete_model(
    temp_model_id: str, prior_default: Optional[str]
) -> bool:
    """Restore the prior default embedding model and delete the temp Model record.
    Best-effort; the temp namespace drop also removes both. Returns success."""
    from open_notebook.ai.models import DefaultModels, Model

    try:
        defaults = await DefaultModels.get_instance()
        defaults.default_embedding_model = prior_default
        await defaults.update()
        model = await Model.get(temp_model_id)
        if model is not None:
            await model.delete()
        return True
    except Exception as exc:  # noqa: BLE001
        logger.error(f"[gr08-precheck] temp model cleanup failed: {type(exc).__name__}")
        return False


async def _embedding_dim_probe() -> int:
    """One tiny embedding call to confirm provider connectivity + dimension."""
    from open_notebook.utils.embedding import generate_embedding

    vec = await generate_embedding("graphrag08 precheck dimension probe")
    return len(vec or [])


async def _normal_source_count() -> Optional[int]:
    from open_notebook.database.repository import repo_query

    try:
        rows = await repo_query("SELECT VALUE id FROM source")
        return len(rows or [])
    except Exception:  # noqa: BLE001
        return None


# ---- orchestrator ----------------------------------------------------------

async def run_micro_precheck(
    *, max_sources: int = 8, max_queries: int = 6, artifact_dir: Optional[Path] = None
) -> PrecheckState:
    from open_notebook.integrations.graphrag.config import load_config
    from open_notebook.integrations.graphrag.eval import dataset08 as d
    from open_notebook.integrations.graphrag.eval import report08 as r
    from open_notebook.integrations.graphrag.eval.gd_seam import GDQueryClient
    from open_notebook.integrations.graphrag.eval.isolation08 import (
        isolated_surreal_eval_runtime,
        normal_identity,
    )
    from open_notebook.integrations.graphrag.eval.runner08 import (
        EvalRunConfig08,
        GraphRAG08EvalRunner,
        select_precheck_subset,
    )
    from open_notebook.integrations.graphrag.service import GraphRAGService

    st = PrecheckState()

    # ---- static: fixture integrity BEFORE any provider traffic (§25) --------
    ok, h = d.verify_integrity()
    st.fixture_hash_before = h if ok else "MISMATCH"
    if not ok or h != "a58a68535c345e18f0263904f818e4e2068a164056408665d8bb9233eceb143d":
        st.failures.append("fixture integrity mismatch before preflight")
        st.state = "PREFLIGHT_FAIL"
        return st

    bench = d.load_benchmark08()
    d.validate_frozen_shape(bench)
    sk, qids = select_precheck_subset(bench, max_sources=max_sources, max_queries=max_queries)
    st.selected_source_keys = sk
    st.selected_query_ids = qids
    if len(sk) > max_sources or len(qids) > max_queries:
        st.failures.append("cap exceeded by subset selection")
        st.state = "PREFLIGHT_FAIL"
        return st
    # HOLDOUT must be zero.
    qmap = {q.query_id: q for q in bench.queries}
    if any(qmap[q].split.value != "dev" for q in qids):
        st.failures.append("non-DEV query selected")
        st.state = "PREFLIGHT_FAIL"
        return st

    st.normal_namespace, st.normal_database = normal_identity()
    st.normal_source_count_before = await _normal_source_count()

    import os

    prior_graph_flag = os.environ.get("OPEN_NOTEBOOK_GRAPHRAG_ENABLED")
    evaluations = None
    metadata: Dict[str, object] = {}
    try:
        # ---- start sidecar + process-local GraphRAG enable (§21/§22) --------
        os.environ["OPEN_NOTEBOOK_GRAPHRAG_ENABLED"] = "true"
        st.state = "SIDECAR_START"
        start_sidecar()
        st.sidecar_started = True
        health = await await_sidecar_health(load_config())
        logger.info(f"[gr08-precheck] sidecar healthy version={health.get('version')}")

        # ---- isolated Surreal runtime (08A) ---------------------------------
        st.state = "ISOLATION_CREATE"
        async with isolated_surreal_eval_runtime() as ctx:
            st.run_id = ctx.run_id
            st.temp_namespace = ctx.namespace
            st.temp_database = ctx.database
            st.state = "MODEL_SEED"
            st.temp_model_id, st.prior_default_embedding = await seed_temp_embedding_model()
            st.embedding_dimension_observed = await _embedding_dim_probe()
            if st.embedding_dimension_observed != _EXPECTED_EMBED_DIM:
                raise RuntimeError(
                    f"embedding dim {st.embedding_dimension_observed} != {_EXPECTED_EMBED_DIM}"
                )

            service = GraphRAGService(load_config())
            gd_client = GDQueryClient(load_config())
            runner = GraphRAG08EvalRunner(
                bench,
                service=service,
                gd_client=gd_client,
                selected_source_keys=sk,
                selected_query_ids=qids,
                # Bounded, but generous enough for 8 sources' LLM graph extraction
                # through OpenRouter (still no unbounded polling; §28).
                config=EvalRunConfig08(
                    index_ready_timeout_s=600.0, poll_interval_s=5.0
                ),
                combined_sha256=st.fixture_hash_before,
            )
            try:
                st.state = "FULL_INDEX"
                await runner.create_and_index()
                st.created_ids = list(runner.created_ids)
                st.state = "FULL_QUERY"
                evaluations = await runner.run()
                metadata = runner.build_metadata()
                st.state = "ANALYZE"
                artifact = r.build_artifact(
                    metadata, evaluations, corpus_size=runner.corpus_size
                )
                out_dir = artifact_dir or Path(".artifacts") / "graphrag-08" / st.run_id
                r.write_artifact(out_dir / "precheck.json", artifact)
                _write_manifest(out_dir / "manifest.json", st, runner)
            finally:
                # ---- owned cleanup: LightRAG per-id, then temp Model (§46) ---
                st.state = "CLEANUP"
                try:
                    cr = await runner.cleanup()
                    st.lightrag_cleanup_ok = cr.clean and not cr.errors
                except Exception as exc:  # noqa: BLE001
                    st.failures.append(f"runner cleanup: {type(exc).__name__}")
                st.temp_model_cleanup_ok = await restore_default_and_delete_model(
                    st.temp_model_id or "", st.prior_default_embedding
                )
            # exiting the context drops the temp namespace (removes sources+model)
    except Exception as exc:  # noqa: BLE001
        st.failures.append(f"{st.state}: {type(exc).__name__}: {exc}")
    finally:
        # ---- stop sidecar + restore graph flag (§49/§50) --------------------
        try:
            stop_sidecar()
        except Exception as exc:  # noqa: BLE001
            st.failures.append(f"sidecar stop: {type(exc).__name__}")
        st.sidecar_stopped = not sidecar_running()
        if prior_graph_flag is None:
            os.environ.pop("OPEN_NOTEBOOK_GRAPHRAG_ENABLED", None)
        else:
            os.environ["OPEN_NOTEBOOK_GRAPHRAG_ENABLED"] = prior_graph_flag

    # ---- postchecks (§51/§53) ----------------------------------------------
    st.normal_source_count_after = await _normal_source_count()
    ok2, h2 = d.verify_integrity()
    st.fixture_hash_after = h2 if ok2 else "MISMATCH"

    if not st.failures and evaluations is not None:
        st.state = "COMPLETE"
    else:
        st.state = "FAILED"
    return st


def _write_manifest(path: Path, st: PrecheckState, runner) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    manifest = {
        "run_id": st.run_id,
        "run_type": "MICRO_PRECHECK",
        "fixture_version": "graphrag_08_eval_v1",
        "fixture_hash": st.fixture_hash_before,
        "temp_namespace": st.temp_namespace,
        "temp_database": st.temp_database,
        "temp_model_id": st.temp_model_id,
        "selected_source_keys": list(st.selected_source_keys),
        "selected_query_ids": list(st.selected_query_ids),
        "created_source_ids": list(st.created_ids),
        "lightrag_ownership": {
            k: v for k, v in runner.key_to_source_id.items()
        },
        "created_utc": datetime.now(timezone.utc).isoformat(),
    }
    with path.open("w", encoding="utf-8") as fh:
        json.dump(manifest, fh, indent=2, sort_keys=True)


__all__ = ["run_micro_precheck", "PrecheckState"]
