"""HTTP routes for memory."""

from typing import Optional

from fastapi import (
    APIRouter,
    Depends,
    File,
    UploadFile,
)
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from ..dependencies import current_user, get_workspace
from ..services import memory as service
from ..workspace import Workspace

router = APIRouter()


class CompanyMemoryUpdate(BaseModel):
    instructions: str = Field(min_length=15, max_length=7000)


class BrandData(BaseModel):
    name: str = Field(min_length=2, max_length=100)
    primary_color: str = "#233A54"
    secondary_color: str = "#F2EEE8"
    accent_color: str = "#B98E5C"
    heading_font: str = Field(default="Cinzel", max_length=90)
    body_font: str = Field(default="Roboto", max_length=90)
    visual_style: str = Field(default="", max_length=1600)
    brand_rules: str = Field(default="", max_length=4000)


class AssignBrand(BaseModel):
    brand_id: Optional[str] = None


class ProductMemoryUpdate(BaseModel):
    facts: str = Field(max_length=6000)


class ProductRuleUpdate(BaseModel):
    instruction: str = Field(min_length=5, max_length=2000)


@router.get("/api/memory/company")
def get_company_memory(
    user: str = Depends(current_user), workspace: Workspace = Depends(get_workspace)
):
    return service.get_company_memory(workspace, user=user)


@router.put("/api/memory/company")
def put_company_memory(
    body: CompanyMemoryUpdate,
    user: str = Depends(current_user),
    workspace: Workspace = Depends(get_workspace),
):
    return service.put_company_memory(workspace, body=body, user=user)


@router.get("/api/memory/brands")
def list_brands(
    user: str = Depends(current_user), workspace: Workspace = Depends(get_workspace)
):
    return service.list_brands(workspace, user=user)


@router.post("/api/memory/brands")
def create_brand(
    body: BrandData,
    user: str = Depends(current_user),
    workspace: Workspace = Depends(get_workspace),
):
    return service.create_brand(workspace, body=body, user=user)


@router.put("/api/memory/brands/{bid}")
def update_brand(
    bid: str,
    body: BrandData,
    user: str = Depends(current_user),
    workspace: Workspace = Depends(get_workspace),
):
    return service.update_brand(workspace, bid=bid, body=body, user=user)


@router.post("/api/memory/brands/{bid}/logo")
def upload_brand_logo(
    bid: str,
    file: UploadFile = File(...),
    user: str = Depends(current_user),
    workspace: Workspace = Depends(get_workspace),
):
    return service.upload_brand_logo(workspace, bid=bid, file=file, user=user)


@router.get("/api/memory/brands/{bid}/logo")
def get_brand_logo(
    bid: str,
    user: str = Depends(current_user),
    workspace: Workspace = Depends(get_workspace),
):
    return FileResponse(
        service.get_brand_logo(workspace, bid=bid, user=user), media_type="image/png"
    )


@router.put("/api/memory/products/{sku}/brand")
def assign_brand(
    sku: str,
    body: AssignBrand,
    user: str = Depends(current_user),
    workspace: Workspace = Depends(get_workspace),
):
    return service.assign_brand(workspace, sku=sku, body=body, user=user)


@router.get("/api/memory/products")
def list_product_memory(
    q: str = "",
    user: str = Depends(current_user),
    workspace: Workspace = Depends(get_workspace),
):
    return service.list_product_memory(workspace, q=q, user=user)


@router.get("/api/memory/products/{sku}")
def get_product_memory(
    sku: str,
    user: str = Depends(current_user),
    workspace: Workspace = Depends(get_workspace),
):
    return service.get_product_memory(workspace, sku=sku, user=user)


@router.put("/api/memory/products/{sku}")
def put_product_memory(
    sku: str,
    body: ProductMemoryUpdate,
    user: str = Depends(current_user),
    workspace: Workspace = Depends(get_workspace),
):
    return service.put_product_memory(workspace, sku=sku, body=body, user=user)


@router.post("/api/memory/products/{sku}/rules")
def add_product_rule(
    sku: str,
    body: ProductRuleUpdate,
    user: str = Depends(current_user),
    workspace: Workspace = Depends(get_workspace),
):
    return service.add_product_rule(workspace, sku=sku, body=body, user=user)


@router.delete("/api/memory/products/{sku}/rules/{rid}")
def delete_product_rule(
    sku: str,
    rid: str,
    user: str = Depends(current_user),
    workspace: Workspace = Depends(get_workspace),
):
    return service.delete_product_rule(workspace, sku=sku, rid=rid, user=user)
