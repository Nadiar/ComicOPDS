# tests/test_opds_browse.py
import pytest
from fastapi.testclient import TestClient
import xml.etree.ElementTree as ET
import sqlite3
from pathlib import Path
from app.main import app


@pytest.mark.integration
@pytest.mark.opds1
class TestOPDS1Browse:
    """Tests for OPDS 1.2 feed generation."""

    @pytest.fixture
    def client_with_data(self, client, test_library_dir, test_db, monkeypatch):
        """Client with test data indexed."""
        from tests.fixtures.test_data import index_test_data
        from app import db

        # Create test database with indexed data
        conn = sqlite3.connect(test_db)
        conn.row_factory = sqlite3.Row
        db._ensure_schema(conn)
        index_test_data(conn, Path(test_library_dir))
        conn.close()

        # Patch db.connect() to use test database
        def mock_connect():
            test_conn = sqlite3.connect(test_db)
            test_conn.row_factory = sqlite3.Row
            db._ensure_schema(test_conn)
            return test_conn

        monkeypatch.setattr(db, "connect", mock_connect)
        monkeypatch.setattr(db, "DB_PATH", Path(test_db))

        return client

    def test_opds1_root_feed_returns_valid_atom_xml(self, client_with_data, auth_headers, opds1_headers):
        """OPDS 1.2 root feed returns valid Atom XML."""
        headers = {**auth_headers, **opds1_headers}
        response = client_with_data.get("/opds", headers=headers)

        assert response.status_code == 200
        assert "application/atom+xml" in response.headers["content-type"]

        # Parse as XML
        root = ET.fromstring(response.content)
        assert root.tag.endswith("feed")

    def test_opds1_feed_includes_required_elements(self, client_with_data, auth_headers, opds1_headers):
        """OPDS 1.2 feed includes required Atom elements."""
        headers = {**auth_headers, **opds1_headers}
        response = client_with_data.get("/opds", headers=headers)

        root = ET.fromstring(response.content)
        ns = {'atom': 'http://www.w3.org/2005/Atom'}

        # Required elements
        assert root.find('atom:title', ns) is not None
        assert root.find('atom:id', ns) is not None
        assert root.find('atom:updated', ns) is not None

    def test_opds1_browse_returns_entries_for_items(self, client_with_data, auth_headers, opds1_headers):
        """OPDS 1.2 browse endpoint returns entries for each item."""
        headers = {**auth_headers, **opds1_headers}
        response = client_with_data.get("/opds?path=", headers=headers)

        root = ET.fromstring(response.content)
        ns = {'atom': 'http://www.w3.org/2005/Atom'}

        entries = root.findall('atom:entry', ns)
        assert len(entries) > 0

    def test_opds1_browse_directory_path(self, client_with_data, auth_headers, opds1_headers):
        """OPDS 1.2 browse specific directory."""
        headers = {**auth_headers, **opds1_headers}
        response = client_with_data.get("/opds?path=Series", headers=headers)

        assert response.status_code == 200
