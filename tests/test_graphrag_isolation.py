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

    def test_removing_integration_package_leaves_one_import_site(self):
        """§21.12 removability: only main.py and the router reference it."""
        referencing = set()
        for base in ("open_notebook", "api", "commands"):
            for path in (REPO_ROOT / base).rglob("*.py"):
                if "integrations/graphrag" in path.as_posix():
                    continue
                text = path.read_text(encoding="utf-8", errors="ignore")
                if "integrations.graphrag" in text:
                    referencing.add(path.relative_to(REPO_ROOT).as_posix())
        assert referencing == {"api/routers/graphrag.py"}, (
            f"GraphRAG must stay isolated; unexpected referrers: {referencing}"
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
            "open_notebook/graphs/source.py",
            "open_notebook/graphs/chat.py",
            "open_notebook/graphs/source_chat.py",
            "open_notebook/domain/notebook.py",
            "commands/source_commands.py",
            "commands/embedding_commands.py",
            "api/routers/search.py",
            "api/routers/sources.py",
        ],
    )
    def test_prohibited_files_have_no_graphrag_reference(self, relative):
        """§21.9: no ingestion or retrieval path may reference GraphRAG."""
        text = (REPO_ROOT / relative).read_text(encoding="utf-8")
        assert "graphrag" not in text.lower(), (
            f"{relative} must not reference GraphRAG in GraphRAG-02"
        )

    def test_no_new_migration_added(self):
        """§21.9: no DB migration in this phase."""
        migrations = sorted(
            p.name
            for p in (
                REPO_ROOT / "open_notebook" / "database" / "migrations"
            ).glob("*.surrealql")
        )
        # 23 up + 23 down. A new migration here would be out of approved scope.
        assert len(migrations) == 46, (
            f"unexpected migration count {len(migrations)}: {migrations}"
        )
        assert not any("graphrag" in name for name in migrations)

    def test_no_production_index_command_registered(self):
        """§21.9: no graphrag_index_source command may exist yet."""
        for path in (REPO_ROOT / "commands").glob("*.py"):
            text = path.read_text(encoding="utf-8").lower()
            assert "graphrag" not in text, f"{path.name} must not register GraphRAG work"

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
