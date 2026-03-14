"""
Pytest configuration and shared fixtures for ComicOPDS test suite.
"""
import os
import sqlite3
import tempfile
from pathlib import Path
from typing import Generator

import pytest
from fastapi.testclient import TestClient

# Override environment for testing
os.environ["CONTENT_BASE_DIR"] = "/tmp/test_library"
os.environ["DISABLE_AUTH"] = "false"
os.environ["OPDS_BASIC_USER"] = "testadmin"
os.environ["OPDS_BASIC_PASS"] = "testpass123"
os.environ["LOG_LEVEL"] = "ERROR"

from app.main import app
from app import db


@pytest.fixture(scope="session")
def test_library_dir():
    """Create temporary library directory for testing."""
    tmpdir = tempfile.mkdtemp(prefix="comicopds_test_")
    Path(tmpdir).mkdir(parents=True, exist_ok=True)
    yield tmpdir


@pytest.fixture(scope="session")
def test_data_dir():
    """Create temporary data directory for database and cache."""
    tmpdir = tempfile.mkdtemp(prefix="comicopds_data_")
    Path(tmpdir).mkdir(parents=True, exist_ok=True)
    yield tmpdir


@pytest.fixture
def test_db(test_data_dir):
    """Create fresh SQLite database for each test."""
    db_path = Path(test_data_dir) / "test.db"

    # Create database
    conn = sqlite3.connect(str(db_path))
    db._ensure_schema(conn)
    conn.close()

    yield str(db_path)

    # Cleanup - close any lingering connections
    import gc
    gc.collect()
    import time
    time.sleep(0.1)  # Brief wait for locks to release
    if db_path.exists():
        try:
            db_path.unlink()
        except PermissionError:
            pass  # File still locked, let OS clean up


@pytest.fixture
def client():
    """FastAPI TestClient for making requests."""
    return TestClient(app)


@pytest.fixture
def auth_headers():
    """HTTP Basic Auth headers for authenticated requests."""
    import base64
    credentials = base64.b64encode(b"testadmin:testpass123").decode()
    return {"Authorization": f"Basic {credentials}"}


@pytest.fixture
def opds1_headers():
    """Headers requesting OPDS 1.2 format."""
    return {"Accept": "application/atom+xml"}


@pytest.fixture
def opds2_headers():
    """Headers requesting OPDS 2.0 format."""
    return {"Accept": "application/opds+json"}
