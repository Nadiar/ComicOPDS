from __future__ import annotations

from fastapi import FastAPI, Query, HTTPException, Request, Response, Depends, Header
from fastapi.responses import (
    StreamingResponse,
    FileResponse,
    PlainTextResponse,
    HTMLResponse,
    JSONResponse,
)
from pathlib import Path
from typing import List, Dict, Any, Optional
from jinja2 import Environment, FileSystemLoader, select_autoescape
from urllib.parse import quote
from collections import Counter
import threading
import time
import re
import json
import datetime as dt

from .config import LIBRARY_DIR, PAGE_SIZE, SERVER_BASE, URL_PREFIX
from . import fs_index
from .opds import now_rfc3339, mime_for
from .auth import require_basic
from .thumbs import have_thumb, generate_thumb

app = FastAPI(title="ComicOPDS")

# Jinja: force UTF-8 + auto-escape .xml/.html/.j2
env = Environment(
    loader=FileSystemLoader(str(Path(__file__).parent / "templates"), encoding="utf-8"),
    autoescape=select_autoescape(enabled_extensions=("xml", "html", "j2"), default=True),
)

# -------------------- Index state (background) --------------------
INDEX: List[fs_index.Item] = []
_INDEX_LOCK = threading.Lock()
_INDEX_STATUS = {
    "running": False,
    "phase": "idle",      # "counting" | "indexing" | "idle"
    "total": 0,
    "done": 0,
    "current": "",
    "started_at": 0.0,
    "ended_at": 0.0,
}


def _abs_path(p: str) -> str:
    """URL prefix helper"""
    return (URL_PREFIX + p) if URL_PREFIX else p


def _count_target_files(root: Path) -> int:
    exts = {".cbz"}
    n = 0
    for p in root.rglob("*"):
        if p.is_file() and p.suffix.lower() in exts:
            n += 1
    return n


def _set_status(**kw):
    _INDEX_STATUS.update(kw)


def _index_progress_tick(info: dict):
    _INDEX_STATUS["done"] += 1
    _INDEX_STATUS["current"] = info.get("rel") or ""


def _run_indexing():
    global INDEX
    try:
        _set_status(running=True, phase="counting", done=0, total=0, current="", started_at=time.time(), ended_at=0.0)
        total = _count_target_files(LIBRARY_DIR)
        _set_status(total=total, phase="indexing")
        items = fs_index.scan(LIBRARY_DIR, progress_cb=_index_progress_tick)
        with _INDEX_LOCK:
            INDEX = items
        _set_status(phase="idle", running=False, ended_at=time.time(), current="")
    except Exception:
        _set_status(phase="idle", running=False, ended_at=time.time())


def _start_indexing_if_needed(force=False):
    if not force and _INDEX_STATUS["running"]:
        return
    if not force and INDEX:
        return
    t = threading.Thread(target=_run_indexing, daemon=True)
    t.start()


@app.on_event("startup")
def startup():
    if not LIBRARY_DIR.exists():
        raise RuntimeError(f"CONTENT_BASE_DIR does not exist: {LIBRARY_DIR}")
    _start_indexing_if_needed(force=True)


# -------------------- OPDS helpers --------------------
def _display_title(item: fs_index.Item) -> str:
    m = item.meta or {}
    series, number, volume = m.get("series"), m.get("number"), m.get("volume")
    title = m.get("title") or item.name
    if series and number:
        vol = f" ({volume})" if volume else ""
        suffix = f" — {title}" if title and title != series else ""
        return f"{series}{vol} #{number}{suffix}"
    return title


def _authors_from_meta(meta: dict) -> list[str]:
    authors = []
    for key in ("writer", "coverartist", "penciller", "inker", "colorist", "letterer"):
        v = meta.get(key)
        if v:
            authors.extend([x.strip() for x in v.split(",") if x.strip()])
    seen = set()
    out = []
    for a in authors:
        if a.lower() in seen:
            continue
        seen.add(a.lower())
        out.append(a)
    return out


def _issued_from_meta(meta: dict) -> Optional[str]:
    y = meta.get("year")
    if not y:
        return None
    m = int(meta.get("month") or 1)
    d = int(meta.get("day") or 1)
    try:
        return f"{int(y):04d}-{m:02d}-{d:02d}"
    except Exception:
        return None


def _categories_from_meta(meta: dict) -> list[str]:
    cats = []
    for k in ("genre", "tags", "characters", "teams", "locations"):
        v = meta.get(k)
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
        feed_id=f"{base}{_abs_path(self_href)}",
        updated=now_rfc3339(),
        title=title,
        self_href=_abs_path(self_href),
        start_href=_abs_path("/opds"),
        base=base,
        next_href=_abs_path(next_href) if next_href else None,
        entries=entries_xml,
    )


def _entry_xml(item: fs_index.Item) -> str:
    tpl = env.get_template("entry.xml.j2")
    if item.is_dir:
        href = f"/opds?path={quote(item.rel)}" if item.rel else "/opds"
        return tpl.render(
            entry_id=f"{SERVER_BASE.rstrip('/')}{_abs_path('/opds/' + quote(item.rel))}",
            updated=now_rfc3339(),
            title=item.name or "/",
            is_dir=True,
            href=_abs_path(href),
        )
    else:
        download_href = f"/download?path={quote(item.rel)}"
        stream_href = f"/stream?path={quote(item.rel)}"
        meta = item.meta or {}
        comicvine_issue = meta.get("comicvineissue")

        thumb_href = None
        if item.path.suffix.lower() == ".cbz":
            p = have_thumb(item.rel, comicvine_issue)
            if not p:
                p = generate_thumb(item.rel, item.path, comicvine_issue)
            if p:
                thumb_href = f"/thumb?path={quote(item.rel)}"

        return tpl.render(
            entry_id=f"{SERVER_BASE.rstrip('/')}{_abs_path(download_href)}",
            updated=now_rfc3339(),
            title=_display_title(item),
            is_dir=False,
            download_href=_abs_path(download_href),
            stream_href=_abs_path(stream_href),
            mime=mime_for(item.path),
            size_str=f"{item.size} bytes",
            thumb_href=_abs_path("/thumb?path=" + quote(item.rel)) if thumb_href else None,
            authors=_authors_from_meta(meta),
            issued=_issued_from_meta(meta),
            summary=(meta.get("summary") or None),
            categories=_categories_from_meta(meta),
        )


# -------------------- Core routes --------------------
@app.get("/healthz")
def health():
    return PlainTextResponse("ok")


@app.get("/opds", response_class=Response)
def browse(path: str = Query("", description="Relative folder path"), page: int = 1, _=Depends(require_basic)):
    path = path.strip("/")
    children = list(fs_index.children(INDEX, path))

    # Sort: dirs first; files by series + number
    def sort_key(it: fs_index.Item):
        if it.is_dir:
            return (0, it.name.lower(), 0)
        meta = it.meta or {}
        series = meta.get("series") or ""
        try:
            num = int(float(meta.get("number", "0")))
        except ValueError:
            num = 10**9
        return (1, series.lower() or it.name.lower(), num)

    children.sort(key=sort_key)

    start = (page - 1) * PAGE_SIZE
    end = start + PAGE_SIZE
    page_items = children[start:end]
    entries_xml = [_entry_xml(it) for it in page_items]

    # "Smart Lists" virtual folder at root page 1
    if path == "" and page == 1:
        tpl = env.get_template("entry.xml.j2")
        smart_href = _abs_path("/opds/smart")
        smart_entry = tpl.render(
            entry_id=f"{SERVER_BASE.rstrip('/')}{smart_href}",
            updated=now_rfc3339(),
            title="📁 Smart Lists",
            is_dir=True,
            href=smart_href,
        )
        entries_xml = [smart_entry] + entries_xml

    self_href = f"/opds?path={quote(path)}&page={page}" if path else f"/opds?page={page}"
    next_href = f"/opds?path={quote(path)}&page={page+1}" if end < len(children) else None
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
    terms = [t.lower() for t in q.split() if t.strip()]
    if not terms:
        return browse(path="", page=page)

    def haystack(it: fs_index.Item) -> str:
        meta = it.meta or {}
        meta_vals = " ".join(str(v) for v in meta.values() if v)
        return (it.name + " " + meta_vals).lower()

    matches = [it for it in INDEX if (not it.is_dir) and all(t in haystack(it) for t in terms)]

    start = (page - 1) * PAGE_SIZE
    end = start + PAGE_SIZE
    page_items = matches[start:end]
    entries_xml = [_entry_xml(it) for it in page_items]
    self_href = f"/opds/search?q={quote(q)}&page={page}"
    next_href = f"/opds/search?q={quote(q)}&page={page+1}" if end < len(matches) else None
    xml = _feed(entries_xml, title=f"Search: {q}", self_href=self_href, next_href=next_href)
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
    p = _abspath(path)
    if not p.exists() or not p.is_file():
        raise HTTPException(404)
    st = p.stat()
    headers = _common_file_headers(p)
    headers["Content-Length"] = str(st.st_size)
    return Response(status_code=200, headers=headers)


@app.get("/stream")
def stream(path: str, request: Request, range: str | None = Header(default=None), _=Depends(require_basic)):
    # Alias of download with Range support (Panels uses /download)
    return download(path=path, request=request, range=range)


@app.get("/thumb")
def thumb(path: str, _=Depends(require_basic)):
    abs_p = _abspath(path)
    if not abs_p.exists() or not abs_p.is_file():
        raise HTTPException(404)
    it = next((x for x in INDEX if not x.is_dir and x.rel == path), None)
    if not it:
        raise HTTPException(404)
    cvid = (it.meta or {}).get("comicvineissue")
    p = have_thumb(path, cvid) or generate_thumb(path, abs_p, cvid)
    if not p or not p.exists():
        raise HTTPException(404, "No thumbnail")
    return FileResponse(p, media_type="image/jpeg")


# -------------------- Dashboard & stats --------------------
@app.get("/dashboard", response_class=HTMLResponse)
def dashboard(_=Depends(require_basic)):
    tpl = env.get_template("dashboard.html")
    return HTMLResponse(tpl.render())


@app.get("/stats.json", response_class=JSONResponse)
def stats(_=Depends(require_basic)):
    files = [it for it in INDEX if not it.is_dir]
    total_comics = len(files)
    series_set = set()
    publishers = Counter()
    formats = Counter()
    writers = Counter()
    timeline = Counter()
    last_updated = 0.0

    for it in files:
        m = it.meta or {}
        if it.mtime > last_updated:
            last_updated = it.mtime
        if m.get("series"):
            series_set.add(m["series"])
        if m.get("publisher"):
            publishers[m["publisher"]] += 1
        ext = it.path.suffix.lower().lstrip(".") or "unknown"
        formats[ext] += 1
        if m.get("writer"):
            for w in [x.strip() for x in m["writer"].split(",") if x.strip()]:
                writers[w] += 1
        if m.get("year"):
            try:
                y = int(m["year"])
                timeline[y] += 1
            except ValueError:
                pass

    thumbs_dir = Path("/data/thumbs")
    total_covers = 0
    if thumbs_dir.exists():
        total_covers = sum(1 for _ in thumbs_dir.glob("*.jpg"))

    pub_labels, pub_values = [], []
    if publishers:
        top = publishers.most_common(15)
        other = sum(v for _, v in list(publishers.items())[15:])
        pub_labels = [k for k, _ in top]
        pub_values = [v for _, v in top]
        if other:
            #pub_labels.append("Other")
            pub_values.append(other)

    years = sorted(timeline.keys())
    year_values = [timeline[y] for y in years]

    w_top = writers.most_common(15)
    w_labels = [k for k, _ in w_top]
    w_values = [v for _, v in w_top]

    payload: Dict[str, Any] = {
        "last_updated": last_updated,
        "total_covers": total_covers,
        "total_comics": total_comics,
        "unique_series": len(series_set),
        "unique_publishers": len(publishers),
        "formats": dict(formats) or {"cbz": 0},
        "publishers": {"labels": pub_labels, "values": pub_values},
        "timeline": {"labels": years, "values": year_values},
        "top_writers": {"labels": w_labels, "values": w_values},
    }
    return JSONResponse(payload)


# -------------------- Debug --------------------
@app.get("/debug/children", response_class=JSONResponse)
def debug_children(path: str = ""):
    ch = list(fs_index.children(INDEX, path.strip("/")))
    return JSONResponse(
        [{"rel": it.rel, "is_dir": it.is_dir, "name": it.name} for it in ch]
    )


# -------------------- Smart Lists (advanced) --------------------
SMARTLISTS_PATH = Path("/data/smartlists.json")


def _slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", (name or "").lower()).strip("-")
    return slug or "list"


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


def _issued_tuple(meta: dict) -> Optional[tuple[int, int, int]]:
    y = meta.get("year")
    if not y:
        return None
    try:
        return (int(y), int(meta.get("month") or 1), int(meta.get("day") or 1))
    except Exception:
        return None


def _get_field_value(it: fs_index.Item, field: str):
    f = (field or "").lower()
    m = it.meta or {}
    if f == "rel": return it.rel
    if f == "title": return m.get("title") or it.name
    if f == "series": return m.get("series")
    if f == "number": return m.get("number")
    if f == "volume": return m.get("volume")
    if f == "publisher": return m.get("publisher")
    if f == "imprint": return m.get("imprint")
    if f == "writer": return m.get("writer")
    if f == "characters": return m.get("characters")
    if f == "teams": return m.get("teams")
    if f == "tags": return m.get("tags") or m.get("genre")
    if f == "year": return m.get("year")
    if f == "month": return m.get("month")
    if f == "day": return m.get("day")
    if f == "issued":
        t = _issued_tuple(m)
        return f"{t[0]:04d}-{t[1]:02d}-{t[2]:02d}" if t else None
    if f == "languageiso": return m.get("languageiso")
    if f == "comicvineissue": return m.get("comicvineissue")
    if f == "ext": return it.path.suffix.lower().lstrip(".")
    if f == "size": return it.size
    if f == "mtime": return int(it.mtime)
    if f == "has_thumb":
        return bool(have_thumb(it.rel, m.get("comicvineissue")))
    if f == "has_meta": return bool(m)
    return None


def _to_float(x) -> Optional[float]:
    try:
        return float(str(x))
    except Exception:
        return None


def _to_date(s: str) -> Optional[dt.date]:
    s = (s or "").strip()
    if not s:
        return None
    parts = s.split("-")
    try:
        if len(parts) == 1:
            return dt.date(int(parts[0]), 1, 1)
        if len(parts) == 2:
            return dt.date(int(parts[0]), int(parts[1]), 1)
        if len(parts) == 3:
            return dt.date(int(parts[0]), int(parts[1]), int(parts[2]))
    except Exception:
        return None
    return None


def _val_to_date(val) -> Optional[dt.date]:
    if isinstance(val, (int, float)):
        try:
            return dt.date.fromtimestamp(int(val))
        except Exception:
            return None
    if isinstance(val, str):
        return _to_date(val)
    return None


def _rule_true(it: fs_index.Item, r: dict) -> bool:
    field = (r.get("field") or "").lower()
    op = (r.get("op") or "contains").lower()
    val = (r.get("value") or "").strip()
    negate = bool(r.get("not", False))

    left = _get_field_value(it, field)

    if op in ("exists", "missing"):
        ok = (left is not None and left != "" and left != 0)
        ok = ok if op == "exists" else (not ok)
        return (not ok) if negate else ok

    if left is None:
        return False if not negate else True

    if op in ("=", "==", "!=", ">", ">=", "<", "<="):
        a = _to_float(left)
        b = _to_float(val)
        if a is None or b is None:
            return False if not negate else True
        result = {
            "=": a == b,
            "==": a == b,
            "!=": a != b,
            ">": a > b,
            ">=": a >= b,
            "<": a < b,
            "<=": a <= b,
        }[op]
        return (not result) if negate else result

    if op in ("on", "before", "after", "between") and field in ("issued", "mtime", "year", "month", "day"):
        if field == "mtime":
            try:
                L = dt.date.fromtimestamp(int(left))
            except Exception:
                L = None
        elif field == "issued":
            L = _val_to_date(left)
        else:
            try:
                yy = int(_get_field_value(it, "year") or 1)
                mm = int(_get_field_value(it, "month") or 1)
                dd = int(_get_field_value(it, "day") or 1)
                L = dt.date(yy, mm, dd)
            except Exception:
                L = None
        if L is None:
            return False if not negate else True

        if op == "between":
            parts = [p.strip() for p in val.split(",")]
            if len(parts) != 2:
                return False if not negate else True
            D1 = _to_date(parts[0])
            D2 = _to_date(parts[1])
            if not D1 or not D2:
                return False if not negate else True
            result = (D1 <= L <= D2)
        else:
            D = _to_date(val)
            if not D:
                return False if not negate else True
            result = (L == D) if op == "on" else ((L < D) if op == "before" else (L > D))
        return (not result) if negate else result

    A = str(left)
    if op == "regex":
        try:
            result = bool(re.search(val, A, flags=re.IGNORECASE))
        except re.error:
            result = False
    else:
        a = A.lower()
        b = val.lower()
        result = (
            (op == "contains" and b in a)
            or (op == "equals" and a == b)
            or (op == "startswith" and a.startswith(b))
            or (op == "endswith" and a.endswith(b))
        )
    return (not result) if negate else result


def _matches_groups(it: fs_index.Item, groups: list[dict]) -> bool:
    valid_groups = [g for g in (groups or []) if g.get("rules")]
    if not valid_groups:
        return False
    for g in valid_groups:
        rules = g.get("rules") or []
        if all(_rule_true(it, r) for r in rules):
            return True
    return False


def _sort_key(item: fs_index.Item, name: str):
    n = (name or "").lower()
    m = item.meta or {}
    if n in ("issued_desc", "issued"):
        t = _issued_tuple(m) or (0, 0, 0)
        return (t[0], t[1], t[2])
    if n == "series_number":
        series = (m.get("series") or item.name or "").lower()
        try:
            num = int(float(m.get("number", "0")))
        except ValueError:
            num = 10**9
        return (series, num)
    if n == "title":
        return ((m.get("title") or item.name).lower(),)
    if n == "publisher":
        return ((m.get("publisher") or "").lower(), (m.get("series") or "").lower())
    return ((item.name or "").lower(),)


def _distinct_latest_by_series(items: list[fs_index.Item]) -> list[fs_index.Item]:
    best: Dict[str, fs_index.Item] = {}

    def rank(x: fs_index.Item):
        m = x.meta or {}
        t = _issued_tuple(m) or (0, 0, 0)
        try:
            num = int(float(m.get("number", "0")))
        except ValueError:
            num = -1
        return (t[0], t[1], t[2], num)

    for it in items:
        series = (it.meta or {}).get("series")
        if not series:
            continue
        key = series.lower()
        cur = best.get(key)
        if cur is None or rank(it) > rank(cur):
            best[key] = it
    no_series = [it for it in items if not (it.meta or {}).get("series")]
    return list(best.values()) + no_series


@app.get("/opds/smart", response_class=Response)
def opds_smart_lists(_=Depends(require_basic)):
    lists = _load_smartlists()
    tpl = env.get_template("entry.xml.j2")
    entries = []
    for sl in lists:
        href = f"/opds/smart/{quote(sl['slug'])}"
        entries.append(
            tpl.render(
                entry_id=f"{SERVER_BASE.rstrip('/')}{_abs_path(href)}",
                updated=now_rfc3339(),
                title=sl["name"],
                is_dir=True,
                href=_abs_path(href),
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
    matches = [it for it in INDEX if (not it.is_dir) and _matches_groups(it, groups)]

    sort_name = (sl.get("sort") or "issued_desc").lower()
    reverse = sort_name in ("issued_desc",)
    matches.sort(key=lambda it: _sort_key(it, sort_name), reverse=reverse)

    if (sl.get("distinct_by") or "") == "series":
        matches = _distinct_latest_by_series(matches)

    limit = int(sl.get("limit") or 0)
    if limit > 0:
        matches = matches[:limit]

    start = (page - 1) * PAGE_SIZE
    end = start + PAGE_SIZE
    page_items = matches[start:end]
    entries_xml = [_entry_xml(it) for it in page_items]

    self_href = f"/opds/smart/{quote(slug)}?page={page}"
    next_href = f"/opds/smart/{quote(slug)}?page={page+1}" if end < len(matches) else None
    xml = _feed(entries_xml, title=f"{sl['name']}", self_href=self_href, next_href=next_href)
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

        sort = (sl.get("sort") or "issued_desc").lower()
        limit = int(sl.get("limit") or 0)
        distinct_by = (sl.get("distinct_by") or "")
        lists.append(
            {
                "name": name,
                "slug": slug,
                "groups": norm_groups,
                "sort": sort,
                "limit": limit,
                "distinct_by": distinct_by,
            }
        )
    _save_smartlists(lists)
    return JSONResponse({"ok": True, "count": len(lists)})


# -------------------- Index status + Reindex --------------------
@app.get("/index/status", response_class=JSONResponse)
def index_status(_=Depends(require_basic)):
    usable = bool(INDEX)
    return JSONResponse({**_INDEX_STATUS, "usable": usable})


@app.post("/admin/reindex", response_class=JSONResponse)
def admin_reindex(_=Depends(require_basic)):
    _start_indexing_if_needed(force=True)
    return JSONResponse({"ok": True, "started": True})
