# app/thumbs.py
from __future__ import annotations

import hashlib
import logging
import warnings
import zipfile
from pathlib import Path
from typing import Optional

from PIL import Image, UnidentifiedImageError

from .config import LIBRARY_DIR

logger = logging.getLogger("comicopds")
warnings.simplefilter("ignore", UserWarning)  # silence noisy EXIF warnings
ERROR_LOG = Path("/data/thumbs_errors.log")

THUMBS_DIR = Path("/data/thumbs")
THUMBS_DIR.mkdir(parents=True, exist_ok=True)

# Keep consistent naming if we have a ComicVine issue id
def _thumb_name(rel: str, comicvine_issue: Optional[str]) -> str:
    if comicvine_issue:
        safe = "".join(c for c in comicvine_issue if c.isalnum() or c in ("-", "_"))
        if not safe:
            safe = comicvine_issue
        return f"{safe}.jpg"
    # stable fallback by path hash
    h = hashlib.sha1(rel.encode("utf-8")).hexdigest()
    return f"{h}.jpg"

def _cover_candidate_names():
    # common cover file names (lowercased)
    return (
        "cover.jpg", "cover.jpeg", "cover.png", "000.jpg", "001.jpg", "0001.jpg", "1.jpg",
        "front.jpg", "folder.jpg"
    )

def _choose_cover_name(names: list[str]) -> str:
    # pick best candidate; otherwise first image by natural order
    lower = {n.lower(): n for n in names}
    for key in _cover_candidate_names():
        if key in lower:
            return lower[key]
    # natural sort by numeric chunks
    import re
    def natkey(s: str):
        return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", s)]
    images = [n for n in names if not n.endswith("/")]
    images.sort(key=natkey)
    return images[0] if images else names[0]

def _list_image_entries(zf: zipfile.ZipFile) -> list[str]:
    valid = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".tif", ".tiff"}
    return [n for n in zf.namelist() if Path(n).suffix.lower() in valid and not n.endswith("/")]

def have_thumb(rel: str, comicvine_issue: Optional[str]) -> Optional[Path]:
    """Check if thumbnail already exists on disk, return path if found."""
    p = THUMBS_DIR / _thumb_name(rel, comicvine_issue)
    return p if p.exists() else None

def _save_as_jpeg(src_img: Image.Image, dest: Path) -> Path:
    im = src_img
    if im.mode not in ("RGB", "L"):
        im = im.convert("RGB")
    elif im.mode == "L":
        im = im.convert("RGB")
    dest.parent.mkdir(parents=True, exist_ok=True)
    # reasonable default size/quality; tweak if you wish
    # resize if huge (e.g., keep max dimension ≈ 1200px to save space)
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

def generate_thumb(rel: str, abs_cbz_path: Path, comicvine_issue: Optional[str]) -> Optional[Path]:
    """Generate and cache thumbnail from CBZ cover image."""
    """
    Create the thumbnail if missing. Returns the path if it exists afterwards.
    Logs errors to /data/thumbs_errors.log via _log_thumb_error().
    """
    out = THUMBS_DIR / _thumb_name(rel, comicvine_issue)

    # Already there?
    if out.exists():
        return out

    # Missing source
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
                    try:
                        img = Image.open(fp)
                        # Force decode to catch truncated/corrupt images early
                        img.load()
                    except UnidentifiedImageError as e:
                        _log_thumb_error(rel, UnidentifiedImageError(f"Unidentified image: {cover_name}"))
                        return None
                    except Exception as e:
                        _log_thumb_error(rel, e)
                        return None

                    try:
                        return _save_as_jpeg(img, out)
                    except Exception as e:
                        _log_thumb_error(rel, e)
                        return None

            except KeyError:
                _log_thumb_error(rel, KeyError(f"Cover not found in zip: {cover_name}"))
                return None

    except zipfile.BadZipFile as e:
        _log_thumb_error(rel, e)
        return None
    except Exception as e:
        _log_thumb_error(rel, e)
        return None

def ensure_thumb(rel: str, comicvine_issue: Optional[str]) -> Optional[Path]:
    """Ensure thumbnail exists for item, generating it if necessary."""
    """
    Ensure a thumb exists (lazy). Uses LIBRARY_DIR and rel to find the CBZ.
    """
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