"""Tests for the experimental LightRAG GraphRAG integration boundary.

Covers the 16 required cases in docs/agribank/development/GRAPHRAG_DECISION.md
§21.10. All HTTP is mocked at the transport boundary with httpx.MockTransport
(no new test dependency, no live sidecar, and no real provider call ever).

The security-relevant tests are the allowlist ones: they assert that
asset.file_path, asset.url, and secret-like fields cannot reach the sidecar even
when a caller passes them in. Those are the regressions that would constitute a
data-egress defect rather than a functional bug.
"""

import json

import httpx
import pytest

from open_notebook.integrations.graphrag.client import GraphRAGClient
from open_notebook.integrations.graphrag.config import (
    DEFAULT_TIMEOUT_SECONDS,
    GraphRAGConfig,
    load_config,
)
from open_notebook.integrations.graphrag.models import (
    ALLOWED_METADATA_FIELDS,
    FORBIDDEN_METADATA_FIELDS,
    GraphRAGConfigurationError,
    GraphRAGDisabledError,
    GraphRAGError,
    GraphRAGProtocolError,
    GraphRAGRequestError,
    GraphRAGServerError,
    GraphRAGUnavailableError,
    IndexState,
    QueryMode,
    normalize_index_state,
)
from open_notebook.integrations.graphrag.service import (
    GraphRAGService,
    build_sidecar_document,
)

BASE_URL = "http://graphrag-sidecar.invalid:9621"


def _config(**overrides) -> GraphRAGConfig:
    defaults = dict(enabled=True, base_url=BASE_URL, timeout=5.0, api_key=None)
    defaults.update(overrides)
    return GraphRAGConfig(**defaults)  # type: ignore[arg-type]


def _client(handler, **config_overrides) -> GraphRAGClient:
    return GraphRAGClient(
        _config(**config_overrides), transport=httpx.MockTransport(handler)
    )


def _json_response(status_code: int, payload) -> httpx.Response:
    return httpx.Response(status_code, json=payload)


# ---------------------------------------------------------------- 1. disabled


class TestFeatureDisabled:
    """Case 1: with the flag off, nothing happens and no client is built."""

    def test_config_defaults_to_disabled(self, monkeypatch):
        for key in (
            "OPEN_NOTEBOOK_GRAPHRAG_ENABLED",
            "OPEN_NOTEBOOK_GRAPHRAG_BASE_URL",
            "OPEN_NOTEBOOK_GRAPHRAG_TIMEOUT",
            "OPEN_NOTEBOOK_GRAPHRAG_API_KEY",
        ):
            monkeypatch.delenv(key, raising=False)
        config = load_config()
        assert config.enabled is False
        assert config.configured is False
        assert config.timeout == DEFAULT_TIMEOUT_SECONDS

    @pytest.mark.parametrize("value", ["true", "TRUE", "1", "yes"])
    def test_flag_accepts_truthy_spellings(self, monkeypatch, value):
        monkeypatch.setenv("OPEN_NOTEBOOK_GRAPHRAG_ENABLED", value)
        monkeypatch.setenv("OPEN_NOTEBOOK_GRAPHRAG_BASE_URL", BASE_URL)
        assert load_config().enabled is True

    @pytest.mark.parametrize("value", ["false", "0", "no", "", "maybe"])
    def test_flag_rejects_everything_else(self, monkeypatch, value):
        monkeypatch.setenv("OPEN_NOTEBOOK_GRAPHRAG_ENABLED", value)
        assert load_config().enabled is False

    def test_malformed_timeout_falls_back_instead_of_raising(self, monkeypatch):
        monkeypatch.setenv("OPEN_NOTEBOOK_GRAPHRAG_TIMEOUT", "not-a-number")
        assert load_config().timeout == DEFAULT_TIMEOUT_SECONDS

    @pytest.mark.parametrize("value", ["0", "-5"])
    def test_nonpositive_timeout_falls_back(self, monkeypatch, value):
        monkeypatch.setenv("OPEN_NOTEBOOK_GRAPHRAG_TIMEOUT", value)
        assert load_config().timeout == DEFAULT_TIMEOUT_SECONDS

    @pytest.mark.asyncio
    async def test_disabled_health_reports_without_network(self):
        def handler(request):  # pragma: no cover - must never be called
            raise AssertionError("disabled service must not make a request")

        service = GraphRAGService(
            config=_config(enabled=False),
            client=GraphRAGClient(
                _config(enabled=False), transport=httpx.MockTransport(handler)
            ),
        )
        result = await service.health()
        assert result.healthy is False
        assert "disabled" in result.detail.lower()

    @pytest.mark.asyncio
    async def test_disabled_query_fails_open_to_none(self):
        service = GraphRAGService(config=_config(enabled=False))
        assert await service.query("anything") is None

    @pytest.mark.asyncio
    async def test_disabled_index_raises_rather_than_silently_skipping(self):
        service = GraphRAGService(config=_config(enabled=False))
        with pytest.raises(GraphRAGDisabledError):
            await service.index_synthetic_document(
                source_id="source:abc", canonical_text="synthetic"
            )

    @pytest.mark.asyncio
    async def test_enabled_without_base_url_is_treated_as_unconfigured(self):
        service = GraphRAGService(config=_config(base_url=""))
        assert service.enabled is False
        assert await service.query("anything") is None
        result = await service.health()
        assert result.healthy is False
        assert "BASE_URL" in result.detail


# ------------------------------------------------------------ 2. healthy path


class TestHealth:
    @pytest.mark.asyncio
    async def test_healthy_sidecar(self):
        def handler(request):
            assert request.url.path == "/health"
            return _json_response(
                200, {"status": "healthy", "core_version": "1.5.6"}
            )

        result = await _client(handler).health()
        assert result.healthy is True
        assert result.version == "1.5.6"

    @pytest.mark.asyncio
    async def test_non_healthy_status_is_not_assumed_healthy(self):
        def handler(request):
            return _json_response(200, {"status": "degraded"})

        result = await _client(handler).health()
        assert result.healthy is False

    @pytest.mark.asyncio
    async def test_api_key_sent_when_configured(self):
        seen = {}

        def handler(request):
            seen["key"] = request.headers.get("X-API-Key")
            return _json_response(200, {"status": "healthy"})

        await _client(handler, api_key="synthetic-test-key").health()
        assert seen["key"] == "synthetic-test-key"

    @pytest.mark.asyncio
    async def test_no_api_key_header_when_unset(self):
        seen = {}

        def handler(request):
            seen["has_key"] = "X-API-Key" in request.headers
            return _json_response(200, {"status": "healthy"})

        await _client(handler).health()
        assert seen["has_key"] is False


# ---------------------------------------- 3-8. normalized transport failures


class TestFailureNormalization:
    """Cases 3-8: every failure mode becomes a typed internal error."""

    @pytest.mark.asyncio
    async def test_connection_refused(self):
        def handler(request):
            raise httpx.ConnectError("connection refused")

        with pytest.raises(GraphRAGUnavailableError):
            await _client(handler).query("q")

    @pytest.mark.asyncio
    async def test_timeout(self):
        def handler(request):
            raise httpx.ReadTimeout("timed out")

        with pytest.raises(GraphRAGUnavailableError):
            await _client(handler).query("q")

    @pytest.mark.asyncio
    @pytest.mark.parametrize("status", [400, 409, 422])
    async def test_http_4xx_caller_errors(self, status):
        """Request-level rejections. 401/403/404/405 are covered separately as
        configuration errors - see the dedicated tests below."""

        def handler(request):
            return _json_response(status, {"detail": "nope"})

        with pytest.raises(GraphRAGRequestError):
            await _client(handler).query("q")

    @pytest.mark.asyncio
    @pytest.mark.parametrize("status", [500, 502, 503])
    async def test_http_5xx(self, status):
        def handler(request):
            return _json_response(status, {"detail": "boom"})

        with pytest.raises(GraphRAGServerError):
            await _client(handler).query("q")

    @pytest.mark.asyncio
    async def test_malformed_json(self):
        def handler(request):
            return httpx.Response(200, content=b"not json at all{{{")

        with pytest.raises(GraphRAGProtocolError):
            await _client(handler).query("q")

    @pytest.mark.asyncio
    async def test_json_array_instead_of_object(self):
        def handler(request):
            return _json_response(200, [1, 2, 3])

        with pytest.raises(GraphRAGProtocolError):
            await _client(handler).query("q")

    @pytest.mark.asyncio
    async def test_unexpected_schema_on_query(self):
        """Case 8: valid JSON, wrong shape (e.g. upstream contract changed)."""

        def handler(request):
            return _json_response(200, {"unexpected": "shape"})

        with pytest.raises(GraphRAGProtocolError):
            await _client(handler).query("q")

    @pytest.mark.asyncio
    async def test_unexpected_schema_on_index(self):
        def handler(request):
            return _json_response(200, {"status": "success"})  # no track_id

        with pytest.raises(GraphRAGProtocolError):
            await _client(handler).index_document(
                canonical_text="synthetic", source_id="source:abc"
            )

    @pytest.mark.asyncio
    async def test_unexpected_schema_on_track_status(self):
        def handler(request):
            return _json_response(200, {"track_id": "t1"})  # no documents list

        with pytest.raises(GraphRAGProtocolError):
            await _client(handler).track_status("t1")

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "transport_error",
        [httpx.ConnectError("refused"), httpx.ReadTimeout("slow")],
    )
    async def test_no_httpx_exception_escapes_the_boundary(self, transport_error):
        """Transport failures surface as GraphRAGError, never as httpx types.

        Only paths that are meant to raise are exercised here. health() is
        excluded on purpose: it has intentionally non-raising semantics (it
        returns an unhealthy HealthResult instead), so asserting it raises would
        contradict its contract.
        """

        def handler(request):
            raise transport_error

        with pytest.raises(GraphRAGError):
            await _client(handler).query("q")

    @pytest.mark.asyncio
    async def test_credential_rejection_is_configuration_not_request_error(self):
        """401/403 is our misconfiguration, not the caller's bad input."""
        for status in (401, 403):

            def handler(request, _status=status):
                return _json_response(_status, {"detail": "unauthorized"})

            with pytest.raises(GraphRAGConfigurationError):
                await _client(handler).query("q")

    @pytest.mark.asyncio
    async def test_missing_endpoint_is_configuration_error(self):
        """404/405 means a version mismatch with the pinned contract."""
        for status in (404, 405):

            def handler(request, _status=status):
                return _json_response(_status, {"detail": "not found"})

            with pytest.raises(GraphRAGConfigurationError):
                await _client(handler).query("q")

    @pytest.mark.asyncio
    async def test_genuine_request_rejection_stays_a_request_error(self):
        """422 really is caller input - it must not be reclassified."""

        def handler(request):
            return _json_response(422, {"detail": "validation failed"})

        with pytest.raises(GraphRAGRequestError):
            await _client(handler).query("q")


# ------------------------------------------------- 9-10. index + query success


class TestIndexAndQuery:
    @pytest.mark.asyncio
    async def test_index_synthetic_document_success(self):
        captured = {}

        def handler(request):
            captured["path"] = request.url.path
            captured["body"] = json.loads(request.content)
            return _json_response(
                200,
                {
                    "status": "success",
                    "message": "queued",
                    "track_id": "insert_20260827_abc",
                },
            )

        ack = await _client(handler).index_document(
            canonical_text="Synthetic document about widgets.",
            source_id="source:synthetic1",
        )
        assert ack.accepted is True
        assert ack.track_id == "insert_20260827_abc"
        assert captured["path"] == "/documents/text"
        # source_id rides in file_source - the only join-key slot upstream has.
        assert captured["body"]["file_source"] == "source:synthetic1"
        assert captured["body"]["text"] == "Synthetic document about widgets."

    @pytest.mark.asyncio
    async def test_index_failure_status_is_reported_not_raised(self):
        def handler(request):
            return _json_response(
                200, {"status": "failure", "message": "bad", "track_id": "t9"}
            )

        ack = await _client(handler).index_document(
            canonical_text="synthetic", source_id="source:x"
        )
        assert ack.accepted is False

    @pytest.mark.asyncio
    async def test_query_success_maps_references(self):
        def handler(request):
            assert request.url.path == "/query"
            body = json.loads(request.content)
            assert body["mode"] == "hybrid"
            assert body["include_references"] is True
            return _json_response(
                200,
                {
                    "response": "Widgets are synthetic.",
                    "references": [
                        {
                            "reference_id": "r1",
                            # Upstream field name is file_path but it carries
                            # the source_id we supplied as file_source.
                            "file_path": "source:synthetic1",
                            "content": ["chunk one", "chunk two"],
                        }
                    ],
                    "response_time": 1.25,
                },
            )

        result = await _client(handler).query("what are widgets?")
        assert result.answer == "Widgets are synthetic."
        assert result.elapsed_seconds == 1.25
        assert len(result.references) == 1
        ref = result.references[0]
        assert ref.source_id == "source:synthetic1"
        assert ref.resolved is True
        assert ref.excerpts == ["chunk one", "chunk two"]

    @pytest.mark.asyncio
    async def test_query_marks_foreign_reference_unresolved(self):
        """A doc indexed outside Open Notebook must not look like a record id."""

        def handler(request):
            return _json_response(
                200,
                {
                    "response": "answer",
                    "references": [
                        {"reference_id": "r1", "file_path": "/var/data/other.pdf"}
                    ],
                },
            )

        result = await _client(handler).query("q")
        assert result.references[0].resolved is False

    @pytest.mark.asyncio
    async def test_query_top_k_forwarded_only_when_set(self):
        seen = {}

        def handler(request):
            seen["body"] = json.loads(request.content)
            return _json_response(200, {"response": "a"})

        await _client(handler).query("q")
        assert "top_k" not in seen["body"]
        await _client(handler).query("q", top_k=7)
        assert seen["body"]["top_k"] == 7

    @pytest.mark.asyncio
    @pytest.mark.parametrize("mode", list(QueryMode))
    async def test_all_upstream_modes_accepted(self, mode):
        def handler(request):
            assert json.loads(request.content)["mode"] == mode.value
            return _json_response(200, {"response": "a"})

        result = await _client(handler).query("q", mode=mode)
        assert result.mode == mode.value

    @pytest.mark.asyncio
    async def test_service_query_fails_open_on_sidecar_error(self):
        def handler(request):
            raise httpx.ConnectError("refused")

        service = GraphRAGService(config=_config(), client=_client(handler))
        assert await service.query("q") is None


class TestTrackStatus:
    """LightRAG's insert is async: acceptance is not completion."""

    @staticmethod
    def _status_handler(statuses):
        def handler(request):
            assert request.url.path.startswith("/documents/track_status/")
            return _json_response(
                200,
                {
                    "track_id": "t1",
                    "documents": [{"id": f"d{i}", "status": s} for i, s in enumerate(statuses)],
                    "total_count": len(statuses),
                    "status_summary": {s: statuses.count(s) for s in set(statuses)},
                },
            )

        return handler

    @pytest.mark.asyncio
    async def test_processed(self):
        status = await _client(self._status_handler(["processed"])).track_status("t1")
        assert status.state is IndexState.PROCESSED

    @pytest.mark.asyncio
    async def test_failed(self):
        status = await _client(self._status_handler(["failed"])).track_status("t1")
        assert status.state is IndexState.FAILED

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "upstream", ["pending", "parsing", "analyzing", "processing", "preprocessed"]
    )
    async def test_intermediate_states_are_in_progress(self, upstream):
        status = await _client(self._status_handler([upstream])).track_status("t1")
        assert status.state is IndexState.IN_PROGRESS

    @pytest.mark.asyncio
    async def test_pending_wins_over_failure(self):
        """Still working - do not report a terminal outcome yet."""
        status = await _client(
            self._status_handler(["failed", "processing"])
        ).track_status("t1")
        assert status.state is IndexState.IN_PROGRESS

    @pytest.mark.asyncio
    async def test_failure_surfaces_over_partial_success(self):
        status = await _client(
            self._status_handler(["processed", "failed"])
        ).track_status("t1")
        assert status.state is IndexState.FAILED

    @pytest.mark.asyncio
    async def test_empty_document_list_is_in_progress_not_failed(self):
        status = await _client(self._status_handler([])).track_status("t1")
        assert status.state is IndexState.IN_PROGRESS

    def test_unknown_upstream_state_degrades_to_in_progress(self):
        """A new upstream state must not break the PoC."""
        assert normalize_index_state("some_future_state") is IndexState.IN_PROGRESS
        assert normalize_index_state("") is IndexState.IN_PROGRESS


# ------------------------------------------------- 11-14. metadata allowlist


class TestMetadataAllowlist:
    """Cases 11-14. These are data-egress tests, not functional ones."""

    def test_only_wire_transmissible_fields_are_built(self):
        """The builder carries exactly what crosses the wire, nothing more.

        Upstream has no metadata field, so title / content_hash / notebook_ids /
        contract_version are deliberately not parameters: building and then
        discarding them would read as though they were transmitted.
        """
        doc = build_sidecar_document(
            source_id="source:abc", canonical_text="synthetic"
        )
        assert set(doc) == {"source_id", "canonical_text"}
        assert set(doc) <= ALLOWED_METADATA_FIELDS

    def test_builder_rejects_untransmittable_metadata(self):
        """Passing metadata upstream cannot carry must be a hard error."""
        for kwarg in ("title", "content_hash", "notebook_ids"):
            with pytest.raises(TypeError):
                build_sidecar_document(
                    source_id="source:abc",
                    canonical_text="s",
                    **{kwarg: "x"},  # type: ignore[arg-type]
                )

    def test_no_forbidden_field_in_document(self):
        doc = build_sidecar_document(
            source_id="source:abc", canonical_text="synthetic"
        )
        assert not (set(doc) & FORBIDDEN_METADATA_FIELDS)

    def test_builder_rejects_positional_source_object(self):
        """Keyword-only signature blocks passing a Source to be dumped."""
        with pytest.raises(TypeError):
            build_sidecar_document("source:abc", "text")  # type: ignore[misc,call-arg]

    @pytest.mark.asyncio
    async def test_file_path_never_sent(self):
        """Case 12: asset.file_path must not reach the sidecar."""
        captured = {}

        def handler(request):
            captured["body"] = json.loads(request.content)
            return _json_response(
                200, {"status": "success", "message": "ok", "track_id": "t1"}
            )

        service = GraphRAGService(config=_config(), client=_client(handler))
        await service.index_synthetic_document(
            source_id="source:abc", canonical_text="synthetic text only"
        )
        serialized = json.dumps(captured["body"])
        assert "file_path" not in serialized
        assert "/uploads/" not in serialized
        assert set(captured["body"]) == {"text", "file_source"}

    @pytest.mark.asyncio
    async def test_url_never_sent(self):
        """Case 13: original/signed URLs must not reach the sidecar."""
        captured = {}

        def handler(request):
            captured["body"] = json.loads(request.content)
            return _json_response(
                200, {"status": "success", "message": "ok", "track_id": "t1"}
            )

        service = GraphRAGService(config=_config(), client=_client(handler))
        await service.index_synthetic_document(
            source_id="source:abc", canonical_text="synthetic text only"
        )
        serialized = json.dumps(captured["body"]).lower()
        for marker in ("http://", "https://", "signature=", "token=", "asset"):
            assert marker not in serialized

    @pytest.mark.asyncio
    async def test_secret_like_extra_fields_cannot_be_dumped_through(self):
        """Case 14: an object with secrets cannot be smuggled into the payload.

        build_sidecar_document takes keyword scalars, so there is no object to
        model_dump(). This asserts that contract holds.
        """

        class SourceLike:
            def __init__(self):
                self.id = "source:abc"
                self.full_text = "synthetic"
                self.api_key = "sk-should-never-leave"  # noqa: S105 - synthetic
                self.asset = {"file_path": "/data/uploads/secret.pdf"}

            def model_dump(self):  # pragma: no cover - must never be called
                raise AssertionError("model_dump() must never be used for the sidecar")

        source_like = SourceLike()
        captured = {}

        def handler(request):
            captured["body"] = json.loads(request.content)
            return _json_response(
                200, {"status": "success", "message": "ok", "track_id": "t1"}
            )

        service = GraphRAGService(config=_config(), client=_client(handler))
        await service.index_synthetic_document(
            source_id=source_like.id, canonical_text=source_like.full_text
        )
        serialized = json.dumps(captured["body"])
        assert "sk-should-never-leave" not in serialized
        assert "secret.pdf" not in serialized

    @pytest.mark.parametrize(
        "bad_source_id",
        [
            "/uploads/private.pdf",
            "C:\\data\\uploads\\secret.pdf",
            "https://internal.example.com/doc?token=abc123",
            "http://host/path",
            "source:abc/../../etc/passwd",
            "sk-live-abcdef123456",
            "source:abc def",
            "notebook:abc",  # real record id, but not an indexable source
            "note:abc",  # valid provenance table, NOT indexable
            "source_insight:abc",  # valid provenance table, NOT indexable
            "",
            "source:",
            "arbitrary free text",
        ],
    )
    def test_source_id_value_is_bounded_not_just_field_name(self, bad_source_id):
        """A field-name allowlist does not bound egress on its own.

        source_id is the one free-form string that crosses to the sidecar (as
        LightRAG's file_source), so its VALUE must be constrained too - otherwise
        a path, signed URL, or token can be handed over through an allowlisted
        field name.
        """
        with pytest.raises(GraphRAGError):
            build_sidecar_document(
                source_id=bad_source_id, canonical_text="synthetic"
            )

    @pytest.mark.parametrize(
        "good_source_id",
        [
            "source:abc123",
            "source:k3l4m5n6p7q8r9s0t1u2",
            "source:abc_def",
            # SurrealDB-escaped presentations: str(RecordID) emits these for
            # all-digit identifiers and identifiers containing '-'.
            "source:⟨0123456789⟩",
            "source:⟨abc-def⟩",
            "source:⟨_9⟩",
        ],
    )
    def test_canonical_record_ids_accepted_and_idempotent(self, good_source_id):
        """Both bare and escaped canonical forms survive unchanged."""
        doc = build_sidecar_document(
            source_id=good_source_id, canonical_text="synthetic"
        )
        assert doc["source_id"] == good_source_id

    def test_rejection_message_does_not_echo_the_rejected_value(self):
        """The refused value may BE the secret; it must not reach logs."""
        secret = "https://internal/doc?token=super-secret-value"
        with pytest.raises(GraphRAGError) as excinfo:
            build_sidecar_document(source_id=secret, canonical_text="s")
        assert "super-secret-value" not in str(excinfo.value)

    @pytest.mark.asyncio
    async def test_bad_source_id_makes_no_outbound_request(self):
        """Validation must happen before any network call."""
        calls = []

        def handler(request):  # pragma: no cover - must not be reached
            calls.append(request)
            raise AssertionError("no request may be made for an invalid source_id")

        service = GraphRAGService(config=_config(), client=_client(handler))
        with pytest.raises(GraphRAGError):
            await service.index_synthetic_document(
                source_id="/uploads/private.pdf", canonical_text="synthetic"
            )
        assert calls == []

    def test_builder_raises_if_allowlist_desyncs(self, monkeypatch):
        """Guard against a future edit adding a field without the allowlist."""
        monkeypatch.setattr(
            "open_notebook.integrations.graphrag.service.ALLOWED_METADATA_FIELDS",
            frozenset({"source_id"}),
        )
        with pytest.raises(GraphRAGError):
            build_sidecar_document(source_id="source:abc", canonical_text="s")


# ------------------------------- endpoint-level error taxonomy preservation


class TestEndpointPreservesErrorTaxonomy:
    """The diagnostic endpoint must not flatten every failure into one status.

    Codex review finding: GraphRAGService.query() fails open to None, so routing
    the endpoint through it collapsed 422 / 401 / 404 / 5xx / timeout / malformed
    JSON into a single 503. The endpoint now uses query_strict() so an operator
    can tell a bad API key from a dead sidecar from their own bad input.
    """

    @staticmethod
    def _client_with(handler, monkeypatch):
        from fastapi.testclient import TestClient

        import api.routers.graphrag as graphrag_router
        from api.main import app

        monkeypatch.setenv("OPEN_NOTEBOOK_GRAPHRAG_ENABLED", "true")
        monkeypatch.setenv("OPEN_NOTEBOOK_GRAPHRAG_BASE_URL", BASE_URL)

        real_service = graphrag_router.GraphRAGService

        def _factory(*args, **kwargs):
            kwargs.setdefault("config", _config())
            kwargs.setdefault("client", _client(handler))
            return real_service(*args, **kwargs)

        monkeypatch.setattr(graphrag_router, "GraphRAGService", _factory)
        return TestClient(app)

    @pytest.mark.parametrize(
        "status,expected",
        [
            (422, 400),   # genuine caller input error
            (401, 502),   # our credentials are wrong -> not the caller's fault
            (403, 502),
            (404, 502),   # endpoint absent -> version mismatch
            (405, 502),
            (500, 502),   # sidecar broke
            (503, 502),
        ],
    )
    def test_upstream_status_maps_to_distinct_endpoint_status(
        self, status, expected, monkeypatch
    ):
        def handler(request):
            return _json_response(status, {"detail": "upstream"})

        response = self._client_with(handler, monkeypatch).post(
            "/api/search/graph", json={"query": "q"}
        )
        assert response.status_code == expected

    def test_timeout_maps_to_504(self, monkeypatch):
        def handler(request):
            raise httpx.ReadTimeout("slow")

        response = self._client_with(handler, monkeypatch).post(
            "/api/search/graph", json={"query": "q"}
        )
        assert response.status_code == 504

    def test_connection_refused_maps_to_504(self, monkeypatch):
        def handler(request):
            raise httpx.ConnectError("refused")

        response = self._client_with(handler, monkeypatch).post(
            "/api/search/graph", json={"query": "q"}
        )
        assert response.status_code == 504

    def test_malformed_json_maps_to_502(self, monkeypatch):
        def handler(request):
            return httpx.Response(200, content=b"not json{{{")

        response = self._client_with(handler, monkeypatch).post(
            "/api/search/graph", json={"query": "q"}
        )
        assert response.status_code == 502

    def test_unexpected_schema_maps_to_502(self, monkeypatch):
        def handler(request):
            return _json_response(200, {"wrong": "shape"})

        response = self._client_with(handler, monkeypatch).post(
            "/api/search/graph", json={"query": "q"}
        )
        assert response.status_code == 502

    def test_credential_failure_is_distinguishable_from_disabled(self, monkeypatch):
        """502 vs 503: an operator must be able to tell these apart."""

        def handler(request):
            return _json_response(401, {"detail": "unauthorized"})

        response = self._client_with(handler, monkeypatch).post(
            "/api/search/graph", json={"query": "q"}
        )
        assert response.status_code == 502
        assert "API_KEY" in response.json()["detail"]

    def test_success_still_returns_200_with_diagnostic_notice(self, monkeypatch):
        def handler(request):
            return _json_response(
                200,
                {
                    "response": "synthetic answer",
                    "references": [
                        {"reference_id": "r1", "file_path": "source:synthetic1"}
                    ],
                },
            )

        response = self._client_with(handler, monkeypatch).post(
            "/api/search/graph", json={"query": "q"}
        )
        assert response.status_code == 200
        body = response.json()
        assert body["references"][0]["source_id"] == "source:synthetic1"
        assert "not the Open Notebook citation contract" in body["notice"].lower() or \
            "citation" in body["notice"].lower()

    def test_invalid_source_id_rejected_at_api_boundary(self, monkeypatch):
        """Pattern on the request model rejects before any work happens."""

        def handler(request):  # pragma: no cover
            raise AssertionError("must not reach the sidecar")

        response = self._client_with(handler, monkeypatch).post(
            "/api/search/graph/index",
            json={
                "source_id": "https://internal/doc?token=abc",
                "canonical_text": "synthetic",
            },
        )
        assert response.status_code == 422
