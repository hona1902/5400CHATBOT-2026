"""Real notebook-local vector backend (Approach B) for the PN02 LIVE driver (B0C-B).

EVALUATION-ONLY. Nothing in production imports this (``PRODUCTION_IMPORTS_EVAL =
NO``). This is the load-bearing real seam behind the B0B ``VectorBackend`` Protocol +
``VectorBackendFactory`` (design §13/§25-§29). The executor
(``vectorlivepn02d.NotebookLocalVectorExecutor``) resolves the queried notebook's
CURRENT member Source ids and hands ONLY those to ``rank_members`` — so candidate
restriction happens BEFORE ranking and a global-top-K-then-post-filter design is
structurally impossible.

**Approach B (frozen, design §13/§26):** the production ``fn::vector_search`` has no
member prefilter (it is a GLOBAL top-K), so instead of a schema migration this backend
fetches ONLY the member rows —
``SELECT source, embedding FROM source_embedding WHERE source IN $ids AND embedding !=
NONE AND array::len(embedding) = array::len($q)`` — computes a genuine cosine
(``dot / (‖a‖‖b‖)``, matching ``vector::similarity::cosine``), takes the ``max`` per
source (mirroring the DB ``math::max`` group-by), sorts desc, and returns one ranked
member list (K=3/K=5 are slices, applied by the evaluator). ``GLOBAL_TOPK_THEN_POST
FILTER_PRESENT = NO``.

**Query embedding (design §13, task §28/§29):** ``embed_query`` reuses the standalone
``utils/embedding.generate_embedding`` (a plain async fn, invoked WITHOUT the
production Ask flow). Before ANY query embedding it attests the active embedding model
identity + dimension (finding L-4): provider ``openrouter``, model
``openai/text-embedding-3-small``, dim ``1536`` — a mismatch fails BEFORE the provider
query.

Every external boundary is INJECTED (embed fn, member-row fetcher, model attestor,
fixture-key→record-id resolver), so B0C-B tests drive this against a fake embedding
provider + fake DB with ZERO provider traffic and ZERO normal-DB access.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import (
    Awaitable,
    Callable,
    Dict,
    List,
    Mapping,
    Sequence,
    Tuple,
)

from open_notebook.integrations.graphrag.eval.authmintlivepn02d import (
    require_live_provider_run_authorization,
)
from open_notebook.integrations.graphrag.eval.provider_binding08 import (
    FROZEN_EMBEDDING_DIM,
    FROZEN_EMBEDDING_MODEL,
)
from open_notebook.integrations.graphrag.eval.routelivepn02d import NotebookRuntimeRoute
from open_notebook.integrations.graphrag.eval.vectorlivepn02d import (
    VectorBackend,
    VectorBackendError,
    VectorBackendFactory,
)

#: Frozen embedding provider identity for the active-model attestation (design §11/L-4).
FROZEN_EMBEDDING_PROVIDER = "openrouter"

#: Fixture-key -> real isolated ``source`` record id (from the corpus provisioner).
MemberIdResolver = Callable[[str], str]
#: Attest the active default embedding model/dim BEFORE any query embedding (L-4).
EmbeddingModelAttestor = Callable[[], Awaitable["ActiveEmbeddingModelAttestation"]]
#: One query embedding (real: ``generate_embedding``; a fake in tests).
QueryEmbedFn = Callable[[str], Awaitable[Sequence[float]]]
#: Member-scoped row fetch: (source_record_ids, query_embedding) -> [(record_id, embedding)].
#: Implements the ``WHERE source IN $ids`` restriction — it NEVER scans the global corpus.
MemberRowFetcher = Callable[
    [Sequence[str], Sequence[float]], Awaitable[Sequence[Tuple[str, Sequence[float]]]]
]


class ActiveEmbeddingModelMismatch(VectorBackendError):
    """The active embedding model/dim did not match the frozen benchmark model (L-4)."""


@dataclass(frozen=True)
class ActiveEmbeddingModelAttestation:
    """Observed active embedding model identity (design §11/L-4). No secret."""

    provider: str
    model: str
    dimension: int

    @property
    def matches_frozen(self) -> bool:
        return (
            self.provider == FROZEN_EMBEDDING_PROVIDER
            and self.model == FROZEN_EMBEDDING_MODEL
            and self.dimension == FROZEN_EMBEDDING_DIM
        )

    def failure_reasons(self) -> Tuple[str, ...]:
        reasons: List[str] = []
        if self.provider != FROZEN_EMBEDDING_PROVIDER:
            reasons.append("embedding_provider_mismatch")
        if self.model != FROZEN_EMBEDDING_MODEL:
            reasons.append("embedding_model_mismatch")
        if self.dimension != FROZEN_EMBEDDING_DIM:
            reasons.append("embedding_dimension_mismatch")
        return tuple(reasons)


def cosine_similarity(a: Sequence[float], b: Sequence[float]) -> float:
    """Cosine ``dot / (‖a‖‖b‖)`` in float64, matching ``vector::similarity::cosine``.

    Returns ``0.0`` for a zero-norm vector or a length mismatch (the DB filters length
    mismatches out with ``array::len`` — mirrored here defensively).
    """
    if len(a) != len(b):
        return 0.0
    dot = 0.0
    na = 0.0
    nb = 0.0
    for x, y in zip(a, b):
        fx = float(x)
        fy = float(y)
        dot += fx * fy
        na += fx * fx
        nb += fy * fy
    if na <= 0.0 or nb <= 0.0:
        return 0.0
    return dot / (math.sqrt(na) * math.sqrt(nb))


class RealPN02VectorBackend(VectorBackend):
    """Member-scoped ranked retrieval over ``source_embedding`` (Approach B, design §13).

    Restriction happens in the fetch (``WHERE source IN $ids``), so the backend never
    sees a non-member row — a global-top-K-then-post-filter is structurally impossible.
    ``embed_query`` attests the active model/dim (L-4) then produces ONE embedding;
    ``rank_members`` fetches member rows, computes cosine, takes ``max`` per source, and
    returns fixture keys ranked best-first (mapped back from record ids).
    """

    def __init__(
        self,
        *,
        member_id_resolver: MemberIdResolver,
        query_embed_fn: QueryEmbedFn,
        member_row_fetcher: MemberRowFetcher,
        model_attestor: EmbeddingModelAttestor,
    ) -> None:
        self._resolve_record_id = member_id_resolver
        self._embed = query_embed_fn
        self._fetch_member_rows = member_row_fetcher
        self._attest_model = model_attestor

    async def embed_query(self, question: str) -> Tuple[float, ...]:
        # L-4: attest the active embedding model/dim BEFORE the provider embedding.
        attestation = await self._attest_model()
        if not attestation.matches_frozen:
            raise ActiveEmbeddingModelMismatch(
                "active embedding model/dim mismatch: "
                f"{', '.join(attestation.failure_reasons()) or 'unknown'} "
                "(fail-closed BEFORE provider query, design §11/L-4)"
            )
        embedding = await self._embed(question)
        if not embedding or len(embedding) != FROZEN_EMBEDDING_DIM:
            raise ActiveEmbeddingModelMismatch(
                "query embedding dimension does not match the frozen corpus dimension "
                f"({FROZEN_EMBEDDING_DIM})"
            )
        return tuple(float(x) for x in embedding)

    async def rank_members(
        self,
        *,
        query_embedding: Sequence[float],
        candidate_source_ids: Sequence[str],
    ) -> Sequence[str]:
        # Resolve fixture member keys -> real isolated record ids (members only).
        key_by_record: Dict[str, str] = {}
        record_ids: List[str] = []
        for key in candidate_source_ids:
            record_id = self._resolve_record_id(key)
            key_by_record[record_id] = key
            record_ids.append(record_id)
        if not record_ids:
            return []

        # Member-scoped fetch (WHERE source IN $ids) — NO global scan, NO ranking here.
        rows = await self._fetch_member_rows(record_ids, query_embedding)

        # Genuine cosine, max per source (mirrors math::max group-by), members only.
        best_by_key: Dict[str, float] = {}
        for record_id, embedding in rows:
            mapped_key = key_by_record.get(str(record_id))
            if mapped_key is None:
                # Defense in depth: a fetcher must NEVER return a non-member row. Drop
                # it (never a post-filter that could rescue a global-top-K design).
                continue
            if len(embedding) != len(query_embedding):
                continue  # dimension mismatch — mirrors the DB array::len guard
            score = cosine_similarity(embedding, query_embedding)
            prior = best_by_key.get(mapped_key)
            if prior is None or score > prior:
                best_by_key[mapped_key] = score

        ranked = sorted(best_by_key.items(), key=lambda kv: kv[1], reverse=True)
        return [key for key, _score in ranked]


def build_real_vector_backend_factory(
    *,
    live_auth: object,
    member_id_resolver: MemberIdResolver,
    query_embed_fn: QueryEmbedFn,
    member_row_fetcher: MemberRowFetcher,
    model_attestor: EmbeddingModelAttestor,
) -> VectorBackendFactory:
    """Build the real vector-backend factory — REJECTS before auth (§12/§16).

    All external boundaries are injected. In a real run the live dependency builder
    wires ``member_row_fetcher`` to a member-scoped ``repo_query`` in the isolated
    namespace, ``query_embed_fn`` to ``generate_embedding``, ``model_attestor`` to a
    reader of the active default embedding model, and ``member_id_resolver`` to the
    corpus provisioner's key→record-id map. B0C-B tests inject fakes for all four.
    """
    require_live_provider_run_authorization(live_auth)
    backend = RealPN02VectorBackend(
        member_id_resolver=member_id_resolver,
        query_embed_fn=query_embed_fn,
        member_row_fetcher=member_row_fetcher,
        model_attestor=model_attestor,
    )

    def _factory(_route: NotebookRuntimeRoute) -> VectorBackend:
        # One shared corpus (Approach B); member scoping comes from the candidate ids.
        return backend

    return _factory


def build_repo_query_member_row_fetcher(
    repo_query: Callable[..., Awaitable[Sequence[Mapping[str, object]]]],
) -> MemberRowFetcher:
    """Build the REAL member-scoped row fetcher over an isolated-namespace ``repo_query``.

    Issues exactly the member-restricted, dimension-guarded SELECT (design §13) — never
    the global ``fn::vector_search``. Used only inside an active isolated namespace by
    the live dependency builder; B0C-B tests inject a fake fetcher instead.
    """
    query = (
        "SELECT source, embedding FROM source_embedding "
        "WHERE source IN $ids AND embedding != NONE "
        "AND array::len(embedding) = array::len($q)"
    )

    async def _fetch(
        source_record_ids: Sequence[str], query_embedding: Sequence[float]
    ) -> Sequence[Tuple[str, Sequence[float]]]:
        rows = await repo_query(
            query, {"ids": list(source_record_ids), "q": list(query_embedding)}
        )
        out: List[Tuple[str, Sequence[float]]] = []
        for row in rows or []:
            source = row.get("source")
            embedding = row.get("embedding")
            if source is None or not isinstance(embedding, (list, tuple)):
                continue
            out.append((str(source), [float(x) for x in embedding]))
        return out

    return _fetch


__all__ = [
    "FROZEN_EMBEDDING_PROVIDER",
    "MemberIdResolver",
    "EmbeddingModelAttestor",
    "QueryEmbedFn",
    "MemberRowFetcher",
    "ActiveEmbeddingModelMismatch",
    "ActiveEmbeddingModelAttestation",
    "cosine_similarity",
    "RealPN02VectorBackend",
    "build_real_vector_backend_factory",
    "build_repo_query_member_row_fetcher",
]
