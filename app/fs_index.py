from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Iterable, Dict, Any
import json, os

from .meta import read_comicinfo

COMIC_EXTS = {".cbz"}

@dataclass
class Item:
    path: Path
    is_dir: bool
    name: str
    rel: str
    size: int = 0
    mtime: float = 0.0
    meta: Dict[str, Any] = field(default_factory=dict)

def _cache_file() -> Path:
    return Path("/data/index.json")

def load_cache() -> Dict[str, dict]:
    p = _cache_file()
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}

def save_cache(items: List[Item]):
    p = _cache_file()
    data = {
        it.rel: {
            "is_dir": it.is_dir,
            "name": it.name,
            "size": it.size,
            "mtime": it.mtime,
            "meta": it.meta
        }
        for it in items
    }
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data), encoding="utf-8")

def scan(root: Path) -> List[Item]:
    root = root.resolve()
    cached = load_cache()
    items: List[Item] = []

    items.append(Item(path=root, is_dir=True, name=root.name or "/", rel=""))

    for dirpath, _, filenames in os.walk(root):
        base = Path(dirpath)
        rel_dir = str(base.relative_to(root)).replace("\\", "/")
        if rel_dir != ".":
            items.append(Item(path=base, is_dir=True, name=base.name, rel=rel_dir))

        for fn in filenames:
            p = base / fn
            ext = p.suffix.lower()
            if ext not in COMIC_EXTS:
                continue
            rel = str(p.relative_to(root)).replace("\\", "/")

            try:
                st = p.stat()
            except FileNotFoundError:
                continue

            size, mtime = st.st_size, st.st_mtime
            c = cached.get(rel)
            meta, name = {}, p.stem

            if c and not c.get("is_dir") and c.get("mtime") == mtime and c.get("size") == size:
                name = c.get("name") or name
                meta = c.get("meta") or {}
            else:
                meta = read_comicinfo(p)
                if meta.get("title"):
                    name = meta["title"]

            items.append(Item(path=p, is_dir=False, name=name, rel=rel, size=size, mtime=mtime, meta=meta))

    save_cache(items)
    return items

def children(items: List[Item], rel_folder: str) -> Iterable[Item]:
    prefix = (rel_folder.strip("/") + "/") if rel_folder else ""
    depth = 0 if rel_folder == "" else rel_folder.count("/") + 1
    for it in items:
        if not it.rel.startswith(prefix):
            continue
        if it.rel == rel_folder:
            continue
        if it.is_dir and it.rel.count("/") == depth:
            yield it
        elif not it.is_dir and it.rel.count("/") == depth:
            yield it
