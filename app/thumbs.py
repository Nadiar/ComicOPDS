from __future__ import annotations

import hashlib
import logging
import re
import zipfile
from pathlib import Path

from PIL import Image

from .config import LIBRARY_DIR

logger = logging.getLogger("comicopds")
ERROR_LOG = Path("/data/thumbs_errors.log")

THUMBS_DIR = Path("/data/thumbs")
THUMBS_DIR.mkdir(parents=True, exist_ok=True)

def _thumb_name(rel: str, comicvine_issue: str | None) -> str:
    if comicvine_issue:
        safe = "".join(c for c in comicvine_issue if c.isalnum() or c in ("-", "_"))
        if safe:
            return f"{safe}.jpg"
    h = hashlib.sha256(rel.encode("utf-8")).hexdigest()
    return f"{h}.jpg"

_COVER_CANDIDATES = (
    "cover.jpg", "cover.jpeg", "cover.png", "000.jpg", "001.jpg", "0001.jpg", "1.jpg",
    "front.jpg", "folder.jpg"
)

def _choose_cover_name(names: list[str]) -> str:
    lower = {n.lower(): n for n in names}
    for key in _COVER_CANDIDATES:
        if key in lower:
            return lower[key]
    def natkey(s: str):
        return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", s)]
    images = [n for n in names if not n.endswith("/")]
    images.sort(key=natkey)
    return images[0] if images else names[0]

def _list_image_entries(zf: zipfile.ZipFile) -> list[str]:
    valid = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".tif", ".tiff"}
    return [n for n in zf.namelist() if Path(n).suffix.lower() in valid and not n.endswith("/")]

def have_thumb(rel: str, comicvine_issue: str | None) -> Path | None:
    p = THUMBS_DIR / _thumb_name(rel, comicvine_issue)
    return p if p.exists() else None

def _save_as_jpeg(src_img: Image.Image, dest: Path) -> Path:
    im = src_img
    if im.mode != "RGB":
        im = im.convert("RGB")
    dest.parent.mkdir(parents=True, exist_ok=True)
    max_dim = 1200
    w, h = im.size
    if max(w, h) > max_dim:
        if w >= h:
            nh = int(h * (max_dim / float(w)))
            im = im.resize((max_dim, nh))
        else:
            nw = int(w * (max_dim / float(h)))
            im = im.resize((nw, max_dim))
    im.save(dest, format="JPEG", quality=88, optimize=True)
    return dest

def generate_thumb(rel: str, abs_cbz_path: Path, comicvine_issue: str | None) -> Path | None:
    out = THUMBS_DIR / _thumb_name(rel, comicvine_issue)

    if out.exists():
        return out

    if not abs_cbz_path.exists() or not abs_cbz_path.is_file():
        _log_thumb_error(rel, FileNotFoundError(f"CBZ not found: {abs_cbz_path}"))
        return None

    try:
        with zipfile.ZipFile(abs_cbz_path, "r") as zf:
            images = _list_image_entries(zf)
            if not images:
                _log_thumb_error(rel, RuntimeError("No image entries in archive"))
                return None

            cover_name = _choose_cover_name(images)
            try:
                with zf.open(cover_name) as fp:
                    img = Image.open(fp)
                    img.load()
                return _save_as_jpeg(img, out)
            except KeyError as e:
                _log_thumb_error(rel, KeyError(f"Cover not found in zip: {cover_name}"))
                return None
            except Exception as e:
                _log_thumb_error(rel, e)
                return None

    except zipfile.BadZipFile as e:
        _log_thumb_error(rel, e)
        return None
    except Exception as e:
        _log_thumb_error(rel, e)
        return None

def ensure_thumb(rel: str, comicvine_issue: str | None) -> Path | None:
    existing = have_thumb(rel, comicvine_issue)
    if existing:
        return existing
    abs_cbz = (LIBRARY_DIR / rel)
    if abs_cbz.suffix.lower() != ".cbz":
        return None
    return generate_thumb(rel, abs_cbz, comicvine_issue)

def _log_thumb_error(rel: str, err: Exception):
    try:
        msg = f"{rel}: {err}"
        logger.warning(f"thumbnail error: {msg}")
        ERROR_LOG.parent.mkdir(parents=True, exist_ok=True)
        with ERROR_LOG.open("a", encoding="utf-8") as fp:
            fp.write(msg + "\n")
    except Exception:
        pass
