"""Asset delivery, mixed generation support, composition, and bulk exports."""

import re
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, UploadFile
from fastapi.responses import StreamingResponse, FileResponse
from pydantic import BaseModel, Field

from ..dependencies import current_user, get_workspace
from ..services import assets, creation, library, maintenance
from ..workspace import Workspace

router = APIRouter()


def asset_response(workspace, aid, filename=None):
    asset = assets.get(workspace, aid)
    try:
        source = workspace.storage.open(asset["object_key"])
    except Exception as exc:
        raise HTTPException(503, "Image storage is temporarily unavailable") from exc

    def stream():
        try:
            while chunk := source.read(256 * 1024):
                yield chunk
        finally:
            source.close()

    headers = {
        "Content-Length": str(asset["byte_size"]),
        "Cache-Control": "private, no-cache",
        "X-Content-Type-Options": "nosniff",
    }
    if filename:
        headers["Content-Disposition"] = "attachment; filename*=UTF-8''" + quote(
            filename
        )
    return StreamingResponse(stream(), media_type=asset["mime"], headers=headers)


@router.post("/api/assets")
def upload_asset(
    file: UploadFile = File(...),
    role: str = Form(...),
    user: str = Depends(current_user),
    workspace: Workspace = Depends(get_workspace),
):
    with workspace.db() as c:
        maintenance.require_writable(c)
    return assets.upload(workspace, file.file.read(18 * 1024 * 1024 + 1), role, user)


@router.get("/api/assets/{aid}")
def get_asset(
    aid: str,
    user: str = Depends(current_user),
    workspace: Workspace = Depends(get_workspace),
):
    return asset_response(workspace, aid)


@router.get("/api/library")
def list_library(
    q: str = "",
    status: str = "all",
    kind: str = "all",
    cursor: int | None = None,
    limit: int = 60,
    selection: bool = False,
    user: str = Depends(current_user),
    workspace: Workspace = Depends(get_workspace),
):
    return library.list_images(
        workspace,
        q=q,
        status=status,
        kind=kind,
        cursor=cursor,
        limit=limit,
        selection=selection,
    )


class CompositionRequest(BaseModel):
    recipe: dict


@router.post("/api/renders/{rid}/compose")
def compose_image(
    rid: str,
    body: CompositionRequest,
    key: str | None = Header(None, alias="Idempotency-Key"),
    user: str = Depends(current_user),
    workspace: Workspace = Depends(get_workspace),
):
    return creation.recompose(workspace, rid, body.recipe, user, key)


class ExportRequest(BaseModel):
    render_ids: list[str] = Field(min_length=1, max_length=250)
    identifier: str = "auto"


@router.post("/api/exports/preview")
def preview_export(
    body: ExportRequest,
    user: str = Depends(current_user),
    workspace: Workspace = Depends(get_workspace),
):
    return library.preview_export(workspace, body.render_ids, body.identifier)


@router.post("/api/exports")
def create_export(
    body: ExportRequest,
    user: str = Depends(current_user),
    workspace: Workspace = Depends(get_workspace),
):
    return library.create_export(workspace, body.render_ids, body.identifier, user)


@router.get("/api/exports/{eid}")
def export_status(
    eid: str,
    user: str = Depends(current_user),
    workspace: Workspace = Depends(get_workspace),
):
    return library.export_status(workspace, eid, user)


@router.get("/api/exports/{eid}/download")
def download_export(
    eid: str,
    user: str = Depends(current_user),
    workspace: Workspace = Depends(get_workspace),
):
    status = library.export_status(workspace, eid, user)
    if status["status"] != "completed" or not re.fullmatch(r"[0-9a-f]{32}", eid):
        raise HTTPException(409, "Export is not ready")
    target = workspace.exports / (eid + ".zip")
    if not target.is_file():
        raise HTTPException(404, "Export file is unavailable; prepare it again")
    return FileResponse(
        target, filename="studioflow-images.zip", media_type="application/zip"
    )


@router.get("/api/renders/{rid}/download")
def download_image(
    rid: str,
    identifier: str = "auto",
    user: str = Depends(current_user),
    workspace: Workspace = Depends(get_workspace),
):
    manifest = library.preview_export(workspace, [rid], identifier)
    item = manifest["items"][0]
    return asset_response(workspace, item["asset_id"], item["filename"])


class VariantRequest(BaseModel):
    variant: str


@router.put("/api/renders/{rid}/variant")
def assign_variant(
    rid: str,
    body: VariantRequest,
    user: str = Depends(current_user),
    workspace: Workspace = Depends(get_workspace),
):
    if not library.VARIANT.fullmatch(body.variant):
        raise HTTPException(400, "Use MAIN or PT01–PT99")
    with workspace.db() as c:
        c.execute("BEGIN IMMEDIATE")
        maintenance.require_writable(c)
        row = c.execute("SELECT * FROM renders WHERE id=?", (rid,)).fetchone()
        if not row:
            raise HTTPException(404, "Image not found")
        if row["variant"]:
            raise HTTPException(409, "This image already has an immutable export slot")
        root, visited = row, set()
        while root["parent_id"]:
            if root["id"] in visited:
                raise HTTPException(
                    409, "Historical revision links need repair before assigning a slot"
                )
            visited.add(root["id"])
            root = c.execute(
                "SELECT * FROM renders WHERE id=?", (root["parent_id"],)
            ).fetchone()
        family = c.execute(
            """WITH RECURSIVE family(id) AS (
            SELECT id FROM renders WHERE id=? UNION SELECT r.id FROM renders r JOIN family f ON r.parent_id=f.id
            ) SELECT renders.id,variant FROM renders JOIN family ON family.id=renders.id""",
            (root["id"],),
        ).fetchall()
        if any(item["variant"] and item["variant"] != body.variant for item in family):
            raise HTTPException(
                409, "Another revision already has a different immutable slot"
            )
        for item in family:
            c.execute(
                "UPDATE renders SET variant=?,sku_snapshot=? WHERE id=?",
                (body.variant, root["sku"], item["id"]),
            )
    return {"ok": True}


@router.get("/api/operations")
def operations(
    user: str = Depends(current_user), workspace: Workspace = Depends(get_workspace)
):
    if not workspace.is_admin(user):
        raise HTTPException(403, "Owner access required")
    return maintenance.state(workspace)
