#!/usr/bin/env python3
"""Asterisk + Quectel EC25 gateway, presented to the hub as the phone client.

Off unless GSM2COMPUTER_ASTERISK_GATEWAY is set. The Moto bridge keeps the
WebSocket, SMS, and outbox contract it has today. When the flag is on, this
process:

- listens for Asterisk AudioSocket (8 kHz s16le TCP) and opens the same hub
  WebSocket a phone would (`client.audio`, `input_audio_buffer.append`,
  `response.output_audio.delta`, close 1000)
- answers AMI on the Pi for outbound originate, SMS events, and the modem CLI
- posts inbound SMS to the hub `/sms` route so the existing webhook path runs
- leases `/sms/outbox` and sends those rows with `quectel sms`

A second GSM leg is refused while this call, a pending leg, or the hub slot
is busy. Refusing it does not hang up the live call (ADR 0004).
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import logging
import os
import time
import uuid
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Optional
from urllib.parse import urlsplit

from portal_sms import normalize_msisdn

LOG = logging.getLogger("gsm2computer.asterisk")

WS_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
AS_HANGUP = 0x00
AS_UUID = 0x01
AS_DTMF = 0x03
AS_AUDIO = 0x10
AS_ERROR = 0xFF
_AS_MAX = 8192

BusyFn = Callable[[], bool]
EndedFn = Callable[[dict[str, Any]], None]


class GatewayError(Exception):
    pass


class PhoneBusy(GatewayError):
    """The hub refused the phone WebSocket (409). The other call stays up."""


def _flag(env: dict[str, str], name: str) -> bool:
    raw = (env.get(name) or "").strip().lower()
    return raw in ("1", "true", "yes", "on")


def _client_host(host: str) -> str:
    if host.strip() in ("", "0.0.0.0", "::"):
        return "127.0.0.1"
    return host.strip()


@dataclass(frozen=True)
class GatewayConfig:
    enabled: bool = False
    ami_host: str = ""
    ami_port: int = 5038
    ami_user: str = "gsm-gateway"
    ami_secret: str = ""
    device: str = "ec25"
    audiosocket_bind: str = "127.0.0.1"
    audiosocket_port: int = 9092
    hub_base: str = "http://127.0.0.1:8787"
    sample_rate: int = 8000
    # Covers the 60s AMI originate plus the AudioSocket connect after answer.
    reservation_s: float = 75.0
    outbox_poll_s: float = 5.0

    @classmethod
    def from_env(cls, env: Optional[dict[str, str]] = None) -> "GatewayConfig":
        src = os.environ if env is None else env
        enabled = _flag(src, "GSM2COMPUTER_ASTERISK_GATEWAY")
        hub_host = _client_host(src.get("GSM2COMPUTER_HUB_HOST") or "127.0.0.1")
        hub_port = (src.get("GSM2COMPUTER_HUB_PORT") or "8787").strip() or "8787"
        bind = (src.get("GSM2COMPUTER_ASTERISK_AUDIOSOCKET_BIND") or "").strip()
        if not bind:
            bind = (src.get("GSM2COMPUTER_HUB_HOST") or "127.0.0.1").strip() or "127.0.0.1"
        try:
            ami_port = int((src.get("GSM2COMPUTER_ASTERISK_AMI_PORT") or "5038").strip())
        except ValueError:
            ami_port = 5038
        try:
            as_port = int((src.get("GSM2COMPUTER_ASTERISK_AUDIOSOCKET_PORT") or "9092").strip())
        except ValueError:
            as_port = 9092
        try:
            rate = int((src.get("GSM2COMPUTER_ASTERISK_SAMPLE_RATE") or "8000").strip())
        except ValueError:
            rate = 8000
        try:
            poll = float((src.get("GSM2COMPUTER_ASTERISK_OUTBOX_POLL_S") or "5").strip())
        except ValueError:
            poll = 5.0
        base = (src.get("GSM2COMPUTER_ASTERISK_HUB_BASE") or "").strip().rstrip("/")
        if not base:
            base = f"http://{hub_host}:{hub_port}"
        return cls(
            enabled=enabled,
            ami_host=(src.get("GSM2COMPUTER_ASTERISK_AMI_HOST") or "").strip(),
            ami_port=ami_port,
            ami_user=(src.get("GSM2COMPUTER_ASTERISK_AMI_USER") or "gsm-gateway").strip() or "gsm-gateway",
            ami_secret=src.get("GSM2COMPUTER_ASTERISK_AMI_SECRET") or "",
            device=(src.get("GSM2COMPUTER_ASTERISK_DEVICE") or "ec25").strip() or "ec25",
            audiosocket_bind=bind,
            audiosocket_port=as_port,
            hub_base=base,
            sample_rate=rate if rate > 0 else 8000,
            outbox_poll_s=poll if poll >= 0 else 5.0,
        )


@dataclass
class _Pending:
    uuid: str
    number: str
    direction: str
    deadline: float


@dataclass
class _Active:
    uuid: str
    number: str
    direction: str
    session_id: str = ""
    started_wall: float = 0.0
    reported: bool = False


def contribute_health(body: dict[str, Any], runtime: Optional["GatewayRuntime"]) -> dict[str, Any]:
    """Add `gateway` to a /health body. Absent when the flag is off."""
    if runtime is None:
        return body
    body["gateway"] = runtime.snapshot()
    return body


def pcm16_from_wire(audio: bytes, fmt: str, rate: int, target_rate: int) -> bytes:
    """Hub downlink → 8 kHz s16le for the modem. PCM at the target rate is copied."""
    name = (fmt or "audio/pcm").strip().lower()
    if name in ("audio/pcmu", "audio/g711-ulaw", "pcmu", "g711_ulaw"):
        import audioop

        audio = audioop.ulaw2lin(audio, 2)
        rate = rate or 8000
    if len(audio) % 2:
        audio = audio[:-1]
    if not audio:
        return b""
    src = rate or target_rate
    if src != target_rate:
        import audioop

        audio, _state = audioop.ratecv(audio, 2, 1, src, target_rate, None)
    return audio


def sms_cli(device: str, number: str, body: str) -> str:
    """One AMI `Command` line. Control characters become spaces."""
    text = "".join(ch if ch >= " " else " " for ch in (body or ""))
    text = " ".join(text.split())
    return f"quectel sms {device} {number} {text}".strip()


def sms_from_ami(event: dict[str, str]) -> Optional[dict[str, str]]:
    """UserEvent GsmSms → the JSON body POST /sms already accepts."""
    if (event.get("Event") or "").lower() != "userevent":
        return None
    if (event.get("UserEvent") or "").lower() != "gsmsms":
        return None
    raw = event.get("Body64") or ""
    if not raw:
        return None
    try:
        body = base64.b64decode(raw, validate=False).decode("utf-8", errors="replace")
    except Exception:
        LOG.warning("gsm sms body64 is not base64")
        return None
    sender = (event.get("From") or "").strip()
    if not sender:
        return None
    return {
        "from": sender,
        "body": body,
        "clientId": (event.get("ClientId") or "").strip()[:80],
        "receivedAt": _utc_now(),
    }


def command_accepted(response: dict[str, str]) -> bool:
    if (response.get("Response") or "").lower() == "error":
        return False
    body = (response.get("_body") or "").lower()
    if "error" in body or "failed" in body or "not connected" in body:
        return False
    return True


def _utc_now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _ws_accept(key: str) -> str:
    digest = hashlib.sha1((key + WS_GUID).encode("ascii")).digest()
    return base64.b64encode(digest).decode("ascii")


def _encode_frame(opcode: int, payload: bytes, masked: bool) -> bytes:
    first = 0x80 | (opcode & 0x0F)
    length = len(payload)
    mask_bit = 0x80 if masked else 0
    if length < 126:
        header = bytes([first, mask_bit | length])
    elif length < 65536:
        header = bytes([first, mask_bit | 126]) + length.to_bytes(2, "big")
    else:
        header = bytes([first, mask_bit | 127]) + length.to_bytes(8, "big")
    if not masked:
        return header + payload
    mask = os.urandom(4)
    mixed = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
    return header + mask + mixed


async def _read_frame(reader: asyncio.StreamReader) -> tuple[int, bytes]:
    hdr = await reader.readexactly(2)
    opcode = hdr[0] & 0x0F
    masked = bool(hdr[1] & 0x80)
    length = hdr[1] & 0x7F
    if length == 126:
        length = int.from_bytes(await reader.readexactly(2), "big")
    elif length == 127:
        length = int.from_bytes(await reader.readexactly(8), "big")
    if length > 1_000_000:
        raise GatewayError("websocket frame too large")
    mask = await reader.readexactly(4) if masked else b""
    payload = await reader.readexactly(length) if length else b""
    if masked:
        payload = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
    return opcode, payload


def _as_encode(kind: int, payload: bytes = b"") -> bytes:
    return bytes([kind]) + len(payload).to_bytes(2, "big") + payload


async def _as_read(reader: asyncio.StreamReader) -> tuple[int, bytes]:
    hdr = await reader.readexactly(3)
    kind = hdr[0]
    length = int.from_bytes(hdr[1:3], "big")
    if length > _AS_MAX:
        raise GatewayError(f"audiosocket frame {length} too large")
    payload = await reader.readexactly(length) if length else b""
    return kind, payload


def _uuid_from_bytes(payload: bytes) -> str:
    if len(payload) != 16:
        raise GatewayError(f"audiosocket uuid is {len(payload)} bytes")
    return str(uuid.UUID(bytes=payload))


async def _read_ami(reader: asyncio.StreamReader) -> dict[str, str]:
    fields: dict[str, str] = {}
    body: list[str] = []
    while True:
        line = await reader.readline()
        if not line:
            raise ConnectionError("ami closed")
        text = line.decode("utf-8", errors="replace").rstrip("\r\n")
        if text == "":
            if fields or body:
                break
            continue
        if ":" in text:
            key, value = text.split(":", 1)
            fields[key.strip()] = value.strip()
        else:
            body.append(text)
    if body:
        fields["_body"] = "\n".join(body)
    return fields


def _encode_ami(fields: dict[str, str]) -> bytes:
    lines = [f"{key}: {value}" for key, value in fields.items()]
    return ("\r\n".join(lines) + "\r\n\r\n").encode("utf-8")


class AmiClient:
    """Minimal Asterisk Manager client. Events and action replies share one socket."""

    def __init__(self, on_event: Callable[[dict[str, str]], Awaitable[None]]) -> None:
        self._on_event = on_event
        self.reader: Optional[asyncio.StreamReader] = None
        self.writer: Optional[asyncio.StreamWriter] = None
        self._pending: dict[str, asyncio.Future[dict[str, str]]] = {}
        self._task: Optional[asyncio.Task[None]] = None
        self.connected = False

    async def connect(self, host: str, port: int, username: str, secret: str) -> None:
        self.reader, self.writer = await asyncio.open_connection(host, port)
        # Greeting is one line ("Asterisk Call Manager/..."). Read it before the
        # packet loop so the login reply is not consumed as the banner.
        try:
            await asyncio.wait_for(self.reader.readline(), timeout=2)
        except asyncio.TimeoutError:
            pass
        self._task = asyncio.create_task(self._read_loop(), name="ami-read")
        action_id = uuid.uuid4().hex
        loop = asyncio.get_running_loop()
        fut: asyncio.Future[dict[str, str]] = loop.create_future()
        self._pending[action_id] = fut
        assert self.writer is not None
        self.writer.write(
            _encode_ami(
                {
                    "Action": "Login",
                    "Username": username,
                    "Secret": secret,
                    "Events": "on",
                    "ActionID": action_id,
                }
            )
        )
        await self.writer.drain()
        try:
            response = await asyncio.wait_for(fut, timeout=5)
        except Exception:
            await self.close()
            raise
        if (response.get("Response") or "").lower() != "success":
            await self.close()
            raise GatewayError(response.get("Message") or "ami login failed")
        self.connected = True

    async def request(self, fields: dict[str, str], timeout: float = 8.0) -> dict[str, str]:
        if self.writer is None:
            raise GatewayError("ami down")
        action_id = uuid.uuid4().hex
        payload = dict(fields)
        payload["ActionID"] = action_id
        loop = asyncio.get_running_loop()
        fut: asyncio.Future[dict[str, str]] = loop.create_future()
        self._pending[action_id] = fut
        self.writer.write(_encode_ami(payload))
        await self.writer.drain()
        try:
            return await asyncio.wait_for(fut, timeout)
        except Exception:
            self._pending.pop(action_id, None)
            raise

    async def wait_closed(self) -> None:
        if self._task is None:
            return
        await self._task

    async def close(self) -> None:
        self.connected = False
        writer = self.writer
        self.writer = None
        if writer is not None:
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass
        if self._task is not None and self._task is not asyncio.current_task():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self._task = None
        for fut in self._pending.values():
            if not fut.done():
                fut.set_exception(ConnectionError("ami closed"))
        self._pending.clear()

    async def _read_loop(self) -> None:
        assert self.reader is not None
        try:
            while True:
                packet = await _read_ami(self.reader)
                action_id = packet.get("ActionID") or ""
                if packet.get("Response") and action_id in self._pending:
                    fut = self._pending.pop(action_id)
                    if not fut.done():
                        fut.set_result(packet)
                    continue
                if packet.get("Event"):
                    try:
                        await self._on_event(packet)
                    except Exception:
                        LOG.warning("ami event failed", exc_info=True)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            LOG.info("ami read ended: %s", exc)
        finally:
            self.connected = False
            for fut in self._pending.values():
                if not fut.done():
                    fut.set_exception(ConnectionError("ami closed"))
            self._pending.clear()


class HttpHubClient:
    """Talks to the hub the way the phone does: HTTP JSON and a WebSocket on `/`."""

    def __init__(self, base: str, sample_rate: int) -> None:
        self.base = base.rstrip("/")
        self.sample_rate = sample_rate

    async def post_sms(self, payload: dict[str, str]) -> dict[str, Any]:
        status, raw = await _http("POST", f"{self.base}/sms", payload)
        return {"status": status, "body": _json_body(raw)}

    async def claim_outbox(self) -> list[dict[str, Any]]:
        _status, raw = await _http("GET", f"{self.base}/sms/outbox", None)
        body = _json_body(raw)
        items = body.get("items") if isinstance(body, dict) else None
        return [item for item in items or [] if isinstance(item, dict)]

    async def ack_outbox(self, item_id: str, status: str, error: Optional[str]) -> None:
        payload: dict[str, str] = {"status": status}
        if error:
            payload["error"] = error
        await _http("POST", f"{self.base}/sms/outbox/{item_id}/ack", payload)

    async def post_call(self, payload: dict[str, Any]) -> None:
        await _http("POST", f"{self.base}/calls", payload)

    async def open_phone(self) -> "PhoneSession":
        parts = urlsplit(self.base)
        host = parts.hostname or "127.0.0.1"
        port = parts.port or (443 if parts.scheme == "https" else 80)
        reader, writer = await asyncio.wait_for(asyncio.open_connection(host, port), timeout=5)
        key = base64.b64encode(os.urandom(16)).decode("ascii")
        request = (
            "GET / HTTP/1.1\r\n"
            f"Host: {host}:{port}\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\n"
            "Sec-WebSocket-Version: 13\r\n"
            "\r\n"
        )
        writer.write(request.encode("ascii"))
        await writer.drain()
        try:
            status, _headers = await _read_http_response(reader)
        except Exception:
            writer.close()
            raise
        if status == 409:
            writer.close()
            raise PhoneBusy("hub call slot busy")
        if status != 101:
            writer.close()
            raise GatewayError(f"phone websocket HTTP {status}")
        session = PhoneSession(reader, writer, self.sample_rate)
        try:
            await session.wait_ready()
        except Exception:
            await session.close()
            raise
        return session


class PhoneSession:
    def __init__(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter, rate: int) -> None:
        self.reader = reader
        self.writer = writer
        self.rate = rate
        self.session_id = ""
        self.out_fmt = "audio/pcm"
        self.out_rate = rate
        self.downlink: asyncio.Queue[Optional[bytes]] = asyncio.Queue()
        self._write_lock = asyncio.Lock()
        self._closed = False
        self._reader_task: Optional[asyncio.Task[None]] = None

    async def wait_ready(self) -> None:
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            opcode, payload = await asyncio.wait_for(_read_frame(self.reader), timeout=8)
            if opcode == 0x9:
                await self._send_frame(0xA, payload)
                continue
            if opcode == 0x8:
                raise GatewayError("phone websocket closed before session.updated")
            if opcode != 0x1:
                continue
            try:
                event = json.loads(payload.decode("utf-8"))
            except json.JSONDecodeError:
                continue
            etype = event.get("type")
            if etype == "error":
                message = event.get("error") if isinstance(event.get("error"), dict) else {}
                raise GatewayError(str(message.get("message") or "phone handshake failed"))
            if etype == "session.updated":
                session = event.get("session") or {}
                self.session_id = str(session.get("id") or "")
                self._reader_task = asyncio.create_task(self._read_loop(), name="phone-downlink")
                return
        raise GatewayError("no session.updated")

    async def set_formats(self) -> None:
        await self.send_json(
            {
                "type": "client.audio",
                "input": {"format": "audio/pcm", "rate": self.rate},
                "output": {"format": "audio/pcm", "rate": self.rate},
            }
        )

    async def send_audio(self, pcm: bytes) -> None:
        await self.send_json(
            {
                "type": "input_audio_buffer.append",
                "audio": base64.b64encode(pcm).decode("ascii"),
                "format": "audio/pcm",
                "rate": self.rate,
            }
        )

    async def send_json(self, payload: dict[str, Any]) -> None:
        await self._send_frame(0x1, json.dumps(payload).encode("utf-8"))

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            reason = (1000).to_bytes(2, "big") + b"call ended"
            await self._send_frame(0x8, reason)
        except Exception:
            pass
        if self._reader_task is not None and self._reader_task is not asyncio.current_task():
            self._reader_task.cancel()
        try:
            self.writer.close()
        except Exception:
            pass
        await self.downlink.put(None)

    async def _send_frame(self, opcode: int, payload: bytes) -> None:
        if self.writer.is_closing():
            raise ConnectionError("phone websocket closed")
        async with self._write_lock:
            self.writer.write(_encode_frame(opcode, payload, masked=True))
            await self.writer.drain()

    async def _read_loop(self) -> None:
        try:
            while not self._closed:
                opcode, payload = await _read_frame(self.reader)
                if opcode == 0x9:
                    await self._send_frame(0xA, payload)
                    continue
                if opcode == 0x8:
                    break
                if opcode != 0x1 or not payload:
                    continue
                try:
                    event = json.loads(payload.decode("utf-8"))
                except json.JSONDecodeError:
                    continue
                if event.get("type") != "response.output_audio.delta":
                    continue
                raw = event.get("delta") or event.get("audio") or ""
                if not raw:
                    continue
                audio = base64.b64decode(str(raw), validate=False)
                fmt = str(event.get("format") or self.out_fmt)
                try:
                    rate = int(event.get("rate") or self.out_rate)
                except (TypeError, ValueError):
                    rate = self.out_rate
                pcm = pcm16_from_wire(audio, fmt, rate, self.rate)
                if pcm:
                    await self.downlink.put(pcm)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            LOG.info("phone downlink ended: %s", exc)
        finally:
            await self.downlink.put(None)


class AudioSocket:
    def __init__(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        self.reader = reader
        self.writer = writer
        self._lock = asyncio.Lock()
        self._hung = False

    async def read(self) -> tuple[int, bytes]:
        return await _as_read(self.reader)

    async def send_audio(self, pcm: bytes) -> None:
        await self._send(_as_encode(AS_AUDIO, pcm))

    async def hangup(self) -> None:
        if self._hung:
            return
        self._hung = True
        try:
            await self._send(_as_encode(AS_HANGUP))
        except Exception:
            pass
        try:
            self.writer.close()
        except Exception:
            pass

    async def _send(self, frame: bytes) -> None:
        async with self._lock:
            self.writer.write(frame)
            await self.writer.drain()


async def _read_http_response(reader: asyncio.StreamReader) -> tuple[int, dict[str, str]]:
    status_line = await asyncio.wait_for(reader.readline(), timeout=5)
    parts = status_line.decode("iso-8859-1", errors="replace").split()
    status = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 0
    headers: dict[str, str] = {}
    while True:
        line = await reader.readline()
        if line in (b"\r\n", b"\n", b""):
            break
        if b":" in line:
            name, value = line.split(b":", 1)
            headers[name.decode("iso-8859-1").strip().lower()] = value.decode("iso-8859-1").strip()
    return status, headers


def _json_body(raw: bytes) -> dict[str, Any]:
    if not raw:
        return {}
    try:
        parsed = json.loads(raw.decode("utf-8"))
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


async def _http(method: str, url: str, payload: Optional[dict[str, Any]]) -> tuple[int, bytes]:
    parts = urlsplit(url)
    host = parts.hostname or "127.0.0.1"
    port = parts.port or 80
    path = parts.path or "/"
    if parts.query:
        path = f"{path}?{parts.query}"
    body = b""
    if payload is not None:
        body = json.dumps(payload).encode("utf-8")
    reader, writer = await asyncio.wait_for(asyncio.open_connection(host, port), timeout=5)
    try:
        headers = [
            f"{method} {path} HTTP/1.1",
            f"Host: {host}:{port}",
            "Connection: close",
            "Accept: application/json",
        ]
        if payload is not None:
            headers.append("Content-Type: application/json")
            headers.append(f"Content-Length: {len(body)}")
        writer.write(("\r\n".join(headers) + "\r\n\r\n").encode("ascii") + body)
        await writer.drain()
        status, response_headers = await _read_http_response(reader)
        length = int(response_headers.get("content-length") or "0")
        if length:
            data = await reader.readexactly(length)
        else:
            data = await reader.read()
        return status, data
    finally:
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass


def json_http(status: int, body: dict[str, Any]) -> bytes:
    payload = json.dumps(body).encode("utf-8")
    text = {200: "OK", 400: "Bad Request", 404: "Not Found", 409: "Conflict", 502: "Bad Gateway", 503: "Service Unavailable"}.get(
        status, "OK"
    )
    head = (
        f"HTTP/1.1 {status} {text}\r\n"
        "Content-Type: application/json\r\n"
        f"Content-Length: {len(payload)}\r\n"
        "Connection: close\r\n"
        "\r\n"
    )
    return head.encode("ascii") + payload


def text_http(status: int, body: str) -> bytes:
    payload = body.encode("utf-8")
    head = (
        f"HTTP/1.1 {status} OK\r\n"
        "Content-Type: text/plain; charset=utf-8\r\n"
        f"Content-Length: {len(payload)}\r\n"
        "Connection: close\r\n"
        "\r\n"
    )
    return head.encode("ascii") + payload


class GatewayRuntime:
    def __init__(
        self,
        config: GatewayConfig,
        busy: Optional[BusyFn] = None,
        on_call_ended: Optional[EndedFn] = None,
        hub: Optional[HttpHubClient] = None,
    ) -> None:
        self.config = config
        self.enabled = True
        self._busy = busy or (lambda: False)
        self._on_call_ended = on_call_ended
        self.hub = hub or HttpHubClient(config.hub_base, config.sample_rate)
        self.bound_port = config.audiosocket_port
        self.ami_connected = False
        self._ami = AmiClient(self._on_ami)
        self._lock = asyncio.Lock()
        self._outbox_lock = asyncio.Lock()
        self._pending: Optional[_Pending] = None
        self.active: Optional[_Active] = None
        self._stopped = asyncio.Event()
        self._server: Optional[asyncio.AbstractServer] = None
        self._tasks: set[asyncio.Task[None]] = set()
        self._ami_task: Optional[asyncio.Task[None]] = None
        self._outbox_task: Optional[asyncio.Task[None]] = None

    def snapshot(self) -> dict[str, Any]:
        call = None
        active = self.active
        if active is not None:
            call = {
                "busy": True,
                "direction": active.direction,
                "number": active.number,
                "uuid": active.uuid,
                "session_id": active.session_id or None,
            }
        return {
            "enabled": True,
            "transport": "audiosocket",
            "device": self.config.device,
            "ami": "up" if self.ami_connected else "down",
            "audiosocket": f"{self.config.audiosocket_bind}:{self.bound_port}",
            "call": call,
        }

    async def start(self) -> None:
        self._server = await asyncio.start_server(
            self._on_audio, self.config.audiosocket_bind, self.config.audiosocket_port
        )
        socks = self._server.sockets or []
        if socks:
            bound = socks[0].getsockname()
            if isinstance(bound, tuple) and len(bound) >= 2:
                self.bound_port = int(bound[1])
        self._ami_task = asyncio.create_task(self._ami_loop(), name="ami-connect")
        if self.config.outbox_poll_s > 0:
            self._outbox_task = asyncio.create_task(self._outbox_loop(), name="sms-outbox")
        LOG.info(
            "asterisk gateway audiosocket %s:%s device=%s hub=%s",
            self.config.audiosocket_bind,
            self.bound_port,
            self.config.device,
            self.config.hub_base,
        )

    async def wait_ami(self, timeout: float = 3.0) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.ami_connected:
                return
            await asyncio.sleep(0.02)
        raise TimeoutError("ami did not log in")

    async def stop(self) -> None:
        self._stopped.set()
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._server = None
        for task in list(self._tasks):
            task.cancel()
        if self._tasks:
            await asyncio.gather(*list(self._tasks), return_exceptions=True)
        for task in (self._ami_task, self._outbox_task):
            if task is not None:
                task.cancel()
        await self._ami.close()
        self.ami_connected = False

    async def admit(self, number: str) -> tuple[bool, str]:
        """Reserve the single call for an inbound GSM leg. Does not touch a live call."""
        cleaned = normalize_msisdn(number) or (number or "").strip()
        async with self._lock:
            self._expire_pending()
            if self.active is not None or self._pending is not None or self._line_busy():
                LOG.info("gateway admit refused number=%s busy_slot=%s", cleaned, self._line_busy())
                return False, ""
            uid = str(uuid.uuid4())
            self._pending = _Pending(uid, cleaned, "in", time.monotonic() + self.config.reservation_s)
            return True, uid

    async def dial(self, number: str) -> dict[str, Any]:
        cleaned = normalize_msisdn(number)
        if not cleaned:
            return {"ok": False, "status": 400, "error": "number required"}
        async with self._lock:
            self._expire_pending()
            if self.active is not None or self._pending is not None or self._line_busy():
                return {"ok": False, "status": 409, "error": "busy"}
            if not self.ami_connected:
                return {"ok": False, "status": 503, "error": "ami down"}
            uid = str(uuid.uuid4())
            self._pending = _Pending(uid, cleaned, "out", time.monotonic() + self.config.reservation_s)
        variables = f"GW_UUID={uid},GW_DIR=out,GW_NUMBER={cleaned}"
        try:
            response = await self._ami.request(
                {
                    "Action": "Originate",
                    "Channel": f"Quectel/{self.config.device}/{cleaned}",
                    "Context": "gateway-media",
                    "Exten": "s",
                    "Priority": "1",
                    "Async": "true",
                    "Timeout": "60000",
                    "Variable": variables,
                    "CallerID": f"gsm-gateway <{cleaned}>",
                }
            )
        except Exception as exc:
            await self._drop_pending(uid)
            LOG.warning("originate failed: %s", exc)
            return {"ok": False, "status": 502, "error": "originate failed"}
        if (response.get("Response") or "").lower() == "error":
            await self._drop_pending(uid)
            return {"ok": False, "status": 502, "error": response.get("Message") or "originate refused"}
        return {"ok": True, "status": 200, "uuid": uid, "to": cleaned}

    async def flush_outbox(self) -> int:
        """Lease due portal outbox rows and send them on the modem. Returns how many were acked."""
        if not self.ami_connected:
            return 0
        async with self._outbox_lock:
            try:
                items = await self.hub.claim_outbox()
            except Exception:
                LOG.warning("outbox poll failed", exc_info=True)
                return 0
            acked = 0
            for item in items:
                item_id = str(item.get("id") or "")
                number = normalize_msisdn(str(item.get("to") or ""))
                body = str(item.get("body") or "")
                if not item_id or not number or not body.strip():
                    await self._ack(item_id, "failed", "missing to or body")
                    acked += 1
                    continue
                command = sms_cli(self.config.device, number, body)
                try:
                    response = await self._ami.request({"Action": "Command", "Command": command})
                    ok = command_accepted(response)
                except Exception as exc:
                    LOG.warning("quectel sms failed: %s", exc)
                    ok = False
                    response = {"Message": str(exc)}
                if ok:
                    await self._ack(item_id, "sent", None)
                else:
                    await self._ack(item_id, "failed", (response.get("Message") or response.get("_body") or "modem rejected")[:200])
                acked += 1
            return acked

    async def _ack(self, item_id: str, status: str, error: Optional[str]) -> None:
        if not item_id:
            return
        try:
            await self.hub.ack_outbox(item_id, status, error)
        except Exception:
            LOG.warning("outbox ack failed id=%s", item_id, exc_info=True)

    async def _on_ami(self, event: dict[str, str]) -> None:
        sms = sms_from_ami(event)
        if sms is None:
            return
        try:
            result = await self.hub.post_sms(sms)
        except Exception:
            LOG.warning("inbound sms post failed", exc_info=True)
            return
        LOG.info(
            "gateway sms from=%s client=%s http=%s",
            sms.get("from"),
            sms.get("clientId") or "-",
            result.get("status"),
        )

    async def _on_audio(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        task = asyncio.current_task()
        if task is not None:
            self._tasks.add(task)
        sock = AudioSocket(reader, writer)
        try:
            try:
                kind, payload = await asyncio.wait_for(sock.read(), timeout=5)
            except Exception as exc:
                LOG.info("audiosocket closed before uuid: %s", exc)
                return
            if kind != AS_UUID:
                await sock.hangup()
                return
            try:
                call_uuid = _uuid_from_bytes(payload)
            except GatewayError as exc:
                LOG.info("audiosocket uuid rejected: %s", exc)
                await sock.hangup()
                return
            claimed = await self._claim(call_uuid)
            if not claimed:
                LOG.info("audiosocket rejected uuid=%s (line busy or reserved)", call_uuid)
                await sock.hangup()
                return
            try:
                await self._bridge(sock, claimed)
            except PhoneBusy:
                LOG.info("hub rejected gateway websocket; leaving the live call up")
                await sock.hangup()
            except Exception:
                LOG.warning("gateway bridge failed", exc_info=True)
                await sock.hangup()
            finally:
                await self._finish(claimed)
        finally:
            if task is not None:
                self._tasks.discard(task)
            try:
                writer.close()
            except Exception:
                pass

    async def _bridge(self, sock: AudioSocket, call: _Active) -> None:
        phone = await self.hub.open_phone()
        call.session_id = phone.session_id
        call.started_wall = time.time()
        try:
            await phone.set_formats()

            async def uplink() -> str:
                while True:
                    kind, payload = await sock.read()
                    if kind in (AS_HANGUP, AS_ERROR):
                        return "gsm"
                    if kind == AS_AUDIO and payload:
                        await phone.send_audio(payload)
                    elif kind == AS_DTMF:
                        continue

            async def downlink() -> str:
                while True:
                    pcm = await phone.downlink.get()
                    if pcm is None:
                        return "hub"
                    await sock.send_audio(pcm)

            up_task = asyncio.create_task(uplink(), name="gsm-uplink")
            down_task = asyncio.create_task(downlink(), name="gsm-downlink")
            done, pending = await asyncio.wait(
                {up_task, down_task}, return_when=asyncio.FIRST_COMPLETED
            )
            for task in pending:
                task.cancel()
            for task in done:
                exc = task.exception()
                if exc is not None and not isinstance(exc, (ConnectionError, asyncio.IncompleteReadError)):
                    LOG.info("gateway audio leg ended: %s", exc)
            await asyncio.gather(*pending, return_exceptions=True)
        finally:
            await phone.close()
            await sock.hangup()

    async def _claim(self, call_uuid: str) -> Optional[_Active]:
        async with self._lock:
            self._expire_pending()
            if self.active is not None or self._line_busy():
                return None
            pending = self._pending
            # Only a uuid this process just handed to Asterisk may open audio.
            # A stray TCP client, or a leg whose reservation expired, is hung up.
            if pending is None or pending.uuid != call_uuid:
                return None
            self._pending = None
            active = _Active(call_uuid, pending.number, pending.direction)
            self.active = active
            return active

    async def _finish(self, call: _Active) -> None:
        async with self._lock:
            if self.active is call:
                self.active = None
        if call.reported or not call.session_id:
            call.reported = True
            return
        call.reported = True
        duration = max(0.0, time.time() - call.started_wall) if call.started_wall else 0.0
        ended = _utc_now()
        started = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(call.started_wall or time.time()))
        text = (
            f"call_ended duration_s={duration:.0f} source=asterisk_gateway "
            f"direction={call.direction} number={call.number or '-'} call_id={call.session_id}"
        )
        payload = {
            "event": "call_ended",
            "test": False,
            "call_id": call.session_id,
            "caller_number": call.number,
            "direction": call.direction,
            "duration_s": round(duration, 1),
            "source": "asterisk_gateway",
            "started_at_utc": started,
            "ended_at_utc": ended,
            "text": text,
        }
        try:
            await self.hub.post_call(
                {
                    "direction": call.direction,
                    "number": call.number or "unknown",
                    "started_at": started,
                    "duration_sec": int(duration),
                    "session_id": call.session_id,
                }
            )
        except Exception:
            LOG.warning("gateway call log post failed", exc_info=True)
        if self._on_call_ended is not None:
            try:
                self._on_call_ended(payload)
            except Exception:
                LOG.warning("gateway call_ended failed", exc_info=True)

    def _line_busy(self) -> bool:
        try:
            return bool(self._busy())
        except Exception:
            LOG.warning("call slot check failed; refusing the new gsm leg", exc_info=True)
            return True

    def _expire_pending(self) -> None:
        pending = self._pending
        if pending is not None and pending.deadline <= time.monotonic():
            self._pending = None

    async def _drop_pending(self, uid: str) -> None:
        async with self._lock:
            if self._pending is not None and self._pending.uuid == uid:
                self._pending = None

    async def _ami_loop(self) -> None:
        while not self._stopped.is_set():
            if not self.config.ami_host:
                return
            try:
                await self._ami.connect(
                    self.config.ami_host,
                    self.config.ami_port,
                    self.config.ami_user,
                    self.config.ami_secret,
                )
                self.ami_connected = True
                LOG.info("ami connected %s:%s", self.config.ami_host, self.config.ami_port)
                await self._ami.wait_closed()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                LOG.warning("ami unavailable: %s", exc)
            self.ami_connected = False
            self._ami.connected = False
            if self._stopped.is_set():
                return
            try:
                await asyncio.wait_for(self._stopped.wait(), timeout=2)
            except asyncio.TimeoutError:
                pass

    async def _outbox_loop(self) -> None:
        while not self._stopped.is_set():
            try:
                await self.flush_outbox()
            except asyncio.CancelledError:
                raise
            except Exception:
                LOG.warning("outbox loop failed", exc_info=True)
            try:
                await asyncio.wait_for(self._stopped.wait(), timeout=self.config.outbox_poll_s)
            except asyncio.TimeoutError:
                pass


async def handle_gateway_http(
    runtime: GatewayRuntime,
    method: str,
    path: str,
    query: dict[str, list[str]],
    body: bytes,
) -> bytes:
    """Tailscale-entrance routes. The public entrance never reaches this."""
    if method == "GET" and path == "/gateway/health":
        return json_http(200, {"ok": True, **runtime.snapshot()})
    if method == "GET" and path == "/gateway/admit":
        number = (query.get("number") or [""])[0]
        ok, uid = await runtime.admit(number)
        if not ok:
            return text_http(200, "no\n")
        return text_http(200, f"yes:{uid}\n")
    if method == "POST" and path == "/gateway/dial":
        try:
            payload = json.loads(body.decode("utf-8") if body else "{}")
        except json.JSONDecodeError:
            return json_http(400, {"ok": False, "error": "invalid json"})
        if not isinstance(payload, dict):
            return json_http(400, {"ok": False, "error": "invalid json"})
        result = await runtime.dial(str(payload.get("to") or payload.get("number") or ""))
        status = int(result.pop("status", 200))
        return json_http(status, result)
    return json_http(404, {"ok": False, "error": "not found"})


async def start_gateway_from_env(busy: BusyFn) -> Optional[GatewayRuntime]:
    """Start the gateway when the flag is on. None leaves the phone path alone."""
    config = GatewayConfig.from_env()
    if not config.enabled:
        return None

    def ended(payload: dict[str, Any]) -> None:
        from alert_webhook import post_alert_event

        post_alert_event(payload)

    runtime = GatewayRuntime(config, busy=busy, on_call_ended=ended)
    await runtime.start()
    return runtime
