"""Workspace availability, capabilities and dashboard counts."""

from ..provider import MODEL_CONFIG
from ..settings import MAX_IMAGES
from .auth import get_session_user


def health(workspace):
    with workspace.db() as connection:
        connection.execute("SELECT 1").fetchone()
    return {"status": "ok"}


def config(workspace, token=None):
    settings = workspace.settings
    person = "Demo user" if settings.anonymous_demo else None
    if token:
        person = get_session_user(workspace, token) or person
    with workspace.db() as connection:
        catalog_count = connection.execute(
            "SELECT COUNT(*) n FROM catalog_products"
        ).fetchone()["n"]
    return {
        "mode": settings.mode,
        "user": person,
        "login_required": bool(settings.team),
        "max_workers": settings.max_workers,
        "max_products": 50,
        "max_scenes": 12,
        "max_images": MAX_IMAGES,
        "models": MODEL_CONFIG,
        "features": {"aplus": settings.enable_aplus, "usage": settings.enable_usage},
        "catalog_count": catalog_count,
    }


def stats(workspace):
    with workspace.db() as c:
        count = c.execute(
            "SELECT COUNT(*) n FROM renders WHERE status='completed'"
        ).fetchone()["n"]
        approved = c.execute(
            "SELECT COUNT(*) n FROM renders WHERE approved=1"
        ).fetchone()["n"]
        pending = c.execute(
            "SELECT COUNT(*) n FROM renders WHERE status IN ('queued','running')"
        ).fetchone()["n"]
        total = c.execute("SELECT COUNT(*) n FROM jobs").fetchone()["n"]
        catalog = c.execute("SELECT COUNT(*) n FROM catalog_products").fetchone()["n"]
    return {
        "generated": count,
        "approved": approved,
        "pending": pending,
        "jobs": total,
        "catalog": catalog,
    }
