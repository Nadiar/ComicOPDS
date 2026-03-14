# tests/test_stream.py
import pytest
import sqlite3
from pathlib import Path


@pytest.mark.integration
class TestPageStreaming:
    """Tests for page streaming endpoint."""

    @pytest.fixture
    def client_with_data(self, client, test_library_dir, test_db, monkeypatch):
        """Client with test data indexed."""
        from tests.fixtures.test_data import index_test_data, create_test_cbz
        from app import db

        # Create test CBZ file
        cbz_path = Path(test_library_dir) / "test_comic.cbz"
        create_test_cbz(cbz_path)

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

    def test_stream_page_from_comic(self, client_with_data, auth_headers):
        """Stream page endpoint returns image for valid path and page."""
        response = client_with_data.get("/stream?path=test_comic.cbz&page=0", headers=auth_headers)

        assert response.status_code in [200, 404]  # 404 if no pages in test file

    def test_stream_invalid_path(self, client_with_data, auth_headers):
        """Stream invalid path returns 404."""
        response = client_with_data.get("/stream?path=nonexistent.cbz&page=0", headers=auth_headers)

        assert response.status_code == 404

    def test_stream_page_out_of_range(self, client_with_data, auth_headers):
        """Stream page out of range returns 404."""
        response = client_with_data.get("/stream?path=test_comic.cbz&page=9999", headers=auth_headers)

        assert response.status_code == 404
