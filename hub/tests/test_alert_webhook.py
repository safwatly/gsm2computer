"""Destination routing tests for the durable alert webhook spool."""
from __future__ import annotations

import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import urllib.request

HUB_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HUB_DIR))

import alert_webhook  # noqa: E402


class AlertWebhookDestinationTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.spool = Path(self._tmp.name) / "outbox.sqlite3"
        self.env = patch.dict(
            os.environ,
            {
                alert_webhook.SPOOL_ENV: str(self.spool),
                alert_webhook.URL_ENV: "https://alerts.invalid/hook",
                alert_webhook.KEY_ENV: "alert-key",
                alert_webhook.SMS_URL_ENV: "",
                alert_webhook.SMS_KEY_ENV: "",
            },
            clear=False,
        )
        self.env.start()

    def tearDown(self) -> None:
        self.env.stop()
        self._tmp.cleanup()

    def _rows(self):
        with alert_webhook._connect() as conn:
            return conn.execute("SELECT * FROM alerts ORDER BY created").fetchall()

    def test_inbound_sms_uses_sms_destination_when_configured(self) -> None:
        os.environ[alert_webhook.SMS_URL_ENV] = "https://sms.invalid/hook"
        with patch.object(alert_webhook, "start_alert_worker"):
            alert_webhook.post_inbound_sms_alert("incoming")
        rows = self._rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["destination"], alert_webhook.DESTINATION_SMS)

    def test_inbound_sms_falls_back_to_alert_destination_when_sms_unset(self) -> None:
        with patch.object(alert_webhook, "start_alert_worker"):
            alert_webhook.post_inbound_sms_alert("incoming")
        rows = self._rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["destination"], alert_webhook.DESTINATION_ALERT)

    def test_non_sms_alerts_remain_at_alert_destination(self) -> None:
        with patch.object(alert_webhook, "start_alert_worker"):
            alert_webhook.post_system_alert("call ended")
        rows = self._rows()
        self.assertEqual(rows[0]["destination"], alert_webhook.DESTINATION_ALERT)

    def test_legacy_spool_rows_migrate_to_alert_destination(self) -> None:
        conn = sqlite3.connect(self.spool)
        conn.execute(
            "CREATE TABLE alerts (id TEXT PRIMARY KEY, original_payload TEXT NOT NULL, "
            "payload TEXT NOT NULL, created REAL NOT NULL, due REAL NOT NULL, "
            "attempts INTEGER NOT NULL DEFAULT 0, status INTEGER, response TEXT, "
            "delivered REAL, lease TEXT)"
        )
        conn.execute(
            "INSERT INTO alerts(id, original_payload, payload, created, due) VALUES(?,?,?,?,?)",
            ("legacy", "{}", "{}", 1, 1),
        )
        conn.commit()
        conn.close()
        rows = self._rows()
        self.assertEqual(rows[0]["destination"], alert_webhook.DESTINATION_ALERT)

    def test_drain_sends_row_to_its_remembered_destination(self) -> None:
        os.environ[alert_webhook.SMS_URL_ENV] = "https://sms.invalid/hook"
        with patch.object(alert_webhook, "start_alert_worker"):
            alert_webhook.post_inbound_sms_alert("incoming")
        with patch.object(alert_webhook, "_send", return_value=(204, "")) as send:
            self.assertTrue(alert_webhook._drain_one())
        self.assertEqual(send.call_args.args[1], alert_webhook.DESTINATION_SMS)

    def test_sms_send_uses_sms_url_and_key(self) -> None:
        os.environ[alert_webhook.SMS_URL_ENV] = "https://sms.invalid/hook"
        os.environ[alert_webhook.SMS_KEY_ENV] = "sms-key"

        class Response:
            status = 204

            def read(self, _size: int) -> bytes:
                return b""

            def __enter__(self):
                return self

            def __exit__(self, *args) -> None:
                return None

        with patch.object(urllib.request, "urlopen", return_value=Response()) as urlopen:
            self.assertEqual(alert_webhook._send("{}", alert_webhook.DESTINATION_SMS), (204, ""))
        request = urlopen.call_args.args[0]
        self.assertEqual(request.full_url, os.environ[alert_webhook.SMS_URL_ENV])
        self.assertEqual(request.get_header("Authorization"), "Bearer sms-key")


if __name__ == "__main__":
    unittest.main()
