## 🌐 API & Endpoints

ComicOPDS exposes both user-facing endpoints (for OPDS clients and the dashboard) and admin/debug endpoints.

### 📡 OPDS Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/` | `GET` | Root OPDS catalog feed (same as `/opds`) |
| `/opds` | `GET` | Root OPDS catalog feed. Supports browsing by folder and smart lists. |
| `/opds?path=...` | `GET` | Browse into a subfolder (series, publisher, etc.). |
| `/opds/v2/manifest?path=...` | `GET` | DiViNa manifest for OPDS 2.0 page streaming (Readium Web Publication Manifest). Returns `readingOrder` with per-page image links. |
| `/opds/search.xml` | `GET` | [OpenSearch 1.1](https://opensearch.org/) descriptor. Tells OPDS clients how to search. |
| `/opds/search?q=...&page=...` | `GET` | Perform a search query (returns OPDS feed of matching comics). |
| `/download?path=...` | `GET` | Download a `.cbz` file. Supports HTTP range requests. |
| `/stream?path=...` | `GET` | Stream a `.cbz` file (content-type `application/vnd.comicbook+zip`). |
| `/pse/pages?path=...` | `GET` | OPDS PSE 1.1 page streaming feed (individual pages as images). Used by Panels and similar OPDS 1.2 clients. |
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

### 📄 Page Streaming Architecture

ComicOPDS uses **different streaming protocols** depending on the OPDS version:

| OPDS Version | Protocol | How It Works |
|---|---|---|
| OPDS 1.2 (Atom XML) | **OPDS PSE 1.1** | Entries include `<link rel="http://vaemendis.net/opds-pse/stream">` with `pse:count` attribute. Clients request pages from the PSE feed. |
| OPDS 2.0 (JSON) | **DiViNa / RWPM** | Entries include a `self` link to a [Readium Web Publication Manifest](https://readium.org/webpub-manifest/profiles/divina) (`application/divina+json`). The manifest contains a `readingOrder` array with one link per page. |

Both protocols use the same underlying `/pse/page` endpoint for actual page image delivery — only the discovery mechanism differs. PSE is an XML namespace extension and is not valid in JSON feeds; DiViNa manifests are the OPDS 2.0-native equivalent.
