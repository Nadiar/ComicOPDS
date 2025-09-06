from __future__ import annotations

from fastapi import FastAPI, Query, HTTPException, Request, Response, Depends, Header
from fastapi.responses import (
    StreamingResponse, FileResponse, PlainTextResponse, HTMLResponse, JSONResponse
)
from pathlib import Path
from typing import List, Dict, Any, Optional
from jinja2 import Environment, FileSystemLoader, select_autoescape
from urllib.parse import quote
import threading
import time
import os
import re
import json
import zipfile
import hashlib
from PIL import Image

from .config import LIBRARY_DIR, PAGE_SIZE, SERVER_BASE, URL_PREFIX
from .opds import now_rfc3339, mime_for
from .auth import require_basic
from .thumbs import have_thumb, generate_thumb
from . import db  # SQLite adapter

# -------------------- FastAPI & Jinja --------------------
app = FastAPI(title="ComicOPDS")

env = Environment(
    loader=FileSystemLoader(str(Path(__file__).parent / "templates"), encoding="utf-8"),
    autoescape=select_autoescape(enabled_extensions=("xml", "html", "j2"), default=True),
)

# -------------------- Index state (background) --------------------
_INDEX_STATUS = {
    "running": False,
    "phase": "idle",      # "counting" | "indexing" | "idle"
    "total": 0,
    "done": 0,
    "current": "",
    "started_at": 0.0,
    "ended_at": 0.0,
}
_INDEX_LOCK = threading.Lock()

AUTO_INDEX_ON_START = os.getenv("AUTO_INDEX_ON_START", "false").strip().lower() not in ("0","false","no","off")

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
    """Lightweight ComicInfo.xml reader."""
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
    """Background scanner thread: writes into SQLite using its own connection."""
    conn = db.connect()
    try:
        db.begin_scan(conn)
        _set_status(running=True, phase="counting", done=0, total=0, current="", started_at=time.time(), ended_at=0.0)

        total = _count_cbz(LIBRARY_DIR)
        _set_status(total=total, phase="indexing")

        for dirpath, dirnames, filenames in os.walk(LIBRARY_DIR):
            dpath = Path(dirpath)
            if dpath != LIBRARY_DIR:
                rel_d = dpath.relative_to(LIBRARY_DIR).as_posix()
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
                st = p.stat()
                db.upsert_file(
                    conn,
                    rel=rel,
                    name=p.stem,
                    size=st.st_size,
                    mtime=st.st_mtime,
                    parent=_parent_rel(rel),
                    ext="cbz",
                )
                meta = _read_comicinfo(p)
                if meta:
                    db.upsert_meta(conn, rel=rel, meta=meta)

                _index_progress(rel)

        db.prune_stale(conn)
        _set_status(phase="idle", running=False, ended_at=time.time(), current="")
    except Exception:
        _set_status(phase="idle", running=False, ended_at=time.time())
    finally:
        try:
            conn.close()
        except Exception:
            pass

def _start_scan(force=False):
    if not force and _INDEX_STATUS["running"]:
        return
    t = threading.Thread(target=_run_scan, daemon=True)
    t.start()

@app.on_event("startup")
def startup():
    if not LIBRARY_DIR.exists():
        raise RuntimeError(f"CONTENT_BASE_DIR does not exist: {LIBRARY_DIR}")

    if AUTO_INDEX_ON_START:
        _start_scan(force=True)
        return

    # Skip auto-index if DB already has rows
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
    # natural sort
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

def _feed(entries_xml: List[str], title: str, self_href: str, next_href: Optional[str] = None):
    tpl = env.get_template("feed.xml.j2")
    base = SERVER_BASE.rstrip("/")
    return tpl.render(
        feed_id=f"{base}{_abs_url(self_href)}",
        updated=now_rfc3339(),
        title=title,
        self_href=_abs_url(self_href),
        start_href=_abs_url("/opds"),
        base=base,
        next_href=_abs_url(next_href) if next_href else None,
        entries=entries_xml,
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

        # PSE: template URL & count (Komga-style)
        pse_template = f"/pse/page?path={quote(rel)}&page={{pageNumber}}"
        page_count = 0
        try:
            if abs_file.exists():
                page_count = len(_cbz_list_pages(abs_file))
        except Exception:
            page_count = 0

        comicvine_issue = rget(row, "comicvineissue")
        thumb_href_abs = None
        image_abs = None
        if (rget(row, "ext") or "").lower() == "cbz":
            p = have_thumb(rel, comicvine_issue) or generate_thumb(rel, abs_file, comicvine_issue)
            if p:
                # we’ll use the same image for both full image and thumbnail rels
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

# -------------------- Routes --------------------
@app.get("/healthz")
def health():
    return PlainTextResponse("ok")

@app.get("/opds", response_class=Response)
def browse(path: str = Query("", description="Relative folder path"), page: int = 1, _=Depends(require_basic)):
    path = path.strip("/")
    conn = db.connect()
    try:
        total = db.children_count(conn, path)
        start = (page - 1) * PAGE_SIZE
        rows = db.children_page(conn, path, PAGE_SIZE, start)
    finally:
        conn.close()

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

    self_href = f"/opds?path={quote(path)}&page={page}" if path else f"/opds?page={page}"
    next_href = f"/opds?path={quote(path)}&page={page+1}" if (start + PAGE_SIZE) < total else None
    xml = _feed(entries_xml, title=f"/{path}" if path else "Library", self_href=self_href, next_href=next_href)
    return Response(content=xml, media_type="application/atom+xml;profile=opds-catalog")

@app.get("/", response_class=Response)
def root(_=Depends(require_basic)):
    return browse(path="", page=1)

@app.get("/opds/search.xml", response_class=Response)
def opensearch_description(_=Depends(require_basic)):
    tpl = env.get_template("search-description.xml.j2")
    xml = tpl.render(base=SERVER_BASE.rstrip("/"))
    return Response(content=xml, media_type="application/opensearchdescription+xml")

@app.get("/opds/search", response_class=Response)
def opds_search(q: str = Query("", alias="q"), page: int = 1, _=Depends(require_basic)):
    q_str = (q or "").strip()
    if not q_str:
        return browse(path="", page=page)

    conn = db.connect()
    try:
        start = (page - 1) * PAGE_SIZE
        rows = db.search_q(conn, q_str, PAGE_SIZE, start)
    finally:
        conn.close()

    entries_xml = [_entry_xml_from_row(r) for r in rows]
    self_href = f"/opds/search?q={quote(q_str)}&page={page}"
    next_href = f"/opds/search?q={quote(q_str)}&page={page+1}" if len(rows) == PAGE_SIZE else None
    xml = _feed(entries_xml, title=f"Search: {q_str}", self_href=self_href, next_href=next_href)
    return Response(content=xml, media_type="application/atom+xml;profile=opds-catalog")

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

    inner = pages[page] # zero-based
    cache_dir = _book_cache_dir(path)
    dest = cache_dir / f"{page+1:04d}.jpg"  # keep filenames 1-based 
    out = _ensure_page_jpeg(abs_cbz, inner, dest)
    return FileResponse(out, media_type="image/jpeg")

# -------------------- Dashboard & stats --------------------
@app.get("/dashboard", response_class=HTMLResponse)
def dashboard(_=Depends(require_basic)):
    tpl = env.get_template("dashboard.html")
    return HTMLResponse(tpl.render())

@app.get("/stats.json", response_class=JSONResponse)
def stats(_=Depends(require_basic)):
    conn = db.connect()
    try:
        payload = db.stats(conn)
    finally:
        conn.close()

    thumbs_dir = Path("/data/thumbs")
    total_covers = 0
    if thumbs_dir.exists():
        total_covers = sum(1 for _ in thumbs_dir.glob("*.jpg"))
    payload["total_covers"] = total_covers

    return JSONResponse(payload)

# -------------------- Debug --------------------
@app.get("/debug/children", response_class=JSONResponse)
def debug_children(path: str = ""):
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
def opds_smart_lists(_=Depends(require_basic)):
    lists = _load_smartlists()
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
    return Response(content=xml, media_type="application/atom+xml;profile=opds-catalog")

@app.get("/opds/smart/{slug}", response_class=Response)
def opds_smart_list(slug: str, page: int = 1, _=Depends(require_basic)):
    lists = _load_smartlists()
    sl = next((x for x in lists if x.get("slug") == slug), None)
    if not sl:
        raise HTTPException(404, "Smart list not found")

    groups = sl.get("groups") or []
    sort = (sl.get("sort") or "issued_desc").lower()
    distinct_by = (sl.get("distinct_by") or "") == "series"

    start = (page - 1) * PAGE_SIZE

    conn = db.connect()
    try:
        rows = db.smartlist_query(conn, groups, sort, PAGE_SIZE, start, distinct_by)
        total = db.smartlist_count(conn, groups)
    finally:
        conn.close()

    entries_xml = [_entry_xml_from_row(r) for r in rows]
    self_href = f"/opds/smart/{quote(slug)}?page={page}"
    next_href = f"/opds/smart/{quote(slug)}?page={page+1}" if (start + PAGE_SIZE) < total else None
    xml = _feed(entries_xml, title=sl["name"], self_href=self_href, next_href=next_href)
    return Response(content=xml, media_type="application/atom+xml;profile=opds-catalog")

@app.get("/search", response_class=HTMLResponse)
def smartlists_page(_=Depends(require_basic)):
    tpl = env.get_template("smartlists.html")
    return HTMLResponse(tpl.render())

@app.get("/smartlists.json", response_class=JSONResponse)
def smartlists_get(_=Depends(require_basic)):
    return JSONResponse(_load_smartlists())

@app.post("/smartlists.json", response_class=JSONResponse)
def smartlists_post(payload: list[dict], _=Depends(require_basic)):
    lists: list[dict] = []
    for sl in (payload or []):
        name = (sl.get("name") or "Smart List").strip()
        slug = _slugify(sl.get("slug") or name)
        groups = sl.get("groups") or []

        norm_groups = []
        for g in groups:
            rules = []
            for r in (g.get("rules") or []):
                op = (r.get("op") or "contains").lower()
                val = (r.get("value") or "")
                if not val.strip() and op not in ("exists", "missing"):
                    continue
                rules.append(
                    {
                        "field": (r.get("field") or "").lower(),
                        "op": op,
                        "value": val,
                        "not": bool(r.get("not", False)),
                    }
                )
            if rules:
                norm_groups.append({"rules": rules})

        lists.append(
            {
                "name": name,
                "slug": slug,
                "groups": norm_groups,
                "sort": (sl.get("sort") or "issued_desc").lower(),
                "limit": int(sl.get("limit") or 0),
                "distinct_by": (sl.get("distinct_by") or ""),
            }
        )

    _save_smartlists(lists)
    return JSONResponse({"ok": True, "count": len(lists)})

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
