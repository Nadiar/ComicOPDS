# tests/test_opds_smartlists.py
import pytest
import sqlite3
from pathlib import Path
import json


@pytest.mark.integration
class TestSmartLists:
    """Tests for smart list endpoints."""

    @pytest.fixture
    def client_with_data(self, client, test_library_dir, test_db, monkeypatch):
        """Client with test data indexed."""
        from tests.fixtures.test_data import index_test_data
        from app import db

        conn = sqlite3.connect(test_db)
        conn.row_factory = sqlite3.Row
        db._ensure_schema(conn)
        index_test_data(conn, Path(test_library_dir))
        conn.close()

        def mock_connect():
            test_conn = sqlite3.connect(test_db)
            test_conn.row_factory = sqlite3.Row
            db._ensure_schema(test_conn)
            return test_conn

        monkeypatch.setattr(db, "connect", mock_connect)
        monkeypatch.setattr(db, "DB_PATH", Path(test_db))

        return client

    def test_get_smartlists(self, client_with_data, auth_headers):
        """Get list of available smart lists."""
        response = client_with_data.get("/smartlists.json", headers=auth_headers)

        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)

    def test_browse_smartlist(self, client_with_data, auth_headers, opds1_headers):
        """Browse a specific smart list by slug."""
        headers = {**auth_headers, **opds1_headers}

        # First get available smartlists
        smartlist_response = client_with_data.get("/smartlists.json", headers=auth_headers)
        smartlists = smartlist_response.json()

        if smartlists:
            slug = smartlists[0].get("slug", "recent")
            response = client_with_data.get(f"/opds/smart/{slug}", headers=headers)
            assert response.status_code == 200

    def test_invalid_smartlist_slug(self, client_with_data, auth_headers, opds1_headers):
        """Invalid smart list slug returns 404."""
        headers = {**auth_headers, **opds1_headers}
        response = client_with_data.get("/opds/smart/nonexistent_slug_xyz", headers=headers)

        assert response.status_code == 404
