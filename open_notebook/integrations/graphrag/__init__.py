"""Experimental LightRAG GraphRAG integration boundary.

DIAGNOSTIC / POC ONLY - see docs/agribank/development/GRAPHRAG_DECISION.md §21.
Disabled by default; not wired into source ingestion, Ask, or Chat.

Importing this package must never require the sidecar to exist or be reachable,
so nothing here performs I/O at import time.
"""

from open_notebook.integrations.graphrag.config import (
    VERIFIED_LIGHTRAG_VERSION,
    GraphRAGConfig,
    load_config,
)
from open_notebook.integrations.graphrag.models import (
    GraphQueryResult,
    GraphRAGConfigurationError,
    GraphRAGDisabledError,
    GraphRAGError,
    GraphRAGProtocolError,
    GraphRAGRequestError,
    GraphRAGServerError,
    GraphRAGUnavailableError,
    GraphRAGValidationError,
    GraphReference,
    HealthResult,
    IndexAck,
    IndexState,
    IndexStatus,
    QueryMode,
)
from open_notebook.integrations.graphrag.service import (
    GraphRAGService,
    build_sidecar_document,
)

__all__ = [
    "VERIFIED_LIGHTRAG_VERSION",
    "GraphQueryResult",
    "GraphRAGConfig",
    "GraphRAGConfigurationError",
    "GraphRAGDisabledError",
    "GraphRAGError",
    "GraphRAGProtocolError",
    "GraphRAGRequestError",
    "GraphRAGServerError",
    "GraphRAGService",
    "GraphRAGUnavailableError",
    "GraphRAGValidationError",
    "GraphReference",
    "HealthResult",
    "IndexAck",
    "IndexState",
    "IndexStatus",
    "QueryMode",
    "build_sidecar_document",
    "load_config",
]
