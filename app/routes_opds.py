"""OPDS browse, search, download, streaming, and smart-list feed routes."""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Header, Query, Request, Response
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse

import email.utils

from . import db
from .auth import require_basic
from .config import LIBRARY_DIR, PAGE_SIZE
from .feeds import (
    BASE, env, rget, abs_url, opds_media_type, opds_base, prefers_opds2,
    opds_cache_headers, xml_response,
    display_title, authors_from_row, issued_from_row,
    entry_xml_from_row, entry_json_from_row, feed, feed_json,
    OPDS_XML_MEDIA, OPDS_NAV_MEDIA, OPDS_ACQ_MEDIA,
)
from .opds import now_rfc3339, mtime_rfc3339, mime_for
from .page_cache import cbz_list_pages, book_cache_dir, ensure_page_jpeg
from .routes_admin import _load_smartlists, SMARTLISTS_PATH
from .scanning import refresh_directory
from .thumbs import have_thumb, generate_thumb

logger = logging.getLogger("comicopds")

router = APIRouter()

# -------------------- Helpers --------------------

def _abspath(rel: str) -> Path:
    p = (LIBRARY_DIR / rel).resolve()
    if LIBRARY_DIR not in p.parents and p != LIBRARY_DIR:
        raise HTTPException(400, "Invalid path")
    return p

def _common_file_headers(p: Path) -> dict:
    safe_name = quote(p.name)
    stat = p.stat()
    etag = f'"{stat.st_size:x}-{int(stat.st_mtime):x}"'
    last_mod = email.utils.formatdate(stat.st_mtime, usegmt=True)
    return {
        "Accept-Ranges": "bytes",
        "Content-Type": mime_for(p),
        "Content-Disposition": f"inline; filename*=UTF-8''{safe_name}",
        "ETag": etag,
        "Last-Modified": last_mod,
        "Cache-Control": "private, max-age=86400",
    }


def _thumb_file_headers(p: Path) -> dict:
    stat = p.stat()
    etag = f'"{stat.st_size:x}-{int(stat.st_mtime):x}"'
    last_mod = email.utils.formatdate(stat.st_mtime, usegmt=True)
    return {
        "ETag": etag,
        "Last-Modified": last_mod,
        "Cache-Control": "private, max-age=86400",
    }


def _resolve_item(item_id: int) -> tuple:
    conn = db.connect()
    try:
        row = db.get_item_by_id(conn, item_id)
    finally:
        conn.close()
    if not row:
        raise HTTPException(404, "Item not found")
    lib_resolved = LIBRARY_DIR.resolve()
    p = (LIBRARY_DIR / row["rel"]).resolve()
    if lib_resolved not in p.parents and p != lib_resolved:
        raise HTTPException(403, "Access denied")
    if not p.exists() or not p.is_file():
        raise HTTPException(404, "File not found")
    return row, p

# -------------------- OPDS Browse --------------------

@router.get("/opds", response_class=Response)
@router.get("/opds12", response_class=Response)
@router.get("/opds20", response_class=Response)
def browse(request: Request, path: str = Query("", description="Relative folder path"), page: int = 1, _=Depends(require_basic)):
    path = path.strip("/")
    refresh_directory(path)
    conn = db.connect()
    try:
        total = db.children_count(conn, path)
        start = (page - 1) * PAGE_SIZE
        rows = db.children_page(conn, path, PAGE_SIZE, start)
        last_mod = db.last_modified(conn)
        feed_kind = db.feed_kind(conn, path)
        dir_paths = [r["rel"] for r in rows if int(r["is_dir"]) == 1]
        dir_kinds = db.feed_kind_batch(conn, dir_paths)
        dir_link_types = {p: opds_media_type(kind) for p, kind in dir_kinds.items()}
    finally:
        conn.close()

    cache_hdrs = opds_cache_headers(last_mod, request)
    if cache_hdrs is None:
        return Response(status_code=304)
    updated_ts = mtime_rfc3339(last_mod)
    is_opds2 = prefers_opds2(request)
    ob = opds_base(request)
    self_href = f"{ob}?path={quote(path)}&page={page}" if path else f"{ob}?page={page}"
    next_href = f"{ob}?path={quote(path)}&page={page+1}" if (start + PAGE_SIZE) < total else None
    prev_href = f"{ob}?path={quote(path)}&page={page-1}" if page > 1 else None

    if is_opds2:
        row_dicts = [dict(r) for r in rows]
        if path == "" and page == 1:
            smart_href = abs_url(f"{ob}/smart")
            row_dicts.insert(0, {
                "is_smart": True,
                "title": "\U0001f4c1 Smart Lists",
                "href": f"{BASE}{smart_href}"
            })

        feed_dict = feed_json(
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
            opds_prefix=ob,
        )
        return JSONResponse(content=feed_dict, media_type="application/opds+json", headers=cache_hdrs)
    else:
        entries_xml = [
            entry_xml_from_row(r, dir_link_type=dir_link_types.get(r["rel"], OPDS_NAV_MEDIA),
                               opds_prefix=ob)
            for r in rows
        ]

        # "Smart Lists" virtual folder at root/page 1
        if path == "" and page == 1:
            tpl = env.get_template("entry.xml.j2")
            smart_href = abs_url(f"{ob}/smart")
            smart_entry = tpl.render(
                entry_id=f"{BASE}{smart_href}",
                updated=updated_ts,
                title="\U0001f4c1 Smart Lists",
                is_dir=True,
                href_abs=f"{BASE}{smart_href}",
                dir_link_type=OPDS_NAV_MEDIA,
            )
            entries_xml = [smart_entry] + entries_xml

        xml = feed(entries_xml, title=f"/{path}" if path else "Library", self_href=self_href, next_href=next_href,
                   search_href=f"{ob}/search.xml", start_href_override=ob, updated=updated_ts,
                   self_type=opds_media_type(feed_kind), start_type=OPDS_NAV_MEDIA,
                   next_type=opds_media_type(feed_kind))
        return xml_response(xml, headers=cache_hdrs)

@router.get("/", response_class=Response)
def root(request: Request, _=Depends(require_basic)):
    return browse(request=request, path="", page=1)

# -------------------- OpenSearch + Search --------------------

@router.get("/opds/search.xml", response_class=Response)
@router.get("/opds12/search.xml", response_class=Response)
@router.get("/opds20/search.xml", response_class=Response)
def opensearch_description(request: Request, _=Depends(require_basic)):
    ob = opds_base(request)
    tpl = env.get_template("search-description.xml.j2")
    xml = tpl.render(base=BASE, opds_base=ob)
    return xml_response(xml, media_type="application/opensearchdescription+xml;charset=utf-8")

@router.get("/opds/search", response_class=Response)
@router.get("/opds12/search", response_class=Response)
@router.get("/opds20/search", response_class=Response)
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

    cache_hdrs = opds_cache_headers(last_mod, request)
    if cache_hdrs is None:
        return Response(status_code=304)
    updated_ts = mtime_rfc3339(last_mod)
    ob = opds_base(request)
    self_href = f"{ob}/search?query={quote(term)}&page={pg}"
    next_href = f"{ob}/search?query={quote(term)}&page={pg+1}" if (offset + len(rows)) < total else None
    prev_href = f"{ob}/search?query={quote(term)}&page={pg-1}" if pg > 1 else None

    is_opds2 = prefers_opds2(request)
    if is_opds2:
        row_dicts = [dict(r) for r in rows]
        feed_dict = feed_json(
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
            opds_prefix=ob,
        )
        return JSONResponse(content=feed_dict, media_type="application/opds+json", headers=cache_hdrs)
    else:
        entries_xml = [entry_xml_from_row(r, opds_prefix=ob) for r in rows]
        xml = feed(
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
        return xml_response(xml, headers=cache_hdrs)

# -------------------- Download / Stream --------------------

@router.head("/download")
def download_head(path: str, user: str = Depends(require_basic)):
    logger.debug("download HEAD: user=%s file=%s", user, path)
    p = _abspath(path)
    if not p.exists() or not p.is_file():
        raise HTTPException(404)
    st = p.stat()
    headers = _common_file_headers(p)
    headers["Content-Length"] = str(st.st_size)
    return Response(status_code=200, headers=headers)

@router.get("/download")
def download(path: str, request: Request, range: str | None = Header(default=None), user: str = Depends(require_basic)):
    logger.info("download: user=%s file=%s", user, path)
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

@router.head("/stream")
def stream_head(path: str, user: str = Depends(require_basic)):
    logger.debug("stream HEAD: user=%s file=%s", user, path)
    return download_head(path, user=user)

@router.get("/stream")
def stream(path: str, request: Request, range: str | None = Header(default=None), user: str = Depends(require_basic)):
    logger.info("stream: user=%s file=%s", user, path)
    return download(path=path, request=request, range=range, user=user)

# -------------------- Thumbnail --------------------

@router.get("/thumb")
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
    return FileResponse(p, media_type="image/jpeg", headers=_thumb_file_headers(p))

# -------------------- DiViNa manifest (OPDS 2.0) --------------------

DIVINA_PROFILE = "https://readium.org/webpub-manifest/profiles/divina"


def _build_divina_manifest(
    row, abs_cbz: Path, page_hrefs: list[str], thumb_href: str, self_href: str,
) -> dict:
    """Build a DiViNa (Readium Web Publication) manifest dict."""
    reading_order = []
    for i, href in enumerate(page_hrefs):
        page_link: dict[str, Any] = {"href": href, "type": "image/jpeg"}
        if i == 0:
            page_link["rel"] = "cover"
        reading_order.append(page_link)

    meta: dict[str, Any] = {
        "title": display_title(row),
        "conformsTo": DIVINA_PROFILE,
        "numberOfPages": len(page_hrefs),
        "readingProgression": "ltr",
    }

    authors = authors_from_row(row)
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

    issued = issued_from_row(row)
    if issued:
        meta["published"] = issued

    summary = rget(row, "summary")
    if summary:
        meta["description"] = summary

    resources = []
    cvid = rget(row, "comicvineissue")
    if (rget(row, "ext") or "").lower() == "cbz":
        rel_path = row["rel"]
        tp = have_thumb(rel_path, cvid) or generate_thumb(rel_path, abs_cbz, cvid)
        if tp:
            resources.append({"rel": "cover", "href": thumb_href, "type": "image/jpeg"})

    manifest = {
        "@context": "https://readium.org/webpub-manifest/context.jsonld",
        "metadata": meta,
        "links": [{"rel": "self", "href": self_href, "type": "application/divina+json"}],
        "readingOrder": reading_order,
    }
    if resources:
        manifest["resources"] = resources
    return manifest


@router.get("/opds/v2/manifest")
def divina_manifest(path: str = Query(..., description="Relative path to CBZ"), user: str = Depends(require_basic)):
    """DiViNa manifest by relative path."""
    logger.info("divina_manifest: user=%s file=%s", user, path)
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

    pages = cbz_list_pages(abs_cbz)
    page_hrefs = [f"{BASE}{abs_url(f'/pse/page?path={quote(path)}&page={i}')}" for i in range(len(pages))]
    thumb_href = f"{BASE}{abs_url('/thumb?path=' + quote(path))}"
    self_href = f"{BASE}{abs_url(f'/opds/v2/manifest?path={quote(path)}')}"

    manifest = _build_divina_manifest(row, abs_cbz, page_hrefs, thumb_href, self_href)
    return JSONResponse(content=manifest, media_type="application/divina+json")

# -------------------- PSE endpoints --------------------

@router.get("/pse/stream", response_class=Response)
def pse_stream(path: str = Query(..., description="Relative path to CBZ"), user: str = Depends(require_basic)):
    logger.info("pse_stream: user=%s file=%s", user, path)
    abs_cbz = _abspath(path)
    if not abs_cbz.exists() or not abs_cbz.is_file() or abs_cbz.suffix.lower() != ".cbz":
        raise HTTPException(404, "Book not found")

    pages = cbz_list_pages(abs_cbz)
    page_entry_tpl = env.get_template("pse_page_entry.xml.j2")
    entries_xml = []
    for i, _name in enumerate(pages, start=1):
        page_href = abs_url(f"/pse/page?path={quote(path)}&page={i}")
        entries_xml.append(
            page_entry_tpl.render(
                entry_id=f"{BASE}{page_href}",
                updated=now_rfc3339(),
                title=f"Page {i}",
                page_href=page_href,
            )
        )

    pse_feed_tpl = env.get_template("pse_feed.xml.j2")
    self_href = f"/pse/stream?path={quote(path)}"
    xml = pse_feed_tpl.render(
        feed_id=f"{BASE}{abs_url(self_href)}",
        updated=now_rfc3339(),
        title=f"Pages \u2014 {Path(path).name}",
        self_href=abs_url(self_href),
        start_href=abs_url("/opds"),
        entries=entries_xml,
    )
    return xml_response(xml)

@router.get("/pse/page")
def pse_page(path: str = Query(...), page: int = Query(0, ge=0), user: str = Depends(require_basic)):
    logger.debug("pse_page: user=%s file=%s page=%d", user, path, page)
    abs_cbz = _abspath(path)
    if not abs_cbz.exists() or not abs_cbz.is_file():
        raise HTTPException(404, "Book not found")

    pages = cbz_list_pages(abs_cbz)
    if not pages or page >= len(pages):
        raise HTTPException(404, "Page not found")

    inner = pages[page]  # zero-based
    cache_dir = book_cache_dir(path)
    dest = cache_dir / f"{page+1:04d}.jpg"
    out = ensure_page_jpeg(abs_cbz, inner, dest)
    try:
        (cache_dir / ".last").touch()
    except Exception:
        pass
    return FileResponse(out, media_type="image/jpeg")

# -------------------- ID-based endpoints (safe for all clients) ----------

@router.head("/book/{item_id}/download")
def book_download_head(item_id: int, user: str = Depends(require_basic)):
    row, p = _resolve_item(item_id)
    headers = _common_file_headers(p)
    headers["Content-Length"] = str(p.stat().st_size)
    return Response(status_code=200, headers=headers)


@router.get("/book/{item_id}/download")
def book_download(item_id: int, request: Request, range: str | None = Header(default=None), user: str = Depends(require_basic)):
    row, p = _resolve_item(item_id)
    logger.info("book_download: user=%s id=%d file=%s", user, item_id, row["rel"])
    return download(path=row["rel"], request=request, range=range, user=user)


@router.get("/book/{item_id}/thumb")
def book_thumb(item_id: int, _=Depends(require_basic)):
    row, p = _resolve_item(item_id)
    cvid = rget(row, "comicvineissue")
    tp = have_thumb(row["rel"], cvid) or generate_thumb(row["rel"], p, cvid)
    if not tp or not tp.exists():
        raise HTTPException(404, "No thumbnail")
    return FileResponse(tp, media_type="image/jpeg", headers=_thumb_file_headers(tp))


@router.get("/book/{item_id}/page/{page_num}")
def book_page(item_id: int, page_num: int, _=Depends(require_basic)):
    """Serve a page by zero-based index."""
    row, p = _resolve_item(item_id)
    if p.suffix.lower() != ".cbz":
        raise HTTPException(400, "Not a CBZ")
    pages = cbz_list_pages(p)
    if not pages or page_num >= len(pages):
        raise HTTPException(404, "Page not found")
    inner = pages[page_num]
    cache_dir = book_cache_dir(row["rel"])
    dest = cache_dir / f"{page_num+1:04d}.jpg"
    out = ensure_page_jpeg(p, inner, dest)
    try:
        (cache_dir / ".last").touch()
    except Exception:
        pass
    return FileResponse(out, media_type="image/jpeg")


@router.get("/book/{item_id}/manifest")
def book_manifest(item_id: int, user: str = Depends(require_basic)):
    """DiViNa manifest by item ID."""
    row, abs_cbz = _resolve_item(item_id)
    if abs_cbz.suffix.lower() != ".cbz":
        raise HTTPException(400, "Not a CBZ")
    logger.info("book_manifest: user=%s id=%d file=%s", user, item_id, row["rel"])

    pages = cbz_list_pages(abs_cbz)
    page_hrefs = [f"{BASE}{abs_url(f'/book/{item_id}/page/{i}')}" for i in range(len(pages))]
    thumb_href = f"{BASE}{abs_url(f'/book/{item_id}/thumb')}"
    self_href = f"{BASE}{abs_url(f'/book/{item_id}/manifest')}"

    manifest = _build_divina_manifest(row, abs_cbz, page_hrefs, thumb_href, self_href)
    return JSONResponse(content=manifest, media_type="application/divina+json")

# -------------------- Smart Lists (OPDS browse) --------------------

@router.get("/opds/smart", response_class=Response)
@router.get("/opds12/smart", response_class=Response)
@router.get("/opds20/smart", response_class=Response)
def opds_smart_lists(request: Request, _=Depends(require_basic)):
    refresh_directory("")
    lists = sorted(_load_smartlists(), key=lambda x: (x.get("name") or "").lower())
    conn = db.connect()
    try:
        last_mod = db.last_modified(conn)
    finally:
        conn.close()
    last_mod = _smartlist_cache_mod(last_mod)
    cache_hdrs = opds_cache_headers(last_mod, request)
    if cache_hdrs is None:
        return Response(status_code=304)
    updated_ts = mtime_rfc3339(last_mod)
    is_opds2 = prefers_opds2(request)
    ob = opds_base(request)

    if is_opds2:
        row_dicts = []
        for sl in lists:
            href = f"{ob}/smart/{quote(sl['slug'])}"
            row_dicts.append({
                "is_smart": True,
                "title": sl["name"],
                "href": f"{BASE}{abs_url(href)}",
            })
        feed_dict = feed_json(row_dicts, title="Smart Lists", self_href=f"{ob}/smart",
                               search_href=f"{ob}/search.xml", start_href_override=ob,
                               updated=updated_ts, opds_prefix=ob)
        return JSONResponse(content=feed_dict, media_type="application/opds+json", headers=cache_hdrs)
    else:
        tpl = env.get_template("entry.xml.j2")
        entries = []
        for sl in lists:
            href = f"{ob}/smart/{quote(sl['slug'])}"
            entries.append(
                tpl.render(
                    entry_id=f"{BASE}{abs_url(href)}",
                    updated=updated_ts,
                    title=sl["name"],
                    is_dir=True,
                    href_abs=f"{BASE}{abs_url(href)}",
                )
            )
        xml = feed(entries, title="Smart Lists", self_href=f"{ob}/smart",
                   search_href=f"{ob}/search.xml", start_href_override=ob,
                   updated=updated_ts)
        return xml_response(xml, headers=cache_hdrs)

def _smartlist_cache_mod(last_mod: float) -> float:
    """Include smartlists.json mtime so edits invalidate the cache."""
    try:
        sl_mtime = SMARTLISTS_PATH.stat().st_mtime
        return max(last_mod, sl_mtime)
    except OSError:
        return last_mod


def _vol_label(series: str, volume: str) -> str:
    """Format a virtual volume folder name: 'Series (Volume)' or just 'Series'."""
    if not series:
        return "(No Series)"
    if volume:
        return f"{series} ({volume})"
    return series


@router.get("/opds/smart/{slug}", response_class=Response)
@router.get("/opds12/smart/{slug}", response_class=Response)
@router.get("/opds20/smart/{slug}", response_class=Response)
def opds_smart_list(request: Request, slug: str, page: int = 1, _=Depends(require_basic)):
    refresh_directory("")
    lists = _load_smartlists()
    sl = next((x for x in lists if x.get("slug") == slug), None)
    if not sl:
        raise HTTPException(404, "Smart list not found")

    groups = sl.get("groups") or []
    sort = (sl.get("sort") or "issued_desc").lower()
    group_by = (sl.get("group_by") or "").strip().lower()

    distinct_by   = (sl.get("distinct_by") or "").strip().lower()
    distinct_mode = (sl.get("distinct_mode") or "latest").strip().lower()
    distinct_flag = distinct_mode if distinct_by == "series_volume" else False

    # ---- group_by=series_volume → show virtual folders ----
    if group_by == "series_volume":
        conn = db.connect()
        try:
            vols = db.smartlist_volumes(conn, groups)
            last_mod = db.last_modified(conn)
        finally:
            conn.close()

        last_mod = _smartlist_cache_mod(last_mod)
        cache_hdrs = opds_cache_headers(last_mod, request)
        if cache_hdrs is None:
            return Response(status_code=304)
        updated_ts = mtime_rfc3339(last_mod)

        ob = opds_base(request)
        is_opds2 = prefers_opds2(request)

        if is_opds2:
            nav_items = []
            for v in vols:
                label = _vol_label(v["series"], v["volume"])
                href = f"{ob}/smart/{quote(slug)}/vol?series={quote(v['series'], safe='')}&volume={quote(v['volume'], safe='')}"
                nav_items.append({
                    "is_smart": True,
                    "title": f"{label} ({v['issue_count']})",
                    "href": f"{BASE}{abs_url(href)}",
                })
            feed_dict = feed_json(nav_items, title=sl["name"],
                                   self_href=f"{ob}/smart/{quote(slug)}",
                                   search_href=f"{ob}/search.xml", start_href_override=ob,
                                   updated=updated_ts, opds_prefix=ob)
            return JSONResponse(content=feed_dict, media_type="application/opds+json", headers=cache_hdrs)
        else:
            tpl = env.get_template("entry.xml.j2")
            entries = []
            for v in vols:
                label = _vol_label(v["series"], v["volume"])
                href = f"{ob}/smart/{quote(slug)}/vol?series={quote(v['series'], safe='')}&volume={quote(v['volume'], safe='')}"
                entries.append(tpl.render(
                    entry_id=f"{BASE}{abs_url(href)}",
                    updated=updated_ts,
                    title=f"{label} ({v['issue_count']})",
                    is_dir=True,
                    href_abs=f"{BASE}{abs_url(href)}",
                ))
            xml = feed(entries, title=sl["name"],
                       self_href=f"{ob}/smart/{quote(slug)}",
                       search_href=f"{ob}/search.xml", start_href_override=ob,
                       updated=updated_ts)
            return xml_response(xml, headers=cache_hdrs)

    # ---- flat issue list (default) ----
    sl_limit = int(sl.get("limit") or 0)

    page = max(1, int(page))
    page_size = PAGE_SIZE
    start = (page - 1) * page_size

    effective_page_size = page_size if sl_limit == 0 else max(0, min(page_size, sl_limit - start))

    conn = db.connect()
    try:
        rows = db.smartlist_query(conn, groups, sort, effective_page_size, start, distinct_flag)
        total = db.smartlist_count(conn, groups)
        last_mod = db.last_modified(conn)
    finally:
        conn.close()

    last_mod = _smartlist_cache_mod(last_mod)
    cache_hdrs = opds_cache_headers(last_mod, request)
    if cache_hdrs is None:
        return Response(status_code=304)
    updated_ts = mtime_rfc3339(last_mod)

    total_for_nav = min(total, sl_limit) if sl_limit > 0 else total

    ob = opds_base(request)
    self_href = f"{ob}/smart/{quote(slug)}?page={page}"
    next_href = None
    if (start + len(rows)) < total_for_nav:
        next_href = f"{ob}/smart/{quote(slug)}?page={page+1}"
    prev_href = f"{ob}/smart/{quote(slug)}?page={page-1}" if page > 1 else None

    is_opds2 = prefers_opds2(request)
    if is_opds2:
        row_dicts = [dict(r) for r in rows]
        feed_dict = feed_json(
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
            opds_prefix=ob,
        )
        return JSONResponse(content=feed_dict, media_type="application/opds+json", headers=cache_hdrs)
    else:
        entries_xml = [entry_xml_from_row(r, opds_prefix=ob) for r in rows]
        xml = feed(entries_xml, title=sl["name"], self_href=self_href, next_href=next_href,
                   search_href=f"{ob}/search.xml", start_href_override=ob, updated=updated_ts)
        return xml_response(xml, headers=cache_hdrs)


@router.get("/opds/smart/{slug}/vol", response_class=Response)
@router.get("/opds12/smart/{slug}/vol", response_class=Response)
@router.get("/opds20/smart/{slug}/vol", response_class=Response)
def opds_smart_list_volume(
    request: Request,
    slug: str,
    series: str = Query("", description="Series name"),
    volume: str = Query("", description="Volume identifier"),
    page: int = 1,
    _=Depends(require_basic),
):
    refresh_directory("")
    lists = _load_smartlists()
    sl = next((x for x in lists if x.get("slug") == slug), None)
    if not sl:
        raise HTTPException(404, "Smart list not found")

    groups = sl.get("groups") or []
    sort = (sl.get("sort") or "issued_desc").lower()

    page = max(1, int(page))
    page_size = PAGE_SIZE
    start = (page - 1) * page_size

    conn = db.connect()
    try:
        rows = db.smartlist_query_for_volume(conn, groups, series, volume, sort, page_size, start)
        total = db.smartlist_count_for_volume(conn, groups, series, volume)
        last_mod = db.last_modified(conn)
    finally:
        conn.close()

    last_mod = _smartlist_cache_mod(last_mod)
    cache_hdrs = opds_cache_headers(last_mod, request)
    if cache_hdrs is None:
        return Response(status_code=304)
    updated_ts = mtime_rfc3339(last_mod)

    ob = opds_base(request)
    vol_label = _vol_label(series, volume)
    qs = f"series={quote(series, safe='')}&volume={quote(volume, safe='')}"
    self_href = f"{ob}/smart/{quote(slug)}/vol?{qs}&page={page}"
    next_href = f"{ob}/smart/{quote(slug)}/vol?{qs}&page={page+1}" if (start + len(rows)) < total else None
    prev_href = f"{ob}/smart/{quote(slug)}/vol?{qs}&page={page-1}" if page > 1 else None

    is_opds2 = prefers_opds2(request)
    if is_opds2:
        row_dicts = [dict(r) for r in rows]
        feed_dict = feed_json(
            row_dicts,
            title=vol_label,
            self_href=self_href,
            next_href=next_href,
            prev_href=prev_href,
            os_total=total,
            os_start=start + 1 if total > 0 else 0,
            os_items=PAGE_SIZE,
            search_href=f"{ob}/search.xml",
            start_href_override=ob,
            updated=updated_ts,
            opds_prefix=ob,
        )
        return JSONResponse(content=feed_dict, media_type="application/opds+json", headers=cache_hdrs)
    else:
        entries_xml = [entry_xml_from_row(r, opds_prefix=ob) for r in rows]
        xml = feed(entries_xml, title=vol_label, self_href=self_href, next_href=next_href,
                   search_href=f"{ob}/search.xml", start_href_override=ob, updated=updated_ts)
        return xml_response(xml, headers=cache_hdrs)
