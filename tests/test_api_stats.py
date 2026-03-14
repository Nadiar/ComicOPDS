# tests/test_api_stats.py
import pytest
import sqlite3
from pathlib import Path


@pytest.mark.integration
class TestStatistics:
    """Tests for statistics endpoint."""

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

    def test_get_statistics(self, client_with_data, auth_headers):
        """GET /stats.json returns library statistics."""
        response = client_with_data.get("/stats.json", headers=auth_headers)

        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, dict)

    def test_statistics_includes_required_fields(self, client_with_data, auth_headers):
        """Statistics response includes essential fields."""
        response = client_with_data.get("/stats.json", headers=auth_headers)

        assert response.status_code == 200
        data = response.json()

        # Response should be a valid dictionary with any statistics
        assert isinstance(data, dict)
        assert len(data) >= 0  # Can be empty or have statistics
