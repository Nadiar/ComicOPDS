## 🔧 Configuration

### Environment Variables

| Variable              | Default     | Required | Description |
|-----------------------|-------------|----------|-------------|
| `CONTENT_BASE_DIR`    | `/library`  | Required | Path inside the container where your comics are stored (mounted volume). |
| `SERVER_BASE`         | (none)      | Required | Public base URL (e.g. `http://10.0.0.1:8382` or `https://comics.example.com`). Used in generated OPDS links. |
| `URL_PREFIX`          | `""`        | Optional | Path prefix when serving behind a reverse proxy (e.g. `/comics`). Leave empty if served at root or subdomain. |
| `PAGE_SIZE`           | `50`        | Optional | Number of entries per page in OPDS feeds. Increase/decrease depending on client performance. |
| `DISABLE_AUTH`        | `false`     | Optional | If `true`, disables authentication completely (public access). |
| `OPDS_BASIC_USER`     | `admin`     | Optional | Username for HTTP Basic Auth. Ignored if `DISABLE_AUTH=true`. |
| `OPDS_BASIC_PASS`     | `change-me` | Optional | Password for HTTP Basic Auth. Ignored if `DISABLE_AUTH=true`. |
| `ENABLE_WATCH`        | `false`     | Optional | Watch filesystem for changes and update index incrementally. (`true`/`false`). |
| `AUTO_INDEX_ON_START` | `false`     | Optional | If `true`, reindexes library on every container start. Recommended `false` for large libraries. |
| `PRECACHE_THUMBS`     | `false`     | Optional | If `true`, enables thumbnail generation when reindexing or via dashboard. |
| `PRECACHE_ON_START`   | `false`     | Optional | If `true`, automatically triggers full thumbnail pre-cache at container start. Recommended `false` for large libraries.  |
| `THUMB_WORKERS`       | `3`         | Optional | Number of parallel workers for thumbnail generation. Tune for your CPU/IO capacity. |
| `PAGE_CACHE_TTL_DAYS` | `14`        | Optional | Delete page caches that have been idle for more than this many days. |
| `PAGE_CACHE_MAX_BYTES`| `10737418240` | Optional | Maximum total size of page cache (10 GiB default). |
| `PAGE_CACHE_AUTOCLEAN`| `true`      | Optional | Enable automatic background cleanup of page caches. |
| `PAGE_CACHE_CLEAN_INTERVAL_MIN` | `360` | Optional | Run page cache cleanup every N minutes (360 = 6 hours). |

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