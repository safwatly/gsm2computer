"""Interactions created by folding the live hub into the public entrance.

Covers the bridge contract from PR #28 (POST /token and the WebSocket on the
same origin, X-Gsm-Device-Token, X-Gsm-Call-Session, 409 with and without a
session id, POST /calls stamping path) together with the live hub's G2 uplink
and Grok Bot session fields. PipeWire and patchbay are stubbed; the bytes on
the socket are the real hub.
"""
from __future__ import annotations

import asyncio
import importlib.util
import json
import os
import struct
import sys
import tempfile
import unittest
from pathlib import Path

_TMP = Path(tempfile.mkdtemp(prefix="gsm-reconcile-"))
os.environ["GSM2COMPUTER_PORTAL_DB"] = str(_TMP / "portal.sqlite")
os.environ["GSM2COMPUTER_DEVICE_TOKENS"] = str(_TMP / "device-tokens.json")
os.environ["GSM2COMPUTER_LINK_GAP_LOG"] = str(_TMP / "gaps.jsonl")
os.environ["GSM2COMPUTER_OPENCLAW_TALK"] = "off"
os.environ["GSM2COMPUTER_AUTO_MODE"] = "openclaw"
os.environ["GSM2COMPUTER_PUBLIC_ENABLE"] = "0"
os.environ.setdefault("GSM2COMPUTER_HUB_HOST", "127.0.0.1")

HUB_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HUB_DIR))

import grokbot_voice  # noqa: E402
from device_auth import Entrance, generate_token, write_device_token  # noqa: E402

_SPEC = importlib.util.spec_from_file_location("gsm2computer_hub_reconcile", HUB_DIR / "hub.py")
assert _SPEC and _SPEC.loader
hub = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(hub)

TOKEN = generate_token()
DEVICE = "moto-g84"
WS_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
WS_KEY = "dGhlIHNhbXBsZSBub25jZQ=="


def _g2(rate: int = 16000, samples: int = 80) -> bytes:
    pcm = struct.pack("<" + "h" * samples, *([0x1234] * samples))
    return b"G2" + bytes((1, 1)) + rate.to_bytes(2, "big") + b"\x00\x00" + pcm


class _Client:
    def __init__(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter, leftover: bytes) -> None:
        self.reader = reader
        self.writer = writer
        self.buf = bytearray(leftover)

    async def _read_exact(self, n: int) -> bytes:
        while len(self.buf) < n:
            chunk = await asyncio.wait_for(self.reader.read(4096), 5)
            if not chunk:
                raise ConnectionError("socket closed")
            self.buf += chunk
        out = bytes(self.buf[:n])
        del self.buf[:n]
        return out

    async def read_frame(self) -> tuple[int, bytes]:
        header = await self._read_exact(2)
        opcode = header[0] & 0x0F
        length = header[1] & 0x7F
        if length == 126:
            length = int.from_bytes(await self._read_exact(2), "big")
        elif length == 127:
            length = int.from_bytes(await self._read_exact(8), "big")
        payload = await self._read_exact(length) if length else b""
        return opcode, payload

    async def read_text(self) -> str:
        deadline = asyncio.get_running_loop().time() + 5
        while asyncio.get_running_loop().time() < deadline:
            opcode, payload = await self.read_frame()
            if opcode == 0x1:
                return payload.decode()
            if opcode == 0x8:
                raise AssertionError(f"hub closed before a text frame: {payload!r}")
        raise AssertionError("no text frame")

    def send(self, opcode: int, payload: bytes) -> None:
        mask = os.urandom(4)
        masked = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
        length = len(payload)
        if length < 126:
            header = bytes((0x80 | opcode, 0x80 | length))
        elif length < 65536:
            header = bytes((0x80 | opcode, 0x80 | 126)) + length.to_bytes(2, "big")
        else:
            raise AssertionError("frame too big")
        self.writer.write(header + mask + masked)

    def close_tcp(self) -> None:
        self.writer.close()

    def send_close(self, code: int = 1000, reason: str = "bye") -> None:
        self.send(0x8, code.to_bytes(2, "big") + reason.encode())


async def _open(port: int, headers: dict[str, str], path: str = "/") -> tuple[int, _Client | None]:
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    lines = [
        f"GET {path} HTTP/1.1",
        "Host: 127.0.0.1",
        "Upgrade: websocket",
        "Connection: Upgrade",
        f"Sec-WebSocket-Key: {WS_KEY}",
        "Sec-WebSocket-Version: 13",
    ]
    for name, value in headers.items():
        lines.append(f"{name}: {value}")
    writer.write(("\r\n".join(lines) + "\r\n\r\n").encode("ascii"))
    await writer.drain()
    buf = bytearray()
    while b"\r\n\r\n" not in buf:
        chunk = await asyncio.wait_for(reader.read(4096), 5)
        if not chunk:
            break
        buf += chunk
    head, _, rest = bytes(buf).partition(b"\r\n\r\n")
    status = int(head.split(b"\r\n", 1)[0].split()[1])
    if status != 101:
        writer.close()
        await writer.wait_closed()
        return status, None
    return status, _Client(reader, writer, rest)


async def _http(port: int, method: str, path: str, headers: dict[str, str] | None = None, body: bytes = b"") -> tuple[int, bytes]:
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    lines = [
        f"{method} {path} HTTP/1.1",
        "Host: 127.0.0.1",
        f"Content-Length: {len(body)}",
        "Connection: close",
    ]
    for name, value in (headers or {}).items():
        lines.append(f"{name}: {value}")
    writer.write(("\r\n".join(lines) + "\r\n\r\n").encode("ascii") + body)
    await writer.drain()
    raw = await reader.read()
    writer.close()
    await writer.wait_closed()
    head, _, payload = raw.partition(b"\r\n\r\n")
    status = int(head.split(b"\r\n", 1)[0].split()[1])
    return status, payload


class ReconcileInteractionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        path = Path(os.environ["GSM2COMPUTER_DEVICE_TOKENS"])
        if path.exists():
            path.unlink()
        write_device_token(path, DEVICE, TOKEN)
        hub.TOKENS.reload_if_needed()
        if hub.live_call.busy:
            hub.live_call.release()
        hub.relink_gate.close()
        hub._finished_call_entrances.clear()
        hub._finished_call_order.clear()
        hub._active_entrances.clear()
        self.offers: list[tuple[bytes, str, int]] = []
        self._orig = (
            hub.PipewireBridge.start,
            hub.PipewireBridge.offer_uplink,
            hub.set_switchboard_mode,
            hub.CallTap.maybe_open,
            hub._grokbot_call_start,
            grokbot_voice.GrokBotVoice.stop,
            grokbot_voice.GrokBotVoice.stop_later,
        )
        offers = self.offers

        async def fake_start(bridge, ws_writer, monitor=None, record_downlink=True):
            del monitor, record_downlink
            bridge._out_writer = None
            return None

        def fake_offer(bridge, audio, fmt="", rate=0):
            del bridge
            offers.append((bytes(audio), str(fmt or ""), int(rate or 0)))

        async def fake_mode(mode: str):
            hub.current_mode = mode
            return {"ok": True, "applied": mode}

        def fake_tap(cls, *, loopback, mode, e2e=False):
            del cls, loopback, mode, e2e
            return None

        async def fake_grok(voice, tap, is_e2e):
            del voice, tap, is_e2e
            return {"ok": True, "rc": 0}

        async def fake_stop(voice):
            voice.live = False
            return {"ok": True, "rc": 0, "meaning": "ok", "ms": 0}

        def fake_stop_later(voice, after=None):
            del voice, after

            async def _done() -> None:
                return None

            return asyncio.create_task(_done())

        hub.PipewireBridge.start = fake_start
        hub.PipewireBridge.offer_uplink = fake_offer
        hub.set_switchboard_mode = fake_mode
        hub.CallTap.maybe_open = classmethod(fake_tap)
        hub._grokbot_call_start = fake_grok
        grokbot_voice.GrokBotVoice.stop = fake_stop
        grokbot_voice.GrokBotVoice.stop_later = fake_stop_later
        self.public = Entrance("public", "127.0.0.1", 0, True)
        self.tailscale = Entrance("tailscale", "127.0.0.1", 0, False)
        self.servers = [
            await hub.listen_entrance(self.public),
            await hub.listen_entrance(self.tailscale),
        ]
        hub._active_entrances.extend([self.public, self.tailscale])
        self.clients: list[_Client] = []

    async def asyncTearDown(self) -> None:
        for client in self.clients:
            try:
                client.close_tcp()
            except Exception:
                pass
        for server in self.servers:
            server.close()
            await server.wait_closed()
        (
            hub.PipewireBridge.start,
            hub.PipewireBridge.offer_uplink,
            hub.set_switchboard_mode,
            hub.CallTap.maybe_open,
            hub._grokbot_call_start,
            grokbot_voice.GrokBotVoice.stop,
            grokbot_voice.GrokBotVoice.stop_later,
        ) = self._orig
        hub.GROKBOT_ON_CALL = False
        hub.AUTO_MODE_ON_CALL = "openclaw"
        hub.relink_gate.close()
        if hub.live_call.busy:
            hub.live_call.release()
        hub._active_entrances.clear()

    def test_g2_frame_parses(self) -> None:
        frame = _g2(16000, 4)
        parsed = hub.parse_uplink_binary(frame)
        self.assertIsNotNone(parsed)
        pcm, fmt, rate = parsed
        self.assertEqual(fmt, "audio/pcm")
        self.assertEqual(rate, 16000)
        self.assertEqual(pcm, struct.pack("<hhhh", 0x1234, 0x1234, 0x1234, 0x1234))
        self.assertIsNone(hub.parse_uplink_binary(b"G1" + b"\x00" * 8))

    async def test_bridge_contract_g2_uplink_and_grokbot_path(self) -> None:
        """PR #28 dial shape, live G2 uplink, then the same call in grokbot mode."""
        pub = self.public.listen_port
        ts = self.tailscale.listen_port
        good = {"X-Gsm-Device-Token": TOKEN}

        status, _ = await _http(pub, "POST", "/token", body=b"")
        self.assertEqual(status, 401)
        status, body = await _http(pub, "POST", "/token", good, b"")
        self.assertEqual(status, 200)
        minted = json.loads(body)["value"]
        self.assertEqual(minted, "hub-token")

        status, _client = await _open(pub, {"X-Gsm-Device-Token": "x" * 32})
        self.assertEqual(status, 401)

        status, client = await _open(
            pub,
            {**good, "Authorization": f"Bearer {minted}"},
        )
        self.assertEqual(status, 101)
        assert client is not None
        self.clients.append(client)
        updated = json.loads(await client.read_text())
        self.assertEqual(updated["type"], "session.updated")
        session_id = updated["session"]["id"]
        self.assertTrue(session_id.startswith("hub-"))
        self.assertEqual(updated["session"]["entrance"], "public")
        self.assertEqual(updated["session"]["talk"], "off")

        client.send(0x2, _g2())
        await client.writer.drain()
        for _ in range(30):
            if self.offers:
                break
            await asyncio.sleep(0.05)
        self.assertTrue(self.offers, "G2 uplink was not offered to the bridge")
        audio, fmt, rate = self.offers[-1]
        self.assertEqual(fmt, "audio/pcm")
        self.assertEqual(rate, 16000)
        self.assertGreaterEqual(len(audio), 2)
        self.assertEqual(hub.live_call.session_id, session_id)
        self.assertEqual(hub.live_call.entrance, "public")

        status, _ = await _open(ts, {})
        self.assertEqual(status, 409)
        self.assertEqual(hub.live_call.session_id, session_id)
        self.assertIsNone(hub.live_call.abort_reason)

        status, _ = await _open(pub, {**good, "X-Gsm-Call-Session": "hub-other"})
        self.assertEqual(status, 409)
        self.assertEqual(hub.live_call.session_id, session_id)
        self.assertFalse(hub.live_call.abort.is_set())

        client.close_tcp()
        await client.writer.wait_closed()
        for _ in range(50):
            if hub.live_call.link_gap_since is not None:
                break
            await asyncio.sleep(0.05)
        self.assertIsNotNone(hub.live_call.link_gap_since)

        status, resumed = await _open(ts, {"X-Gsm-Call-Session": session_id})
        self.assertEqual(status, 101, "relink with X-Gsm-Call-Session was rejected")
        assert resumed is not None
        self.clients.append(resumed)
        again = json.loads(await resumed.read_text())
        self.assertEqual(again["session"]["id"], session_id)
        self.assertEqual(again["session"]["entrance"], "tailscale")
        self.assertEqual(hub.live_call.entrance, "tailscale")

        report = json.dumps(
            {
                "direction": "in",
                "number": "+15550001001",
                "started_at": "2026-11-09T00:00:00Z",
                "duration_sec": 3,
                "session_id": session_id,
                "path": "public",
            }
        ).encode()
        status, payload = await _http(ts, "POST", "/calls", body=report)
        self.assertEqual(status, 201)
        self.assertEqual(json.loads(payload)["call"]["path"], "tailscale")
        self.assertEqual(json.loads(payload)["call"]["session_id"], session_id)

        resumed.send_close()
        await resumed.writer.drain()
        for _ in range(40):
            if not hub.live_call.busy:
                break
            await asyncio.sleep(0.05)
        self.assertFalse(hub.live_call.busy, "clean hangup left the slot busy")

        hub.GROKBOT_ON_CALL = True
        hub.AUTO_MODE_ON_CALL = "grokbot"
        self.offers.clear()
        status, grok = await _open(pub, good)
        self.assertEqual(status, 101)
        assert grok is not None
        self.clients.append(grok)
        described = json.loads(await grok.read_text())
        self.assertEqual(described["session"]["talk"], "grokbot")
        self.assertEqual(described["session"]["model"], "gsm2computer-hub-grokbot")
        self.assertEqual(described["session"]["entrance"], "public")
        grok_id = described["session"]["id"]
        grok.send(0x2, _g2(8000, 8))
        await grok.writer.drain()
        for _ in range(30):
            if self.offers:
                break
            await asyncio.sleep(0.05)
        self.assertEqual(self.offers[-1][2], 8000)

        status, health_body = await _http(ts, "GET", "/health")
        self.assertEqual(status, 200)
        health = json.loads(health_body)
        self.assertEqual(health["auto_mode"], "grokbot")
        self.assertEqual(health["call"]["path"], "public")
        self.assertEqual(health["call"]["entrance"], "public")
        self.assertEqual(health["call"]["session_id"], grok_id)
        self.assertIn("grokbot", health)
        self.assertFalse(health["grokbot"]["live"])
        names = {item["name"] for item in health["entrances"]}
        self.assertEqual(names, {"public", "tailscale"})

        grok_report = json.dumps(
            {
                "direction": "in",
                "number": "+15550001002",
                "started_at": "2026-11-09T00:01:00Z",
                "duration_sec": 2,
                "session_id": grok_id,
                "path": "tailscale",
            }
        ).encode()
        status, payload = await _http(pub, "POST", "/calls", good, grok_report)
        self.assertEqual(status, 201)
        stored = json.loads(payload)["call"]
        self.assertEqual(stored["path"], "public")
        self.assertEqual(stored["session_id"], grok_id)
        # The client-supplied path must not win, including over the public socket.
        self.assertNotEqual(stored["path"], "tailscale")
        grok.send_close()
        await grok.writer.drain()
        for _ in range(40):
            if not hub.live_call.busy:
                break
            await asyncio.sleep(0.05)
        self.assertFalse(hub.live_call.busy)


if __name__ == "__main__":
    unittest.main()
