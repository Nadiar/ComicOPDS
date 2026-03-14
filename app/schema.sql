-- Filesystem entries (directories and files)
CREATE TABLE IF NOT EXISTS items (
  rel     TEXT PRIMARY KEY,
  name    TEXT,
  parent  TEXT,
  is_dir  INTEGER NOT NULL,
  size    INTEGER,
  mtime   REAL,
  ext     TEXT
);

-- Migration: page_count column added in _ensure_schema() if missing
-- ALTER TABLE items ADD COLUMN page_count INTEGER;

-- ComicInfo metadata extracted from CBZ files
CREATE TABLE IF NOT EXISTS meta (
  rel             TEXT PRIMARY KEY,
  title           TEXT,
  series          TEXT,
  number          TEXT,
  volume          TEXT,
  year            TEXT,
  month           TEXT,
  day             TEXT,
  writer          TEXT,
  publisher       TEXT,
  summary         TEXT,
  genre           TEXT,
  tags            TEXT,
  characters      TEXT,
  teams           TEXT,
  locations       TEXT,
  comicvineissue  TEXT
);

-- Migration: format column added in _ensure_schema() if missing
-- ALTER TABLE meta ADD COLUMN format TEXT;

-- Indexes for common queries
CREATE INDEX IF NOT EXISTS idx_items_parent   ON items(parent);
CREATE INDEX IF NOT EXISTS idx_items_name     ON items(name);
CREATE INDEX IF NOT EXISTS idx_items_isdir    ON items(is_dir);
CREATE INDEX IF NOT EXISTS idx_meta_series    ON meta(series);
CREATE INDEX IF NOT EXISTS idx_meta_title     ON meta(title);
CREATE INDEX IF NOT EXISTS idx_meta_year      ON meta(year);
CREATE INDEX IF NOT EXISTS idx_meta_writer    ON meta(writer);
CREATE INDEX IF NOT EXISTS idx_meta_publisher ON meta(publisher);

-- Full-text search table (FTS5, optional if extension unavailable)
-- Created by Python code in _ensure_schema(); uses unicode61 tokenizer
CREATE VIRTUAL TABLE IF NOT EXISTS fts USING fts5(
  rel UNINDEXED,
  text,
  tokenize = 'unicode61'
);

-- User accounts with bcrypt password hashes
CREATE TABLE IF NOT EXISTS users (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  username TEXT UNIQUE NOT NULL,
  password_hash TEXT NOT NULL,
  is_admin INTEGER NOT NULL DEFAULT 0
);
