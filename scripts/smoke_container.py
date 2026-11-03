"""Exercise the built container with disposable demo data and no provider key."""

import io
import os
import re
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path

import httpx
from PIL import Image


def smoke(image):
    # Resolve the exact production Compose file in CI before deployment can run.
    with tempfile.TemporaryDirectory(prefix="studioflow-compose-check-") as directory:
        base = Path(directory)
        (base / "data").mkdir()
        (base / "runtime.env").write_text(
            "APP_MODE=demo\nTEAM_USERS=ci:disposable-test-password\n", encoding="utf-8"
        )
        env = dict(
            os.environ,
            STUDIOFLOW_IMAGE=image,
            STUDIOFLOW_DATA_PATH=str(base / "data"),
            STUDIOFLOW_ENV_FILE=str(base / "runtime.env"),
            STUDIOFLOW_BIND_ADDRESS="127.0.0.1",
        )
        subprocess.run(
            [
                "docker",
                "compose",
                "--env-file",
                str(base / "runtime.env"),
                "-f",
                str(
                    Path(__file__).resolve().parents[1]
                    / "deploy/compose.production.yml"
                ),
                "config",
                "--quiet",
            ],
            check=True,
            env=env,
        )
    name = "studioflow-ci-" + uuid.uuid4().hex[:10]
    try:
        subprocess.run(
            [
                "docker",
                "run",
                "-d",
                "--name",
                name,
                "-p",
                "127.0.0.1::8000",
                "-e",
                "APP_MODE=demo",
                "-e",
                "OPENAI_API_KEY=",
                "-e",
                "TEAM_USERS=ci-smoke:disposable-test-password",
                "-e",
                "ADMIN_USERS=ci-smoke",
                "-e",
                "STUDIOFLOW_REQUIRE_AUTH=1",
                "-e",
                "STUDIOFLOW_LOAD_DOTENV=0",
                "-e",
                "STUDIOFLOW_DATA_DIR=/app/data",
                image,
            ],
            check=True,
        )
        address = subprocess.check_output(
            ["docker", "port", name, "8000/tcp"], text=True
        ).strip()
        with httpx.Client(
            base_url="http://" + address, timeout=10, trust_env=False
        ) as client:
            deadline = time.monotonic() + 90
            while True:
                try:
                    client.get("/api/health").raise_for_status()
                    break
                except httpx.HTTPError:
                    if time.monotonic() >= deadline:
                        raise RuntimeError("Container never became healthy")
                    time.sleep(1)
            assert client.get("/api/renders").status_code == 401
            page = client.get("/")
            page.raise_for_status()
            assert 'type="module"' in page.text
            client.get("/static/css/styles.css").raise_for_status()
            pending, visited = ["/static/js/app.js"], set()
            while pending:
                url = pending.pop()
                if url in visited:
                    continue
                visited.add(url)
                module = client.get(url)
                module.raise_for_status()
                for relative in re.findall(
                    r"from\s+['\"](\./[^'\"]+\.js)['\"]", module.text
                ):
                    pending.append(url.rsplit("/", 1)[0] + "/" + relative[2:])
            template = client.get("/api/catalog/template")
            template.raise_for_status()
            assert "SKU" in template.text and "ASIN" in template.text
            client.post(
                "/api/login",
                json={"username": "ci-smoke", "password": "disposable-test-password"},
            ).raise_for_status()
            profile = client.post(
                "/api/aplus/profiles", json={"name": "Container smoke profile"}
            )
            profile.raise_for_status()
            photo = io.BytesIO()
            Image.new("RGB", (160, 160), "blue").save(photo, "PNG")
            for canvas in ("banner", "mobile"):
                response = client.post(
                    "/api/generate",
                    data={
                        "kind": "aplus",
                        "aplus_profile_id": profile.json()["id"],
                        "skus": '["CI-SKU"]',
                        "scenes": "A simple product background",
                        "canvas": canvas,
                    },
                    files={"files": ("ci.png", photo.getvalue(), "image/png")},
                )
                response.raise_for_status()
            deadline = time.monotonic() + 60
            while True:
                response = client.get("/api/renders")
                response.raise_for_status()
                renders = response.json()
                if len(renders) == 2 and all(
                    row["status"] == "completed" for row in renders
                ):
                    break
                if (
                    any(row["status"] == "failed" for row in renders)
                    or time.monotonic() >= deadline
                ):
                    raise RuntimeError("Container demo generation failed")
                time.sleep(0.5)
            for row in renders:
                asset = client.get(row["url"])
                asset.raise_for_status()
                with Image.open(io.BytesIO(asset.content)) as result:
                    assert (
                        result.size
                        == {"banner": (1464, 600), "mobile": (600, 450)}[row["canvas"]]
                    )
            assert client.get("/api/usage").json()["requests"] == 0
            print(
                "Container assets/template, authentication, A+ generation, image export and demo metering passed."
            )
    finally:
        # This unique disposable CI container has no production volume.
        subprocess.run(
            ["docker", "rm", "-f", name], check=False, stdout=subprocess.DEVNULL
        )


if __name__ == "__main__":
    smoke(sys.argv[1])
