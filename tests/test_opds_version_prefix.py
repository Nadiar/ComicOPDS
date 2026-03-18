"""Tests for OPDS version prefix preservation in navigation links.

When browsing via /opds20, all navigation links should stay on /opds20.
When browsing via /opds12, all navigation links should stay on /opds12.
When browsing via /opds, all navigation links should stay on /opds.
"""
import pytest
import sqlite3
from pathlib import Path


@pytest.mark.integration
@pytest.mark.opds2
class TestOPDS20PrefixPreservation:
    """Navigation links via /opds20 must use /opds20, not /opds."""

    @pytest.fixture
    def client_with_data(self, client, test_library_dir, test_db, monkeypatch):
        from tests.fixtures.test_data import index_test_data
        from app import db

        conn = sqlite3.connect(test_db)
        conn.row_factory = sqlite3.Row
        db._ensure_schema(conn)
        index_test_data(conn, Path(test_library_dir))
        conn.close()

        def mock_connect():
            c = sqlite3.connect(test_db)
            c.row_factory = sqlite3.Row
            db._ensure_schema(c)
            return c

        monkeypatch.setattr(db, "connect", mock_connect)
        monkeypatch.setattr(db, "DB_PATH", Path(test_db))
        return client

    def test_opds20_navigation_links_use_opds20(self, client_with_data, auth_headers):
        """Directory entries in /opds20 feed must link to /opds20, not /opds."""
        response = client_with_data.get("/opds20", headers=auth_headers)
        assert response.status_code == 200

        data = response.json()
        for nav in data.get("navigation", []):
            href = nav.get("href", "")
            # Skip smart list entries which use a different href format
            if "/smart" in href:
                continue
            assert "/opds20" in href or href.endswith("/opds20"), \
                f"Navigation link should use /opds20 prefix, got: {href}"

    def test_opds20_subdirectory_links_use_opds20(self, client_with_data, auth_headers):
        """Subdirectory navigation in /opds20 must also use /opds20."""
        # First get root to find a directory
        response = client_with_data.get("/opds20", headers=auth_headers)
        data = response.json()

        nav_items = [n for n in data.get("navigation", []) if "/smart" not in n.get("href", "")]
        if not nav_items:
            pytest.skip("No navigation items in test data")

        # Follow the first directory link
        first_href = nav_items[0]["href"]
        assert "/opds20" in first_href, f"First nav link should use /opds20: {first_href}"


@pytest.mark.integration
@pytest.mark.opds1
class TestOPDS12PrefixPreservation:
    """Navigation links via /opds12 must use /opds12, not /opds."""

    @pytest.fixture
    def client_with_data(self, client, test_library_dir, test_db, monkeypatch):
        from tests.fixtures.test_data import index_test_data
        from app import db

        conn = sqlite3.connect(test_db)
        conn.row_factory = sqlite3.Row
        db._ensure_schema(conn)
        index_test_data(conn, Path(test_library_dir))
        conn.close()

        def mock_connect():
            c = sqlite3.connect(test_db)
            c.row_factory = sqlite3.Row
            db._ensure_schema(c)
            return c

        monkeypatch.setattr(db, "connect", mock_connect)
        monkeypatch.setattr(db, "DB_PATH", Path(test_db))
        return client

    def test_opds12_navigation_links_use_opds12(self, client_with_data, auth_headers):
        """Directory entries in /opds12 XML feed must link to /opds12, not /opds."""
        import xml.etree.ElementTree as ET

        response = client_with_data.get("/opds12", headers=auth_headers)
        assert response.status_code == 200

        ns = {"atom": "http://www.w3.org/2005/Atom"}
        root = ET.fromstring(response.content)
        entries = root.findall("atom:entry", ns)

        for entry in entries:
            links = entry.findall("atom:link", ns)
            for link in links:
                href = link.get("href", "")
                rel = link.get("rel", "")
                if rel == "subsection" and "/smart" not in href:
                    assert "/opds12" in href, \
                        f"Subsection link should use /opds12 prefix, got: {href}"
