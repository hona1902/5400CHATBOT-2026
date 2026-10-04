"""GraphRAG-09E dedicated citable-provenance validator (provider-free).

Converts UNTRUSTED graph references into typed, immutable ``ValidatedProvenance`` ONLY after
canonical Open Notebook authority confirms, against CURRENT database state, that:

    1. the graph-supplied source identifier is structurally a canonical source RecordID; and
    2. that source still EXISTS (not deleted); and
    3. that source is CURRENTLY a member of the requesting notebook via the canonical
       ``reference`` relation (in = source, out = notebook).

Authority hierarchy (frozen, 09E): Open Notebook / SurrealDB is the sole provenance authority for
source existence, identity, notebook membership and citation eligibility. The graph sidecar supplies
only candidate identifier HINTS; nothing it returns — including ``GraphReference.resolved`` (a
structural shape flag) — is ever trusted as citation authority. Every reference is re-authorized
canonically.

Scope: identity + notebook-scope validation ONLY. A ``ValidatedProvenance`` means "this reference
points to a currently-live source currently permitted in this notebook" — it does NOT mean the graph's
semantic claim is factually verified, and it carries NO graph answer text, excerpts, raw source content
or secrets. Citation CONTENT authority remains the canonical ON source/chunk pipeline.

This module performs NO sidecar/provider call and alters NO raw GraphRAG query API: it consumes
already-parsed ``GraphReference`` objects. Validation is bounded, batched (no N+1), order-preserving,
deduplicated by canonical source id, fail-closed on any uncertainty, and content-safe on error.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Awaitable, Callable, Optional, Sequence

from loguru import logger

from open_notebook.database.repository import ensure_record_id
from open_notebook.database.repository import repo_query as _default_repo_query
from open_notebook.integrations.graphrag.models import (
    _INDEXABLE_TABLES,
    GraphRAGValidationError,
    GraphReference,
    record_id_for,
)

#: Hard cap on how many references are ever considered / looked up / returned in one call. A bad or
#: hostile sidecar response can never force an unbounded parameter list or result set.
MAX_REFERENCES = 50

#: Fixed, content-safe rejection vocabulary (surfaced only as counters, never with raw content).
REASON_MALFORMED = "malformed_graph_reference"
REASON_INVALID_ID = "invalid_source_id"
REASON_ABSENT = "source_absent"
REASON_NOT_MEMBER = "not_notebook_member"
REASON_DUPLICATE = "duplicate_reference"
REASON_VALIDATION_UNAVAILABLE = "validation_unavailable"

#: A repo-query callable: ``(surrealql, params) -> awaitable[list[dict]]``. Injected so the validator
#: is trivially provider-free and unit-testable; defaults to the real repository query.
RepoQuery = Callable[..., Awaitable[list]]


@dataclass(frozen=True)
class ValidatedProvenance:
    """A graph reference that canonical authority confirmed is live AND in the requesting notebook.

    MEANS: identity + notebook scope are valid at validation time.
    DOES NOT MEAN: the graph's semantic claim is factually verified, nor that graph content is fresh
    relative to the current canonical source. Carries only canonical-safe identity fields — never a
    graph answer, excerpt, raw source content, or secret.
    """

    canonical_source_id: str
    notebook_id: str
    rank: int  # original graph order among the considered references
    title: Optional[str] = None  # canonical source title, if returned by the existence query


@dataclass(frozen=True)
class ProvenanceValidationResult:
    """Immutable outcome: the validated provenance plus content-free counters (no raw refs/content)."""

    validated: tuple[ValidatedProvenance, ...] = ()
    refs_received: int = 0
    refs_considered: int = 0
    refs_validated: int = 0
    rejected_malformed: int = 0
    rejected_invalid_id: int = 0
    rejected_absent: int = 0
    rejected_not_member: int = 0
    duplicates_removed: int = 0
    validation_error: int = 0


@dataclass
class _Counters:
    refs_received: int = 0
    refs_considered: int = 0
    rejected_malformed: int = 0
    rejected_invalid_id: int = 0
    rejected_absent: int = 0
    rejected_not_member: int = 0
    duplicates_removed: int = 0
    validation_error: int = 0

    def result(self, validated: tuple[ValidatedProvenance, ...]) -> ProvenanceValidationResult:
        return ProvenanceValidationResult(
            validated=validated,
            refs_received=self.refs_received,
            refs_considered=self.refs_considered,
            refs_validated=len(validated),
            rejected_malformed=self.rejected_malformed,
            rejected_invalid_id=self.rejected_invalid_id,
            rejected_absent=self.rejected_absent,
            rejected_not_member=self.rejected_not_member,
            duplicates_removed=self.duplicates_removed,
            validation_error=self.validation_error,
        )


async def validate_graph_references(
    references: Sequence[GraphReference],
    *,
    notebook_id: str,
    repo_query: RepoQuery = _default_repo_query,
    max_references: int = MAX_REFERENCES,
) -> ProvenanceValidationResult:
    """Validate untrusted ``references`` for ``notebook_id``; return typed validated provenance.

    Pipeline (bounded ``max_references``):
      1. structural: reject missing source_id (malformed) / non-canonical source_id (invalid_id)
         BEFORE any DB lookup;
      2. canonical existence: ONE batched ``source`` query (current state) — missing => absent;
      3. notebook membership: ONE batched ``reference`` query (in=source, out=notebook) — missing
         => not_notebook_member;
      4. keep validated refs in original graph order, deduplicated by canonical source id.

    Fail-closed: any DB/membership error (or an unusable notebook id) yields NO validated provenance
    and a ``validation_error`` counter — never a graph-authorized fallback, never the ``resolved``
    flag. Content-safe: no exception text / raw graph body / source content appears in the result.
    """
    c = _Counters(refs_received=len(references))

    bound = max(0, int(max_references))
    considered = list(references)[:bound]
    c.refs_considered = len(considered)

    # --- 1. structural validation (no DB, no network) ------------------------------- #
    # candidate = (canonical_key, record_id, original_rank)
    candidates: list[tuple[str, object, int]] = []
    for rank, ref in enumerate(considered):
        raw = ref.source_id
        if not isinstance(raw, str) or not raw.strip():
            c.rejected_malformed += 1
            continue
        try:
            rid = record_id_for(raw, tables=_INDEXABLE_TABLES)
        except GraphRAGValidationError:
            # Non-canonical / path-URL-token-shaped id: reject BEFORE any DB query. The rejected
            # value is never logged (it may be hostile content).
            c.rejected_invalid_id += 1
            continue
        candidates.append((str(rid), rid, rank))

    if not candidates:
        return c.result(())

    # Parameterize the notebook identity as a typed RecordID (never string-interpolated). An
    # unusable notebook id fails closed rather than authorizing anything.
    try:
        notebook_rid = ensure_record_id(notebook_id)
    except Exception:  # noqa: BLE001 - content-safe: do not leak the raw value
        c.validation_error += 1
        return c.result(())

    # Unique id params preserving first-seen order (bounded by candidates, itself bounded).
    seen_param: set[str] = set()
    id_params: list[object] = []
    for key, cand_rid, _rank in candidates:
        if key not in seen_param:
            seen_param.add(key)
            id_params.append(cand_rid)

    # --- 2 & 3. batched canonical existence + membership (fail-closed) --------------- #
    try:
        live_rows = await repo_query(
            "SELECT id, title FROM source WHERE id IN $ids", {"ids": id_params}
        )
        member_rows = await repo_query(
            "SELECT in FROM reference WHERE out = $notebook AND in IN $ids",
            {"notebook": notebook_rid, "ids": id_params},
        )
    except Exception as e:  # noqa: BLE001 - never leak query/exception text; fail closed
        logger.warning(f"GraphRAG provenance validation unavailable: {type(e).__name__}")
        c.validation_error += 1
        return c.result(())

    live_titles: dict[str, Optional[str]] = {}
    for row in live_rows or []:
        rid_val = row.get("id")
        if rid_val is not None:
            title = row.get("title")
            live_titles[str(rid_val)] = title if isinstance(title, str) else None

    members: set[str] = set()
    for row in member_rows or []:
        in_val = row.get("in")
        if in_val is not None:
            members.add(str(in_val))

    # --- 4. authorize in original order, dedup by canonical source id ---------------- #
    validated: list[ValidatedProvenance] = []
    emitted: set[str] = set()
    for key, _rid, rank in candidates:
        if key not in live_titles:
            c.rejected_absent += 1
            continue
        if key not in members:
            c.rejected_not_member += 1
            continue
        if key in emitted:
            c.duplicates_removed += 1
            continue
        emitted.add(key)
        validated.append(
            ValidatedProvenance(
                canonical_source_id=key,
                notebook_id=str(notebook_rid),
                rank=rank,
                title=live_titles.get(key),
            )
        )

    return c.result(tuple(validated))


__all__ = [
    "MAX_REFERENCES",
    "REASON_MALFORMED",
    "REASON_INVALID_ID",
    "REASON_ABSENT",
    "REASON_NOT_MEMBER",
    "REASON_DUPLICATE",
    "REASON_VALIDATION_UNAVAILABLE",
    "ValidatedProvenance",
    "ProvenanceValidationResult",
    "validate_graph_references",
]
