# app/db.py
from __future__ import annotations

import re
import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Tuple

DB_PATH = Path("/data/library.db")

# Feature flag: set after schema init
HAS_FTS5: bool = False

def has_fts5() -> bool:
    """Return True if the DB initialized an FTS5 virtual table."""
    return HAS_FTS5

# ----------------------------- Connection & Schema -----------------------------

def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    # Pragmas for speed & concurrency (tweak as needed)
    try: conn.execute("PRAGMA journal_mode=WAL;")
    except Exception: pass
    try: conn.execute("PRAGMA synchronous=NORMAL;")
    except Exception: pass
    try: conn.execute("PRAGMA temp_store=MEMORY;")
    except Exception: pass
    # ~200MB page cache (negative means KiB)
    try: conn.execute("PRAGMA cache_size=-200000;")
    except Exception: pass
    _ensure_schema(conn)
    return conn

def _ensure_schema(conn: sqlite3.Connection) -> None:
    global HAS_FTS5

    # Core tables
    conn.execute("""
    CREATE TABLE IF NOT EXISTS items (
      rel    TEXT PRIMARY KEY,
      name   TEXT,
      parent TEXT,
      is_dir INTEGER NOT NULL,
      size   INTEGER,
      mtime  REAL,
      ext    TEXT
    )
    """)
    conn.execute("""
    CREATE TABLE IF NOT EXISTS meta (
      rel            TEXT PRIMARY KEY,
      title          TEXT,
      series         TEXT,
      number         TEXT,
      volume         TEXT,
      year           TEXT,
      month          TEXT,
      day            TEXT,
      writer         TEXT,
      publisher      TEXT,
      summary        TEXT,
      genre          TEXT,
      tags           TEXT,
      characters     TEXT,
      teams          TEXT,
      locations      TEXT,
      comicvineissue TEXT
    )
    """)

    # Helpful indexes
    conn.execute("CREATE INDEX IF NOT EXISTS idx_items_parent   ON items(parent)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_items_name     ON items(name)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_items_isdir    ON items(is_dir)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_meta_series    ON meta(series)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_meta_title     ON meta(title)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_meta_year      ON meta(year)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_meta_writer    ON meta(writer)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_meta_publisher ON meta(publisher)")

    # Try FTS5 — if it fails, we fall back to LIKE search
    try:
        conn.execute("""
        CREATE VIRTUAL TABLE IF NOT EXISTS fts
        USING fts5(
          rel UNINDEXED,
          text,
          tokenize = 'unicode61'
        )""")
        HAS_FTS5 = True
    except Exception:
        HAS_FTS5 = False

# ----------------------------- Scan lifecycle ---------------------------------

def begin_scan(conn: sqlite3.Connection) -> None:
    """Called once at the beginning of a full reindex."""
    conn.execute("DELETE FROM items")
    conn.execute("DELETE FROM meta")
    if HAS_FTS5:
        conn.execute("DELETE FROM fts")
    conn.commit()

def upsert_dir(conn: sqlite3.Connection, rel: str, name: str, parent: str, mtime: float) -> None:
    conn.execute(
        """
        INSERT INTO items(rel, name, parent, is_dir, size, mtime, ext)
        VALUES (?, ?, ?, 1, NULL, ?, NULL)
        ON CONFLICT(rel) DO UPDATE SET
          name=excluded.name,
          parent=excluded.parent,
          is_dir=excluded.is_dir,
          mtime=excluded.mtime
        """,
        (rel, name, parent, mtime),
    )

def upsert_file(conn: sqlite3.Connection, rel: str, name: str, size: int, mtime: float, parent: str, ext: str) -> None:
    conn.execute(
        """
        INSERT INTO items(rel, name, parent, is_dir, size, mtime, ext)
        VALUES (?, ?, ?, 0, ?, ?, ?)
        ON CONFLICT(rel) DO UPDATE SET
          name=excluded.name,
          parent=excluded.parent,
          is_dir=excluded.is_dir,
          size=excluded.size,
          mtime=excluded.mtime,
          ext=excluded.ext
        """,
        (rel, name, parent, size, mtime, ext),
    )

def upsert_meta(conn: sqlite3.Connection, rel: str, meta: Dict[str, Any]) -> None:
    fields = (
        "title","series","number","volume","year","month","day",
        "writer","publisher","summary","genre","tags","characters",
        "teams","locations","comicvineissue"
    )
    vals = [meta.get(k) for k in fields]

    exists = conn.execute("SELECT 1 FROM meta WHERE rel=?", (rel,)).fetchone() is not None
    if exists:
        sets = ",".join([f"{k}=?" for k in fields])
        conn.execute(f"UPDATE meta SET {sets} WHERE rel=?", (*vals, rel))
    else:
        cols = ",".join(fields)
        qms  = ",".join(["?"] * len(fields))
        conn.execute(f"INSERT INTO meta(rel,{cols}) VALUES (?,{qms})", (rel, *vals))

    # Refresh FTS row for this file (only if supported & it's a file)
    if HAS_FTS5:
        it = conn.execute("SELECT name, is_dir FROM items WHERE rel=?", (rel,)).fetchone()
        if not it or int(it["is_dir"]) != 0:
            return

        parts: List[str] = []
        def add(x):
            if x is not None:
                s = str(x).strip()
                if s:
                    parts.append(s)

        add(meta.get("title"))
        add(meta.get("series"))
        add(meta.get("writer"))
        add(meta.get("publisher"))
        add(meta.get("genre"))
        add(meta.get("tags"))
        add(meta.get("characters"))
        add(meta.get("teams"))
        add(meta.get("locations"))
        add(it["name"])
        add(meta.get("year"))
        add(meta.get("number"))
        add(meta.get("volume"))

        conn.execute("DELETE FROM fts WHERE rel=?", (rel,))
        if parts:
            conn.execute("INSERT INTO fts(rel, text) VALUES (?, ?)", (rel, " ".join(parts)))

def prune_stale(conn: sqlite3.Connection) -> None:
    if HAS_FTS5:
        conn.execute("""
            DELETE FROM fts
             WHERE rel NOT IN (SELECT rel FROM items WHERE is_dir=0)
        """)
    conn.commit()

# ----------------------------- Browsing ---------------------------------------

def children_count(conn: sqlite3.Connection, path: str) -> int:
    if path == "":
        row = conn.execute("SELECT COUNT(*) FROM items WHERE parent=''", ()).fetchone()
    else:
        row = conn.execute("SELECT COUNT(*) FROM items WHERE parent=?", (path,)).fetchone()
    return int(row[0]) if row else 0

def children_page(conn: sqlite3.Connection, path: str, limit: int, offset: int):
    sql_base = """
        SELECT i.*, m.*
        FROM items i
        LEFT JOIN meta m ON m.rel = i.rel
    """
    if path == "":
        sql = sql_base + " WHERE i.parent='' ORDER BY i.is_dir DESC, i.name LIMIT ? OFFSET ?"
        return conn.execute(sql, (limit, offset)).fetchall()
    else:
        sql = sql_base + " WHERE i.parent=? ORDER BY i.is_dir DESC, i.name LIMIT ? OFFSET ?"
        return conn.execute(sql, (path, limit, offset)).fetchall()

def get_item(conn: sqlite3.Connection, rel: str):
    return conn.execute("""
        SELECT i.*, m.*
        FROM items i
        LEFT JOIN meta m ON m.rel = i.rel
        WHERE i.rel=?
    """, (rel,)).fetchone()

# ----------------------------- Search (FTS5 optional + year) ------------------

_year_re = re.compile(r"\b(19|20)\d{2}\b")

def _split_query(q: str) -> Tuple[List[str], List[str]]:
    tokens = re.findall(r"[A-Za-z0-9]+", q or "")
    years  = [t for t in tokens if _year_re.fullmatch(t)]
    words  = [t for t in tokens if t not in years]
    return words, years

def _like_term(s: str) -> str:
    return f"%{s}%"

def search_q(conn: sqlite3.Connection, q: str, limit: int, offset: int):
    words, years = _split_query(q)
    params: List[Any] = []
    where: List[str] = ["i.is_dir=0"]

    if HAS_FTS5 and words:
        match = " AND ".join([f"{w}*" for w in words])
        where.append("i.rel IN (SELECT rel FROM fts WHERE fts MATCH ?)")
        params.append(match)
    elif words:
        # Fallback LIKEs on selected columns
        for w in words:
            where.append("""
            (
              i.name LIKE ? OR
              m.title LIKE ? OR
              m.series LIKE ? OR
              m.writer LIKE ? OR
              m.publisher LIKE ?
            )
            """)
            like = _like_term(w)
            params.extend([like, like, like, like, like])

    if years:
        where.append("(" + " OR ".join(["m.year=?" for _ in years]) + ")")
        params.extend(years)

    sql = f"""
    SELECT i.*, m.*
    FROM items i
    LEFT JOIN meta m ON m.rel = i.rel
    WHERE {' AND '.join(where)}
    ORDER BY
      COALESCE(m.series, i.name),
      CAST(COALESCE(NULLIF(m.number,''),'0') AS INTEGER),
      i.name
    LIMIT ? OFFSET ?
    """
    params.extend([limit, offset])
    return conn.execute(sql, params).fetchall()

def search_count(conn: sqlite3.Connection, q: str) -> int:
    words, years = _split_query(q)
    params: List[Any] = []
    where: List[str] = ["i.is_dir=0"]

    if HAS_FTS5 and words:
        match = " AND ".join([f"{w}*" for w in words])
        where.append("i.rel IN (SELECT rel FROM fts WHERE fts MATCH ?)")
        params.append(match)
    elif words:
        for w in words:
            where.append("""
            (
              i.name LIKE ? OR
              m.title LIKE ? OR
              m.series LIKE ? OR
              m.writer LIKE ? OR
              m.publisher LIKE ?
            )
            """)
            like = _like_term(w)
            params.extend([like, like, like, like, like])

    if years:
        where.append("(" + " OR ".join(["m.year=?" for _ in years]) + ")")
        params.extend(years)

    row = conn.execute(f"""
        SELECT COUNT(*)
        FROM items i
        LEFT JOIN meta m ON m.rel = i.rel
        WHERE {' AND '.join(where)}
    """, params).fetchone()
    return int(row[0]) if row else 0

# ----------------------------- Smart Lists ------------------------------------

FIELD_MAP = {
    "title": "m.title",
    "series": "m.series",
    "number": "m.number",
    "volume": "m.volume",
    "year": "m.year",
    "month": "m.month",
    "day": "m.day",
    "writer": "m.writer",
    "publisher": "m.publisher",
    "summary": "m.summary",
    "genre": "m.genre",
    "tags": "m.tags",
    "characters": "m.characters",
    "teams": "m.teams",
    "locations": "m.locations",
    "filename": "i.name",
    "name": "i.name",
}

NUMERIC_FIELDS = {"number", "volume", "year", "month", "day"}

def _field_sql(field: str) -> str:
    f = (field or "").lower()
    col = FIELD_MAP.get(f)
    if not col:
        if f in ("title","series","number","volume","year","month","day","writer","publisher",
                 "summary","genre","tags","characters","teams","locations"):
            col = f"m.{f}"
        else:
            col = "i.name"
    return col

def _numeric_cast(col: str, field: str) -> str:
    return f"CAST(COALESCE(NULLIF({col},''),'0') AS INTEGER)" if field in NUMERIC_FIELDS else col

def _rule_to_sql(rule: Dict[str, Any]) -> Tuple[str, List[Any]]:
    field = (rule.get("field") or "").lower()
    op    = (rule.get("op") or "contains").lower()
    val   = rule.get("value")
    negate = bool(rule.get("not", False))

    col = _field_sql(field)
    col_cmp = _numeric_cast(col, field)

    sql = ""
    params: List[Any] = []

    if op == "exists":
        sql = f"({col} IS NOT NULL AND {col}!='')"
    elif op == "missing":
        sql = f"({col} IS NULL OR {col}='')"
    elif op == "equals":
        sql = f"{col} = ?"
        params = [val]
    elif op == "contains":
        sql = f"{col} LIKE ?"
        params = [f"%{val}%"]
    elif op == "startswith":
        sql = f"{col} LIKE ?"
        params = [f"{val}%"]
    elif op == "endswith":
        sql = f"{col} LIKE ?"
        params = [f"%{val}"]
    elif op in ("gt","gte","lt","lte"):
        op_map = {"gt":">","gte":">=","lt":"<","lte":"<="}
        sql = f"{col_cmp} {op_map[op]} ?"
        params = [val]
    else:
        sql = f"{col} LIKE ?"
        params = [f"%{val}%"]

    if negate:
        sql = f"NOT ({sql})"

    return sql, params

def _groups_to_where(groups: List[Dict[str, Any]]) -> Tuple[str, List[Any]]:
    if not groups:
        return "1=1", []

    clauses: List[str] = []
    params: List[Any]  = []

    for g in groups:
        rs = g.get("rules") or []
        r_sqls: List[str] = []
        r_params: List[Any] = []
        for r in rs:
            s, p = _rule_to_sql(r)
            r_sqls.append(s)
            r_params.extend(p)
        if r_sqls:
            clauses.append("(" + " AND ".join(r_sqls) + ")")
            params.extend(r_params)

    if not clauses:
        return "1=1", []
    return "(" + " OR ".join(clauses) + ")", params

def _order_by_for_sort(sort: str) -> str:
    s = (sort or "").lower()
    if s == "issued_asc":
        return "CAST(COALESCE(NULLIF(m.year,''),'0') AS INTEGER) ASC, " \
               "CAST(COALESCE(NULLIF(m.month,''),'0') AS INTEGER) ASC, " \
               "CAST(COALESCE(NULLIF(m.day,''),'0') AS INTEGER) ASC, i.name ASC"
    if s == "issued_desc":
        return "CAST(COALESCE(NULLIF(m.year,''),'0') AS INTEGER) DESC, " \
               "CAST(COALESCE(NULLIF(m.month,''),'0') AS INTEGER) DESC, " \
               "CAST(COALESCE(NULLIF(m.day,''),'0') AS INTEGER) DESC, i.name ASC"
    if s == "series_asc":
        return "COALESCE(m.series, i.name) ASC, i.name ASC"
    if s == "series_desc":
        return "COALESCE(m.series, i.name) DESC, i.name ASC"
    if s == "title_asc":
        return "COALESCE(m.title, i.name) ASC"
    if s == "title_desc":
        return "COALESCE(m.title, i.name) DESC"
    if s == "added_asc":
        return "i.mtime ASC"
    if s == "added_desc":
        return "i.mtime DESC"
    return "COALESCE(m.series, i.name) ASC, " \
           "CAST(COALESCE(NULLIF(m.number,''),'0') AS INTEGER) ASC, i.name ASC"

def smartlist_query(conn: sqlite3.Connection, groups: List[Dict[str, Any]], sort: str,
                    limit: int, offset: int, distinct_by_series: bool):
    where, params = _groups_to_where(groups)
    order_clause = _order_by_for_sort(sort)

    if not distinct_by_series:
        sql = f"""
        SELECT i.*, m.*
        FROM items i
        LEFT JOIN meta m ON m.rel = i.rel
        WHERE i.is_dir=0 AND {where}
        ORDER BY {order_clause}
        LIMIT ? OFFSET ?
        """
        return conn.execute(sql, (*params, limit, offset)).fetchall()

    # Portable DISTINCT-by-series using a correlated NOT EXISTS:
    # pick the "newest" per series by (year desc, number desc, mtime desc)
    sql = f"""
    SELECT i.*, m.*
      FROM items i
      LEFT JOIN meta m ON m.rel = i.rel
     WHERE i.is_dir=0
       AND {where}
       AND (
         m.series IS NULL OR m.series='' OR
         NOT EXISTS (
           SELECT 1
             FROM items i2
             LEFT JOIN meta m2 ON m2.rel = i2.rel
            WHERE i2.is_dir=0
              AND m2.series = m.series
              AND (
                CAST(COALESCE(NULLIF(m2.year,''),'0') AS INTEGER) > CAST(COALESCE(NULLIF(m.year,''),'0') AS INTEGER) OR
                (
                  CAST(COALESCE(NULLIF(m2.year,''),'0') AS INTEGER) = CAST(COALESCE(NULLIF(m.year,''),'0') AS INTEGER)
                  AND CAST(COALESCE(NULLIF(m2.number,''),'0') AS INTEGER) > CAST(COALESCE(NULLIF(m.number,''),'0') AS INTEGER)
                ) OR
                (
                  CAST(COALESCE(NULLIF(m2.year,''),'0') AS INTEGER) = CAST(COALESCE(NULLIF(m.year,''),'0') AS INTEGER)
                  AND CAST(COALESCE(NULLIF(m2.number,''),'0') AS INTEGER) = CAST(COALESCE(NULLIF(m.number,''),'0') AS INTEGER)
                  AND i2.mtime > i.mtime
                )
              )
         )
       )
    ORDER BY {order_clause}
    LIMIT ? OFFSET ?
    """
    return conn.execute(sql, (*params, limit, offset)).fetchall()

def smartlist_count(conn: sqlite3.Connection, groups: List[Dict[str, Any]]) -> int:
    where, params = _groups_to_where(groups)
    row = conn.execute(f"""
        SELECT COUNT(*)
        FROM items i
        LEFT JOIN meta m ON m.rel = i.rel
        WHERE i.is_dir=0 AND {where}
    """, params).fetchone()
    return int(row[0]) if row else 0

# ----------------------------- Stats ------------------------------------------

def stats(conn: sqlite3.Connection) -> Dict[str, Any]:
    out: Dict[str, Any] = {}

    # Core counts
    out["total_comics"] = conn.execute(
        "SELECT COUNT(*) FROM items WHERE is_dir=0"
    ).fetchone()[0]

    out["unique_series"] = conn.execute("""
        SELECT COUNT(DISTINCT series)
          FROM meta
         WHERE series IS NOT NULL AND TRIM(series)!=''
    """).fetchone()[0]

    out["publishers"] = conn.execute("""
        SELECT COUNT(DISTINCT publisher)
          FROM meta
         WHERE publisher IS NOT NULL AND TRIM(publisher)!=''
    """).fetchone()[0]

    out["last_updated"] = conn.execute(
        "SELECT MAX(mtime) FROM items"
    ).fetchone()[0]

    # Publishers breakdown (top N) — used by doughnut chart
    top_pubs = [
        {"publisher": row[0], "count": row[1]}
        for row in conn.execute("""
           SELECT IFNULL(NULLIF(TRIM(m.publisher),''),'(Unknown)') AS publisher,
                  COUNT(*) AS c
             FROM items i
             LEFT JOIN meta m ON m.rel=i.rel
            WHERE i.is_dir=0
            GROUP BY publisher
            ORDER BY c DESC
            LIMIT 20
        """)
    ]
    out["top_publishers"] = top_pubs                     # existing name
    out["publishers_breakdown"] = top_pubs              # alias for dashboards that expect this

    # Publication timeline by year (ascending) — used by line chart
    timeline = [
        {"year": int(row[0]), "count": row[1]}
        for row in conn.execute("""
           SELECT CAST(COALESCE(NULLIF(m.year,''),'0') AS INTEGER) AS y,
                  COUNT(*) AS c
             FROM items i
             LEFT JOIN meta m ON m.rel=i.rel
            WHERE i.is_dir=0 AND TRIM(m.year)!=''
            GROUP BY y
            ORDER BY y ASC
        """)
        if row[0] is not None
    ]
    out["timeline_by_year"] = timeline                   # new
    out["publication_timeline"] = timeline               # alias (some dashboards used this name)

    # Top writers (split on commas, normalized) — used by horizontal bar chart
    rows = conn.execute("""
        SELECT m.writer
          FROM items i
          LEFT JOIN meta m ON m.rel=i.rel
         WHERE i.is_dir=0 AND m.writer IS NOT NULL AND TRIM(m.writer)!=''
    """).fetchall()

    counts: Dict[str, int] = {}
    for (w,) in rows:
        for name in (x.strip() for x in w.split(",") if x.strip()):
            key = name.lower()
            counts[key] = counts.get(key, 0) + 1

    top_writers = sorted(
        ({"writer": name.title(), "count": c} for name, c in counts.items()),
        key=lambda d: d["count"],
        reverse=True,
    )[:20]

    out["top_writers"] = top_writers

    return out

