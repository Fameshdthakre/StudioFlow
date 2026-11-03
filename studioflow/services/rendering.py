"""Prompt composition, render execution and transactional image workflows."""

import io
import json
import logging
import re
import uuid

from fastapi import HTTPException
from PIL import Image, ImageDraw, ImageOps

from ..provider import (
    PRESETS,
    render_live,
    validate_settings,
)
from ..settings import MAX_BATCH_BYTES, MAX_IMAGE_BYTES, MAX_IMAGES, MAX_PENDING_IMAGES
from ..submissions import fingerprint, remember, replay, validate_key
from . import aplus, usage
from .catalog import canonical_sku, resolve_product_sku
from .common import audit, now, owned
from .memory import BASE_RULES, memory_snapshot

logger = logging.getLogger(__name__)

TASK_RULES = {
    "aplus": "Create an Amazon Premium A+ banner background. Preserve the exact product and leave space for later copy. Do not render text, numbers, logos, divider lines, children or invented product features.",
    "lifestyle": "Create a photorealistic lifestyle product image. Product should be clearly visible in an appropriate setting. No advertising text or extra logos.",
    "main": "Create a compliant-style Amazon MAIN product shot: pure white RGB(255,255,255) background, product only, centered, fills most of frame, no props, no labels added, no text. Preserve original packaging and genuine product marks. No people.",
    "infographic": "Create a clean product-focused graphic with generous negative space for overlay text. Do NOT generate words, numbers or labels; these will be added as crisp editable text after generation. Product remains accurate and clearly visible.",
}


def make_prompt(kind, scene, memory_json=""):
    memory = json.loads(memory_json or "{}")
    company = memory.get("company") or BASE_RULES
    facts = memory.get("facts") or ""
    rules = memory.get("rules") or []
    sku = memory.get("sku") or ""
    branding = memory.get("branding") or None
    catalog = memory.get("catalog") or None

    if kind == "aplus" and memory.get("aplus"):
        brand_info = aplus.prompt(memory["aplus"])
    elif kind == "main":
        brand_info = "Brand profile is not applied to MAIN image visual styling. Do not add new text, graphic logo, advertising, borders, or branding overlays. Genuine marks already on the product must remain unchanged."
    elif branding:
        brand_info = (
            "BRAND IDENTITY — apply to the background/composition only, not the physical product:\n"
            f"Brand: {branding['name']}\nPrimary colour: {branding['primary_color']}; "
            f"Secondary colour: {branding['secondary_color']}; Accent colour: {branding['accent_color']}\n"
            f"Heading font (for downstream text layout): {branding['heading_font']}; "
            f"Body font (for downstream text layout): {branding['body_font']}\n"
            f"Visual style: {branding['visual_style'] or 'not specified'}\n"
            f"Brand-specific guardrails: {branding['brand_rules'] or 'none'}\n"
            "Logo reference is saved in StudioFlow separately; do not invent, mimic, draw, or add a logo. "
            "Do not render marketing text into the generated image; approved typography and logo should be applied in post-production."
        )
    else:
        brand_info = "No brand profile assigned."

    catalog_context = ""
    if catalog:
        catalog_lines = []
        if catalog.get("title"):
            catalog_lines.append(f"Product Title: {catalog['title']}")
        if catalog.get("colour"):
            catalog_lines.append(f"Colour: {catalog['colour']}")
        if catalog.get("size"):
            catalog_lines.append(f"Size: {catalog['size']}")
        if catalog.get("variation"):
            catalog_lines.append(f"Variation: {catalog['variation']}")
        if catalog.get("item_highlight"):
            catalog_lines.append(f"Key Highlight: {catalog['item_highlight']}")
        bullets = [
            catalog.get(f"bullet_{i}")
            for i in range(1, 6)
            if catalog.get(f"bullet_{i}")
        ]
        if bullets:
            catalog_lines.append(
                "Features to visually reflect or stage for:\n"
                + "\n".join(f"- {b}" for b in bullets)
            )
        if catalog_lines:
            catalog_context = (
                "STRUCTURED PRODUCT SPECIFICATIONS:\n"
                + "\n".join(catalog_lines)
                + "\n\n"
            )

    return (
        "COMPANY INSTRUCTIONS:\n"
        + company
        + "\n\n"
        + "PRODUCT IDENTIFIER: "
        + sku
        + "\n\n"
        + catalog_context
        + "VERIFIED PRODUCT FACTS (do not invent missing facts):\n"
        + (facts or "No extra facts saved")
        + "\n\n"
        + "PRODUCT-SPECIFIC CORRECTIONS, MUST PRESERVE:\n"
        + (
            "\n".join("- " + r for r in rules)
            if rules
            else "No extra corrections saved"
        )
        + "\n\n"
        + "BRANDING:\n"
        + brand_info
        + "\n\n"
        + "IMAGE TYPE:\n"
        + TASK_RULES[kind]
        + "\n\nSCENE / CURRENT INSTRUCTIONS:\n"
        + scene
    )


def save_image_to_png(raw, path):
    if len(raw) > 18 * 1024 * 1024:
        raise HTTPException(400, "Images must be under 18 MB each")
    try:
        with Image.open(io.BytesIO(raw)) as img:
            if img.format not in ("PNG", "JPEG", "WEBP"):
                raise HTTPException(400, "Upload a JPG, PNG or WEBP image")
            if img.width < 120 or img.height < 120:
                raise HTTPException(400, "Image is too small (minimum 120px per side)")
            if img.width * img.height > 45_000_000:
                raise HTTPException(400, "Image dimensions are too large")
            ImageOps.exif_transpose(img).convert("RGB").save(path, "PNG")
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(400, "Please upload a valid JPG, PNG or WEBP image")


def render_demo(source, target, kind, scene, canvas):
    _, out_size = PRESETS[canvas]
    w, h = out_size
    background = {
        "lifestyle": "#ebe7df",
        "main": "#ffffff",
        "infographic": "#e8eff5",
        "aplus": "#e9e6de",
    }[kind]
    image = Image.new("RGB", (w, h), background)
    with Image.open(source) as original:
        original = original.convert("RGBA")
        tw = int(w * (0.60 if kind == "lifestyle" else 0.78))
        th = int(h * 0.70)
        original.thumbnail((tw, th), Image.Resampling.LANCZOS)
        x = (w - original.width) // 2
        y = (h - original.height) // 2
        image.paste(original, (x, y), original)
    d = ImageDraw.Draw(image)
    d.rounded_rectangle(
        (20, 20, w - 20, max(86, int(h * 0.10))), radius=15, fill="#142b3e"
    )
    d.text((42, 36), "DEMO PREVIEW — NO AI USED", fill="white")
    d.text(
        (42, h - 52),
        "Connect OPENAI_API_KEY + APP_MODE=live for real image editing",
        fill="#39536c",
    )
    image.save(target, "JPEG", quality=90)


def finish_job(c, job_id):
    c.execute(
        "UPDATE jobs SET status='finished' WHERE id=? AND NOT EXISTS "
        "(SELECT 1 FROM renders WHERE job_id=? AND status IN ('queued','running'))",
        (job_id, job_id),
    )


def generate_one(workspace, r, owner):
    output = workspace.outputs / f"{r['id']}.jpg"
    attempt = None

    def started():
        nonlocal attempt
        attempt = usage.begin_attempt(workspace.db, r)

    def received(response):
        usage.record_response(workspace.db, attempt, response)

    try:
        if workspace.settings.mode == "demo":
            render_demo(
                workspace.uploads / r["source"],
                output,
                r["kind"],
                r["prompt"],
                r["canvas"],
            )
        else:
            inputs = [workspace.uploads / r["source"]]
            if r["original"]:
                prior = workspace.outputs / r["original"]
                if not prior.is_file():
                    raise RuntimeError("Previous image could not be found for revision")
                inputs = [prior, workspace.uploads / r["source"]]
            render_live(
                inputs,
                output,
                make_prompt(r["kind"], r["prompt"], r["memory_snapshot"]),
                r["model"],
                r["quality"],
                r["canvas"],
                workspace.settings.api_key,
                aplus=r["kind"] == "aplus",
                on_request=started,
                on_response=received,
            )
        usage.finish_attempt(workspace.db, attempt, True)
        with workspace.db() as c:
            c.execute(
                "UPDATE renders SET status='completed',file=?,error='' WHERE id=? AND worker_id=? AND status='running'",
                (output.name, r["id"], owner),
            )
            finish_job(c, r["job_id"])
    except Exception as exc:
        usage.finish_attempt(workspace.db, attempt, False)
        logger.exception("Render %s failed", r["id"])
        # Store actionable categories, never provider responses containing secrets.
        error = str(exc).lower()
        message = "Image generation failed. Check server logs before retrying."
        if "invalid_api_key" in error or "incorrect api key" in error:
            message = (
                "Image provider authentication failed. Check the configured API key."
            )
        elif "quota" in error:
            message = "Image provider quota exceeded. Check workspace billing."
        elif "rate_limit" in error:
            message = "Image provider rate limit reached. Try again later."
        elif "safety" in error or "content_policy" in error:
            message = (
                "The image provider rejected this prompt. Refine the scene description."
            )
        with workspace.db() as c:
            c.execute(
                "UPDATE renders SET status='failed',error=? WHERE id=? AND worker_id=? AND status='running'",
                (message, r["id"], owner),
            )
            finish_job(c, r["job_id"])


def validate_render_settings(model, quality, canvas):
    try:
        validate_settings(model, quality, canvas)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


def check_capacity(c, count):
    pending = c.execute(
        "SELECT COUNT(*) FROM renders WHERE status IN ('queued','running')"
    ).fetchone()[0]
    if pending + count > MAX_PENDING_IMAGES:
        raise HTTPException(
            429, "The production queue is full. Wait for existing images to finish."
        )


def render_history(workspace, rid, user):
    chain = []
    visited = set()
    with workspace.db() as c:
        curr = c.execute("SELECT * FROM renders WHERE id=?", (rid,)).fetchone()
        if not curr:
            raise HTTPException(404, "Image not found")
        while curr and curr["id"] not in visited and len(chain) < 50:
            visited.add(curr["id"])
            item = dict(curr)
            chain.append(
                {
                    "id": item["id"],
                    "sku": item["sku"],
                    "user": item["user"],
                    "created_at": item["created_at"],
                    "prompt": item["prompt"],
                    "status": item["status"],
                    "memory_snapshot": json.loads(item["memory_snapshot"] or "{}"),
                    "url": f"/api/media/{item['file']}" if item["file"] else None,
                }
            )
            curr = (
                c.execute(
                    "SELECT * FROM renders WHERE id=?", (item["parent_id"],)
                ).fetchone()
                if item["parent_id"]
                else None
            )
    chain.reverse()
    return chain


def generate(
    workspace,
    files,
    skus,
    kind,
    scenes,
    scene_sets,
    model,
    quality,
    canvas,
    aplus_profile_id,
    idempotency_key,
    user,
):
    validate_key(idempotency_key)
    if kind not in TASK_RULES:
        raise HTTPException(400, "Unsupported image type")
    if kind == "aplus":
        if not workspace.settings.enable_aplus:
            raise HTTPException(404, "A+ Studio is disabled")
        if canvas not in ("banner", "mobile"):
            raise HTTPException(400, "A+ requires the desktop banner or mobile canvas")
        if not aplus_profile_id:
            raise HTTPException(400, "Choose a saved A+ profile")
    elif aplus_profile_id:
        raise HTTPException(400, "An A+ profile applies only to A+ generation")
    validate_render_settings(model, quality, canvas)
    if not 1 <= len(files) <= 50:
        raise HTTPException(400, "Upload 1–50 product photos per job")
    try:
        sku_list = json.loads(skus)
        overrides = json.loads(scene_sets) if scene_sets else [""] * len(files)
    except (ValueError, TypeError):
        raise HTTPException(400, "Invalid product identifiers or scene overrides")
    if (
        not isinstance(sku_list, list)
        or len(sku_list) != len(files)
        or not all(isinstance(x, str) for x in sku_list)
    ):
        raise HTTPException(400, "Provide one text SKU per uploaded photo")
    sku_list = [
        canonical_sku(sku or f"Product-{index + 1}")
        for index, sku in enumerate(sku_list)
    ]
    defaults = [s.strip() for s in scenes.splitlines() if s.strip()]
    if not 1 <= len(defaults) <= 12:
        raise HTTPException(400, "Enter 1–12 default scenes, one per line")
    if not isinstance(overrides, list) or len(overrides) != len(files):
        raise HTTPException(400, "Scene overrides must match product count")
    per_product = []
    for override in overrides:
        if not isinstance(override, str):
            raise HTTPException(400, "Invalid scene override")
        lines = (
            [x.strip() for x in override.splitlines() if x.strip()]
            if override.strip()
            else defaults
        )
        if not 1 <= len(lines) <= 12 or any(len(line) > 3500 for line in lines):
            raise HTTPException(
                400, "Each product needs 1–12 scenes of at most 3500 characters"
            )
        per_product.append(lines)
    count = sum(map(len, per_product))
    if count > MAX_IMAGES:
        raise HTTPException(400, f"A job can contain at most {MAX_IMAGES} images")
    raw_images = []
    total_bytes = 0
    for upload in files:
        raw = upload.file.read(MAX_IMAGE_BYTES + 1)
        total_bytes += len(raw)
        if len(raw) > MAX_IMAGE_BYTES or total_bytes > MAX_BATCH_BYTES:
            raise HTTPException(
                413, "Photos must be under 18 MB each and 100 MB combined"
            )
        raw_images.append(raw)
    request_data = {
        "skus": sku_list,
        "kind": kind,
        "scenes": per_product,
        "model": model,
        "quality": quality,
        "canvas": canvas,
    }
    if kind == "aplus":
        request_data["aplus_profile_id"] = aplus_profile_id
    digest = fingerprint(request_data, raw_images)
    with workspace.db() as c:
        previous = replay(c, user, idempotency_key, "generate", digest)
        if previous:
            return previous
    saved = []
    committed = False
    try:
        for raw in raw_images:
            filename = f"{uuid.uuid4().hex}.png"
            saved.append(filename)
            save_image_to_png(raw, workspace.uploads / filename)
        job_id = uuid.uuid4().hex
        result = {
            "job_id": job_id,
            "images_queued": count,
            "mode": workspace.settings.mode,
        }
        with workspace.db() as c:
            c.execute("BEGIN IMMEDIATE")
            previous = replay(c, user, idempotency_key, "generate", digest)
            if previous:
                return previous
            check_capacity(c, count)
            profile = aplus.snapshot(c, aplus_profile_id) if kind == "aplus" else None
            c.execute(
                "INSERT INTO jobs VALUES(?,?,?,?,?,?)",
                (job_id, user, kind, now(), count, "running"),
            )
            for index, source in enumerate(saved):
                sku = resolve_product_sku(c, sku_list[index])
                snapshot = memory_snapshot(workspace, sku, connection=c)
                if profile:
                    snapshot = json.dumps(
                        {**json.loads(snapshot), "aplus": profile}, ensure_ascii=False
                    )
                for scene in per_product[index]:
                    if len(make_prompt(kind, scene, snapshot)) > 32000:
                        raise HTTPException(
                            400,
                            "Combined product memory and scene exceed the 32000-character provider limit",
                        )
                    render_id = uuid.uuid4().hex
                    c.execute(
                        "INSERT INTO renders (id,job_id,user,sku,kind,source,original,prompt,model,quality,canvas,created_at,memory_snapshot) "
                        "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (
                            render_id,
                            job_id,
                            user,
                            sku,
                            kind,
                            source,
                            "",
                            scene,
                            model,
                            quality,
                            canvas,
                            now(),
                            snapshot,
                        ),
                    )
                    if profile:
                        c.execute(
                            "UPDATE renders SET aplus_profile_id=? WHERE id=?",
                            (aplus_profile_id, render_id),
                        )
            remember(c, user, idempotency_key, "generate", digest, result, now())
        committed = True
        return result
    finally:
        if not committed:
            for filename in saved:
                (workspace.uploads / filename).unlink(missing_ok=True)


def revise(workspace, body, idempotency_key, user):
    validate_key(idempotency_key)
    if len(body.instructions.strip()) < 5:
        raise HTTPException(400, "Explain the correction in at least five characters")
    digest = fingerprint(body.model_dump())
    with workspace.db() as c:
        c.execute("BEGIN IMMEDIATE")
        previous = replay(c, user, idempotency_key, "revise", digest)
        if previous:
            return previous
        parent = owned(
            c.execute("SELECT * FROM renders WHERE id=?", (body.render_id,)).fetchone(),
            user,
        )
        if parent["status"] != "completed":
            raise HTTPException(400, "Only completed images can be revised")
        if parent["kind"] == "aplus" and not workspace.settings.enable_aplus:
            raise HTTPException(404, "A+ Studio is disabled")
        validate_render_settings(body.model, body.quality, parent["canvas"])
        check_capacity(c, 1)
        sku = resolve_product_sku(c, parent["sku"])
        snapshot = memory_snapshot(workspace, sku, connection=c)
        if parent["kind"] == "aplus":
            original_profile = json.loads(parent["memory_snapshot"] or "{}").get(
                "aplus"
            )
            if not original_profile:
                raise HTTPException(
                    409,
                    "This A+ render has no saved profile snapshot. Create a new A+ job.",
                )
            snapshot = json.dumps(
                {**json.loads(snapshot), "aplus": original_profile}, ensure_ascii=False
            )
        if len(make_prompt(parent["kind"], body.instructions, snapshot)) > 32000:
            raise HTTPException(
                400,
                "Combined product memory and revision exceed the 32000-character provider limit",
            )
        job_id, rid = uuid.uuid4().hex, uuid.uuid4().hex
        c.execute(
            "INSERT INTO jobs VALUES(?,?,?,?,?,?)",
            (job_id, user, parent["kind"], now(), 1, "running"),
        )
        c.execute(
            "INSERT INTO renders (id,job_id,user,sku,kind,source,original,prompt,model,quality,canvas,parent_id,created_at,memory_snapshot,pending_rule) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                rid,
                job_id,
                user,
                sku,
                parent["kind"],
                parent["source"],
                parent["file"],
                body.instructions.strip(),
                body.model,
                body.quality,
                parent["canvas"],
                parent["id"],
                now(),
                snapshot,
                body.instructions.strip() if body.remember_rule else "",
            ),
        )
        if parent["kind"] == "aplus":
            c.execute(
                "UPDATE renders SET aplus_profile_id=? WHERE id=?",
                (parent["aplus_profile_id"], rid),
            )
        result = {
            "job_id": job_id,
            "render_id": rid,
            "mode": workspace.settings.mode,
            "saved_rule": False,
            "pending_rule": body.remember_rule,
        }
        remember(c, user, idempotency_key, "revise", digest, result, now())
    return result


def renders(workspace, user):
    with workspace.db() as c:
        rows = [
            dict(x)
            for x in c.execute(
                "SELECT * FROM renders ORDER BY created_at DESC, rowid DESC LIMIT 300"
            )
        ]
    for r in rows:
        r["url"] = f"/api/media/{r['file']}" if r["file"] else None
        r.pop("source", None)
        r.pop("original", None)
        r.pop("memory_snapshot", None)
    return rows


def jobs(workspace, user):
    with workspace.db() as c:
        rows = [
            dict(x)
            for x in c.execute("""SELECT j.*,SUM(CASE WHEN r.status='completed' THEN 1 ELSE 0 END) completed,
           SUM(CASE WHEN r.status='failed' THEN 1 ELSE 0 END) failed FROM jobs j
           LEFT JOIN renders r ON j.id=r.job_id GROUP BY j.id ORDER BY j.created_at DESC LIMIT 25""")
        ]
    return rows


def media(workspace, filename, user):
    if not re.fullmatch(r"[0-9a-f]{32}\.jpg", filename):
        raise HTTPException(404, "Image not found")
    with workspace.db() as c:
        exists = c.execute(
            "SELECT id FROM renders WHERE file=?", (filename,)
        ).fetchone()
    if not exists or not (workspace.outputs / filename).exists():
        raise HTTPException(404, "Image not found")
    return workspace.outputs / filename


def approve(workspace, rid, body, user):
    with workspace.db() as c:
        c.execute("BEGIN IMMEDIATE")
        row = c.execute("SELECT * FROM renders WHERE id=?", (rid,)).fetchone()
        if not row:
            raise HTTPException(404, "Image not found")
        if row["status"] != "completed":
            raise HTTPException(400, "Image not ready")
        c.execute("UPDATE renders SET approved=? WHERE id=?", (int(body.approved), rid))
        if body.approved and row["pending_rule"]:
            sku = resolve_product_sku(c, row["sku"])
            c.execute(
                "INSERT OR IGNORE INTO product_memory(sku,facts,updated_by,updated_at) VALUES(?,?,?,?)",
                (sku, "", user, now()),
            )
            c.execute(
                "INSERT INTO product_rules VALUES(?,?,?,?,?,?)",
                (uuid.uuid4().hex, sku, row["pending_rule"], user, now(), rid),
            )
            c.execute("UPDATE renders SET pending_rule='' WHERE id=?", (rid,))
            audit(c, user, "approved_revision_saved_as_rule", sku, row["pending_rule"])
    return {"approved": body.approved}
