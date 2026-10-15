#!/usr/bin/env python3
"""Committed root-context launcher for the GraphRAG-09H governed live validation (F4-A).

The live-run-#5 canonical-chat failure (`ModuleNotFoundError: No module named 'api'`)
was a pure launch-context defect: a path-launched scratchpad driver put the scratchpad
directory on ``sys.path[0]`` instead of the repository root, so the top-level ``api``
package (not an installed distribution package — only ``open_notebook`` is) was not
importable, and the orchestrator's canonical-chat seam ``from api.routers.chat import
...`` raised. This committed runner removes that dependence: it establishes the
repository-root application import context (mirroring the accepted ``run_api.py``
bootstrap idiom) BEFORE importing any code that can reach ``api.routers.chat``, then
invokes the governed one-shot executor ``run_live_validation`` EXACTLY ONCE with no
retry.

Governance invariants (do not weaken):
  * The runner MINTS NOTHING: no auth tag, no grant, no Manifest, no authorization
    decision. It only executes already-authorized state (ManifestInputs derived from the
    governed git evidence tags) and relies entirely on the fail-closed gates INSIDE
    ``run_live_validation`` (HEAD check, recomputed-Manifest check, exact-head live-auth
    verification). It cannot bypass them.
  * Importing this module runs NOTHING beyond the idempotent ``sys.path`` bootstrap;
    governed execution happens only through an explicit ``--execute`` invocation (or a
    caller-supplied executor in tests).
  * No secret is hardcoded; the run-owned sidecar/provider/control tokens are ephemeral
    per invocation and the OpenRouter credential is read by the governed harness from the
    environment variable name only.
"""

from __future__ import annotations

import argparse
import asyncio
import secrets
import socket
import subprocess
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Callable, Optional

# --------------------------------------------------------------------------- #
# Establish repository-root application import context BEFORE importing any app-layer
# code (so top-level packages such as `api` resolve). Mirrors run_api.py; idempotent;
# performs NO governed execution.
# --------------------------------------------------------------------------- #
_REPO_ROOT = Path(__file__).resolve().parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

if TYPE_CHECKING:  # import only for type checkers; no runtime app-layer import on import
    from open_notebook.integrations.graphrag.eval.productlivepn02d import (
        LiveRunResult,
        RunDeps,
    )

# Stable governance evidence tag NAMES (resolved to object/peel SHAs from git at run
# time, so the runner is never pinned to one HEAD's SHAs). The per-HEAD capture-evidence
# tag is auto-detected from the tags pointing at HEAD (the governance `-approved` tag,
# never the `-exact-head-auth` live-auth tag).
_HISTORICAL_EVIDENCE_TAGS = {
    "tag_09g": "graphrag-09g-offline-integration-proof-approved",
    "tag_09h_bounds": "graphrag-09h-canonical-chat-execution-bounds-approved",
    "tag_09h_slice": "graphrag-09h-pn02-slice-hash-derivation-approved",
}
_CAPTURE_EVIDENCE_SUFFIX = "-approved"
_LIVE_AUTH_TAG_INFIX = "-exact-head-auth"
_PROVIDER_KEY_ENV = "OPENROUTER_API_KEY"


def establish_root_context() -> str:
    """Idempotently ensure the repository root is importable; return it as a string."""
    root = str(_REPO_ROOT)
    if root not in sys.path:
        sys.path.insert(0, root)
    return root


def canonical_chat_seam_importable() -> bool:
    """Provider-free: verify the canonical-chat seam imports from the established root
    context. Resolves the symbols only; executes NO chat and contacts no provider."""
    establish_root_context()
    from api.routers.chat import ExecuteChatRequest, execute_chat  # noqa: F401

    return callable(execute_chat) and isinstance(ExecuteChatRequest, type)


def _free_loopback_port() -> int:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])
    finally:
        sock.close()


def _git(*args: str) -> str:
    result = subprocess.run(
        ["git", *args], cwd=str(_REPO_ROOT), capture_output=True, text=True
    )
    if result.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {result.stderr.strip()}")
    return result.stdout.strip()


def _resolve_tag(tag: str) -> "tuple[str, str]":
    """Return (annotated-object-sha, peeled-commit-sha) for a governance tag."""
    return _git("rev-parse", tag), _git("rev-parse", f"{tag}^{{commit}}")


def _detect_current_head_capture_tag() -> str:
    """Auto-detect the single governance capture-evidence tag pointing at HEAD.

    Fail closed (RuntimeError) if zero or more than one qualifies, so the runner never
    guesses an authorization binding. The live-auth (`-exact-head-auth`) tag is excluded.
    """
    candidates = [
        t
        for t in _git("tag", "--points-at", "HEAD").splitlines()
        if t.strip().endswith(_CAPTURE_EVIDENCE_SUFFIX)
        and _LIVE_AUTH_TAG_INFIX not in t
    ]
    if len(candidates) != 1:
        raise RuntimeError(
            "expected exactly one governance capture-evidence tag at HEAD, "
            f"found {len(candidates)}: {candidates}"
        )
    return candidates[0]


def build_default_deps(
    *, current_head_capture_tag: Optional[str] = None, host_port: Optional[int] = None
) -> "RunDeps":
    """Assemble real RunDeps for the current HEAD from the governed git evidence tags.

    Mints nothing: the ManifestInputs are derived from committed git tags and validated
    (fail-closed) inside ``run_live_validation``. Run-owned tokens are ephemeral.
    """
    from open_notebook.integrations.graphrag.eval import productlivepn02d as plo

    head = _git("rev-parse", "HEAD")
    parent = _git("rev-parse", "HEAD~1")
    g09_obj, g09_peel = _resolve_tag(_HISTORICAL_EVIDENCE_TAGS["tag_09g"])
    gb_obj, gb_peel = _resolve_tag(_HISTORICAL_EVIDENCE_TAGS["tag_09h_bounds"])
    gs_obj, gs_peel = _resolve_tag(_HISTORICAL_EVIDENCE_TAGS["tag_09h_slice"])
    head_tag = current_head_capture_tag or _detect_current_head_capture_tag()
    head_obj, head_peel = _resolve_tag(head_tag)

    manifest_inputs = plo.ManifestInputs(
        head_commit=head,
        parent_commit=parent,
        tag_09g_object=g09_obj,
        tag_09g_peel=g09_peel,
        tag_09h_bounds_object=gb_obj,
        tag_09h_bounds_peel=gb_peel,
        tag_09h_slice_object=gs_obj,
        tag_09h_slice_peel=gs_peel,
        product_live_evidence=plo.EvidenceTagBinding(
            tag=plo.PRODUCT_LIVE_CAPTURE_TAG,
            object_sha=plo.PRODUCT_LIVE_CAPTURE_OBJECT_SHA,
            peel_sha=plo.PRODUCT_LIVE_CAPTURE_PEEL_SHA,
        ),
        current_head_capture_evidence=plo.EvidenceTagBinding(
            tag=head_tag, object_sha=head_obj, peel_sha=head_peel
        ),
    )
    port = host_port if host_port is not None else _free_loopback_port()
    return plo.default_run_deps(
        manifest_inputs,
        sidecar_api_token=secrets.token_hex(16),
        local_provider_token=secrets.token_hex(16),
        control_token=secrets.token_hex(16),
        provider_key_env=_PROVIDER_KEY_ENV,
        host_port=port,
    )


def run(
    *,
    deps_factory: Callable[[], "RunDeps"],
    run_validation: Optional[Callable[..., object]] = None,
    head_expected: Optional[str] = None,
) -> "LiveRunResult":
    """Invoke the governed one-shot executor EXACTLY ONCE (no retry, ever).

    The caller supplies ``deps_factory`` (already-authorized state); the default executor
    is the governed ``run_live_validation`` whose fail-closed gates this runner never
    bypasses. ``run_validation``/``head_expected`` are injectable so provider-free tests
    can drive the control flow with a fake executor.
    """
    establish_root_context()
    if run_validation is None:
        from open_notebook.integrations.graphrag.eval import productlivepn02d as plo

        run_validation = plo.run_live_validation
    deps = deps_factory()
    if head_expected is None:
        head_expected = deps.git.head()
    # Single governed invocation. No retry loop under any outcome.
    return asyncio.run(run_validation(deps, head_expected=head_expected))  # type: ignore[arg-type]


def main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Root-context launcher for the GraphRAG-09H governed one-shot live "
            "validation. Establishes repository-root import context, then (only with "
            "--execute) invokes run_live_validation exactly once. Mints no "
            "authorization and bypasses no governance gate."
        )
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--check-context",
        action="store_true",
        help="Provider-free: establish root context and verify api.routers.chat imports; "
        "run NOTHING else.",
    )
    group.add_argument(
        "--execute",
        action="store_true",
        help="Invoke the governed one-shot live validation ONCE (requires an operator "
        "grant; consumes it). No retry.",
    )
    parser.add_argument(
        "--current-head-capture-tag",
        default=None,
        help="Override the auto-detected current-HEAD capture-evidence tag.",
    )
    args = parser.parse_args(argv)

    root = establish_root_context()
    if args.check_context:
        ok = canonical_chat_seam_importable()
        print(f"RUNNER_ROOT={root}")
        print(f"CANONICAL_CHAT_SEAM_IMPORT={'PASS' if ok else 'FAIL'}")
        return 0 if ok else 2

    if args.execute:
        result = run(
            deps_factory=lambda: build_default_deps(
                current_head_capture_tag=args.current_head_capture_tag
            )
        )
        verdict = getattr(result.verdict, "name", result.verdict)
        value = getattr(result.verdict, "value", result.verdict)
        print(f"LIVE_VERDICT={verdict} ({value}) reason={result.reason}")
        # PASS only for the bounded-live-provenance-plumbing verdict; any other outcome
        # is a non-zero exit. The grant is consumed regardless; NEVER retried here.
        return 0 if value == "09H_LIVE_A" else 1

    parser.print_help()
    print(
        "\nNothing executed: pass --check-context for a provider-free self-check, or "
        "--execute under an operator grant to run the governed one-shot."
    )
    return 3


if __name__ == "__main__":
    raise SystemExit(main())
