#!/usr/bin/env python3
"""Quectel EC25 USB-audio setup used by the Pi install.

The planner functions are pure so they can be tested without a modem.
`main` talks to the AT port when the operator runs the boot unit.
"""
from __future__ import annotations

import os
import re
import sys
import time
from typing import Optional


class UacUnavailable(Exception):
    """This firmware's USBCFG has no UAC flag."""


_USBCFG_RE = re.compile(r'\+QCFG:\s*"usbcfg"\s*,\s*([^\r\n]+)', re.IGNORECASE)
_CARD_RE = re.compile(r"card \d+:\s*(\S+)\s+\[([^\]]*)\]")


def plan_usbcfg(response: str) -> Optional[str]:
    """Return the AT command that turns the UAC bit on, or None if it is already on.

    Quectel documents the last function flag as UAC. Fewer than seven flags
    means this firmware cannot expose the USB sound card.
    """
    match = _USBCFG_RE.search(response or "")
    if not match:
        raise ValueError("modem did not return USBCFG")
    parts = [part.strip() for part in match.group(1).split(",") if part.strip()]
    if len(parts) < 3:
        raise ValueError("USBCFG response is too short")
    flags = parts[2:]
    if len(flags) < 7:
        raise UacUnavailable(
            "USBCFG has no UAC flag. This EC25 firmware cannot do USB audio."
        )
    if flags[-1] == "1":
        return None
    flags[-1] = "1"
    return 'AT+QCFG="USBCFG",' + ",".join([*parts[:2], *flags])


def alsa_card_id(aplay_listing: str) -> Optional[str]:
    """Card id for `hw:CARD=<id>,DEV=0`, or None when no Quectel card is listed."""
    for line in (aplay_listing or "").splitlines():
        match = _CARD_RE.search(line)
        if not match:
            continue
        ident, desc = match.group(1), match.group(2)
        if re.search(r"android|quectel|ec25", f"{ident} {desc}", re.IGNORECASE):
            return ident
    return None


def update_quectel_conf(text: str, data_port: Optional[str] = None, alsa_id: Optional[str] = None) -> str:
    """Rewrite the managed data= and alsadev= lines. Comments stay put."""
    out: list[str] = []
    for line in text.splitlines(keepends=True):
        stripped = line.lstrip()
        newline = "\n" if line.endswith("\n") else ""
        if data_port and stripped.startswith("data=") and not stripped.startswith(";"):
            out.append(f"data={data_port}{newline}")
            continue
        if alsa_id and stripped.startswith("alsadev=") and not stripped.startswith(";"):
            out.append(f"alsadev=hw:CARD={alsa_id},DEV=0{newline}")
            continue
        out.append(line)
    return "".join(out)


def _configure_tty(fd: int) -> None:
    import termios

    attrs = termios.tcgetattr(fd)
    attrs[0] = 0
    attrs[1] = 0
    attrs[2] = termios.CS8 | termios.CREAD | termios.CLOCAL | termios.B115200
    attrs[3] = 0
    attrs[4] = termios.B115200
    attrs[5] = termios.B115200
    attrs[6][termios.VMIN] = 0
    attrs[6][termios.VTIME] = 0
    termios.tcsetattr(fd, termios.TCSANOW, attrs)
    termios.tcflush(fd, termios.TCIOFLUSH)


def at_exchange(port: str, command: str, timeout: float = 1.5) -> str:
    """Send one AT command and collect bytes until OK, ERROR, or the timeout."""
    fd = os.open(port, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
    try:
        _configure_tty(fd)
        os.write(fd, (command + "\r").encode("ascii"))
        deadline = time.monotonic() + timeout
        chunks: list[bytes] = []
        while time.monotonic() < deadline:
            try:
                block = os.read(fd, 512)
            except BlockingIOError:
                block = b""
            if block:
                chunks.append(block)
                text = b"".join(chunks).decode("utf-8", errors="replace")
                if "\nOK" in text or text.rstrip().endswith("OK") or "ERROR" in text:
                    break
            else:
                time.sleep(0.05)
        return b"".join(chunks).decode("utf-8", errors="replace")
    finally:
        os.close(fd)


def _candidate_ports() -> list[str]:
    found = []
    for index in range(0, 8):
        path = f"/dev/ttyUSB{index}"
        if os.path.exists(path):
            found.append(path)
    return found


def find_at_port() -> Optional[str]:
    for port in _candidate_ports():
        try:
            reply = at_exchange(port, "AT", timeout=0.8)
        except OSError:
            continue
        if "OK" in reply:
            return port
    return None


def enable_voice(port: str) -> str:
    """Enable UAC if needed, then arm PCM. Returns a short status line."""
    current = at_exchange(port, 'AT+QCFG="USBCFG"', timeout=2)
    command = plan_usbcfg(current)
    notes = []
    if command:
        at_exchange(port, command, timeout=2)
        notes.append("usbcfg updated; the modem re-enumerates")
        time.sleep(5)
        port = find_at_port() or port
    mode = (os.environ.get("GSM_GATEWAY_QAUDMOD") or "3").strip()
    if mode.lower() not in ("", "off", "skip"):
        at_exchange(port, f"AT+QAUDMOD={mode}", timeout=1.5)
        notes.append(f"qaudmod={mode}")
    # QPCMV does not persist across reboot. The channel driver also toggles
    # it per call; arming it here makes the USB sound card appear before the
    # first ring.
    at_exchange(port, "AT+QPCMV=1,2", timeout=1.5)
    notes.append("qpcmv=1,2")
    return "; ".join(notes) or "uac already armed"


def main() -> int:
    port = find_at_port()
    if port is None:
        print("gsm-gateway-enable-uac: no AT port answered; plug in the EC25", file=sys.stderr)
        return 0
    try:
        print(f"gsm-gateway-enable-uac: {port}: {enable_voice(port)}")
    except UacUnavailable as exc:
        print(f"gsm-gateway-enable-uac: {exc}", file=sys.stderr)
        return 1
    except OSError as exc:
        print(f"gsm-gateway-enable-uac: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
