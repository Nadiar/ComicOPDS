import hashlib, re, io, zipfile
from pathlib import Path
from typing import Optional
from PIL import Image

THUMB_DIR: Path = Path("/data/thumbs")
THUMB_DIR.mkdir(parents=True, exist_ok=True)

SAFE = re.compile(r"[^A-Za-z0-9._-]")

def _sanitize(name: str) -> str:
    name = SAFE.sub("_", name).strip("._")
    return name[:128] or "unnamed"

def _thumb_name(rel: str, comicvine_issue: Optional[str]) -> str:
    if comicvine_issue:
        return _sanitize(comicvine_issue) + ".jpg"
    return hashlib.sha1(rel.encode("utf-8")).hexdigest() + ".jpg"

def thumb_path(rel: str, comicvine_issue: Optional[str]) -> Path:
    return THUMB_DIR / _thumb_name(rel, comicvine_issue)

def have_thumb(rel: str, comicvine_issue: Optional[str]) -> Optional[Path]:
    p = thumb_path(rel, comicvine_issue)
    return p if p.exists() else None

def _first_image_from_cbz(fp: Path) -> Optional[bytes]:
    try:
        with zipfile.ZipFile(fp, "r") as zf:
            names = sorted(zf.namelist())
            for n in names:
                ln = n.lower()
                if ln.endswith((".jpg", ".jpeg", ".png", ".webp")) and not ln.endswith("/"):
                    with zf.open(n) as f:
                        return f.read()
    except Exception:
        return None
    return None

def generate_thumb(rel: str, abs_path: Path, comicvine_issue: Optional[str], size=(512, 512)) -> Optional[Path]:
    out = thumb_path(rel, comicvine_issue)
    out.parent.mkdir(parents=True, exist_ok=True)

    img_bytes = _first_image_from_cbz(abs_path)
    if img_bytes is None:
        return None

    try:
        im = Image.open(io.BytesIO(img_bytes)).convert("RGB")
        im.thumbnail(size)
        im.save(out, "JPEG", quality=85)
        return out
    except Exception:
        return None
