"""Request dependencies shared by feature routers."""

from typing import Optional

from fastapi import Cookie, Depends, HTTPException, Request

from .services.auth import get_session_user
from .workspace import Workspace


def get_workspace(request: Request) -> Workspace:
    return request.app.state.workspace


def current_user(
    studio_session: Optional[str] = Cookie(default=None),
    workspace: Workspace = Depends(get_workspace),
):
    if workspace.settings.anonymous_demo:
        return "Demo user"
    if not studio_session:
        raise HTTPException(401, "Sign in required")
    user = get_session_user(workspace, studio_session)
    if not user:
        raise HTTPException(401, "Session expired. Sign in again.")
    return user
