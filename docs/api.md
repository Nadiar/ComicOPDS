## 🌐 API & Endpoints

ComicOPDS exposes both user-facing endpoints (for OPDS clients and the dashboard) and admin/debug endpoints.

### 📡 OPDS Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/` | `GET` | Root OPDS catalog feed (same as `/opds`) |
| `/opds` | `GET` | Root OPDS catalog feed. Supports browsing by folder and smart lists. |
| `/opds?path=...` | `GET` | Browse into a subfolder (series, publisher, etc.). |
| `/opds/search.xml` | `GET` | [OpenSearch 1.1](https://opensearch.org/) descriptor. Tells OPDS clients how to search. |
| `/opds/search?q=...&page=...` | `GET` | Perform a search query (returns OPDS feed of matching comics). |
| `/download?path=...` | `GET` | Download a `.cbz` file. Supports HTTP range requests. |
| `/stream?path=...` | `GET` | Stream a `.cbz` file (content-type `application/vnd.comicbook+zip`). |
| `/pse/pages?path=...` | `GET` | OPDS PSE 1.1 page streaming (individual pages as images). Used by Panels and similar clients. |
| `/thumb?path=...` | `GET` | Get thumbnail image for a comic (JPEG format). |

### 📊 Dashboard & Stats

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/dashboard` | `GET` | Dashboard (HTML UI with Bootstrap & Chart.js). |
| `/stats.json` | `GET` | JSON with library statistics (total comics, unique series, publishers, etc.). |
| `/search` | `GET` | Smart Lists UI (create/edit saved searches). |
| `/healthz` | `GET` | Health check endpoint (returns "ok"). |

### 🛠️ Admin Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/admin/reindex` | `POST` | Trigger a full library reindex. Shows progress in dashboard. |
| `/admin/thumbs/precache` | `POST` | Trigger full thumbnail pre-cache. Shows progress in dashboard. |
| `/index/status` | `GET` | JSON status of current indexing task. |
| `/thumbs/status` | `GET` | JSON status of current thumbnail caching task. |
| `/thumbs/errors/log` | `GET` | Download the thumbnail extraction error log (`/data/thumbs_errors.log`). |
| `/admin/pages/cleanup` | `POST` | Trigger manual cleanup of page-cache | 
| `/pages/cache/status` | `GET` | Check page-cache size and statistics |

### 🧪 Debug Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/debug/children?path=...` | `GET` | JSON list of child items (files/folders) under a path. Useful for testing indexing. |
| `/debug/fts` | `GET` | Returns `{ "fts5": true/false }` indicating whether SQLite FTS5 is enabled. |

⚠️ **Note:**

- Admin and debug endpoints require Basic Auth unless `DISABLE_AUTH=true` is set.
- OPDS endpoints automatically serve OPDS 1.2 or OPDS 2.0 based on the client's `Accept` header (content negotiation). This works transparently with any compliant OPDS 1.2/2.0 client.
  