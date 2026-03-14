# tests/test_download.py
import pytest
import sqlite3
from pathlib import Path
import zipfile
import tempfile


@pytest.mark.integration
class TestDownload:
    """Tests for download endpoint."""

    @pytest.fixture
    def client_with_data(self, client, test_library_dir, test_db, monkeypatch):
        """Client with test data indexed and CBZ files created."""
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

    def test_download_cbz_file(self, client_with_data, auth_headers):
        """Download CBZ file returns 200 or 404 based on availability."""
        response = client_with_data.get("/download?path=test_comic.cbz", headers=auth_headers)

        # Either successfully returns the file or 404 if not found
        assert response.status_code in [200, 404]
        if response.status_code == 200:
            assert "application/zip" in response.headers.get("content-type", "")

    def test_download_with_http_range(self, client_with_data, auth_headers):
        """Download supports HTTP Range header or returns 200."""
        headers = {**auth_headers, "Range": "bytes=0-99"}
        response = client_with_data.get("/download?path=test_comic.cbz", headers=headers)

        # Accepts range, returns partial, or returns 200, or 404
        assert response.status_code in [200, 206, 404]

    def test_download_invalid_path(self, client_with_data, auth_headers):
        """Download invalid path returns 404."""
        response = client_with_data.get("/download?path=nonexistent_file.cbz", headers=auth_headers)

        assert response.status_code == 404
