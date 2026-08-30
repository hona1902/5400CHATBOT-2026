"""Isolation and regression guards for the GraphRAG integration.

Covers cases 15-16 of docs/agribank/development/GRAPHRAG_DECISION.md §21.10 plus
the §21.9 prohibitions and §21.12 removability criteria.

The point of these tests is that GraphRAG-02 changed nothing that already
worked. They assert absence: no ingestion wiring, no retrieval changes, no
import-time dependency on the sidecar.
"""

import importlib
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

REPO_ROOT = Path(__file__).resolve().parents[1]


# ----------------------------------------- 15. imports/startup independence


class TestImportAndStartupIndependence:
    """Case 15: importing and booting must not need LightRAG at all."""

    def test_integration_imports_without_lightrag_installed(self):
        """No `import lightrag` anywhere - the sidecar is reached over HTTP."""
        assert "lightrag" not in sys.modules
        for name in ("config", "models", "client", "service"):
            importlib.import_module(f"open_notebook.integrations.graphrag.{name}")
        assert "lightrag" not in sys.modules

    def test_no_lightrag_package_import_in_source(self):
        """LightRAG is never vendored or imported (AGR-005 §21.11)."""
        offenders = []
        for path in (REPO_ROOT / "open_notebook").rglob("*.py"):
            text = path.read_text(encoding="utf-8", errors="ignore")
            for line in text.splitlines():
                stripped = line.strip()
                if stripped.startswith(("import lightrag", "from lightrag")):
                    offenders.append(f"{path}: {stripped}")
        for path in (REPO_ROOT / "api").rglob("*.py"):
            text = path.read_text(encoding="utf-8", errors="ignore")
            for line in text.splitlines():
                stripped = line.strip()
                if stripped.startswith(("import lightrag", "from lightrag")):
                    offenders.append(f"{path}: {stripped}")
        assert offenders == [], f"LightRAG must not be imported: {offenders}"

    def test_no_network_io_at_import_time(self):
        """Importing the package in a clean interpreter must not open a connection.

        Patches socket.create_connection / socket.socket.connect rather than
        removing socket.socket entirely: asyncio (imported transitively via
        loguru) needs the class to exist at import time, so nulling it would
        test stdlib import order instead of network access.
        """
        code = (
            "import socket\n"
            "def _boom(*a, **k):\n"
            "    raise AssertionError('network I/O at import time')\n"
            "socket.create_connection = _boom\n"
            "socket.socket.connect = _boom\n"
            "import open_notebook.integrations.graphrag as g\n"
            "assert g.load_config is not None\n"
            "print('ok')\n"
        )
        result = subprocess.run(
            [sys.executable, "-c", code],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=120,
        )
        assert result.returncode == 0, result.stderr
        assert "ok" in result.stdout

    def test_app_boots_with_graphrag_unset(self, monkeypatch):
        for key in (
            "OPEN_NOTEBOOK_GRAPHRAG_ENABLED",
            "OPEN_NOTEBOOK_GRAPHRAG_BASE_URL",
        ):
            monkeypatch.delenv(key, raising=False)
        from api.main import app

        assert app is not None

    def test_health_endpoint_reports_disabled_rather_than_404(self, monkeypatch):
        """Registered unconditionally so operators get a real answer."""
        monkeypatch.delenv("OPEN_NOTEBOOK_GRAPHRAG_ENABLED", raising=False)
        from api.main import app

        response = TestClient(app).get("/api/search/graph/health")
        assert response.status_code == 200
        body = response.json()
        assert body["enabled"] is False
        assert body["healthy"] is False

    def test_query_endpoint_degrades_when_disabled(self, monkeypatch):
        monkeypatch.delenv("OPEN_NOTEBOOK_GRAPHRAG_ENABLED", raising=False)
        from api.main import app

        response = TestClient(app).post(
            "/api/search/graph", json={"query": "anything"}
        )
        assert response.status_code == 503
        assert "vector search is unaffected" in response.json()["detail"]

    def test_integration_package_referrers_are_the_approved_set(self):
        """Removability: the integration package is referenced only from an
        explicit, approved set of call sites.

        GraphRAG-02 allowed exactly one (the diagnostic router). GraphRAG-03A
        adds the lifecycle command and the fail-open enqueue seam in
        graphs/source.py. GraphRAG-03C adds the deletion-drain wake-up: the
        FastAPI lifespan starts a cancellable periodic task, and Source.delete
        fires a best-effort wake-up. All lazy-import the integration. Any referrer
        outside this set is a scatter regression (AGR-005 §21.2) and must be
        justified before it lands."""
        approved = {
            "api/routers/graphrag.py",  # GraphRAG-02 diagnostic endpoint
            "commands/graphrag_commands.py",  # 03A lifecycle + 03C drain + 03D reconcile
            "open_notebook/graphs/source.py",  # GraphRAG-03A fail-open enqueue seam
            "api/main.py",  # GraphRAG-03C lifespan drain wake-up
            "open_notebook/domain/notebook.py",  # 03C best-effort drain wake-up on delete
        }
        referencing = set()
        for base in ("open_notebook", "api", "commands"):
            for path in (REPO_ROOT / base).rglob("*.py"):
                if "integrations/graphrag" in path.as_posix():
                    continue
                text = path.read_text(encoding="utf-8", errors="ignore")
                if "integrations.graphrag" in text:
                    referencing.add(path.relative_to(REPO_ROOT).as_posix())
        assert referencing == approved, (
            f"GraphRAG referrers changed; expected {approved}, got {referencing}"
        )


# --------------------------------------- 16. existing behavior not modified


class TestNoExistingPathModified:
    """Case 16 + §21.9: the prohibited call sites are untouched."""

    def test_vector_search_signature_unchanged(self):
        import inspect

        from open_notebook.domain.notebook import vector_search

        params = list(inspect.signature(vector_search).parameters)
        assert params == ["keyword", "results", "source", "note", "minimum_score"]

    def test_text_search_signature_unchanged(self):
        import inspect

        from open_notebook.domain.notebook import text_search

        assert list(inspect.signature(text_search).parameters)[:1] == ["keyword"]

    def test_ask_graph_still_uses_vector_search_only(self):
        """Ask must not have gained a GraphRAG path in this phase."""
        source = (REPO_ROOT / "open_notebook" / "graphs" / "ask.py").read_text(
            encoding="utf-8"
        )
        assert "vector_search" in source
        assert "graphrag" not in source.lower()

    @pytest.mark.parametrize(
        "relative",
        [
            # graphs/source.py is DELIBERATELY excluded: GraphRAG-03A adds the
            # approved fail-open enqueue seam there. domain/notebook.py is excluded
            # too: GraphRAG-03C adds the approved best-effort deletion-drain
            # wake-up in Source.delete (§27) — an optimisation only, covered by
            # its own no-direct-HTTP guard in test_graphrag_deletion.py. Every
            # OTHER ingestion and retrieval path must still be free of GraphRAG.
            "open_notebook/graphs/chat.py",
            "open_notebook/graphs/source_chat.py",
            "open_notebook/graphs/ask.py",
            "commands/source_commands.py",
            "commands/embedding_commands.py",
            "api/routers/search.py",
            "api/routers/sources.py",
        ],
    )
    def test_prohibited_files_have_no_graphrag_reference(self, relative):
        """No retrieval path, and no ingestion path OTHER than the approved
        save_source enqueue seam, may reference GraphRAG (AGR-005 §21.9;
        GraphRAG-03A scope keeps DELETE/reindex out of domain/commands here)."""
        text = (REPO_ROOT / relative).read_text(encoding="utf-8")
        assert "graphrag" not in text.lower(), (
            f"{relative} must not reference GraphRAG"
        )

    def test_source_graph_enqueue_seam_is_failopen_and_minimal(self):
        """The one allowed ingestion reference (graphs/source.py) must be the
        fail-open enqueue seam and nothing more: it submits by command name and
        must NOT import LightRAG or the GraphRAG client/service (only config,
        lazily). Property: a scatter of HTTP/client logic into the graph would
        break failure isolation and removability."""
        text = (REPO_ROOT / "open_notebook" / "graphs" / "source.py").read_text(
            encoding="utf-8"
        )
        assert "graphrag_index_source" in text  # the seam exists
        # No LightRAG client/service/http coupling in the graph module.
        assert "GraphRAGClient" not in text
        assert "GraphRAGService" not in text
        assert "import lightrag" not in text and "from lightrag" not in text

    def test_migration_count_matches_approved_phases(self):
        """Migration count is a scope guard. GraphRAG-02/03A added NO migration
        (count 46). GraphRAG-03B added migration 24 (durable tombstone + event),
        taking it to 48. GraphRAG-03C adds EXACTLY ONE more — number 25: the
        `next_attempt_at` fair-drain scheduling field plus the event OVERWRITE —
        taking the on-disk file count to 50 (25 up + 25 down). Any other delta
        means scope creep. Migrations keep numeric filenames, so no migration file
        is named for GraphRAG even though 24/25 are GraphRAG-owned."""
        migrations = sorted(
            p.name
            for p in (
                REPO_ROOT / "open_notebook" / "database" / "migrations"
            ).glob("*.surrealql")
        )
        assert len(migrations) == 50, (
            f"unexpected migration count {len(migrations)}: {migrations}"
        )
        assert not any("graphrag" in name for name in migrations)
        assert "24.surrealql" in migrations and "24_down.surrealql" in migrations
        assert "25.surrealql" in migrations and "25_down.surrealql" in migrations

    def test_only_approved_lifecycle_commands_registered(self):
        """The registered GraphRAG command set matches the approved slices: 03A
        index/reindex, 03C deletion drain, 03D reconcile, and 03E rebuild. Any
        standalone delete-source command must NOT exist — its appearance would mean
        scope creep past what is approved. Deletion is a DRAIN of durable tombstones,
        never a separate `graphrag_delete_source` command."""
        import commands

        registered = set(commands.__all__)
        assert "graphrag_index_source_command" in registered  # 03A
        assert "graphrag_drain_deletions_command" in registered  # 03C
        assert "graphrag_reconcile_command" in registered  # 03D
        assert "graphrag_rebuild_command" in registered  # 03E (approved)
        forbidden = {
            # Deletion is the durable tombstone DRAIN, never a standalone delete
            # command that could bypass the retention/confirmation lifecycle.
            "graphrag_delete_source_command",
        }
        assert forbidden & registered == set(), (
            f"unapproved GraphRAG lifecycle verbs must not be registered: "
            f"{forbidden & registered}"
        )
        # No standalone delete-source command declaration anywhere in commands/:
        # `graphrag_delete` is absent because deletion is the drain
        # (`graphrag_drain_deletions`), not a separate delete command. (03E
        # `graphrag_rebuild` is now approved and deliberately allowed.)
        for path in (REPO_ROOT / "commands").glob("*.py"):
            text = path.read_text(encoding="utf-8").lower()
            assert "graphrag_delete_source" not in text, (
                f"{path.name} must not define a standalone graphrag_delete_source command"
            )

    def test_existing_search_endpoint_untouched(self):
        """The production search route keeps its own contract.

        Checks for GraphRAG specifically, not the substring "graph": search.py
        legitimately imports ask_graph from open_notebook.graphs.ask, so a bare
        substring test would fail on pre-existing, unrelated code.
        """
        text = (REPO_ROOT / "api" / "routers" / "search.py").read_text(encoding="utf-8")
        assert "/search" in text
        lowered = text.lower()
        assert "graphrag" not in lowered
        assert "search/graph" not in lowered
        # The existing vector/text search calls are still the only retrieval.
        assert "vector_search" in text and "text_search" in text

    def test_frontend_untouched(self):
        """§21.9: no production frontend change."""
        frontend = REPO_ROOT / "frontend"
        if not frontend.exists():
            pytest.skip("frontend directory not present")
        hits = [
            p.relative_to(REPO_ROOT).as_posix()
            for p in frontend.rglob("*.ts*")
            if "node_modules" not in p.as_posix()
            and "graphrag" in p.read_text(encoding="utf-8", errors="ignore").lower()
        ]
        assert hits == [], f"frontend must not consume GraphRAG yet: {hits}"
