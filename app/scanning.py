"""Filesystem scanning, index management, and thumbnail precaching."""
from __future__ import annotations

import logging
import os
import threading
import time
import unicodedata
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any
import defusedxml.ElementTree as ET

from . import db
from .config import LIBRARY_DIR, PRECACHE_THUMBS, THUMB_WORKERS
from .page_cache import cbz_list_pages
from .thumbs import ERROR_LOG, generate_thumb

logger = logging.getLogger("comicopds")

# -------------------- Thumbnail state (background) ----------------

THUMB_STATUS: dict = {
    "running": False,
    "total": 0,
    "done": 0,
    "started_at": 0.0,
    "ended_at": 0.0,
}
THUMB_LOCK = threading.Lock()

# -------------------- Index state (background) --------------------

INDEX_STATUS: dict = {
    "running": False,
    "phase": "idle",
    "total": 0,
    "done": 0,
    "current": "",
    "started_at": 0.0,
    "ended_at": 0.0,
}
INDEX_LOCK = threading.Lock()

# Mapping of Windows-1252 C1 control chars (0x80-0x9F) to their Unicode equivalents.
_CP1252_MAP: dict[int, str] = {
    0x80: "\u20AC", 0x82: "\u201A", 0x83: "\u0192", 0x84: "\u201E",
    0x85: "\u2026", 0x86: "\u2020", 0x87: "\u2021", 0x88: "\u02C6",
    0x89: "\u2030", 0x8A: "\u0160", 0x8B: "\u2039", 0x8C: "\u0152",
    0x8E: "\u017D", 0x91: "\u2018", 0x92: "\u2019", 0x93: "\u201C",
    0x94: "\u201D", 0x95: "\u2022", 0x96: "\u2013", 0x97: "\u2014",
    0x98: "\u02DC", 0x99: "\u2122", 0x9A: "\u0161", 0x9B: "\u203A",
    0x9C: "\u0153", 0x9E: "\u017E", 0x9F: "\u0178",
}


def normalize_text(text: str) -> str:
    """Normalize Unicode text: NFC, fix Windows-1252 C1 chars, strip replacements."""
    text = text.translate({k: v for k, v in _CP1252_MAP.items()})
    text = unicodedata.normalize("NFC", text)
    text = text.replace("\ufffd", "")
    return text


def set_status(**kw):
    INDEX_STATUS.update(kw)


def count_cbz(root: Path) -> int:
    n = 0
    for p in root.rglob("*"):
        if p.is_file() and p.suffix.lower() == ".cbz":
            n += 1
    return n


def parent_rel(rel: str) -> str:
    return "" if "/" not in rel else rel.rsplit("/", 1)[0]


def read_comicinfo(cbz_path: Path) -> dict[str, Any]:
    """Extract ComicInfo.xml metadata from a CBZ file."""
    meta: dict[str, Any] = {}
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
                    v = normalize_text((el.text or "").strip())
                    if v:
                        meta[k] = v
                if "title" not in meta and "booktitle" in meta:
                    meta["title"] = meta.get("booktitle")
                for k in ("number", "volume", "year", "month", "day"):
                    if k in meta:
                        meta[k] = meta[k].strip()
    except (zipfile.BadZipFile, KeyError, ET.ParseError):
        pass
    except Exception:
        logger.debug("read_comicinfo error for %s", cbz_path, exc_info=True)
    return meta


def _index_progress(rel: str):
    INDEX_STATUS["done"] += 1
    INDEX_STATUS["current"] = rel


def run_scan(force: bool = False):
    """Background scanner: writes into SQLite using its own connection."""
    logger.info(f"Starting {'full' if force else 'incremental'} filesystem scan of {LIBRARY_DIR}")
    conn = db.connect()
    try:
        set_status(running=True, phase="counting", done=0, total=0, current="", started_at=time.time(), ended_at=0.0)

        scan_stats = {"new": 0, "updated": 0, "skipped": 0, "errors": 0}

        logger.info("Counting CBZ files...")
        total = count_cbz(LIBRARY_DIR)
        logger.info(f"Found {total} CBZ files, beginning index")
        set_status(total=total, phase="indexing")

        existing_items = {} if force else db.get_existing_items_mtime(conn)
        current_rels = set()
        _uncommitted = 0

        for dirpath, dirnames, filenames in os.walk(LIBRARY_DIR):
            dpath = Path(dirpath)
            if dpath != LIBRARY_DIR:
                rel_d = dpath.relative_to(LIBRARY_DIR).as_posix()
                current_rels.add(rel_d)
                db.upsert_dir(
                    conn,
                    rel=rel_d,
                    name=dpath.name,
                    parent=parent_rel(rel_d),
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

                existing = existing_items.get(rel)

                if existing and existing[0] == float(mtime) and existing[1] == int(size) and existing[2] > 0:
                    scan_stats["skipped"] += 1
                    _index_progress(rel)
                    continue
                elif existing:
                    scan_stats["updated"] += 1
                else:
                    scan_stats["new"] += 1

                try:
                    page_count = len(cbz_list_pages(p))
                except Exception:
                    page_count = 0

                db.upsert_file(
                    conn,
                    rel=rel,
                    name=p.stem,
                    size=size,
                    mtime=mtime,
                    parent=parent_rel(rel),
                    ext="cbz",
                    page_count=page_count,
                )
                meta = read_comicinfo(p)
                if meta:
                    db.upsert_meta(conn, rel=rel, meta=meta)

                _index_progress(rel)
                _uncommitted += 1
                if _uncommitted >= 100:
                    conn.commit()
                    _uncommitted = 0

                processed = scan_stats["new"] + scan_stats["updated"] + scan_stats["skipped"]
                if processed % 500 == 0:
                    logger.debug("scan progress: %d processed (%d new, %d updated, %d skipped)",
                                   processed, scan_stats["new"], scan_stats["updated"], scan_stats["skipped"])

        if _uncommitted:
            conn.commit()

        logger.info("Cleaning up deleted items...")
        removed = db.cleanup_deleted_items(conn, current_rels)
        if removed:
            logger.info("Removed %d deleted items from database", removed)
        db.prune_stale(conn)

        # Reset ad-hoc directory check timestamps after a full scan
        with _DIR_CHECK_LOCK:
            _DIR_CHECKED.clear()

        if PRECACHE_THUMBS:
            logger.info("Pre-caching thumbnails...")
            set_status(phase="thumbnails")
            run_precache_thumbs(THUMB_WORKERS)

        scan_end = time.time()
        logger.info(
            "Scan completed in %.2fs: %d new, %d updated, %d skipped, %d errors",
            scan_end - INDEX_STATUS.get('started_at', scan_end),
            scan_stats["new"], scan_stats["updated"],
            scan_stats["skipped"], scan_stats["errors"]
        )
        set_status(phase="idle", running=False, ended_at=scan_end, current="")
    except Exception as e:
        logger.error(f"scan error: {e}", exc_info=True)
        set_status(phase="idle", running=False, ended_at=time.time())
    finally:
        try:
            conn.close()
        except Exception:
            pass


def collect_cbz_rows() -> list[dict]:
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
        abs_cbz = (LIBRARY_DIR / rel)
        if abs_cbz.exists():
            generate_thumb(rel, abs_cbz, cvid)
    except Exception:
        logger.debug("thumb_task error for %s", rel, exc_info=True)
    finally:
        with THUMB_LOCK:
            THUMB_STATUS["done"] += 1


def run_precache_thumbs(workers: int):
    with THUMB_LOCK:
        THUMB_STATUS.update({"running": True, "total": 0, "done": 0, "started_at": time.time(), "ended_at": 0.0})

    # Clear stale error log — precache retries everything
    try:
        if ERROR_LOG.exists():
            ERROR_LOG.unlink()
    except OSError:
        pass

    items = collect_cbz_rows()
    total = len(items)
    with THUMB_LOCK:
        THUMB_STATUS["total"] = total

    if total == 0:
        with THUMB_LOCK:
            THUMB_STATUS.update({"running": False, "ended_at": time.time()})
        return

    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        futures = [pool.submit(_thumb_task, it["rel"], it["cvid"]) for it in items]
        for _ in as_completed(futures):
            pass

    with THUMB_LOCK:
        THUMB_STATUS.update({"running": False, "ended_at": time.time()})


def start_scan(force=False):
    if INDEX_STATUS["running"]:
        return
    t = threading.Thread(target=run_scan, args=(force,), daemon=True)
    t.start()


# -------------------- Ad-hoc directory refresh --------------------

_DIR_CHECKED: dict[str, float] = {}
_DIR_CHECK_LOCK = threading.Lock()
DIR_REFRESH_INTERVAL = float(os.getenv("DIR_REFRESH_INTERVAL", "300"))  # seconds


def refresh_directory(dir_rel: str) -> bool:
    """Non-recursive refresh of a single directory if stale. Returns True if changed."""
    now = time.time()
    with _DIR_CHECK_LOCK:
        last = _DIR_CHECKED.get(dir_rel, 0.0)
        if now - last < DIR_REFRESH_INTERVAL:
            return False
        _DIR_CHECKED[dir_rel] = now

    # Don't run during a full scan
    if INDEX_STATUS["running"]:
        return False

    abs_dir = (LIBRARY_DIR / dir_rel) if dir_rel else LIBRARY_DIR
    if not abs_dir.is_dir():
        return False

    conn = db.connect()
    changed = False
    try:
        # Current DB children for this directory
        db_children = {
            r["rel"]: (float(r["mtime"] or 0), int(r["size"] or 0), int(r["page_count"] or 0))
            for r in conn.execute(
                "SELECT rel, mtime, size, page_count FROM items WHERE parent=?",
                (dir_rel,),
            ).fetchall()
        }

        fs_rels = set()
        for entry in os.scandir(abs_dir):
            if entry.is_dir(follow_symlinks=False):
                rel = entry.path.replace("\\", "/")
                rel = Path(rel).relative_to(LIBRARY_DIR).as_posix()
                fs_rels.add(rel)
                if rel not in db_children:
                    db.upsert_dir(conn, rel=rel, name=entry.name,
                                  parent=dir_rel, mtime=entry.stat().st_mtime)
                    changed = True
            elif entry.is_file(follow_symlinks=False) and entry.name.lower().endswith(".cbz"):
                rel = Path(entry.path.replace("\\", "/")).relative_to(LIBRARY_DIR).as_posix()
                fs_rels.add(rel)
                st = entry.stat()
                existing = db_children.get(rel)
                if existing and existing[0] == float(st.st_mtime) and existing[1] == int(st.st_size) and existing[2] > 0:
                    continue  # unchanged
                # New or updated file
                try:
                    page_count = len(cbz_list_pages(Path(entry.path)))
                except Exception:
                    page_count = 0
                db.upsert_file(conn, rel=rel, name=Path(entry.name).stem,
                               size=st.st_size, mtime=st.st_mtime,
                               parent=dir_rel, ext="cbz", page_count=page_count)
                meta = read_comicinfo(Path(entry.path))
                if meta:
                    db.upsert_meta(conn, rel=rel, meta=meta)
                changed = True

        # Remove DB entries that no longer exist on disk
        deleted = set(db_children.keys()) - fs_rels
        if deleted:
            for rel in deleted:
                conn.execute("DELETE FROM items WHERE rel=?", (rel,))
                conn.execute("DELETE FROM meta WHERE rel=?", (rel,))
            changed = True

        if changed:
            conn.commit()
            logger.info("refresh_directory(%s): updated index", dir_rel or "/")
    except Exception:
        logger.debug("refresh_directory(%s) error", dir_rel, exc_info=True)
    finally:
        conn.close()
    return changed


def index_single_file(conn, abs_path: Path) -> int:
    """Index a single CBZ file into the database. Returns 1 on success, 0 on error."""
    rel = abs_path.relative_to(LIBRARY_DIR).as_posix()
    st = abs_path.stat()
    try:
        page_count = len(cbz_list_pages(abs_path))
    except Exception:
        page_count = 0
    db.upsert_file(conn, rel=rel, name=abs_path.stem, size=st.st_size,
                   mtime=st.st_mtime, parent=parent_rel(rel), ext="cbz",
                   page_count=page_count)
    meta = read_comicinfo(abs_path)
    if meta:
        db.upsert_meta(conn, rel=rel, meta=meta)
    return 1
