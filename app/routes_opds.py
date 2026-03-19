"""OPDS browse, search, download, streaming, and smart-list feed routes."""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Header, Query, Request, Response
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse

from . import db
from .auth import require_basic
from .config import LIBRARY_DIR, PAGE_SIZE, SERVER_BASE
from .feeds import (
    env, rget, abs_url, opds_media_type, opds_base, prefers_opds2,
    opds_cache_headers, xml_response,
    display_title, authors_from_row, issued_from_row,
    entry_xml_from_row, entry_json_from_row, feed, feed_json,
    OPDS_XML_MEDIA, OPDS_NAV_MEDIA, OPDS_ACQ_MEDIA,
)
from .opds import now_rfc3339, mtime_rfc3339, mime_for
from .page_cache import cbz_list_pages, book_cache_dir, ensure_page_jpeg
from .routes_admin import _load_smartlists, SMARTLISTS_PATH
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
    return {
        "Accept-Ranges": "bytes",
        "Content-Type": mime_for(p),
        "Content-Disposition": f'inline; filename="{p.name}"',
    }


def _resolve_item(item_id: int) -> tuple:
    """Look up a DB item by rowid and return (row, abs_path).

    Raises HTTPException(404) if the item doesn't exist in the DB
    or the file is missing on disk.
    """
    conn = db.connect()
    try:
        row = db.get_item_by_id(conn, item_id)
    finally:
        conn.close()
    if not row:
        raise HTTPException(404, "Item not found")
    p = (LIBRARY_DIR / row["rel"]).resolve()
    if not p.exists() or not p.is_file():
        raise HTTPException(404, "File not found")
    return row, p

# -------------------- OPDS Browse --------------------

@router.get("/opds", response_class=Response)
@router.get("/opds12", response_class=Response)
@router.get("/opds20", response_class=Response)
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
            r["rel"]: opds_media_type(db.feed_kind(conn, r["rel"]))
            for r in rows if int(r["is_dir"]) == 1
        }
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
            base = SERVER_BASE.rstrip("/")
            smart_href = abs_url(f"{ob}/smart")
            row_dicts.insert(0, {
                "is_smart": True,
                "title": "\U0001f4c1 Smart Lists",
                "href": f"{base}{smart_href}"
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
            base = SERVER_BASE.rstrip("/")
            smart_href = abs_url(f"{ob}/smart")
            smart_entry = tpl.render(
                entry_id=f"{base}{smart_href}",
                updated=updated_ts,
                title="\U0001f4c1 Smart Lists",
                is_dir=True,
                href_abs=f"{base}{smart_href}",
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
    xml = tpl.render(base=SERVER_BASE.rstrip("/"), opds_base=ob)
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
    return FileResponse(p, media_type="image/jpeg")

# -------------------- DiViNa manifest (OPDS 2.0) --------------------

DIVINA_PROFILE = "https://readium.org/webpub-manifest/profiles/divina"

@router.get("/opds/v2/manifest")
def divina_manifest(path: str = Query(..., description="Relative path to CBZ"), user: str = Depends(require_basic)):
    """Return a DiViNa (Readium Web Publication) manifest for page-level streaming."""
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

    base = SERVER_BASE.rstrip("/")
    pages = cbz_list_pages(abs_cbz)
    page_count = len(pages)

    reading_order = []
    for i in range(page_count):
        page_link: dict[str, Any] = {
            "href": f"{base}{abs_url(f'/pse/page?path={quote(path)}&page={i}')}",
            "type": "image/jpeg",
        }
        if i == 0:
            page_link["rel"] = "cover"
        reading_order.append(page_link)

    meta: dict[str, Any] = {
        "title": display_title(row),
        "conformsTo": DIVINA_PROFILE,
        "numberOfPages": page_count,
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
        p = have_thumb(path, cvid) or generate_thumb(path, abs_cbz, cvid)
        if p:
            resources.append({
                "rel": "cover",
                "href": f"{base}{abs_url('/thumb?path=' + quote(path))}",
                "type": "image/jpeg",
            })

    self_href = f"{base}{abs_url(f'/opds/v2/manifest?path={quote(path)}')}"

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

@router.get("/pse/stream", response_class=Response)
def pse_stream(path: str = Query(..., description="Relative path to CBZ"), user: str = Depends(require_basic)):
    """Optional: Atom feed per-pages (kept for compatibility)."""
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
                entry_id=f"{SERVER_BASE.rstrip('/')}{page_href}",
                updated=now_rfc3339(),
                title=f"Page {i}",
                page_href=page_href,
            )
        )

    pse_feed_tpl = env.get_template("pse_feed.xml.j2")
    self_href = f"/pse/stream?path={quote(path)}"
    xml = pse_feed_tpl.render(
        feed_id=f"{SERVER_BASE.rstrip('/')}{abs_url(self_href)}",
        updated=now_rfc3339(),
        title=f"Pages \u2014 {Path(path).name}",
        self_href=abs_url(self_href),
        start_href=abs_url("/opds"),
        entries=entries_xml,
    )
    return xml_response(xml)

@router.get("/pse/page")
def pse_page(path: str = Query(...), page: int = Query(0, ge=0), user: str = Depends(require_basic)):
    """Serve page by ZERO-BASED index to match Panels (0 == first page)."""
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
    return FileResponse(tp, media_type="image/jpeg")


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
    """DiViNa manifest by item ID — no path encoding issues."""
    row, abs_cbz = _resolve_item(item_id)
    if abs_cbz.suffix.lower() != ".cbz":
        raise HTTPException(400, "Not a CBZ")
    logger.info("book_manifest: user=%s id=%d file=%s", user, item_id, row["rel"])

    base = SERVER_BASE.rstrip("/")
    pages = cbz_list_pages(abs_cbz)
    page_count = len(pages)

    reading_order = []
    for i in range(page_count):
        page_link: dict[str, Any] = {
            "href": f"{base}{abs_url(f'/book/{item_id}/page/{i}')}",
            "type": "image/jpeg",
        }
        if i == 0:
            page_link["rel"] = "cover"
        reading_order.append(page_link)

    meta: dict[str, Any] = {
        "title": display_title(row),
        "conformsTo": DIVINA_PROFILE,
        "numberOfPages": page_count,
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
        tp = have_thumb(row["rel"], cvid) or generate_thumb(row["rel"], abs_cbz, cvid)
        if tp:
            resources.append({
                "rel": "cover",
                "href": f"{base}{abs_url(f'/book/{item_id}/thumb')}",
                "type": "image/jpeg",
            })

    self_href = f"{base}{abs_url(f'/book/{item_id}/manifest')}"

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

# -------------------- Smart Lists (OPDS browse) --------------------

@router.get("/opds/smart", response_class=Response)
@router.get("/opds12/smart", response_class=Response)
@router.get("/opds20/smart", response_class=Response)
def opds_smart_lists(request: Request, _=Depends(require_basic)):
    lists = _load_smartlists()
    conn = db.connect()
    try:
        last_mod = db.last_modified(conn)
    finally:
        conn.close()
    # Include smartlists.json mtime so edits invalidate the cache
    try:
        sl_mtime = SMARTLISTS_PATH.stat().st_mtime
        last_mod = max(last_mod, sl_mtime)
    except OSError:
        pass
    cache_hdrs = opds_cache_headers(last_mod, request)
    if cache_hdrs is None:
        return Response(status_code=304)
    updated_ts = mtime_rfc3339(last_mod)
    is_opds2 = prefers_opds2(request)
    ob = opds_base(request)

    if is_opds2:
        row_dicts = []
        base = SERVER_BASE.rstrip("/")
        for sl in lists:
            href = f"{ob}/smart/{quote(sl['slug'])}"
            row_dicts.append({
                "is_smart": True,
                "title": sl["name"],
                "href": f"{base}{abs_url(href)}"
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
                    entry_id=f"{SERVER_BASE.rstrip('/')}{abs_url(href)}",
                    updated=updated_ts,
                    title=sl["name"],
                    is_dir=True,
                    href_abs=f"{SERVER_BASE.rstrip('/')}{abs_url(href)}",
                )
            )
        xml = feed(entries, title="Smart Lists", self_href=f"{ob}/smart",
                   search_href=f"{ob}/search.xml", start_href_override=ob,
                   updated=updated_ts)
        return xml_response(xml, headers=cache_hdrs)

@router.get("/opds/smart/{slug}", response_class=Response)
@router.get("/opds12/smart/{slug}", response_class=Response)
@router.get("/opds20/smart/{slug}", response_class=Response)
def opds_smart_list(request: Request, slug: str, page: int = 1, _=Depends(require_basic)):
    lists = _load_smartlists()
    sl = next((x for x in lists if x.get("slug") == slug), None)
    if not sl:
        raise HTTPException(404, "Smart list not found")

    groups = sl.get("groups") or []
    sort = (sl.get("sort") or "issued_desc").lower()

    distinct_by   = (sl.get("distinct_by") or "").strip().lower()
    distinct_mode = (sl.get("distinct_mode") or "latest").strip().lower()
    distinct_flag = distinct_mode if distinct_by == "series_volume" else False

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

    # Include smartlists.json mtime so sort/filter edits invalidate the cache
    try:
        sl_mtime = SMARTLISTS_PATH.stat().st_mtime
        last_mod = max(last_mod, sl_mtime)
    except OSError:
        pass
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
