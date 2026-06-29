"""Admin, dashboard, debug, and smart-list management routes."""
from __future__ import annotations

import json
import logging
import os
import re
import sqlite3
import threading
from pathlib import Path
from urllib.parse import quote

import bcrypt
import secrets
from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request
from fastapi.responses import (
    FileResponse, HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse,
)
from pydantic import BaseModel, Field

from . import auth, db
from .auth import require_session_or_basic, require_session_or_admin
from .config import (
    LIBRARY_DIR, PAGE_CACHE_TTL_DAYS, PAGE_CACHE_MAX_BYTES, THUMB_WORKERS,
)
from .feeds import env, abs_url
from .page_cache import clean_page_cache, page_cache_status
from .scanning import (
    INDEX_STATUS, THUMB_STATUS,
    start_scan, run_precache_thumbs, index_single_file, parent_rel,
)

logger = logging.getLogger("comicopds")

router = APIRouter()

ERROR_LOG_PATH = Path("/data/thumbs_errors.log")
LOG_PATH = Path("/data/app.log")

# -------------------- Pydantic models --------------------

class UserCreate(BaseModel):
    username: str = Field(..., min_length=1, max_length=64)
    password: str = Field(..., min_length=8, max_length=72)
    is_admin: bool = False

class UserUpdate(BaseModel):
    password: str | None = Field(default=None, min_length=8, max_length=72)
    is_admin: bool | None = None

# -------------------- Login / Logout --------------------

_LOGIN_CSRF_COOKIE = "csrf_login"


def _safe_next(url: str | None) -> str:
    """Only allow relative redirect targets — prevent open redirect.
    Rejects protocol-relative (//), backslash variants (/\\ or \\), and anything
    not starting with a single slash.
    """
    if not url:
        return "/dashboard"
    if not url.startswith("/") or url.startswith("//") or "\\" in url:
        return "/dashboard"
    return url


@router.get("/login", response_class=HTMLResponse)
def login_page(request: Request, next: str = "", error: str = ""):
    # Already logged in via session → go straight to dashboard
    token = request.cookies.get("session")
    if token and auth.validate_session(token):
        return RedirectResponse(_safe_next(next) or "/dashboard", status_code=302)
    csrf_token = secrets.token_urlsafe(32)
    tpl = env.get_template("login.html")
    html = tpl.render(next=next, error=error, csrf_token=csrf_token)
    response = HTMLResponse(html)
    response.set_cookie(
        _LOGIN_CSRF_COOKIE, csrf_token,
        httponly=True, samesite="strict", max_age=1800,
    )
    return response


@router.post("/login")
async def login_submit(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    next: str = Form(default=""),
    csrf_token: str = Form(...),
):
    # CSRF: verify form token matches cookie
    cookie_csrf = request.cookies.get(_LOGIN_CSRF_COOKIE, "")
    if not cookie_csrf or not secrets.compare_digest(csrf_token, cookie_csrf):
        return RedirectResponse("/login?error=invalid_request", status_code=303)

    # Authenticate via existing rate-limited helper
    from fastapi.security import HTTPBasicCredentials
    creds = HTTPBasicCredentials(username=username, password=password)
    try:
        user = auth._authenticate(request, creds)
    except HTTPException as exc:
        if exc.status_code == 429:
            next_enc = next.replace("&", "%26")
            return RedirectResponse(f"/login?next={next_enc}&error=rate_limited", status_code=303)
        next_enc = next.replace("&", "%26")
        return RedirectResponse(f"/login?next={next_enc}&error=invalid_credentials", status_code=303)

    if not user["is_admin"]:
        next_enc = next.replace("&", "%26")
        return RedirectResponse(f"/login?next={next_enc}&error=invalid_credentials", status_code=303)

    token = auth.create_session(user["username"], bool(user["is_admin"]))
    target = _safe_next(next) or "/dashboard"
    response = RedirectResponse(target, status_code=303)
    response.set_cookie(
        "session", token,
        httponly=True, samesite="lax", max_age=8 * 3600,
    )
    response.delete_cookie(_LOGIN_CSRF_COOKIE)
    logger.info("login: session created for user=%s", user["username"])
    return response


@router.get("/logout")
def logout(request: Request):
    token = request.cookies.get("session")
    if token:
        auth.invalidate_session(token)
    response = RedirectResponse("/login", status_code=303)
    response.delete_cookie("session")
    return response


# -------------------- Dashboard --------------------

@router.get("/dashboard", response_class=HTMLResponse)
def dashboard_page(request: Request, user: str = Depends(require_session_or_admin)):
    tpl = env.get_template("dashboard.html")
    return HTMLResponse(tpl.render(user=user))

@router.get("/stats.json", response_class=JSONResponse)
def stats(_=Depends(require_session_or_basic)):
    conn = db.connect()
    try:
        return db.stats(conn)
    finally:
        conn.close()

# -------------------- User Administration --------------------

@router.get("/api/users")
def list_users(admin: str = Depends(require_session_or_admin)):
    logger.info("admin: list users by=%s", admin)
    conn = db.connect()
    try:
        rows = conn.execute("SELECT id, username, is_admin FROM users").fetchall()
        return [{"id": r["id"], "username": r["username"], "is_admin": bool(r["is_admin"])} for r in rows]
    finally:
        conn.close()

@router.post("/api/users")
def create_user(
    user: UserCreate,
    _: None = Depends(auth.require_csrf_header),
    admin: str = Depends(require_session_or_admin),
):
    logger.info("admin: user created username=%s by=%s", user.username, admin)
    conn = db.connect()
    try:
        hashed = bcrypt.hashpw(user.password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')
        conn.execute(
            "INSERT INTO users (username, password_hash, is_admin) VALUES (?, ?, ?)",
            (user.username, hashed, 1 if user.is_admin else 0)
        )
        conn.commit()
        return {"status": "ok"}
    except sqlite3.IntegrityError:
        raise HTTPException(status_code=400, detail="Username already exists")
    finally:
        conn.close()

@router.put("/api/users/{user_id}")
def update_user(
    user_id: int,
    user: UserUpdate,
    _: None = Depends(auth.require_csrf_header),
    admin: str = Depends(require_session_or_admin),
):
    logger.info("admin: user updated id=%d by=%s", user_id, admin)
    conn = db.connect()
    try:
        if user_id == 1:
            raise HTTPException(status_code=403, detail="Cannot modify the master Admin account from the UI. Change OPDS_BASIC_PASS in your docker environment.")

        updates = []
        params = []
        if user.password:
            hashed = bcrypt.hashpw(user.password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')
            updates.append("password_hash = ?")
            params.append(hashed)
        if user.is_admin is not None:
            updates.append("is_admin = ?")
            params.append(1 if user.is_admin else 0)

        if updates:
            params.append(user_id)
            conn.execute(f"UPDATE users SET {', '.join(updates)} WHERE id = ?", params)
            conn.commit()
        return {"status": "ok"}
    finally:
        conn.close()

@router.delete("/api/users/{user_id}")
def delete_user(
    user_id: int,
    _: None = Depends(auth.require_csrf_header),
    admin: str = Depends(require_session_or_admin),
):
    logger.info("admin: user deleted id=%d by=%s", user_id, admin)
    conn = db.connect()
    try:
        if user_id == 1:
            raise HTTPException(status_code=403, detail="Cannot delete the master Admin account.")
        conn.execute("DELETE FROM users WHERE id = ?", (user_id,))
        conn.commit()
        return {"status": "ok"}
    finally:
        conn.close()

# -------------------- Index status & Reindex --------------------

@router.get("/index/status", response_class=JSONResponse)
def index_status(_=Depends(require_session_or_basic)):
    conn = db.connect()
    try:
        usable = conn.execute("SELECT EXISTS(SELECT 1 FROM items LIMIT 1)").fetchone()[0] == 1
    finally:
        conn.close()
    return JSONResponse({**INDEX_STATUS, "usable": usable})

@router.post("/admin/reindex", response_class=JSONResponse)
def admin_reindex(
    force: bool = Query(False),
    _: None = Depends(auth.require_csrf_header),
    admin: str = Depends(require_session_or_admin),
):
    logger.info("admin: reindex triggered by=%s force=%s", admin, force)
    if INDEX_STATUS["running"]:
        return JSONResponse({"ok": True, "started": False, "reason": "already running"})
    start_scan(force=force)
    return JSONResponse({"ok": True, "started": True, "mode": "full" if force else "incremental"})

@router.post("/admin/reindex/path", response_class=JSONResponse)
def admin_reindex_path(path: str = Query(..., description="Relative path to a file or folder inside the library"),
                       _: None = Depends(auth.require_csrf_header),
                       admin: str = Depends(require_session_or_admin)):
    """Index or re-index a specific file or folder by relative path."""
    logger.info("admin: path reindex triggered by=%s path=%s", admin, path)
    abs_path = (LIBRARY_DIR / path).resolve()
    try:
        abs_path.relative_to(LIBRARY_DIR.resolve())
    except ValueError:
        raise HTTPException(status_code=400, detail="Path is outside the library directory")
    if not abs_path.exists():
        raise HTTPException(status_code=404, detail="Path not found")

    conn = db.connect()
    try:
        indexed = 0
        if abs_path.is_file():
            if abs_path.suffix.lower() != ".cbz":
                raise HTTPException(status_code=400, detail="Only .cbz files can be indexed")
            indexed = index_single_file(conn, abs_path)
        else:
            for dirpath, _dirnames, filenames in os.walk(abs_path):
                dpath = Path(dirpath)
                if dpath != LIBRARY_DIR:
                    rel_d = dpath.relative_to(LIBRARY_DIR).as_posix()
                    db.upsert_dir(conn, rel=rel_d, name=dpath.name,
                                  parent=parent_rel(rel_d), mtime=dpath.stat().st_mtime)
                for fn in filenames:
                    p = dpath / fn
                    if p.suffix.lower() == ".cbz":
                        indexed += index_single_file(conn, p)
        conn.commit()
    finally:
        conn.close()

    logger.info("admin: path reindex completed path=%s indexed=%d", path, indexed)
    return JSONResponse({"ok": True, "path": path, "indexed": indexed})

# -------------------- Thumbnails --------------------

@router.post("/admin/thumbs/precache", response_class=JSONResponse)
def admin_thumbs_precache(
    _: None = Depends(auth.require_csrf_header),
    admin: str = Depends(require_session_or_admin),
):
    logger.info("admin: thumbnail precache triggered by=%s", admin)
    if THUMB_STATUS["running"]:
        return JSONResponse({"ok": True, "started": False, "reason": "already running"})
    t = threading.Thread(target=run_precache_thumbs, args=(THUMB_WORKERS,), daemon=True)
    t.start()
    return JSONResponse({"ok": True, "started": True})

@router.get("/thumbs/status", response_class=JSONResponse)
def thumbs_status(_=Depends(require_session_or_basic)):
    return JSONResponse(THUMB_STATUS)

@router.get("/thumbs/errors/count", response_class=JSONResponse)
def thumbs_errors_count(_=Depends(require_session_or_basic)):
    n = 0
    size = 0
    mtime = 0.0
    if ERROR_LOG_PATH.exists():
        try:
            with ERROR_LOG_PATH.open("rb") as f:
                n = sum(1 for _ in f)
            st = ERROR_LOG_PATH.stat()
            size = st.st_size
            mtime = st.st_mtime
        except OSError:
            pass
    return {"lines": n, "size_bytes": size, "modified": mtime}

@router.get("/thumbs/errors/log")
def thumbs_errors_log(_=Depends(require_session_or_basic)):
    if not ERROR_LOG_PATH.exists():
        return PlainTextResponse("", media_type="text/plain", headers={
            "Content-Disposition": "attachment; filename=thumbs_errors.log"
        })
    return FileResponse(
        path=str(ERROR_LOG_PATH),
        media_type="text/plain",
        filename="thumbs_errors.log",
        headers={"Cache-Control": "no-store"}
    )

@router.post("/thumbs/errors/clear", response_class=JSONResponse)
def thumbs_errors_clear(
    _: None = Depends(auth.require_csrf_header),
    __=Depends(require_session_or_admin),
):
    try:
        if ERROR_LOG_PATH.exists():
            ERROR_LOG_PATH.unlink()
    except OSError:
        pass
    return {"ok": True}

# -------------------- Page cache --------------------

@router.get("/pages/cache/status", response_class=JSONResponse)
def pages_cache_status_route(_=Depends(require_session_or_basic)):
    return JSONResponse(page_cache_status())

@router.post("/admin/pages/cleanup", response_class=JSONResponse)
def admin_pages_cleanup(
    _: None = Depends(auth.require_csrf_header),
    admin: str = Depends(require_session_or_admin),
):
    logger.info("admin: page cache cleanup triggered by=%s", admin)
    res = clean_page_cache(PAGE_CACHE_TTL_DAYS, PAGE_CACHE_MAX_BYTES)
    return JSONResponse({"ok": True, **res})

# -------------------- Application logs --------------------

@router.get("/logs", response_class=HTMLResponse)
def logs_page(request: Request, user: str = Depends(require_session_or_admin)):
    tpl = env.get_template("logs.html")
    return HTMLResponse(tpl.render(user=user))

@router.get("/admin/logs", response_class=JSONResponse)
def get_logs(
    lines: int = Query(500, ge=1, le=2000),
    admin: str = Depends(require_session_or_admin),
):
    if not LOG_PATH.exists():
        return JSONResponse({"lines": [], "total_lines": 0, "file_size": 0})
    try:
        text = LOG_PATH.read_text(encoding="utf-8", errors="replace")
        all_lines = text.splitlines()
        total = len(all_lines)
        file_size = LOG_PATH.stat().st_size
        return JSONResponse({
            "lines": all_lines[-lines:],
            "total_lines": total,
            "file_size": file_size,
        })
    except OSError as e:
        logger.error("Could not read log file: %s", e)
        raise HTTPException(500, "Could not read log file")

@router.get("/admin/logs/download")
def download_logs(admin: str = Depends(require_session_or_admin)):
    if not LOG_PATH.exists():
        raise HTTPException(404, "Log file not found")
    return FileResponse(
        str(LOG_PATH),
        media_type="text/plain",
        filename="app.log",
        headers={"Cache-Control": "no-store"},
    )

@router.post("/admin/logs/clear", response_class=JSONResponse)
def clear_logs(
    _: None = Depends(auth.require_csrf_header),
    admin: str = Depends(require_session_or_admin),
):
    logger.info("admin: log file cleared by=%s", admin)
    try:
        if LOG_PATH.exists():
            LOG_PATH.write_text("", encoding="utf-8")
    except OSError as e:
        logger.error("Could not clear log file: %s", e)
        raise HTTPException(500, "Could not clear log file")
    return JSONResponse({"ok": True})

# -------------------- Smart Lists (CRUD) --------------------

SMARTLISTS_PATH = Path("/data/smartlists.json")
_SMARTLISTS_MAX_BODY = 512 * 1024  # 512 KB


def _slugify(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (name or "").lower()).strip("-") or "list"


def _load_smartlists() -> list[dict]:
    if not SMARTLISTS_PATH.exists():
        return []
    try:
        data = json.loads(SMARTLISTS_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        logger.warning("Failed to load smartlists: %s", e)
        return []

    if isinstance(data, dict) and "lists" in data and isinstance(data["lists"], list):
        data = data["lists"]
    elif not isinstance(data, list):
        return []

    return [item for item in data if isinstance(item, dict)]


def _save_smartlists(lists: list[dict]) -> None:
    SMARTLISTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    SMARTLISTS_PATH.write_text(
        json.dumps(lists, ensure_ascii=False, indent=2), encoding="utf-8"
    )

@router.get("/search", response_class=HTMLResponse)
def smartlists_page(user: str = Depends(require_session_or_admin)):
    tpl = env.get_template("smartlists.html")
    return HTMLResponse(tpl.render(user=user))

@router.get("/smartlists.json", response_class=JSONResponse)
def smartlists_get(_=Depends(require_session_or_basic)):
    return JSONResponse(_load_smartlists())

@router.post("/smartlists.json", response_class=JSONResponse)
async def smartlists_post(
    request: Request,
    _: None = Depends(auth.require_csrf_header),
    admin: str = Depends(require_session_or_admin),
):
    logger.info("admin: smart lists updated by=%s", admin)
    raw = await request.body()
    if not raw:
        return JSONResponse({"ok": False, "error": "empty body"}, status_code=400)
    if len(raw) > _SMARTLISTS_MAX_BODY:
        return JSONResponse({"ok": False, "error": "body too large"}, status_code=413)

    try:
        data = json.loads(raw.decode("utf-8"))
    except Exception as e:
        return JSONResponse({"ok": False, "error": f"invalid json: {e}"}, status_code=400)

    if isinstance(data, dict) and "lists" in data and isinstance(data["lists"], list):
        lists = data["lists"]
    elif isinstance(data, dict):
        lists = [data]
    elif isinstance(data, list):
        lists = data
    else:
        return JSONResponse({"ok": False, "error": "expected JSON array or object"}, status_code=400)

    try:
        _save_smartlists(lists)
    except Exception as e:
        return JSONResponse({"ok": False, "error": f"write failed: {e}"}, status_code=500)

    return JSONResponse({"ok": True, "saved": len(lists)})

# -------------------- Debug --------------------

@router.get("/debug/children", response_class=JSONResponse)
def debug_children(path: str = "", _=Depends(require_session_or_admin)):
    conn = db.connect()
    try:
        rows = db.children_page(conn, path.strip("/"), 1000, 0)
    finally:
        conn.close()
    return JSONResponse([{"rel": r["rel"], "is_dir": int(r["is_dir"]), "name": r["name"]} for r in rows])

@router.get("/debug/fts")
def debug_fts(_=Depends(require_session_or_admin)):
    return {"fts5": db.has_fts5()}

@router.get("/debug/build", response_class=JSONResponse)
def debug_build(_=Depends(require_session_or_admin)):
    from .main import _git_commit
    from .feeds import abs_url
    from .config import SERVER_BASE, URL_PREFIX
    return JSONResponse({
        "commit": _git_commit(),
        "server_base": SERVER_BASE,
        "url_prefix": URL_PREFIX,
        "opds2_manifest_path": abs_url("/opds/v2/manifest"),
    })
