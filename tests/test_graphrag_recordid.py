"""Canonical SurrealDB RecordID semantics at the GraphRAG boundary.

Background: Codex flagged that `source_id` reached the sidecar unvalidated. The
first fix applied a regex to the presentation string, which then rejected
legitimate SurrealDB-escaped ids — `str(RecordID)` wraps an identifier in
U+27E8/U+27E9 when it is all-digit or contains characters outside
`[A-Za-z0-9_]`. `source:123` is a real fixture id (`tests/test_domain.py:283`)
whose canonical string form is `source:⟨123⟩`.

The validator is therefore structural: split table/identifier, unwrap one layer
of escaping, validate both parts, then re-serialize through the SDK. Escaping is
preserved on the wire — unescaping would discard SurrealDB's distinction between
a numeric id and a string id of the same digits.

All HTTP is mocked at the transport; no live sidecar, no provider calls.
"""

import json
from pathlib import Path

import httpx
import pytest
from surrealdb.data.types.record_id import RecordID

from open_notebook.integrations.graphrag.client import (
    GraphRAGClient,
    _looks_like_record_id,
)
from open_notebook.integrations.graphrag.config import GraphRAGConfig
from open_notebook.integrations.graphrag.models import GraphRAGValidationError
from open_notebook.integrations.graphrag.service import (
    _INDEXABLE_TABLES,
    GraphRAGService,
    validate_source_id,
)

BASE_URL = "http://graphrag-sidecar.invalid:9621"

ESCAPED_DIGITS = "source:\u27e80123456789\u27e9"
ESCAPED_HYPHEN = "source:\u27e8abc-def\u27e9"
ESCAPED_USCORE = "source:\u27e8_9\u27e9"

CANONICAL_IDS = [
    "source:abc123xyz",
    "source:k3l4m5n6p7q8r9s0t1u2",
    "source:abc_def",
    ESCAPED_DIGITS,
    ESCAPED_HYPHEN,
    ESCAPED_USCORE,
]


def _config(**overrides) -> GraphRAGConfig:
    defaults = dict(enabled=True, base_url=BASE_URL, timeout=5.0, api_key=None)
    defaults.update(overrides)
    return GraphRAGConfig(**defaults)  # type: ignore[arg-type]


def _client(handler) -> GraphRAGClient:
    return GraphRAGClient(_config(), transport=httpx.MockTransport(handler))


def _json_response(status_code: int, payload) -> httpx.Response:
    return httpx.Response(status_code, json=payload)


class TestCanonicalRecordIdSemantics:
    @pytest.mark.parametrize("canonical", CANONICAL_IDS)
    def test_validation_is_idempotent(self, canonical):
        once = validate_source_id(canonical)
        assert once == canonical
        assert validate_source_id(once) == once

    def test_escaping_is_preserved_not_stripped(self):
        out = validate_source_id(ESCAPED_DIGITS)
        assert out.startswith("source:\u27e8")
        assert out.endswith("\u27e9")
        assert out != "source:0123456789"

    def test_numeric_and_string_digit_ids_are_not_collapsed(self):
        """SurrealDB treats numeric 123 and string "123" as distinct records.

        Regression for a Codex HIGH finding: the validator unwrapped to a Python
        str and always rebuilt ``RecordID(table, str)``, so a numeric
        ``source:123`` came back as ``source:<123>`` \u2014 silently re-keying it onto
        the string identity. The earlier version of this test asserted only the
        SDK's behavior, never the validator's, so it missed the very property it
        claimed to protect. Both identities now go through validate_source_id().
        """
        numeric = str(RecordID("source", 123))
        string_digits = str(RecordID("source", "123"))
        assert numeric == "source:123"
        assert string_digits == "source:\u27e8123\u27e9"
        assert numeric != string_digits

        # THE point: each identity survives validation as itself.
        assert validate_source_id(numeric) == numeric
        assert validate_source_id(string_digits) == string_digits
        assert validate_source_id(numeric) != validate_source_id(string_digits)

    @pytest.mark.asyncio
    async def test_numeric_and_string_digit_ids_stay_distinct_on_the_wire(self):
        """The distinction must survive all the way into file_source."""
        seen = []

        def handler(request):
            seen.append(json.loads(request.content)["file_source"])
            return _json_response(
                200, {"status": "success", "message": "ok", "track_id": "t1"}
            )

        service = GraphRAGService(config=_config(), client=_client(handler))
        for ident in (str(RecordID("source", 123)), str(RecordID("source", "123"))):
            await service.index_synthetic_document(
                source_id=ident, canonical_text="synthetic"
            )
        assert seen == ["source:123", "source:\u27e8123\u27e9"]
        assert seen[0] != seen[1]

    def test_sdk_parse_would_double_escape_hence_not_used(self):
        """Guards against 'simplifying' the validator into a lossy round trip.

        RecordID.parse() treats an already-escaped presentation string as a
        literal identifier, so re-serializing escapes it a second time.
        """
        double = str(RecordID.parse(ESCAPED_DIGITS))
        assert double != ESCAPED_DIGITS
        assert double.count("\u27e8") == 2
        assert validate_source_id(ESCAPED_DIGITS) == ESCAPED_DIGITS

    @pytest.mark.parametrize(
        "malformed",
        [
            "source:\u27e8123",
            "source:123\u27e9",
            "source:12\u27e934",
            "source:\u27e8a\u27e9b",
            "source:\u27e8\u27e8a\u27e9\u27e9",
        ],
    )
    def test_malformed_escape_delimiters_rejected(self, malformed):
        with pytest.raises(GraphRAGValidationError):
            validate_source_id(malformed)

    @pytest.mark.parametrize(
        "hostile",
        [
            "source:\u27e8../../secret\u27e9",
            "source:\u27e8/uploads/private.pdf\u27e9",
            "source:\u27e8https://internal/doc?token=x\u27e9",
            "source:\u27e8a b\u27e9",
            "source:\u27e8a\nb\u27e9",
        ],
    )
    def test_escaping_cannot_smuggle_a_payload(self, hostile):
        """Validation applies to the UNESCAPED identifier.

        Otherwise wrapping a path in escape brackets would bypass the guard —
        which is why the brackets are handled structurally rather than added to a
        character class.
        """
        with pytest.raises(GraphRAGValidationError):
            validate_source_id(hostile)

    @pytest.mark.parametrize(
        "control_char", ["\n", "\r", "\t", "\x00", "\x1b", "\x7f"]
    )
    def test_control_characters_rejected(self, control_char):
        with pytest.raises(GraphRAGValidationError):
            validate_source_id(f"source:abc{control_char}def")

    @pytest.mark.parametrize(
        "hostile",
        [
            "/uploads/private.pdf",
            "C:\\private\\document.pdf",
            "../secret",
            "https://internal/doc",
            "https://internal/doc?token=secret",
            "source:https://internal",
            "source:../../secret",
            "source:a?b=c",
            "source:a#b",
            "source:a%2fb",
            "source:a&b",
            "sk-live-abcdef123456",
            "",
            "nocolon",
            "source:",
            ":abc",
        ],
    )
    def test_hostile_values_rejected(self, hostile):
        with pytest.raises(GraphRAGValidationError):
            validate_source_id(hostile)


    def test_redundant_escaping_is_canonicalized_not_rejected(self):
        """Escaping an identifier that does not need it yields the bare form.

        The SDK decides what requires escaping, so "source:<a>" canonicalizes to
        "source:a". Still idempotent, and still not a semantic change: SurrealDB
        treats both as the string id "a".
        """
        assert validate_source_id("source:⟨a⟩") == "source:a"
        assert validate_source_id("source:a") == "source:a"

    def test_empty_escaped_identifier_rejected(self):
        with pytest.raises(GraphRAGValidationError):
            validate_source_id("source:⟨⟩")

    def test_length_bound_applies_to_underlying_identifier(self):
        assert validate_source_id("source:" + "a" * 128)
        with pytest.raises(GraphRAGValidationError):
            validate_source_id("source:" + "a" * 129)

    @pytest.mark.parametrize(
        "hostile",
        [
            "source:\u27e8https://internal/doc?token=super-secret-value\u27e9",
            "https://internal/doc?token=super-secret-value",
        ],
    )
    def test_rejection_never_echoes_the_value(self, hostile):
        """The refused value may BE the secret; it must not reach logs."""
        with pytest.raises(GraphRAGValidationError) as excinfo:
            validate_source_id(hostile)
        message = str(excinfo.value)
        assert "super-secret-value" not in message
        assert "internal" not in message


class TestIndexableTableAllowlist:
    """Indexing accepts fewer tables than retrieval provenance may return.

    index_document() only ever sends Source.full_text, so the indexing boundary
    is restricted to `source`. note / source_insight remain valid PROVENANCE
    tables (fn::vector_search returns all three), which a later hybrid layer may
    surface without them becoming indexable here.
    """

    def test_source_is_indexable(self):
        assert validate_source_id("source:abc123") == "source:abc123"

    @pytest.mark.parametrize(
        "table", ["note", "source_insight", "notebook", "chat_session"]
    )
    def test_non_source_tables_are_not_indexable(self, table):
        with pytest.raises(GraphRAGValidationError):
            validate_source_id(f"{table}:abc123")

    def test_provenance_recognises_more_tables_than_indexing(self):
        """The asymmetry is deliberate, not an oversight."""
        assert _INDEXABLE_TABLES == {"source"}
        for provenance_only in ("note:abc", "source_insight:abc"):
            assert _looks_like_record_id(provenance_only) is True
            with pytest.raises(GraphRAGValidationError):
                validate_source_id(provenance_only)

    def test_escaped_provenance_ids_are_recognised(self):
        """A regex over the bare form would call these unresolved."""
        assert _looks_like_record_id(ESCAPED_DIGITS) is True
        assert _looks_like_record_id(ESCAPED_HYPHEN) is True


class TestProvenanceRoundTrip:
    """canonical id -> file_source -> GraphReference -> same canonical id."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("canonical", CANONICAL_IDS)
    async def test_no_lossy_normalization(self, canonical):
        sent = {}

        def index_handler(request):
            sent["body"] = json.loads(request.content)
            return _json_response(
                200, {"status": "success", "message": "ok", "track_id": "t1"}
            )

        service = GraphRAGService(config=_config(), client=_client(index_handler))
        await service.index_synthetic_document(
            source_id=canonical, canonical_text="synthetic"
        )
        # Outbound: canonical form, escaping intact.
        assert sent["body"]["file_source"] == canonical

        def query_handler(request):
            return _json_response(
                200,
                {
                    "response": "answer",
                    "references": [
                        {
                            "reference_id": "r1",
                            "file_path": sent["body"]["file_source"],
                        }
                    ],
                },
            )

        result = await _client(query_handler).query("q")
        # Inbound: recovered identically, and recognised as ours.
        assert result.references[0].source_id == canonical
        assert result.references[0].resolved is True

    @pytest.mark.asyncio
    async def test_invalid_source_id_makes_no_outbound_request(self):
        calls = []

        def handler(request):  # pragma: no cover - must not be reached
            calls.append(request)
            raise AssertionError("no request may be made for an invalid source_id")

        service = GraphRAGService(config=_config(), client=_client(handler))
        for hostile in (
            "/uploads/private.pdf",
            "source:../../secret",
            ESCAPED_DIGITS.replace("0123456789", "../x"),
            "note:abc",
        ):
            with pytest.raises(GraphRAGValidationError):
                await service.index_synthetic_document(
                    source_id=hostile, canonical_text="synthetic"
                )
        assert calls == []

    def test_lightrag_file_source_transformation_preserves_canonical_ids(self):
        """LightRAG runs file_source through Path(x).name before storing it.

        Verified against pinned v1.5.6: normalize_file_path ->
        canonicalize_parser_hinted_basename -> Path(file_path).name
        (lightrag/parser/routing.py:1090). A value containing a path separator
        would be TRUNCATED, so this asserts our canonical ids pass through
        untouched — and is the reason no extra transport encoding (e.g.
        base64url) is required.
        """
        for canonical in CANONICAL_IDS:
            assert Path(canonical).name == canonical
