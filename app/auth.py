# app/auth.py
import ipaddress
import logging
import os
import secrets
from typing import Optional

import bcrypt
from fastapi import Depends, HTTPException, Request, Security, status
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

def get_real_client_ip(request: Request) -> str:
    """Check if the requesting client is a trusted proxy, and if so, return the forwarded IP."""
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
            # X-Forwarded-For can be a comma separated list, the first is the real client
            return forwarded_for.split(",")[0].strip()

        real_ip = request.headers.get("X-Real-IP")
        if real_ip:
            return real_ip.strip()

    return client_host

def authenticate_user(credentials: HTTPBasicCredentials) -> Optional[dict]:
    """Authenticate user by username and password.

    Checks environment variables first, then database. Returns user dict on success, None on failure.
    """
    supplied_user = credentials.username.encode("utf8")
    supplied_pass = credentials.password.encode("utf8")

    # Standard constant-time check against the root config user
    if secrets.compare_digest(supplied_user, USER.encode("utf8")) and \
       secrets.compare_digest(supplied_pass, PASS.encode("utf8")):
        log.info("auth success (env): user=%s", credentials.username)
        return {"id": 1, "username": USER, "is_admin": 1}

    # If that fails, check the SQLite database
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

def require_basic(request: Request, credentials: HTTPBasicCredentials = Depends(security)) -> str:
    """FastAPI dependency to require basic HTTP authentication.

    Returns username on success, raises HTTPException on authentication failure.
    """
    if DISABLE_AUTH:
        return "anonymous"

    user = authenticate_user(credentials)
    client_ip = get_real_client_ip(request)
    ua = request.headers.get("User-Agent", "Unknown")
    accept = request.headers.get("Accept") or "No mime type requested"
    if not user:
        log.warning("login rejected: user=%s ip=%s ua=%s accept=%s", credentials.username, client_ip, ua, accept)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
            headers={"WWW-Authenticate": "Basic"},
        )
    log.info("login accepted: user=%s ip=%s ua=%s accept=%s", user["username"], client_ip, ua, accept)
    return user["username"]

def require_admin(request: Request, credentials: HTTPBasicCredentials = Depends(security)) -> str:
    """FastAPI dependency to require admin-level HTTP authentication.

    Returns username on success, raises HTTPException if user is not admin.
    """
    if DISABLE_AUTH:
        return "anonymous"

    user = authenticate_user(credentials)
    client_ip = get_real_client_ip(request)
    ua = request.headers.get("User-Agent", "Unknown")
    accept = request.headers.get("Accept") or "No mime type requested"
    if not user or not user["is_admin"]:
        log.warning("admin access rejected: user=%s ip=%s ua=%s accept=%s", credentials.username, client_ip, ua, accept)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username, password, or insufficient permissions",
            headers={"WWW-Authenticate": "Basic"},
        )
    log.info("admin access accepted: user=%s ip=%s ua=%s accept=%s", user["username"], client_ip, ua, accept)
    return user["username"]
