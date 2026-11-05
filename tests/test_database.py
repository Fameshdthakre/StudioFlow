"""Migration safety for an existing catalog, sessions, memory and render history."""

import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient
from PIL import Image

from studioflow.application import create_app
from studioflow.database import SCHEMA, connect, initialize
from studioflow.persistence.repository import initialize_preview
from studioflow.services import assets
from studioflow.settings import Settings
from studioflow.workspace import Workspace


class MigrationTests(unittest.TestCase):
    def test_existing_preview_assets_upgrade_before_background_transfers(self):
        with tempfile.TemporaryDirectory() as directory:
            settings = Settings(Path(directory), "demo", "", 1, {}, set(), True, False)
            workspace = Workspace(settings, Path(__file__).resolve().parents[1])
            initialize(workspace.db_path)
            initialize_preview(workspace)
            source = workspace.data / "reference.png"
            Image.new("RGBA", (160, 160), (0, 120, 0, 100)).save(source)
            ready = assets.publish(workspace, source, "product_reference", "owner")
            pending = assets.reserve(workspace, source, "generated_master", "owner")
            with workspace.db() as c:
                c.execute(
                    "UPDATE assets SET state='transferring' WHERE id=?", (pending,)
                )
                # Reproduce the preview schema that predates transfer leases.
                c.execute("ALTER TABLE assets DROP COLUMN transfer_worker_id")
                before = [
                    dict(row) for row in c.execute("SELECT * FROM assets ORDER BY id")
                ]
            app = create_app(settings, start_queue=False)
            with TestClient(
                app, base_url="http://localhost", client=("127.0.0.1", 50000)
            ) as client:
                self.assertEqual(client.get("/api/health").status_code, 200)
                initialize_preview(workspace)  # Repeated startup is safe.
                with workspace.db() as c:
                    after = [
                        dict(row)
                        for row in c.execute("SELECT * FROM assets ORDER BY id")
                    ]
                    for row in after:
                        self.assertEqual(row.pop("transfer_worker_id"), "")
                    self.assertEqual(after, before)
                    self.assertEqual(
                        c.execute("PRAGMA integrity_check").fetchone()[0], "ok"
                    )
                assets.process_auxiliary(workspace, "recovered-worker")
                self.assertEqual(assets.get(workspace, pending)["state"], "ready")
                for asset_id in (ready, pending):
                    with assets.materialize(workspace, asset_id) as path:
                        self.assertEqual(path.read_bytes(), source.read_bytes())

    def test_additive_migrations_preserve_legacy_data_and_are_repeatable(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "legacy.sqlite3"
            with connect(path) as c:
                c.executescript(SCHEMA)
                c.execute(
                    "INSERT INTO sessions VALUES('session-hash','owner','created','expires')"
                )
                c.execute(
                    "INSERT INTO jobs(id,user,kind,total,status) VALUES('job','owner','main',1,'finished')"
                )
                c.execute(
                    "INSERT INTO renders(id,job_id,user,sku,kind,status,file) VALUES('render','job','owner','SKU-1','main','completed','original.jpg')"
                )
                c.execute(
                    "INSERT INTO product_memory(sku,facts) VALUES('SKU-1','Verified facts')"
                )
                c.execute(
                    "INSERT INTO catalog_products(id,sku,asin,title) VALUES('product','SKU-1','ASIN-1','Original title')"
                )
                c.execute(
                    "INSERT INTO product_rules(id,sku,instruction) VALUES('rule','SKU-1','Approved rule')"
                )
                c.execute("INSERT INTO brands(id,name) VALUES('brand','Saved brand')")
                tables = [
                    row[0]
                    for row in c.execute(
                        "SELECT name FROM sqlite_master WHERE type='table'"
                    )
                ]
                columns = {
                    name: [
                        row["name"] for row in c.execute(f"PRAGMA table_info({name})")
                    ]
                    for name in tables
                }
                before = {
                    name: [tuple(row) for row in c.execute(f"SELECT * FROM {name}")]
                    for name in tables
                }
            initialize(path)
            initialize(path)
            with connect(path) as c:
                for name in tables:
                    selected = ",".join(columns[name])
                    self.assertEqual(
                        [
                            tuple(row)
                            for row in c.execute(f"SELECT {selected} FROM {name}")
                        ],
                        before[name],
                        name,
                    )
                self.assertEqual(
                    [
                        row[0]
                        for row in c.execute(
                            "SELECT version FROM schema_migrations ORDER BY version"
                        )
                    ],
                    [1, 2, 3, 4],
                )
                self.assertEqual(
                    c.execute("SELECT aplus_profile_id FROM renders").fetchone()[0], ""
                )
                self.assertEqual(
                    c.execute("SELECT COUNT(*) FROM usage_ledger").fetchone()[0], 0
                )
                self.assertEqual(
                    c.execute("PRAGMA integrity_check").fetchone()[0], "ok"
                )
                self.assertEqual(c.execute("PRAGMA foreign_key_check").fetchall(), [])


if __name__ == "__main__":
    unittest.main()
