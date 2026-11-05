"""aplus operations and persistence."""

import io
import re
import sqlite3
from sqlalchemy.exc import IntegrityError
import uuid
from datetime import datetime, timezone

from fastapi import HTTPException
from PIL import Image, ImageOps

from .common import audit
from . import assets

DEFAULT_MASTER = (
    "Create an Amazon Premium A+ banner background using the exact source product. "
    "Preserve its shape, colour, texture, labels and scale. Leave clear space for "
    "headlines and feature copy. Use relevant environments and consistent brand colours. "
    "Do not render text, numbers, logos, divider lines or invented product features. "
    "Approved typography and logo artwork are applied separately. No children."
)


def snapshot(connection, profile_id):
    row = connection.execute(
        "SELECT * FROM aplus_profiles WHERE id=?", (profile_id,)
    ).fetchone()
    if not row:
        raise HTTPException(400, "Choose a saved A+ profile before generating")
    return dict(row)


def prompt(profile):
    return (
        "A+ PROFILE — background composition and styling only:\n"
        f"Profile: {profile['name']}\n"
        f"Colours: {profile['primary_color']}, {profile['secondary_color']}, {profile['accent_color']}\n"
        f"Downstream typography: {profile['heading_font']} / {profile['body_font']}\n"
        f"Branding notes: {profile['branding_notes']}\n"
        f"A+ instructions: {profile['master_instructions']}\n"
        "The logo is stored as a reference for later composition. Do not draw a logo or render words. "
        "The real product and verified SKU facts remain authoritative."
    )


def owner(workspace, user):
    if not workspace.is_admin(user):
        raise HTTPException(403, "Only a workspace owner can change A+ profiles")


def payload(row):
    result = dict(row)
    result["logo_url"] = (
        f"/api/aplus/profiles/{result['id']}/logo" if result.pop("logo_file") else ""
    )
    return result


def profiles(workspace, user):
    with workspace.db() as c:
        rows = c.execute("SELECT * FROM aplus_profiles ORDER BY name").fetchall()
    return {
        "profiles": [payload(row) for row in rows],
        "can_edit": workspace.is_admin(user),
        "default_master": DEFAULT_MASTER,
    }


def create(workspace, body, user):
    owner(workspace, user)
    pid = uuid.uuid4().hex
    values = body.model_dump()
    with workspace.db() as c:
        try:
            c.execute(
                "INSERT INTO aplus_profiles(id,name,master_instructions,branding_notes,heading_font,body_font,primary_color,secondary_color,accent_color,updated_at,updated_by) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (pid, *values.values(), datetime.now(timezone.utc).isoformat(), user),
            )
        except (sqlite3.IntegrityError, IntegrityError):
            raise HTTPException(409, "An A+ profile with this name already exists")
        audit(c, user, "aplus_profile_created", "", pid)
    return {"id": pid, "ok": True}


def update(workspace, pid, body, user):
    owner(workspace, user)
    with workspace.db() as c:
        c.execute("BEGIN IMMEDIATE")
        if not c.execute("SELECT 1 FROM aplus_profiles WHERE id=?", (pid,)).fetchone():
            raise HTTPException(404, "A+ profile not found")
        try:
            c.execute(
                "UPDATE aplus_profiles SET name=?,master_instructions=?,branding_notes=?,heading_font=?,body_font=?,primary_color=?,secondary_color=?,accent_color=?,updated_at=?,updated_by=? WHERE id=?",
                (
                    *body.model_dump().values(),
                    datetime.now(timezone.utc).isoformat(),
                    user,
                    pid,
                ),
            )
        except (sqlite3.IntegrityError, IntegrityError):
            raise HTTPException(409, "An A+ profile with this name already exists")
        audit(c, user, "aplus_profile_updated", "", pid)
    return {"id": pid, "ok": True}


def upload_logo(workspace, pid, file, user):
    owner(workspace, user)
    with workspace.db() as c:
        if not c.execute("SELECT 1 FROM aplus_profiles WHERE id=?", (pid,)).fetchone():
            raise HTTPException(404, "A+ profile not found")
    raw = file.file.read(5 * 1024 * 1024 + 1)
    if len(raw) > 5 * 1024 * 1024:
        raise HTTPException(413, "Logo must be under 5 MB")
    filename = uuid.uuid4().hex + ".png"
    target = workspace.brand_logos / filename
    try:
        try:
            with Image.open(io.BytesIO(raw)) as image:
                if (
                    image.format not in ("PNG", "JPEG", "WEBP")
                    or min(image.size) < 16
                    or image.width * image.height > 16_000_000
                ):
                    raise ValueError("Invalid image")
                image = ImageOps.exif_transpose(image).convert("RGBA")
                image.thumbnail((1800, 1800), Image.Resampling.LANCZOS)
                image.save(target, "PNG")
        except Exception as exc:
            raise HTTPException(400, "Upload a valid PNG, JPG or WEBP logo") from exc
        assets.publish_logo(workspace, raw, target, user, "aplus_profile", pid)
        with workspace.db() as c:
            c.execute(
                "UPDATE aplus_profiles SET logo_file=?,updated_at=?,updated_by=? WHERE id=?",
                (filename, datetime.now(timezone.utc).isoformat(), user, pid),
            )
            audit(c, user, "aplus_logo_uploaded", "", pid)
    except BaseException:
        target.unlink(missing_ok=True)
        raise
    # Retain previous logos: an accepted job's profile snapshot may refer to one.
    return {"ok": True, "logo_url": f"/api/aplus/profiles/{pid}/logo"}


def logo(workspace, pid, user):
    with workspace.db() as c:
        row = c.execute(
            "SELECT logo_file FROM aplus_profiles WHERE id=?", (pid,)
        ).fetchone()
    name = row["logo_file"] if row else ""
    if not re.fullmatch(r"[0-9a-f]{32}\.png", name) or not (
        (workspace.brand_logos / name).is_file()
        or assets.legacy_id(workspace, "brand_logos/" + name)
    ):
        raise HTTPException(404, "A+ logo not found")
    return workspace.brand_logos / name
