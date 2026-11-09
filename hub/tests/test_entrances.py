"""Device token, dual entrance, and call-path stamping."""
from __future__ import annotations

import asyncio
import importlib.util
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

_TMP = Path(tempfile.mkdtemp(prefix="gsm-entrances-"))
os.environ["GSM2COMPUTER_PORTAL_DB"] = str(_TMP / "portal.sqlite")
os.environ["GSM2COMPUTER_DEVICE_TOKENS"] = str(_TMP / "device-tokens.json")
os.environ["GSM2COMPUTER_LINK_GAP_LOG"] = str(_TMP / "gaps.jsonl")
os.environ["GSM2COMPUTER_OPENCLAW_TALK"] = "off"
os.environ["GSM2COMPUTER_PUBLIC_ENABLE"] = "0"
os.environ.setdefault("GSM2COMPUTER_HUB_HOST", "127.0.0.1")

HUB_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HUB_DIR))

from call_slot import CallSlot, CallWatchdogConfig  # noqa: E402
from device_auth import (  # noqa: E402
    EPHEMERAL_TOKEN,
    DeviceTokenFile,
    Entrance,
    authorize,
    generate_token,
    public_route_allowed,
    write_device_token,
)
from portal_store import PortalStore  # noqa: E402

_HUB_SPEC = importlib.util.spec_from_file_location("gsm2computer_hub", HUB_DIR / "hub.py")
assert _HUB_SPEC and _HUB_SPEC.loader
hub = importlib.util.module_from_spec(_HUB_SPEC)
_HUB_SPEC.loader.exec_module(hub)

TOKEN = generate_token()
DEVICE = "moto-g84"


def _write_tokens() -> None:
    path = Path(os.environ["GSM2COMPUTER_DEVICE_TOKENS"])
    if path.exists():
        path.unlink()
    write_device_token(path, DEVICE, TOKEN)
    hub.TOKENS.reload_if_needed()


async def _raw(port: int, payload: bytes, read_timeout: float = 2.0) -> bytes:
    asyncio = __import__("asyncio")
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    writer.write(payload)
    await writer.drain()
    try:
        return await asyncio.wait_for(reader.read(512), read_timeout)
    finally:
        writer.close()
        await writer.wait_closed()


async def _http(
    port: int,
    method: str,
    path: str,
    headers: dict[str, str] | None = None,
    body: bytes = b"",
) -> tuple[int, dict, bytes]:
    reader, writer = await __import__("asyncio").open_connection("127.0.0.1", port)
    header_lines = [
        f"{method} {path} HTTP/1.1",
        "Host: 127.0.0.1",
        f"Content-Length: {len(body)}",
        "Connection: close",
    ]
    for key, value in (headers or {}).items():
        header_lines.append(f"{key}: {value}")
    writer.write(("\r\n".join(header_lines) + "\r\n\r\n").encode("ascii") + body)
    await writer.drain()
    raw = await reader.read()
    writer.close()
    await writer.wait_closed()
    head, _, payload = raw.partition(b"\r\n\r\n")
    lines = head.decode("iso-8859-1").split("\r\n")
    status = int(lines[0].split()[1])
    parsed: dict[str, str] = {}
    for line in lines[1:]:
        if ":" in line:
            name, value = line.split(":", 1)
            parsed[name.strip().lower()] = value.strip()
    return status, parsed, payload


class TokenUnitTests(unittest.TestCase):
    def test_short_and_ephemeral_tokens_are_rejected(self) -> None:
        with self.assertRaises(ValueError):
            write_device_token(_TMP / "bad.json", "moto-g84", "hub-token")
        with self.assertRaises(ValueError):
            write_device_token(_TMP / "bad.json", "moto-g84", EPHEMERAL_TOKEN)

    def test_same_check_required_vs_optional(self) -> None:
        path = _TMP / "unit-tokens.json"
        if path.exists():
            path.unlink()
        write_device_token(path, DEVICE, TOKEN)
        tokens = DeviceTokenFile(path)
        public = Entrance("public", "127.0.0.1", 8790, True)
        tailscale = Entrance("tailscale", "127.0.0.1", 8787, False)
        locked = Entrance("tailscale", "127.0.0.1", 8787, True)
        good = {"x-gsm-device-token": TOKEN}
        bad = {"x-gsm-device-token": TOKEN[:-4] + "xxxx"}
        legacy = {"authorization": f"Bearer {EPHEMERAL_TOKEN}"}

        self.assertTrue(authorize({}, tailscale, tokens).ok)
        self.assertFalse(authorize({}, public, tokens).ok)
        self.assertEqual(authorize({}, public, tokens).error, "device token required")
        self.assertFalse(authorize({}, locked, tokens).ok)
        self.assertEqual(authorize(bad, tailscale, tokens).error, "device token rejected")
        self.assertEqual(authorize(bad, public, tokens).error, "device token rejected")
        self.assertEqual(authorize(good, public, tokens).device_id, DEVICE)
        self.assertEqual(authorize(good, tailscale, tokens).device_id, DEVICE)
        self.assertTrue(authorize(legacy, tailscale, tokens).ok)
        self.assertIsNone(authorize(legacy, tailscale, tokens).device_id)
        self.assertFalse(authorize(legacy, public, tokens).ok)
        self.assertEqual(authorize({"authorization": f"Bearer {TOKEN}"}, public, tokens).device_id, DEVICE)
        empty = {"x-gsm-device-token": "  ", "authorization": f"Bearer {TOKEN}"}
        self.assertEqual(authorize(empty, public, tokens).device_id, DEVICE)
        both = {"x-gsm-device-token": "x" * 40, "authorization": f"Bearer {TOKEN}"}
        self.assertEqual(authorize(both, public, tokens).error, "device token rejected")

    def test_world_readable_token_file_is_not_loaded(self) -> None:
        path = _TMP / "open-tokens.json"
        if path.exists():
            path.unlink()
        write_device_token(path, DEVICE, TOKEN)
        os.chmod(path, 0o644)
        tokens = DeviceTokenFile(path)
        self.assertIsNone(tokens.device_id_for(TOKEN))
        os.chmod(path, 0o600)
        self.assertEqual(tokens.device_id_for(TOKEN), DEVICE)

    def test_public_allowlist_blocks_portal_and_admin(self) -> None:
        ws = {"upgrade": "websocket", "sec-websocket-key": "x"}
        self.assertTrue(public_route_allowed("GET", "/health", {}))
        self.assertTrue(public_route_allowed("GET", "/sms/outbox", {}))
        self.assertTrue(public_route_allowed("POST", "/sms/outbox/abc/ack", {}))
        self.assertTrue(public_route_allowed("GET", "/", ws))
        self.assertFalse(public_route_allowed("GET", "/loopback", ws))
        self.assertFalse(public_route_allowed("GET", "/e2e-test", ws))
        self.assertFalse(public_route_allowed("GET", "/portal/", ws))
        self.assertFalse(public_route_allowed("GET", "/portal/", {}))
        self.assertFalse(public_route_allowed("POST", "/admin/call/release", {}))
        self.assertFalse(public_route_allowed("POST", "/switchboard/mode", {}))

    def test_call_path_requires_an_observed_session(self) -> None:
        hub._finished_call_entrances.clear()
        hub._finished_call_order.clear()
        if hub.live_call.busy:
            hub.live_call.release()
        hub.remember_finished_call("hub-real", "public")
        self.assertEqual(hub.resolve_call_path("hub-real", "tailscale"), "public")
        self.assertIsNone(hub.resolve_call_path("hub-other", "public"))
        self.assertIsNone(hub.resolve_call_path(None, "public"))
        self.assertIsNone(hub.resolve_call_path("", "tailscale"))
        hub.live_call.claim("/", entrance="tailscale")
        hub.live_call.session_id = "hub-live"
        hub.live_call.note_entrance("tailscale", "")
        try:
            self.assertEqual(hub.resolve_call_path("hub-live", "public"), "tailscale")
            self.assertIsNone(hub.resolve_call_path(None, "tailscale"))
            self.assertIsNone(hub.resolve_call_path("hub-other", "public"))
            hub.live_call.note_entrance("public", DEVICE)
            first = hub.session_description("hub-live", loopback=False, e2e=False)
            hub.live_call.note_entrance("tailscale", DEVICE)
            second = hub.session_description("hub-live", loopback=False, e2e=False)
            self.assertEqual(first["session"]["entrance"], "public")
            self.assertEqual(second["session"]["entrance"], "tailscale")
        finally:
            hub.live_call.release()

    def test_snapshot_path_is_the_entrance(self) -> None:
        slot = CallSlot(CallWatchdogConfig(enabled=False))
        self.assertTrue(slot.claim("/loopback", entrance="public"))
        slot.note_entrance("public", DEVICE)
        snap = slot.snapshot()
        self.assertEqual(snap["path"], "public")
        self.assertEqual(snap["entrance"], "public")
        self.assertEqual(snap["route"], "/loopback")
        self.assertEqual(snap["device_id"], DEVICE)
        self.assertTrue(slot.path_is_e2e("/e2e-test"))


class StorePathTests(unittest.TestCase):
    def test_legacy_calls_table_gains_path(self) -> None:
        db = _TMP / "legacy.sqlite"
        conn = sqlite3.connect(db)
        conn.execute(
            """CREATE TABLE calls (
                id TEXT PRIMARY KEY,
                direction TEXT NOT NULL,
                number TEXT NOT NULL,
                started_at TEXT NOT NULL,
                duration_sec INTEGER NOT NULL,
                switchboard_mode TEXT,
                session_id TEXT,
                tap_summary TEXT
            )"""
        )
        conn.commit()
        conn.close()
        store = PortalStore(db)
        try:
            item = store.add_call("in", "+15550001111", "2026-11-08T00:00:00Z", 3, path="public")
            self.assertEqual(item["path"], "public")
            self.assertEqual(store.list_calls()[0]["path"], "public")
            ignored = store.add_call("in", "+15550002222", "2026-11-08T00:01:00Z", 1, path="loopback")
            self.assertIsNone(ignored["path"])
        finally:
            store.close()


class EntranceHttpTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        _write_tokens()
        hub._active_entrances.clear()
        self.tailscale = Entrance("tailscale", "127.0.0.1", 0, False)
        self.public = Entrance("public", "127.0.0.1", 0, True)
        self.servers = [
            await hub.listen_entrance(self.tailscale),
            await hub.listen_entrance(self.public),
        ]
        hub._active_entrances.extend([self.tailscale, self.public])

    async def asyncTearDown(self) -> None:
        for server in self.servers:
            server.close()
            await server.wait_closed()
        hub._active_entrances.clear()
        hub._finished_call_entrances.clear()
        hub._finished_call_order.clear()

    async def test_token_matrix_and_shared_outbox(self) -> None:
        pub = self.public.listen_port
        ts = self.tailscale.listen_port
        good = {"X-Gsm-Device-Token": TOKEN}
        bad = {"X-Gsm-Device-Token": "x" * 32}

        status, _, body = await _http(pub, "GET", "/health")
        self.assertEqual(status, 401)
        self.assertEqual(json.loads(body)["error"], "device token required")

        status, _, body = await _http(pub, "GET", "/health", bad)
        self.assertEqual(status, 401)
        self.assertEqual(json.loads(body)["error"], "device token rejected")

        status, _, body = await _http(pub, "GET", "/health", {"Authorization": f"Bearer {EPHEMERAL_TOKEN}"})
        self.assertEqual(status, 401)

        status, _, body = await _http(pub, "GET", "/health", good)
        self.assertEqual(status, 200)
        health = json.loads(body)
        names = {item["name"] for item in health["entrances"]}
        self.assertEqual(names, {"public", "tailscale"})
        public_row = next(item for item in health["entrances"] if item["name"] == "public")
        self.assertEqual(public_row["client_scheme"], "https")
        self.assertTrue(public_row["token_required"])
        self.assertFalse(next(item["token_required"] for item in health["entrances"] if item["name"] == "tailscale"))
        self.assertIsNone(health["call"]["path"])

        status, _, _ = await _http(ts, "GET", "/health")
        self.assertEqual(status, 200)
        status, _, _ = await _http(ts, "GET", "/health", {"Authorization": f"Bearer {EPHEMERAL_TOKEN}"})
        self.assertEqual(status, 200)
        status, _, body = await _http(ts, "GET", "/health", bad)
        self.assertEqual(status, 401)
        self.assertEqual(json.loads(body)["error"], "device token rejected")
        status, _, body = await _http(ts, "GET", "/health", good)
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["call"]["path"], None)

        status, _, _ = await _http(pub, "GET", "/portal/", good)
        self.assertEqual(status, 404)
        status, _, _ = await _http(pub, "POST", "/admin/call/release", good)
        self.assertEqual(status, 404)
        status, _, _ = await _http(ts, "GET", "/")
        self.assertEqual(status, 302)

        send = json.dumps({"to": "+15550001111", "body": "ping"}).encode()
        status, _, body = await _http(ts, "POST", "/portal/api/messages/send", body=send)
        self.assertEqual(status, 201)
        outbox_id = json.loads(body)["id"]

        status, _, _ = await _http(pub, "GET", "/sms/outbox")
        self.assertEqual(status, 401)
        status, _, body = await _http(pub, "GET", "/sms/outbox", good)
        self.assertEqual(status, 200)
        items = json.loads(body)["items"]
        self.assertEqual(items[0]["id"], outbox_id)
        ack = json.dumps({"status": "sent"}).encode()
        status, _, _ = await _http(pub, "POST", f"/sms/outbox/{outbox_id}/ack", good, ack)
        self.assertEqual(status, 200)

        ws = (
            "GET /loopback HTTP/1.1\r\n"
            "Host: 127.0.0.1\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            "Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==\r\n"
            "Sec-WebSocket-Version: 13\r\n"
            "\r\n"
        )
        reader, writer = await __import__("asyncio").open_connection("127.0.0.1", pub)
        writer.write(ws.encode("ascii"))
        await writer.drain()
        raw = await reader.read(256)
        writer.close()
        await writer.wait_closed()
        self.assertTrue(raw.startswith(b"HTTP/1.1 401"))

        status, _, body = await _http(
            pub,
            "GET",
            "/loopback",
            {**good, "Upgrade": "websocket", "Sec-WebSocket-Key": "dGhlIHNhbXBsZSBub25jZQ=="},
        )
        self.assertEqual(status, 404)
        mode_sms = json.dumps({"from": "+15551212", "body": "MODE loopback"}).encode()
        before = hub.current_mode
        status, _, body = await _http(pub, "POST", "/sms", good, mode_sms)
        self.assertEqual(status, 200)
        self.assertTrue(json.loads(body)["commandIgnored"])
        self.assertEqual(hub.current_mode, before)
        status, _, _ = await _http(
            pub,
            "POST",
            "/switchboard/mode",
            good,
            json.dumps({"mode": "loopback"}).encode(),
        )
        self.assertEqual(status, 404)
        self.assertEqual(hub.current_mode, before)

    async def test_public_ingress_does_not_buffer_unbounded_bodies(self) -> None:
        pub = self.public.listen_port
        huge = (
            "POST /sms HTTP/1.1\r\n"
            "Host: 127.0.0.1\r\n"
            "Content-Length: 8000000\r\n"
            "Connection: close\r\n"
            "\r\n"
        ).encode()
        raw = await _raw(pub, huge)
        self.assertTrue(raw.startswith(b"HTTP/1.1 401"), raw[:80])
        authed = (
            "POST /sms HTTP/1.1\r\n"
            "Host: 127.0.0.1\r\n"
            f"X-Gsm-Device-Token: {TOKEN}\r\n"
            "Content-Length: 8000000\r\n"
            "Connection: close\r\n"
            "\r\n"
        ).encode()
        raw = await _raw(pub, authed)
        self.assertTrue(raw.startswith(b"HTTP/1.1 413"), raw[:80])
        os.environ["GSM2COMPUTER_HTTP_BODY_TIMEOUT_S"] = "0.3"
        try:
            stalled = (
                "POST /sms HTTP/1.1\r\n"
                "Host: 127.0.0.1\r\n"
                f"X-Gsm-Device-Token: {TOKEN}\r\n"
                "Content-Length: 4\r\n"
                "Connection: close\r\n"
                "\r\n"
            ).encode()
            raw = await _raw(pub, stalled, read_timeout=2.0)
            self.assertTrue(raw.startswith(b"HTTP/1.1 408"), raw[:80])
        finally:
            os.environ.pop("GSM2COMPUTER_HTTP_BODY_TIMEOUT_S", None)
        os.environ["GSM2COMPUTER_HTTP_HEADER_TIMEOUT_S"] = "0.3"
        try:
            raw = await _raw(pub, b"GET /health HTTP/1.1\r\nHost: 127.0.0.1\r\n", read_timeout=2.0)
            self.assertTrue(raw.startswith(b"HTTP/1.1 408"), raw[:80])
        finally:
            os.environ.pop("GSM2COMPUTER_HTTP_HEADER_TIMEOUT_S", None)
        padded = b"GET /health HTTP/1.1\r\nHost: 127.0.0.1\r\nX-Pad: " + (b"a" * 20000) + b"\r\n\r\n"
        raw = await _raw(pub, padded)
        self.assertTrue(raw.startswith(b"HTTP/1.1 431"), raw[:80])
        # One line past StreamReader's 64 KiB limit. readline() raises before
        # the byte counter runs, and that must still be a 431, not a bare close.
        huge_line = b"GET /health HTTP/1.1\r\nX-Pad: " + (b"b" * 70000) + b"\r\n\r\n"
        raw = await _raw(pub, huge_line)
        self.assertTrue(raw.startswith(b"HTTP/1.1 431"), raw[:120])

    async def test_call_report_records_observed_entrance(self) -> None:
        hub.remember_finished_call("hub-abc", "public")
        body = json.dumps(
            {
                "direction": "in",
                "number": "+15550003333",
                "started_at": "2026-11-08T18:00:00Z",
                "duration_sec": 9,
                "session_id": "hub-abc",
                "path": "tailscale",
            }
        ).encode()
        status, _, payload = await _http(self.tailscale.listen_port, "POST", "/calls", body=body)
        self.assertEqual(status, 201)
        call = json.loads(payload)["call"]
        self.assertEqual(call["path"], "public")
        omitted = json.dumps(
            {
                "direction": "in",
                "number": "+15550003334",
                "started_at": "2026-11-08T18:01:00Z",
                "duration_sec": 1,
                "path": "public",
            }
        ).encode()
        status, _, payload = await _http(self.tailscale.listen_port, "POST", "/calls", body=omitted)
        self.assertEqual(status, 201)
        self.assertIsNone(json.loads(payload)["call"]["path"])
        unknown = json.dumps(
            {
                "direction": "in",
                "number": "+15550003335",
                "started_at": "2026-11-08T18:02:00Z",
                "duration_sec": 1,
                "session_id": "hub-not-ours",
                "path": "public",
            }
        ).encode()
        status, _, payload = await _http(self.tailscale.listen_port, "POST", "/calls", body=unknown)
        self.assertEqual(status, 201)
        self.assertIsNone(json.loads(payload)["call"]["path"])

    async def test_tailscale_required_flag_uses_the_same_check(self) -> None:
        locked = Entrance("tailscale", "127.0.0.1", 0, True)
        server = await hub.listen_entrance(locked)
        try:
            status, _, body = await _http(locked.listen_port, "GET", "/health")
            self.assertEqual(status, 401)
            self.assertEqual(json.loads(body)["error"], "device token required")
            status, _, body = await _http(
                locked.listen_port,
                "GET",
                "/health",
                {"X-Gsm-Device-Token": TOKEN},
            )
            self.assertEqual(status, 200)
            self.assertIn(b"ok", body)
        finally:
            server.close()
            await server.wait_closed()


class EnvEntranceTests(unittest.TestCase):
    def test_public_entrance_is_opt_in(self) -> None:
        os.environ["GSM2COMPUTER_PUBLIC_ENABLE"] = "0"
        os.environ["GSM2COMPUTER_TAILSCALE_TOKEN_REQUIRED"] = "0"
        names = [(item.name, item.token_required) for item in hub.entrances_from_env()]
        self.assertEqual(names, [("tailscale", False)])
        os.environ["GSM2COMPUTER_PUBLIC_ENABLE"] = "1"
        os.environ["GSM2COMPUTER_PUBLIC_HOST"] = "127.0.0.1"
        os.environ["GSM2COMPUTER_PUBLIC_PORT"] = "8790"
        os.environ["GSM2COMPUTER_TAILSCALE_TOKEN_REQUIRED"] = "1"
        described = {item.name: item.token_required for item in hub.entrances_from_env()}
        self.assertEqual(described, {"tailscale": True, "public": True})
        os.environ["GSM2COMPUTER_PUBLIC_TOKEN_REQUIRED"] = "0"
        self.assertEqual([item.name for item in hub.entrances_from_env()], ["tailscale"])
        os.environ["GSM2COMPUTER_PUBLIC_TOKEN_REQUIRED"] = "1"
        os.environ["GSM2COMPUTER_PUBLIC_HOST"] = "0.0.0.0"
        self.assertEqual([item.name for item in hub.entrances_from_env()], ["tailscale"])
        os.environ["GSM2COMPUTER_PUBLIC_ENABLE"] = "0"
        os.environ["GSM2COMPUTER_PUBLIC_HOST"] = "127.0.0.1"
        os.environ.pop("GSM2COMPUTER_PUBLIC_TOKEN_REQUIRED", None)
        os.environ["GSM2COMPUTER_TAILSCALE_TOKEN_REQUIRED"] = "0"


class OverlappingRelinkTests(unittest.IsolatedAsyncioTestCase):
    async def test_loser_does_not_stamp_the_live_entrance(self) -> None:
        hub.relink_gate.close()
        if hub.live_call.busy:
            hub.live_call.release()
        hub.live_call.note_entrance("tailscale", "original")
        waiter = hub.relink_gate.begin("hub-race")
        writers: dict[str, bytearray] = {}

        class YieldingWriter:
            def __init__(self, name: str) -> None:
                self.name = name
                self.buf = bytearray()
                writers[name] = self.buf

            def write(self, data: bytes) -> None:
                self.buf += data

            async def drain(self) -> None:
                # The overlap the race needs: both handlers are in flight.
                await asyncio.sleep(0)

        async def park(name: str) -> bool:
            entrance = Entrance(name, "127.0.0.1", 9, name == "public")
            entrance_token = hub._current_entrance.set(entrance)
            device_token = hub._current_device_id.set(f"{name}-phone")
            try:
                return await hub._park_relink_socket(
                    None,  # type: ignore[arg-type]
                    YieldingWriter(name),  # type: ignore[arg-type]
                    "hub-race",
                    "dGhlIHNhbXBsZSBub25jZQ==",
                )
            finally:
                hub._current_device_id.reset(device_token)
                hub._current_entrance.reset(entrance_token)

        public_task = asyncio.create_task(park("public"))
        tail_task = asyncio.create_task(park("tailscale"))
        for _ in range(20):
            if waiter.done() and (public_task.done() or tail_task.done()):
                break
            await asyncio.sleep(0)
        self.assertTrue(waiter.done())
        _reader, delivered, _release = waiter.result()
        hub.relink_gate.release_holder()
        public_ok = await public_task
        tail_ok = await tail_task
        results = {"public": public_ok, "tailscale": tail_ok}
        other = "tailscale" if delivered.name == "public" else "public"
        self.assertTrue(results[delivered.name])
        self.assertFalse(results[other])
        self.assertEqual(hub.live_call.entrance, delivered.name)
        self.assertEqual(hub.live_call.device_id, f"{delivered.name}-phone")
        self.assertTrue(bytes(writers[delivered.name]).startswith(b"HTTP/1.1 101"))
        self.assertEqual(bytes(writers[other]), b"")
        hub.relink_gate.close()
        hub.live_call.note_entrance("tailscale", "")


class PublicBindTests(unittest.IsolatedAsyncioTestCase):
    async def test_public_bind_failure_keeps_tailscale(self) -> None:
        blocker = await __import__("asyncio").start_server(lambda r, w: None, "127.0.0.1", 0)
        port = blocker.sockets[0].getsockname()[1]
        tail = Entrance("tailscale", "127.0.0.1", 0, False)
        public = Entrance("public", "127.0.0.1", port, True)
        servers, active = await hub.start_entrances([tail, public])
        try:
            self.assertEqual([item.name for item in active], ["tailscale"])
            status, _, _ = await _http(tail.listen_port, "GET", "/health")
            self.assertEqual(status, 200)
        finally:
            for server in servers:
                server.close()
                await server.wait_closed()
            blocker.close()
            await blocker.wait_closed()


class CaddyBindTests(unittest.TestCase):
    def test_precheck_rejects_missing_and_wildcard_binds(self) -> None:
        script = HUB_DIR.parent / "deploy" / "cup" / "gsm2computer-caddy-pre.sh"
        caddyfile = HUB_DIR.parent / "deploy" / "cup" / "Caddyfile"

        def run_pre(bind: str | None) -> subprocess.CompletedProcess[str]:
            env = os.environ.copy()
            env.pop("GSM2COMPUTER_PUBLIC_BIND", None)
            if bind is not None:
                env["GSM2COMPUTER_PUBLIC_BIND"] = bind
            return subprocess.run(
                ["bash", str(script)],
                env=env,
                text=True,
                capture_output=True,
                check=False,
            )

        good = run_pre("62.83.35.197")
        self.assertEqual(good.returncode, 0, good.stderr)
        self.assertIn("62.83.35.197", good.stdout)
        for bad in (None, "", "0.0.0.0", "::", "*", "gsm.ta3leam.com", "62.83.35.197 127.0.0.1"):
            refused = run_pre(bad)
            self.assertNotEqual(refused.returncode, 0, bad)
            self.assertIn("GSM2COMPUTER_PUBLIC_BIND", refused.stderr)

        def adapt(bind: str) -> dict:
            env = os.environ.copy()
            env.update({
                "GSM2COMPUTER_PUBLIC_HOSTNAME": "gsm.ta3leam.com",
                "GSM2COMPUTER_PUBLIC_BIND": bind,
                "GSM2COMPUTER_ACME_EMAIL": "ops@example.com",
                "GSM2COMPUTER_PUBLIC_UPSTREAM": "127.0.0.1:8790",
            })
            proc = subprocess.run(
                ["caddy", "adapt", "--config", str(caddyfile), "--adapter", "caddyfile"],
                env=env,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(proc.returncode, 0, proc.stderr)
            return json.loads(proc.stdout)

        def listens_of(cfg: dict) -> list[str]:
            servers = (cfg.get("apps") or {}).get("http", {}).get("servers") or {}
            return [
                addr
                for server in servers.values()
                for addr in server.get("listen") or []
            ]

        self.assertEqual(listens_of(adapt("0.0.0.0")), ["0.0.0.0:443"])
        self.assertEqual(listens_of(adapt("")), [])


if __name__ == "__main__":
    unittest.main()
