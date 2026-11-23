"""Shared fixtures.

The database override is set before any `app.*` import: app.db builds its engine
at import time, so it has to be in place first. conftest is the earliest module
pytest imports, which also keeps a test run off the developer's real database.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

_TMP_DIR = Path(tempfile.mkdtemp(prefix="campaign-hub-tests-"))
os.environ["CAMPAIGN_HUB_DATABASE_URL"] = f"sqlite:///{_TMP_DIR / 'test.db'}"

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.adapters import build_adapters  # noqa: E402
from app.config import DELIVERIES_DIR  # noqa: E402
from app.currency import load_rates  # noqa: E402
from app.db import init_db  # noqa: E402
from app.pipeline.ingest import build_run_context, ingest  # noqa: E402


@pytest.fixture(scope="session")
def adapters():
    return build_adapters(load_rates())


@pytest.fixture(scope="session")
def run_context(adapters):
    """Parsed view of the real deliveries, without touching storage.

    Session-scoped: parsing is the expensive part and nothing here mutates it.
    """
    run, _unrecognised = build_run_context(DELIVERIES_DIR, adapters)
    return run


@pytest.fixture(scope="session")
def ingested():
    """Database with one completed ingestion run."""
    init_db()
    return ingest()


@pytest.fixture()
def client(ingested):
    from app.main import app

    return TestClient(app, raise_server_exceptions=False)
