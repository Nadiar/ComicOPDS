import os
from pathlib import Path

def _env_bool(name: str, default: bool) -> bool:
    val = os.environ.get(name)
    if val is None:
        return default
    return val.strip().lower() in ("true", "yes", "on")

LIBRARY_DIR = Path(os.environ.get("CONTENT_BASE_DIR", "/library")).resolve()
PAGE_SIZE = int(os.environ.get("PAGE_SIZE", "50"))

# Public base URL used to build absolute links in the OPDS feed
SERVER_BASE = os.environ.get("SERVER_BASE", "http://localhost:8080").rstrip("/")

# Optional path prefix if you serve the app under a subpath (e.g. /comics)
URL_PREFIX = os.environ.get("URL_PREFIX", "").rstrip("/")
if URL_PREFIX == "/":
    URL_PREFIX = ""

ENABLE_WATCH = _env_bool("ENABLE_WATCH", True)
