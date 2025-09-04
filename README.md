# ComicOPDS

A tiny, fast **OPDS 1.x** server for **CBZ** comics with ComicRack metadata.
Built with FastAPI, runs great in Docker, works nicely with Panels for iOS.

## Features

* 📁 **Browse** your library by folder (no database)
* 🔎 **Search** (OPDS 1.x + OpenSearch) across filenames and ComicInfo metadata
* ⬇️ **Download** & ▶️ **Stream** (HTTP Range support) CBZ files
* 🖼️ **Thumbnails** extracted from CBZ (first image), cached on disk
  – If `ComicVineIssue` exists in `ComicInfo.xml`, the cover is named `<ComicVineIssue>.jpg`
* 🏷️ **ComicInfo.xml** parsing and caching (Series, Number, Title, Publisher, Writer, Year, …)
* ⚡ **Warm index** persisted to `/data/index.json` (no full re-index on startup)
* 🔐 Optional **HTTP Basic Auth**
* 📊 **Dashboard** at `/dashboard` with overview stats & charts (powered by `/stats.json`)
* 🧰 Reverse-proxy friendly (domain/subpath support)

> **Scope**: CBZ **only** (by design).

---

## Quick start

### 1) Folder layout (example)

```
/your/comics/
  Batman (2016)/
    Batman (2016) - 001.cbz  # contains ComicInfo.xml
    Batman (2016) - 002.cbz
  Saga/
    Saga - 001.cbz
```

Make sure each CBZ has a `ComicInfo.xml` inside (ComicRack-compatible).

### 2) Docker Compose

```yaml
services:
  comicopds:
    build: .   # build the image from the included Dockerfile
    image: comicopds:latest
    container_name: comicopds
    ports:
      - "8080:8080"
    environment:
      CONTENT_BASE_DIR: /library
      PAGE_SIZE: "50"
      SERVER_BASE: "https://comics.example.com"  # your public origin (or http://localhost:8080)
      URL_PREFIX: ""                              # e.g. "/comics" if served under a subpath
      OPDS_BASIC_USER: "admin"                    # leave empty to disable auth
      OPDS_BASIC_PASS: "change-me"
    volumes:
      - /path/to/your/comics:/library:ro
      - ./data:/data
    healthcheck:
      test: ["CMD", "wget", "-qO-", "http://localhost:8080/healthz"]
      interval: 30s
      timeout: 3s
      retries: 3
```

Build & run:

```bash
docker compose up --build
```
On the first run, Docker will build the image from the included Dockerfile.

On later runs, Docker will reuse the existing image unless you change the code/Dockerfile (then it will rebuild automatically).

Visit:

* OPDS root feed: `https://comics.example.com/` (or `http://localhost:8080/`)
* Dashboard: `https://comics.example.com/dashboard`

> If you run behind a reverse proxy **and** a subpath (e.g. `/comics`), set
> `SERVER_BASE=https://example.com` and `URL_PREFIX=/comics`.
> Uvicorn is started with `--proxy-headers`, so `X-Forwarded-*` are respected.

---

## OPDS endpoints

* `GET /` → OPDS start feed (Atom)
* `GET /opds` → folder browsing feed (`?path=` and `?page=`)
* `GET /opds/search.xml` → OpenSearch description
* `GET /opds/search?q=…&page=` → search results feed (Atom)
* `GET /download?path=…` → download CBZ (correct MIME)
* `GET /stream?path=…` → range-enabled streaming (206 Partial Content)
* `GET /thumb?path=…` → JPEG cover (from CBZ’s first image; cached)
* `GET /dashboard` → HTML dashboard
* `GET /stats.json` → library stats for the dashboard
* `GET /healthz` → “ok” healthcheck

All routes can be protected by HTTP Basic (set `OPDS_BASIC_USER/PASS`).

---

## How it works

### Warm index

* On first run, scans `/library` for `.cbz`, reads **`ComicInfo.xml` once**, and writes `/data/index.json`.
* On later startups, **reuses cached metadata** if file `size` & `mtime` match; only new/changed files are opened.
* Thumbnails live in `/data/thumbs/`:

  * If `<ComicVineIssue>` exists → `THUMBS/<comicvine_issue>.jpg`
  * Else → `THUMBS/<sha1-of-relpath>.jpg`

### Metadata fields used (from ComicInfo.xml)

* Titles/ordering: `Series`, `Number`, `Volume`, `Title`
* People: `Writer`, `Penciller`, `Inker`, `Colorist`, `Letterer`, `CoverArtist`
* Catalog: `Publisher`, `Imprint`, `Genre`, `Tags`, `Characters`, `Teams`, `Locations`
* Dates: `Year`, `Month`, `Day` → `dcterms:issued`
* Extras: `ComicVineIssue` (for stable cover filenames)

### Dashboard stats

* **Overview**: total comics, unique series, publishers, formats
* **Charts**:

  * Publishers (doughnut)
  * Publication timeline by year (line)
  * Formats breakdown (bar — mostly CBZ)
  * Top writers (horizontal bar)

---

## Using with Panels (iOS)

In Panels → **Add Catalog** → **OPDS**:

* **URL**: your OPDS root (e.g. `https://comics.example.com/`)
* Add your username/password if you enabled Basic Auth
* Panels will show covers via OPDS `image` links and use your folder structure for browsing

Search in Panels maps to `/opds/search`.

---

## Environment variables

| Name               | Default                 | Purpose                                            |
| ------------------ | ----------------------- | -------------------------------------------------- |
| `CONTENT_BASE_DIR` | `/library`              | Path inside the container where comics are mounted |
| `PAGE_SIZE`        | `50`                    | Items per OPDS page                                |
| `SERVER_BASE`      | `http://localhost:8080` | Public origin used to build absolute OPDS links    |
| `URL_PREFIX`       | `""`                    | If serving under a subpath (e.g. `/comics`)        |
| `OPDS_BASIC_USER`  | *(empty)*               | Username for HTTP Basic (if empty → auth off)      |
| `OPDS_BASIC_PASS`  | *(empty)*               | Password for HTTP Basic                            |

---

## Reverse proxy tips

* **Domain at root**: set `SERVER_BASE=https://comics.example.com`
* **Domain + subpath**: set `SERVER_BASE=https://example.com` and `URL_PREFIX=/comics`

---

## Troubleshooting

* **No comics show up**: confirm your host path is mounted read-only at `/library` and files end with `.cbz`.
* **Missing covers**: first request to an entry generates the cover. Check `/data/thumbs/` is writable.
* **Auth prompts repeatedly**: verify browser/app supports Basic Auth and credentials match `OPDS_BASIC_USER/PASS`.
* **Wrong links behind proxy**: ensure `SERVER_BASE` and (if needed) `URL_PREFIX` are set correctly.

---

## Development

Install deps locally:

```bash
pip install fastapi uvicorn jinja2 pillow
uvicorn app.main:app --reload --proxy-headers --forwarded-allow-ips="*"
```

---
