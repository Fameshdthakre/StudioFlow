"""HTTP routes for usage."""

from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from ..dependencies import current_user, get_workspace
from ..services import usage as service
from ..workspace import Workspace


class Checkpoint(BaseModel):
    balance_checkpoint_usd: Decimal = Field(
        ge=0, le=10_000_000, max_digits=14, decimal_places=6, allow_inf_nan=False
    )
    alert_percent: int = Field(default=20, ge=1, le=90)


def require_feature(workspace: Workspace = Depends(get_workspace)):
    if not workspace.settings.enable_usage:
        raise HTTPException(404, "Usage dashboard is disabled")


router = APIRouter(prefix="/api/usage", dependencies=[Depends(require_feature)])


@router.get("")
def get_usage(
    user=Depends(current_user), workspace: Workspace = Depends(get_workspace)
):
    return service.get_usage(workspace, user=user)


@router.put("/checkpoint")
def save_checkpoint(
    body: Checkpoint,
    user=Depends(current_user),
    workspace: Workspace = Depends(get_workspace),
):
    return service.save_checkpoint(workspace, body=body, user=user)
