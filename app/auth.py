from __future__ import annotations

import ipaddress
import logging
import os
import secrets
import threading
import time
from datetime import datetime, timedelta
from urllib.parse import quote as _url_quote

import bcrypt
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPBasic, HTTPBasicCredentials

from . import db
from .config import TRUSTED_PROXIES_STR, _parse_bool

log = logging.getLogger("comicopds.auth")

DISABLE_AUTH = _parse_bool("DISABLE_AUTH", False)
USER = os.getenv("OPDS_BASIC_USER", "admin")
PASS = os.getenv("OPDS_BASIC_PASS", "change-me")

if DISABLE_AUTH:
    log.warning("authentication is DISABLED via DISABLE_AUTH env var")

security = HTTPBasic()

# Pre-parse trusted networks
try:
    TRUSTED_NETWORKS = [ipaddress.ip_network(n.strip()) for n in TRUSTED_PROXIES_STR.split(",") if n.strip()]
except ValueError:
    TRUSTED_NETWORKS = []

_FAIL_COUNTS: dict[str, list[float]] = {}
_FAIL_COUNTS_LOCK = threading.Lock()
_RATE_LIMIT_WINDOW = 300
_RATE_LIMIT_MAX = 10


def _prune_failures(now: float, ip: str) -> list[float]:
    attempts = [t for t in _FAIL_COUNTS.get(ip, []) if (now - t) < _RATE_LIMIT_WINDOW]
    _FAIL_COUNTS[ip] = attempts
    return attempts


def _is_rate_limited(ip: str) -> bool:
    now = time.time()
    with _FAIL_COUNTS_LOCK:
        attempts = _prune_failures(now, ip)
        return len(attempts) >= _RATE_LIMIT_MAX


def _record_failure(ip: str) -> None:
    now = time.time()
    with _FAIL_COUNTS_LOCK:
        attempts = _prune_failures(now, ip)
        attempts.append(now)
        _FAIL_COUNTS[ip] = attempts


def _clear_failures(ip: str) -> None:
    with _FAIL_COUNTS_LOCK:
        _FAIL_COUNTS.pop(ip, None)


# -------------------- Session store --------------------

_SESSION_TTL = timedelta(hours=8)
_SESSION_STORE: dict[str, dict] = {}
_SESSION_LOCK = threading.Lock()


def create_session(username: str, is_admin: bool) -> str:
    token = secrets.token_urlsafe(32)
    with _SESSION_LOCK:
        _SESSION_STORE[token] = {
            "username": username,
            "is_admin": is_admin,
            "expires": datetime.utcnow() + _SESSION_TTL,
        }
    return token


def validate_session(token: str) -> dict | None:
    with _SESSION_LOCK:
        session = _SESSION_STORE.get(token)
        if not session:
            return None
        if datetime.utcnow() > session["expires"]:
            del _SESSION_STORE[token]
            return None
        session["expires"] = datetime.utcnow() + _SESSION_TTL
        return dict(session)


def invalidate_session(token: str) -> None:
    with _SESSION_LOCK:
        _SESSION_STORE.pop(token, None)


# -------------------- CSRF header check --------------------

def require_csrf_header(request: Request) -> None:
    if request.headers.get("X-Requested-With") != "XMLHttpRequest":
        raise HTTPException(status_code=403, detail="Missing required header")

def get_real_client_ip(request: Request) -> str:
    client_host = request.client.host if request.client else "127.0.0.1"

    # Is the direct client in our trusted networks?
    is_trusted = False
    try:
        client_ip = ipaddress.ip_address(client_host)
        is_trusted = any(client_ip in net for net in TRUSTED_NETWORKS)
    except ValueError:
        pass

    if is_trusted:
        # Trust the proxy headers
        forwarded_for = request.headers.get("X-Forwarded-For")
        if forwarded_for:
            # Take the rightmost entry — appended by the trusted proxy, not spoofable by client
            return forwarded_for.split(",")[-1].strip()

        real_ip = request.headers.get("X-Real-IP")
        if real_ip:
            return real_ip.strip()

    return client_host

def authenticate_user(credentials: HTTPBasicCredentials) -> dict | None:
    supplied_user = credentials.username.encode("utf8")
    supplied_pass = credentials.password.encode("utf8")

    if secrets.compare_digest(supplied_user, USER.encode("utf8")) and \
       secrets.compare_digest(supplied_pass, PASS.encode("utf8")):
        log.info("auth success (env): user=%s", credentials.username)
        return {"id": 1, "username": USER, "is_admin": 1}

    conn = db.connect()
    try:
        user_row = conn.execute(
            "SELECT id, username, password_hash, is_admin FROM users WHERE username = ?",
            (credentials.username,)
        ).fetchone()

        if user_row:
            hashed = user_row["password_hash"]
            if bcrypt.checkpw(supplied_pass, hashed.encode('utf-8')):
                log.info("auth success (db): user=%s", credentials.username)
                return {
                    "id": user_row["id"],
                    "username": user_row["username"],
                    "is_admin": user_row["is_admin"]
                }
    finally:
        conn.close()

    log.warning("auth failure: user=%s", credentials.username)
    return None

def _authenticate(request: Request, credentials: HTTPBasicCredentials) -> dict:
    """Rate-limit check + credential verification. Returns user dict or raises."""
    client_ip = get_real_client_ip(request)
    ua = request.headers.get("User-Agent", "Unknown")
    accept = request.headers.get("Accept", "-")

    if _is_rate_limited(client_ip):
        log.warning("login rate-limited: ip=%s ua=%s", client_ip, ua)
        raise HTTPException(status_code=429, detail="Too many failed login attempts")

    user = authenticate_user(credentials)
    if user:
        _clear_failures(client_ip)
        return user

    _record_failure(client_ip)
    log.warning("login rejected: user=%s ip=%s ua=%s accept=%s", credentials.username, client_ip, ua, accept)
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Incorrect username or password",
        headers={"WWW-Authenticate": "Basic"},
    )

def require_basic(request: Request, credentials: HTTPBasicCredentials = Depends(security)) -> str:
    if DISABLE_AUTH:
        return "anonymous"
    user = _authenticate(request, credentials)
    log.info("login accepted: user=%s ip=%s", user["username"], get_real_client_ip(request))
    return user["username"]

def require_admin(request: Request, credentials: HTTPBasicCredentials = Depends(security)) -> str:
    if DISABLE_AUTH:
        return "anonymous"
    user = _authenticate(request, credentials)
    if not user["is_admin"]:
        log.warning("admin access denied (not admin): user=%s", user["username"])
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username, password, or insufficient permissions",
            headers={"WWW-Authenticate": "Basic"},
        )
    log.info("admin access accepted: user=%s ip=%s", user["username"], get_real_client_ip(request))
    return user["username"]


# -------------------- Session-aware dashboard dependencies --------------------

optional_basic = HTTPBasic(auto_error=False)


def _unauthed_response(request: Request) -> None:
    """Raise 302 for browser page requests, 401 JSON for API/XHR calls."""
    if (request.headers.get("X-Requested-With") == "XMLHttpRequest"
            or "application/json" in request.headers.get("Accept", "")):
        raise HTTPException(status_code=401, detail="Authentication required")
    next_path = _url_quote(
        request.url.path + (f"?{request.url.query}" if request.url.query else "")
    )
    raise HTTPException(status_code=302, headers={"Location": f"/login?next={next_path}"})


def require_session_or_basic(
    request: Request,
    credentials: HTTPBasicCredentials | None = Depends(optional_basic),
) -> str:
    """Dashboard auth: accepts session cookie OR Basic Auth. Any authenticated user."""
    if DISABLE_AUTH:
        return "anonymous"
    token = request.cookies.get("session")
    if token:
        session = validate_session(token)
        if session:
            return session["username"]
    if credentials:
        user = _authenticate(request, credentials)
        return user["username"]
    _unauthed_response(request)


def require_session_or_admin(
    request: Request,
    credentials: HTTPBasicCredentials | None = Depends(optional_basic),
) -> str:
    """Dashboard auth: accepts session cookie OR Basic Auth. Admin users only."""
    if DISABLE_AUTH:
        return "anonymous"
    token = request.cookies.get("session")
    if token:
        session = validate_session(token)
        if session:
            if not session["is_admin"]:
                raise HTTPException(status_code=403, detail="Admin access required")
            return session["username"]
    if credentials:
        user = _authenticate(request, credentials)
        if not user["is_admin"]:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Incorrect username, password, or insufficient permissions",
                headers={"WWW-Authenticate": "Basic"},
            )
        log.info("admin access accepted (basic): user=%s ip=%s", user["username"], get_real_client_ip(request))
        return user["username"]
    _unauthed_response(request)
