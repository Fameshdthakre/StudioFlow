"""Regression coverage for catalog imports and canonical product memory."""

import csv
import io
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from studioflow.database import SCHEMA, connect, initialize
from studioflow.dependencies import current_user
from studioflow.services import memory
from tests.support import isolated_app


class ProductMemoryTests(unittest.TestCase):
    def setUp(self):
        context = isolated_app()
        self.studio, self.client = context.__enter__()
        self.addCleanup(context.__exit__, None, None, None)
        self.client.app.dependency_overrides[current_user] = lambda: "tester"

    def import_rows(self, *rows):
        output = io.StringIO()
        writer = csv.DictWriter(
            output, fieldnames=["SKU", "ASIN", "Item Code", "Title", "Colour"]
        )
        writer.writeheader()
        writer.writerows(rows)
        return self.client.post(
            "/api/catalog/import",
            files={
                "file": ("catalog.csv", output.getvalue().encode("utf-8"), "text/csv")
            },
        )

    def import_product(self, **overrides):
        row = {
            "SKU": "SKU-1",
            "ASIN": "ASIN-1",
            "Item Code": "CODE-1",
            "Title": "Original bottle",
            "Colour": "Blue",
        }
        row.update(overrides)
        response = self.import_rows(row)
        self.assertEqual(response.status_code, 200, response.text)
        return response

    def test_aliases_share_manual_facts_rules_and_branding(self):
        self.import_product()
        brand = self.client.post("/api/memory/brands", json={"name": "Test brand"})
        self.assertEqual(brand.status_code, 200, brand.text)
        brand_id = brand.json()["id"]
        self.assertEqual(
            self.client.put(
                "/api/memory/products/asin-1",
                json={"facts": "Verified capacity is 750ml"},
            ).status_code,
            200,
        )
        rule = self.client.post(
            "/api/memory/products/code-1/rules",
            json={"instruction": "Keep the original blue cap"},
        )
        self.assertEqual(rule.status_code, 200, rule.text)
        self.assertEqual(
            self.client.put(
                "/api/memory/products/ASIN-1/brand", json={"brand_id": brand_id}
            ).status_code,
            200,
        )

        for identifier in ("SKU-1", "ASIN-1", "CODE-1"):
            with self.subTest(identifier=identifier):
                record = self.client.get("/api/memory/products/" + identifier).json()
                self.assertEqual(record["sku"], "SKU-1")
                self.assertEqual(record["facts"], "Verified capacity is 750ml")
                self.assertEqual(
                    record["rules"][0]["instruction"], "Keep the original blue cap"
                )
                self.assertEqual(record["brand_id"], brand_id)
                snapshot = json.loads(memory.memory_snapshot(self.studio, identifier))
                self.assertIn("Verified capacity is 750ml", snapshot["facts"])
                self.assertEqual(snapshot["rules"], ["Keep the original blue cap"])
                self.assertEqual(snapshot["branding"]["id"], brand_id)
        with self.studio.db() as connection:
            keys = [
                row["sku"]
                for row in connection.execute("SELECT sku FROM product_memory")
            ]
        self.assertEqual(keys, ["SKU-1"])

    def test_reimport_preserves_manual_facts_and_refreshes_catalog_facts(self):
        self.import_product()
        self.client.put(
            "/api/memory/products/ASIN-1", json={"facts": "Verified capacity is 750ml"}
        )
        self.import_product(Title="Updated bottle")
        record = self.client.get("/api/memory/products/SKU-1").json()
        self.assertEqual(record["facts"], "Verified capacity is 750ml")
        self.assertIn("Updated bottle", record["catalog_facts"])
        snapshot = json.loads(memory.memory_snapshot(self.studio, "CODE-1"))
        self.assertIn("Verified capacity is 750ml", snapshot["facts"])
        self.assertIn("Updated bottle", snapshot["facts"])
        self.assertNotIn("Original bottle", snapshot["facts"])

    def test_legacy_imported_copy_does_not_survive_as_stale_manual_facts(self):
        self.import_product()
        with self.studio.db() as connection:
            summary = connection.execute(
                "SELECT facts_summary FROM catalog_products"
            ).fetchone()["facts_summary"]
            connection.execute(
                "UPDATE product_memory SET facts=? WHERE sku=?", (summary, "SKU-1")
            )
        self.import_product(Title="Updated bottle")
        record = self.client.get("/api/memory/products/SKU-1").json()
        self.assertEqual(record["facts"], "")
        self.assertNotIn(
            "Original bottle",
            json.loads(memory.memory_snapshot(self.studio, "SKU-1"))["facts"],
        )

    def test_manually_saved_catalog_copy_is_preserved_by_audit_provenance(self):
        self.import_product()
        with self.studio.db() as connection:
            summary = connection.execute(
                "SELECT facts_summary FROM catalog_products"
            ).fetchone()["facts_summary"]
        self.assertEqual(
            self.client.put(
                "/api/memory/products/SKU-1", json={"facts": summary}
            ).status_code,
            200,
        )
        self.import_product(Title="Updated bottle")
        self.assertEqual(
            self.client.get("/api/memory/products/SKU-1").json()["facts"], summary
        )

    def test_invalid_identifier_rolls_back_the_entire_import(self):
        response = self.import_rows(
            {"SKU": "VALID-1", "Title": "Valid item"},
            {"SKU": "BAD/SKU", "Title": "Invalid item"},
        )
        self.assertEqual(response.status_code, 400, response.text)
        self.assertIn("CSV row 3", response.json()["detail"])
        with self.studio.db() as connection:
            self.assertEqual(
                connection.execute("SELECT COUNT(*) FROM catalog_products").fetchone()[
                    0
                ],
                0,
            )
            self.assertEqual(
                connection.execute("SELECT COUNT(*) FROM product_memory").fetchone()[0],
                0,
            )

    def test_case_insensitive_reimport_updates_one_catalog_product(self):
        self.import_product(SKU="sku-1")
        response = self.import_product(SKU="SKU-1", Title="Updated bottle")
        self.assertEqual(response.json()["updated"], 1)
        self.assertEqual(response.json()["total"], 1)

    def test_ambiguous_alias_requires_exact_sku(self):
        self.import_product()
        self.import_product(SKU="SKU-2", **{"Item Code": "CODE-2"})
        response = self.client.get("/api/memory/products/ASIN-1")
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(self.client.get("/api/memory/products/SKU-1").status_code, 200)
        self.assertEqual(self.client.get("/api/memory/products/SKU-2").status_code, 200)

    def test_ambiguous_filename_match_does_not_choose_an_arbitrary_product(self):
        self.import_product()
        self.import_product(SKU="SKU-2", **{"Item Code": "CODE-2"})
        response = self.client.post(
            "/api/catalog/match-files", json={"filenames": ["ASIN-1.jpg"]}
        )
        self.assertEqual(response.status_code, 200, response.text)
        match = response.json()["matches"]["ASIN-1.jpg"]
        self.assertFalse(match["matched"])
        self.assertTrue(match["ambiguous"])

    def test_catalog_facts_are_separate_from_the_manual_editor(self):
        self.import_product()
        record = self.client.get("/api/memory/products/SKU-1").json()
        self.assertEqual(record["facts"], "")
        self.assertIn("Original bottle", record["catalog_facts"])

    def test_snapshot_can_share_the_callers_transaction(self):
        self.import_product()
        with self.studio.db() as connection:
            connection.execute(
                "UPDATE product_memory SET facts=? WHERE sku=?",
                ("Uncommitted verified fact", "SKU-1"),
            )
            snapshot = json.loads(
                memory.memory_snapshot(self.studio, "ASIN-1", connection=connection)
            )
            self.assertIn("Uncommitted verified fact", snapshot["facts"])


class CatalogMigrationTests(unittest.TestCase):
    def test_new_catalog_rejects_case_insensitive_duplicate_skus(self):
        with tempfile.TemporaryDirectory(prefix="studioflow-migration-") as directory:
            path = Path(directory) / "test.sqlite3"
            initialize(path)
            with connect(path) as connection:
                connection.execute(
                    "INSERT INTO catalog_products(id,sku) VALUES(?,?)",
                    ("first", "SKU-1"),
                )
            with self.assertRaises(sqlite3.IntegrityError):
                with connect(path) as connection:
                    connection.execute(
                        "INSERT INTO catalog_products(id,sku) VALUES(?,?)",
                        ("second", "sku-1"),
                    )

    def test_legacy_duplicates_are_preserved_and_migration_retries_after_resolution(
        self,
    ):
        with tempfile.TemporaryDirectory(prefix="studioflow-migration-") as directory:
            path = Path(directory) / "test.sqlite3"
            with connect(path) as connection:
                connection.executescript(SCHEMA)
                connection.execute(
                    "INSERT INTO catalog_products(id,sku,title) VALUES(?,?,?)",
                    ("first", "SKU-1", "First record"),
                )
                connection.execute(
                    "INSERT INTO catalog_products(id,sku,title) VALUES(?,?,?)",
                    ("second", "sku-1", "Second record"),
                )
            with self.assertLogs(
                "studioflow.migrations.v002_catalog_uniqueness", level="WARNING"
            ):
                initialize(path)
            with connect(path) as connection:
                self.assertEqual(
                    connection.execute(
                        "SELECT COUNT(*) FROM catalog_products"
                    ).fetchone()[0],
                    2,
                )
                self.assertIsNone(
                    connection.execute(
                        "SELECT 1 FROM schema_migrations WHERE version=2"
                    ).fetchone()
                )
                connection.execute(
                    "UPDATE catalog_products SET sku=? WHERE id=?", ("SKU-2", "second")
                )
            initialize(path)
            with connect(path) as connection:
                self.assertIsNotNone(
                    connection.execute(
                        "SELECT 1 FROM schema_migrations WHERE version=2"
                    ).fetchone()
                )
                self.assertEqual(
                    connection.execute(
                        "SELECT COUNT(*) FROM catalog_products"
                    ).fetchone()[0],
                    2,
                )


if __name__ == "__main__":
    unittest.main()
