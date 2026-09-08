"""OFFLINE CLI for the PN02 live driver (PN02D-B0B).

EVALUATION-ONLY. Nothing in production imports this (``PRODUCTION_IMPORTS_EVAL =
NO``). Provider-free verbs only (design §46/§49): ``validate-live-driver`` (checks
fixture hash + routing + provider-config identity + secret-name presence — NO
provider call), ``dry-run-b1-plan`` (serializes the full plan, no execution), and
``run-offline-b1-simulation`` (runs the whole orchestrator against fakes). There is
DELIBERATELY no ``execute-b1`` verb here: a real provider-backed run structurally
requires an operator-granted ``PN02ProviderRunAuthorization`` and is never reachable
from this offline CLI (``PN02_PROVIDER_RUN_AUTHORIZED = NO``).
"""

from __future__ import annotations

import argparse
import asyncio
import json
from typing import Optional, Sequence

from open_notebook.integrations.graphrag.eval import datasetpn02 as ds
from open_notebook.integrations.graphrag.eval.authlivepn02d import (
    PN02_PROVIDER_RUN_AUTHORIZED,
    frozen_provider_config_id,
)
from open_notebook.integrations.graphrag.eval.driverpn02d import (
    plan_b1,
    run_offline_b1_simulation,
)
from open_notebook.integrations.graphrag.eval.provbindpn02d import (
    env_present_secret_names,
)
from open_notebook.integrations.graphrag.eval.provider_binding08 import (
    frozen_provider_binding,
)
from open_notebook.integrations.graphrag.eval.routelivepn02d import (
    PN02Router,
    build_route_table,
)


def cmd_validate_live_driver(args: argparse.Namespace) -> int:
    fx = ds.load_fixture()
    ds.validate_fixture(fx)
    ok, detail = ds.verify_fixture_hash()
    router = PN02Router(build_route_table(fx))
    binding = frozen_provider_binding()
    binding.validate()
    present = env_present_secret_names(set(binding.required_secret_envs()))
    out = {
        "validation": "PASS" if ok else "FAIL",
        "fixture_name": ds.FIXTURE_NAME,
        "fixture_hash": detail,
        "fixture_hash_ok": ok,
        "notebook_routes": {
            nb: router.route_for(nb).as_public_dict() for nb in router.notebook_ids()
        },
        "provider_config_id": frozen_provider_config_id(),
        "provider_models": {
            "llm_model": binding.llm_model,
            "embedding_model": binding.embedding_model,
            "embedding_dim": binding.embedding_dim,
        },
        "required_secret_envs": list(binding.required_secret_envs()),
        "present_secret_envs": list(present),
        "provider_traffic": 0,
        "pn02_provider_run_authorized": PN02_PROVIDER_RUN_AUTHORIZED,
    }
    print(json.dumps(out, indent=2, sort_keys=True))
    return 0 if ok else 1


def cmd_dry_run_b1_plan(args: argparse.Namespace) -> int:
    plan = plan_b1(run_id=args.run_id)
    if args.out:
        from pathlib import Path

        Path(args.out).write_text(
            json.dumps(plan, indent=2, sort_keys=True), encoding="utf-8"
        )
    print(json.dumps(plan, indent=2, sort_keys=True))
    return 0


def cmd_run_offline_b1_simulation(args: argparse.Namespace) -> int:
    outcome = asyncio.run(
        run_offline_b1_simulation(run_id=args.run_id, delete_succeed=not args.delete_fails)
    )
    if args.out:
        from pathlib import Path

        Path(args.out).write_text(
            json.dumps(outcome.report, indent=2, sort_keys=True), encoding="utf-8"
        )
    summary = {
        "state": outcome.state,
        "technical_status": outcome.technical_status.value,
        "provider_traffic": 0,
        "report": outcome.report,
        "budget_snapshot": outcome.budget_snapshot,
        "cleanup": outcome.cleanup,
    }
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0 if outcome.state == "COMPLETE" else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="graphrag-pn02-b0b",
        description="OFFLINE PN02 live-driver CLI — no provider, no LightRAG, no network.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    v = sub.add_parser(
        "validate-live-driver",
        help="validate fixture/routing/provider-config/secret-name presence (no provider)",
    )
    v.set_defaults(func=cmd_validate_live_driver)

    d = sub.add_parser(
        "dry-run-b1-plan", help="serialize the full content-safe B1 plan (no execution)"
    )
    d.add_argument("--run-id", default="DRYRUN")
    d.add_argument("--out", help="optional path to write the plan JSON")
    d.set_defaults(func=cmd_dry_run_b1_plan)

    s = sub.add_parser(
        "run-offline-b1-simulation",
        help="run the full B1 orchestrator against fakes (no provider)",
    )
    s.add_argument("--run-id", default="pn02b0b-sim")
    s.add_argument("--out", help="optional path to write the report JSON")
    s.add_argument(
        "--delete-fails",
        action="store_true",
        help="simulate a FAILED graph delete (exercise the stale-evidence backstop)",
    )
    s.set_defaults(func=cmd_run_offline_b1_simulation)
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())


__all__ = ["build_parser", "main"]
