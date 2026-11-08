#!/usr/bin/env python3
"""Per-device secret for the phone-facing hub entrance.

One check serves every listen socket. The entrance (public or tailscale) only
decides whether a missing token is allowed. A presented device token that does
not match is rejected on every entrance.

The existing bridge sends ``Authorization: Bearer <ephemeral>`` where the
ephemeral value comes from ``POST /token`` (today the literal ``hub-token``).
That header is not a device credential. The device secret is
``X-Gsm-Device-Token``. ``Authorization: Bearer`` counts only when the bearer
value itself is a configured device token, so the legacy ephemeral bearer keeps
working on the Tailscale entrance while the transition lasts.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import re
import secrets
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Optional, Sequence

LOG = logging.getLogger("gsm2computer-hub")

DEVICE_TOKEN_HEADER = "x-gsm-device-token"
ENTRANCE_PUBLIC = "public"
ENTRANCE_TAILSCALE = "tailscale"
ENTRANCES = (ENTRANCE_PUBLIC, ENTRANCE_TAILSCALE)
MIN_TOKEN_LEN = 32
MAX_TOKEN_LEN = 256
# POST /token mints this placeholder. It must never be a device secret.
EPHEMERAL_TOKEN = "hub-token"
DEVICE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
OUTBOX_ACK_RE = re.compile(r"^/sms/outbox/([^/]+)/ack$")

# Phone-facing routes the public entrance may serve after the token check.
# Portal, switchboard, and admin stay on the Tailscale entrance.
_PUBLIC_HTTP = {
    ("GET", "/health"),
    ("POST", "/token"),
    ("POST", "/sms"),
    ("GET", "/sms/outbox"),
    ("POST", "/calls"),
}


@dataclass
class Entrance:
    """A listen socket. The connection handler is not part of this object."""

    name: str
    host: str
    port: int
    token_required: bool
    bound_port: int = 0

    def __post_init__(self) -> None:
        if self.name not in ENTRANCES:
            raise ValueError(f"entrance must be one of {ENTRANCES}, got {self.name!r}")

    @property
    def listen_port(self) -> int:
        return self.bound_port or self.port

    def describe(self) -> dict:
        item = {
            "name": self.name,
            "listen": f"{self.host}:{self.listen_port}",
            "token_required": self.token_required,
            # The hub socket is plain HTTP either way. Public clients reach it
            # through Caddy, which owns the certificate.
            "tls": False,
            "client_scheme": "https" if self.name == ENTRANCE_PUBLIC else "http",
        }
        if self.name == ENTRANCE_PUBLIC:
            hostname = os.environ.get("GSM2COMPUTER_PUBLIC_HOSTNAME", "").strip()
            item["edge"] = "caddy"
            item["public_url"] = f"https://{hostname}" if hostname else None
        return item


@dataclass(frozen=True)
class AuthDecision:
    ok: bool
    device_id: Optional[str] = None
    # "required" (header absent on a locked entrance) or "rejected" (bad token).
    reason: Optional[str] = None

    @property
    def error(self) -> Optional[str]:
        if self.ok:
            return None
        if self.reason == "rejected":
            return "device token rejected"
        return "device token required"

    @property
    def status(self) -> int:
        return 200 if self.ok else 401


@dataclass(frozen=True)
class DeviceToken:
    device_id: str
    digest: bytes


def _digest(token: str) -> bytes:
    return hashlib.sha256(token.encode("utf-8")).digest()


def validate_device_id(device_id: str) -> str:
    cleaned = (device_id or "").strip()
    if not DEVICE_ID_RE.fullmatch(cleaned):
        raise ValueError(
            "device id must be 1–64 characters of letters, digits, '.', '_' or '-'"
        )
    return cleaned


def validate_token(token: str) -> str:
    cleaned = (token or "").strip()
    if len(cleaned) < MIN_TOKEN_LEN or len(cleaned) > MAX_TOKEN_LEN:
        raise ValueError(f"device token must be {MIN_TOKEN_LEN}–{MAX_TOKEN_LEN} characters")
    if any(ch.isspace() for ch in cleaned):
        raise ValueError("device token must not contain whitespace")
    if cleaned == EPHEMERAL_TOKEN:
        raise ValueError("device token must not be the ephemeral hub-token placeholder")
    return cleaned


def default_token_path() -> Path:
    raw = os.environ.get("GSM2COMPUTER_DEVICE_TOKENS", "").strip()
    if raw:
        return Path(raw)
    return Path.home() / ".config" / "gsm2computer" / "device-tokens.json"


class DeviceTokenFile:
    """JSON file of ``{"devices": [{"id", "token"}, ...]}``. Reloads when mtime changes."""

    def __init__(self, path: Optional[Path] = None) -> None:
        self._path_override = path
        self._loaded_path: Optional[Path] = None
        self._mtime_ns: Optional[int] = None
        self._devices: tuple[DeviceToken, ...] = ()

    def path(self) -> Path:
        return self._path_override if self._path_override is not None else default_token_path()

    def reload_if_needed(self) -> None:
        path = self.path()
        try:
            st = path.stat()
        except FileNotFoundError:
            if self._devices or self._loaded_path != path:
                LOG.info("no device token file at %s (public entrance fails closed)", path)
            self._devices = ()
            self._mtime_ns = None
            self._loaded_path = path
            return
        except OSError as exc:
            LOG.error("device token file %s unreadable: %s", path, exc)
            self._devices = ()
            self._mtime_ns = None
            self._loaded_path = path
            return
        if self._loaded_path == path and self._mtime_ns == st.st_mtime_ns:
            return
        try:
            devices = load_device_tokens(path)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            LOG.error("device token file %s rejected: %s", path, exc)
            self._devices = ()
            self._mtime_ns = st.st_mtime_ns
            self._loaded_path = path
            return
        if st.st_mode & (stat.S_IRGRP | stat.S_IWGRP | stat.S_IXGRP | stat.S_IROTH | stat.S_IWOTH | stat.S_IXOTH):
            LOG.warning("device token file %s is readable by group or others; chmod 600", path)
        self._devices = tuple(devices)
        self._mtime_ns = st.st_mtime_ns
        self._loaded_path = path
        LOG.info(
            "loaded %d device token(s) from %s: %s",
            len(self._devices),
            path,
            ", ".join(item.device_id for item in self._devices) or "-",
        )

    def device_id_for(self, token: str) -> Optional[str]:
        self.reload_if_needed()
        if not token:
            return None
        want = _digest(token)
        found: Optional[str] = None
        for item in self._devices:
            if hmac.compare_digest(item.digest, want):
                found = item.device_id
        return found


TOKENS = DeviceTokenFile()


def load_device_tokens(path: Path) -> list[DeviceToken]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("token file must be a JSON object")
    rows = raw.get("devices")
    if not isinstance(rows, list):
        raise ValueError("token file must contain a devices array")
    devices: list[DeviceToken] = []
    seen: set[str] = set()
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("each device must be an object with id and token")
        device_id = validate_device_id(str(row.get("id") or ""))
        token = validate_token(str(row.get("token") or ""))
        if device_id in seen:
            raise ValueError(f"duplicate device id {device_id}")
        seen.add(device_id)
        devices.append(DeviceToken(device_id=device_id, digest=_digest(token)))
    return devices


def authorize(
    headers: Mapping[str, str],
    entrance: Entrance,
    tokens: Optional[DeviceTokenFile] = None,
) -> AuthDecision:
    """Accept a device, or an anonymous Tailscale client while that entrance allows it.

    A bad ``X-Gsm-Device-Token`` is rejected on every entrance. The legacy
    ephemeral bearer is ignored unless it equals a configured device token.
    """
    store = tokens if tokens is not None else TOKENS
    if DEVICE_TOKEN_HEADER in headers:
        presented = headers.get(DEVICE_TOKEN_HEADER, "").strip()
        device_id = store.device_id_for(presented)
        if not device_id:
            return AuthDecision(ok=False, reason="rejected")
        return AuthDecision(ok=True, device_id=device_id)

    auth = headers.get("authorization", "").strip()
    if auth.lower().startswith("bearer "):
        bearer = auth[7:].strip()
        device_id = store.device_id_for(bearer)
        if device_id:
            return AuthDecision(ok=True, device_id=device_id)
    if entrance.token_required:
        return AuthDecision(ok=False, reason="required")
    return AuthDecision(ok=True, device_id=None)


def public_route_allowed(method: str, path_only: str, headers: Mapping[str, str]) -> bool:
    """Routes a device token may use on the public entrance."""
    if headers.get("upgrade", "").lower() == "websocket" or "sec-websocket-key" in headers:
        return True
    if (method, path_only) in _PUBLIC_HTTP:
        return True
    return method == "POST" and OUTBOX_ACK_RE.match(path_only) is not None


def generate_token() -> str:
    # token_urlsafe(32) is 43 characters, inside the accepted length.
    return secrets.token_urlsafe(32)


def write_device_token(path: Path, device_id: str, token: str) -> None:
    device_id = validate_device_id(device_id)
    token = validate_token(token)
    existing: dict = {"devices": []}
    if path.is_file():
        loaded = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(loaded, dict) or not isinstance(loaded.get("devices"), list):
            raise ValueError(f"{path} is not a device token file")
        existing = loaded
    devices = [row for row in existing["devices"] if not (isinstance(row, dict) and row.get("id") == device_id)]
    devices.append({"id": device_id, "token": token})
    # Validate the whole file before replacing it.
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps({"devices": devices}, indent=2) + "\n"
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(payload, encoding="utf-8")
    os.chmod(tmp, 0o600)
    tmp.replace(path)
    os.chmod(path, 0o600)
    load_device_tokens(path)


def _cli(argv: Optional[Sequence[str]] = None) -> int:
    import argparse
    import sys

    parser = argparse.ArgumentParser(description="Generate a gsm2computer per-device hub token")
    parser.add_argument("command", choices=("generate",))
    parser.add_argument("device_id", help="stable id, e.g. moto-g84")
    parser.add_argument(
        "--file",
        default="",
        help="write into this token file (mode 0600). Omit to print the token only.",
    )
    args = parser.parse_args(argv)
    token = generate_token()
    if args.file:
        destination = Path(args.file)
        write_device_token(destination, args.device_id, token)
        print(token)
        print(f"wrote {args.device_id} to {destination} (mode 0600)", file=sys.stderr)
        return 0
    print(token)
    print(
        f"add to the token file as "
        f'{{"id": "{validate_device_id(args.device_id)}", "token": "<stdout>"}}',
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli())
