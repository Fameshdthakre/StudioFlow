"""Opt-in production-size library and concurrent reader exercise, without paid APIs."""

import os
import time
import unittest
from concurrent.futures import ThreadPoolExecutor

from sqlalchemy import insert, text

from studioflow.persistence.connection import engine
from studioflow.persistence.schema import assets, renders, jobs
from studioflow.services import library
from fastapi import HTTPException
from tests.support import isolated_app


@unittest.skipUnless(
    os.getenv("STUDIOFLOW_SCALE_TEST") == "1"
    and os.getenv("STUDIOFLOW_TEST_DATABASE_URL"),
    "Opt-in 100,000 record PostgreSQL exercise",
)
class ScaleTests(unittest.TestCase):
    def test_100000_assets_and_twenty_concurrent_library_readers(self):
        with isolated_app() as (workspace, _):
            db = engine(workspace.settings.database_url)
            with db.begin() as c:
                c.execute(
                    insert(jobs).values(
                        id="scale-job",
                        user="tester",
                        kind="lifestyle",
                        total=100000,
                        status="finished",
                    )
                )
                for start in range(0, 100000, 1000):
                    batch = [
                        {
                            "id": f"{i + 1:032x}",
                            "object_key": f"assets/00/{i + 1:032x}.jpg",
                            "role": "finished_image",
                            "state": "ready",
                            "mime": "image/jpeg",
                            "byte_size": 2000000,
                            "width": 2000,
                            "height": 2000,
                            "sha256": "0" * 64,
                        }
                        for i in range(start, start + 1000)
                    ]
                    c.execute(insert(assets), batch)
                    c.execute(
                        insert(renders),
                        [
                            {
                                "id": "r-" + row["id"],
                                "job_id": "scale-job",
                                "user": "tester",
                                "sku": "SKU-" + str(start + index),
                                "sku_snapshot": "SKU-" + str(start + index),
                                "kind": "lifestyle",
                                "status": "completed",
                                "variant": "PT01",
                                "enqueue_seq": start + index + 1,
                                "output_asset_id": row["id"],
                                "master_asset_id": row["id"],
                            }
                            for index, row in enumerate(batch)
                        ],
                    )
                c.execute(text("ANALYZE renders"))
            started = time.monotonic()
            with ThreadPoolExecutor(max_workers=20) as pool:
                pages = list(
                    pool.map(
                        lambda _: library.list_images(
                            workspace, status="completed", limit=60
                        ),
                        range(20),
                    )
                )
            self.assertTrue(all(len(page["items"]) == 60 for page in pages))
            next_page = library.list_images(
                workspace, cursor=pages[0]["next_cursor"], limit=60
            )
            self.assertFalse(
                {r["id"] for r in pages[0]["items"]}
                & {r["id"] for r in next_page["items"]}
            )
            with self.assertRaises(HTTPException) as error:
                library.list_images(workspace, selection=True)
            self.assertEqual(error.exception.status_code, 400)
            with db.connect() as c:
                query = c.execute(
                    text(
                        "EXPLAIN (FORMAT JSON) SELECT id FROM renders WHERE status='completed' AND enqueue_seq<50000 ORDER BY enqueue_seq DESC LIMIT 60"
                    )
                ).scalar()
            self.assertIn("Index", str(query))
            print(
                f"100,000 asset/render rows; 20 readers plus cursor checks: {time.monotonic() - started:.2f}s"
            )
