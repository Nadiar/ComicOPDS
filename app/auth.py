from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBasic, HTTPBasicCredentials
import secrets
import os

security = HTTPBasic()

DISABLE_AUTH = os.getenv("DISABLE_AUTH", "false").strip().lower() in ("1","true","yes")
USER = os.getenv("OPDS_BASIC_USER", "").strip()
PASS = os.getenv("OPDS_BASIC_PASS", "").strip()

async def require_basic(credentials: HTTPBasicCredentials = Depends(security)):
    if DISABLE_AUTH:
        return True

    # Use secrets.compare_digest to avoid timing attacks
    correct_user = secrets.compare_digest(credentials.username, USER or "")
    correct_pass = secrets.compare_digest(credentials.password, PASS or "")

    if not (correct_user and correct_pass):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials",
            headers={"WWW-Authenticate": "Basic"},
        )
    return True