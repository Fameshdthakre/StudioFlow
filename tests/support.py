"""Load each test application against a fresh temporary database."""
import importlib.util
import os
import sys
import tempfile
import uuid
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient


@contextmanager
def isolated_app(*, authenticated=True, anonymous=False, start_queue=False):
    with tempfile.TemporaryDirectory(prefix='studioflow-test-') as directory:
        env = {
            'STUDIOFLOW_LOAD_DOTENV': '0',
            'STUDIOFLOW_DATA_DIR': directory,
            'APP_MODE': 'demo',
            'OPENAI_API_KEY': '',
            'TEAM_USERS': '' if anonymous else 'tester:password',
            'ADMIN_USERS': '' if anonymous else 'tester',
            'ALLOW_ANONYMOUS_DEMO': '1' if anonymous else '0',
            'STUDIOFLOW_REQUIRE_AUTH': '0',
            'COOKIE_SECURE': '0',
            'MAX_WORKERS': '1',
            'STUDIOFLOW_ENABLE_APLUS': '1',
            'STUDIOFLOW_ENABLE_USAGE': '1',
        }
        with patch.dict(os.environ, env):
            name = 'studioflow_test_' + uuid.uuid4().hex
            spec = importlib.util.spec_from_file_location(name, Path(__file__).resolve().parents[1] / 'app.py')
            module = importlib.util.module_from_spec(spec)
            sys.modules[name] = module
            try:
                spec.loader.exec_module(module)
                with patch.object(module.RenderQueue, 'start', autospec=True) if not start_queue else _unchanged():
                    with TestClient(module.app, base_url='http://localhost', client=('127.0.0.1', 50000)) as client:
                        if authenticated and not anonymous:
                            response = client.post('/api/login', json={'username': 'tester', 'password': 'password'})
                            assert response.status_code == 200, response.text
                        yield module, client
            finally:
                sys.modules.pop(name, None)


@contextmanager
def _unchanged():
    yield
