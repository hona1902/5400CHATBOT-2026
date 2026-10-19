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
import dataclasses
import datetime
import enum
import hashlib
import json
import os
import re
import secrets
import socket
import stat
import subprocess
import sys
import tempfile
from collections.abc import Mapping
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

# --------------------------------------------------------------------------- #
# Safe governed-evidence export (09H_live_run_evidence_v1).
#
# After the governed one-shot `run_live_validation` returns a LiveRunResult, the
# runner persists a durable, machine-readable, SECRET-SAFE JSON artifact of the
# complete governed evidence BEFORE process exit, so a failure run (the main
# forensic case) is not lost to unsurfaced stdout. This is PROVENANCE/EVIDENCE
# plumbing only: it changes NO governed runtime, provenance, security, grant, or
# provider semantics, performs NO provider/DB egress, and never reruns execution.
# --------------------------------------------------------------------------- #
EVIDENCE_EXPORT_SCHEMA = "09H_live_run_evidence_v1"
_PASS_VERDICT_VALUE = "09H_LIVE_A"  # LiveVerdict.PASS_BOUNDED_LIVE_PROVENANCE_PLUMBING

_EVIDENCE_REDACTED = "[REDACTED]"
_EVIDENCE_SECRET_MARK = "[REDACTED_SECRET]"
_EVIDENCE_MAX_STRING = 20000
_EVIDENCE_MAX_DEPTH = 64

#: Substrings that make a MAPPING KEY credential-sensitive: its value is redacted
#: WITHOUT ever being serialized (§16), so a raw secret behind such a key can never
#: reach the serializer.
_SENSITIVE_KEY_SUBSTRINGS = (
    "authorization",
    "api_key",
    "api-key",
    "apikey",
    "token",
    "secret",
    "password",
    "credential",
    "x-pbp-control",
)

#: Secret-looking VALUE patterns scrubbed out of every string before serialization
#: (§15). Over-redaction is the safe direction; a token boundary avoids false hits on
#: innocuous substrings (e.g. "disk-"/"task-").
_SECRET_VALUE_PATTERNS = (
    re.compile(r"sk-or-v1-[A-Za-z0-9_\-]+"),  # OpenRouter credential
    re.compile(r"(?<![A-Za-z0-9])sk-[A-Za-z0-9_\-]{8,}"),  # generic sk- api key
    re.compile(r"(?i)(?<![A-Za-z])bearer\s+[A-Za-z0-9._~+/\-]+=*"),  # bearer token
)

# Explicit runner exit codes (§42).
EXIT_PASS = 0  # governed PASS (09H_LIVE_A) AND artifact persisted
EXIT_GOVERNED_FAIL = 1  # governed non-PASS verdict AND artifact persisted
EXIT_CHECK_CONTEXT_FAIL = 2  # --check-context seam import failed
EXIT_NO_FLAG = 3  # no execution flag (guidance printed)
EXIT_EVIDENCE_PREFLIGHT_FAIL = 4  # evidence destination invalid; grant NOT consumed
EXIT_EVIDENCE_EXPORT_FAIL = 5  # governed result exists but artifact persistence failed


@dataclasses.dataclass
class RunnerOutcome:
    """Result of a governed --execute invocation plus its evidence-export outcome."""

    verdict: str
    verdict_value: str
    reason: str
    exit_code: int
    export_ok: bool
    artifact_path: Optional[str] = None
    artifact_sha256: Optional[str] = None
    artifact_bytes: Optional[int] = None
    export_error: Optional[str] = None


def _is_sensitive_key(key: object) -> bool:
    k = str(key).lower()
    return any(sub in k for sub in _SENSITIVE_KEY_SUBSTRINGS)


def _scrub_secret_string(text: str) -> str:
    """Remove secret-looking substrings, then bound length. Applied to EVERY string
    before it can reach logging/serialization (§17)."""
    scrubbed = text
    for pat in _SECRET_VALUE_PATTERNS:
        scrubbed = pat.sub(_EVIDENCE_SECRET_MARK, scrubbed)
    if len(scrubbed) > _EVIDENCE_MAX_STRING:
        scrubbed = scrubbed[:_EVIDENCE_MAX_STRING] + "...[TRUNCATED]"
    return scrubbed


def scrub_evidence(value: object, *, _depth: int = 0) -> object:
    """Recursively scrub arbitrary governed-evidence into JSON-safe, secret-free data.

    Handles the value types governed evidence actually uses (None/bool/int/float/str/
    enum/bytes/mapping/list/tuple/set/dataclass). Credential-sensitive mapping keys have
    their value redacted BEFORE serialization. Every string is pattern-scrubbed. Unknown
    objects are reduced to a bare ``[UNSUPPORTED_TYPE:<name>]`` marker and are NEVER
    ``repr()``-ed, so an object whose repr embeds a secret (the provisioning-repr defect
    class) cannot leak.
    """
    if _depth > _EVIDENCE_MAX_DEPTH:
        return "[REDACTED_MAX_DEPTH]"
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value
    if isinstance(value, str):
        return _scrub_secret_string(value)
    if isinstance(value, enum.Enum):
        return scrub_evidence(value.value, _depth=_depth + 1)
    if isinstance(value, (bytes, bytearray)):
        return f"[BYTES:{len(value)}]"
    if isinstance(value, Mapping):
        out: dict = {}
        for k, v in value.items():
            ks = str(k)
            out[ks] = _EVIDENCE_REDACTED if _is_sensitive_key(ks) else scrub_evidence(
                v, _depth=_depth + 1
            )
        return out
    if isinstance(value, (list, tuple, set, frozenset)):
        return [scrub_evidence(v, _depth=_depth + 1) for v in value]
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        out = {}
        for f in dataclasses.fields(value):
            out[f.name] = _EVIDENCE_REDACTED if _is_sensitive_key(f.name) else scrub_evidence(
                getattr(value, f.name), _depth=_depth + 1
            )
        return out
    # Unknown/unsupported type: bare type name only; NEVER repr() (may embed secrets).
    return f"[UNSUPPORTED_TYPE:{type(value).__name__}]"


def build_evidence_artifact(
    result: object,
    *,
    head: Optional[str] = None,
    capture_tag: Optional[str] = None,
    live_auth_tag: Optional[str] = None,
    exported_at: Optional[str] = None,
) -> dict:
    """Assemble the secret-safe artifact mapping from a (fake or real) LiveRunResult.

    Only identity metadata (HEAD, tag NAMES, verdict, reason) plus the recursively
    scrubbed evidence and an export timestamp. No credential, no environment dump, no
    Authorization header, no proxy/control token.
    """
    verdict = getattr(result, "verdict", None)
    verdict_name = getattr(verdict, "name", str(verdict))
    verdict_value = getattr(verdict, "value", verdict_name)
    reason = getattr(result, "reason", "")
    evidence = getattr(result, "evidence", {}) or {}
    return {
        "schema": EVIDENCE_EXPORT_SCHEMA,
        "head": scrub_evidence(head),
        "capture_evidence_tag": scrub_evidence(capture_tag),
        "live_auth_tag": scrub_evidence(live_auth_tag),
        "verdict": scrub_evidence(verdict_name),
        "verdict_value": scrub_evidence(verdict_value),
        "reason": scrub_evidence(reason),
        "evidence": scrub_evidence(evidence),
        "exported_at": exported_at
        or datetime.datetime.now(datetime.timezone.utc).isoformat(),
    }


def serialize_evidence_artifact(artifact: dict) -> bytes:
    """Deterministic UTF-8 JSON (sorted keys, ASCII, bounded separators, no BOM).

    The ``default`` backstop also degrades any stray unknown object to a bare type
    marker, never its ``repr()``.
    """
    text = json.dumps(
        artifact,
        sort_keys=True,
        ensure_ascii=True,
        separators=(",", ":"),
        default=lambda o: f"[UNSUPPORTED_TYPE:{type(o).__name__}]",
    )
    return text.encode("utf-8")


class EvidenceOutputPolicyError(ValueError):
    """Raised when an evidence destination violates the path-safety policy.

    Policy violations (a relative path, a destination inside the governed repository,
    a redirecting symlink/junction/reparse parent component, or an indeterminable
    reparse status) are fatal and fail closed BEFORE any governed execution, so they can
    never consume a one-shot grant.
    """


#: Canonical, CWD-INDEPENDENT governed repository root, derived from the committed
#: runner's own real on-disk location (never the process working directory).
_CANONICAL_REPO_ROOT = Path(os.path.realpath(str(_REPO_ROOT)))


def _path_within(ancestor: Path, descendant: Path) -> bool:
    """Component-aware containment (NOT string-prefix), case-normalized for Windows.

    True iff ``descendant`` is ``ancestor`` itself or lies beneath it. Paths on different
    drives are never contained. Uses ``commonpath`` so ``.../open-notebook2`` is not
    treated as inside ``.../open-notebook`` (§8).
    """
    a = os.path.normcase(str(ancestor))
    d = os.path.normcase(str(descendant))
    if a == d:
        return True
    try:
        return os.path.commonpath([a, d]) == a
    except ValueError:  # different drives / mixed abs-rel
        return False


def _is_redirecting_component(p: Path) -> bool:
    """True iff an existing path component is a symlink / junction / reparse point.

    Fail closed (``EvidenceOutputPolicyError``) if the reparse status cannot be
    determined on the host (§11 UNKNOWN_REPARSE_STATUS_POLICY = FAIL_CLOSED).
    """
    try:
        st = os.lstat(str(p))
    except OSError as exc:
        raise EvidenceOutputPolicyError(
            f"cannot determine reparse status (fail-closed): {p}: {exc}"
        )
    if stat.S_ISLNK(st.st_mode):
        return True
    attrs = getattr(st, "st_file_attributes", 0)
    if attrs & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400):
        return True
    if getattr(st, "st_reparse_tag", 0):
        return True
    return False


def _reject_redirecting_parent_chain(parent: Path) -> None:
    """Reject if ANY existing component on the way to ``parent`` can redirect traversal.

    Walks the full existing ancestor chain (not only the leaf parent), so an apparently
    external destination cannot be redirected into the repo, ``.git`` or another
    secret-sensitive location through a symlink/junction/reparse point (§10/§11).
    """
    current = parent
    while True:
        if current.exists() and _is_redirecting_component(current):
            raise EvidenceOutputPolicyError(
                "redirecting parent component (symlink/junction/reparse) rejected: "
                f"{current}"
            )
        nxt = current.parent
        if nxt == current:
            break
        current = nxt


def _validate_evidence_destination(
    output_path: object, *, overwrite: bool = False
) -> Path:
    """Resolve + policy-check an evidence destination; return the canonical final path.

    Enforced fail-closed, BEFORE any governed execution and AGAIN inside the writer
    (defense in depth, so a direct caller cannot bypass CLI preflight):
      * must be an ABSOLUTE path (no CWD/repo/home/temp reinterpretation) (§4);
      * parent directory must already exist (no recursive tree creation) (§6/§15);
      * no redirecting (symlink/junction/reparse) component anywhere on the parent
        chain, fail-closed on unknown status (§10/§11);
      * the canonical final target must be OUTSIDE the governed repository (the WHOLE
        tree — repo root, ``.git``, every tracked/untracked descendant), component-safe
        and case-normalized (§7/§8/§9);
      * FAIL_IF_EXISTS — never overwrite a prior one-shot's evidence (§15);
      * parent must be writable.
    """
    raw = str(output_path)
    if not os.path.isabs(raw):
        raise EvidenceOutputPolicyError(
            f"evidence output must be an absolute path (relative rejected): {raw!r}"
        )
    path = Path(raw)
    parent = path.parent
    if not parent.exists():
        raise FileNotFoundError(
            f"evidence output parent directory does not exist: {parent}"
        )
    _reject_redirecting_parent_chain(parent)
    # Canonicalize via the real parent + the requested leaf name, then check containment
    # on the canonical target (so a resolved symlink/junction cannot smuggle it in).
    real_parent = Path(os.path.realpath(str(parent)))
    final = real_parent / path.name
    if _path_within(_CANONICAL_REPO_ROOT, final) or _path_within(
        _CANONICAL_REPO_ROOT, real_parent
    ):
        raise EvidenceOutputPolicyError(
            f"evidence output inside the governed repository is rejected: {final}"
        )
    if final.exists() and not overwrite:
        raise FileExistsError(f"evidence output already exists (fail-closed): {final}")
    if not os.access(str(real_parent), os.W_OK):
        raise PermissionError(f"evidence output parent not writable: {real_parent}")
    return final


def preflight_evidence_output(output_path: object, *, overwrite: bool = False) -> None:
    """Validate the evidence destination BEFORE governed execution (§45).

    Fail-closed path-safety policy (see ``_validate_evidence_destination``): a relative
    path, a repo/``.git``/in-tree destination, a redirecting parent, a missing parent, an
    existing target, or an unwritable parent all raise here — before any grant is
    consumed. Creates nothing (no arbitrary directory trees).
    """
    _validate_evidence_destination(output_path, overwrite=overwrite)


def write_evidence_artifact(
    data: bytes, output_path: object, *, overwrite: bool = False
) -> dict:
    """Persist the artifact with RACE-SAFE, no-clobber final publication.

    Re-validates the full path-safety policy (defense in depth, so a direct caller cannot
    bypass CLI preflight — §14), writes the fully serialized bytes to an ``fsync``-ed
    temporary sibling, then publishes the final name with an exclusive ``os.link`` which
    FAILS (``FileExistsError``) if the target was created by anyone after preflight. There
    is no check-then-``os.replace`` window, so a concurrently-created target can NEVER be
    overwritten (§16 RACE_SAFE_NO_CLOBBER). The temp sibling is always cleaned up, so a
    failure leaves no partial final artifact (§17). Returns {path, sha256, bytes}.
    """
    final = _validate_evidence_destination(output_path, overwrite=overwrite)
    parent = final.parent
    fd, tmp = tempfile.mkstemp(dir=str(parent), prefix=".evidence-", suffix=".tmp")
    tmp_path: Optional[str] = tmp
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        if overwrite:
            os.replace(tmp, str(final))
            tmp_path = None
        else:
            # Exclusive, atomic, no-clobber publication: raises FileExistsError if the
            # final name exists — the fully written temp content is only ever linked
            # into place, never an unconditional overwrite.
            os.link(tmp, str(final))
    finally:
        if tmp_path is not None and os.path.exists(tmp_path):
            os.unlink(tmp_path)
    return {
        "path": str(final),
        "sha256": hashlib.sha256(data).hexdigest(),
        "bytes": len(data),
    }


def execute_and_export(
    *,
    deps_factory: Callable[[], "RunDeps"],
    evidence_output: object,
    run_validation: Optional[Callable[..., object]] = None,
    head_expected: Optional[str] = None,
    overwrite: bool = False,
    head: Optional[str] = None,
    capture_tag: Optional[str] = None,
    live_auth_tag: Optional[str] = None,
    exported_at: Optional[str] = None,
    _writer: Optional[Callable[..., dict]] = None,
) -> RunnerOutcome:
    """Preflight destination, run the governed one-shot EXACTLY ONCE, then export.

    Order is load-bearing for one-shot governance:
      1. ``preflight_evidence_output`` BEFORE execution (raises => grant NOT consumed);
      2. ``run`` the governed executor exactly once (never retried);
      3. build + serialize + atomically write the secret-safe artifact.

    If step 3 fails, execution is already terminal: the governed verdict/reason are
    preserved and returned with a DISTINCT non-success exit code — the executor is never
    called again (so a known-bad export target cannot burn a second one-shot).
    """
    # §45: pre-consumption destination validation.
    preflight_evidence_output(evidence_output, overwrite=overwrite)

    # §12/§46: exactly one governed invocation.
    result = run(
        deps_factory=deps_factory,
        run_validation=run_validation,
        head_expected=head_expected,
    )
    verdict = getattr(result, "verdict", None)
    verdict_name = getattr(verdict, "name", str(verdict))
    verdict_value = getattr(verdict, "value", verdict_name)
    reason = getattr(result, "reason", "")
    governed_exit = (
        EXIT_PASS if str(verdict_value) == _PASS_VERDICT_VALUE else EXIT_GOVERNED_FAIL
    )

    artifact = build_evidence_artifact(
        result,
        head=head,
        capture_tag=capture_tag,
        live_auth_tag=live_auth_tag,
        exported_at=exported_at,
    )
    data = serialize_evidence_artifact(artifact)
    writer = _writer or write_evidence_artifact
    try:
        info = writer(data, evidence_output, overwrite=overwrite)
    except Exception as exc:  # noqa: BLE001 — export failure must not rerun execution
        # §23/§24: governed run already terminal; DO NOT retry. Distinct runner failure,
        # governed verdict/reason preserved. Error text is scrubbed before surfacing.
        return RunnerOutcome(
            verdict=str(verdict_name),
            verdict_value=str(verdict_value),
            reason=str(reason),
            exit_code=EXIT_EVIDENCE_EXPORT_FAIL,
            export_ok=False,
            export_error=_scrub_secret_string(f"{type(exc).__name__}: {exc}"),
        )
    return RunnerOutcome(
        verdict=str(verdict_name),
        verdict_value=str(verdict_value),
        reason=str(reason),
        exit_code=governed_exit,
        export_ok=True,
        artifact_path=info["path"],
        artifact_sha256=info["sha256"],
        artifact_bytes=info["bytes"],
    )


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
    parser.add_argument(
        "--evidence-output",
        default=None,
        help="Path for the secret-safe JSON live-run evidence artifact (schema "
        f"{EVIDENCE_EXPORT_SCHEMA}). REQUIRED with --execute; fail-closed if the "
        "target already exists. Ignored by --check-context (no live artifact).",
    )
    args = parser.parse_args(argv)

    root = establish_root_context()
    if args.check_context:
        # §11: --check-context never creates a live evidence artifact, even if
        # --evidence-output is supplied.
        ok = canonical_chat_seam_importable()
        print(f"RUNNER_ROOT={root}")
        print(f"CANONICAL_CHAT_SEAM_IMPORT={'PASS' if ok else 'FAIL'}")
        return EXIT_PASS if ok else EXIT_CHECK_CONTEXT_FAIL

    if args.execute:
        # §9/§45: --execute REQUIRES a destination and validates it BEFORE consuming the
        # one-shot grant, so a known-invalid target never burns an execution.
        if not args.evidence_output:
            print(
                "ERROR: --execute requires --evidence-output <path> "
                "(fail-closed governed-evidence persistence)."
            )
            return EXIT_EVIDENCE_PREFLIGHT_FAIL
        head = _git("rev-parse", "HEAD")
        capture_tag = (
            args.current_head_capture_tag or _detect_current_head_capture_tag()
        )
        try:
            outcome = execute_and_export(
                deps_factory=lambda: build_default_deps(
                    current_head_capture_tag=args.current_head_capture_tag
                ),
                evidence_output=args.evidence_output,
                head=head,
                capture_tag=capture_tag,
            )
        except (
            EvidenceOutputPolicyError,
            FileExistsError,
            FileNotFoundError,
            PermissionError,
            OSError,
        ) as exc:
            # Raised by preflight BEFORE the governed executor ran => grant NOT consumed.
            print(f"EVIDENCE_OUTPUT_PREFLIGHT=FAIL ({type(exc).__name__}: {exc})")
            print(
                "Nothing executed: evidence destination invalid; governed grant "
                "NOT consumed."
            )
            return EXIT_EVIDENCE_PREFLIGHT_FAIL
        print(
            f"LIVE_VERDICT={outcome.verdict} ({outcome.verdict_value}) "
            f"reason={outcome.reason}"
        )
        if outcome.export_ok:
            print(f"EVIDENCE_ARTIFACT={outcome.artifact_path}")
            print(f"EVIDENCE_SCHEMA={EVIDENCE_EXPORT_SCHEMA}")
            print(f"EVIDENCE_SHA256={outcome.artifact_sha256}")
            print(f"EVIDENCE_BYTES={outcome.artifact_bytes}")
        else:
            # §24/§25: governed execution already terminal; NOT retried. Compact
            # secret-safe fallback only — never the raw evidence dict.
            print(f"EVIDENCE_EXPORT=FAIL ({outcome.export_error})")
            print(
                "Governed execution already terminal and NOT retried; verdict/reason "
                "above are authoritative."
            )
        # §24: never converts a governed FAIL to success, and an export failure is its
        # own distinct non-success exit code.
        return outcome.exit_code

    parser.print_help()
    print(
        "\nNothing executed: pass --check-context for a provider-free self-check, or "
        "--execute under an operator grant to run the governed one-shot."
    )
    return EXIT_NO_FLAG


if __name__ == "__main__":
    raise SystemExit(main())
