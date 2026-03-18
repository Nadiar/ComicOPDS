"""Page extraction, caching, and cleanup for CBZ page streaming."""
from __future__ import annotations

import hashlib
import logging
import os
import re
import time
import zipfile
from pathlib import Path

from PIL import Image

from .config import (
    PAGE_CACHE_DIR, PAGE_CACHE_TTL_DAYS, PAGE_CACHE_MAX_BYTES,
    PAGE_CACHE_CLEAN_INTERVAL_MIN,
)

logger = logging.getLogger("comicopds")

VALID_PAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".tif", ".tiff"}


def cbz_list_pages(cbz_path: Path) -> list[str]:
    """List image entries in a CBZ file, natural-sorted."""
    with zipfile.ZipFile(cbz_path, "r") as zf:
        names = [n for n in zf.namelist()
                 if Path(n).suffix.lower() in VALID_PAGE_EXTS and not n.endswith("/")]

    def natkey(s: str):
        return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", s)]

    names.sort(key=natkey)
    return names


def book_cache_dir(rel_path: str) -> Path:
    """Get or create a cache directory for a book's extracted pages."""
    h = hashlib.sha1(rel_path.encode("utf-8")).hexdigest()
    d = PAGE_CACHE_DIR / h
    d.mkdir(parents=True, exist_ok=True)
    return d


def ensure_page_jpeg(cbz_path: Path, inner_name: str, dest: Path) -> Path:
    """Extract a page from a CBZ and save as JPEG. Returns dest path."""
    if dest.exists():
        return dest
    with zipfile.ZipFile(cbz_path, "r") as zf:
        with zf.open(inner_name) as fp:
            im = Image.open(fp)
            if im.mode not in ("RGB",):
                im = im.convert("RGB")
            dest.parent.mkdir(parents=True, exist_ok=True)
            im.save(dest, format="JPEG", quality=90, optimize=True)
    return dest


# -------- Page cache cleanup --------

_LAST_CACHE_CLEAN: dict = {"ts": 0.0, "deleted_dirs": 0, "deleted_bytes": 0, "reason": ""}


def _dir_size(p: Path) -> int:
    total = 0
    for root, _, files in os.walk(p):
        for fn in files:
            try:
                total += (Path(root) / fn).stat().st_size
            except OSError:
                pass
    return total


def _book_cache_entries() -> list[tuple[Path, float, int]]:
    """Returns list of (dir_path, last_mtime, size_bytes) for each book cache dir."""
    entries = []
    if not PAGE_CACHE_DIR.exists():
        return entries
    for d in PAGE_CACHE_DIR.iterdir():
        if not d.is_dir():
            continue
        hb = d / ".last"
        try:
            last = hb.stat().st_mtime if hb.exists() else d.stat().st_mtime
        except OSError:
            last = 0.0
        try:
            sz = _dir_size(d)
        except OSError:
            sz = 0
        entries.append((d, last, sz))
    return entries


def _remove_dir(p: Path) -> int:
    """Remove directory tree, return bytes freed (best-effort)."""
    size = 0
    try:
        size = _dir_size(p)
    except OSError:
        pass
    try:
        for root, dirs, files in os.walk(p, topdown=False):
            for fn in files:
                try:
                    (Path(root) / fn).unlink()
                except OSError:
                    pass
            for dn in dirs:
                try:
                    (Path(root) / dn).rmdir()
                except OSError:
                    pass
        p.rmdir()
    except OSError:
        pass
    return size


def clean_page_cache(ttl_days: int, max_bytes: int) -> dict:
    """Evict page cache entries by TTL and size cap."""
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
        entries = _book_cache_entries()

    # 2) Size cap eviction
    total_bytes = sum(sz for _d, _last, sz in entries)
    if max_bytes > 0 and total_bytes > max_bytes:
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


def page_cache_status() -> dict:
    """Return current page cache statistics."""
    entries = _book_cache_entries()
    return {
        "dir_count": len(entries),
        "total_bytes": sum(sz for _d, _last, sz in entries),
        "last_clean": _LAST_CACHE_CLEAN,
        "ttl_days": PAGE_CACHE_TTL_DAYS,
        "max_bytes": PAGE_CACHE_MAX_BYTES,
    }


def autoclean_loop():
    """Background loop that periodically cleans the page cache."""
    while True:
        try:
            clean_page_cache(PAGE_CACHE_TTL_DAYS, PAGE_CACHE_MAX_BYTES)
        except Exception as e:
            logger.error(f"page cache autoclean error: {e}")
        interval = max(1, PAGE_CACHE_CLEAN_INTERVAL_MIN) * 60
        time.sleep(interval)
