# tests/test_opds_format_validation.py
"""
Comprehensive OPDS format validation tests.

Validates that all feed endpoints produce spec-compliant output for
both OPDS 1.2 (Atom XML) and OPDS 2.0 (JSON).
"""
import pytest
import json
import xml.etree.ElementTree as ET
import sqlite3
from pathlib import Path

from tests.opds_validators import (
    validate_opds1_feed,
    validate_opds2_feed,
    _is_valid_rfc3339,
    ATOM_NS,
)


# Shared fixture that all test classes use
@pytest.fixture
def client_with_data(client, test_library_dir, test_db, monkeypatch):
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


# ─── Validator unit tests ──────────────────────────────────────

@pytest.mark.unit
class TestRFC3339Validation:
    """Tests for the RFC 3339 timestamp validator itself."""

    @pytest.mark.parametrize("ts", [
        "2024-01-15T10:30:00+00:00",
        "2024-01-15T10:30:00Z",
        "2024-01-15T10:30:00.123456+00:00",
        "2024-12-31T23:59:59-05:00",
    ])
    def test_valid_timestamps(self, ts):
        assert _is_valid_rfc3339(ts)

    @pytest.mark.parametrize("ts", [
        "",
        "not-a-date",
        "2024-01-15",
        "2024-01-15 10:30:00",
        "2024-13-01T00:00:00Z",
    ])
    def test_invalid_timestamps(self, ts):
        assert not _is_valid_rfc3339(ts)


# ─── OPDS 1.2 format validation ───────────────────────────────

@pytest.mark.integration
@pytest.mark.opds1
class TestOPDS1FormatValidation:
    """Validate OPDS 1.2 Atom XML feeds against the specification."""

    def test_root_feed_valid(self, client_with_data, auth_headers, opds1_headers):
        """Root feed is fully spec-compliant OPDS 1.2."""
        headers = {**auth_headers, **opds1_headers}
        response = client_with_data.get("/opds", headers=headers)
        assert response.status_code == 200

        result = validate_opds1_feed(response.content)
        result.assert_valid()

    def test_browse_directory_feed_valid(self, client_with_data, auth_headers, opds1_headers):
        """Browse feed for a directory is spec-compliant."""
        headers = {**auth_headers, **opds1_headers}
        response = client_with_data.get("/opds?path=Series", headers=headers)
        assert response.status_code == 200

        result = validate_opds1_feed(response.content)
        result.assert_valid()

    def test_browse_subdirectory_feed_valid(self, client_with_data, auth_headers, opds1_headers):
        """Browse feed for a subdirectory with CBZ files is spec-compliant."""
        headers = {**auth_headers, **opds1_headers}
        response = client_with_data.get("/opds?path=Series/Amazing Spider-Man", headers=headers)
        assert response.status_code == 200

        result = validate_opds1_feed(response.content)
        result.assert_valid()

    def test_search_feed_valid(self, client_with_data, auth_headers, opds1_headers):
        """Search results feed is spec-compliant."""
        headers = {**auth_headers, **opds1_headers}
        response = client_with_data.get("/opds/search?query=Spider", headers=headers)
        assert response.status_code == 200

        result = validate_opds1_feed(response.content)
        result.assert_valid()

    def test_search_empty_results_valid(self, client_with_data, auth_headers, opds1_headers):
        """Search with no results still produces valid feed."""
        headers = {**auth_headers, **opds1_headers}
        response = client_with_data.get(
            "/opds/search?query=zzzzzzzzzzz_no_match", headers=headers
        )
        assert response.status_code == 200

        result = validate_opds1_feed(response.content)
        result.assert_valid()

    def test_opds12_prefix_feed_valid(self, client_with_data, auth_headers):
        """Feed via /opds12 prefix is spec-compliant OPDS 1.2."""
        response = client_with_data.get("/opds12", headers=auth_headers)
        assert response.status_code == 200
        assert "atom+xml" in response.headers.get("content-type", "")

        result = validate_opds1_feed(response.content)
        result.assert_valid()

    def test_feed_content_type(self, client_with_data, auth_headers, opds1_headers):
        """OPDS 1.2 feed has correct content-type."""
        headers = {**auth_headers, **opds1_headers}
        response = client_with_data.get("/opds", headers=headers)
        assert "application/atom+xml" in response.headers["content-type"]

    def test_feed_updated_is_not_epoch(self, client_with_data, auth_headers, opds1_headers):
        """Feed <updated> is a real timestamp, not epoch zero."""
        headers = {**auth_headers, **opds1_headers}
        response = client_with_data.get("/opds", headers=headers)
        root = ET.fromstring(response.content)

        updated = root.find(f"{{{ATOM_NS}}}updated")
        assert updated is not None
        assert updated.text is not None
        assert not updated.text.startswith("1970-01-01")

    def test_entry_ids_are_unique(self, client_with_data, auth_headers, opds1_headers):
        """All entry IDs in a feed are unique."""
        headers = {**auth_headers, **opds1_headers}
        response = client_with_data.get("/opds?path=Series/Amazing Spider-Man", headers=headers)
        root = ET.fromstring(response.content)

        ids = [
            entry.find(f"{{{ATOM_NS}}}id").text
            for entry in root.findall(f"{{{ATOM_NS}}}entry")
            if entry.find(f"{{{ATOM_NS}}}id") is not None
        ]
        assert len(ids) == len(set(ids)), f"Duplicate entry IDs found: {ids}"

    def test_acquisition_entries_have_correct_mime(self, client_with_data, auth_headers, opds1_headers):
        """Acquisition links use correct MIME type for CBZ files."""
        headers = {**auth_headers, **opds1_headers}
        response = client_with_data.get("/opds?path=Series/Amazing Spider-Man", headers=headers)
        root = ET.fromstring(response.content)

        for entry in root.findall(f"{{{ATOM_NS}}}entry"):
            for link in entry.findall(f"{{{ATOM_NS}}}link"):
                rel = link.get("rel", "")
                if rel.startswith("http://opds-spec.org/acquisition"):
                    link_type = link.get("type", "")
                    assert link_type == "application/vnd.comicbook+zip", (
                        f"CBZ acquisition link should have type "
                        f"'application/vnd.comicbook+zip', got '{link_type}'"
                    )

    def test_navigation_entries_are_directories(self, client_with_data, auth_headers, opds1_headers):
        """Navigation entries (subsection links) exist for directories."""
        headers = {**auth_headers, **opds1_headers}
        response = client_with_data.get("/opds?path=Series", headers=headers)
        root = ET.fromstring(response.content)

        entries = root.findall(f"{{{ATOM_NS}}}entry")
        assert len(entries) > 0

        # At least some entries should be subsection (directory navigation) links
        nav_entries = [
            entry for entry in entries
            if any(
                link.get("rel") == "subsection"
                for link in entry.findall(f"{{{ATOM_NS}}}link")
            )
        ]
        assert len(nav_entries) > 0, "Expected at least one navigation (subsection) entry"

    def test_opensearch_elements_present_in_search(self, client_with_data, auth_headers, opds1_headers):
        """Search results feed includes OpenSearch response elements."""
        headers = {**auth_headers, **opds1_headers}
        response = client_with_data.get("/opds/search?query=Spider", headers=headers)
        root = ET.fromstring(response.content)

        ns_os = {"os": "http://a9.com/-/spec/opensearch/1.1/"}
        total = root.find("os:totalResults", ns_os)
        start = root.find("os:startIndex", ns_os)
        items = root.find("os:itemsPerPage", ns_os)

        assert total is not None, "Search feed should include opensearch:totalResults"
        assert start is not None, "Search feed should include opensearch:startIndex"
        assert items is not None, "Search feed should include opensearch:itemsPerPage"


# ─── OPDS 2.0 format validation ───────────────────────────────

@pytest.mark.integration
@pytest.mark.opds2
class TestOPDS2FormatValidation:
    """Validate OPDS 2.0 JSON feeds against the specification."""

    def test_root_feed_valid(self, client_with_data, auth_headers, opds2_headers):
        """Root feed is fully spec-compliant OPDS 2.0."""
        headers = {**auth_headers, **opds2_headers}
        response = client_with_data.get("/opds", headers=headers)
        assert response.status_code == 200

        data = response.json()
        result = validate_opds2_feed(data)
        result.assert_valid()

    def test_browse_directory_feed_valid(self, client_with_data, auth_headers, opds2_headers):
        """Browse feed for a directory is spec-compliant."""
        headers = {**auth_headers, **opds2_headers}
        response = client_with_data.get("/opds?path=Series", headers=headers)
        assert response.status_code == 200

        result = validate_opds2_feed(response.json())
        result.assert_valid()

    def test_browse_subdirectory_feed_valid(self, client_with_data, auth_headers, opds2_headers):
        """Browse feed for CBZ-containing directory is spec-compliant."""
        headers = {**auth_headers, **opds2_headers}
        response = client_with_data.get("/opds?path=Series/Amazing Spider-Man", headers=headers)
        assert response.status_code == 200

        result = validate_opds2_feed(response.json())
        result.assert_valid()

    def test_search_feed_valid(self, client_with_data, auth_headers, opds2_headers):
        """Search results feed is spec-compliant."""
        headers = {**auth_headers, **opds2_headers}
        response = client_with_data.get("/opds/search?query=Spider", headers=headers)
        assert response.status_code == 200

        result = validate_opds2_feed(response.json())
        result.assert_valid()

    def test_search_empty_results_valid(self, client_with_data, auth_headers, opds2_headers):
        """Search with no results still produces valid feed."""
        headers = {**auth_headers, **opds2_headers}
        response = client_with_data.get(
            "/opds/search?query=zzzzzzzzzzz_no_match", headers=headers
        )
        assert response.status_code == 200

        result = validate_opds2_feed(response.json())
        result.assert_valid()

    def test_opds20_prefix_feed_valid(self, client_with_data, auth_headers):
        """Feed via /opds20 prefix is spec-compliant OPDS 2.0."""
        response = client_with_data.get("/opds20", headers=auth_headers)
        assert response.status_code == 200
        assert response.headers.get("content-type") == "application/opds+json"

        result = validate_opds2_feed(response.json())
        result.assert_valid()

    def test_feed_content_type(self, client_with_data, auth_headers, opds2_headers):
        """OPDS 2.0 feed has correct content-type."""
        headers = {**auth_headers, **opds2_headers}
        response = client_with_data.get("/opds", headers=headers)
        assert response.headers["content-type"] == "application/opds+json"

    def test_self_link_matches_request(self, client_with_data, auth_headers, opds2_headers):
        """Self link href matches the requested URL path."""
        headers = {**auth_headers, **opds2_headers}
        response = client_with_data.get("/opds?path=Series", headers=headers)
        data = response.json()

        self_links = [l for l in data["links"] if l.get("rel") == "self"]
        assert len(self_links) == 1
        assert "/opds" in self_links[0]["href"]
        assert "Series" in self_links[0]["href"]

    def test_navigation_items_have_type(self, client_with_data, auth_headers, opds2_headers):
        """Navigation items include type field."""
        headers = {**auth_headers, **opds2_headers}
        response = client_with_data.get("/opds?path=Series", headers=headers)
        data = response.json()

        for nav in data.get("navigation", []):
            assert "type" in nav, f"Navigation item '{nav.get('title')}' missing 'type'"
            assert nav["type"] == "application/opds+json"

    def test_publications_have_identifier(self, client_with_data, auth_headers, opds2_headers):
        """Each publication has an identifier in metadata."""
        headers = {**auth_headers, **opds2_headers}
        response = client_with_data.get(
            "/opds?path=Series/Amazing Spider-Man", headers=headers
        )
        data = response.json()

        for pub in data.get("publications", []):
            assert pub["metadata"].get("identifier"), (
                f"Publication '{pub['metadata'].get('title')}' missing identifier"
            )

    def test_publication_modified_timestamps_valid(self, client_with_data, auth_headers, opds2_headers):
        """All publication modified timestamps are valid RFC 3339."""
        headers = {**auth_headers, **opds2_headers}
        response = client_with_data.get(
            "/opds?path=Series/Amazing Spider-Man", headers=headers
        )
        data = response.json()

        for pub in data.get("publications", []):
            modified = pub.get("metadata", {}).get("modified")
            if modified:
                assert _is_valid_rfc3339(modified), (
                    f"Publication '{pub['metadata'].get('title')}' has invalid modified: {modified}"
                )

    def test_pagination_metadata_integers(self, client_with_data, auth_headers, opds2_headers):
        """Pagination metadata fields are integers."""
        headers = {**auth_headers, **opds2_headers}
        response = client_with_data.get("/opds?path=&page=1", headers=headers)
        data = response.json()
        meta = data.get("metadata", {})

        if "numberOfItems" in meta:
            assert isinstance(meta["numberOfItems"], int)
        if "itemsPerPage" in meta:
            assert isinstance(meta["itemsPerPage"], int)

    def test_acquisition_links_have_type(self, client_with_data, auth_headers, opds2_headers):
        """Acquisition links include MIME type."""
        headers = {**auth_headers, **opds2_headers}
        response = client_with_data.get(
            "/opds?path=Series/Amazing Spider-Man", headers=headers
        )
        data = response.json()

        for pub in data.get("publications", []):
            for link in pub.get("links", []):
                rel = link.get("rel", "")
                if rel.startswith("http://opds-spec.org/acquisition"):
                    assert link.get("type"), (
                        f"Acquisition link in '{pub['metadata'].get('title')}' missing type"
                    )


# ─── Cross-version consistency ─────────────────────────────────

@pytest.mark.integration
class TestCrossVersionConsistency:
    """Verify that OPDS 1.2 and 2.0 serve consistent data for the same queries."""

    def test_same_entry_count(self, client_with_data, auth_headers, opds1_headers, opds2_headers):
        """Both versions return the same number of items for the same path."""
        # OPDS 1.2
        h1 = {**auth_headers, **opds1_headers}
        r1 = client_with_data.get("/opds?path=Series/Amazing Spider-Man", headers=h1)
        root = ET.fromstring(r1.content)
        entries_v1 = root.findall(f"{{{ATOM_NS}}}entry")

        # OPDS 2.0
        h2 = {**auth_headers, **opds2_headers}
        r2 = client_with_data.get("/opds?path=Series/Amazing Spider-Man", headers=h2)
        data = r2.json()
        entries_v2 = data.get("publications", [])

        assert len(entries_v1) == len(entries_v2), (
            f"OPDS 1.2 returned {len(entries_v1)} entries, "
            f"OPDS 2.0 returned {len(entries_v2)} publications"
        )

    def test_same_titles(self, client_with_data, auth_headers, opds1_headers, opds2_headers):
        """Both versions return the same titles for the same path."""
        # OPDS 1.2
        h1 = {**auth_headers, **opds1_headers}
        r1 = client_with_data.get("/opds?path=Series/Amazing Spider-Man", headers=h1)
        root = ET.fromstring(r1.content)
        titles_v1 = sorted([
            entry.find(f"{{{ATOM_NS}}}title").text
            for entry in root.findall(f"{{{ATOM_NS}}}entry")
        ])

        # OPDS 2.0
        h2 = {**auth_headers, **opds2_headers}
        r2 = client_with_data.get("/opds?path=Series/Amazing Spider-Man", headers=h2)
        data = r2.json()
        titles_v2 = sorted([
            pub["metadata"]["title"]
            for pub in data.get("publications", [])
        ])

        assert titles_v1 == titles_v2, (
            f"Title mismatch: v1={titles_v1}, v2={titles_v2}"
        )

    def test_version_prefix_consistency(self, client_with_data, auth_headers):
        """Explicit version prefixes return correct format."""
        # /opds12 should always be XML
        r12 = client_with_data.get("/opds12", headers=auth_headers)
        assert "atom+xml" in r12.headers.get("content-type", "")

        # /opds20 should always be JSON
        r20 = client_with_data.get("/opds20", headers=auth_headers)
        assert r20.headers.get("content-type") == "application/opds+json"


# ─── Unicode / encoding validation ────────────────────────────

@pytest.mark.integration
class TestUnicodeEncoding:
    """Validate that Unicode characters (especially em dashes) survive XML/JSON rendering."""

    @pytest.fixture
    def client_with_titled_data(self, client, test_library_dir, test_db, monkeypatch):
        """Client with entries that have series+number+title metadata (triggers em dash)."""
        from tests.fixtures.cbz_samples import create_sample_cbz
        from app import db

        lib = Path(test_library_dir)
        series_dir = lib / "Absolute Flash (2025)"
        series_dir.mkdir(parents=True, exist_ok=True)

        conn = sqlite3.connect(test_db)
        conn.row_factory = sqlite3.Row
        db._ensure_schema(conn)

        # Create directory entry
        series_rel = "Absolute Flash (2025)"
        db.upsert_dir(conn, series_rel, "Absolute Flash (2025)", "", series_dir.stat().st_mtime)

        # Create CBZ files with series+number+title metadata
        issues = [
            ("001", "Of Two Worlds, Part One"),
            ("002", "Of Two Worlds, Part Two"),
            ("003", "Of Two Worlds — Part Three"),  # title itself has em dash
        ]
        for num, title in issues:
            fname = f"Absolute Flash (2025) {num}.cbz"
            cbz_path = series_dir / fname
            create_sample_cbz(cbz_path, title=title)
            rel = f"Absolute Flash (2025)/{fname}"
            db.upsert_file(
                conn, rel, fname,
                cbz_path.stat().st_size, cbz_path.stat().st_mtime,
                series_rel, ".cbz",
            )
            db.upsert_meta(conn, rel, {
                "title": title,
                "series": "Absolute Flash",
                "number": num,
                "volume": "2025",
                "format": "CBZ",
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
        return client

    def test_xml_em_dash_encoding(self, client_with_titled_data, auth_headers, opds1_headers):
        """The em dash separator in titles must be valid UTF-8 (U+2014) in XML output."""
        headers = {**auth_headers, **opds1_headers}
        response = client_with_titled_data.get(
            "/opds?path=Absolute Flash (2025)", headers=headers
        )
        assert response.status_code == 200

        raw = response.content  # bytes
        # The em dash U+2014 in UTF-8 is E2 80 94
        CORRECT_EM_DASH = b"\xe2\x80\x94"
        # Common corruption: CP437 interpretation gives CE 93 C3 87 C3 B6 (ΓÇö)
        CP437_CORRUPTION = b"\xce\x93\xc3\x87\xc3\xb6"

        assert CP437_CORRUPTION not in raw, (
            "Em dash corrupted: UTF-8 bytes decoded as CP437 then re-encoded. "
            f"Found ΓÇö (CP437) instead of — (U+2014) in XML output."
        )
        assert CORRECT_EM_DASH in raw, (
            "Em dash (U+2014, UTF-8: E2 80 94) not found in XML output"
        )

    def test_json_em_dash_encoding(self, client_with_titled_data, auth_headers, opds2_headers):
        """The em dash separator in titles must be valid UTF-8 (U+2014) in JSON output."""
        headers = {**auth_headers, **opds2_headers}
        response = client_with_titled_data.get(
            "/opds?path=Absolute Flash (2025)", headers=headers
        )
        assert response.status_code == 200

        raw = response.content  # bytes
        CORRECT_EM_DASH = b"\xe2\x80\x94"
        CP437_CORRUPTION = b"\xce\x93\xc3\x87\xc3\xb6"

        assert CP437_CORRUPTION not in raw, (
            "Em dash corrupted in JSON: UTF-8 bytes decoded as CP437."
        )
        # JSON may use \u2014 escape or raw UTF-8 — both are valid
        has_raw = CORRECT_EM_DASH in raw
        has_escaped = b"\\u2014" in raw
        assert has_raw or has_escaped, (
            "Em dash (U+2014) not found in JSON output as raw UTF-8 or \\u2014 escape"
        )

    def test_xml_titles_match_json_titles(self, client_with_titled_data, auth_headers, opds1_headers, opds2_headers):
        """Titles in XML and JSON feeds must be byte-identical when compared as Unicode strings."""
        path = "Absolute Flash (2025)"

        # Get XML titles
        h1 = {**auth_headers, **opds1_headers}
        r1 = client_with_titled_data.get(f"/opds?path={path}", headers=h1)
        root = ET.fromstring(r1.content)
        xml_titles = sorted([
            entry.find(f"{{{ATOM_NS}}}title").text
            for entry in root.findall(f"{{{ATOM_NS}}}entry")
        ])

        # Get JSON titles
        h2 = {**auth_headers, **opds2_headers}
        r2 = client_with_titled_data.get(f"/opds?path={path}", headers=h2)
        data = r2.json()
        json_titles = sorted([
            pub["metadata"]["title"]
            for pub in data.get("publications", [])
        ])

        assert xml_titles == json_titles, (
            f"Title encoding mismatch between XML and JSON:\n"
            f"  XML:  {xml_titles}\n"
            f"  JSON: {json_titles}"
        )

    def test_xml_unicode_in_metadata_fields(self, client_with_titled_data, auth_headers, opds1_headers):
        """Unicode characters in title metadata survive XML rendering intact."""
        headers = {**auth_headers, **opds1_headers}
        response = client_with_titled_data.get(
            "/opds?path=Absolute Flash (2025)", headers=headers
        )
        root = ET.fromstring(response.content)
        titles = [
            entry.find(f"{{{ATOM_NS}}}title").text
            for entry in root.findall(f"{{{ATOM_NS}}}entry")
        ]

        # Issue 3 has em dash in the title itself ("Of Two Worlds — Part Three")
        em_dash_titles = [t for t in titles if "\u2014" in t]
        assert len(em_dash_titles) >= 1, (
            f"Expected at least one title with em dash (U+2014). "
            f"Titles found: {titles}"
        )

    def test_xml_feed_is_valid_utf8(self, client_with_titled_data, auth_headers, opds1_headers):
        """The entire XML response must be valid UTF-8."""
        headers = {**auth_headers, **opds1_headers}
        response = client_with_titled_data.get(
            "/opds?path=Absolute Flash (2025)", headers=headers
        )
        raw = response.content
        try:
            decoded = raw.decode("utf-8")
        except UnicodeDecodeError as e:
            pytest.fail(f"XML response is not valid UTF-8: {e}")

        # Check XML declaration claims utf-8
        assert b'encoding="utf-8"' in raw, (
            "XML declaration should specify encoding='utf-8'"
        )


# ─── Text normalization tests ──────────────────────────────────

@pytest.mark.unit
class TestTextNormalization:
    """Tests for _normalize_text used during metadata ingestion."""

    def test_nfc_normalization(self):
        """NFD-composed characters are normalized to NFC."""
        from app.main import _normalize_text
        # NFD: 'e' + combining acute accent (U+0301)
        nfd = "e\u0301"
        result = _normalize_text(nfd)
        assert result == "\u00e9"  # NFC: single codepoint é

    def test_nfc_preserves_already_nfc(self):
        """NFC text passes through unchanged."""
        from app.main import _normalize_text
        nfc = "caf\u00e9"
        assert _normalize_text(nfc) == "caf\u00e9"

    def test_cp1252_smart_quotes_mapped(self):
        """Windows-1252 C1 control chars are mapped to proper Unicode."""
        from app.main import _normalize_text
        # 0x93 = left double quote in CP1252, 0x94 = right double quote
        text = "\x93Hello\x94"
        result = _normalize_text(text)
        assert result == "\u201cHello\u201d"

    def test_cp1252_em_dash_mapped(self):
        """Windows-1252 em dash (0x97) is mapped to U+2014."""
        from app.main import _normalize_text
        text = "Part One \x97 The Beginning"
        result = _normalize_text(text)
        assert result == "Part One \u2014 The Beginning"

    def test_cp1252_ellipsis_mapped(self):
        """Windows-1252 ellipsis (0x85) is mapped to U+2026."""
        from app.main import _normalize_text
        text = "To be continued\x85"
        result = _normalize_text(text)
        assert result == "To be continued\u2026"

    def test_replacement_chars_stripped(self):
        """Unicode replacement characters from decoding errors are removed."""
        from app.main import _normalize_text
        text = "Batman \ufffd Returns"
        result = _normalize_text(text)
        assert result == "Batman  Returns"
        assert "\ufffd" not in result

    def test_ascii_unchanged(self):
        """Plain ASCII text passes through unchanged."""
        from app.main import _normalize_text
        text = "Action Comics #1"
        assert _normalize_text(text) == text

    def test_real_world_characters(self):
        """Common Unicode characters found in comic metadata survive normalization."""
        from app.main import _normalize_text
        chars = {
            "\u2014": "\u2014",  # em dash
            "\u2018": "\u2018",  # left single quote
            "\u2019": "\u2019",  # right single quote
            "\u201c": "\u201c",  # left double quote
            "\u201d": "\u201d",  # right double quote
            "\u2026": "\u2026",  # ellipsis
            "\u00e1": "\u00e1",  # á
            "\u00e9": "\u00e9",  # é
        }
        for inp, expected in chars.items():
            assert _normalize_text(inp) == expected
