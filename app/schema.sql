-- Items (dirs & files)
CREATE TABLE IF NOT EXISTS items (
  rel    TEXT PRIMARY KEY,
  name   TEXT,
  parent TEXT,
  is_dir INTEGER NOT NULL,
  size   INTEGER,
  mtime  REAL,
  ext    TEXT
);
CREATE INDEX IF NOT EXISTS idx_items_parent ON items(parent);
CREATE INDEX IF NOT EXISTS idx_items_name   ON items(name);
CREATE INDEX IF NOT EXISTS idx_items_isdir  ON items(is_dir);

-- ComicInfo metadata
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
  comicvineissue TEXT,
  format         TEXT
);
CREATE INDEX IF NOT EXISTS idx_meta_series    ON meta(series);
CREATE INDEX IF NOT EXISTS idx_meta_title     ON meta(title);
CREATE INDEX IF NOT EXISTS idx_meta_year      ON meta(year);
CREATE INDEX IF NOT EXISTS idx_meta_writer    ON meta(writer);
CREATE INDEX IF NOT EXISTS idx_meta_publisher ON meta(publisher);
CREATE INDEX IF NOT EXISTS idx_meta_format    ON meta(format);

-- Full-text search table (FTS5 with unicode61 tokenizer)
-- Note: Requires SQLite compiled with FTS5 support; safe to skip if unavailable
CREATE VIRTUAL TABLE IF NOT EXISTS fts
USING fts5(
  rel UNINDEXED,
  text,
  tokenize = 'unicode61'
);

-- User accounts for basic auth
CREATE TABLE IF NOT EXISTS users (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  username      TEXT UNIQUE NOT NULL,
  password_hash TEXT NOT NULL,
  is_admin      INTEGER NOT NULL DEFAULT 0
);
