"""
Pytest configuration file.

This file ensures that the project root is in the Python path,
allowing tests to import from the api and open_notebook modules.
"""

import os
import sys
from pathlib import Path

# Ensure password auth is disabled for tests BEFORE any imports
# The PasswordAuthMiddleware skips auth when this env var is not set
# Set to empty string instead of deleting to prevent it from being reloaded
os.environ["OPEN_NOTEBOOK_PASSWORD"] = ""

# Load environment variables from .env file
# This must be done BEFORE any imports that depend on environment variables
from dotenv import load_dotenv

# Load .env file from project root
dotenv_path = Path(__file__).parent.parent / ".env"
if dotenv_path.exists():
    load_dotenv(dotenv_path)
    print(f"Loaded environment variables from {dotenv_path}")
else:
    print(f"Warning: .env file not found at {dotenv_path}")

# Add the project root to the Python path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def _isolate_oneshot_ledger(tmp_path, monkeypatch):
    """PN02D-B3H: isolate the durable one-shot live-auth ledger per test WITHOUT any public env
    var (PN02DB3G-OR1-H1 removed the public override). This patches the INTERNAL resolver seam
    ``authledgerpn02d.default_ledger_path`` to a unique per-test temp file, so no test reads or
    writes the real production ledger (TEST_LEDGER_ISOLATED_FROM_PRODUCTION). The real production
    resolver ``_production_ledger_path`` is intentionally NOT patched, so a dedicated test can
    assert the true production path directly. monkeypatch auto-restores after each test (no leak).

    Tests that need explicit control (e.g. a replay across two driver runs) pass an explicit
    internal ``oneshot_ledger_path=`` / ``ledger_path=`` which takes precedence."""
    try:
        from open_notebook.integrations.graphrag.eval import authledgerpn02d as _ledger
    except Exception:
        return  # ledger module not importable in this environment -> nothing to isolate
    _isolated = str(tmp_path / "oneshot_ledger_isolated.sqlite")
    monkeypatch.setattr(_ledger, "default_ledger_path", lambda: _isolated)
