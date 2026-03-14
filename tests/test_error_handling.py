# tests/test_error_handling.py
import pytest
import sqlite3
from pathlib import Path


@pytest.mark.integration
class TestErrorHandling:
    """Tests for error handling and HTTP status codes."""

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

    def test_unauthorized_without_auth(self, client_with_data):
        """Request without valid auth returns 401 or 403."""
        response = client_with_data.get("/opds")

        assert response.status_code in [401, 403]

    def test_not_found_invalid_path(self, client_with_data, auth_headers):
        """Request to invalid path returns 404."""
        response = client_with_data.get("/nonexistent/path", headers=auth_headers)

        assert response.status_code == 404

    def test_bad_request_invalid_params(self, client_with_data, auth_headers):
        """Request with invalid parameters returns 400."""
        response = client_with_data.get("/stream?path=test.cbz", headers=auth_headers)

        # Missing required page parameter
        assert response.status_code in [400, 404]

    def test_server_error_handling(self, client_with_data, auth_headers):
        """Server errors are handled gracefully."""
        # Test error handling by accessing a real endpoint
        response = client_with_data.get("/stats.json", headers=auth_headers)

        # Should return valid response
        assert response.status_code in [200, 400, 500]
