"""HTTP routes for catalog."""

from fastapi import APIRouter, Depends, File, Response, UploadFile
from pydantic import BaseModel, Field

from ..dependencies import current_user, get_workspace
from ..services import catalog as service
from ..workspace import Workspace

router = APIRouter()


class MatchFilesBody(BaseModel):
    filenames: list[str] = Field(max_length=50)


@router.post("/api/catalog/import")
def import_catalog(
    file: UploadFile = File(...),
    user: str = Depends(current_user),
    workspace: Workspace = Depends(get_workspace),
):
    return service.import_catalog(workspace, file=file, user=user)


@router.get("/api/catalog")
def get_catalog(
    q: str = "",
    user: str = Depends(current_user),
    workspace: Workspace = Depends(get_workspace),
):
    return service.get_catalog(workspace, q=q, user=user)


@router.delete("/api/catalog/{id}")
def delete_catalog_product(
    id: str,
    user: str = Depends(current_user),
    workspace: Workspace = Depends(get_workspace),
):
    return service.delete_catalog_product(workspace, id=id, user=user)


@router.post("/api/catalog/match-files")
def match_files(
    body: MatchFilesBody,
    user: str = Depends(current_user),
    workspace: Workspace = Depends(get_workspace),
):
    return service.match_files(workspace, body=body, user=user)


@router.get("/api/catalog/template")
def download_catalog_template(workspace: Workspace = Depends(get_workspace)):
    return Response(
        content=(
            workspace.base_dir / "resources/studioflow_product_template.csv"
        ).read_bytes(),
        media_type="text/csv",
        headers={
            "Content-Disposition": 'attachment; filename="studioflow_product_template.csv"'
        },
    )
