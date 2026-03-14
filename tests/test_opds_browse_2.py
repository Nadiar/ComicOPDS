# tests/test_opds_browse_2.py
import pytest
import json
import sqlite3
from pathlib import Path
from app.main import app


@pytest.mark.integration
@pytest.mark.opds2
class TestOPDS2Browse:
    """Tests for OPDS 2.0 feed generation."""

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

    def test_opds2_root_feed_returns_valid_json(self, client_with_data, auth_headers, opds2_headers):
        """OPDS 2.0 root feed returns valid JSON."""
        headers = {**auth_headers, **opds2_headers}
        response = client_with_data.get("/opds", headers=headers)

        assert response.status_code == 200
        assert response.headers["content-type"] == "application/opds+json"

        # Parse as JSON
        data = response.json()
        assert isinstance(data, dict)
        assert "metadata" in data or "catalogs" in data or "publications" in data

    def test_opds2_feed_includes_required_fields(self, client_with_data, auth_headers, opds2_headers):
        """OPDS 2.0 feed includes required fields."""
        headers = {**auth_headers, **opds2_headers}
        response = client_with_data.get("/opds", headers=headers)

        data = response.json()
        assert "metadata" in data
        assert "title" in data["metadata"]
        assert "links" in data
        assert "navigation" in data
        assert "publications" in data

    def test_opds2_browse_returns_publications(self, client_with_data, auth_headers, opds2_headers):
        """OPDS 2.0 browse endpoint returns publications or navigation."""
        headers = {**auth_headers, **opds2_headers}
        # Browse root returns directories (navigation items)
        response = client_with_data.get("/opds?path=", headers=headers)

        data = response.json()
        assert "publications" in data
        assert "navigation" in data
        assert isinstance(data["publications"], list)
        assert isinstance(data["navigation"], list)
        # With test data indexed, there should be navigation items (directories)
        assert len(data["navigation"]) > 0

    def test_opds2_browse_directory_path(self, client_with_data, auth_headers, opds2_headers):
        """OPDS 2.0 browse specific directory."""
        headers = {**auth_headers, **opds2_headers}
        response = client_with_data.get("/opds?path=Series", headers=headers)

        assert response.status_code == 200
