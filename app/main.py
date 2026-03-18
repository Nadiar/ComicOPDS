from __future__ import annotations

import email.utils
import hashlib
import json
import logging
import os
import re
import subprocess
import sys
import threading
import time
import unicodedata
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from functools import lru_cache
from math import ceil
from pathlib import Path
from typing import List, Dict, Any, Optional
from urllib.parse import quote

from PIL import Image
from fastapi import FastAPI, Query, HTTPException, Request, Response, Depends, Header
from fastapi.responses import (
    StreamingResponse, FileResponse, PlainTextResponse, HTMLResponse, JSONResponse
)
from jinja2 import Environment, FileSystemLoader, select_autoescape

from . import auth, db
from .auth import require_basic
from .config import LIBRARY_DIR, PAGE_SIZE, SERVER_BASE, URL_PREFIX, PRECACHE_THUMBS, THUMB_WORKERS, PRECACHE_ON_START, AUTO_INDEX_ON_START, ENABLE_WATCH, _parse_bool
from .opds import now_rfc3339, mtime_rfc3339, mime_for
from .thumbs import have_thumb, generate_thumb

# -------------------- Logging --------------------
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
ERROR_LOG_PATH = Path("/data/thumbs_errors.log")
app_logger = logging.getLogger("comicopds")
app_logger.setLevel(LOG_LEVEL)
_handler = logging.StreamHandler(sys.stdout)
_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
app_logger.handlers.clear()
app_logger.addHandler(_handler)
app_logger.propagate = False

PAGE_CACHE_DIR = Path("/data/pages")
PAGE_CACHE_TTL_DAYS = int(os.getenv("PAGE_CACHE_TTL_DAYS", "14"))         # delete book caches idle > 14 days
PAGE_CACHE_MAX_BYTES = int(os.getenv("PAGE_CACHE_MAX_BYTES", str(10*1024*1024*1024)))  # 10 GiB cap by default
PAGE_CACHE_AUTOCLEAN = _parse_bool("PAGE_CACHE_AUTOCLEAN", True) # run background cleaner
PAGE_CACHE_CLEAN_INTERVAL_MIN = int(os.getenv("PAGE_CACHE_CLEAN_INTERVAL_MIN", "360")) # every 6h


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

env = Environment(
    loader=FileSystemLoader(str(Path(__file__).parent / "templates"), encoding="utf-8"),
    autoescape=select_autoescape(enabled_extensions=("xml", "html", "j2"), default=True),
)

OPDS_XML_MEDIA = "application/atom+xml;profile=opds-catalog;charset=utf-8"
OPDS_NAV_MEDIA = "application/atom+xml;profile=opds-catalog;kind=navigation"
OPDS_ACQ_MEDIA = "application/atom+xml;profile=opds-catalog;kind=acquisition"

def _xml_response(xml: str, headers: dict | None = None,
                  media_type: str = OPDS_XML_MEDIA) -> Response:
    """Return an XML Response with explicit UTF-8 encoding.

    Pre-encodes the string to bytes to avoid environment-dependent
    encoding issues (e.g. CP437 misinterpretation in Docker containers).
    """
    return Response(
        content=xml.encode("utf-8"),
        media_type=media_type,
        headers=headers,
    )

@app.middleware("http")
async def log_requests(request: Request, call_next):
    try:
        app_logger.info(f"--> {request.method} {request.url.path}?{request.url.query}")
        qp = dict(request.query_params)
        if qp:
            app_logger.info(f"    query: {qp}")
        app_logger.debug(f"    headers: {_mask_headers(dict(request.headers))}")
    except Exception:
        pass
    resp = await call_next(request)
    try:
        app_logger.info(f"<-- {request.method} {request.url.path} {resp.status_code}")
    except Exception:
        pass
    return resp

# -------------------- Thumbnail state (background) ----------------

_THUMB_STATUS = {
    "running": False,
    "total": 0,
    "done": 0,
    "started_at": 0.0,
    "ended_at": 0.0,
}
_THUMB_LOCK = threading.Lock()

# -------------------- Index state (background) --------------------
_INDEX_STATUS = {
    "running": False,
    "phase": "idle",
    "total": 0,
    "done": 0,
    "current": "",
    "started_at": 0.0,
    "ended_at": 0.0,
}
_INDEX_LOCK = threading.Lock()

# Mapping of Windows-1252 C1 control chars (0x80-0x9F) to their Unicode equivalents.
# These bytes are invalid in UTF-8 but sometimes appear when metadata tools
# write CP1252 text without proper encoding declaration.
_CP1252_MAP: dict[int, str] = {
    0x80: "\u20AC", 0x82: "\u201A", 0x83: "\u0192", 0x84: "\u201E",
    0x85: "\u2026", 0x86: "\u2020", 0x87: "\u2021", 0x88: "\u02C6",
    0x89: "\u2030", 0x8A: "\u0160", 0x8B: "\u2039", 0x8C: "\u0152",
    0x8E: "\u017D", 0x91: "\u2018", 0x92: "\u2019", 0x93: "\u201C",
    0x94: "\u201D", 0x95: "\u2022", 0x96: "\u2013", 0x97: "\u2014",
    0x98: "\u02DC", 0x99: "\u2122", 0x9A: "\u0161", 0x9B: "\u203A",
    0x9C: "\u0153", 0x9E: "\u017E", 0x9F: "\u0178",
}

def _normalize_text(text: str) -> str:
    """Normalize Unicode text for safe storage and XML/JSON output.

    Applies NFC normalization, maps stray Windows-1252 C1 control characters
    to their proper Unicode equivalents, and strips replacement characters.
    """
    # Map C1 control chars that may have leaked from CP1252 metadata
    text = text.translate({k: v for k, v in _CP1252_MAP.items()})
    # NFC: canonical decomposition then canonical composition
    text = unicodedata.normalize("NFC", text)
    # Strip replacement characters from decoding errors
    text = text.replace("\ufffd", "")
    return text

# -------------------- Small helpers --------------------
def rget(row, key: str, default=None):
    """Safe access for sqlite3.Row (no .get())."""
    try:
        val = row[key]
        return default if val in (None, "") else val
    except Exception:
        return default

def _abs_url(p: str) -> str:
    return (URL_PREFIX + p) if URL_PREFIX else p

def _opds_media_type(kind: str) -> str:
    return OPDS_ACQ_MEDIA if kind == "acquisition" else OPDS_NAV_MEDIA

@lru_cache(maxsize=1)
def _git_commit() -> Optional[str]:
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
        "opds2_manifest_path": _abs_url("/opds/v2/manifest"),
    }

def _health_payload() -> dict[str, Any]:
    return {"ok": True, **_build_info()}

def _opds_cache_headers(last_mod: float, request: Request | None = None) -> dict | None:
    """HTTP caching headers for OPDS feed responses.

    Returns None if the client sent a matching ETag (304 Not Modified).
    """
    etag = f'"opds-{int(last_mod)}"'
    if request:
        if_none_match = request.headers.get("if-none-match", "")
        if if_none_match == etag:
            return None  # signal caller to return 304
    return {
        "Cache-Control": "private, max-age=60",
        "Last-Modified": email.utils.formatdate(last_mod, usegmt=True),
        "ETag": etag,
    }

def _set_status(**kw):
    _INDEX_STATUS.update(kw)

def _count_cbz(root: Path) -> int:
    n = 0
    for p in root.rglob("*"):
        if p.is_file() and p.suffix.lower() == ".cbz":
            n += 1
    return n

def _parent_rel(rel: str) -> str:
    return "" if "/" not in rel else rel.rsplit("/", 1)[0]

def _read_comicinfo(cbz_path: Path) -> Dict[str, Any]:
    """Read ComicInfo.xml metadata from a CBZ file.

    Extracts metadata from the ComicInfo.xml file embedded in a CBZ archive.
    Returns an empty dictionary if the XML file is not found or parsing fails.

    Args:
        cbz_path: Path to the CBZ file to read metadata from.

    Returns:
        Dictionary with lowercase tag names as keys and text values from ComicInfo.xml.
        Returns empty dict if no ComicInfo.xml is found or if any error occurs.
    """
    from xml.etree import ElementTree as ET
    meta: Dict[str, Any] = {}
    try:
        with zipfile.ZipFile(cbz_path, "r") as zf:
            xml_name = None
            for n in zf.namelist():
                if n.lower().endswith("comicinfo.xml") and not n.endswith("/"):
                    xml_name = n
                    break
            if not xml_name:
                return meta
            with zf.open(xml_name) as fp:
                tree = ET.parse(fp)
                root = tree.getroot()
                for el in root:
                    k = el.tag.lower()
                    v = _normalize_text((el.text or "").strip())
                    if v:
                        meta[k] = v
                if "title" not in meta and "booktitle" in meta:
                    meta["title"] = meta.get("booktitle")
                for k in ("number", "volume", "year", "month", "day"):
                    if k in meta:
                        meta[k] = meta[k].strip()
    except Exception:
        pass
    return meta

def _index_progress(rel: str):
    _INDEX_STATUS["done"] += 1
    _INDEX_STATUS["current"] = rel

def _run_scan(force: bool = False):
    """Background scanner: writes into SQLite using its own connection."""
    app_logger.info(f"Starting {'full' if force else 'incremental'} filesystem scan of {LIBRARY_DIR}")
    conn = db.connect()
    try:
        db.begin_scan(conn)
        _set_status(running=True, phase="counting", done=0, total=0, current="", started_at=time.time(), ended_at=0.0)

        scan_stats = {"new": 0, "updated": 0, "skipped": 0, "errors": 0}

        app_logger.info("Counting CBZ files...")
        total = _count_cbz(LIBRARY_DIR)
        app_logger.info(f"Found {total} CBZ files, beginning index")
        _set_status(total=total, phase="indexing")

        existing_items = {} if force else db.get_existing_items_mtime(conn)
        current_rels = set()
        _uncommitted = 0

        for dirpath, dirnames, filenames in os.walk(LIBRARY_DIR):
            dpath = Path(dirpath)
            if dpath != LIBRARY_DIR:
                rel_d = dpath.relative_to(LIBRARY_DIR).as_posix()
                current_rels.add(rel_d)
                # Dirs are cheap so we can just blindly upsert them
                db.upsert_dir(
                    conn,
                    rel=rel_d,
                    name=dpath.name,
                    parent=_parent_rel(rel_d),
                    mtime=dpath.stat().st_mtime,
                )

            for fn in filenames:
                p = dpath / fn
                if p.suffix.lower() != ".cbz":
                    continue
                rel = p.relative_to(LIBRARY_DIR).as_posix()
                current_rels.add(rel)
                st = p.stat()
                mtime = st.st_mtime
                size = st.st_size

                # Check for existing matching item
                existing = existing_items.get(rel)

                if existing and existing[0] == float(mtime) and existing[1] == int(size) and existing[2] > 0:
                    # mtime+size match and page_count is populated — skip
                    scan_stats["skipped"] += 1
                    _index_progress(rel)
                    continue
                elif existing:
                    scan_stats["updated"] += 1
                else:
                    scan_stats["new"] += 1

                try:
                    page_count = len(_cbz_list_pages(p))
                except Exception:
                    page_count = 0

                db.upsert_file(
                    conn,
                    rel=rel,
                    name=p.stem,
                    size=size,
                    mtime=mtime,
                    parent=_parent_rel(rel),
                    ext="cbz",
                    page_count=page_count,
                )
                meta = _read_comicinfo(p)
                if meta:
                    db.upsert_meta(conn, rel=rel, meta=meta)

                _index_progress(rel)
                _uncommitted += 1
                if _uncommitted >= 100:
                    conn.commit()
                    _uncommitted = 0

                processed = scan_stats["new"] + scan_stats["updated"] + scan_stats["skipped"]
                if processed % 500 == 0:
                    app_logger.debug("scan progress: %d processed (%d new, %d updated, %d skipped)",
                                   processed, scan_stats["new"], scan_stats["updated"], scan_stats["skipped"])

        if _uncommitted:
            conn.commit()

        app_logger.info("Cleaning up deleted items...")
        removed = db.cleanup_deleted_items(conn, current_rels)
        if removed:
            app_logger.info("Removed %d deleted items from database", removed)
        db.prune_stale(conn)

        # after scanning and pruning
        if PRECACHE_THUMBS:
            app_logger.info("Pre-caching thumbnails...")
            _set_status(phase="thumbnails")
            _run_precache_thumbs(THUMB_WORKERS)

        scan_end = time.time()
        app_logger.info(
            "Scan completed in %.2fs: %d new, %d updated, %d skipped, %d errors",
            scan_end - _INDEX_STATUS.get('started_at', scan_end),
            scan_stats["new"], scan_stats["updated"],
            scan_stats["skipped"], scan_stats["errors"]
        )
        _set_status(phase="idle", running=False, ended_at=scan_end, current="")
    except Exception as e:
        app_logger.error(f"scan error: {e}", exc_info=True)
        _set_status(phase="idle", running=False, ended_at=time.time())
    finally:
        try:
            conn.close()
        except Exception:
            pass

def _collect_cbz_rows() -> list[dict]:
    """Fetch all file rows (is_dir=0, ext='cbz') with comicvineissue."""
    conn = db.connect()
    try:
        rows = conn.execute("""
            SELECT i.rel, i.ext, m.comicvineissue
              FROM items i
              LEFT JOIN meta m ON m.rel = i.rel
             WHERE i.is_dir=0 AND LOWER(i.ext)='cbz'
        """).fetchall()
        return [{"rel": r["rel"], "cvid": r["comicvineissue"]} for r in rows]
    finally:
        conn.close()

def _thumb_task(rel: str, cvid: str | None):
    try:
        ensure = generate_thumb  # we’ll call with abs path to avoid second stat
        abs_cbz = (LIBRARY_DIR / rel)
        if abs_cbz.exists():
            ensure(rel, abs_cbz, cvid)
    except Exception:
        pass
    finally:
        with _THUMB_LOCK:
            _THUMB_STATUS["done"] += 1

def _run_precache_thumbs(workers: int):
    with _THUMB_LOCK:
        _THUMB_STATUS.update({"running": True, "total": 0, "done": 0, "started_at": time.time(), "ended_at": 0.0})

    items = _collect_cbz_rows()
    total = len(items)
    with _THUMB_LOCK:
        _THUMB_STATUS["total"] = total

    if total == 0:
        with _THUMB_LOCK:
            _THUMB_STATUS.update({"running": False, "ended_at": time.time()})
        return

    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        futures = [pool.submit(_thumb_task, it["rel"], it["cvid"]) for it in items]
        for _ in as_completed(futures):
            pass

    with _THUMB_LOCK:
        _THUMB_STATUS.update({"running": False, "ended_at": time.time()})
def _start_scan(force=False):
    if _INDEX_STATUS["running"]:
        return
    t = threading.Thread(target=_run_scan, args=(force,), daemon=True)
    t.start()

@app.get("/debug/fts")
def debug_fts(_=Depends(require_basic)):
    return {"fts5": db.has_fts5()}

@app.on_event("startup")
def startup():
    if not LIBRARY_DIR.exists():
       raise RuntimeError(f"CONTENT_BASE_DIR does not exist: {LIBRARY_DIR}")

    # Ensure admin user exists
    db.seed_admin_user(auth.USER, auth.PASS)

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
    app_logger.info("===============================")

    # Always start the page cache cleaner first so it runs regardless of scan mode
    if PAGE_CACHE_AUTOCLEAN:
        t = threading.Thread(target=_autoclean_loop, daemon=True)
        t.start()
        app_logger.info(f"Page cache auto-clean enabled: every {PAGE_CACHE_CLEAN_INTERVAL_MIN} min, "
                        f"ttl={PAGE_CACHE_TTL_DAYS}d, cap={PAGE_CACHE_MAX_BYTES} bytes")

    if AUTO_INDEX_ON_START:
        _start_scan(force=True)
        return
    # Run thumbnails pre-cache at startup even if no scan runs
    if PRECACHE_ON_START and not _INDEX_STATUS["running"] and not _THUMB_STATUS["running"]:
        t = threading.Thread(target=_run_precache_thumbs, args=(THUMB_WORKERS,), daemon=True)
        t.start()

    conn = db.connect()
    try:
        has_any = conn.execute("SELECT EXISTS(SELECT 1 FROM items LIMIT 1)").fetchone()[0] == 1
    finally:
        conn.close()

    if not has_any:
        _start_scan(force=True)
    else:
        _set_status(running=False, phase="idle", total=0, done=0, current="", ended_at=time.time())

# -------------------- PSE (Page Streaming) helpers --------------------
PAGE_CACHE_DIR = Path("/data/pages")
VALID_PAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".tif", ".tiff"}

def _cbz_list_pages(cbz_path: Path) -> list[str]:
    with zipfile.ZipFile(cbz_path, "r") as zf:
        names = [n for n in zf.namelist() if Path(n).suffix.lower() in VALID_PAGE_EXTS and not n.endswith("/")]
    import re as _re
    def natkey(s: str):
        return [int(t) if t.isdigit() else t.lower() for t in _re.split(r"(\d+)", s)]
    names.sort(key=natkey)
    return names

def _book_cache_dir(rel_path: str) -> Path:
    h = hashlib.sha1(rel_path.encode("utf-8")).hexdigest()
    d = PAGE_CACHE_DIR / h
    d.mkdir(parents=True, exist_ok=True)
    return d

def _ensure_page_jpeg(cbz_path: Path, inner_name: str, dest: Path) -> Path:
    if dest.exists():
        return dest
    with zipfile.ZipFile(cbz_path, "r") as zf:
        with zf.open(inner_name) as fp:
            im = Image.open(fp)
            if im.mode not in ("RGB", "L"):
                im = im.convert("RGB")
            elif im.mode == "L":
                im = im.convert("RGB")
            dest.parent.mkdir(parents=True, exist_ok=True)
            im.save(dest, format="JPEG", quality=90, optimize=True)
    return dest

# -------------------- OPDS helpers (templating) --------------------
def _display_title(row) -> str:
    series = rget(row, "series")
    number = rget(row, "number")
    volume = rget(row, "volume")
    title = rget(row, "title") or rget(row, "name") or ""
    if series and number:
        vol = f" ({volume})" if volume else ""
        suffix = f" — {title}" if title and title != series else ""
        return f"{series}{vol} #{number}{suffix}"
    return title

def _authors_from_row(row) -> list[str]:
    authors = []
    v = rget(row, "writer")
    if v:
        authors.extend([x.strip() for x in v.split(",") if x.strip()])
    seen = set()
    out = []
    for a in authors:
        la = a.lower()
        if la in seen:
            continue
        seen.add(la)
        out.append(a)
    return out

def _issued_from_row(row) -> Optional[str]:
    y = rget(row, "year")
    if not y:
        return None
    try:
        m = int(rget(row, "month") or 1)
        d = int(rget(row, "day") or 1)
        return f"{int(y):04d}-{m:02d}-{d:02d}"
    except Exception:
        return None

def _categories_from_row(row) -> list[str]:
    cats = []
    for k in ("genre", "tags", "characters", "teams", "locations"):
        v = rget(row, k)
        if v:
            cats += [x.strip() for x in v.split(",") if x.strip()]
    seen = set()
    out = []
    for c in cats:
        lc = c.lower()
        if lc in seen:
            continue
        seen.add(lc)
        out.append(c)
    return out

def _feed(entries_xml: List[str], title: str, self_href: str,
          next_href: Optional[str] = None,
          os_total: Optional[int] = None,
          os_start: Optional[int] = None,
          os_items: Optional[int] = None,
          search_href: str = "/opds/search.xml",
          start_href_override: Optional[str] = None,
          updated: Optional[str] = None,
          self_type: str = OPDS_NAV_MEDIA,
          start_type: str = OPDS_NAV_MEDIA,
          next_type: Optional[str] = None):
    tpl = env.get_template("feed.xml.j2")
    base = SERVER_BASE.rstrip("/")
    return tpl.render(
        feed_id=f"{base}{_abs_url(self_href)}",
        updated=updated or now_rfc3339(),
        title=title,
        self_href=_abs_url(self_href),
        start_href=_abs_url(start_href_override or "/opds"),
        search_href=_abs_url(search_href),
        base=base,
        next_href=_abs_url(next_href) if next_href else None,
        self_type=self_type,
        start_type=start_type,
        next_type=next_type or self_type,
        entries=entries_xml,
        os_total=os_total,
        os_start=os_start,
        os_items=os_items,
    )

def _entry_xml_from_row(row, dir_link_type: str = OPDS_NAV_MEDIA) -> str:
    tpl = env.get_template("entry.xml.j2")
    base = SERVER_BASE.rstrip("/")

    if row["is_dir"]:
        href = f"/opds?path={quote(row['rel'])}" if row["rel"] else "/opds"
        return tpl.render(
            entry_id=f"{base}{_abs_url('/opds/' + quote(row['rel']))}",
            updated=mtime_rfc3339(row["mtime"]),
            title=row["name"] or "/",
            is_dir=True,
            href_abs=f"{base}{_abs_url(href)}",
            dir_link_type=dir_link_type,
        )
    else:
        rel = row["rel"]
        abs_file = LIBRARY_DIR / rel

        download_href = f"/download?path={quote(rel)}"
        stream_href = f"/stream?path={quote(rel)}"

        # PSE: template URL & count (Panels-compatible)
        pse_template = f"/pse/page?path={quote(rel)}&page={{pageNumber}}"
        page_count = int(rget(row, "page_count") or 0)

        comicvine_issue = rget(row, "comicvineissue")
        thumb_href_abs = None
        image_abs = None
        if (rget(row, "ext") or "").lower() == "cbz":
            p = have_thumb(rel, comicvine_issue) or generate_thumb(rel, abs_file, comicvine_issue)
            if p:
                image_abs = f"{base}{_abs_url('/thumb?path=' + quote(rel))}"
                thumb_href_abs = image_abs

        return tpl.render(
            entry_id=f"{base}{_abs_url(download_href)}",
            updated=mtime_rfc3339(row["mtime"]),
            title=_display_title(row),
            is_dir=False,
            download_href_abs=f"{base}{_abs_url(download_href)}",
            stream_href_abs=f"{base}{_abs_url(stream_href)}",
            pse_template_abs=f"{base}{_abs_url(pse_template)}",
            page_count=page_count,
            mime=mime_for(abs_file),
            size_str=f"{row['size']} bytes",
            thumb_href_abs=thumb_href_abs,
            image_abs=image_abs,
            authors=_authors_from_row(row),
            issued=_issued_from_row(row),
            summary=(rget(row, "summary") or None),
            categories=_categories_from_row(row),
        )

# -------------------- OPDS 2.0 helpers (JSON) --------------------
def _opds_base(request: Request) -> str:
    """Return the OPDS path base ('/opds', '/opds12', or '/opds20') from the request URL."""
    path = request.url.path
    if path.startswith(f"{URL_PREFIX}/opds20"):
        return "/opds20"
    if path.startswith(f"{URL_PREFIX}/opds12"):
        return "/opds12"
    return "/opds"

def _prefers_opds2(request: Request) -> bool:
    if not request:
        return False
    # Path-based override: /opds20/... forces OPDS 2.0, /opds12/... forces 1.2
    path = request.url.path
    if path.startswith(f"{URL_PREFIX}/opds20"):
        return True
    if path.startswith(f"{URL_PREFIX}/opds12"):
        return False
    accept_headers = request.headers.get("accept", "").split(",")
    for header in accept_headers:
        media_type = header.split(";")[0].strip().lower()
        if media_type in ("application/opds+json", "application/json"):
            return True
        elif media_type == "application/atom+xml":
            return False
    return False

def _entry_json_from_row(row) -> dict:
    base = SERVER_BASE.rstrip("/")
    if row["is_dir"]:
        href = f"/opds?path={quote(row['rel'])}" if row["rel"] else "/opds"
        return {
            "title": row["name"] or "/",
            "href": f"{base}{_abs_url(href)}",
            "type": "application/opds+json"
        }
    else:
        rel = row["rel"]
        abs_file = LIBRARY_DIR / rel

        download_href = f"/download?path={quote(rel)}"
        manifest_href = f"/opds/v2/manifest?path={quote(rel)}"
        page_count = int(rget(row, "page_count") or 0)

        comicvine_issue = rget(row, "comicvineissue")
        thumb_href_abs = None
        if (rget(row, "ext") or "").lower() == "cbz":
            p = have_thumb(rel, comicvine_issue) or generate_thumb(rel, abs_file, comicvine_issue)
            if p:
                thumb_href_abs = f"{base}{_abs_url('/thumb?path=' + quote(rel))}"

        meta: dict[str, Any] = {
            "title": _display_title(row),
            "author": [{"name": a} for a in _authors_from_row(row)],
            "identifier": f"{base}{_abs_url(download_href)}",
            "modified": mtime_rfc3339(row["mtime"]),
        }
        if page_count:
            meta["numberOfPages"] = page_count

        series = rget(row, "series")
        number = rget(row, "number")
        if series:
            belongs_to: dict[str, Any] = {"series": {"name": series}}
            if number:
                try:
                    belongs_to["series"]["position"] = float(number) if "." in number else int(number)
                except (ValueError, TypeError):
                    pass
            meta["belongsTo"] = belongs_to

        pub = {
            "metadata": meta,
            "links": [
                {
                    "rel": "self",
                    "href": f"{base}{_abs_url(manifest_href)}",
                    "type": "application/divina+json"
                },
                {
                    "rel": "http://opds-spec.org/acquisition",
                    "href": f"{base}{_abs_url(download_href)}",
                    "type": mime_for(abs_file)
                },
            ],
            "images": []
        }

        issued = _issued_from_row(row)
        if issued:
            pub["metadata"]["published"] = issued

        summary = rget(row, "summary")
        if summary:
            pub["metadata"]["description"] = summary

        cats = _categories_from_row(row)
        if cats:
            pub["metadata"]["subject"] = [{"name": c} for c in cats]

        if thumb_href_abs:
            pub["images"].append({
                "href": thumb_href_abs,
                "type": "image/jpeg",
                "rel": "http://opds-spec.org/image"
            })
            pub["images"].append({
                "href": thumb_href_abs,
                "type": "image/jpeg",
                "rel": "http://opds-spec.org/image/thumbnail"
            })

        return pub

def _feed_json(rows: list, title: str, self_href: str,
          next_href: Optional[str] = None,
          prev_href: Optional[str] = None,
          os_total: Optional[int] = None,
          os_start: Optional[int] = None,
          os_items: Optional[int] = None,
          search_href: str = "/opds/search.xml",
          start_href_override: Optional[str] = None,
          updated: Optional[str] = None) -> dict:

    base = SERVER_BASE.rstrip("/")
    feed = {
        "metadata": {
            "title": title,
            "modified": updated or now_rfc3339()
        },
        "links": [
            {"rel": "self", "href": f"{base}{_abs_url(self_href)}", "type": "application/opds+json"},
            {"rel": "start", "href": f"{base}{_abs_url(start_href_override or '/opds')}", "type": "application/opds+json"},
            {"rel": "search", "href": f"{base}{_abs_url(search_href)}", "type": "application/opensearchdescription+xml"}
        ],
        "navigation": [],
        "publications": []
    }

    if os_total is not None:
        feed["metadata"]["numberOfItems"] = os_total

    if os_items is not None:
        feed["metadata"]["itemsPerPage"] = os_items

    if next_href:
        feed["links"].append({"rel": "next", "href": f"{base}{_abs_url(next_href)}", "type": "application/opds+json"})

    if prev_href:
        feed["links"].append({"rel": "previous", "href": f"{base}{_abs_url(prev_href)}", "type": "application/opds+json"})

    for r in rows:
        if isinstance(r, dict) and 'is_smart' in r:
            feed["navigation"].append({"title": r["title"], "href": r["href"], "type": "application/opds+json", "rel": "subsection"})
            continue

        entry = _entry_json_from_row(r)
        if r.get("is_dir") if isinstance(r, dict) else r["is_dir"]:
            entry["rel"] = "subsection"
            feed["navigation"].append(entry)
        else:
            feed["publications"].append(entry)

    return feed

# -------------------- Routes --------------------
@app.get("/healthz")
def health():
    return JSONResponse(_health_payload())

@app.get("/debug/build", response_class=JSONResponse)
def debug_build(_=Depends(require_basic)):
    return JSONResponse(_build_info())

@app.get("/opds", response_class=Response)
@app.get("/opds12", response_class=Response)
@app.get("/opds20", response_class=Response)
def browse(request: Request, path: str = Query("", description="Relative folder path"), page: int = 1, _=Depends(require_basic)):
    path = path.strip("/")
    conn = db.connect()
    try:
        total = db.children_count(conn, path)
        start = (page - 1) * PAGE_SIZE
        rows = db.children_page(conn, path, PAGE_SIZE, start)
        last_mod = db.last_modified(conn)
        feed_kind = db.feed_kind(conn, path)
        dir_link_types = {
            r["rel"]: _opds_media_type(db.feed_kind(conn, r["rel"]))
            for r in rows if int(r["is_dir"]) == 1
        }
    finally:
        conn.close()

    cache_hdrs = _opds_cache_headers(last_mod, request)
    if cache_hdrs is None:
        return Response(status_code=304)
    updated_ts = mtime_rfc3339(last_mod)
    is_opds2 = _prefers_opds2(request)
    ob = _opds_base(request)
    self_href = f"{ob}?path={quote(path)}&page={page}" if path else f"{ob}?page={page}"
    next_href = f"{ob}?path={quote(path)}&page={page+1}" if (start + PAGE_SIZE) < total else None
    prev_href = f"{ob}?path={quote(path)}&page={page-1}" if page > 1 else None

    if is_opds2:
        row_dicts = [dict(r) for r in rows]
        if path == "" and page == 1:
            base = SERVER_BASE.rstrip("/")
            smart_href = _abs_url(f"{ob}/smart")
            row_dicts.insert(0, {
                "is_smart": True,
                "title": "📁 Smart Lists",
                "href": f"{base}{smart_href}"
            })

        feed_dict = _feed_json(
            row_dicts,
            title=f"/{path}" if path else "Library",
            self_href=self_href,
            next_href=next_href,
            prev_href=prev_href,
            os_total=total,
            os_start=start + 1 if total > 0 else 0,
            os_items=PAGE_SIZE,
            search_href=f"{ob}/search.xml",
            start_href_override=ob,
            updated=updated_ts,
        )
        return JSONResponse(content=feed_dict, media_type="application/opds+json", headers=cache_hdrs)
    else:
        entries_xml = [
            _entry_xml_from_row(r, dir_link_type=dir_link_types.get(r["rel"], OPDS_NAV_MEDIA))
            for r in rows
        ]

        # "Smart Lists" virtual folder at root/page 1
        if path == "" and page == 1:
            tpl = env.get_template("entry.xml.j2")
            base = SERVER_BASE.rstrip("/")
            smart_href = _abs_url(f"{ob}/smart")
            smart_entry = tpl.render(
                entry_id=f"{base}{smart_href}",
                updated=updated_ts,
                title="📁 Smart Lists",
                is_dir=True,
                href_abs=f"{base}{smart_href}",
                dir_link_type=OPDS_NAV_MEDIA,
            )
            entries_xml = [smart_entry] + entries_xml

        xml = _feed(entries_xml, title=f"/{path}" if path else "Library", self_href=self_href, next_href=next_href,
                   search_href=f"{ob}/search.xml", start_href_override=ob, updated=updated_ts,
                   self_type=_opds_media_type(feed_kind), start_type=OPDS_NAV_MEDIA,
                   next_type=_opds_media_type(feed_kind))
        return _xml_response(xml, headers=cache_hdrs)

@app.get("/", response_class=Response)
def root(request: Request, _=Depends(require_basic)):
    return browse(request=request, path="", page=1)

# ---- OpenSearch (descriptor) + Search results (OPDS 1.x) ----
@app.get("/opds/search.xml", response_class=Response)
@app.get("/opds12/search.xml", response_class=Response)
@app.get("/opds20/search.xml", response_class=Response)
def opensearch_description(request: Request, _=Depends(require_basic)):
    ob = _opds_base(request)
    tpl = env.get_template("search-description.xml.j2")
    xml = tpl.render(base=SERVER_BASE.rstrip("/"), opds_base=ob)
    return _xml_response(xml, media_type="application/opensearchdescription+xml;charset=utf-8")

@app.get("/opds/search", response_class=Response)
@app.get("/opds12/search", response_class=Response)
@app.get("/opds20/search", response_class=Response)
def opds_search(request: Request,
                query: str | None = Query(None, alias="query"),
                page: int | None = Query(None),
                _=Depends(require_basic)):
    term = (query or "").strip()
    if not term:
        return browse(request=request, path="", page=1)

    items = PAGE_SIZE
    pg = max(1, int(page or 1))
    offset = (pg - 1) * items

    conn = db.connect()
    try:
        rows = db.search_q(conn, term, items, offset)
        total = db.search_count(conn, term)
        last_mod = db.last_modified(conn)
    finally:
        conn.close()

    cache_hdrs = _opds_cache_headers(last_mod, request)
    if cache_hdrs is None:
        return Response(status_code=304)
    updated_ts = mtime_rfc3339(last_mod)
    ob = _opds_base(request)
    self_href = f"{ob}/search?query={quote(term)}&page={pg}"
    next_href = f"{ob}/search?query={quote(term)}&page={pg+1}" if (offset + len(rows)) < total else None
    prev_href = f"{ob}/search?query={quote(term)}&page={pg-1}" if pg > 1 else None

    is_opds2 = _prefers_opds2(request)
    if is_opds2:
        row_dicts = [dict(r) for r in rows]
        feed_dict = _feed_json(
            row_dicts,
            title=f"Search: {term}",
            self_href=self_href,
            next_href=next_href,
            prev_href=prev_href,
            os_total=total,
            os_start=offset + 1 if total > 0 else 0,
            os_items=items,
            search_href=f"{ob}/search.xml",
            start_href_override=ob,
            updated=updated_ts,
        )
        return JSONResponse(content=feed_dict, media_type="application/opds+json", headers=cache_hdrs)
    else:
        entries_xml = [_entry_xml_from_row(r) for r in rows]
        xml = _feed(
            entries_xml,
            title=f"Search: {term}",
            self_href=self_href,
            next_href=next_href,
            os_total=total,
            os_start=offset + 1 if total > 0 else 0,
            os_items=items,
            search_href=f"{ob}/search.xml",
            start_href_override=ob,
            updated=updated_ts,
            self_type=OPDS_ACQ_MEDIA,
            start_type=OPDS_NAV_MEDIA,
            next_type=OPDS_ACQ_MEDIA,
        )
        return _xml_response(xml, headers=cache_hdrs)

# -------------------- File endpoints --------------------
def _abspath(rel: str) -> Path:
    p = (LIBRARY_DIR / rel).resolve()
    if LIBRARY_DIR not in p.parents and p != LIBRARY_DIR:
        raise HTTPException(400, "Invalid path")
    return p

def _common_file_headers(p: Path) -> dict:
    return {
        "Accept-Ranges": "bytes",
        "Content-Type": mime_for(p),
        "Content-Disposition": f'inline; filename="{p.name}"',
    }

@app.head("/download")
def download_head(path: str, user: str = Depends(require_basic)):
    app_logger.debug("download HEAD: user=%s file=%s", user, path)
    p = _abspath(path)
    if not p.exists() or not p.is_file():
        raise HTTPException(404)
    st = p.stat()
    headers = _common_file_headers(p)
    headers["Content-Length"] = str(st.st_size)
    return Response(status_code=200, headers=headers)

@app.get("/download")
def download(path: str, request: Request, range: str | None = Header(default=None), user: str = Depends(require_basic)):
    app_logger.info("download: user=%s file=%s", user, path)
    p = _abspath(path)
    if not p.exists() or not p.is_file():
        raise HTTPException(404)

    file_size = p.stat().st_size
    headers = _common_file_headers(p)

    rng_header = range or request.headers.get("range")
    if not rng_header:
        headers["Content-Length"] = str(file_size)
        return FileResponse(p, headers=headers)

    try:
        unit, rngs = rng_header.split("=", 1)
        if unit.strip().lower() != "bytes":
            raise ValueError
        first_range = rngs.split(",")[0].strip()
        start_str, end_str = (first_range.split("-") + [""])[:2]

        if start_str == "" and end_str == "":
            raise ValueError

        if start_str == "":
            length = int(end_str)
            if length <= 0:
                raise ValueError
            start = max(file_size - length, 0)
            end = file_size - 1
        else:
            start = int(start_str)
            end = int(end_str) if end_str else (file_size - 1)

        if start < 0 or end < start or start >= file_size:
            raise ValueError

        end = min(end, file_size - 1)
    except Exception:
        raise HTTPException(
            status_code=416,
            detail="Invalid Range",
            headers={"Content-Range": f"bytes */{file_size}"},
        )

    def iter_file(fp: Path, s: int, e: int, chunk: int = 1024 * 1024):
        with fp.open("rb") as f:
            f.seek(s)
            remaining = e - s + 1
            while remaining > 0:
                data = f.read(min(chunk, remaining))
                if not data:
                    break
                remaining -= len(data)
                yield data

    part_len = end - start + 1
    headers.update({
        "Content-Range": f"bytes {start}-{end}/{file_size}",
        "Content-Length": str(part_len),
    })
    return StreamingResponse(iter_file(p, start, end), status_code=206, headers=headers)

@app.head("/stream")
def stream_head(path: str, user: str = Depends(require_basic)):
    app_logger.debug("stream HEAD: user=%s file=%s", user, path)
    return download_head(path, user=user)

@app.get("/stream")
def stream(path: str, request: Request, range: str | None = Header(default=None), user: str = Depends(require_basic)):
    app_logger.info("stream: user=%s file=%s", user, path)
    return download(path=path, request=request, range=range, user=user)

@app.get("/thumb")
def thumb(path: str, _=Depends(require_basic)):
    abs_p = _abspath(path)
    if not abs_p.exists() or not abs_p.is_file():
        raise HTTPException(404)

    conn = db.connect()
    try:
        row = db.get_item(conn, path)
    finally:
        conn.close()

    if not row:
        raise HTTPException(404)

    cvid = rget(row, "comicvineissue")
    p = have_thumb(path, cvid) or generate_thumb(path, abs_p, cvid)
    if not p or not p.exists():
        raise HTTPException(404, "No thumbnail")
    return FileResponse(p, media_type="image/jpeg")

# -------------------- DiViNa manifest (OPDS 2.0 page streaming) --------------------
DIVINA_PROFILE = "https://readium.org/webpub-manifest/profiles/divina"

@app.get("/opds/v2/manifest")
def divina_manifest(path: str = Query(..., description="Relative path to CBZ"), user: str = Depends(require_basic)):
    """Return a DiViNa (Readium Web Publication) manifest for page-level streaming.

    This is the OPDS 2.0-native mechanism for page streaming, replacing the
    OPDS-PSE 1.1 extension used in OPDS 1.2 Atom feeds.
    """
    app_logger.info("divina_manifest: user=%s file=%s", user, path)
    abs_cbz = _abspath(path)
    if not abs_cbz.exists() or not abs_cbz.is_file() or abs_cbz.suffix.lower() != ".cbz":
        raise HTTPException(404, "Book not found")

    conn = db.connect()
    try:
        row = db.get_item(conn, path)
    finally:
        conn.close()

    if not row:
        raise HTTPException(404, "Item not in index")

    base = SERVER_BASE.rstrip("/")
    pages = _cbz_list_pages(abs_cbz)
    page_count = len(pages)

    # Build readingOrder: one link per page image
    reading_order = []
    for i in range(page_count):
        page_link: dict[str, Any] = {
            "href": f"{base}{_abs_url(f'/pse/page?path={quote(path)}&page={i}')}",
            "type": "image/jpeg",
        }
        if i == 0:
            page_link["rel"] = "cover"
        reading_order.append(page_link)

    # Metadata
    meta: dict[str, Any] = {
        "title": _display_title(row),
        "conformsTo": DIVINA_PROFILE,
        "numberOfPages": page_count,
        "readingProgression": "ltr",
    }

    authors = _authors_from_row(row)
    if authors:
        meta["author"] = [{"name": a} for a in authors]

    series = rget(row, "series")
    number = rget(row, "number")
    if series:
        belongs_to: dict[str, Any] = {"series": {"name": series}}
        if number:
            try:
                belongs_to["series"]["position"] = float(number) if "." in number else int(number)
            except (ValueError, TypeError):
                pass
        meta["belongsTo"] = belongs_to

    issued = _issued_from_row(row)
    if issued:
        meta["published"] = issued

    summary = rget(row, "summary")
    if summary:
        meta["description"] = summary

    # Thumbnail resources
    resources = []
    cvid = rget(row, "comicvineissue")
    if (rget(row, "ext") or "").lower() == "cbz":
        p = have_thumb(path, cvid) or generate_thumb(path, abs_cbz, cvid)
        if p:
            resources.append({
                "rel": "cover",
                "href": f"{base}{_abs_url('/thumb?path=' + quote(path))}",
                "type": "image/jpeg",
            })

    self_href = f"{base}{_abs_url(f'/opds/v2/manifest?path={quote(path)}')}"

    manifest = {
        "@context": "https://readium.org/webpub-manifest/context.jsonld",
        "metadata": meta,
        "links": [
            {"rel": "self", "href": self_href, "type": "application/divina+json"}
        ],
        "readingOrder": reading_order,
    }
    if resources:
        manifest["resources"] = resources

    return JSONResponse(content=manifest, media_type="application/divina+json")

# -------------------- PSE endpoints --------------------
@app.get("/pse/stream", response_class=Response)
def pse_stream(path: str = Query(..., description="Relative path to CBZ"), user: str = Depends(require_basic)):
    """Optional: Atom feed per-pages (kept for compatibility)."""
    app_logger.info("pse_stream: user=%s file=%s", user, path)
    abs_cbz = _abspath(path)
    if not abs_cbz.exists() or not abs_cbz.is_file() or abs_cbz.suffix.lower() != ".cbz":
        raise HTTPException(404, "Book not found")

    pages = _cbz_list_pages(abs_cbz)
    page_entry_tpl = env.get_template("pse_page_entry.xml.j2")
    entries_xml = []
    for i, _name in enumerate(pages, start=1):
        page_href = _abs_url(f"/pse/page?path={quote(path)}&page={i}")
        entries_xml.append(
            page_entry_tpl.render(
                entry_id=f"{SERVER_BASE.rstrip('/')}{page_href}",
                updated=now_rfc3339(),
                title=f"Page {i}",
                page_href=page_href,
            )
        )

    pse_feed_tpl = env.get_template("pse_feed.xml.j2")
    self_href = f"/pse/stream?path={quote(path)}"
    xml = pse_feed_tpl.render(
        feed_id=f"{SERVER_BASE.rstrip('/')}{_abs_url(self_href)}",
        updated=now_rfc3339(),
        title=f"Pages — {Path(path).name}",
        self_href=_abs_url(self_href),
        start_href=_abs_url("/opds"),
        entries=entries_xml,
    )
    return _xml_response(xml)

@app.get("/pse/page")
def pse_page(path: str = Query(...), page: int = Query(0, ge=0), user: str = Depends(require_basic)):
    """Serve page by ZERO-BASED index to match Panels (0 == first page)."""
    app_logger.debug("pse_page: user=%s file=%s page=%d", user, path, page)
    abs_cbz = _abspath(path)
    if not abs_cbz.exists() or not abs_cbz.is_file():
        raise HTTPException(404, "Book not found")

    pages = _cbz_list_pages(abs_cbz)
    if not pages or page >= len(pages):
        raise HTTPException(404, "Page not found")

    inner = pages[page]  # zero-based
    cache_dir = _book_cache_dir(path)
    dest = cache_dir / f"{page+1:04d}.jpg"
    out = _ensure_page_jpeg(abs_cbz, inner, dest)
    # --- heartbeat: mark this book cache as recently used ---
    try:
        (cache_dir / ".last").touch()
    except Exception:
        pass
    return FileResponse(out, media_type="image/jpeg")

# -------- Page cache cleanup --------
_LAST_CACHE_CLEAN = {"ts": 0.0, "deleted_dirs": 0, "deleted_bytes": 0, "reason": ""}

def _dir_size(p: Path) -> int:
    total = 0
    for root, _, files in os.walk(p):
        for fn in files:
            try:
                total += (Path(root) / fn).stat().st_size
            except Exception:
                pass
    return total

def _book_cache_entries() -> list[tuple[Path, float, int]]:
    """
    Returns list of (dir_path, last_mtime, size_bytes) for each book cache dir.
    last_mtime prefers .last heartbeat; falls back to dir mtime.
    """
    entries = []
    if not PAGE_CACHE_DIR.exists():
        return entries
    for d in PAGE_CACHE_DIR.iterdir():
        if not d.is_dir():
            continue
        hb = d / ".last"
        try:
            last = hb.stat().st_mtime if hb.exists() else d.stat().st_mtime
        except Exception:
            last = 0.0
        try:
            sz = _dir_size(d)
        except Exception:
            sz = 0
        entries.append((d, last, sz))
    return entries

def _remove_dir(p: Path) -> int:
    """Remove directory tree, return bytes freed (best-effort)."""
    size = 0
    try:
        size = _dir_size(p)
    except Exception:
        pass
    try:
        for root, dirs, files in os.walk(p, topdown=False):
            for fn in files:
                try: (Path(root) / fn).unlink()
                except Exception: pass
            for dn in dirs:
                try: (Path(root) / dn).rmdir()
                except Exception: pass
        p.rmdir()
    except Exception:
        pass
    return size

def _clean_page_cache(ttl_days: int, max_bytes: int) -> dict:
    now = time.time()
    ttl_secs = max(0, int(ttl_days)) * 86400
    entries = _book_cache_entries()

    deleted_dirs = 0
    deleted_bytes = 0

    # 1) TTL eviction
    if ttl_secs > 0:
        for d, last, _sz in entries:
            if (now - last) > ttl_secs:
                deleted_bytes += _remove_dir(d)
                deleted_dirs += 1
        # refresh list after TTL deletes
        entries = _book_cache_entries()

    # 2) Size cap eviction
    total_bytes = sum(sz for _d, _last, sz in entries)
    if max_bytes > 0 and total_bytes > max_bytes:
        # sort by last mtime ascending (oldest first)
        entries.sort(key=lambda t: t[1])
        i = 0
        while total_bytes > max_bytes and i < len(entries):
            d, _last, sz = entries[i]
            total_bytes -= sz
            deleted_bytes += _remove_dir(d)
            deleted_dirs += 1
            i += 1

    _LAST_CACHE_CLEAN.update({"ts": now, "deleted_dirs": deleted_dirs, "deleted_bytes": deleted_bytes, "reason": "manual/auto"})
    return dict(_LAST_CACHE_CLEAN)

def _page_cache_status() -> dict:
    entries = _book_cache_entries()
    return {
        "dir_count": len(entries),
        "total_bytes": sum(sz for _d, _last, sz in entries),
        "last_clean": _LAST_CACHE_CLEAN,
        "ttl_days": PAGE_CACHE_TTL_DAYS,
        "max_bytes": PAGE_CACHE_MAX_BYTES,
    }

def _autoclean_loop():
    while True:
        try:
            _clean_page_cache(PAGE_CACHE_TTL_DAYS, PAGE_CACHE_MAX_BYTES)
        except Exception as e:
            app_logger.error(f"page cache autoclean error: {e}")
        # sleep
        interval = max(1, PAGE_CACHE_CLEAN_INTERVAL_MIN) * 60
        time.sleep(interval)


# ------------------------------------------------------------------------------
# User Administration UI
# ------------------------------------------------------------------------------

@app.get("/dashboard", response_class=HTMLResponse)
def dashboard_page(request: Request, user: str = Depends(auth.require_admin)):
    """The dashboard is now restricted to users with is_admin=1 (or the master ENV admin)"""
    tpl = env.get_template("dashboard.html")
    return HTMLResponse(tpl.render())

@app.get("/stats.json", response_class=JSONResponse)
def stats(_=Depends(require_basic)):
    conn = db.connect()
    try:
        return db.stats(conn)
    finally:
        conn.close()

# ------------------------------------------------------------------------------
# Admin API Routes (Users)
# ------------------------------------------------------------------------------
from pydantic import BaseModel
from typing import List
import sqlite3

class UserCreate(BaseModel):
    username: str
    password: str
    is_admin: bool = False

class UserUpdate(BaseModel):
    password: str = None
    is_admin: bool = None

@app.get("/api/users")
def list_users(admin: str = Depends(auth.require_admin)):
    app_logger.info("admin: list users by=%s", admin)
    conn = db.connect()
    try:
        rows = conn.execute("SELECT id, username, is_admin FROM users").fetchall()
        return [{"id": r["id"], "username": r["username"], "is_admin": bool(r["is_admin"])} for r in rows]
    finally:
        conn.close()

@app.post("/api/users")
def create_user(user: UserCreate, admin: str = Depends(auth.require_admin)):
    import bcrypt
    app_logger.info("admin: user created username=%s by=%s", user.username, admin)
    conn = db.connect()
    try:
        hashed = bcrypt.hashpw(user.password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')
        conn.execute(
            "INSERT INTO users (username, password_hash, is_admin) VALUES (?, ?, ?)",
            (user.username, hashed, 1 if user.is_admin else 0)
        )
        conn.commit()
        return {"status": "ok"}
    except sqlite3.IntegrityError:
        raise HTTPException(status_code=400, detail="Username already exists")
    finally:
        conn.close()

@app.put("/api/users/{user_id}")
def update_user(user_id: int, user: UserUpdate, admin: str = Depends(auth.require_admin)):
    import bcrypt
    app_logger.info("admin: user updated id=%d by=%s", user_id, admin)
    conn = db.connect()
    try:
        if user_id == 1:
            raise HTTPException(status_code=403, detail="Cannot modify the master Admin account from the UI. Change OPDS_BASIC_PASS in your docker environment.")

        updates = []
        params = []
        if user.password:
            hashed = bcrypt.hashpw(user.password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')
            updates.append("password_hash = ?")
            params.append(hashed)
        if user.is_admin is not None:
            updates.append("is_admin = ?")
            params.append(1 if user.is_admin else 0)

        if updates:
            params.append(user_id)
            conn.execute(f"UPDATE users SET {', '.join(updates)} WHERE id = ?", params)
            conn.commit()
        return {"status": "ok"}
    finally:
        conn.close()

@app.delete("/api/users/{user_id}")
def delete_user(user_id: int, admin: str = Depends(auth.require_admin)):
    app_logger.info("admin: user deleted id=%d by=%s", user_id, admin)
    conn = db.connect()
    try:
        if user_id == 1:
            raise HTTPException(status_code=403, detail="Cannot delete the master Admin account.")
        conn.execute("DELETE FROM users WHERE id = ?", (user_id,))
        conn.commit()
        return {"status": "ok"}
    finally:
        conn.close()

# ------------------------------------------------------------------------------
# Image Serving
# ------------------------------------------------------------------------------

# -------------------- Debug --------------------
@app.get("/debug/children", response_class=JSONResponse)
def debug_children(path: str = "", _=Depends(require_basic)):
    conn = db.connect()
    try:
        rows = db.children_page(conn, path.strip("/"), 1000, 0)
    finally:
        conn.close()
    return JSONResponse([{"rel": r["rel"], "is_dir": int(r["is_dir"]), "name": r["name"]} for r in rows])

# -------------------- Smart Lists --------------------
SMARTLISTS_PATH = Path("/data/smartlists.json")

def _slugify(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (name or "").lower()).strip("-") or "list"

def _load_smartlists() -> list[dict]:
    if SMARTLISTS_PATH.exists():
        try:
            return json.loads(SMARTLISTS_PATH.read_text(encoding="utf-8"))
        except Exception:
            return []
    return []

def _save_smartlists(lists: list[dict]) -> None:
    SMARTLISTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    SMARTLISTS_PATH.write_text(json.dumps(lists, ensure_ascii=False, indent=0), encoding="utf-8")

@app.get("/opds/smart", response_class=Response)
@app.get("/opds12/smart", response_class=Response)
@app.get("/opds20/smart", response_class=Response)
def opds_smart_lists(request: Request, _=Depends(require_basic)):
    lists = _load_smartlists()
    conn = db.connect()
    try:
        last_mod = db.last_modified(conn)
    finally:
        conn.close()
    cache_hdrs = _opds_cache_headers(last_mod, request)
    if cache_hdrs is None:
        return Response(status_code=304)
    updated_ts = mtime_rfc3339(last_mod)
    is_opds2 = _prefers_opds2(request)
    ob = _opds_base(request)

    if is_opds2:
        row_dicts = []
        base = SERVER_BASE.rstrip("/")
        for sl in lists:
            href = f"{ob}/smart/{quote(sl['slug'])}"
            row_dicts.append({
                "is_smart": True,
                "title": sl["name"],
                "href": f"{base}{_abs_url(href)}"
            })
        feed_dict = _feed_json(row_dicts, title="Smart Lists", self_href=f"{ob}/smart",
                               search_href=f"{ob}/search.xml", start_href_override=ob,
                               updated=updated_ts)
        return JSONResponse(content=feed_dict, media_type="application/opds+json", headers=cache_hdrs)
    else:
        tpl = env.get_template("entry.xml.j2")
        entries = []
        for sl in lists:
            href = f"{ob}/smart/{quote(sl['slug'])}"
            entries.append(
                tpl.render(
                    entry_id=f"{SERVER_BASE.rstrip('/')}{_abs_url(href)}",
                    updated=updated_ts,
                    title=sl["name"],
                    is_dir=True,
                    href_abs=f"{SERVER_BASE.rstrip('/')}{_abs_url(href)}",
                )
            )
        xml = _feed(entries, title="Smart Lists", self_href=f"{ob}/smart",
                   search_href=f"{ob}/search.xml", start_href_override=ob,
                   updated=updated_ts)
        return _xml_response(xml, headers=cache_hdrs)

@app.get("/opds/smart/{slug}", response_class=Response)
@app.get("/opds12/smart/{slug}", response_class=Response)
@app.get("/opds20/smart/{slug}", response_class=Response)
def opds_smart_list(request: Request, slug: str, page: int = 1, _=Depends(require_basic)):
    lists = _load_smartlists()
    sl = next((x for x in lists if x.get("slug") == slug), None)
    if not sl:
        raise HTTPException(404, "Smart list not found")

    groups = sl.get("groups") or []
    sort = (sl.get("sort") or "issued_desc").lower()

    # Distinct handling (series+volume) + mode
    distinct_by   = (sl.get("distinct_by") or "").strip().lower()
    distinct_mode = (sl.get("distinct_mode") or "latest").strip().lower()
    distinct_flag = distinct_mode if distinct_by == "series_volume" else False  # db.smartlist_query expects False | "latest" | "oldest"

    # Hard cap per list
    sl_limit = int(sl.get("limit") or 0)

    # paging
    page = max(1, int(page))
    page_size = PAGE_SIZE
    start = (page - 1) * page_size

    # effective page size when a hard cap exists
    effective_page_size = page_size if sl_limit == 0 else max(0, min(page_size, sl_limit - start))

    conn = db.connect()
    try:
        rows = db.smartlist_query(conn, groups, sort, effective_page_size, start, distinct_flag)
        total = db.smartlist_count(conn, groups)
        last_mod = db.last_modified(conn)
    finally:
        conn.close()

    cache_hdrs = _opds_cache_headers(last_mod, request)
    if cache_hdrs is None:
        return Response(status_code=304)
    updated_ts = mtime_rfc3339(last_mod)

    # Total for navigation honors the hard cap
    total_for_nav = min(total, sl_limit) if sl_limit > 0 else total

    ob = _opds_base(request)
    self_href = f"{ob}/smart/{quote(slug)}?page={page}"
    next_href = None
    if (start + len(rows)) < total_for_nav:
        next_href = f"{ob}/smart/{quote(slug)}?page={page+1}"
    prev_href = f"{ob}/smart/{quote(slug)}?page={page-1}" if page > 1 else None

    is_opds2 = _prefers_opds2(request)
    if is_opds2:
        row_dicts = [dict(r) for r in rows]
        feed_dict = _feed_json(
            row_dicts,
            title=sl["name"],
            self_href=self_href,
            next_href=next_href,
            prev_href=prev_href,
            os_total=total_for_nav,
            os_start=start + 1 if total_for_nav > 0 else 0,
            os_items=PAGE_SIZE,
            search_href=f"{ob}/search.xml",
            start_href_override=ob,
            updated=updated_ts,
        )
        return JSONResponse(content=feed_dict, media_type="application/opds+json", headers=cache_hdrs)
    else:
        entries_xml = [_entry_xml_from_row(r) for r in rows]
        xml = _feed(entries_xml, title=sl["name"], self_href=self_href, next_href=next_href,
                   search_href=f"{ob}/search.xml", start_href_override=ob, updated=updated_ts)
        return _xml_response(xml, headers=cache_hdrs)

@app.get("/search", response_class=HTMLResponse)
def smartlists_page(_=Depends(require_basic)):
    tpl = env.get_template("smartlists.html")
    return HTMLResponse(tpl.render())

def _smartlists_load():
    if SMARTLISTS_PATH.exists():
        try:
            with SMARTLISTS_PATH.open("r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict) and "lists" in data and isinstance(data["lists"], list):
                return data["lists"]
            if isinstance(data, list):
                return data
        except Exception:
            pass
    return []

def _smartlists_save(lists):
    SMARTLISTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    with SMARTLISTS_PATH.open("w", encoding="utf-8") as f:
        json.dump(lists, f, ensure_ascii=False, indent=2)

@app.get("/smartlists.json", response_class=JSONResponse)
def smartlists_get(_=Depends(require_basic)):
    """Return the raw JSON array of smart lists (or [] if none)."""
    return JSONResponse(_smartlists_load())

@app.post("/smartlists.json", response_class=JSONResponse)
async def smartlists_post(request: Request, admin: str = Depends(auth.require_admin)):
    app_logger.info("admin: smart lists updated by=%s", admin)
    raw = await request.body()
    if not raw:
        return JSONResponse({"ok": False, "error": "empty body"}, status_code=400)

    try:
        data = json.loads(raw.decode("utf-8"))
    except Exception as e:
        return JSONResponse({"ok": False, "error": f"invalid json: {e}"}, status_code=400)

    if isinstance(data, dict) and "lists" in data and isinstance(data["lists"], list):
        lists = data["lists"]
    elif isinstance(data, dict):
        lists = [data]
    elif isinstance(data, list):
        lists = data
    else:
        return JSONResponse({"ok": False, "error": "expected JSON array or object"}, status_code=400)

    try:
        _smartlists_save(lists)
    except Exception as e:
        return JSONResponse({"ok": False, "error": f"write failed: {e}"}, status_code=500)

    return JSONResponse({"ok": True, "saved": len(lists)})



# -------------------- Index status & Reindex --------------------
@app.get("/index/status", response_class=JSONResponse)
def index_status(_=Depends(require_basic)):
    conn = db.connect()
    try:
        usable = conn.execute("SELECT EXISTS(SELECT 1 FROM items LIMIT 1)").fetchone()[0] == 1
    finally:
        conn.close()
    return JSONResponse({**_INDEX_STATUS, "usable": usable})

@app.post("/admin/reindex", response_class=JSONResponse)
def admin_reindex(force: bool = Query(False), admin: str = Depends(auth.require_admin)):
    app_logger.info("admin: reindex triggered by=%s force=%s", admin, force)
    if _INDEX_STATUS["running"]:
        return JSONResponse({"ok": True, "started": False, "reason": "already running"})
    _start_scan(force=force)
    return JSONResponse({"ok": True, "started": True, "mode": "full" if force else "incremental"})

@app.post("/admin/reindex/path", response_class=JSONResponse)
def admin_reindex_path(path: str = Query(..., description="Relative path to a file or folder inside the library"),
                       admin: str = Depends(auth.require_admin)):
    """Index or re-index a specific file or folder by relative path."""
    app_logger.info("admin: path reindex triggered by=%s path=%s", admin, path)
    abs_path = (LIBRARY_DIR / path).resolve()
    # Prevent path traversal outside library
    try:
        abs_path.relative_to(LIBRARY_DIR.resolve())
    except ValueError:
        raise HTTPException(status_code=400, detail="Path is outside the library directory")
    if not abs_path.exists():
        raise HTTPException(status_code=404, detail="Path not found")

    conn = db.connect()
    try:
        indexed = 0
        if abs_path.is_file():
            if abs_path.suffix.lower() != ".cbz":
                raise HTTPException(status_code=400, detail="Only .cbz files can be indexed")
            indexed = _index_single_file(conn, abs_path)
        else:
            # Walk the directory tree
            for dirpath, _dirnames, filenames in os.walk(abs_path):
                dpath = Path(dirpath)
                if dpath != LIBRARY_DIR:
                    rel_d = dpath.relative_to(LIBRARY_DIR).as_posix()
                    db.upsert_dir(conn, rel=rel_d, name=dpath.name,
                                  parent=_parent_rel(rel_d), mtime=dpath.stat().st_mtime)
                for fn in filenames:
                    p = dpath / fn
                    if p.suffix.lower() == ".cbz":
                        indexed += _index_single_file(conn, p)
        conn.commit()
    finally:
        conn.close()

    app_logger.info("admin: path reindex completed path=%s indexed=%d", path, indexed)
    return JSONResponse({"ok": True, "path": path, "indexed": indexed})

def _index_single_file(conn, abs_path: Path) -> int:
    """Index a single CBZ file into the database. Returns 1 on success, 0 on error."""
    rel = abs_path.relative_to(LIBRARY_DIR).as_posix()
    st = abs_path.stat()
    try:
        page_count = len(_cbz_list_pages(abs_path))
    except Exception:
        page_count = 0
    db.upsert_file(conn, rel=rel, name=abs_path.stem, size=st.st_size,
                   mtime=st.st_mtime, parent=_parent_rel(rel), ext="cbz",
                   page_count=page_count)
    meta = _read_comicinfo(abs_path)
    if meta:
        db.upsert_meta(conn, rel=rel, meta=meta)
    return 1

@app.post("/admin/thumbs/precache", response_class=JSONResponse)
def admin_thumbs_precache(admin: str = Depends(auth.require_admin)):
    app_logger.info("admin: thumbnail precache triggered by=%s", admin)
    if _THUMB_STATUS["running"]:
        return JSONResponse({"ok": True, "started": False, "reason": "already running"})
    t = threading.Thread(target=_run_precache_thumbs, args=(THUMB_WORKERS,), daemon=True)
    t.start()
    return JSONResponse({"ok": True, "started": True})

@app.get("/thumbs/status", response_class=JSONResponse)
def thumbs_status(_=Depends(require_basic)):
    return JSONResponse(_THUMB_STATUS)

# -------------------- Thumbs Errors --------------------

@app.get("/thumbs/errors/count", response_class=JSONResponse)
def thumbs_errors_count(_=Depends(require_basic)):
    n = 0
    size = 0
    mtime = 0.0
    if ERROR_LOG_PATH.exists():
        try:
            with ERROR_LOG_PATH.open("rb") as f:
                n = sum(1 for _ in f)
            st = ERROR_LOG_PATH.stat()
            size = st.st_size
            mtime = st.st_mtime
        except Exception:
            pass
    return {"lines": n, "size_bytes": size, "modified": mtime}

@app.get("/thumbs/errors/log")
def thumbs_errors_log(_=Depends(require_basic)):
    if not ERROR_LOG_PATH.exists():
        # return an empty text file to keep the link working
        return PlainTextResponse("", media_type="text/plain", headers={
            "Content-Disposition": "attachment; filename=thumbs_errors.log"
        })
    return FileResponse(
        path=str(ERROR_LOG_PATH),
        media_type="text/plain",
        filename="thumbs_errors.log",
        headers={"Cache-Control": "no-store"}
    )

@app.get("/pages/cache/status", response_class=JSONResponse)
def pages_cache_status(_=Depends(require_basic)):
    return JSONResponse(_page_cache_status())

@app.post("/admin/pages/cleanup", response_class=JSONResponse)
def admin_pages_cleanup(admin: str = Depends(auth.require_admin)):
    app_logger.info("admin: page cache cleanup triggered by=%s", admin)
    res = _clean_page_cache(PAGE_CACHE_TTL_DAYS, PAGE_CACHE_MAX_BYTES)
    return JSONResponse({"ok": True, **res})


if __name__ == "__main__":
    import uvicorn
    import sys
    import argparse

    parser = argparse.ArgumentParser(description="ComicOPDS Server")
    parser.add_argument("--scan-only", action="store_true", help="Run the library scanner and exit")
    args = parser.parse_args()

    # Pre-flight check: Connect to database to trigger the initial migrations
    db.connect().close()

    if args.scan_only:
        print("Running specific filesystem scan (--scan-only)...")
        _run_scan()
        print("Success! Exiting.")
        sys.exit(0)

    # Note that `from app.config import SERVER_PORT` could be used here but the existing codebase launches with CLI uvicorn rather than this block directly.
    # Uvicorn does run this block if called via `python main.py` or similar.
    # However we will use the standard default for testing:
    uvicorn.run("app.main:app", host="0.0.0.0", port=8080, loop="asyncio")
