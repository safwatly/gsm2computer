import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

HUB_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HUB_DIR))

from portal_store import PortalStore, normalize_peer  # noqa: E402


class PortalStoreTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = PortalStore(Path(self.tmp.name) / "portal.sqlite")

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def test_normalize_peer(self):
        self.assertEqual(normalize_peer("0015551212"), "+15551212")
        self.assertEqual(normalize_peer(" +1 (555) 987-6 "), "+15559876")

    def test_threads_order_latest_first(self):
        self.store.add_message("in", "+15551111", "older", ts="2026-01-01T00:00:00Z")
        self.store.add_message("in", "+15552222", "newest", ts="2026-09-19T12:00:00Z")
        self.store.add_message("in", "+15551111", "mid", ts="2026-03-01T00:00:00Z")
        threads = self.store.list_threads()
        self.assertEqual([t["peer"] for t in threads], ["+15552222", "+15551111"])
        self.assertEqual(threads[0]["lastBody"], "newest")
        self.assertEqual(threads[1]["lastBody"], "mid")

    def test_messages_sorted_oldest_first(self):
        self.store.add_message("in", "+1555", "a", ts="2026-01-01T00:00:00Z")
        self.store.add_message("in", "+1555", "b", ts="2026-01-02T00:00:00Z")
        bodies = [m["body"] for m in self.store.list_messages("+1555")]
        self.assertEqual(bodies, ["a", "b"])

    def test_outbound_queue_and_ack(self):
        queued = self.store.enqueue_outbound("+15553333", "ping")
        self.assertEqual(queued["message"]["status"], "queued")
        self.assertEqual(queued["status"], "pending")
        self.assertEqual(len(self.store.list_outbox_pending()), 1)
        acked = self.store.ack_outbox(queued["id"])
        self.assertIsNotNone(acked)
        self.assertEqual(acked["status"], "sent")
        self.assertEqual(self.store.list_outbox_pending(), [])
        self.assertEqual(self.store.list_messages("+15553333")[0]["status"], "sent")

    def test_failed_ack_keeps_the_carrier_error(self):
        queued = self.store.enqueue_outbound("+201001234567", "hello")
        self.store.claim_outbox_for_send()
        reason = "result=40 MODEM_ERR rp=38"
        acked = self.store.ack_outbox(queued["id"], status="failed", error=reason)
        self.assertEqual(acked["status"], "failed")
        self.assertEqual(acked["error"], reason)
        message = self.store.list_messages("+201001234567")[0]
        self.assertEqual(message["status"], "failed")
        self.assertEqual(message["error"], reason)
        again = self.store.ack_outbox(queued["id"], status="sent")
        self.assertEqual(again["status"], "failed")

    def test_stale_sending_lease_times_out_and_is_not_resent(self):
        queued = self.store.enqueue_outbound("+201001234567", "hello")
        claimed = self.store.claim_outbox_for_send()
        self.assertEqual(claimed[0]["status"], "sending")
        self.store._conn.execute(
            "UPDATE outbox SET claimed_at = ? WHERE id = ?",
            ("2026-01-01T00:00:00Z", queued["id"]),
        )
        self.store._conn.commit()
        message = self.store.list_messages("+201001234567")[0]
        self.assertEqual(message["status"], "timeout")
        self.assertEqual(message["error"], "send lease expired")
        expired = self.store.take_lease_timeouts()
        self.assertEqual(expired[0]["id"], queued["id"])
        self.assertEqual(expired[0]["status"], "timeout")
        self.assertEqual(self.store.list_outbox_pending(), [])
        self.assertEqual(self.store.claim_outbox_for_send(), [])
        late = self.store.ack_outbox(queued["id"], status="sent")
        self.assertEqual(late["status"], "timeout")

    def test_fresh_send_lease_stays_queued(self):
        queued = self.store.enqueue_outbound("+201001234567", "hello")
        self.store.claim_outbox_for_send()
        recent = (datetime.now(timezone.utc) - timedelta(seconds=30)).isoformat().replace("+00:00", "Z")
        self.store._conn.execute(
            "UPDATE outbox SET claimed_at = ? WHERE id = ?",
            (recent, queued["id"]),
        )
        self.store._conn.commit()
        self.assertEqual(self.store.list_messages("+201001234567")[0]["status"], "queued")
        self.assertEqual(self.store.claim_outbox_for_send(), [])

    def test_timeout_ack_is_not_stored_as_sent(self):
        queued = self.store.enqueue_outbound("+201001234567", "hello")
        self.store.claim_outbox_for_send()
        acked = self.store.ack_outbox(queued["id"], status="timeout", error="send result timeout")
        self.assertEqual(acked["status"], "timeout")
        self.assertEqual(acked["error"], "send result timeout")
        self.assertEqual(self.store.list_messages("+201001234567")[0]["status"], "timeout")

    def test_blank_peer_is_not_a_number(self):
        item = self.store.add_message("in", "  ", "hi")
        self.assertEqual(item["peer"], "")


if __name__ == "__main__":
    unittest.main()
