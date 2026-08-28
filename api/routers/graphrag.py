"""Experimental GraphRAG diagnostic router.

POC / DIAGNOSTIC ONLY - see docs/agribank/development/GRAPHRAG_DECISION.md §21.6.

These endpoints exist to exercise the LightRAG integration boundary with
synthetic data. They are NOT part of any production path:

- They do not replace or alter POST /api/search.
- They do not touch vector_search(), text_search(), Ask, or Chat.
- Their response shape is graph-native and is explicitly NOT the Open Notebook
  citation contract (AGR-005 §13). Nothing returned here may be fed into a
  prompt or rendered as a citation: references are unvalidated against live
  records, and a reference the sidecar returns may name a source that no longer
  exists.
- No production frontend path consumes them.

Boundary B (sidecar -> LLM/embedding provider) is NOT approved for real internal
data, so the index endpoint accepts synthetic/public/anonymized content only.

Endpoints:
- GET  /api/search/graph/health  - sidecar liveness
- POST /api/search/graph/index   - index one synthetic document (manual PoC)
- GET  /api/search/graph/status/{track_id} - poll indexing progress
- POST /api/search/graph         - experimental graph query
"""

from typing import Dict, List, Optional

from fastapi import APIRouter, HTTPException
from loguru import logger
from pydantic import BaseModel, Field

from open_notebook.integrations.graphrag.config import (
    VERIFIED_LIGHTRAG_VERSION,
    load_config,
)
from open_notebook.integrations.graphrag.models import (
    GraphRAGConfigurationError,
    GraphRAGDisabledError,
    GraphRAGError,
    GraphRAGProtocolError,
    GraphRAGRequestError,
    GraphRAGServerError,
    GraphRAGUnavailableError,
    GraphRAGValidationError,
    QueryMode,
)
from open_notebook.integrations.graphrag.service import GraphRAGService

router = APIRouter(prefix="/search/graph", tags=["graphrag-experimental"])

# Repeated in every response so a caller cannot mistake diagnostic output for a
# supported retrieval contract.
_DIAGNOSTIC_NOTICE = (
    "EXPERIMENTAL diagnostic output. Not the Open Notebook citation contract: "
    "references are NOT validated against live records and must not be used as "
    "citations or inserted into prompts."
)


class GraphHealthResponse(BaseModel):
    enabled: bool = Field(description="Whether GraphRAG is enabled by feature flag")
    healthy: bool = Field(description="Whether the sidecar answered a liveness probe")
    detail: str = Field(description="Human-readable status detail")
    sidecar_version: Optional[str] = Field(
        None, description="Version reported by the sidecar, when available"
    )
    verified_against: str = Field(
        VERIFIED_LIGHTRAG_VERSION,
        description="LightRAG revision this integration was written against",
    )


class GraphIndexRequest(BaseModel):
    """Only the two fields that actually cross the wire.

    Upstream POST /documents/text accepts nothing but text / file_source /
    chunking, so title / content_hash / notebook_ids have no transport in this
    phase and are deliberately not accepted here rather than advertised and
    discarded. See docs/agribank/architecture/GRAPHRAG_POC.md §2.1.
    """

    source_id: str = Field(
        # Deliberately NOT a regex mirror of validate_source_id(): a pattern over
        # the presentation string cannot distinguish a legitimate SurrealDB-escaped
        # identifier (source:<123>) from an injected one, which is the bug this
        # replaced. Only bound the length here; the service layer performs the
        # structural table/identifier validation and is the security boundary.
        min_length=3,
        max_length=160,
        description=(
            "Open Notebook record id used as the join key, transmitted as "
            "LightRAG's file_source. Must be an indexable record id; paths, "
            "URLs, and free-form text are rejected by the service layer"
        ),
    )
    canonical_text: str = Field(
        min_length=1,
        description="SYNTHETIC/PUBLIC/ANONYMIZED text only. Real internal data is not permitted",
    )


class GraphIndexResponse(BaseModel):
    track_id: str = Field(description="Upstream tracking id for this insertion")
    accepted: bool = Field(description="Whether the sidecar accepted the document")
    detail: str = Field(description="Upstream message")
    notice: str = Field(
        _DIAGNOSTIC_NOTICE,
        description="Indexing is asynchronous: acceptance is not completion",
    )


class GraphStatusResponse(BaseModel):
    track_id: str
    state: str = Field(description="in_progress | processed | failed")
    total_count: int
    state_counts: Dict[str, int] = Field(default_factory=dict)


class GraphQueryRequest(BaseModel):
    query: str = Field(min_length=1, description="Question to ask the graph")
    mode: QueryMode = Field(
        QueryMode.HYBRID, description="LightRAG retrieval mode"
    )
    top_k: Optional[int] = Field(
        None, ge=1, le=1000, description="Optional retrieval breadth"
    )


class GraphReferenceModel(BaseModel):
    source_id: Optional[str] = Field(
        None,
        description=(
            "Recovered from the sidecar's file_path field, which carries the "
            "source_id we supplied. NOT a filesystem path"
        ),
    )
    reference_id: Optional[str] = None
    resolved: bool = Field(
        description=(
            "True when the value is shaped like an Open Notebook record id. "
            "Shape only - NOT proof the record exists and NOT authorization"
        )
    )
    excerpts: List[str] = Field(default_factory=list)


class GraphQueryResponse(BaseModel):
    answer: str = Field(description="Sidecar-generated prose. Ungrounded diagnostic output")
    references: List[GraphReferenceModel] = Field(default_factory=list)
    mode: str
    elapsed_seconds: Optional[float] = None
    notice: str = Field(_DIAGNOSTIC_NOTICE)


def _fail(error: GraphRAGError) -> HTTPException:
    """Map integration errors onto HTTP status codes.

    502/504 for sidecar-side problems so a caller can distinguish "the sidecar
    is broken" from "your request was wrong", and 503 when the feature is simply
    switched off.
    """
    if isinstance(error, GraphRAGDisabledError):
        return HTTPException(
            status_code=503,
            detail=(
                f"{error} Existing vector search is unaffected."
            ),
        )
    if isinstance(error, GraphRAGUnavailableError):
        return HTTPException(status_code=504, detail=str(error))
    # Sidecar-side problems, including our own misconfiguration (bad API key,
    # missing endpoint / version mismatch). These are emphatically NOT 400: the
    # caller of this diagnostic endpoint did nothing wrong, and telling them
    # otherwise hides the actual deployment fault.
    if isinstance(
        error,
        (GraphRAGServerError, GraphRAGProtocolError, GraphRAGConfigurationError),
    ):
        return HTTPException(status_code=502, detail=str(error))
    # Caller input we refused to transmit - nothing left the process.
    if isinstance(error, GraphRAGValidationError):
        return HTTPException(status_code=422, detail=str(error))
    # Genuine caller-input rejection by the sidecar (e.g. upstream 422).
    if isinstance(error, GraphRAGRequestError):
        return HTTPException(status_code=400, detail=str(error))
    return HTTPException(status_code=500, detail=str(error))


@router.get("/health", response_model=GraphHealthResponse)
async def graph_health():
    """Report GraphRAG flag state and sidecar liveness. Never raises."""
    config = load_config()
    result = await GraphRAGService(config=config).health()
    return GraphHealthResponse(
        enabled=config.enabled,
        healthy=result.healthy,
        detail=result.detail,
        sidecar_version=result.version,
    )


@router.post("/index", response_model=GraphIndexResponse)
async def graph_index(request: GraphIndexRequest):
    """Index one SYNTHETIC document through the PoC path (manual use only)."""
    try:
        ack = await GraphRAGService().index_synthetic_document(
            source_id=request.source_id,
            canonical_text=request.canonical_text,
        )
    except GraphRAGError as e:
        logger.warning(f"Experimental GraphRAG index failed: {e}")
        raise _fail(e)
    return GraphIndexResponse(
        track_id=ack.track_id, accepted=ack.accepted, detail=ack.detail
    )


@router.get("/status/{track_id}", response_model=GraphStatusResponse)
async def graph_status(track_id: str):
    """Poll indexing progress for a track_id."""
    try:
        status = await GraphRAGService().track_status(track_id)
    except GraphRAGError as e:
        logger.warning(f"Experimental GraphRAG status check failed: {e}")
        raise _fail(e)
    return GraphStatusResponse(
        track_id=status.track_id,
        state=status.state.value,
        total_count=status.total_count,
        state_counts=status.state_counts,
    )


@router.post("", response_model=GraphQueryResponse)
async def graph_query(request: GraphQueryRequest):
    """Run an experimental graph query.

    Returns 503 when GraphRAG is disabled or the sidecar is unusable: the
    service layer fails open to None, and this diagnostic endpoint reports that
    plainly rather than inventing an empty successful result.
    """
    # query_strict, not query: this endpoint exists to diagnose, so it must
    # preserve the error taxonomy (503 disabled / 504 timeout / 502 sidecar or
    # config / 400 caller input) instead of flattening everything into one
    # "no result" response. Fail-open degradation is query()'s job, for the
    # hybrid retrieval path that lands in GraphRAG-05.
    try:
        result = await GraphRAGService().query_strict(
            request.query, mode=request.mode, top_k=request.top_k
        )
    except GraphRAGError as e:
        logger.warning(f"Experimental GraphRAG query failed: {e}")
        raise _fail(e)

    references: List[GraphReferenceModel] = [
        GraphReferenceModel(
            source_id=ref.source_id,
            reference_id=ref.reference_id,
            resolved=ref.resolved,
            excerpts=ref.excerpts,
        )
        for ref in result.references
    ]
    return GraphQueryResponse(
        answer=result.answer,
        references=references,
        mode=result.mode,
        elapsed_seconds=result.elapsed_seconds,
    )
