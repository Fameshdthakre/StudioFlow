"""Application shell and workspace status routes."""

from typing import Optional

from fastapi import APIRouter, Cookie, Depends
from fastapi.responses import FileResponse

from ..dependencies import current_user, get_workspace
from ..services import system as service
from ..workspace import Workspace

router = APIRouter()


@router.get("/")
def home(workspace: Workspace = Depends(get_workspace)):
    return FileResponse(workspace.base_dir / "static/index.html")


@router.get("/api/health")
def health(workspace: Workspace = Depends(get_workspace)):
    return service.health(workspace)


@router.get("/api/config")
def config(
    studio_session: Optional[str] = Cookie(default=None),
    workspace: Workspace = Depends(get_workspace),
):
    return service.config(workspace, studio_session)


@router.get("/api/stats")
def stats(
    user: str = Depends(current_user), workspace: Workspace = Depends(get_workspace)
):
    return service.stats(workspace)
