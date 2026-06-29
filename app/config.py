import os
from pathlib import Path

def _parse_bool(name: str, default: bool) -> bool:
    val = os.environ.get(name)
    if val is None:
        return default
    return val.strip().lower() in ("1", "true", "yes", "on")

LIBRARY_DIR = Path(os.environ.get("CONTENT_BASE_DIR", "/library")).resolve()
PAGE_SIZE = int(os.environ.get("PAGE_SIZE", "50"))

# Public base URL used to build absolute links in the OPDS feed
SERVER_BASE = os.environ.get("SERVER_BASE", "http://localhost:8080").rstrip("/")

# Optional path prefix if you serve the app under a subpath (e.g. /comics)
URL_PREFIX = os.environ.get("URL_PREFIX", "").rstrip("/")
if URL_PREFIX == "/":
    URL_PREFIX = ""

ENABLE_WATCH = _parse_bool("ENABLE_WATCH", True)
PRECACHE_THUMBS = _parse_bool("PRECACHE_THUMBS", False)
THUMB_WORKERS = max(1, int(os.getenv("THUMB_WORKERS", "2")))
PRECACHE_ON_START = _parse_bool("PRECACHE_ON_START", False)
AUTO_INDEX_ON_START = _parse_bool("AUTO_INDEX_ON_START", True)

# Default trusted proxy subnets are standard private boundaries.
TRUSTED_PROXIES_STR = os.getenv("TRUSTED_PROXIES", "10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16")

# Page cache configuration
PAGE_CACHE_DIR = Path(os.environ.get("PAGE_CACHE_DIR", "/data/pages"))
PAGE_CACHE_TTL_DAYS = int(os.environ.get("PAGE_CACHE_TTL_DAYS", "14"))
PAGE_CACHE_MAX_BYTES = int(os.environ.get("PAGE_CACHE_MAX_BYTES", str(10*1024*1024*1024)))
PAGE_CACHE_AUTOCLEAN = _parse_bool("PAGE_CACHE_AUTOCLEAN", True)
PAGE_CACHE_CLEAN_INTERVAL_MIN = int(os.environ.get("PAGE_CACHE_CLEAN_INTERVAL_MIN", "360"))

# SQLite maintenance (VACUUM + FTS optimize)
DB_MAINTENANCE_INTERVAL_MIN = int(os.environ.get("DB_MAINTENANCE_INTERVAL_MIN", "1440"))

LOG_AUTH = _parse_bool("LOG_AUTH", False)