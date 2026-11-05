"""Durable asset reservations and restartable transfers; never invokes a provider."""

import io
import os
import re
import shutil
import tempfile
import uuid
import logging
import time
from contextlib import contextmanager
from pathlib import Path

from fastapi import HTTPException
from PIL import Image, ImageOps

from ..storage import checksum
from .common import now

MIME_EXT = {"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp"}
UPLOAD_ROLES = {"product_reference", "style_reference", "cutout"}
logger = logging.getLogger(__name__)


def atomic_write(path, raw):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name("." + path.name + "." + uuid.uuid4().hex)
    try:
        with temporary.open("xb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)
        if os.name == "posix":
            descriptor = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
    finally:
        temporary.unlink(missing_ok=True)


def get(workspace, aid, *, ready=True):
    if not re.fullmatch(r"[0-9a-f]{32}", aid or ""):
        raise HTTPException(404, "Asset not found")
    with workspace.db() as c:
        row = c.execute("SELECT * FROM assets WHERE id=?", (aid,)).fetchone()
    if not row:
        raise HTTPException(404, "Asset not found")
    if ready and row["state"] != "ready":
        raise HTTPException(409, "Asset transfer has not completed")
    return dict(row)


def reserve(workspace, path, role, user, *, aid=None, parent_id=None):
    path = Path(path)
    digest = checksum(path)
    with Image.open(path) as image:
        mime = Image.MIME[image.format]
        width, height = image.size
    aid = aid or uuid.uuid4().hex
    suffix = MIME_EXT[mime]
    staging = workspace.staging / f"{aid}.{suffix}"
    with workspace.db() as c:
        c.execute("BEGIN IMMEDIATE")
        existing = c.execute("SELECT sha256 FROM assets WHERE id=?", (aid,)).fetchone()
        if existing:
            if existing[0] != digest:
                raise ValueError("Asset reservation collision")
            return aid
        if path != staging:
            atomic_write(staging, path.read_bytes())
        c.execute(
            """INSERT INTO assets(id,object_key,role,state,mime,byte_size,width,height,
            sha256,created_by,created_at,staging_file,parent_id) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                aid,
                f"assets/{aid[:2]}/{aid}.{suffix}",
                role,
                "pending",
                mime,
                staging.stat().st_size,
                width,
                height,
                digest,
                user,
                now(),
                staging.name,
                parent_id,
            ),
        )
    return aid


def transfer(workspace, aid):
    asset = get(workspace, aid, ready=False)
    if asset["state"] == "ready":
        return asset
    path = workspace.staging / asset["staging_file"]
    if (
        path.parent != workspace.staging
        or not path.is_file()
        or checksum(path) != asset["sha256"]
    ):
        raise RuntimeError("Reserved asset staging file is missing or corrupt")
    try:
        workspace.storage.put(asset["object_key"], path, asset["mime"], asset["sha256"])
    except Exception:
        with workspace.db() as c:
            c.execute(
                "UPDATE assets SET error=? WHERE id=?",
                ("Storage transfer failed; staged image retained", aid),
            )
        raise
    with workspace.db() as c:
        c.execute(
            "UPDATE assets SET state='ready',error='',transfer_worker_id='' WHERE id=?",
            (aid,),
        )
    # Staging cleanup is a separate operation: a backup may currently reference it.
    return get(workspace, aid)


def publish(workspace, path, role, user, **kwargs):
    aid = reserve(workspace, path, role, user, **kwargs)
    transfer(workspace, aid)
    return aid


def upload(workspace, raw, role, user):
    if role not in UPLOAD_ROLES:
        raise HTTPException(400, "Choose product reference, style reference, or cutout")
    if len(raw) > 18 * 1024 * 1024:
        raise HTTPException(413, "Images must be under 18 MB")
    try:
        with Image.open(io.BytesIO(raw)) as source:
            if (
                source.format not in ("PNG", "JPEG", "WEBP")
                or min(source.size) < 120
                or source.width * source.height > 45_000_000
            ):
                raise ValueError("Unsupported image")
            original_ext = MIME_EXT[Image.MIME[source.format]]
            normalized = ImageOps.exif_transpose(source).convert("RGBA")
            if role == "cutout" and normalized.getchannel("A").getextrema()[0] == 255:
                raise ValueError("Cutouts must have a transparent background")
            data = io.BytesIO()
            normalized.save(data, "PNG")
    except Exception as exc:
        raise HTTPException(
            400, "Upload a valid image; cutouts require transparency"
        ) from exc
    original = workspace.staging / f"{uuid.uuid4().hex}.{original_ext}"
    atomic_write(original, raw)
    original_id = publish(workspace, original, "original_upload", user)
    target = workspace.staging / f"{uuid.uuid4().hex}.png"
    atomic_write(target, data.getvalue())
    aid = publish(workspace, target, role, user, parent_id=original_id)
    original.unlink(missing_ok=True)
    target.unlink(missing_ok=True)
    return {
        "asset_id": aid,
        "original_asset_id": original_id,
        "role": role,
        "width": normalized.width,
        "height": normalized.height,
        "url": f"/api/assets/{aid}",
    }


@contextmanager
def materialize(workspace, aid):
    asset = get(workspace, aid)
    with tempfile.TemporaryDirectory(
        dir=workspace.staging, prefix="read-"
    ) as directory:
        path = Path(directory) / (aid + "." + MIME_EXT[asset["mime"]])
        with workspace.storage.open(asset["object_key"]) as src, path.open("wb") as dst:
            shutil.copyfileobj(src, dst, 1024 * 1024)
        if checksum(path) != asset["sha256"]:
            raise RuntimeError("Asset checksum mismatch")
        yield path


def map_legacy(workspace, path, aid):
    with workspace.db() as c:
        c.execute(
            "INSERT INTO legacy_assets(path,asset_id) VALUES(?,?) ON CONFLICT(path) DO NOTHING",
            (path, aid),
        )


def legacy_id(workspace, path):
    with workspace.db() as c:
        row = c.execute(
            "SELECT asset_id FROM legacy_assets WHERE path=?", (path,)
        ).fetchone()
    return row[0] if row else None


def publish_logo(workspace, raw, normalized_path, user, owner_type, owner_id):
    from ..persistence.repository import link_asset

    with Image.open(io.BytesIO(raw)) as image:
        suffix = MIME_EXT[Image.MIME[image.format]]
    original = workspace.staging / (uuid.uuid4().hex + "." + suffix)
    atomic_write(original, raw)
    original_id = publish(workspace, original, "original_upload", user)
    aid = publish(workspace, normalized_path, "logo", user, parent_id=original_id)
    map_legacy(workspace, "brand_logos/" + normalized_path.name, aid)
    with workspace.db() as c:
        link_asset(c, aid, owner_type, owner_id)
    original.unlink(missing_ok=True)
    return aid


def process_auxiliary(workspace, owner):
    """Resume durable upload transfers independently of generation and ZIP work."""
    from . import maintenance, library

    with workspace.db() as c:
        c.execute("BEGIN IMMEDIATE")
        if maintenance.paused(c):
            return
        c.execute(
            "UPDATE assets SET state='pending',transfer_worker_id='' WHERE state='transferring' AND NOT EXISTS (SELECT 1 FROM workers w WHERE w.id=assets.transfer_worker_id AND heartbeat>=?)",
            (time.time() - 90,),
        )
        row = c.execute(
            "SELECT id FROM assets WHERE state='pending' ORDER BY created_at LIMIT 1"
        ).fetchone()
        if row:
            c.execute(
                "UPDATE assets SET state='transferring',transfer_worker_id=? WHERE id=?",
                (owner, row["id"]),
            )
    if row:
        try:
            transfer(workspace, row["id"])
        except Exception:
            logger.warning("Asset transfer remains pending: %s", row["id"])
            with workspace.db() as c:
                c.execute(
                    "UPDATE assets SET state='pending',transfer_worker_id='' WHERE id=? AND state<>'ready'",
                    (row["id"],),
                )
    library.process_exports(workspace, owner)
