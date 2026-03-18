"""OPDS feed rendering: shared entry extraction, XML/JSON builders."""
from __future__ import annotations

import email.utils
import hashlib
from pathlib import Path
from typing import Any, List, Optional
from urllib.parse import quote

from fastapi import Request, Response
from jinja2 import Environment, FileSystemLoader, select_autoescape

from .config import LIBRARY_DIR, SERVER_BASE, URL_PREFIX
from .opds import now_rfc3339, mtime_rfc3339, mime_for

# -------------------- Jinja2 template environment --------------------

env = Environment(
    loader=FileSystemLoader(str(Path(__file__).parent / "templates"), encoding="utf-8"),
    autoescape=select_autoescape(enabled_extensions=("xml", "html", "j2"), default=True),
)

# -------------------- OPDS media type constants --------------------

OPDS_XML_MEDIA = "application/atom+xml;profile=opds-catalog;charset=utf-8"
OPDS_NAV_MEDIA = "application/atom+xml;profile=opds-catalog;kind=navigation"
OPDS_ACQ_MEDIA = "application/atom+xml;profile=opds-catalog;kind=acquisition"


def xml_response(xml: str, headers: dict | None = None,
                 media_type: str = OPDS_XML_MEDIA) -> Response:
    """Return an XML Response with explicit UTF-8 encoding."""
    return Response(
        content=xml.encode("utf-8"),
        media_type=media_type,
        headers=headers,
    )


# -------------------- Small helpers --------------------

def rget(row, key: str, default=None):
    """Safe access for sqlite3.Row (no .get())."""
    try:
        val = row[key]
        return default if val in (None, "") else val
    except Exception:
        return default


def abs_url(p: str) -> str:
    return (URL_PREFIX + p) if URL_PREFIX else p


def opds_media_type(kind: str) -> str:
    return OPDS_ACQ_MEDIA if kind == "acquisition" else OPDS_NAV_MEDIA


def opds_base(request: Request) -> str:
    """Return the OPDS path base ('/opds', '/opds12', or '/opds20') from the request URL."""
    path = request.url.path
    if path.startswith(f"{URL_PREFIX}/opds20"):
        return "/opds20"
    if path.startswith(f"{URL_PREFIX}/opds12"):
        return "/opds12"
    return "/opds"


def prefers_opds2(request: Request) -> bool:
    if not request:
        return False
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


def opds_cache_headers(last_mod: float, request: Request | None = None) -> dict | None:
    """HTTP caching headers for OPDS feed responses.

    Returns None if the client sent a matching ETag (304 Not Modified).
    """
    path_hash = ""
    if request:
        path_key = (request.url.path + "?" + str(request.url.query or "")).encode()
        path_hash = "-" + hashlib.md5(path_key).hexdigest()[:8]
    etag = f'"opds-{int(last_mod)}{path_hash}"'
    if request:
        if_none_match = request.headers.get("if-none-match", "")
        if if_none_match == etag:
            return None
    return {
        "Cache-Control": "private, max-age=60",
        "Last-Modified": email.utils.formatdate(last_mod, usegmt=True),
        "ETag": etag,
    }


# -------------------- Shared entry data extraction --------------------

def display_title(row) -> str:
    series = rget(row, "series")
    number = rget(row, "number")
    volume = rget(row, "volume")
    title = rget(row, "title") or rget(row, "name") or ""
    if series and number:
        vol = f" ({volume})" if volume else ""
        suffix = f" — {title}" if title and title != series else ""
        return f"{series}{vol} #{number}{suffix}"
    return title


def authors_from_row(row) -> list[str]:
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


def issued_from_row(row) -> Optional[str]:
    y = rget(row, "year")
    if not y:
        return None
    try:
        m = int(rget(row, "month") or 1)
        d = int(rget(row, "day") or 1)
        return f"{int(y):04d}-{m:02d}-{d:02d}"
    except Exception:
        return None


def categories_from_row(row) -> list[str]:
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


def _entry_data_from_row(row) -> dict[str, Any]:
    """Extract common entry data from a database row.

    Both XML and JSON entry builders use this to avoid duplicating
    thumbnail/metadata logic.
    """
    base = SERVER_BASE.rstrip("/")
    rel = row["rel"]
    abs_file = LIBRARY_DIR / rel

    download_href = f"/download?path={quote(rel)}"
    stream_href = f"/stream?path={quote(rel)}"
    manifest_href = f"/opds/v2/manifest?path={quote(rel)}"

    pse_template = f"/pse/page?path={quote(rel)}&page={{pageNumber}}"
    page_count = int(rget(row, "page_count") or 0)

    comicvine_issue = rget(row, "comicvineissue")
    thumb_href_abs = None
    image_abs = None
    if (rget(row, "ext") or "").lower() == "cbz":
        # Always emit the thumbnail URL — the /thumb endpoint generates on demand.
        # This avoids blocking feed generation on synchronous PIL processing.
        image_abs = f"{base}{abs_url('/thumb?path=' + quote(rel))}"
        thumb_href_abs = image_abs

    return {
        "rel": rel,
        "abs_file": abs_file,
        "base": base,
        "title": display_title(row),
        "authors": authors_from_row(row),
        "issued": issued_from_row(row),
        "categories": categories_from_row(row),
        "summary": rget(row, "summary") or None,
        "thumb_href_abs": thumb_href_abs,
        "image_abs": image_abs,
        "download_href": download_href,
        "stream_href": stream_href,
        "manifest_href": manifest_href,
        "pse_template": pse_template,
        "page_count": page_count,
        "mime": mime_for(abs_file),
        "size_str": f"{row['size']} bytes",
        "comicvine_issue": comicvine_issue,
        "mtime": row["mtime"],
        "series": rget(row, "series"),
        "number": rget(row, "number"),
    }


# -------------------- OPDS 1.2 XML entry/feed builders --------------------

def entry_xml_from_row(row, dir_link_type: str = OPDS_NAV_MEDIA) -> str:
    tpl = env.get_template("entry.xml.j2")
    base = SERVER_BASE.rstrip("/")

    if row["is_dir"]:
        href = f"/opds?path={quote(row['rel'])}" if row["rel"] else "/opds"
        return tpl.render(
            entry_id=f"{base}{abs_url('/opds/' + quote(row['rel']))}",
            updated=mtime_rfc3339(row["mtime"]),
            title=row["name"] or "/",
            is_dir=True,
            href_abs=f"{base}{abs_url(href)}",
            dir_link_type=dir_link_type,
        )

    d = _entry_data_from_row(row)
    return tpl.render(
        entry_id=f"{base}{abs_url(d['download_href'])}",
        updated=mtime_rfc3339(d["mtime"]),
        title=d["title"],
        is_dir=False,
        download_href_abs=f"{base}{abs_url(d['download_href'])}",
        stream_href_abs=f"{base}{abs_url(d['stream_href'])}",
        pse_template_abs=f"{base}{abs_url(d['pse_template'])}",
        page_count=d["page_count"],
        mime=d["mime"],
        size_str=d["size_str"],
        thumb_href_abs=d["thumb_href_abs"],
        image_abs=d["image_abs"],
        authors=d["authors"],
        issued=d["issued"],
        summary=d["summary"],
        categories=d["categories"],
    )


def feed(entries_xml: List[str], title: str, self_href: str,
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
        feed_id=f"{base}{abs_url(self_href)}",
        updated=updated or now_rfc3339(),
        title=title,
        self_href=abs_url(self_href),
        start_href=abs_url(start_href_override or "/opds"),
        search_href=abs_url(search_href),
        base=base,
        next_href=abs_url(next_href) if next_href else None,
        self_type=self_type,
        start_type=start_type,
        next_type=next_type or self_type,
        entries=entries_xml,
        os_total=os_total,
        os_start=os_start,
        os_items=os_items,
    )


# -------------------- OPDS 2.0 JSON entry/feed builders --------------------

def entry_json_from_row(row) -> dict:
    base = SERVER_BASE.rstrip("/")
    if row["is_dir"]:
        href = f"/opds?path={quote(row['rel'])}" if row["rel"] else "/opds"
        return {
            "title": row["name"] or "/",
            "href": f"{base}{abs_url(href)}",
            "type": "application/opds+json"
        }

    d = _entry_data_from_row(row)

    meta: dict[str, Any] = {
        "title": d["title"],
        "author": [{"name": a} for a in d["authors"]],
        "identifier": f"{base}{abs_url(d['download_href'])}",
        "modified": mtime_rfc3339(d["mtime"]),
    }
    if d["page_count"]:
        meta["numberOfPages"] = d["page_count"]

    series = d["series"]
    number = d["number"]
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
                "href": f"{base}{abs_url(d['manifest_href'])}",
                "type": "application/divina+json"
            },
            {
                "rel": "http://opds-spec.org/acquisition",
                "href": f"{base}{abs_url(d['download_href'])}",
                "type": d["mime"]
            },
        ],
        "images": []
    }

    if d["issued"]:
        pub["metadata"]["published"] = d["issued"]

    if d["summary"]:
        pub["metadata"]["description"] = d["summary"]

    if d["categories"]:
        pub["metadata"]["subject"] = [{"name": c} for c in d["categories"]]

    if d["thumb_href_abs"]:
        pub["images"].append({
            "href": d["thumb_href_abs"],
            "type": "image/jpeg",
            "rel": "http://opds-spec.org/image"
        })
        pub["images"].append({
            "href": d["thumb_href_abs"],
            "type": "image/jpeg",
            "rel": "http://opds-spec.org/image/thumbnail"
        })

    return pub


def feed_json(rows: list, title: str, self_href: str,
              next_href: Optional[str] = None,
              prev_href: Optional[str] = None,
              os_total: Optional[int] = None,
              os_start: Optional[int] = None,
              os_items: Optional[int] = None,
              search_href: str = "/opds/search.xml",
              start_href_override: Optional[str] = None,
              updated: Optional[str] = None) -> dict:

    base = SERVER_BASE.rstrip("/")
    feed_dict = {
        "metadata": {
            "title": title,
            "modified": updated or now_rfc3339()
        },
        "links": [
            {"rel": "self", "href": f"{base}{abs_url(self_href)}", "type": "application/opds+json"},
            {"rel": "start", "href": f"{base}{abs_url(start_href_override or '/opds')}", "type": "application/opds+json"},
            {"rel": "search", "href": f"{base}{abs_url(search_href)}", "type": "application/opensearchdescription+xml"}
        ],
        "navigation": [],
        "publications": []
    }

    if os_total is not None:
        feed_dict["metadata"]["numberOfItems"] = os_total

    if os_items is not None:
        feed_dict["metadata"]["itemsPerPage"] = os_items

    if next_href:
        feed_dict["links"].append({"rel": "next", "href": f"{base}{abs_url(next_href)}", "type": "application/opds+json"})

    if prev_href:
        feed_dict["links"].append({"rel": "previous", "href": f"{base}{abs_url(prev_href)}", "type": "application/opds+json"})

    for r in rows:
        if isinstance(r, dict) and 'is_smart' in r:
            feed_dict["navigation"].append({"title": r["title"], "href": r["href"], "type": "application/opds+json", "rel": "subsection"})
            continue

        entry = entry_json_from_row(r)
        if r.get("is_dir") if isinstance(r, dict) else r["is_dir"]:
            entry["rel"] = "subsection"
            feed_dict["navigation"].append(entry)
        else:
            feed_dict["publications"].append(entry)

    return feed_dict
