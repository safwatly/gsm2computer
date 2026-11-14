import asyncio
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

os.environ["GSM2COMPUTER_OPENCLAW_TALK"] = "off"

HUB_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HUB_DIR))

import hub as hub_mod  # noqa: E402
from portal_store import reset_for_tests  # noqa: E402
from hub import handle_client, split_request_target  # noqa: E402


def _encode_request(method: str, target: str, body: bytes = b"", extra_headers: list[str] | None = None) -> bytes:
    headers = [
        f"{method} {target} HTTP/1.1",
        "Host: 127.0.0.1",
        f"Content-Length: {len(body)}",
        "Connection: close",
    ]
    if body:
        headers.append("Content-Type: application/json")
    if extra_headers:
        headers.extend(extra_headers)
    return ("\r\n".join(headers) + "\r\n\r\n").encode("ascii") + body


def _parse_http(blob: bytes) -> tuple[int, dict[str, str], bytes]:
    header_blob, _, payload = blob.partition(b"\r\n\r\n")
    lines = header_blob.decode("iso-8859-1").split("\r\n")
    status = int(lines[0].split()[1])
    headers: dict[str, str] = {}
    for line in lines[1:]:
        if ":" not in line:
            continue
        name, value = line.split(":", 1)
        headers[name.strip().lower()] = value.strip()
    return status, headers, payload


class SplitTargetTest(unittest.TestCase):
    def test_query(self):
        path, query = split_request_target("/portal/api/messages?peer=%2B1555")
        self.assertEqual(path, "/portal/api/messages")
        self.assertEqual(query.get("peer"), "+1555")


class PortalHttpTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        db = Path(self.tmp.name) / "portal.sqlite"
        os.environ["GSM2COMPUTER_PORTAL_DB"] = str(db)
        reset_for_tests(db)
        self.server = await asyncio.start_server(handle_client, "127.0.0.1", 0)
        self.port = self.server.sockets[0].getsockname()[1]

    async def asyncTearDown(self):
        self.server.close()
        await self.server.wait_closed()
        self.tmp.cleanup()

    async def _exchange(self, request: bytes) -> tuple[int, dict[str, str], bytes]:
        reader, writer = await asyncio.open_connection("127.0.0.1", self.port)
        writer.write(request)
        await writer.drain()
        blob = await asyncio.wait_for(reader.read(), timeout=5)
        writer.close()
        await writer.wait_closed()
        return _parse_http(blob)

    async def test_sms_persists_and_lists_latest_thread_first(self):
        first = json.dumps({"from": "+15551111", "body": "older", "receivedAt": "2026-01-01T00:00:00Z"}).encode()
        second = json.dumps({"from": "+15552222", "body": "newest", "receivedAt": "2026-09-19T12:00:00Z"}).encode()
        status, _, body = await self._exchange(_encode_request("POST", "/sms", first))
        self.assertEqual(status, 200)
        self.assertTrue(json.loads(body)["ok"])
        await self._exchange(_encode_request("POST", "/sms", second))
        status, _, body = await self._exchange(_encode_request("GET", "/portal/api/threads"))
        self.assertEqual(status, 200)
        threads = json.loads(body)
        self.assertEqual(threads[0]["peer"], "+15552222")
        self.assertEqual(threads[0]["lastBody"], "newest")
        status, _, body = await self._exchange(_encode_request("GET", "/portal/api/messages?peer=%2B15552222"))
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)[0]["body"], "newest")

    async def test_send_queues_outbox_ack_updates_status(self):
        payload = json.dumps({"to": "+15553333", "body": "desk ping"}).encode()
        status, _, body = await self._exchange(_encode_request("POST", "/portal/api/messages/send", payload))
        self.assertEqual(status, 201)
        rec = json.loads(body)["message"]
        self.assertEqual(rec["status"], "queued")
        status, _, body = await self._exchange(_encode_request("GET", "/sms/outbox"))
        self.assertEqual(status, 200)
        item = json.loads(body)["items"][0]
        status, _, body = await self._exchange(
            _encode_request("POST", f"/sms/outbox/{item['id']}/ack", b"{}")
        )
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["item"]["status"], "sent")

    async def test_failed_and_timeout_acks_are_not_sent(self):
        payload = json.dumps({"to": "+201009998888", "body": "carrier"}).encode()
        status, _, body = await self._exchange(_encode_request("POST", "/portal/api/messages/send", payload))
        self.assertEqual(status, 201)
        item_id = json.loads(body)["id"]
        await self._exchange(_encode_request("GET", "/sms/outbox"))
        ack = json.dumps({"status": "failed", "error": "result=40 MODEM_ERR rp=38"}).encode()
        status, _, body = await self._exchange(_encode_request("POST", f"/sms/outbox/{item_id}/ack", ack))
        self.assertEqual(status, 200)
        item = json.loads(body)["item"]
        self.assertEqual(item["status"], "failed")
        self.assertEqual(item["error"], "result=40 MODEM_ERR rp=38")
        status, _, body = await self._exchange(
            _encode_request("GET", "/portal/api/messages?peer=%2B201009998888")
        )
        message = json.loads(body)[0]
        self.assertEqual(message["status"], "failed")
        self.assertEqual(message["error"], "result=40 MODEM_ERR rp=38")

        later = json.dumps({"to": "+201009998888", "body": "later"}).encode()
        status, _, body = await self._exchange(_encode_request("POST", "/portal/api/messages/send", later))
        timed_id = json.loads(body)["id"]
        await self._exchange(_encode_request("GET", "/sms/outbox"))
        timeout_ack = json.dumps({"status": "timeout", "error": "send result timeout"}).encode()
        status, _, body = await self._exchange(_encode_request("POST", f"/sms/outbox/{timed_id}/ack", timeout_ack))
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["item"]["status"], "timeout")

    async def test_expired_send_lease_publishes_timeout(self):
        payload = json.dumps({"to": "+201001112233", "body": "lease"}).encode()
        status, _, body = await self._exchange(_encode_request("POST", "/portal/api/messages/send", payload))
        self.assertEqual(status, 201)
        item_id = json.loads(body)["id"]
        await self._exchange(_encode_request("GET", "/sms/outbox"))
        hub_mod.portal_store._conn.execute(
            "UPDATE outbox SET claimed_at = ? WHERE id = ?",
            ("2026-01-01T00:00:00Z", item_id),
        )
        hub_mod.portal_store._conn.commit()
        q = hub_mod.portal_events.subscribe()
        try:
            status, _, body = await self._exchange(_encode_request("GET", "/sms/outbox"))
            self.assertEqual(status, 200)
            self.assertEqual(json.loads(body)["items"], [])
            event_type, data = q.get_nowait()
            self.assertEqual(event_type, "message")
            self.assertEqual(data["id"], item_id)
            self.assertEqual(data["status"], "timeout")
            self.assertEqual(data["error"], "send lease expired")
        finally:
            hub_mod.portal_events.unsubscribe(q)

    async def test_sse_emits_inbound_message(self):
        reader, writer = await asyncio.open_connection("127.0.0.1", self.port)
        writer.write(_encode_request("GET", "/portal/api/events", extra_headers=["Accept: text/event-stream"]))
        await writer.drain()
        header = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), timeout=5)
        self.assertIn(b"200", header.split(b"\r\n", 1)[0])
        # connected comment
        await asyncio.wait_for(reader.readline(), timeout=5)
        await asyncio.wait_for(reader.readline(), timeout=5)
        sms = json.dumps({"from": "+15554444", "body": "sse-hi", "receivedAt": "2026-09-19T12:00:00Z"}).encode()
        await self._exchange(_encode_request("POST", "/sms", sms))
        event_line = await asyncio.wait_for(reader.readline(), timeout=5)
        data_line = await asyncio.wait_for(reader.readline(), timeout=5)
        self.assertTrue(event_line.startswith(b"event: message"))
        payload = json.loads(data_line.decode("utf-8").split("data:", 1)[1].strip())
        self.assertEqual(payload["body"], "sse-hi")
        self.assertEqual(payload["peer"], "+15554444")
        writer.close()
        await writer.wait_closed()

    async def test_message_window_and_per_viewer_read(self):
        for index, body in enumerate(("one", "two", "three")):
            payload = json.dumps(
                {
                    "from": "+15556666",
                    "body": body,
                    "receivedAt": f"2026-09-19T12:00:0{index}Z",
                }
            ).encode()
            status, _, _ = await self._exchange(_encode_request("POST", "/sms", payload))
            self.assertEqual(status, 200)
        status, _, body = await self._exchange(
            _encode_request("GET", "/portal/api/messages?peer=%2B15556666")
        )
        self.assertEqual(status, 200)
        listed = json.loads(body)
        self.assertIsInstance(listed, list)
        self.assertEqual([item["body"] for item in listed], ["one", "two", "three"])

        status, _, body = await self._exchange(
            _encode_request("GET", "/portal/api/messages?peer=%2B15556666&limit=2")
        )
        self.assertEqual(status, 200)
        page = json.loads(body)
        self.assertEqual([item["body"] for item in page["messages"]], ["two", "three"])
        self.assertTrue(page["hasOlder"])
        self.assertFalse(page["hasNewer"])
        cursor = page["messages"][0]
        older_url = (
            "/portal/api/messages?peer=%2B15556666&limit=2"
            f"&before={cursor['ts']}&before_id={cursor['id']}"
        )
        status, _, body = await self._exchange(_encode_request("GET", older_url))
        self.assertEqual(status, 200)
        older = json.loads(body)
        self.assertEqual([item["body"] for item in older["messages"]], ["one"])
        self.assertFalse(older["hasOlder"])

        newest = listed[-1]
        since_url = (
            "/portal/api/messages?peer=%2B15556666&limit=50"
            f"&since={listed[0]['ts']}&since_id={listed[0]['id']}"
        )
        status, _, body = await self._exchange(_encode_request("GET", since_url))
        self.assertEqual([item["body"] for item in json.loads(body)["messages"]], ["two", "three"])

        mark = json.dumps(
            {"viewer": "human-owner", "peer": "+15556666", "ts": newest["ts"], "id": newest["id"]}
        ).encode()
        status, _, body = await self._exchange(_encode_request("POST", "/portal/api/messages/read", mark))
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["unread"], 0)

        await self._exchange(_encode_request("GET", "/portal/api/messages?peer=%2B15556666"))
        status, _, body = await self._exchange(_encode_request("GET", "/portal/api/threads?viewer=ahmed"))
        ahmed = next(item for item in json.loads(body) if item["peer"] == "+15556666")
        self.assertEqual(ahmed["unread"], 3)
        self.assertIsNone(ahmed["readTs"])
        status, _, body = await self._exchange(
            _encode_request("GET", "/portal/api/threads?viewer=human-owner")
        )
        owner = next(item for item in json.loads(body) if item["peer"] == "+15556666")
        self.assertEqual(owner["unread"], 0)
        status, _, body = await self._exchange(_encode_request("GET", "/portal/api/threads"))
        plain = next(item for item in json.loads(body) if item["peer"] == "+15556666")
        self.assertEqual(plain["unread"], 0)
        self.assertNotIn("readTs", plain)

        status, _, body = await self._exchange(
            _encode_request("GET", "/portal/api/messages?peer=%2B15556666&before=1&since=2")
        )
        self.assertEqual(status, 400)
        status, _, body = await self._exchange(
            _encode_request("POST", "/portal/api/messages/read", b'{"viewer":"bad viewer","peer":"+15556666","ts":"2026-01-01T00:00:00Z"}')
        )
        self.assertEqual(status, 400)

    async def test_health_still_ok(self):
        status, _, body = await self._exchange(_encode_request("GET", "/health"))
        self.assertEqual(status, 200)
        self.assertTrue(json.loads(body)["ok"])


if __name__ == "__main__":
    unittest.main()
