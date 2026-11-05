"""Real database/object-store migration and encrypted backup rehearsal."""

import dataclasses
import json
import os
import subprocess
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

from PIL import Image
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from studioflow.database import initialize, connect
from studioflow.application import create_app
from studioflow.jobs import RenderQueue
from studioflow.operations import migration
from studioflow.operations.backup import backup, restore, restic_env
from studioflow.operations.cli import preflight, activate
from studioflow.persistence.connection import engine
from studioflow.services import assets, maintenance, rendering, usage
from studioflow.workspace import Workspace
from tests.support import isolated_app


def legacy_fixture(root):
    root.mkdir()
    (root / "uploads").mkdir()
    (root / "outputs").mkdir()
    Image.new("RGB", (200, 200), "#126644").save(root / "uploads/reference.png")
    Image.new("RGB", (2000, 2000), "#126644").save(root / "outputs/finished.jpg")
    initialize(root / "studio.sqlite3")
    with connect(root / "studio.sqlite3") as c:
        c.execute(
            "INSERT INTO jobs VALUES('job-legacy','tester','main','2026-10-01',1,'finished')"
        )
        c.execute("""INSERT INTO renders(id,job_id,user,sku,kind,source,original,prompt,model,quality,canvas,file,status,approved,created_at,memory_snapshot)
            VALUES('render-legacy','job-legacy','tester','SKU-1','main','reference.png','','White background','gpt-image-2','high','square','finished.jpg','completed',1,'2026-10-01','{}')""")
        c.execute(
            "INSERT INTO catalog_products(id,sku,asin) VALUES('catalog-public','SKU-1','B012345678')"
        )
        c.execute(
            "INSERT INTO sessions VALUES('legacy-session','tester','2026-10-01','2099-01-01')"
        )
        c.execute(
            "INSERT INTO product_memory(sku,facts) VALUES('SKU-1','Verified green item')"
        )
        c.execute(
            "INSERT INTO product_rules VALUES('rule-public','SKU-1','Keep the colour green','tester','2026-10-01','render-legacy')"
        )
        c.execute("""INSERT INTO usage_ledger(attempt_id,render_id,job_id,user,model,kind,status,cost_microusd,started_at,rate_card_version,rates_json)
            VALUES('attempt-public','render-legacy','job-legacy','tester','gpt-image-2','main','completed',12345,'2026-10-01','fixture','{}')""")
        c.execute(
            "INSERT INTO idempotency VALUES('tester','old-submission-key','generate','old-fingerprint','{\"job_id\":\"job-legacy\"}','2026-10-01')"
        )


class MigrationAuditTests(unittest.TestCase):
    def test_readonly_audit_detects_missing_referenced_images(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "legacy"
            legacy_fixture(root)
            before = (root / "studio.sqlite3").read_bytes()
            report = migration.audit(root)
            self.assertEqual(report["conflicts"], [])
            self.assertEqual(report["cost_microusd"], 12345)
            self.assertEqual((root / "studio.sqlite3").read_bytes(), before)
            (root / "outputs/finished.jpg").unlink()
            self.assertIn(
                "Missing or unsafe referenced asset: outputs/finished.jpg",
                migration.audit(root)["conflicts"],
            )


@unittest.skipUnless(
    os.getenv("STUDIOFLOW_TEST_DATABASE_URL")
    and os.getenv("STUDIOFLOW_TEST_S3_ENDPOINT"),
    "Requires isolated PostgreSQL and SeaweedFS",
)
class PlatformOperationsTests(unittest.TestCase):
    def test_resumable_migration_preserves_ids_accounting_sessions_and_checksums(self):
        with (
            tempfile.TemporaryDirectory() as directory,
            isolated_app(authenticated=False) as (ws, client),
        ):
            root = Path(directory) / "legacy"
            legacy_fixture(root)
            result = migration.migrate(ws, root)
            again = migration.migrate(ws, root)
            self.assertEqual(result["counts"], again["counts"])
            self.assertEqual(result["cost_microusd"], 12345)
            client.cookies.set("studioflow_session", "legacy-session")
            with ws.db() as c:
                self.assertEqual(
                    c.execute("SELECT id FROM catalog_products").fetchone()[0],
                    "catalog-public",
                )
                self.assertEqual(
                    c.execute("SELECT token FROM sessions").fetchone()[0],
                    "legacy-session",
                )
                row = c.execute("SELECT * FROM renders").fetchone()
                self.assertEqual(row["id"], "render-legacy")
                self.assertEqual(row["variant"], "")
                self.assertTrue(row["product_id"])
                self.assertTrue(row["thumbnail_asset_id"])
                self.assertEqual(
                    c.execute("SELECT ledger_boundary FROM usage_budget").fetchone(),
                    None,
                )
            with assets.materialize(ws, row["output_asset_id"]) as path:
                self.assertEqual(
                    path.read_bytes(), (root / "outputs/finished.jpg").read_bytes()
                )
            with self.assertRaisesRegex(RuntimeError, "cutover"):
                preflight(ws)

    @unittest.skipUnless(
        os.getenv("STUDIOFLOW_RESTIC") and os.getenv("STUDIOFLOW_PG_DUMP"),
        "Requires Restic and PostgreSQL tools",
    )
    def test_backup_restores_into_empty_stack_without_repeating_paid_work(self):
        with (
            tempfile.TemporaryDirectory() as directory,
            isolated_app(authenticated=False) as (ws, _),
        ):
            base = Path(directory)
            legacy = base / "legacy"
            legacy_fixture(legacy)
            migration.migrate(ws, legacy)
            staged = ws.staging / "pending-reference.png"
            Image.new("RGB", (200, 200), "#661244").save(staged)
            pending_id = assets.reserve(ws, staged, "product_reference", "tester")
            with ws.db() as c:
                c.execute(
                    "INSERT INTO renders(id,job_id,user,sku,kind,model,quality,canvas,created_at,status,worker_id,enqueue_seq) VALUES('uncertain','job-legacy','tester','SKU-1','lifestyle','gpt-image-2','high','square','2026-10-01','running','lost-worker',100)"
                )
                row = dict(
                    c.execute("SELECT * FROM renders WHERE id='uncertain'").fetchone()
                )
            usage.begin_attempt(ws.db, row)
            password = base / "restic-password"
            password.write_text(uuid.uuid4().hex)
            env = {
                "STUDIOFLOW_LOAD_DOTENV": "0",
                "APP_MODE": "demo",
                "TEAM_USERS": "tester:password",
                "DATABASE_URL": ws.settings.database_url,
                "STUDIOFLOW_DATA_DIR": str(ws.data),
                "STUDIOFLOW_STORAGE": "s3",
                "S3_ENDPOINT": ws.settings.s3_endpoint,
                "S3_BUCKET": ws.settings.s3_bucket,
                "S3_ACCESS_KEY_ID": "test-access",
                "S3_SECRET_ACCESS_KEY": "test-secret-long-enough",
                "RESTIC_REPOSITORY": str(base / "repository"),
                "RESTIC_PASSWORD_FILE": str(password),
            }
            with patch.dict(os.environ, env):
                subprocess.run(
                    [os.environ["STUDIOFLOW_RESTIC"], "init"],
                    env=restic_env(external=False),
                    check=True,
                    capture_output=True,
                )
                receipt = backup(ws, external=False)
                target_url = make_url(ws.settings.database_url).set(
                    database="studioflow_test_" + uuid.uuid4().hex
                )
                admin = create_engine(
                    os.environ["STUDIOFLOW_TEST_DATABASE_URL"],
                    isolation_level="AUTOCOMMIT",
                )
                with admin.connect() as c:
                    c.execute(text('CREATE DATABASE "' + target_url.database + '"'))
                restored = Workspace(
                    dataclasses.replace(
                        ws.settings,
                        data_dir=base / "restored",
                        database_url=target_url.render_as_string(hide_password=False),
                        s3_bucket=target_url.database.replace("_", "-"),
                    ),
                    ws.base_dir,
                )
                restored.staging.mkdir(parents=True)
                try:
                    proof = restore(restored, receipt["snapshot_id"], external=False)
                    self.assertEqual(proof["counts"]["renders"], 2)
                    self.assertEqual(proof["asset_count"], 4)
                    self.assertFalse(proof["external"])
                    self.assertEqual(
                        maintenance.state(restored)["maintenance"]["value"], "1"
                    )
                    proof_file = base / "proof.json"
                    proof_file.write_text(json.dumps(proof))
                    with self.assertRaisesRegex(RuntimeError, "external restore"):
                        activate(ws, proof_file)
                    with self.assertRaisesRegex(RuntimeError, "empty PostgreSQL"):
                        restore(restored, receipt["snapshot_id"], external=False)
                    self.assertEqual(
                        assets.get(restored, pending_id, ready=False)["state"],
                        "pending",
                    )
                    with restored.db() as c:
                        self.assertEqual(
                            c.execute(
                                "SELECT status FROM usage_ledger WHERE render_id='uncertain'"
                            ).fetchone()[0],
                            "unknown",
                        )
                    maintenance.set_value(restored, "maintenance", "0")
                    with patch.object(
                        rendering,
                        "render_live",
                        side_effect=AssertionError("Restore repeated a paid request"),
                    ):
                        assets.process_auxiliary(restored, "restored-worker")
                        self.assertIsNone(
                            RenderQueue(
                                restored.db, lambda *args: None, platform=True
                            ).claim()
                        )
                    self.assertEqual(assets.get(restored, pending_id)["state"], "ready")
                    with TestClient(
                        create_app(restored.settings, start_queue=False)
                    ) as client:
                        login = client.post(
                            "/api/login",
                            json={"username": "tester", "password": "password"},
                        )
                        self.assertEqual(login.status_code, 200)
                        self.assertEqual(
                            len(client.get("/api/library").json()["items"]), 2
                        )
                finally:
                    engine(restored.settings.database_url).dispose()
                    with admin.connect() as c:
                        c.execute(
                            text(
                                'DROP DATABASE "'
                                + target_url.database
                                + '" WITH (FORCE)'
                            )
                        )
                    admin.dispose()
                    batch = restored.storage.client.list_objects_v2(
                        Bucket=restored.storage.bucket
                    ).get("Contents", [])
                    if batch:
                        restored.storage.client.delete_objects(
                            Bucket=restored.storage.bucket,
                            Delete={"Objects": [{"Key": a["Key"]} for a in batch]},
                        )
                    restored.storage.client.delete_bucket(
                        Bucket=restored.storage.bucket
                    )
