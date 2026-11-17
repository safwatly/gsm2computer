"""Offline delivery tests. Never contact the configured production webhook."""
from __future__ import annotations

import io
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from unittest.mock import patch
import urllib.error

import alert_webhook as w


class _FakeResponse:
    def __init__(self, status: int = 200, body: bytes = b"ok") -> None:
        self.status = status
        self._body = body

    def read(self, _n: int = -1) -> bytes:
        return self._body

    def getcode(self) -> int:
        return self.status

    def __enter__(self) -> "_FakeResponse":
        return self

    def __exit__(self, *args: object) -> None:
        return None


class Alerts(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, {
            w.URL_ENV: "https://example.invalid/private?token=secret-url",
            w.KEY_ENV: "Bearer secret-key",
            w.SPOOL_ENV: self.temp.name + "/private/outbox.sqlite3",
            w.SMS_URL_ENV: "",
            w.SMS_KEY_ENV: "",
            w.SMS_SPOOL_ENV: self.temp.name + "/sms/outbox.sqlite3",
        })
        self.env.start()
        self.worker = patch.object(w, "start_alert_worker")
        self.worker.start()
        self.sms_worker = patch.object(w, "start_sms_worker")
        self.sms_worker.start()

    def tearDown(self):
        self.sms_worker.stop()
        self.worker.stop()
        self.env.stop()
        self.temp.cleanup()

    def rows(self):
        with w._connect() as c:
            return c.execute("SELECT * FROM alerts ORDER BY created").fetchall()

    def test_disabled_and_test_filters(self):
        with patch.dict(os.environ, {w.URL_ENV: ""}):
            w.post_system_alert("real alert")
        w.post_system_alert("OpenClaw e2e failed")
        w.post_alert_event({"event": "other", "e2e": True})
        self.assertFalse(w._spool_path().exists())

    def test_call_ended_allowed(self):
        w.post_alert_event({"event": "call_ended", "test": True, "text": "e2e"})
        self.assertEqual(json.loads(self.rows()[0]["payload"])["event"], "call_ended")

    def test_private_durable_queue_and_unicode(self):
        text = "SMS مرحبا 🙂\nnew line\ttext\x00"
        w.post_system_alert(text)
        row = self.rows()[0]
        self.assertEqual(json.loads(row["original_payload"])["text"], text)
        self.assertEqual(json.loads(row["payload"])["text"], text[:-1])
        self.assertEqual(w._spool_path().stat().st_mode & 0o777, 0o600)
        self.assertEqual(w._spool_path().parent.stat().st_mode & 0o777, 0o700)

    def test_long_text_retains_original_and_structured_fields(self):
        w.post_alert_event({"event": "call_ended", "duration_s": 42, "text": "🙂" * 5000})
        row = self.rows()[0]
        self.assertEqual(len(json.loads(row["original_payload"])["text"]), 5000)
        sent = json.loads(row["payload"])
        self.assertLessEqual(len(sent["text"].encode()), w.MAX_TEXT_BYTES)
        self.assertEqual(sent["duration_s"], 42)

    def test_failure_retry_then_success_survives_reopen(self):
        w.post_system_alert("real missed SMS")
        with patch.object(w, "_send", return_value=(400, "temporarily unavailable")):
            with self.assertLogs(w.LOG, "WARNING") as logs:
                self.assertTrue(w._drain_one())
        row = self.rows()[0]
        self.assertIsNone(row["delivered"])
        self.assertEqual(row["status"], 400)
        self.assertIn("temporarily unavailable", "\n".join(logs.output))
        self.assertFalse(w._drain_one())  # Backoff, no immediate busy retry.
        with w._connect() as c:
            c.execute("UPDATE alerts SET due=0")
        with patch.object(w, "_send", return_value=(200, '{"success":true}')) as send:
            self.assertTrue(w._drain_one())
        self.assertEqual(json.loads(send.call_args.args[0]), {"text": "real missed SMS"})
        row = self.rows()[0]
        self.assertEqual(row["attempts"], 2)
        self.assertIsNotNone(row["delivered"])
        self.assertFalse(w._drain_one())

    def test_lease_prevents_parallel_delivery_and_recovers_crash(self):
        w.post_system_alert("SMS")
        entered, release = threading.Event(), threading.Event()

        def send(_):
            entered.set()
            release.wait(3)
            return 200, "ok"

        with patch.object(w, "_send", side_effect=send) as mock:
            thread = threading.Thread(target=w._drain_one)
            thread.start()
            self.assertTrue(entered.wait(3))
            self.assertFalse(w._drain_one())
            release.set()
            thread.join(3)
            self.assertEqual(mock.call_count, 1)
        w.post_system_alert("next")
        with w._connect() as c:
            c.execute("UPDATE alerts SET lease='dead-process', due=0 WHERE delivered IS NULL")
        with patch.object(w, "_send", return_value=(200, "ok")):
            self.assertTrue(w._drain_one())

    def test_error_body_redacts_before_truncation(self):
        url = os.environ[w.URL_ENV]
        body = ("x" * 2040 + url + " secret-key\nAuthorization: Bearer secret-key\n" + url).encode()
        err = urllib.error.HTTPError(url, 400, "Bad request", {}, io.BytesIO(body))
        with patch.object(w.urllib.request, "urlopen", side_effect=err) as send:
            status, detail = w._send('{"text":"SMS"}')
        self.assertEqual(status, 400)
        self.assertNotIn("secret", detail)
        self.assertNotIn(url, detail)
        self.assertLessEqual(len(detail), 2048)
        req = send.call_args.args[0]
        self.assertEqual(req.get_header("Content-type"), "application/json")
        self.assertEqual(req.get_header("Authorization"), "Bearer secret-key")

    def test_transport_errors_never_expose_destination(self):
        with patch.object(w.urllib.request, "urlopen", side_effect=TimeoutError(os.environ[w.URL_ENV])):
            self.assertEqual(w._send("{}"), (0, "TimeoutError"))

    def test_enqueue_failures_do_not_break_hub(self):
        with patch.object(w, "_connect_path", side_effect=OSError("sensitive details")):
            with self.assertLogs(w.LOG, "ERROR") as logs:
                w.post_system_alert("SMS")
        self.assertNotIn("sensitive details", "\n".join(logs.output))

    def test_sms_secret_is_redacted_from_ops_error_body(self):
        os.environ[w.SMS_URL_ENV] = "https://sms.example/hook?token=sms-url-secret"
        os.environ[w.SMS_KEY_ENV] = "sms-key-secret"
        url = os.environ[w.URL_ENV]
        body = b"echo sms-key-secret and https://sms.example/hook?token=sms-url-secret"
        err = urllib.error.HTTPError(url, 500, "Error", {}, io.BytesIO(body))
        with patch.object(w.urllib.request, "urlopen", side_effect=err):
            _status, detail = w._send("{}")
        self.assertNotIn("sms-key-secret", detail)
        self.assertNotIn("sms-url-secret", detail)
        self.assertNotIn("sms.example", detail)


class SmsRouting(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.alert_spool = str(Path(self.temp.name) / "alert" / "outbox.sqlite3")
        self.sms_spool = str(Path(self.temp.name) / "sms" / "outbox.sqlite3")
        self.env = patch.dict(os.environ, {
            w.URL_ENV: "https://ops.example/hook",
            w.KEY_ENV: "ops-key",
            w.SPOOL_ENV: self.alert_spool,
            w.SMS_URL_ENV: "",
            w.SMS_KEY_ENV: "",
            w.SMS_SPOOL_ENV: self.sms_spool,
        })
        self.env.start()
        self.worker = patch.object(w, "start_alert_worker")
        self.worker.start()
        self.sms_worker = patch.object(w, "start_sms_worker")
        self.sms_worker.start()

    def tearDown(self):
        self.sms_worker.stop()
        self.worker.stop()
        self.env.stop()
        self.temp.cleanup()

    def rows(self, dest):
        path = dest.spool_path()
        if not path.exists():
            return []
        with w._connect_path(path) as conn:
            return list(conn.execute("SELECT * FROM alerts ORDER BY created"))

    def test_unset_sms_url_uses_ops_spool_and_text_only(self):
        text = "ops SMS wake-up from +1555: hi (receivedAt=t)"
        w.post_sms_event(text, sender="+1555", body="hi", received_at="t")
        self.assertFalse(Path(self.sms_spool).exists())
        payload = json.loads(self.rows(w.ALERT)[0]["payload"])
        self.assertEqual(payload, {"text": text})

    def test_blank_sms_url_matches_unset(self):
        os.environ[w.SMS_URL_ENV] = "   "
        w.post_sms_event("plain", sender="+1", body="b", received_at="t")
        self.assertFalse(Path(self.sms_spool).exists())
        self.assertEqual(json.loads(self.rows(w.ALERT)[0]["payload"]), {"text": "plain"})

    def test_set_sms_url_skips_ops_spool_and_adds_fields(self):
        os.environ[w.SMS_URL_ENV] = "https://sms.example/hook"
        os.environ[w.SMS_KEY_ENV] = "sms-key"
        text = "ops SMS wake-up from +1555: hi (receivedAt=2026-11-17T00:00:00Z)"
        w.post_sms_event(
            text,
            sender="+1555",
            body="hi",
            received_at="2026-11-17T00:00:00Z",
        )
        self.assertFalse(Path(self.alert_spool).exists())
        payload = json.loads(self.rows(w.SMS)[0]["payload"])
        self.assertEqual(payload, {
            "text": text,
            "event": "sms_in",
            "from": "+1555",
            "body": "hi",
            "receivedAt": "2026-11-17T00:00:00Z",
        })
        self.assertNotEqual(w.ALERT.spool_path(), w.SMS.spool_path())

    def test_ops_alert_stays_on_ops_spool_when_sms_url_is_set(self):
        os.environ[w.SMS_URL_ENV] = "https://sms.example/hook"
        os.environ[w.SMS_KEY_ENV] = "sms-key"
        w.post_sms_event("sms text", sender="+1", body="b", received_at="t")
        w.post_system_alert("call watchdog abort: idle")
        self.assertEqual(json.loads(self.rows(w.SMS)[0]["payload"])["event"], "sms_in")
        self.assertEqual(
            json.loads(self.rows(w.ALERT)[0]["payload"]),
            {"text": "call watchdog abort: idle"},
        )
        self.assertEqual(len(self.rows(w.SMS)), 1)
        self.assertEqual(len(self.rows(w.ALERT)), 1)

    def test_sms_failure_does_not_block_ops_delivery(self):
        os.environ[w.SMS_URL_ENV] = "https://sms.example/hook"
        os.environ[w.SMS_KEY_ENV] = "sms-key"
        w.post_sms_event("sms text", sender="+1", body="b", received_at="t")
        w.post_system_alert("hub down")
        with patch.object(w, "_deliver", return_value=(500, "sms down")):
            self.assertTrue(w._drain_dest(w.SMS))
        with patch.object(w, "_send", return_value=(200, "ok")):
            self.assertTrue(w._drain_one())
        self.assertIsNone(self.rows(w.SMS)[0]["delivered"])
        self.assertIsNotNone(self.rows(w.ALERT)[0]["delivered"])

    def test_hung_ops_post_does_not_block_sms_post(self):
        os.environ[w.SMS_URL_ENV] = "https://sms.example/hook"
        os.environ[w.SMS_KEY_ENV] = "Bearer sms-key"
        w.post_sms_event("sms text", sender="+1", body="b", received_at="t")
        w.post_system_alert("hub down")
        entered, release = threading.Event(), threading.Event()

        def urlopen(req, timeout=None):
            if req.full_url == "https://ops.example/hook":
                entered.set()
                self.assertTrue(release.wait(3), "ops POST was not released")
                return _FakeResponse(200)
            self.assertEqual(req.full_url, "https://sms.example/hook")
            self.assertEqual(req.get_header("Authorization"), "Bearer sms-key")
            self.assertEqual(timeout, w.TIMEOUT_S)
            return _FakeResponse(200, b"sms ok")

        with patch.object(w.urllib.request, "urlopen", side_effect=urlopen):
            alert_thread = threading.Thread(target=w._drain_dest, args=(w.ALERT,))
            alert_thread.start()
            self.assertTrue(entered.wait(3))
            self.assertTrue(w._drain_dest(w.SMS))
            self.assertIsNotNone(self.rows(w.SMS)[0]["delivered"])
            self.assertIsNone(self.rows(w.ALERT)[0]["delivered"])
            release.set()
            alert_thread.join(3)
            self.assertFalse(alert_thread.is_alive())
        self.assertIsNotNone(self.rows(w.ALERT)[0]["delivered"])

    def test_sms_http_error_redacts_sms_secrets(self):
        os.environ[w.SMS_URL_ENV] = "https://sms.example/hook?token=sms-url-secret"
        os.environ[w.SMS_KEY_ENV] = "sms-key-secret"
        body = (
            "see https://sms.example/hook?token=sms-url-secret "
            "sms-key-secret\nAuthorization: Bearer sms-key-secret"
        ).encode()
        err = urllib.error.HTTPError(
            os.environ[w.SMS_URL_ENV], 400, "Bad", {}, io.BytesIO(body)
        )
        with patch.object(w.urllib.request, "urlopen", side_effect=err):
            status, detail = w._deliver(w.SMS, "{}")
        self.assertEqual(status, 400)
        self.assertNotIn("sms-key-secret", detail)
        self.assertNotIn("sms-url-secret", detail)
        self.assertNotIn("sms.example", detail)

    def test_live_sms_post_reaches_only_the_sms_socket(self):
        received = {}

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                length = int(self.headers.get("Content-Length") or 0)
                received["body"] = self.rfile.read(length)
                received["auth"] = self.headers.get("Authorization")
                received["path"] = self.path
                self.send_response(204)
                self.end_headers()

            def log_message(self, fmt, *args):
                return None

        server = HTTPServer(("127.0.0.1", 0), Handler)
        port = server.server_address[1]
        thread = threading.Thread(target=server.handle_request, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        os.environ[w.SMS_URL_ENV] = f"http://127.0.0.1:{port}/suzie"
        os.environ[w.SMS_KEY_ENV] = "suzie-key"
        os.environ[w.URL_ENV] = "http://127.0.0.1:1/ops-must-not-be-called"
        text = "ops SMS wake-up from +201280043725: still down (receivedAt=t)"
        w.post_sms_event(text, sender="+201280043725", body="still down", received_at="t")
        self.assertTrue(w._drain_dest(w.SMS))
        thread.join(3)
        self.assertFalse(thread.is_alive())
        body = json.loads(received["body"].decode("utf-8"))
        self.assertEqual(received["path"], "/suzie")
        self.assertEqual(received["auth"], "Bearer suzie-key")
        self.assertEqual(body["event"], "sms_in")
        self.assertEqual(body["from"], "+201280043725")
        self.assertEqual(body["body"], "still down")
        self.assertEqual(body["receivedAt"], "t")
        self.assertEqual(body["text"], text)
        self.assertIsNotNone(self.rows(w.SMS)[0]["delivered"])
        self.assertFalse(Path(self.alert_spool).exists())


if __name__ == "__main__":
    unittest.main()
