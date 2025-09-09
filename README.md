# 📚 ComicOPDS

ComicOPDS is a lightweight [OPDS 1.2](https://specs.opds.io/opds-1.2) server written in Python, designed for serving **CBZ comics** with metadata extracted from `ComicInfo.xml`.  
It's optimized for large libraries (10k–100k+ comics), supports FastAPI + SQLite + FTS5 search, thumbnail caching, and streaming (OPDS PSE 1.1).

Works great with [Panels for iOS](https://panels.app) and other OPDS readers.

<a href="https://www.buymeacoffee.com/frederikb" target="_blank"><img src="https://cdn.buymeacoffee.com/buttons/v2/default-yellow.png" alt="Buy Me A Coffee" height="41" width="174"></a>

---

## 📋 Table of Contents

- [Features](#-features)
- [Quick Start](#-quick-start)
- [Installation Methods](#-installation-methods)
- [Configuration](#-configuration)
- [API Documentation](#-api--endpoints)
- [Dashboard](#-dashboard)
- [Smart Lists](#-smart-lists)
- [Search](#-search)
- [Client Setup](#-client-setup)
- [Troubleshooting](#-troubleshooting)
- [Contributing](#-contributing)
- [License](#-license)

---

## ✨ Features

- 📂 Browse your folder hierarchy
- 🔍 Full-text search (title, series, writer, publisher, year, etc.)
- 📥 Download comics (CBZ)
- 📖 Page streaming (OPDS PSE 1.1)
- 🖼️ Thumbnail extraction & caching (from CBZ covers)
- 📊 Dashboard with stats & charts
- 🧠 Smart Lists (saved search filters)
- 🔐 Optional Basic Auth
- 🐋 Runs easily with Docker / Docker Compose
- ⚡ Fast indexing with SQLite FTS5
- 🔄 File system watching for auto-updates
- 📱 Mobile-optimized dashboard

---

## 🚀 Quick Start

### Prerequisites

- Docker and Docker Compose installed
- Comic collection in CBZ format with `ComicInfo.xml` metadata

### Folder Structure

Your comics should be organized like this:
```
/your/comics/
├── Batman (2016)/
│   ├── Batman (2016) - 001.cbz  # contains ComicInfo.xml
│   └── Batman (2016) - 002.cbz
├── Saga/
│   └── Saga - 001.cbz
└── ...
```

**Recommended**: I use [ComicRack CE](https://github.com/maforget/ComicRackCE) to organize my comic library and generate proper `ComicInfo.xml` metadata. For a comprehensive guide on setting up an optimal comic library structure and metadata management, see my detailed guide at: **[https://comicrack.baerentsen.space/](https://comicrack.baerentsen.space/)**


### Docker Compose Setup

Create a `docker-compose.yml` file:

```yaml
services:
  comicopds:
    image: gitea.baerentsen.space/frederikbaerentsen/comicopds:latest
    restart: unless-stopped
    ports:
      - "8382:8080"
    environment:
      CONTENT_BASE_DIR: /library
      SERVER_BASE: "http://192.168.10.10:8382"   # Replace with your server IP/domain
      URL_PREFIX: ""                             # set to "/comics" if behind a reverse proxy subpath
      DISABLE_AUTH: "false"                      # set to true to disable authentication 
      OPDS_BASIC_USER: "admin"
      OPDS_BASIC_PASS: "change-me-please"        # Use a strong password!
      ENABLE_WATCH: "false"                      # auto-update index on file changes
      PRECACHE_THUMBS: "false"                   # set true to pre-cache thumbs on reindex
      AUTO_INDEX_ON_START: "false"               # skip reindexing on each container start
    volumes:
      - "./comics:/library:ro"
      - "./data:/data"
```

### Launch Commands

```bash
# Build and start
docker compose build
docker compose up -d

# View logs
docker compose logs -f

# Stop
docker compose down
```

### Access Points

- 📡 **OPDS Feed**: http://localhost:8382/
- 📊 **Dashboard**: http://localhost:8382/dashboard  
- 🧠 **Smart Lists**: http://localhost:8382/search

<!-- --- -->

<!-- ## 📖 Documentation

- Reindex library: click the Reindex button in dashboard
- Pre-cache thumbnails: click Pre-cache Thumbnails (generates all covers ahead of time)
  If covers aren't pre-cached, each cover will be generated when needed. 
- Search: available in Panels & other OPDS apps
- Smart Lists: create saved filters on /search -->

---

## 🔧 Configuration

### Environment Variables

| Variable              | Default     | Description |
|-----------------------|-------------|-------------|
| `CONTENT_BASE_DIR`    | `/library`  | Path inside the container where your comics are stored (mounted volume). |
| `PAGE_SIZE`           | `50`        | Number of entries per page in OPDS feeds. Increase/decrease depending on client performance. |
| `SERVER_BASE`         | (required)  | Public base URL (e.g. `http://10.0.0.1:8382` or `https://comics.example.com`). Used in generated OPDS links. |
| `URL_PREFIX`          | `""`        | Path prefix when serving behind a reverse proxy (e.g. `/comics`). Leave empty if served at root or subdomain. |
| `DISABLE_AUTH`        | `false`     | If `true`, disables authentication completely (public access). |
| `OPDS_BASIC_USER`     | `admin`     | Username for HTTP Basic Auth. Ignored if `DISABLE_AUTH=true`. |
| `OPDS_BASIC_PASS`     | `change-me` | Password for HTTP Basic Auth. Ignored if `DISABLE_AUTH=true`. |
| `ENABLE_WATCH`        | `false`     | Watch filesystem for changes and update index incrementally. (`true`/`false`). |
| `AUTO_INDEX_ON_START` | `false`     | If `true`, reindexes library on every container start. Recommended `false` for large libraries. |
| `PRECACHE_THUMBS`     | `false`     | If `true`, enables thumbnail generation when reindexing or via dashboard. |
| `PRECACHE_ON_START`   | `false`     | If `true`, automatically triggers full thumbnail pre-cache at container start. Recommended `false` for large libraries.  |
| `THUMB_WORKERS`       | `3`         | Number of parallel workers for thumbnail generation. Tune for your CPU/IO capacity. |

### 📚 Recommended Settings for Large Libraries (30k–100k+ comics)

For very large collections, some defaults should be adjusted to avoid long startup times and high resource usage:

#### Indexing Settings
- `AUTO_INDEX_ON_START=false` → prevents reindexing every time the container starts  
- Use the **Reindex** button on the dashboard when needed instead

#### Thumbnail Settings
- `PRECACHE_ON_START=false` → don't pre-cache on every boot  
- Run pre-cache manually via the dashboard button after a big import
- `THUMB_WORKERS=4`–`6` → if you have enough CPU/IO, increase worker count for faster thumbnail generation

#### Performance Settings
- Keep `PAGE_SIZE=50` unless your client struggles with large feeds  
- Some OPDS readers work better with smaller pages (e.g. `25`)

#### Security Settings
- For private servers behind a VPN, you can disable auth: `DISABLE_AUTH=true`  
- Otherwise, keep Basic Auth enabled (`OPDS_BASIC_USER` / `OPDS_BASIC_PASS`)

These settings ensure the container starts faster, avoids unnecessary reprocessing, and lets you control when heavy tasks (indexing, thumbnailing) happen.

---

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

### 🧪 Debug Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/debug/children?path=...` | `GET` | JSON list of child items (files/folders) under a path. Useful for testing indexing. |
| `/debug/fts` | `GET` | Returns `{ "fts5": true/false }` indicating whether SQLite FTS5 is enabled. |

⚠️ **Note:**  
- Admin and debug endpoints require Basic Auth unless `DISABLE_AUTH=true` is set.  
- OPDS endpoints follow the OPDS 1.2 specification and should work with Panels and other compliant OPDS clients.  

---

## 📊 Dashboard

> Access through `/dashboard`

The dashboard provides a comprehensive overview of your comic library:

### Features
- **Library Statistics**: Total comics, unique series, publishers
- **Interactive Charts**:
  - Publishers distribution (doughnut chart)
  - Publication timeline (line chart)  
  - Top writers (horizontal bar chart)
  - Format breakdown
- **Management Tools**:
  - Reindex library button
  - Pre-cache thumbnails button
  - Progress bars for ongoing operations
- **Error Monitoring**: 
  - Thumbnail extraction error counter
  - Downloadable error log

---

## 🧠 Smart Lists

> Access through `/search`

Smart Lists allow you to create saved search filters that appear as "virtual folders" in your OPDS feed.

### Creating Smart Lists

#### Simple Filters
Add filters using the web interface:
- `series=Batman` 
- `year=2024`
- `publisher=DC Comics`

#### Advanced Filters
Create complex queries with multiple conditions:

```
series contains 'Scrooge McDuck'
volume equals 1953
number >= 285
number <= 297
```

#### JSON Configuration Example
For advanced users, Smart Lists are stored in `/data/smartlist.json`:

```
 {
    "name": "Maul + Vader (1-5)",
    "slug": "maul-vader-1-5",
    "groups": [
      {
        "rules": [
          {
            "not": false,
            "field": "series",
            "op": "contains",
            "value": "Maul"
          },
          {
            "not": false,
            "field": "number",
            "op": "<=",
            "value": "5"
          },
          {
            "not": false,
            "field": "format",
            "op": "equals",
            "value": "Limited Series"
          }
        ]
      },
      {
        "rules": [
          {
            "not": false,
            "field": "series",
            "op": "contains",
            "value": "Vader"
          },
          {
            "not": false,
            "field": "number",
            "op": "<=",
            "value": "5"
          },
          {
            "not": false,
            "field": "format",
            "op": "equals",
            "value": "Main Series"
          }
        ]
      }
    ],
    "sort": "series_number",
    "limit": 0,
    "distinct_by": "",
    "distinct_mode": "oldest"
  }
```

**Maul + Vader (1-5)**:

- Group 1: 
  - series contains "Maul" 
  - number <= 5 
  - format = "Limited Series" 
- Group 2:
  - series contains "Vader" 
  - number <= 5 
  - format = "Main Series"
- Sort: 
  - series_number
  - limit: 0 
  - Distinct: no

### Supported Operations
- `equals`, `contains`, `startswith`, `endswith`
- `=`, `!=`, `>=`, `<=`, `>`, `<` (for numeric fields)
- `not` modifier for any operation

### "Distinct by series and volume (latest)"

When that option is enabled, a smart list will return at most one comic per series and volume.
For each series, it picks the latest issue, using this tie-break:

1. Newer year (cast to integer)
2. If year ties: higher number (cast to integer)
3. If number ties: newer file mtime (last modified time)

So you get a de-duplicated "what's the newest issue for each series?" view.

**Use Cases**:

- A clean "latest per series" shelf (e.g., to see what's new without 300 issues of Batman).
- Weekly pulls / backlog triage: combine with filters like `publisher=Image` or `year >= 2020`.

**Important details / edge cases**

- Numeric casting: blank or non-numeric `year`/`number` are treated as `NULL` → effectively `0`, so those won't beat entries with proper numbers (eg. `16A`). 

**Example use**

- "Latest Image series":
  - Rules: `publisher = "Image Comics"`, `year >= 2018`
  - Distinct by series: on
  
→ One newest issue per Image series since 2018.

---

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

---

## 📱 Clients

**Supported Clients**

| App                          | Downloads | Search | Streaming | 
| ---------------------------  | --  | -- | -- | 
| KyBook 3 (iOS)               | ✔️  | ✔️  | ❌  |
| Cantook (iOS)                | ✔️  | ❌  | ❌  |
| Panels (iOS)                 | ✔️  |✔️  |✔️  |
| Marvin 3 (iOS)                 | ✔️  | ❌  | ❌  |
| Chunky (iOS)                 | ✔️  | ❌  | ❌  |

### Panels for iOS
1. Open Panels → Library → Connect Service → OPDS
2. **URL**: Your OPDS root (e.g., `https://comics.example.com/`)
3. **Username/Password**: If you enabled Basic Auth
4. Panels will display covers and use your folder structure for browsing

### Client-Specific Notes
- Some clients work better with smaller `PAGE_SIZE` (e.g., 25 instead of 50)
- Page streaming (PSE 1.2) requires client support
- Thumbnail quality may vary between clients

---

## 🛠️ Troubleshooting

### Common Issues

#### No Comics Appearing
- **Check mount**: Ensure your comics folder is mounted at `/library`
- **File format**: Only `.cbz` files are supported
- **Metadata**: Ensure CBZ files contain `ComicInfo.xml`
- **Permissions**: Verify read permissions on comic files

#### Missing Thumbnails
- **First load**: Thumbnails generate on first request
- **Check permissions**: Ensure `/data/thumbs` is writable
- **View errors**: Check error log via dashboard or `/data/thumbs_errors.log`

#### Authentication Problems
- **Verify credentials**: Check `OPDS_BASIC_USER` and `OPDS_BASIC_PASS`
- **Client support**: Ensure your client supports HTTP Basic Auth
- **Disable auth**: Set `DISABLE_AUTH=true` for testing

#### Wrong Links/URLs
- **Behind proxy**: Set `SERVER_BASE` and `URL_PREFIX` correctly
- **Protocol mismatch**: Ensure HTTP/HTTPS consistency

### Debug Commands

```bash
# View container logs
docker compose logs -f comicopds

# Check container health
docker compose ps

# Inspect configuration
docker compose exec comicopds env | grep -E "(SERVER_BASE|URL_PREFIX|CONTENT_BASE_DIR)"

# Test internal connectivity  
docker compose exec comicopds wget -qO- http://localhost:8080/healthz

# Check FTS5 availability
curl -u admin:password http://localhost:8382/debug/fts
```

### Log Files
- **Application logs**: `docker compose logs -f`
- **Thumbnail errors**: `/data/thumbs_errors.log`

### Performance Tuning
- **Large libraries**: Disable `AUTO_INDEX_ON_START`
- **Slow thumbnails**: Increase `THUMB_WORKERS`
- **Memory usage**: Reduce `PAGE_SIZE`
- **Network issues**: Check `SERVER_BASE` configuration

---

## 📄 License

MIT License – use freely, modify, and contribute.

```
Copyright (c) 2024 Frederik Baerentsen

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

---

## 🔗 Links

- **Repository**: [Gitea](https://gitea.baerentsen.space/FrederikBaerentsen/ComicOPDS)
- **OPDS Specification**: [OPDS 1.2](https://specs.opds.io/opds-1.2)
- **OPDS Page Streaming Extension: [OPDS PSE 1.2](https://anansi-project.github.io/docs/opds-pse/specs/v1.2)
- **Buy Me a Coffee**: [frederikb](https://www.buymeacoffee.com/frederikb)

---

*Made with ❤️ for comic book enthusiasts*