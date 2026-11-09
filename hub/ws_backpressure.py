#!/usr/bin/env python3
"""Downlink WebSocket backpressure: survive a transient stall, detect a dead socket.

A short Tailscale hiccup stops TCP ACKs from the Pixel. Awaiting
``writer.drain()`` on the downlink then blocks for the whole outage; the old
code aborted the call after 5 s (TC@352, 2026-09-24 4:35 PM ET: 49 s gap).

Policy here:
- never block the downlink pump on drain; write frames into the transport
  buffer only while the backlog is under ~backlog_s of audio, otherwise drop
  the frame (stale call audio is useless once the link is back);
- the socket is only "dead" when buffered bytes make no forward progress for
  ``dead_s`` (default 45 s). Inbound silence is handled by the CallSlot
  ws-idle watchdog (60 s), which pongs/uplink frames keep fresh.

The asyncio buffer can be empty while TCP still holds the bytes (Call 1: a
~5 s stall after ``write()``). ``KernelQueueWatch`` reads the kernel send
queue with ``SIOCOUTQ`` and only records it. It does not drain or drop.
"""
from __future__ import annotations

import array
import asyncio
import os
import time
from typing import Any, Callable, Optional


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    try:
        return float(raw) if raw not in (None, "") else default
    except ValueError:
        return default


DOWNLINK_BACKLOG_S = _env_float("GSM2COMPUTER_WS_DOWNLINK_BACKLOG_S", 0.3)
DOWNLINK_DEAD_S = _env_float("GSM2COMPUTER_WS_DOWNLINK_DEAD_S", 45.0)
FRAME_S = 0.02
FRAME_MS = 20.0
# Linux TIOCOUTQ / SIOCOUTQ. Best-effort; any failure leaves the queue unread.
SIOCOUTQ = 0x5411
KERNEL_QUEUE_HIGH_MS = _env_float("GSM2COMPUTER_KERNEL_QUEUE_HIGH_MS", 500.0)
KERNEL_QUEUE_HOLD_S = _env_float("GSM2COMPUTER_KERNEL_QUEUE_HOLD_S", 0.5)
MIN_BACKLOG_BYTES = 4096
# Half a second of 20 ms frames. Older audio is dropped, not allowed to grow.
UPLINK_QUEUE_FRAMES = 25
# ~0.7 s of 48 kHz mono s16. Past this, pw-cat is not reading; don't write more.
UPLINK_BACKLOG_BYTES = 64 * 1024
UPLINK_WRITE_TIMEOUT_S = 0.5


def offer_nowait(queue: asyncio.Queue, item: Any) -> int:
    """Enqueue item, dropping the oldest entries if the queue is full.

    Never waits. Returns how many older items were discarded. The WebSocket
    read loop uses this so a stalled pw-cat cannot stop us reading pings.
    """
    dropped = 0
    while True:
        try:
            queue.put_nowait(item)
            return dropped
        except asyncio.QueueFull:
            try:
                queue.get_nowait()
            except asyncio.QueueEmpty:
                return dropped
            dropped += 1


def kernel_send_queue(writer: Any) -> Optional[int]:
    """Bytes sitting in the kernel TCP send queue. None when the read is unavailable.

    Linux only. Does not send, drain, or change the socket.
    """
    sock = None
    try:
        sock = writer.get_extra_info("socket")
    except Exception:
        sock = None
    if sock is None:
        transport = getattr(writer, "transport", None)
        if transport is not None:
            try:
                sock = transport.get_extra_info("socket")
            except Exception:
                sock = None
    if sock is None:
        return None
    try:
        import fcntl

        fd = sock.fileno() if hasattr(sock, "fileno") else sock
        buf = array.array("I", [0])
        fcntl.ioctl(fd, SIOCOUTQ, buf, True)
        return int(buf[0])
    except Exception:
        return None


class KernelQueueWatch:
    """Track a kernel send queue that stays above ~500 ms of audio for >= 500 ms."""

    def __init__(
        self,
        *,
        high_ms: float = KERNEL_QUEUE_HIGH_MS,
        hold_s: float = KERNEL_QUEUE_HOLD_S,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.high_ms = high_ms
        self.hold_s = hold_s
        self._clock = clock
        self.max_outq = 0
        self.stall_count = 0
        self.samples = 0
        self._high_since: Optional[float] = None
        self._open = False

    def note(
        self,
        outq: Optional[int],
        frame_bytes: int,
        *,
        entrance: str = "",
        now: Optional[float] = None,
    ) -> Optional[str]:
        """Return the stall log line once an episode has lasted ``hold_s``.

        ``frame_bytes`` is one 20 ms downlink frame, so the queue depth converts
        to audio time. A failed ioctl (``outq is None``) is ignored.
        """
        if outq is None or frame_bytes <= 0:
            return None
        outq = max(0, int(outq))
        now = self._clock() if now is None else now
        self.samples += 1
        self.max_outq = max(self.max_outq, outq)
        ms_est = outq * FRAME_MS / float(frame_bytes)
        if ms_est < self.high_ms:
            self._high_since = None
            self._open = False
            return None
        if self._high_since is None:
            self._high_since = now
            return None
        if self._open or now - self._high_since < self.hold_s:
            return None
        self._open = True
        self.stall_count += 1
        return (
            f"downlink kernel queue high outq={outq} ms_est={ms_est:.0f} "
            f"entrance={entrance or '-'}"
        )

    def snapshot(self) -> dict[str, int]:
        return {
            "max_outq": self.max_outq,
            "stall_count": self.stall_count,
            "samples": self.samples,
        }


def transport_buffered(writer: Any) -> int:
    """Bytes queued in the asyncio transport (not yet handed to the kernel)."""
    transport = getattr(writer, "transport", None)
    if transport is None:
        return 0
    try:
        return int(transport.get_write_buffer_size())
    except Exception:
        return 0


def writer_closing(writer: Any) -> bool:
    try:
        return bool(writer.is_closing())
    except Exception:
        return False


class DownlinkSendGuard:
    """Admission + liveness for downlink frames on one WebSocket."""

    def __init__(
        self,
        *,
        backlog_s: float = DOWNLINK_BACKLOG_S,
        dead_s: float = DOWNLINK_DEAD_S,
        frame_s: float = FRAME_S,
        min_backlog_bytes: int = MIN_BACKLOG_BYTES,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.backlog_s = backlog_s
        self.dead_s = dead_s
        self.frame_s = frame_s
        self.min_backlog_bytes = min_backlog_bytes
        self._clock = clock
        self.written_bytes = 0
        self._sent_bytes = 0
        self.buffered = 0
        self.last_progress_at = clock()
        self.stall_started_at: Optional[float] = None
        self.max_stall_s = 0.0
        self.dropped_frames = 0
        self.drop_run = 0
        self.stalls = 0

    def observe(self, buffered: int) -> None:
        """Record transport backlog; progress = bytes actually left the buffer."""
        now = self._clock()
        buffered = max(0, int(buffered))
        sent = self.written_bytes - buffered
        if buffered == 0 or sent > self._sent_bytes:
            if self.stall_started_at is not None:
                self.max_stall_s = max(self.max_stall_s, now - self.stall_started_at)
            self.stall_started_at = None
            self.last_progress_at = now
            self.drop_run = 0
        elif self.stall_started_at is None:
            self.stall_started_at = now
            self.stalls += 1
        if self.stall_started_at is not None:
            self.max_stall_s = max(self.max_stall_s, now - self.stall_started_at)
        self._sent_bytes = max(self._sent_bytes, sent)
        self.buffered = buffered

    def cap_bytes(self, frame_bytes: int) -> int:
        frames = max(1, int(round(self.backlog_s / self.frame_s)))
        return max(self.min_backlog_bytes, frames * max(1, int(frame_bytes)))

    def admit(self, frame_bytes: int) -> bool:
        """True → write this frame now. False → drop it (backlog over cap)."""
        if self.buffered + frame_bytes > self.cap_bytes(frame_bytes):
            self.dropped_frames += 1
            self.drop_run += 1
            return False
        self.written_bytes += frame_bytes
        self.buffered += frame_bytes
        return True

    def note_control_write(self, nbytes: int) -> None:
        """Pings/pongs/text written outside admit() still count toward progress."""
        self.written_bytes += max(0, int(nbytes))
        self.buffered += max(0, int(nbytes))

    def stalled_for(self) -> float:
        if self.stall_started_at is None:
            return 0.0
        return self._clock() - self.stall_started_at

    def dead_reason(self) -> Optional[str]:
        if self.dead_s <= 0 or self.buffered <= 0:
            return None
        quiet = self._clock() - self.last_progress_at
        if quiet >= self.dead_s:
            return (
                f"websocket downlink dead: {self.buffered} bytes unsent for {quiet:.0f}s "
                f"(GSM2COMPUTER_WS_DOWNLINK_DEAD_S={self.dead_s:.0f})"
            )
        return None

    def snapshot(self) -> dict[str, Any]:
        return {
            "buffered": self.buffered,
            "stalled_s": round(self.stalled_for(), 1),
            "max_stall_s": round(self.max_stall_s, 1),
            "stalls": self.stalls,
            "dropped_frames": self.dropped_frames,
        }


def reconnect_should_supersede(snapshot: dict, *, stale_s: float = 1.5) -> bool:
    """A new real (non-e2e) Pixel socket may replace the live one only if it looks stale.

    The Pixel opens a second socket only after it thinks the first died. A healthy
    call (inbound frames < stale_s ago, downlink not stalled) keeps its slot so a
    stray client cannot hijack it.
    """
    if not snapshot.get("busy") or snapshot.get("e2e"):
        return False
    last_ws = snapshot.get("last_ws_s")
    stalled = float(snapshot.get("downlink_stalled_s") or 0.0)
    if stalled >= stale_s:
        return True
    if last_ws is None:
        return False  # still handshaking: never kick a session mid-setup
    return float(last_ws) >= stale_s
