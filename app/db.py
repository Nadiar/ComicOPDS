# app/db.py
from __future__ import annotations

import re
import sqlite3
import time
from pathlib import Path
from typing import Any, Dict, List, Tuple, Optional

DB_PATH = Path("/data/library.db")

HAS_FTS5: bool = False

def has_fts5() -> bool:
    """Check if SQLite FTS5 extension is available."""
    return HAS_FTS5

def connect() -> sqlite3.Connection:
    """Create and return a new SQLite database connection with schema initialization."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try: conn.execute("PRAGMA journal_mode=WAL;")
    except Exception: pass
    try: conn.execute("PRAGMA synchronous=NORMAL;")
    except Exception: pass
    try: conn.execute("PRAGMA temp_store=MEMORY;")
    except Exception: pass
    try: conn.execute("PRAGMA cache_size=-200000;")
    except Exception: pass
    _ensure_schema(conn)
    return conn

def _column_exists(conn: sqlite3.Connection, table: str, column: str) -> bool:
    row = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return any(r[1].lower() == column.lower() for r in row)

def _add_column(conn: sqlite3.Connection, table: str, column: str, decl: str) -> None:
    try:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")
    except sqlite3.OperationalError:
        pass

def _ensure_schema(conn: sqlite3.Connection) -> None:
    global HAS_FTS5

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

    # migration: ensure 'format' column exists
    if not _column_exists(conn, "meta", "format"):
        _add_column(conn, "meta", "format", "TEXT")

    # migration: ensure 'page_count' column exists in items table
    if not _column_exists(conn, "items", "page_count"):
        _add_column(conn, "items", "page_count", "INTEGER DEFAULT 0")

    conn.execute("CREATE INDEX IF NOT EXISTS idx_items_parent   ON items(parent)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_items_name     ON items(name)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_items_isdir    ON items(is_dir)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_meta_series    ON meta(series)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_meta_title     ON meta(title)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_meta_year      ON meta(year)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_meta_writer    ON meta(writer)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_meta_publisher ON meta(publisher)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_meta_format    ON meta(format)")

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

    conn.execute("""
    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT UNIQUE NOT NULL,
        password_hash TEXT NOT NULL,
        is_admin INTEGER NOT NULL DEFAULT 0
    )
    """)

def seed_admin_user(username: str, password: str) -> None:
    """Seed the default admin user (ID=1) if it doesn't exist.

    Args:
        username: Admin username from environment
        password: Admin password from environment (plaintext, will be hashed)
    """
    import bcrypt

    conn = connect()
    try:
        admin_exists = conn.execute("SELECT 1 FROM users WHERE id=1").fetchone()
        if not admin_exists:
            hashed = bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')
            conn.execute(
                "INSERT INTO users (id, username, password_hash, is_admin) VALUES (?, ?, ?, ?)",
                (1, username, hashed, 1)
            )
            conn.commit()
    finally:
        conn.close()

# ----------------------------- Scan lifecycle ---------------------------------

def get_existing_items_mtime(conn: sqlite3.Connection) -> Dict[str, tuple[float, int, int]]:
    """Get all current items with their modification times, sizes, and page counts.

    Returns:
        Dictionary mapping rel paths to (mtime, size, page_count) tuples for incremental scan comparison.
    """
    rows = conn.execute("SELECT rel, mtime, size, page_count FROM items").fetchall()
    return {r["rel"]: (float(r["mtime"] or 0), int(r["size"] or 0), int(r["page_count"] or 0)) for r in rows}

def begin_scan(conn: sqlite3.Connection) -> None:
    # No longer deletes everything; this is an incremental scan.
    pass

def cleanup_deleted_items(conn: sqlite3.Connection, current_rels: set[str]) -> int:
    """Remove items from database that are no longer present on disk.

    Args:
        conn: SQLite connection
        current_rels: Set of rel paths that currently exist on filesystem

    Returns:
        Number of items removed from database.
    """
    rows = conn.execute("SELECT rel FROM items").fetchall()
    db_rels = {r["rel"] for r in rows}

    missing = db_rels - current_rels
    if missing:
        # SQLite maximum parameters per query is usually 999
        batch_size = 900
        missing_list = list(missing)
        for i in range(0, len(missing_list), batch_size):
            batch = missing_list[i:i + batch_size]
            holders = ",".join(["?"] * len(batch))
            conn.execute(f"DELETE FROM items WHERE rel IN ({holders})", batch)
            conn.execute(f"DELETE FROM meta WHERE rel IN ({holders})", batch)
            if HAS_FTS5:
                conn.execute(f"DELETE FROM fts WHERE rel IN ({holders})", batch)
        conn.commit()
    return len(missing)

def upsert_dir(conn: sqlite3.Connection, rel: str, name: str, parent: str, mtime: float) -> None:
    """Insert or update a directory entry in the database."""
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

def upsert_file(conn: sqlite3.Connection, rel: str, name: str, size: int, mtime: float, parent: str, ext: str, page_count: int = 0) -> None:
    """Insert or update a file entry in the database."""
    conn.execute(
        """
        INSERT INTO items(rel, name, parent, is_dir, size, mtime, ext, page_count)
        VALUES (?, ?, ?, 0, ?, ?, ?, ?)
        ON CONFLICT(rel) DO UPDATE SET
          name=excluded.name,
          parent=excluded.parent,
          is_dir=excluded.is_dir,
          size=excluded.size,
          mtime=excluded.mtime,
          ext=excluded.ext,
          page_count=excluded.page_count
        """,
        (rel, name, parent, size, mtime, ext, page_count),
    )

def upsert_meta(conn: sqlite3.Connection, rel: str, meta: Dict[str, Any]) -> None:
    """Insert or update comic metadata (from ComicInfo.xml) for an item."""
    cols = [
        "title","series","number","volume","year","month","day",
        "writer","publisher","summary","genre","tags","characters",
        "teams","locations","comicvineissue"
    ]
    if _column_exists(conn, "meta", "format"):
        cols.append("format")

    vals = [meta.get(k) for k in cols]

    exists = conn.execute("SELECT 1 FROM meta WHERE rel=?", (rel,)).fetchone() is not None
    if exists:
        sets = ",".join([f"{k}=?" for k in cols])
        conn.execute(f"UPDATE meta SET {sets} WHERE rel=?", (*vals, rel))
    else:
        col_csv = ",".join(cols)
        qms  = ",".join(["?"] * len(cols))
        conn.execute(f"INSERT INTO meta(rel,{col_csv}) VALUES (?,{qms})", (rel, *vals))

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
        if "format" in meta:
            add(meta.get("format"))

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

def feed_kind(conn: sqlite3.Connection, path: str) -> str:
    """Classify a feed as navigation or acquisition based on its direct children.

    A folder containing only file entries is an acquisition feed. All other cases,
    including empty or mixed folders, are treated as navigation feeds for safety.
    """
    if path == "":
        row = conn.execute(
            """
            SELECT
              SUM(CASE WHEN is_dir=1 THEN 1 ELSE 0 END) AS dir_count,
              SUM(CASE WHEN is_dir=0 THEN 1 ELSE 0 END) AS file_count
            FROM items
            WHERE parent=''
            """
        ).fetchone()
    else:
        row = conn.execute(
            """
            SELECT
              SUM(CASE WHEN is_dir=1 THEN 1 ELSE 0 END) AS dir_count,
              SUM(CASE WHEN is_dir=0 THEN 1 ELSE 0 END) AS file_count
            FROM items
            WHERE parent=?
            """,
            (path,),
        ).fetchone()

    dir_count = int(row["dir_count"] or 0) if row else 0
    file_count = int(row["file_count"] or 0) if row else 0
    if file_count > 0 and dir_count == 0:
        return "acquisition"
    return "navigation"

def get_item(conn: sqlite3.Connection, rel: str):
    return conn.execute("""
        SELECT i.*, m.*
        FROM items i
        LEFT JOIN meta m ON m.rel = i.rel
        WHERE i.rel=?
    """, (rel,)).fetchone()

def last_modified(conn: sqlite3.Connection) -> float:
    """Get the timestamp of the most recently modified item in the database."""
    result = conn.execute("SELECT MAX(mtime) FROM items").fetchone()
    if result and result[0]:
        return float(result[0])
    return time.time()

# ----------------------------- Search (FTS5 optional + year) ------------------

_year_re = re.compile(r"\b(19|20)\d{2}\b")

def _split_query(q: str) -> Tuple[List[str], List[str]]:
    tokens = re.findall(r"[A-Za-z0-9]+", q or "")
    years  = [t for t in tokens if _year_re.fullmatch(t)]
    words  = [t for t in tokens if t not in years]
    return words, years

def _like_term(s: str) -> str:
    return f"%{s}%"

def _search_where(q: str) -> tuple[str, list]:
    """Build WHERE clause and params for search queries."""
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

    return " AND ".join(where), params


def search_q(conn: sqlite3.Connection, q: str, limit: int, offset: int):
    """Execute full-text search query against indexed content."""
    where_clause, params = _search_where(q)
    sql = f"""
    SELECT i.*, m.*
    FROM items i
    LEFT JOIN meta m ON m.rel = i.rel
    WHERE {where_clause}
    ORDER BY
      COALESCE(m.series, i.name),
      CAST(COALESCE(NULLIF(m.number,''),'0') AS INTEGER),
      i.name
    LIMIT ? OFFSET ?
    """
    params.extend([limit, offset])
    return conn.execute(sql, params).fetchall()

def search_count(conn: sqlite3.Connection, q: str) -> int:
    """Get total count of full-text search results."""
    where_clause, params = _search_where(q)
    row = conn.execute(f"""
        SELECT COUNT(*)
        FROM items i
        LEFT JOIN meta m ON m.rel = i.rel
        WHERE {where_clause}
    """, params).fetchone()
    return int(row[0]) if row else 0

# ----------------------------- Smart Lists ------------------------------------

FIELD_MAP: Dict[str, str] = {
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
    "format": "m.format",
}

NUMERIC_FIELDS = {"number", "volume", "year", "month", "day"}

def _like_escape(s: str) -> str:
    return s.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")

def _sql_expr_for_field(field: str) -> str:
    col = FIELD_MAP.get(field, f"m.{field}")
    if field in NUMERIC_FIELDS:
        return f"CAST(NULLIF({col},'') AS INTEGER)"
    return col

def build_smartlist_where(spec_or_groups: Any) -> Tuple[str, List[Any]]:
    """
    Groups are OR'd by default. Rules inside a group are AND'd.
    """
    if isinstance(spec_or_groups, dict):
        groups = spec_or_groups.get("groups") or []
        across = (spec_or_groups.get("join") or "OR").upper()  # <<< default OR
    else:
        groups = spec_or_groups or []
        across = "OR"  # <<< default OR

    if across not in ("AND", "OR"):
        across = "OR"

    where_parts: List[str] = []
    params: List[Any] = []

    for g in groups:
        rules = g.get("rules") or []
        rule_sqls: List[str] = []

        for r in rules:
            field = (r.get("field") or "").strip()
            op    = (r.get("op") or "").strip().lower()
            value = r.get("value")
            is_not = bool(r.get("not"))

            if not field or op == "":
                continue

            expr = _sql_expr_for_field(field)

            if field in NUMERIC_FIELDS:
                try:
                    if isinstance(value, str):
                        value = value.strip()
                    value = int(value)
                except Exception:
                    rule_sqls.append("1=0")
                    continue

            if op in ("=", "eq", "equals"):
                sql = f"{expr} = ?"; params.append(value)
            elif op in ("!=", "ne", "notequals"):
                sql = f"{expr} <> ?"; params.append(value)
            elif op in (">=", "gte"):
                sql = f"{expr} >= ?"; params.append(value)
            elif op in ("<=", "lte"):
                sql = f"{expr} <= ?"; params.append(value)
            elif op in (">", "gt"):
                sql = f"{expr} > ?"; params.append(value)
            elif op in ("<", "lt"):
                sql = f"{expr} < ?"; params.append(value)
            elif op in ("contains", "~"):
                sql = f"{expr} LIKE ? ESCAPE '\\' COLLATE NOCASE"
                params.append(f"%{_like_escape(str(value))}%")
            elif op in ("startswith", "prefix"):
                sql = f"{expr} LIKE ? ESCAPE '\\' COLLATE NOCASE"
                params.append(f"{_like_escape(str(value))}%")
            elif op in ("endswith", "suffix"):
                sql = f"{expr} LIKE ? ESCAPE '\\' COLLATE NOCASE"
                params.append(f"%{_like_escape(str(value))}")
            else:
                continue

            if is_not:
                sql = f"NOT ({sql})"

            rule_sqls.append(sql)

        if rule_sqls:
            where_parts.append("(" + " AND ".join(rule_sqls) + ")")

    if not where_parts:
        return "1=1", []

    joiner = f" {across} "
    return joiner.join(where_parts), params

# ---- FTS prefilter for smartlists (matches per-group, then ORs groups) ----

_TEXT_FIELDS_FOR_FTS = {
    "title","series","publisher","writer","summary","genre",
    "tags","characters","teams","locations","name","filename","format"
}

def _fts_group_expr_from_rules(rules: List[Dict[str, Any]]) -> Optional[str]:
    """
    Build an FTS 'group' expression like: "batman* AND 2016*"
    Only from rules that are: field in text set, op in ('contains','~'), not negated, and string values.
    If the group has zero qualifying rules, return None (we'll skip FTS prefilter to avoid over-restricting).
    """
    tokens: List[str] = []
    for r in (rules or []):
        field = (r.get("field") or "").lower()
        op    = (r.get("op") or "").lower()
        val   = r.get("value")
        if field in _TEXT_FIELDS_FOR_FTS and op in ("contains","~") and isinstance(val, str) and not r.get("not"):
            tokens.extend(re.findall(r"[0-9A-Za-z]{2,}", val))
    if not tokens:
        return None
    return " AND ".join(f"{t}*" for t in tokens)

def _build_fts_prefilter(groups: List[Dict[str, Any]]) -> Tuple[str, List[Any]]:
    """
    Returns (fts_sql_fragment, params). If any group cannot be expressed in FTS, returns ("", []) to skip prefilter.
    Otherwise returns:
        AND i.rel IN (SELECT rel FROM fts WHERE fts MATCH ?)
    with a parameter like: "(g1expr) OR (g2expr) OR ..."
    """
    if not HAS_FTS5:
        return "", []
    exprs: List[str] = []
    for g in (groups or []):
        expr = _fts_group_expr_from_rules(g.get("rules") or [])
        if expr is None:
            # at least one group has no 'contains' terms -> skip FTS to avoid excluding valid rows
            return "", []
        exprs.append(f"({expr})")
    if not exprs:
        return "", []
    return " AND i.rel IN (SELECT rel FROM fts WHERE fts MATCH ?)", [" OR ".join(exprs)]

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
    if s == "publisher":
        return "COALESCE(m.publisher, '') COLLATE NOCASE ASC, m.series COLLATE NOCASE ASC, i.name ASC"
    if s == "title":
        return "COALESCE(m.title, i.name) COLLATE NOCASE ASC"
    if s == "series_number":
        return "COALESCE(m.series, i.name) COLLATE NOCASE ASC, CAST(COALESCE(NULLIF(m.number,''),'0') AS INTEGER) ASC, i.name ASC"
    if s == "added_asc":
        return "i.mtime ASC"
    if s == "added_desc":
        return "i.mtime DESC"
    return "COALESCE(m.series, i.name) ASC, " \
           "CAST(COALESCE(NULLIF(m.number,''),'0') AS INTEGER) ASC, i.name ASC"

# ---- Smartlist runners --------------------------------------------------------

def smartlist_query(  # pylint: disable=too-many-branches
    conn: sqlite3.Connection,
    groups: List[Dict[str, Any]],
    sort: str,
    limit: int,
    offset: int,
    distinct_by_series: Any
):
    """Execute smart list query with filtering and pagination."""
    where, params = build_smartlist_where(groups)
    order_clause = _order_by_for_sort(sort)

    fts_sql, fts_params = _build_fts_prefilter(groups)

    mode = "latest"
    if isinstance(distinct_by_series, str) and distinct_by_series in ("latest", "oldest"):
        use_distinct = True
        mode = distinct_by_series
    else:
        use_distinct = bool(distinct_by_series)

    if not use_distinct:
        sql = f"""
        SELECT i.*, m.*
          FROM items i
          LEFT JOIN meta m ON m.rel = i.rel
         WHERE i.is_dir=0 AND {where}{fts_sql}
         ORDER BY {order_clause}
         LIMIT ? OFFSET ?
        """
        return conn.execute(sql, (*params, *fts_params, limit, offset)).fetchall()

    cmp_year   = "CAST(COALESCE(NULLIF(m2.year,''),'0') AS INTEGER) {op} CAST(COALESCE(NULLIF(m.year,''),'0') AS INTEGER)"
    cmp_number = "CAST(COALESCE(NULLIF(m2.number,''),'0') AS INTEGER) {op} CAST(COALESCE(NULLIF(m.number,''),'0') AS INTEGER)"
    cmp_mtime  = "i2.mtime {op} i.mtime"

    if mode == "oldest":
        op_main, op_eq, op_time = "<", "=", "<"
    else:
        op_main, op_eq, op_time = ">", "=", ">"

    dominance = f"""
        (
          {cmp_year.format(op=op_main)} OR
          ({cmp_year.format(op=op_eq)} AND {cmp_number.format(op=op_main)}) OR
          ({cmp_year.format(op=op_eq)} AND {cmp_number.format(op=op_eq)} AND {cmp_mtime.format(op=op_time)})
        )
    """

    sql = f"""
    SELECT i.*, m.*
      FROM items i
      LEFT JOIN meta m ON m.rel = i.rel
     WHERE i.is_dir=0 AND {where}{fts_sql}
       AND (
         m.series IS NULL OR m.series='' OR
         NOT EXISTS (
           SELECT 1
             FROM items i2
             LEFT JOIN meta m2 ON m2.rel = i2.rel
            WHERE i2.is_dir=0
              AND m2.series = m.series
              AND COALESCE(m2.volume,'') = COALESCE(m.volume,'')
              AND {dominance}
         )
       )
    ORDER BY {order_clause}
    LIMIT ? OFFSET ?
    """
    return conn.execute(sql, (*params, *fts_params, limit, offset)).fetchall()

def smartlist_count(conn: sqlite3.Connection, groups: List[Dict[str, Any]]) -> int:
    """Get total count of items matching smart list filters."""
    where, params = build_smartlist_where(groups)
    fts_sql, fts_params = _build_fts_prefilter(groups)
    row = conn.execute(f"""
        SELECT COUNT(*)
          FROM items i
          LEFT JOIN meta m ON m.rel = i.rel
         WHERE i.is_dir=0 AND {where}{fts_sql}
    """, (*params, *fts_params)).fetchone()
    return int(row[0]) if row else 0

# ----------------------------- Stats ------------------------------------------

def stats(conn: sqlite3.Connection) -> Dict[str, Any]:
    """Get library statistics (item counts, storage size, etc.)."""
    out: Dict[str, Any] = {}

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
    out["top_publishers"] = top_pubs
    out["publishers_breakdown"] = top_pubs

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
    out["timeline_by_year"] = timeline
    out["publication_timeline"] = timeline

    # formats breakdown (expects column present; unknowns grouped)
    rows = conn.execute("""
       SELECT LOWER(TRIM(IFNULL(m.format,''))) AS fmt, COUNT(*) AS c
         FROM items i
         LEFT JOIN meta m ON m.rel=i.rel
        WHERE i.is_dir=0
        GROUP BY fmt
    """).fetchall()
    alias = {
        "trade paperback": "tpb", "tpb":"tpb",
        "hardcover":"hc", "hc":"hc",
        "one-shot":"one-shot","oneshot":"one-shot",
        "limited series":"limited series",
        "ongoing series":"ongoing series",
        "graphic novel":"graphic novel",
        "web":"web","digital":"digital"
    }
    counts: Dict[str,int] = {}
    for r in rows:
        key = (r["fmt"] or "").strip() or "(unknown)"
        key = alias.get(key, key)
        counts[key] = counts.get(key, 0) + int(r["c"])
    sorted_items = sorted(counts.items(), key=lambda kv: kv[1], reverse=True)
    top = sorted_items[:12]
    other = sum(v for _, v in sorted_items[12:])
    formats = [{"format": k, "count": v} for k, v in top]
    if other:
        formats.append({"format":"other","count":other})
    out["formats_breakdown"] = formats

    rows = conn.execute("""
        SELECT m.writer
          FROM items i
          LEFT JOIN meta m ON m.rel=i.rel
         WHERE i.is_dir=0 AND m.writer IS NOT NULL AND TRIM(m.writer)!=''
    """).fetchall()

    counts_w: Dict[str, int] = {}
    for (w,) in rows:
        for name in (x.strip() for x in w.split(",") if x.strip()):
            key = name.lower()
            counts_w[key] = counts_w.get(key, 0) + 1

    top_writers = sorted(
        ({"writer": name.title(), "count": c} for name, c in counts_w.items()),
        key=lambda d: d["count"],
        reverse=True,
    )[:20]
    out["top_writers"] = top_writers

    return out
