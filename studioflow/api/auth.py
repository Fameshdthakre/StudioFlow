"""Sign-in and session cookies."""

import hmac
from typing import Optional

from fastapi import APIRouter, Cookie, Depends, HTTPException, Response
from pydantic import BaseModel

from ..dependencies import get_workspace
from ..services.auth import create_session, delete_session
from ..workspace import Workspace

router = APIRouter()


class LoginBody(BaseModel):
    username: str
    password: str


@router.post("/api/login")
def login(
    body: LoginBody, response: Response, workspace: Workspace = Depends(get_workspace)
):
    settings = workspace.settings
    if not settings.team:
        if settings.mode == "demo":
            return {"user": "Demo user", "mode": settings.mode}
        raise HTTPException(500, "Team users are not configured")
    accepted = any(
        hmac.compare_digest(name.encode(), body.username.encode())
        and hmac.compare_digest(password.encode(), body.password.encode())
        for name, password in settings.team.items()
    )
    if not accepted:
        raise HTTPException(401, "Incorrect username or password")
    token = create_session(workspace, body.username, max_age_days=7)
    response.set_cookie(
        "studio_session",
        token,
        httponly=True,
        secure=settings.secure_cookie,
        samesite="lax",
        max_age=7 * 24 * 3600,
    )
    return {"user": body.username, "mode": settings.mode}


@router.post("/api/logout")
def logout(
    response: Response,
    studio_session: Optional[str] = Cookie(default=None),
    workspace: Workspace = Depends(get_workspace),
):
    delete_session(workspace, studio_session)
    response.delete_cookie("studio_session")
    return {"ok": True}
