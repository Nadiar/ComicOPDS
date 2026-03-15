# tests/test_opds2_streaming.py
"""
Tests for OPDS 2.0 page streaming via DiViNa manifest.

Validates:
  - OPDS 2.0 publication entries link to DiViNa manifest (not PSE)
  - DiViNa manifest endpoint returns valid Readium Web Publication format
  - PSE links are absent from OPDS 2.0 JSON feeds
  - PSE links are present in OPDS 1.2 Atom feeds (unchanged)
"""
import json
import pytest
import sqlite3
import xml.etree.ElementTree as ET
from pathlib import Path
from urllib.parse import quote


ATOM_NS = "http://www.w3.org/2005/Atom"
DIVINA_PROFILE = "https://readium.org/webpub-manifest/profiles/divina"
PSE_REL = "http://vaemendis.net/opds-pse/stream"
ACQUISITION_REL = "http://opds-spec.org/acquisition"


@pytest.fixture
def client_with_comic(client, test_library_dir, test_db, monkeypatch):
    """Client with a CBZ file that has metadata and multiple pages."""
    from tests.fixtures.cbz_samples import create_cbz_with_metadata
    from app import db

    lib = Path(test_library_dir)
    series_dir = lib / "Test Series (2025)"
    series_dir.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(test_db)
    conn.row_factory = sqlite3.Row
    db._ensure_schema(conn)

    # Create directory
    series_rel = "Test Series (2025)"
    db.upsert_dir(conn, series_rel, "Test Series (2025)", "", series_dir.stat().st_mtime)

    # Create CBZ with 5 pages and metadata
    fname = "Test Series (2025) 001.cbz"
    cbz_path = series_dir / fname
    create_cbz_with_metadata(cbz_path, metadata={
        "Title": "The Beginning",
        "Series": "Test Series",
        "Number": "001",
        "Volume": "2025",
        "Writer": "Test Author",
        "Summary": "A test comic issue.",
    }, pages=5)

    rel = f"Test Series (2025)/{fname}"
    db.upsert_file(
        conn, rel, fname,
        cbz_path.stat().st_size, cbz_path.stat().st_mtime,
        series_rel, ".cbz", page_count=5,
    )
    db.upsert_meta(conn, rel, {
        "title": "The Beginning",
        "series": "Test Series",
        "number": "001",
        "volume": "2025",
        "writer": "Test Author",
        "summary": "A test comic issue.",
    })

    conn.commit()
    conn.close()

    def mock_connect():
        c = sqlite3.connect(test_db)
        c.row_factory = sqlite3.Row
        db._ensure_schema(c)
        return c

    monkeypatch.setattr(db, "connect", mock_connect)
    monkeypatch.setattr(db, "DB_PATH", Path(test_db))

    # Point LIBRARY_DIR to test library
    from app import main as main_mod
    monkeypatch.setattr(main_mod, "LIBRARY_DIR", Path(test_library_dir))

    return client


@pytest.mark.integration
class TestOPDS2PublicationLinks:
    """OPDS 2.0 publications must use DiViNa manifest, not PSE."""

    def test_publication_has_self_link_to_manifest(self, client_with_comic, auth_headers, opds2_headers):
        """OPDS 2.0 publication has a self link pointing to a DiViNa manifest."""
        headers = {**auth_headers, **opds2_headers}
        response = client_with_comic.get("/opds?path=Test Series (2025)", headers=headers)
        assert response.status_code == 200

        data = response.json()
        pubs = data.get("publications", [])
        assert len(pubs) >= 1

        pub = pubs[0]
        links = pub.get("links", [])
        self_links = [l for l in links if l.get("rel") == "self"]
        assert len(self_links) == 1, f"Expected exactly one self link, got {len(self_links)}"

        self_link = self_links[0]
        assert self_link["type"] == "application/divina+json", (
            f"Self link type should be 'application/divina+json', got '{self_link['type']}'"
        )
        assert "/opds/v2/manifest" in self_link["href"], (
            f"Self link should point to manifest endpoint, got '{self_link['href']}'"
        )

    def test_publication_has_acquisition_link(self, client_with_comic, auth_headers, opds2_headers):
        """OPDS 2.0 publication has an acquisition link for file download."""
        headers = {**auth_headers, **opds2_headers}
        response = client_with_comic.get("/opds?path=Test Series (2025)", headers=headers)
        data = response.json()
        pubs = data.get("publications", [])
        assert len(pubs) >= 1

        pub = pubs[0]
        links = pub.get("links", [])
        acq_links = [l for l in links if l.get("rel", "").startswith(ACQUISITION_REL)]
        assert len(acq_links) >= 1, "Publication must have at least one acquisition link"
        assert acq_links[0].get("type"), "Acquisition link must have a type"
        assert "/download" in acq_links[0]["href"], "Acquisition link should point to download endpoint"

    def test_publication_has_no_pse_link(self, client_with_comic, auth_headers, opds2_headers):
        """OPDS 2.0 publication must NOT have PSE stream links."""
        headers = {**auth_headers, **opds2_headers}
        response = client_with_comic.get("/opds?path=Test Series (2025)", headers=headers)
        data = response.json()
        pubs = data.get("publications", [])
        assert len(pubs) >= 1

        pub = pubs[0]
        links = pub.get("links", [])
        pse_links = [l for l in links if l.get("rel") == PSE_REL]
        assert len(pse_links) == 0, (
            f"OPDS 2.0 must not contain PSE stream links (PSE is OPDS 1.2 only). "
            f"Found: {pse_links}"
        )

    def test_publication_metadata_has_number_of_pages(self, client_with_comic, auth_headers, opds2_headers):
        """OPDS 2.0 publication metadata includes numberOfPages."""
        headers = {**auth_headers, **opds2_headers}
        response = client_with_comic.get("/opds?path=Test Series (2025)", headers=headers)
        data = response.json()
        pubs = data.get("publications", [])
        assert len(pubs) >= 1

        meta = pubs[0].get("metadata", {})
        assert "numberOfPages" in meta, "Publication metadata should include numberOfPages"
        assert meta["numberOfPages"] == 5

    def test_publication_metadata_has_belongs_to_series(self, client_with_comic, auth_headers, opds2_headers):
        """OPDS 2.0 publication metadata includes belongsTo.series."""
        headers = {**auth_headers, **opds2_headers}
        response = client_with_comic.get("/opds?path=Test Series (2025)", headers=headers)
        data = response.json()
        pubs = data.get("publications", [])
        assert len(pubs) >= 1

        meta = pubs[0].get("metadata", {})
        assert "belongsTo" in meta, "Publication metadata should include belongsTo"
        series_info = meta["belongsTo"].get("series", {})
        assert series_info.get("name") == "Test Series"
        assert series_info.get("position") == 1


@pytest.mark.integration
class TestOPDS1PSEPreserved:
    """OPDS 1.2 feeds must still have PSE links."""

    def test_opds1_entry_has_pse_link(self, client_with_comic, auth_headers, opds1_headers):
        """OPDS 1.2 Atom entries must still contain PSE stream links."""
        headers = {**auth_headers, **opds1_headers}
        response = client_with_comic.get("/opds?path=Test Series (2025)", headers=headers)
        assert response.status_code == 200

        root = ET.fromstring(response.content)
        entries = root.findall(f"{{{ATOM_NS}}}entry")
        assert len(entries) >= 1

        entry = entries[0]
        links = entry.findall(f"{{{ATOM_NS}}}link")
        pse_links = [l for l in links if l.get("rel") == PSE_REL]
        assert len(pse_links) >= 1, "OPDS 1.2 entries must have PSE stream links"

    def test_opds1_entry_has_no_divina_link(self, client_with_comic, auth_headers, opds1_headers):
        """OPDS 1.2 Atom entries must NOT have DiViNa manifest links."""
        headers = {**auth_headers, **opds1_headers}
        response = client_with_comic.get("/opds?path=Test Series (2025)", headers=headers)
        root = ET.fromstring(response.content)
        entries = root.findall(f"{{{ATOM_NS}}}entry")
        assert len(entries) >= 1

        for entry in entries:
            links = entry.findall(f"{{{ATOM_NS}}}link")
            for link in links:
                link_type = link.get("type", "")
                assert "divina" not in link_type, (
                    f"OPDS 1.2 entry should not have DiViNa links, got type='{link_type}'"
                )


@pytest.mark.integration
class TestDiViNaManifest:
    """Tests for the DiViNa manifest endpoint."""

    def test_manifest_returns_valid_divina(self, client_with_comic, auth_headers):
        """DiViNa manifest has required structure."""
        rel = "Test Series (2025)/Test Series (2025) 001.cbz"
        response = client_with_comic.get(
            f"/opds/v2/manifest?path={quote(rel)}", headers=auth_headers
        )
        assert response.status_code == 200
        assert "divina+json" in response.headers.get("content-type", "")

        data = response.json()

        # Required top-level keys
        assert "@context" in data
        assert data["@context"] == "https://readium.org/webpub-manifest/context.jsonld"
        assert "metadata" in data
        assert "links" in data
        assert "readingOrder" in data

    def test_manifest_metadata(self, client_with_comic, auth_headers):
        """DiViNa manifest metadata includes required fields."""
        rel = "Test Series (2025)/Test Series (2025) 001.cbz"
        response = client_with_comic.get(
            f"/opds/v2/manifest?path={quote(rel)}", headers=auth_headers
        )
        data = response.json()
        meta = data["metadata"]

        assert meta["conformsTo"] == DIVINA_PROFILE
        assert "title" in meta
        assert meta["numberOfPages"] == 5
        assert meta["readingProgression"] == "ltr"

    def test_manifest_reading_order_has_all_pages(self, client_with_comic, auth_headers):
        """readingOrder contains one link per page image."""
        rel = "Test Series (2025)/Test Series (2025) 001.cbz"
        response = client_with_comic.get(
            f"/opds/v2/manifest?path={quote(rel)}", headers=auth_headers
        )
        data = response.json()
        ro = data["readingOrder"]

        assert len(ro) == 5, f"Expected 5 pages in readingOrder, got {len(ro)}"
        for page in ro:
            assert "href" in page, "Each readingOrder item must have href"
            assert page.get("type") == "image/jpeg"
            assert "/pse/page" in page["href"]

    def test_manifest_first_page_has_cover_rel(self, client_with_comic, auth_headers):
        """First page in readingOrder has rel='cover'."""
        rel = "Test Series (2025)/Test Series (2025) 001.cbz"
        response = client_with_comic.get(
            f"/opds/v2/manifest?path={quote(rel)}", headers=auth_headers
        )
        data = response.json()
        ro = data["readingOrder"]

        assert ro[0].get("rel") == "cover", "First page should have rel='cover'"
        # Other pages should not have cover rel
        for page in ro[1:]:
            assert page.get("rel") != "cover"

    def test_manifest_self_link(self, client_with_comic, auth_headers):
        """Manifest has a self link with application/divina+json type."""
        rel = "Test Series (2025)/Test Series (2025) 001.cbz"
        response = client_with_comic.get(
            f"/opds/v2/manifest?path={quote(rel)}", headers=auth_headers
        )
        data = response.json()
        links = data["links"]

        self_links = [l for l in links if l.get("rel") == "self"]
        assert len(self_links) == 1
        assert self_links[0]["type"] == "application/divina+json"

    def test_manifest_series_metadata(self, client_with_comic, auth_headers):
        """Manifest metadata includes belongsTo.series info."""
        rel = "Test Series (2025)/Test Series (2025) 001.cbz"
        response = client_with_comic.get(
            f"/opds/v2/manifest?path={quote(rel)}", headers=auth_headers
        )
        data = response.json()
        meta = data["metadata"]

        assert "belongsTo" in meta
        series = meta["belongsTo"]["series"]
        assert series["name"] == "Test Series"
        assert series["position"] == 1

    def test_manifest_page_hrefs_are_sequential(self, client_with_comic, auth_headers):
        """Page hrefs use sequential zero-based page numbers."""
        rel = "Test Series (2025)/Test Series (2025) 001.cbz"
        response = client_with_comic.get(
            f"/opds/v2/manifest?path={quote(rel)}", headers=auth_headers
        )
        data = response.json()
        ro = data["readingOrder"]

        for i, page in enumerate(ro):
            assert f"page={i}" in page["href"], (
                f"Page {i} href should contain 'page={i}', got '{page['href']}'"
            )

    def test_manifest_invalid_path_returns_404(self, client_with_comic, auth_headers):
        """Requesting manifest for non-existent file returns 404."""
        response = client_with_comic.get(
            "/opds/v2/manifest?path=nonexistent.cbz", headers=auth_headers
        )
        assert response.status_code == 404

    def test_manifest_for_non_cbz_returns_404(self, client_with_comic, auth_headers):
        """Requesting manifest for a non-CBZ file returns 404."""
        response = client_with_comic.get(
            "/opds/v2/manifest?path=readme.txt", headers=auth_headers
        )
        assert response.status_code == 404
