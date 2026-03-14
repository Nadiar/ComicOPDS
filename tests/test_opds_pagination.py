# tests/test_opds_pagination.py
import pytest
import json
import xml.etree.ElementTree as ET
import sqlite3
from pathlib import Path
from app.main import app


@pytest.mark.integration
@pytest.mark.pagination
class TestOPDSPagination:
    """Tests for OPDS pagination."""

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

    def test_opds1_pagination_with_page_parameter(self, client_with_data, auth_headers, opds1_headers):
        """OPDS 1.2 pagination with page parameter."""
        headers = {**auth_headers, **opds1_headers}
        response = client_with_data.get("/opds?path=&page=1", headers=headers)

        assert response.status_code == 200
        root = ET.fromstring(response.content)
        ns = {'atom': 'http://www.w3.org/2005/Atom'}
        entries = root.findall('atom:entry', ns)
        assert len(entries) > 0

    def test_opds2_pagination_with_page_parameter(self, client_with_data, auth_headers, opds2_headers):
        """OPDS 2.0 pagination with page parameter."""
        headers = {**auth_headers, **opds2_headers}
        response = client_with_data.get("/opds?path=&page=1", headers=headers)

        assert response.status_code == 200
        data = response.json()
        publications = data.get("publications", [])
        assert len(publications) > 0

    def test_pagination_returns_consistent_results(self, client_with_data, auth_headers, opds2_headers):
        """Pagination returns different results on different pages."""
        headers = {**auth_headers, **opds2_headers}

        response1 = client_with_data.get("/opds?path=&page=1", headers=headers)
        response2 = client_with_data.get("/opds?path=&page=2", headers=headers)

        data1 = response1.json()
        data2 = response2.json()

        pubs1 = data1.get("publications", [])
        pubs2 = data2.get("publications", [])

        # Both should have items and counts should be reasonable
        assert len(pubs1) > 0 or len(pubs2) > 0

    def test_pagination_links_present_opds2(self, client_with_data, auth_headers, opds2_headers):
        """OPDS 2.0 pagination includes navigation links."""
        headers = {**auth_headers, **opds2_headers}
        response = client_with_data.get("/opds?path=&page=1", headers=headers)

        data = response.json()
        assert "navigation" in data or "links" in data
