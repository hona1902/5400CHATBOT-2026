"""PN02D-B0B — offline CLI verbs (design §46/§49). No provider, no network."""

from __future__ import annotations

import json

from open_notebook.integrations.graphrag.eval.liveclipn02d import build_parser, main


def test_cli_has_no_execute_b1_verb():
    parser = build_parser()
    # Only offline verbs; no provider-executing verb is reachable here.
    sub = [
        a for a in parser._subparsers._group_actions  # type: ignore[attr-defined]
    ]
    choices = set()
    for action in sub:
        if hasattr(action, "choices") and action.choices:
            choices.update(action.choices.keys())
    assert "execute-b1" not in choices
    assert {"validate-live-driver", "dry-run-b1-plan", "run-offline-b1-simulation"} <= choices


def test_validate_live_driver(capsys):
    rc = main(["validate-live-driver"])
    out = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert out["validation"] == "PASS"
    assert out["provider_traffic"] == 0
    assert out["pn02_provider_run_authorized"] is False
    assert len(out["notebook_routes"]) == 3


def test_dry_run_b1_plan(capsys):
    rc = main(["dry-run-b1-plan", "--run-id", "CLIDRY"])
    out = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert out["plan_kind"] == "graphrag_pn02_b1_dry_run_plan"
    assert len(out["index_plan"]) == 24
    assert out["provider_traffic"] == 0


def test_run_offline_b1_simulation(capsys):
    rc = main(["run-offline-b1-simulation", "--run-id", "CLISIM"])
    out = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert out["state"] == "COMPLETE"
    assert out["provider_traffic"] == 0
    assert out["report"]["stage1"]["stage1_status"] == "PASS"
