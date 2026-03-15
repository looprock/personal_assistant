"""
Single-user JWT auth for the web UI.

Login: POST /auth/login  { username, password }  → sets httponly JWT cookie
Logout: POST /auth/logout                         → clears cookie

Required env vars:
    PA_UI_USERNAME  — web UI username (default: admin)
    PA_UI_PASSWORD  — web UI password (required)
    PA_JWT_SECRET   — secret for signing JWTs (required)
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Form, HTTPException, Request, Response, status
from fastapi.responses import RedirectResponse
from jose import JWTError, jwt
from passlib.context import CryptContext

router = APIRouter(prefix="/auth", tags=["auth"])

_pwd_ctx = CryptContext(schemes=["bcrypt"], deprecated="auto")
_ALGORITHM = "HS256"
_TOKEN_EXPIRE_DAYS = 30
_COOKIE_NAME = "pa_token"


def _secret() -> str:
    s = os.environ.get("PA_JWT_SECRET")
    if not s:
        raise RuntimeError("PA_JWT_SECRET env var is not set")
    return s


def _valid_credentials(username: str, password: str) -> bool:
    from app.config import cfg
    expected_pass = os.environ.get("PA_UI_PASSWORD", "")
    return username == cfg.ui.username and password == expected_pass


def create_token() -> str:
    expire = datetime.now(timezone.utc) + timedelta(days=_TOKEN_EXPIRE_DAYS)
    return jwt.encode({"exp": expire}, _secret(), algorithm=_ALGORITHM)


def verify_token(token: str) -> bool:
    try:
        jwt.decode(token, _secret(), algorithms=[_ALGORITHM])
        return True
    except JWTError:
        return False


def require_auth(request: Request) -> None:
    """FastAPI dependency — raises 401 or redirects to /auth/login if not authed."""
    token = request.cookies.get(_COOKIE_NAME)
    if not token or not verify_token(token):
        raise HTTPException(
            status_code=status.HTTP_303_SEE_OTHER,
            headers={"Location": "/auth/login"},
        )


# ── Routes ────────────────────────────────────────────────────────────────────

@router.get("/login")
async def login_page(request: Request):
    from fastapi.templating import Jinja2Templates
    from pathlib import Path
    templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
    return templates.TemplateResponse("login.html", {"request": request})


@router.post("/login")
async def login(
    request: Request,
    response: Response,
    username: str = Form(...),
    password: str = Form(...),
):
    if not _valid_credentials(username, password):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")

    token = create_token()
    resp = RedirectResponse(url="/", status_code=status.HTTP_303_SEE_OTHER)
    resp.set_cookie(
        _COOKIE_NAME,
        token,
        httponly=True,
        secure=request.url.scheme == "https",
        samesite="lax",
        max_age=_TOKEN_EXPIRE_DAYS * 86400,
    )
    return resp


@router.post("/logout")
async def logout():
    resp = RedirectResponse(url="/auth/login", status_code=status.HTTP_303_SEE_OTHER)
    resp.delete_cookie(_COOKIE_NAME)
    return resp
