from __future__ import annotations

import email.utils
import hashlib
import json
import logging
import os
import re
import sys
import threading
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
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
from .config import LIBRARY_DIR, PAGE_SIZE, SERVER_BASE, URL_PREFIX, PRECACHE_THUMBS, THUMB_WORKERS, PRECACHE_ON_START, AUTO_INDEX_ON_START, _parse_bool
from .opds import now_rfc3339, mime_for
from .thumbs import have_thumb, generate_thumb

# -------------------- Logging --------------------
LOG_LEVEL = os.getenv("LOG_LEVEL", "ERROR").upper()
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

@app.middleware("http")
async def log_requests(request: Request, call_next):
    try:
        app_logger.info(f"--> {request.method} {request.url.path}?{request.url.query}")
        qp = dict(request.query_params)
        if qp:
            app_logger.info(f"    query: {qp}")
        app_logger.info(f"    headers: {_mask_headers(dict(request.headers))}")
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

def _opds_cache_headers(last_mod: float) -> dict:
    """HTTP caching headers for OPDS feed responses."""
    return {
        "Cache-Control": "private, max-age=60",
        "Last-Modified": email.utils.formatdate(last_mod, usegmt=True),
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
                    v = (el.text or "").strip()
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

def _run_scan():
    """Background scanner: writes into SQLite using its own connection."""
    conn = db.connect()
    try:
        db.begin_scan(conn)
        _set_status(running=True, phase="counting", done=0, total=0, current="", started_at=time.time(), ended_at=0.0)

        total = _count_cbz(LIBRARY_DIR)
        _set_status(total=total, phase="indexing")
        
        existing_items = db.get_existing_items_mtime(conn)
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
                
                if existing and existing[0] == float(mtime) and existing[1] == int(size):
                    # We have a matching file size & mtime, so skip the costly metadata parsing
                    _index_progress(rel)
                    continue

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

        if _uncommitted:
            conn.commit()
        db.cleanup_deleted_items(conn, current_rels)
        db.prune_stale(conn)

        # after scanning and pruning
        if PRECACHE_THUMBS:
            _set_status(phase="thumbnails")
            _run_precache_thumbs(THUMB_WORKERS)

        _set_status(phase="idle", running=False, ended_at=time.time(), current="")
    except Exception as e:
        app_logger.error(f"scan error: {e}")
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
@app.post("/admin/reindex")
def trigger_reindex(_=Depends(auth.require_admin)):
    """Trigger the background scanner to perform an incremental update."""
    if _INDEX_STATUS["running"]:
        return {"status": "already_running"}
        
    threading.Thread(target=_run_scan, daemon=True).start()
    return {"status": "started"}

def _start_scan(force=False):
    if not force and _INDEX_STATUS["running"]:
        return
    t = threading.Thread(target=_run_scan, daemon=True)
    t.start()

@app.get("/debug/fts")
def debug_fts(_=Depends(require_basic)):
    return {"fts5": db.has_fts5()}

@app.on_event("startup")
def startup():
    if not LIBRARY_DIR.exists():
       raise RuntimeError(f"CONTENT_BASE_DIR does not exist: {LIBRARY_DIR}")

    # Ensure admin user exists
    db.ensure_admin_user(auth.USER, auth.PASS)

    # Show SQLite version + FTS status in logs
    conn = db.connect()
    try:
        sqlite_version = conn.execute("select sqlite_version()").fetchone()[0]
    finally:
        conn.close()
    app_logger.info(f"SQLite version: {sqlite_version}")
    app_logger.info(f"SQLite FTS5: {'ENABLED' if db.has_fts5() else 'DISABLED'}")

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
          start_href_override: Optional[str] = None):
    tpl = env.get_template("feed.xml.j2")
    base = SERVER_BASE.rstrip("/")
    return tpl.render(
        feed_id=f"{base}{_abs_url(self_href)}",
        updated=now_rfc3339(),
        title=title,
        self_href=_abs_url(self_href),
        start_href=_abs_url(start_href_override or "/opds"),
        search_href=_abs_url(search_href),
        base=base,
        next_href=_abs_url(next_href) if next_href else None,
        entries=entries_xml,
        os_total=os_total,
        os_start=os_start,
        os_items=os_items,
    )

def _entry_xml_from_row(row) -> str:
    tpl = env.get_template("entry.xml.j2")
    base = SERVER_BASE.rstrip("/")

    if row["is_dir"]:
        href = f"/opds?path={quote(row['rel'])}" if row["rel"] else "/opds"
        return tpl.render(
            entry_id=f"{base}{_abs_url('/opds/' + quote(row['rel']))}",
            updated=now_rfc3339(),
            title=row["name"] or "/",
            is_dir=True,
            href_abs=f"{base}{_abs_url(href)}",
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
            p = have_thumb(rel, comicvine_issue)
            if p:
                image_abs = f"{base}{_abs_url('/thumb?path=' + quote(rel))}"
                thumb_href_abs = image_abs

        return tpl.render(
            entry_id=f"{base}{_abs_url(download_href)}",
            updated=now_rfc3339(),
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
def _prefers_opds2(request: Request) -> bool:
    if not request:
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
        pse_template = f"/pse/page?path={quote(rel)}&page={{pageNumber}}"
        page_count = int(rget(row, "page_count") or 0)

        comicvine_issue = rget(row, "comicvineissue")
        thumb_href_abs = None
        if (rget(row, "ext") or "").lower() == "cbz":
            p = have_thumb(rel, comicvine_issue)
            if p:
                thumb_href_abs = f"{base}{_abs_url('/thumb?path=' + quote(rel))}"

        pub = {
            "metadata": {
                "title": _display_title(row),
                "author": [{"name": a} for a in _authors_from_row(row)],
                "identifier": f"{base}{_abs_url(download_href)}",
                "modified": now_rfc3339()
            },
            "links": [
                {
                    "rel": "http://opds-spec.org/acquisition",
                    "href": f"{base}{_abs_url(download_href)}",
                    "type": mime_for(abs_file)
                },
                {
                    "rel": "http://vaemendis.net/opds-pse/stream",
                    "href": f"{base}{_abs_url(pse_template)}",
                    "type": "image/jpeg",
                    "properties": {
                        "numberOfItems": page_count
                    },
                    "templated": True
                }
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
          os_total: Optional[int] = None,
          os_start: Optional[int] = None,
          os_items: Optional[int] = None,
          search_href: str = "/opds/search.xml",
          start_href_override: Optional[str] = None) -> dict:
          
    base = SERVER_BASE.rstrip("/")
    feed = {
        "metadata": {
            "title": title,
            "modified": now_rfc3339()
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
    
    if next_href:
        feed["links"].append({"rel": "next", "href": f"{base}{_abs_url(next_href)}", "type": "application/opds+json"})
        
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
    return PlainTextResponse("ok")

@app.get("/opds", response_class=Response)
def browse(request: Request, path: str = Query("", description="Relative folder path"), page: int = 1, _=Depends(require_basic)):
    path = path.strip("/")
    conn = db.connect()
    try:
        total = db.children_count(conn, path)
        start = (page - 1) * PAGE_SIZE
        rows = db.children_page(conn, path, PAGE_SIZE, start)
        last_mod = db.last_modified(conn)
    finally:
        conn.close()

    cache_hdrs = _opds_cache_headers(last_mod)
    is_opds2 = _prefers_opds2(request)
    self_href = f"/opds?path={quote(path)}&page={page}" if path else f"/opds?page={page}"
    next_href = f"/opds?path={quote(path)}&page={page+1}" if (start + PAGE_SIZE) < total else None

    if is_opds2:
        row_dicts = [dict(r) for r in rows]
        if path == "" and page == 1:
            base = SERVER_BASE.rstrip("/")
            smart_href = _abs_url("/opds/smart")
            row_dicts.insert(0, {
                "is_smart": True,
                "title": "📁 Smart Lists",
                "href": f"{base}{smart_href}"
            })

        feed_dict = _feed_json(
            row_dicts,
            title=f"/{path}" if path else "Library",
            self_href=self_href,
            next_href=next_href
        )
        return JSONResponse(content=feed_dict, media_type="application/opds+json", headers=cache_hdrs)
    else:
        entries_xml = [_entry_xml_from_row(r) for r in rows]

        # "Smart Lists" virtual folder at root/page 1
        if path == "" and page == 1:
            tpl = env.get_template("entry.xml.j2")
            base = SERVER_BASE.rstrip("/")
            smart_href = _abs_url("/opds/smart")
            smart_entry = tpl.render(
                entry_id=f"{base}{smart_href}",
                updated=now_rfc3339(),
                title="📁 Smart Lists",
                is_dir=True,
                href_abs=f"{base}{smart_href}",
            )
            entries_xml = [smart_entry] + entries_xml

        xml = _feed(entries_xml, title=f"/{path}" if path else "Library", self_href=self_href, next_href=next_href)
        return Response(content=xml, media_type="application/atom+xml;profile=opds-catalog", headers=cache_hdrs)

@app.get("/", response_class=Response)
def root(request: Request, _=Depends(require_basic)):
    return browse(request=request, path="", page=1)

# ---- OpenSearch (descriptor) + Search results (OPDS 1.x) ----
@app.get("/opds/search.xml", response_class=Response)
def opensearch_description(_=Depends(require_basic)):
    tpl = env.get_template("search-description.xml.j2")
    xml = tpl.render(base=SERVER_BASE.rstrip("/"))
    return Response(content=xml, media_type="application/opensearchdescription+xml")

@app.get("/opds/search", response_class=Response)
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

    cache_hdrs = _opds_cache_headers(last_mod)
    self_href = f"/opds/search?query={quote(term)}&page={pg}"
    next_href = f"/opds/search?query={quote(term)}&page={pg+1}" if (offset + len(rows)) < total else None

    is_opds2 = _prefers_opds2(request)
    if is_opds2:
        row_dicts = [dict(r) for r in rows]
        feed_dict = _feed_json(
            row_dicts,
            title=f"Search: {term}",
            self_href=self_href,
            next_href=next_href,
            os_total=total,
            os_start=offset + 1 if total > 0 else 0,
            os_items=items,
            search_href="/opds/search.xml",
            start_href_override="/opds",
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
            search_href="/opds/search.xml",
            start_href_override="/opds",
        )
        return Response(content=xml, media_type="application/atom+xml;profile=opds-catalog", headers=cache_hdrs)

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
def download_head(path: str, _=Depends(require_basic)):
    p = _abspath(path)
    if not p.exists() or not p.is_file():
        raise HTTPException(404)
    st = p.stat()
    headers = _common_file_headers(p)
    headers["Content-Length"] = str(st.st_size)
    return Response(status_code=200, headers=headers)

@app.get("/download")
def download(path: str, request: Request, range: str | None = Header(default=None), _=Depends(require_basic)):
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
def stream_head(path: str, _=Depends(require_basic)):
    return download_head(path)

@app.get("/stream")
def stream(path: str, request: Request, range: str | None = Header(default=None), _=Depends(require_basic)):
    return download(path=path, request=request, range=range)

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

# -------------------- PSE endpoints --------------------
@app.get("/pse/stream", response_class=Response)
def pse_stream(path: str = Query(..., description="Relative path to CBZ"), _=Depends(require_basic)):
    """Optional: Atom feed per-pages (kept for compatibility)."""
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
    return Response(content=xml, media_type="application/atom+xml;profile=opds-catalog")

@app.get("/pse/page")
def pse_page(path: str = Query(...), page: int = Query(0, ge=0), _=Depends(require_basic)):
    """Serve page by ZERO-BASED index to match Panels (0 == first page)."""
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
def list_users(_=Depends(auth.require_admin)):
    conn = db.connect()
    try:
        rows = conn.execute("SELECT id, username, is_admin FROM users").fetchall()
        return [{"id": r["id"], "username": r["username"], "is_admin": bool(r["is_admin"])} for r in rows]
    finally:
        conn.close()

@app.post("/api/users")
def create_user(user: UserCreate, _=Depends(auth.require_admin)):
    import bcrypt
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
def update_user(user_id: int, user: UserUpdate, _=Depends(auth.require_admin)):
    import bcrypt
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
def delete_user(user_id: int, _=Depends(auth.require_admin)):
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
def opds_smart_lists(request: Request, _=Depends(require_basic)):
    lists = _load_smartlists()
    conn = db.connect()
    try:
        last_mod = db.last_modified(conn)
    finally:
        conn.close()
    cache_hdrs = _opds_cache_headers(last_mod)
    is_opds2 = _prefers_opds2(request)
    
    if is_opds2:
        row_dicts = []
        base = SERVER_BASE.rstrip("/")
        for sl in lists:
            href = f"/opds/smart/{quote(sl['slug'])}"
            row_dicts.append({
                "is_smart": True,
                "title": sl["name"],
                "href": f"{base}{_abs_url(href)}"
            })
        feed_dict = _feed_json(row_dicts, title="Smart Lists", self_href="/opds/smart")
        return JSONResponse(content=feed_dict, media_type="application/opds+json", headers=cache_hdrs)
    else:
        tpl = env.get_template("entry.xml.j2")
        entries = []
        for sl in lists:
            href = f"/opds/smart/{quote(sl['slug'])}"
            entries.append(
                tpl.render(
                    entry_id=f"{SERVER_BASE.rstrip('/')}{_abs_url(href)}",
                    updated=now_rfc3339(),
                    title=sl["name"],
                    is_dir=True,
                    href_abs=f"{SERVER_BASE.rstrip('/')}{_abs_url(href)}",
                )
            )
        xml = _feed(entries, title="Smart Lists", self_href="/opds/smart")
        return Response(content=xml, media_type="application/atom+xml;profile=opds-catalog", headers=cache_hdrs)

@app.get("/opds/smart/{slug}", response_class=Response)
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

    cache_hdrs = _opds_cache_headers(last_mod)

    # Total for navigation honors the hard cap
    total_for_nav = min(total, sl_limit) if sl_limit > 0 else total

    self_href = f"/opds/smart/{quote(slug)}?page={page}"
    next_href = None
    if (start + len(rows)) < total_for_nav:
        next_href = f"/opds/smart/{quote(slug)}?page={page+1}"

    is_opds2 = _prefers_opds2(request)
    if is_opds2:
        row_dicts = [dict(r) for r in rows]
        feed_dict = _feed_json(row_dicts, title=sl["name"], self_href=self_href, next_href=next_href)
        return JSONResponse(content=feed_dict, media_type="application/opds+json", headers=cache_hdrs)
    else:
        entries_xml = [_entry_xml_from_row(r) for r in rows]
        xml = _feed(entries_xml, title=sl["name"], self_href=self_href, next_href=next_href)
        return Response(content=xml, media_type="application/atom+xml;profile=opds-catalog", headers=cache_hdrs)

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
async def smartlists_post(request: Request, _=Depends(require_basic)):
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
def admin_reindex(_=Depends(require_basic)):
    _start_scan(force=True)
    return JSONResponse({"ok": True, "started": True})

@app.post("/admin/thumbs/precache", response_class=JSONResponse)
def admin_thumbs_precache(_=Depends(require_basic)):
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
def admin_pages_cleanup(_=Depends(require_basic)):
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
