"""Create independent test applications without touching the runtime environment."""

import tempfile
import os
import uuid
import dataclasses
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
        test_url = os.getenv("STUDIOFLOW_TEST_DATABASE_URL")
        if test_url:
            from sqlalchemy import create_engine, text
            from sqlalchemy.engine import make_url
            from studioflow.operations.cli import initialize
            from studioflow.workspace import Workspace

            target = make_url(test_url).set(
                database="studioflow_test_" + uuid.uuid4().hex
            )
            admin = create_engine(test_url, isolation_level="AUTOCOMMIT")
            with admin.connect() as c:
                c.execute(text('CREATE DATABASE "' + target.database + '"'))
            settings = dataclasses.replace(
                settings, database_url=target.render_as_string(hide_password=False)
            )
            endpoint = os.getenv("STUDIOFLOW_TEST_S3_ENDPOINT")
            if endpoint:
                settings = dataclasses.replace(
                    settings,
                    storage_backend="s3",
                    s3_endpoint=endpoint,
                    s3_bucket=target.database.replace("_", "-"),
                    s3_access_key="test-access",
                    s3_secret_key="test-secret-long-enough",
                )
            initialize(Workspace(settings, Path(__file__).resolve().parents[1]))
        app = create_app(settings, start_queue=start_queue)
        try:
            with TestClient(
                app, base_url="http://localhost", client=("127.0.0.1", 50000)
            ) as client:
                if authenticated and not anonymous:
                    response = client.post(
                        "/api/login",
                        json={"username": "tester", "password": "password"},
                    )
                    assert response.status_code == 200, response.text
                yield app.state.workspace, client
        finally:
            if test_url:
                from studioflow.persistence.connection import engine

                engine(settings.database_url).dispose()
                with admin.connect() as c:
                    c.execute(
                        text('DROP DATABASE "' + target.database + '" WITH (FORCE)')
                    )
                admin.dispose()
                if endpoint:
                    storage = app.state.workspace.storage
                    while True:
                        batch = storage.client.list_objects_v2(
                            Bucket=storage.bucket
                        ).get("Contents", [])
                        if not batch:
                            break
                        storage.client.delete_objects(
                            Bucket=storage.bucket,
                            Delete={"Objects": [{"Key": o["Key"]} for o in batch]},
                        )
                    storage.client.delete_bucket(Bucket=storage.bucket)
