from __future__ import annotations

import json
import os
import re
import zipfile
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional
from xml.etree import ElementTree as ET

WARM_INDEX_PATH = Path("/data/index.json")
VALID_EXTS = {".cbz"}


@dataclass
class Item:
    path: Path
    rel: str
    name: str
    is_dir: bool
    size: int = 0
    mtime: float = 0.0
    meta: Optional[Dict[str, Any]] = None

    def to_json(self) -> Dict[str, Any]:
        return {
            "rel": self.rel,
            "name": self.name,
            "is_dir": self.is_dir,
            "size": self.size,
            "mtime": self.mtime,
            "meta": self.meta or {},
        }


def _relpath(root: Path, p: Path) -> str:
    rel = p.relative_to(root).as_posix()
    return rel


def _read_comicinfo_from_cbz(cbz_path: Path, prev_meta: Optional[dict] = None) -> Dict[str, Any]:
    """
    Read ComicInfo.xml from a CBZ. Returns {} if not present.
    """
    meta: Dict[str, Any] = {}
    try:
        with zipfile.ZipFile(cbz_path, "r") as zf:
            # find ComicInfo.xml (case-insensitive)
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
                    key = el.tag.lower()
                    val = (el.text or "").strip()
                    if not val:
                        continue
                    # normalize common fields
                    meta[key] = val
                # convenience aliases
                if "title" not in meta and "booktitle" in meta:
                    meta["title"] = meta.get("booktitle")
                # prefer Number/Year/Month/Day as simple scalars
                for k in ("number", "volume", "year", "month", "day"):
                    if k in meta:
                        meta[k] = meta[k].strip()
                return meta
    except Exception:
        # return whatever we could parse (or empty)
        return meta


def _load_warm_index_map() -> Dict[str, Dict[str, Any]]:
    """
    Return a map: rel -> {size, mtime, meta}
    """
    if not WARM_INDEX_PATH.exists():
        return {}
    try:
        data = json.loads(WARM_INDEX_PATH.read_text(encoding="utf-8"))
        # data may be list or dict, normalize to map by rel
        if isinstance(data, list):
            return {d.get("rel"): {"size": d.get("size"), "mtime": d.get("mtime"), "meta": d.get("meta")} for d in data if d.get("rel")}
        elif isinstance(data, dict):
            return data
    except Exception:
        pass
    return {}


def _save_warm_index(items: List[Item]) -> None:
    WARM_INDEX_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = [it.to_json() for it in items]
    WARM_INDEX_PATH.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")


def scan(root: Path, progress_cb=None) -> List[Item]:
    """
    Walk the library and build the index (dirs + files).
    Uses warm index to avoid re-reading CBZ metadata if size/mtime unchanged.
    Calls progress_cb(dict) after each FILE item if provided.
    """
    root = root.resolve()
    items: List[Item] = []

    prev = _load_warm_index_map()

    # Collect directories first (skip root itself)
    for dirpath, dirnames, filenames in os.walk(root):
        dpath = Path(dirpath)
        if dpath == root:
            # Don't add root as an item
            pass
        else:
            rel = _relpath(root, dpath)
            st = dpath.stat()
            items.append(Item(
                path=dpath,
                rel=rel,
                name=dpath.name,
                is_dir=True,
                size=0,
                mtime=st.st_mtime,
                meta=None
            ))

        # Files in this folder
        for fn in filenames:
            p = dpath / fn
            ext = p.suffix.lower()
            if ext not in VALID_EXTS:
                continue
            rel = _relpath(root, p)
            st = p.stat()
            key = rel
            meta = None
            prev_rec = prev.get(key)
            if prev_rec and prev_rec.get("size") == st.st_size and int(prev_rec.get("mtime", 0)) == int(st.st_mtime):
                # unchanged — reuse cached meta
                meta = prev_rec.get("meta") or {}
            else:
                meta = _read_comicinfo_from_cbz(p)

            it = Item(
                path=p,
                rel=rel,
                name=p.stem,
                is_dir=False,
                size=st.st_size,
                mtime=st.st_mtime,
                meta=meta or {}
            )
            items.append(it)
            if progress_cb:
                try:
                    progress_cb({"rel": it.rel, "size": it.size, "mtime": it.mtime})
                except Exception:
                    pass

    # Save warm index
    _save_warm_index(items)
    return items


def children(items: List[Item], rel_path: str) -> Iterable[Item]:
    """
    Return immediate children of a given folder rel_path.
    rel_path: "" for root, else "Folder/Subfolder"
    """
    rel_path = (rel_path or "").strip("/")

    def parent_of(rel: str) -> str:
        if "/" not in rel:
            return ""
        return rel.rsplit("/", 1)[0]

    # Directories whose parent == rel_path
    dirs = [it for it in items if it.is_dir and parent_of(it.rel) == rel_path]
    # Files whose parent == rel_path
    files = [it for it in items if (not it.is_dir) and parent_of(it.rel) == rel_path]
    return dirs + files
