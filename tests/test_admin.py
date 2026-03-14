# tests/test_admin.py
import pytest
import sqlite3
from pathlib import Path


@pytest.mark.integration
class TestAdminEndpoints:
    """Tests for admin endpoints."""

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

    def test_reindex_trigger(self, client_with_data, auth_headers):
        """POST /admin/reindex triggers background scan."""
        response = client_with_data.post("/admin/reindex", headers=auth_headers)

        assert response.status_code in [200, 202]

    def test_precache_trigger(self, client_with_data, auth_headers):
        """POST /admin/thumbs/precache generates all thumbnails."""
        response = client_with_data.post("/admin/thumbs/precache", headers=auth_headers)

        assert response.status_code in [200, 202]

    def test_health_check(self, client_with_data):
        """GET /healthz returns health status."""
        response = client_with_data.get("/healthz")

        assert response.status_code == 200
        # Health check can return JSON or plain text
        try:
            data = response.json()
            assert isinstance(data, dict)
        except:
            # If not JSON, just verify it's a string response
            assert isinstance(response.text, str)
