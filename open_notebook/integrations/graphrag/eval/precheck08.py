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
    #: GraphRAG-08B content-free telemetry (survives a pre-ANALYZE abort).
    run_validity: str = "UNKNOWN"
    graphrag_indexed_count: Optional[int] = None
    retry_accounting: Optional[dict] = None
    failed_logical_ids: List[Optional[str]] = field(default_factory=list)
    #: GraphRAG-08C preflight-safety + sidecar-readiness observability.
    authorization_label: str = "UNSPECIFIED"
    normal_db_before: Optional[dict] = None
    normal_db_after: Optional[dict] = None
    #: NORMAL_DB_UNCHANGED verdict — YES only from two valid concrete observations,
    #: never from null == null (task §3/§18). NOT_PROVEN blocks operational sign-off.
    normal_db_unchanged: str = "NOT_PROVEN"
    preflight_blocked: bool = False
    isolation_entered: bool = False
    sidecar_diagnostic: Optional[dict] = None
    failure_stage: str = ""
    failure_reason_code: str = ""
    #: GraphRAG-08D launcher import-path preflight (content-free); fixes the
    #: attempt-#4 ``No module named 'commands'`` defect by fail-closing BEFORE any
    #: normal-DB read / sidecar / isolation / Source creation / provider traffic.
    launcher_preflight: Optional[dict] = None


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


def _container_id() -> Optional[str]:
    """First running-or-not compose container id (content-free), or None."""
    out = _compose(["ps", "-a", "-q"])
    if out.returncode != 0:
        return None
    cid = (out.stdout or "").strip().splitlines()
    return cid[0].strip() if cid and cid[0].strip() else None


def _inspect_line(container_id: str) -> Optional[str]:
    """Targeted ``docker inspect`` of ONLY coarse fields (never logs/env; §21)."""
    from open_notebook.integrations.graphrag.eval.sidecar_diag08 import INSPECT_FORMAT

    out = subprocess.run(
        ["docker", "inspect", "-f", INSPECT_FORMAT, container_id],
        capture_output=True,
        text=True,
        timeout=30,
    )
    if out.returncode != 0:
        return None
    line = (out.stdout or "").strip()
    return line or None


def _port_open(host: str, port: int, *, timeout_s: float = 2.0) -> bool:
    import socket

    try:
        with socket.create_connection((host, port), timeout=timeout_s):
            return True
    except OSError:
        return False


async def gather_sidecar_diagnostic(
    config, *, timeout_seconds: float, elapsed_seconds: Optional[float]
):
    """Best-effort content-safe SIDECAR_START diagnostic (task §7/§8/§21).

    Reads ONLY coarse facts: container running/exit/health/restart via a targeted
    inspect template, TCP port openness, and a single health probe's success/status
    class (never its body). Never raises — a probing failure degrades the diagnostic
    to INSPECT_UNAVAILABLE rather than masking the original startup failure. Tests
    monkeypatch this whole function."""
    from urllib.parse import urlparse

    from open_notebook.integrations.graphrag.client import GraphRAGClient
    from open_notebook.integrations.graphrag.eval.sidecar_diag08 import (
        build_sidecar_diagnostic,
        parse_inspect_line,
        with_health,
    )

    # -- container facts (coarse) --
    obs = parse_inspect_line(None)
    try:
        cid = _container_id()
        if cid is not None:
            obs = parse_inspect_line(_inspect_line(cid))
    except Exception:  # noqa: BLE001 - probing must never mask the real failure
        obs = parse_inspect_line(None)

    # -- port + one coarse health probe (status/success only, never body) --
    port_open: Optional[bool] = None
    health_reachable: Optional[bool] = None
    health_status_code: Optional[int] = None
    healthy = False
    try:
        base = getattr(config, "base_url", "") or ""
        parsed = urlparse(base)
        if parsed.hostname and parsed.port:
            port_open = _port_open(parsed.hostname, int(parsed.port))
        if base:
            client = GraphRAGClient(config)
            health = await client.health()
            health_reachable = True
            healthy = bool(getattr(health, "healthy", False))
            code = getattr(health, "status_code", None)
            health_status_code = int(code) if isinstance(code, int) else None
    except Exception:  # noqa: BLE001 - unreachable health -> coarse reachable=False
        health_reachable = False

    obs = with_health(
        obs,
        port_open=port_open,
        health_reachable=health_reachable,
        health_status_code=health_status_code,
        healthy=healthy,
        timeout_reached=True,
    )
    return build_sidecar_diagnostic(
        obs, timeout_seconds=timeout_seconds, elapsed_seconds=elapsed_seconds
    )


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
                graphrag_config=load_config(),
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
        # Type name only — never str(exc), which could carry provider/LightRAG text
        # into the failure record (GraphRAG-08B raw-containment; review LOW-1).
        st.failures.append(f"{st.state}: {type(exc).__name__}")
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


async def run_full_benchmark(
    *,
    authorization_label: str = "UNSPECIFIED",
    artifact_dir: Optional[Path] = None,
) -> PrecheckState:
    """AUTHORIZED full frozen 75-Source / 60-query value benchmark (one run).

    Same isolated, owned, content-free machinery as the micro-precheck, but over
    ALL 75 Sources and ALL 60 queries (DEV + HOLDOUT). candidate_fraction uses the
    full 75-Source denominator. Value interpretation is done from the artifact
    (HOLDOUT authoritative); this function only executes + cleans up.

    GraphRAG-08C pre-run safety ORDER (task §5): fixture verification -> normal DB
    identity + baseline count verification -> (STOP BEFORE sidecar if unreadable) ->
    sidecar startup (with content-safe diagnostics on failure) -> isolation runtime.
    ``authorization_label`` is provenance METADATA only (task §13/§14): it never
    affects the fixture, provider, model, retry policy, concurrency, query selection,
    HOLDOUT access, or metrics.
    """
    from open_notebook.integrations.graphrag.config import load_config
    from open_notebook.integrations.graphrag.eval import dataset08 as d
    from open_notebook.integrations.graphrag.eval import launcher_preflight08 as lp
    from open_notebook.integrations.graphrag.eval import preflight08 as pf
    from open_notebook.integrations.graphrag.eval import report08 as r
    from open_notebook.integrations.graphrag.eval.gd_seam import GDQueryClient
    from open_notebook.integrations.graphrag.eval.isolation08 import (
        isolated_surreal_eval_runtime,
        make_run_id,
        normal_identity,
    )
    from open_notebook.integrations.graphrag.eval.runner08 import (
        EvalRunConfig08,
        GraphRAG08EvalRunner,
    )
    from open_notebook.integrations.graphrag.service import GraphRAGService

    st = PrecheckState()
    st.authorization_label = authorization_label
    # Pre-isolation run id so a PREFLIGHT / SIDECAR_START abort still has a stable id
    # for durable content-free failure telemetry (task §11).
    st.run_id = make_run_id()

    # ---- 1) fixture verification (§5) --------------------------------------
    ok, h = d.verify_integrity()
    st.fixture_hash_before = h if ok else "MISMATCH"
    st.fixture_hash_after = st.fixture_hash_before
    out_dir = artifact_dir or Path(".artifacts") / "graphrag-08-full" / st.run_id
    if not ok or h != "a58a68535c345e18f0263904f818e4e2068a164056408665d8bb9233eceb143d":
        st.failures.append("fixture integrity mismatch before full run")
        st.failure_stage = "PREFLIGHT"
        st.failure_reason_code = "FIXTURE_HASH_MISMATCH"
        st.normal_db_unchanged = pf.UNCHANGED_NOT_PROVEN
        st.preflight_blocked = True
        st.state = "PREFLIGHT_FAIL"
        return st

    bench = d.load_benchmark08()
    d.validate_frozen_shape(bench)
    sk = tuple(s.key for s in bench.sources)          # all 75
    qids = tuple(q.query_id for q in bench.queries)   # all 60 (DEV + HOLDOUT)
    st.selected_source_keys = sk
    st.selected_query_ids = qids

    # ---- 1.5) launcher import-path preflight (GraphRAG-08D §5/§8/§9) --------
    # Resolve the repo root deterministically from the eval module's own location
    # (never the shell cwd) and verify the EXACT import surface the full-index path
    # requires (``commands.embedding_commands``). This FAILS CLOSED here — before the
    # normal-DB read, sidecar, isolation, Source creation, and any provider traffic —
    # so an invalid import environment can never again be discovered only after 75
    # Sources have been created (the attempt-#4 defect: ``No module named
    # 'commands'`` inside ``runner08._vector_embed_all``).
    try:
        launcher = lp.run_launcher_preflight()
        st.launcher_preflight = launcher.as_dict()
        lp.require_launcher_ready(launcher)
    except Exception as exc:  # noqa: BLE001 - ANY launcher-preflight error fails closed
        # A LauncherImportPathError carries a precise reason code; any other error
        # (e.g. an unresolvable/deleted cwd) still fails closed under the umbrella
        # code, so the durable telemetry is never lost. This is strictly BEFORE the
        # normal-DB read / sidecar / provider, so no live work can have started.
        reason = getattr(exc, "reason_code", None) or lp.LauncherReasonCode.IMPORT_PATH_INVALID
        st.failures.append(f"launcher import path invalid: {reason}")
        st.failure_stage = "PREFLIGHT"
        st.failure_reason_code = reason
        st.normal_db_unchanged = pf.UNCHANGED_NOT_PROVEN
        st.preflight_blocked = True
        st.state = "PREFLIGHT_FAIL"
        try:
            _write_preisolation_failure_telemetry(
                out_dir, st,
                failure_stage="PREFLIGHT",
                failure_reason_code=reason,
            )
        except Exception as exc2:  # noqa: BLE001 - never mask the block
            st.failures.append(f"preflight telemetry write: {type(exc2).__name__}")
        return st  # STOP BEFORE normal-DB read / sidecar / provider (§8)

    # ---- 2)+3) normal DB identity + baseline count verification (§2/§5) -----
    # A full run must NOT begin unless the normal DB baseline reads as CONCRETE
    # values; an unreadable baseline FAILS CLOSED here, BEFORE any sidecar/provider
    # action (task §2/§5/§16). null is never accepted as "unchanged" (task §3).
    st.normal_namespace, st.normal_database = normal_identity()
    before = await pf.read_normal_db_baseline()
    st.normal_db_before = before.as_dict()
    st.normal_source_count_before = before.source_count
    try:
        pf.require_readable_baseline(before)
    except pf.NormalDbBaselineError:
        st.failures.append(f"normal DB baseline unreadable: {before.reason_code}")
        st.failure_stage = "PREFLIGHT"
        st.failure_reason_code = before.reason_code
        st.normal_db_unchanged = pf.UNCHANGED_NOT_PROVEN
        st.preflight_blocked = True
        st.state = "PREFLIGHT_FAIL"
        try:
            _write_preisolation_failure_telemetry(
                out_dir, st,
                failure_stage="PREFLIGHT",
                failure_reason_code=before.reason_code,
            )
        except Exception as exc:  # noqa: BLE001 - never mask the block
            st.failures.append(f"preflight telemetry write: {type(exc).__name__}")
        return st  # STOP BEFORE sidecar startup (§5/§16)

    import os

    prior_graph_flag = os.environ.get("OPEN_NOTEBOOK_GRAPHRAG_ENABLED")
    evaluations = None
    try:
        os.environ["OPEN_NOTEBOOK_GRAPHRAG_ENABLED"] = "true"
        # ---- 4) sidecar startup — ONLY after the baseline is proven (§5) ----
        st.state = "SIDECAR_START"
        started_at = time.monotonic()
        start_sidecar()
        st.sidecar_started = True
        try:
            health = await await_sidecar_health(load_config())
            logger.info(f"[gr08-full] sidecar healthy version={health.get('version')}")
        except Exception:
            # GraphRAG-08C: content-safe SIDECAR_START diagnostics + durable
            # pre-isolation failure telemetry (task §7/§11/§22), BEFORE re-raising.
            elapsed = time.monotonic() - started_at
            try:
                diag = await gather_sidecar_diagnostic(
                    load_config(), timeout_seconds=120.0, elapsed_seconds=elapsed
                )
                st.sidecar_diagnostic = diag.as_dict()
                st.failure_reason_code = diag.failure_reason_code
            except Exception as exc:  # noqa: BLE001 - diagnostic must not mask failure
                st.failures.append(f"sidecar diagnostic: {type(exc).__name__}")
            st.failure_stage = "SIDECAR_START"
            st.normal_db_unchanged = pf.UNCHANGED_NOT_PROVEN
            try:
                _write_preisolation_failure_telemetry(
                    out_dir, st,
                    failure_stage="SIDECAR_START",
                    failure_reason_code=st.failure_reason_code or "SIDECAR_START_UNKNOWN",
                    sidecar_diagnostic=st.sidecar_diagnostic,
                )
            except Exception as exc:  # noqa: BLE001 - never block cleanup
                st.failures.append(f"sidecar telemetry write: {type(exc).__name__}")
            raise

        # ---- 5) isolation runtime — ONLY after sidecar healthy (§5) ---------
        st.state = "ISOLATION_CREATE"
        async with isolated_surreal_eval_runtime() as ctx:
            st.isolation_entered = True
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
                allow_holdout=True,  # AUTHORIZED full run executes HOLDOUT
                config=EvalRunConfig08(
                    run_type="FULL_BENCHMARK",
                    index_ready_timeout_s=1800.0,
                    poll_interval_s=5.0,
                ),
                combined_sha256=st.fixture_hash_before,
                graphrag_config=load_config(),
            )
            value_out_dir = (
                artifact_dir or Path(".artifacts") / "graphrag-08-full" / st.run_id
            )
            try:
                st.state = "FULL_INDEX"
                await runner.create_and_index()
                st.created_ids = list(runner.created_ids)
                _capture_index_telemetry(st, runner)
                st.state = "FULL_QUERY"
                evaluations = await runner.run()
                metadata = {
                    **runner.build_metadata(),
                    "run_type": "FULL_BENCHMARK",
                    # Provenance METADATA only (task §13/§14) — never affects execution.
                    "full_run_authorization": st.authorization_label,
                    "value_run": True,
                    "holdout_used": True,
                    "full_benchmark_executed": True,
                    "benchmark_corpus_size": len(bench.sources),
                }
                st.state = "ANALYZE"
                artifact = r.build_artifact(
                    metadata, evaluations, corpus_size=runner.corpus_size
                )
                r.write_artifact(value_out_dir / "benchmark.json", artifact)
                _write_manifest(value_out_dir / "manifest.json", st, runner)
            except Exception:
                # GraphRAG-08B: preserve content-free failure telemetry BEFORE
                # destructive cleanup, so a failed-before-query run is diagnosable.
                st.created_ids = list(runner.created_ids)
                _capture_index_telemetry(st, runner)
                st.run_validity = "FAILED"
                try:
                    _write_failure_telemetry(value_out_dir, st, runner)
                except Exception as exc:  # noqa: BLE001 - never block cleanup
                    st.failures.append(f"failure telemetry write: {type(exc).__name__}")
                raise
            finally:
                st.state = "CLEANUP"
                try:
                    cr = await runner.cleanup()
                    st.lightrag_cleanup_ok = cr.clean and not cr.errors
                except Exception as exc:  # noqa: BLE001
                    st.failures.append(f"runner cleanup: {type(exc).__name__}")
                st.temp_model_cleanup_ok = await restore_default_and_delete_model(
                    st.temp_model_id or "", st.prior_default_embedding
                )
    except Exception as exc:  # noqa: BLE001
        # Type name only — never str(exc), which could carry provider/LightRAG text
        # into the failure record (GraphRAG-08B raw-containment; review LOW-1).
        st.failures.append(f"{st.state}: {type(exc).__name__}")
    finally:
        try:
            stop_sidecar()
        except Exception as exc:  # noqa: BLE001
            st.failures.append(f"sidecar stop: {type(exc).__name__}")
        st.sidecar_stopped = not sidecar_running()
        if prior_graph_flag is None:
            os.environ.pop("OPEN_NOTEBOOK_GRAPHRAG_ENABLED", None)
        else:
            os.environ["OPEN_NOTEBOOK_GRAPHRAG_ENABLED"] = prior_graph_flag

    # ---- 6) post-run normal DB verification (§6) ---------------------------
    # Re-read the baseline through the SAME verified mechanism, after runtime
    # restoration. NORMAL_DB_UNCHANGED is YES only when BOTH observations are valid
    # concrete observations and satisfy the approved comparison; otherwise NOT_PROVEN
    # (which blocks operational sign-off) even if benchmark-owned cleanup passed.
    after = await pf.read_normal_db_baseline()
    st.normal_db_after = after.as_dict()
    st.normal_source_count_after = after.source_count
    st.normal_db_unchanged = pf.compare_normal_db(before, after)

    ok2, h2 = d.verify_integrity()
    st.fixture_hash_after = h2 if ok2 else "MISMATCH"
    complete = not st.failures and evaluations is not None
    st.state = "COMPLETE" if complete else "FAILED"
    st.run_validity = (
        "COMPLETE_VALID"
        if (
            complete
            and st.fixture_hash_after == st.fixture_hash_before
            and st.normal_db_unchanged == pf.UNCHANGED_YES
        )
        else "FAILED"
    )
    return st


def _atomic_write_json(path: Path, obj: dict) -> None:
    """Crash-safe JSON write: temp file then atomic replace (task §13)."""
    import os

    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    try:
        with tmp.open("w", encoding="utf-8") as fh:
            json.dump(obj, fh, indent=2, sort_keys=True)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except Exception:
        # Never leave a stray partial temp file behind (review LOW-2).
        tmp.unlink(missing_ok=True)
        raise


def _capture_index_telemetry(st: PrecheckState, runner) -> None:
    """Copy the runner's content-free indexing telemetry into the state.

    Called on BOTH the success and the failure path, so a run that aborts during
    FULL_INDEX still preserves retry accounting + failure diagnostics (task §10)."""
    tel = runner.index_telemetry()
    st.graphrag_indexed_count = tel.get("graphrag_indexed_count")
    st.retry_accounting = tel.get("retry_accounting")
    st.failed_logical_ids = list(tel.get("failed_logical_ids") or [])


def build_preisolation_failure_artifact(
    st: PrecheckState,
    *,
    failure_stage: str,
    failure_reason_code: str,
    sidecar_diagnostic: Optional[dict] = None,
    cleanup_outcome: Optional[dict] = None,
) -> dict:
    """Content-free failure artifact for a PREFLIGHT / SIDECAR_START abort (task §11).

    Durable even though NO temp namespace was ever created (ISOLATION_ENTERED=NO).
    Records only ids, labels, coarse baseline/sidecar status, and zeroed execution
    counters — never raw content, provider text, or secrets (§12)."""
    return {
        "run_type": "FULL_VALUE_BENCHMARK",
        "authorization_label": st.authorization_label,
        "benchmark_version": "graphrag_08_eval_v1",
        "run_id": st.run_id,
        "run_validity": "FAILED",
        "isolation_entered": False,
        "failure_stage": failure_stage,
        "failure_reason_code": failure_reason_code,
        "fixture_hash": st.fixture_hash_before,
        "launcher_preflight": st.launcher_preflight,
        "normal_db_baseline": st.normal_db_before,
        "normal_db_unchanged": st.normal_db_unchanged,
        "sidecar_diagnostic": sidecar_diagnostic,
        "dev_executed": 0,
        "holdout_executed": 0,
        "value_decision_made": False,
        "cleanup_restoration": cleanup_outcome
        or {
            "sidecar_stopped": st.sidecar_stopped,
            "temp_namespace_created": False,
        },
    }


def _write_preisolation_failure_telemetry(
    out_dir: Path,
    st: PrecheckState,
    *,
    failure_stage: str,
    failure_reason_code: str,
    sidecar_diagnostic: Optional[dict] = None,
) -> None:
    """Atomic write of the pre-isolation failure artifact (task §11/§12)."""
    artifact = build_preisolation_failure_artifact(
        st,
        failure_stage=failure_stage,
        failure_reason_code=failure_reason_code,
        sidecar_diagnostic=sidecar_diagnostic,
    )
    _atomic_write_json(out_dir / "preflight_failure.json", artifact)


def _write_failure_telemetry(out_dir: Path, st: PrecheckState, runner) -> None:
    """Persist a content-free failure telemetry artifact BEFORE cleanup (task §10/§12)."""
    tel = runner.index_telemetry()
    artifact = {
        "run_type": "FULL_VALUE_BENCHMARK",
        # Provenance METADATA only (task §13/§14): the explicit authorization label,
        # never a hard-coded per-attempt string.
        "full_run_authorization": st.authorization_label,
        "benchmark_version": "graphrag_08_eval_v1",
        "run_id": st.run_id,
        "run_validity": "FAILED",
        "value_decision_made": False,
        "dev_executed": 0,
        "holdout_executed": 0,
        "fixture_hash": st.fixture_hash_before,
        "temp_namespace": st.temp_namespace,
        "index_telemetry": tel,
    }
    _atomic_write_json(out_dir / "failure_telemetry.json", artifact)


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


__all__ = [
    "run_micro_precheck",
    "run_full_benchmark",
    "PrecheckState",
    "gather_sidecar_diagnostic",
    "build_preisolation_failure_artifact",
]
