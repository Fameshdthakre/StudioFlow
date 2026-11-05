"""Validated mixed-image plans with immutable references and stable listing slots."""

import json
import uuid
from typing import Literal

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from ..composition import Recipe
from ..persistence.repository import (
    ensure_product,
    next_sequence,
    link_asset,
    link_snapshot,
)
from ..provider import DEFAULT_MODEL, validate_settings, validate_native_size
from ..submissions import validate_key, fingerprint, replay, remember
from . import assets, maintenance
from .catalog import resolve_product_sku
from .common import now, owned
from .memory import memory_snapshot


class OutputSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["main", "lifestyle", "infographic"]
    scene: str = Field(default="", max_length=3500)
    recipe: Recipe | None = None
    reuse_output_index: int | None = Field(default=None, ge=0)


class ProductSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    sku: str
    product_references: list[str] = Field(min_length=1, max_length=12)
    style_references: list[str] = Field(default_factory=list, max_length=3)
    cutout_asset_id: str | None = None
    cutout_approved: bool = False
    cutout_is_single_item: bool = False
    pack_count: int = Field(default=1, ge=1, le=12)
    pack_confirmed: bool = False
    outputs: list[OutputSpec] = Field(min_length=1, max_length=12)


class GenerationPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: Literal[1] = 1
    model: str = DEFAULT_MODEL
    quality: str = "high"
    canvas: Literal["square", "landscape", "portrait"] = "square"
    native_size: str = ""
    products: list[ProductSpec] = Field(min_length=1, max_length=50)


def submit(workspace, value, user, key):
    from .rendering import check_capacity, make_prompt

    if not workspace.settings.enable_creation:
        raise HTTPException(404, "Multi-type creation is not enabled")
    validate_key(key)
    try:
        plan = GenerationPlan.model_validate(value)
        validate_settings(plan.model, plan.quality, plan.canvas)
        validate_native_size(
            plan.model,
            plan.canvas,
            plan.native_size,
            workspace.settings.validated_high_res_models,
        )
    except (ValueError, ValidationError) as exc:
        raise HTTPException(400, str(exc)) from exc
    count = sum(len(p.outputs) for p in plan.products)
    if count > 250:
        raise HTTPException(400, "A job can contain at most 250 images")
    digest = fingerprint(plan.model_dump())
    with workspace.db() as c:
        previous = replay(c, user, key, "generate", digest)
        if previous:
            return previous
    known_assets = {}
    paid_count = 0
    for product in plan.products:
        if sum(o.kind == "main" for o in product.outputs) > 1:
            raise HTTPException(400, "Choose one MAIN output per product image set")
        ids = [(a, "product_reference") for a in product.product_references] + [
            (a, "style_reference") for a in product.style_references
        ]
        if product.cutout_asset_id:
            ids.append((product.cutout_asset_id, "cutout"))
        for index, output in enumerate(product.outputs):
            if output.kind == "main" and not (
                product.pack_confirmed or product.cutout_approved
            ):
                raise HTTPException(
                    400, "Confirm the pack quantity before creating MAIN images"
                )
            if output.kind == "infographic":
                if not workspace.settings.enable_composition:
                    raise HTTPException(404, "Finished graphic cards are not enabled")
                if not output.recipe:
                    raise HTTPException(
                        400, "Infographics require a confirmed layout and copy"
                    )
                if plan.canvas != "square":
                    raise HTTPException(
                        400,
                        "Finished graphic templates currently require a square canvas",
                    )
                if output.recipe.cutout_asset_id:
                    ids.append((output.recipe.cutout_asset_id, "cutout"))
            elif output.recipe:
                raise HTTPException(400, "Layouts apply only to infographic outputs")
            if output.reuse_output_index is not None:
                if output.kind != "infographic" or output.reuse_output_index >= index:
                    raise HTTPException(
                        400,
                        "A card can reuse only an earlier output in this product set",
                    )
            elif output.kind == "main" and product.cutout_asset_id:
                if (
                    not product.cutout_approved
                    or not product.cutout_is_single_item
                    or plan.canvas != "square"
                ):
                    raise HTTPException(
                        400,
                        "Local MAIN requires a confirmed single-item cutout and square canvas",
                    )
            else:
                if not output.scene.strip():
                    raise HTTPException(400, "Enter a scene for every paid generation")
                paid_count += 1
        for aid, role in ids:
            asset = known_assets.get(aid) or assets.get(workspace, aid)
            if asset["role"] != role:
                raise HTTPException(400, f"Asset must be a {role.replace('_', ' ')}")
            known_assets[aid] = asset
    if sum(a["byte_size"] for a in known_assets.values()) > 100 * 1024 * 1024:
        raise HTTPException(413, "Selected references exceed 100 MB")
    job_id = uuid.uuid4().hex
    response = {
        "job_id": job_id,
        "images_queued": count,
        "paid_requests": paid_count if workspace.settings.mode == "live" else 0,
        "mode": workspace.settings.mode,
    }
    with workspace.db() as c:
        c.execute("BEGIN IMMEDIATE")
        maintenance.require_writable(c)
        previous = replay(c, user, key, "generate", digest)
        if previous:
            return previous
        check_capacity(c, count)
        c.execute(
            "INSERT INTO jobs(id,user,kind,created_at,total,status) VALUES(?,?,?,?,?,?)",
            (job_id, user, "mixed", now(), count, "running"),
        )
        seen = set()
        for product in plan.products:
            sku = resolve_product_sku(c, product.sku)
            if sku in seen:
                raise HTTPException(400, "Group all reference angles under one product")
            seen.add(sku)
            pid = ensure_product(c, sku)
            snapshot = memory_snapshot(workspace, sku, connection=c)
            memory = json.loads(snapshot)
            asin = (memory.get("catalog") or {}).get("asin", "")
            sid = uuid.uuid4().hex
            specification = {
                "version": 1,
                "product": product.model_dump(),
                "model": plan.model,
                "quality": plan.quality,
                "canvas": plan.canvas,
                "native_size": plan.native_size,
                "memory_snapshot": memory,
            }
            c.execute(
                "INSERT INTO image_sets(id,product_id,job_id,sku,asin,spec_json,created_at) VALUES(?,?,?,?,?,?,?)",
                (sid, pid, job_id, sku, asin, json.dumps(specification), now()),
            )
            prior_ids, pt = [], 0
            for index, output in enumerate(product.outputs):
                rid = uuid.uuid4().hex
                variant = "MAIN" if output.kind == "main" else f"PT{pt + 1:02}"
                pt += output.kind != "main"
                operation = "generate"
                recipe = output.recipe.model_dump() if output.recipe else {}
                dependency = None
                if output.reuse_output_index is not None:
                    operation = "compose"
                    dependency = prior_ids[output.reuse_output_index]
                elif output.kind == "main" and product.cutout_asset_id:
                    operation = "main"
                    recipe = {
                        "cutout_asset_id": product.cutout_asset_id,
                        "pack_count": product.pack_count,
                    }
                scene = output.scene
                if output.kind == "main":
                    scene += f"\nVerified pack quantity: {product.pack_count}. Show exactly this many complete items, clearly separated."
                if len(make_prompt(output.kind, scene, snapshot)) > 31000:
                    raise HTTPException(
                        400, "Combined scene and product memory are too long"
                    )
                references = [
                    {"asset_id": a, "role": "product_reference"}
                    for a in product.product_references
                ]
                references += [
                    {"asset_id": a, "role": "style_reference"}
                    for a in product.style_references
                ]
                c.execute(
                    """INSERT INTO renders(id,job_id,user,sku,kind,prompt,model,quality,canvas,created_at,
                    memory_snapshot,enqueue_seq,product_id,image_set_id,variant,sku_snapshot,asin_snapshot,
                    operation,recipe_json,references_json,native_size,source_asset_id,depends_on)
                    VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        rid,
                        job_id,
                        user,
                        sku,
                        output.kind,
                        scene,
                        plan.model,
                        plan.quality,
                        plan.canvas,
                        now(),
                        snapshot,
                        next_sequence(c),
                        pid,
                        sid,
                        variant,
                        sku,
                        asin,
                        operation,
                        json.dumps(recipe),
                        json.dumps(references),
                        plan.native_size,
                        product.product_references[0],
                        dependency,
                    ),
                )
                for ref in references:
                    link_asset(c, ref["asset_id"], "render", rid)
                link_asset(c, recipe.get("cutout_asset_id"), "render", rid)
                link_snapshot(c, memory, rid)
                prior_ids.append(rid)
        remember(c, user, key, "generate", digest, response, now())
    return response


def recompose(workspace, rid, recipe, user, key):
    from .rendering import check_capacity

    if not workspace.settings.enable_composition:
        raise HTTPException(404, "Composition is not enabled")
    validate_key(key)
    try:
        recipe = Recipe.model_validate(recipe)
    except ValidationError as exc:
        raise HTTPException(400, str(exc)) from exc
    if (
        recipe.cutout_asset_id
        and assets.get(workspace, recipe.cutout_asset_id)["role"] != "cutout"
    ):
        raise HTTPException(400, "Select an approved cutout")
    digest = fingerprint({"render_id": rid, "recipe": recipe.model_dump()})
    with workspace.db() as c:
        c.execute("BEGIN IMMEDIATE")
        maintenance.require_writable(c)
        previous = replay(c, user, key, "compose", digest)
        if previous:
            return previous
        parent = owned(
            c.execute("SELECT * FROM renders WHERE id=?", (rid,)).fetchone(), user
        )
        if parent["status"] != "completed" or parent["canvas"] != "square":
            raise HTTPException(400, "Choose a completed square image")
        master = parent["master_asset_id"] or parent["output_asset_id"]
        if not master:
            raise HTTPException(
                409, "Import this historical image into asset storage before composing"
            )
        check_capacity(c, 1)
        job_id, revision_id = uuid.uuid4().hex, uuid.uuid4().hex
        c.execute(
            "INSERT INTO jobs(id,user,kind,created_at,total,status) VALUES(?,?,?,?,?,?)",
            (job_id, user, "infographic", now(), 1, "running"),
        )
        fields = (
            "sku",
            "source",
            "original",
            "model",
            "quality",
            "canvas",
            "memory_snapshot",
            "product_id",
            "image_set_id",
            "variant",
            "sku_snapshot",
            "asin_snapshot",
            "source_asset_id",
            "references_json",
            "native_size",
        )
        values = {k: parent[k] for k in fields}
        values.update(
            id=revision_id,
            job_id=job_id,
            user=user,
            kind="infographic",
            prompt=parent["prompt"],
            created_at=now(),
            parent_id=rid,
            operation="compose",
            master_asset_id=master,
            recipe_json=recipe.model_dump_json(),
            enqueue_seq=next_sequence(c),
        )
        c.execute(
            f"INSERT INTO renders({','.join(values)}) VALUES({','.join('?' for _ in values)})",
            tuple(values.values()),
        )
        link_asset(c, master, "render", revision_id)
        link_asset(c, recipe.cutout_asset_id, "render", revision_id)
        link_asset(c, parent["source_asset_id"], "render", revision_id)
        for ref in json.loads(parent["references_json"] or "[]"):
            link_asset(c, ref["asset_id"], "render", revision_id)
        link_snapshot(c, json.loads(parent["memory_snapshot"] or "{}"), revision_id)
        result = {
            "job_id": job_id,
            "render_id": revision_id,
            "mode": workspace.settings.mode,
            "paid_requests": 0,
        }
        remember(c, user, key, "compose", digest, result, now())
    return result
