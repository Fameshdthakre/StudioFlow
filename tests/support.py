"""Create independent test applications without touching the runtime environment."""

import tempfile
from contextlib import contextmanager
from pathlib import Path

from fastapi.testclient import TestClient

from studioflow.application import create_app
from studioflow.settings import Settings


@contextmanager
def isolated_app(*, authenticated=True, anonymous=False, start_queue=False):
    with tempfile.TemporaryDirectory(prefix="studioflow-test-") as directory:
        settings = Settings(
            data_dir=Path(directory),
            mode="demo",
            api_key="",
            max_workers=1,
            team={} if anonymous else {"tester": "password"},
            admins=set() if anonymous else {"tester"},
            anonymous_demo=anonymous,
            secure_cookie=False,
        )
        app = create_app(settings, start_queue=start_queue)
        with TestClient(
            app, base_url="http://localhost", client=("127.0.0.1", 50000)
        ) as client:
            if authenticated and not anonymous:
                response = client.post(
                    "/api/login", json={"username": "tester", "password": "password"}
                )
                assert response.status_code == 200, response.text
            yield app.state.workspace, client
