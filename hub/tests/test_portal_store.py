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

    def _seed_thread(self, peer="+15557777", count=5):
        rows = []
        for index in range(count):
            rows.append(
                self.store.add_message(
                    "in",
                    peer,
                    f"m{index}",
                    ts=f"2026-04-01T00:00:{index:02d}Z",
                    msg_id=f"id-{index:02d}",
                )
            )
        return rows

    def test_page_is_the_newest_window_and_walks_older(self):
        self._seed_thread(count=5)
        full = self.store.list_messages("+15557777")
        self.assertEqual([m["body"] for m in full], ["m0", "m1", "m2", "m3", "m4"])
        page = self.store.page_messages("+15557777", limit=2)
        self.assertEqual([m["body"] for m in page["messages"]], ["m3", "m4"])
        self.assertTrue(page["hasOlder"])
        self.assertFalse(page["hasNewer"])
        oldest = page["messages"][0]
        older = self.store.page_messages(
            "+15557777", before=oldest["ts"], before_id=oldest["id"], limit=2
        )
        self.assertEqual([m["body"] for m in older["messages"]], ["m1", "m2"])
        self.assertTrue(older["hasOlder"])
        self.assertTrue(older["hasNewer"])
        newest = full[-1]
        since = self.store.page_messages("+15557777", since=newest["ts"], since_id=newest["id"], limit=10)
        self.assertEqual(since["messages"], [])
        self.assertFalse(since["hasNewer"])

    def test_same_timestamp_cursor_uses_id_tie_break(self):
        peer = "+15558888"
        for msg_id in ("a", "b", "c"):
            self.store.add_message("in", peer, msg_id, ts="2026-05-01T00:00:00Z", msg_id=msg_id)
        page = self.store.page_messages(peer, limit=2)
        self.assertEqual([m["id"] for m in page["messages"]], ["b", "c"])
        older = self.store.page_messages(peer, before="2026-05-01T00:00:00Z", before_id="b", limit=2)
        self.assertEqual([m["id"] for m in older["messages"]], ["a"])

    def test_listing_messages_does_not_mark_them_read(self):
        self._seed_thread(count=3)
        self.store.list_messages("+15557777")
        self.store.page_messages("+15557777", limit=1)
        threads = self.store.list_threads("owner")
        self.assertEqual(threads[0]["unread"], 3)
        self.assertIsNone(threads[0]["readTs"])
        self.assertEqual(self.store.list_threads()[0]["unread"], 0)
        self.assertNotIn("readTs", self.store.list_threads()[0])

    def test_read_cursors_are_per_viewer_and_only_move_forward(self):
        rows = self._seed_thread(count=3)
        self.store.add_message("out", "+15557777", "sent-by-us", ts="2026-04-01T00:00:03Z", msg_id="out-1")
        owner = self.store.mark_read("owner", "+15557777", rows[1]["ts"], rows[1]["id"])
        self.assertEqual(owner["unread"], 1)
        self.assertEqual(self.store.list_threads("ahmed")[0]["unread"], 3)
        rewound = self.store.mark_read("owner", "+15557777", rows[0]["ts"], rows[0]["id"])
        self.assertEqual(rewound["readId"], rows[1]["id"])
        self.assertEqual(rewound["unread"], 1)
        done = self.store.mark_read("owner", "+15557777", rows[2]["ts"], rows[2]["id"])
        self.assertEqual(done["unread"], 0)
        self.assertEqual(self.store.list_threads("ahmed")[0]["unread"], 3)
        with self.assertRaises(ValueError):
            self.store.page_messages("+15557777", before="2026-04-01T00:00:00Z", since="2026-04-01T00:00:01Z")
        with self.assertRaises(ValueError):
            self.store.mark_read("not a viewer", "+15557777", rows[2]["ts"], rows[2]["id"])


if __name__ == "__main__":
    unittest.main()
