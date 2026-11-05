"""HTTP routes for rendering."""

from typing import Optional
import json

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    Header,
    UploadFile,
    HTTPException,
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
    files: list[UploadFile] | None = File(None),
    skus: str = Form(""),
    kind: str = Form(""),
    scenes: str = Form(""),
    generation_plan: str = Form(""),
    scene_sets: str = Form(""),
    model: str = Form(DEFAULT_MODEL),
    quality: str = Form("high"),
    canvas: str = Form("square"),
    aplus_profile_id: str = Form(""),
    idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
    user: str = Depends(current_user),
    workspace: Workspace = Depends(get_workspace),
):
    if generation_plan:
        from ..services.creation import submit

        try:
            plan = json.loads(generation_plan)
        except ValueError as exc:
            raise HTTPException(400, "Invalid generation plan JSON") from exc
        return submit(workspace, plan, user, idempotency_key)
    return service.generate(
        workspace,
        files=files or [],
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
    from ..services.assets import legacy_id
    from .library import asset_response

    aid = legacy_id(workspace, "outputs/" + filename)
    if aid:
        return asset_response(workspace, aid)
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
