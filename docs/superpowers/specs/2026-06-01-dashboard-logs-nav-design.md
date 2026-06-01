# Dashboard: Logs Page, Auth Logging & Nav Reorganisation

**Date:** 2026-06-01
**Status:** Approved

---

## Overview

Three related improvements to the ComicOPDS admin dashboard:

1. **Rotating log file** — persist `WARNING+` (optionally `INFO+`) application logs to `/data/app.log`
2. **Logs page** — new `/logs` admin page to view, filter, download, and clear the log file
3. **Nav reorganisation** — rename "search" → "Smart Lists" across all templates, add "Logs" nav link

---

## 1. Backend — Logging Infrastructure

### File Handler (`app/main.py`)

Add a `RotatingFileHandler` to the existing `comicopds` logger at startup:

- **Path:** `/data/app.log`
- **Rotation:** max 5 MB per file, 3 backups (i.e. `app.log`, `app.log.1`, `app.log.2`, `app.log.3`)
- **Level:** `WARNING` by default; `INFO` when `LOG_AUTH=true`
- **Formatter:** same as stdout — `%(asctime)s %(levelname)s %(name)s: %(message)s`
- Attached to the existing `comicopds` logger — no new logger needed

### Auth Logging (`app/auth.py`, `app/config.py`)

New `LOG_AUTH` boolean env var (default `false`) parsed in `app/config.py` via existing `_parse_bool`.

Behaviour in the HTTP Basic Auth dependency:
- **Successful login:** `logger.info("auth: login user=%s ip=%s", username, ip)` — only written to file when `LOG_AUTH=true` (file handler level becomes `INFO`)
- **Failed login (wrong password):** `logger.warning("auth: failed user=%s ip=%s reason=bad_password", username, ip)` — always written regardless of `LOG_AUTH`
- **Failed login (unknown user):** `logger.warning("auth: failed user=%s ip=%s reason=unknown_user", username, ip)` — always written

IP extracted from `request.client.host` (already available in the `require_basic` dependency via the `Request` object).

---

## 2. API Endpoints (`app/routes_admin.py`)

All three endpoints require admin auth.

### `GET /admin/logs?lines=500`

- Reads the last `lines` lines from `/data/app.log` (default 500, max 2000)
- Uses seek-from-end for efficiency — does not read the whole file
- Returns: `{"lines": ["..."], "total_lines": N, "file_size": N}`
- If file does not exist: `{"lines": [], "total_lines": 0, "file_size": 0}`

### `GET /admin/logs/download`

- Streams `/data/app.log` as a file download (`Content-Disposition: attachment; filename=app.log`)
- 404 if file does not exist

### `POST /admin/logs/clear`

- Requires CSRF header (same pattern as other mutating admin endpoints)
- Truncates `/data/app.log` by opening with mode `"w"` and closing immediately
- Returns `{"ok": true}`
- No-op (still returns `{"ok": true}`) if file does not exist

---

## 3. Frontend

### New Template: `app/templates/logs.html`

New page served at `GET /logs` (admin-only route in `routes_admin.py`, same pattern as `/dashboard`).

**Layout:**

- Same navbar as `dashboard.html` / `smartlists.html` (Bootstrap 5, Bootstrap Icons, brand + nav links)
- **Controls bar** (top of main content):
  - Auto-refresh toggle (10 s interval, off by default)
  - Level filter dropdown: All / Warning+ / Error+
  - Search box (client-side substring filter on displayed lines)
  - Line count selector: 100 / 500 / 2000 (default 500)
  - Download button → `GET /admin/logs/download`
  - Clear button → confirms, then `POST /admin/logs/clear`, then reloads
- **Log panel:** scrollable `<pre>`-style block, monospace font, lines colour-coded:
  - `ERROR` / `CRITICAL` → red (`text-danger`)
  - `WARNING` → amber (`text-warning`)
  - `INFO` → muted (`text-secondary`)
  - Everything else → default
- **Status bar** (below panel): "Showing X of Y lines · File: Z KB · Last refreshed: HH:MM:SS"

**Behaviour:**

- On page load: fetch `GET /admin/logs?lines=N`, render lines
- Auto-refresh: `setInterval` at 10 s when toggle is on; clears interval when toggled off
- Level filter and search box are client-side only (filter the already-fetched lines array)
- Changing line count selector re-fetches from server

### Nav Changes (all three templates)

Applied to `dashboard.html`, `smartlists.html`, and the new `logs.html`:

| Before | After |
|--------|-------|
| `bi-search` icon + "search" link → `/search` | `bi-funnel` icon + "Smart Lists" link → `/search` |
| _(absent)_ | `bi-journal-text` icon + "Logs" link → `/logs` |

The `/search` URL is **not renamed** — avoids breaking any existing bookmarks or OPDS reader references.

### New Route

```python
@router.get("/logs", response_class=HTMLResponse)
def logs_page(request: Request, user: str = Depends(auth.require_admin)):
    tpl = env.get_template("logs.html")
    return HTMLResponse(tpl.render())
```

---

## 4. Configuration Reference

| Env var | Default | Purpose |
|---------|---------|---------|
| `LOG_AUTH` | `false` | Log successful logins at INFO level; failed logins are always logged at WARNING |

All other logging controlled by existing `LOG_LEVEL` env var (controls stdout handler level; file handler is always `WARNING+` unless `LOG_AUTH=true`).

---

## 5. Files Changed

| File | Change |
|------|--------|
| `app/config.py` | Add `LOG_AUTH` bool |
| `app/main.py` | Add `RotatingFileHandler` at startup |
| `app/auth.py` | Add auth success/failure log calls |
| `app/routes_admin.py` | Add `/logs` route + 3 API endpoints |
| `app/templates/logs.html` | New template |
| `app/templates/dashboard.html` | Nav: rename Search → Smart Lists, add Logs link |
| `app/templates/smartlists.html` | Nav: rename Search → Smart Lists, add Logs link |

---

## 6. Out of Scope

- Log streaming via WebSocket (polling at 10 s is sufficient)
- Structured/JSON log format (plain text is adequate and matches existing stdout output)
- Log viewer for rotated backup files (`.log.1`, `.log.2`) — primary file only
