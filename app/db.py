from __future__ import annotations
import sqlite3
import contextlib
from pathlib import Path
from typing import Iterable, Mapping, Any, Optional

DB_PATH = Path("/data/library.db")

SCHEMA_FILE = Path(__file__).with_name("schema.sql")


def connect() -> sqlite3.Connection:
    """
    Create a new connection (safe to use per-request / per-thread).
    """
    conn = sqlite3.connect(
        DB_PATH,
        check_same_thread=False,   # allow use across threads (each thread should use its own conn)
        isolation_level=None,      # autocommit; we manage explicit BEGIN via our tx() helper
    )
    conn.row_factory = sqlite3.Row
    # Pragmas for concurrency + perf
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=5000")  # wait up to 5s if DB is temporarily locked

    # Ensure schema exists (idempotent)
    conn.executescript(SCHEMA_FILE.read_text(encoding="utf-8"))
    return conn


@contextlib.contextmanager
def tx(conn: sqlite3.Connection):
    cur = conn.cursor()
    try:
        yield cur
        conn.commit()
    except Exception:
        conn.rollback()
        raise


# ----------------- scan lifecycle -----------------

def begin_scan(conn: sqlite3.Connection):
    with conn:
        conn.execute("UPDATE items SET seen=0")


def upsert_dir(conn: sqlite3.Connection, *, rel: str, name: str, parent: str, mtime: float):
    with conn:
        conn.execute(
            """INSERT INTO items(rel,name,is_dir,size,mtime,parent,ext,seen)
               VALUES(?,?,?,?,?,?,?,1)
               ON CONFLICT(rel) DO UPDATE SET
                 name=excluded.name,
                 is_dir=excluded.is_dir,
                 mtime=excluded.mtime,
                 parent=excluded.parent,
                 ext='',
                 seen=1""",
            (rel, name, 1, 0, mtime, parent, ""),
        )


def upsert_file(conn: sqlite3.Connection, *, rel: str, name: str, size: int, mtime: float, parent: str, ext: str):
    with conn:
        conn.execute(
            """INSERT INTO items(rel,name,is_dir,size,mtime,parent,ext,seen)
               VALUES(?,?,?,?,?,?,?,1)
               ON CONFLICT(rel) DO UPDATE SET
                 name=excluded.name,
                 is_dir=excluded.is_dir,
                 size=excluded.size,
                 mtime=excluded.mtime,
                 parent=excluded.parent,
                 ext=excluded.ext,
                 seen=1""",
            (rel, name, 0, size, mtime, parent, ext),
        )


def upsert_meta(conn: sqlite3.Connection, *, rel: str, meta: Mapping[str, Any]):
    # keep only fields we actually show/filter on
    keep = ("title","series","number","volume","publisher","imprint","writer",
            "year","month","day","languageiso","comicvineissue","genre","tags","summary","characters","teams","locations")
    filtered = {k: str(v) for k, v in (meta or {}).items() if k in keep and v not in (None, "")}
    cols = ",".join(filtered.keys())
    vals = [filtered[k] for k in filtered.keys()]

    with conn:
        # ensure row exists
        conn.execute("INSERT OR IGNORE INTO meta(rel) VALUES(?)", (rel,))
        if filtered:
            set_clause = ",".join(f"{k}=?" for k in filtered.keys())
            conn.execute(f"UPDATE meta SET {set_clause} WHERE rel=?", (*vals, rel))

        # update FTS (best-effort; ignore if FTS not present)
        try:
            conn.execute("DELETE FROM search WHERE rel=?", (rel,))
            conn.execute(
                """INSERT INTO search(rel,name,title,series,publisher,writer,tags,genre)
                   SELECT i.rel,i.name,m.title,m.series,m.publisher,m.writer,m.tags,m.genre
                   FROM items i LEFT JOIN meta m ON m.rel=i.rel WHERE i.rel=? AND i.is_dir=0""",
                (rel,),
            )
        except sqlite3.OperationalError:
            pass


def prune_stale(conn: sqlite3.Connection):
    with conn:
        conn.execute("DELETE FROM meta WHERE rel IN (SELECT rel FROM items WHERE seen=0)")
        conn.execute("DELETE FROM items WHERE seen=0")


# ----------------- queries -----------------

def children_page(conn: sqlite3.Connection, parent: str, limit: int, offset: int) -> list[sqlite3.Row]:
    # dirs first by name, then files by series/name + numeric number
    q = """
    SELECT i.rel,i.name,i.is_dir,i.size,i.mtime,i.ext,
           m.title,m.series,m.number,m.volume,m.publisher,m.writer,m.year,m.month,m.day,
           m.summary,m.languageiso,m.comicvineissue,m.genre,m.tags,m.characters,m.teams,m.locations
    FROM items i LEFT JOIN meta m ON m.rel=i.rel
    WHERE i.parent=?
    ORDER BY i.is_dir DESC,
             CASE WHEN i.is_dir=1 THEN LOWER(i.name) END ASC,
             CASE WHEN i.is_dir=0 THEN LOWER(COALESCE(m.series, i.name)) END ASC,
             CASE WHEN i.is_dir=0 THEN CAST(REPLACE(m.number, ',', '.') AS REAL) END ASC
    LIMIT ? OFFSET ?"""
    return conn.execute(q, (parent, limit, offset)).fetchall()


def children_count(conn: sqlite3.Connection, parent: str) -> int:
    return conn.execute("SELECT COUNT(*) FROM items WHERE parent=?", (parent,)).fetchone()[0]


def get_item(conn: sqlite3.Connection, rel: str) -> Optional[sqlite3.Row]:
    q = """SELECT i.rel,i.name,i.is_dir,i.size,i.mtime,i.ext,i.parent,
                  m.title,m.series,m.number,m.volume,m.publisher,m.writer,m.year,m.month,m.day,
                  m.summary,m.languageiso,m.comicvineissue,m.genre,m.tags,m.characters,m.teams,m.locations
           FROM items i LEFT JOIN meta m ON m.rel=i.rel WHERE i.rel=?"""
    return conn.execute(q, (rel,)).fetchone()


def stats(conn: sqlite3.Connection) -> dict:
    out = {}
    out["total_comics"] = conn.execute("SELECT COUNT(*) FROM items WHERE is_dir=0").fetchone()[0]
    out["unique_series"] = conn.execute("SELECT COUNT(DISTINCT series) FROM meta WHERE series IS NOT NULL").fetchone()[0]
    out["unique_publishers"] = conn.execute("SELECT COUNT(DISTINCT publisher) FROM meta WHERE publisher IS NOT NULL").fetchone()[0]
    out["last_updated"] = conn.execute("SELECT MAX(mtime) FROM items WHERE is_dir=0").fetchone()[0] or 0

    formats = dict(conn.execute("SELECT ext, COUNT(*) FROM items WHERE is_dir=0 GROUP BY ext"))
    out["formats"] = formats

    pubs = conn.execute("""SELECT publisher, COUNT(*) c
                           FROM meta WHERE publisher IS NOT NULL AND publisher <> ''
                           GROUP BY publisher ORDER BY c DESC""").fetchall()
    out["publishers"] = {"labels":[r[0] for r in pubs[:15]],
                         "values":[r[1] for r in pubs[:15]]}

    years = conn.execute("""SELECT CAST(year AS INT) y, COUNT(*) c
                            FROM meta WHERE year GLOB '[0-9]*'
                            GROUP BY y ORDER BY y""").fetchall()
    out["timeline"] = {"labels":[r[0] for r in years],
                       "values":[r[1] for r in years]}

    # split writers by comma into rows
    writers = conn.execute("""
        WITH split AS (
          SELECT rel, TRIM(value) w
          FROM meta m
          JOIN json_each( json_array( REPLACE(IFNULL(m.writer,''), ',', '","') ) )
        )
        SELECT w, COUNT(*) c FROM split WHERE w <> '' GROUP BY w ORDER BY c DESC LIMIT 15
    """).fetchall()
    out["top_writers"] = {"labels":[r[0] for r in writers], "values":[r[1] for r in writers]}
    return out


def search_q(conn: sqlite3.Connection, q: str, limit: int, offset: int) -> list[sqlite3.Row]:
    # Use FTS if available; else fallback to LIKE across a few fields
    q = (q or "").strip()
    if not q:
        return []
    try:
        return conn.execute(
            """SELECT i.rel,i.name,i.is_dir,i.size,i.mtime,i.ext,
                      m.title,m.series,m.number,m.volume,m.publisher,m.writer,m.year,m.month,m.day,
                      m.summary,m.languageiso,m.comicvineissue,m.genre,m.tags,m.characters,m.teams,m.locations
               FROM search s
               JOIN items i ON i.rel=s.rel
               LEFT JOIN meta m ON m.rel=i.rel
               WHERE s MATCH ? AND i.is_dir=0
               ORDER BY rank LIMIT ? OFFSET ?""",
            (q, limit, offset),
        ).fetchall()
    except sqlite3.OperationalError:
        qlike = f"%{q.lower()}%"
        return conn.execute(
            """SELECT i.rel,i.name,i.is_dir,i.size,i.mtime,i.ext,
                      m.title,m.series,m.number,m.volume,m.publisher,m.writer,m.year,m.month,m.day,
                      m.summary,m.languageiso,m.comicvineissue,m.genre,m.tags,m.characters,m.teams,m.locations
               FROM items i LEFT JOIN meta m ON m.rel=i.rel
               WHERE i.is_dir=0 AND (
                 LOWER(i.name) LIKE ? OR LOWER(IFNULL(m.title,'')) LIKE ? OR LOWER(IFNULL(m.series,'')) LIKE ?
                 OR LOWER(IFNULL(m.publisher,'')) LIKE ? OR LOWER(IFNULL(m.writer,'')) LIKE ?
               )
               LIMIT ? OFFSET ?""",
            (qlike, qlike, qlike, qlike, qlike, limit, offset),
        ).fetchall()


# ------- smart list (advanced) dynamic WHERE builder -------

def smartlist_query(conn: sqlite3.Connection, groups: list[dict], sort: str, limit: int, offset: int, distinct_series: bool) -> list[sqlite3.Row]:
    # Build WHERE (groups ORed, rules ANDed)
    where_parts = []
    params: list[Any] = []
    for g in groups or []:
        rules = g.get("rules") or []
        if not rules:
            continue
        sub, sub_p = _rules_to_where(rules)
        if sub:
            where_parts.append(f"({sub})")
            params.extend(sub_p)
    where_sql = " AND ".join(["i.is_dir=0"]) if not where_parts else "i.is_dir=0 AND (" + " OR ".join(where_parts) + ")"

    order_sql = {
        "issued_desc": "issued_sort DESC, series_sort ASC, num_sort ASC",
        "series_number": "series_sort ASC, num_sort ASC, title_sort ASC",
        "title": "title_sort ASC",
        "publisher": "publisher_sort ASC, series_sort ASC",
    }.get((sort or "").lower(), "issued_sort DESC, series_sort ASC, num_sort ASC")

    base_select = f"""
    SELECT i.rel,i.name,i.is_dir,i.size,i.mtime,i.ext,
           m.title,m.series,m.number,m.volume,m.publisher,m.writer,m.year,m.month,m.day,
           m.summary,m.languageiso,m.comicvineissue,m.genre,m.tags,m.characters,m.teams,m.locations,
           LOWER(COALESCE(m.series,i.name)) AS series_sort,
           CAST(REPLACE(m.number, ',', '.') AS REAL) AS num_sort,
           LOWER(COALESCE(m.title,i.name)) AS title_sort,
           LOWER(COALESCE(m.publisher,'')) AS publisher_sort,
           printf('%04d-%02d-%02d',
                  CASE WHEN m.year GLOB '[0-9]*' THEN CAST(m.year AS INT) ELSE 0 END,
                  CASE WHEN m.month GLOB '[0-9]*' THEN CAST(m.month AS INT) ELSE 1 END,
                  CASE WHEN m.day GLOB '[0-9]*' THEN CAST(m.day AS INT) ELSE 1 END
           ) AS issued_sort
    FROM items i LEFT JOIN meta m ON m.rel=i.rel
    WHERE {where_sql}
    """

    if distinct_series:
        # latest per series
        q = f"""
        WITH ranked AS (
          {base_select}
        )
        SELECT * FROM ranked
        WHERE series_sort <> ''
        QUALIFY ROW_NUMBER() OVER (PARTITION BY series_sort ORDER BY issued_sort DESC, num_sort DESC) = 1
        ORDER BY {order_sql}
        LIMIT ? OFFSET ?
        """
        # QUALIFY / WINDOW ROW_NUMBER needs SQLite 3.43+. If your SQLite is older, emulate with correlated subquery:
        try:
            return conn.execute(q, (*params, limit, offset)).fetchall()
        except sqlite3.OperationalError:
            q2 = f"""
            WITH ranked AS (
              {base_select}
            )
            SELECT r1.* FROM ranked r1
            WHERE NOT EXISTS (
              SELECT 1 FROM ranked r2
              WHERE r2.series_sort=r1.series_sort
                AND (r2.issued_sort>r1.issued_sort OR (r2.issued_sort=r1.issued_sort AND r2.num_sort>r1.num_sort))
            )
            ORDER BY {order_sql}
            LIMIT ? OFFSET ?
            """
            return conn.execute(q2, (*params, limit, offset)).fetchall()
    else:
        q = f"""{base_select}
        ORDER BY {order_sql}
        LIMIT ? OFFSET ?"""
        return conn.execute(q, (*params, limit, offset)).fetchall()


def smartlist_count(conn: sqlite3.Connection, groups: list[dict]) -> int:
    where_parts = []
    params: list[Any] = []
    for g in groups or []:
        rules = g.get("rules") or []
        if not rules:
            continue
        sub, sub_p = _rules_to_where(rules)
        if sub:
            where_parts.append(f"({sub})")
            params.extend(sub_p)
    where_sql = " AND ".join(["i.is_dir=0"]) if not where_parts else "i.is_dir=0 AND (" + " OR ".join(where_parts) + ")"
    q = f"SELECT COUNT(*) FROM items i LEFT JOIN meta m ON m.rel=i.rel WHERE {where_sql}"
    return conn.execute(q, params).fetchone()[0]


def _rules_to_where(rules: list[dict]) -> tuple[str, list[Any]]:
    parts = []
    params: list[Any] = []
    for r in rules:
        field = (r.get("field") or "").lower()
        op = (r.get("op") or "contains").lower()
        val = (r.get("value") or "").strip()
        neg = bool(r.get("not", False))

        # map field -> column/expression
        col = {
            "rel": "i.rel", "title": "IFNULL(m.title,i.name)", "series": "m.series",
            "number": "m.number", "volume": "m.volume", "publisher": "m.publisher",
            "imprint": "m.imprint", "writer": "m.writer", "characters":"m.characters",
            "teams":"m.teams", "tags":"IFNULL(m.tags,m.genre)", "genre":"m.genre",
            "year":"m.year", "month":"m.month", "day":"m.day",
            "languageiso":"m.languageiso", "comicvineissue":"m.comicvineissue",
            "ext":"i.ext", "size":"i.size", "mtime":"i.mtime",
        }.get(field, None)

        if op in ("exists","missing"):
            expr = f"{col} IS NOT NULL AND {col} <> ''" if col else "0"
            p = f"({expr})"
            parts.append(f"NOT {p}" if (neg ^ (op=='missing')) else p)
            continue

        if col is None:
            continue

        if op in ("=","==","!=","<",">","<=",">=") and field in ("size","mtime","year","month","day","number","volume"):
            cmp_col = f"CAST({col} AS REAL)" if field in ("year","month","day","number","volume") else col
            p = f"{cmp_col} {op if op!='==' else '='} ?"
            parts.append(("NOT " if neg else "") + p)
            params.append(float(val) if val else 0)
            continue

        if op in ("on","before","after","between") and field in ("year","month","day"):
            # synthesize issued string Y-M-D and compare lexicographically
            issued = "printf('%04d-%02d-%02d', CAST(m.year AS INT), CAST(m.month AS INT), CAST(m.day AS INT))"
            if op == "between":
                try:
                    a,b = [x.strip() for x in val.split(",",1)]
                except ValueError:
                    continue
                p = f"{issued} BETWEEN ? AND ?"
                parts.append(("NOT " if neg else "") + p)
                params.extend([_normalize_date(a), _normalize_date(b)])
            else:
                sym = {"on":"=","before":"<","after":">"}[op]
                p = f"{issued} {sym} ?"
                parts.append(("NOT " if neg else "") + p)
                params.append(_normalize_date(val))
            continue

        # text ops
        if op == "regex":
            # SQLite has no native regex; fallback to LIKE
            like = f"%{val.lower()}%"
            p = f"LOWER({col}) LIKE ?"
            parts.append(("NOT " if neg else "") + p)
            params.append(like)
        else:
            if op == "equals":
                p = f"LOWER({col}) = ?"; params.append(val.lower())
            elif op == "contains":
                p = f"LOWER({col}) LIKE ?"; params.append(f"%{val.lower()}%")
            elif op == "startswith":
                p = f"LOWER({col}) LIKE ?"; params.append(f"{val.lower()}%")
            elif op == "endswith":
                p = f"LOWER({col}) LIKE ?"; params.append(f"%{val.lower()}")
            else:
                continue
            parts.append(("NOT " if neg else "") + p)

    return (" AND ".join(parts), params)


def _normalize_date(s: str) -> str:
    s = (s or "").strip()
    if not s:
        return "0000-01-01"
    parts = s.split("-")
    try:
        if len(parts) == 1:
            y = int(parts[0]); return f"{y:04d}-01-01"
        if len(parts) == 2:
            y = int(parts[0]); m = int(parts[1]); return f"{y:04d}-{m:02d}-01"
        if len(parts) == 3:
            y = int(parts[0]); m = int(parts[1]); d = int(parts[2]); return f"{y:04d}-{m:02d}-{d:02d}"
    except Exception:
        pass
    return "0000-01-01"
