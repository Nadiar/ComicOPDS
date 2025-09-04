from fastapi import FastAPI, Query, HTTPException, Request, Response, Depends
from fastapi.responses import StreamingResponse, FileResponse, PlainTextResponse, HTMLResponse, JSONResponse
from pathlib import Path
from typing import List, Dict, Any
from jinja2 import Environment, FileSystemLoader, select_autoescape
from urllib.parse import quote
import os
from collections import Counter, defaultdict
import hashlib, email.utils

from .config import LIBRARY_DIR, PAGE_SIZE, SERVER_BASE, URL_PREFIX, ENABLE_WATCH
from . import fs_index
from .opds import now_rfc3339, mime_for
from .auth import require_basic
from .thumbs import have_thumb, generate_thumb

app = FastAPI(title="ComicOPDS")

env = Environment(
    loader=FileSystemLoader(str(Path(__file__).parent / "templates")),
    autoescape=select_autoescape(enabled_extensions=("xml","html"))
)

INDEX: List[fs_index.Item] = []

def _etag_for(p: Path) -> str:
    st = p.stat()
    return '"' + hashlib.md5(f"{st.st_size}-{st.st_mtime}".encode()).hexdigest() + '"'

def _last_modified_for(p: Path) -> str:
    return email.utils.formatdate(p.stat().st_mtime, usegmt=True)

def _abs_path(p: str) -> str:
    return (URL_PREFIX + p) if URL_PREFIX else p

@app.on_event("startup")
def build_index():
    if not LIBRARY_DIR.exists():
        raise RuntimeError(f"CONTENT_BASE_DIR does not exist: {LIBRARY_DIR}")
    global INDEX
    INDEX = fs_index.scan(LIBRARY_DIR)

# ---------- helpers for OPDS ----------
def _display_title(item):
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
    for key in ("writer","coverartist","penciller","inker","colorist","letterer"):
        v = meta.get(key)
        if v:
            authors.extend([x.strip() for x in v.split(",") if x.strip()])
    seen=set(); out=[]
    for a in authors:
        if a.lower() in seen: continue
        seen.add(a.lower()); out.append(a)
    return out

def _issued_from_meta(meta: dict) -> str | None:
    y = meta.get("year")
    if not y: return None
    m = int(meta.get("month") or 1)
    d = int(meta.get("day") or 1)
    try: return f"{int(y):04d}-{m:02d}-{d:02d}"
    except Exception: return None

def _categories_from_meta(meta: dict) -> list[str]:
    cats=[]
    for k in ("genre","tags","characters","teams","locations"):
        v=meta.get(k)
        if v:
            cats += [x.strip() for x in v.split(",") if x.strip()]
    seen=set(); out=[]
    for c in cats:
        lc=c.lower()
        if lc in seen: continue
        seen.add(lc); out.append(c)
    return out

def _feed(entries_xml: List[str], title: str, self_href: str, next_href: str | None = None):
    tpl = env.get_template("feed.xml.j2")
    return tpl.render(
        feed_id=f"{SERVER_BASE}{_abs_path(self_href)}",
        updated=now_rfc3339(),
        title=title,
        self_href=_abs_path(self_href),
        start_href=_abs_path("/opds"),
        base=SERVER_BASE,
        next_href=_abs_path(next_href) if next_href else None,
        entries=entries_xml
    )

def _entry_xml(item: fs_index.Item):
    tpl = env.get_template("entry.xml.j2")
    if item.is_dir:
        href = f"/opds?path={quote(item.rel)}" if item.rel else "/opds"
        return tpl.render(
            entry_id=f"{SERVER_BASE}{_abs_path('/opds/' + quote(item.rel))}",
            updated=now_rfc3339(),
            title=item.name or "/",
            is_dir=True,
            href=_abs_path(href)
        )
    else:
        download_href=f"/download?path={quote(item.rel)}"
        stream_href=f"/stream?path={quote(item.rel)}"
        meta=item.meta or {}
        comicvine_issue=meta.get("comicvineissue")

        thumb_href=None
        if item.path.suffix.lower()==".cbz":
            p=have_thumb(item.rel, comicvine_issue)
            if not p:
                p=generate_thumb(item.rel,item.path,comicvine_issue)
            if p:
                thumb_href=f"/thumb?path={quote(item.rel)}"

        return tpl.render(
            entry_id=f"{SERVER_BASE}{_abs_path(download_href)}",
            updated=now_rfc3339(),
            title=_display_title(item),
            is_dir=False,
            download_href=_abs_path(download_href),
            stream_href=_abs_path(stream_href),
            mime=mime_for(item.path),
            size_str=f"{item.size} bytes",
            thumb_href=_abs_path("/thumb?path="+quote(item.rel)) if thumb_href else None,
            authors=_authors_from_meta(meta),
            issued=_issued_from_meta(meta),
            summary=(meta.get("summary") or None),
            categories=_categories_from_meta(meta),
        )

# ---------- OPDS routes ----------
@app.get("/healthz")
def health():
    return PlainTextResponse("ok")

@app.get("/opds", response_class=Response)
def browse(path: str = Query("", description="Relative folder path"), page: int = 1, _=Depends(require_basic)):
    path=path.strip("/")
    children=list(fs_index.children(INDEX,path))
    # Sort files in a nicer way: by series + numeric Number when present, else by name
    def sort_key(it: fs_index.Item):
        if it.is_dir:
            return (0, it.name.lower(), 0)
        meta = it.meta or {}
        series = meta.get("series") or ""
        # force numeric-ish, shove non-numeric to end
        try:
            num = int(float(meta.get("number", "0")))
        except ValueError:
            num = 10**9
        return (1, series.lower() or it.name.lower(), num)


    start=(page-1)*PAGE_SIZE
    end=start+PAGE_SIZE
    page_items=children[start:end]
    entries_xml=[_entry_xml(it) for it in page_items]
    self_href=f"/opds?path={quote(path)}&page={page}" if path else f"/opds?page={page}"
    next_href=None
    if end<len(children):
        next_href=f"/opds?path={quote(path)}&page={page+1}" if path else f"/opds?page={page+1}"
    xml=_feed(entries_xml,title=f"/{path}" if path else "Library",self_href=self_href,next_href=next_href)
    return Response(content=xml,media_type="application/atom+xml;profile=opds-catalog")

@app.get("/", response_class=Response)
def root(_=Depends(require_basic)):
    # Keep root as OPDS start for clients
    return browse(path="",page=1)

@app.get("/opds/search.xml", response_class=Response)
def opensearch_description(_=Depends(require_basic)):
    tpl=env.get_template("search-description.xml.j2")
    xml=tpl.render(base=SERVER_BASE)
    return Response(content=xml,media_type="application/opensearchdescription+xml")

@app.get("/opds/search", response_class=Response)
def search(q: str = Query("",alias="q"), page: int = 1, _=Depends(require_basic)):
    terms=[t.lower() for t in q.split() if t.strip()]
    if not terms:
        return browse(path="",page=page)

    def haystack(it: fs_index.Item) -> str:
        meta = it.meta or {}
        meta_vals = " ".join(str(v) for v in meta.values() if v)
        return (it.name + " " + meta_vals).lower()

    matches=[it for it in INDEX if (not it.is_dir) and all(t in haystack(it) for t in terms)]

    start=(page-1)*PAGE_SIZE
    end=start+PAGE_SIZE
    page_items=matches[start:end]
    entries_xml=[_entry_xml(it) for it in page_items]
    self_href=f"/opds/search?q={quote(q)}&page={page}"
    next_href=f"/opds/search?q={quote(q)}&page={page+1}" if end<len(matches) else None
    xml=_feed(entries_xml,title=f"Search: {q}",self_href=self_href,next_href=next_href)
    return Response(content=xml,media_type="application/atom+xml;profile=opds-catalog")

# ---------- file endpoints ----------
def _abspath(rel: str) -> Path:
    p=(LIBRARY_DIR/rel).resolve()
    if LIBRARY_DIR not in p.parents and p!=LIBRARY_DIR:
        raise HTTPException(400,"Invalid path")
    return p

@app.get("/download")
def download(path: str, request: Request, _=Depends(require_basic)):
    p = _abspath(path)
    if not p.exists() or not p.is_file():
        raise HTTPException(404)

    etag = _etag_for(p)
    lastmod = _last_modified_for(p)

    # Handle If-None-Match / If-Modified-Since
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304)
    if request.headers.get("if-modified-since") == lastmod:
        return Response(status_code=304)

    resp = FileResponse(p, media_type=mime_for(p), filename=p.name)
    resp.headers["ETag"] = etag
    resp.headers["Last-Modified"] = lastmod
    resp.headers["Accept-Ranges"] = "bytes"
    return resp

@app.get("/stream")
def stream(path: str, request: Request, _=Depends(require_basic)):
    p=_abspath(path)
    if not p.exists() or not p.is_file():
        raise HTTPException(404)
    file_size=p.stat().st_size
    range_header=request.headers.get("range")
    if range_header is None:
        return FileResponse(p,media_type=mime_for(p),filename=p.name)
    try:
        _,rng=range_header.split("=")
        start_str,end_str=(rng.split("-")+[""])[:2]
        start=int(start_str) if start_str else 0
        end=int(end_str) if end_str else file_size-1
        end=min(end,file_size-1)
        if start>end or start<0:
            raise ValueError
    except Exception:
        raise HTTPException(416,"Invalid Range")
    def iter_file(fp: Path,s:int,e:int,chunk:int=1024*1024):
        with fp.open("rb") as f:
            f.seek(s)
            remaining=e-s+1
            while remaining>0:
                data=f.read(min(chunk,remaining))
                if not data: break
                remaining-=len(data)
                yield data
    headers={
        "Content-Range":f"bytes {start}-{end}/{file_size}",
        "Accept-Ranges":"bytes",
        "Content-Length":str(end-start+1),
        "Content-Type":mime_for(p),
        "Content-Disposition":f'inline; filename="{p.name}"',
    }
    return StreamingResponse(iter_file(p,start,end),status_code=206,headers=headers)

@app.get("/thumb")
def thumb(path: str, request: Request, _=Depends(require_basic)):
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

    etag = _etag_for(p)
    lastmod = _last_modified_for(p)
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304)
    if request.headers.get("if-modified-since") == lastmod:
        return Response(status_code=304)

    resp = FileResponse(p, media_type="image/jpeg")
    resp.headers["ETag"] = etag
    resp.headers["Last-Modified"] = lastmod
    resp.headers["Accept-Ranges"] = "bytes"
    return resp

# ---------- dashboard & stats ----------
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
    timeline = Counter()  # year -> count
    last_updated = 0.0

    for it in files:
        m = it.meta or {}
        if it.mtime > last_updated:
            last_updated = it.mtime
        if m.get("series"):
            series_set.add(m["series"])
        if m.get("publisher"):
            publishers[m["publisher"]] += 1
        # formats by extension
        ext = it.path.suffix.lower().lstrip(".") or "unknown"
        formats[ext] += 1
        # writers
        if m.get("writer"):
            for w in [x.strip() for x in m["writer"].split(",") if x.strip()]:
                writers[w] += 1
        # timeline by year
        if m.get("year"):
            try:
                y = int(m["year"])
                timeline[y] += 1
            except ValueError:
                pass

    # total covers = files in /data/thumbs
    thumbs_dir = Path("/data/thumbs")
    total_covers = 0
    if thumbs_dir.exists():
        total_covers = sum(1 for _ in thumbs_dir.glob("*.jpg"))

    # Compact publisher chart (top 15 + "Other")
    pub_labels, pub_values = [], []
    if publishers:
        top = publishers.most_common(15)
        other = sum(v for _, v in list(publishers.items())[15:])
        pub_labels = [k for k,_ in top]
        pub_values = [v for _,v in top]
        if other:
            pub_labels.append("Other")
            pub_values.append(other)

    # Timeline sorted by year
    years = sorted(timeline.keys())
    year_values = [timeline[y] for y in years]

    # Top writers (top 15)
    w_top = writers.most_common(15)
    w_labels = [k for k,_ in w_top]
    w_values = [v for _,v in w_top]

    payload: Dict[str, Any] = {
        "last_updated": last_updated,
        "total_covers": total_covers,
        "total_comics": total_comics,
        "unique_series": len(series_set),
        "unique_publishers": len(publishers),
        "formats": dict(formats) or {"cbz": 0},
        "publishers": { "labels": pub_labels, "values": pub_values },
        "timeline": { "labels": years, "values": year_values },
        "top_writers": { "labels": w_labels, "values": w_values },
    }
    return JSONResponse(payload)

# ---------- debug ----------
@app.get("/debug/children", response_class=JSONResponse)
def debug_children(path: str = ""):
    ch = list(fs_index.children(INDEX, path.strip("/")))
    return JSONResponse([{"rel": it.rel, "is_dir": it.is_dir, "name": it.name} for it in ch])