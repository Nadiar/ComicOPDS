-- Items (dirs & files)
CREATE TABLE IF NOT EXISTS items (
  rel     TEXT PRIMARY KEY,
  name    TEXT NOT NULL,
  is_dir  INTEGER NOT NULL,       -- 1=dir, 0=file
  size    INTEGER DEFAULT 0,
  mtime   REAL    DEFAULT 0,
  parent  TEXT NOT NULL,
  ext     TEXT    DEFAULT '',
  seen    INTEGER DEFAULT 1        -- used during scans
);
CREATE INDEX IF NOT EXISTS idx_items_parent ON items(parent);
CREATE INDEX IF NOT EXISTS idx_items_isdir ON items(is_dir);
CREATE INDEX IF NOT EXISTS idx_items_ext ON items(ext);
CREATE INDEX IF NOT EXISTS idx_items_mtime ON items(mtime);

-- ComicInfo metadata (only the fields we actually use)
CREATE TABLE IF NOT EXISTS meta (
  rel             TEXT PRIMARY KEY REFERENCES items(rel) ON DELETE CASCADE,
  title           TEXT,
  series          TEXT,
  number          TEXT,
  volume          TEXT,
  publisher       TEXT,
  imprint         TEXT,
  writer          TEXT,
  year            TEXT,
  month           TEXT,
  day             TEXT,
  languageiso     TEXT,
  comicvineissue  TEXT,
  genre           TEXT,
  tags            TEXT,
  summary         TEXT,
  characters      TEXT,
  teams           TEXT,
  locations       TEXT
);
CREATE INDEX IF NOT EXISTS idx_meta_series ON meta(series);
CREATE INDEX IF NOT EXISTS idx_meta_publisher ON meta(publisher);
CREATE INDEX IF NOT EXISTS idx_meta_year ON meta(year);
CREATE INDEX IF NOT EXISTS idx_meta_writer ON meta(writer);

-- Optional: FTS5 for fast search (ignore if extension unavailable)
-- Wrap in a try for environments without FTS5 (executed by Python).
CREATE VIRTUAL TABLE IF NOT EXISTS search USING fts5(
  rel UNINDEXED,
  name,
  title,
  series,
  publisher,
  writer,
  tags,
  genre,
  content=''
);
