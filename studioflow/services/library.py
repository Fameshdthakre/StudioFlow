"""Indexed library queries and bounded, authenticated, immutable export manifests."""

import json
import re
import time
import uuid
import zipfile

from fastapi import HTTPException

from . import assets, maintenance
from .common import now
from ..persistence.repository import link_asset

VARIANT = re.compile(r"MAIN|PT(?:0[1-9]|[1-9][0-9])")


def filename(row, identifier="auto"):
    if identifier not in ("auto", "asin", "sku"):
        raise HTTPException(400, "Choose auto, ASIN or SKU naming")
    variant = row.get("variant") or ""
    if not VARIANT.fullmatch(variant):
        raise HTTPException(
            409, "Assign a MAIN or PT01–PT99 slot to this historical image first"
        )
    asin = (row.get("asin_snapshot") or "").strip().upper()
    valid_asin = bool(re.fullmatch(r"[A-Z0-9]{10}", asin))
    if identifier == "asin" and not valid_asin:
        raise HTTPException(
            400, "ASIN naming requires a valid saved ten-character ASIN"
        )
    key = (
        asin
        if (identifier == "asin" or identifier == "auto" and valid_asin)
        else (row.get("sku_snapshot") or row["sku"])
    )
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,60}", key):
        raise HTTPException(
            400,
            "Export identifiers must contain letters, digits, hyphens or underscores",
        )
    return f"{key}.{variant}.jpg"


def payload(row):
    row = dict(row)
    row["url"] = f"/api/media/{row['file']}" if row["file"] else None
    row["thumbnail_url"] = (
        f"/api/assets/{row['thumbnail_asset_id']}"
        if row.get("thumbnail_asset_id")
        else row["url"]
    )
    try:
        row["download_filename"] = filename(row)
    except HTTPException:
        row["download_filename"] = None
    row["recipe"] = json.loads(row.get("recipe_json") or "{}")
    for field in (
        "source",
        "original",
        "memory_snapshot",
        "references_json",
        "recipe_json",
    ):
        row.pop(field, None)
    return row


def list_images(
    workspace, *, q="", status="all", kind="all", cursor=None, limit=60, selection=False
):
    conditions, args = ["1=1"], []
    if status == "approved":
        conditions.append("approved=1 AND status='completed'")
    elif status != "all":
        if status not in ("queued", "running", "completed", "failed", "transferring"):
            raise HTTPException(400, "Unknown image status")
        conditions.append("status=?")
        args.append(status)
    if selection:
        conditions.append("status='completed'")
    if kind != "all":
        if kind not in ("main", "lifestyle", "infographic", "aplus"):
            raise HTTPException(400, "Unknown image type")
        conditions.append("kind=?")
        args.append(kind)
    if q.strip():
        conditions.append(
            "(UPPER(sku) LIKE ? OR UPPER(asin_snapshot) LIKE ? OR UPPER(prompt) LIKE ?)"
        )
        args.extend(["%" + q.strip().upper()[:100] + "%"] * 3)
    if cursor is not None:
        if cursor < 1:
            raise HTTPException(400, "Invalid library cursor")
        conditions.append("enqueue_seq<?")
        args.append(cursor)
    limit = 250 if selection else min(100, max(1, limit))
    with workspace.db() as c:
        rows = c.execute(
            "SELECT renders.*,assets.width AS native_width,assets.height AS native_height FROM renders LEFT JOIN assets ON assets.id=renders.master_asset_id WHERE "
            + " AND ".join(conditions)
            + " ORDER BY enqueue_seq DESC LIMIT ?",
            (*args, limit + 1),
        ).fetchall()
    if selection and len(rows) > 250:
        raise HTTPException(
            400, "More than 250 images match; narrow your filters before selecting all"
        )
    items = [payload(r) for r in rows[:limit]]
    return {
        "items": items,
        "next_cursor": items[-1]["enqueue_seq"] if len(rows) > limit else None,
    }


def preview_export(workspace, ids, identifier):
    if not 1 <= len(ids) <= 250 or len(set(ids)) != len(ids):
        raise HTTPException(400, "Select 1–250 distinct completed images")
    items, names, total = [], set(), 0
    with workspace.db() as c:
        rows = {
            r["id"]: dict(r)
            for r in c.execute(
                "SELECT * FROM renders WHERE id IN ("
                + ",".join("?" for _ in ids)
                + ")",
                tuple(ids),
            )
        }
    for rid in ids:
        row = rows.get(rid)
        if not row or row["status"] != "completed":
            raise HTTPException(409, "One selected image is missing or incomplete")
        name = filename(row, identifier)
        if name.casefold() in names:
            raise HTTPException(
                409,
                f"Duplicate filename {name}. Select one revision/image set or use SKU naming.",
            )
        names.add(name.casefold())
        aid = row.get("output_asset_id") or assets.legacy_id(
            workspace, "outputs/" + row["file"]
        )
        if not aid:
            raise HTTPException(
                409,
                "Import this historical image into asset storage before bulk export",
            )
        asset = assets.get(workspace, aid)
        total += asset["byte_size"]
        items.append(
            {
                "render_id": rid,
                "asset_id": aid,
                "filename": name,
                "bytes": asset["byte_size"],
            }
        )
    if total > 512 * 1024 * 1024:
        raise HTTPException(413, "Selected images exceed the 512 MiB ZIP limit")
    return {"items": items, "total_bytes": total}


def create_export(workspace, ids, identifier, user):
    if not workspace.settings.enable_exports:
        raise HTTPException(404, "Bulk exports are not enabled")
    manifest = preview_export(workspace, ids, identifier)
    eid = uuid.uuid4().hex
    with workspace.db() as c:
        c.execute("BEGIN IMMEDIATE")
        maintenance.require_writable(c)
        pending = c.execute(
            "SELECT COUNT(*) FROM exports WHERE status IN ('queued','running')"
        ).fetchone()[0]
        if pending >= 20:
            raise HTTPException(
                429, "Export queue is full; wait for an existing download"
            )
        c.execute(
            "INSERT INTO exports(id,user,selection_json,created_at,expires_at) VALUES(?,?,?,?,?)",
            (eid, user, json.dumps(manifest["items"]), now(), time.time() + 3600),
        )
        for item in manifest["items"]:
            link_asset(c, item["asset_id"], "export", eid)
    return {"id": eid, "status": "queued", **manifest}


def export_status(workspace, eid, user):
    with workspace.db() as c:
        row = c.execute(
            "SELECT * FROM exports WHERE id=? AND user=?", (eid, user)
        ).fetchone()
    if not row or row["expires_at"] <= time.time():
        raise HTTPException(404, "Export not found or expired")
    return {
        "id": eid,
        "status": row["status"],
        "error": row["error"],
        "download_url": f"/api/exports/{eid}/download"
        if row["status"] == "completed"
        else None,
    }


def process_exports(workspace, owner):
    with workspace.db() as c:
        c.execute("BEGIN IMMEDIATE")
        if maintenance.paused(c):
            return
        c.execute(
            "UPDATE exports SET status='queued' WHERE status='running' AND NOT EXISTS (SELECT 1 FROM workers WHERE workers.id=exports.worker_id AND heartbeat>=?)",
            (time.time() - 90,),
        )
        sql = "SELECT * FROM exports WHERE status='queued' AND expires_at>? ORDER BY created_at LIMIT 1"
        if getattr(c, "dialect", "sqlite") == "postgresql":
            sql += " FOR UPDATE SKIP LOCKED"
        row = c.execute(sql, (time.time(),)).fetchone()
        if not row:
            return
        c.execute(
            "UPDATE exports SET status='running',worker_id=? WHERE id=?",
            (owner, row["id"]),
        )
    target = workspace.exports / (row["id"] + ".zip")
    partial = workspace.exports / (row["id"] + "." + owner + ".partial")
    try:
        with zipfile.ZipFile(
            partial, "w", compression=zipfile.ZIP_STORED, allowZip64=True
        ) as archive:
            for item in json.loads(row["selection_json"]):
                # materialize validates the stored checksum before adding an entry.
                with assets.materialize(workspace, item["asset_id"]) as path:
                    archive.write(path, item["filename"])
        partial.replace(target)
        with workspace.db() as c:
            c.execute(
                "UPDATE exports SET status='completed' WHERE id=? AND worker_id=?",
                (row["id"], owner),
            )
    except Exception:
        partial.unlink(missing_ok=True)
        with workspace.db() as c:
            c.execute(
                "UPDATE exports SET status='failed',error=? WHERE id=? AND worker_id=?",
                (
                    "Could not read every selected image. No partial ZIP was published.",
                    row["id"],
                    owner,
                ),
            )
