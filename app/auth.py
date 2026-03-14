# app/auth.py
import ipaddress
import logging
import os
import secrets

import bcrypt
from fastapi import Security, HTTPException, status, Request, Depends
from fastapi.security import HTTPBasic, HTTPBasicCredentials

from . import db
from .config import TRUSTED_PROXIES_STR, _parse_bool

log = logging.getLogger("comicopds.auth")

DISABLE_AUTH = _parse_bool("DISABLE_AUTH", False)
USER = os.getenv("OPDS_BASIC_USER", "admin")
PASS = os.getenv("OPDS_BASIC_PASS", "change-me")

security = HTTPBasic()

# Pre-parse trusted networks
try:
    TRUSTED_NETWORKS = [ipaddress.ip_network(n.strip()) for n in TRUSTED_PROXIES_STR.split(",") if n.strip()]
except ValueError:
    TRUSTED_NETWORKS = []

def get_real_client_ip(request: Request) -> str:
    """
    Get the real client IP address, respecting trusted proxy headers.

    If the direct client IP is in TRUSTED_NETWORKS, returns the IP from X-Forwarded-For
    or X-Real-IP headers. Otherwise returns the direct client IP.

    Args:
        request: FastAPI Request object

    Returns:
        Client IP address string
    """
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

def authenticate_user(credentials: HTTPBasicCredentials) -> dict:
    supplied_user = credentials.username.encode("utf8")
    supplied_pass = credentials.password.encode("utf8")
    
    # Standard constant-time check against the root config user
    if secrets.compare_digest(supplied_user, USER.encode("utf8")) and \
       secrets.compare_digest(supplied_pass, PASS.encode("utf8")):
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
                return {
                    "id": user_row["id"],
                    "username": user_row["username"],
                    "is_admin": user_row["is_admin"]
                }
    finally:
        conn.close()
        
    return None

def require_basic(request: Request, credentials: HTTPBasicCredentials = Depends(security)) -> str:
    """
    Dependency for HTTP Basic Auth on general endpoints.

    Returns the authenticated username if auth is valid, or raises HTTP 401.
    Returns "anonymous" if DISABLE_AUTH is true.

    Args:
        request: FastAPI Request object
        credentials: HTTP Basic credentials from header

    Returns:
        Username string

    Raises:
        HTTPException: 401 Unauthorized if credentials are invalid
    """
    # Optional IP logging can be done here using get_real_client_ip(request)

    if DISABLE_AUTH:
        return "anonymous"

    user = authenticate_user(credentials)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
            headers={"WWW-Authenticate": "Basic"},
        )
    return user["username"]

def require_admin(request: Request, credentials: HTTPBasicCredentials = Depends(security)) -> str:
    """
    Dependency for HTTP Basic Auth on admin-only endpoints.

    Returns the authenticated admin username if auth is valid and user is admin, or raises HTTP 401.
    Returns "anonymous" if DISABLE_AUTH is true.

    Args:
        request: FastAPI Request object
        credentials: HTTP Basic credentials from header

    Returns:
        Username string

    Raises:
        HTTPException: 401 Unauthorized if credentials are invalid or user lacks admin permissions
    """
    if DISABLE_AUTH:
        return "anonymous"

    user = authenticate_user(credentials)
    if not user or not user["is_admin"]:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username, password, or insufficient permissions",
            headers={"WWW-Authenticate": "Basic"},
        )
    return user["username"]
