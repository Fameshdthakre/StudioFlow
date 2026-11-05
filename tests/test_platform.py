"""New workflows and paid-request recovery, using offline provider fixtures only."""

import base64
import dataclasses
import io
import json
import unittest
import zipfile
from concurrent.futures import ThreadPoolExecutor
from functools import partial
from types import SimpleNamespace
from unittest.mock import patch

from PIL import Image, ImageDraw

from studioflow.jobs import RenderQueue
from studioflow.services import assets, library, maintenance, rendering
from studioflow.services.pipeline import asset_identity
from tests.support import isolated_app


def picture(cutout=False):
    image = Image.new("RGBA", (800, 1000), (0, 0, 0, 0) if cutout else "#e9dfcd")
    draw = ImageDraw.Draw(image)
    draw.rectangle((320, 100, 480, 900), fill="#086143")
    out = io.BytesIO()
    image.save(out, "PNG")
    return out.getvalue()


class PlatformTests(unittest.TestCase):
    def setUp(self):
        context = isolated_app()
        self.ws, self.client = context.__enter__()
        self.addCleanup(context.__exit__, None, None, None)
        self.reference = self.upload()
        self.cutout = self.upload("cutout")

    def upload(self, role="product_reference"):
        response = self.client.post(
            "/api/assets",
            data={"role": role},
            files={"file": ("reference.png", picture(role == "cutout"), "image/png")},
        )
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()["asset_id"]

    def recipe(self, **changes):
        return dict(
            template="dimensions",
            headline="60 CM PICKS",
            body="Three picks included",
            measurement=60,
            unit="cm",
            pack_count=3,
            facts_confirmed=True,
            cutout_asset_id=self.cutout,
            cutout_approved=True,
            cutout_is_single_item=True,
            **changes,
        )

    def plan(self):
        return {
            "version": 1,
            "products": [
                {
                    "sku": "GREEN-PICKS",
                    "product_references": [self.reference],
                    "cutout_asset_id": self.cutout,
                    "cutout_approved": True,
                    "cutout_is_single_item": True,
                    "pack_count": 3,
                    "outputs": [
                        {"kind": "main"},
                        {
                            "kind": "lifestyle",
                            "scene": "Warm elegant interior, accurate green product",
                        },
                        {
                            "kind": "infographic",
                            "recipe": self.recipe(),
                            "reuse_output_index": 1,
                        },
                    ],
                }
            ],
        }

    def submit(self, plan=None, key="platform-request-0001"):
        return self.client.post(
            "/api/generate",
            data={"generation_plan": json.dumps(plan or self.plan())},
            headers={"Idempotency-Key": key},
        )

    def run_queue(self):
        queue = RenderQueue(
            self.ws.db, partial(rendering.generate_one, self.ws), platform=True
        )
        queue.heartbeat()
        while row := queue.claim():
            queue.execute(row)
        return queue

    def test_mixed_plan_outputs_variants_transparency_and_zip(self):
        first = self.submit()
        self.assertEqual(first.status_code, 200, first.text)
        self.assertEqual(self.submit().json(), first.json())
        self.assertEqual(first.json()["images_queued"], 3)
        self.run_queue()
        rows = self.client.get("/api/library").json()["items"]
        self.assertEqual({r["status"] for r in rows}, {"completed"})
        self.assertEqual({r["variant"] for r in rows}, {"MAIN", "PT01", "PT02"})
        self.assertFalse(any(r["approved"] for r in rows))
        with (
            assets.materialize(self.ws, self.cutout) as path,
            Image.open(path) as image,
        ):
            self.assertEqual(image.mode, "RGBA")
            self.assertEqual(image.size, (800, 1000))
        for row in rows:
            image = Image.open(io.BytesIO(self.client.get(row["url"]).content))
            self.assertEqual(image.size, (2000, 2000))
            self.assertEqual(image.format, "JPEG")
            if row["kind"] == "main":
                self.assertEqual(image.getpixel((560, 1000)), (255, 255, 255))
                for point in ((560, 560), (1440, 560), (1000, 1440)):
                    self.assertLess(image.getpixel(point)[0], 30)
            self.assertEqual(
                self.client.get(row["thumbnail_url"]).headers["content-type"],
                "image/webp",
            )
        response = self.client.post(
            "/api/exports", json={"render_ids": [r["id"] for r in rows]}
        )
        self.assertEqual(response.status_code, 200, response.text)
        library.process_exports(self.ws, "test-export")
        status = self.client.get("/api/exports/" + response.json()["id"]).json()
        self.assertEqual(status["status"], "completed", status)
        with zipfile.ZipFile(
            io.BytesIO(self.client.get(status["download_url"]).content)
        ) as archive:
            self.assertEqual(
                set(archive.namelist()),
                {
                    "GREEN-PICKS.MAIN.jpg",
                    "GREEN-PICKS.PT01.jpg",
                    "GREEN-PICKS.PT02.jpg",
                },
            )
        self.client.post("/api/logout")
        self.assertEqual(self.client.get(status["download_url"]).status_code, 401)
        self.assertEqual(self.client.get(rows[0]["url"]).status_code, 401)

    def test_local_recomposition_inherits_slot_and_never_calls_provider(self):
        self.submit()
        self.run_queue()
        parent = next(
            r
            for r in self.client.get("/api/library").json()["items"]
            if r["kind"] == "infographic"
        )
        recipe = self.recipe()
        recipe["headline"] = "Updated verified headline"
        response = self.client.post(
            f"/api/renders/{parent['id']}/compose",
            json={"recipe": recipe},
            headers={"Idempotency-Key": "recompose-0001"},
        )
        self.assertEqual(response.status_code, 200, response.text)
        with patch.object(
            rendering,
            "render_live",
            side_effect=AssertionError("Local composition called provider"),
        ):
            self.run_queue()
        with self.ws.db() as c:
            child = dict(
                c.execute(
                    "SELECT * FROM renders WHERE id=?", (response.json()["render_id"],)
                ).fetchone()
            )
        self.assertEqual(
            (child["variant"], child["image_set_id"], child["approved"]),
            (parent["variant"], parent["image_set_id"], 0),
        )
        collision = self.client.post(
            "/api/exports/preview", json={"render_ids": [parent["id"], child["id"]]}
        )
        self.assertEqual(collision.status_code, 409)

    def test_changed_copy_invalidates_idempotency_and_unvalidated_2k_rejected(self):
        self.assertEqual(self.submit().status_code, 200)
        plan = self.plan()
        plan["products"][0]["outputs"][2]["recipe"]["headline"] = "Changed text"
        self.assertEqual(self.submit(plan).status_code, 409)
        plan["native_size"] = "2048x2048"
        self.assertEqual(self.submit(plan, "new-native-request").status_code, 400)

    def test_missing_facts_unsupported_slots_and_limits_block_before_queue(self):
        plan = self.plan()
        plan["products"][0]["outputs"][2]["recipe"]["headline"] = "W" * 100
        self.assertEqual(self.submit(plan).status_code, 400)
        plan = self.plan()
        plan["products"][0]["outputs"][2]["recipe"]["facts_confirmed"] = False
        self.assertEqual(self.submit(plan).status_code, 400)
        plan = self.plan()
        plan["products"][0]["outputs"].append({"kind": "main"})
        self.assertEqual(self.submit(plan).status_code, 400)
        plan = self.plan()
        plan["products"][0]["outputs"] = [{"kind": "lifestyle", "scene": "studio"}] * 13
        self.assertEqual(self.submit(plan).status_code, 400)
        self.assertEqual(self.client.get("/api/jobs").json(), [])

    def test_saved_receipt_survives_storage_failure_without_repeating_paid_call(self):
        self.ws.settings = dataclasses.replace(
            self.ws.settings, mode="live", api_key="offline-fixture"
        )
        plan = self.plan()
        plan["products"][0]["outputs"] = [
            {"kind": "lifestyle", "scene": "Elegant interior"}
        ]
        self.assertEqual(self.submit(plan).status_code, 200)
        queue = RenderQueue(
            self.ws.db, partial(rendering.generate_one, self.ws), platform=True
        )
        queue.heartbeat()
        response = SimpleNamespace(
            data=[SimpleNamespace(b64_json=base64.b64encode(picture()).decode())],
            usage=None,
            _request_id="offline-receipt",
        )

        def provider(inputs, output, prompt, model, quality, canvas, key, **kwargs):
            kwargs["on_request"]()
            kwargs["on_response"](response)
            from studioflow.provider import write_response_image

            write_response_image(
                response, output, canvas, master_output=kwargs["master_output"]
            )

        with patch.object(rendering, "render_live", side_effect=provider) as call:
            with patch.object(
                self.ws.storage,
                "put",
                side_effect=ConnectionError("offline storage outage"),
            ):
                with self.assertLogs(rendering.logger, level="ERROR"):
                    queue.execute(queue.claim())
            with self.ws.db() as c:
                row = c.execute("SELECT * FROM renders").fetchone()
                self.assertEqual(row["status"], "transferring")
                c.execute("UPDATE renders SET transfer_after=0")
            queue.execute(queue.claim())
            self.assertEqual(call.call_count, 1)
        with self.ws.db() as c:
            self.assertEqual(
                c.execute("SELECT status FROM renders").fetchone()[0], "completed"
            )
            self.assertEqual(
                c.execute("SELECT COUNT(*) FROM usage_ledger").fetchone()[0], 1
            )

    def test_page_cursor_and_selection_limits(self):
        self.submit()
        self.run_queue()
        page = self.client.get("/api/library?limit=2").json()
        next_page = self.client.get(
            "/api/library", params={"cursor": page["next_cursor"], "limit": 2}
        ).json()
        self.assertEqual(len(page["items"]), 2)
        self.assertEqual(len(next_page["items"]), 1)
        self.assertFalse(
            {r["id"] for r in page["items"]} & {r["id"] for r in next_page["items"]}
        )
        self.assertEqual(
            len(
                self.client.get("/api/library?selection=true&kind=main").json()["items"]
            ),
            1,
        )

    def test_parallel_claims_never_assign_an_output_twice(self):
        plan = self.plan()
        plan["products"][0]["outputs"] = [
            {"kind": "lifestyle", "scene": str(i)} for i in range(12)
        ]
        self.assertEqual(self.submit(plan).status_code, 200)
        queues = [
            RenderQueue(self.ws.db, lambda *args: None, platform=True) for _ in range(4)
        ]
        with ThreadPoolExecutor(max_workers=4) as pool:
            rows = list(pool.map(lambda i: queues[i % 4].claim(), range(12)))
        self.assertEqual(len({r["id"] for r in rows}), 12)

    def test_maintenance_blocks_writes_and_claims_but_allows_reads(self):
        self.submit()
        maintenance.set_value(self.ws, "maintenance", "1")
        self.assertEqual(self.submit(key="other-request").status_code, 503)
        queue = RenderQueue(self.ws.db, lambda *args: None, platform=True)
        self.assertIsNone(queue.claim())
        self.assertEqual(self.client.get("/api/library").status_code, 200)

    def test_main_revision_does_not_treat_cutout_arrangement_as_a_text_recipe(self):
        self.submit()
        self.run_queue()
        parent = next(
            r
            for r in self.client.get("/api/library").json()["items"]
            if r["kind"] == "main"
        )
        response = self.client.post(
            "/api/revise",
            json={
                "render_id": parent["id"],
                "instructions": "Improve the shadows while preserving the product",
            },
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.run_queue()
        with self.ws.db() as c:
            child = c.execute(
                "SELECT * FROM renders WHERE id=?", (response.json()["render_id"],)
            ).fetchone()
        self.assertEqual(
            (child["status"], child["variant"], child["approved"]),
            ("completed", "MAIN", 0),
        )
        self.assertEqual(child["image_set_id"], parent["image_set_id"])

    def test_uncertain_paid_request_is_never_repeated_by_queue_recovery(self):
        self.ws.settings = dataclasses.replace(
            self.ws.settings, mode="live", api_key="offline-fixture"
        )
        plan = self.plan()
        plan["products"][0]["outputs"] = [
            {"kind": "lifestyle", "scene": "Warm interior"},
            {"kind": "infographic", "recipe": self.recipe(), "reuse_output_index": 0},
        ]
        self.submit(plan)
        queue = RenderQueue(
            self.ws.db, partial(rendering.generate_one, self.ws), platform=True
        )
        queue.heartbeat()

        def uncertain(*args, **kwargs):
            kwargs["on_request"]()
            raise TimeoutError("Simulated lost provider response")

        with patch.object(rendering, "render_live", side_effect=uncertain) as provider:
            with self.assertLogs(rendering.logger, level="ERROR"):
                queue.execute(queue.claim())
            queue.recover()
            self.assertIsNone(queue.claim())
            self.assertEqual(provider.call_count, 1)
        with self.ws.db() as c:
            self.assertEqual(
                c.execute("SELECT status FROM usage_ledger").fetchone()[0], "unknown"
            )
            self.assertEqual(
                {r[0] for r in c.execute("SELECT status FROM renders")}, {"failed"}
            )

    def test_stale_worker_with_saved_receipt_recovers_without_a_second_request(self):
        self.ws.settings = dataclasses.replace(
            self.ws.settings, mode="live", api_key="offline-fixture"
        )
        plan = self.plan()
        plan["products"][0]["outputs"] = [
            {"kind": "lifestyle", "scene": "Warm interior"}
        ]
        self.submit(plan)
        queue = RenderQueue(
            self.ws.db,
            partial(rendering.generate_one, self.ws),
            platform=True,
            saved_receipt=lambda rid: (
                self.ws.staging / (rid + ".receipt.json")
            ).is_file(),
        )
        row = queue.claim()
        from studioflow.services import usage

        attempt = usage.begin_attempt(self.ws.db, row)
        assets.atomic_write(
            self.ws.staging / (row["id"] + ".receipt.json"),
            json.dumps(
                {
                    "attempt": attempt,
                    "request_id": "saved-before-crash",
                    "usage": None,
                    "data": [{"b64_json": base64.b64encode(picture()).decode()}],
                }
            ).encode(),
        )
        # No worker heartbeat: simulate process loss after fsync, before its DB update.
        queue.recover()
        queue.heartbeat()
        with patch.object(
            rendering,
            "render_live",
            side_effect=AssertionError("Repeated a paid request"),
        ):
            queue.execute(queue.claim())
        with self.ws.db() as c:
            self.assertEqual(
                c.execute("SELECT status FROM renders").fetchone()[0], "completed"
            )
            self.assertEqual(
                c.execute("SELECT COUNT(*) FROM usage_ledger").fetchone()[0], 1
            )

    def test_unsupported_returned_resolution_keeps_master_and_receipt_for_review(self):
        self.ws.settings = dataclasses.replace(
            self.ws.settings,
            mode="live",
            api_key="offline-fixture",
            validated_high_res_models=("gpt-image-2",),
        )
        plan = self.plan()
        plan.update(model="gpt-image-2", native_size="2048x2048")
        plan["products"][0]["outputs"] = [
            {"kind": "lifestyle", "scene": "Warm interior"}
        ]
        self.assertEqual(self.submit(plan).status_code, 200)

        def provider(*args, **kwargs):
            kwargs["on_request"]()
            response = SimpleNamespace(
                data=[SimpleNamespace(b64_json=base64.b64encode(picture()).decode())],
                usage=None,
            )
            kwargs["on_response"](response)
            from studioflow.provider import write_response_image

            write_response_image(
                response, args[1], args[5], master_output=kwargs["master_output"]
            )

        with (
            patch.object(rendering, "render_live", side_effect=provider),
            self.assertLogs(rendering.logger, level="ERROR"),
        ):
            self.run_queue()
        with self.ws.db() as c:
            row = c.execute("SELECT * FROM renders").fetchone()
        self.assertEqual(row["status"], "failed")
        self.assertIn("different native resolution", row["error"])
        self.assertEqual(
            assets.get(self.ws, asset_identity(row["id"], "master"))["state"], "ready"
        )

    def test_export_limits_expiration_and_missing_objects(self):
        self.submit()
        self.run_queue()
        row = self.client.get("/api/library").json()["items"][0]
        response = self.client.post("/api/exports", json={"render_ids": [row["id"]]})
        eid = response.json()["id"]
        with self.ws.db() as c:
            c.execute(
                "UPDATE assets SET byte_size=? WHERE id=?",
                (512 * 1024 * 1024 + 1, row["output_asset_id"]),
            )
        self.assertEqual(
            self.client.post(
                "/api/exports/preview", json={"render_ids": [row["id"]]}
            ).status_code,
            413,
        )
        self.assertEqual(
            self.client.post(
                "/api/exports", json={"render_ids": ["x"] * 251}
            ).status_code,
            422,
        )
        asset = assets.get(self.ws, row["output_asset_id"])
        self.ws.storage.delete(asset["object_key"])
        library.process_exports(self.ws, "offline-export")
        status = self.client.get("/api/exports/" + eid).json()
        self.assertEqual(status["status"], "failed")
        self.assertFalse((self.ws.exports / (eid + ".zip")).exists())
        with self.ws.db() as c:
            c.execute("UPDATE exports SET expires_at=0 WHERE id=?", (eid,))
        self.assertEqual(
            self.client.get("/api/exports/" + eid + "/download").status_code, 404
        )

    def test_pending_asset_transfer_can_resume_independently(self):
        path = self.ws.staging / "offline-recover.png"
        path.write_bytes(picture())
        aid = assets.reserve(self.ws, path, "product_reference", "tester")
        assets.process_auxiliary(self.ws, "offline-transfer")
        self.assertEqual(assets.get(self.ws, aid)["state"], "ready")

    def test_export_names_keep_identifier_snapshot_after_catalog_edits(self):
        def catalog(asin):
            return self.client.post(
                "/api/catalog/import",
                files={
                    "file": (
                        "catalog.csv",
                        f"SKU,ASIN\nGREEN-PICKS,{asin}\n".encode(),
                        "text/csv",
                    )
                },
            )

        self.assertEqual(catalog("B012345678").status_code, 200)
        self.assertEqual(self.submit().status_code, 200)
        self.run_queue()
        self.assertEqual(catalog("B098765432").status_code, 200)
        rows = self.client.get("/api/library").json()["items"]
        preview = self.client.post(
            "/api/exports/preview", json={"render_ids": [r["id"] for r in rows]}
        ).json()
        self.assertEqual(
            {i["filename"] for i in preview["items"]},
            {"B012345678.MAIN.jpg", "B012345678.PT01.jpg", "B012345678.PT02.jpg"},
        )
        main = next(r for r in rows if r["kind"] == "main")
        download = self.client.get(
            "/api/renders/" + main["id"] + "/download?identifier=sku"
        )
        self.assertEqual(download.status_code, 200)
        self.assertIn("GREEN-PICKS.MAIN.jpg", download.headers["content-disposition"])

    def test_all_five_templates_render_locally_with_distinct_usage_layout(self):
        from studioflow.composition import TEMPLATES, compose

        source = self.ws.staging / "composition-scene.png"
        cutout = self.ws.staging / "composition-cutout.png"
        source.write_bytes(picture())
        cutout.write_bytes(picture(True))
        results = {}
        for template in TEMPLATES:
            recipe = self.recipe()
            recipe["template"] = template
            destination = self.ws.staging / (template + ".jpg")
            compose(source, destination, recipe, cutout=cutout)
            with Image.open(destination) as image:
                self.assertEqual((image.format, image.size), ("JPEG", (2000, 2000)))
            results[template] = destination.read_bytes()
        self.assertNotEqual(results["usage"], results["feature"])
