from __future__ import annotations

import logging
import os
import subprocess
import sys
import threading
import time
from functools import lru_cache
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from . import auth, db
from .config import (
    LIBRARY_DIR, PAGE_SIZE, SERVER_BASE, URL_PREFIX,
    PRECACHE_THUMBS, THUMB_WORKERS, PRECACHE_ON_START, AUTO_INDEX_ON_START, ENABLE_WATCH,
    PAGE_CACHE_TTL_DAYS, PAGE_CACHE_MAX_BYTES, PAGE_CACHE_AUTOCLEAN, PAGE_CACHE_CLEAN_INTERVAL_MIN,
    DB_MAINTENANCE_INTERVAL_MIN, LOG_AUTH,
)
from .feeds import abs_url
from .page_cache import autoclean_loop
from .routes_admin import router as admin_router
from .routes_opds import router as opds_router
from .scanning import (
    INDEX_STATUS, THUMB_STATUS,
    set_status, start_scan, run_scan, run_precache_thumbs, start_watcher,
)

# -------------------- Logging --------------------
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
app_logger = logging.getLogger("comicopds")
app_logger.setLevel(LOG_LEVEL)
_handler = logging.StreamHandler(sys.stdout)
_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
app_logger.handlers.clear()
app_logger.addHandler(_handler)
app_logger.propagate = False

try:
    _log_file = Path("/data/app.log")
    _log_file.parent.mkdir(parents=True, exist_ok=True)
    _file_handler = RotatingFileHandler(
        str(_log_file), maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8"
    )
    _file_handler.setLevel(logging.INFO if LOG_AUTH else logging.WARNING)
    _file_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    app_logger.addHandler(_file_handler)
except OSError:
    pass  # /data not available (dev environment)


def _mask_headers(h: dict) -> dict:
    masked = {}
    for k, v in h.items():
        if k.lower() in ("authorization", "cookie", "set-cookie", "x-api-key"):
            masked[k] = "***"
        else:
            masked[k] = v
    return masked

# -------------------- FastAPI & Jinja --------------------
app = FastAPI(title="ComicOPDS")
app.include_router(admin_router)
app.include_router(opds_router)


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    app_logger.error("Unhandled exception at %s: %s", request.url.path, exc, exc_info=True)
    return JSONResponse(status_code=500, content={"detail": "Internal server error"})

@app.middleware("http")
async def log_requests(request: Request, call_next):
    try:
        ua = request.headers.get("user-agent", "-")
        accept = request.headers.get("accept", "-")
        app_logger.info(
            f"--> {request.method} {request.url.path}"
            f"{'?' + str(request.url.query) if request.url.query else ''}"
            f"  UA={ua}  Accept={accept}"
        )
        app_logger.debug(f"    headers: {_mask_headers(dict(request.headers))}")
    except Exception:
        pass
    resp = await call_next(request)
    try:
        ct = resp.headers.get("content-type", "-")
        app_logger.info(
            f"<-- {resp.status_code} {request.method} {request.url.path}"
            f"  Content-Type={ct}"
        )
    except Exception:
        pass
    return resp

# -------------------- Small helpers --------------------

@lru_cache(maxsize=1)
def _git_commit() -> str | None:
    for name in ("GIT_COMMIT", "SOURCE_COMMIT", "COMMIT_SHA", "GITHUB_SHA"):
        value = (os.getenv(name) or "").strip()
        if value:
            return value[:12]

    repo_root = Path(__file__).resolve().parent.parent
    try:
        result = subprocess.run(
            ["git", "-C", str(repo_root), "rev-parse", "--short=12", "HEAD"],
            capture_output=True,
            text=True,
            check=False,
        )
    except Exception:
        return None

    if result.returncode == 0:
        commit = (result.stdout or "").strip()
        return commit or None
    return None

def _build_info() -> dict[str, Any]:
    return {
        "commit": _git_commit(),
        "server_base": SERVER_BASE,
        "url_prefix": URL_PREFIX,
        "opds2_manifest_path": abs_url("/opds/v2/manifest"),
    }

def _health_payload() -> dict[str, Any]:
    try:
        conn = db.connect()
        conn.execute("SELECT 1").fetchone()
        conn.close()
        db_ok = True
    except Exception:
        db_ok = False
    return {"ok": db_ok, "db": db_ok}

@app.on_event("startup")
def startup():
    if not LIBRARY_DIR.exists():
       raise RuntimeError(f"CONTENT_BASE_DIR does not exist: {LIBRARY_DIR}")

    # Ensure admin user exists
    db.seed_admin_user(auth.USER, auth.PASS)
    if auth.USER == "admin" and auth.PASS == "change-me":
        app_logger.warning("DEFAULT CREDENTIALS IN USE - set OPDS_BASIC_USER and OPDS_BASIC_PASS")

    # Show SQLite version + FTS status in logs
    conn = db.connect()
    try:
        sqlite_version = conn.execute("select sqlite_version()").fetchone()[0]
    finally:
        conn.close()
    app_logger.info(f"SQLite version: {sqlite_version}")
    app_logger.info(f"SQLite FTS5: {'ENABLED' if db.has_fts5() else 'DISABLED'}")
    build = _build_info()
    app_logger.info(f"Build commit: {build['commit'] or 'unknown'}")
    app_logger.info(f"OPDS 2 manifest path: {build['opds2_manifest_path']}")

    app_logger.info("=== ComicOPDS Configuration ===")
    app_logger.info(f"  Library dir: {LIBRARY_DIR}")
    app_logger.info(f"  Server base: {SERVER_BASE}")
    app_logger.info(f"  URL prefix: {URL_PREFIX or '(none)'}")
    app_logger.info(f"  Page size: {PAGE_SIZE}")
    app_logger.info(f"  Auth disabled: {auth.DISABLE_AUTH}")
    app_logger.info(f"  Auto-index on start: {AUTO_INDEX_ON_START}")
    app_logger.info(f"  Precache thumbs: {PRECACHE_THUMBS}")
    app_logger.info(f"  Thumb workers: {THUMB_WORKERS}")
    app_logger.info(f"  Watch enabled: {ENABLE_WATCH}")
    app_logger.info(f"  Log level: {LOG_LEVEL}")
    app_logger.info(f"  Auth logging (LOG_AUTH): {LOG_AUTH}")
    app_logger.info(f"  Log file: /data/app.log ({'INFO+' if LOG_AUTH else 'WARNING+'})")
    app_logger.info("===============================")

    if ENABLE_WATCH:
        start_watcher()

    # Always start the page cache cleaner first so it runs regardless of scan mode
    if PAGE_CACHE_AUTOCLEAN:
        t = threading.Thread(target=autoclean_loop, daemon=True)
        t.start()
        app_logger.info(f"Page cache auto-clean enabled: every {PAGE_CACHE_CLEAN_INTERVAL_MIN} min, "
                        f"ttl={PAGE_CACHE_TTL_DAYS}d, cap={PAGE_CACHE_MAX_BYTES} bytes")

    t = threading.Thread(target=db.maintenance_loop, args=(DB_MAINTENANCE_INTERVAL_MIN,), daemon=True)
    t.start()
    app_logger.info(f"DB maintenance enabled: every {DB_MAINTENANCE_INTERVAL_MIN} min")

    if AUTO_INDEX_ON_START:
        start_scan(force=True)
        return
    # Run thumbnails pre-cache at startup even if no scan runs
    if PRECACHE_ON_START and not INDEX_STATUS["running"] and not THUMB_STATUS["running"]:
        t = threading.Thread(target=run_precache_thumbs, args=(THUMB_WORKERS,), daemon=True)
        t.start()

    conn = db.connect()
    try:
        has_any = conn.execute("SELECT EXISTS(SELECT 1 FROM items LIMIT 1)").fetchone()[0] == 1
    finally:
        conn.close()

    if not has_any:
        start_scan(force=True)
    else:
        set_status(running=False, phase="idle", total=0, done=0, current="", ended_at=time.time())

# -------------------- Routes --------------------

_OPDS_ACCEPT = ("application/atom+xml", "application/opds+json", "application/json")
_BROWSER_UA = ("mozilla/", "chrome/", "safari/", "firefox/", "edg/", "opera/")

def _is_browser(request: Request) -> bool:
    """Detect browser clients by UA string and Accept header."""
    accept = request.headers.get("accept", "")
    if any(t in accept for t in _OPDS_ACCEPT):
        return False
    if "text/html" in accept:
        return True
    ua = request.headers.get("user-agent", "").lower()
    return any(tok in ua for tok in _BROWSER_UA)

@app.get("/")
def landing_page(request: Request):
    if not _is_browser(request):
        from .feeds import abs_url
        return RedirectResponse(abs_url("/opds"), status_code=302)
    from .feeds import env, BASE
    tpl = env.get_template("landing.html")
    return HTMLResponse(tpl.render(feed_url=f"{BASE}/opds", commit=_git_commit()))

@app.get("/healthz")
def health():
    return JSONResponse(_health_payload())


if __name__ == "__main__":
    import argparse
    import uvicorn

    parser = argparse.ArgumentParser(description="ComicOPDS Server")
    parser.add_argument("--scan-only", action="store_true", help="Run the library scanner and exit")
    args = parser.parse_args()

    # Pre-flight check: Connect to database to trigger the initial migrations
    db.connect().close()

    if args.scan_only:
        print("Running specific filesystem scan (--scan-only)...")
        run_scan()
        print("Success! Exiting.")
        sys.exit(0)

    uvicorn.run("app.main:app", host="0.0.0.0", port=8080, loop="asyncio")
