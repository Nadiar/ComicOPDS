import os
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from secrets import compare_digest

USER = os.environ.get("OPDS_BASIC_USER")
PASS = os.environ.get("OPDS_BASIC_PASS")

security = HTTPBasic()

def require_basic(creds: HTTPBasicCredentials = Depends(security)):
    if not USER or not PASS:
        return  # auth disabled
    if compare_digest(creds.username, USER) and compare_digest(creds.password, PASS):
        return
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Unauthorized",
        headers={"WWW-Authenticate": "Basic"},
    )
