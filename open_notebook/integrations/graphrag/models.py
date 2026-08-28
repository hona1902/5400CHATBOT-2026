"""Internal types and errors for the LightRAG GraphRAG integration boundary.

Everything LightRAG-shaped stops here. Callers outside
``open_notebook/integrations/graphrag/`` see only these types and never
LightRAG's own field names, status strings, or httpx exceptions (AGR-005 §21.2).

Exceptions subclass OpenNotebookError to match open_notebook/exceptions.py.
"""

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional

from surrealdb.data.types.record_id import RecordID

from open_notebook.exceptions import OpenNotebookError

# Version of OUR outbound document contract (not LightRAG's API version).
METADATA_CONTRACT_VERSION = 2

# Fields Open Notebook is permitted to build a sidecar payload from (AGR-005
# §21.5). Note that upstream POST /documents/text accepts only text /
# file_source / chunking, so most of these never leave Open Notebook - they are
# joined locally by source_id. Kept explicit so a future upstream metadata field
# cannot be populated by accident.
ALLOWED_METADATA_FIELDS = frozenset(
    {
        "source_id",
        "title",
        "content_hash",
        "notebook_ids",
        "canonical_text",
        "contract_version",
    }
)

# Never sent to the sidecar under any circumstance. Enforced by an explicit
# allowlist build (service.py), with this set as a belt-and-braces assertion in
# tests: a payload containing any of these is a security defect, not a bug.
FORBIDDEN_METADATA_FIELDS = frozenset(
    {
        "asset",
        "file_path",
        "url",
        "api_key",
        "token",
        "credentials",
        "password",
        "secret",
        "embedding",
        "full_text",
    }
)


class GraphRAGError(OpenNotebookError):
    """Base class for GraphRAG integration failures."""


class GraphRAGDisabledError(GraphRAGError):
    """Raised when GraphRAG is called while disabled or unconfigured."""


class GraphRAGUnavailableError(GraphRAGError):
    """Sidecar unreachable: connection refused, DNS failure, or timeout."""


class GraphRAGRequestError(GraphRAGError):
    """Sidecar rejected the request itself (e.g. HTTP 422 schema validation).

    Genuine caller-input errors only. Auth failures and missing endpoints are
    GraphRAGConfigurationError - they are our misconfiguration, not bad input.
    """


class GraphRAGValidationError(GraphRAGError):
    """Open Notebook refused to send the value - it never left the process.

    Distinct from GraphRAGRequestError, which means the SIDECAR rejected a
    request we did send. This one is caller input we declined to transmit (e.g. a
    source_id that is not an indexable record id), so it maps to a 4xx and
    guarantees zero outbound egress.

    Instances must never embed the offending value: it may be the very path,
    URL, or token being refused, and the message reaches logs.
    """


class GraphRAGConflictError(GraphRAGError):
    """Sidecar rejected an insert because a document for this file_source
    still exists (HTTP 409).

    Distinct from GraphRAGRequestError (422, a permanent schema rejection):
    a 409 during REINDEX is TRANSIENT. LightRAG deletion is asynchronous
    (`deletion_started` != deleted), so an insert issued right after a delete
    can still see the old document and refuse it as a filename duplicate
    (lightrag/pipeline.py:1121-1170, verified v1.5.6). The correct response is
    to retry, not to fail permanently - hence its own type.
    """


class GraphRAGConfigurationError(GraphRAGError):
    """Sidecar is reachable but we are talking to it wrongly.

    Covers HTTP 401/403 (missing or wrong X-API-Key) and 404/405 (the path or
    method this client was written against is absent, i.e. a version mismatch).
    Reporting these as caller errors would blame whoever called the diagnostic
    endpoint for a deployment problem on our side.
    """


class GraphRAGServerError(GraphRAGError):
    """Sidecar failed internally (HTTP 5xx)."""


class GraphRAGProtocolError(GraphRAGError):
    """Response was unparseable or did not match the expected schema.

    Covers malformed JSON and a structurally valid but unexpected payload -
    both mean the sidecar cannot be trusted for this call, e.g. after an
    unannounced upstream version change.
    """


# SurrealDB escape delimiters (U+27E8/U+27E9). str(RecordID) wraps an identifier
# in these when it contains anything outside [A-Za-z0-9_] or has no alphabetic
# character at all (surrealdb/data/types/record_id.py::_escape_identifier). So
# "source:<20-char-alnum>" and "source:⟨123⟩" are BOTH canonical presentations -
# a regex over the presentation string cannot tell a legitimate escaped id from
# an injected one.
_ESCAPE_OPEN = "\u27e8"
_ESCAPE_CLOSE = "\u27e9"

# Tables whose records may be INDEXED as documents. Deliberately narrower than
# the set of tables that may later appear as retrieval provenance: see the
# module docstring note in models.py and GRAPHRAG_POC.md §4.
_INDEXABLE_TABLES = frozenset({"source"})

# The underlying identifier alphabet. Applied to the UNESCAPED identifier, so it
# bounds the real value rather than its presentation form. Excludes / \ : ? & = #
# % whitespace, control characters, and the escape delimiters themselves.
_IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,128}$")


def _reject(reason: str) -> GraphRAGValidationError:
    """Build a rejection error that never echoes the offending value.

    The rejected input may itself be the path, URL, or token we are refusing to
    transmit, and this message reaches logs.
    """
    return GraphRAGValidationError(
        f"Refusing to send an invalid source_id to the GraphRAG sidecar: {reason}. "
        f"Expected a canonical Open Notebook record id (table:identifier)."
    )


def _split_record_id(source_id: str) -> tuple[str, str]:
    """Split "table:identifier" structurally, returning the RAW identifier part.

    Deliberately does NOT use surrealdb.RecordID.parse():
      - parse() splits on ':' with str.split(':'), so it raises on any value
        containing a second colon (e.g. "source:https://x") rather than
        returning something we can inspect and reject with a clear reason;
      - more importantly, parse() treats an already-escaped presentation string
        as a LITERAL identifier, so re-serializing double-escapes it:
        "source:⟨123⟩" -> RecordID(id="⟨123⟩") -> "source:⟨⟨123\\⟩⟩".
        Round-tripping through the SDK is therefore lossy for exactly the
        escaped ids this validator exists to accept.
    """
    table, separator, identifier = source_id.partition(":")
    if not separator:
        raise _reject("value is not in 'table:identifier' form")
    return table, identifier


def _unwrap_identifier(identifier: str) -> str:
    """Return the underlying identifier, removing one layer of SurrealDB escaping.

    Used for VALIDATION ONLY. The canonical presentation (with escaping intact)
    is what gets transmitted - unwrapping to build the wire value would discard
    SurrealDB's semantic distinction between a numeric id and a string id that
    happens to contain only digits.
    """
    if identifier.startswith(_ESCAPE_OPEN) and identifier.endswith(_ESCAPE_CLOSE):
        # len >= 2 is implied: both delimiters matched and they are distinct
        # characters. An empty inner value ("<>") unwraps to "" and is then
        # rejected by the identifier pattern, which requires at least one char.
        inner = identifier[1:-1]
        if _ESCAPE_OPEN in inner or _ESCAPE_CLOSE in inner:
            # Nested or doubled escaping, e.g. the output of round-tripping an
            # already-escaped string through RecordID.parse().
            raise _reject("nested SurrealDB escape delimiters")
        return inner
    # A delimiter anywhere other than as a balanced wrapper is malformed - either
    # a truncated presentation string or an injection attempt.
    if _ESCAPE_OPEN in identifier or _ESCAPE_CLOSE in identifier:
        raise _reject("unbalanced or embedded SurrealDB escape delimiters")
    return identifier


def is_valid_record_id(value: str, *, tables: frozenset[str]) -> bool:
    """Whether ``value`` is structurally a canonical record id for ``tables``.

    Boolean sibling of validate_source_id(), sharing its structural logic so
    inbound provenance checking cannot drift from outbound validation. Used by the
    client for returned provenance, where the permitted table set is wider than
    the indexable one.
    """
    try:
        _validate_record_id(value, tables=tables)
    except GraphRAGValidationError:
        return False
    return True


def _build_record_id(value: str, *, tables: frozenset[str]) -> RecordID:
    """Shared structural validation. Returns the validated RecordID OBJECT.

    Building the object (rather than re-parsing the canonical string) is the
    only lossless path: ``RecordID.parse()`` double-escapes an already-escaped
    presentation string (e.g. ``source:⟨123⟩`` -> ``source:⟨⟨123\\⟩⟩``), so any
    downstream ``ensure_record_id(canonical_str)`` would bind the WRONG record.
    Callers that need to query/load by this id must use this object, not
    re-parse the string form.
    """
    if not isinstance(value, str) or not value:
        raise _reject("value is empty or not a string")

    table, raw_identifier = _split_record_id(value)

    if table not in tables:
        raise _reject("table is not permitted here")

    was_escaped = raw_identifier.startswith(_ESCAPE_OPEN)
    identifier = _unwrap_identifier(raw_identifier)

    if not _IDENTIFIER_PATTERN.match(identifier):
        raise _reject("identifier contains disallowed characters or is too long")

    if not was_escaped and identifier.isdigit():
        return RecordID(table, int(identifier))
    return RecordID(table, identifier)


def _validate_record_id(value: str, *, tables: frozenset[str]) -> str:
    """Shared structural validation. Returns the canonical serialized form."""
    return str(_build_record_id(value, tables=tables))


def record_id_for(value: str, *, tables: frozenset[str]) -> RecordID:
    """Public: validate ``value`` and return a losslessly-built RecordID object.

    Use this instead of ``ensure_record_id(validate_source_id(x))`` — the latter
    re-parses the canonical string and double-escapes escaped identifiers,
    silently binding the wrong record (numeric vs string-numeric identity bug).
    """
    return _build_record_id(value, tables=tables)


class QueryMode(str, Enum):
    """LightRAG retrieval modes (lightrag/api/routers/query_routes.py:36)."""

    LOCAL = "local"
    GLOBAL = "global"
    HYBRID = "hybrid"
    NAIVE = "naive"
    MIX = "mix"
    BYPASS = "bypass"


class IndexState(str, Enum):
    """Normalized indexing state.

    Collapses LightRAG's 7-state DocStatus pipeline (lightrag/base.py:888:
    pending/parsing/analyzing/processing/preprocessed/processed/failed) into the
    three outcomes a caller can act on. Unknown upstream states normalize to
    IN_PROGRESS rather than raising, so a new intermediate state added upstream
    degrades to "keep waiting" instead of breaking the PoC.
    """

    IN_PROGRESS = "in_progress"
    PROCESSED = "processed"
    FAILED = "failed"


_TERMINAL_UPSTREAM_STATES = {
    "processed": IndexState.PROCESSED,
    "failed": IndexState.FAILED,
}


def normalize_index_state(raw: str) -> IndexState:
    """Map a LightRAG DocStatus string onto IndexState."""
    return _TERMINAL_UPSTREAM_STATES.get(
        (raw or "").strip().lower(), IndexState.IN_PROGRESS
    )


@dataclass(frozen=True)
class HealthResult:
    """Outcome of a sidecar liveness probe.

    ``healthy`` is a plain bool so callers never have to catch an exception just
    to render a status badge. ``detail`` is a short human-readable string safe
    to surface in a diagnostic response - it must never contain the API key.
    """

    healthy: bool
    detail: str
    version: Optional[str] = None


class DeleteState(str, Enum):
    """Normalized outcome of a sidecar document-delete request.

    Maps LightRAG's DeleteDocByIdResponse.status
    (lightrag/api/routers/document_routes.py:6111-6118, verified against
    v1.5.6) onto the three outcomes a lifecycle caller can act on:

    - GONE: the document is (now) absent. Covers upstream ``deletion_started``
      (background delete scheduled) *and* ``not_found`` (already absent). Both
      mean "there is nothing left for us to keep" from the caller's side, so
      both are idempotent success for a delete-then-insert.
    - BUSY: upstream refused because the pipeline is busy/scanning/enqueuing
      (``status="busy"``). NOT success - the copy still exists; retry later.
    - REFUSED: upstream ``not_allowed`` - a policy/state refusal that will not
      succeed on blind retry; surfaced distinctly so callers do not treat it as
      progress.
    """

    GONE = "gone"
    BUSY = "busy"
    REFUSED = "refused"


@dataclass(frozen=True)
class DeleteOutcome:
    """Normalized result of requesting deletion of one sidecar document."""

    doc_id: str
    state: DeleteState
    detail: str


@dataclass(frozen=True)
class IndexAck:
    """Acknowledgement that a document was accepted for indexing.

    LightRAG's insert is asynchronous: acceptance is not completion. Callers
    must poll track_status() before assuming the document is queryable.
    """

    track_id: str
    accepted: bool
    detail: str


@dataclass(frozen=True)
class IndexStatus:
    """Normalized indexing progress for a track_id."""

    track_id: str
    state: IndexState
    total_count: int = 0
    state_counts: Dict[str, int] = field(default_factory=dict)


@dataclass(frozen=True)
class GraphReference:
    """One piece of supporting evidence returned by a graph query.

    ``source_id`` is recovered from LightRAG's ``ReferenceItem.file_path``,
    which for documents Open Notebook indexed carries the source_id we supplied
    in ``file_source`` - NOT a filesystem path. The mapping happens in client.py
    so no caller ever sees the misleading upstream field name.

    ``resolved`` is False when the value does not look like an Open Notebook
    record id (e.g. a document indexed directly into the sidecar by someone
    else). Unresolved references are diagnostic-only and are never citable.
    """

    source_id: Optional[str]
    reference_id: Optional[str]
    resolved: bool
    excerpts: List[str] = field(default_factory=list)


@dataclass(frozen=True)
class GraphQueryResult:
    """Normalized result of an experimental graph query.

    DIAGNOSTIC ONLY. This is deliberately NOT the Open Notebook citation
    contract (AGR-005 §13): ``answer`` is sidecar-generated prose and
    ``references`` are unvalidated against live records. Nothing here may be
    fed into an Ask/Chat prompt or presented as a citation before the strict
    row contract lands in GraphRAG-06.
    """

    answer: str
    references: List[GraphReference] = field(default_factory=list)
    mode: str = QueryMode.HYBRID.value
    elapsed_seconds: Optional[float] = None
    raw: Optional[Dict[str, Any]] = None
