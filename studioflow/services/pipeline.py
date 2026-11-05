"""Stage-aware rendering; replay saved responses and transfers, never paid calls."""

import json
import logging
import time
import uuid
from contextlib import ExitStack
from types import SimpleNamespace

from botocore.exceptions import BotoCoreError, ClientError
from PIL import Image, ImageOps
from sqlalchemy.exc import OperationalError

from . import assets, usage
from ..composition import compose, main_image, thumbnail
from ..persistence.repository import link_asset
from ..provider import PRESETS, write_response_image

logger = logging.getLogger(__name__)


class ResolutionMismatch(ValueError):
    pass


def asset_identity(rid, role):
    return uuid.uuid5(uuid.NAMESPACE_URL, f"studioflow:render:{rid}:{role}").hex


def execute(workspace, row, owner):
    from . import rendering

    rid = row["id"]
    receipt_file = workspace.staging / f"{rid}.receipt.json"
    master_path = workspace.staging / f"{rid}.master.png"
    output = workspace.staging / f"{rid}.final.jpg"
    thumb = workspace.staging / f"{rid}.thumb.webp"
    operation = row.get("operation", "generate")
    attempt = None
    master_id = row.get("master_asset_id")
    try:
        if receipt_file.exists():
            attempt = json.loads(receipt_file.read_text("utf-8"))["attempt"]
        recipe = json.loads(row.get("recipe_json") or "{}")
        with ExitStack() as stack:

            def local_asset(aid):
                return stack.enter_context(assets.materialize(workspace, aid))

            def legacy_path(folder, filename):
                aid = assets.legacy_id(workspace, folder + "/" + filename)
                if aid:
                    return local_asset(aid)
                path = workspace.data / folder / filename
                if path.parent != workspace.data / folder or not path.is_file():
                    raise RuntimeError("Source image is missing")
                return path

            if operation == "compose":
                if not master_id:
                    with workspace.db() as c:
                        parent = c.execute(
                            "SELECT master_asset_id,output_asset_id,status FROM renders WHERE id=?",
                            (row["depends_on"],),
                        ).fetchone()
                    if not parent or parent["status"] != "completed":
                        raise RuntimeError("Background dependency did not complete")
                    master_id = parent["master_asset_id"] or parent["output_asset_id"]
                compose(
                    local_asset(master_id),
                    output,
                    recipe,
                    cutout=local_asset(recipe["cutout_asset_id"])
                    if recipe.get("cutout_asset_id")
                    else None,
                )
            elif operation == "main":
                main_image(
                    local_asset(recipe["cutout_asset_id"]),
                    output,
                    recipe.get("pack_count", 1),
                )
                master_id = assets.reserve(
                    workspace,
                    output,
                    "generated_master",
                    row["user"],
                    aid=asset_identity(rid, "master"),
                )
            else:
                if not master_id:
                    source = (
                        local_asset(row["source_asset_id"])
                        if row.get("source_asset_id")
                        else legacy_path("uploads", row["source"])
                    )
                    if receipt_file.exists():
                        receipt = json.loads(receipt_file.read_text("utf-8"))
                        response = SimpleNamespace(
                            data=[SimpleNamespace(**d) for d in receipt["data"]],
                            usage=receipt.get("usage"),
                            _request_id=receipt.get("request_id"),
                        )
                        usage.record_response(
                            workspace.db, receipt["attempt"], response
                        )
                        write_response_image(
                            response, output, row["canvas"], master_output=master_path
                        )
                    elif workspace.settings.mode == "demo":
                        rendering.render_demo(
                            source, output, row["kind"], row["prompt"], row["canvas"]
                        )
                        assets.atomic_write(master_path, output.read_bytes())
                    else:
                        with workspace.db() as c:
                            previous = c.execute(
                                "SELECT attempt_id FROM usage_ledger WHERE render_id=? LIMIT 1",
                                (rid,),
                            ).fetchone()
                        if previous:
                            raise RuntimeError(
                                "Paid request outcome is uncertain; review before resubmitting"
                            )
                        references = json.loads(row.get("references_json") or "[]")
                        inputs = (
                            [local_asset(ref["asset_id"]) for ref in references]
                            if references
                            else [source]
                        )
                        if row.get("original"):
                            with workspace.db() as c:
                                parent = c.execute(
                                    "SELECT master_asset_id FROM renders WHERE id=?",
                                    (row["parent_id"],),
                                ).fetchone()
                            # Edit the clean saved master; controlled typography
                            # is composed again after the paid image edit.
                            previous_image = (
                                local_asset(parent["master_asset_id"])
                                if parent and parent["master_asset_id"]
                                else legacy_path("outputs", row["original"])
                            )
                            inputs.insert(0, previous_image)
                        prompt = rendering.make_prompt(
                            row["kind"], row["prompt"], row["memory_snapshot"]
                        )
                        if references:
                            offset = 2 if row.get("original") else 1
                            prompt += "\nREFERENCE ROLES: " + "; ".join(
                                f"Image {i + offset}: {ref['role']}"
                                for i, ref in enumerate(references)
                            )
                            prompt += ". Product photos define the item. Style references define only setting, colours and lighting; never copy their text or infer product facts."

                        def started():
                            nonlocal attempt
                            attempt = usage.begin_attempt(workspace.db, row)

                        def received(response):
                            raw_usage = getattr(response, "usage", None)
                            if hasattr(raw_usage, "model_dump"):
                                raw_usage = raw_usage.model_dump(mode="json")
                            receipt = {
                                "attempt": attempt,
                                "request_id": getattr(response, "_request_id", None),
                                "usage": raw_usage
                                if isinstance(raw_usage, dict)
                                else None,
                                "data": [
                                    {"b64_json": getattr(d, "b64_json", None)}
                                    for d in (getattr(response, "data", None) or [])
                                ],
                            }
                            assets.atomic_write(
                                receipt_file, json.dumps(receipt).encode()
                            )
                            usage.record_response(workspace.db, attempt, response)

                        rendering.render_live(
                            inputs,
                            output,
                            prompt,
                            row["model"],
                            row["quality"],
                            row["canvas"],
                            workspace.settings.api_key,
                            aplus=row["kind"] == "aplus",
                            on_request=started,
                            on_response=received,
                            native_size=row.get("native_size", ""),
                            validated_models=workspace.settings.validated_high_res_models,
                            master_output=master_path,
                        )
                    master_id = assets.reserve(
                        workspace,
                        master_path,
                        "generated_master",
                        row["user"],
                        aid=asset_identity(rid, "master"),
                    )
                    with workspace.db() as c:
                        c.execute(
                            "UPDATE renders SET master_asset_id=?,stage='generated' WHERE id=? AND worker_id=?",
                            (master_id, rid, owner),
                        )
                        link_asset(c, master_id, "render", rid)
                assets.transfer(workspace, master_id)
                if (
                    row.get("native_size") == "2048x2048"
                    and workspace.settings.mode == "live"
                ):
                    master = assets.get(workspace, master_id)
                    if (master["width"], master["height"]) != (2048, 2048):
                        raise ResolutionMismatch(
                            "Provider returned a different native resolution. The master and receipt were saved; review before another paid request."
                        )
                background = local_asset(master_id)
                if recipe:
                    compose(
                        background,
                        output,
                        recipe,
                        cutout=local_asset(recipe["cutout_asset_id"])
                        if recipe.get("cutout_asset_id")
                        else None,
                    )
                elif not output.is_file():
                    with Image.open(background) as image:
                        image = ImageOps.pad(
                            image.convert("RGB"),
                            PRESETS[row["canvas"]][1],
                            color="white",
                        )
                        image.save(output, "JPEG", quality=95, optimize=True)
            if master_id:
                assets.transfer(workspace, master_id)
            output_id = assets.reserve(
                workspace,
                output,
                "finished_image",
                row["user"],
                aid=asset_identity(rid, "output"),
                parent_id=master_id,
            )
            thumbnail(output, thumb)
            thumb_id = assets.reserve(
                workspace,
                thumb,
                "thumbnail",
                row["user"],
                aid=asset_identity(rid, "thumbnail"),
                parent_id=output_id,
            )
            with workspace.db() as c:
                c.execute(
                    "UPDATE renders SET master_asset_id=?,output_asset_id=?,thumbnail_asset_id=?,stage='transferring' WHERE id=? AND worker_id=?",
                    (master_id, output_id, thumb_id, rid, owner),
                )
                for aid in (master_id, output_id, thumb_id):
                    link_asset(c, aid, "render", rid)
            assets.transfer(workspace, output_id)
            assets.transfer(workspace, thumb_id)
            assets.map_legacy(workspace, "outputs/" + rid + ".jpg", output_id)
            if workspace.settings.storage_backend == "local":
                assets.atomic_write(
                    workspace.outputs / (rid + ".jpg"), output.read_bytes()
                )
            usage.finish_attempt(workspace.db, attempt, True)
            with workspace.db() as c:
                c.execute(
                    "UPDATE renders SET status='completed',file=?,stage='ready',error='' WHERE id=? AND worker_id=? AND status='running'",
                    (rid + ".jpg", rid, owner),
                )
                rendering.finish_job(c, row["job_id"])
    except Exception as exc:
        rendering.logger.exception("Render pipeline failed for %s", rid)
        retryable = isinstance(
            exc,
            (
                BotoCoreError,
                ClientError,
                ConnectionError,
                TimeoutError,
                OSError,
                OperationalError,
            ),
        ) and (
            receipt_file.exists()
            or master_id
            or operation != "generate"
            or attempt is None
        )
        try:
            if not retryable:
                usage.finish_attempt(workspace.db, attempt, False)
            with workspace.db() as c:
                c.execute(
                    "UPDATE renders SET status=?,error=?,transfer_after=? WHERE id=? AND worker_id=?",
                    (
                        "transferring" if retryable else "failed",
                        "Saved image transfer is pending; retrying without an API call"
                        if retryable
                        else str(exc)
                        if isinstance(exc, ResolutionMismatch)
                        else "Image processing failed. Review before submitting another paid request.",
                        time.time() + 30 if retryable else 0,
                        rid,
                        owner,
                    ),
                )
                rendering.finish_job(c, row["job_id"])
        except Exception:
            logger.exception(
                "Could not record failure for %s; receipt remains on disk", rid
            )
