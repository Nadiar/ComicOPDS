"""Tests for path-specific ETag and async thumbnail generation (Task 9)."""
from __future__ import annotations

import pytest
from unittest.mock import MagicMock

from app.feeds import opds_cache_headers, _entry_data_from_row


class TestPathSpecificETag:
    """ETag must include the request path so different feeds invalidate independently."""

    def test_different_paths_produce_different_etags(self):
        """Two requests with the same mtime but different paths must have different ETags."""
        mtime = 1700000000.0

        req_a = MagicMock()
        req_a.url.path = "/opds"
        req_a.url.query = "path=Comics/SeriesA"
        req_a.headers = {}

        req_b = MagicMock()
        req_b.url.path = "/opds"
        req_b.url.query = "path=Comics/SeriesB"
        req_b.headers = {}

        hdrs_a = opds_cache_headers(mtime, req_a)
        hdrs_b = opds_cache_headers(mtime, req_b)

        assert hdrs_a is not None
        assert hdrs_b is not None
        assert hdrs_a["ETag"] != hdrs_b["ETag"]

    def test_same_path_same_mtime_produces_same_etag(self):
        """Same path and mtime must produce the same ETag."""
        mtime = 1700000000.0

        req1 = MagicMock()
        req1.url.path = "/opds"
        req1.url.query = "path=Comics/SeriesA"
        req1.headers = {}

        req2 = MagicMock()
        req2.url.path = "/opds"
        req2.url.query = "path=Comics/SeriesA"
        req2.headers = {}

        hdrs1 = opds_cache_headers(mtime, req1)
        hdrs2 = opds_cache_headers(mtime, req2)

        assert hdrs1["ETag"] == hdrs2["ETag"]

    def test_matching_etag_returns_none_for_304(self):
        """When client sends matching ETag, return None (304 Not Modified)."""
        mtime = 1700000000.0

        # First get the ETag
        req = MagicMock()
        req.url.path = "/opds"
        req.url.query = "path=Comics/SeriesA"
        req.headers = {}
        hdrs = opds_cache_headers(mtime, req)
        etag = hdrs["ETag"]

        # Now send it back as If-None-Match
        req2 = MagicMock()
        req2.url.path = "/opds"
        req2.url.query = "path=Comics/SeriesA"
        req2.headers = {"if-none-match": etag}

        result = opds_cache_headers(mtime, req2)
        assert result is None

    def test_no_request_still_works(self):
        """opds_cache_headers works without a request (no path component)."""
        mtime = 1700000000.0
        hdrs = opds_cache_headers(mtime)
        assert hdrs is not None
        assert "ETag" in hdrs


class TestAsyncThumbnailGeneration:
    """Feed entry builders must always emit thumbnail URLs for CBZ files
    without blocking on synchronous thumbnail generation."""

    def test_entry_always_emits_thumb_url_for_cbz(self, monkeypatch):
        """CBZ entries must always have a thumbnail URL, even when no thumb exists yet."""
        from app import feeds
        from pathlib import Path

        # Mock LIBRARY_DIR
        monkeypatch.setattr(feeds, "LIBRARY_DIR", Path("/fake/library"))

        row = {
            "rel": "Series/Issue 001.cbz",
            "is_dir": 0,
            "name": "Issue 001.cbz",
            "title": "Issue 001",
            "series": "Series",
            "number": "1",
            "volume": None,
            "writer": "Author",
            "year": None,
            "month": None,
            "day": None,
            "summary": None,
            "genre": None,
            "tags": None,
            "characters": None,
            "teams": None,
            "locations": None,
            "ext": "cbz",
            "size": 1000,
            "mtime": 1700000000.0,
            "page_count": 10,
            "comicvineissue": None,
        }

        data = _entry_data_from_row(row)

        # Must always have thumb URL for CBZ, regardless of have_thumb/generate_thumb
        assert data["thumb_href_abs"] is not None
        assert "/thumb?path=" in data["thumb_href_abs"]
        assert data["image_abs"] is not None
