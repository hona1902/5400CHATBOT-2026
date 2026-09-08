"""Canonical/derived document identity + run-owned mapping store (PN02D-B0B).

EVALUATION-ONLY. Nothing in production imports this (``PRODUCTION_IMPORTS_EVAL =
NO``; the dependency direction is eval -> production only). Implements the frozen
B0A identity model (design §13/§R1.1-R1.6): three identities are kept strictly
separate —

  * ``CANONICAL_SOURCE_ID``      — Open Notebook-owned, cross-notebook stable
    (the fixture key ``A1`` / ``SH_AB``);
  * ``WORKSPACE_ID``             — the notebook-local LightRAG namespace/runtime
    (``"nb_"+sha256(record_id)[:16]``, ``manifestpn02.workspace_id_for``);
  * ``DERIVED_LIGHTRAG_DOCUMENT_ID`` — the vendor id of the workspace-local
    indexed representation.

The authoritative logical lookup key is the PAIR ``(workspace_id,
canonical_source_id)`` — never the endpoint/port and never the vendor id alone
(``ENDPOINT_IDENTITY_IS_DOCUMENT_IDENTITY = NO``, design §R1.2). The vendor
derivation is verified against pinned LightRAG v1.5.6 (design §R1.3):
``doc_id = "doc-"+md5(file_source)`` and ON always sets ``file_source =
canonical_source_id`` — so the vendor id is deterministic and content-independent,
and the SAME canonical Source yields the SAME vendor id in every workspace.
Collision is safe ONLY because the two live in isolated workspaces reached through
distinct attested endpoints (design §14/§29). This module computes ids and keeps a
run-owned in-memory mapping; it mutates NO canonical Open Notebook state, opens no
DB, and makes no network/provider call.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Dict, Optional, Tuple

#: Endpoint/port is routing state, NOT identity (design §R1.2).
ENDPOINT_IDENTITY_IS_DOCUMENT_IDENTITY = False

# Index-status vocabulary for a run-owned mapping record (design §R1.5).
INDEX_STATUS_PLANNED = "PLANNED"
INDEX_STATUS_SUBMITTED = "SUBMITTED"
INDEX_STATUS_PROCESSED = "PROCESSED"
INDEX_STATUS_FAILED = "FAILED"


class DocIdentityError(ValueError):
    """A document-identity or mapping-store invariant was violated (fail-closed)."""


def compute_derived_document_id(canonical_source_id: str) -> str:
    """Vendor derived doc id for a canonical Source (design §R1.3, verified v1.5.6).

    ``doc-`` + md5(file_source), where ON always supplies ``file_source =
    canonical_source_id``. Deterministic and content-independent; identical across
    workspaces for the same canonical Source (safe only under workspace isolation).
    """
    key = (canonical_source_id or "").strip()
    if not key:
        raise DocIdentityError("canonical_source_id must be non-empty")
    return "doc-" + hashlib.md5(key.encode("utf-8")).hexdigest()  # noqa: S324 - vendor-fixed


@dataclass(frozen=True)
class LogicalDocumentKey:
    """The authoritative per-membership logical key (design §R1.4)."""

    workspace_id: str
    canonical_source_id: str

    def __post_init__(self) -> None:
        if not self.workspace_id or not self.canonical_source_id:
            raise DocIdentityError("logical key needs both workspace_id and source id")


@dataclass(frozen=True)
class DeleteTarget:
    """A per-workspace delete address (design §R1.6/§29).

    Deletion addresses the derived document WITHIN a specific workspace's attested
    endpoint; the workspace id is carried so the removal executor can prove the
    target endpoint == route(that workspace) before dispatch. A (workspace_A,
    SH_AB) target can never be dispatched to workspace B even though the vendor
    ``derived_document_id`` is identical.
    """

    workspace_id: str
    notebook_id: str
    canonical_source_id: str
    derived_document_id: str


@dataclass
class DocMappingRecordPN02:
    """Run-owned derived-document mapping record (design §R1.5). Content-safe.

    Never a canonical ON record — a purely in-run bookkeeping row. Carries no
    Source text and no secret.
    """

    canonical_source_id: str
    workspace_id: str
    notebook_id: str
    derived_document_id: str
    content_identity: Optional[str] = None
    index_operation_id: Optional[str] = None
    index_status: str = INDEX_STATUS_PLANNED

    def logical_key(self) -> LogicalDocumentKey:
        return LogicalDocumentKey(self.workspace_id, self.canonical_source_id)

    def delete_target(self) -> DeleteTarget:
        return DeleteTarget(
            workspace_id=self.workspace_id,
            notebook_id=self.notebook_id,
            canonical_source_id=self.canonical_source_id,
            derived_document_id=self.derived_document_id,
        )

    def as_dict(self) -> Dict[str, object]:
        return {
            "canonical_source_id": self.canonical_source_id,
            "workspace_id": self.workspace_id,
            "notebook_id": self.notebook_id,
            "derived_document_id": self.derived_document_id,
            "content_identity": self.content_identity,
            "index_operation_id": self.index_operation_id,
            "index_status": self.index_status,
        }


class DerivedDocMappingStore:
    """Run-owned, in-memory ``(workspace_id, canonical_source_id) -> record`` store.

    Injectable and reset per run. Two workspaces may hold the same vendor
    ``derived_document_id`` for the same canonical Source; they remain distinct
    logical rows because the key is the pair, not the vendor id (design §14/§17).
    """

    def __init__(self) -> None:
        self._by_key: Dict[Tuple[str, str], DocMappingRecordPN02] = {}

    def register(
        self,
        *,
        canonical_source_id: str,
        workspace_id: str,
        notebook_id: str,
        content_identity: Optional[str] = None,
    ) -> DocMappingRecordPN02:
        """Create (or return the existing) mapping row for a membership edge."""
        key = LogicalDocumentKey(workspace_id, canonical_source_id)
        existing = self._by_key.get((workspace_id, canonical_source_id))
        if existing is not None:
            return existing
        record = DocMappingRecordPN02(
            canonical_source_id=canonical_source_id,
            workspace_id=workspace_id,
            notebook_id=notebook_id,
            derived_document_id=compute_derived_document_id(canonical_source_id),
            content_identity=content_identity,
        )
        self._by_key[(key.workspace_id, key.canonical_source_id)] = record
        return record

    def get(
        self, workspace_id: str, canonical_source_id: str
    ) -> Optional[DocMappingRecordPN02]:
        return self._by_key.get((workspace_id, canonical_source_id))

    def require(
        self, workspace_id: str, canonical_source_id: str
    ) -> DocMappingRecordPN02:
        record = self.get(workspace_id, canonical_source_id)
        if record is None:
            raise DocIdentityError(
                f"no derived-document mapping for ({workspace_id}, {canonical_source_id})"
            )
        return record

    def resolve_delete_target(
        self, workspace_id: str, canonical_source_id: str
    ) -> DeleteTarget:
        """Address a delete to the derived doc WITHIN this workspace only (§29)."""
        return self.require(workspace_id, canonical_source_id).delete_target()

    def __len__(self) -> int:
        return len(self._by_key)

    def records(self) -> Tuple[DocMappingRecordPN02, ...]:
        return tuple(self._by_key.values())


__all__ = [
    "ENDPOINT_IDENTITY_IS_DOCUMENT_IDENTITY",
    "INDEX_STATUS_PLANNED",
    "INDEX_STATUS_SUBMITTED",
    "INDEX_STATUS_PROCESSED",
    "INDEX_STATUS_FAILED",
    "DocIdentityError",
    "compute_derived_document_id",
    "LogicalDocumentKey",
    "DeleteTarget",
    "DocMappingRecordPN02",
    "DerivedDocMappingStore",
]
