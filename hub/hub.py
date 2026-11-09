#!/usr/bin/env python3
"""gsm2computer hub: PCM/μ-law WebSocket ↔ PipeWire gsm_bus + switchboard control."""
from __future__ import annotations

import asyncio
import collections
import audioop
import base64
import contextvars
import enum
import hashlib
import json
import logging
import os
import re
import signal
import socket
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional, Tuple

from alert_webhook import post_system_alert, start_alert_worker
from device_auth import (
    ENTRANCE_PUBLIC,
    TOKENS,
    Entrance,
    authorize,
    public_route_allowed,
)
from ws_backpressure import (
    UPLINK_BACKLOG_BYTES,
    UPLINK_QUEUE_FRAMES,
    UPLINK_WRITE_TIMEOUT_S,
    DownlinkSendGuard,
    offer_nowait,
    reconnect_should_supersede,
    transport_buffered,
    writer_closing,
)
from call_link import (
    RelinkGate,
    WsRead,
    is_clean_hangup,
    load_tailscale_status,
    parse_tailscale_status,
    read_peer_socket,
    wait_for_ws,
)
from call_slot import (
    CallSlot,
    CallWatchdogConfig,
    admin_release_allowed,
    ensure_released_after_abort,
    preempt_e2e_for_real_call,
    wait_or_abort,
)
from link_gap_log import append_link_gap
from grokbot_transcript import (
    announce_transcript,
    attach_levels,
    capture_call_transcript,
    cdp_base_from_status,
    load_saved_pointer,
    pointer_or_aborted,
    save_transcript_result,
)
from grokbot_voice import (
    GROKBOT_MIC_SINK,
    GROKBOT_MIC_SOURCE,
    GROKBOT_SPK_SINK,
    UPLINK_GAIN as GROKBOT_UPLINK_GAIN,
    get_grokbot_voice,
    run_patchbay,
)
from urllib.parse import parse_qs, urlsplit
from call_tap import RECORD_DIR, CallTap
from portal_http import EventBus, PortalApp
from portal_sms import ingest_inbound_sms
from portal_store import PortalStore
from pipewire_target import (
    PipewireLinkError,
    pipewire_stream_env,
    pw_cat_raw_args,
    pw_latency_args,
    record_uses_sink_capture,
    stdbuf_unbuffered,
    resolve_pipewire_playback_target,
    resolve_pipewire_record_target,
    wait_for_pipewire_link,
)

HOST = os.environ.get("GSM2COMPUTER_HUB_HOST", "100.113.222.47")
PORT = int(os.environ.get("GSM2COMPUTER_HUB_PORT", "8787"))
HUB_DIR = Path(os.environ.get("GSM2COMPUTER_HUB_DIR", Path(__file__).resolve().parent))
SWITCHBOARD = HUB_DIR / "switchboard.sh"
GSM_SINK = os.environ.get("GSM2COMPUTER_GSM_SINK", "gsm_bus")
GSM_MONITOR = os.environ.get("GSM2COMPUTER_GSM_MONITOR", "gsm_bus")
PHONE_UPLINK_SINK = os.environ.get("GSM2COMPUTER_PHONE_UPLINK_SINK", "phone_uplink")
OPENCLAW_DOWNLINK_MONITOR = os.environ.get(
    "GSM2COMPUTER_OPENCLAW_DOWNLINK_MONITOR", "openclaw_bus"
)
AUDIO_RATE = int(os.environ.get("GSM2COMPUTER_AUDIO_RATE", "8000"))
# PipeWire buses on safwat-eu are 48 kHz. Clients may send 8 kHz μ-law
# (simulator) or PCM at whatever the phone HAL gave us; we resample here.
BUS_RATE = int(os.environ.get("GSM2COMPUTER_BUS_RATE", "48000"))
AUTO_MODE_ON_CALL = os.environ.get("GSM2COMPUTER_AUTO_MODE", "openclaw")
# AUTO_MODE=grokbot answers calls with Grok Bot live voice (patchbay) instead of
# OpenClaw Talk. OpenClaw stays selectable with AUTO_MODE=openclaw.
GROKBOT_ON_CALL = (AUTO_MODE_ON_CALL or "").strip().lower() == "grokbot"


def _parse_openclaw_talk_mode(raw: Optional[str]) -> str:
    """webrtc-ui is default. relay is an explicit legacy escape. Never auto-fallback."""
    value = (raw if raw is not None else "webrtc-ui").strip().lower()
    if value in ("0", "false", "no", "off"):
        return "off"
    if value in ("relay", "gateway-relay", "session"):
        return "relay"
    return "webrtc-ui"


OPENCLAW_TALK_MODE = _parse_openclaw_talk_mode(os.environ.get("GSM2COMPUTER_OPENCLAW_TALK", "webrtc-ui"))
OPENCLAW_TALK_ON_CALL = OPENCLAW_TALK_MODE != "off"
RECORD_CHUNK = int(os.environ.get("GSM2COMPUTER_RECORD_CHUNK", "160"))
DEFAULT_CLIENT_FORMAT = "audio/pcmu"
DEFAULT_CLIENT_RATE = AUDIO_RATE
PCM_FORMATS = {"audio/pcm", "audio/l16", "pcm"}
ULAW_FORMATS = {"audio/pcmu", "audio/g711-ulaw", "pcmu", "g711_ulaw"}
ULAW_BIAS = 0x84
TTS_ENERGY_WATCHDOG_S = float(os.environ.get("GSM2COMPUTER_TTS_ENERGY_WATCHDOG", "2.5"))
TTS_WATCHDOG_LOUD_FRAMES = int(os.environ.get("GSM2COMPUTER_TTS_WATCHDOG_LOUD_FRAMES", "8"))
TALK_ROLLOVER_S = float(os.environ.get("GSM2COMPUTER_TALK_ROLLOVER_S", "1500"))
CALL_WATCHDOG = CallWatchdogConfig.from_env()

LOG = logging.getLogger("gsm2computer-hub")
WS_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"

current_mode: Optional[str] = None
WS_SEND_TIMEOUT_S = float(os.environ.get("GSM2COMPUTER_WS_SEND_TIMEOUT_S", "5"))
WS_PING_SKIP_BACKLOG_BYTES = 256 * 1024
DOWNLINK_MAX_AGE_S = float(os.environ.get("GSM2COMPUTER_WS_DOWNLINK_MAX_AGE_S", "1.2") or 1.2)
SUPERSEDED_REASON = "superseded by Pixel reconnect"
RECONNECT_TAKEOVER_WAIT_S = float(os.environ.get("GSM2COMPUTER_RECONNECT_TAKEOVER_WAIT_S", "8"))
active_bridge: Optional["PipewireBridge"] = None
active_ws_writer: Optional[asyncio.StreamWriter] = None
active_openclaw: Optional[Any] = None
live_call = CallSlot(CALL_WATCHDOG)
relink_gate = RelinkGate()
last_call_tap: Optional[dict[str, Any]] = None
# session_id -> (entrance, monotonic time the socket finished)
_finished_call_entrances: dict[str, tuple[str, float]] = {}
_finished_call_order: list[str] = []
_active_entrances: list[Entrance] = []
_current_entrance: contextvars.ContextVar[Optional[Entrance]] = contextvars.ContextVar(
    "gsm_entrance", default=None
)
_current_device_id: contextvars.ContextVar[str] = contextvars.ContextVar(
    "gsm_device_id", default=""
)

try:
    from openclaw_talk_bridge import OpenClawTalkBridge
except ImportError:
    OpenClawTalkBridge = None  # type: ignore[misc, assignment]

try:
    from talk_chromium import OpenClawTalkUI, TalkUiError, get_talk_ui
except ImportError:
    OpenClawTalkUI = None  # type: ignore[misc, assignment]
    TalkUiError = RuntimeError  # type: ignore[misc, assignment]
    get_talk_ui = None  # type: ignore[misc, assignment]


def json_response(status: int, body: dict, extra_headers: Optional[dict] = None) -> bytes:
    payload = json.dumps(body).encode("utf-8")
    status_text = {
        200: "OK",
        201: "Created",
        400: "Bad Request",
        401: "Unauthorized",
        404: "Not Found",
        408: "Request Timeout",
        409: "Conflict",
        413: "Payload Too Large",
        431: "Request Header Fields Too Large",
        500: "Internal Server Error",
        503: "Service Unavailable",
    }.get(status, "")
    lines = [
        f"HTTP/1.1 {status} {status_text}".rstrip(),
        "Content-Type: application/json",
        f"Content-Length: {len(payload)}",
        "Connection: close",
    ]
    if extra_headers:
        for k, v in extra_headers.items():
            lines.append(f"{k}: {v}")
    lines.extend(["", ""])
    return "\r\n".join(lines).encode("ascii") + payload


class HttpRequestError(Exception):
    """A request we can answer and then close. status is the HTTP code."""

    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status


# Headers are read before auth, so the cap applies to every entrance.
# Bodies are read only after auth. Public phone payloads (SMS, call reports)
# are far below 64 KiB; the Tailscale cap still bounds the portal.
MAX_HTTP_HEADER_BYTES = 16 * 1024
MAX_HTTP_BODY_BYTES = 1024 * 1024
MAX_PUBLIC_HTTP_BODY_BYTES = 64 * 1024


def _http_timeout(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None or not str(raw).strip():
        return default
    try:
        return float(raw)
    except ValueError:
        return default


async def read_http_headers(reader: asyncio.StreamReader) -> Tuple[str, str, dict]:
    """Read the request line and headers. Does not read a body."""
    timeout = _http_timeout("GSM2COMPUTER_HTTP_HEADER_TIMEOUT_S", 8.0)

    async def _read() -> Tuple[str, str, dict]:
        header_lines: list[bytes] = []
        seen = 0
        while True:
            try:
                line = await reader.readline()
            except asyncio.LimitOverrunError as exc:
                # StreamReader gives up at 64 KiB before this loop can count.
                raise HttpRequestError(431, "headers too large") from exc
            except ValueError as exc:
                # readline() turns LimitOverrunError into ValueError and drops the line.
                if "limit" in str(exc).lower():
                    raise HttpRequestError(431, "headers too large") from exc
                raise
            if not line:
                raise ConnectionError("client closed")
            seen += len(line)
            if seen > MAX_HTTP_HEADER_BYTES:
                raise HttpRequestError(431, "headers too large")
            if line in (b"\r\n", b"\n"):
                break
            header_lines.append(line.rstrip(b"\r\n"))
        if not header_lines:
            raise ValueError("empty request")
        request_line = header_lines[0].decode("iso-8859-1")
        parts = request_line.split()
        if len(parts) < 2:
            raise ValueError(f"bad request line: {request_line!r}")
        method, path = parts[0], parts[1]
        headers: dict[str, str] = {}
        for raw in header_lines[1:]:
            if b":" not in raw:
                continue
            name, value = raw.split(b":", 1)
            headers[name.decode("iso-8859-1").strip().lower()] = value.decode("iso-8859-1").strip()
        return method.upper(), path, headers

    try:
        return await asyncio.wait_for(_read(), timeout)
    except asyncio.TimeoutError as exc:
        raise HttpRequestError(408, "header timeout") from exc


def split_request_target(target: str) -> tuple[str, dict[str, str]]:
    """Path plus the last value of each query key. Portal tests import this."""
    parts = urlsplit(target)
    query = {key: values[-1] for key, values in parse_qs(parts.query, keep_blank_values=True).items()}
    return parts.path or "/", query


def content_length_header(headers: dict) -> int:
    raw = headers.get("content-length")
    if raw is None or raw == "":
        return 0
    if not str(raw).isdigit():
        raise HttpRequestError(400, "bad content-length")
    return int(raw)


async def read_http_body(reader: asyncio.StreamReader, length: int) -> bytes:
    if length <= 0:
        return b""
    timeout = _http_timeout("GSM2COMPUTER_HTTP_BODY_TIMEOUT_S", 15.0)
    try:
        return await asyncio.wait_for(reader.readexactly(length), timeout)
    except asyncio.TimeoutError as exc:
        raise HttpRequestError(408, "body timeout") from exc


def ws_accept_key(sec_key: str) -> str:
    digest = hashlib.sha1((sec_key + WS_GUID).encode("ascii")).digest()
    return base64.b64encode(digest).decode("ascii")


def ws_encode_text(text: str) -> bytes:
    data = text.encode("utf-8")
    length = len(data)
    if length < 126:
        header = bytes([0x81, length])
    elif length < 65536:
        header = bytes([0x81, 126]) + length.to_bytes(2, "big")
    else:
        header = bytes([0x81, 127]) + length.to_bytes(8, "big")
    return header + data


async def _soft_drain(writer: asyncio.StreamWriter, timeout: float = WS_SEND_TIMEOUT_S) -> bool:
    """Wait for the transport to flush, but never fail the call on a slow link.

    A drain timeout means the peer is not ACKing (e.g. Tailscale hiccup); the
    bytes stay queued. Dead sockets are detected by DownlinkSendGuard (no
    progress for GSM2COMPUTER_WS_DOWNLINK_DEAD_S) and the ws-idle watchdog.
    Connection errors still raise.
    """
    try:
        await asyncio.wait_for(writer.drain(), timeout=timeout)
        return True
    except asyncio.TimeoutError:
        LOG.warning("websocket drain slow (>%.0fs); keeping call, data queued", timeout)
        return False


async def ws_send_text(writer: asyncio.StreamWriter, text: str) -> bool:
    writer.write(ws_encode_text(text))
    return await _soft_drain(writer)


def ws_queue_text(writer: asyncio.StreamWriter, text: str) -> None:
    """Queue a text frame and return. Awaiting drain here stalls the uplink read loop."""
    if writer_closing(writer):
        raise ConnectionResetError("websocket transport closing")
    writer.write(ws_encode_text(text))


async def ws_send_pong(writer: asyncio.StreamWriter, payload: bytes = b"") -> None:
    # Never await drain here: this runs inside the uplink read loop, and a
    # stalled downlink must not stop us reading the Pixel's frames.
    length = len(payload)
    header = bytes([0x8A, length])
    if not writer_closing(writer):
        writer.write(header + payload)


async def ws_send_ping(writer: asyncio.StreamWriter, payload: bytes = b"hub") -> None:
    length = len(payload)
    if length > 125:
        raise ValueError("ping payload too long")
    header = bytes([0x89, length])
    if writer_closing(writer):
        raise ConnectionResetError("websocket transport closing")
    # Non-blocking: during a stall the ping just queues (skip if backlog huge).
    if transport_buffered(writer) < WS_PING_SKIP_BACKLOG_BYTES:
        writer.write(header + payload)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _use_utc_logging() -> None:
    fmt = logging.Formatter(
        "%(asctime)s.%(msecs)03dZ %(levelname)s %(name)s: %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )
    fmt.converter = time.gmtime  # type: ignore[attr-defined]
    root = logging.getLogger()
    if not root.handlers:
        logging.basicConfig(level=logging.INFO)
    for handler in root.handlers:
        handler.setFormatter(fmt)


async def ws_read_frame(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> WsRead:
    """Read one frame. An ended frame carries the close code and who closed."""
    try:
        hdr = await reader.readexactly(2)
    except asyncio.IncompleteReadError:
        return WsRead(ended=True, initiator="eof", close_reason="incomplete read")
    except (ConnectionResetError, BrokenPipeError) as exc:
        return WsRead(ended=True, initiator="reset", close_reason=str(exc) or type(exc).__name__)
    except (ConnectionError, OSError) as exc:
        return WsRead(ended=True, initiator="reset", close_reason=type(exc).__name__)
    opcode = hdr[0] & 0x0F
    masked = bool(hdr[1] & 0x80)
    length = hdr[1] & 0x7F
    try:
        if length == 126:
            length = int.from_bytes(await reader.readexactly(2), "big")
        elif length == 127:
            length = int.from_bytes(await reader.readexactly(8), "big")
        mask = await reader.readexactly(4) if masked else None
        payload = await reader.readexactly(length)
    except asyncio.IncompleteReadError:
        return WsRead(ended=True, initiator="eof", close_reason="incomplete read")
    except (ConnectionResetError, BrokenPipeError, ConnectionError, OSError) as exc:
        return WsRead(ended=True, initiator="reset", close_reason=type(exc).__name__)
    if masked and mask:
        payload = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))

    if opcode == 0x8:
        code = None
        reason = ""
        if len(payload) >= 2:
            code = int.from_bytes(payload[:2], "big")
            reason = payload[2:].decode("utf-8", errors="replace")
        return WsRead(ended=True, initiator="peer", close_code=code, close_reason=reason)
    if opcode == 0x9:
        await ws_send_pong(writer, payload)
        return WsRead(text="")
    if opcode == 0xA:
        return WsRead(text="", pong=True)
    if opcode == 0x1:
        return WsRead(text=payload.decode("utf-8", errors="replace"))
    if opcode == 0x2:
        return WsRead(binary=payload)
    return WsRead(text="")



def parse_uplink_binary(payload: bytes) -> Optional[Tuple[bytes, str, int]]:
    """G2 v1 uplink frame from phone builds >=331: 'G2', ver 1, fmt 1 (s16le mono), rate u16 BE, 2 reserved, pcm."""
    if len(payload) < 8 or payload[0:2] != b"G2" or payload[2] != 1 or payload[3] != 1:
        return None
    rate = int.from_bytes(payload[4:6], "big")
    pcm = payload[8:]
    if rate <= 0 or len(pcm) < 2:
        return None
    if len(pcm) & 1:
        pcm = pcm[:-1]
    return pcm, "audio/pcm", rate

async def ws_send_close(writer: asyncio.StreamWriter, code: int, reason: str) -> None:
    payload = code.to_bytes(2, "big") + reason.encode("utf-8")[:120]
    writer.write(bytes([0x88, len(payload)]) + payload)
    await asyncio.wait_for(writer.drain(), timeout=WS_SEND_TIMEOUT_S)


async def fail_call_handshake(writer: asyncio.StreamWriter, message: str) -> None:
    LOG.error("call handshake failed: %s", message)
    try:
        await ws_send_text(
            writer,
            json.dumps(
                {
                    "type": "error",
                    "error": {
                        "message": message,
                        "code": "pipewire_handshake_failed",
                    },
                }
            ),
        )
        await ws_send_close(writer, 1011, message)
    except (ConnectionError, BrokenPipeError, OSError) as exc:
        LOG.error("could not send handshake error to client: %s", exc)


async def run_switchboard(*args: str) -> tuple[int, str, str]:
    if not SWITCHBOARD.is_file():
        return 1, "", f"missing switchboard script: {SWITCHBOARD}"
    proc = await asyncio.create_subprocess_exec(
        str(SWITCHBOARD),
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=str(HUB_DIR),
    )
    stdout_b, stderr_b = await proc.communicate()
    return proc.returncode or 0, stdout_b.decode("utf-8", errors="replace"), stderr_b.decode("utf-8", errors="replace")


async def parse_switchboard_links() -> list[dict[str, str]]:
    proc = await asyncio.create_subprocess_exec(
        "pw-link",
        "-l",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )
    stdout_b, _ = await proc.communicate()
    links: list[dict[str, str]] = []
    bus_re = re.compile(r"(gsm_bus|openclaw_bus|phone_uplink|whatsapp_bus|telegram_bus)")
    current_src: Optional[str] = None
    for raw in stdout_b.decode("utf-8", errors="replace").splitlines():
        line = raw.strip()
        if not line:
            continue
        if "|->" in line:
            if not bus_re.search(line):
                continue
            _, dst = [part.strip() for part in line.split("|->", 1)]
            if current_src:
                links.append({"from": current_src, "to": dst})
            continue
        if bus_re.search(line):
            current_src = line
    return links


async def switchboard_state() -> dict:
    rc, status_out, status_err = await run_switchboard("status")
    links = await parse_switchboard_links()
    return {
        "ok": rc == 0,
        "mode": current_mode,
        "links": links,
        "status_text": status_out.strip(),
        "stderr": status_err.strip() or None,
    }


async def set_switchboard_mode(mode: str) -> dict:
    global current_mode
    allowed = {"clear", "openclaw", "grokbot", "loopback", "conference", "status"}
    if mode not in allowed:
        return {"ok": False, "error": f"unknown mode {mode!r}", "allowed": sorted(allowed - {"status"})}
    rc, out, err = await run_switchboard(mode)
    if rc != 0:
        return {"ok": False, "error": err or out or f"switchboard {mode} failed"}
    if mode != "status":
        current_mode = None if mode == "clear" else mode
    state = await switchboard_state()
    state["applied"] = mode
    state["message"] = out.strip()
    return state


def _ulaw_decode_sample(pcmu: int) -> int:
    b = (pcmu & 0xff) ^ 0xff
    sign = b & 0x80
    exponent = (b >> 4) & 0x07
    mantissa = b & 0x0f
    sample = ((mantissa << 3) + ULAW_BIAS) << exponent
    sample -= ULAW_BIAS
    return -sample if sign else sample


def _ulaw_encode_sample(pcm: int) -> int:
    sample = pcm
    sign = (sample >> 8) & 0x80
    if sign:
        sample = -sample
    if sample > 32635:
        sample = 32635
    sample += ULAW_BIAS
    if sample > 32635:
        sample = 32635
    exponent = 0
    while exponent < 7 and (sample >> (exponent + 8)) != 0:
        exponent += 1
    mantissa = (sample >> (exponent + 3)) & 0x0f
    raw = (sign & 0x80) | (exponent << 4) | mantissa
    return raw ^ 0xff


def _mulaw_rms_energy(ulaw: bytes) -> float:
    """RMS of decoded PCM / 32768 (~0–1; matches simulator mulawEnergy)."""
    if not ulaw:
        return 0.0
    sum_sq = 0.0
    for b in ulaw:
        s = _ulaw_decode_sample(b) / 32768.0
        sum_sq += s * s
    return (sum_sq / len(ulaw)) ** 0.5


def _pcm_rms_energy(pcm: bytes) -> float:
    if not pcm or len(pcm) < 2:
        return 0.0
    return audioop.rms(pcm, 2) / 32768.0


def _normalize_format(raw: Any) -> str:
    value = str(raw or "").strip().lower()
    if value in PCM_FORMATS:
        return "audio/pcm"
    if value in ULAW_FORMATS:
        return "audio/pcmu"
    return ""


def _as_rate(raw: Any, default: int) -> int:
    try:
        rate = int(raw)
    except (TypeError, ValueError):
        return default
    return rate if rate > 0 else default


def _to_pcm16(audio: bytes, fmt: str) -> bytes:
    if fmt == "audio/pcm":
        if len(audio) & 1:
            audio = audio[:-1]
        return audio
    return audioop.ulaw2lin(audio, 2)


def _resample_pcm(pcm: bytes, src_rate: int, dst_rate: int, state: Any) -> Tuple[bytes, Any]:
    if src_rate == dst_rate:
        return pcm, state
    converted, new_state = audioop.ratecv(pcm, 2, 1, src_rate, dst_rate, state)
    return converted, new_state


PORTAL_DB = Path(os.environ.get("GSM2COMPUTER_PORTAL_DB", str(HUB_DIR / "portal.sqlite")))
portal_store = PortalStore(PORTAL_DB)
portal_events = EventBus()


def _env_flag(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None or not str(raw).strip():
        return default
    return str(raw).strip().lower() not in ("0", "false", "no", "off")


def request_entrance() -> Entrance:
    """Listen socket for this connection. Unset means the Tailscale entrance."""
    current = _current_entrance.get()
    if current is not None:
        return current
    return Entrance(
        "tailscale",
        HOST,
        PORT,
        _env_flag("GSM2COMPUTER_TAILSCALE_TOKEN_REQUIRED", False),
    )


def remember_finished_call(session_id: str, entrance: str) -> None:
    if not session_id or entrance not in ("public", "tailscale"):
        return
    _finished_call_entrances[session_id] = (entrance, time.monotonic())
    _finished_call_order.append(session_id)
    while len(_finished_call_order) > 32:
        old = _finished_call_order.pop(0)
        if old not in _finished_call_order:
            _finished_call_entrances.pop(old, None)


def resolve_call_path(session_id: Optional[str], requested: Optional[str]) -> Optional[str]:
    """Entrance this process observed for session_id.

    The client's path is never stored. A missing session id, or a session id
    this process did not accept, is unknown (None) even when another call is
    busy and even when the client claims public or tailscale.
    """
    if requested and requested not in (None, ""):
        LOG.info(
            "call report client path %s ignored session=%s",
            requested,
            session_id or "-",
        )
    if not session_id:
        return None
    hit = _finished_call_entrances.get(session_id)
    if hit is not None:
        return hit[0]
    if (
        live_call.busy
        and live_call.session_id == session_id
        and live_call.entrance in ("public", "tailscale")
    ):
        return live_call.entrance
    return None


def session_description(
    session_id: str, *, loopback: bool, e2e: bool, grokbot: bool = False
) -> dict[str, Any]:
    """session.updated from the live slot. Built at send time so a relink onto
    the other entrance is not stuck with the entrance of the first socket.

    grokbot selects the production Grok Bot model/talk fields. The entrance
    still comes from the slot, so a public-socket call reports public.
    """
    if loopback:
        model = "gsm2computer-hub"
        talk = "off"
    elif grokbot:
        model = "gsm2computer-hub-grokbot"
        talk = "grokbot"
    else:
        model = f"gsm2computer-hub-{OPENCLAW_TALK_MODE}"
        talk = OPENCLAW_TALK_MODE
    return {
        "type": "session.updated",
        "session": {
            "id": session_id,
            "entrance": live_call.entrance or None,
            "model": model,
            "audio": {
                "format": DEFAULT_CLIENT_FORMAT,
                "rate": DEFAULT_CLIENT_RATE,
                "preferred": {"format": "audio/pcm", "rate": BUS_RATE, "encoding": "s16le"},
            },
            "talk": talk,
            "e2e": e2e,
        },
    }


def _portal_mode() -> Optional[str]:
    return current_mode


def _portal_tap() -> Optional[dict[str, Any]]:
    return last_call_tap


portal_app = PortalApp(
    store=portal_store,
    events=portal_events,
    dist_dir=HUB_DIR / "portal" / "dist",
    mode_getter=_portal_mode,
    tap_getter=_portal_tap,
    record_dir=RECORD_DIR,
    path_getter=resolve_call_path,
)


class PipewireBridge:
    def __init__(
        self,
        sink: str,
        monitor: str,
        rate: int,
        tap: Optional[CallTap] = None,
        on_abort: Optional[Any] = None,
    ) -> None:
        self.sink = sink
        self.monitor = monitor
        self.rate = rate
        self.tap = tap
        self._on_abort = on_abort
        self._playback: Optional[asyncio.subprocess.Process] = None
        self._record: Optional[asyncio.subprocess.Process] = None
        self._record_task: Optional[asyncio.Task] = None
        self._helper_tasks: list[asyncio.Task] = []
        self._closed = False
        self._abort = asyncio.Event()
        self.downlink_energy_ticks = 0
        self.playback_linked = False
        self.record_linked = False
        self.client_in_format = DEFAULT_CLIENT_FORMAT
        self.client_in_rate = DEFAULT_CLIENT_RATE
        self.client_out_format = DEFAULT_CLIENT_FORMAT
        self.client_out_rate = DEFAULT_CLIENT_RATE
        self._in_ratecv: Any = None
        self._out_ratecv: Any = None
        self._in_src_rate = DEFAULT_CLIENT_RATE
        self._out_dst_rate = DEFAULT_CLIENT_RATE
        self.downlink_guard = DownlinkSendGuard()
        self._dl_q = collections.deque()
        self.dl_seq = 0
        self.dl_sent = 0
        self.dl_dropped_oldest = 0
        self.dl_max_age_s = 0.0
        self._stall_logged_recovery = True
        self._out_writer: Optional[asyncio.StreamWriter] = None
        # Caller audio is queued here and written to pw-cat on a side task.
        # The WebSocket read loop must not await stdin.drain(): a stalled
        # playback pipe used to freeze ping/pong handling and the relink path.
        self._uplink_q: Optional[asyncio.Queue] = None
        self._uplink_task: Optional[asyncio.Task] = None
        self.uplink_dropped = 0
        self.uplink_written = 0
        # Linear gain on caller audio written to the playback sink (grokbot path).
        self.uplink_gain = 1.0

    def _track_helper(self, proc: asyncio.subprocess.Process, label: str) -> None:
        self._helper_tasks.append(asyncio.create_task(self._log_helper_stderr(proc, label)))
        self._helper_tasks.append(asyncio.create_task(self._watch_helper_exit(proc, label)))

    async def _log_helper_stderr(self, proc: asyncio.subprocess.Process, label: str) -> None:
        if not proc.stderr:
            return
        try:
            while True:
                line = await proc.stderr.readline()
                if not line:
                    return
                text = line.decode("utf-8", errors="replace").rstrip()
                if text:
                    LOG.warning("%s stderr: %s", label, text)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            if not self._closed:
                LOG.warning("%s stderr reader ended: %s", label, exc)

    def _signal_abort(self, reason: str) -> None:
        if self._closed:
            return
        self._abort.set()
        if self._on_abort is not None:
            self._on_abort(reason)

    async def _watch_helper_exit(self, proc: asyncio.subprocess.Process, label: str) -> None:
        rc = await proc.wait()
        if self._closed:
            return
        LOG.error("%s exited early rc=%s; ending call", label, rc)
        self._signal_abort(f"{label} exited early rc={rc}")

    def aborted(self) -> bool:
        return self._abort.is_set()

    async def start(
        self,
        ws_writer: asyncio.StreamWriter,
        monitor: Optional[str] = None,
        record_downlink: bool = True,
    ) -> None:
        record_target = monitor or self.monitor
        playback_serial = await resolve_pipewire_playback_target(self.sink)
        LOG.info("pw-cat playback target %s -> serial %s", self.sink, playback_serial)
        raw = await pw_cat_raw_args()
        latency = pw_latency_args()
        pw_env = pipewire_stream_env(self.rate)
        self._playback = await asyncio.create_subprocess_exec(
            *stdbuf_unbuffered(),
            "pw-cat",
            "--playback",
            *raw,
            *latency,
            "--target",
            playback_serial,
            "--rate",
            str(self.rate),
            "--channels",
            "1",
            "--format",
            "s16",
            "-",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
            env=pw_env,
        )
        self._track_helper(self._playback, "hub pw-cat")
        detail = await wait_for_pipewire_link(
            self._playback,
            playback_serial,
            direction="playback",
            label="hub pw-cat",
        )
        self.playback_linked = True
        LOG.info("hub playback linked: %s", detail)

        if record_downlink:
            capture_sink = record_uses_sink_capture(record_target)
            record_serial = await resolve_pipewire_record_target(record_target)
            LOG.info(
                "pw-record target %s -> serial %s capture_sink=%s",
                record_target,
                record_serial,
                capture_sink,
            )
            rec_env = pipewire_stream_env(self.rate, capture_sink=capture_sink)
            self._record = await asyncio.create_subprocess_exec(
                *stdbuf_unbuffered(),
                "pw-record",
                *raw,
                *latency,
                "--media-category",
                "Capture",
                "--target",
                record_serial,
                "--rate",
                str(self.rate),
                "--channels",
                "2",
                "--format",
                "s16",
                "-",
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=rec_env,
            )
            self._track_helper(self._record, "hub pw-record")
            rec_detail = await wait_for_pipewire_link(
                self._record,
                record_serial,
                direction="record",
                label="hub pw-record",
            )
            self.record_linked = True
            LOG.info("hub record linked: %s", rec_detail)
            self._out_writer = ws_writer
            self._record_task = asyncio.create_task(self._pump_out())
        self._uplink_q = asyncio.Queue(maxsize=UPLINK_QUEUE_FRAMES)
        self._uplink_task = asyncio.create_task(self._pump_uplink(), name="uplink-playback")
        LOG.info(
            "pipewire bridge started sink=%s serial=%s monitor=%s record=%s",
            self.sink,
            playback_serial,
            record_target if record_downlink else "off",
            record_downlink,
        )

    def set_client_audio(
        self,
        input_format: str,
        input_rate: int,
        output_format: str,
        output_rate: int,
    ) -> None:
        in_fmt = _normalize_format(input_format) or DEFAULT_CLIENT_FORMAT
        out_fmt = _normalize_format(output_format) or DEFAULT_CLIENT_FORMAT
        in_rate = _as_rate(input_rate, DEFAULT_CLIENT_RATE)
        out_rate = _as_rate(output_rate, BUS_RATE if out_fmt == "audio/pcm" else DEFAULT_CLIENT_RATE)
        if (
            in_fmt != self.client_in_format
            or in_rate != self.client_in_rate
            or out_fmt != self.client_out_format
            or out_rate != self.client_out_rate
        ):
            LOG.info(
                "client.audio in=%s/%s out=%s/%s",
                in_fmt,
                in_rate,
                out_fmt,
                out_rate,
            )
        if in_rate != self._in_src_rate:
            self._in_ratecv = None
            self._in_src_rate = in_rate
        if out_rate != self._out_dst_rate:
            self._out_ratecv = None
            self._out_dst_rate = out_rate
        self.client_in_format = in_fmt
        self.client_in_rate = in_rate
        self.client_out_format = out_fmt
        self.client_out_rate = out_rate

    def downlink_snapshot(self) -> dict[str, Any]:
        try:
            snap = dict(self.downlink_guard.snapshot())
        except Exception:
            snap = {}
        snap["uplink_dropped_frames"] = self.uplink_dropped
        snap["dl_seq"] = getattr(self, "dl_seq", 0)
        snap["dl_sent"] = getattr(self, "dl_sent", 0)
        snap["dl_dropped_oldest"] = getattr(self, "dl_dropped_oldest", 0)
        snap["dl_queued"] = len(getattr(self, "_dl_q", ()))
        snap["dl_max_age_s"] = round(getattr(self, "dl_max_age_s", 0.0), 2)
        q = self._uplink_q
        snap["uplink_queued"] = q.qsize() if q is not None else 0
        return snap

    def current_downlink_writer(self) -> Optional[asyncio.StreamWriter]:
        writer = self._out_writer
        if writer is None or writer_closing(writer):
            return None
        return writer

    def detach_downlink(self) -> None:
        """Drop the phone socket. pw-record keeps running; frames are discarded."""
        self._out_writer = None

    def attach_downlink(self, writer: asyncio.StreamWriter) -> None:
        """Point downlink at a relinked socket and forget the previous stall."""
        self.downlink_guard = DownlinkSendGuard()
        self._stall_logged_recovery = True
        self._out_writer = writer

    async def _pump_out(self) -> None:
        assert self._record and self._record.stdout
        # 20 ms of stereo s16le at the PipeWire bus rate.
        read_size = self.rate // 50 * 2 * 2
        nonzero = 0
        while not self._closed:
            try:
                chunk = await self._record.stdout.readexactly(read_size)
            except asyncio.IncompleteReadError:
                self._signal_abort("hub pw-record stdout ended")
                break
            except Exception as exc:
                LOG.warning("record read failed: %s", exc)
                self._signal_abort(f"hub pw-record read failed: {exc}")
                break
            left = audioop.tomono(chunk, 2, 1, 0)
            right = audioop.tomono(chunk, 2, 0, 1)
            mono_pcm = audioop.tomono(chunk, 2, 0.5, 0.5)
            if self.tap:
                self.tap.write_s16("gsm-downlink-8k-mono", mono_pcm, self.rate, 1)
            energy_l = audioop.rms(left, 2) / 32768.0
            energy_r = audioop.rms(right, 2) / 32768.0
            if energy_l > 0.02 or energy_r > 0.02:
                nonzero += 1
                self.downlink_energy_ticks = nonzero
                if nonzero == 1 or nonzero % 50 == 0:
                    LOG.info("downlink energy l=%.3f r=%.3f", energy_l, energy_r)
            out_fmt = self.client_out_format
            out_rate = self.client_out_rate
            if out_rate != self._out_dst_rate:
                self._out_ratecv = None
                self._out_dst_rate = out_rate
            payload_pcm, self._out_ratecv = _resample_pcm(
                mono_pcm, self.rate, out_rate, self._out_ratecv
            )
            # What the caller should hear, every mode. Independent of the
            # existing bus-rate gsm-downlink stem and of the app-specific taps.
            self.note_phone_downlink(payload_pcm, out_rate)
            if out_fmt == "audio/pcm":
                payload = payload_pcm
                wire_fmt = "audio/pcm"
            else:
                payload = audioop.lin2ulaw(payload_pcm, 2)
                wire_fmt = "audio/pcmu"
            event = {
                "type": "response.output_audio.delta",
                "delta": base64.b64encode(payload).decode("ascii"),
                "format": wire_fmt,
                "rate": out_rate,
                "channels": {"l": energy_l, "r": energy_r},
            }
            # Never await drain here (a Tailscale hiccup stalls it for the whole
            # outage and used to abort the call after 5 s). Queue while the
            # backlog is small, drop stale frames beyond it, and only give up
            # when queued bytes make no progress for DOWNLINK_DEAD_S.
            guard = self.downlink_guard
            ws_writer = self._out_writer
            if ws_writer is None or writer_closing(ws_writer):
                if ws_writer is not None and self._out_writer is ws_writer and writer_closing(ws_writer):
                    self._out_writer = None
                continue
            guard.observe(transport_buffered(ws_writer))
            dead = guard.dead_reason()
            if dead:
                LOG.error("%s; aborting call %s", dead, guard.snapshot())
                self._signal_abort(dead)
                break
            # 338: keep a bounded app-side queue and drop the OLDEST frames
            # (latency cap DOWNLINK_MAX_AGE_S) instead of refusing new ones.
            self.dl_seq += 1
            event["seq"] = self.dl_seq
            now = time.monotonic()
            q = self._dl_q
            q.append((now, ws_encode_text(json.dumps(event))))
            while q and now - q[0][0] > DOWNLINK_MAX_AGE_S:
                q.popleft()
                self.dl_dropped_oldest += 1
                guard.dropped_frames += 1
                if self.dl_dropped_oldest == 1 or self.dl_dropped_oldest % 250 == 0:
                    LOG.warning("downlink backlog: dropped oldest frames %s", self.downlink_snapshot())
            broken = False
            while q:
                frame = q[0][1]
                if not guard.admit(len(frame)):
                    guard.dropped_frames -= 1
                    guard.drop_run = 0
                    break
                ts, _ = q.popleft()
                self.dl_max_age_s = max(self.dl_max_age_s, now - ts)
                try:
                    ws_writer.write(frame)
                    self.dl_sent += 1
                except (ConnectionError, BrokenPipeError, RuntimeError):
                    if self._out_writer is ws_writer:
                        self._out_writer = None
                    broken = True
                    break
            if broken:
                continue
            if guard.stall_started_at is None and guard.max_stall_s >= 1.0 and not self._stall_logged_recovery:
                self._stall_logged_recovery = True
                LOG.info("downlink recovered after stall %s", guard.snapshot())
            elif guard.stall_started_at is not None:
                self._stall_logged_recovery = False
        LOG.info("downlink call counters %s", self.downlink_snapshot())
        if self._record.returncode is None:
            await self._record.wait()

    def offer_uplink(self, audio: bytes, fmt: str = "", rate: int = 0) -> None:
        """Queue one caller frame without waiting. Drops the oldest if pw-cat is behind."""
        if self._closed or not audio:
            return
        q = self._uplink_q
        if q is None:
            self.uplink_dropped += 1
            self._maybe_log_uplink_drop("no queue")
            return
        dropped = offer_nowait(q, (bytes(audio), str(fmt or ""), int(rate or 0)))
        if dropped:
            self.uplink_dropped += dropped
            self._maybe_log_uplink_drop("queue")

    def _playback_backlog(self) -> int:
        proc = self._playback
        stdin = getattr(proc, "stdin", None) if proc is not None else None
        if stdin is None:
            return 0
        return transport_buffered(stdin)

    def _discard_queued_uplink(self) -> int:
        q = self._uplink_q
        if q is None:
            return 0
        n = 0
        while True:
            try:
                q.get_nowait()
            except asyncio.QueueEmpty:
                return n
            n += 1

    def _maybe_log_uplink_drop(self, why: str) -> None:
        n = self.uplink_dropped
        if n != 1 and n % 50 != 0:
            return
        queued = self._uplink_q.qsize() if self._uplink_q is not None else 0
        LOG.warning(
            "uplink audio dropped (%s) total=%s backlog=%s queued=%s",
            why,
            n,
            self._playback_backlog(),
            queued,
        )

    async def _pump_uplink(self) -> None:
        """Write queued caller audio to pw-cat. Never runs on the WebSocket read task."""
        q = self._uplink_q
        if q is None:
            return
        while not self._closed:
            item = await q.get()
            if self._closed or item is None:
                return
            audio, fmt, rate = item
            if self._playback_backlog() > UPLINK_BACKLOG_BYTES:
                extra = self._discard_queued_uplink()
                self.uplink_dropped += 1 + extra
                self._maybe_log_uplink_drop("pw-cat backlog")
                continue
            try:
                await asyncio.wait_for(
                    self.write_in(audio, fmt, rate),
                    timeout=UPLINK_WRITE_TIMEOUT_S,
                )
                self.uplink_written += 1
            except asyncio.TimeoutError:
                extra = self._discard_queued_uplink()
                self.uplink_dropped += 1 + extra
                self._maybe_log_uplink_drop("pw-cat write timeout")
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                LOG.warning("uplink playback failed: %s", exc)

    async def write_in(self, audio: bytes, fmt: str = "", rate: int = 0) -> None:
        if self._closed or not audio:
            return
        wire_fmt = _normalize_format(fmt) or self.client_in_format
        src_rate = _as_rate(rate, self.client_in_rate)
        pcm = _to_pcm16(audio, wire_fmt)
        if not pcm:
            return
        if self.tap:
            if wire_fmt == "audio/pcmu":
                self.tap.write_ulaw("gsm-uplink-8k-mono", audio, src_rate)
            else:
                self.tap.write_s16("gsm-uplink-8k-mono", pcm, src_rate, 1)
        if src_rate != self._in_src_rate:
            self._in_ratecv = None
            self._in_src_rate = src_rate
        bus_pcm, self._in_ratecv = _resample_pcm(pcm, src_rate, self.rate, self._in_ratecv)
        if not bus_pcm:
            return
        if self.uplink_gain != 1.0:
            bus_pcm = audioop.mul(bus_pcm, 2, self.uplink_gain)
        proc = self._playback
        if not proc or not proc.stdin:
            return
        if proc.returncode is not None:
            LOG.error("gsm playback write skipped: pw-cat already exited rc=%s", proc.returncode)
            self._signal_abort(f"hub pw-cat already exited rc={proc.returncode}")
            return
        try:
            proc.stdin.write(bus_pcm)
            await proc.stdin.drain()
        except (BrokenPipeError, ConnectionResetError, OSError) as exc:
            LOG.error("gsm playback write failed: %s (pw-cat rc=%s)", exc, proc.returncode)
            self._signal_abort(f"hub pw-cat write failed: {exc}")

    def note_phone_downlink(self, pcm: bytes, rate: int) -> None:
        """Record phone-bound audio. Disk or WAV errors stay off the audio path."""
        tap = self.tap
        if tap is None or not pcm:
            return
        try:
            tap.write_phone_downlink(pcm, rate)
        except Exception:
            if not getattr(self, "_downlink_record_warned", False):
                self._downlink_record_warned = True
                LOG.warning("phone downlink record failed", exc_info=True)

    def to_ulaw_8k(self, audio: bytes, fmt: str = "", rate: int = 0) -> bytes:
        """8 kHz μ-law for the legacy relay Talk path."""
        wire_fmt = _normalize_format(fmt) or self.client_in_format
        src_rate = _as_rate(rate, self.client_in_rate)
        pcm = _to_pcm16(audio, wire_fmt)
        if not pcm:
            return b""
        pcm8k, _ = _resample_pcm(pcm, src_rate, 8000, None)
        return audioop.lin2ulaw(pcm8k, 2)

    async def stop(self) -> None:
        if self._closed:
            return
        self._closed = True
        uplink_task = self._uplink_task
        self._uplink_task = None
        if uplink_task is not None:
            uplink_task.cancel()
            try:
                await uplink_task
            except asyncio.CancelledError:
                pass
        self.playback_linked = False
        self.record_linked = False
        if self._record_task:
            self._record_task.cancel()
            try:
                await self._record_task
            except asyncio.CancelledError:
                pass
        for proc in (self._playback, self._record):
            if not proc:
                continue
            if proc.stdin:
                proc.stdin.close()
            if proc.returncode is None:
                proc.terminate()
                try:
                    await asyncio.wait_for(proc.wait(), timeout=2)
                except asyncio.TimeoutError:
                    proc.kill()
                    await proc.wait()
        helper_tasks = self._helper_tasks
        self._helper_tasks = []
        for task in helper_tasks:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass


async def _watch_live_call(
    slot: CallSlot,
    bridge: Optional[PipewireBridge],
) -> None:
    """Abort a half-open or idle GSM WebSocket so finally can release the lock.

    A failed ping during an enabled relink grace asks the reader to hold the
    call instead of aborting it. The grace backstop in CallSlot.check still
    frees a slot whose handler never returns.
    """
    ping_every = slot.config.ping_s
    last_ping = time.monotonic()
    while slot.busy:
        # Never return without aborting: a closed PipeWire bridge + half-dead GSM
        # WebSocket previously left CallSlot.busy stuck for hours (wait_or_abort
        # never woke, finally never released).
        if bridge is not None and bridge._closed and not slot.abort.is_set():
            slot.abort_call("pipewire bridge closed while call slot still held")
        if not slot.abort.is_set():
            await asyncio.sleep(1.0)
        now = time.monotonic()
        writer = bridge.current_downlink_writer() if bridge is not None else None
        if (
            not slot.abort.is_set()
            and writer is not None
            and ping_every > 0
            and slot.established_at is not None
            and slot.link_gap_since is None
            and now - last_ping >= ping_every
        ):
            try:
                await ws_send_ping(writer)
            except (ConnectionError, BrokenPipeError, OSError) as exc:
                if slot.config.relink_grace_s > 0:
                    slot.ask_link_gap(f"websocket ping send failed: {exc}")
                else:
                    slot.abort_call(f"websocket ping send failed: {exc}")
            else:
                slot.ping_sent_at = time.monotonic()
                last_ping = now
        elif writer is None:
            last_ping = now
        reason = slot.abort_reason or slot.check(now)
        if not reason and not slot.abort.is_set():
            continue
        if reason:
            slot.abort_call(reason)
        else:
            reason = slot.abort_reason or "call aborted"
        LOG.error(
            "call watchdog abort: %s cleared_lock=after_cleanup %s utc=%s",
            reason,
            slot.snapshot(now),
            _utc_now(),
        )
        if reason != SUPERSEDED_REASON:
            post_system_alert(
                f"call watchdog abort: {reason}",
                e2e=bool(getattr(slot, "is_e2e", False) or CallSlot.path_is_e2e(getattr(slot, "path", "") or "")),
            )
        if writer is not None:
            try:
                await ws_send_close(writer, 1011, reason)
            except (ConnectionError, BrokenPipeError, OSError) as exc:
                LOG.warning("call watchdog could not send ws close: %s", exc)
            try:
                writer.close()
            except Exception as exc:
                LOG.warning("call watchdog could not close ws writer: %s", exc)
        # finally may never run (dead GSM peer); do not await here — this task
        # is cancelled from the WS finally. Schedule an independent ensure.
        asyncio.create_task(
            _ensure_call_slot_freed(slot, reason, wait_s=5.0),
            name="call-slot-ensure-release",
        )
        return


async def _clear_stale_active_bridge(
    bridge_ref: Optional["PipewireBridge"] = None,
    *,
    stop_timeout_s: float = 5.0,
) -> bool:
    """Drop a stale active_bridge so new dials are not 409'd.

    Clears the global *before* awaiting stop(), so a hung PipeWire/Talk stop
    cannot leave the reject gate stuck after CallSlot is already free.
    Returns True if a bridge reference was cleared.
    """
    global active_bridge
    ref = bridge_ref if bridge_ref is not None else active_bridge
    if ref is None:
        return False
    if active_bridge is ref:
        active_bridge = None
    try:
        await asyncio.wait_for(ref.stop(), timeout=stop_timeout_s)
    except Exception:
        LOG.warning("stale active_bridge stop failed", exc_info=True)
    return True


async def _ensure_call_slot_freed(
    slot: CallSlot,
    reason: str,
    *,
    wait_s: float = 5.0,
) -> None:
    """After abort, force-release the same claim if the WS finally path never ran.

    Always clears a stale active_bridge, even when busy is already false —
    that is the ghost-reject path (reaper freed the slot; bridge ref lingered).
    """
    claimed_at = slot.claimed_at
    bridge_ref = active_bridge
    try:
        if wait_s > 0:
            await asyncio.sleep(wait_s)
        if slot.busy and slot.claimed_at is not None and slot.claimed_at == claimed_at:
            LOG.error(
                "force-release CallSlot after abort wait: %s %s",
                reason,
                slot.snapshot(),
            )
            slot.release()
            post_system_alert(
                f"call slot force-release after abort: {reason}",
                e2e=bool(getattr(slot, "is_e2e", False)),
            )
        cleared = await _clear_stale_active_bridge(bridge_ref)
        if cleared:
            LOG.error(
                "cleared stale active_bridge after abort (%s) %s",
                reason,
                slot.snapshot(),
            )
    except Exception:
        LOG.exception("ensure call slot freed failed")


async def _watch_webrtc_ui_talk(openclaw: Any, bridge: "PipewireBridge") -> None:
    """End the call if Control UI Talk dies while the gsm slot is still held."""
    grace_s = 60.0
    dead_threshold_s = 45.0
    cdp_dead_threshold_s = 90.0
    started = time.monotonic()
    dead_since: Optional[float] = None
    cdp_dead_since: Optional[float] = None
    while not bridge._closed and not bridge.aborted():
        await asyncio.sleep(15.0)
        if time.monotonic() - started < grace_s:
            continue
        if getattr(openclaw, "talk_rollover_in_progress", False):
            dead_since = None
            cdp_dead_since = None
            continue
        if not getattr(openclaw, "talk_active", False):
            dead_since = None
            cdp_dead_since = None
            continue
        healthy = await openclaw.talk_session_healthy()
        if healthy is None:
            if cdp_dead_since is None:
                cdp_dead_since = time.monotonic()
            elif time.monotonic() - cdp_dead_since >= cdp_dead_threshold_s:
                LOG.error(
                    "webrtc-ui CDP unavailable for %.0fs during call; ending call",
                    cdp_dead_threshold_s,
                )
                bridge._signal_abort(
                    f"webrtc-ui CDP unavailable for {cdp_dead_threshold_s:.0f}s"
                )
                return
            continue
        cdp_dead_since = None
        if healthy:
            dead_since = None
            openclaw.webrtc_connected = True
            continue
        if dead_since is None:
            dead_since = time.monotonic()
        elif time.monotonic() - dead_since >= dead_threshold_s:
            LOG.error(
                "webrtc-ui talk unhealthy for %.0fs; ending call",
                dead_threshold_s,
            )
            bridge._signal_abort(f"webrtc-ui talk unhealthy for {dead_threshold_s:.0f}s")
            return


async def _talk_rollover_watch(openclaw: Any, bridge: "PipewireBridge") -> None:
    """Restart Control UI Talk before OpenAI/WebRTC session limits (~30 min)."""
    while not openclaw._closed and not bridge._closed and not bridge.aborted():
        if not getattr(openclaw, "talk_active", False):
            if getattr(openclaw, "talk_rollover_in_progress", False):
                await asyncio.sleep(0.25)
                continue
            await asyncio.sleep(1.0)
            continue
        started = getattr(openclaw, "talk_started_at", None)
        if started is None:
            await asyncio.sleep(1.0)
            continue
        remaining = TALK_ROLLOVER_S - (time.monotonic() - started)
        if remaining > 0:
            await asyncio.sleep(min(remaining, 30.0))
            continue
        if openclaw._closed or bridge._closed or bridge.aborted():
            return
        if not getattr(openclaw, "talk_active", False) and not getattr(
            openclaw, "talk_rollover_in_progress", False
        ):
            continue
        try:
            await openclaw.restart_talk()
        except Exception as exc:
            LOG.error("talk rollover failed: %s; ending call", exc)
            bridge._signal_abort(f"talk rollover failed: {exc}")
            return


async def _watch_talk_frames_without_energy(
    openclaw: Any,
    bridge: PipewireBridge,
) -> None:
    logged = False
    logged_silence = False
    while not bridge._closed:
        await asyncio.sleep(0.5)
        if bridge.downlink_energy_ticks > 0:
            return
        if (
            openclaw.audio_frames >= 50
            and openclaw.loud_audio_frames == 0
            and not logged_silence
        ):
            logged_silence = True
            LOG.error(
                "talk PCM is digital silence frames=%s loud=0 — "
                "gateway sent zeros (not a PipeWire miss)",
                openclaw.audio_frames,
            )
        started = openclaw.first_loud_audio_frame_at
        if started is None or openclaw.loud_audio_frames < TTS_WATCHDOG_LOUD_FRAMES:
            continue
        elapsed = time.monotonic() - started
        if elapsed >= TTS_ENERGY_WATCHDOG_S and not logged:
            logged = True
            LOG.error(
                "talk loud frames=%s (total=%s) received but monitor energy ~0 for %.1fs — "
                "gateway PCM is not reaching the phone",
                openclaw.loud_audio_frames,
                openclaw.audio_frames,
                elapsed,
            )



def _tap_stream_peak(tap_summary: dict[str, Any], name: str) -> float:
    streams = tap_summary.get("streams") or {}
    stream = streams.get(name) or {}
    try:
        return float(stream.get("peak") or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _tap_duration_s(tap_summary: dict[str, Any]) -> float:
    streams = tap_summary.get("streams") or {}
    best = 0.0
    for stream in streams.values():
        if not isinstance(stream, dict):
            continue
        try:
            best = max(best, float(stream.get("seconds") or 0.0))
        except (TypeError, ValueError):
            continue
    return best


_e2e_after_silent_lock = asyncio.Lock()
_e2e_after_silent_scheduled = False


def _maybe_schedule_e2e_after_silent_call(tap_summary: dict[str, Any]) -> None:
    """If a real call >10s had silent OpenClaw reply audio, queue an e2e self-test."""
    global _e2e_after_silent_scheduled
    if os.environ.get("GSM2COMPUTER_E2E_AFTER_SILENT", "1").lower() in ("0", "false", "no", "off"):
        return
    meta = tap_summary.get("meta") or {}
    if meta.get("e2e"):
        return
    duration = _tap_duration_s(tap_summary)
    if duration < float(os.environ.get("GSM2COMPUTER_E2E_SILENT_MIN_S", "10")):
        return
    thresh = float(os.environ.get("GSM2COMPUTER_E2E_PEAK_MIN", "0.02"))
    if meta.get("mode") == "grokbot":
        # gsm-downlink also carries the patchbay hold tone; judge Suzie only.
        spk = _tap_stream_peak(tap_summary, "grok-spk-48k-stereo")
        downlink = _tap_stream_peak(tap_summary, "gsm-downlink-8k-mono")
        if spk >= thresh:
            return
        _request_live_e2e(tap_summary.get("id") or "unknown")
    else:
        spk = _tap_stream_peak(tap_summary, "openclaw-spk-48k-stereo")
        downlink = _tap_stream_peak(tap_summary, "gsm-downlink-8k-mono")
        if spk >= thresh or downlink >= thresh:
            return
    LOG.warning(
        "silent bot reply after %.1fs call (mode=%s spk=%.4f dl=%.4f); scheduling e2e",
        duration,
        meta.get("mode"),
        spk,
        downlink,
    )
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    loop.create_task(_run_e2e_after_silent_call(tap_summary.get("id") or "unknown"))


E2E_LIVE_REQUEST = Path(
    os.environ.get(
        "GSM2COMPUTER_E2E_LIVE_REQUEST",
        str(Path.home() / "gsm2computer-e2e" / "live-request"),
    )
)


def _request_live_e2e(call_id: str) -> None:
    """Ask the next e2e run for a live Grok voice test (timer runs stay non-invasive)."""
    try:
        E2E_LIVE_REQUEST.parent.mkdir(parents=True, exist_ok=True)
        E2E_LIVE_REQUEST.write_text(json.dumps({"call_id": call_id, "ts": _utc_now()}), encoding="utf-8")
    except OSError as exc:
        LOG.warning("could not write live e2e request: %s", exc)


def _remember_transcript(call_id: str, pointer: dict[str, Any]) -> None:
    global last_call_tap
    if isinstance(last_call_tap, dict) and last_call_tap.get("id") == call_id:
        updated = dict(last_call_tap)
        updated["transcript"] = pointer
        last_call_tap = updated


async def _finish_grok_transcript(
    voice: Any,
    call_dir: Path,
    call_id: str,
    off_res: dict[str, Any],
    started_at: Optional[float] = None,
) -> None:
    """After patchbay off: read the voice transcript. Failures stay off the call.

    A new call sets ``voice.transcript_abort``. This read checks that flag and
    does not click the Grok page once it is set. ``call_ended`` is not delayed;
    ops get a ``call_transcript`` event when the file is written.
    """
    pointer: Optional[dict[str, Any]] = None
    abort = getattr(voice, "transcript_abort", None)
    try:
        if abort is not None and abort.is_set():
            pointer = pointer_or_aborted(call_dir, call_id)
        elif not off_res.get("ok"):
            pointer = save_transcript_result(
                call_dir,
                call_id,
                "off_failed",
                error=off_res.get("error") or off_res.get("meaning") or "patchbay off failed",
            )
        else:
            status = await run_patchbay("status", "--json", timeout_s=8, binary=voice.binary)
            if abort is not None and abort.is_set():
                pointer = pointer_or_aborted(call_dir, call_id)
            else:
                pointer = await capture_call_transcript(
                    call_dir,
                    call_id,
                    cdp_http=cdp_base_from_status(status),
                    status=status,
                    abort=abort,
                    started_at=started_at,
                    ended_at=time.time(),
                )
    except asyncio.CancelledError:
        if pointer is None:
            try:
                pointer = pointer_or_aborted(call_dir, call_id)
            except Exception:
                LOG.warning("grok transcript abort write failed", exc_info=True)
        raise
    except Exception as exc:
        LOG.warning("grok transcript capture failed: %s", exc)
        try:
            pointer = save_transcript_result(call_dir, call_id, "error", error=str(exc))
        except Exception:
            LOG.warning("grok transcript status write failed", exc_info=True)
            pointer = None
    if pointer is None:
        return
    try:
        attach_levels(call_dir, pointer)
    except Exception:
        LOG.warning("grok transcript levels update failed", exc_info=True)
    _remember_transcript(call_id, pointer)
    try:
        announce_transcript(pointer)
    except Exception:
        LOG.warning("grok transcript announce failed", exc_info=True)
    setattr(voice, "_transcript_announced", True)


async def _grokbot_call_start(
    voice: Any,
    tap: Optional[CallTap],
    is_e2e: bool,
) -> dict[str, Any]:
    """Switch Grok Bot live voice on for this call; tap Suzie only once she is live."""
    res = await voice.start()
    if res.get("ok") and tap is not None:
        # Started after the switch so the patchbay hold tone is not counted as Suzie.
        await tap.start_source("grok-spk-48k-stereo", GROKBOT_SPK_SINK, 48000, 2)
    elif not res.get("ok"):
        LOG.error(
            "grokbot call path without live voice (e2e=%s): rc=%s %s",
            is_e2e,
            res.get("rc"),
            res.get("meaning"),
        )
    return res


async def _run_e2e_after_silent_call(call_id: str) -> None:
    global _e2e_after_silent_scheduled
    async with _e2e_after_silent_lock:
        if _e2e_after_silent_scheduled:
            return
        _e2e_after_silent_scheduled = True
    try:
        await asyncio.sleep(2.0)
        if live_call.busy or (active_bridge is not None):
            LOG.info("skip e2e after silent call %s: line busy again", call_id)
            return
        cmd = os.environ.get(
            "GSM2COMPUTER_E2E_CMD",
            "systemctl --user start gsm2computer-openclaw-e2e.service",
        )
        LOG.info("starting e2e after silent call %s: %s", call_id, cmd)
        proc = await asyncio.create_subprocess_shell(
            cmd,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            _, err = await asyncio.wait_for(proc.communicate(), timeout=5.0)
        except asyncio.TimeoutError:
            LOG.warning("e2e start command timed out")
            return
        if proc.returncode:
            LOG.warning(
                "e2e start failed rc=%s %s",
                proc.returncode,
                (err or b"").decode("utf-8", "replace")[:300],
            )
    finally:
        async with _e2e_after_silent_lock:
            _e2e_after_silent_scheduled = False


def _record_link_event(kind: str, **fields: Any) -> None:
    """Persist a link event. Failures stay off the audio path."""
    event = {
        "kind": kind,
        "ts": _utc_now(),
        "entrance": live_call.entrance or None,
        **fields,
    }
    try:
        append_link_gap(event)
    except Exception:
        LOG.warning("link gap log write failed", exc_info=True)
    try:
        portal_store.add_link_event(
            kind=kind,
            session_id=fields.get("session_id"),
            close_code=fields.get("close_code"),
            close_reason=str(fields.get("close_reason") or "") or None,
            initiator=fields.get("initiator"),
            detail=str(fields.get("detail") or "") or None,
            ts=event["ts"],
            entrance=event.get("entrance") if isinstance(event.get("entrance"), str) else None,
        )
    except Exception:
        LOG.warning("link gap sqlite write failed", exc_info=True)
    LOG.info("link_event %s", json.dumps(event, default=str, sort_keys=True))


def _log_ws_end(slot: CallSlot, frame: Optional[WsRead], initiator: str, detail: str = "") -> None:
    code = frame.close_code if frame is not None else None
    reason = (frame.close_reason if frame is not None else "") or detail or (slot.abort_reason or "")
    who = initiator or (frame.initiator if frame is not None else "") or "unknown"
    slot.note_close(code, who, reason)
    clean = is_clean_hangup(code, who)
    LOG.info(
        "ws close session=%s code=%s reason=%s initiator=%s clean=%s abort=%s utc=%s",
        slot.session_id,
        code,
        reason,
        who,
        clean,
        slot.abort_reason,
        _utc_now(),
    )
    _record_link_event(
        "close" if clean else "drop",
        session_id=slot.session_id,
        close_code=code,
        close_reason=reason,
        initiator=who,
        detail=slot.abort_reason or detail or reason,
    )


class RelinkAdmit(enum.Enum):
    """How a websocket relates to the open grace window.

    NOT_APPLICABLE is a normal connect: there is no grace waiter for it.
    PARKED means this socket owns the live call.
    REJECTED means a relink was in progress and this socket did not get it.
    REJECTED must close. It must not fall through into admission, or a stale
    slot will be superseded and the winner's call aborted.
    """

    NOT_APPLICABLE = "not_applicable"
    PARKED = "parked"
    REJECTED = "rejected"


def _relink_in_progress() -> bool:
    """True while a grace window still owns the call, even after its waiter was taken.

    The header does not matter. A different session id during the gap is still
    not a new call, and it is not allowed to supersede the one in grace.
    """
    if not (live_call.busy or active_bridge is not None):
        return False
    return live_call.link_gap_since is not None


async def _park_relink_socket(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    session_id: str,
    key: str,
) -> bool:
    """101 and hand the socket to the live call.

    ``deliver`` reserves the single waiter with no await. Only that winner
    stamps the live entrance. False means the reservation was already taken
    or cancelled. The caller must reject that socket and return.
    """
    release_fut = relink_gate.deliver(session_id, reader, writer)
    if release_fut is None:
        return False
    entrance = request_entrance()
    live_call.note_entrance(entrance.name, _current_device_id.get())
    LOG.info(
        "relink socket parked session=%s entrance=%s device=%s utc=%s",
        session_id,
        entrance.name,
        _current_device_id.get() or "-",
        _utc_now(),
    )
    accept = ws_accept_key(key)
    writer.write(
        (
            "HTTP/1.1 101 Switching Protocols\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Accept: {accept}\r\n"
            "\r\n"
        ).encode("ascii")
    )
    try:
        await writer.drain()
    except (ConnectionError, OSError):
        LOG.warning("relink 101 drain failed session=%s", session_id, exc_info=True)
    except asyncio.CancelledError:
        # The holder already owns this socket. Cancelling the parked handler
        # must not look like a lost reservation.
        raise
    await release_fut
    return True


async def admit_relink(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    resume_id: str,
    key: str,
) -> RelinkAdmit:
    """Reserve the grace waiter, or say this socket is not the one that may."""
    park_id = relink_gate.resume_session(resume_id)
    if not park_id:
        if _relink_in_progress():
            return RelinkAdmit.REJECTED
        return RelinkAdmit.NOT_APPLICABLE
    try:
        parked = await _park_relink_socket(reader, writer, park_id, key)
    except asyncio.CancelledError:
        raise
    except Exception:
        LOG.warning("relink park failed session=%s", park_id, exc_info=True)
        return RelinkAdmit.REJECTED
    if parked:
        return RelinkAdmit.PARKED
    return RelinkAdmit.REJECTED


async def _hold_for_relink(
    slot: CallSlot,
    bridge: Optional["PipewireBridge"],
    session_id: str,
    writer: asyncio.StreamWriter,
    reason: str,
    frame: Optional[WsRead],
) -> Optional[tuple]:
    """Keep Talk and PipeWire up until the same session connects again."""
    global active_ws_writer
    grace = slot.config.relink_grace_s
    if grace <= 0:
        return None
    code = frame.close_code if frame is not None else None
    initiator = frame.initiator if frame is not None and frame.ended else "hub"
    close_reason = (frame.close_reason if frame is not None else "") or reason
    slot.note_close(code, initiator, close_reason)
    slot.note_link_gap()
    if bridge is not None:
        bridge.detach_downlink()
    if active_ws_writer is writer:
        active_ws_writer = None
    relink_gate.release_holder()
    try:
        writer.close()
    except Exception:
        LOG.debug("close during link gap failed", exc_info=True)
    _record_link_event(
        "gap_open",
        session_id=session_id,
        close_code=code,
        close_reason=close_reason,
        initiator=initiator,
        detail=reason,
    )
    LOG.warning(
        "link gap open session=%s grace=%.0fs initiator=%s code=%s reason=%s utc=%s",
        session_id,
        grace,
        initiator,
        code,
        close_reason,
        _utc_now(),
    )
    try:
        fut = relink_gate.begin(session_id)
        result = await wait_or_abort(asyncio.wait_for(fut, timeout=grace), slot.abort)
    except (asyncio.TimeoutError, TimeoutError):
        result = None
    if result is None:
        relink_gate.cancel_wait()
        if not slot.abort_reason:
            slot.abort_call(f"relink grace expired ({grace:.0f}s)")
        _record_link_event(
            "gap_expired",
            session_id=session_id,
            initiator="hub",
            detail=slot.abort_reason or "relink grace expired",
        )
        LOG.warning(
            "link gap expired session=%s abort=%s utc=%s",
            session_id,
            slot.abort_reason,
            _utc_now(),
        )
        return None
    new_reader, new_writer, _release = result
    if bridge is not None:
        bridge.attach_downlink(new_writer)
    slot.clear_link_gap()
    slot.note_ws_activity()
    active_ws_writer = new_writer
    _record_link_event("relink", session_id=session_id, initiator="peer", detail="same session")
    LOG.info("relinked session=%s utc=%s", session_id, _utc_now())
    return new_reader, new_writer


async def _watch_call_path(slot: CallSlot, peer_ip: str, claimed_at: float) -> None:
    """Tailscale direct/relay plus socket stats while this claim is live."""
    last_fail = 0.0
    while slot.busy and slot.claimed_at == claimed_at:
        try:
            status = await asyncio.to_thread(load_tailscale_status)
            info = parse_tailscale_status(status if isinstance(status, dict) else {}, peer_ip)
            slot.note_tailscale(
                str(info.get("mode") or "unknown"),
                str(info.get("relay") or ""),
                str(info.get("addr") or ""),
            )
            sock = await asyncio.to_thread(read_peer_socket, peer_ip)
            LOG.info(
                "call path session=%s mode=%s relay=%s addr=%s peer=%s rtt_ms=%s ss=%s utc=%s",
                slot.session_id,
                info.get("mode"),
                info.get("relay") or "-",
                info.get("addr") or "-",
                peer_ip or "-",
                slot.last_pong_rtt_ms,
                sock,
                _utc_now(),
            )
        except Exception as exc:
            now = time.monotonic()
            if now - last_fail > 30:
                LOG.info("call path probe failed: %s", exc)
                last_fail = now
        await asyncio.sleep(5.0)


async def handle_websocket(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    headers: dict,
    path: str,
) -> None:
    global active_bridge, active_openclaw, active_ws_writer, last_call_tap

    tap: Optional[CallTap] = None
    e2e = CallSlot.path_is_e2e(path)
    key = headers.get("sec-websocket-key")
    if not key:
        writer.write(b"HTTP/1.1 400 Bad Request\r\nConnection: close\r\n\r\n")
        await writer.drain()
        return
    # Phone ingest on the public socket is the call route only. /loopback and
    # /e2e-test change the switchboard; they stay on the Tailscale entrance.
    if request_entrance().name == ENTRANCE_PUBLIC and not public_route_allowed(
        "GET", path if path.startswith("/") else f"/{path}", {"upgrade": "websocket"}
    ):
        writer.write(json_response(404, {"ok": False, "error": "not found"}))
        await writer.drain()
        return

    resume_id = (headers.get("x-gsm-call-session") or "").strip()
    admitted = await admit_relink(reader, writer, resume_id, key)
    if admitted is RelinkAdmit.PARKED:
        return
    if admitted is RelinkAdmit.REJECTED:
        # Lost the reservation, or the grace window is still open and this
        # socket is not the one that took it. Do not supersede: that aborts
        # the call the winner just joined.
        LOG.warning(
            "rejecting relink session_hdr=%s gap=%s %s utc=%s",
            resume_id or "-",
            live_call.link_gap_since is not None,
            live_call.snapshot(),
            _utc_now(),
        )
        writer.write(b"HTTP/1.1 409 Conflict\r\nConnection: close\r\n\r\n")
        await writer.drain()
        return

    incoming_e2e = CallSlot.path_is_e2e(path)
    if (live_call.busy or active_bridge is not None) and not incoming_e2e:
        # Real Pixel call preempts a synthetic e2e self-test (never leave Safwat busy).
        if live_call.busy and live_call.is_e2e:
            LOG.warning(
                "preempting e2e-test for real call path=%s %s",
                path,
                live_call.snapshot(),
            )
            freed = await preempt_e2e_for_real_call(live_call, wait_s=5.0)
            # Wait for the e2e handler finally-path to drop active_bridge too.
            deadline = time.monotonic() + 5.0
            while active_bridge is not None and time.monotonic() < deadline:
                await asyncio.sleep(0.1)
            if not freed or live_call.busy or active_bridge is not None:
                LOG.warning(
                    "e2e preempt incomplete; rejecting real call %s active_bridge=%s",
                    live_call.snapshot(),
                    active_bridge is not None,
                )
                writer.write(b"HTTP/1.1 409 Conflict\r\nConnection: close\r\n\r\n")
                await writer.drain()
                return
        elif live_call.link_gap_since is not None:
            # Grace is still open. Supersede here would abort the live session.
            LOG.warning(
                "rejecting websocket during link gap %s utc=%s",
                live_call.snapshot(),
                _utc_now(),
            )
            writer.write(b"HTTP/1.1 409 Conflict\r\nConnection: close\r\n\r\n")
            await writer.drain()
            return
        elif live_call.busy and reconnect_should_supersede(live_call.snapshot()):
            # The Pixel only opens a new socket after it gave up on the old one
            # (e.g. Tailscale hiccup). Retire the stale session and let this
            # socket take the call instead of 409-ing the reconnect.
            LOG.warning(
                "Pixel reconnect supersedes stale call session %s", live_call.snapshot()
            )
            live_call.abort_call(SUPERSEDED_REASON)
            deadline = time.monotonic() + RECONNECT_TAKEOVER_WAIT_S
            while (live_call.busy or active_bridge is not None) and time.monotonic() < deadline:
                await asyncio.sleep(0.1)
            if live_call.busy or active_bridge is not None:
                LOG.warning(
                    "stale session did not release in %.0fs; rejecting reconnect %s",
                    RECONNECT_TAKEOVER_WAIT_S,
                    live_call.snapshot(),
                )
                writer.write(b"HTTP/1.1 409 Conflict\r\nConnection: close\r\n\r\n")
                await writer.drain()
                return
            LOG.info("stale session released; Pixel reconnect re-attaching to the call")
        elif live_call.busy or active_bridge is not None:
            LOG.warning(
                "rejecting websocket: call already in progress %s",
                live_call.snapshot(),
            )
            writer.write(b"HTTP/1.1 409 Conflict\r\nConnection: close\r\n\r\n")
            await writer.drain()
            return
    elif live_call.busy or active_bridge is not None:
        LOG.warning(
            "rejecting e2e websocket: call already in progress %s",
            live_call.snapshot(),
        )
        writer.write(b"HTTP/1.1 409 Conflict\r\nConnection: close\r\n\r\n")
        await writer.drain()
        return

    # Keep call admission out of the short idle URL repair window.
    # The flag lives on the Talk UI when that supervisor is current. A build
    # that imported talk_chromium without the flag must still accept the call.
    talk_ui = get_talk_ui() if get_talk_ui is not None else None
    if talk_ui is not None and getattr(talk_ui, "page_heal_in_progress", False):
        writer.write(b"HTTP/1.1 503 Service Unavailable\r\nRetry-After: 1\r\nConnection: close\r\n\r\n")
        await writer.drain()
        return
    session_id = "hub-" + uuid.uuid4().hex[:16]
    entrance = request_entrance()
    if not live_call.claim(path, entrance=entrance.name):
        LOG.warning("rejecting websocket: call slot raced %s", live_call.snapshot())
        writer.write(b"HTTP/1.1 409 Conflict\r\nConnection: close\r\n\r\n")
        await writer.drain()
        return
    live_call.session_id = session_id
    live_call.note_entrance(entrance.name, _current_device_id.get())

    playback_sink = GSM_SINK
    openclaw_bridge: Optional[Any] = None
    watchdog_task: Optional[asyncio.Task] = None
    rollover_task: Optional[asyncio.Task] = None
    call_watchdog_task: Optional[asyncio.Task] = None
    path_task: Optional[asyncio.Task] = None
    bridge: Optional[PipewireBridge] = None
    grok_voice: Optional[Any] = None
    grok_switch_task: Optional[asyncio.Task] = None
    try:
        accept = ws_accept_key(key)
        response = (
            "HTTP/1.1 101 Switching Protocols\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Accept: {accept}\r\n"
            "\r\n"
        )
        writer.write(response.encode("ascii"))
        await writer.drain()
        active_ws_writer = writer

        loopback = path.strip("/") == "loopback" or path.startswith("/loopback")
        e2e = CallSlot.path_is_e2e(path)
        LOG.info(
            "WebSocket connected entrance=%s route=%s loopback=%s e2e=%s device=%s",
            live_call.entrance or "-",
            path,
            loopback,
            e2e,
            live_call.device_id or "-",
        )
        tap = CallTap.maybe_open(
            loopback=loopback,
            mode="off" if loopback else ("grokbot" if GROKBOT_ON_CALL else OPENCLAW_TALK_MODE),
            e2e=e2e,
        )
        if loopback:
            result = await set_switchboard_mode("loopback")
            LOG.info("loopback call: %s", result.get("applied") or result.get("error"))
        elif AUTO_MODE_ON_CALL:
            result = await set_switchboard_mode(AUTO_MODE_ON_CALL)
            LOG.info("auto mode on call: %s", result.get("applied") or result.get("error"))

        use_openclaw = not loopback and not GROKBOT_ON_CALL
        use_grokbot = not loopback and GROKBOT_ON_CALL
        if loopback:
            downlink_monitor = GSM_MONITOR
        elif use_grokbot:
            # Caller -> patchbay_mic (grok_mic); grok_bus -> caller. No OpenClaw.
            playback_sink = GROKBOT_MIC_SINK
            downlink_monitor = GROKBOT_SPK_SINK
        elif OPENCLAW_TALK_MODE == "webrtc-ui":
            playback_sink = PHONE_UPLINK_SINK
            downlink_monitor = OPENCLAW_DOWNLINK_MONITOR
        elif OPENCLAW_TALK_ON_CALL:
            downlink_monitor = OPENCLAW_DOWNLINK_MONITOR
        else:
            downlink_monitor = GSM_MONITOR
        bridge = PipewireBridge(
            playback_sink,
            GSM_MONITOR,
            BUS_RATE,
            tap=tap,
            on_abort=live_call.abort_call,
        )
        live_call.downlink_probe = bridge.downlink_snapshot
        call_watchdog_task = asyncio.create_task(_watch_live_call(live_call, bridge))
        if use_openclaw and OPENCLAW_TALK_MODE == "webrtc-ui":
            if OpenClawTalkUI is None or get_talk_ui is None:
                await fail_call_handshake(writer, "OpenClaw Control UI Talk supervisor is not available")
                return
            try:
                openclaw_bridge = get_talk_ui()
                await openclaw_bridge.start_audio()
                active_openclaw = openclaw_bridge
                LOG.info("openclaw webrtc-ui chromium handshake ready")
            except Exception as exc:
                openclaw_bridge = None
                active_openclaw = None
                await fail_call_handshake(writer, f"openclaw webrtc-ui handshake failed: {exc}")
                return
        elif use_openclaw and OPENCLAW_TALK_MODE == "relay":
            if OpenClawTalkBridge is None:
                await fail_call_handshake(writer, "OpenClaw talk bridge is not available")
                return
            try:
                openclaw_bridge = OpenClawTalkBridge(mic_source="hub", tap=tap)
                await openclaw_bridge.start_audio()
                active_openclaw = openclaw_bridge
                LOG.info("openclaw playback+talk handshake ready")
            except Exception as exc:
                if openclaw_bridge is not None:
                    await openclaw_bridge.stop()
                    openclaw_bridge = None
                    active_openclaw = None
                await fail_call_handshake(writer, f"openclaw pipewire handshake failed: {exc}")
                return

        if openclaw_bridge is not None and OPENCLAW_TALK_MODE == "webrtc-ui":
            # Start Talk + re-link the mic before pw-cat/pw-record so any brief
            # graph settle is inaudible on the phone. Do not restart the
            # loopback unit after uplink is live (that was the start-of-call cut).
            try:
                await openclaw_bridge.start_talk()
                LOG.info("openclaw talk ready before phone uplink (mode=%s)", OPENCLAW_TALK_MODE)
            except Exception as exc:
                await fail_call_handshake(writer, f"openclaw talk session failed: {exc}")
                return

        try:
            await bridge.start(writer, monitor=downlink_monitor, record_downlink=not loopback)
        except (PipewireLinkError, RuntimeError) as exc:
            await fail_call_handshake(writer, f"hub pipewire handshake failed: {exc}")
            return

        if use_grokbot:
            bridge.uplink_gain = GROKBOT_UPLINK_GAIN
            if tap is not None:
                await tap.start_source("grok-mic-48k-stereo", GROKBOT_MIC_SOURCE, 48000, 2)

        if tap is not None and use_openclaw and OPENCLAW_TALK_MODE == "webrtc-ui":
            await tap.start_source(
                "openclaw-mic-48k-stereo",
                # Prefer sink-capture virtual mic (same node Talk Chromium uses).
                # phone_uplink.monitor is silent on current PipeWire.
                os.environ.get("GSM2COMPUTER_OPENCLAW_MIC_TAP", "output.openclaw_phone_mic"),
                48000,
                2,
            )
            await tap.start_source(
                "openclaw-spk-48k-stereo",
                OPENCLAW_DOWNLINK_MONITOR,
                48000,
                2,
            )

        if openclaw_bridge is not None and OPENCLAW_TALK_MODE != "webrtc-ui":
            try:
                await openclaw_bridge.start_talk()
                LOG.info("openclaw talk ready for session.updated (mode=%s)", OPENCLAW_TALK_MODE)
            except Exception as exc:
                await fail_call_handshake(writer, f"openclaw talk session failed: {exc}")
                return

        await ws_send_text(
            writer,
            json.dumps(
                session_description(session_id, loopback=loopback, e2e=e2e, grokbot=use_grokbot)
            ),
        )
        active_bridge = bridge
        live_call.mark_established()
        if use_grokbot:
            # Run the switch beside the WS loop: uplink keeps flowing into
            # grok_mic and the caller hears patchbay's hold tone meanwhile.
            grok_voice = get_grokbot_voice()
            grok_switch_task = asyncio.create_task(_grokbot_call_start(grok_voice, tap, e2e))

        if openclaw_bridge is not None and OPENCLAW_TALK_MODE == "relay":
            watchdog_task = asyncio.create_task(
                _watch_talk_frames_without_energy(openclaw_bridge, bridge)
            )
        elif openclaw_bridge is not None and OPENCLAW_TALK_MODE == "webrtc-ui":
            watchdog_task = asyncio.create_task(_watch_webrtc_ui_talk(openclaw_bridge, bridge))
            rollover_task = asyncio.create_task(_talk_rollover_watch(openclaw_bridge, bridge))

        peername = writer.get_extra_info("peername")
        peer_ip = peername[0] if isinstance(peername, tuple) and peername else ""
        path_task = asyncio.create_task(
            _watch_call_path(live_call, peer_ip, live_call.claimed_at or 0.0)
        )

        while True:
            if bridge.aborted() or (live_call.abort.is_set() and not live_call.gap_wake.is_set()):
                LOG.error(
                    "call aborted (%s); closing websocket %s utc=%s",
                    live_call.abort_reason or "pipewire abort",
                    live_call.snapshot(),
                    _utc_now(),
                )
                _log_ws_end(live_call, None, "hub", live_call.abort_reason or "aborted")
                break
            outcome, frame = await wait_for_ws(
                ws_read_frame(reader, writer), live_call.abort, live_call.gap_wake
            )
            if outcome == "abort":
                LOG.error(
                    "call watchdog ended websocket: %s %s utc=%s",
                    live_call.abort_reason,
                    live_call.snapshot(),
                    _utc_now(),
                )
                _log_ws_end(live_call, None, "hub", live_call.abort_reason or "aborted")
                break
            ended = frame is not None and frame.ended
            if outcome == "gap" or ended:
                if ended and frame is not None and is_clean_hangup(frame.close_code, frame.initiator):
                    _log_ws_end(live_call, frame, "peer")
                    break
                if ended and live_call.abort.is_set() and live_call.abort_reason:
                    _log_ws_end(live_call, frame, "hub", live_call.abort_reason)
                    break
                if live_call.is_e2e or live_call.config.relink_grace_s <= 0:
                    who = frame.initiator if frame is not None else "eof"
                    _log_ws_end(live_call, frame, who)
                    break
                gap_reason = live_call.take_gap_request()
                if not gap_reason:
                    gap_reason = (frame.close_reason if frame is not None else "") or "socket ended"
                held = await _hold_for_relink(
                    live_call, bridge, session_id, writer, gap_reason, frame if ended else None
                )
                if held is None:
                    break
                reader, writer = held
                try:
                    await ws_send_text(
                        writer,
                        json.dumps(session_description(session_id, loopback=loopback, e2e=e2e, grokbot=use_grokbot)),
                    )
                except (ConnectionError, BrokenPipeError, OSError) as exc:
                    LOG.warning("relink session.updated failed: %s", exc)
                    held_again = await _hold_for_relink(
                        live_call, bridge, session_id, writer, f"relink write failed: {exc}", None
                    )
                    if held_again is None:
                        break
                    reader, writer = held_again
                continue
            if frame is None:
                _log_ws_end(live_call, None, "eof", "no frame")
                break
            if frame.pong:
                sent = live_call.ping_sent_at
                if sent is not None:
                    rtt_ms = (time.monotonic() - sent) * 1000.0
                    live_call.note_pong_rtt(rtt_ms)
                    live_call.ping_sent_at = None
                    LOG.info(
                        "ws pong rtt_ms=%.0f session=%s utc=%s",
                        rtt_ms,
                        live_call.session_id,
                        _utc_now(),
                    )
                live_call.note_ws_activity()
                continue
            msg = frame.text
            live_call.note_ws_activity()
            if frame.binary:
                parsed = parse_uplink_binary(frame.binary)
                if parsed is None and bridge.client_in_format == "audio/pcm" and len(frame.binary) >= 2:
                    raw = frame.binary[: len(frame.binary) & ~1]
                    parsed = (raw, "audio/pcm", bridge.client_in_rate) if raw else None
                if parsed is not None:
                    audio, fmt, rate = parsed
                    live_call.note_uplink()
                    bridge.offer_uplink(audio, fmt, rate)
                    if not loopback and OPENCLAW_TALK_MODE == "relay" and openclaw_bridge is not None:
                        openclaw_bridge.feed_gsm_ulaw(bridge.to_ulaw_8k(audio, fmt, rate))
                continue
            if not msg:
                continue
            try:
                event = json.loads(msg)
            except json.JSONDecodeError:
                LOG.debug("non-json ws frame: %r", msg[:200])
                continue

            etype = event.get("type")
            if etype == "client.audio":
                inp = event.get("input") or {}
                out = event.get("output") or {}
                bridge.set_client_audio(
                    inp.get("format") or "",
                    inp.get("rate") or 0,
                    out.get("format") or "",
                    out.get("rate") or 0,
                )
            elif etype == "input_audio_buffer.append":
                audio_b64 = event.get("audio") or event.get("data") or ""
                if audio_b64:
                    live_call.note_uplink()
                    audio = base64.b64decode(audio_b64, validate=False)
                    fmt = event.get("format") or ""
                    rate = event.get("rate") or 0
                    # Do not await pw-cat here. A full playback pipe blocks
                    # drain() forever, the socket stops being read, and the
                    # phone's ping then dies the connection (~40s) without a
                    # close frame. Relink never runs while this handler is stuck.
                    bridge.offer_uplink(audio, fmt, rate)
                    if loopback:
                        echo_fmt = _normalize_format(fmt) or bridge.client_in_format
                        bridge.note_phone_downlink(
                            _to_pcm16(audio, echo_fmt),
                            _as_rate(rate, bridge.client_in_rate),
                        )
                        energy = (
                            _pcm_rms_energy(_to_pcm16(audio, _normalize_format(fmt) or bridge.client_in_format))
                            if (_normalize_format(fmt) or bridge.client_in_format) == "audio/pcm"
                            else _mulaw_rms_energy(audio)
                        )
                        echo = {
                            "type": "response.output_audio.delta",
                            "delta": base64.b64encode(audio).decode("ascii"),
                            "format": _normalize_format(fmt) or bridge.client_in_format,
                            "rate": _as_rate(rate, bridge.client_in_rate),
                            "channels": {"l": energy, "r": energy},
                        }
                        await ws_send_text(writer, json.dumps(echo))
                    elif OPENCLAW_TALK_MODE == "relay" and openclaw_bridge is not None:
                        openclaw_bridge.feed_gsm_ulaw(bridge.to_ulaw_8k(audio, fmt, rate))
            elif etype == "client.ping":
                try:
                    ws_queue_text(
                        writer,
                        json.dumps({"type": "client.pong", "t": event.get("t")}),
                    )
                except (ConnectionError, BrokenPipeError, OSError) as exc:
                    LOG.warning("client.pong queue failed: %s", exc)
            else:
                LOG.debug("ws event type=%s", etype)
    finally:
        abort_reason = live_call.abort_reason
        relink_gate.close()
        # Drop reject-gate refs first so a hung Talk/PipeWire stop cannot 409
        # the next dial (ghost reject after reaper freed CallSlot).
        if active_ws_writer is writer:
            active_ws_writer = None
        if active_bridge is bridge:
            active_bridge = None
        if path_task:
            path_task.cancel()
            try:
                await path_task
            except asyncio.CancelledError:
                pass
        if call_watchdog_task:
            call_watchdog_task.cancel()
            try:
                await call_watchdog_task
            except asyncio.CancelledError:
                pass
        if watchdog_task:
            watchdog_task.cancel()
            try:
                await watchdog_task
            except asyncio.CancelledError:
                pass
        if rollover_task:
            rollover_task.cancel()
            try:
                await rollover_task
            except asyncio.CancelledError:
                pass
        if bridge is not None:
            try:
                await asyncio.wait_for(bridge.stop(), timeout=5.0)
            except Exception:
                LOG.warning("bridge stop in finally failed", exc_info=True)
        if tap is not None:
            try:
                await asyncio.wait_for(tap.stop_capture(), timeout=5.0)
            except Exception:
                LOG.warning("tap stop in finally failed", exc_info=True)
        if openclaw_bridge is not None:
            try:
                await asyncio.wait_for(openclaw_bridge.stop(), timeout=8.0)
            except Exception:
                LOG.warning("openclaw stop in finally failed", exc_info=True)
            active_openclaw = None
            LOG.info("openclaw talk stopped (mode=%s)", OPENCLAW_TALK_MODE)
        remember_finished_call(live_call.session_id, live_call.entrance)
        if grok_voice is not None:
            # Call is over (clean hangup, abort, or relink grace expired).
            # patchbay off runs after any still-running switch, off the hot path.
            # The transcript read starts only after off. A new call aborts it
            # before patchbay switch, so the click cannot land on the next session.
            on_transcript = None
            if tap is not None:
                transcript_dir = tap.dir
                transcript_id = tap.call_id
                transcript_started = tap.started_wall

                def on_abort(
                    directory: Path = transcript_dir,
                    call_id: str = transcript_id,
                    voice: Any = grok_voice,
                ) -> None:
                    if getattr(voice, "_transcript_announced", False):
                        return
                    existing = load_saved_pointer(directory)
                    pointer = existing if existing is not None else pointer_or_aborted(directory, call_id)
                    try:
                        attach_levels(directory, pointer)
                    except Exception:
                        LOG.warning("grok transcript levels update failed", exc_info=True)
                    _remember_transcript(call_id, pointer)
                    announce_transcript(pointer)
                    voice._transcript_announced = True

                grok_voice._transcript_announced = False
                grok_voice.on_transcript_abort = on_abort

                async def on_transcript(res: dict[str, Any], voice: Any = grok_voice) -> None:
                    await _finish_grok_transcript(
                        voice,
                        transcript_dir,
                        transcript_id,
                        res,
                        started_at=transcript_started,
                    )

            grok_voice.stop_later(after=grok_switch_task, on_stopped=on_transcript)
        live_call.release()
        if abort_reason:
            LOG.info("WebSocket disconnected after watchdog: %s", abort_reason)
        else:
            LOG.info("WebSocket disconnected")
        if tap is not None:
            try:
                last_call_tap = await asyncio.wait_for(tap.close(), timeout=5.0)
            except Exception:
                LOG.warning("tap close in finally failed", exc_info=True)
            else:
                if last_call_tap is not None and not e2e:
                    _maybe_schedule_e2e_after_silent_call(last_call_tap)


def _enable_tcp_keepalive(sock: socket.socket) -> None:
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
    idle = getattr(socket, "TCP_KEEPIDLE", None)
    interval = getattr(socket, "TCP_KEEPINTVL", None)
    count = getattr(socket, "TCP_KEEPCNT", None)
    if idle is not None:
        sock.setsockopt(socket.IPPROTO_TCP, idle, 30)
    if interval is not None:
        sock.setsockopt(socket.IPPROTO_TCP, interval, 10)
    if count is not None:
        sock.setsockopt(socket.IPPROTO_TCP, count, 3)


async def handle_client(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    sock = writer.get_extra_info("socket")
    if sock is not None:
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        try:
            _enable_tcp_keepalive(sock)
        except OSError as exc:
            LOG.debug("tcp keepalive not set: %s", exc)
    peer = writer.get_extra_info("peername")
    try:
        method, path, headers = await read_http_headers(reader)
    except HttpRequestError as exc:
        writer.write(json_response(exc.status, {"ok": False, "error": str(exc)}))
        await writer.drain()
        writer.close()
        await writer.wait_closed()
        return
    except (ConnectionError, ValueError, asyncio.IncompleteReadError) as exc:
        LOG.debug("bad request from %s: %s", peer, exc)
        writer.close()
        await writer.wait_closed()
        return

    LOG.debug("%s %s from %s", method, path, peer)
    split = urlsplit(path)
    path_only = split.path or "/"
    query = parse_qs(split.query)

    # Auth before any body read. A public client must not be able to make the
    # process buffer an unbounded or slow body just by opening a socket.
    entrance = request_entrance()
    decision = authorize(headers, entrance, TOKENS)
    if not decision.ok:
        LOG.info(
            "device token %s entrance=%s peer=%s %s %s",
            decision.reason,
            entrance.name,
            peer,
            method,
            path_only,
        )
        writer.write(json_response(decision.status, {"ok": False, "error": decision.error}))
        await writer.drain()
        writer.close()
        await writer.wait_closed()
        return
    _current_device_id.set(decision.device_id or "")
    if entrance.name == ENTRANCE_PUBLIC and not public_route_allowed(method, path_only, headers):
        writer.write(json_response(404, {"ok": False, "error": "not found"}))
        await writer.drain()
        writer.close()
        await writer.wait_closed()
        return

    try:
        length = content_length_header(headers)
    except HttpRequestError as exc:
        writer.write(json_response(exc.status, {"ok": False, "error": str(exc)}))
        await writer.drain()
        writer.close()
        await writer.wait_closed()
        return
    body_limit = MAX_PUBLIC_HTTP_BODY_BYTES if entrance.name == ENTRANCE_PUBLIC else MAX_HTTP_BODY_BYTES
    if length > body_limit:
        writer.write(json_response(413, {"ok": False, "error": "body too large"}))
        await writer.drain()
        writer.close()
        await writer.wait_closed()
        return
    try:
        body = await read_http_body(reader, length)
    except HttpRequestError as exc:
        writer.write(json_response(exc.status, {"ok": False, "error": str(exc)}))
        await writer.drain()
        writer.close()
        await writer.wait_closed()
        return
    except (asyncio.IncompleteReadError, ConnectionError):
        writer.close()
        await writer.wait_closed()
        return

    if headers.get("upgrade", "").lower() == "websocket" or "sec-websocket-key" in headers:
        await handle_websocket(reader, writer, headers, path_only)
        writer.close()
        await writer.wait_closed()
        return

    try:
        portal_result = await portal_app.dispatch(method, path, headers, body, writer)
        if portal_result == "stream":
            writer.close()
            await writer.wait_closed()
            return
        if portal_result == "handled":
            await writer.drain()
            writer.close()
            await writer.wait_closed()
            return

        if method == "GET" and path_only == "/health":
            body: dict[str, Any] = {
                "ok": True,
                "mode": current_mode,
                "openclaw_talk": OPENCLAW_TALK_MODE,
                "entrances": [item.describe() for item in _active_entrances],
                "auto_mode": AUTO_MODE_ON_CALL or None,
                "call": live_call.snapshot(),
            }
            if GROKBOT_ON_CALL:
                body["grokbot"] = get_grokbot_voice().snapshot()
            if last_call_tap:
                body["last_call_tap"] = last_call_tap
            if OPENCLAW_TALK_MODE == "webrtc-ui" and get_talk_ui is not None:
                try:
                    ui = get_talk_ui()
                    ui.idle_check = lambda: not live_call.busy and active_bridge is None
                    body["talk"] = await ui.health()
                    body["call"] = live_call.snapshot()
                except Exception as exc:
                    body["talk"] = {"error": str(exc)}
            writer.write(json_response(200, body))
        elif method == "POST" and path_only == "/token":
            expires = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat().replace("+00:00", "Z")
            writer.write(
                json_response(
                    200,
                    {"value": "hub-token", "model": "hub", "expires_at": expires},
                )
            )
        elif method == "GET" and path_only == "/switchboard/state":
            writer.write(json_response(200, await switchboard_state()))
        elif method == "POST" and path_only == "/switchboard/mode":
            try:
                payload = json.loads(body.decode("utf-8") if body else "{}")
            except json.JSONDecodeError:
                writer.write(json_response(400, {"ok": False, "error": "invalid json"}))
                await writer.drain()
                writer.close()
                await writer.wait_closed()
                return
            mode = str(payload.get("mode", "")).strip().lower()
            if not mode:
                writer.write(json_response(400, {"ok": False, "error": "mode required"}))
            else:
                writer.write(json_response(200, await set_switchboard_mode(mode)))
        elif method == "POST" and path_only == "/sms":
            try:
                payload = json.loads(body.decode("utf-8") if body else "{}")
            except json.JSONDecodeError:
                payload = {"raw": body.decode("utf-8", errors="replace")}
            log_entry, command = ingest_inbound_sms(portal_store, portal_events, payload)
            LOG.info("sms %s", json.dumps(log_entry, ensure_ascii=False))
            # A repeated clientId is the same SMS retried on another entrance.
            # Do not store it again, wake cup, or run the command a second time.
            if log_entry.get("duplicate"):
                writer.write(json_response(200, {"ok": True, "sms": log_entry, "duplicate": True}))
            else:
                # The webhook repeats the SMS text the phone reported. It does not
                # parse MODE or STATUS and it does not change the switchboard.
                # Those commands run only on the Tailscale entrance, below.
                try:
                    from portal_sms import should_wake_cup, format_ops_sms_alert
                    if should_wake_cup(
                        str(log_entry.get("from") or ""),
                        str(log_entry.get("body") or ""),
                    ):
                        post_system_alert(
                            format_ops_sms_alert(
                                str(log_entry.get("from") or ""),
                                str(log_entry.get("body") or ""),
                                str(log_entry.get("receivedAt") or ""),
                            )
                        )
                except Exception:
                    LOG.warning("ops SMS alert webhook failed", exc_info=True)
                if command and entrance.name != ENTRANCE_PUBLIC:
                    result = await set_switchboard_mode(command)
                    writer.write(json_response(200, {"ok": True, "sms": log_entry, "routing": result}))
                elif command:
                    LOG.info(
                        "sms switchboard command ignored on public entrance: %s",
                        command,
                    )
                    writer.write(
                        json_response(200, {"ok": True, "sms": log_entry, "commandIgnored": True})
                    )
                else:
                    writer.write(json_response(200, {"ok": True, "sms": log_entry}))
        elif method == "POST" and path_only == "/admin/call/release":
            # Emergency CallSlot / stale-bridge release.
            # Refuse while a live call has fresh ws/uplink unless force=1 —
            # a triage probe cut a healthy call tonight.
            # Always clear stale active_bridge even when busy=false (ghost reject).
            snap = live_call.snapshot()
            force = (query.get("force") or ["0"])[0].lower() in ("1", "true", "yes")
            allowed, deny_reason = admin_release_allowed(snap, force=force)
            if not allowed:
                LOG.warning(
                    "admin call/release refused (live call): %s %s",
                    deny_reason,
                    snap,
                )
                writer.write(
                    json_response(
                        409,
                        {
                            "ok": False,
                            "error": deny_reason,
                            "call": snap,
                            "hint": "POST /admin/call/release?force=1 to override",
                        },
                    )
                )
            else:
                bridge_ref = active_bridge
                forced = False
                if live_call.busy:
                    forced = await ensure_released_after_abort(
                        live_call, "admin force-release", wait_s=5.0
                    )
                cleared = await _clear_stale_active_bridge(bridge_ref)
                post_system_alert(
                    f"admin force-release CallSlot (was busy={snap.get('busy')} "
                    f"age_s={snap.get('age_s')} forced={forced} bridge_cleared={cleared} "
                    f"force={force} path={snap.get('path')})",
                    e2e=bool(snap.get("e2e")),
                )
                LOG.error(
                    "admin force-release CallSlot forced=%s bridge_cleared=%s %s -> %s",
                    forced,
                    cleared,
                    snap,
                    live_call.snapshot(),
                )
                writer.write(
                    json_response(
                        200,
                        {
                            "ok": True,
                            "call": live_call.snapshot(),
                            "before": snap,
                            "forced": forced,
                            "bridge_cleared": cleared,
                            "force": force,
                        },
                    )
                )
        else:
            writer.write(json_response(404, {"ok": False, "error": "not found"}))
    except Exception:
        LOG.exception("handler error for %s %s", method, path)
        writer.write(json_response(500, {"ok": False, "error": "internal error"}))

    await writer.drain()
    writer.close()
    await writer.wait_closed()



async def _reap_stuck_call_slot() -> None:
    """Last-resort: force-release CallSlot if max duration exceeded and handler wedged."""
    global active_openclaw
    while True:
        await asyncio.sleep(30.0)
        try:
            if not live_call.busy:
                continue
            now = time.monotonic()
            reason = live_call.check(now)
            if not reason:
                continue
            snap = live_call.snapshot(now)
            LOG.error("call slot reaper force-release: %s %s", reason, snap)
            bridge_ref = active_bridge
            openclaw_ref = active_openclaw
            forced = await ensure_released_after_abort(live_call, reason, wait_s=5.0)
            # Reaper previously freed CallSlot but left active_bridge set → ghost
            # "call already in progress" with /health busy=false. Always clear.
            cleared = await _clear_stale_active_bridge(bridge_ref)
            talk_stopped = False
            if openclaw_ref is not None and active_openclaw is openclaw_ref:
                active_openclaw = None
                try:
                    await asyncio.wait_for(openclaw_ref.stop(), timeout=8.0)
                    talk_stopped = True
                except Exception:
                    LOG.warning("reaper openclaw stop failed", exc_info=True)
            if forced or cleared or talk_stopped:
                post_system_alert(
                    f"call slot reaper force-release: {reason} "
                    f"(forced={forced} bridge_cleared={cleared} talk_stopped={talk_stopped} "
                    f"path={snap.get('path')})",
                    e2e=bool(snap.get("e2e")),
                )
                LOG.error(
                    "call slot reaper released lock after abort wait "
                    "forced=%s bridge_cleared=%s talk_stopped=%s %s",
                    forced,
                    cleared,
                    talk_stopped,
                    live_call.snapshot(),
                )
        except Exception:
            LOG.exception("call slot reaper error")


async def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s.%(msecs)03dZ %(levelname)s %(name)s: %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )
    _use_utc_logging()
    start_alert_worker()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, lambda: asyncio.create_task(shutdown()))

    TOKENS.reload_if_needed()
    entrances = entrances_from_env()
    servers, bound = await start_entrances(entrances)
    _active_entrances.extend(bound)
    if not servers:
        raise SystemExit("no listen socket could be bound")
    asyncio.create_task(_reap_stuck_call_slot())
    described = ", ".join(
        f"{item.name}={item.host}:{item.listen_port} token_required={item.token_required}"
        for item in _active_entrances
    )
    LOG.info(
        "listening on %s (sink=%s auto_mode=%s openclaw_talk=%s grokbot=%s "
        "call_watchdog=ws_idle=%s uplink_idle=%s max=%s relink_grace=%s)",
        described,
        GSM_SINK,
        AUTO_MODE_ON_CALL or "off",
        OPENCLAW_TALK_MODE,
        GROKBOT_ON_CALL,
        CALL_WATCHDOG.ws_idle_s,
        CALL_WATCHDOG.uplink_idle_s,
        CALL_WATCHDOG.max_s,
        CALL_WATCHDOG.relink_grace_s,
    )
    try:
        await asyncio.gather(*(server.serve_forever() for server in servers))
    finally:
        for server in servers:
            server.close()
        await asyncio.gather(*(server.wait_closed() for server in servers))


_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost"})


def entrances_from_env() -> list[Entrance]:
    """Tailscale socket always. Public socket only when it fails closed.

    A public socket without a required device token, or bound to something
    other than loopback, is not opened. The Tailscale listener still starts.
    """
    tailscale = Entrance(
        "tailscale",
        os.environ.get("GSM2COMPUTER_HUB_HOST", HOST),
        int(os.environ.get("GSM2COMPUTER_HUB_PORT", str(PORT))),
        _env_flag("GSM2COMPUTER_TAILSCALE_TOKEN_REQUIRED", False),
    )
    configured = [tailscale]
    if not _env_flag("GSM2COMPUTER_PUBLIC_ENABLE", False):
        return configured
    public_host = os.environ.get("GSM2COMPUTER_PUBLIC_HOST", "127.0.0.1").strip()
    public = Entrance(
        ENTRANCE_PUBLIC,
        public_host,
        int(os.environ.get("GSM2COMPUTER_PUBLIC_PORT", "8790")),
        _env_flag("GSM2COMPUTER_PUBLIC_TOKEN_REQUIRED", True),
    )
    if not public.token_required:
        LOG.error(
            "refusing public entrance: GSM2COMPUTER_PUBLIC_TOKEN_REQUIRED cannot be disabled"
        )
        return configured
    if public_host not in _LOOPBACK_HOSTS:
        LOG.error(
            "refusing public entrance: bind %s is not loopback; Caddy owns the public address",
            public_host,
        )
        return configured
    configured.append(public)
    return configured


async def start_entrances(
    entrances: list[Entrance],
) -> tuple[list[asyncio.AbstractServer], list[Entrance]]:
    """Bind each entrance. A public bind failure leaves the Tailscale socket up."""
    servers: list[asyncio.AbstractServer] = []
    bound: list[Entrance] = []
    for entrance in entrances:
        try:
            servers.append(await listen_entrance(entrance))
        except OSError as exc:
            if entrance.name != ENTRANCE_PUBLIC:
                for server in servers:
                    server.close()
                raise
            LOG.error(
                "public entrance bind failed %s:%s (%s); tailscale listener stays up",
                entrance.host,
                entrance.port,
                exc,
            )
            continue
        bound.append(entrance)
    return servers, bound


async def listen_entrance(entrance: Entrance) -> asyncio.AbstractServer:
    """Bind one entrance. The accept callback only tags the socket, then calls handle_client."""

    async def client(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        token = _current_entrance.set(entrance)
        try:
            await handle_client(reader, writer)
        finally:
            _current_entrance.reset(token)

    server = await asyncio.start_server(client, entrance.host, entrance.port)
    socks = server.sockets or []
    if socks:
        bound = socks[0].getsockname()
        if isinstance(bound, tuple) and len(bound) >= 2:
            entrance.bound_port = int(bound[1])
    return server


_server: Optional[asyncio.AbstractServer] = None


async def shutdown() -> None:
    global active_bridge, active_openclaw, active_ws_writer
    LOG.info("shutting down")
    live_call.abort_call("hub shutdown")
    writer = active_ws_writer
    active_ws_writer = None
    if writer is not None:
        try:
            await asyncio.wait_for(ws_send_close(writer, 1001, "hub shutdown"), timeout=1.0)
        except Exception:
            pass
        try:
            writer.close()
        except Exception:
            pass
    bridge_ref = active_bridge
    active_bridge = None
    openclaw = active_openclaw
    active_openclaw = None
    if openclaw is not None:
        try:
            await asyncio.wait_for(openclaw.stop(), timeout=5.0)
        except Exception:
            LOG.warning("shutdown openclaw stop failed", exc_info=True)
    if bridge_ref is not None:
        try:
            await asyncio.wait_for(bridge_ref.stop(), timeout=5.0)
        except Exception:
            LOG.warning("shutdown bridge stop failed", exc_info=True)
    if GROKBOT_ON_CALL:
        voice = get_grokbot_voice()
        if voice.live or voice.switching:
            try:
                await asyncio.wait_for(voice.stop(), timeout=10.0)
            except Exception:
                LOG.warning("shutdown grokbot off failed", exc_info=True)
    live_call.release()
    raise SystemExit(0)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        pass
