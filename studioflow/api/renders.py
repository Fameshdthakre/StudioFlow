"""HTTP routes for rendering."""

from typing import Optional

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    Header,
    UploadFile,
)
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from ..dependencies import current_user, get_workspace
from ..provider import DEFAULT_MODEL, REVISION_MODEL
from ..services import rendering as service
from ..workspace import Workspace

router = APIRouter()


class Revision(BaseModel):
    render_id: str
    instructions: str = Field(min_length=5, max_length=3500)
    model: str = REVISION_MODEL
    quality: str = "high"
    remember_rule: bool = False


class Approval(BaseModel):
    approved: bool


@router.get("/api/renders/{rid}/history")
def render_history(
    rid: str,
    user: str = Depends(current_user),
    workspace: Workspace = Depends(get_workspace),
):
    return service.render_history(workspace, rid=rid, user=user)


@router.post("/api/generate")
def generate(
    files: list[UploadFile] = File(...),
    skus: str = Form(...),
    kind: str = Form(...),
    scenes: str = Form(...),
    scene_sets: str = Form(""),
    model: str = Form(DEFAULT_MODEL),
    quality: str = Form("medium"),
    canvas: str = Form("square"),
    aplus_profile_id: str = Form(""),
    idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
    user: str = Depends(current_user),
    workspace: Workspace = Depends(get_workspace),
):
    return service.generate(
        workspace,
        files=files,
        skus=skus,
        kind=kind,
        scenes=scenes,
        scene_sets=scene_sets,
        model=model,
        quality=quality,
        canvas=canvas,
        aplus_profile_id=aplus_profile_id,
        idempotency_key=idempotency_key,
        user=user,
    )


@router.post("/api/revise")
def revise(
    body: Revision,
    idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
    user: str = Depends(current_user),
    workspace: Workspace = Depends(get_workspace),
):
    return service.revise(
        workspace, body=body, idempotency_key=idempotency_key, user=user
    )


@router.get("/api/renders")
def renders(
    user: str = Depends(current_user), workspace: Workspace = Depends(get_workspace)
):
    return service.renders(workspace, user=user)


@router.get("/api/jobs")
def jobs(
    user: str = Depends(current_user), workspace: Workspace = Depends(get_workspace)
):
    return service.jobs(workspace, user=user)


@router.get("/api/media/{filename}")
def media(
    filename: str,
    user: str = Depends(current_user),
    workspace: Workspace = Depends(get_workspace),
):
    return FileResponse(
        service.media(workspace, filename=filename, user=user),
        media_type="image/jpeg",
        filename=filename,
    )


@router.post("/api/renders/{rid}/approve")
def approve(
    rid: str,
    body: Approval,
    user: str = Depends(current_user),
    workspace: Workspace = Depends(get_workspace),
):
    return service.approve(workspace, rid=rid, body=body, user=user)
