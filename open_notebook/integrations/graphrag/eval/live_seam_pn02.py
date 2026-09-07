"""Inert live-execution seam for a FUTURE PN02 live phase (task §59/§42/§60).

EVALUATION-ONLY. Nothing in production imports this. PN02B defines the interfaces
(``Protocol``s) a future live runner would implement, plus DELIBERATELY
NON-INVOKABLE stubs. There is NO hidden HTTP client here: the stubs raise
``LiveExecutionNotAuthorized`` before doing anything, so importing or wiring this
module can never produce provider traffic (``PN02_LIVE_AUTHORIZED = NO``).

The production Structured Evidence Adapter is NOT implemented (task §60):
``STRUCTURED_EVIDENCE_IMPLEMENTATION_READY = False``. Any future GD input contract
stays under this eval/test namespace and is vendor-facing, never production.
"""

from __future__ import annotations

from typing import AbstractSet, Optional, Protocol, Sequence, runtime_checkable

from open_notebook.integrations.graphrag.eval.manifestpn02 import WorkspaceAttestation
from open_notebook.integrations.graphrag.eval.schemaspn02 import (
    GDEvidenceResult,
    QAAnswerResult,
    VectorEvidenceResult,
)

# Frozen posture flags (task §60).
PN02_LIVE_AUTHORIZED = False
STRUCTURED_EVIDENCE_IMPLEMENTATION_READY = False
PRODUCTION_IMPORTS_EVAL = False


class LiveExecutionNotAuthorized(RuntimeError):
    """A live-execution seam was invoked. PN02B ships only inert stubs (task §59)."""


@runtime_checkable
class MembershipIndexerSeam(Protocol):
    """Future per-workspace graph indexer (one membership edge -> one index op)."""

    def index_membership(
        self, workspace_id: str, source_key: str, text: str
    ) -> None: ...


@runtime_checkable
class GDQuerySeam(Protocol):
    """Future notebook-isolated ``/query/data`` seam (requires attestation first)."""

    async def query_evidence(
        self,
        attestation: WorkspaceAttestation,
        question: str,
        *,
        benchmark_ids: Optional[AbstractSet[str]] = None,
    ) -> GDEvidenceResult: ...


@runtime_checkable
class VectorQuerySeam(Protocol):
    """Future notebook-scoped vector baseline seam (member-scoped, §8b)."""

    async def query(
        self, notebook_id: str, question: str, *, k: int
    ) -> VectorEvidenceResult: ...


@runtime_checkable
class FinalAnswerSeam(Protocol):
    """Future ON-owned final-answer seam (QA-V / QA-GD / QA-V+GD)."""

    async def answer(
        self, notebook_id: str, question: str, evidence_source_ids: Sequence[str]
    ) -> QAAnswerResult: ...


class _InertSeam:
    """Base for all inert stubs — every method refuses before any side effect."""

    def _refuse(self, name: str) -> "LiveExecutionNotAuthorized":
        return LiveExecutionNotAuthorized(
            f"{name} is an inert PN02B stub: live execution is NOT authorized "
            "(PN02_LIVE_AUTHORIZED=NO). No provider traffic is possible here."
        )


class InertMembershipIndexer(_InertSeam):
    def index_membership(self, workspace_id: str, source_key: str, text: str) -> None:
        raise self._refuse("index_membership")


class InertGDQuerySeam(_InertSeam):
    async def query_evidence(
        self,
        attestation: WorkspaceAttestation,
        question: str,
        *,
        benchmark_ids: Optional[AbstractSet[str]] = None,
    ) -> GDEvidenceResult:
        raise self._refuse("query_evidence")


class InertVectorQuerySeam(_InertSeam):
    async def query(
        self, notebook_id: str, question: str, *, k: int
    ) -> VectorEvidenceResult:
        raise self._refuse("query")


class InertFinalAnswerSeam(_InertSeam):
    async def answer(
        self, notebook_id: str, question: str, evidence_source_ids: Sequence[str]
    ) -> QAAnswerResult:
        raise self._refuse("answer")


__all__ = [
    "PN02_LIVE_AUTHORIZED",
    "STRUCTURED_EVIDENCE_IMPLEMENTATION_READY",
    "PRODUCTION_IMPORTS_EVAL",
    "LiveExecutionNotAuthorized",
    "MembershipIndexerSeam",
    "GDQuerySeam",
    "VectorQuerySeam",
    "FinalAnswerSeam",
    "InertMembershipIndexer",
    "InertGDQuerySeam",
    "InertVectorQuerySeam",
    "InertFinalAnswerSeam",
]
