# tests/test_opds_utils.py
import pytest
from datetime import datetime, timezone
from app.opds import now_rfc3339, mime_for
from pathlib import Path


@pytest.mark.unit
class TestRFC3339:
    """Tests for RFC3339 timestamp formatting."""

    def test_now_rfc3339_returns_iso_format(self):
        """now_rfc3339 returns valid RFC3339 formatted timestamp."""
        result = now_rfc3339()
        assert isinstance(result, str)
        assert 'T' in result
        assert '+' in result or 'Z' in result or result.endswith('+00:00')

    def test_now_rfc3339_is_valid_iso8601(self):
        """Returned timestamp can be parsed as ISO8601."""
        result = now_rfc3339()
        # Should not raise
        datetime.fromisoformat(result.replace('Z', '+00:00'))

    def test_now_rfc3339_timezone_is_utc(self):
        """Timestamp should indicate UTC timezone."""
        result = now_rfc3339()
        assert result.endswith('+00:00') or 'Z' in result


@pytest.mark.unit
class TestMimeType:
    """Tests for MIME type detection."""

    def test_mime_for_cbz(self):
        """CBZ files return comic book MIME type."""
        result = mime_for(Path("test.cbz"))
        assert result == "application/vnd.comicbook+zip"

    def test_mime_for_cbz_case_insensitive(self):
        """MIME type detection is case-insensitive."""
        assert mime_for(Path("test.CBZ")) == "application/vnd.comicbook+zip"
        assert mime_for(Path("test.Cbz")) == "application/vnd.comicbook+zip"

    def test_mime_for_unknown_extension(self):
        """Unknown extensions return generic octet-stream."""
        result = mime_for(Path("test.unknown"))
        assert result == "application/octet-stream"

    def test_mime_for_no_extension(self):
        """Files without extension return octet-stream."""
        result = mime_for(Path("testfile"))
        assert result == "application/octet-stream"
