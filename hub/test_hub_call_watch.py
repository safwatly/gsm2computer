"""Phone address for the hub-side call watcher."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import hub_call_watch


class PhonePeerTests(unittest.TestCase):
    def test_moto_ts_ip_wins(self) -> None:
        env = {
            "MOTO_TS_IP": "192.0.2.8",
            "GSM2COMPUTER_CALL_END_ADB": "192.0.2.9:5555",
        }
        self.assertEqual(hub_call_watch.phone_peer(env), "192.0.2.8")

    def test_host_of_call_end_adb(self) -> None:
        env = {"GSM2COMPUTER_CALL_END_ADB": "192.0.2.9:5555"}
        self.assertEqual(hub_call_watch.phone_peer(env), "192.0.2.9")

    def test_bare_host(self) -> None:
        self.assertEqual(hub_call_watch.host_of_adb("192.0.2.9"), "192.0.2.9")

    def test_bracketed_ipv6_host(self) -> None:
        self.assertEqual(hub_call_watch.host_of_adb("[2001:db8::8]:5555"), "2001:db8::8")

    def test_neither_is_empty(self) -> None:
        self.assertEqual(hub_call_watch.phone_peer({}), "")


class TranscriptFollowupTests(unittest.TestCase):
    def test_adb_already_fired_does_not_post(self) -> None:
        with patch.object(hub_call_watch, "time") as clock, patch.object(
            hub_call_watch, "adb_fired_since", return_value=True
        ), patch.object(hub_call_watch, "post_alert_event") as post:
            clock.sleep.return_value = None
            clock.time.return_value = 100.0
            hub_call_watch.on_end(10.0, "20261109T120000Z-grokbot", "peer")
        post.assert_not_called()

    def test_fallback_posts_call_transcript_after_call_ended(self) -> None:
        import grokbot_transcript as gt

        posts: list[dict] = []

        def capture(payload):
            posts.append(dict(payload))

        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            tap = "20261109T120000Z-grokbot"
            record = folder / tap
            record.mkdir()
            gt.save_transcript_result(
                record,
                tap,
                "ok",
                [{"speaker": "caller", "text": "Hi", "timestamp": None}],
            )
            with patch.object(hub_call_watch, "time") as clock, patch.object(
                hub_call_watch, "adb_fired_since", return_value=False
            ), patch.object(hub_call_watch, "post_alert_event", side_effect=capture), patch(
                "alert_webhook.post_alert_event", side_effect=capture
            ), patch.dict(
                "os.environ", {"GSM2COMPUTER_CALL_RECORD_DIR": str(folder)}
            ):
                clock.sleep.return_value = None
                clock.time.return_value = 40.0
                hub_call_watch.on_end(10.0, tap, "peer")
        self.assertEqual([item["event"] for item in posts], ["call_ended", "call_transcript"])
        self.assertEqual(posts[1]["transcript_status"], "ok")
        self.assertEqual(posts[0]["source"], "hub_call_watch")


if __name__ == "__main__":
    unittest.main()
