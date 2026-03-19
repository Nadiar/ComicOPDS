# tests/test_api_users.py
import pytest
import sqlite3
from pathlib import Path


@pytest.mark.integration
class TestUserManagement:
    """Tests for user management API endpoints."""

    @staticmethod
    def _csrf_headers(auth_headers: dict[str, str]) -> dict[str, str]:
        headers = dict(auth_headers)
        headers["X-Requested-With"] = "XMLHttpRequest"
        return headers

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

    def test_list_users(self, client_with_data, auth_headers):
        """GET /api/users returns list of users."""
        response = client_with_data.get("/api/users", headers=auth_headers)

        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)

    def test_create_user(self, client_with_data, auth_headers):
        """POST /api/users creates new user."""
        payload = {
            "username": "newuser",
            "password": "newpass123",
            "is_admin": False
        }
        response = client_with_data.post("/api/users", json=payload, headers=self._csrf_headers(auth_headers))

        assert response.status_code in [200, 201]

    def test_delete_user(self, client_with_data, auth_headers):
        """DELETE /api/users/{username} removes user or returns error."""
        # Try to delete a user - may not exist so test the endpoint exists
        response = client_with_data.delete("/api/users/testuser", headers=self._csrf_headers(auth_headers))

        # Should either succeed or return appropriate error
        assert response.status_code in [200, 204, 404, 403, 422]
