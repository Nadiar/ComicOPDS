## 🔍 Search

ComicOPDS provides powerful search capabilities:

### Search Technology
- **SQLite FTS5**: Full-text search when available
- **Fallback**: LIKE queries when FTS5 unavailable

> Check `/debug/fts`, which returns `{ "fts5": true/false }` indicating whether SQLite FTS5 is enabled.

### Searchable Fields
- `series` - Comic series name
- `title` - Individual issue title  
- `publisher` - Publishing company
- `year` - Publication year
- `writer` - Writer(s)
- `penciller` - Artist(s)
- `genre` - Comic genre/category
- `characters` - Featured characters
- `tags` - Custom tags
- `format` - TPB, Main Series, Annual, One-Shot etc. 

### Search Tips
- Use quotes for exact phrases: `"Dark Knight"`
- Combine terms: `batman joker`
- Use wildcards: `bat*` (when FTS5 available)