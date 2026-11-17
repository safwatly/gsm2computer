#!/usr/bin/env python3
"""Asterisk gateway adapter against a fake modem, AMI, and phone WebSocket.

No Quectel, no PipeWire. The fake phone hub speaks the same WebSocket the
Moto uses and claims a real CallSlot, which is what /health call.busy reads.
"""
from __future__ import annotations

import asyncio
import base64
import importlib.util
import json
import math
import os
import struct
import subprocess
import sys
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest import mock

HUB = Path(__file__).resolve().parent
REPO = HUB.parent
sys.path.insert(0, str(HUB))

from asterisk_gateway import (  # noqa: E402
    AS_AUDIO,
    AS_HANGUP,
    AS_UUID,
    GatewayConfig,
    GatewayRuntime,
    _as_encode,
    _as_read,
    _encode_frame,
    _read_frame,
    contribute_health,
    handle_gateway_http,
    pcm16_from_wire,
    sms_from_ami,
)
from call_slot import CallSlot  # noqa: E402
from device_auth import public_route_allowed  # noqa: E402
from portal_http import EventBus  # noqa: E402
from portal_sms import fanout_inbound_sms, ingest_inbound_sms  # noqa: E402
from portal_store import PortalStore  # noqa: E402

SESSION = "hub-gw-test"


def _load_uac():
    path = REPO / "ops" / "asterisk-gateway" / "uac_at.py"
    spec = importlib.util.spec_from_file_location("uac_at", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _tone(freq: int, ms: int = 20, rate: int = 8000, amp: int = 8000) -> bytes:
    count = rate * ms // 1000
    frames = [
        int(amp * math.sin(2 * math.pi * freq * i / rate))
        for i in range(count)
    ]
    return struct.pack("<" + "h" * count, *frames)


def _http_body(raw: bytes) -> bytes:
    return raw.split(b"\r\n\r\n", 1)[1]


async def _read_request(reader: asyncio.StreamReader) -> tuple[str, str, dict[str, str], bytes]:
    lines: list[bytes] = []
    while True:
        line = await reader.readline()
        if line in (b"\r\n", b"\n", b""):
            break
        lines.append(line)
    if not lines:
        raise ConnectionError("empty request")
    method, target, _version = lines[0].decode("iso-8859-1").split()
    headers: dict[str, str] = {}
    for raw in lines[1:]:
        if b":" in raw:
            name, value = raw.split(b":", 1)
            headers[name.decode("iso-8859-1").strip().lower()] = value.decode("iso-8859-1").strip()
    length = int(headers.get("content-length") or "0")
    body = await reader.readexactly(length) if length else b""
    return method, target, headers, body


class FakeAmi:
    def __init__(self) -> None:
        self.actions: list[dict[str, str]] = []
        self.connected = asyncio.Event()
        self._writer: asyncio.StreamWriter | None = None
        self._lock = asyncio.Lock()
        self.server: asyncio.AbstractServer | None = None

    async def start(self) -> None:
        self.server = await asyncio.start_server(self._client, "127.0.0.1", 0)

    @property
    def port(self) -> int:
        sock = (self.server.sockets or [])[0]
        return int(sock.getsockname()[1])

    async def stop(self) -> None:
        if self.server is not None:
            self.server.close()
            await self.server.wait_closed()

    async def push(self, fields: dict[str, str]) -> None:
        await asyncio.wait_for(self.connected.wait(), timeout=2)
        await self._send(fields)

    async def _send(self, fields: dict[str, str]) -> None:
        writer = self._writer
        if writer is None:
            return
        lines = [f"{key}: {value}" for key, value in fields.items()]
        async with self._lock:
            writer.write(("\r\n".join(lines) + "\r\n\r\n").encode())
            await writer.drain()

    async def _client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        from asterisk_gateway import _read_ami

        self._writer = writer
        writer.write(b"Asterisk Call Manager/2.10.0\r\n")
        await writer.drain()
        self.connected.set()
        try:
            while True:
                packet = await _read_ami(reader)
                self.actions.append(packet)
                action = packet.get("Action") or ""
                action_id = packet.get("ActionID") or ""
                if action == "Login" and packet.get("Secret") != "test-secret":
                    await self._send(
                        {"Response": "Error", "Message": "Authentication failed", "ActionID": action_id}
                    )
                    continue
                await self._send({"Response": "Success", "Message": "OK", "ActionID": action_id})
        except Exception:
            pass
        finally:
            writer.close()


class FakePhone:
    """Phone-shaped hub: HTTP /sms /calls /sms/outbox and a WebSocket on /."""

    def __init__(self, store: PortalStore) -> None:
        self.store = store
        self.events = EventBus()
        self.slot = CallSlot()
        self.uplink = bytearray()
        self.released = asyncio.Event()
        self.close_code: int | None = None
        self.calls: list[dict] = []
        self.sms: list[dict] = []
        self.reject_ws = False
        self.server: asyncio.AbstractServer | None = None
        self._ws_alive = False

    async def start(self) -> None:
        self.server = await asyncio.start_server(self._client, "127.0.0.1", 0)

    @property
    def port(self) -> int:
        sock = (self.server.sockets or [])[0]
        return int(sock.getsockname()[1])

    @property
    def base(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    async def stop(self) -> None:
        if self.server is not None:
            self.server.close()
            await self.server.wait_closed()

    async def _client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            method, target, headers, body = await _read_request(reader)
        except Exception:
            writer.close()
            return
        path = target.split("?", 1)[0]
        if headers.get("upgrade", "").lower() == "websocket":
            await self._websocket(reader, writer)
            return
        status, payload = self._http(method, path, body)
        raw = json.dumps(payload).encode()
        text = {200: "OK", 201: "Created"}.get(status, "OK")
        writer.write(
            (
                f"HTTP/1.1 {status} {text}\r\n"
                "Content-Type: application/json\r\n"
                f"Content-Length: {len(raw)}\r\n"
                "Connection: close\r\n\r\n"
            ).encode("ascii")
            + raw
        )
        await writer.drain()
        writer.close()

    def _http(self, method: str, path: str, body: bytes) -> tuple[int, dict]:
        if method == "POST" and path == "/sms":
            payload = json.loads(body.decode() or "{}")
            entry, _command = ingest_inbound_sms(self.store, self.events, payload)
            if not entry.get("duplicate"):
                fanout_inbound_sms(entry)
            self.sms.append(entry)
            return 200, {"ok": True, "sms": entry, "duplicate": bool(entry.get("duplicate"))}
        if method == "GET" and path == "/sms/outbox":
            return 200, {"ok": True, "items": self.store.claim_outbox_for_send()}
        if method == "POST" and path.startswith("/sms/outbox/") and path.endswith("/ack"):
            item_id = path.split("/")[3]
            payload = json.loads(body.decode() or "{}")
            item = self.store.ack_outbox(
                item_id,
                status=str(payload.get("status") or "sent"),
                error=payload.get("error"),
            )
            return 200, {"ok": item is not None, "item": item}
        if method == "POST" and path == "/calls":
            payload = json.loads(body.decode() or "{}")
            self.calls.append(payload)
            self.store.add_call(
                direction=str(payload.get("direction") or "in"),
                number=str(payload.get("number") or ""),
                started_at=str(payload.get("started_at") or ""),
                duration_sec=int(payload.get("duration_sec") or 0),
                session_id=str(payload.get("session_id") or "") or None,
            )
            return 201, {"ok": True}
        return 404, {"ok": False}

    async def _websocket(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        if self.reject_ws:
            writer.write(b"HTTP/1.1 409 Conflict\r\nConnection: close\r\nContent-Length: 0\r\n\r\n")
            await writer.drain()
            writer.close()
            return
        if not self.slot.claim("/", entrance="tailscale"):
            writer.write(b"HTTP/1.1 409 Conflict\r\nConnection: close\r\nContent-Length: 0\r\n\r\n")
            await writer.drain()
            writer.close()
            return
        self.slot.session_id = SESSION
        self.slot.mark_established()
        self._ws_alive = True
        writer.write(
            b"HTTP/1.1 101 Switching Protocols\r\n"
            b"Upgrade: websocket\r\n"
            b"Connection: Upgrade\r\n"
            b"Sec-WebSocket-Accept: test\r\n\r\n"
        )
        session = {
            "type": "session.updated",
            "session": {"id": SESSION, "audio": {"format": "audio/pcm", "rate": 8000}},
        }
        writer.write(_encode_frame(0x1, json.dumps(session).encode(), masked=False))
        await writer.drain()
        sent_downlink = False
        try:
            while True:
                opcode, payload = await _read_frame(reader)
                if opcode == 0x8:
                    if len(payload) >= 2:
                        self.close_code = int.from_bytes(payload[:2], "big")
                    break
                if opcode == 0x9:
                    writer.write(_encode_frame(0xA, payload, masked=False))
                    await writer.drain()
                    continue
                if opcode != 0x1:
                    continue
                event = json.loads(payload.decode())
                if event.get("type") != "input_audio_buffer.append":
                    continue
                pcm = base64.b64decode(event.get("audio") or "")
                self.uplink.extend(pcm)
                self.slot.note_uplink()
                if not sent_downlink:
                    sent_downlink = True
                    delta = {
                        "type": "response.output_audio.delta",
                        "delta": base64.b64encode(self.downlink_pcm).decode("ascii"),
                        "format": "audio/pcm",
                        "rate": 8000,
                    }
                    writer.write(_encode_frame(0x1, json.dumps(delta).encode(), masked=False))
                    await writer.drain()
        except Exception:
            pass
        finally:
            self._ws_alive = False
            self.slot.release()
            self.released.set()
            writer.close()


class AdapterTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = PortalStore(Path(self.tmp.name) / "portal.sqlite")
        self.ami = FakeAmi()
        self.phone = FakePhone(self.store)
        self.phone.downlink_pcm = _tone(880)
        await self.ami.start()
        await self.phone.start()
        self.ended: list[dict] = []
        self.ended_flag = asyncio.Event()
        self.gw = GatewayRuntime(
            GatewayConfig(
                enabled=True,
                ami_host="127.0.0.1",
                ami_port=self.ami.port,
                ami_user="gsm-gateway",
                ami_secret="test-secret",
                device="ec25",
                audiosocket_bind="127.0.0.1",
                audiosocket_port=0,
                hub_base=self.phone.base,
                sample_rate=8000,
                outbox_poll_s=0,
            ),
            busy=lambda: self.phone.slot.busy,
            on_call_ended=self._ended,
        )
        await self.gw.start()
        await self.gw.wait_ami()

    def _ended(self, payload: dict) -> None:
        self.ended.append(payload)
        self.ended_flag.set()

    async def asyncTearDown(self) -> None:
        await self.gw.stop()
        await self.ami.stop()
        await self.phone.stop()

    async def _exchange(self, call_uuid: str, uplink: bytes, release: asyncio.Event) -> bytes:
        reader, writer = await asyncio.open_connection("127.0.0.1", self.gw.bound_port)
        writer.write(_as_encode(AS_UUID, uuid.UUID(call_uuid).bytes))
        writer.write(_as_encode(AS_AUDIO, uplink))
        await writer.drain()
        downlink = b""
        while True:
            kind, payload = await asyncio.wait_for(_as_read(reader), timeout=3)
            if kind == AS_AUDIO:
                downlink += payload
                break
            if kind == AS_HANGUP:
                raise AssertionError("audio leg hung up before downlink")
        await release.wait()
        writer.write(_as_encode(AS_HANGUP))
        await writer.drain()
        writer.close()
        await writer.wait_closed()
        return downlink

    async def test_inbound_call_audio_both_ways_and_second_leg_rejected(self) -> None:
        uplink = _tone(440)
        raw = await handle_gateway_http(
            self.gw, "GET", "/gateway/admit", {"number": ["+15551212"], "dir": ["in"]}, b""
        )
        text = _http_body(raw).decode().strip()
        self.assertTrue(text.startswith("yes:"), text)
        call_uuid = text.split(":", 1)[1]
        release = asyncio.Event()
        modem = asyncio.create_task(self._exchange(call_uuid, uplink, release))
        for _ in range(50):
            if self.phone.uplink:
                break
            await asyncio.sleep(0.02)
        self.assertEqual(bytes(self.phone.uplink), uplink)
        self.assertTrue(self.phone.slot.snapshot()["busy"])
        self.assertEqual(self.phone.slot.snapshot()["session_id"], SESSION)
        self.assertEqual(self.phone.slot.snapshot()["path"], "tailscale")
        health = contribute_health({"ok": True, "call": self.phone.slot.snapshot()}, self.gw)
        self.assertTrue(health["call"]["busy"])
        self.assertTrue(health["gateway"]["call"]["busy"])
        self.assertEqual(health["gateway"]["call"]["direction"], "in")
        self.assertEqual(health["gateway"]["call"]["session_id"], SESSION)
        self.assertEqual(health["gateway"]["transport"], "audiosocket")

        again = await handle_gateway_http(
            self.gw, "GET", "/gateway/admit", {"number": ["+1999"], "dir": ["in"]}, b""
        )
        self.assertEqual(_http_body(again).decode().strip(), "no")
        other = CallSlot()
        self.assertTrue(other.claim("/"))
        reader, writer = await asyncio.open_connection("127.0.0.1", self.gw.bound_port)
        writer.write(_as_encode(AS_UUID, uuid.uuid4().bytes))
        await writer.drain()
        kind, _payload = await asyncio.wait_for(_as_read(reader), timeout=2)
        self.assertEqual(kind, AS_HANGUP)
        writer.close()
        self.assertTrue(self.phone.slot.busy)
        self.assertIsNone(self.phone.slot.abort_reason)
        self.assertTrue(other.busy)
        self.assertIsNone(other.abort_reason)
        self.assertFalse(any(item.get("Action") == "Hangup" for item in self.ami.actions))
        self.assertTrue(self.phone._ws_alive)

        release.set()
        downlink = await modem
        self.assertEqual(downlink, self.phone.downlink_pcm)
        await asyncio.wait_for(self.phone.released.wait(), timeout=3)
        await asyncio.wait_for(self.ended_flag.wait(), timeout=3)
        self.assertFalse(self.phone.slot.snapshot()["busy"])
        self.assertEqual(self.phone.close_code, 1000)
        self.assertEqual(len(self.ended), 1)
        self.assertEqual(self.ended[0]["event"], "call_ended")
        self.assertEqual(self.ended[0]["source"], "asterisk_gateway")
        self.assertEqual(self.ended[0]["call_id"], SESSION)
        self.assertEqual(self.ended[0]["caller_number"], "+15551212")
        self.assertEqual(self.ended[0]["direction"], "in")
        self.assertFalse(self.ended[0]["test"])
        self.assertEqual(self.phone.calls[0]["session_id"], SESSION)
        self.assertEqual(self.phone.calls[0]["direction"], "in")
        idle = contribute_health({"ok": True, "call": self.phone.slot.snapshot()}, self.gw)
        self.assertFalse(idle["call"]["busy"])
        self.assertIsNone(idle["gateway"]["call"])

        reader, writer = await asyncio.open_connection("127.0.0.1", self.gw.bound_port)
        writer.write(_as_encode(AS_UUID, uuid.uuid4().bytes))
        await writer.drain()
        kind, _payload = await asyncio.wait_for(_as_read(reader), timeout=2)
        self.assertEqual(kind, AS_HANGUP)
        writer.close()
        await asyncio.sleep(0.05)
        self.assertEqual(len(self.ended), 1)
        self.assertEqual(len(self.phone.calls), 1)

    async def test_outbound_originate_then_audio(self) -> None:
        result = await self.gw.dial("+201234567890")
        self.assertTrue(result["ok"], result)
        originate = next(item for item in self.ami.actions if item.get("Action") == "Originate")
        self.assertEqual(originate["Channel"], "Quectel/ec25/+201234567890")
        self.assertEqual(originate["Context"], "gateway-media")
        self.assertIn(f"GW_UUID={result['uuid']}", originate["Variable"])
        self.assertIn("GW_DIR=out", originate["Variable"])
        release = asyncio.Event()
        uplink = _tone(660)
        modem = asyncio.create_task(self._exchange(result["uuid"], uplink, release))
        for _ in range(50):
            if self.phone.uplink:
                break
            await asyncio.sleep(0.02)
        self.assertEqual(bytes(self.phone.uplink), uplink)
        self.assertTrue(self.phone.slot.busy)
        release.set()
        downlink = await modem
        self.assertEqual(downlink, self.phone.downlink_pcm)
        await asyncio.wait_for(self.ended_flag.wait(), timeout=3)
        self.assertEqual(self.ended[0]["direction"], "out")
        self.assertEqual(self.ended[0]["caller_number"], "+201234567890")

    async def test_busy_slot_refuses_audio_without_touching_it(self) -> None:
        held = CallSlot()
        self.assertTrue(held.claim("/", entrance="tailscale"))
        held.session_id = "live-moto"
        self.gw._busy = lambda: held.busy
        raw = await handle_gateway_http(
            self.gw, "GET", "/gateway/admit", {"number": ["+15550001"]}, b""
        )
        self.assertEqual(_http_body(raw).decode().strip(), "no")
        reader, writer = await asyncio.open_connection("127.0.0.1", self.gw.bound_port)
        writer.write(_as_encode(AS_UUID, uuid.uuid4().bytes))
        await writer.drain()
        kind, _payload = await asyncio.wait_for(_as_read(reader), timeout=2)
        self.assertEqual(kind, AS_HANGUP)
        writer.close()
        self.assertTrue(held.busy)
        self.assertEqual(held.session_id, "live-moto")
        self.assertIsNone(held.abort_reason)
        self.assertFalse(held.abort.is_set())
        self.assertEqual(self.ended, [])
        self.assertFalse(any(item.get("Action") == "Hangup" for item in self.ami.actions))

    async def test_phone_409_does_not_emit_call_ended(self) -> None:
        self.phone.reject_ws = True
        self.gw._busy = lambda: False
        _ok, call_uuid = await self.gw.admit("+15550002")
        reader, writer = await asyncio.open_connection("127.0.0.1", self.gw.bound_port)
        writer.write(_as_encode(AS_UUID, uuid.UUID(call_uuid).bytes))
        await writer.drain()
        kind, _payload = await asyncio.wait_for(_as_read(reader), timeout=2)
        self.assertEqual(kind, AS_HANGUP)
        writer.close()
        await asyncio.sleep(0.05)
        self.assertEqual(self.ended, [])
        self.assertIsNone(self.gw.active)

    async def test_inbound_sms_reaches_store_and_webhook_once(self) -> None:
        spool = Path(self.tmp.name) / "sms-webhook.sqlite3"
        alert = Path(self.tmp.name) / "alert.sqlite3"
        with mock.patch.dict(
            os.environ,
            {
                "GSM2COMPUTER_SMS_WEBHOOK_URL": "https://sms.example/hook",
                "GSM2COMPUTER_SMS_WEBHOOK_KEY": "sms-key",
                "GSM2COMPUTER_SMS_SPOOL": str(spool),
                "GSM2COMPUTER_ALERT_SPOOL": str(alert),
            },
        ), mock.patch("alert_webhook.start_sms_worker"), mock.patch("alert_webhook.start_alert_worker"):
            body = base64.b64encode(b"hello from modem").decode("ascii")
            event = {
                "Event": "UserEvent",
                "UserEvent": "GsmSms",
                "From": "+15551212",
                "Body64": body,
                "ClientId": "sms-1",
            }
            await self.ami.push(event)
            await self.ami.push(event)
            for _ in range(50):
                if len(self.phone.sms) >= 2:
                    break
                await asyncio.sleep(0.02)
        self.assertEqual(len(self.phone.sms), 2)
        self.assertFalse(self.phone.sms[0].get("duplicate"))
        self.assertTrue(self.phone.sms[1].get("duplicate"))
        self.assertEqual(self.phone.sms[0]["body"], "hello from modem")
        rows = self.store.list_messages("+15551212")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["body"], "hello from modem")
        self.assertEqual(rows[0]["status"], "received")
        import sqlite3

        with sqlite3.connect(spool) as conn:
            stored = list(conn.execute("SELECT payload FROM alerts"))
        self.assertEqual(len(stored), 1)
        posted = json.loads(stored[0][0])
        self.assertEqual(posted["event"], "sms_in")
        self.assertEqual(posted["from"], "+15551212")
        self.assertEqual(posted["body"], "hello from modem")
        self.assertFalse(alert.exists())

    async def test_outbox_send_uses_the_modem(self) -> None:
        queued = self.store.enqueue_outbound("+15551212", "reply from portal")
        acked = await self.gw.flush_outbox()
        self.assertEqual(acked, 1)
        command = next(item for item in self.ami.actions if item.get("Action") == "Command")
        self.assertEqual(command["Command"], "quectel sms ec25 +15551212 reply from portal")
        message = self.store.get_message(queued["id"])
        assert message is not None
        self.assertEqual(message["status"], "sent")
        self.assertEqual(await self.gw.flush_outbox(), 0)


class UnitTests(unittest.TestCase):
    def test_flag_off_leaves_health_unchanged(self) -> None:
        config = GatewayConfig.from_env({})
        self.assertFalse(config.enabled)
        body = {"ok": True, "call": {"busy": False}}
        self.assertEqual(contribute_health(dict(body), None), body)
        self.assertNotIn("gateway", body)

    def test_public_entrance_does_not_serve_gateway_routes(self) -> None:
        self.assertFalse(public_route_allowed("GET", "/gateway/admit", {}))
        self.assertFalse(public_route_allowed("POST", "/gateway/dial", {}))
        self.assertFalse(public_route_allowed("GET", "/gateway/health", {}))

    def test_pcm_passthrough_and_ulaw(self) -> None:
        import audioop

        pcm = _tone(440)
        self.assertEqual(pcm16_from_wire(pcm, "audio/pcm", 8000, 8000), pcm)
        ulaw = audioop.lin2ulaw(pcm, 2)
        self.assertEqual(pcm16_from_wire(ulaw, "audio/pcmu", 8000, 8000), audioop.ulaw2lin(ulaw, 2))

    def test_sms_event_parser(self) -> None:
        parsed = sms_from_ami(
            {
                "Event": "UserEvent",
                "UserEvent": "GsmSms",
                "From": "+2010",
                "Body64": base64.b64encode("مرحبا".encode()).decode(),
                "ClientId": "sms-9",
            }
        )
        assert parsed is not None
        self.assertEqual(parsed["body"], "مرحبا")
        self.assertEqual(parsed["clientId"], "sms-9")
        self.assertIsNone(sms_from_ami({"Event": "Hangup"}))

    def test_usbcfg_and_alsa_and_conf(self) -> None:
        uac = _load_uac()
        armed = '+QCFG: "usbcfg",0x2C7C,0x0125,1,1,1,1,1,1,1'
        self.assertIsNone(uac.plan_usbcfg(armed))
        off = '+QCFG: "usbcfg",0x2C7C,0x0125,1,1,1,1,1,0,0'
        command = uac.plan_usbcfg(off)
        self.assertEqual(command, 'AT+QCFG="USBCFG",0x2C7C,0x0125,1,1,1,1,1,0,1')
        with self.assertRaises(uac.UacUnavailable):
            uac.plan_usbcfg('+QCFG: "usbcfg",0x2C7C,0x0125,1,1,1,1,1,0')
        listing = "card 1: Android [Android], device 0: USB Audio [USB Audio]\n"
        self.assertEqual(uac.alsa_card_id(listing), "Android")
        self.assertIsNone(uac.alsa_card_id("card 0: Headphones [Headphones], device 0: x\n"))
        conf = (REPO / "ops" / "asterisk-gateway" / "asterisk" / "quectel.conf").read_text()
        updated = uac.update_quectel_conf(conf, data_port="/dev/ttyUSB3", alsa_id="EC25")
        self.assertIn("data=/dev/ttyUSB3\n", updated)
        self.assertIn("alsadev=hw:CARD=EC25,DEV=0\n", updated)
        self.assertNotIn("audio=/dev/", updated)

    def test_dialplan_and_install_script(self) -> None:
        dialplan = (REPO / "ops" / "asterisk-gateway" / "asterisk" / "extensions.conf").read_text()
        self.assertIn("AudioSocket(${GW_UUID},${ASOCK})", dialplan)
        self.assertIn("Hangup(17)", dialplan)
        self.assertIn("/gateway/admit", dialplan)
        self.assertIn("UserEvent(GsmSms", dialplan)
        self.assertIn("gsm-gateway-sms", dialplan)
        quectel = (REPO / "ops" / "asterisk-gateway" / "asterisk" / "quectel.conf").read_text()
        self.assertIn("quec_uac=1", quectel)
        self.assertNotIn("\naudio=", quectel)
        self.assertIn("dtmf=off", quectel)
        install = REPO / "ops" / "asterisk-gateway" / "install.sh"
        subprocess.check_call(["bash", "-n", str(install)])
        subprocess.check_call(["bash", "-n", str(REPO / "ops" / "asterisk-gateway" / "tailscale-join.sh")])
        text = install.read_text()
        self.assertIn("3d45c7f072131296a7e3c1a4faf5bb18751dbd87", text)
        self.assertNotIn("tailscale up", text)
        unit = (REPO / "hub" / "gsm2computer-hub.service").read_text()
        self.assertNotIn("ASTERISK_GATEWAY", unit)
        hub_py = (REPO / "hub" / "hub.py").read_text()
        self.assertIn("contribute_health(body, _gateway_runtime)", hub_py)
        self.assertIn('"/gateway/admit"', hub_py)

    def test_sms_script_posts_the_phone_payload(self) -> None:
        import threading
        from http.server import BaseHTTPRequestHandler, HTTPServer

        seen: list[dict] = []

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:  # noqa: N802
                length = int(self.headers.get("Content-Length") or "0")
                seen.append(json.loads(self.rfile.read(length).decode()))
                raw = b'{"ok":true}'
                self.send_response(200)
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def log_message(self, fmt: str, *args) -> None:
                return

        server = HTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()

        def _stop_server() -> None:
            server.shutdown()
            server.server_close()

        self.addCleanup(_stop_server)
        port = server.server_address[1]
        script = REPO / "ops" / "asterisk-gateway" / "gsm-gateway-sms"
        body64 = base64.b64encode(b"hello from modem").decode()
        env = dict(os.environ)
        env["GSM_GATEWAY_HUB_BASE"] = f"http://127.0.0.1:{port}"
        completed = subprocess.run(
            [
                sys.executable,
                str(script),
                "--from",
                "+15551212",
                "--body64",
                body64,
                "--client-id",
                "sms-script-1",
            ],
            check=False,
            env=env,
            capture_output=True,
            text=True,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(
            seen,
            [{"from": "+15551212", "body": "hello from modem", "clientId": "sms-script-1"}],
        )


if __name__ == "__main__":
    unittest.main()
