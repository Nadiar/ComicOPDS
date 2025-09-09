import os
from fastapi import HTTPException, Request
from fastapi.security import HTTPBasic, HTTPBasicCredentials

security = HTTPBasic()

DISABLE_AUTH = os.getenv("DISABLE_AUTH", "false").strip().lower() in ("1","true","yes")
USER = os.getenv("OPDS_BASIC_USER", "").strip()
PASS = os.getenv("OPDS_BASIC_PASS", "").strip()

def require_basic(request: Request, credentials: HTTPBasicCredentials = None):
    # If disabled, or no credentials configured at all, allow through
    if DISABLE_AUTH or not USER or not PASS:
        return
    if credentials is None:
        credentials = security(request)
    if not (credentials.username == USER and credentials.password == PASS):
        raise HTTPException(status_code=401, detail="Not authenticated",
                            headers={"WWW-Authenticate": "Basic"})
