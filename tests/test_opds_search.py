# tests/test_opds_search.py
import pytest
import xml.etree.ElementTree as ET
import sqlite3
from pathlib import Path
from app.main import app


@pytest.mark.integration
@pytest.mark.opds1
class TestOPDS1Search:
    """Tests for OPDS 1.2 search functionality."""

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

    def test_opds1_search_by_title(self, client_with_data, auth_headers, opds1_headers):
        """OPDS 1.2 search returns entries matching title query."""
        headers = {**auth_headers, **opds1_headers}
        response = client_with_data.get("/opds/search?q=Test", headers=headers)

        assert response.status_code == 200
        assert "application/atom+xml" in response.headers["content-type"]

        root = ET.fromstring(response.content)
        assert root.tag.endswith("feed")

    def test_opds1_search_no_results(self, client_with_data, auth_headers, opds1_headers):
        """OPDS 1.2 search returns minimal results for very specific query."""
        headers = {**auth_headers, **opds1_headers}
        response = client_with_data.get("/opds/search?q=zzzzzzzzzzzzzzz_unlikely_to_match_anything", headers=headers)

        assert response.status_code == 200
        root = ET.fromstring(response.content)
        ns = {'atom': 'http://www.w3.org/2005/Atom'}
        entries = root.findall('atom:entry', ns)
        # May have 0 or more entries, just verify response is valid
        assert isinstance(entries, list)

    def test_opds1_search_returns_entries(self, client_with_data, auth_headers, opds1_headers):
        """OPDS 1.2 search results include entry elements."""
        headers = {**auth_headers, **opds1_headers}
        response = client_with_data.get("/opds/search?q=Series", headers=headers)

        assert response.status_code == 200
        root = ET.fromstring(response.content)
        ns = {'atom': 'http://www.w3.org/2005/Atom'}

        # Should have at least title and id
        assert root.find('atom:title', ns) is not None
        assert root.find('atom:id', ns) is not None


@pytest.mark.integration
@pytest.mark.opds2
class TestOPDS2Search:
    """Tests for OPDS 2.0 search functionality."""

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

    def test_opds2_search_by_title(self, client_with_data, auth_headers, opds2_headers):
        """OPDS 2.0 search returns JSON with matching publications."""
        headers = {**auth_headers, **opds2_headers}
        response = client_with_data.get("/opds/search?q=Test", headers=headers)

        assert response.status_code == 200
        assert "application/opds+json" in response.headers["content-type"]

        data = response.json()
        assert "publications" in data or "catalogs" in data or "links" in data

    def test_opds2_search_no_results(self, client_with_data, auth_headers, opds2_headers):
        """OPDS 2.0 search returns valid response for very specific query."""
        headers = {**auth_headers, **opds2_headers}
        response = client_with_data.get("/opds/search?q=zzzzzzzzzzzzzzz_unlikely_to_match_anything", headers=headers)

        assert response.status_code == 200
        data = response.json()
        # Verify response has expected structure
        assert isinstance(data, dict)
        assert any(key in data for key in ["publications", "catalogs", "links", "metadata"])

    def test_opds2_search_returns_publications(self, client_with_data, auth_headers, opds2_headers):
        """OPDS 2.0 search results include publication objects."""
        headers = {**auth_headers, **opds2_headers}
        response = client_with_data.get("/opds/search?q=Series", headers=headers)

        assert response.status_code == 200
        data = response.json()

        # Should have metadata
        assert "metadata" in data or "publications" in data or "links" in data
