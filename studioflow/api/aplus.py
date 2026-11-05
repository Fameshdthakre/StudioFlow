"""HTTP routes for aplus."""

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field

from ..dependencies import current_user, get_workspace
from ..services import aplus as service
from ..services import assets
from .library import asset_response
from ..services.aplus import DEFAULT_MASTER
from ..workspace import Workspace


class ProfileInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)
    name: str = Field(min_length=2, max_length=100)
    master_instructions: str = Field(
        default=DEFAULT_MASTER, min_length=15, max_length=9000
    )
    branding_notes: str = Field(default="", max_length=5000)
    heading_font: str = Field(default="Cinzel", min_length=1, max_length=100)
    body_font: str = Field(default="Roboto", min_length=1, max_length=100)
    primary_color: str = Field(default="#283F44", pattern=r"^#[0-9a-fA-F]{6}$")
    secondary_color: str = Field(default="#F4E7D9", pattern=r"^#[0-9a-fA-F]{6}$")
    accent_color: str = Field(default="#9E7655", pattern=r"^#[0-9a-fA-F]{6}$")


def require_feature(workspace: Workspace = Depends(get_workspace)):
    if not workspace.settings.enable_aplus:
        raise HTTPException(404, "A+ Studio is disabled")


router = APIRouter(prefix="/api/aplus", dependencies=[Depends(require_feature)])


@router.get("/profiles")
def profiles(user=Depends(current_user), workspace: Workspace = Depends(get_workspace)):
    return service.profiles(workspace, user=user)


@router.post("/profiles")
def create(
    body: ProfileInput,
    user=Depends(current_user),
    workspace: Workspace = Depends(get_workspace),
):
    return service.create(workspace, body=body, user=user)


@router.put("/profiles/{pid}")
def update(
    pid: str,
    body: ProfileInput,
    user=Depends(current_user),
    workspace: Workspace = Depends(get_workspace),
):
    return service.update(workspace, pid=pid, body=body, user=user)


@router.post("/profiles/{pid}/logo")
def upload_logo(
    pid: str,
    file: UploadFile = File(...),
    user=Depends(current_user),
    workspace: Workspace = Depends(get_workspace),
):
    return service.upload_logo(workspace, pid=pid, file=file, user=user)


@router.get("/profiles/{pid}/logo")
def logo(
    pid: str, user=Depends(current_user), workspace: Workspace = Depends(get_workspace)
):
    path = service.logo(workspace, pid=pid, user=user)
    aid = assets.legacy_id(workspace, "brand_logos/" + path.name)
    return (
        asset_response(workspace, aid)
        if aid
        else FileResponse(path, media_type="image/png")
    )
