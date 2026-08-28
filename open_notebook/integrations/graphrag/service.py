"""Facade Open Notebook calls for GraphRAG.

Enforces the feature flag, the metadata allowlist, and fail-open semantics
(AGR-005 §21.5). Callers use this, not GraphRAGClient directly, so the flag and
allowlist cannot be bypassed by accident.

Fail-open policy for this phase: INDEXING and QUERY may degrade. Deletion is
deliberately absent - durable deletion is GraphRAG-03 and must NOT fail open
(AGR-005 §9), so shipping a best-effort delete here would set the wrong
precedent.
"""

from typing import Any, Dict, Optional

from loguru import logger

from open_notebook.integrations.graphrag.client import GraphRAGClient
from open_notebook.integrations.graphrag.config import GraphRAGConfig, load_config
from open_notebook.integrations.graphrag.models import (
    _INDEXABLE_TABLES,
    ALLOWED_METADATA_FIELDS,
    GraphQueryResult,
    GraphRAGDisabledError,
    GraphRAGError,
    HealthResult,
    IndexAck,
    IndexStatus,
    QueryMode,
    _validate_record_id,
)


def validate_source_id(source_id: str) -> str:
    """Validate an Open Notebook record id and return its CANONICAL form.

    Structural, not regex-over-presentation:

        input -> split table/identifier -> unwrap escaping -> validate both
              -> re-serialize canonically -> canonical wire value

    Escaping is preserved in the output. ``source:123`` (numeric id) and
    ``source:⟨123⟩`` (string id "123") are distinct records in SurrealDB and must
    stay distinct here; normalizing either into the other to satisfy a validator
    would silently merge two different documents.

    Enforced at the service boundary rather than trusted from the caller, so a
    GraphRAG-03 ingestion implementer cannot hand a file path, signed URL, or
    token to the sidecar through this field.
    """
    return _validate_record_id(source_id, tables=_INDEXABLE_TABLES)


def build_sidecar_document(*, source_id: str, canonical_text: str) -> Dict[str, Any]:
    """Build the outbound sidecar payload from an explicit allowlist.

    Deliberately takes keyword scalars rather than a Source object: there is no
    object here to accidentally ``model_dump()``, which is exactly how
    asset.file_path / asset.url leaked into the rev-1 contract (AGR-005 §7).
    Adding a field requires editing this function AND ALLOWED_METADATA_FIELDS,
    which is the point.

    The signature carries only what actually crosses the wire. Upstream
    POST /documents/text accepts nothing but text / file_source / chunking, so
    title, content_hash, notebook_ids and contract_version have no transport at
    all in this phase; they stay in Open Notebook and are joined locally by
    source_id. They are deliberately NOT accepted here - a parameter that is
    built and then discarded reads as though it were transmitted, and would
    mislead whoever wires up GraphRAG-03. The full forward-looking contract
    lives in the architecture docs, not in a dead runtime signature.
    """
    document: Dict[str, Any] = {
        # Value-level guard, not just field-name: this is the only free-form
        # string that reaches the sidecar.
        "source_id": validate_source_id(source_id),
        "canonical_text": canonical_text,
    }

    leaked = set(document) - ALLOWED_METADATA_FIELDS
    if leaked:
        # Defensive: unreachable unless this function is edited without also
        # updating the allowlist. Raising beats silently exporting a new field.
        raise GraphRAGError(
            f"Refusing to build sidecar document with non-allowlisted field(s): "
            f"{sorted(leaked)}"
        )
    return document


class GraphRAGService:
    """Flag-gated facade over GraphRAGClient."""

    def __init__(
        self,
        config: Optional[GraphRAGConfig] = None,
        client: Optional[GraphRAGClient] = None,
    ) -> None:
        self._config = config or load_config()
        self._client = client

    @property
    def config(self) -> GraphRAGConfig:
        return self._config

    @property
    def enabled(self) -> bool:
        return self._config.configured

    def _require_client(self) -> GraphRAGClient:
        """Return the client, refusing when disabled or unconfigured.

        With the flag off, no client is constructed at all - the disabled path
        performs no network setup whatsoever.
        """
        if not self._config.enabled:
            raise GraphRAGDisabledError(
                "GraphRAG is disabled (set OPEN_NOTEBOOK_GRAPHRAG_ENABLED=true to enable)"
            )
        if not self._config.base_url:
            raise GraphRAGDisabledError(
                "GraphRAG is enabled but OPEN_NOTEBOOK_GRAPHRAG_BASE_URL is not set"
            )
        if self._client is None:
            self._client = GraphRAGClient(self._config)
        return self._client

    async def health(self) -> HealthResult:
        """Report sidecar health. Never raises (AGR-005 §21.8)."""
        if not self._config.enabled:
            return HealthResult(healthy=False, detail="GraphRAG is disabled")
        if not self._config.base_url:
            return HealthResult(
                healthy=False, detail="OPEN_NOTEBOOK_GRAPHRAG_BASE_URL is not set"
            )
        try:
            return await self._require_client().health()
        except GraphRAGError as e:
            return HealthResult(healthy=False, detail=str(e))

    async def index_synthetic_document(
        self,
        *,
        source_id: str,
        canonical_text: str,
    ) -> IndexAck:
        """Index one synthetic document through the PoC path.

        Named 'synthetic' on purpose: Boundary B (sidecar -> LLM/embedding
        provider) is NOT approved for real internal data, so this must only ever
        be handed synthetic, public, or anonymized content (AGR-005 §6, §21).

        Raises rather than failing open - a manual PoC call that silently did
        nothing would be actively misleading. The ingestion-path fail-open
        contract is a GraphRAG-03 concern and has no caller here.
        """
        document = build_sidecar_document(
            source_id=source_id, canonical_text=canonical_text
        )
        client = self._require_client()
        return await client.index_document(
            canonical_text=document["canonical_text"],
            source_id=document["source_id"],
        )

    async def track_status(self, track_id: str) -> IndexStatus:
        """Poll indexing progress. Raises on failure (diagnostic call)."""
        return await self._require_client().track_status(track_id)

    async def query_strict(
        self,
        question: str,
        *,
        mode: QueryMode = QueryMode.HYBRID,
        top_k: Optional[int] = None,
    ) -> GraphQueryResult:
        """Run a graph query, raising typed errors.

        For the diagnostic endpoint, whose whole purpose is telling an operator
        precisely what is broken. Collapsing 401/403 (bad key), 404/405 (version
        mismatch), 422 (bad input), 5xx, malformed JSON, and timeout into one
        "no result" answer would defeat that.

        Use query() instead for any retrieval path that must degrade rather than
        fail.
        """
        return await self._require_client().query(question, mode=mode, top_k=top_k)

    async def query(
        self,
        question: str,
        *,
        mode: QueryMode = QueryMode.HYBRID,
        top_k: Optional[int] = None,
    ) -> Optional[GraphQueryResult]:
        """Run a graph query, failing open to None.

        Returns None when GraphRAG is disabled or the sidecar is unusable, so a
        caller can fall back to vector-only retrieval without catching anything.
        This mirrors the degradation contract hybrid retrieval will need in
        GraphRAG-05 (AGR-005 §9). Results are DIAGNOSTIC and are not citable.

        Diagnostic callers should prefer query_strict(): this method deliberately
        discards the error taxonomy in exchange for never raising.
        """
        try:
            return await self.query_strict(question, mode=mode, top_k=top_k)
        except GraphRAGDisabledError:
            return None
        except GraphRAGError as e:
            logger.warning(f"GraphRAG query failed, degrading to no graph results: {e}")
            return None
