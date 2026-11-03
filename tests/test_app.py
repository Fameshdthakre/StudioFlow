"""API regressions run against temporary databases and offline demo images."""
import io
import json
import os
import tempfile
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from studioflow.settings import Settings
from tests.support import isolated_app


def photo(color='blue'):
    result = io.BytesIO()
    Image.new('RGB', (160, 160), color).save(result, 'PNG')
    return result.getvalue()


class SubmissionTests(unittest.TestCase):
    def setUp(self):
        context = isolated_app()
        self.studio, self.client = context.__enter__()
        self.addCleanup(context.__exit__, None, None, None)

    def submit(self, *, key='request-0001', image=None, **changes):
        fields = {'skus': '["SKU-1"]', 'kind': 'main', 'scenes': 'White product background',
                  'model': 'gpt-image-2.5-flare', 'quality': 'medium', 'canvas': 'square'}
        fields.update(changes)
        return self.client.post('/api/generate', data=fields,
                                files=[('files', ('source.png', image if image is not None else photo(), 'image/png'))],
                                headers={'Idempotency-Key': key})

    def test_retry_returns_same_job_and_preserves_one_source(self):
        first = self.submit()
        second = self.submit()
        self.assertEqual(first.status_code, 200, first.text)
        self.assertEqual(second.json(), first.json())
        self.assertEqual(len(list(self.studio.UPLOADS.iterdir())), 1)
        self.assertEqual(len(self.client.get('/api/jobs').json()), 1)

    def test_simultaneous_retries_create_one_job(self):
        with ThreadPoolExecutor(max_workers=2) as pool:
            responses = list(pool.map(lambda _: self.submit(), range(2)))
        self.assertEqual([r.status_code for r in responses], [200, 200])
        self.assertEqual(responses[0].json(), responses[1].json())
        self.assertEqual(len(list(self.studio.UPLOADS.iterdir())), 1)
        self.assertEqual(len(self.client.get('/api/jobs').json()), 1)

    def test_key_cannot_be_reused_for_changed_content(self):
        self.assertEqual(self.submit().status_code, 200)
        response = self.submit(image=photo('red'))
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(len(list(self.studio.UPLOADS.iterdir())), 1)

    def test_invalid_identifier_does_not_save_files(self):
        response = self.submit(skus='["bad/sku"]')
        self.assertEqual(response.status_code, 400)
        self.assertEqual(list(self.studio.UPLOADS.iterdir()), [])

    def test_invalid_second_upload_cleans_first_and_rolls_back_job(self):
        response = self.client.post('/api/generate',
                                    data={'skus': '["A", "B"]', 'kind': 'main', 'scenes': 'White background'},
                                    files=[('files', ('a.png', photo(), 'image/png')),
                                           ('files', ('b.png', b'not an image', 'image/png'))])
        self.assertEqual(response.status_code, 400, response.text)
        self.assertEqual(list(self.studio.UPLOADS.iterdir()), [])
        self.assertEqual(self.client.get('/api/jobs').json(), [])

    def test_full_queue_rolls_back_files_and_idempotency(self):
        with patch.object(self.studio, 'MAX_PENDING_IMAGES', 0):
            response = self.submit()
        self.assertEqual(response.status_code, 429, response.text)
        self.assertEqual(list(self.studio.UPLOADS.iterdir()), [])
        self.assertEqual(self.submit().status_code, 200)

    def test_retired_model_is_rejected_before_saving_uploads(self):
        response = self.submit(model='dall-e-3')
        self.assertEqual(response.status_code, 400)
        self.assertEqual(list(self.studio.UPLOADS.iterdir()), [])

    def test_server_enforces_overall_image_limit(self):
        count = 21
        response = self.client.post('/api/generate',
                                    data={'skus': json.dumps([f'SKU-{i}' for i in range(count)]), 'kind': 'main',
                                          'scenes': '\n'.join(f'Scene {i}' for i in range(12))},
                                    files=[('files', (f'{i}.png', photo(), 'image/png')) for i in range(count)])
        self.assertEqual(response.status_code, 400, response.text)
        self.assertIn('250', response.json()['detail'])
        self.assertEqual(list(self.studio.UPLOADS.iterdir()), [])

    def test_ambiguous_catalog_identity_rolls_back_upload(self):
        with self.studio.db() as c:
            c.execute('INSERT INTO catalog_products(id,sku,asin) VALUES(?,?,?)', ('a', 'A', 'SHARED'))
            c.execute('INSERT INTO catalog_products(id,sku,asin) VALUES(?,?,?)', ('b', 'B', 'SHARED'))
        response = self.submit(skus='["SHARED"]')
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(list(self.studio.UPLOADS.iterdir()), [])
        self.assertEqual(self.client.get('/api/jobs').json(), [])

    def test_revision_rule_is_promoted_once_only_after_approval(self):
        self.assertEqual(self.submit().status_code, 200)
        parent = self.client.get('/api/renders').json()[0]
        with self.studio.db() as c:
            c.execute("UPDATE renders SET status='completed',file=? WHERE id=?", (parent['id'] + '.jpg', parent['id']))
        body = {'render_id': parent['id'], 'instructions': 'Keep the blue cap unchanged', 'remember_rule': True}
        first = self.client.post('/api/revise', json=body, headers={'Idempotency-Key': 'revision-0001'})
        second = self.client.post('/api/revise', json=body, headers={'Idempotency-Key': 'revision-0001'})
        self.assertEqual(first.status_code, 200, first.text)
        self.assertEqual(first.json(), second.json())
        self.assertTrue(first.json()['pending_rule'])
        self.assertEqual(self.client.get('/api/memory/products/SKU-1').json()['rules'], [])
        rid = first.json()['render_id']
        self.assertEqual(self.client.post(f'/api/renders/{rid}/approve', json={'approved': True}).status_code, 400)
        with self.studio.db() as c:
            c.execute("UPDATE renders SET status='completed' WHERE id=?", (rid,))
        for approved in (True, True, False, True):
            self.assertEqual(self.client.post(f'/api/renders/{rid}/approve', json={'approved': approved}).status_code, 200)
        rules = self.client.get('/api/memory/products/SKU-1').json()['rules']
        self.assertEqual([r['instruction'] for r in rules], [body['instructions']])
        self.assertEqual(rules[0]['source_render_id'], rid)


class AuthenticationTests(unittest.TestCase):
    def test_anonymous_access_requires_explicit_opt_in(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.dict(os.environ, {'STUDIOFLOW_LOAD_DOTENV': '0', 'APP_MODE': 'demo', 'TEAM_USERS': '',
                                         'ALLOW_ANONYMOUS_DEMO': '0', 'STUDIOFLOW_REQUIRE_AUTH': '0'}):
                with self.assertRaises(RuntimeError):
                    Settings.load(Path(directory))

    def test_container_auth_requirement_overrides_anonymous_opt_in(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.dict(os.environ, {'STUDIOFLOW_LOAD_DOTENV': '0', 'APP_MODE': 'demo', 'TEAM_USERS': '',
                                         'ALLOW_ANONYMOUS_DEMO': '1', 'STUDIOFLOW_REQUIRE_AUTH': '1'}):
                with self.assertRaises(RuntimeError):
                    Settings.load(Path(directory))

    def test_local_demo_rejects_network_host_and_remote_peer(self):
        from fastapi.testclient import TestClient
        with isolated_app(anonymous=True) as (studio, client):
            self.assertEqual(client.get('/api/config').status_code, 200)
            self.assertEqual(client.get('/api/config', headers={'Host': 'studio.example.com'}).status_code, 403)
            with TestClient(studio.app, base_url='http://localhost', client=('192.0.2.1', 50000)) as remote:
                self.assertEqual(remote.get('/api/config').status_code, 403)

    def test_removed_account_session_is_revoked(self):
        with isolated_app() as (studio, client):
            self.assertEqual(client.get('/api/stats').status_code, 200)
            with patch.object(studio, 'TEAM', {}):
                self.assertIsNone(studio.get_session_user(client.cookies.get('studio_session')))

    def test_team_endpoints_require_login(self):
        with isolated_app(authenticated=False) as (_, client):
            self.assertEqual(client.get('/api/stats').status_code, 401)
            self.assertEqual(client.post('/api/login', json={'username': 'tester', 'password': 'wrong'}).status_code, 401)


class DemoWorkflowTests(unittest.TestCase):
    def test_generate_review_and_download_offline(self):
        with isolated_app(start_queue=True) as (studio, client):
            response = client.post('/api/generate',
                                   data={'skus': '["DEMO-1"]', 'kind': 'main', 'scenes': 'White background'},
                                   files={'files': ('product.png', photo(), 'image/png')},
                                   headers={'Idempotency-Key': 'demo-flow-0001'})
            self.assertEqual(response.status_code, 200, response.text)
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                render = client.get('/api/renders').json()[0]
                if render['status'] in ('completed', 'failed'):
                    break
                time.sleep(0.05)
            self.assertEqual(render['status'], 'completed', render.get('error'))
            asset = client.get(render['url'])
            self.assertEqual(asset.status_code, 200)
            self.assertEqual(Image.open(io.BytesIO(asset.content)).size, (2000, 2000))
            self.assertEqual(client.post(f"/api/renders/{render['id']}/approve", json={'approved': True}).status_code, 200)
            self.assertEqual(client.get('/api/jobs').json()[0]['status'], 'finished')
            self.assertEqual(client.get('/api/health').json(), {'status': 'ok'})


if __name__ == '__main__':
    unittest.main()
