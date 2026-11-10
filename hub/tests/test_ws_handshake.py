"""Device call websockets are logged and are not rejected for a Talk-page heal."""
from __future__ import annotations

import importlib.util
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

_TMP = Path(tempfile.mkdtemp(prefix="gsm-hs-"))
os.environ["GSM2COMPUTER_PORTAL_DB"] = str(_TMP / "portal.sqlite")
os.environ["GSM2COMPUTER_DEVICE_TOKENS"] = str(_TMP / "device-tokens.json")
os.environ["GSM2COMPUTER_LINK_GAP_LOG"] = str(_TMP / "gaps.jsonl")
os.environ["GSM2COMPUTER_OPENCLAW_TALK"] = "off"
os.environ["GSM2COMPUTER_PUBLIC_ENABLE"] = "0"
os.environ.setdefault("GSM2COMPUTER_HUB_HOST", "127.0.0.1")

HUB_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HUB_DIR))

_HUB_SPEC = importlib.util.spec_from_file_location("gsm2computer_hub_hs", HUB_DIR / "hub.py")
assert _HUB_SPEC and _HUB_SPEC.loader
hub = importlib.util.module_from_spec(_HUB_SPEC)
_HUB_SPEC.loader.exec_module(hub)

from talk_chromium import OpenClawTalkUI  # noqa: E402


class _Writer:
    def __init__(self) -> None:
        self.buf = bytearray()

    def get_extra_info(self, key: str):
        if key == "peername":
            return ("203.0.113.8", 44000)
        return None

    def write(self, data: bytes) -> None:
        self.buf += data

    async def drain(self) -> None:
        return None


def _headers() -> dict:
    return {"sec-websocket-key": "dGhlIHNhbXBsZSBub25jZQ=="}


class HandshakeTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        if hub.live_call.busy:
            hub.live_call.release()
        hub.active_bridge = None

    async def test_page_heal_does_not_503_or_block_the_handshake(self) -> None:
        ui = OpenClawTalkUI()
        ui.page_heal_in_progress = True
        called = {"n": 0}

        def blocked(*_args, **_kwargs):
            called["n"] += 1
            time.sleep(0.5)
            return {}

        writer = _Writer()
        device = hub._current_device_id.set("moto-g84")
        try:
            with patch.object(hub.live_call, "claim", return_value=False), patch(
                "talk_chromium._cdp_http", side_effect=blocked
            ), patch.object(hub, "get_talk_ui", return_value=ui), self.assertLogs(
                "gsm2computer-hub", level="INFO"
            ) as logs:
                started = time.monotonic()
                await hub.handle_websocket(None, writer, _headers(), "/")  # type: ignore[arg-type]
                elapsed = time.monotonic() - started
        finally:
            hub._current_device_id.reset(device)
        body = bytes(writer.buf)
        self.assertNotIn(b"503", body)
        self.assertIn(b"409", body)
        self.assertLess(elapsed, 0.3)
        self.assertEqual(0, called["n"])
        text = "\n".join(logs.output)
        self.assertIn("device ws handshake attempt", text)
        self.assertIn("device ws handshake rejected", text)
        self.assertIn("reason=call slot raced", text)
        self.assertIn("peer=('203.0.113.8', 44000)", text)
        self.assertIn("device=moto-g84", text)
        self.assertIn("entrance=tailscale", text)

    async def test_admission_error_is_logged_with_peer_and_device(self) -> None:
        writer = _Writer()
        device = hub._current_device_id.set("pixel-7")

        async def boom(*_args, **_kwargs):
            raise RuntimeError("upgrade blew up")

        try:
            with patch.object(hub, "admit_relink", side_effect=boom), self.assertLogs(
                "gsm2computer-hub", level="INFO"
            ) as logs:
                with self.assertRaises(RuntimeError):
                    await hub.handle_websocket(None, writer, _headers(), "/ws")  # type: ignore[arg-type]
        finally:
            hub._current_device_id.reset(device)
        text = "\n".join(logs.output)
        self.assertIn("device ws handshake attempt", text)
        self.assertIn("device ws handshake error", text)
        self.assertIn("upgrade blew up", text)
        self.assertIn("device=pixel-7", text)
        self.assertIn("peer=('203.0.113.8', 44000)", text)
        self.assertIn("path=/ws", text)

    async def test_accepted_handshake_is_logged_before_setup_fails(self) -> None:
        writer = _Writer()
        device = hub._current_device_id.set("moto-g84")
        try:
            with patch.object(hub.CallTap, "maybe_open", side_effect=RuntimeError("stop after 101")), self.assertLogs(
                "gsm2computer-hub", level="INFO"
            ) as logs:
                with self.assertRaises(RuntimeError):
                    await hub.handle_websocket(None, writer, _headers(), "/")  # type: ignore[arg-type]
        finally:
            hub._current_device_id.reset(device)
            if hub.live_call.busy:
                hub.live_call.release()
            hub.active_bridge = None
            hub.active_ws_writer = None
        body = bytes(writer.buf)
        self.assertTrue(body.startswith(b"HTTP/1.1 101"))
        self.assertNotIn(b"503", body)
        text = "\n".join(logs.output)
        self.assertIn("device ws handshake accepted", text)
        self.assertIn("device=moto-g84", text)
        self.assertNotIn("device ws handshake error", text)

    def test_health_status_is_the_cache_not_a_live_probe(self) -> None:
        class Fake:
            def __init__(self) -> None:
                self.scheduled = False
                self.idle_check = None

            def schedule_maintenance(self) -> None:
                self.scheduled = True

            def health_snapshot(self) -> dict:
                return {"cdp": None, "age_s": 1.5, "page": None, "error": "cached"}

            async def health(self) -> dict:
                raise AssertionError("GET /health must not probe CDP")

        fake = Fake()
        with patch.object(hub, "get_talk_ui", return_value=fake):
            snap = hub.talk_status_for_health()
        self.assertTrue(fake.scheduled)
        self.assertIsNone(snap["cdp"])
        self.assertEqual(1.5, snap["age_s"])
        self.assertEqual("cached", snap["error"])
        self.assertTrue(callable(fake.idle_check))
        self.assertIs(fake.idle_check(), True)


if __name__ == "__main__":
    unittest.main()
