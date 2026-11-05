"""Product facts, approved rules, brand profiles and immutable prompt snapshots."""

import io
import json
import re
import sqlite3
from sqlalchemy.exc import IntegrityError
import uuid

from fastapi import HTTPException
from PIL import Image, ImageOps

from .catalog import find_catalog_product, resolve_product_sku
from .common import audit, now
from ..persistence.repository import ensure_product
from . import assets

HEX_RE = re.compile(r"^#[0-9a-fA-F]{6}$")

BASE_RULES = """Use the uploaded photograph as the exact source of truth for the product. Retain the real product's silhouette, colour, texture, materials, count, proportions, labels, and decoration. Do not invent attachments or modify the product itself. Photorealistic commercial photography, realistic perspective and scale, sharp product details, clean lighting. No children. Only adult women if a human model is requested."""


def memory_snapshot(workspace, sku, extra_rule="", connection=None):
    from contextlib import nullcontext

    with nullcontext(connection) if connection is not None else workspace.db() as c:
        key = resolve_product_sku(c, sku)
        r = c.execute(
            "SELECT value FROM memory_settings WHERE key='master_rules'"
        ).fetchone()
        product = c.execute(
            "SELECT facts,brand_id FROM product_memory WHERE sku=?", (key,)
        ).fetchone()
        cat = find_catalog_product(c, key)
        rules = [
            x["instruction"]
            for x in c.execute(
                "SELECT instruction FROM product_rules WHERE sku=? ORDER BY created_at,id",
                (key,),
            )
        ]
        brand_id = (
            product["brand_id"]
            if product and product["brand_id"]
            else (cat["brand_id"] if cat else "")
        )
        br = (
            c.execute("SELECT * FROM brands WHERE id=?", (brand_id,)).fetchone()
            if brand_id
            else None
        )

    if extra_rule.strip():
        rules.append(extra_rule.strip())
    branding = (
        {
            k: br[k]
            for k in (
                "id",
                "name",
                "primary_color",
                "secondary_color",
                "accent_color",
                "heading_font",
                "body_font",
                "visual_style",
                "brand_rules",
                "logo_file",
            )
        }
        if br
        else None
    )

    combined_facts = []
    if cat and cat.get("facts_summary"):
        combined_facts.append(cat["facts_summary"])
    if product and product["facts"] and product["facts"].strip():
        if not cat or product["facts"].strip() != cat.get("facts_summary", "").strip():
            combined_facts.append(product["facts"].strip())

    facts_str = "\n\n".join(combined_facts)

    return json.dumps(
        {
            "company": r["value"] if r else BASE_RULES,
            "facts": facts_str,
            "rules": rules,
            "sku": key,
            "branding": branding,
            "catalog": cat,
        },
        ensure_ascii=False,
    )


def brand_payload(row):
    r = dict(row)
    r["logo_url"] = f"/api/memory/brands/{r['id']}/logo" if r.get("logo_file") else ""
    r.pop("logo_file", None)
    return r


def check_brand(body):
    name = body.name.strip()
    if len(name) < 2:
        raise HTTPException(400, "Brand name must have at least 2 characters")
    for key in ("primary_color", "secondary_color", "accent_color"):
        if not HEX_RE.fullmatch(getattr(body, key)):
            raise HTTPException(400, key + " must be a 6-digit hex colour (#RRGGBB)")
    return name


def get_company_memory(workspace, user):
    with workspace.db() as c:
        r = c.execute(
            "SELECT * FROM memory_settings WHERE key='master_rules'"
        ).fetchone()
    return {
        "instructions": r["value"] if r else BASE_RULES,
        "updated_by": r["updated_by"] if r else "StudioFlow default",
        "updated_at": r["updated_at"] if r else "",
        "can_edit": workspace.is_admin(user),
    }


def put_company_memory(workspace, body, user):
    if not workspace.is_admin(user):
        raise HTTPException(
            403, "Only the workspace owner can edit company instructions"
        )
    with workspace.db() as c:
        c.execute(
            "INSERT INTO memory_settings(key,value,updated_by,updated_at) VALUES('master_rules',?,?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated_by=excluded.updated_by,updated_at=excluded.updated_at",
            (body.instructions.strip(), user, now()),
        )
        audit(c, user, "company_rules_updated", "", body.instructions)
    return {"ok": True}


def list_brands(workspace, user):
    with workspace.db() as c:
        rows = c.execute("SELECT * FROM brands ORDER BY LOWER(name)").fetchall()
    return [brand_payload(r) for r in rows]


def create_brand(workspace, body, user):
    if not workspace.is_admin(user):
        raise HTTPException(403, "Only an owner can create brand profiles")
    name = check_brand(body)
    bid = uuid.uuid4().hex
    with workspace.db() as c:
        try:
            c.execute(
                """INSERT INTO brands(id,name,primary_color,secondary_color,accent_color,heading_font,body_font,visual_style,brand_rules,updated_by,updated_at)
            VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    bid,
                    name,
                    body.primary_color.upper(),
                    body.secondary_color.upper(),
                    body.accent_color.upper(),
                    body.heading_font.strip(),
                    body.body_font.strip(),
                    body.visual_style.strip(),
                    body.brand_rules.strip(),
                    user,
                    now(),
                ),
            )
        except (sqlite3.IntegrityError, IntegrityError):
            raise HTTPException(409, "A brand with this name already exists")
        audit(c, user, "brand_created", "", name)
    return {"ok": True, "id": bid}


def update_brand(workspace, bid, body, user):
    if not workspace.is_admin(user):
        raise HTTPException(403, "Only an owner can edit brand profiles")
    name = check_brand(body)
    with workspace.db() as c:
        if not c.execute("SELECT 1 FROM brands WHERE id=?", (bid,)).fetchone():
            raise HTTPException(404, "Brand not found")
        try:
            c.execute(
                """UPDATE brands SET name=?,primary_color=?,secondary_color=?,accent_color=?,heading_font=?,body_font=?,visual_style=?,brand_rules=?,updated_by=?,updated_at=? WHERE id=?""",
                (
                    name,
                    body.primary_color.upper(),
                    body.secondary_color.upper(),
                    body.accent_color.upper(),
                    body.heading_font.strip(),
                    body.body_font.strip(),
                    body.visual_style.strip(),
                    body.brand_rules.strip(),
                    user,
                    now(),
                    bid,
                ),
            )
        except (sqlite3.IntegrityError, IntegrityError):
            raise HTTPException(409, "A brand with this name already exists")
        audit(c, user, "brand_updated", "", name)
    return {"ok": True, "id": bid}


def upload_brand_logo(workspace, bid, file, user):
    if not workspace.is_admin(user):
        raise HTTPException(403, "Only an owner can upload brand logos")
    with workspace.db() as c:
        previous = c.execute(
            "SELECT logo_file FROM brands WHERE id=?", (bid,)
        ).fetchone()
    if not previous:
        raise HTTPException(404, "Brand not found")
    raw = file.file.read(5 * 1024 * 1024 + 1)
    if len(raw) > 5 * 1024 * 1024:
        raise HTTPException(400, "Logo must be under 5MB")
    try:
        with Image.open(io.BytesIO(raw)) as src:
            if (
                src.format not in ("PNG", "JPEG", "WEBP")
                or src.width < 16
                or src.height < 16
                or src.width * src.height > 16_000_000
            ):
                raise ValueError("Invalid logo size or format")
            logo = ImageOps.exif_transpose(src).convert("RGBA")
            logo.thumbnail((1800, 1800), Image.Resampling.LANCZOS)
            filename = f"{uuid.uuid4().hex}.png"
            logo.save(workspace.brand_logos / filename, "PNG")
    except Exception:
        raise HTTPException(400, "Upload a valid PNG, JPG or WEBP logo")
    assets.publish_logo(
        workspace, raw, workspace.brand_logos / filename, user, "brand", bid
    )
    with workspace.db() as c:
        c.execute(
            "UPDATE brands SET logo_file=?,updated_by=?,updated_at=? WHERE id=?",
            (filename, user, now(), bid),
        )
        audit(c, user, "brand_logo_uploaded", "", bid)
    # Accepted snapshots may still reference previous logos; retain them.
    return {"ok": True, "logo_url": f"/api/memory/brands/{bid}/logo"}


def get_brand_logo(workspace, bid, user):
    with workspace.db() as c:
        r = c.execute("SELECT logo_file FROM brands WHERE id=?", (bid,)).fetchone()
    if not r or not r["logo_file"]:
        raise HTTPException(404, "Brand logo not found")
    filename = r["logo_file"]
    if not re.fullmatch(r"[0-9a-f]{32}\.png", filename) or not (
        (workspace.brand_logos / filename).is_file()
        or assets.legacy_id(workspace, "brand_logos/" + filename)
    ):
        raise HTTPException(404, "Brand logo not found")
    return workspace.brand_logos / filename


def assign_brand(workspace, sku, body, user):
    if not workspace.is_admin(user):
        raise HTTPException(403, "Only an owner can assign product branding")
    bid = (body.brand_id or "").strip()
    with workspace.db() as c:
        key = resolve_product_sku(c, sku)
        if bid and not c.execute("SELECT 1 FROM brands WHERE id=?", (bid,)).fetchone():
            raise HTTPException(404, "Brand not found")
        c.execute(
            "INSERT INTO product_memory(sku,facts,updated_by,updated_at) VALUES(?,?,?,?) ON CONFLICT(sku) DO NOTHING",
            (key, "", user, now()),
        )
        c.execute(
            "UPDATE product_memory SET brand_id=?,updated_by=?,updated_at=? WHERE sku=?",
            (bid or None, user, now(), key),
        )
        c.execute(
            "UPDATE product_memory SET product_id=? WHERE sku=?",
            (ensure_product(c, key), key),
        )
        audit(c, user, "sku_brand_assigned", key, bid or "(none)")
    return {"ok": True, "sku": key, "brand_id": bid}


def list_product_memory(workspace, q, user):
    with workspace.db() as c:
        rows = [
            dict(r)
            for r in c.execute(
                """SELECT p.sku,p.facts,p.updated_at,p.updated_by,
        (SELECT COUNT(*) FROM product_rules r WHERE r.sku=p.sku) rule_count,
        COALESCE((SELECT name FROM brands b WHERE b.id=p.brand_id),'') brand_name
        FROM product_memory p WHERE p.sku LIKE ? ORDER BY p.updated_at DESC LIMIT 100""",
                ("%" + q.strip().upper()[:60] + "%",),
            )
        ]
    return rows


def get_product_memory(workspace, sku, user):
    with workspace.db() as c:
        key = resolve_product_sku(c, sku)
        r = c.execute("SELECT * FROM product_memory WHERE sku=?", (key,)).fetchone()
        cat = find_catalog_product(c, key)
        rules = [
            dict(x)
            for x in c.execute(
                "SELECT * FROM product_rules WHERE sku=? ORDER BY created_at,id", (key,)
            )
        ]
    for item in rules:
        item["can_delete"] = workspace.is_admin(user) or item["created_by"] == user

    brand_id = (
        r["brand_id"] if r and r["brand_id"] else (cat["brand_id"] if cat else "")
    )
    facts = r["facts"] if r else ""

    return {
        "sku": key,
        "facts": facts,
        "catalog_facts": cat["facts_summary"] if cat else "",
        "brand_id": brand_id,
        "updated_by": r["updated_by"] if r else "",
        "updated_at": r["updated_at"] if r else "",
        "can_manage_branding": workspace.is_admin(user),
        "rules": rules,
        "catalog": cat,
    }


def put_product_memory(workspace, sku, body, user):
    with workspace.db() as c:
        c.execute("BEGIN IMMEDIATE")
        key = resolve_product_sku(c, sku)
        c.execute(
            """INSERT INTO product_memory(sku,facts,updated_by,updated_at) VALUES(?,?,?,?)
        ON CONFLICT(sku) DO UPDATE SET facts=excluded.facts,updated_by=excluded.updated_by,updated_at=excluded.updated_at""",
            (key, body.facts.strip(), user, now()),
        )
        c.execute(
            "UPDATE product_memory SET product_id=? WHERE sku=?",
            (ensure_product(c, key), key),
        )
        audit(c, user, "sku_facts_updated", key, body.facts)
    return {"ok": True, "sku": key}


def add_product_rule(workspace, sku, body, user):
    rid = uuid.uuid4().hex
    with workspace.db() as c:
        c.execute("BEGIN IMMEDIATE")
        key = resolve_product_sku(c, sku)
        c.execute(
            "INSERT INTO product_memory(sku,facts,updated_by,updated_at) VALUES(?,?,?,?) ON CONFLICT(sku) DO NOTHING",
            (key, "", user, now()),
        )
        c.execute(
            "INSERT INTO product_rules(id,sku,instruction,created_by,created_at,source_render_id) VALUES (?,?,?,?,?,?)",
            (rid, key, body.instruction.strip(), user, now(), None),
        )
        pid = ensure_product(c, key)
        c.execute("UPDATE product_memory SET product_id=? WHERE sku=?", (pid, key))
        c.execute("UPDATE product_rules SET product_id=? WHERE id=?", (pid, rid))
        audit(c, user, "sku_rule_added", key, body.instruction)
    return {"ok": True, "id": rid}


def delete_product_rule(workspace, sku, rid, user):
    with workspace.db() as c:
        key = resolve_product_sku(c, sku)
        rule = c.execute(
            "SELECT * FROM product_rules WHERE id=? AND sku=?", (rid, key)
        ).fetchone()
        if not rule:
            raise HTTPException(404, "Rule not found")
        if not (workspace.is_admin(user) or rule["created_by"] == user):
            raise HTTPException(403, "Only owner or rule author can delete")
        c.execute("DELETE FROM product_rules WHERE id=?", (rid,))
        audit(c, user, "sku_rule_deleted", key, rule["instruction"])
    return {"ok": True}
