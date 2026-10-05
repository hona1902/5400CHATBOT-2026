"""GraphRAG-09F product-surface provenance helper (provider-free, post-answer, additive).

Turns an UNTRUSTED graph query into a bounded, content-free provenance MARKER for a chat
answer. It is PROVENANCE-ONLY (AGR-005 / GraphRAG-09F): it never adds evidence, never expands
or reorders the chat answer context, never reranks, never alters a model prompt or the model
call count, never generates answer text, and is never a citation authority. Canonical Open
Notebook chat remains the sole answer authority; the graph sidecar supplies only candidate
identifier hints that canonical authority (09E) must re-validate.

Pipeline (at most one graph query + at most one 09E validation pass per chat request):

    1. feature gate FIRST (``load_config``). Disabled/unconfigured => return ``None`` with ZERO
       work: no GraphRAG service/client constructed, no graph query, no validation, no DB query.
    2. short-circuit when there is no canonical answer-context source set (nothing could ever
       survive the intersection) BEFORE any graph query.
    3. one raw graph query, keyed on the CURRENT user message only (minimizes Boundary-B
       exposure). Typed GraphRAG failure => return ``None`` (canonical chat is unaffected).
    4. one 09E ``validate_graph_references`` pass, scoped to the SERVER-RESOLVED notebook id.
       Validation unavailable/empty => return ``None``.
    5. intersect the 09E-validated canonical source ids with the caller's canonical answer-context
       source ids, in CANONICAL CONTEXT ORDER. A graph-validated source that is NOT already in the
       chat answer context is DROPPED from the product surface.

The result carries only canonical source ids already present in the chat answer context, plus a
count and a boolean. It carries NO graph answer text, excerpts, relationship/entity labels,
scores, graph document ids, reference ids, or raw source content. Boundary B (real internal data
egress) remains unapproved; with the feature off (default) this module does nothing.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Awaitable, Callable, Optional, Sequence

from loguru import logger

from open_notebook.database.repository import repo_query as _default_repo_query
from open_notebook.integrations.graphrag.config import load_config
from open_notebook.integrations.graphrag.models import (
    _INDEXABLE_TABLES,
    GraphRAGError,
    GraphRAGValidationError,
    GraphReference,
    record_id_for,
)
from open_notebook.integrations.graphrag.provenance import (
    ProvenanceValidationResult,
    RepoQuery,
    validate_graph_references,
)

#: A graph-query callable: ``(question) -> awaitable[sequence[GraphReference] | None]``. Injected
#: so the helper is trivially provider-free and unit-testable; the default runs the real diagnostic
#: graph query through the flag-gated service (constructed ONLY after the feature gate passes).
GraphQuery = Callable[[str], Awaitable[Optional[Sequence[GraphReference]]]]

#: A 09E validator callable, defaulting to the real captured validator. Injected for tests only.
Validate = Callable[..., Awaitable[ProvenanceValidationResult]]


@dataclass(frozen=True)
class ProductProvenance:
    """Bounded, immutable, content-free provenance marker for a chat answer.

    ``source_ids`` are canonical Open Notebook source ids that are BOTH (a) present in the chat
    answer context and (b) 09E-validated (live source + current notebook membership), in canonical
    context order. Never a graph-only id, never graph content.
    """

    has_validated_graph_provenance: bool
    source_ids: tuple[str, ...]
    count: int


def _canonical_context_keys(canonical_source_ids: Sequence[str]) -> list[str]:
    """Canonicalize caller-supplied context source ids into 09E's key space, order-preserving.

    Uses the SAME ``record_id_for`` canonicalization the validator applies to graph source ids, so
    the two sides intersect in one canonical space. Malformed / non-``source`` ids are skipped for
    provenance purposes (they never reach a DB query and never break chat); duplicates collapse to
    their first-seen position.
    """
    keys: list[str] = []
    seen: set[str] = set()
    for raw in canonical_source_ids:
        if not isinstance(raw, str) or not raw.strip():
            continue
        try:
            key = str(record_id_for(raw, tables=_INDEXABLE_TABLES))
        except GraphRAGValidationError:
            # Not a canonical source id (should not happen for chat context sources); skip for
            # provenance only. The offending value is never logged (content-safe).
            continue
        if key not in seen:
            seen.add(key)
            keys.append(key)
    return keys


async def _default_graph_query(question: str) -> Optional[Sequence[GraphReference]]:
    """Run the real diagnostic graph query through the flag-gated service.

    Constructed here, AFTER the caller's feature gate has already passed, so a disabled feature
    never builds a GraphRAG service/client. Uses the strict query so typed ``GraphRAGError`` surfaces
    to the helper boundary (which maps every expected failure to "no provenance").
    """
    # Imported lazily so merely importing this module never pulls the service/client graph into a
    # disabled deployment's import path at call-gate time.
    from open_notebook.integrations.graphrag.service import GraphRAGService

    service = GraphRAGService()
    result = await service.query_strict(question)
    # Only the untrusted reference identifiers are consumed; the sidecar-generated ``answer`` prose
    # and any excerpts are deliberately discarded here and never cross into the product surface.
    return result.references


async def build_chat_graph_provenance(
    *,
    question: str,
    notebook_id: Optional[str],
    canonical_source_ids: Sequence[str],
    graph_query: Optional[GraphQuery] = None,
    validate: Validate = validate_graph_references,
    repo_query: RepoQuery = _default_repo_query,
) -> Optional[ProductProvenance]:
    """Build a bounded provenance marker for a completed chat answer, or ``None``.

    Returns ``None`` (the normal case) whenever the feature is off, there is no answer-context
    source set, no server-resolved notebook, the graph query or validation is unavailable, or the
    validated set does not intersect the answer context. Never raises for an EXPECTED GraphRAG
    runtime failure: those are caught at the typed ``GraphRAGError`` boundary and degrade to
    ``None`` so the canonical chat response is unaffected. Programming errors are NOT swallowed.
    """
    # --- 1. feature gate FIRST — zero work when disabled ----------------------------- #
    config = load_config()
    if not config.configured:
        return None

    # --- 2. no answer-context sources => nothing can survive intersection ------------ #
    context_keys = _canonical_context_keys(canonical_source_ids)
    if not context_keys:
        return None

    # Notebook scope MUST come from the server-resolved session notebook, never the graph.
    if not isinstance(notebook_id, str) or not notebook_id.strip():
        return None

    # --- 3. one raw graph query (current user message only) -------------------------- #
    query = graph_query or _default_graph_query
    try:
        references = await query(question)
    except GraphRAGError:
        logger.debug("graphrag 09F provenance: graph query unavailable; omitting provenance")
        return None
    if not references:
        return None

    # --- 4. one 09E canonical validation pass (server-resolved notebook scope) -------- #
    try:
        result = await validate(references, notebook_id=notebook_id, repo_query=repo_query)
    except GraphRAGError:
        logger.debug("graphrag 09F provenance: validation unavailable; omitting provenance")
        return None

    if not result.validated:
        # Covers validation_error (fail-closed) and "nothing validated" alike.
        return None

    # --- 5. intersect validated ids with answer-context ids, in canonical context order #
    validated_keys = {vp.canonical_source_id for vp in result.validated}
    product_ids = tuple(key for key in context_keys if key in validated_keys)
    if not product_ids:
        return None

    logger.debug(
        "graphrag 09F provenance attached: {n} validated answer-context source(s)",
        n=len(product_ids),
    )
    return ProductProvenance(
        has_validated_graph_provenance=True,
        source_ids=product_ids,
        count=len(product_ids),
    )


__all__ = [
    "GraphQuery",
    "Validate",
    "ProductProvenance",
    "build_chat_graph_provenance",
]
