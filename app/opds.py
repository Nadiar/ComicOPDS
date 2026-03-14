from datetime import datetime, timezone
from pathlib import Path

MIME_MAP = { ".cbz": "application/vnd.comicbook+zip" }

def now_rfc3339() -> str:
    """Get current UTC time in RFC3339 format."""
    return datetime.now(timezone.utc).isoformat()

def mime_for(path: Path) -> str:
    """Get MIME type for a file path based on extension."""
    return MIME_MAP.get(path.suffix.lower(), "application/octet-stream")
