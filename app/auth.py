# app/auth.py
from fastapi import Security, HTTPException, status
from fastapi.security import HTTPBasic, HTTPBasicCredentials
import os, secrets, logging

log = logging.getLogger("comicopds.auth")

def _truthy(v: str | None) -> bool:
    return str(v or "").strip().lower() in ("1", "true", "yes", "on")

DISABLE_AUTH = _truthy(os.getenv("DISABLE_AUTH"))
USER = os.getenv("OPDS_BASIC_USER", "admin")
PASS = os.getenv("OPDS_BASIC_PASS", "change-me")

# IMPORTANT: auto_error=False so missing creds don't trigger 401 automatically
security = HTTPBasic(auto_error=False)

def require_basic(credentials: HTTPBasicCredentials | None = Security(security)):
    """
    Use as: Depends(require_basic)
    - If DISABLE_AUTH is true -> allow all (no browser prompt).
    - Otherwise verify Basic credentials.
    """
    if DISABLE_AUTH:
        return  # auth disabled entirely

    if credentials is None:
        # No header provided -> prompt
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": 'Basic realm="ComicOPDS"'},
        )

    ok_user = secrets.compare_digest(credentials.username or "", USER)
    ok_pass = secrets.compare_digest(credentials.password or "", PASS)
    if not (ok_user and ok_pass):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials",
            headers={"WWW-Authenticate": 'Basic realm="ComicOPDS"'},
        )
    # authenticated -> nothing to return
