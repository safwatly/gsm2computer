"""OpenClaw call recording resolution + portal streaming API."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from portal_http import EventBus, PortalApp, resolve_openclaw_recording, started_at_to_tap_stamps
from portal_store import PortalStore


class FakeWriter:
    def __init__(self) -> None:
        self.buf = bytearray()

    def write(self, data: bytes) -> None:
        self.buf.extend(data)

    async def drain(self) -> None:
        return None


def _parse(raw: bytes):
    header, _, body = raw.partition(b"\r\n\r\n")
    status = int(header.split(b"\r\n", 1)[0].split()[1])
    headers = {}
    for line in header.split(b"\r\n")[1:]:
        if b":" in line:
            k, v = line.split(b":", 1)
            headers[k.decode().lower()] = v.strip().decode()
    return status, headers, body


class ResolveTests(unittest.TestCase):
    def test_stamps(self) -> None:
        stamps = started_at_to_tap_stamps("2026-09-20T09:15:30.335Z")
        self.assertEqual(stamps[0], "20260920T091530Z")
        self.assertIn("20260920T091529Z", stamps)

    def test_resolve_session_started_at_and_escape(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            tap = root / "20260920T091530Z-webrtc-ui"
            tap.mkdir()
            mp3 = tap / "openclaw.mp3"
            mp3.write_bytes(b"ID3fake")

            self.assertEqual(
                resolve_openclaw_recording(
                    {"session_id": "20260920T091530Z-webrtc-ui", "started_at": ""},
                    root,
                ),
                mp3.resolve(),
            )
            self.assertEqual(
                resolve_openclaw_recording(
                    {"session_id": None, "tap_summary": None, "started_at": "2026-09-20T09:15:30.335Z"},
                    root,
                ),
                mp3.resolve(),
            )
            self.assertIsNone(
                resolve_openclaw_recording(
                    {"session_id": "../etc/passwd", "started_at": ""},
                    root,
                )
            )


class RecordingApiTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        self.store = PortalStore(root / "portal.sqlite")
        self.record_dir = root / "calls"
        self.record_dir.mkdir()
        tap = self.record_dir / "20260920T091530Z-webrtc-ui"
        tap.mkdir()
        (tap / "openclaw.mp3").write_bytes(b"ID3audio")
        dist = root / "dist"
        dist.mkdir()
        (dist / "index.html").write_text("<!doctype html><title>portal</title>", encoding="utf-8")
        self.app = PortalApp(
            store=self.store,
            events=EventBus(),
            dist_dir=dist,
            record_dir=self.record_dir,
        )

    async def asyncTearDown(self) -> None:
        self.store.close()
        self._tmp.cleanup()

    async def test_recording_streams_mpeg(self) -> None:
        call = self.store.add_call(
            direction="in",
            number="+15551212",
            started_at="2026-09-20T09:15:30.335Z",
            duration_sec=60,
        )
        linked = self.store.link_call_to_tap(
            {"id": "20260920T091530Z-webrtc-ui", "mixes": {"openclaw.mp3": {"bytes": 8}}}
        )
        self.assertIsNotNone(linked)
        self.assertEqual(linked["session_id"], "20260920T091530Z-webrtc-ui")

        writer = FakeWriter()
        result = await self.app.dispatch(
            "GET",
            f"/portal/api/calls/{call['id']}/recording",
            {},
            b"",
            writer,
        )
        self.assertEqual(result, "handled")
        status, headers, body = _parse(bytes(writer.buf))
        self.assertEqual(status, 200)
        self.assertEqual(headers["content-type"], "audio/mpeg")
        self.assertEqual(body, b"ID3audio")

        writer = FakeWriter()
        await self.app.dispatch("GET", "/portal/api/calls/missing/recording", {}, b"", writer)
        status, _, _ = _parse(bytes(writer.buf))
        self.assertEqual(status, 404)


if __name__ == "__main__":
    unittest.main()
