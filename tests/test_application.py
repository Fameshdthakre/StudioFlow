"""Application isolation and shipped-resource integration after modularization."""

import csv
import io
import re
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from studioflow.application import create_app
from studioflow.dependencies import current_user
from studioflow.settings import Settings
from tests.support import isolated_app


class ApplicationTests(unittest.TestCase):
    def test_explicit_factory_does_not_load_environment_or_create_data(self):
        with tempfile.TemporaryDirectory() as directory:
            data = Path(directory) / "not-created"
            settings = Settings(data, "demo", "", 1, {}, set(), True, False)
            with patch.object(
                Settings,
                "load",
                side_effect=AssertionError("Unexpected environment read"),
            ):
                first = create_app(settings, start_queue=False)
                second = create_app(settings, start_queue=False)
            self.assertFalse(data.exists())
            self.assertIsNot(first.state.workspace, second.state.workspace)

    def test_instances_isolate_sessions_data_features_and_dependencies(self):
        with (
            isolated_app() as (first, a),
            isolated_app(authenticated=False) as (second, b),
        ):
            self.assertNotEqual(first.db_path, second.db_path)
            self.assertIsNot(a.app.state.render_queue, b.app.state.render_queue)
            b.cookies.set("studio_session", a.cookies.get("studio_session"))
            self.assertEqual(b.get("/api/stats").status_code, 401)
            self.assertEqual(
                a.post(
                    "/api/aplus/profiles", json={"name": "Only first workspace"}
                ).status_code,
                200,
            )
            a.app.dependency_overrides[current_user] = lambda: "viewer"
            self.assertEqual(a.get("/api/aplus/profiles").json()["can_edit"], False)
            self.assertEqual(b.get("/api/aplus/profiles").status_code, 401)
            self.assertEqual(
                b.post(
                    "/api/login", json={"username": "tester", "password": "password"}
                ).status_code,
                200,
            )
            self.assertEqual(b.get("/api/aplus/profiles").json()["profiles"], [])
            second.settings = replace(second.settings, enable_aplus=False)
            self.assertEqual(b.get("/api/aplus/profiles").status_code, 404)
            self.assertEqual(len(a.get("/api/aplus/profiles").json()["profiles"]), 1)

    def test_browser_import_graph_and_template_are_shipped(self):
        with isolated_app() as (_, client):
            page = client.get("/").text
            self.assertIn('type="module"', page)
            styles = client.get("/static/css/styles.css")
            self.assertEqual(styles.status_code, 200)
            self.assertIn("text/css", styles.headers["content-type"])
            pending, visited = ["/static/js/app.js"], set()
            while pending:
                url = pending.pop()
                if url in visited:
                    continue
                visited.add(url)
                response = client.get(url)
                self.assertEqual(response.status_code, 200, url)
                self.assertIn("javascript", response.headers["content-type"], url)
                for relative in re.findall(
                    r"from\s+['\"](\./[^'\"]+\.js)['\"]", response.text
                ):
                    pending.append(url.rsplit("/", 1)[0] + "/" + relative[2:])
            self.assertEqual(len(visited), 11)
            response = client.get("/api/catalog/template")
            self.assertEqual(response.status_code, 200)
            rows = list(csv.DictReader(io.StringIO(response.text)))
            self.assertIn("SKU", rows[0])
            self.assertEqual(
                response.headers["content-disposition"],
                'attachment; filename="studioflow_product_template.csv"',
            )
