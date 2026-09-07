"""OFFLINE PN02 command-line entrypoint (task §58).

EVALUATION-ONLY. Nothing in production imports this. Explicitly OFFLINE verbs
(``validate`` / ``hash`` / ``evaluate-results`` / ``report``) — deliberately NOT a
``run-benchmark`` verb that could be mistaken for a live run. It NEVER starts a
sidecar, calls a provider, queries LightRAG, or opens a network socket: it only
reads local JSON, computes deterministic metrics/decisions, and prints content-free
JSON. There is no HTTP client anywhere in this module or its imports (the live seam
is inert, ``live_seam_pn02``).

Result JSON contract (``evaluate-results <file>``), all content-free ids/tokens:

    {
      "run_id": "...", "git_commit": "...",
      "vector": {"<query_id>": ["<ranked source id>", ...], ...},
      "gd":     {"<query_id>": {"sources": [...], "foreign": 0, "malformed": 0,
                                "latency_ms": null}, ...},
      "qa":     [ {"query_id": "...", "arm": "QA-V|QA-GD|QA-V+GD",
                   "abstained": false, "citation_source_ids": [...],
                   "emitted_answer_facts": [...], "latency_ms": null}, ... ],
      "removal": {
        "removed_after":  {"query_id": "...", "gd_sources": [...],
                           "vector_sources": [...], "graph_delete_succeeded": true},
        "retained_after": {"query_id": "...", "gd_sources": [...],
                           "vector_sources": [...], "graph_delete_succeeded": true}
      }
    }
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence

from open_notebook.integrations.graphrag.eval import datasetpn02 as ds
from open_notebook.integrations.graphrag.eval.evaluatepn02 import run_offline_evaluation
from open_notebook.integrations.graphrag.eval.normalizepn02 import (
    normalize_graph,
    normalize_vector,
)
from open_notebook.integrations.graphrag.eval.reportpn02 import build_report
from open_notebook.integrations.graphrag.eval.schemaspn02 import (
    ArmId,
    GDEvidenceResult,
    QAAnswerResult,
    RemovalPhase,
    RemovalProbeResult,
    VectorEvidenceResult,
)


def _load_results_json(
    fx: ds.FixturePN02, raw: Mapping[str, Any]
) -> Dict[str, Any]:
    allow = fx.source_keys

    vector_results: Dict[str, VectorEvidenceResult] = {}
    for qid, ranked in (raw.get("vector") or {}).items():
        vector_results[qid] = VectorEvidenceResult(
            query_id=qid,
            notebook_id=fx.query(qid).notebook_id,
            evidence=normalize_vector(list(ranked), allowlist=allow),
        )

    gd_results: Dict[str, GDEvidenceResult] = {}
    for qid, entry in (raw.get("gd") or {}).items():
        sources = list(entry.get("sources") or [])
        # foreign/malformed can be modeled by extra ids the normalizer classifies;
        # explicit counts in the JSON are added on top for out-of-fixture provenance.
        ev = normalize_graph(sources, allowlist=allow)
        extra_foreign = int(entry.get("foreign") or 0)
        extra_malformed = int(entry.get("malformed") or 0)
        if extra_foreign or extra_malformed:
            from open_notebook.integrations.graphrag.eval.normalizepn02 import (
                NormalizedEvidencePN02,
                ProvenancePN02,
            )

            stats = ev.stats
            ev = NormalizedEvidencePN02(
                source_ids=ev.source_ids,
                ordered=False,
                stats=ProvenancePN02(
                    total=stats.total + extra_foreign + extra_malformed,
                    valid_unique=stats.valid_unique,
                    duplicates=stats.duplicates,
                    malformed=stats.malformed + extra_malformed,
                    foreign=stats.foreign + extra_foreign,
                ),
            )
        gd_results[qid] = GDEvidenceResult(
            query_id=qid,
            notebook_id=fx.query(qid).notebook_id,
            evidence=ev,
        )

    qa_results: List[QAAnswerResult] = []
    for entry in raw.get("qa") or []:
        qid = str(entry["query_id"])
        qa_results.append(
            QAAnswerResult(
                query_id=qid,
                notebook_id=fx.query(qid).notebook_id,
                arm=ArmId(entry["arm"]),
                abstained=bool(entry.get("abstained", False)),
                citation_source_ids=tuple(entry.get("citation_source_ids") or ()),
                answer_text=entry.get("answer_text"),
                emitted_answer_facts=(
                    tuple(entry["emitted_answer_facts"])
                    if entry.get("emitted_answer_facts") is not None
                    else None
                ),
                latency_ms=entry.get("latency_ms"),
            )
        )

    removal = raw.get("removal") or {}
    after_a = _removal_probe(fx, removal.get("removed_after"), allow)
    after_b = _removal_probe(fx, removal.get("retained_after"), allow)

    return {
        "vector_results": vector_results,
        "gd_results": gd_results,
        "qa_results": qa_results,
        "removal_after_removed_nb": after_a,
        "removal_after_retained_nb": after_b,
    }


def _removal_probe(
    fx: ds.FixturePN02, entry: Optional[Mapping[str, Any]], allow
) -> RemovalProbeResult:
    if not entry:
        raise ValueError("results.removal must supply removed_after and retained_after")
    qid = str(entry["query_id"])
    return RemovalProbeResult(
        query_id=qid,
        notebook_id=fx.query(qid).notebook_id,
        phase=RemovalPhase.AFTER,
        gd_evidence=normalize_graph(list(entry.get("gd_sources") or []), allowlist=allow),
        vector_evidence=normalize_vector(
            list(entry.get("vector_sources") or []), allowlist=allow
        ),
        graph_delete_succeeded=bool(entry.get("graph_delete_succeeded", True)),
    )


def cmd_validate(args: argparse.Namespace) -> int:
    fx = ds.load_fixture()
    ds.validate_fixture(fx)
    out = {
        "fixture_name": ds.FIXTURE_NAME,
        "fixture_validation": "PASS",
        "notebook_count": len(fx.notebooks),
        "canonical_source_count": len(fx.sources),
        "workspace_membership_count": len(fx.memberships),
        "query_count": len(fx.queries),
    }
    print(json.dumps(out, indent=2, sort_keys=True))
    return 0


def cmd_hash(args: argparse.Namespace) -> int:
    fx = ds.load_fixture()
    live = ds.compute_fixture_hash(fx)
    ok, detail = ds.verify_fixture_hash()
    out = {
        "fixture_name": ds.FIXTURE_NAME,
        "fixture_hash": live,
        "fixture_hash_validation": "PASS" if ok else "FAIL",
        "detail": detail,
    }
    print(json.dumps(out, indent=2, sort_keys=True))
    return 0 if ok else 1


def cmd_evaluate_results(args: argparse.Namespace) -> int:
    fx = ds.load_fixture()
    ds.validate_fixture(fx)
    ok, fixture_hash = ds.verify_fixture_hash()
    if not ok:
        print(json.dumps({"error": "fixture_hash_mismatch"}), file=sys.stderr)
        return 1
    raw = json.loads(Path(args.results).read_text(encoding="utf-8"))
    parsed = _load_results_json(fx, raw)
    scenario = ds.membership_removal_scenario(fx)
    result = run_offline_evaluation(
        fx,
        fixture_hash=fixture_hash,
        scenario=scenario,
        **parsed,
    )
    report = build_report(
        stage1=result.stage1,
        retrieval=result.retrieval if result.isolation_evidenced else None,
        qa_baseline=result.qa_baseline,
        qa_graph_arms=result.qa_graph_arms if result.qa_graph_arms else None,
        qa_decision_result=result.qa if result.isolation_evidenced else None,
        multihop=result.multihop if result.isolation_evidenced else None,
        removal=result.removal,
        scientific=result.scientific,
    )
    if args.out:
        Path(args.out).write_text(
            json.dumps(report, indent=2, sort_keys=True), encoding="utf-8"
        )
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="graphrag-pn02-offline",
        description="OFFLINE PN02 evaluator — no provider, no LightRAG, no network.",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("validate", help="validate the frozen fixture").set_defaults(
        func=cmd_validate
    )
    sub.add_parser("hash", help="print and verify the fixture hash").set_defaults(
        func=cmd_hash
    )
    ev = sub.add_parser(
        "evaluate-results",
        help="evaluate a local synthetic result JSON (no provider)",
    )
    ev.add_argument("results", help="path to a content-free result JSON file")
    ev.add_argument("--out", help="optional path to write the report JSON")
    ev.set_defaults(func=cmd_evaluate_results)
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())


__all__ = ["build_parser", "main"]
