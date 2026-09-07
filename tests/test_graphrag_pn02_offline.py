"""GraphRAG-PN02 offline harness / CLI / network-denial / determinism tests.

Covers task §46 (no network required — sockets are blocked while the harness runs),
§54 (determinism), §58 (offline CLI verbs), §59 (inert live seam), and §68 Q
(no production module imports the PN02 eval code).
"""

from __future__ import annotations

import asyncio
import json
import socket
from pathlib import Path

import pytest

from open_notebook.integrations.graphrag.eval import datasetpn02 as ds
from open_notebook.integrations.graphrag.eval import offlinepn02 as cli
from open_notebook.integrations.graphrag.eval.evaluatepn02 import run_offline_evaluation
from open_notebook.integrations.graphrag.eval.live_seam_pn02 import (
    PN02_LIVE_AUTHORIZED,
    STRUCTURED_EVIDENCE_IMPLEMENTATION_READY,
    InertGDQuerySeam,
    InertMembershipIndexer,
    LiveExecutionNotAuthorized,
)
from open_notebook.integrations.graphrag.eval.reportpn02 import build_report

FX = ds.load_fixture()
HASH = ds.compute_fixture_hash(FX)


# --------------------------------------------------------------------------- #
# Clean + leaking synthetic result builders
# --------------------------------------------------------------------------- #

def _clean_results_dict() -> dict:
    scen = ds.membership_removal_scenario(FX)
    vector, gd = {}, {}
    for q in FX.queries:
        members = sorted(FX.members_of(q.notebook_id))
        ranked = list(q.required_source_ids) + [
            m for m in members if m not in q.required_source_ids
        ][:4]
        vector[q.query_id] = ranked
        gd[q.query_id] = {
            "sources": list(q.required_source_ids) + list(q.optional_support_source_ids),
            "foreign": 0,
            "malformed": 0,
        }
    qa = []
    for q in FX.queries:
        for a in ("QA-V", "QA-GD", "QA-V+GD"):
            if q.is_negative:
                qa.append({"query_id": q.query_id, "arm": a, "abstained": True})
            else:
                qa.append(
                    {
                        "query_id": q.query_id,
                        "arm": a,
                        "abstained": False,
                        "citation_source_ids": list(q.required_citation_source_ids),
                        "emitted_answer_facts": list(q.expected_answer_facts),
                    }
                )
    qa_id, qb_id = scen.reprobe_query_ids
    return {
        "run_id": "test",
        "git_commit": "test",
        "vector": vector,
        "gd": gd,
        "qa": qa,
        "removal": {
            "removed_after": {
                "query_id": qa_id,
                "gd_sources": sorted(scen.members_after_removed_nb)[:2],
                "vector_sources": sorted(scen.members_after_removed_nb)[:2],
                "graph_delete_succeeded": True,
            },
            "retained_after": {
                "query_id": qb_id,
                "gd_sources": [scen.shared_source],
                "vector_sources": [scen.shared_source],
                "graph_delete_succeeded": True,
            },
        },
    }


def _run(results_dict: dict):
    parsed = cli._load_results_json(FX, results_dict)
    scen = ds.membership_removal_scenario(FX)
    return run_offline_evaluation(FX, fixture_hash=HASH, scenario=scen, **parsed)


# --------------------------------------------------------------------------- #
# Network denial (task §46)
# --------------------------------------------------------------------------- #

@pytest.fixture()
def no_network(monkeypatch):
    def _blocked(*a, **k):
        raise AssertionError("network access attempted by an offline harness")

    monkeypatch.setattr(socket, "socket", _blocked)
    # Also block the lower-level connect on any existing socket class attr.
    monkeypatch.setattr(socket, "create_connection", _blocked)
    return None


def test_full_evaluation_needs_no_network(no_network) -> None:
    result = _run(_clean_results_dict())
    assert result.stage1.stage1_status == "PASS"
    assert result.isolation_evidenced is True


def test_cli_validate_and_hash_need_no_network(no_network, capsys) -> None:
    assert cli.main(["validate"]) == 0
    assert cli.main(["hash"]) == 0
    out = capsys.readouterr().out
    assert "PASS" in out


# --------------------------------------------------------------------------- #
# CLI evaluate-results end-to-end (task §58)
# --------------------------------------------------------------------------- #

def test_cli_evaluate_results(tmp_path: Path, capsys) -> None:
    results_file = tmp_path / "results.json"
    results_file.write_text(json.dumps(_clean_results_dict()), encoding="utf-8")
    out_file = tmp_path / "report.json"
    rc = cli.main(["evaluate-results", str(results_file), "--out", str(out_file)])
    assert rc == 0
    report = json.loads(out_file.read_text(encoding="utf-8"))
    assert report["stage1"]["stage1_status"] == "PASS"
    assert report["scientific_outputs"]["PER_NOTEBOOK_ISOLATION_EVIDENCED"] == "YES"


# --------------------------------------------------------------------------- #
# Fail-closed: Stage 2 unreachable after a Stage-1 failure (task §21)
# --------------------------------------------------------------------------- #

def test_stage1_failure_blocks_stage2_via_orchestrator() -> None:
    d = _clean_results_dict()
    # Inject a cross-notebook leak into an NB_A query's GD evidence (B3).
    d["gd"]["PN02Q01"]["sources"] = ["A3", "B3"]
    result = _run(d)
    assert result.stage1.stage1_status == "FAIL"
    assert result.isolation_evidenced is False
    assert result.scientific.per_notebook_isolation_evidenced.value == "NO"
    assert result.scientific.per_notebook_graph_retrieval_value_evidenced.value == "NOT_EVALUATED"
    assert result.scientific.per_notebook_graph_qa_value_evidenced.value == "NOT_EVALUATED"
    assert result.scientific.per_notebook_multihop_incremental_value_evidenced.value == "NOT_EVALUATED"
    # No Stage-2 QA metrics were computed.
    assert result.qa_baseline is None
    assert result.qa_graph_arms == ()


# --------------------------------------------------------------------------- #
# Determinism (task §54)
# --------------------------------------------------------------------------- #

def test_qa_not_supplied_is_not_evaluated_not_q3() -> None:
    # Stage 1 passes but no QA answers supplied -> a data-absent technical state,
    # reported as NOT_EVALUATED / QA_NOT_SUPPLIED, never a scientific Q3 (LOW-2).
    d = _clean_results_dict()
    d["qa"] = []
    result = _run(d)
    assert result.isolation_evidenced is True
    assert result.qa.verdict.value == "NOT_EVALUATED"
    assert result.qa.rule == "QA_NOT_SUPPLIED"
    # Retrieval/multi-hop are still evaluated (Stage 1 passed).
    assert result.retrieval.rule != "R0"
    assert result.multihop.rule != "M0"


def test_determinism_same_input_same_report() -> None:
    d = _clean_results_dict()
    r1 = _run(d)
    r2 = _run(d)
    rep1 = build_report(stage1=r1.stage1, scientific=r1.scientific)
    rep2 = build_report(stage1=r2.stage1, scientific=r2.scientific)
    assert json.dumps(rep1, sort_keys=True) == json.dumps(rep2, sort_keys=True)
    assert ds.compute_fixture_hash(FX) == ds.compute_fixture_hash(ds.load_fixture())


# --------------------------------------------------------------------------- #
# Inert live seam (task §59/§60)
# --------------------------------------------------------------------------- #

def test_live_seam_is_inert() -> None:
    assert PN02_LIVE_AUTHORIZED is False
    assert STRUCTURED_EVIDENCE_IMPLEMENTATION_READY is False
    with pytest.raises(LiveExecutionNotAuthorized):
        InertMembershipIndexer().index_membership("ws", "A1", "text")

    async def _call():
        await InertGDQuerySeam().query_evidence(None, "q")  # type: ignore[arg-type]

    with pytest.raises(LiveExecutionNotAuthorized):
        asyncio.run(_call())


# --------------------------------------------------------------------------- #
# No production module imports the PN02 eval code (task §68 Q)
# --------------------------------------------------------------------------- #

def test_no_production_import_of_pn02_eval() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    offenders = []
    for base in ("open_notebook", "api", "commands"):
        root = repo_root / base
        if not root.exists():
            continue
        for path in root.rglob("*.py"):
            # The eval package is allowed to import itself.
            if "integrations/graphrag/eval" in path.as_posix():
                continue
            text = path.read_text(encoding="utf-8", errors="ignore")
            if "pn02" in text.lower() and "import" in text:
                # Only fail if it actually imports a pn02 module.
                for line in text.splitlines():
                    if "import" in line and "pn02" in line.lower():
                        offenders.append(f"{path}: {line.strip()}")
    assert not offenders, "production code imports PN02 eval:\n" + "\n".join(offenders)
