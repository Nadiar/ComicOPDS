from datetime import datetime, timezone
from pathlib import Path

MIME_MAP = { ".cbz": "application/vnd.comicbook+zip" }

def now_rfc3339():
    """
    Get current UTC time in RFC3339 format.

    Returns:
        ISO 8601 formatted datetime string in UTC timezone
    """
    return datetime.now(timezone.utc).isoformat()

def mime_for(path: Path) -> str:
    """
    Get MIME type for file by extension.

    Args:
        path: File path

    Returns:
        MIME type string (e.g., "application/vnd.comicbook+zip" for .cbz, "application/octet-stream" as default)
    """
    return MIME_MAP.get(path.suffix.lower(), "application/octet-stream")
