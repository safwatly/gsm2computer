#!/usr/bin/env python3
"""Drive both hub entrances: token check, loopback audio, /health path, call report.

Reads $GSM2COMPUTER_VERIFY_STATE/runtime.env (written by launch-hub.sh).
Writes evidence JSON under $EVIDENCE. Does not print the device token.
"""
from __future__ import annotations

import base64
import json
import os
import socket
import ssl
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path


def load_env(path: Path) -> dict[str, str]:
    env: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        env[key] = value
    return env


def http(url: str, method: str = "GET", headers: dict[str, str] | None = None, body: bytes = b"", timeout: float = 5.0):
    req = urllib.request.Request(url, data=body if method != "GET" else None, method=method)
    for key, value in (headers or {}).items():
        req.add_header(key, value)
    if method != "GET" and "Content-Length" not in (headers or {}):
        req.add_header("Content-Length", str(len(body)))
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, dict(resp.headers), resp.read()
    except urllib.error.HTTPError as exc:
        return exc.code, dict(exc.headers), exc.read()


class Ws:
    def __init__(self, host: str, port: int, path: str, headers: dict[str, str], ssl_ctx: ssl.SSLContext | None = None):
        raw = socket.create_connection((host, port), timeout=5)
        raw.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        if ssl_ctx is not None:
            raw = ssl_ctx.wrap_socket(raw, server_hostname=host)
        self.sock = raw
        self.buf = b""
        key = base64.b64encode(os.urandom(16)).decode("ascii")
        lines = [
            f"GET {path} HTTP/1.1",
            f"Host: {host}:{port}",
            "Upgrade: websocket",
            "Connection: Upgrade",
            f"Sec-WebSocket-Key: {key}",
            "Sec-WebSocket-Version: 13",
        ]
        for name, value in headers.items():
            lines.append(f"{name}: {value}")
        self.sock.sendall(("\r\n".join(lines) + "\r\n\r\n").encode("ascii"))
        raw_head = b""
        while b"\r\n\r\n" not in raw_head:
            chunk = self.sock.recv(4096)
            if not chunk:
                break
            raw_head += chunk
        head, _, rest = raw_head.partition(b"\r\n\r\n")
        self.buf = rest
        status_line = head.split(b"\r\n", 1)[0].decode("iso-8859-1", "replace")
        parts = status_line.split()
        self.status = int(parts[1]) if len(parts) > 1 else 0
        self.head = head.decode("iso-8859-1", "replace")

    def send_text(self, text: str) -> None:
        data = text.encode("utf-8")
        mask = os.urandom(4)
        header = bytearray([0x81])
        length = len(data)
        if length < 126:
            header.append(0x80 | length)
        elif length < 65536:
            header.append(0x80 | 126)
            header += length.to_bytes(2, "big")
        else:
            header.append(0x80 | 127)
            header += length.to_bytes(8, "big")
        masked = bytes(byte ^ mask[index % 4] for index, byte in enumerate(data))
        self.sock.sendall(bytes(header) + mask + masked)

    def _exact(self, size: int) -> bytes:
        while len(self.buf) < size:
            chunk = self.sock.recv(4096)
            if not chunk:
                raise ConnectionError("websocket closed")
            self.buf += chunk
        out, self.buf = self.buf[:size], self.buf[size:]
        return out

    def recv_text(self, timeout: float) -> str | None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self.sock.settimeout(max(0.1, deadline - time.monotonic()))
            try:
                header = self._exact(2)
            except (TimeoutError, socket.timeout):
                return None
            opcode = header[0] & 0x0F
            length = header[1] & 0x7F
            if length == 126:
                length = int.from_bytes(self._exact(2), "big")
            elif length == 127:
                length = int.from_bytes(self._exact(8), "big")
            payload = self._exact(length) if length else b""
            if opcode == 0x1:
                return payload.decode("utf-8", "replace")
            if opcode == 0x8:
                return None
            if opcode == 0x9:
                # Protocol pong. Not required for this short call.
                continue
        return None

    def close(self) -> None:
        # 1000 is a clean hangup. An empty close is a drop, and the hub holds
        # the call open for the relink grace instead of releasing the slot.
        try:
            code = (1000).to_bytes(2, "big")
            mask = os.urandom(4)
            masked = bytes(byte ^ mask[index % 4] for index, byte in enumerate(code))
            self.sock.sendall(bytes([0x88, 0x82]) + mask + masked)
        except OSError:
            pass
        try:
            self.sock.close()
        except OSError:
            pass

    def drop(self) -> None:
        """TCP close without a 1000 frame, so the hub treats it as a link gap."""
        try:
            self.sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        try:
            self.sock.close()
        except OSError:
            pass


def wait_idle(base: str, timeout: float = 8.0) -> dict:
    deadline = time.monotonic() + timeout
    last: dict = {}
    while time.monotonic() < deadline:
        status, _, body = http(base + "/health")
        if status == 200:
            last = json.loads(body)
            if not (last.get("call") or {}).get("busy"):
                return last
        time.sleep(0.1)
    raise AssertionError(f"call slot stayed busy: {last}")


def place_loopback(
    base_ws: tuple[str, int],
    health_url: str,
    headers: dict[str, str],
    ssl_ctx=None,
    path: str = "/loopback",
    expect_echo: bool = True,
    hangup: str = "clean",
) -> dict:
    host, port = base_ws
    ws = Ws(host, port, path, headers, ssl_ctx=ssl_ctx)
    if ws.status != 101:
        ws.close()
        raise AssertionError(f"expected 101, got {ws.status}: {ws.head[:300]}")
    session = None
    deadline = time.monotonic() + 12
    while time.monotonic() < deadline and session is None:
        text = ws.recv_text(12)
        if not text:
            break
        event = json.loads(text)
        if event.get("type") == "session.updated":
            session = event
        elif event.get("type") == "error":
            ws.close()
            raise AssertionError(f"handshake error: {event}")
    if session is None:
        ws.close()
        raise AssertionError("no session.updated")
    deadline = time.monotonic() + 8
    tone = bytes([0x00, 0x7F, 0xFF, 0x80]) * 40
    echo = None
    if expect_echo:
        ws.send_text(json.dumps({
            "type": "input_audio_buffer.append",
            "audio": base64.b64encode(tone).decode("ascii"),
            "format": "audio/pcmu",
            "rate": 8000,
        }))
    while expect_echo and time.monotonic() < deadline:
        text = ws.recv_text(12)
        if not text:
            break
        event = json.loads(text)
        if event.get("type") == "response.output_audio.delta":
            echo = event
            break
        if event.get("type") == "error":
            ws.close()
            raise AssertionError(f"audio error: {event}")
    status, _, body = http(health_url)
    health = json.loads(body) if status == 200 else {"http": status, "body": body.decode("utf-8", "replace")[:400]}
    if hangup == "drop":
        ws.drop()
    elif hangup == "stay":
        pass
    else:
        ws.close()
    echoed = b""
    if echo is not None:
        echoed = base64.b64decode(echo.get("delta") or "")
    if expect_echo and echoed != tone:
        raise AssertionError(f"no audio echo; health={health}")
    result = {
        "session_entrance": (session.get("session") or {}).get("entrance"),
        "session_id": (session.get("session") or {}).get("id"),
        "echo_bytes": len(echoed),
        "echo_matches": echoed == tone,
        "health_status": status,
        "health_call": health.get("call"),
    }
    if hangup == "stay":
        result["ws"] = ws
    return result


def main() -> int:
    state = Path(os.environ.get("GSM2COMPUTER_VERIFY_STATE", "/tmp/gsm2computer-verify"))
    env = load_env(state / "runtime.env")
    evidence_dir = Path(env["EVIDENCE"])
    evidence_dir.mkdir(parents=True, exist_ok=True)
    token = env["DEVICE_TOKEN"]
    good = {"X-Gsm-Device-Token": token}
    bad = {"X-Gsm-Device-Token": "x" * 40}
    legacy = {"Authorization": "Bearer hub-token"}
    ts = env["TAILSCALE_URL"]
    pub = env["PUBLIC_URL"]
    report: dict = {"checks": []}

    def check(name: str, ok: bool, detail: dict | None = None) -> None:
        report["checks"].append({"name": name, "ok": bool(ok), **(detail or {})})
        if not ok:
            raise AssertionError(name)

    status, _, body = http(pub + "/health")
    check("public-no-token", status == 401 and json.loads(body)["error"] == "device token required", {"status": status})
    status, _, body = http(pub + "/health", headers=bad)
    check("public-bad-token", status == 401 and json.loads(body)["error"] == "device token rejected", {"status": status})
    status, _, body = http(pub + "/health", headers=legacy)
    check("public-ephemeral-bearer-rejected", status == 401, {"status": status})
    status, _, body = http(pub + "/health", headers=good)
    health = json.loads(body)
    names = {item["name"]: item for item in health["entrances"]}
    check(
        "public-good-token",
        status == 200 and names["public"]["token_required"] and names["public"]["client_scheme"] == "https",
        {"status": status},
    )
    status, _, _ = http(pub + "/portal/", headers=good)
    check("public-portal-hidden", status == 404, {"status": status})
    status, _, _ = http(pub + "/admin/call/release", "POST", good)
    check("public-admin-hidden", status == 404, {"status": status})

    status, _, _ = http(ts + "/health")
    check("tailscale-no-token", status == 200, {"status": status})
    status, _, _ = http(ts + "/health", headers=legacy)
    check("tailscale-ephemeral-bearer", status == 200, {"status": status})
    status, _, body = http(ts + "/health", headers=bad)
    check("tailscale-bad-token", status == 401 and json.loads(body)["error"] == "device token rejected", {"status": status})
    status, _, body = http(ts + "/health", headers=good)
    check("tailscale-good-token", status == 200 and json.loads(body)["call"]["path"] is None, {"status": status})

    send = json.dumps({"to": "+15550001111", "body": "verify"}).encode()
    status, _, body = http(ts + "/portal/api/messages/send", "POST", body=send)
    outbox_id = json.loads(body)["id"]
    check("tailscale-queue-sms", status == 201, {"id": outbox_id})
    status, _, _ = http(pub + "/sms/outbox")
    check("public-outbox-no-token", status == 401, {"status": status})
    status, _, body = http(pub + "/sms/outbox", headers=good)
    items = json.loads(body)["items"]
    check("public-outbox-good-token", status == 200 and items and items[0]["id"] == outbox_id, {"status": status})
    ack = json.dumps({"status": "sent"}).encode()
    status, _, _ = http(pub + f"/sms/outbox/{outbox_id}/ack", "POST", good, ack)
    check("public-outbox-ack", status == 200, {"status": status})

    status, _, body = http(ts + "/switchboard/state")
    mode_before = json.loads(body).get("mode") if status == 200 else None
    ws_headers = {
        **good,
        "Upgrade": "websocket",
        "Connection": "Upgrade",
        "Sec-WebSocket-Key": "dGhlIHNhbXBsZSBub25jZQ==",
        "Sec-WebSocket-Version": "13",
    }
    status, _, _ = http(pub + "/loopback", headers=ws_headers)
    check("public-loopback-forbidden", status == 404, {"status": status})
    status, _, _ = http(pub + "/e2e-test", headers=ws_headers)
    check("public-e2e-forbidden", status == 404, {"status": status})
    status, _, _ = http(
        pub + "/switchboard/mode",
        "POST",
        good,
        json.dumps({"mode": "loopback"}).encode(),
    )
    check("public-switchboard-forbidden", status == 404, {"status": status})
    status, _, body = http(
        pub + "/sms",
        "POST",
        good,
        json.dumps({"from": "+15551212", "body": "MODE loopback"}).encode(),
    )
    sms_body = json.loads(body)
    status_mode, _, mode_body = http(ts + "/switchboard/state")
    mode_after = json.loads(mode_body).get("mode") if status_mode == 200 else None
    check(
        "public-sms-command-ignored",
        status == 200 and sms_body.get("commandIgnored") is True and mode_after == mode_before,
        {"status": status, "mode": mode_after},
    )

    wait_idle(ts)
    public_call = place_loopback(
        ("127.0.0.1", int(env["PUBLIC_PORT"])),
        ts + "/health",
        good,
        path="/",
        expect_echo=False,
    )
    check(
        "public-call",
        public_call["session_entrance"] == "public"
        and (public_call["health_call"] or {}).get("path") == "public"
        and (public_call["health_call"] or {}).get("entrance") == "public"
        and (public_call["health_call"] or {}).get("route") == "/"
        and (public_call["health_call"] or {}).get("device_id") == "moto-g84",
        public_call,
    )
    idle = wait_idle(ts)
    call_body = json.dumps({
        "direction": "in",
        "number": "+15550004444",
        "started_at": "2026-11-08T18:00:00Z",
        "duration_sec": 2,
        "session_id": public_call["session_id"],
    }).encode()
    status, _, body = http(ts + "/calls", "POST", body=call_body)
    stored = json.loads(body)["call"]
    check("public-call-report", status == 201 and stored["path"] == "public", {"status": status, "path": stored.get("path")})
    lied = json.dumps({
        "direction": "in",
        "number": "+15550005555",
        "started_at": "2026-11-08T18:05:00Z",
        "duration_sec": 1,
        "session_id": "hub-not-ours",
        "path": "public",
    }).encode()
    status, _, body = http(ts + "/calls", "POST", body=lied)
    check(
        "unknown-session-not-client-path",
        status == 201 and json.loads(body)["call"]["path"] is None,
        {"status": status, "path": json.loads(body)["call"].get("path")},
    )
    omitted = json.dumps({
        "direction": "in",
        "number": "+15550006666",
        "started_at": "2026-11-08T18:06:00Z",
        "duration_sec": 1,
        "path": "tailscale",
    }).encode()
    status, _, body = http(ts + "/calls", "POST", body=omitted)
    check(
        "omitted-session-unknown",
        status == 201 and json.loads(body)["call"]["path"] is None,
        {"status": status, "path": json.loads(body)["call"].get("path")},
    )

    legacy_call = place_loopback(
        ("127.0.0.1", int(env["TAILSCALE_PORT"])),
        ts + "/health",
        legacy,
        hangup="stay",
    )
    check(
        "tailscale-legacy-loopback",
        legacy_call["session_entrance"] == "tailscale"
        and legacy_call["echo_matches"]
        and (legacy_call["health_call"] or {}).get("path") == "tailscale"
        and (legacy_call["health_call"] or {}).get("device_id") is None,
        {key: value for key, value in legacy_call.items() if key != "ws"},
    )
    legacy_call["ws"].drop()
    relink_headers = {**good, "X-Gsm-Call-Session": legacy_call["session_id"]}
    relinked = None
    deadline = time.monotonic() + 3
    last_error = "relink did not attach"
    while time.monotonic() < deadline:
        try:
            candidate = place_loopback(
                ("127.0.0.1", int(env["PUBLIC_PORT"])),
                ts + "/health",
                relink_headers,
                path="/",
                expect_echo=False,
            )
            if (
                candidate["session_id"] == legacy_call["session_id"]
                and candidate["session_entrance"] == "public"
            ):
                relinked = candidate
                break
            last_error = "relink opened a different session"
        except AssertionError as exc:
            last_error = str(exc)
            time.sleep(0.1)
    if relinked is None:
        raise AssertionError(last_error)
    check(
        "cross-entrance-relink",
        relinked["session_entrance"] == "public"
        and relinked["session_id"] == legacy_call["session_id"]
        and (relinked["health_call"] or {}).get("path") == "public"
        and (relinked["health_call"] or {}).get("entrance") == "public",
        {key: value for key, value in relinked.items() if key != "ws"},
    )
    wait_idle(ts)
    token_call = place_loopback(("127.0.0.1", int(env["TAILSCALE_PORT"])), ts + "/health", good)
    check(
        "tailscale-token-loopback",
        token_call["session_entrance"] == "tailscale"
        and token_call["echo_matches"]
        and (token_call["health_call"] or {}).get("device_id") == "moto-g84",
        token_call,
    )
    wait_idle(ts)

    # Same binary, Tailscale entrance with the token required. Proves the flag
    # is config on the listen socket, not a second handler.
    required_log = state / "hub-required.log"
    required_db = state / "portal-required.sqlite"
    proc_env = os.environ.copy()
    proc_env.update({
        "GSM2COMPUTER_HUB_HOST": "127.0.0.1",
        "GSM2COMPUTER_HUB_PORT": "18891",
        "GSM2COMPUTER_PUBLIC_ENABLE": "1",
        "GSM2COMPUTER_PUBLIC_HOST": "127.0.0.1",
        "GSM2COMPUTER_PUBLIC_PORT": "18892",
        "GSM2COMPUTER_PUBLIC_HOSTNAME": "gsm.ta3leam.com",
        "GSM2COMPUTER_TAILSCALE_TOKEN_REQUIRED": "1",
        "GSM2COMPUTER_PUBLIC_TOKEN_REQUIRED": "1",
        "GSM2COMPUTER_DEVICE_TOKENS": env["TOKEN_FILE"],
        "GSM2COMPUTER_PORTAL_DB": str(required_db),
        "GSM2COMPUTER_LINK_GAP_LOG": str(state / "required-gaps.jsonl"),
        "GSM2COMPUTER_OPENCLAW_TALK": "off",
        "GSM2COMPUTER_HUB_DIR": str(Path(env["ROOT"]) / "hub"),
    })
    log_fh = required_log.open("w", encoding="utf-8")
    proc = subprocess.Popen(
        [sys.executable, "hub/hub.py"],
        cwd=env["ROOT"],
        env=proc_env,
        stdout=log_fh,
        stderr=subprocess.STDOUT,
    )
    (state / "hub-required.pid").write_text(str(proc.pid), encoding="utf-8")
    try:
        deadline = time.monotonic() + 8
        ready = False
        while time.monotonic() < deadline:
            try:
                status, _, _ = http("http://127.0.0.1:18891/health")
                ready = True
                break
            except OSError:
                time.sleep(0.1)
        if not ready:
            raise AssertionError("required-token hub did not listen")
        status, _, body = http("http://127.0.0.1:18891/health")
        check(
            "tailscale-required-no-token",
            status == 401 and json.loads(body)["error"] == "device token required",
            {"status": status},
        )
        status, _, _ = http("http://127.0.0.1:18891/health", headers=good)
        check("tailscale-required-good-token", status == 200, {"status": status})
        bad_ws = Ws("127.0.0.1", 18891, "/loopback", bad)
        check("tailscale-required-bad-websocket", bad_ws.status == 401, {"status": bad_ws.status})
        bad_ws.close()
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
        log_fh.close()

    caddy_note = start_caddy(state, int(env["PUBLIC_PORT"]))
    if caddy_note.get("ok"):
        ctx = ssl.create_default_context(cafile=caddy_note["ca"])
        status, _, body = https_request(caddy_note["url"] + "/health", ctx, {})
        check("caddy-no-token", status == 401, {"status": status})
        status, _, body = https_request(caddy_note["url"] + "/health", ctx, good)
        tls_health = json.loads(body)
        check("caddy-good-token", status == 200 and tls_health["ok"], {"status": status})
        wait_idle(ts)
        tls_call = place_loopback(
            ("127.0.0.1", caddy_note["port"]),
            ts + "/health",
            good,
            ssl_ctx=ctx,
            path="/",
            expect_echo=False,
        )
        check(
            "caddy-call",
            tls_call["session_entrance"] == "public"
            and (tls_call["health_call"] or {}).get("path") == "public"
            and (tls_call["health_call"] or {}).get("route") == "/",
            tls_call,
        )
        wait_idle(ts)
        stop_pid(state / "caddy.pid")
    report["caddy"] = {key: value for key, value in caddy_note.items() if key != "ca"}
    if not caddy_note.get("ok"):
        raise AssertionError(f"caddy tls entrance failed: {caddy_note}")

    report["ok"] = all(item["ok"] for item in report["checks"])
    destination = evidence_dir / "entrances.json"
    destination.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {destination} checks={len(report['checks'])} ok={report['ok']}")
    return 0 if report["ok"] else 1


def https_request(url: str, ctx: ssl.SSLContext, headers: dict[str, str]):
    req = urllib.request.Request(url, method="GET")
    for key, value in headers.items():
        req.add_header(key, value)
    try:
        with urllib.request.urlopen(req, timeout=5, context=ctx) as resp:
            return resp.status, dict(resp.headers), resp.read()
    except urllib.error.HTTPError as exc:
        return exc.code, dict(exc.headers), exc.read()


def start_caddy(state: Path, upstream_port: int) -> dict:
    port = 18443
    data = state / "caddy-data"
    data.mkdir(parents=True, exist_ok=True)
    caddyfile = state / "Caddyfile"
    caddyfile.write_text(
        "\n".join([
            "{",
            "\tadmin off",
            "\tauto_https disable_redirects",
            "\thttps_port 18443",
            "\tdefault_bind 127.0.0.1",
            "\tlocal_certs",
            "\tskip_install_trust",
            "}",
            "https://127.0.0.1:18443 {",
            "\ttls internal",
            f"\treverse_proxy 127.0.0.1:{upstream_port} {{",
            "\t\tflush_interval -1",
            "\t\ttransport http {",
            "\t\t\tversions 1.1",
            "\t\t}",
            "\t}",
            "}",
            "",
        ]),
        encoding="utf-8",
    )
    log = (state / "caddy.log").open("w", encoding="utf-8")
    env = os.environ.copy()
    env["XDG_DATA_HOME"] = str(data)
    env["XDG_CONFIG_HOME"] = str(state / "caddy-config")
    try:
        proc = subprocess.Popen(
            ["caddy", "run", "--config", str(caddyfile), "--adapter", "caddyfile"],
            stdout=log,
            stderr=subprocess.STDOUT,
            env=env,
        )
    except FileNotFoundError:
        log.close()
        return {"ok": False, "error": "caddy not installed"}
    (state / "caddy.pid").write_text(str(proc.pid), encoding="utf-8")
    ca = data / "caddy" / "pki" / "authorities" / "local" / "root.crt"
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline:
        if ca.is_file() and proc.poll() is None:
            try:
                ctx = ssl.create_default_context(cafile=str(ca))
                https_request(f"https://127.0.0.1:{port}/health", ctx, {})
                log.close()
                return {"ok": True, "url": f"https://127.0.0.1:{port}", "port": port, "ca": str(ca), "internal_cert": True}
            except OSError:
                pass
        if proc.poll() is not None:
            break
        time.sleep(0.1)
    log.close()
    text = (state / "caddy.log").read_text(encoding="utf-8", errors="replace")[-800:]
    stop_pid(state / "caddy.pid")
    return {"ok": False, "error": "caddy did not serve", "log": text}


def stop_pid(path: Path) -> None:
    if not path.is_file():
        return
    pid = int(path.read_text(encoding="utf-8").strip() or "0")
    if pid <= 0:
        return
    try:
        os.kill(pid, 15)
    except OSError:
        return


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        state = Path(os.environ.get("GSM2COMPUTER_VERIFY_STATE", "/tmp/gsm2computer-verify"))
        env_path = state / "runtime.env"
        evidence = Path("/opt/cursor/artifacts/verify-gsm2computer")
        if env_path.is_file():
            for line in env_path.read_text(encoding="utf-8").splitlines():
                if line.startswith("EVIDENCE="):
                    evidence = Path(line.split("=", 1)[1])
        evidence.mkdir(parents=True, exist_ok=True)
        (evidence / "entrances-error.txt").write_text(f"{type(exc).__name__}: {exc}\n", encoding="utf-8")
        print(f"drive failed: {exc}", file=sys.stderr)
        raise
