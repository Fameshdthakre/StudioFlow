"""Disposable demo server used only by browser acceptance tests."""

import os
from pathlib import Path
import uvicorn
from studioflow.application import create_app
from tests.support import isolated_app
from tests.test_platform import picture

ROOT = Path(__file__).resolve().parents[1]
fixtures = ROOT / ".tmp/browser-fixtures"
fixtures.mkdir(parents=True, exist_ok=True)
(fixtures / "ui-reference.png").write_bytes(picture())
(fixtures / "ui-cutout.png").write_bytes(picture(True))
with isolated_app(authenticated=False) as (workspace, _):
    uvicorn.run(
        create_app(workspace.settings),
        host="127.0.0.1",
        port=int(os.getenv("STUDIOFLOW_BROWSER_PORT", "18001")),
        log_level="warning",
    )
