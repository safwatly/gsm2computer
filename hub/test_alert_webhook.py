"""Offline delivery tests. Never contact the configured production webhook."""
import io
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
import urllib.error

import alert_webhook as w


class Alerts(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, {w.URL_ENV: 'https://example.invalid/private?token=secret-url',
                                         w.KEY_ENV: 'Bearer secret-key',
                                         w.SPOOL_ENV: self.temp.name + '/private/outbox.sqlite3'})
        self.env.start()
        self.worker = patch.object(w, 'start_alert_worker')
        self.worker.start()

    def tearDown(self):
        self.worker.stop()
        self.env.stop()
        self.temp.cleanup()

    def rows(self):
        with w._connect() as c:
            return c.execute('SELECT * FROM alerts ORDER BY created').fetchall()

    def test_disabled_and_test_filters(self):
        with patch.dict(os.environ, {w.URL_ENV: ''}):
            w.post_system_alert('real alert')
        w.post_system_alert('OpenClaw e2e failed')
        w.post_alert_event({'event': 'other', 'e2e': True})
        self.assertFalse(w._spool_path().exists())

    def test_call_ended_allowed(self):
        w.post_alert_event({'event': 'call_ended', 'test': True, 'text': 'e2e'})
        self.assertEqual(json.loads(self.rows()[0]['payload'])['event'], 'call_ended')

    def test_private_durable_queue_and_unicode(self):
        text = 'SMS مرحبا 🙂\nnew line\ttext\x00'
        w.post_system_alert(text)
        row = self.rows()[0]
        self.assertEqual(json.loads(row['original_payload'])['text'], text)
        self.assertEqual(json.loads(row['payload'])['text'], text[:-1])
        self.assertEqual(w._spool_path().stat().st_mode & 0o777, 0o600)
        self.assertEqual(w._spool_path().parent.stat().st_mode & 0o777, 0o700)

    def test_long_text_retains_original_and_structured_fields(self):
        w.post_alert_event({'event': 'call_ended', 'duration_s': 42, 'text': '🙂' * 5000})
        row = self.rows()[0]
        self.assertEqual(len(json.loads(row['original_payload'])['text']), 5000)
        sent = json.loads(row['payload'])
        self.assertLessEqual(len(sent['text'].encode()), w.MAX_TEXT_BYTES)
        self.assertEqual(sent['duration_s'], 42)

    def test_failure_retry_then_success_survives_reopen(self):
        w.post_system_alert('real missed SMS')
        with patch.object(w, '_send', return_value=(400, 'temporarily unavailable')):
            with self.assertLogs(w.LOG, 'WARNING') as logs:
                self.assertTrue(w._drain_one())
        row = self.rows()[0]
        self.assertIsNone(row['delivered'])
        self.assertEqual(row['status'], 400)
        self.assertIn('temporarily unavailable', '\n'.join(logs.output))
        self.assertFalse(w._drain_one())  # Backoff, no immediate busy retry.
        with w._connect() as c:
            c.execute('UPDATE alerts SET due=0')
        with patch.object(w, '_send', return_value=(200, '{"success":true}')) as send:
            self.assertTrue(w._drain_one())
        self.assertEqual(json.loads(send.call_args.args[0]), {'text': 'real missed SMS'})
        row = self.rows()[0]
        self.assertEqual(row['attempts'], 2)
        self.assertIsNotNone(row['delivered'])
        self.assertFalse(w._drain_one())

    def test_lease_prevents_parallel_delivery_and_recovers_crash(self):
        w.post_system_alert('SMS')
        entered, release = threading.Event(), threading.Event()
        def send(_):
            entered.set()
            release.wait(3)
            return 200, 'ok'
        with patch.object(w, '_send', side_effect=send) as mock:
            thread = threading.Thread(target=w._drain_one)
            thread.start()
            self.assertTrue(entered.wait(3))
            self.assertFalse(w._drain_one())
            release.set()
            thread.join(3)
            self.assertEqual(mock.call_count, 1)
        w.post_system_alert('next')
        with w._connect() as c:
            c.execute("UPDATE alerts SET lease='dead-process', due=0 WHERE delivered IS NULL")
        with patch.object(w, '_send', return_value=(200, 'ok')):
            self.assertTrue(w._drain_one())

    def test_error_body_redacts_before_truncation(self):
        url = os.environ[w.URL_ENV]
        body = ('x' * 2040 + url + ' secret-key\nAuthorization: Bearer secret-key\n' + url).encode()
        err = urllib.error.HTTPError(url, 400, 'Bad request', {}, io.BytesIO(body))
        with patch.object(w.urllib.request, 'urlopen', side_effect=err) as send:
            status, detail = w._send('{"text":"SMS"}')
        self.assertEqual(status, 400)
        self.assertNotIn('secret', detail)
        self.assertNotIn(url, detail)
        self.assertLessEqual(len(detail), 2048)
        req = send.call_args.args[0]
        self.assertEqual(req.get_header('Content-type'), 'application/json')
        self.assertEqual(req.get_header('Authorization'), 'Bearer secret-key')

    def test_transport_errors_never_expose_destination(self):
        with patch.object(w.urllib.request, 'urlopen', side_effect=TimeoutError(os.environ[w.URL_ENV])):
            self.assertEqual(w._send('{}'), (0, 'TimeoutError'))

    def test_enqueue_failures_do_not_break_hub(self):
        with patch.object(w, '_connect', side_effect=OSError('sensitive details')):
            with self.assertLogs(w.LOG, 'ERROR') as logs:
                w.post_system_alert('SMS')
        self.assertNotIn('sensitive details', '\n'.join(logs.output))


if __name__ == '__main__':
    unittest.main()
