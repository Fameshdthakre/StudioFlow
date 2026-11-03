"""Offline accounting regressions, including charged requests with failed exports."""
import base64
import dataclasses
import json
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from openai.types.images_response import ImagesResponse

from studioflow import pricing, usage
from studioflow.jobs import RenderQueue
from tests.support import isolated_app
from tests.test_app import photo


def reported(*, split=False):
    result = {'input_tokens': 100, 'input_tokens_details': {'image_tokens': 80, 'text_tokens': 20},
              'output_tokens': 200, 'total_tokens': 300}
    if split:
        result['output_tokens_details'] = {'image_tokens': 150, 'text_tokens': 50}
    return result


class PricingTests(unittest.TestCase):
    def test_mixed_output_uses_distinct_image_and_text_rates(self):
        result = pricing.estimate(reported(split=True), pricing.rate_card('gpt-image-1.5'))
        self.assertEqual(result['cost_microusd'], 6040)
        self.assertEqual(result['meter_status'], 'reported')
        result = pricing.estimate(reported(), pricing.rate_card('gpt-image-2.5-flare'))
        self.assertEqual(result['cost_microusd'], 6740)

    def test_missing_or_inconsistent_counts_are_unknown_not_free(self):
        variants = [None, {}, {'output_tokens': 200}]
        for field, value in [('input_tokens', -1), ('output_tokens', 200.0), ('input_tokens', True),
                             ('input_tokens', 101), ('output_tokens_details', []),
                             ('input_tokens_details', {'image_tokens': 100}),
                             ('input_tokens_details', {'image_tokens': 80, 'text_tokens': 20, 'cached_tokens': 30})]:
            item = reported()
            item[field] = value
            variants.append(item)
        for raw in variants:
            with self.subTest(raw=raw):
                self.assertIsNone(pricing.estimate(raw, pricing.rate_card('gpt-image-2'))['cost_microusd'])
        self.assertIsNone(pricing.estimate(reported(), pricing.rate_card('gpt-image-1.5'))['cost_microusd'])
        self.assertIsNone(pricing.estimate(reported(), pricing.rate_card('future-model'))['cost_microusd'])

    def test_cache_breakdown_and_fixed_precision_rounding(self):
        raw = reported(split=True)
        raw['input_tokens_details'].update(cached_tokens=50, cached_tokens_details={'image_tokens': 40, 'text_tokens': 10})
        self.assertEqual(pricing.estimate(raw, pricing.rate_card('gpt-image-1.5'))['cost_microusd'], 5763)
        # Synchronous Image 2 edits have no cached-input discount.
        raw.pop('output_tokens_details')
        self.assertEqual(pricing.estimate(raw, pricing.rate_card('gpt-image-2'))['cost_microusd'], 6740)
        raw['input_tokens_details']['cached_tokens_details']['image_tokens'] = 81
        self.assertIsNone(pricing.estimate(raw, pricing.rate_card('gpt-image-1.5'))['cost_microusd'])


class UsageTests(unittest.TestCase):
    def setUp(self):
        context = isolated_app()
        self.studio, self.client = context.__enter__()
        self.addCleanup(context.__exit__, None, None, None)

    def queued_render(self, model='gpt-image-2.5-flare'):
        result = self.client.post('/api/generate', data={'skus': '["SKU-1"]', 'kind': 'main',
            'scenes': 'A white background', 'model': model}, files={'files': ('a.png', photo(), 'image/png')})
        self.assertEqual(result.status_code, 200, result.text)
        queue = RenderQueue(self.studio.db, self.studio.generate_one)
        return queue, queue.claim()

    def receipt(self, attempt):
        with self.studio.db() as c:
            return dict(c.execute('SELECT * FROM usage_ledger WHERE attempt_id=?', (attempt,)).fetchone())

    def add_attempt(self, raw=None):
        _, row = self.queued_render()
        attempt = usage.begin_attempt(self.studio.db, row)
        if raw is not None:
            usage.record_response(self.studio.db, attempt, SimpleNamespace(usage=raw))
        usage.finish_attempt(self.studio.db, attempt, raw is not None)
        return attempt

    def test_receipt_survives_image_decode_failure_and_keeps_provider_metadata(self):
        queue, render = self.queued_render()
        response = ImagesResponse(created=1, data=[{'b64_json': 'invalid!'}], usage=reported())
        response._request_id = 'req-offline'
        fake = SimpleNamespace(images=SimpleNamespace(edit=Mock(return_value=response)), close=Mock())
        with patch.object(self.studio, 'MODE', 'live'), patch.object(self.studio, 'KEY', 'offline-placeholder'), \
                patch('openai.OpenAI', return_value=fake) as factory, self.assertLogs(self.studio.logger, level='ERROR'):
            queue.execute(render)
        factory.assert_called_once_with(api_key='offline-placeholder', timeout=180, max_retries=0)
        fake.images.edit.assert_called_once()
        with self.studio.db() as c:
            receipt = dict(c.execute('SELECT * FROM usage_ledger').fetchone())
            self.assertEqual(c.execute('SELECT status FROM renders').fetchone()[0], 'failed')
        self.assertEqual(receipt['status'], 'failed')
        self.assertEqual(receipt['request_id'], 'req-offline')
        self.assertEqual(receipt['cost_microusd'], 6740)
        self.assertEqual(json.loads(receipt['usage_json'])['input_tokens'], 100)
        self.assertEqual(receipt['rate_card_version'], pricing.VERSION)
        self.assertFalse((self.studio.OUTPUTS / (render['id'] + '.jpg')).exists())
        self.assertEqual(self.client.get('/api/usage').json()['tracked_spend_usd'], .006740)

    def test_successful_image_with_unreported_usage_is_an_unknown_cost(self):
        queue, render = self.queued_render()
        response = SimpleNamespace(data=[SimpleNamespace(b64_json=base64.b64encode(photo()).decode())], usage=None)
        fake = SimpleNamespace(images=SimpleNamespace(edit=Mock(return_value=response)), close=Mock())
        with patch.object(self.studio, 'MODE', 'live'), patch.object(self.studio, 'KEY', 'offline-placeholder'), patch('openai.OpenAI', return_value=fake):
            queue.execute(render)
        data = self.client.get('/api/usage').json()
        self.assertEqual((data['requests'], data['unreported'], data['pending']), (1, 1, 0))
        with self.studio.db() as c:
            receipt = c.execute('SELECT * FROM usage_ledger').fetchone()
            self.assertEqual(receipt['status'], 'completed')
            self.assertIsNone(receipt['cost_microusd'])
            self.assertEqual(c.execute('SELECT status FROM renders').fetchone()[0], 'completed')

    def test_network_timeout_is_recorded_once_with_unknown_cost(self):
        queue, render = self.queued_render()
        fake = SimpleNamespace(images=SimpleNamespace(edit=Mock(side_effect=TimeoutError('offline timeout'))), close=Mock())
        with patch.object(self.studio, 'MODE', 'live'), patch.object(self.studio, 'KEY', 'offline-placeholder'), \
                patch('openai.OpenAI', return_value=fake), self.assertLogs(self.studio.logger, level='ERROR'):
            queue.execute(render)
        fake.images.edit.assert_called_once()
        self.assertIsNone(queue.claim())
        with self.studio.db() as c:
            row = c.execute('SELECT * FROM usage_ledger').fetchone()
        self.assertEqual(row['status'], 'unknown')
        self.assertIsNone(row['received_at'])
        self.assertIsNone(row['cost_microusd'])

    def test_local_preflight_failure_never_creates_a_provider_attempt(self):
        queue, render = self.queued_render()
        (self.studio.UPLOADS / render['source']).unlink()
        with patch.object(self.studio, 'MODE', 'live'), patch('openai.OpenAI') as factory, self.assertLogs(self.studio.logger, level='ERROR'):
            queue.execute(render)
        factory.assert_not_called()
        self.assertEqual(self.client.get('/api/usage').json()['requests'], 0)

    def test_response_is_immutable_and_uses_the_rates_saved_before_the_call(self):
        _, render = self.queued_render()
        attempt = usage.begin_attempt(self.studio.db, render)
        with patch.object(pricing, 'rate_card', return_value={}):
            usage.record_response(self.studio.db, attempt, SimpleNamespace(usage=reported(), _request_id='first'))
        accepted = self.receipt(attempt)
        usage.record_response(self.studio.db, attempt, SimpleNamespace(usage=None, _request_id='later'))
        self.assertEqual(self.receipt(attempt), accepted)
        self.assertEqual(accepted['cost_microusd'], 6740)

    def test_checkpoint_refuses_inflight_and_uses_an_atomic_ledger_boundary(self):
        _, render = self.queued_render()
        attempt = usage.begin_attempt(self.studio.db, render)
        body = {'balance_checkpoint_usd': '0.010000', 'alert_percent': 50}
        self.assertEqual(self.client.put('/api/usage/checkpoint', json=body).status_code, 409)
        usage.record_response(self.studio.db, attempt, SimpleNamespace(usage=reported()))
        self.assertEqual(self.client.put('/api/usage/checkpoint', json=body).status_code, 409)
        usage.finish_attempt(self.studio.db, attempt, True)
        self.assertEqual(self.client.put('/api/usage/checkpoint', json=body).status_code, 200)
        self.add_attempt(reported())
        self.add_attempt()  # Unknown costs remain visible, and never reduce spend by a guessed zero.
        data = self.client.get('/api/usage').json()
        balance = data['balance_estimate']
        self.assertEqual(data['tracked_spend_usd'], .013480)
        self.assertEqual(balance['tracked_since_checkpoint_usd'], .006740)
        self.assertEqual(balance['estimated_remaining_usd'], .003260)
        self.assertEqual(balance['remaining_percent'], 32.6)
        self.assertTrue(balance['incomplete'])
        self.assertTrue(balance['low'])
        self.assertEqual(balance['unreported_since_checkpoint'], 1)
        self.assertEqual(data['by_user'][0]['unreported'], 1)
        self.assertEqual(data['by_model'][0]['requests'], 3)

    def test_checkpoint_validates_money_and_zero_balance(self):
        for amount in ('-1', 'NaN', 'Infinity', '10000001', '0.0000001'):
            with self.subTest(amount=amount):
                self.assertEqual(self.client.put('/api/usage/checkpoint', json={'balance_checkpoint_usd': amount}).status_code, 422)
        self.assertEqual(self.client.put('/api/usage/checkpoint', json={'balance_checkpoint_usd': '0'}).status_code, 200)
        balance = self.client.get('/api/usage').json()['balance_estimate']
        self.assertEqual(balance['remaining_percent'], 0)
        self.assertTrue(balance['low'])

    def test_worker_recovery_closes_ambiguous_attempt_without_repeating_it(self):
        queue, render = self.queued_render()
        attempt = usage.begin_attempt(self.studio.db, render)
        queue.recover()  # The claimed worker has no live heartbeat.
        self.assertIsNone(queue.claim())
        self.assertEqual(self.receipt(attempt)['status'], 'unknown')
        self.assertIsNotNone(self.receipt(attempt)['finished_at'])
        self.assertEqual(self.client.get('/api/usage').json()['pending'], 0)

    def test_metering_continues_when_only_the_dashboard_is_disabled(self):
        self.studio.settings = dataclasses.replace(self.studio.settings, enable_usage=False)
        self.add_attempt(reported())
        self.assertEqual(self.client.get('/api/usage').status_code, 404)
        self.assertEqual(usage.summary(self.studio.db)['requests'], 1)


if __name__ == '__main__':
    unittest.main()
