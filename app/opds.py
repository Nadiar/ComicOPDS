from datetime import datetime, timezone
from pathlib import Path

MIME_MAP = {".cbz": "application/vnd.comicbook+zip"}


def now_rfc3339() -> str:
    return datetime.now(timezone.utc).isoformat()


def mtime_rfc3339(mtime: float) -> str:
    return datetime.fromtimestamp(mtime, tz=timezone.utc).isoformat()


def mime_for(path: Path) -> str:
    return MIME_MAP.get(path.suffix.lower(), "application/octet-stream")
