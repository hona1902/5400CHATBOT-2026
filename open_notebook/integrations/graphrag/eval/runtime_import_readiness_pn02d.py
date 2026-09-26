"""PN02D-B3Y — provider-free preclaim runtime import-readiness guard.

Root cause of the B3Y postclaim failure: the governed run was launched from a wrapper
script outside the repository root, so ``sys.path`` did not include the repo root. The
editable install exposes only ``open_notebook`` — NOT the first-party top-level
``commands`` package — so the corpus-embed step's lazy
``from commands.embedding_commands import ...`` (corpuslivepn02d.py:266) raised
``ModuleNotFoundError: No module named 'commands'`` AFTER the irreversible one-shot ledger
claim, burning the grant with zero provider traffic.

This module provides a small, explicit, deterministic guard that resolves the exact set
of live-path imports the governed B3 run depends on. ``RealB1Driver.run`` calls it AFTER
mint validation and BEFORE the one-shot ledger claim, so an invalid launch import
environment FAILS CLOSED here — no claim, no provider contact, no grant burn.

It is NOT a recursive whole-application import: the checked set is an explicit, reviewable
tuple. It performs NO provider I/O, reads NO secrets, and mutates NO ledger. A failure is
classified as a LOCAL runtime import problem — never a provider failure.
"""

from __future__ import annotations

import importlib
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

#: Explicit, reviewable set of (module, required_symbols) the governed B3 live path imports
#: lazily during execution. ``commands.embedding_commands`` (+ its two symbols) is the exact
#: surface that failed in B3Y; the rest are the other live-path imports identified in the
#: forensic analysis (model provisioning, esperanto conversion, treatment materializer,
#: final-answer seam, isolation/runtime boot). Kept explicit on purpose — do NOT expand this
#: into a recursive package walk.
REQUIRED_LIVE_IMPORTS: Tuple[Tuple[str, Tuple[str, ...]], ...] = (
    # THE exact B3Y failure surface (first-party top-level package; needs repo root on sys.path).
    ("commands.embedding_commands", ("EmbedSourceInput", "embed_source_command")),
    # Embedding + final-answer model provisioning.
    ("open_notebook.ai.models", ("model_manager",)),
    ("open_notebook.ai.provision", ("provision_langchain_model",)),
    ("esperanto", ("AIFactory",)),
    # Treatment generation-evidence materialization + its Source fetch.
    (
        "open_notebook.integrations.graphrag.eval.evidence_materialization_pn02d",
        ("build_runtime_evidence_materializer",),
    ),
    ("open_notebook.domain.notebook", ("Source",)),
    # Final-answer seam + isolation/runtime boot surface (module resolution only).
    ("open_notebook.integrations.graphrag.eval.real_final_answer_seam_pn02d", ()),
    ("open_notebook.integrations.graphrag.eval.isolation08", ()),
)


@dataclass(frozen=True)
class ImportReadinessReport:
    """Content-safe structured result of the import-readiness check.

    Carries only module names, symbol names, an error-type label and the missing-module
    name (all safe-by-construction) — never source content, prompt, answer text or secrets.
    """

    status: str  # "OK" | "FAIL"
    checked_imports: Tuple[str, ...]
    failed_import: Optional[str] = None
    failing_symbol: Optional[str] = None
    missing_module_name: Optional[str] = None
    error_type: Optional[str] = None

    def as_safe_dict(self) -> Dict[str, object]:
        return {
            "status": self.status,
            "checked_imports": list(self.checked_imports),
            "failed_import": self.failed_import,
            "failing_symbol": self.failing_symbol,
            "missing_module_name": self.missing_module_name,
            "error_type": self.error_type,
            "classification": "LOCAL_RUNTIME_IMPORT_UNRESOLVABLE",
        }


class LiveRuntimeImportReadinessError(RuntimeError):
    """Raised (fail-closed) when a required live-path import cannot be resolved.

    This is a LOCAL runtime/launch-environment failure (e.g. repo root absent from
    ``sys.path`` so the first-party ``commands`` package is unresolvable) — it is NOT a
    provider failure and must never be classified as one. It carries only content-safe
    identifiers.
    """

    def __init__(self, report: ImportReadinessReport) -> None:
        self.report = report
        detail = report.error_type or "import_error"
        if report.missing_module_name:
            detail = f"{detail}: no module named {report.missing_module_name!r}"
        elif report.failing_symbol:
            detail = f"{detail}: {report.failed_import} missing symbol {report.failing_symbol!r}"
        super().__init__(
            "governed B3 live run pre-claim import readiness failed "
            f"(LOCAL_RUNTIME_IMPORT_UNRESOLVABLE; {report.failed_import}: {detail})"
        )

    def as_safe_dict(self) -> Dict[str, object]:
        return self.report.as_safe_dict()


def check_live_runtime_import_readiness(
    required: Tuple[Tuple[str, Tuple[str, ...]], ...] = REQUIRED_LIVE_IMPORTS,
) -> ImportReadinessReport:
    """Resolve the explicit live-path import set. Provider-free; no ledger, no secrets.

    Returns an :class:`ImportReadinessReport` (``status`` "OK"/"FAIL"). The first
    unresolvable module (``ModuleNotFoundError``/``ImportError``) or missing required symbol
    stops the check and is reported with content-safe identifiers only.
    """
    checked: List[str] = []
    for module_name, symbols in required:
        try:
            module = importlib.import_module(module_name)
        except ModuleNotFoundError as exc:
            return ImportReadinessReport(
                status="FAIL",
                checked_imports=tuple(checked),
                failed_import=module_name,
                missing_module_name=exc.name,
                error_type="ModuleNotFoundError",
            )
        except ImportError as exc:  # pragma: no cover - defensive; other import failures
            return ImportReadinessReport(
                status="FAIL",
                checked_imports=tuple(checked),
                failed_import=module_name,
                missing_module_name=getattr(exc, "name", None),
                error_type=type(exc).__name__,
            )
        for symbol in symbols:
            if not hasattr(module, symbol):
                return ImportReadinessReport(
                    status="FAIL",
                    checked_imports=tuple(checked),
                    failed_import=module_name,
                    failing_symbol=symbol,
                    error_type="AttributeError",
                )
        checked.append(module_name)
    return ImportReadinessReport(status="OK", checked_imports=tuple(checked))


def assert_live_runtime_import_readiness(
    required: Tuple[Tuple[str, Tuple[str, ...]], ...] = REQUIRED_LIVE_IMPORTS,
) -> ImportReadinessReport:
    """Fail-closed wrapper: raise :class:`LiveRuntimeImportReadinessError` unless status OK."""
    report = check_live_runtime_import_readiness(required)
    if report.status != "OK":
        raise LiveRuntimeImportReadinessError(report)
    return report


__all__ = [
    "REQUIRED_LIVE_IMPORTS",
    "ImportReadinessReport",
    "LiveRuntimeImportReadinessError",
    "check_live_runtime_import_readiness",
    "assert_live_runtime_import_readiness",
]
